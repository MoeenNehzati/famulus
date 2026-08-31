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


renderer = load_module(
    "standard_v6_renderer", "references/standards-schema/render_standard_v6.py"
)
validator = load_module(
    "standard_v6_validator", "references/standards-schema/validate_standard_v6.py"
)
render_document = renderer.render_document
validate_document = validator.validate_document
validate_file = validator.validate_file


@pytest.fixture(scope="module")
def prepared_schema_validator():
    return validator._prepare_schema_validator()


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
                            {
                                "id": "preserve-behavior",
                                "statement": "Preserve observable behavior.",
                            }
                        ],
                        "completion_conditions": [
                            {"id": "tests-pass", "statement": "Focused tests pass."}
                        ],
                        "risk": {
                            "level": "medium",
                            "statement": "Moving logic can change interfaces.",
                        },
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


def test_schema_accepts_applicability_and_assertion_remedy_source() -> None:
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
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "assertion",
        "ref": "skill-refactoring.rules.small-scope#preserve-contract",
    }

    assert validate_document(value, ROOT) == []


def test_schema_rejects_non_scalar_values(
    monkeypatch: pytest.MonkeyPatch, prepared_schema_validator
) -> None:
    monkeypatch.setattr(
        validator,
        "_prepare_schema_validator",
        lambda: pytest.fail("injected validation must not prepare the schema again"),
    )
    scenarios = (
        ("domain fact", {"gateway.language": ["python"]}, None),
        (
            "equals predicate",
            None,
            {"fact": "gateway.language", "equals": {"name": "python"}},
        ),
        (
            "in predicate",
            None,
            {"fact": "gateway.language", "in": ["python", ["markdown"]]},
        ),
    )
    for label, domain_facts, predicate in scenarios:
        value = document()
        if domain_facts is not None:
            value["domain_facts"] = domain_facts
        else:
            value["standards"][0]["applies_when"] = predicate

        errors = validate_document(
            value, ROOT, _schema_validator=prepared_schema_validator
        )

        assert any("schema validation failed" in error for error in errors), label


def test_schema_removes_conversion_audit_fields_but_retains_derived_origins(
    prepared_schema_validator,
) -> None:
    for field in ("sources", "source_units", "migration"):
        value = document()
        value[field] = {}

        errors = validate_document(value, ROOT, _schema_validator=prepared_schema_validator)
        assert any("schema validation failed" in error for error in errors), field
    value = document()
    value["standards"][0]["origin"] = {
        "kind": "imported",
        "source_units": [{"kind": "source-unit", "ref": "legacy"}],
    }
    errors = validate_document(value, ROOT, _schema_validator=prepared_schema_validator)
    assert any("schema validation failed" in error for error in errors)
    value["standards"][0]["origin"] = {
        "kind": "derived",
        "derived_from": [{"kind": "family", "ref": "skill-refactoring.refactoring-moves"}],
    }
    assert validate_document(value, ROOT, _schema_validator=prepared_schema_validator) == []


def test_applicability_predicates_use_three_valued_logic() -> None:
    facts = {"gateway.language": "python", "node.public": True}

    assert (
        validator.evaluate_predicate(
            {"fact": "gateway.language", "equals": "python"}, facts
        )
        == "true"
    )
    assert (
        validator.evaluate_predicate(
            {"fact": "gateway.language", "equals": "markdown"}, facts
        )
        == "false"
    )
    assert (
        validator.evaluate_predicate(
            {"fact": "python.uses-inheritance", "equals": True}, facts
        )
        == "unknown"
    )
    assert (
        validator.evaluate_predicate(
            {
                "all": [
                    {"fact": "node.public", "equals": True},
                    {"fact": "python.uses-inheritance", "equals": True},
                ]
            },
            facts,
        )
        == "unknown"
    )
    assert (
        validator.evaluate_predicate(
            {
                "any": [
                    {"fact": "gateway.language", "equals": "python"},
                    {"fact": "python.uses-inheritance", "equals": True},
                ]
            },
            facts,
        )
        == "true"
    )
    assert (
        validator.evaluate_predicate(
            {"not": {"fact": "python.uses-inheritance", "equals": True}}, facts
        )
        == "unknown"
    )
    assert (
        validator.evaluate_predicate(
            {"fact": "python.version", "equals": 1.0}, {"python.version": 1}
        )
        == "false"
    )


def test_remedied_by_rejects_invalid_source_and_target_kinds(
    prepared_schema_validator,
) -> None:
    value = document()
    value["links"]["link.bloated-skill.extract-script"]["source"] = {
        "kind": "definition",
        "ref": "skill-refactoring.smells.bloated-skill.signal",
    }
    value["links"]["link.bloated-skill.extract-script"]["target"] = {
        "kind": "family",
        "ref": "skill-refactoring.smells.bloated-skill",
    }

    errors = validate_document(value, ROOT, _schema_validator=prepared_schema_validator)

    assert any(
        "source must be a family, rule, or assertion" in error for error in errors
    )
    assert any("target must be a procedure" in error for error in errors)


def test_renderer_is_deterministic_and_matches_representative_golden() -> None:
    first = render_document(document())
    second = render_document(document())

    assert first == second
    assert (
        first
        == """<!-- Generated from references/skill-standards/skill-refactoring.standard.yaml; do not edit. -->

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
    )


def test_validate_document_reports_bounded_import_paths(
    tmp_path: Path, prepared_schema_validator
) -> None:
    value = document()
    value["imports"] = {
        "missing": {
            "standard_id": "missing",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "missing"},
        },
        "absolute": {
            "standard_id": "absolute",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "absolute"},
        },
        "escape": {
            "standard_id": "escape",
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": "escape"},
        },
    }
    value["artifacts"]["missing"] = {
        "path": "references/standards-schema/missing.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }
    value["artifacts"]["absolute"] = {
        "path": str(Path(tmp_path.anchor) / "outside.standard.yaml"),
        "format": "yaml",
        "roles": ["other"],
    }
    value["artifacts"]["escape"] = {
        "path": "../outside.standard.yaml",
        "format": "yaml",
        "roles": ["other"],
    }

    errors = validate_document(
        value, tmp_path, _schema_validator=prepared_schema_validator
    )

    assert any(
        "imports.missing: missing import file under root" in error for error in errors
    )
    assert "imports.absolute: import path must be repository-relative" in errors
    assert "imports.escape: import path escapes repository root" in errors


def _write_standard(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def test_validate_file_cache_findings_depend_on_traversal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prepared_schema_validator,
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

    fresh_b = validate_file(
        b_path, root=root, _schema_validator=prepared_schema_validator
    )
    shared: dict[Path, tuple[dict, list[str]]] = {}
    validate_file(
        a_path,
        root=root,
        cache=shared,
        _schema_validator=prepared_schema_validator,
    )
    cached_b = validate_file(
        b_path,
        root=root,
        cache=shared,
        _schema_validator=prepared_schema_validator,
    )

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


def test_validate_file_reuses_semantic_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prepared_schema_validator,
) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    _write_standard(standard, document())
    original_index = validator._index
    index_count = 0

    def counting_index(items, errors):
        nonlocal index_count
        index_count += 1
        return original_index(items, errors)

    monkeypatch.setattr(validator, "_index", counting_index)

    assert (
        validate_file(standard, root=root, _schema_validator=prepared_schema_validator)
        == []
    )
    assert index_count == 1

    index_count = 0
    imported_path = root / "standards" / "imported.standard.yaml"
    value = document()
    _add_import(value, "standards/imported.standard.yaml", imported_path)
    link = value["links"]["link.bloated-skill.extract-script"]
    link["source"]["document"] = "imported"
    link["target"]["document"] = "imported"
    _write_standard(standard, value)

    assert (
        validate_file(standard, root=root, _schema_validator=prepared_schema_validator)
        == []
    )
    assert index_count == 3


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
            "digest": "sha256:"
            + hashlib.sha256(imported_path.read_bytes()).hexdigest(),
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


def test_update_standards_acceptance_cascades_pins_and_view(
    tmp_path: Path,
    prepared_schema_validator,
) -> None:
    root = tmp_path / "repo"
    base_path = root / "references" / "standards-schema" / "base.standard.yaml"
    dependent_path = (
        root / "references" / "standards-schema" / "dependent.standard.yaml"
    )
    view_path = root / "references" / "standards-schema" / "base.md"

    base = document()
    base["id"] = "base"
    base["canonical_path"] = "references/standards-schema/base.standard.yaml"
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

    assert (
        validate_file(
            dependent_path, root=root, _schema_validator=prepared_schema_validator
        )
        == []
    )

    base["revision"] = 2
    base["purpose"] = "Diagnose skill smells under the revised policy."
    _write_standard(base_path, base)

    assert any(
        "imports.base" in error and "digest mismatch" in error
        for error in validate_file(
            dependent_path, root=root, _schema_validator=prepared_schema_validator
        )
    )
    assert view_path.read_text(encoding="utf-8") != render_document(base)

    dependent["revision"] = 2
    dependent["imports"]["base"]["revision"] = base["revision"]
    dependent["imports"]["base"]["digest"] = (
        "sha256:" + hashlib.sha256(base_path.read_bytes()).hexdigest()
    )
    _write_standard(dependent_path, dependent)
    view_path.write_text(render_document(base), encoding="utf-8")

    assert (
        validate_file(
            dependent_path, root=root, _schema_validator=prepared_schema_validator
        )
        == []
    )
    assert view_path.read_text(encoding="utf-8") == render_document(base)


def test_validate_file_checks_import_domain_compatibility(
    tmp_path: Path, prepared_schema_validator
) -> None:
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

    assert (
        validate_file(standard, root=root, _schema_validator=prepared_schema_validator)
        == []
    )

    _set_imported_domain(value, imported_path, {"node.present": True})
    value["domain_facts"] = {"gateway.language": "python"}
    _write_standard(standard, value)

    errors = validate_file(
        standard, root=root, _schema_validator=prepared_schema_validator
    )

    assert any(
        "imports.imported.domain_facts: missing inherited fact node.present" in error
        for error in errors
    )

    _set_imported_domain(value, imported_path, {"gateway.language": "python"})
    value["domain_facts"] = {"gateway.language": "markdown"}
    _write_standard(standard, value)

    errors = validate_file(
        standard, root=root, _schema_validator=prepared_schema_validator
    )

    assert any(
        "imports.imported.domain_facts: conflicting fact gateway.language" in error
        for error in errors
    )

    _set_imported_domain(value, imported_path, {"python.version": 1})
    value["domain_facts"] = {"python.version": 1.0}
    _write_standard(standard, value)

    errors = validate_file(
        standard, root=root, _schema_validator=prepared_schema_validator
    )

    assert any(
        "imports.imported.domain_facts: conflicting fact python.version" in error
        for error in errors
    )


def test_validate_file_rejects_unbounded_import_artifacts(
    tmp_path: Path, prepared_schema_validator
) -> None:
    root = tmp_path / "repo"
    standard = root / "standards" / "main.standard.yaml"
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    value = document()
    value["artifacts"].update(
        {
            "absolute": {
                "path": str(tmp_path / "absolute.standard.yaml"),
                "format": "yaml",
                "roles": ["other"],
            },
            "escape": {
                "path": "../outside.standard.yaml",
                "format": "yaml",
                "roles": ["other"],
            },
        }
    )
    value["imports"] = {
        alias: {
            "standard_id": alias,
            "standard_version": "1.0.0",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
            "artifact": {"kind": "artifact", "ref": alias},
        }
        for alias in ("absolute", "escape")
    }
    _write_standard(standard, value)

    errors = validate_file(
        standard, root=root, _schema_validator=prepared_schema_validator
    )

    assert "imports.absolute: artifact path must be repository-relative" in errors
    assert "imports.escape: artifact path escapes repository root" in errors
