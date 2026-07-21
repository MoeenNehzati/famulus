from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
CERTIFICATION_ROOT = REPO_ROOT / "references" / "certification"
MACHINE_MODULE_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "machine_modules"
CONFORMANCE_OPERATION_FIXTURE_ROOT = (
    REPO_ROOT / "tests" / "fixtures" / "conformance_operations"
)
MACHINE_MODULE_EXAMPLE_ROOT = (
    REPO_ROOT / "docs" / "plans" / "machine-module-contract" / "examples"
)


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


def _machine_module_fixture(name: str) -> dict:
    return yaml.safe_load(
        (MACHINE_MODULE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


def test_common_scopes_v4_blueprint_locators_without_changing_pre_v4() -> None:
    common = _load("common.schema.json")
    resolver = jsonschema.RefResolver.from_schema(common)
    definitions = common["definitions"]
    legacy = jsonschema.Draft7Validator(
        definitions["blueprintLocator"], resolver=resolver
    )
    v4 = jsonschema.Draft7Validator(
        definitions["v4BlueprintLocator"], resolver=resolver
    )

    legacy.validate({"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"})
    assert list(
        legacy.iter_errors({"base": "module-root", "path": "blueprints/root.yaml"})
    )

    v4.validate({"base": "module-root", "path": "blueprints/root.yaml"})
    v4.validate(
        {"base": "repository-root", "path": "skills/demo/blueprints/root.yaml"}
    )
    assert list(
        v4.iter_errors({"base": "skill-root", "path": "blueprints/root.yaml"})
    )


def test_v4_requirement_grammar_accepts_names_exact_versions_and_intersections() -> None:
    requirement = _load("common.schema.json")["definitions"]["requirement"]
    validator = jsonschema.Draft7Validator(requirement)

    for value in ("Python", "Python==3.11", "Python>=3.11,<4"):
        validator.validate(value)

    for value in ("", "Python=3.11", "Python>=3.11,", "Python>=3.11, <4"):
        assert list(validator.iter_errors(value)), value


def test_v4_gateway_is_a_closed_whole_file_with_language_and_optional_machines() -> None:
    common = _load("common.schema.json")
    resolver = jsonschema.RefResolver.from_schema(common)
    validator = jsonschema.Draft7Validator(
        common["definitions"]["gateway"], resolver=resolver
    )

    validator.validate({"path": "SKILL.md", "language": "Markdown"})
    validator.validate(
        {
            "path": "_rtx/_runner.py",
            "language": "Python>=3.11,<4",
            "machines": ["CPython==3.11", "PyPy>=3.10"],
        }
    )

    for invalid in (
        {"path": "../escape.py", "language": "Python"},
        {"path": "SKILL.md", "language": "Markdown", "machines": []},
        {"path": "SKILL.md", "language": "Markdown", "kind": "file"},
        {"path": "_rtx/_runner.py", "language": "Python", "symbol": "Interface"},
    ):
        assert list(validator.iter_errors(invalid)), invalid


def test_v4_process_binding_groups_transport_and_entry_mechanics() -> None:
    schema = _load("caller-contract.schema.json")
    resolver = jsonschema.RefResolver.from_schema(schema)
    validator = jsonschema.Draft7Validator(
        schema["definitions"]["v4ProcessBinding"], resolver=resolver
    )
    binding = {
        "kind": "process",
        "entry": "provider://records/run",
        "args_prefix": ["records"],
        "arguments": {
            "targets": {
                "kind": "option",
                "name": "--target",
                "arity": {"minimum": 1, "maximum": None},
            }
        },
        "fixed": [{"kind": "switch", "name": "--json"}],
        "outputs": {
            "records": {
                "channel": "stdout",
                "encoding": "utf-8",
                "framing": "exactly-one-json-document",
            }
        },
        "outcomes": {"ok": {"exit_codes": [0]}},
        "cancellation": {"kind": "signal", "signal": "SIGTERM"},
        "stop": {"kind": "dispatcher-cancel"},
    }

    validator.validate({"kind": "process"})
    validator.validate(binding)

    empty_entry = deepcopy(binding)
    empty_entry["entry"] = ""
    assert list(validator.iter_errors(empty_entry))

    binding["symbol"] = "Interface"
    assert list(validator.iter_errors(binding))


def _valid_v4_contract() -> dict:
    return {
        "arguments": {
            "target": {
                "description": "Record identifier.",
                "required": True,
                "sensitivity": "public",
                "type": {"kind": "string"},
            }
        },
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "record",
                "audience": "machine",
                "description": "Selected record.",
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "No record is emitted.",
            }
        ],
        "outcomes": [
            {
                "id": "ok",
                "class": "success",
                "outputs": ["record"],
                "effects": [],
                "caller_action": "Use the record.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "Reads one snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "record"}],
        },
        "helpers": [],
        "direct_io": _empty_io(),
    }


def test_v4_interface_contract_is_semantic_and_excludes_process_mechanics() -> None:
    validator = _validator("caller-contract.schema.json").evolve(
        schema=_load("caller-contract.schema.json")["definitions"]["v4Contract"]
    )
    document = _valid_v4_contract()

    validator.validate(document)

    mechanical_aliases = (
        ("arguments", "target", "invocation_binding"),
        ("outputs", 0, "channel"),
        ("outcomes", 0, "signal"),
        ("interaction", None, "cancellation"),
    )
    values = (
        {"kind": "option", "name": "--target", "arity": {"minimum": 1, "maximum": 1}},
        "stdout",
        {"exit_codes": [0]},
        {"kind": "dispatcher-cancel"},
    )
    for location, value in zip(mechanical_aliases, values):
        invalid = deepcopy(document)
        section, item, field = location
        target = invalid[section] if item is None else invalid[section][item]
        target[field] = value
        assert _errors(invalid, "caller-contract.schema.json"), location


def _valid_v4_behavioral_source() -> dict:
    return {
        "schema_version": 4,
        "node_type": "behavioral_source",
        "id": "demo-skill.source.gateway",
        "version": 1,
        "description": "Owns the primary instructions.",
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            "demo-skill.source.gateway.interface.default": {
                "version": 1,
                "description": "Primary instructions.",
                "contract": _valid_v4_contract(),
            }
        },
    }


def test_v4_behavioral_source_owns_intrinsic_interfaces_and_generic_edges() -> None:
    document = _valid_v4_behavioral_source()
    assert _errors(document, "behavioral-source.schema.json") == []

    document["interfaces"]["demo-skill.source.gateway.interface.run"] = {
        "version": 2,
        "description": "Run the source.",
        "process_binding": {"kind": "process", "entry": "Interface"},
        "contract": _valid_v4_contract(),
    }
    document["dependencies"] = [
        {
            "source": "other-skill.source.policy",
            "version": 3,
            "blueprint": {
                "base": "repository-root",
                "path": "skills/other-skill/blueprints/policy.yaml",
            },
            "reason": "Supplies policy.",
        }
    ]
    document["uses_interfaces"] = [
        {"interface": "other-skill.interface.read", "version": 2}
    ]
    assert _errors(document, "behavioral-source.schema.json") == []

    document["semantic_type"] = "instructions"
    assert _errors(document, "behavioral-source.schema.json")


def test_v4_behavioral_source_runtime_declarations_are_optional_and_paired() -> None:
    document = _valid_v4_behavioral_source()
    document["platform_support"] = {
        "linux": True,
        "macos": True,
        "windows": False,
    }
    document["runtime_dependencies"] = []

    assert _errors(document, "behavioral-source.schema.json") == []

    missing_dependencies = deepcopy(document)
    del missing_dependencies["runtime_dependencies"]
    assert _errors(missing_dependencies, "behavioral-source.schema.json")

    missing_platforms = deepcopy(document)
    del missing_platforms["platform_support"]
    assert _errors(missing_platforms, "behavioral-source.schema.json")

    interface_scoped = deepcopy(document)
    interface_scoped["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["platform_support"] = {"linux": True, "macos": True, "windows": False}
    assert _errors(interface_scoped, "behavioral-source.schema.json")


def _valid_v4_module() -> dict:
    return {
        "schema_version": 4,
        "node_type": "module",
        "id": "demo-skill",
        "version": 1,
        "description": "Provides the demo skill.",
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "discovery": {"mechanism": "skill"},
        "authority": {"owns_filesystem": []},
        "sources": {
            "demo-skill.source.gateway": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/gateway.yaml",
                }
            }
        },
        "exports": {
            "demo-skill.interface.default": {
                "source_interface": "demo-skill.source.gateway.interface.default",
                "access": {"allow_all_modules": True, "allowed_callers": []},
            }
        },
    }


def test_v4_module_owns_discovery_authority_containment_and_access_only() -> None:
    document = _valid_v4_module()
    assert _errors(document, "module.schema.json") == []

    dependency_only = deepcopy(document)
    del dependency_only["discovery"]
    assert _errors(dependency_only, "module.schema.json") == []

    document["sources"]["demo-skill.source.gateway"]["version"] = 1
    document["exports"]["demo-skill.interface.default"]["version"] = 1
    assert _errors(document, "module.schema.json")


def test_v4_module_export_access_preserves_unrestricted_and_allowlist_semantics() -> None:
    document = _valid_v4_module()
    access = document["exports"]["demo-skill.interface.default"]["access"]

    access.update(allow_all_modules=False, allowed_callers=["other-skill"])
    assert _errors(document, "module.schema.json") == []

    access["allowed_callers"] = []
    assert _errors(document, "module.schema.json")

    access.update(allow_all_modules=True, allowed_callers=["other-skill"])
    assert _errors(document, "module.schema.json")


def test_v4_module_filesystem_authority_uses_generic_interface_readers() -> None:
    document = _valid_v4_module()
    ownership = {
        "match": "exact",
        "path": "state.json",
        "allowed_readers": ["other-skill.interface.read"],
        "reason": "Shares the current state.",
    }
    document["authority"]["owns_filesystem"] = [ownership]

    assert _errors(document, "module.schema.json") == []

    ownership["allowed_readers"] = ["other-skill.machine.read"]
    assert _errors(document, "module.schema.json")


def test_staged_live_schema_keeps_v4_nodes_isolated_until_cutover() -> None:
    assert _errors(_valid_v4_module(), "module.schema.json") == []
    assert _errors(
        _valid_v4_behavioral_source(), "behavioral-source.schema.json"
    ) == []

    assert _errors(_valid_v4_module())
    assert _errors(_valid_v4_behavioral_source())

    live_refs = [choice["$ref"] for choice in _load("schema.json")["oneOf"]]
    assert "machine-module.schema.json" in live_refs
    assert "behavior-source.schema.json" in live_refs
    assert "module.schema.json" not in live_refs
    assert "behavioral-source.schema.json" not in live_refs


def test_node_hash_policy_schema_enforces_ordered_include_exclude_rules() -> None:
    path = CERTIFICATION_ROOT / "node-hash-policy.schema.json"
    assert path.is_file(), "node-hash-policy.schema.json is absent"
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    assert schema["x-famulus"]["evaluation"] == "sequential-last-match-wins"
    assert schema["x-famulus"]["non_configurable_invariants"] == [
        "blueprint-gateway-and-same-owner-authored-contract-closure",
        "reserved-certification-output-rejection",
        "path-boundary-symlink-and-special-file-safety",
    ]
    document = {
        "policy_version": 1,
        "path_syntax": "gitignore",
        "starting_set": "git-tracked-directly-owned-regular-files",
        "rules": [
            {"action": "exclude", "pattern": "**/_build/**"},
            {
                "action": "include",
                "pattern": "skills/demo/_build/required.json",
                "require_match": True,
            },
        ],
    }
    validator.validate(document)

    document["rules"][0]["require_match"] = False
    assert list(validator.iter_errors(document))

    del document["rules"][0]["require_match"]
    document["rules"][1]["pattern"] = "../escape"
    assert list(validator.iter_errors(document))


def test_canonical_node_hash_policy_is_closed_and_excludes_only_reserved_outputs() -> None:
    schema = json.loads(
        (CERTIFICATION_ROOT / "node-hash-policy.schema.json").read_text(
            encoding="utf-8"
        )
    )
    policy_path = CERTIFICATION_ROOT / "node-hash-policy.yaml"
    assert policy_path.is_file(), "node-hash-policy.yaml is absent"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))

    jsonschema.Draft7Validator(schema).validate(policy)
    assert policy["rules"] == [
        {"action": "exclude", "pattern": pattern}
        for pattern in (
            "**/*.log",
            "**/log/**",
            "**/logs/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/.pytest_cache/**",
            "**/.cache/**",
            "**/_build/**",
            "**/build/**",
            "**/dist/**",
            "**/.certificates/**",
            "**/.last_audit.json",
            "**/.*.health.json",
            "**/.pooled-blueprint-review.yaml",
        )
    ]


def _valid_v4_certificate() -> dict:
    digest = "sha256:" + "a" * 64
    return {
        "payload": {
            "certificate_schema_version": 1,
            "subject": {
                "id": "demo-skill.source.gateway",
                "node_type": "behavioral_source",
                "version": 1,
                "blueprint_path": "skills/demo-skill/blueprints/gateway.yaml",
                "gateway_path": "skills/demo-skill/SKILL.md",
            },
            "node_hash": digest,
            "source_commit": "b" * 40,
            "input_manifest": [
                {
                    "path": "skills/demo-skill/SKILL.md",
                    "digest": digest,
                    "git_provenance": "untracked",
                }
            ],
            "dependencies": [
                {
                    "relation": "uses-export",
                    "target": "other-skill",
                    "version": 2,
                    "node_hash": "sha256:" + "c" * 64,
                }
            ],
            "certification_basis_hash": "sha256:" + "d" * 64,
            "certifier": {
                "interface": "skill-certifier.interface.certify",
                "version": 1,
                "node_hash": "sha256:" + "e" * 64,
                "source_commit": "f" * 64,
            },
            "checks": [
                {
                    "id": "schema-valid",
                    "version": 1,
                    "passed": True,
                    "findings": [],
                }
            ],
            "machine_evidence": [
                {
                    "kind": "gateway-language",
                    "name": "Python",
                    "requirement": "Python>=3.11,<4",
                    "evaluated_version": "3.13.5",
                    "check": {"id": "runtime-probe", "version": 1},
                }
            ],
            "key_id": "sha256:" + "1" * 64,
            "previous_entry_hash": None,
            "certified_at": "2026-07-20T12:00:00Z",
        },
        "signature": {"scheme": "ed25519", "value": "base64:YWJjZA=="},
    }


def test_v4_certificate_signs_one_closed_payload_with_local_git_provenance() -> None:
    path = SCHEMA_ROOT / "certificate.schema.json"
    assert path.is_file(), "certificate.schema.json is absent"
    document = _valid_v4_certificate()

    assert _errors(document, "certificate.schema.json") == []

    for location, field, value in (
        (("root",), "key_id", "sha256:" + "2" * 64),
        (("payload",), "hash_policy_hash", "sha256:" + "3" * 64),
        (("dependency",), "certificate_hash", "sha256:" + "4" * 64),
    ):
        invalid = deepcopy(document)
        if location == ("root",):
            invalid[field] = value
        elif location == ("payload",):
            invalid["payload"][field] = value
        else:
            invalid["payload"]["dependencies"][0][field] = value
        assert _errors(invalid, "certificate.schema.json"), location


def test_v4_certificate_requires_version_bound_machine_evidence() -> None:
    document = _valid_v4_certificate()
    assert _errors(document, "certificate.schema.json") == []

    empty = deepcopy(document)
    empty["payload"]["machine_evidence"] = []
    assert _errors(empty, "certificate.schema.json") == []

    authored_runtime_requirement = deepcopy(document)
    authored_runtime_requirement["payload"]["machine_evidence"][0].update(
        kind="runtime-dependency",
        name="demo-runtime",
        requirement="3.11.*",
    )
    assert _errors(authored_runtime_requirement, "certificate.schema.json") == []

    missing = deepcopy(document)
    del missing["payload"]["machine_evidence"]
    assert _errors(missing, "certificate.schema.json")

    for field, value in (
        ("kind", "dependency-certificate"),
        ("name", ""),
        ("requirement", ""),
        ("evaluated_version", ">=3.11,<4"),
    ):
        invalid = deepcopy(document)
        invalid["payload"]["machine_evidence"][0][field] = value
        assert _errors(invalid, "certificate.schema.json"), field

    invalid_check = deepcopy(document)
    invalid_check["payload"]["machine_evidence"][0]["check"]["passed"] = True
    assert _errors(invalid_check, "certificate.schema.json")

    invalid_version = deepcopy(document)
    invalid_version["payload"]["machine_evidence"][0]["check"]["version"] = 0
    assert _errors(invalid_version, "certificate.schema.json")


def test_v4_certificate_machine_evidence_requirement_type_follows_kind() -> None:
    document = _valid_v4_certificate()
    evidence = document["payload"]["machine_evidence"][0]
    evidence.update(
        kind="platform",
        name="linux",
        requirement=True,
        evaluated_version="6.15.2",
    )
    assert _errors(document, "certificate.schema.json") == []

    platform_string = deepcopy(document)
    platform_string["payload"]["machine_evidence"][0]["requirement"] = "true"
    assert _errors(platform_string, "certificate.schema.json")

    for kind in ("gateway-language", "gateway-machine", "runtime-dependency"):
        non_platform_boolean = deepcopy(document)
        non_platform_boolean["payload"]["machine_evidence"][0].update(
            kind=kind,
            requirement=True,
        )
        assert _errors(non_platform_boolean, "certificate.schema.json"), kind


def _machine_module_example(name: str) -> dict:
    return yaml.safe_load(
        (MACHINE_MODULE_EXAMPLE_ROOT / name).read_text(encoding="utf-8")
    )


def _conformance_operation_fixture(name: str) -> dict:
    return yaml.safe_load(
        (CONFORMANCE_OPERATION_FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


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


def test_live_dispatch_schema_accepts_canonical_machine_module_fixture() -> None:
    assert _errors(_machine_module_fixture("records.valid.yaml")) == []


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


def test_version_three_skill_keeps_canonical_pre_v4_interface_edges() -> None:
    schema = _load("skill.schema.json")

    assert schema["properties"]["interfaces"]["items"] == {
        "$ref": "common.schema.json#/definitions/interfaceEdge"
    }
    assert "definitions" not in schema


def test_version_three_llm_source_edge_accepts_skill_root_locator() -> None:
    document = _valid_skill_v3()
    document["default_interface"]["behavior_sources"] = [
        {
            "source": "demo-skill.source.policy",
            "version": 1,
            "blueprint": {
                "base": "skill-root",
                "path": "references/.policy.md.blueprint.yaml",
            },
            "reason": "Supplies policy.",
        }
    ]

    assert _errors(document, "skill.schema.json") == []


def test_version_three_filesystem_ownership_accepts_legacy_machine_readers() -> None:
    document = _valid_skill_v3()
    document["default_interface"]["owns_filesystem"] = [
        {
            "match": "exact",
            "path": "state.json",
            "allowed_readers": ["other-skill.machine.read"],
            "reason": "Shares the current state.",
        }
    ]

    assert _errors(document, "skill.schema.json") == []

    document["default_interface"]["owns_filesystem"][0]["allowed_readers"] = [
        "other-skill.interface.read"
    ]
    assert _errors(document, "skill.schema.json")


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


def test_target_v3_selects_machine_modules() -> None:
    document = _machine_module_fixture("records.valid.yaml")

    assert _errors(document) == []


@pytest.mark.parametrize(
    "removed_field",
    [
        "calls",
        "selector",
        "accepts",
        "constraints",
        "conditional_default",
        "profile",
        "draft",
        "unresolved",
        "dispatcher_consequences",
    ],
)
def test_target_v3_rejects_removed_machine_contract_structures(
    removed_field: str,
) -> None:
    document = _machine_module_fixture("records.valid.yaml")
    document["interfaces"]["inspect-records"]["contract"][removed_field] = {}

    assert _errors(document)


@pytest.mark.parametrize(
    "name", ["machine-module.yaml", "advanced-machine-module.yaml"]
)
def test_target_machine_module_examples_validate(name: str) -> None:
    assert _errors(_machine_module_example(name), "machine-module.schema.json") == []


@pytest.mark.parametrize(
    "removed_field",
    [
        "calls",
        "selector",
        "accepts",
        "constraints",
        "conditional_default",
        "profile",
        "draft",
        "unresolved",
        "dispatcher_consequences",
    ],
)
def test_v4_rejects_removed_machine_contract_structures(
    removed_field: str,
) -> None:
    document = _valid_v4_behavioral_source()
    interface = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]
    interface["contract"][removed_field] = {}

    assert _errors(document, "behavioral-source.schema.json")


@pytest.mark.parametrize(
    "name", ["interface-conformance.yaml", "advanced-interface-conformance.yaml"]
)
def test_target_conformance_examples_validate(name: str) -> None:
    schema_path = SCHEMA_ROOT / "interface-conformance.schema.json"
    assert schema_path.is_file(), "interface-conformance.schema.json is absent"
    assert _errors(_machine_module_example(name), schema_path.name) == []


def test_conformance_boundary_registry_validates() -> None:
    registry = yaml.safe_load(
        (SCHEMA_ROOT / "conformance-boundary-operations.yaml").read_text(
            encoding="utf-8"
        )
    )

    _validator("conformance-boundary-operations.schema.json").validate(registry)


@pytest.mark.parametrize("fixture_name", ["valid.yaml", "invalid.yaml"])
def test_conformance_operation_fixtures_cover_every_registered_operation(
    fixture_name: str,
) -> None:
    registry = yaml.safe_load(
        (SCHEMA_ROOT / "conformance-boundary-operations.yaml").read_text(
            encoding="utf-8"
        )
    )
    fixtures = _conformance_operation_fixture(fixture_name)
    registered = {
        f"{boundary}/{operation}"
        for boundary, operations in registry["boundaries"].items()
        for operation in operations
    }

    assert set(fixtures) == registered
    for operation_id, fixture in fixtures.items():
        boundary, operation = operation_id.split("/", 1)
        specification = registry["boundaries"][boundary][operation]
        for envelope in ("request", "success"):
            reference = specification[f"{envelope}_schema"]
            validator = _validator(reference["path"])
            subschema = validator.schema
            for component in reference["fragment"].removeprefix("#/").split("/"):
                subschema = subschema[component]
            errors = list(validator.evolve(schema=subschema).iter_errors(fixture[envelope]))
            if fixture_name == "valid.yaml":
                assert errors == [], f"{operation_id}:{envelope}: {errors}"
            else:
                assert errors, f"{operation_id}:{envelope} unexpectedly valid"


def test_target_v3_rejects_command_gateways() -> None:
    document = _machine_module_fixture("records.valid.yaml")
    document["gateway"] = {
        "kind": "command-file",
        "path": "_cx/records",
        "args_prefix": [],
    }

    assert _errors(document, "machine-module.schema.json")


def test_target_recursive_type_branches_reject_irrelevant_fields() -> None:
    document = _machine_module_fixture("records.valid.yaml")
    string_type = document["interfaces"]["inspect-records"]["contract"][
        "arguments"
    ]["targets"]["type"]["element_type"]
    string_type["element_type"] = {"kind": "string"}

    assert _errors(document, "machine-module.schema.json")


def test_target_unattended_interaction_rejects_interactive_fields() -> None:
    document = _machine_module_fixture("records.valid.yaml")
    interaction = document["interfaces"]["inspect-records"]["contract"][
        "interaction"
    ]
    interaction["channel"] = "tty"

    assert _errors(document, "machine-module.schema.json")


def test_target_direct_io_rejects_literal_and_dynamic_path_together() -> None:
    document = _machine_module_example("advanced-machine-module.yaml")
    entry = document["interfaces"]["update-record"]["direct_io"]["reads"][0]
    entry["path"] = "record.json"

    assert _errors(document, "machine-module.schema.json")


def test_target_helper_nested_shapes_are_closed() -> None:
    document = _machine_module_example("advanced-machine-module.yaml")
    helper = document["interfaces"]["inspect-account"]["helpers"][0]
    helper["result"]["unknown"] = True

    assert _errors(document, "machine-module.schema.json")


def test_v4_recursive_type_branches_reject_irrelevant_fields() -> None:
    document = _valid_v4_behavioral_source()
    contract = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["contract"]
    string_type = contract["arguments"]["target"]["type"]
    string_type["element_type"] = {"kind": "string"}

    assert _errors(document, "behavioral-source.schema.json")


def test_v4_unattended_interaction_rejects_interactive_fields() -> None:
    document = _valid_v4_behavioral_source()
    interaction = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["contract"]["interaction"]
    interaction["channel"] = "tty"

    assert _errors(document, "behavioral-source.schema.json")


def test_v4_direct_io_rejects_literal_and_dynamic_path_together() -> None:
    document = _valid_v4_behavioral_source()
    contract = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["contract"]
    contract["direct_io"]["reads"] = [
        {
            "id": "record-input",
            "medium": "local-filesystem",
            "access": "read",
            "content": "One record.",
            "format": "json",
            "sensitivity": "public",
            "path": "record.json",
            "path_source": {"kind": "argument", "argument_ref": "target"},
        }
    ]

    assert _errors(document, "behavioral-source.schema.json")


def test_v4_helper_nested_shapes_are_closed() -> None:
    document = _valid_v4_behavioral_source()
    contract = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["contract"]
    helper = {
        "id": "lookup",
        "role": "Look up the record.",
        "interface": "other-skill.interface.read",
        "version": 1,
        "inputs": {},
        "result": {"output_ref": "record", "selector": {"kind": "whole-output"}},
        "route": {"kind": "output", "target": "record"},
        "empty": {"outcome": "ok", "caller_action": "Use no value."},
        "failure": {"outcome": "ok"},
    }
    contract["helpers"] = [helper]
    assert _errors(document, "behavioral-source.schema.json") == []

    helper["result"]["unknown"] = True

    assert _errors(document, "behavioral-source.schema.json")


def test_target_long_running_conformance_case_requires_complete_cleanup_branch() -> None:
    document = _machine_module_example("advanced-interface-conformance.yaml")
    case = deepcopy(
        document["exports"]["example-skill.machine.watch-records"]["cases"][0]
    )
    del case["cleanup"]
    document["exports"]["example-skill.machine.watch-records"]["cases"] = [case]

    assert _errors(document, "interface-conformance.schema.json")
