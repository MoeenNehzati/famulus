"""Validate skill directory names and SKILL.md frontmatter name fields."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from validators.skill_md_body import selected_skill_files

REQUIRES_BLUEPRINT_GRAPH = True

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
_FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def validate(
    repo_root: Path, validation_paths: tuple[str, ...] | None = None,
    validation_node_ids: tuple[str, ...] | None = None, graph: object | None = None,
) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    if validation_paths is None and not skills_root.is_dir():
        return errors

    skill_dirs = (
        sorted(p for p in skills_root.iterdir() if p.is_dir())
        if validation_paths is None else
        [path.parent for path in selected_skill_files(repo_root, validation_paths, validation_node_ids, graph)]
    )
    for skill_dir in skill_dirs:
        skill_name = skill_dir.name

        if not _NAME_PATTERN.match(skill_name):
            errors.append(
                f"{skill_dir}: skill directory name must be lower-case "
                f"dash-separated with at least two words"
            )

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            errors.append(f"{skill_dir}: missing SKILL.md")
            continue

        text = skill_file.read_text(encoding="utf-8")
        match = _FRONTMATTER_PATTERN.match(text)
        if not match:
            errors.append(f"{skill_file}: missing YAML frontmatter")
            continue

        try:
            metadata = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            errors.append(f"{skill_file}: invalid YAML frontmatter: {e}")
            continue

        frontmatter_name = metadata.get("name")
        if frontmatter_name != skill_name:
            errors.append(
                f"{skill_file}: frontmatter name must match directory name {skill_name}"
            )

    return errors


def test_skill_names(repo_root, graph, validation_paths, validation_node_ids):
    """Validate only selected skill entry subjects when certification supplies them."""
    return validate(repo_root, validation_paths, validation_node_ids, graph)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[2])
    if errors:
        print("error: invalid skill names.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
