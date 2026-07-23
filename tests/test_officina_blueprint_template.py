from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import yaml
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.blueprint_template import (  # noqa: E402
    load_schema,
    refresh_blueprint_documentation,
    render_blueprint_template,
    schema_validator,
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


def test_live_module_template_renders_parseable_v4_yaml() -> None:
    schema = load_schema(Path("references/blueprint/module.schema.json"))

    text = render_blueprint_template(schema)

    loaded = yaml.safe_load(text)
    assert loaded["schema_version"] == 4
    assert loaded["node_type"] == "module"
    schema_validator(load_schema(Path("references/blueprint/schema.json"))).validate(
        loaded
    )


def test_generated_v4_templates_validate_against_live_authoring_schemas() -> None:
    for path in (
        Path("references/blueprint/module.schema.json"),
        Path("references/blueprint/behavioral-source.schema.json"),
        Path("references/blueprint/schema.annotated-draft.json"),
    ):
        schema = load_schema(path)
        for doc_mode in ["full", "compact"]:
            text = render_blueprint_template(schema, doc_mode=doc_mode)
            schema_validator(schema).validate(yaml.safe_load(text))


def test_annotated_authoring_schema_routes_only_live_v4_nodes() -> None:
    schema = load_schema(Path("references/blueprint/schema.annotated-draft.json"))

    assert [branch["$ref"] for branch in schema["oneOf"]] == [
        "module.schema.json",
        "behavioral-source.schema.json",
    ]
    assert list(
        schema_validator(schema).iter_errors(
            {
                "schema_version": 3,
                "node_type": "skill",
                "id": "example-skill",
            }
        )
    )


def test_each_live_schema_generates_a_valid_authoring_template() -> None:
    for name in [
        "module.schema.json",
        "behavioral-source.schema.json",
    ]:
        schema = load_schema(Path("references/blueprint") / name)
        rendered = render_blueprint_template(schema)
        schema_validator(schema).validate(yaml.safe_load(rendered))


def test_compatibility_entry_rejects_pre_v4_authoring_values() -> None:
    schema = load_schema(Path("references/blueprint/schema.json"))

    with pytest.raises(ValueError, match="module or behavioral_source"):
        refresh_blueprint_documentation(
            schema,
            "schema_version: 3\nnode_type: skill\nid: old\n",
        )


def test_committed_template_describes_the_live_v4_layout() -> None:
    committed = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())

    assert committed["examples"] == {
        "module": "blueprint.yaml",
        "behavioral_sources": [
            "blueprints/gateway.yaml",
            "blueprints/runner.yaml",
        ],
    }
    assert committed["generated_outputs"] == [
        "SKILL.md blueprint contract block",
        "SKILL.md blueprint interface block",
    ]
    assert set(committed) == {"examples", "generated_outputs"}


def test_schema_family_examples_create_complete_valid_documents(tmp_path: Path) -> None:
    examples = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())["examples"]
    skill = tmp_path / "skills" / "example-skill"
    references = tmp_path / "references"

    schemas = {
        name: load_schema(Path("references/blueprint") / name)
        for name in ["module.schema.json", "behavioral-source.schema.json"]
    }
    root = yaml.safe_load(render_blueprint_template(schemas["module.schema.json"]))
    gateway = yaml.safe_load(
        render_blueprint_template(schemas["behavioral-source.schema.json"])
    )
    runner = yaml.safe_load(
        render_blueprint_template(schemas["behavioral-source.schema.json"])
    )

    root.update(
        {
            "id": "example-skill",
            "content": ["SKILL\\.md", "_rtx/_runner\\.py"],
            "sources": {
                "example-skill.source.gateway": {
                    "blueprint": {
                        "base": "module-root",
                        "path": examples["behavioral_sources"][0],
                    }
                },
                "example-skill.source.runner": {
                    "blueprint": {
                        "base": "module-root",
                        "path": examples["behavioral_sources"][1],
                    }
                },
            },
            "exports": {
                "example-skill.interface.default": {
                    "source_interface": (
                        "example-skill.source.gateway.interface.default"
                    ),
                    "access": {
                        "allow_all_modules": True,
                        "allowed_callers": [],
                    },
                },
                "example-skill.interface.run": {
                    "source_interface": "example-skill.source.runner.interface.run",
                    "access": {
                        "allow_all_modules": False,
                        "allowed_callers": [],
                    },
                },
            },
        }
    )
    gateway.update(
        {
            "id": "example-skill.source.gateway",
            "interfaces": {
                "example-skill.source.gateway.interface.default": {"version": 1}
            },
        }
    )
    runner.update(
        {
            "id": "example-skill.source.runner",
            "gateway": {"path": "_rtx/_runner.py", "language": "Python>=3.11"},
            "content": ["_rtx/_runner\\.py"],
            "interfaces": {
                "example-skill.source.runner.interface.run": {"version": 1}
            },
        }
    )
    documents = [
        (schemas["module.schema.json"], root, skill / examples["module"]),
        (
            schemas["behavioral-source.schema.json"],
            gateway,
            skill / examples["behavioral_sources"][0],
        ),
        (
            schemas["behavioral-source.schema.json"],
            runner,
            skill / examples["behavioral_sources"][1],
        ),
    ]
    for schema, document, path in documents:
        schema_validator(schema).validate(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    schema_validator(load_schema(Path("references/blueprint/schema.json"))).validate(root)

    skill_md = skill / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(
        "---\nname: example-skill\n---\n"
        "<!-- BEGIN BLUEPRINT CONTRACT -->\n"
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.\n"
        "<!-- END BLUEPRINT CONTRACT -->\n"
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.\n"
        "<!-- END BLUEPRINT INTERFACES -->\n"
        "Hand-authored instructions.\n",
        encoding="utf-8",
    )
    runner_path = skill / "_rtx" / "_runner.py"
    runner_path.parent.mkdir(parents=True, exist_ok=True)
    runner_path.write_text("class Interface: pass\n", encoding="utf-8")
    source_schema_root = Path("references/blueprint")
    fixture_schema_root = references / "blueprint"
    for source_path in [
        *source_schema_root.glob("*.schema.json"),
        source_schema_root / "schema.annotated-draft.json",
        source_schema_root / "schema.json",
        source_schema_root / "schema-meta.json",
        source_schema_root / "template.yaml",
    ]:
        destination = fixture_schema_root / source_path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_path.read_bytes())

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "skills", "references"], cwd=tmp_path, check=True)

    generated_outputs = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())["generated_outputs"]
    assert generated_outputs == [
        "SKILL.md blueprint contract block",
        "SKILL.md blueprint interface block",
    ]
    assert "<!-- BEGIN BLUEPRINT CONTRACT -->" in skill_md.read_text(encoding="utf-8")
    assert "<!-- BEGIN BLUEPRINT INTERFACES -->" in skill_md.read_text(encoding="utf-8")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert {
        "skills/example-skill/SKILL.md",
        "skills/example-skill/blueprint.yaml",
        "skills/example-skill/blueprints/gateway.yaml",
        "skills/example-skill/blueprints/runner.yaml",
        "skills/example-skill/_rtx/_runner.py",
    } <= set(tracked)

    assert all(path.is_file() for _, _, path in documents)


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

    output = write_regenerated_skill_blueprint(
        "demo-skill",
        repo_root=repo,
        output_dir=tmp_path,
        schema_path=schema_dir / "schema.annotated-draft.json",
    )

    assert output == tmp_path / "demo-skill_blueprint.yaml"
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == yaml.safe_load(original)
    assert "# @schema-doc path=name" in output.read_text(encoding="utf-8")


def test_regeneration_rejects_pre_v4_blueprints(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill_dir = repo / "skills" / "demo-skill"
    schema_dir = repo / "references" / "blueprint"
    skill_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    blueprint = {"schema_version": 2, "blueprint_type": "skill", "id": "demo-skill"}
    (skill_dir / "blueprint.yaml").write_text(yaml.safe_dump(blueprint), encoding="utf-8")

    def authoring_schema(marker: str) -> dict:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["schema_version", "blueprint_type", "id"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": 2, "description": marker},
                "blueprint_type": {"const": "skill"},
                "id": {"type": "string"},
            },
        }

    (schema_dir / "schema.annotated-draft.json").write_text(
        __import__("json").dumps(authoring_schema("legacy marker")), encoding="utf-8"
    )
    (schema_dir / "skill.schema.json").write_text(
        __import__("json").dumps(authoring_schema("typed marker")), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="schema_version 4"):
        write_regenerated_skill_blueprint(
            "demo-skill",
            repo_root=repo,
            output_dir=tmp_path,
        )


def test_v4_regeneration_selects_existing_module_schema_owner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    module_dir = repo / "skills" / "demo-skill"
    schema_dir = repo / "references" / "blueprint"
    module_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    blueprint = {
        "schema_version": 4,
        "node_type": "module",
        "id": "demo-skill",
    }
    (module_dir / "blueprint.yaml").write_text(
        yaml.safe_dump(blueprint), encoding="utf-8"
    )

    def authoring_schema(marker: str, version: int, node_type: str) -> dict:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["schema_version", "node_type", "id"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": version, "description": marker},
                "node_type": {"const": node_type},
                "id": {"type": "string"},
            },
        }

    (schema_dir / "schema.annotated-draft.json").write_text(
        __import__("json").dumps(authoring_schema("legacy marker", 3, "skill")),
        encoding="utf-8",
    )
    (schema_dir / "module.schema.json").write_text(
        __import__("json").dumps(authoring_schema("module marker", 4, "module")),
        encoding="utf-8",
    )

    output = write_regenerated_skill_blueprint(
        "demo-skill", repo_root=repo, output_dir=tmp_path
    )

    text = output.read_text(encoding="utf-8")
    assert "module marker" in text
    assert "legacy marker" not in text
