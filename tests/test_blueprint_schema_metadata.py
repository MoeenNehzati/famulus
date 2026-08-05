from __future__ import annotations

import json
from pathlib import Path

import jsonschema


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
V5_SCHEMA_ROOT = LIVE_SCHEMA_ROOT / "migrations" / "v5"
SCHEMA_ROOT = LIVE_SCHEMA_ROOT / "migrations" / "v4"
V4_TYPED_SCHEMAS = ("module.schema.json", "behavioral-source.schema.json")
REQUIRED_RULES = {
    "access-control",
    "behavioral-source-edge",
    "canonical-id",
    "canonical-pooled-review",
    "content-exclusive",
    "content-files",
    "dispatcher-argv-pattern",
    "content-non-symlink",
    "direct-io-description",
    "filesystem-ownership",
    "gateway-file",
    "generated-contract-block",
    "interface-body-use",
    "relationship-edge",
    "relationship-matrix",
    "root-directory-id",
    "runtime-dependency",
    "schema-shape",
    "version-pin",
}


def _load(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _load_v5(name: str) -> dict:
    return json.loads((V5_SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_v4_schema_fields_contain_complete_authoring_metadata() -> None:
    protocol = _load("schema-meta.json")
    catalog = protocol["x-famulus"]["validation_rule_catalog"]

    for name in V4_TYPED_SCHEMAS:
        schema = _load(name)
        jsonschema.Draft7Validator(protocol).validate(schema)
        required = set(schema["required"])
        for field, definition in schema["properties"].items():
            metadata = definition.get("x-famulus")
            assert isinstance(metadata, dict), f"{name}:{field} missing x-famulus"
            expected_status = "required" if field in required else "optional"
            assert metadata["field_status"] == expected_status
            assert "audit_hash" not in metadata
            assert isinstance(metadata["template"]["include"], bool)
            assert metadata["doc"]["authoring"]
            assert metadata["related_validation_rules"]
            assert set(metadata["related_validation_rules"]) <= set(catalog)


def test_common_schema_nested_fields_have_complete_authoring_metadata() -> None:
    protocol = _load("schema-meta.json")
    metadata_validator = jsonschema.Draft7Validator(
        protocol["definitions"]["fieldMetadata"]
    )
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
                assert "audit_hash" not in metadata, f"{path}.{field}"
                visit(child, f"{path}.{field}")
        for keyword in (
            "definitions",
            "items",
            "allOf",
            "oneOf",
            "anyOf",
        ):
            child = schema.get(keyword)
            if keyword == "definitions" and isinstance(child, dict):
                for name, definition in child.items():
                    visit(definition, f"{path}.{name}")
            elif child is not None:
                visit(child, path)

    visit(common, "common")


def test_live_schema_annotations_have_no_audit_hash_authority() -> None:
    def visit(value: object, path: str) -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        metadata = value.get("x-famulus")
        if isinstance(metadata, dict):
            assert "audit_hash" not in metadata, path
        for key, child in value.items():
            visit(child, f"{path}.{key}")

    for name in (*V4_TYPED_SCHEMAS, "common.schema.json"):
        visit(_load(name), name)


def test_schema_meta_contains_only_v4_repository_validation_authority() -> None:
    metadata = _load("schema-meta.json")

    assert set(metadata["definitions"]) == {
        "enforcementMetadata",
        "fieldMetadata",
        "repositoryValidationRule",
        "validationRule",
    }
    assert metadata["definitions"]["validationRule"] == {
        "$ref": "#/definitions/repositoryValidationRule"
    }
    field_metadata = metadata["definitions"]["fieldMetadata"]
    assert "audit_hash" not in field_metadata["required"]
    assert "audit_hash" not in field_metadata["properties"]
    assert "hashing" not in metadata["description"].lower()
    catalog = metadata["x-famulus"]["validation_rule_catalog"]
    assert REQUIRED_RULES <= set(catalog)
    assert {entry["rule_kind"] for entry in catalog.values()} == {
        "repository-validation"
    }
    assert not any(rule_id.startswith("interface.") for rule_id in catalog)


def test_validation_rule_catalog_points_to_existing_enforcement_and_tests() -> None:
    protocol = _load_v5("schema-meta.json")
    catalog = protocol["x-famulus"]["validation_rule_catalog"]
    entry_schema = dict(protocol["definitions"]["repositoryValidationRule"])
    entry_schema["definitions"] = protocol["definitions"]
    entry_validator = jsonschema.Draft7Validator(entry_schema)

    for rule_id, rule in catalog.items():
        entry_validator.validate(rule)
        assert (REPO_ROOT / rule["validator"]).is_file(), rule_id
        assert all((REPO_ROOT / path).is_file() for path in rule["tests"]), rule_id
        assert rule["enforcement"]["state"] in {"schema", "current"}, rule_id
        assert rule["enforcement"]["task"] == "current", rule_id
        assert rule["enforcement"]["acceptance"] != "not-yet-available", rule_id


def test_schema_meta_declares_only_the_v4_relationship_matrix() -> None:
    metadata = _load("schema-meta.json")["x-famulus"]

    assert set(metadata) == {
        "field_metadata_ref",
        "relationship_matrix",
        "validation_rule_catalog",
    }
    assert metadata["relationship_matrix"] == {
        "module": {
            "contains-source": ["behavioral_source"],
            "exports-interface": ["behavioral_source"],
            "references-cross-owner-contract": ["module", "behavioral_source"],
        },
        "behavioral_source": {
            "uses-source": ["behavioral_source"],
            "uses-private-interface": ["behavioral_source"],
            "uses-export": ["module"],
            "references-cross-owner-contract": ["module", "behavioral_source"],
        },
    }


def test_v4_nodes_declare_content_as_ownership_not_direct_hash_inputs() -> None:
    for name in V4_TYPED_SCHEMAS:
        properties = _load(name)["properties"]
        assert "local_hash_inputs" not in properties
        assert "audit_hash" not in properties["gateway"]["x-famulus"]
        field = properties["content"]
        assert field["type"] == "array"
        assert field["uniqueItems"] is True
        assert field["minItems"] == 1
        assert "audit_hash" not in field["x-famulus"]
        assert field["x-famulus"]["related_validation_rules"] == [
            "content-files",
            "content-exclusive",
        ]

    policy = _load("common.schema.json")["x-famulus"][
        "content_ownership_policy"
    ]
    assert "ownership" in policy["content"]
    assert "node-input policy" in policy["content"]


def test_v4_behavioral_source_has_intrinsic_interfaces_and_source_owned_io() -> None:
    schema = _load("behavioral-source.schema.json")
    direct_io = _load("direct-io.schema.json")

    assert schema["properties"]["node_type"]["const"] == "behavioral_source"
    assert "semantic_type" not in schema["properties"]
    assert schema["properties"]["interfaces"]["propertyNames"]["$ref"] == (
        "common.schema.json#/definitions/sourceInterfaceId"
    )
    assert direct_io["title"] == "Interface Direct I/O"
    assert "source-owned" in direct_io["description"]
    assert not any(name.startswith("preV4") for name in direct_io["definitions"])


def test_runtime_dependency_platforms_are_documented_as_applicability() -> None:
    metadata = _load("schema-meta.json")["x-famulus"]["validation_rule_catalog"]
    rule = metadata["runtime-dependency"]
    wording = " ".join(
        [rule["description"], rule["creation"], rule["enforcement"]["note"]]
    ).lower()

    assert "where each dependency applies" in wording
    assert "required interface" in wording
    assert "required dependency" not in wording


def test_v5_schema_fields_carry_shadow_authoring_metadata() -> None:
    protocol = _load_v5("schema-meta.json")
    catalog = protocol["x-famulus"]["validation_rule_catalog"]

    for name in V4_TYPED_SCHEMAS:
        schema = _load_v5(name)
        jsonschema.Draft7Validator(protocol).validate(schema)
        required = set(schema["required"])
        for field, definition in schema["properties"].items():
            metadata = definition.get("x-famulus")
            assert isinstance(metadata, dict), f"{name}:{field} missing x-famulus"
            assert metadata["field_status"] == (
                "required" if field in required else "optional"
            )
            assert set(metadata["related_validation_rules"]) <= set(catalog)

    module = _load_v5("module.schema.json")
    assert {"children", "namespace_exports"} <= set(module["required"])
    assert module["properties"]["children"]["x-famulus"][
        "related_validation_rules"
    ] == ["nested-topology"]
    assert module["properties"]["namespace_exports"]["x-famulus"][
        "related_validation_rules"
    ] == ["namespace-export"]


def test_v5_schema_meta_declares_nested_relationships_and_schema_rules() -> None:
    metadata = _load_v5("schema-meta.json")["x-famulus"]
    assert metadata["relationship_matrix"] == {
        "module": {
            "contains-module": ["module"],
            "contains-source": ["behavioral_source"],
            "exports-interface": ["behavioral_source"],
            "facades-child-export": ["module"],
            "facades-implementing-source": ["behavioral_source"],
            "references-cross-owner-contract": ["module", "behavioral_source"],
            "routes-child-namespace": ["module"],
            "routes-terminal-module": ["module"],
        },
        "behavioral_source": {
            "uses-source": ["behavioral_source"],
            "uses-private-interface": ["behavioral_source"],
            "uses-export": ["module"],
            "references-cross-owner-contract": ["module", "behavioral_source"],
        },
    }
    catalog = metadata["validation_rule_catalog"]
    assert {
        "nested-topology",
        "namespace-export",
        "facade-export",
        "relative-caller",
    } <= set(catalog)
    for rule_id in (
        "nested-topology",
        "namespace-export",
        "facade-export",
        "relative-caller",
    ):
        rule = catalog[rule_id]
        assert rule["enforcement"]["state"] == "current"
        assert rule["tests"] == ["tests/test_nested_module_v5_schemas.py"]
