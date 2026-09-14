"""Shared helpers for validating hand-authored SKILL.md text."""
from __future__ import annotations

import re
from pathlib import Path


INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_GENERATED_BLOCK_RE = re.compile(
    r"<!-- BEGIN BLUEPRINT INTERFACES -->"
    r".*?"
    r"<!-- END BLUEPRINT INTERFACES -->",
    re.DOTALL,
)


def selected_skill_files(
    repo_root: Path, validation_paths: tuple[str, ...],
    validation_node_ids: tuple[str, ...] | None, graph: object | None,
) -> tuple[Path, ...]:
    """Select entry documents, retaining missing entries of selected skill modules."""
    selected = {
        repo_root / path for path in validation_paths
        if len(Path(path).parts) == 3 and Path(path).parts[0] == "skills"
        and Path(path).name == "SKILL.md"
    }
    nodes = getattr(graph, "nodes", {})
    for node_id in validation_node_ids or ():
        node = nodes[node_id]
        if node.node_type == "module" and node.module_root.parent == repo_root / "skills":
            selected.add(node.module_root / "SKILL.md")
    return tuple(sorted(selected))


def strip_frontmatter(text: str) -> str:
    """Return text without a leading YAML frontmatter block."""
    return _FRONTMATTER_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text, count=1)


def strip_generated_blueprint_blocks(text: str) -> str:
    """Return text without the generated blueprint interface block."""
    return _GENERATED_BLOCK_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def hand_authored_skill_body(text: str) -> str:
    """Return SKILL.md hand-authored body text for policy validation."""
    return strip_generated_blueprint_blocks(strip_frontmatter(text))


def strip_fenced_code_blocks(text: str) -> str:
    """Return text without Markdown fenced code blocks."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def generated_interface_block(text: str) -> str | None:
    """Return the generated interface block body, if present."""
    match = re.search(
        rf"{re.escape(INTERFACES_START)}(.*?){re.escape(INTERFACES_END)}",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(1)
