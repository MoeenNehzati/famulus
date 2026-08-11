"""Tests for repository-configured JSON Schema loading and composition."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from officina.common import configured_schema as configured_schema_module
from officina.common.configured_schema import (
    ConfiguredSchemaBundle,
    ConfiguredSchemaError,
    configured_validator,
    load_configuration,
    load_configured_schema,
    load_configured_schema_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_SCHEMA_PATH = REPO_ROOT / "src/officina/common/configuration.schema.json"


def _protocol() -> dict:
    protocol = json.loads(ANNOTATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(protocol)
    definitions = protocol.get("definitions", {})
    annotation = definitions.get("configAnnotation")
    if annotation is None:
        return protocol
    return {
        **annotation,
        "definitions": {
            "nonRootJsonPointer": definitions["nonRootJsonPointer"],
        },
    }


def _write_config(tmp_path: Path, content: str, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _write_config_files(
    tmp_path: Path,
    *,
    config: str = "values: [research, system]\n",
) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config, encoding="utf-8")
    companion_path = tmp_path / "config.schema.json"
    companion_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["values"],
                "additionalProperties": False,
                "properties": {
                    "values": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path, companion_path


@pytest.mark.parametrize(
    "annotation",
    [
        {"operation": "keys-to-enum", "source": "/taxonomy/categories"},
        {"operation": "values-to-enum", "source": "/actions"},
        {"operation": "extend-required", "source": "/required_metadata"},
        {"operation": "keys-to-enum", "source": "/a~0b/c~1d"},
        {"operation": "keys-to-enum", "source": "/"},
    ],
)
def test_annotation_protocol_accepts_supported_injections(annotation: dict) -> None:
    jsonschema.Draft7Validator(_protocol()).validate(annotation)


@pytest.mark.parametrize(
    "annotation",
    [
        None,
        {"operation": "replace-required", "source": "/required"},
        {"operation": "value-to-bound", "source": "/limits/name"},
        {"operation": "mapping-to-allowed-pairs", "source": "/subsystems"},
        {"operation": "keys-to-property-names", "source": "/profiles"},
        {"operation": "keys-to-enum", "source": "taxonomy/categories"},
        {"operation": "keys-to-enum", "source": ""},
        {"operation": "keys-to-enum", "source": "/a~2b"},
        {"operation": "keys-to-enum", "source": "/a~"},
        {"operation": "keys-to-enum", "source": 1},
        {"operation": "keys-to-enum"},
        {"operation": "keys-to-enum", "source": "/values", "extra": True},
    ],
)
def test_annotation_protocol_rejects_unsafe_or_malformed_injections(
    annotation: object,
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_protocol()).validate(annotation)


def test_enum_composition_is_monotonic_and_immutable(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    base = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "string",
        "enum": ["research", "internal"],
        "x-officina-config": {
            "operation": "values-to-enum",
            "source": "/values",
        },
    }
    schema_path.write_text(json.dumps(base), encoding="utf-8")
    config_path, companion_path = _write_config_files(tmp_path)

    resolved = load_configured_schema(
        schema_path,
        config_path=config_path,
        config_schema_path=companion_path,
    )

    assert resolved["enum"] == ["research"]
    assert "x-officina-config" not in resolved
    assert json.loads(schema_path.read_text(encoding="utf-8")) == base


def test_extend_required_preserves_existing_fields(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string"},
                },
                "additionalProperties": False,
                "x-officina-config": {
                    "operation": "extend-required",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(
        tmp_path, config="values: [category, id]\n"
    )

    resolved = load_configured_schema(
        schema_path,
        config_path=config_path,
        config_schema_path=companion_path,
    )

    assert resolved["required"] == ["id", "category"]


def test_extend_required_rejects_non_object_or_forbidden_property(
    tmp_path: Path,
) -> None:
    config_path, companion_path = _write_config_files(
        tmp_path, config="values: [category]\n"
    )
    for name, schema in {
        "scalar": {
            "type": "string",
            "x-officina-config": {
                "operation": "extend-required",
                "source": "/values",
            },
        },
        "closed": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
            "x-officina-config": {
                "operation": "extend-required",
                "source": "/values",
            },
        },
    }.items():
        schema_path = tmp_path / f"{name}.schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        with pytest.raises(ConfiguredSchemaError):
            load_configured_schema(
                schema_path,
                config_path=config_path,
                config_schema_path=companion_path,
            )


def test_schema_traversal_ignores_literal_annotation_keys(tmp_path: Path) -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "enum": [{"x-officina-config": "literal instance data"}],
        "default": {"x-officina-config": None},
    }
    schema_path = tmp_path / "literal.schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    assert load_configured_schema(schema_path) == schema


def test_null_annotation_at_schema_location_is_rejected(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"category": {"type": "string", "x-officina-config": None}},
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(tmp_path)

    with pytest.raises(ConfiguredSchemaError, match="invalid x-officina-config"):
        load_configured_schema(
            schema_path,
            config_path=config_path,
            config_schema_path=companion_path,
        )


def test_bundle_composes_annotated_external_reference(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    root_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "root.schema.json",
                "$ref": "child.schema.json",
            }
        ),
        encoding="utf-8",
    )
    child_path = tmp_path / "child.schema.json"
    child_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "child.schema.json",
                "type": "string",
                "x-officina-config": {
                    "operation": "values-to-enum",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(tmp_path)

    bundle = load_configured_schema_bundle(
        root_path,
        config_path=config_path,
        config_schema_path=companion_path,
    )

    assert isinstance(bundle, ConfiguredSchemaBundle)
    assert bundle.documents[child_path.resolve()]["enum"] == ["research", "system"]
    bundle.validator().validate("research")
    with pytest.raises(jsonschema.ValidationError):
        bundle.validator().validate("other")


def test_absolute_reference_requires_and_uses_local_catalog(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    child_path = tmp_path / "child.schema.json"
    child_id = "https://officina.example/schemas/child"
    root_path.write_text(json.dumps({"$ref": child_id}), encoding="utf-8")
    child_path.write_text(
        json.dumps(
            {
                "$id": child_id,
                "type": "string",
                "x-officina-config": {
                    "operation": "values-to-enum",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(tmp_path)

    with pytest.raises(ConfiguredSchemaError, match="referenced_schema_paths"):
        load_configured_schema_bundle(root_path)

    bundle = load_configured_schema_bundle(
        root_path,
        config_path=config_path,
        config_schema_path=companion_path,
        referenced_schema_paths=[child_path],
    )
    bundle.validator().validate("research")
    with pytest.raises(jsonschema.ValidationError):
        bundle.validator().validate("other")


def test_nested_id_scope_resolves_cataloged_reference(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    child_path = tmp_path / "child.schema.json"
    root_path.write_text(
        json.dumps(
            {
                "$id": "https://officina.example/root",
                "$defs": {
                    "scope": {
                        "$id": "nested/",
                        "$ref": "child",
                    }
                },
                "$ref": "#/$defs/scope",
            }
        ),
        encoding="utf-8",
    )
    child_path.write_text(
        json.dumps(
            {
                "$id": "https://officina.example/nested/child",
                "type": "string",
            }
        ),
        encoding="utf-8",
    )

    validator = configured_validator(
        root_path,
        referenced_schema_paths=[child_path],
    )
    validator.validate("value")
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(1)


def test_extend_required_uses_composed_property_names_and_patterns(
    tmp_path: Path,
) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "propertyNames": {
                    "type": "string",
                    "x-officina-config": {
                        "operation": "values-to-enum",
                        "source": "/values",
                    },
                },
                "patternProperties": {"^research$": {"type": "string"}},
                "additionalProperties": False,
                "x-officina-config": {
                    "operation": "extend-required",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(
        tmp_path, config="values: [research]\n"
    )

    resolved = load_configured_schema(
        schema_path,
        config_path=config_path,
        config_schema_path=companion_path,
    )

    assert resolved["required"] == ["research"]
    assert resolved["propertyNames"]["enum"] == ["research"]


def test_extend_required_resolves_property_names_reference(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    names_path = tmp_path / "names.schema.json"
    root_path.write_text(
        json.dumps(
            {
                "type": "object",
                "propertyNames": {"$ref": "names.schema.json"},
                "x-officina-config": {
                    "operation": "extend-required",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    names_path.write_text(
        json.dumps({"type": "string", "pattern": "^research$"}), encoding="utf-8"
    )
    config_path, companion_path = _write_config_files(
        tmp_path, config="values: [research]\n"
    )

    bundle = load_configured_schema_bundle(
        root_path,
        config_path=config_path,
        config_schema_path=companion_path,
    )
    assert bundle.root_schema["required"] == ["research"]


def test_bundle_document_snapshots_do_not_mutate_validation(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"type": "string"}), encoding="utf-8")
    bundle = load_configured_schema_bundle(schema_path)

    snapshot = bundle.documents
    snapshot[schema_path.resolve()]["type"] = "integer"

    bundle.validator().validate("still a string")

    validator = bundle.validator()
    validator.schema["type"] = "integer"
    bundle.validator().validate("still isolated")


def test_duplicate_schema_identifiers_are_rejected(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    first_path = tmp_path / "first.schema.json"
    second_path = tmp_path / "second.schema.json"
    duplicate_id = "https://officina.example/duplicate"
    root_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    first_path.write_text(json.dumps({"$id": duplicate_id}), encoding="utf-8")
    second_path.write_text(json.dumps({"$id": duplicate_id}), encoding="utf-8")

    with pytest.raises(ConfiguredSchemaError, match="duplicate schema identifier"):
        load_configured_schema_bundle(
            root_path,
            referenced_schema_paths=[first_path, second_path],
        )


def test_relative_nested_ids_are_scoped_not_global_aliases(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    first_path = tmp_path / "first.schema.json"
    second_path = tmp_path / "second.schema.json"
    root_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    first_path.write_text(
        json.dumps({"$id": "https://officina.example/first/", "$defs": {"x": {"$id": "item"}}}),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps({"$id": "https://officina.example/second/", "$defs": {"x": {"$id": "item"}}}),
        encoding="utf-8",
    )

    bundle = load_configured_schema_bundle(
        root_path,
        referenced_schema_paths=[first_path, second_path],
    )
    assert bundle.document_count == 3


def test_extend_required_rejects_boolean_false_property_names(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "propertyNames": False,
                "x-officina-config": {
                    "operation": "extend-required",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    config_path, companion_path = _write_config_files(tmp_path)

    with pytest.raises(ConfiguredSchemaError, match="forbidden"):
        load_configured_schema(
            schema_path,
            config_path=config_path,
            config_schema_path=companion_path,
        )


def test_companion_schema_absolute_reference_uses_local_catalog(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("value: ok\n", encoding="utf-8")
    companion_path = tmp_path / "config.schema.json"
    value_path = tmp_path / "value.schema.json"
    value_id = "https://officina.example/config/value"
    companion_path.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"$ref": value_id}},
            }
        ),
        encoding="utf-8",
    )
    value_path.write_text(
        json.dumps({"$id": value_id, "const": "ok"}), encoding="utf-8"
    )

    assert load_configuration(
        config_path,
        config_schema_path=companion_path,
        referenced_schema_paths=[value_path],
    ) == {"value": "ok"}


def test_companion_schema_honors_nested_id_reference_scope(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("value: ok\n", encoding="utf-8")
    companion_path = tmp_path / "config.schema.json"
    value_path = tmp_path / "value.schema.json"
    companion_path.write_text(
        json.dumps(
            {
                "$id": "https://officina.example/config/root",
                "type": "object",
                "properties": {
                    "value": {
                        "$id": "nested/",
                        "$ref": "value",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    value_path.write_text(
        json.dumps(
            {
                "$id": "https://officina.example/config/nested/value",
                "const": "ok",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_configuration(
        config_path,
        config_schema_path=companion_path,
        referenced_schema_paths=[value_path],
    )
    assert loaded == {"value": "ok"}


def test_invalid_local_reference_fragment_is_domain_error(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps({"$ref": "#/definitions/missing"}), encoding="utf-8"
    )

    with pytest.raises(ConfiguredSchemaError, match="cannot resolve reference"):
        load_configured_schema_bundle(schema_path)


def test_standalone_loader_rejects_multi_document_bundle(tmp_path: Path) -> None:
    root_path = tmp_path / "root.schema.json"
    root_path.write_text(json.dumps({"$ref": "child.schema.json"}), encoding="utf-8")
    (tmp_path / "child.schema.json").write_text(
        json.dumps({"type": "string"}), encoding="utf-8"
    )

    with pytest.raises(ConfiguredSchemaError, match="multi-document bundle"):
        load_configured_schema(root_path)


@pytest.mark.parametrize(
    "content, expected_key",
    [
        (
            "allowed_abs: [officina, skills]\n"
            "names_for_dependency_sections:\n"
            "  calls: CallsFromRepo\n"
            "  instantiations: InstantiationsFromRepo\n"
            "  dispatches: Dispatches\n",
            "allowed_abs",
        ),
        (
            "policy_version: 1\n"
            "path_syntax: gitignore\n"
            "starting_set: git-tracked-directly-owned-regular-files\n"
            "rules:\n"
            "  - action: exclude\n"
            "    pattern: '**/*.log'\n",
            "policy_version",
        ),
        (
            "jobs:\n"
            "  - name: daily-plan\n"
            "    description: Generate the daily plan\n"
            "    command: invoke-skill daily-plan\n"
            "    schedule: '0 * * * *'\n"
            "    enabled: true\n",
            "jobs",
        ),
        (
            '{"remote_llm_root": "assistant/", "timeout_seconds": 45}',
            "remote_llm_root",
        ),
    ],
)
def test_default_configuration_schema_accepts_lightweight_families(
    tmp_path: Path,
    content: str,
    expected_key: str,
) -> None:
    config_path = _write_config(tmp_path, content)

    loaded = load_configuration(config_path)

    assert expected_key in loaded


def test_default_configuration_schema_rejects_mixed_families(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        "allowed_abs: [officina]\njobs: []\n",
    )

    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        load_configuration(config_path)


def test_configuration_error_reports_deepest_domain_problem(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        "jobs:\n  - name: daily-plan\n    schedule: '0 * * * *'\n",
    )

    with pytest.raises(ConfiguredSchemaError, match="'command' is a required property"):
        load_configuration(config_path)


def test_default_configuration_schema_rejects_schema_vocabulary(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        "allowed_abs: [officina]\ntype: object\nproperties: {}\n",
    )

    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        load_configuration(config_path)


def test_configuration_rejects_nonfinite_yaml_number(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        "remote_llm_root: assistant/\ntimeout_seconds: .nan\n",
    )

    with pytest.raises(ConfiguredSchemaError, match="finite"):
        load_configuration(config_path)


def test_validate_configuration_checks_in_memory_documents() -> None:
    validate = getattr(configured_schema_module, "validate_configuration", None)
    assert callable(validate)

    validate({"remote_llm_root": "assistant/", "timeout_seconds": 45})
    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        validate({"jobs": [], "allowed_abs": ["officina"]})


def test_validator_for_schema_owns_self_contained_mapping_validation() -> None:
    factory = getattr(configured_schema_module, "validator_for_schema", None)
    assert callable(factory)

    validator = factory({"type": "string", "minLength": 2})
    validator.validate("ok")
    with pytest.raises(jsonschema.ValidationError):
        validator.validate("x")


def test_companion_schema_supports_relative_refs_and_formats(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("contact: not-an-email\n", encoding="utf-8")
    companion_path = tmp_path / "config.schema.json"
    companion_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["contact"],
                "properties": {"contact": {"$ref": "email.schema.json"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "email.schema.json").write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "email.schema.json",
                "type": "string",
                "format": "email",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfiguredSchemaError, match="contact"):
        load_configuration(
            config_path,
            config_schema_path=companion_path,
            format_checker=jsonschema.FormatChecker(),
        )


def test_loader_rejects_duplicate_keys_and_nonstandard_json(tmp_path: Path) -> None:
    companion_path = tmp_path / "config.schema.json"
    companion_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    for name, content in {
        "duplicate.yaml": "values: [a]\nvalues: [b]\n",
        "duplicate.json": '{"values": ["a"], "values": ["b"]}',
        "nan.json": '{"value": NaN}',
    }.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ConfiguredSchemaError):
            load_configuration(path, config_schema_path=companion_path)


def test_loader_requires_companion_and_config_for_annotations(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("values: [a]\n", encoding="utf-8")
    with pytest.raises(ConfiguredSchemaError, match="invalid configuration"):
        load_configuration(config_path)

    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "string",
                "x-officina-config": {
                    "operation": "values-to-enum",
                    "source": "/values",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfiguredSchemaError, match="requires configuration"):
        load_configured_schema(schema_path)


def test_loader_confines_relative_schema_references(tmp_path: Path) -> None:
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    root_path = schema_root / "root.schema.json"
    root_path.write_text(json.dumps({"$ref": "../outside.schema.json"}), encoding="utf-8")
    (tmp_path / "outside.schema.json").write_text(json.dumps({"type": "string"}), encoding="utf-8")

    with pytest.raises(ConfiguredSchemaError, match="outside allowed schema root"):
        load_configured_schema_bundle(root_path)


def test_file_uri_path_decoding_preserves_windows_drive_root() -> None:
    assert configured_schema_module._decoded_file_uri_path(
        "/D:/a/famulus/common.schema.json",
        drive_letter_root=True,
    ) == "D:/a/famulus/common.schema.json"


def test_file_uri_path_decoding_preserves_posix_root() -> None:
    assert configured_schema_module._decoded_file_uri_path(
        "/opt/famulus/common.schema.json",
        drive_letter_root=False,
    ) == "/opt/famulus/common.schema.json"


def test_configured_validator_honors_format_checker(tmp_path: Path) -> None:
    schema_path = tmp_path / "email.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "string",
                "format": "email",
            }
        ),
        encoding="utf-8",
    )

    validator = configured_validator(
        schema_path, format_checker=jsonschema.FormatChecker()
    )

    with pytest.raises(jsonschema.ValidationError):
        validator.validate("not-an-email")
