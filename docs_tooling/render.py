"""Render generated documentation artifacts and embedded coverage blocks."""
from __future__ import annotations

from pathlib import Path
import re

from .catalog import CATEGORY_DISPLAY, CATEGORY_TREE, COVERAGE_BLOCKS, SKILL_INDEX_PATH, load_catalog, skills_by_category


def begin_marker(marker_id: str) -> str:
    return f"<!-- BEGIN AUTO-GENERATED DOCS: {marker_id} -->"


def end_marker(marker_id: str) -> str:
    return f"<!-- END AUTO-GENERATED DOCS: {marker_id} -->"


def render_coverage_block(repo_root: Path, category: str) -> str:
    catalog = skills_by_category(load_catalog(repo_root))
    skills = catalog.get(category, [])
    lines = [begin_marker(category), "> Generated from live blueprints. Do not edit this block by hand.", ""]
    if skills:
        for skill in skills:
            lines.append(f"- `{skill.name}` — {skill.summary}")
    else:
        lines.append("- No skills currently map to this category.")
    lines.append(end_marker(category))
    return "\n".join(lines)


def _replace_block(text: str, marker_id: str, replacement: str, rel_path: Path) -> str:
    pattern = re.compile(
        rf"{re.escape(begin_marker(marker_id))}.*?{re.escape(end_marker(marker_id))}",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise ValueError(f"{rel_path} is missing marker block {marker_id}")
    return pattern.sub(replacement, text)


def render_doc_with_updated_blocks(repo_root: Path, rel_path: Path) -> str:
    path = repo_root / rel_path
    text = path.read_text(encoding="utf-8")
    for block in COVERAGE_BLOCKS:
        if block.doc_path != rel_path:
            continue
        text = _replace_block(text, block.marker_id, render_coverage_block(repo_root, block.category), rel_path)
    return text


def render_skill_index(repo_root: Path) -> str:
    grouped = skills_by_category(load_catalog(repo_root))
    lines = [
        "# Skill Index",
        "",
        "> Generated from live blueprints and `SKILL.md` descriptions. Do not edit by hand.",
        "",
        "This page is the complete skill inventory. For walkthroughs and examples, start from the user docs or contributor docs linked from [README.md](../README.md).",
        "",
    ]
    for audience, categories in CATEGORY_TREE:
        lines.append(f"## {audience}")
        lines.append("")
        for category in categories:
            section_title = CATEGORY_DISPLAY[category]
            if len(categories) == 1 and section_title == audience:
                section_title = "Skills"
            lines.append(f"### {section_title}")
            lines.append("")
            for skill in grouped.get(category, []):
                lines.append(f"- `{skill.name}` — {skill.summary}")
            if not grouped.get(category):
                lines.append("- No skills currently map to this category.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def generate_all(repo_root: Path) -> list[Path]:
    changed: list[Path] = []
    for doc_path in sorted({block.doc_path for block in COVERAGE_BLOCKS}):
        rendered = render_doc_with_updated_blocks(repo_root, doc_path)
        if _write_if_changed(repo_root / doc_path, rendered):
            changed.append(doc_path)
    skill_index = render_skill_index(repo_root)
    if _write_if_changed(repo_root / SKILL_INDEX_PATH, skill_index):
        changed.append(SKILL_INDEX_PATH)
    return changed
