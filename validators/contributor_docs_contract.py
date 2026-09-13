"""Validate contributor docs and development-skill coverage."""
from __future__ import annotations

from pathlib import Path

from docs_tooling.catalog import CONTRIBUTOR_DOC, DOC_SYSTEM_DOC
from docs_tooling.render import render_doc_with_updated_blocks


_README_REQUIRED = (
    "blueprint.yaml",
    '"interface":"skill-maker._rtx.interface.sync-blueprints","version":1',
    '"--check":true',
    '"caller":"<caller>","interface":"<callee>.interface.<name>"',
    "repo_checks.py",
    ".githooks/pre-commit",
    "../officina/blueprints.md",
    "references/blueprint-schema/schema.json",
    "references/blueprint-schema/template.yaml",
    "../officina/scaffolding/README.md",
    "documentation-system.md",
)

_DOC_SYSTEM_REQUIRED = (
    "docs_tooling/",
    "python3 scripts/generate-doc-artifacts.py",
    "validators/readme_user_contract.py",
    "validators/domain_docs_cover_blueprints.py",
    "validators/contributor_docs_contract.py",
    "validators/generated_skill_docs.py",
)


def validate(repo_root: Path) -> list[str]:
    """Validate generated contributor documentation and required references.

    Intent
    ------
    Check the contributor guide and documentation-system page as one contract.

    Rationale
    ---------
    Generated coverage and canonical command references must remain synchronized.

    Pseudocode
    ----------
    - set errors = empty finding list
    - if documentation and skills roots are absent:
      - return errors
    - set contributor_findings = generated coverage and required snippet findings
    - set system_findings = documentation-system required snippet findings
    - return errors plus contributor_findings plus system_findings

    Wraps
    -----
    - none

    """
    errors: list[str] = []
    if not (repo_root / "docs").exists() and not (repo_root / "skills").exists():
        return []

    contributor_readme = repo_root / CONTRIBUTOR_DOC
    if not contributor_readme.is_file():
        errors.append(f"{CONTRIBUTOR_DOC}: missing")
    else:
        actual = contributor_readme.read_text(encoding="utf-8")
        try:
            rendered = render_doc_with_updated_blocks(repo_root, CONTRIBUTOR_DOC)
        except ValueError as exc:
            errors.append(str(exc))
            rendered = actual
        if actual != rendered:
            errors.append(
                f"{CONTRIBUTOR_DOC}: generated coverage blocks are stale; run python3 scripts/generate-doc-artifacts.py"
            )
        for snippet in _README_REQUIRED:
            if snippet not in actual:
                errors.append(f"{CONTRIBUTOR_DOC}: missing contributor contract content `{snippet}`")

    doc_system = repo_root / DOC_SYSTEM_DOC
    if not doc_system.is_file():
        errors.append(f"{DOC_SYSTEM_DOC}: missing")
    else:
        text = doc_system.read_text(encoding="utf-8")
        for snippet in _DOC_SYSTEM_REQUIRED:
            if snippet not in text:
                errors.append(f"{DOC_SYSTEM_DOC}: missing documentation-system content `{snippet}`")

    return errors
