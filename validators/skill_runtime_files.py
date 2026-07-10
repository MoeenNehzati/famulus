"""Validate private skill runtime file layout and names."""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

RTX_DIR_NAME = "_rtx"
ALLOWED_RTX_SUFFIXES = {".py", ".sh"}
EXEMPT_RTX_FILENAMES = {"__init__.py"}
RUNTIME_STEM_RE = re.compile(r"^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$")

_SKIP_SKILLS = {".system"}


def _tracked_files(repo_root: Path) -> set[Path] | None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return {repo_root / path for path in result.stdout.splitlines()}


def _iter_skill_files(repo_root: Path):
    tracked = _tracked_files(repo_root)
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return
    for path in skills_root.rglob("*"):
        if not path.is_file():
            continue
        if tracked is not None and path not in tracked:
            continue
        rel_path = path.relative_to(repo_root)
        if len(rel_path.parts) < 3:
            continue
        if rel_path.parts[1] in _SKIP_SKILLS:
            continue
        yield path, rel_path


def _validate_rtx_file(path: Path, rel_path: Path) -> list[str]:
    errors: list[str] = []
    if path.name in EXEMPT_RTX_FILENAMES:
        return errors
    if len(rel_path.parts) != 4:
        errors.append(f"{rel_path}: runtime files must be direct children of skills/<skill>/{RTX_DIR_NAME}/")
        return errors
    if path.suffix not in ALLOWED_RTX_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_RTX_SUFFIXES))
        errors.append(f"{rel_path}: unsupported runtime suffix `{path.suffix}`; allowed suffixes: {allowed}")
    if not RUNTIME_STEM_RE.fullmatch(path.stem):
        errors.append(
            f"{rel_path}: runtime filename stem must match "
            "`^_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$`"
        )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    seen_by_skill: dict[Path, dict[str, Path]] = defaultdict(dict)

    for path, rel_path in _iter_skill_files(repo_root):
        parts = rel_path.parts
        skill_root = Path(parts[0]) / parts[1]

        if len(parts) >= 4 and parts[2] == "scripts" and path.suffix in ALLOWED_RTX_SUFFIXES:
            errors.append(
                f"{rel_path}: skill runtime files must live under "
                f"`skills/<skill>/{RTX_DIR_NAME}/`, not `scripts/`"
            )
            continue

        if len(parts) >= 4 and parts[2] == RTX_DIR_NAME:
            errors.extend(_validate_rtx_file(path, rel_path))
            folded = path.name.casefold()
            previous = seen_by_skill[skill_root].get(folded)
            if previous is not None and previous != rel_path:
                errors.append(f"{rel_path}: case-insensitive runtime filename collision with {previous}")
            else:
                seen_by_skill[skill_root][folded] = rel_path

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    if errors:
        print("Skill runtime file violations found:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
