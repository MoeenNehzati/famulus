"""Shared helpers for validating hand-authored SKILL.md text."""
from __future__ import annotations

import re


CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_GENERATED_BLOCK_RE = re.compile(
    r"<!-- BEGIN BLUEPRINT (?:CONTRACT|INTERFACES) -->"
    r".*?"
    r"<!-- END BLUEPRINT (?:CONTRACT|INTERFACES) -->",
    re.DOTALL,
)


def strip_frontmatter(text: str) -> str:
    """Return text without a leading YAML frontmatter block."""
    return _FRONTMATTER_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text, count=1)


def strip_generated_blueprint_blocks(text: str) -> str:
    """Return text without generated blueprint contract/interface blocks."""
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
