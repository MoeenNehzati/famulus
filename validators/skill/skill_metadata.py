"""Validate that skills have well-formed YAML frontmatter accepted by all platforms."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from validators.skill_md_body import selected_skill_files

REQUIRES_BLUEPRINT_GRAPH = True

MAX_CODEX_DESCRIPTION_LENGTH = 1024


def validate(
    repo_root: Path, validation_paths: tuple[str, ...] | None = None,
    validation_node_ids: tuple[str, ...] | None = None, graph: object | None = None,
) -> list[str]:
    """Return error strings for every skill with invalid frontmatter."""
    errors: list[str] = []
    skills_dir = repo_root / "skills"
    if validation_paths is None and not skills_dir.is_dir():
        return errors

    paths = (sorted(skills_dir.glob("*/SKILL.md")) if validation_paths is None else
             selected_skill_files(repo_root, validation_paths, validation_node_ids, graph))
    for skill_path in paths:
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


def test_skill_metadata(repo_root, graph, validation_paths, validation_node_ids):
    """Validate selected entry metadata with the canonical subject selection."""
    return validate(repo_root, validation_paths, validation_node_ids, graph)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    errors = validate(repo_root)
    if errors:
        print("Invalid skill metadata:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
