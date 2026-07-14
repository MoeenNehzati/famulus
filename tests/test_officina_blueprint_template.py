from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.blueprint_template import (  # noqa: E402
    load_schema,
    refresh_blueprint_documentation,
    render_blueprint_template,
    schema_validator,
    write_regenerated_skill_blueprint,
)
from officina.common.blueprint_graph import load_skill_blueprint_graph  # noqa: E402


def _load_repository_validator(relative_path: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            schema_validator(schema).validate(yaml.safe_load(text))


def test_each_typed_schema_generates_a_valid_authoring_template() -> None:
    for name in [
        "skill.schema.json",
        "llm-interface.schema.json",
        "machine-interface.schema.json",
        "behavior-source.schema.json",
    ]:
        schema = load_schema(Path("references/blueprint") / name)
        rendered = render_blueprint_template(schema)
        schema_validator(schema).validate(yaml.safe_load(rendered))


def test_committed_typed_skill_template_matches_schema_generated_values() -> None:
    committed = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())

    assert committed["examples"] == {
        "skill_root": "blueprint.yaml",
        "default_llm": ".SKILL.md.blueprint.yaml",
        "shared_python_interfaces": [
            "_rtx/._runner.py.first.blueprint.yaml",
            "_rtx/._runner.py.second.blueprint.yaml",
        ],
        "command_interface": "_cx/._command.blueprint.yaml",
        "repository_behavior_source": "references/.policy.md.blueprint.yaml",
    }
    assert committed["generated_outputs"] == [
        "SKILL.md blueprint contract block",
        "SKILL.md blueprint interface block",
    ]


def test_schema_family_examples_create_a_complete_valid_graph(tmp_path: Path) -> None:
    examples = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())["examples"]
    skill = tmp_path / "skills" / "example-skill"
    references = tmp_path / "references"

    schemas = {
        name: load_schema(Path("references/blueprint") / name)
        for name in [
            "skill.schema.json",
            "llm-interface.schema.json",
            "machine-interface.schema.json",
            "behavior-source.schema.json",
        ]
    }
    root = yaml.safe_load(render_blueprint_template(schemas["skill.schema.json"]))
    llm = yaml.safe_load(render_blueprint_template(schemas["llm-interface.schema.json"]))
    first = yaml.safe_load(render_blueprint_template(schemas["machine-interface.schema.json"]))
    second = deepcopy(first)
    command = deepcopy(first)
    source = yaml.safe_load(render_blueprint_template(schemas["behavior-source.schema.json"]))

    root["id"] = "example-skill"
    root["interfaces"] = [
        {"interface": "example-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": examples["default_llm"]}},
        {"interface": "example-skill.machine.first", "version": 1, "blueprint": {"base": "skill-root", "path": examples["shared_python_interfaces"][0]}},
        {"interface": "example-skill.machine.second", "version": 1, "blueprint": {"base": "skill-root", "path": examples["shared_python_interfaces"][1]}},
        {"interface": "example-skill.machine.command", "version": 1, "blueprint": {"base": "skill-root", "path": examples["command_interface"]}},
    ]
    llm["id"] = "example-skill.llm.default"
    llm["behavior_sources"] = [{
        "source": "references.source.policy",
        "version": 1,
        "blueprint": {"base": "repository-root", "path": examples["repository_behavior_source"]},
        "reason": "Supplies shared policy.",
    }]
    for document, name in [(first, "first"), (second, "second")]:
        document["id"] = f"example-skill.machine.{name}"
        document["binding"] = {
            "kind": "python-entrypoint",
            "path": "_rtx/_runner.py",
            "symbol": "Interface",
            "args_prefix": [],
        }
        document["usage"] = ""
    command["id"] = "example-skill.machine.command"
    command["binding"] = {"kind": "command-file", "path": "_cx/_command", "args_prefix": []}
    command["usage"] = ""
    source["id"] = "references.source.policy"
    source["binding"] = {"kind": "file", "path": "references/policy.md"}

    documents = [
        (schemas["skill.schema.json"], root, skill / examples["skill_root"]),
        (schemas["llm-interface.schema.json"], llm, skill / examples["default_llm"]),
        (schemas["machine-interface.schema.json"], first, skill / examples["shared_python_interfaces"][0]),
        (schemas["machine-interface.schema.json"], second, skill / examples["shared_python_interfaces"][1]),
        (schemas["machine-interface.schema.json"], command, skill / examples["command_interface"]),
        (schemas["behavior-source.schema.json"], source, references / ".policy.md.blueprint.yaml"),
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
    runner = skill / "_rtx" / "_runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("class Interface: pass\n", encoding="utf-8")
    command_path = skill / "_cx" / "_command"
    command_path.parent.mkdir(parents=True, exist_ok=True)
    command_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command_path.chmod(0o755)
    policy = references / "policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("Generated policy fixture.\n", encoding="utf-8")
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
    assert os.access(command_path, os.X_OK)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert {"skills/example-skill/SKILL.md", "skills/example-skill/_cx/_command"} <= set(tracked)

    graph = load_skill_blueprint_graph(skill)
    assert set(graph.nodes) == {
        "example-skill",
        "example-skill.llm.default",
        "example-skill.machine.first",
        "example-skill.machine.second",
        "example-skill.machine.command",
        "references.source.policy",
    }
    catalog = json.loads(
        Path("references/blueprint/schema-meta.json").read_text(encoding="utf-8")
    )["x-famulus"]["validation_rule_catalog"]
    graph_acceptance_paths = {
        rule["validator"]
        for rule in catalog.values()
        if rule["enforcement"]["acceptance"] == "isolated-graph"
    }
    assert graph_acceptance_paths == {
        "src/officina/common/artifact_health.py",
        "src/officina/common/blueprint_graph.py",
        "src/officina/common/pooled_blueprint.py",
    }
    acceptance_paths = {
        rule["validator"]
        for rule in catalog.values()
        if rule["enforcement"]["acceptance"] == "isolated-validator"
    }
    assert acceptance_paths == {
        "skills/skill-maker/validators/blueprint_relationships.py",
        "skills/skill-maker/validators/blueprints.py",
        "skills/skill-maker/validators/dependencies.py",
        "skills/skill-maker/validators/interface_ids.py",
    }
    for path in sorted(acceptance_paths):
        assert _load_repository_validator(path).validate(tmp_path) == []
    assert _load_repository_validator(
        "skills/skill-maker/validators/skill_body_execution.py"
    ).validate(tmp_path) == []
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

    output = write_regenerated_skill_blueprint("demo-skill", repo_root=repo, output_dir=tmp_path)

    assert output == tmp_path / "demo-skill_blueprint.yaml"
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == yaml.safe_load(original)
    assert "# @schema-doc path=name" in output.read_text(encoding="utf-8")


def test_typed_regeneration_selects_its_concrete_authoring_schema(tmp_path: Path) -> None:
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

    output = write_regenerated_skill_blueprint("demo-skill", repo_root=repo, output_dir=tmp_path)

    text = output.read_text(encoding="utf-8")
    assert "typed marker" in text
    assert "legacy marker" not in text
