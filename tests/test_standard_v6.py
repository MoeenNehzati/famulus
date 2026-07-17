import importlib.util
import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_module("standard_v6_renderer", "references/standards/render_standard_v6.py")
validator = load_module("standard_v6_validator", "references/standards/validate_standard_v6.py")
render_document = renderer.render_document
validate_document = validator.validate_document
validate_file = validator.validate_file


def document() -> dict:
    return {
        "schema_version": 6,
        "standard_version": "1.0.0",
        "revision": 1,
        "id": "skill-refactoring",
        "canonical_path": "references/skill-standards/skill-refactoring.standard.yaml",
        "title": "Skill Refactoring",
        "purpose": "Diagnose skill smells and identify applicable remedies.",
        "standards": [
            {
                "kind": "family",
                "id": "skill-refactoring.diagnostic-signals",
                "title": "Diagnostic signals",
                "summary": "Observable signs that a skill may need refactoring.",
                "rationale": "Signals organize diagnosis without prescribing a remedy.",
                "children": [
                    {
                        "kind": "family",
                        "id": "skill-refactoring.smells.bloated-skill",
                        "title": "Bloated skill",
                        "children": [
                            {
                                "kind": "definition",
                                "id": "skill-refactoring.smells.bloated-skill.signal",
                                "term": "Signal",
                                "meaning": "The skill mixes several responsibilities.",
                            }
                        ],
                    }
                ],
            },
            {
                "kind": "family",
                "id": "skill-refactoring.refactoring-moves",
                "title": "Refactoring moves",
                "children": [
                    {
                        "kind": "procedure",
                        "id": "skill-refactoring.remedies.extract-script",
                        "title": "Extract script",
                        "summary": "Move executable logic into a dedicated script.",
                        "ordered": True,
                        "steps": [
                            {
                                "kind": "step",
                                "id": "move-logic",
                                "instruction": "Move executable logic into a script.",
                                "rationale": "Keep the instruction document concise.",
                                "verification": "Run the script's focused tests.",
                            }
                        ],
                        "invariants": [
                            {"id": "preserve-behavior", "statement": "Preserve observable behavior."}
                        ],
                        "completion_conditions": [
                            {"id": "tests-pass", "statement": "Focused tests pass."}
                        ],
                        "risk": {"level": "medium", "statement": "Moving logic can change interfaces."},
                    }
                ],
            },
        ],
        "artifacts": {},
        "links": {
            "link.bloated-skill.extract-script": {
                "source": {
                    "kind": "family",
                    "ref": "skill-refactoring.smells.bloated-skill",
                },
                "relation": "remedied-by",
                "target": {
                    "kind": "procedure",
                    "ref": "skill-refactoring.remedies.extract-script",
                },
                "lifecycle": "active",
                "resolution": {"state": "resolved"},
            }
        },
    }


def test_schema_accepts_remedied_by() -> None:
    assert validate_document(document(), ROOT) == []


def test_remedied_by_source_must_descend_from_diagnostic_signals() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "family",
        "ref": "skill-refactoring.remedies.extract-script",
    }

    errors = validate_document(value, ROOT)

    assert any("diagnostic-signals" in error for error in errors)


def test_remedied_by_target_must_descend_from_refactoring_moves() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["target"] = {
        "kind": "family",
        "ref": "skill-refactoring.smells.bloated-skill",
    }

    errors = validate_document(value, ROOT)

    assert any("refactoring-moves" in error for error in errors)


def test_remedied_by_source_must_be_a_smell_family() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "definition",
        "ref": "skill-refactoring.smells.bloated-skill.signal",
    }

    errors = validate_document(value, ROOT)

    assert any("source must be a family" in error for error in errors)


def test_remedied_by_target_must_be_a_family_or_procedure() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["target"] = {
        "kind": "step",
        "ref": "skill-refactoring.remedies.extract-script#move-logic",
    }

    errors = validate_document(value, ROOT)

    assert any("target must be a family or procedure" in error for error in errors)


def test_renderer_labels_forward_and_inverse_remedy_links() -> None:
    rendered = render_document(document())

    assert "Remedies: Extract script" in rendered
    assert "Addresses: Bloated skill" in rendered


def test_renderer_preserves_readable_normative_content() -> None:
    rendered = render_document(document())

    assert rendered.startswith(
        "<!-- Generated from references/skill-standards/skill-refactoring.standard.yaml; do not edit. -->"
    )
    assert "Observable signs that a skill may need refactoring." in rendered
    assert "Signals organize diagnosis without prescribing a remedy." in rendered
    assert "Move executable logic into a dedicated script." in rendered
    assert "1. Move executable logic into a script." in rendered
    assert "Rationale: Keep the instruction document concise." in rendered
    assert "Verification: Run the script's focused tests." in rendered
    assert "Preserve observable behavior." in rendered
    assert "Focused tests pass." in rendered
    assert "**Risk (medium):** Moving logic can change interfaces." in rendered


def test_renderer_is_deterministic_and_matches_representative_golden() -> None:
    first = render_document(document())
    second = render_document(document())

    assert first == second
    assert first == """<!-- Generated from references/skill-standards/skill-refactoring.standard.yaml; do not edit. -->

# Skill Refactoring

Diagnose skill smells and identify applicable remedies.

## Diagnostic signals

Observable signs that a skill may need refactoring.

**Rationale:** Signals organize diagnosis without prescribing a remedy.

### Bloated skill

#### Signal

The skill mixes several responsibilities.

## Refactoring moves

### Extract script

Move executable logic into a dedicated script.

**Steps**

1. Move executable logic into a script.
   - Rationale: Keep the instruction document concise.
   - Verification: Run the script's focused tests.

**Invariants**
- Preserve observable behavior.

**Completion conditions**
- Focused tests pass.

**Risk (medium):** Moving logic can change interfaces.

## Remedy relationships
- Bloated skill
  - Remedies: Extract script
- Extract script
  - Addresses: Bloated skill
"""


def test_validate_document_uses_root_to_resolve_imports(tmp_path: Path) -> None:
    value = document()
    value["imports"] = {
        "guidelines": {
            "standard_id": "missing",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "missing-standard"},
        }
    }
    value["artifacts"]["missing-standard"] = {
        "path": "references/standards/missing.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }

    errors = validate_document(value, tmp_path)

    assert any("imports.guidelines: missing import file under root" in error for error in errors)


def test_validate_document_rejects_absolute_import_artifact(tmp_path: Path) -> None:
    value = document()
    value["imports"] = {
        "guidelines": {
            "standard_id": "missing",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "outside"},
        }
    }
    value["artifacts"]["outside"] = {
        "path": "/tmp/outside.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }

    errors = validate_document(value, tmp_path)

    assert any("imports.guidelines: import path must be repository-relative" in error for error in errors)


def test_validate_document_rejects_import_escape(tmp_path: Path) -> None:
    value = document()
    value["imports"] = {
        "guidelines": {
            "standard_id": "missing",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "outside"},
        }
    }
    value["artifacts"]["outside"] = {
        "path": "../outside.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }

    errors = validate_document(value, tmp_path)

    assert any("imports.guidelines: import path escapes repository root" in error for error in errors)


def _write_standard(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def _add_import(value: dict, artifact_path: str, imported_path: Path) -> None:
    value["artifacts"]["imported-standard"] = {
        "path": artifact_path,
        "format": "yaml",
        "roles": ["other"],
    }
    imported = document()
    imported["id"] = "imported-standard"
    imported["canonical_path"] = "references/standards/imported.standard.yaml"
    _write_standard(imported_path, imported)
    value["imports"] = {
        "imported": {
            "standard_id": "imported-standard",
            "standard_version": imported["standard_version"],
            "revision": imported["revision"],
            "digest": "sha256:" + hashlib.sha256(imported_path.read_bytes()).hexdigest(),
            "artifact": {"kind": "artifact", "ref": "imported-standard"},
        }
    }


def test_validate_file_accepts_in_root_import_and_source_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = root / "standards" / "imported.standard.yaml"
    source_path = root / "sources" / "original.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("original\n")
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    value["artifacts"]["original-source"] = {
        "path": "sources/original.md",
        "format": "markdown",
        "roles": ["documentation"],
    }
    value["sources"] = {
        "original": {
            "artifact": {"kind": "artifact", "ref": "original-source"},
            "revision": "migration-input",
            "digest": "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest(),
        }
    }
    _write_standard(standard, value)

    assert validate_file(standard, root=root) == []


def test_validate_file_rejects_absolute_import_artifact(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = tmp_path / "outside.standard.yaml"
    value = document()
    _add_import(value, str(imported_path), imported_path)
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any("imports.imported: artifact path must be repository-relative" in error for error in errors)


def test_validate_file_rejects_import_artifact_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = tmp_path / "outside.standard.yaml"
    value = document()
    _add_import(value, "../outside.standard.yaml", imported_path)
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any("imports.imported: artifact path escapes repository root" in error for error in errors)


def test_validate_file_rejects_source_artifact_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    linked = root / "sources" / "linked.md"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    value = document()
    value["artifacts"]["linked-source"] = {
        "path": "sources/linked.md",
        "format": "markdown",
        "roles": ["documentation"],
    }
    value["sources"] = {
        "linked": {
            "artifact": {"kind": "artifact", "ref": "linked-source"},
            "revision": "migration-input",
            "digest": "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest(),
        }
    }
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any("sources.linked: artifact path escapes repository root" in error for error in errors)
