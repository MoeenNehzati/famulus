#!/usr/bin/env python3
"""Reject direct cross-skill script-path reach-through for blueprint skills."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"
SCRIPT_SUFFIXES = {".py", ".sh"}


def is_text_script(path: Path) -> bool:
    return path.is_file() and path.suffix in SCRIPT_SUFFIXES


def main() -> int:
    errors: list[str] = []
    skill_names = sorted(path.name for path in SKILLS_ROOT.iterdir() if path.is_dir())
    blueprint_skills = sorted(path for path in SKILLS_ROOT.glob("*/blueprint.yaml"))

    for blueprint_path in blueprint_skills:
        skill_dir = blueprint_path.parent
        skill_name = skill_dir.name
        other_skills = [name for name in skill_names if name != skill_name]
        script_files = [path for path in skill_dir.rglob("*") if is_text_script(path)]

        for path in script_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                for other_skill in other_skills:
                    direct_patterns = [
                        rf"(?:^|[^A-Za-z0-9_-])(?:\.\./)+{re.escape(other_skill)}/scripts/",
                        rf"(?:^|[^A-Za-z0-9_-])skills/{re.escape(other_skill)}/scripts/",
                        rf"/skills/{re.escape(other_skill)}/scripts/",
                    ]
                    if any(re.search(pattern, line) for pattern in direct_patterns):
                        rel = path.relative_to(REPO_ROOT)
                        errors.append(
                            f"{rel}:{lineno}: direct cross-skill script path to {other_skill} is forbidden"
                        )
                        break

                    has_skill_tokens = "skills" in line and "scripts" in line and other_skill in line
                    if has_skill_tokens and "sys.path.insert" in line:
                        rel = path.relative_to(REPO_ROOT)
                        errors.append(
                            f"{rel}:{lineno}: cross-skill sys.path insertion to {other_skill} is forbidden"
                        )
                        break

    if errors:
        print("error: invalid cross-skill boundary usage.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
