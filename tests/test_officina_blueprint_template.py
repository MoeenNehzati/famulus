from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.blueprint_template import (  # noqa: E402
    load_schema,
    refresh_blueprint_documentation,
    render_blueprint_template,
    write_regenerated_skill_blueprint,
)


def _schema() -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "description": "Blueprint fixture.",
        "type": "object",
        "required": ["name", "interfaces"],
        "additionalProperties": False,
        "properties": {
            "name": {
                "type": "string",
                "description": "Old schema name documentation.",
                "x-famulus": {
                    "doc": {
                        "authoring": ["Use the skill directory name."],
                        "red_flags": ["Using a display title."],
                    },
                    "template": {"include": True, "example": "demo-skill"},
                },
            },
            "interfaces": {
                "type": "object",
                "required": ["llm"],
                "properties": {
                    "llm": {"$ref": "#/definitions/llmInterfaceMap"},
                },
            },
        },
        "definitions": {
            "llmInterfaceMap": {
                "type": "object",
                "required": ["default"],
                "additionalProperties": {"$ref": "#/definitions/llmInterface"},
            },
            "llmInterface": {
                "type": "object",
                "required": ["version", "description", "behavior_sources"],
                "properties": {
                    "version": {
                        "type": "integer",
                        "description": "Interface contract version.",
                        "default": 1,
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line interface summary.",
                        "x-famulus": {"template": {"include": True, "example": "Primary instructions."}},
                    },
                    "binding": {"$ref": "#/definitions/llmBinding"},
                    "behavior_sources": {
                        "type": "array",
                        "description": "Non-code files that shape behavior.",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
                "allOf": [{"oneOf": [{"required": ["binding"]}, {"required": ["file"]}]}],
            },
            "llmBinding": {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["kind", "path"],
                        "properties": {
                            "kind": {"const": "skill_file"},
                            "path": {"const": "SKILL.md"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["kind", "uri"],
                        "properties": {
                            "kind": {"const": "uri"},
                            "uri": {"type": "string", "examples": ["https://example.test/prompt"]},
                        },
                    },
                ]
            },
        },
    }


def test_template_renderer_uses_schema_docs_and_examples() -> None:
    text = render_blueprint_template(_schema())

    assert "# Generated documentation comments." in text
    assert "# @schema-doc path=name" in text
    assert "# @summary Old schema name documentation." in text
    assert "Old schema name documentation." in text
    assert "# @authoring Use the skill directory name." in text
    assert "# @red-flag Using a display title." in text

    loaded = yaml.safe_load(text)
    assert loaded == {
        "name": "demo-skill",
        "interfaces": {
            "llm": {
                "default": {
                    "version": 1,
                    "description": "Primary instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "behavior_sources": [],
                }
            }
        },
    }


def test_refresh_replaces_stale_docs_but_preserves_values() -> None:
    old_template = render_blueprint_template(_schema())
    values = yaml.safe_load(old_template)
    values["name"] = "custom-skill"
    values["interfaces"]["llm"]["default"]["description"] = "Custom summary."
    stale_yaml = old_template.replace("demo-skill", "custom-skill").replace(
        "Primary instructions.", "Custom summary."
    )

    updated_schema = deepcopy(_schema())
    updated_schema["properties"]["name"]["description"] = "Fresh schema name documentation."

    refreshed = refresh_blueprint_documentation(updated_schema, stale_yaml)

    assert "Fresh schema name documentation." in refreshed
    assert "Old schema name documentation." not in refreshed
    assert yaml.safe_load(refreshed) == values


def test_refresh_discards_existing_yaml_comments() -> None:
    text = "# user note that should not survive\nname: custom-skill\ninterfaces:\n  # nested note\n  llm:\n    default:\n      version: 2\n      description: Custom.\n      binding:\n        kind: skill_file\n        path: SKILL.md\n      behavior_sources: []\n"

    refreshed = refresh_blueprint_documentation(_schema(), text)

    assert "user note that should not survive" not in refreshed
    assert "nested note" not in refreshed
    assert "# @schema-doc path=name" in refreshed
    assert yaml.safe_load(refreshed)["name"] == "custom-skill"


def test_compact_doc_mode_omits_authoring_and_red_flags() -> None:
    text = render_blueprint_template(_schema(), doc_mode="compact")

    assert "# @schema-doc path=name" in text
    assert "# @summary Old schema name documentation." in text
    assert "# @authoring" not in text
    assert "# @red-flag" not in text
    assert yaml.safe_load(text)["name"] == "demo-skill"


def test_long_strings_render_as_folded_scalars_without_changing_values() -> None:
    schema = _schema()
    values = yaml.safe_load(render_blueprint_template(schema))
    long_description = (
        "This is a deliberately long interface description that should wrap as a "
        "folded YAML scalar while preserving the parsed string value."
    )
    values["interfaces"]["llm"]["default"]["description"] = long_description

    refreshed = refresh_blueprint_documentation(schema, yaml.safe_dump(values, sort_keys=False))

    assert "description: >-" in refreshed
    assert yaml.safe_load(refreshed)["interfaces"]["llm"]["default"]["description"] == long_description


def test_hyphenated_long_strings_do_not_change_on_refresh() -> None:
    schema = _schema()
    values = yaml.safe_load(render_blueprint_template(schema))
    value = (
        "Use the diff-fenced output mode when a caller asks for markdown output "
        "that can be relayed without extra rewriting."
    )
    values["interfaces"]["llm"]["default"]["description"] = value

    refreshed = refresh_blueprint_documentation(schema, yaml.safe_dump(values, sort_keys=False))

    assert yaml.safe_load(refreshed)["interfaces"]["llm"]["default"]["description"] == value
    assert "diff- fenced" not in refreshed


def test_dynamic_mapping_numeric_keys_are_preserved() -> None:
    schema = {
        "type": "object",
        "properties": {
            "patterns": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            }
        },
    }
    text = "patterns:\n  0: '^.+$'\n"

    refreshed = refresh_blueprint_documentation(schema, text)

    assert yaml.safe_load(refreshed) == yaml.safe_load(text)


def test_refresh_preserves_extra_valid_fields_at_the_end() -> None:
    schema = _schema()
    schema["additionalProperties"] = True
    text = "name: custom-skill\nextra: kept\ninterfaces:\n  llm:\n    default:\n      version: 2\n      description: Custom.\n      binding:\n        kind: skill_file\n        path: SKILL.md\n      behavior_sources: []\n"

    refreshed = refresh_blueprint_documentation(schema, text)

    assert yaml.safe_load(refreshed)["extra"] == "kept"
    assert refreshed.rstrip().endswith("extra: kept")


def test_live_schema_template_renders_parseable_yaml() -> None:
    schema = load_schema(Path("references/blueprint/schema.json"))

    text = render_blueprint_template(schema)

    loaded = yaml.safe_load(text)
    assert loaded["category"]
    assert loaded["interfaces"]["llm"]["default"]["version"] == 1


def test_generated_templates_validate_against_live_and_annotated_schemas() -> None:
    for path in [
        Path("references/blueprint/schema.json"),
        Path("references/blueprint/schema.annotated-draft.json"),
    ]:
        schema = load_schema(path)
        for doc_mode in ["full", "compact"]:
            text = render_blueprint_template(schema, doc_mode=doc_mode)
            jsonschema.Draft7Validator(schema).validate(yaml.safe_load(text))


def test_real_blueprint_refresh_preserves_loaded_values() -> None:
    schema = load_schema(Path("references/blueprint/schema.annotated-draft.json"))
    for path in [
        Path("skills/list-manager/blueprint.yaml"),
        Path("skills/g-calendar/blueprint.yaml"),
        Path("skills/email-triage/blueprint.yaml"),
    ]:
        original = path.read_text(encoding="utf-8")
        refreshed = refresh_blueprint_documentation(schema, original, doc_mode="compact")
        assert yaml.safe_load(refreshed) == yaml.safe_load(original)


def test_write_regenerated_skill_blueprint_writes_tmp_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "demo-skill"
    schema_dir = repo / "references" / "blueprint"
    skill_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    schema = _schema()
    (schema_dir / "schema.annotated-draft.json").write_text(__import__("json").dumps(schema), encoding="utf-8")
    original = render_blueprint_template(schema, doc_mode="compact")
    (skill_dir / "blueprint.yaml").write_text(original, encoding="utf-8")

    output = write_regenerated_skill_blueprint("demo-skill", repo_root=repo, output_dir=tmp_path)

    assert output == tmp_path / "demo-skill_blueprint.yaml"
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == yaml.safe_load(original)
    assert "# @schema-doc path=name" in output.read_text(encoding="utf-8")
