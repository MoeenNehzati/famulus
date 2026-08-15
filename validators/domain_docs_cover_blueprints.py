"""Validate public domain guides against live blueprint coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from docs_tooling.catalog import (
    COVERAGE_BLOCKS,
    DOMAIN_DOCS,
    SkillInfo,
    load_catalog,
)
from docs_tooling.render import render_doc_with_updated_blocks


@pytest.fixture(scope="module")
def skill_catalog(repo_root: Path) -> list[SkillInfo]:
    """Build the validated skill catalog once per validator module.

    Intent
    ------
    Supply shared schema-validated skill metadata to domain-document checks.

    Rationale
    ---------
    Module scope avoids reconstructing the catalog for each conformance item.

    Pseudocode
    ----------
    - return catalog loaded from staged repository root

    Wraps
    -----
    .docs_tooling.catalog.load_catalog -> preprocess: none; postprocess: none; fixed_arguments: none
    """

    return load_catalog(repo_root)


def _validate_domain_coverage(
    repo_root: Path,
    catalog: list[SkillInfo],
) -> list[str]:
    """Return findings for live domains without documentation mappings.

    Intent
    ------
    Check that every non-contributor domain has a configured domain-doc block.

    Rationale
    ---------
    Live blueprint domains must remain discoverable through generated domain docs.

    Pseudocode
    ----------
    - if catalog and documentation root are both absent:
      - return no findings
    - set covered_domains = domain-document coverage block domains
    - set contributor_domains = contributor-only coverage block domains
    - set live_domains = catalog domains excluding contributor-only domains
    - set missing_domains = live domains absent from covered domains
    - if missing_domains is empty:
      - return no findings
    - return missing-domain finding

    Wraps
    -----
    - none
    """

    if not catalog and not (repo_root / "docs").exists():
        return []
    covered_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path in DOMAIN_DOCS
    }
    contributor_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path not in DOMAIN_DOCS
    }
    live_domains = {
        skill.domain for skill in catalog if skill.domain not in contributor_domains
    }
    missing_domains = sorted(live_domains - covered_domains)
    if not missing_domains:
        return []
    return [
        (
            "docs/domains: missing coverage mapping for domains "
            + ", ".join(missing_domains)
        )
    ]


def _validate_domain_document(
    repo_root: Path,
    catalog: list[SkillInfo],
    rel_path: Path,
) -> list[str]:
    """Return missing, malformed, or stale findings for one domain document.

    Intent
    ------
    Compare one checked-in domain document with its catalog-derived rendering.

    Rationale
    ---------
    Per-document checks provide precise pytest reports while sharing preparation.

    Pseudocode
    ----------
    - if catalog and documentation root are both absent:
      - return no findings
    - if document is missing:
      - return missing-document finding
    - set rendered_document = document rendered from prepared catalog
    - if rendering fails:
      - return rendering finding
    - if checked-in document equals rendered document:
      - return no findings
    - return stale-document finding

    Wraps
    -----
    - none

    """

    if not catalog and not (repo_root / "docs").exists():
        return []
    path = repo_root / rel_path
    if not path.is_file():
        return [f"{rel_path}: missing"]
    try:
        rendered = render_doc_with_updated_blocks(
            repo_root,
            rel_path,
            catalog=catalog,
        )
    except ValueError as exc:
        return [str(exc)]
    actual = path.read_text(encoding="utf-8")
    if actual == rendered:
        return []
    return [
        f"{rel_path}: generated coverage blocks are stale; "
        "run python3 scripts/generate-doc-artifacts.py"
    ]


def test_domain_coverage(
    repo_root: Path,
    skill_catalog: list[SkillInfo],
) -> list[str]:
    """Check that every live user-facing domain has a documentation block.

    Intent
    ------
    Expose the domain mapping contract as one pytest validator item.

    Rationale
    ---------
    A separate item identifies coverage failures independently of stale documents.

    Pseudocode
    ----------
    - return domain coverage findings from prepared catalog

    Wraps
    -----
    ._validate_domain_coverage -> preprocess: none; postprocess: none; fixed_arguments: none
    """

    return _validate_domain_coverage(repo_root, skill_catalog)


@pytest.mark.parametrize(
    "rel_path",
    DOMAIN_DOCS,
    ids=lambda path: path.as_posix(),
)
def test_domain_document(
    repo_root: Path,
    skill_catalog: list[SkillInfo],
    rel_path: Path,
) -> list[str]:
    """Check one domain document against blocks rendered from the shared catalog.

    Intent
    ------
    Expose one document's generated-block contract as a pytest validator item.

    Rationale
    ---------
    Parametrized items identify the exact stale or malformed domain document.

    Pseudocode
    ----------
    - return document findings from staged root prepared catalog and relative path

    Wraps
    -----
    ._validate_domain_document -> preprocess: none; postprocess: none; fixed_arguments: none
    """

    return _validate_domain_document(repo_root, skill_catalog, rel_path)


def validate(repo_root: Path) -> list[str]:
    """Run domain-document contracts for direct non-pytest callers.

    Intent
    ------
    Preserve the historical validator API over the fixture-backed check helpers.

    Rationale
    ---------
    Direct callers need identical findings without initiating a pytest session.

    Pseudocode
    ----------
    - set catalog = catalog loaded once from repository root
    - set errors = domain coverage findings
    - for domain_document in configured domain documents:
      - set errors = errors plus domain document findings
    - return errors

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._validate_domain_coverage:
      why:
        transforms: "Converts prepared catalog domains into coverage findings."
    ._validate_domain_document:
      why:
        transforms: "Converts one document comparison into conformance findings."
    """

    catalog = load_catalog(repo_root)
    errors = _validate_domain_coverage(repo_root, catalog)
    for rel_path in DOMAIN_DOCS:
        errors.extend(_validate_domain_document(repo_root, catalog, rel_path))
    return errors
