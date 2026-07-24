from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
CERTIFICATION_ROOT = REPO_ROOT / "references" / "certification"


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


def test_common_scopes_v4_blueprint_locators_to_module_and_repository_roots() -> None:
    common = _load("common.schema.json")
    resolver = jsonschema.RefResolver.from_schema(common)
    definitions = common["definitions"]
    v4 = jsonschema.Draft7Validator(
        definitions["v4BlueprintLocator"], resolver=resolver
    )

    v4.validate({"base": "module-root", "path": "blueprints/root.yaml"})
    v4.validate(
        {"base": "repository-root", "path": "skills/demo/blueprints/root.yaml"}
    )
    assert list(
        v4.iter_errors({"base": "skill-root", "path": "blueprints/root.yaml"})
    )


def test_caller_contract_scopes_relative_paths_to_module_roots() -> None:
    contract = _load("caller-contract.schema.json")
    resolver = jsonschema.RefResolver.from_schema(contract)
    path_type = jsonschema.Draft7Validator(
        contract["definitions"]["pathType"],
        resolver=resolver,
    )
    value = {
        "kind": "path",
        "syntax": "literal",
        "relative_to": "module-root",
        "must_exist": True,
        "access": "read",
    }

    path_type.validate(value)
    value["relative_to"] = "skill-root"
    assert list(path_type.iter_errors(value))


def test_live_v4_schema_closure_has_no_legacy_node_schema_reference() -> None:
    live_schemas = {
        "schema.json",
        "module.schema.json",
        "behavioral-source.schema.json",
        "common.schema.json",
        "caller-contract.schema.json",
        "direct-io.schema.json",
        "certificate.schema.json",
        "interface-projection.schema.json",
    }
    legacy_names = {
        "legacy-skill.schema.json",
        "skill.schema.json",
        "default-" "llm" "-interface.schema.json",
        "llm" "-interface.schema.json",
        "machine-interface.schema.json",
        "machine" "-module.schema.json",
        "behavior" "-source.schema.json",
        "health.schema.json",
    }

    references: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                references.add(reference.split("#", 1)[0])
            for child in value.values():
                collect(child)

    for name in live_schemas:
        collect(_load(name))

    assert not (references & legacy_names)


def test_common_schema_contains_only_v4_shared_definitions() -> None:
    definitions = set(_load("common.schema.json")["definitions"])

    assert definitions == {
        "behavioralSourceId",
        "contentPatterns",
        "contractReference",
        "exportInterfaceId",
        "gateway",
        "interfaceId",
        "interfaceUse",
        "interfaceUseList",
        "interfaceVersion",
        "moduleId",
        "pattern",
        "platformSupport",
        "relativePath",
        "requirement",
        "runtimeDependencies",
        "runtimeDependency",
        "runtimeDependencyKind",
        "runtimeSystemServiceName",
        "sourceContainment",
        "sourceDependency",
        "sourceInterfaceId",
        "v4BlueprintLocator",
        "v4FilesystemOwnershipList",
    }


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


def test_caller_contract_root_validates_the_current_v4_contract() -> None:
    _validator("caller-contract.schema.json").validate(_valid_v4_contract())


def test_caller_contract_has_no_obsolete_parallel_contract_definitions() -> None:
    definitions = set(_load("caller-contract.schema.json")["definitions"])

    assert definitions.isdisjoint(
        {"argument", "interaction", "output", "outcome", "execution"}
    )


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


def test_v4_execution_requires_at_least_one_verification() -> None:
    validator = _validator("caller-contract.schema.json").evolve(
        schema=_load("caller-contract.schema.json")["definitions"]["v4Contract"]
    )
    document = _valid_v4_contract()
    document["execution"]["verification"] = []

    assert tuple(validator.iter_errors(document))


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


def test_v4_structural_draft_allows_certifier_owned_semantics_to_be_absent() -> None:
    module = _valid_v4_module()
    source = _valid_v4_behavioral_source()
    interface = source["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]

    del module["description"]
    del source["description"]
    del interface["description"]
    del interface["contract"]

    assert _errors(module, "module.schema.json") == []
    assert _errors(source, "behavioral-source.schema.json") == []


def test_v4_structural_draft_validates_each_present_contract_section() -> None:
    document = _valid_v4_behavioral_source()
    interface = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]
    interface["contract"] = {"arguments": {}}

    assert _errors(document, "behavioral-source.schema.json") == []

    interface["contract"]["invented"] = {}
    assert _errors(document, "behavioral-source.schema.json")


def test_v4_preserves_usage_and_legacy_argv_patterns_as_evidence() -> None:
    document = _valid_v4_behavioral_source()
    interface = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]
    interface["usage"] = "<name> --cloud [--refresh]"
    interface["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "patterns": [
            {
                "name": "cloud",
                "min_positionals": 1,
                "max_positionals": 1,
                "allow_stdin": False,
                "required_flags": ["--cloud"],
                "allowed_flags": ["--cloud", "--refresh"],
                "positional_patterns": {"0": "^[a-z]+$"},
            }
        ],
    }

    assert _errors(document, "behavioral-source.schema.json") == []

    interface["process_binding"]["patterns"][0]["invented"] = True
    assert _errors(document, "behavioral-source.schema.json")


def test_v4_direct_io_uses_the_canonical_lossless_evidence_shape() -> None:
    document = _valid_v4_behavioral_source()
    direct_io = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["contract"]["direct_io"]
    direct_io["network"] = [
        {
            "id": "network-1",
            "medium": "network-request",
            "access": "download",
            "system": "google-drive",
            "content": "list",
            "formats": ["yaml", "text"],
            "auth": {"kind": "google-drive-oauth", "mode": "uses-existing"},
            "sensitivity": "user-private",
            "path": "$home/lists/<name>.yaml",
            "path_match": "glob",
            "reason": "Preserved authored evidence.",
        }
    ]

    assert _errors(document, "behavioral-source.schema.json") == []

    direct_io["network"][0]["format"] = "yaml"
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
    assert _errors(document, "module.schema.json") == []

    access.update(allow_all_modules=True, allowed_callers=["other-skill"])
    assert _errors(document, "module.schema.json")


def test_v4_module_export_can_deny_all_external_callers() -> None:
    document = _valid_v4_module()
    document["exports"]["demo-skill.interface.default"]["access"] = {
        "allow_all_modules": False,
        "allowed_callers": [],
    }

    assert _errors(document, "module.schema.json") == []


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

    ownership["allowed_readers"] = ["other-skill" ".machine" "." "read"]
    assert _errors(document, "module.schema.json")


def test_live_schema_routes_only_v4_nodes_after_cutover() -> None:
    assert _errors(_valid_v4_module(), "module.schema.json") == []
    assert _errors(
        _valid_v4_behavioral_source(), "behavioral-source.schema.json"
    ) == []

    assert _errors(_valid_v4_module()) == []
    assert _errors(_valid_v4_behavioral_source()) == []

    live_refs = [choice["$ref"] for choice in _load("schema.json")["oneOf"]]
    assert live_refs == [
        "module.schema.json",
        "behavioral-source.schema.json",
    ]


def test_dispatch_schema_accepts_live_v4_blueprints() -> None:
    document = yaml.safe_load(
        (REPO_ROOT / "skills" / "skill-drift" / "blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert _errors(document) == []


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


def test_v4_certificate_keeps_runtime_claim_audits_in_versioned_checks() -> None:
    document = _valid_v4_certificate()
    assert _errors(document, "certificate.schema.json") == []

    invalid = deepcopy(document)
    invalid["payload"]["unexpected_field"] = []
    assert _errors(invalid, "certificate.schema.json")

    runtime_check = deepcopy(document)
    runtime_check["payload"]["checks"].append(
        {
            "id": "runtime-declarations-accurate",
            "version": 1,
            "passed": True,
            "findings": [],
        }
    )
    assert _errors(runtime_check, "certificate.schema.json") == []


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
