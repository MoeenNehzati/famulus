#!/usr/bin/env python3
"""Validate skill metadata accepted by Codex."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


MAX_CODEX_DESCRIPTION_LENGTH = 1024


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    for skill_path in sorted((repo_root / "skills").glob("*/SKILL.md")):
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

    if errors:
        print("Invalid skill metadata:")
        for error in errors:
            print(f"- {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
