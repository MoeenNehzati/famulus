"""Validate contributor docs and development-skill coverage."""
from __future__ import annotations

from pathlib import Path

from docs_tooling.catalog import CONTRIBUTOR_DOC, DOC_SYSTEM_DOC
from docs_tooling.render import render_doc_with_updated_blocks


_README_REQUIRED = (
    "blueprint.yaml",
    "python3 skills/skill-maker/scripts/sync_skill_blueprints.py",
    "dispatcher --caller-skill <caller> <callee> <interface-id> [args...]",
    "validators/runner.py",
    ".githooks/pre-commit",
    "references/blueprint/guide.md",
    "references/blueprint/schema.json",
    "references/blueprint/template.yaml",
    "docs/scaffolding/README.md",
    "docs/contributors/documentation-system.md",
)

_DOC_SYSTEM_REQUIRED = (
    "docs_tooling/",
    "python3 scripts/generate-doc-artifacts.py",
    "validators/readme_user_contract.py",
    "validators/user_docs_cover_blueprints.py",
    "validators/contributor_docs_contract.py",
    "validators/generated_skill_docs.py",
)


def validate(repo_root: Path) -> list[str]:
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
