"""Discover and run all validator modules in both validator packages.

Each validator module must export:
    validate(repo_root: Path) -> list[str]

Validator packages:
  - validators/  (repo-wide checks)
  - skills/my-writing-skills/validators/  (skill-system checks)

Validators are handed a *mirror* of the repo containing only git-tracked
(indexed) file content, not the real working tree. This is the single choke
point that keeps every validator's filesystem walk (`iterdir`, `rglob`,
`glob`, ...) insensitive to local, gitignored clutter under skills/ — a
personal scratch skill, a platform's own bundled built-ins, an editor cache,
etc. Individual validators don't need their own git-awareness; they just
walk `repo_root` like normal and get the filtered view for free. If git is
unavailable for some reason, we fall back to the real repo root so
validation still runs (matching prior behavior).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_VALIDATOR_PACKAGES = [
    REPO_ROOT / "validators",
    REPO_ROOT / "skills" / "my-writing-skills" / "validators",
]

_SKIP = {"__init__.py", "runner.py"}


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
    try:
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
