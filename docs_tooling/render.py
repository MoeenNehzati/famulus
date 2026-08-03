"""Render generated documentation artifacts and embedded coverage blocks."""
from __future__ import annotations

from pathlib import Path
import re

from .catalog import (
    COVERAGE_BLOCKS,
    SKILL_INDEX_PATH,
    configured_domains,
    configured_visibilities,
    load_catalog,
    skills_by_domain,
)


def begin_marker(marker_id: str) -> str:
    return f"<!-- BEGIN AUTO-GENERATED DOCS: {marker_id} -->"


def end_marker(marker_id: str) -> str:
    return f"<!-- END AUTO-GENERATED DOCS: {marker_id} -->"


def render_coverage_block(repo_root: Path, domain: str) -> str:
    catalog = skills_by_domain(load_catalog(repo_root))
    skills = catalog.get(domain, [])
    lines = [begin_marker(domain), "> Generated from live blueprints. Do not edit this block by hand.", ""]
    if skills:
        for skill in skills:
            lines.append(f"- `{skill.name}` — {skill.summary}")
    else:
        lines.append("- No skills currently map to this domain.")
    lines.append(end_marker(domain))
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
        text = _replace_block(text, block.marker_id, render_coverage_block(repo_root, block.domain), rel_path)
    return text


def render_skill_index(repo_root: Path) -> str:
    catalog = load_catalog(repo_root)
    grouped = skills_by_domain(catalog)
    lines = [
        "# Skill Index",
        "",
        "> Generated from live blueprints and `SKILL.md` descriptions. Do not edit by hand.",
        "",
        "This page is the complete skill inventory. For walkthroughs and examples, start from the user docs or contributor docs linked from [README.md](../README.md).",
        "",
        "![Skill taxonomy](graphs/skill-taxonomy.svg)",
        "",
        "The graph gives a visual overview of the live skill set. The sections below are the complete text inventory.",
        "",
    ]
    for domain in configured_domains(repo_root, catalog):
        skills = grouped.get(domain, [])
        if not skills:
            continue
        lines.append(f"## {domain.replace('-', ' ').title()}")
        lines.append("")
        for visibility in configured_visibilities(repo_root):
            if visibility == "hidden":
                continue
            selected = [skill for skill in skills if skill.visibility == visibility]
            if not selected:
                continue
            lines.append(f"### {visibility.title()}")
            lines.append("")
            for skill in selected:
                topics = ", ".join(skill.topics)
                activation = ", ".join(
                    value.replace("-", " ") for value in skill.activated_by
                )
                modifier = (
                    "; persistent modifier" if skill.persistent_modifier else ""
                )
                lines.append(
                    f"- `{skill.name}` — {skill.summary} "
                    f"_(topics: {topics}; activated by: {activation}{modifier})_"
                )
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
