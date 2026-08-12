"""Run repository validators against the exact staged Git index."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Callable, Iterator, NamedTuple, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_VALIDATOR_PACKAGE = ("repo", Path("validators"))
_CURRENT_SKILL_VALIDATOR_PACKAGE = (
    "skill-maker",
    Path("skills/skill-maker/validators"),
)
_FUTURE_SKILL_VALIDATOR_PACKAGE = (
    "skill-maker",
    Path("validators/skill"),
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
    """Signal that validators cannot run against one exact staged view.

    Intent
    ------
    Distinguish validator-runner contract failures from ordinary findings.

    Rationale
    ---------
    Parent and child processes need one bounded failure type for malformed Git
    state, validator protocols, and serialized results.

    Pseudocode
    ----------
    - set runner_error = staged validation contract failure
    - return runner_error

    Wraps
    -----
    - none
    """


class _IndexEntry(NamedTuple):
    """Record one mode, object, stage, and path from the captured Git index.

    Intent
    ------
    Carry immutable index metadata between snapshot, mirror, and validation steps.

    Rationale
    ---------
    Explicit fields prevent later steps from reparsing or reopening the live index.

    Pseudocode
    ----------
    - set index_entry = mode object_id stage and relative_path
    - return index_entry

    Wraps
    -----
    - none
    """

    mode: str
    object_id: str
    stage: str
    relative_path: str


class _RepositorySnapshot(NamedTuple):
    """Record the temporary Git state captured for one validator run.

    Intent
    ------
    Keep the copied index, delta Git directory, and immutable HEAD baseline together.

    Rationale
    ---------
    A single record ensures path selection and mirror materialization share one index.

    Pseudocode
    ----------
    - set repository_snapshot = root git_dir index_path and head_commit
    - return repository_snapshot

    Wraps
    -----
    - none
    """

    root: Path
    git_dir: Path
    index_path: Path
    head_commit: str | None


class PreparedRepositoryView(NamedTuple):
    """Expose one materialized staged tree and its immutable changed paths."""

    root: Path
    staged_paths: tuple[str, ...]


def _source_git_environment() -> dict[str, str]:
    """Return an environment whose Git repository comes only from cwd.

    Intent
    ------
    Remove ambient Git routing variables before repository commands execute.

    Rationale
    ---------
    Validator behavior must depend on the explicit repository, not a caller's
    worktree, namespace, index, or object-store overrides.

    Pseudocode
    ----------
    - set clean_environment = process environment without Git routing variables
    - return clean_environment

    Wraps
    -----
    - none
    """

    env = os.environ.copy()
    for name in _GIT_REPOSITORY_ENV:
        env.pop(name, None)
    return env


def _run_source_git(
    repo_root: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one Git command against the explicit source repository.

    Intent
    ------
    Centralize byte-preserving Git execution with ambient routing removed.

    Rationale
    ---------
    Index paths may contain non-UTF-8 bytes, so callers need captured byte streams
    and consistent OS-error conversion.

    Pseudocode
    ----------
    - set git_result = Git args in repo_root with clean environment and input_bytes
    - return git_result

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._source_git_environment:
      why:
        computes: "Builds the ambient-isolated environment used for the source Git subprocess."

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports an operating-system failure to start the source Git subprocess."
    """
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


def _run_snapshot_git(
    repo_root: Path,
    snapshot: _RepositorySnapshot,
    *args: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run one Git command against the copied index snapshot.

    Intent
    ------
    Query temporary Git metadata while retaining the source working-tree location.

    Rationale
    ---------
    Delta discovery must ignore live index replacements after snapshot capture.

    Pseudocode
    ----------
    - set snapshot_environment = clean environment plus snapshot Git locations
    - set git_result = Git args with snapshot_environment
    - return git_result

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._source_git_environment:
      why:
        constructs: "Builds the clean environment extended with captured Git locations."
    .ValidatorRunnerError:
      why:
        raises: "Reports an operating-system failure to start the snapshot Git subprocess."
    """
    env = _source_git_environment()
    env.update(
        {
            "GIT_DIR": str(snapshot.git_dir),
            "GIT_WORK_TREE": str(repo_root),
        }
    )
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            env=env,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValidatorRunnerError(f"cannot execute Git: {exc}") from exc


def _index_entries(
    repo_root: Path,
    *,
    snapshot: _RepositorySnapshot | None = None,
) -> tuple[_IndexEntry, ...]:
    """Parse all staged index records from live or snapshotted Git metadata.

    Intent
    ------
    Convert NUL-delimited Git index output into validated immutable records.

    Rationale
    ---------
    Mode, stage, duplicate, and path checks fail closed before filesystem writes.

    Pseudocode
    ----------
    - set raw_records = staged index listing from live or snapshot Git
    - set parsed_entries = validated mode object stage and path records
    - return parsed_entries

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_source_git:
      why:
        constructs: "Builds the live index listing when no repository snapshot is supplied."
    ._run_snapshot_git:
      why:
        constructs: "Builds the captured index listing when a repository snapshot is supplied."
    ._IndexEntry:
      why:
        constructs: "Builds each immutable validated index record returned to later stages."
    .ValidatorRunnerError:
      why:
        raises: "Reports malformed, duplicate, unsupported, or unreadable index records."
    """
    if snapshot is None:
        result = _run_source_git(repo_root, "ls-files", "--stage", "-z")
    else:
        result = _run_snapshot_git(
            repo_root,
            snapshot,
            "ls-files",
            "--stage",
            "-z",
        )
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
    """Resolve one safe repository-relative path beneath a mirror root.

    Intent
    ------
    Reject absolute and parent-traversing Git paths before materialization.

    Rationale
    ---------
    Tracked filenames are untrusted inputs to temporary filesystem writes.

    Pseudocode
    ----------
    - set logical_path = relative_path parsed as POSIX parts
    - if logical_path is unsafe:
      - raise validator runner error
    - return mirror_root plus logical_path

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Rejects an absolute, empty, or parent-traversing staged repository path."
    """
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
    """Read exact blob bytes for regular entries through one Git batch request.

    Intent
    ------
    Materialize captured object IDs without consulting working-tree files.

    Rationale
    ---------
    Batch reads preserve ordering and verify each object header, type, and size.

    Pseudocode
    ----------
    - set object_request = ordered object ids from entries
    - set batch_output = Git cat-file response for object_request
    - set blob_bytes = validated blob payloads in entry order
    - return blob_bytes

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_source_git:
      why:
        constructs: "Builds the ordered cat-file batch response for captured object IDs."
    .ValidatorRunnerError:
      why:
        raises: "Reports Git failures or malformed, truncated, non-blob batch responses."
    """
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
    entries: Sequence[_IndexEntry],
) -> tuple[Path, tuple[_IndexEntry, ...]]:
    """Write stage-zero regular blobs into a temporary repository mirror.

    Intent
    ------
    Build the exact filesystem view consumed by isolated validators.

    Rationale
    ---------
    Reading by captured object ID makes unstaged working-tree bytes irrelevant.

    Pseudocode
    ----------
    - set frozen_entries = immutable copy of entries
    - set regular_entries = stage-zero regular records
    - set mirror_files = captured blobs at safe paths with captured modes
    - return mirror_root and frozen_entries

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._read_regular_blobs:
      why:
        reads: "Reads exact captured blob bytes in the same order as regular index entries."

    InstantiationsFromRepo
    ----------------------
    ._safe_mirror_path:
      why:
        constructs: "Builds each bounded destination path before captured bytes are written."
    """
    frozen_entries = tuple(entries)
    mirror_root = Path(tempfile.mkdtemp(prefix="ai-repo-validator-mirror-"))
    regular = tuple(
        entry
        for entry in frozen_entries
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
        return mirror_root, frozen_entries
    except BaseException:
        shutil.rmtree(mirror_root, ignore_errors=True)
        raise


def _source_git_dir(repo_root: Path) -> Path:
    """Resolve the source repository's absolute per-worktree Git directory.

    Intent
    ------
    Locate the live index file that must be copied at snapshot capture.

    Rationale
    ---------
    Linked worktrees may not store their index under the common Git directory.

    Pseudocode
    ----------
    - set git_dir = absolute Git directory reported for repo_root
    - return git_dir

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_source_git:
      why:
        constructs: "Builds the rev-parse response containing the per-worktree Git directory."
    .ValidatorRunnerError:
      why:
        raises: "Reports an unavailable or non-UTF-8 per-worktree Git directory."
    """
    result = _run_source_git(repo_root, "rev-parse", "--absolute-git-dir")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot enumerate staged files: {detail}")
    try:
        return Path(result.stdout.decode("utf-8", errors="strict").strip())
    except UnicodeError as exc:
        raise ValidatorRunnerError("Git metadata path is not UTF-8") from exc


def _source_git_common_dir(repo_root: Path) -> Path:
    """Resolve the source repository's common Git metadata directory.

    Intent
    ------
    Locate the object store shared by ordinary and linked worktrees.

    Rationale
    ---------
    Snapshot delta commands need read-only object access without copying history.

    Pseudocode
    ----------
    - set common_dir = Git common directory resolved against repo_root
    - return common_dir

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_source_git:
      why:
        constructs: "Builds the rev-parse response containing the common Git directory."
    .ValidatorRunnerError:
      why:
        raises: "Reports an unavailable or non-UTF-8 common Git directory."
    """
    result = _run_source_git(repo_root, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot locate common Git metadata: {detail}")
    try:
        raw = result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise ValidatorRunnerError("common Git metadata path is not UTF-8") from exc
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _capture_repository_snapshot(repo_root: Path) -> _RepositorySnapshot:
    """Copy index state and capture the immutable HEAD baseline for one run.

    Intent
    ------
    Create temporary Git metadata used by both delta selection and mirror creation.

    Rationale
    ---------
    Copying the index once prevents concurrent live-index changes from mixing paths
    and bytes. Bracketing that copy with HEAD reads rejects a concurrent commit
    that would pair the copied index with the wrong baseline, while object
    alternates avoid mutating or duplicating source history.

    Pseudocode
    ----------
    - set source_metadata = worktree index common objects and current HEAD
    - set head_before = current commit or unborn state
    - set snapshot_index = source index and shared-index bytes
    - set head_after = current commit or unborn state
    - if head_before differs from head_after:
      - raise concurrent HEAD transition error
    - set snapshot_head = stable captured commit or isolated unborn branch
    - return repository snapshot

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._source_git_common_dir:
      why:
        reads: "Locates the immutable source object store exposed to temporary Git metadata."

    InstantiationsFromRepo
    ----------------------
    ._source_git_dir:
      why:
        constructs: "Builds the source index path copied into the temporary snapshot."
    ._run_source_git:
      why:
        constructs: "Builds the immutable current-HEAD response recorded in temporary metadata."
    ._RepositorySnapshot:
      why:
        constructs: "Builds the snapshot record shared by delta and mirror construction."
    .ValidatorRunnerError:
      why:
        raises: "Reports missing index state or an unreadable current HEAD baseline."
    """
    source_git_dir = _source_git_dir(repo_root)
    source_index = source_git_dir / "index"
    if not source_index.is_file():
        raise ValidatorRunnerError("source Git index is unavailable")
    snapshot_root = Path(tempfile.mkdtemp(prefix="ai-repo-validator-index-"))
    snapshot_git_dir = snapshot_root / "git"
    try:
        head_before_result = _run_source_git(
            repo_root,
            "rev-parse",
            "--verify",
            "--quiet",
            "HEAD^{commit}",
        )
        if head_before_result.returncode == 0:
            head_before = head_before_result.stdout.decode("ascii").strip()
        elif head_before_result.returncode == 1:
            head_before = None
        else:
            detail = head_before_result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise ValidatorRunnerError(f"cannot capture HEAD: {detail}")

        (snapshot_git_dir / "objects" / "info").mkdir(parents=True)
        (snapshot_git_dir / "refs" / "heads").mkdir(parents=True)
        (snapshot_git_dir / "refs" / "tags").mkdir(parents=True)
        snapshot_index = snapshot_git_dir / "index"
        shutil.copy2(source_index, snapshot_index)
        for shared_index in source_git_dir.glob("sharedindex.*"):
            if shared_index.is_file():
                shutil.copy2(shared_index, snapshot_git_dir / shared_index.name)
        (snapshot_git_dir / "config").write_text(
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            "\tlogallrefupdates = false\n",
            encoding="utf-8",
            newline="",
        )
        common_objects = _source_git_common_dir(repo_root) / "objects"
        (snapshot_git_dir / "objects" / "info" / "alternates").write_text(
            f"{common_objects}\n",
            encoding="utf-8",
            newline="",
        )
        head_after_result = _run_source_git(
            repo_root,
            "rev-parse",
            "--verify",
            "--quiet",
            "HEAD^{commit}",
        )
        if head_after_result.returncode == 0:
            head_commit = head_after_result.stdout.decode("ascii").strip()
        elif head_after_result.returncode == 1:
            head_commit = None
        else:
            detail = head_after_result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise ValidatorRunnerError(f"cannot capture HEAD: {detail}")
        if head_before != head_commit:
            raise ValidatorRunnerError(
                "HEAD changed while capturing repository snapshot"
            )
        if head_commit is not None:
            (snapshot_git_dir / "HEAD").write_text(
                f"{head_commit}\n",
                encoding="ascii",
                newline="",
            )
        else:
            (snapshot_git_dir / "HEAD").write_text(
                "ref: refs/heads/validator-unborn\n",
                encoding="ascii",
                newline="",
            )
        return _RepositorySnapshot(
            snapshot_root,
            snapshot_git_dir,
            snapshot_index,
            head_commit,
        )
    except BaseException:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise


def _changed_regular_paths(
    repo_root: Path,
    snapshot: _RepositorySnapshot,
    entries: Sequence[_IndexEntry],
) -> tuple[str, ...]:
    """Return changed stage-zero regular paths from the captured index.

    Intent
    ------
    Select the bounded path set supplied to staged-aware validators.

    Rationale
    ---------
    Git diff semantics exclude intent-to-add and deletion entries; intersecting
    regular records excludes symlinks and unresolved conflict-only paths.

    Pseudocode
    ----------
    - set changed_names = snapshot index diff with intent-to-add hidden
    - set eligible_names = stage-zero regular entry paths
    - return sorted intersection of changed_names and eligible_names

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_snapshot_git:
      why:
        constructs: "Builds the intent-to-add-aware index-versus-HEAD changed-path stream."
    .ValidatorRunnerError:
      why:
        raises: "Reports failure to derive changed paths from captured Git metadata."
    """
    result = _run_snapshot_git(
        repo_root,
        snapshot,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        "--ita-invisible-in-index",
        "--diff-filter=ACMRT",
        "--no-renames",
        "--",
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValidatorRunnerError(f"cannot identify staged changes: {detail}")
    eligible = {
        entry.relative_path
        for entry in entries
        if entry.stage == "0" and entry.mode in _REGULAR_MODES
    }
    changed: set[str] = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if path in eligible:
            changed.add(path)
    return tuple(sorted(changed))


def _build_isolated_git_dir(
    snapshot: _RepositorySnapshot,
    mirror_root: Path,
) -> Path:
    """Construct child Git metadata from the same copied index snapshot.

    Intent
    ------
    Give validators writable isolated Git state paired with staged mirror bytes.

    Rationale
    ---------
    Copying snapshot metadata rather than live metadata preserves one run boundary
    and prevents validator Git mutations from reaching the source repository.

    Pseudocode
    ----------
    - set isolated_metadata = Git directories and unborn validator HEAD
    - set isolated_index = captured index and shared-index bytes
    - return isolated Git directory

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports filesystem failure while constructing isolated child Git metadata after substantial local directory and index setup."
    """
    isolated_git_dir = mirror_root / ".git"
    try:
        (isolated_git_dir / "objects").mkdir(parents=True)
        (isolated_git_dir / "refs" / "heads").mkdir(parents=True)
        (isolated_git_dir / "refs" / "tags").mkdir(parents=True)
        (isolated_git_dir / "HEAD").write_text(
            "ref: refs/heads/validator-mirror\n",
            encoding="utf-8",
            newline="",
        )
        (isolated_git_dir / "config").write_text(
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = false\n"
            "\tlogallrefupdates = false\n",
            encoding="utf-8",
            newline="",
        )
        shutil.copy2(snapshot.index_path, isolated_git_dir / "index")
        for shared_index in snapshot.git_dir.glob("sharedindex.*"):
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
    """Discover canonical validator IDs and source paths in supported layouts.

    Intent
    ------
    Build the deterministic validator catalog for the staged repository view.

    Rationale
    ---------
    Explicit package IDs and layout ambiguity checks prevent silent shadowing.

    Pseudocode
    ----------
    - set validator_packages = repository and one supported skill layout
    - set validator_paths = discovered Python validators excluding helpers
    - return validator_paths keyed by canonical id

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports ambiguous skill layouts or duplicate canonical validator identifiers."
    """
    current_skill_dir = repo_root / _CURRENT_SKILL_VALIDATOR_PACKAGE[1]
    future_skill_dir = repo_root / _FUTURE_SKILL_VALIDATOR_PACKAGE[1]
    if current_skill_dir.is_dir() and future_skill_dir.is_dir():
        raise ValidatorRunnerError(
            "ambiguous skill validator layout: both "
            "skills/skill-maker/validators and validators/skill exist"
        )
    packages = [
        _REPOSITORY_VALIDATOR_PACKAGE,
        *(
            [_CURRENT_SKILL_VALIDATOR_PACKAGE]
            if current_skill_dir.is_dir()
            else []
        ),
        *(
            [_FUTURE_SKILL_VALIDATOR_PACKAGE]
            if future_skill_dir.is_dir()
            else []
        ),
    ]
    paths: dict[str, Path] = {}
    for package_id, relative_package in packages:
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
    excluded_validator_ids: Sequence[str] | None = None,
) -> tuple[tuple[str, Path], ...]:
    """Resolve an optional validator-ID selection against the staged catalog.

    Intent
    ------
    Return a sorted, duplicate-free validator execution list.

    Rationale
    ---------
    Unknown or duplicate selections indicate caller mistakes and must fail closed.

    Pseudocode
    ----------
    - set available_validators = discovered canonical validator paths
    - set selected_ids = validated requested ids or all available ids
    - set selected_ids = selected_ids without validated excluded ids
    - return selected id and path pairs in lexical order

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._validator_paths:
      why:
        constructs: "Builds the available canonical validator catalog used for selection."
    .ValidatorRunnerError:
      why:
        raises: "Reports duplicate requested identifiers or identifiers absent from the catalog."
    """
    available = _validator_paths(repo_root)
    excluded = tuple(excluded_validator_ids or ())
    if len(set(excluded)) != len(excluded):
        raise ValidatorRunnerError("duplicate validator exclusion")
    unknown_excluded = sorted(set(excluded) - set(available))
    if unknown_excluded:
        raise ValidatorRunnerError(
            "unknown excluded validator: " + ", ".join(unknown_excluded)
        )
    if validator_ids is None:
        selected = tuple(sorted(set(available) - set(excluded)))
    else:
        if len(set(validator_ids)) != len(validator_ids):
            raise ValidatorRunnerError("duplicate validator selection")
        unknown = sorted(set(validator_ids) - set(available))
        if unknown:
            raise ValidatorRunnerError(
                "unknown validator: " + ", ".join(unknown)
            )
        selected = tuple(sorted(set(validator_ids) - set(excluded)))
    return tuple((validator_id, available[validator_id]) for validator_id in selected)


def _load_validator(
    validator_id: str,
    path: Path,
) -> tuple[ModuleType, Callable[[Path], list[str]]]:
    """Import one staged validator module and resolve its compatibility entry point.

    Intent
    ------
    Load validator code exclusively from the materialized staged mirror.

    Rationale
    ---------
    Requiring callable `validate` preserves the established root-validator contract
    while optional protocols are inspected after import.

    Pseudocode
    ----------
    - set module_spec = import specification for validator path
    - set validator_module = module executed from module_spec
    - set validate_function = callable validate attribute
    - return validator_module and validate_function

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports unavailable import machinery, import failure, or a missing validate entry point."
    """
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
    return module, validate_fn


def _validated_errors(
    validator_id: str,
    entry_point: str,
    errors: object,
) -> list[str]:
    """Validate and return one validator entry point's finding list.

    Intent
    ------
    Enforce the shared `list[str]` result boundary for every validator protocol.

    Rationale
    ---------
    Protocol-neutral validation keeps malformed returns from corrupting result JSON.

    Pseudocode
    ----------
    - if errors is not a list of strings:
      - raise entry-point-specific validator runner error
    - return errors

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports a validator entry point that returned anything other than list[str]."
    """
    if not isinstance(errors, list) or not all(
        isinstance(error, str) for error in errors
    ):
        raise ValidatorRunnerError(
            f"{validator_id}: {entry_point} must return list[str]"
        )
    return errors



def _tracked_child_environment(
    mirror_root: Path,
    isolated_git_dir: Path,
) -> dict[str, str]:
    """Build the isolated process environment for the tracked child runner.

    Intent
    ------
    Route imports, Git commands, and text encoding to staged mirror resources.

    Rationale
    ---------
    Explicit paths prevent source-tree imports, user-site packages, and live Git
    metadata from affecting validator execution.

    Pseudocode
    ----------
    - set child_environment = clean process environment
    - set child_environment = child_environment plus mirror paths and UTF-8 settings
    - return child_environment

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._source_git_environment:
      why:
        constructs: "Builds the ambient-isolated base extended with staged child locations."
    """
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


def _load_staged_paths(
    tracked_root: Path,
    staged_paths_file: Path,
) -> tuple[str, ...]:
    """Load and validate the parent's serialized changed-path selection.

    Intent
    ------
    Reconstruct the staged-aware validator path tuple inside the child process.

    Rationale
    ---------
    Type, duplicate, traversal, and index-membership checks keep the private file
    channel fail closed and preserve surrogateescaped Git path strings.

    Pseudocode
    ----------
    - set path_payload = decoded JSON from staged_paths_file
    - set validated_payload = unique safe relative path strings
    - set validated_payload = validated_payload intersect stage-zero regular paths
    - return staged path tuple

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._index_entries:
      why:
        reads: "Reads the isolated copied index used to validate regular-file membership."
    ._safe_mirror_path:
      why:
        validates: "Rejects absolute and parent-traversing paths in the private payload."

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports malformed JSON, invalid list structure, duplicates, or ineligible paths."
    """
    try:
        payload = json.loads(staged_paths_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidatorRunnerError("staged paths payload is invalid") from exc
    if not isinstance(payload, list) or not all(
        isinstance(path, str) for path in payload
    ):
        raise ValidatorRunnerError("staged paths payload must be list[str]")
    if len(set(payload)) != len(payload):
        raise ValidatorRunnerError("staged paths payload contains duplicates")
    eligible = {
        entry.relative_path
        for entry in _index_entries(tracked_root)
        if entry.stage == "0" and entry.mode in _REGULAR_MODES
    }
    for relative_path in payload:
        _safe_mirror_path(tracked_root, relative_path)
        if relative_path not in eligible:
            raise ValidatorRunnerError(
                f"{relative_path}: staged path is not a regular index entry"
            )
    return tuple(payload)


def _run_tracked_child(
    repo_root: Path,
    mirror_root: Path,
    isolated_git_dir: Path,
    validator_ids: Sequence[str] | None,
    excluded_validator_ids: Sequence[str] | None,
    staged_paths: Sequence[str],
    timing_output: Path | None = None,
) -> dict[str, list[str]]:
    """Run the staged validator runner in a separate isolated Python process.

    Intent
    ------
    Execute staged code and return validated structured findings to the parent.

    Rationale
    ---------
    Process isolation resets imports and environment state while temporary JSON
    files avoid command-length limits for findings and changed paths.

    Pseudocode
    ----------
    - set staged_path_payload = changed paths as private JSON
    - set child_result = staged runner process result in mirror environment
    - set findings = validated result JSON mapping
    - set cleanup_state = temporary JSON files deleted
    - return validator findings

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._tracked_child_environment:
      why:
        computes: "Supplies the staged import, Git, and encoding environment for the child process."

    InstantiationsFromRepo
    ----------------------
    .ValidatorRunnerError:
      why:
        raises: "Reports child failure or malformed, missing, or structurally invalid result JSON."
    """
    runner_path = mirror_root / "repo_checks.py"
    if not runner_path.is_file():
        raise ValidatorRunnerError(
            "staged repository does not contain repo_checks.py"
        )
    result_path: Path | None = None
    staged_paths_file: Path | None = None
    try:
        descriptor, raw_result_path = tempfile.mkstemp(
            prefix="ai-repo-validator-result-",
            suffix=".json",
        )
        os.close(descriptor)
        result_path = Path(raw_result_path)
        staged_descriptor, raw_staged_paths_file = tempfile.mkstemp(
            prefix="ai-repo-validator-staged-paths-",
            suffix=".json",
        )
        os.close(staged_descriptor)
        staged_paths_file = Path(raw_staged_paths_file)
        staged_paths_file.write_text(
            json.dumps(list(staged_paths), ensure_ascii=True),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(runner_path),
            "--tracked-root",
            str(mirror_root),
            "--display-root",
            str(repo_root),
            "--result-path",
            str(result_path),
            "--staged-paths-file",
            str(staged_paths_file),
        ]
        for validator_id in validator_ids or ():
            command.extend(("--validator", validator_id))
        for validator_id in excluded_validator_ids or ():
            command.extend(("--exclude-validator", validator_id))
        if timing_output is not None:
            command.extend(("--timing-output", str(timing_output)))
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
            detail = "\n".join(
                part
                for part in (
                    completed.stderr.strip(),
                    completed.stdout.strip(),
                )
                if part
            )
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
        if result_path is not None:
            result_path.unlink(missing_ok=True)
        if staged_paths_file is not None:
            staged_paths_file.unlink(missing_ok=True)


def capture_staged_paths(repo_root: Path) -> tuple[str, ...]:
    """Capture the current index once and return its changed regular paths."""
    root = Path(repo_root).resolve()
    snapshot = _capture_repository_snapshot(root)
    try:
        entries = _index_entries(root, snapshot=snapshot)
        return _changed_regular_paths(root, snapshot, entries)
    finally:
        shutil.rmtree(snapshot.root, ignore_errors=True)


@contextmanager
def staged_repository_view(repo_root: Path) -> Iterator[PreparedRepositoryView]:
    """Materialize one exact staged tree for a complete pytest invocation."""
    root = Path(repo_root).resolve()
    snapshot = _capture_repository_snapshot(root)
    mirror_root: Path | None = None
    try:
        entries = _index_entries(root, snapshot=snapshot)
        staged_paths = _changed_regular_paths(root, snapshot, entries)
        mirror_root, _entries = _materialize_tracked_mirror(root, entries)
        _build_isolated_git_dir(snapshot, mirror_root)
        yield PreparedRepositoryView(mirror_root, staged_paths)
    finally:
        if mirror_root is not None:
            shutil.rmtree(mirror_root, ignore_errors=True)
        shutil.rmtree(snapshot.root, ignore_errors=True)


def run_all(
    repo_root: Path = REPO_ROOT,
    validator_ids: Sequence[str] | None = None,
    excluded_validator_ids: Sequence[str] | None = None,
    timing_output: Path | None = None,
) -> dict[str, list[str]]:
    """Run validators from and against the exact staged repository view.

    Intent
    ------
    Coordinate one captured index, changed-path set, staged mirror, and child run.

    Rationale
    ---------
    A single lifecycle guarantees validators see staged bytes and bounded path
    metadata while cleanup runs after success or failure.

    Pseudocode
    ----------
    - set snapshot = copied index and captured HEAD baseline
    - set entries = parsed snapshot index records
    - set staged_paths = changed regular paths from snapshot
    - set mirror = materialized blobs and isolated Git metadata
    - set findings = tracked child validator results
    - set cleanup_state = mirror and snapshot resources deleted
    - return findings

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._capture_repository_snapshot:
      why:
        constructs: "Builds the immutable index and HEAD state shared by every step in the run."
    ._index_entries:
      why:
        constructs: "Builds validated index records from the captured snapshot."
    ._changed_regular_paths:
      why:
        constructs: "Builds the bounded changed-path tuple supplied to staged-aware validators."
    ._materialize_tracked_mirror:
      why:
        constructs: "Builds the staged filesystem mirror from captured blob object IDs."
    ._build_isolated_git_dir:
      why:
        constructs: "Builds writable child Git metadata from the copied snapshot index."
    ._run_tracked_child:
      why:
        constructs: "Builds the canonical validator finding mapping returned to the caller."
    """

    root = Path(repo_root).resolve()
    with staged_repository_view(root) as view:
        isolated_git_dir = view.root / ".git"
        return _run_tracked_child(
            root,
            view.root,
            isolated_git_dir,
            validator_ids,
            excluded_validator_ids,
            view.staged_paths,
            timing_output,
        )


def _write_tracked_result(
    tracked_root: Path,
    display_root: Path,
    result_path: Path,
    validator_ids: Sequence[str] | None,
    excluded_validator_ids: Sequence[str] | None,
    staged_paths_file: Path,
    timing_output: Path | None = None,
) -> int:
    """Execute child validators and serialize their result for the parent.

    Intent
    ------
    Provide the private child-process command boundary used by `run_all`.

    Rationale
    ---------
    Writing one validated JSON object separates expected findings from process
    failures and keeps stdout free of transport data.

    Pseudocode
    ----------
    - set staged_paths = validated private path payload
    - set findings = isolated tracked validator results
    - set result_payload = findings as sorted JSON at result_path
    - return success or bounded runner-error status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._load_staged_paths:
      why:
        constructs: "Builds the validated changed-path tuple supplied to staged-aware validators."
    """
    try:
        staged_paths = _load_staged_paths(tracked_root, staged_paths_file)
        from officina.repository_checks import run_validators_with_pytest

        results = run_validators_with_pytest(
            runner=sys.modules[__name__],
            tracked_root=tracked_root,
            display_root=display_root,
            validator_ids=validator_ids,
            excluded_validator_ids=excluded_validator_ids,
            staged_paths=staged_paths,
            timing_output=timing_output,
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
    """Render validator findings to stderr and return the hook exit status.

    Intent
    ------
    Present grouped root-validator failures to command-line callers.

    Rationale
    ---------
    A stable summary distinguishes a clean run from policy findings without
    conflating either state with runner execution errors.

    Pseudocode
    ----------
    - if results is empty:
      - return success
    - set stderr_report = each validator id count and finding
    - return findings-present status

    Wraps
    -----
    - none
    """
    if not results:
        return 0
    for name, errors in results.items():
        print(f"error: {name} found {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    return 1
