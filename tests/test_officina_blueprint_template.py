from __future__ import annotations

import sys
from copy import deepcopy
from functools import cache
from pathlib import Path

import yaml
import pytest
from test_support.git_repository import GitTestRepository

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import officina.common.blueprint_template as blueprint_template  # noqa: E402
from officina.common.blueprint_graph import (  # noqa: E402
    load_repository_blueprint_graph,
)
from officina.common.blueprint_template import (  # noqa: E402
    load_schema as _load_schema,
    refresh_blueprint_documentation,
    render_blueprint_template,
    schema_validator,
    write_regenerated_skill_blueprint,
)


@cache
def load_schema(path: str | Path):
    """Reuse read-only schema bundles by their exact requested path."""

    return _load_schema(path)


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


def test_live_module_template_renders_parseable_v6_yaml() -> None:
    schema = load_schema(Path("references/blueprint/module.schema.json"))

    text = render_blueprint_template(schema)

    loaded = yaml.safe_load(text)
    assert loaded["schema_version"] == 6
    assert loaded["node_type"] == "module"
    schema_validator(load_schema(Path("references/blueprint/schema.json"))).validate(
        loaded
    )


def test_generated_v5_templates_validate_against_live_authoring_schemas() -> None:
    for path in (
        Path("references/blueprint/module.schema.json"),
        Path("references/blueprint/behavioral-source.schema.json"),
        Path("references/blueprint/schema.annotated-draft.json"),
    ):
        schema = load_schema(path)
        for doc_mode in ["full", "compact"]:
            text = render_blueprint_template(schema, doc_mode=doc_mode)
            schema_validator(schema).validate(yaml.safe_load(text))


def test_annotated_authoring_schema_routes_only_live_v5_nodes() -> None:
    schema = load_schema(Path("references/blueprint/schema.annotated-draft.json"))

    assert schema["$ref"].endswith("schema.json")
    assert "Canonical authoring entry point" in schema["description"]
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


def test_compatibility_entry_rejects_pre_v5_authoring_values() -> None:
    schema = load_schema(Path("references/blueprint/schema.json"))

    with pytest.raises(ValueError, match="module or behavioral_source"):
        refresh_blueprint_documentation(
            schema,
            "schema_version: 3\nnode_type: skill\nid: old\n",
        )


def test_committed_template_is_a_complete_live_v6_module() -> None:
    committed = yaml.safe_load(Path("references/blueprint/template.yaml").read_text())

    assert committed["schema_version"] == 6
    assert committed["node_type"] == "module"
    assert committed["children"] == {}
    assert committed["namespace_exports"] == {}
    schema_validator(
        load_schema(Path("references/blueprint/module.schema.json"))
    ).validate(committed)


def test_schema_family_examples_create_complete_valid_documents(tmp_path: Path) -> None:
    del tmp_path
    for name in ("module.schema.json", "behavioral-source.schema.json"):
        schema = load_schema(Path("references/blueprint") / name)
        rendered = yaml.safe_load(render_blueprint_template(schema))
        schema_validator(schema).validate(rendered)


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


def test_regeneration_rejects_pre_v5_blueprints(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="schema_version 5"):
        write_regenerated_skill_blueprint(
            "demo-skill",
            repo_root=repo,
            output_dir=tmp_path,
        )


def test_v5_regeneration_selects_existing_module_schema_owner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    module_dir = repo / "skills" / "demo-skill"
    schema_dir = repo / "references" / "blueprint" / "migrations" / "v5"
    module_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    blueprint = {
        "schema_version": 5,
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
        __import__("json").dumps(authoring_schema("module marker", 5, "module")),
        encoding="utf-8",
    )

    output = write_regenerated_skill_blueprint(
        "demo-skill", repo_root=repo, output_dir=tmp_path
    )

    text = output.read_text(encoding="utf-8")
    assert "module marker" in text
    assert "legacy marker" not in text


def test_v5_repository_managed_skill_generator_creates_parent_and_code_child(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    schema_root = Path("references/blueprint").resolve()
    skill_file = repo / "skills" / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\nDemo.\n",
        encoding="utf-8",
    )

    outputs = blueprint_template.write_repository_managed_skill_blueprints(
        "demo-skill",
        domain="assistant-development",
        topics=("assistant-authoring",),
        visibility="listed",
        activated_by=("user-request",),
        persistent_modifier=False,
        repo_root=repo,
        schema_root=schema_root,
        include_code_child=True,
    )

    parent_path = repo / "skills" / "demo-skill" / "blueprint.yaml"
    child_path = repo / "skills" / "demo-skill" / "_rtx" / "blueprint.yaml"
    init_path = repo / "skills" / "demo-skill" / "_rtx" / "__init__.py"
    assert outputs == (parent_path, child_path, init_path)
    assert all(path.is_file() for path in outputs)

    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    child = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    assert parent["id"] == "demo-skill"
    assert parent["discovery"] == {
        "mechanism": "skill",
        "catalog": {
            "domain": "assistant-development",
            "topics": ["assistant-authoring"],
            "visibility": "listed",
        },
        "activated_by": ["user-request"],
        "persistent_modifier": False,
    }
    assert parent["children"] == {"_rtx": {}}
    assert child["id"] == "demo-skill._rtx"
    assert "discovery" not in child
    assert child["gateway"] == {
        "path": "__init__.py",
        "language": "Python>=3.11",
    }
    assert init_path.read_text(encoding="utf-8") == ""
    validator = schema_validator(load_schema(schema_root / "module.schema.json"))
    validator.validate(parent)
    validator.validate(child)
    graph = load_repository_blueprint_graph(
        repo,
        schema_root=schema_root,
        expected_schema_version=6,
    )
    assert set(graph.nodes) == {"demo-skill", "demo-skill._rtx"}
    assert graph.module_children == {
        "demo-skill": ("demo-skill._rtx",),
        "demo-skill._rtx": (),
    }


def test_v5_repository_managed_skill_generator_defaults_to_parent_only(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    skill_root = repo / "skills" / "instruction-only"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: instruction-only\n---\nInstructions.\n",
        encoding="utf-8",
    )

    outputs = blueprint_template.write_repository_managed_skill_blueprints(
        "instruction-only",
        domain="assistant-development",
        topics=("assistant-authoring",),
        visibility="listed",
        activated_by=("user-request",),
        persistent_modifier=False,
        repo_root=repo,
        schema_root=Path("references/blueprint").resolve(),
    )

    assert outputs == (skill_root / "blueprint.yaml",)
    assert not (skill_root / "_rtx").exists()
    graph = load_repository_blueprint_graph(
        repo,
        schema_root=Path("references/blueprint").resolve(),
        expected_schema_version=6,
    )
    assert set(graph.nodes) == {"instruction-only"}


def test_v5_repository_managed_skill_generator_requires_parent_skill_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    skill_root = repo / "skills" / "demo-skill"

    with pytest.raises(
        FileNotFoundError,
        match=r"missing parent SKILL\.md",
    ):
        blueprint_template.write_repository_managed_skill_blueprints(
            "demo-skill",
            domain="assistant-development",
            topics=("assistant-authoring",),
            visibility="listed",
            activated_by=("user-request",),
            persistent_modifier=False,
            repo_root=repo,
            schema_root=Path("references/blueprint").resolve(),
            include_code_child=True,
        )

    assert not (skill_root / "blueprint.yaml").exists()
    assert not (skill_root / "_rtx" / "blueprint.yaml").exists()
    assert not (skill_root / "_rtx" / "__init__.py").exists()


def test_v5_repository_managed_skill_generator_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    skill_root = repo / "skills" / "demo-skill"
    skill_file = skill_root / "SKILL.md"
    child_root = skill_root / "_rtx"
    parent_path = skill_root / "blueprint.yaml"
    child_path = child_root / "blueprint.yaml"
    init_path = child_root / "__init__.py"
    outputs = (parent_path, child_path, init_path)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\nDemo.\n",
        encoding="utf-8",
    )

    original_write_text = Path.write_text
    failed = False

    def fail_child_write_once(
        path: Path,
        data: str,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal failed
        if path == child_path and not failed:
            failed = True
            assert parent_path.is_file()
            original_write_text(path, "partial", *args, **kwargs)
            raise OSError("injected child blueprint write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_child_write_once)

    with pytest.raises(OSError, match="injected child blueprint write failure"):
        blueprint_template.write_repository_managed_skill_blueprints(
            "demo-skill",
            domain="assistant-development",
            topics=("assistant-authoring",),
            visibility="listed",
            activated_by=("user-request",),
            persistent_modifier=False,
            repo_root=repo,
            schema_root=Path("references/blueprint").resolve(),
            include_code_child=True,
        )

    assert failed
    assert not any(path.exists() for path in outputs)
    assert not child_root.exists()

    retried = blueprint_template.write_repository_managed_skill_blueprints(
        "demo-skill",
        domain="assistant-development",
        topics=("assistant-authoring",),
        visibility="listed",
        activated_by=("user-request",),
        persistent_modifier=False,
        repo_root=repo,
        schema_root=Path("references/blueprint").resolve(),
        include_code_child=True,
    )

    assert retried == outputs
    assert all(path.is_file() for path in outputs)


def test_v5_repository_managed_skill_generator_preserves_preexisting_child_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    skill_root = repo / "skills" / "demo-skill"
    skill_file = skill_root / "SKILL.md"
    child_root = skill_root / "_rtx"
    parent_path = skill_root / "blueprint.yaml"
    child_path = child_root / "blueprint.yaml"
    init_path = child_root / "__init__.py"
    outputs = (parent_path, child_path, init_path)
    child_root.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: demo-skill\n---\nDemo.\n",
        encoding="utf-8",
    )
    user_file = child_root / "user.py"
    user_file.write_text("USER_VALUE = 1\n", encoding="utf-8")

    original_write_text = Path.write_text

    def fail_child_write(
        path: Path,
        data: str,
        *args: object,
        **kwargs: object,
    ) -> int:
        if path == child_path:
            original_write_text(path, "partial", *args, **kwargs)
            raise OSError("injected child blueprint write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_child_write)

    with pytest.raises(OSError, match="injected child blueprint write failure"):
        blueprint_template.write_repository_managed_skill_blueprints(
            "demo-skill",
            domain="assistant-development",
            topics=("assistant-authoring",),
            visibility="listed",
            activated_by=("user-request",),
            persistent_modifier=False,
            repo_root=repo,
            schema_root=Path("references/blueprint").resolve(),
            include_code_child=True,
        )

    assert child_root.is_dir()
    assert user_file.read_text(encoding="utf-8") == "USER_VALUE = 1\n"
    assert not any(path.exists() for path in outputs)
