"""Run repository validators against the exact staged Git index."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR_PACKAGES = (
    ("repo", Path("validators")),
    ("skill-maker", Path("skills/skill-maker/validators")),
)
_SKIP = {"__init__.py", "runner.py", "skill_md_body.py"}
_REGULAR_MODES = {"100644", "100755"}
_SUPPORTED_MODES = {*_REGULAR_MODES, "120000"}
_GIT_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


class ValidatorRunnerError(RuntimeError):
    """Raised when validators cannot run against one exact staged view."""


class _IndexEntry(NamedTuple):
    mode: str
    object_id: str
    stage: str
    relative_path: str


def _source_git_environment() -> dict[str, str]:
    """Return an environment whose Git repository comes only from cwd."""

    env = os.environ.copy()
    for name in _GIT_REPOSITORY_ENV:
        env.pop(name, None)
    return env


def _run_source_git(
    repo_root: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            env=_source_git_environment(),
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValidatorRunnerError(f"cannot execute Git: {exc}") from exc


def _index_entries(repo_root: Path) -> tuple[_IndexEntry, ...]:
    result = _run_source_git(repo_root, "ls-files", "--stage", "-z")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot enumerate staged files: {detail}")
    entries: list[_IndexEntry] = []
    seen_stage_zero: set[str] = set()
    for raw_record in result.stdout.split(b"\0"):
        if not raw_record:
            continue
        metadata, separator, raw_path = raw_record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or not raw_path:
            raise ValidatorRunnerError("malformed Git index record")
        mode_bytes, object_id_bytes, stage_bytes = fields
        try:
            mode = mode_bytes.decode("ascii")
            object_id = object_id_bytes.decode("ascii")
            stage = stage_bytes.decode("ascii")
            relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        except UnicodeError as exc:
            raise ValidatorRunnerError("malformed Git index encoding") from exc
        if mode not in _SUPPORTED_MODES:
            raise ValidatorRunnerError(
                f"{relative_path}: unsupported staged Git mode {mode}"
            )
        if stage not in {"0", "1", "2", "3"}:
            raise ValidatorRunnerError(
                f"{relative_path}: unsupported Git index stage {stage}"
            )
        if stage == "0":
            if relative_path in seen_stage_zero:
                raise ValidatorRunnerError(
                    f"{relative_path}: duplicate stage-0 Git index entry"
                )
            seen_stage_zero.add(relative_path)
        entries.append(_IndexEntry(mode, object_id, stage, relative_path))
    return tuple(entries)


def _safe_mirror_path(mirror_root: Path, relative_path: str) -> Path:
    logical = PurePosixPath(relative_path)
    if logical.is_absolute() or not logical.parts or ".." in logical.parts:
        raise ValidatorRunnerError(
            f"{relative_path}: unsafe staged repository path"
        )
    return mirror_root.joinpath(*logical.parts)


def _read_regular_blobs(
    repo_root: Path,
    entries: Sequence[_IndexEntry],
) -> tuple[bytes, ...]:
    if not entries:
        return ()
    request = b"".join(
        entry.object_id.encode("ascii") + b"\n" for entry in entries
    )
    result = _run_source_git(repo_root, "cat-file", "--batch", input_bytes=request)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot read staged blobs: {detail}")
    output = memoryview(result.stdout)
    offset = 0
    blobs: list[bytes] = []
    for entry in entries:
        line_end = result.stdout.find(b"\n", offset)
        if line_end < 0:
            raise ValidatorRunnerError(
                f"{entry.relative_path}: missing Git blob header"
            )
        header = bytes(output[offset:line_end]).split()
        if len(header) != 3 or header[0].decode("ascii") != entry.object_id:
            raise ValidatorRunnerError(
                f"{entry.relative_path}: unexpected Git blob header"
            )
        if header[1] != b"blob":
            raise ValidatorRunnerError(
                f"{entry.relative_path}: staged object is not a blob"
            )
        try:
            size = int(header[2])
        except ValueError as exc:
            raise ValidatorRunnerError(
                f"{entry.relative_path}: invalid Git blob size"
            ) from exc
        start = line_end + 1
        end = start + size
        if end >= len(output) or output[end] != ord("\n"):
            raise ValidatorRunnerError(
                f"{entry.relative_path}: truncated Git blob"
            )
        blobs.append(bytes(output[start:end]))
        offset = end + 1
    if offset != len(output):
        raise ValidatorRunnerError("unexpected trailing Git blob output")
    return tuple(blobs)


def _materialize_tracked_mirror(
    repo_root: Path,
) -> tuple[Path, tuple[_IndexEntry, ...]]:
    entries = _index_entries(repo_root)
    mirror_root = Path(tempfile.mkdtemp(prefix="ai-repo-validator-mirror-"))
    regular = tuple(
        entry
        for entry in entries
        if entry.stage == "0" and entry.mode in _REGULAR_MODES
    )
    try:
        for entry, content in zip(
            regular,
            _read_regular_blobs(repo_root, regular),
            strict=True,
        ):
            destination = _safe_mirror_path(mirror_root, entry.relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            if os.name == "posix":
                destination.chmod(0o755 if entry.mode == "100755" else 0o644)
        return mirror_root, entries
    except BaseException:
        shutil.rmtree(mirror_root, ignore_errors=True)
        raise


def _source_git_dir(repo_root: Path) -> Path:
    result = _run_source_git(repo_root, "rev-parse", "--absolute-git-dir")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot locate Git metadata: {detail}")
    try:
        return Path(result.stdout.decode("utf-8", errors="strict").strip())
    except UnicodeError as exc:
        raise ValidatorRunnerError("Git metadata path is not UTF-8") from exc


def _build_isolated_git_dir(repo_root: Path, mirror_root: Path) -> Path:
    source_git_dir = _source_git_dir(repo_root)
    source_index = source_git_dir / "index"
    if not source_index.is_file():
        raise ValidatorRunnerError("source Git index is unavailable")
    isolated_git_dir = mirror_root / ".git"
    try:
        (isolated_git_dir / "objects").mkdir(parents=True)
        (isolated_git_dir / "refs" / "heads").mkdir(parents=True)
        (isolated_git_dir / "refs" / "tags").mkdir(parents=True)
        (isolated_git_dir / "HEAD").write_text(
            "ref: refs/heads/validator-mirror\n",
            encoding="utf-8",
        )
        (isolated_git_dir / "config").write_text(
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            "\tlogallrefupdates = false\n",
            encoding="utf-8",
        )
        shutil.copy2(source_index, isolated_git_dir / "index")
        for shared_index in source_git_dir.glob("sharedindex.*"):
            if shared_index.is_file():
                shutil.copy2(shared_index, isolated_git_dir / shared_index.name)
    except OSError as exc:
        raise ValidatorRunnerError(
            f"cannot construct isolated Git metadata: {exc}"
        ) from exc
    return isolated_git_dir


def _validator_paths(
    repo_root: Path,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for package_id, relative_package in _VALIDATOR_PACKAGES:
        package_dir = repo_root / relative_package
        if not package_dir.is_dir():
            continue
        for path in sorted(package_dir.glob("*.py")):
            if path.name in _SKIP:
                continue
            validator_id = f"{package_id}/{path.stem}"
            if validator_id in paths:
                raise ValidatorRunnerError(
                    f"duplicate validator ID: {validator_id}"
                )
            paths[validator_id] = path
    return paths


def _selected_validator_paths(
    repo_root: Path,
    validator_ids: Sequence[str] | None,
) -> tuple[tuple[str, Path], ...]:
    available = _validator_paths(repo_root)
    if validator_ids is None:
        selected = tuple(sorted(available))
    else:
        if len(set(validator_ids)) != len(validator_ids):
            raise ValidatorRunnerError("duplicate validator selection")
        unknown = sorted(set(validator_ids) - set(available))
        if unknown:
            raise ValidatorRunnerError(
                "unknown validator: " + ", ".join(unknown)
            )
        selected = tuple(sorted(validator_ids))
    return tuple((validator_id, available[validator_id]) for validator_id in selected)


def _load_validator(
    validator_id: str,
    path: Path,
) -> Callable[[Path], list[str]]:
    module_name = "_validator_" + validator_id.replace("/", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValidatorRunnerError(f"{validator_id}: cannot load validator module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:
        raise ValidatorRunnerError(
            f"{validator_id}: validator import failed: {exc}"
        ) from exc
    validate_fn = getattr(module, "validate", None)
    if not callable(validate_fn):
        raise ValidatorRunnerError(
            f"{validator_id}: validator has no callable validate"
        )
    return validate_fn


def _run_tracked_validators(
    tracked_root: Path,
    display_root: Path,
    validator_ids: Sequence[str] | None,
) -> dict[str, list[str]]:
    mirror_paths = [str(tracked_root), str(tracked_root / "src")]
    sys.path[:] = mirror_paths + [
        entry
        for entry in sys.path
        if entry
        and not Path(entry).is_relative_to(REPO_ROOT)
        and entry not in mirror_paths
    ]
    results: dict[str, list[str]] = {}
    for validator_id, path in _selected_validator_paths(
        tracked_root,
        validator_ids,
    ):
        validate_fn = _load_validator(validator_id, path)
        try:
            errors = validate_fn(tracked_root)
        except BaseException as exc:
            raise ValidatorRunnerError(
                f"{validator_id}: validator execution failed: {exc}"
            ) from exc
        if not isinstance(errors, list) or not all(
            isinstance(error, str) for error in errors
        ):
            raise ValidatorRunnerError(
                f"{validator_id}: validate must return list[str]"
            )
        if errors:
            mirror_prefix = str(tracked_root)
            display_prefix = str(display_root)
            results[validator_id] = [
                error.replace(mirror_prefix, display_prefix) for error in errors
            ]
    return results


def _tracked_child_environment(
    mirror_root: Path,
    isolated_git_dir: Path,
) -> dict[str, str]:
    env = _source_git_environment()
    env.update(
        {
            "GIT_DIR": str(isolated_git_dir),
            "GIT_WORK_TREE": str(mirror_root),
            "PYTHONPATH": os.pathsep.join(
                (str(mirror_root), str(mirror_root / "src"))
            ),
            "PYTHONIOENCODING": "utf-8:strict",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def _run_tracked_child(
    repo_root: Path,
    mirror_root: Path,
    isolated_git_dir: Path,
    validator_ids: Sequence[str] | None,
) -> dict[str, list[str]]:
    runner_path = mirror_root / "validators" / "runner.py"
    if not runner_path.is_file():
        raise ValidatorRunnerError(
            "staged repository does not contain validators/runner.py"
        )
    descriptor, raw_result_path = tempfile.mkstemp(
        prefix="ai-repo-validator-result-",
        suffix=".json",
    )
    os.close(descriptor)
    result_path = Path(raw_result_path)
    command = [
        sys.executable,
        str(runner_path),
        "--tracked-root",
        str(mirror_root),
        "--display-root",
        str(repo_root),
        "--result-path",
        str(result_path),
    ]
    for validator_id in validator_ids or ():
        command.extend(("--validator", validator_id))
    try:
        completed = subprocess.run(
            command,
            cwd=mirror_root,
            env=_tracked_child_environment(mirror_root, isolated_git_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ValidatorRunnerError(
                f"tracked validator runner failed: {detail}"
            )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValidatorRunnerError(
                "tracked validator runner returned no valid result"
            ) from exc
        results = payload.get("results")
        if not isinstance(results, dict):
            raise ValidatorRunnerError(
                "tracked validator runner returned an invalid result"
            )
        if not all(
            isinstance(key, str)
            and isinstance(value, list)
            and all(isinstance(error, str) for error in value)
            for key, value in results.items()
        ):
            raise ValidatorRunnerError(
                "tracked validator runner returned invalid findings"
            )
        return results
    finally:
        result_path.unlink(missing_ok=True)


def run_all(
    repo_root: Path = REPO_ROOT,
    validator_ids: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    """Run validators from and against the exact staged repository view."""

    root = Path(repo_root).resolve()
    mirror_root, _entries = _materialize_tracked_mirror(root)
    try:
        isolated_git_dir = _build_isolated_git_dir(root, mirror_root)
        return _run_tracked_child(
            root,
            mirror_root,
            isolated_git_dir,
            validator_ids,
        )
    finally:
        shutil.rmtree(mirror_root, ignore_errors=True)


def _write_tracked_result(
    tracked_root: Path,
    display_root: Path,
    result_path: Path,
    validator_ids: Sequence[str] | None,
) -> int:
    try:
        results = _run_tracked_validators(
            tracked_root,
            display_root,
            validator_ids,
        )
        result_path.write_text(
            json.dumps({"results": results}, sort_keys=True),
            encoding="utf-8",
        )
        return 0
    except (OSError, UnicodeError, ValidatorRunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _render_findings(results: dict[str, list[str]]) -> int:
    if not results:
        return 0
    for name, errors in results.items():
        print(f"error: {name} found {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--validator", action="append", dest="validator_ids")
    parser.add_argument("--tracked-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--display-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.tracked_root is not None:
        if args.display_root is None or args.result_path is None:
            parser.error(
                "--tracked-root requires --display-root and --result-path"
            )
        return _write_tracked_result(
            args.tracked_root.resolve(),
            args.display_root.resolve(),
            args.result_path,
            args.validator_ids,
        )
    if args.display_root is not None or args.result_path is not None:
        parser.error("--display-root and --result-path are private child options")
    try:
        results = run_all(
            repo_root=args.repo_root,
            validator_ids=args.validator_ids,
        )
    except ValidatorRunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _render_findings(results)


if __name__ == "__main__":
    raise SystemExit(main())
