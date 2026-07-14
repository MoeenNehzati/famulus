"""Discover and run all validator modules in both validator packages.

Each validator module must export:
    validate(repo_root: Path) -> list[str]

Validator packages:
  - validators/  (repo-wide checks)
  - skills/skill-maker/validators/  (skill-system checks)

Validator discovery and validator input come from different places:

- discovery loads live `.py` modules from the real `validators/` and
  `skills/skill-maker/validators/` directories
- validation passes those modules a *mirror* of the repo containing only
  git-tracked (indexed) file content, not the real working tree

This separation keeps every validator's filesystem walk (`iterdir`, `rglob`,
`glob`, ...) insensitive to local, gitignored clutter under skills/ — a
personal scratch skill, a platform's own bundled built-ins, an editor cache,
etc. Individual validators don't need their own git-awareness; they just
walk `repo_root` like normal and get the filtered view for free.

Consequence: an untracked validator module in one of the live validator
packages still gets discovered and can affect commit-time validation, even
though ordinary file scanning inside `validate(...)` sees only tracked repo
content. If git is unavailable for some reason, we fall back to the real
repo root so validation still runs (matching prior behavior).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_VALIDATOR_PACKAGES = [
    REPO_ROOT / "validators",
    REPO_ROOT / "skills" / "skill-maker" / "validators",
]

_SKIP = {"__init__.py", "runner.py"}
_GIT_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def _source_git_environment() -> dict[str, str]:
    """Return an environment that resolves Git from the requested cwd only."""

    env = os.environ.copy()
    for name in _GIT_REPOSITORY_ENV:
        env.pop(name, None)
    return env


def _build_tracked_mirror(repo_root: Path) -> Path | None:
    """Copy every git-tracked (indexed) file into a temp dir mirroring repo_root.

    Uses `git ls-files`, which reflects the index — so staged-but-uncommitted
    new files are included (this is what's about to be committed), while
    untracked files (tracked-and-gitignored or simply not yet `git add`ed)
    are excluded. Returns None if git isn't available, so callers can fall
    back to validating the real repo root.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        env=_source_git_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    mirror_root = Path(tempfile.mkdtemp(prefix="ai-repo-validator-mirror-"))
    for rel in result.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not rel:
            continue
        src = repo_root / rel
        if not src.is_file():
            continue
        dst = mirror_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError:
            continue
    return mirror_root


def _build_isolated_git_dir(repo_root: Path, mirror_root: Path) -> Path | None:
    """Create mirror-local Git metadata containing only the source index state."""

    result = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=repo_root,
        env=_source_git_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        return None

    source_git_dir = Path(result.stdout.strip())
    isolated_git_dir = mirror_root / ".git"
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
    source_index = source_git_dir / "index"
    if source_index.is_file():
        shutil.copy2(source_index, isolated_git_dir / "index")
    for shared_index in source_git_dir.glob("sharedindex.*"):
        if shared_index.is_file():
            shutil.copy2(shared_index, isolated_git_dir / shared_index.name)
    return isolated_git_dir


def _load_validators():
    """Yield (name, validate_fn) for every eligible validator module."""
    for package_dir in _VALIDATOR_PACKAGES:
        if not package_dir.is_dir():
            continue
        for path in sorted(package_dir.glob("*.py")):
            if path.name in _SKIP:
                continue
            module_name = f"_validator_{package_dir.name}_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            validate_fn = getattr(module, "validate", None)
            if callable(validate_fn):
                yield path.stem, validate_fn


def run_all(repo_root: Path = REPO_ROOT) -> dict[str, list[str]]:
    """Run all validators and return {module_stem: [errors]}.

    Validators run against a git-tracked mirror of repo_root (see
    `_build_tracked_mirror`), not repo_root itself. Error messages are
    rewritten afterward to reference real repo_root paths so output stays
    readable regardless of where validation actually ran.
    """
    mirror_root = _build_tracked_mirror(repo_root)
    validation_root = mirror_root if mirror_root is not None else repo_root
    previous_git_environment = {
        name: os.environ.get(name) for name in _GIT_REPOSITORY_ENV
    }
    try:
        for name in _GIT_REPOSITORY_ENV:
            os.environ.pop(name, None)
        if mirror_root is not None:
            isolated_git_dir = _build_isolated_git_dir(repo_root, mirror_root)
            if isolated_git_dir is not None:
                os.environ["GIT_DIR"] = str(isolated_git_dir)
                os.environ["GIT_WORK_TREE"] = str(mirror_root)
        results: dict[str, list[str]] = {}
        for name, validate_fn in _load_validators():
            errors = validate_fn(validation_root)
            if errors:
                if mirror_root is not None:
                    mirror_prefix = str(mirror_root)
                    real_prefix = str(repo_root)
                    errors = [e.replace(mirror_prefix, real_prefix) for e in errors]
                results[name] = errors
        return results
    finally:
        for name, previous_value in previous_git_environment.items():
            if previous_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous_value
        if mirror_root is not None:
            shutil.rmtree(mirror_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run all validators")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Root of the repository to validate (default: auto-detected from script location)",
    )
    args = parser.parse_args(argv)

    results = run_all(repo_root=args.repo_root)
    if not results:
        return 0

    for name, errors in results.items():
        print(f"error: {name} found {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
