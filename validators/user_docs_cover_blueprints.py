"""Validate user docs against live non-development blueprint coverage."""
from __future__ import annotations

from pathlib import Path

from docs_tooling.catalog import COVERAGE_BLOCKS, USER_DOCS, load_catalog
from docs_tooling.render import render_doc_with_updated_blocks


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    catalog = load_catalog(repo_root)
    if not catalog and not (repo_root / "docs").exists():
        return []
    covered_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path in USER_DOCS
    }
    contributor_domains = {
        block.domain for block in COVERAGE_BLOCKS if block.doc_path not in USER_DOCS
    }
    live_domains = {
        skill.domain for skill in catalog if skill.domain not in contributor_domains
    }
    missing_domains = sorted(live_domains - covered_domains)
    if missing_domains:
        errors.append(
            "docs/user: missing coverage mapping for domains "
            + ", ".join(missing_domains)
        )

    for rel_path in USER_DOCS:
        path = repo_root / rel_path
        if not path.is_file():
            errors.append(f"{rel_path}: missing")
            continue
        try:
            rendered = render_doc_with_updated_blocks(repo_root, rel_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != rendered:
            errors.append(f"{rel_path}: generated coverage blocks are stale; run python3 scripts/generate-doc-artifacts.py")
    return errors
