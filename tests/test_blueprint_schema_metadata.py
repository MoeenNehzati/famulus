from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
TYPED_SCHEMAS = [
    "skill.schema.json",
    "llm-interface.schema.json",
    "machine-interface.schema.json",
    "behavior-source.schema.json",
]
REQUIRED_RULES = {
    "generated-contract-block",
    "sidecar-naming",
    "binding-tracked",
    "binding-non-symlink",
    "behavior-source-visibility",
    "relationship-matrix",
    "commit-backed-stamp",
    "canonical-pooled-review",
}


def _load(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_typed_schema_fields_contain_complete_authoring_and_hash_metadata() -> None:
    protocol = _load("schema-meta.json")
    catalog = protocol["x-famulus"]["validation_rule_catalog"]

    for name in TYPED_SCHEMAS:
        schema = _load(name)
        jsonschema.Draft7Validator(protocol).validate(schema)
        required = set(schema["required"])
        for field, definition in schema["properties"].items():
            metadata = definition.get("x-famulus")
            assert isinstance(metadata, dict), f"{name}:{field} missing x-famulus"
            expected_status = "required" if field in required else "optional"
            assert metadata["field_status"] == expected_status
            assert metadata["audit_hash"] in {"include", "exclude"}
            assert isinstance(metadata["template"]["include"], bool)
            assert metadata["doc"]["authoring"]
            assert metadata["related_validation_rules"]
            assert set(metadata["related_validation_rules"]) <= set(catalog)


def test_common_schema_nested_fields_have_complete_authoring_metadata() -> None:
    protocol = _load("schema-meta.json")
    metadata_validator = jsonschema.Draft7Validator(protocol["definitions"]["fieldMetadata"])
    common = _load("common.schema.json")

    def visit(schema: object, path: str) -> None:
        if isinstance(schema, list):
            for index, child in enumerate(schema):
                visit(child, f"{path}[{index}]")
            return
        if not isinstance(schema, dict):
            return
        properties = schema.get("properties")
        if isinstance(properties, dict):
            required = set(schema.get("required", []))
            for field, child in properties.items():
                metadata = child.get("x-famulus") if isinstance(child, dict) else None
                assert isinstance(metadata, dict), f"{path}.{field} missing x-famulus"
                metadata_validator.validate(metadata)
                expected = "required" if field in required else "optional"
                assert metadata["field_status"] == expected, f"{path}.{field}"
                visit(child, f"{path}.{field}")
        for keyword in ("definitions", "items", "allOf", "oneOf", "anyOf", "if", "then", "else"):
            child = schema.get(keyword)
            if keyword == "definitions" and isinstance(child, dict):
                for name, definition in child.items():
                    visit(definition, f"{path}.{name}")
            elif child is not None:
                visit(child, path)

    visit(common, "common")


def test_validation_rule_catalog_points_to_existing_enforcement_and_tests() -> None:
    protocol = _load("schema-meta.json")
    catalog = protocol["x-famulus"]["validation_rule_catalog"]
    entry_schema = dict(protocol["definitions"]["validationRule"])
    entry_schema["definitions"] = protocol["definitions"]
    entry_validator = jsonschema.Draft7Validator(entry_schema)

    for rule_id, rule in catalog.items():
        entry_validator.validate(rule)
        assert (REPO_ROOT / rule["validator"]).is_file(), rule_id
        assert all((REPO_ROOT / path).is_file() for path in rule["tests"]), rule_id


def test_completed_health_and_typed_graph_rules_are_current() -> None:
    catalog = _load("schema-meta.json")["x-famulus"]["validation_rule_catalog"]
    expected_acceptance = {
        "file-binding": "isolated-validator",
        "direct-io-description": "isolated-validator",
        "filesystem-ownership": "isolated-validator",
        "behavior-source-edge": "isolated-validator",
        "generated-contract-block": "isolated-validator",
        "binding-tracked": "isolated-validator",
        "binding-non-symlink": "isolated-validator",
        "behavior-source-visibility": "isolated-validator",
        "relationship-matrix": "isolated-validator",
        "commit-backed-stamp": "isolated-graph",
        "canonical-pooled-review": "isolated-graph",
    }

    for rule_id, acceptance in expected_acceptance.items():
        enforcement = catalog[rule_id]["enforcement"]
        assert enforcement["state"] == "current", rule_id
        assert enforcement["acceptance"] == acceptance, rule_id
        assert enforcement["acceptance"] != "not-yet-available", rule_id
        assert "will" not in enforcement["note"].lower(), rule_id
        assert "future" not in enforcement["note"].lower(), rule_id


def test_schema_meta_catalogs_every_repository_rule() -> None:
    catalog = _load("schema-meta.json")["x-famulus"]["validation_rule_catalog"]

    assert REQUIRED_RULES <= set(catalog)


def test_schema_meta_declares_relationship_and_visibility_policy() -> None:
    metadata = _load("schema-meta.json")["x-famulus"]

    assert metadata["relationship_matrix"] == {
        "skill": {"declares-interface": ["llm-interface", "machine-interface"]},
        "llm-interface": {
            "uses-interface": ["llm-interface", "machine-interface"],
            "uses-behavior-source": ["behavior-source"],
        },
        "machine-interface": {
            "uses-interface": ["machine-interface"],
            "uses-behavior-source": ["behavior-source"],
        },
        "behavior-source": {
            "uses-interface": ["llm-interface", "machine-interface"],
            "uses-behavior-source": ["behavior-source"],
        },
    }
    assert metadata["behavior_source_visibility"] == {
        "skill_local": "declaring-skill-only",
        "repository_references": "all-skills",
    }


def test_file_backed_typed_nodes_declare_local_hash_inputs() -> None:
    for name in [
        "llm-interface.schema.json",
        "machine-interface.schema.json",
        "behavior-source.schema.json",
    ]:
        field = _load(name)["properties"]["local_hash_inputs"]
        assert field["type"] == "array"
        assert field["uniqueItems"] is True
        assert field["x-famulus"]["audit_hash"] == "include"
        assert field["x-famulus"]["related_validation_rules"] == [
            "file-binding",
            "commit-backed-stamp",
        ]


def test_direct_io_is_explicitly_excluded_from_certified_contract_hashes() -> None:
    for name in ["llm-interface.schema.json", "machine-interface.schema.json"]:
        metadata = _load(name)["properties"]["direct_io"]["x-famulus"]
        assert metadata["audit_hash"] == "exclude"
        assert "does not prove" in " ".join(metadata["doc"]["authoring"])


def test_machine_interface_support_is_bounded_by_required_interfaces() -> None:
    interfaces: dict[str, dict] = {}
    for path in REPO_ROOT.glob("skills/*/blueprint.yaml"):
        declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared_interfaces = declaration.get("interfaces", {})
        machine = (
            declared_interfaces.get("machine", {})
            if isinstance(declared_interfaces, dict)
            else {}
        )
        if isinstance(machine, dict):
            for local_name, specification in machine.items():
                interfaces[f"{path.parent.name}.machine.{local_name}"] = specification
    for path in REPO_ROOT.glob("skills/*/_rtx/.*.blueprint.yaml"):
        declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
        if declaration.get("blueprint_type") == "machine-interface":
            interfaces[declaration["id"]] = declaration

    for interface_id, declaration in interfaces.items():
        support = declaration["platform_support"]
        for edge in declaration.get("uses_interfaces", []):
            target_id = edge["interface"]
            target_support = interfaces[target_id]["platform_support"]
            for platform, supported in support.items():
                assert not supported or target_support[platform], (
                    f"{interface_id} claims {platform} support but required "
                    f"interface {target_id} does not"
                )


def test_runtime_dependency_platforms_are_documented_as_applicability() -> None:
    metadata = _load("schema-meta.json")["x-famulus"]["validation_rule_catalog"]
    rule = metadata["runtime-dependency"]
    wording = " ".join(
        [
            rule["description"],
            rule["creation"],
            rule["enforcement"]["note"],
        ]
    ).lower()

    assert "where each dependency applies" in wording
    assert "required interface" in wording
    assert "required dependency" not in wording
