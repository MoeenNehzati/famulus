"""Focused contracts for configured blueprint catalog metadata."""

from __future__ import annotations

from copy import deepcopy
from functools import cache
import json
from pathlib import Path

import pytest
import yaml

from officina.blueprints.graph import (
    BlueprintGraphError,
    load_module_blueprint,
    load_repository_blueprint_graph,
)
from officina.blueprints.template import load_schema
from officina.configuration.configured_schema import ConfiguredSchemaError

from officina.configuration.configured_schema import configured_validator, load_configuration


REPO_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_ROOT = REPO_ROOT / "references" / "blueprint"
CONFIG_PATH = BLUEPRINT_ROOT / "config.yaml"


@cache
def _validator():
    """Build the immutable configured catalog validator once."""

    return configured_validator(
        BLUEPRINT_ROOT / "module.schema.json",
        config_path=CONFIG_PATH,
        allowed_schema_root=BLUEPRINT_ROOT,
    )


def _module() -> dict:
    return {
        "schema_version": 6,
        "node_type": "module",
        "id": "demo-skill",
        "version": 1,
        "description": "Provides a synthetic configured skill.",
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "discovery": {
            "mechanism": "skill",
            "catalog": {
                "domain": "research",
                "topics": ["mathematical-reasoning", "visualization"],
                "visibility": "featured",
            },
            "activated_by": ["user-request", "skill-workflow"],
            "persistent_modifier": False,
        },
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {},
        "namespace_exports": {},
        "exports": {},
    }


def _messages(document: dict) -> list[str]:
    return [error.message for error in _validator().iter_errors(document)]


def _write_unconfigured_annotated_schema(schema_root: Path) -> Path:
    schema_root.mkdir(parents=True)
    schema_path = schema_root / "module.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "x-officina-config": {
                    "operation": "values-to-enum",
                    "source": "/blueprint_catalog/domains",
                },
            }
        ),
        encoding="utf-8",
    )
    return schema_path


def test_blueprint_catalog_configuration_uses_central_schema() -> None:
    config = load_configuration(CONFIG_PATH)

    assert config["blueprint_catalog"]["domains"]
    assert config["blueprint_catalog"]["topics"]
    assert config["blueprint_catalog"]["visibility"] == [
        "featured",
        "listed",
        "hidden",
    ]


def test_every_configured_discovery_value_has_defined_semantics() -> None:
    config = load_configuration(CONFIG_PATH)["blueprint_catalog"]
    standard = (
        REPO_ROOT / "docs" / "officina" / "blueprint-discovery-metadata.md"
    ).read_text(encoding="utf-8")

    for values in config.values():
        for value in values:
            assert f"| `{value}` |" in standard, value


def test_configured_module_accepts_complete_catalog_metadata() -> None:
    _validator().validate(_module())


def test_template_schema_loading_fails_closed_without_configuration(
    tmp_path: Path,
) -> None:
    schema_path = _write_unconfigured_annotated_schema(tmp_path / "schemas")

    with pytest.raises(ConfiguredSchemaError, match="config.yaml is missing"):
        load_schema(schema_path)


def test_template_schema_loading_fails_closed_for_configured_reference(
    tmp_path: Path,
) -> None:
    schema_root = tmp_path / "schemas"
    configured_path = _write_unconfigured_annotated_schema(schema_root)
    entry_path = schema_root / "schema.json"
    entry_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$ref": configured_path.name,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfiguredSchemaError, match="config.yaml is missing"):
        load_schema(entry_path)


def test_graph_schema_loading_fails_closed_without_configuration(
    tmp_path: Path,
) -> None:
    schema_root = tmp_path / "schemas"
    _write_unconfigured_annotated_schema(schema_root)
    module_root = tmp_path / "skills" / "demo-skill"
    module_root.mkdir(parents=True)
    (module_root / "SKILL.md").write_text("# Demo skill\n", encoding="utf-8")
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(_module(), sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(BlueprintGraphError, match="config.yaml is missing"):
        load_module_blueprint(
            tmp_path,
            module_root,
            schema_root=schema_root,
            expected_schema_version=6,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "unknown-domain"),
        ("topics", ["unknown-topic"]),
        ("visibility", "unknown-visibility"),
    ],
)
def test_configured_module_rejects_unknown_catalog_values(
    field: str,
    value: object,
) -> None:
    document = _module()
    document["discovery"]["catalog"][field] = value

    assert _messages(document)


def test_configured_module_rejects_unknown_activation_source() -> None:
    document = _module()
    document["discovery"]["activated_by"] = ["daemon-magic"]

    assert _messages(document)


def test_persistent_modifier_requires_reasoning_control_topic() -> None:
    document = _module()
    document["discovery"]["persistent_modifier"] = True

    assert _messages(document)

    document["discovery"]["catalog"]["topics"].append("reasoning-control")
    _validator().validate(document)


def test_new_module_format_rejects_legacy_discovery_tags() -> None:
    document = deepcopy(_module())
    document.update(
        category="research-assistant",
        role="math-reasoning",
        kind="reviewer",
    )

    assert _messages(document)


def test_repository_graph_preserves_validated_discovery_metadata(
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "skills" / "demo-skill"
    module_root.mkdir(parents=True)
    (module_root / "SKILL.md").write_text("# Demo skill\n", encoding="utf-8")
    declaration = _module()
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(declaration, sort_keys=False),
        encoding="utf-8",
    )

    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=BLUEPRINT_ROOT,
        expected_schema_version=6,
    )

    assert graph.nodes["demo-skill"].declaration["discovery"] == declaration["discovery"]
