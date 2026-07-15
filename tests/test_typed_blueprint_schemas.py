from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"


def _load(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _validator(name: str = "schema.json") -> jsonschema.Draft7Validator:
    schema = _load(name)
    store = {
        child.relative_to(SCHEMA_ROOT).as_posix(): json.loads(
            child.read_text(encoding="utf-8")
        )
        for child in SCHEMA_ROOT.rglob("*.schema.json")
    }
    store.update({(SCHEMA_ROOT / key).resolve().as_uri(): value for key, value in store.items()})
    resolver = jsonschema.RefResolver(
        base_uri=(SCHEMA_ROOT / name).resolve().as_uri(),
        referrer=schema,
        store=store,
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _errors(document: dict, name: str = "schema.json") -> list[str]:
    return [error.message for error in _validator(name).iter_errors(document)]


def _empty_io() -> dict:
    return {"reads": [], "writes": [], "network": []}


@pytest.fixture
def health_validator() -> jsonschema.Draft7Validator:
    return _validator("health.schema.json")


@pytest.fixture
def node_health() -> dict:
    digest = "sha256:" + "a" * 64
    return {
        "health_schema_version": 1,
        "record_type": "node-health",
        "subject": {
            "id": "demo-skill.llm.default",
            "blueprint_type": "llm-interface",
            "version": 1,
            "blueprint_path": ".SKILL.md.blueprint.yaml",
            "binding_path": "SKILL.md",
        },
        "certification": {"result": "passed", "certified_at": "2026-07-13T00:00:00Z"},
        "certifier": {"interface": "skill-audit.machine.certify", "version": 1},
        "hashes": {
            "blueprint_file_hash": digest,
            "blueprint_contract_hash": digest,
            "bound_file_hash": digest,
            "local_hash": digest,
            "downstream_artifact_hash": digest,
            "artifact_graph_hash": digest,
            "downstream_health_hash": digest,
            "certified_health_hash": digest,
            "schema_hash": digest,
            "policy_hash": digest,
        },
        "dependencies": [],
        "checks": [{"id": "schema", "version": 1, "passed": True, "findings": []}],
        "coverage": {},
        "record_hash": digest,
        "authentication": {
            "scheme": "hmac-sha256",
            "key_id": "sha256:" + "a" * 16,
            "mac": "base64:" + "a" * 43 + "=",
        },
    }


def test_dispatch_schema_still_accepts_live_legacy_blueprints() -> None:
    document = yaml.safe_load((REPO_ROOT / "skills" / "skill-drift" / "blueprint.yaml").read_text())
    assert _errors(document) == []


def test_frozen_legacy_schema_retains_pre_typed_contract() -> None:
    content = (SCHEMA_ROOT / "legacy-skill.schema.json").read_bytes()

    assert hashlib.sha256(content).hexdigest() == (
        "170c42096ad03a071b100bb08fbf31d21788ff0a062eb277328f49ece3d54554"
    )


def test_health_schema_fixes_certifier_interface_version() -> None:
    certifier = _load("health.schema.json")["definitions"]["certifier"]

    assert certifier["properties"]["version"] == {"const": 1}


def test_node_health_requires_commit_backed_source(health_validator, node_health) -> None:
    node_health["source"] = {
        "vcs": "git",
        "commit": "a" * 40,
        "input_paths": ["skills/demo/SKILL.md", "skills/demo/.SKILL.md.blueprint.yaml"],
    }
    health_validator.validate(node_health)


def test_node_health_rejects_missing_source(health_validator, node_health) -> None:
    with pytest.raises(jsonschema.ValidationError):
        health_validator.validate(node_health)


def test_dispatch_schema_accepts_typed_skill_root() -> None:
    document = {
        "schema_version": 2,
        "blueprint_type": "skill",
        "id": "demo-skill",
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "interfaces": [
            {
                "interface": "demo-skill.llm.default",
                "version": 1,
                "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
            }
        ],
    }
    assert _errors(document) == []


def test_typed_skill_root_accepts_exactly_one_default_interface_representation() -> None:
    inline = {
        "schema_version": 2,
        "blueprint_type": "skill",
        "id": "demo-skill",
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "default_interface": {
            "version": 1,
            "description": "Primary instructions.",
            "allow_all_skills": True,
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": _empty_io(),
            "owns_filesystem": [],
        },
        "interfaces": [],
    }

    assert _errors(inline, "v2/skill.schema.json") == []

    neither = {key: value for key, value in inline.items() if key != "default_interface"}
    assert _errors(neither, "v2/skill.schema.json")

    both = dict(inline)
    both["interfaces"] = [
        {
            "interface": "demo-skill.llm.default",
            "version": 1,
            "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
        }
    ]
    assert _errors(both, "v2/skill.schema.json")


def test_llm_schema_requires_explicit_file_binding() -> None:
    document = {
        "schema_version": 2,
        "blueprint_type": "llm-interface",
        "id": "demo-skill.llm.default",
        "version": 1,
        "description": "Primary instructions.",
        "binding": {"kind": "instruction-file", "path": "SKILL.md"},
        "allow_all_skills": True,
        "allowed_callers": [],
        "routing_hints": [],
        "uses_interfaces": [],
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }
    assert _errors(document, "v2/llm-interface.schema.json") == []

    del document["binding"]
    assert any(
        "binding" in error
        for error in _errors(document, "v2/llm-interface.schema.json")
    )


def test_default_llm_interface_must_bind_skill_md() -> None:
    document = {
        "schema_version": 2,
        "blueprint_type": "llm-interface",
        "id": "demo-skill.llm.default",
        "version": 1,
        "description": "Primary instructions.",
        "binding": {"kind": "instruction-file", "path": "references/other.md"},
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }

    assert _errors(document, "v2/llm-interface.schema.json")

    document["id"] = "demo-skill.llm.specialized"
    assert _errors(document, "v2/llm-interface.schema.json") == []


def test_machine_schema_accepts_python_and_command_file_bindings_only() -> None:
    base = {
        "schema_version": 2,
        "blueprint_type": "machine-interface",
        "id": "demo-skill.machine.run",
        "version": 1,
        "description": "Run the operation.",
        "usage": "run <path>",
        "allow_all_skills": False,
        "allowed_callers": [],
        "platform_support": {"linux": True, "macos": True, "windows": True},
        "dependencies": [],
        "uses_interfaces": [],
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }
    python_document = {
        **base,
        "binding": {
            "kind": "python-entrypoint",
            "path": "_rtx/_demo_runner.py",
            "symbol": "Interface",
            "args_prefix": [],
        },
    }
    command_document = {
        **base,
        "binding": {"kind": "command-file", "path": "_cx/_demo_run.sh", "args_prefix": []},
    }
    inline_document = {
        **base,
        "binding": {"kind": "command-file", "path": "_cx/_demo_run.sh", "command": "bash -c true"},
    }

    assert _errors(python_document, "v2/machine-interface.schema.json") == []
    assert _errors(command_document, "v2/machine-interface.schema.json") == []
    assert _errors(inline_document, "v2/machine-interface.schema.json")


def test_machine_bindings_reject_parent_traversal() -> None:
    base = {
        "schema_version": 2,
        "blueprint_type": "machine-interface",
        "id": "demo-skill.machine.run",
        "version": 1,
        "description": "Run.",
        "usage": "run",
        "dependencies": [],
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }

    for binding in [
        {"kind": "python-entrypoint", "path": "_rtx/../escape.py", "symbol": "Interface"},
        {"kind": "command-file", "path": "_cx/../escape"},
    ]:
        assert _errors(
            {**base, "binding": binding}, "v2/machine-interface.schema.json"
        )


def test_behavior_source_schema_allows_behavior_source_and_interface_edges() -> None:
    document = {
        "schema_version": 2,
        "blueprint_type": "behavior-source",
        "id": "demo-skill.source.policy",
        "version": 1,
        "description": "Defines policy.",
        "binding": {"kind": "file", "path": "references/policy.md"},
        "content": "config",
        "format": "markdown",
        "uses_behavior_sources": [
            {
                "source": "demo-skill.source.rules",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": "references/.rules.md.blueprint.yaml",
                },
                "reason": "Supplies detailed rules.",
            }
        ],
    }
    assert _errors(document, "v2/behavior-source.schema.json") == []

    document["uses_interfaces"] = [
        {"interface": "other-skill.machine.run", "version": 1}
    ]
    assert _errors(document, "v2/behavior-source.schema.json") == []


def _valid_skill_v3() -> dict:
    return {
        "schema_version": 3,
        "node_type": "skill",
        "id": "demo-skill",
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "gateway": {"kind": "instruction-file", "path": "SKILL.md"},
        "content": [r"SKILL\.md"],
        "default_interface": {
            "version": 1,
            "description": "Primary instructions.",
            "allow_all_skills": True,
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": _empty_io(),
            "owns_filesystem": [],
        },
        "interfaces": [],
    }


def test_version_three_skill_requires_uniform_node_fields() -> None:
    document = _valid_skill_v3()
    assert _errors(document, "skill.schema.json") == []
    assert _errors(document) == []

    for field in ("node_type", "gateway", "content"):
        invalid = dict(document)
        del invalid[field]
        assert any(field in error for error in _errors(invalid, "skill.schema.json"))


@pytest.mark.parametrize(
    "legacy_field",
    ["blueprint_type", "binding", "entry_point", "local_hash_inputs"],
)
def test_version_three_skill_rejects_replaced_aliases(legacy_field: str) -> None:
    document = _valid_skill_v3()
    document[legacy_field] = (
        {} if legacy_field in {"binding", "entry_point"} else []
    )
    assert _errors(document, "skill.schema.json")


def test_version_three_skill_requires_inline_default_without_default_sidecar() -> None:
    document = _valid_skill_v3()
    del document["default_interface"]
    assert _errors(document, "skill.schema.json")

    document = _valid_skill_v3()
    document["interfaces"] = [
        {
            "interface": "demo-skill.llm.default",
            "version": 1,
            "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
        }
    ]
    assert _errors(document, "skill.schema.json")


def test_version_three_llm_interface_uses_gateway_and_content() -> None:
    document = {
        "schema_version": 3,
        "node_type": "llm-interface",
        "id": "demo-skill.llm.specialized",
        "version": 1,
        "description": "Specialized instructions.",
        "gateway": {
            "kind": "instruction-file",
            "path": "llm_interfaces/specialized.md",
        },
        "content": [r"llm_interfaces/specialized\.md"],
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }
    assert _errors(document, "llm-interface.schema.json") == []


def test_version_three_machine_interface_uses_gateway_and_content() -> None:
    document = {
        "schema_version": 3,
        "node_type": "machine-interface",
        "id": "demo-skill.machine.run",
        "version": 1,
        "description": "Run.",
        "gateway": {
            "kind": "python-entrypoint",
            "path": "_rtx/_run.py",
            "symbol": "Interface",
        },
        "content": [r"_rtx/_run\.py", r"_rtx/helpers/.+\.py"],
        "platform_support": {"linux": True, "macos": True, "windows": True},
        "dependencies": [],
        "behavior_sources": [],
        "direct_io": _empty_io(),
        "owns_filesystem": [],
    }
    assert _errors(document, "machine-interface.schema.json") == []


SEMANTIC_TYPES = (
    "policy",
    "instructions",
    "reference",
    "configuration",
    "preference",
    "schema",
    "template",
    "example",
    "checklist",
    "dataset",
)


@pytest.mark.parametrize("semantic_type", SEMANTIC_TYPES)
def test_version_three_behavior_source_uses_closed_semantic_type(
    semantic_type: str,
) -> None:
    document = {
        "schema_version": 3,
        "node_type": "behavior-source",
        "id": "demo-skill.source.policy",
        "version": 1,
        "description": "Defines policy.",
        "gateway": {"kind": "file", "path": "references/policy.md"},
        "content": [r"references/policy\.md"],
        "semantic_type": semantic_type,
        "format": "markdown",
        "uses_behavior_sources": [],
    }
    assert _errors(document, "behavior-source.schema.json") == []

    document["semantic_type"] = "skill"
    assert _errors(document, "behavior-source.schema.json")
