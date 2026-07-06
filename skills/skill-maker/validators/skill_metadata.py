"""Validate that skills have well-formed YAML frontmatter accepted by all platforms."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

MAX_CODEX_DESCRIPTION_LENGTH = 1024


def validate(repo_root: Path) -> list[str]:
    """Return error strings for every skill with invalid frontmatter."""
    errors: list[str] = []
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return errors

    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
        if not match:
            errors.append(f"{skill_path}: missing YAML frontmatter")
            continue

        metadata = yaml.safe_load(match.group(1)) or {}
        description = metadata.get("description")
        if not description:
            errors.append(f"{skill_path}: missing description")
            continue

        if len(description) > MAX_CODEX_DESCRIPTION_LENGTH:
            errors.append(
                f"{skill_path}: description is {len(description)} characters; "
                f"Codex maximum is {MAX_CODEX_DESCRIPTION_LENGTH}"
            )

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    errors = validate(repo_root)
    if errors:
        print("Invalid skill metadata:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
