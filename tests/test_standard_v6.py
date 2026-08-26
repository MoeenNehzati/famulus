import importlib.util
import hashlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_module("standard_v6_renderer", "references/standards-schema/render_standard_v6.py")
validator = load_module("standard_v6_validator", "references/standards-schema/validate_standard_v6.py")
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


def test_schema_accepts_applicability_on_semantic_items() -> None:
    value = document()
    value["domain_facts"] = {"gateway.language": "python"}
    diagnostic_family = value["standards"][0]
    diagnostic_family["applies_when"] = {
        "fact": "gateway.language",
        "equals": "python",
    }
    diagnostic_family["children"].append(
        {
            "kind": "guidance",
            "id": "skill-refactoring.guidance.python-scope",
            "statement": "Apply this guidance to Python gateways.",
            "applies_when": {"fact": "gateway.language", "equals": "python"},
        }
    )
    diagnostic_family["children"].append(
        {
            "kind": "rule",
            "id": "skill-refactoring.rules.small-scope",
            "applies_when": {"fact": "gateway.language", "equals": "python"},
            "assertions": [
                {
                    "id": "preserve-contract",
                    "modality": "required",
                    "statement": "Preserve the selected scope's observable contract.",
                    "applies_when": {
                        "fact": "gateway.language",
                        "equals": "python",
                    },
                }
            ],
        }
    )
    value["standards"][1]["children"][0]["applies_when"] = {
        "fact": "gateway.language",
        "equals": "python",
    }

    assert validate_document(value, ROOT) == []


def test_schema_rejects_non_scalar_domain_facts() -> None:
    value = document()
    value["domain_facts"] = {"gateway.language": ["python"]}

    errors = validate_document(value, ROOT)

    assert any("schema validation failed" in error for error in errors)


@pytest.mark.parametrize(
    "predicate",
    [
        {"fact": "gateway.language", "equals": {"name": "python"}},
        {"fact": "gateway.language", "in": ["python", ["markdown"]]},
    ],
)
def test_schema_rejects_non_scalar_predicate_values(predicate: dict) -> None:
    value = document()
    value["standards"][0]["applies_when"] = predicate

    errors = validate_document(value, ROOT)

    assert any("schema validation failed" in error for error in errors)


def test_applicability_predicates_use_three_valued_logic() -> None:
    facts = {"gateway.language": "python", "node.public": True}

    assert validator.evaluate_predicate(
        {"fact": "gateway.language", "equals": "python"}, facts
    ) == "true"
    assert validator.evaluate_predicate(
        {"fact": "gateway.language", "equals": "markdown"}, facts
    ) == "false"
    assert validator.evaluate_predicate(
        {"fact": "python.uses-inheritance", "equals": True}, facts
    ) == "unknown"
    assert validator.evaluate_predicate(
        {
            "all": [
                {"fact": "node.public", "equals": True},
                {"fact": "python.uses-inheritance", "equals": True},
            ]
        },
        facts,
    ) == "unknown"
    assert validator.evaluate_predicate(
        {
            "any": [
                {"fact": "gateway.language", "equals": "python"},
                {"fact": "python.uses-inheritance", "equals": True},
            ]
        },
        facts,
    ) == "true"
    assert validator.evaluate_predicate(
        {"not": {"fact": "python.uses-inheritance", "equals": True}}, facts
    ) == "unknown"
    assert validator.evaluate_predicate(
        {"fact": "python.version", "equals": 1.0}, {"python.version": 1}
    ) == "false"


def test_remedied_by_accepts_assertion_noncompliance_source() -> None:
    value = document()
    value["standards"][0]["children"].append(
        {
            "kind": "rule",
            "id": "skill-refactoring.rules.small-scope",
            "assertions": [
                {
                    "id": "preserve-contract",
                    "modality": "required",
                    "statement": "Preserve the selected scope's observable contract.",
                }
            ],
        }
    )
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "assertion",
        "ref": "skill-refactoring.rules.small-scope#preserve-contract",
    }

    assert validate_document(value, ROOT) == []


def test_remedied_by_rejects_non_diagnostic_source_kind() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "definition",
        "ref": "skill-refactoring.smells.bloated-skill.signal",
    }

    errors = validate_document(value, ROOT)

    assert any("source must be a family, rule, or assertion" in error for error in errors)


def test_remedied_by_target_must_be_a_procedure() -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["target"] = {
        "kind": "family",
        "ref": "skill-refactoring.smells.bloated-skill",
    }

    errors = validate_document(value, ROOT)

    assert any("target must be a procedure" in error for error in errors)


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
        "path": "references/standards-schema/missing.standard.yaml",
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
        "path": str(Path(tmp_path.anchor) / "outside.standard.yaml"),
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


def test_validate_file_cache_findings_depend_on_traversal_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    a_path = root / "references" / "standards-schema" / "a.standard.yaml"
    b_path = root / "references" / "standards-schema" / "b.standard.yaml"
    a = document()
    a["id"] = "a"
    a["canonical_path"] = "references/standards-schema/a.standard.yaml"
    b = document()
    b["id"] = "b"
    b["canonical_path"] = "references/standards-schema/b.standard.yaml"
    for value, imported_id, imported_path in (
        (a, "b", "references/standards-schema/b.standard.yaml"),
        (b, "a", "references/standards-schema/a.standard.yaml"),
    ):
        value["artifacts"]["imported-standard"] = {
            "path": imported_path,
            "format": "yaml",
            "roles": ["other"],
        }
        value["imports"] = {
            "imported": {
                "standard_id": imported_id,
                "standard_version": "1.0.0",
                "revision": 1,
                "digest": "sha256:" + "0" * 64,
                "artifact": {"kind": "artifact", "ref": "imported-standard"},
            }
        }
    _write_standard(a_path, a)
    _write_standard(b_path, b)

    class ZeroDigest:
        def hexdigest(self) -> str:
            return "0" * 64

    class HashlibDouble:
        @staticmethod
        def sha256(_: bytes) -> ZeroDigest:
            return ZeroDigest()

    monkeypatch.setattr(validator, "hashlib", HashlibDouble)

    fresh_a = validate_file(a_path, root=root)
    fresh_b = validate_file(b_path, root=root)
    shared: dict[Path, tuple[dict, list[str]]] = {}
    validate_file(a_path, root=root, cache=shared)
    cached_b = validate_file(b_path, root=root, cache=shared)

    assert any("cycle" in error for error in fresh_a)
    assert any("cycle" in error for error in fresh_b)
    assert cached_b != fresh_b


def test_validate_file_preserves_best_schema_error(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    value = document()
    value["standards"][0]["children"] = []
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    # jsonschema has emitted both "is too short" and "should be non-empty"
    # for this minItems failure. The stable contract is that validation keeps
    # the specific empty-children finding instead of a less relevant error.
    assert len(errors) == 1
    assert errors[0].startswith("schema validation failed: [] ")


def _add_import(value: dict, artifact_path: str, imported_path: Path) -> None:
    value["artifacts"]["imported-standard"] = {
        "path": artifact_path,
        "format": "yaml",
        "roles": ["other"],
    }
    imported = document()
    imported["id"] = "imported-standard"
    imported["canonical_path"] = "references/standards-schema/imported.standard.yaml"
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


def _set_imported_domain(
    value: dict,
    imported_path: Path,
    domain_facts: dict,
) -> None:
    imported = yaml.safe_load(imported_path.read_text(encoding="utf-8"))
    imported["domain_facts"] = domain_facts
    _write_standard(imported_path, imported)
    value["imports"]["imported"]["digest"] = (
        "sha256:" + hashlib.sha256(imported_path.read_bytes()).hexdigest()
    )


def test_update_standards_acceptance_cascades_pins_evidence_and_view(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    base_path = root / "references" / "standards-schema" / "base.standard.yaml"
    dependent_path = root / "references" / "standards-schema" / "dependent.standard.yaml"
    source_path = root / "evidence" / "policy.md"
    view_path = root / "references" / "standards-schema" / "base.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("policy v1\n", encoding="utf-8")

    base = document()
    base["id"] = "base"
    base["canonical_path"] = "references/standards-schema/base.standard.yaml"
    base["artifacts"]["policy-source"] = {
        "path": "evidence/policy.md",
        "format": "markdown",
        "roles": ["documentation"],
    }
    base["sources"] = {
        "policy": {
            "artifact": {"kind": "artifact", "ref": "policy-source"},
            "revision": "v1",
            "digest": "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest(),
        }
    }
    _write_standard(base_path, base)
    view_path.write_text(render_document(base), encoding="utf-8")

    dependent = document()
    dependent["id"] = "dependent"
    dependent["canonical_path"] = "references/standards-schema/dependent.standard.yaml"
    dependent["artifacts"]["base-standard"] = {
        "path": "references/standards-schema/base.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }
    dependent["imports"] = {
        "base": {
            "standard_id": "base",
            "standard_version": base["standard_version"],
            "revision": base["revision"],
            "digest": "sha256:" + hashlib.sha256(base_path.read_bytes()).hexdigest(),
            "artifact": {"kind": "artifact", "ref": "base-standard"},
        }
    }
    _write_standard(dependent_path, dependent)

    assert validate_file(base_path, root=root) == []
    assert validate_file(dependent_path, root=root) == []

    source_path.write_text("policy v2\n", encoding="utf-8")
    base["revision"] = 2
    base["purpose"] = "Diagnose skill smells under the revised policy."
    _write_standard(base_path, base)

    assert any(
        "sources.policy" in error and "digest" in error
        for error in validate_file(base_path, root=root)
    )
    assert any(
        "imports.base" in error and "digest mismatch" in error
        for error in validate_file(dependent_path, root=root)
    )
    assert view_path.read_text(encoding="utf-8") != render_document(base)

    base["sources"]["policy"]["revision"] = "v2"
    base["sources"]["policy"]["digest"] = (
        "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    )
    _write_standard(base_path, base)
    dependent["revision"] = 2
    dependent["imports"]["base"]["revision"] = base["revision"]
    dependent["imports"]["base"]["digest"] = (
        "sha256:" + hashlib.sha256(base_path.read_bytes()).hexdigest()
    )
    _write_standard(dependent_path, dependent)
    view_path.write_text(render_document(base), encoding="utf-8")

    assert validate_file(base_path, root=root) == []
    assert validate_file(dependent_path, root=root) == []
    assert view_path.read_text(encoding="utf-8") == render_document(base)


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


def test_validate_file_accepts_import_domain_subset(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = root / "standards" / "imported.standard.yaml"
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    _set_imported_domain(
        value,
        imported_path,
        {"node.present": True, "gateway.language": "python"},
    )
    value["domain_facts"] = {
        "node.present": True,
        "gateway.language": "python",
        "node.structure": "module",
    }
    _write_standard(standard, value)

    assert validate_file(standard, root=root) == []


def test_validate_file_rejects_missing_import_domain_fact(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = root / "standards" / "imported.standard.yaml"
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    _set_imported_domain(value, imported_path, {"node.present": True})
    value["domain_facts"] = {"gateway.language": "python"}
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any(
        "imports.imported.domain_facts: missing inherited fact node.present" in error
        for error in errors
    )


def test_validate_file_rejects_conflicting_import_domain_fact(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = root / "standards" / "imported.standard.yaml"
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    _set_imported_domain(value, imported_path, {"gateway.language": "python"})
    value["domain_facts"] = {"gateway.language": "markdown"}
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any(
        "imports.imported.domain_facts: conflicting fact gateway.language" in error
        for error in errors
    )


def test_validate_file_rejects_numeric_import_domain_type_change(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    imported_path = root / "standards" / "imported.standard.yaml"
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    _set_imported_domain(value, imported_path, {"python.version": 1})
    value["domain_facts"] = {"python.version": 1.0}
    _write_standard(standard, value)

    errors = validate_file(standard, root=root)

    assert any(
        "imports.imported.domain_facts: conflicting fact python.version" in error
        for error in errors
    )


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
