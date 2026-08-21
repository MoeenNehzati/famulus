"""Render generated documentation artifacts and embedded coverage blocks."""
from __future__ import annotations

from pathlib import Path
import re

from .catalog import (
    COVERAGE_BLOCKS,
    SKILL_INDEX_PATH,
    SkillInfo,
    configured_domains,
    configured_visibilities,
    load_catalog,
    skills_by_domain,
)


def begin_marker(marker_id: str) -> str:
    """Return the opening generated-document marker for an identifier.

    Intent
    ------
    Centralize the exact opening delimiter used by generators and validators.

    Rationale
    ---------
    Producers and replacement logic must agree on one marker spelling.

    Pseudocode
    ----------
    - return formatted opening marker

    Wraps
    -----
    - none
    """

    return f"<!-- BEGIN AUTO-GENERATED DOCS: {marker_id} -->"


def end_marker(marker_id: str) -> str:
    """Return the closing generated-document marker for an identifier.

    Intent
    ------
    Centralize the exact closing delimiter used by generators and validators.

    Rationale
    ---------
    Producers and replacement logic must agree on one marker spelling.

    Pseudocode
    ----------
    - return formatted closing marker

    Wraps
    -----
    - none
    """

    return f"<!-- END AUTO-GENERATED DOCS: {marker_id} -->"


def render_coverage_block(
    repo_root: Path,
    domain: str,
    *,
    catalog: list[SkillInfo] | None = None,
) -> str:
    """Render one domain coverage block from prepared or freshly loaded data.

    Intent
    ------
    Produce the canonical Markdown inventory for one documentation domain.

    Rationale
    ---------
    Accepting prepared data lets callers reuse one validated catalog safely.

    Pseudocode
    ----------
    - set prepared_catalog = supplied catalog or catalog loaded from repository
    - set grouped_skills = visible skills grouped by domain
    - set lines = opening marker provenance notice and blank line
    - if domain has skills:
      - for skill in domain skills:
        - set lines = lines plus skill summary
    - else:
      - set lines = lines plus empty-domain notice
    - set lines = lines plus closing marker
    - return joined lines

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .catalog.load_catalog:
      why:
        reads: "Loads catalog data when the caller did not prepare it."

    InstantiationsFromRepo
    ----------------------
    .catalog.skills_by_domain:
      why:
        constructs: "Groups visible skills for domain-specific rendering."
    .begin_marker:
      why:
        constructs: "Builds the opening delimiter for the rendered domain."
    .end_marker:
      why:
        constructs: "Builds the closing delimiter for the rendered domain."
    """

    grouped = skills_by_domain(
        load_catalog(repo_root) if catalog is None else catalog
    )
    skills = grouped.get(domain, [])
    lines = [begin_marker(domain), "> Generated from live blueprints. Do not edit this block by hand.", ""]
    if skills:
        for skill in skills:
            lines.append(f"- `{skill.name}` — {skill.summary}")
    else:
        lines.append("- No skills currently map to this domain.")
    lines.append(end_marker(domain))
    return "\n".join(lines)


def _replace_block(text: str, marker_id: str, replacement: str, rel_path: Path) -> str:
    """Replace one complete generated block or reject a missing marker pair.

    Intent
    ------
    Update only the bounded generated region identified by a domain marker.

    Rationale
    ---------
    Failing on missing markers prevents generators from rewriting authored prose.

    Pseudocode
    ----------
    - set marker_pattern = escaped opening through closing marker expression
    - if marker_pattern is absent from text:
      - raise missing marker error
    - return text with marker region replaced

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .begin_marker:
      why:
        computes: "Provides the exact opening marker matched by the expression."
    .end_marker:
      why:
        computes: "Provides the exact closing marker matched by the expression."
    """

    pattern = re.compile(
        rf"{re.escape(begin_marker(marker_id))}.*?{re.escape(end_marker(marker_id))}",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise ValueError(f"{rel_path} is missing marker block {marker_id}")
    return pattern.sub(replacement, text)


def render_doc_with_updated_blocks(
    repo_root: Path,
    rel_path: Path,
    *,
    catalog: list[SkillInfo] | None = None,
) -> str:
    """Render every configured coverage block in one documentation file.

    Intent
    ------
    Produce an expected document using one consistent skill catalog.

    Rationale
    ---------
    Loading at this top-level boundary prevents per-block schema reconstruction.

    Pseudocode
    ----------
    - set prepared_catalog = supplied catalog or catalog loaded from repository
    - set text = current document text
    - for coverage_block in configured coverage blocks:
      - if coverage_block belongs to another document:
        - continue
      - set replacement = rendered block from prepared catalog
      - set text = text with bounded block replaced
    - return text

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .catalog.load_catalog:
      why:
        reads: "Loads catalog data when the caller did not prepare it."
    .render_coverage_block:
      why:
        computes: "Renders each replacement block from shared catalog data."

    InstantiationsFromRepo
    ----------------------
    ._replace_block:
      why:
        constructs: "Replaces one bounded generated region in the document."
    """

    prepared = load_catalog(repo_root) if catalog is None else catalog
    path = repo_root / rel_path
    text = path.read_text(encoding="utf-8")
    for block in COVERAGE_BLOCKS:
        if block.doc_path != rel_path:
            continue
        text = _replace_block(
            text,
            block.marker_id,
            render_coverage_block(
                repo_root,
                block.domain,
                catalog=prepared,
            ),
            rel_path,
        )
    return text


def render_skill_index(repo_root: Path) -> str:
    """Render the complete skill index in configured catalog order.

    Intent
    ------
    Produce the canonical generated inventory of visible repository skills.

    Rationale
    ---------
    Central rendering keeps domain visibility topic and activation views aligned.

    Pseudocode
    ----------
    - set catalog = catalog loaded from repository
    - set grouped_skills = visible skills grouped by domain
    - set lines = skill index introduction
    - for domain in configured domain order:
      - if domain has no visible skills:
        - continue
      - set lines = lines plus domain heading
      - for visibility in configured visibility order:
        - if visibility is hidden or has no skills:
          - continue
        - set lines = lines plus visibility heading and skill details
    - return normalized joined lines

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .catalog.load_catalog:
      why:
        reads: "Loads validated skill metadata for the complete index."
    .catalog.configured_domains:
      why:
        reads: "Provides canonical domain display order."
    .catalog.configured_visibilities:
      why:
        reads: "Provides canonical visibility display order."

    InstantiationsFromRepo
    ----------------------
    .catalog.skills_by_domain:
      why:
        constructs: "Groups visible skills under their configured domains."
    """

    catalog = load_catalog(repo_root)
    grouped = skills_by_domain(catalog)
    lines = [
        "# Skill Index",
        "",
        "> Generated from live blueprints and `SKILL.md` descriptions. Do not edit by hand.",
        "",
        "This page is the complete skill inventory. For workflows and examples, start from the quickstarts, domain guides, or contributor docs linked from [README.md](../README.md).",
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
    """Write generated text only when its target content differs.

    Intent
    ------
    Avoid unnecessary filesystem mutations during deterministic generation.

    Rationale
    ---------
    Stable modification times reduce noisy diffs and redundant downstream work.

    Pseudocode
    ----------
    - if target exists and target text equals generated text:
      - return false
    - set target_contents = generated text persisted at target
    - return true

    Wraps
    -----
    - none
    """

    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def generate_all(repo_root: Path) -> list[Path]:
    """Regenerate documentation coverage blocks and the complete skill index.

    Intent
    ------
    Synchronize all catalog-derived Markdown artifacts in one command.

    Rationale
    ---------
    Returning changed paths lets hooks stage only artifacts whose bytes changed.

    Pseudocode
    ----------
    - set changed_paths = empty list
    - for document_path in unique configured coverage documents:
      - set rendered_document = document with refreshed coverage blocks
      - if rendered document changes target:
        - set changed_paths = changed_paths plus document path
    - set rendered_index = complete rendered skill index
    - if rendered index changes target:
      - set changed_paths = changed_paths plus skill index path
    - return changed_paths

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .render_doc_with_updated_blocks:
      why:
        constructs: "Builds refreshed coverage documents."
    .render_skill_index:
      why:
        constructs: "Builds the complete generated skill inventory."

    CallsFromRepo
    -------------
    ._write_if_changed:
      why:
        writes: "Persists only generated artifacts whose bytes changed."
    """

    changed: list[Path] = []
    for doc_path in sorted({block.doc_path for block in COVERAGE_BLOCKS}):
        rendered = render_doc_with_updated_blocks(repo_root, doc_path)
        if _write_if_changed(repo_root / doc_path, rendered):
            changed.append(doc_path)
    skill_index = render_skill_index(repo_root)
    if _write_if_changed(repo_root / SKILL_INDEX_PATH, skill_index):
        changed.append(SKILL_INDEX_PATH)
    return changed
