"""Discover and run all validator modules in both validator packages.

Each validator module must export:
    validate(repo_root: Path) -> list[str]

Validator packages:
  - validators/  (repo-wide checks)
  - skills/my-writing-skills/validators/  (skill-system checks)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_VALIDATOR_PACKAGES = [
    REPO_ROOT / "validators",
    REPO_ROOT / "skills" / "my-writing-skills" / "validators",
]

_SKIP = {"__init__.py", "runner.py"}


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
    """Run all validators and return {module_stem: [errors]}."""
    results: dict[str, list[str]] = {}
    for name, validate_fn in _load_validators():
        errors = validate_fn(repo_root)
        if errors:
            results[name] = errors
    return results


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
