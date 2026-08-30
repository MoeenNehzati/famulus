from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = (
    REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v4"
)
LIVE_V6_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint-schema"
CERTIFICATION_ROOT = REPO_ROOT / "references" / "certification-policy"
FROZEN_V4_SCHEMA_NAMES = (
    "schema.json",
    "module.schema.json",
    "behavioral-source.schema.json",
    "common.schema.json",
    "caller-contract.schema.json",
    "direct-io.schema.json",
    "certificate.schema.json",
    "interface-projection.schema.json",
)


@dataclass(frozen=True)
class _SchemaBundle:
    """One worker-local, immutable parsed schema closure."""

    root: Path
    version: int
    documents: Mapping[str, object]


_FROZEN_V4_BUNDLE: _SchemaBundle | None = None
_LIVE_V6_BUNDLE: _SchemaBundle | None = None


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _parsed_schema_bundle(
    root: Path, version: int, paths: list[Path]
) -> _SchemaBundle:
    documents = {
        path.relative_to(root).as_posix(): _freeze(
            json.loads(path.read_text(encoding="utf-8"))
        )
        for path in paths
    }
    schema = documents["schema.json"]
    assert isinstance(schema, Mapping)
    assert f"version-{version}" in schema["description"]
    return _SchemaBundle(
        root=root.resolve(),
        version=version,
        documents=MappingProxyType(documents),
    )


@pytest.fixture(scope="module", autouse=True)
def _worker_scoped_schema_bundles() -> Iterator[None]:
    """Parse each exact schema root once in each xdist worker."""

    global _FROZEN_V4_BUNDLE, _LIVE_V6_BUNDLE
    previous_v4, previous_v6 = _FROZEN_V4_BUNDLE, _LIVE_V6_BUNDLE
    _FROZEN_V4_BUNDLE = _parsed_schema_bundle(
        SCHEMA_ROOT,
        4,
        [SCHEMA_ROOT / name for name in FROZEN_V4_SCHEMA_NAMES],
    )
    _LIVE_V6_BUNDLE = _parsed_schema_bundle(
        LIVE_V6_SCHEMA_ROOT,
        6,
        sorted(LIVE_V6_SCHEMA_ROOT.glob("*.json")),
    )
    try:
        yield
    finally:
        _FROZEN_V4_BUNDLE, _LIVE_V6_BUNDLE = previous_v4, previous_v6


def _schema_bundle(
    bundle: _SchemaBundle | None, root: Path, version: int
) -> _SchemaBundle:
    assert bundle is not None, "worker-scoped schema bundle is not initialized"
    assert bundle.root == root.resolve()
    assert bundle.version == version
    return bundle


def _schema_store(bundle: _SchemaBundle) -> dict[str, object]:
    return {name: _thaw(document) for name, document in bundle.documents.items()}


def _load(name: str) -> dict:
    bundle = _schema_bundle(_FROZEN_V4_BUNDLE, SCHEMA_ROOT, 4)
    document = _thaw(bundle.documents[name])
    assert isinstance(document, dict)
    return document


def _validator(name: str = "schema.json") -> jsonschema.Draft7Validator:
    """Build a validator with a fresh legacy resolver for each test use.

    RefResolver tracks resolution scope internally, so sharing it through a
    cached validator makes later fragment validation depend on test order.
    """

    bundle = _schema_bundle(_FROZEN_V4_BUNDLE, SCHEMA_ROOT, 4)
    schema = _load(name)
    store = _schema_store(bundle)
    store.update(
        {
            (SCHEMA_ROOT / key).resolve().as_uri(): value
            for key, value in store.items()
        }
    )
    resolver = jsonschema.RefResolver(
        base_uri=(SCHEMA_ROOT / name).resolve().as_uri(),
        referrer=schema,
        store=store,
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _errors(document: dict, name: str = "schema.json") -> list[str]:
    return [error.message for error in _validator(name).iter_errors(document)]


def _live_v6_validator(name: str) -> jsonschema.Draft7Validator:
    """Build a validator for one live v6 schema and its local references."""

    bundle = _schema_bundle(_LIVE_V6_BUNDLE, LIVE_V6_SCHEMA_ROOT, 6)
    schema = _thaw(bundle.documents[name])
    assert isinstance(schema, dict)
    store = _schema_store(bundle)
    store.update(
        {
            (LIVE_V6_SCHEMA_ROOT / key).resolve().as_uri(): value
            for key, value in store.items()
        }
    )
    resolver = jsonschema.RefResolver(
        base_uri=(LIVE_V6_SCHEMA_ROOT / name).resolve().as_uri(),
        referrer=schema,
        store=store,
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _valid_live_v6_module() -> dict:
    return {
        "schema_version": 6,
        "node_type": "module",
        "id": "demo-skill",
        "version": 1,
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\\.md"],
        "discovery": {
            "mechanism": "skill",
            "catalog": {
                "domain": "software-development",
                "topics": ["repository-workflow"],
                "visibility": "listed",
            },
            "activated_by": ["user-request"],
            "persistent_modifier": False,
        },
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {},
        "namespace_exports": {},
        "exports": {},
    }


def _valid_live_v6_behavioral_source() -> dict:
    return {
        "schema_version": 6,
        "node_type": "behavioral_source",
        "id": "demo-skill.source.gateway",
        "version": 1,
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\\.md"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {},
    }


def test_live_v6_maturity_accepts_only_stable_or_experimental() -> None:
    """A typo must not silently publish a node with an unknown maturity."""

    module = _valid_live_v6_module()
    source = _valid_live_v6_behavioral_source()
    module_validator = _live_v6_validator("module.schema.json")
    source_validator = _live_v6_validator("behavioral-source.schema.json")

    for document, validator in ((module, module_validator), (source, source_validator)):
        for maturity in ("stable", "experimental"):
            candidate = deepcopy(document)
            candidate["maturity"] = maturity
            if candidate["node_type"] == "module":
                candidate["installation_tier"] = "core"
                candidate["personal_preference"] = {"applies": False}
            validator.validate(candidate)

        candidate = deepcopy(document)
        candidate["maturity"] = "preview"
        assert list(validator.iter_errors(candidate))


def test_live_v6_installation_metadata_is_module_only() -> None:
    """Installation selection belongs to discoverable modules, never sources."""

    module = _valid_live_v6_module()
    module.update(
        {
            "maturity": "stable",
            "installation_tier": "optional",
            "personal_preference": {"applies": False},
        }
    )
    _live_v6_validator("module.schema.json").validate(module)

    source = _valid_live_v6_behavioral_source()
    source.update({"maturity": "experimental", "installation_tier": "optional"})
    assert list(_live_v6_validator("behavioral-source.schema.json").iter_errors(source))


def test_live_v6_discoverable_module_requires_installation_metadata() -> None:
    """Discoverable modules cannot omit the installation-selection record."""

    module = _valid_live_v6_module()
    module.update(
        {
            "maturity": "stable",
            "discovery": {
                "mechanism": "skill",
                "catalog": {
                    "domain": "software-development",
                    "topics": ["repository-workflow"],
                    "visibility": "listed",
                },
                "activated_by": ["user-request"],
                "persistent_modifier": False,
            },
        }
    )
    validator = _live_v6_validator("module.schema.json")

    assert list(validator.iter_errors(module))
    module["installation_tier"] = "core"
    module["personal_preference"] = {"applies": False}
    validator.validate(module)


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_tier", "core"),
        ("personal_preference", {"applies": False}),
    ],
)
def test_live_v6_non_discoverable_module_rejects_installation_metadata(
    field: str, value: object
) -> None:
    """Installation metadata is meaningful only for discoverable modules."""

    module = _valid_live_v6_module()
    module.pop("discovery")
    module["maturity"] = "stable"
    module[field] = value

    assert list(_live_v6_validator("module.schema.json").iter_errors(module))


def test_live_v6_personal_preference_requires_description_when_applicable() -> None:
    """A preference claim needs an author-facing reason when it applies."""

    missing_description = _valid_live_v6_module()
    missing_description.update(
        {
            "maturity": "stable",
            "installation_tier": "core",
            "personal_preference": {"applies": True},
        }
    )
    validator = _live_v6_validator("module.schema.json")

    assert list(validator.iter_errors(missing_description)), "missing description"

    substantive_description = _valid_live_v6_module()
    substantive_description.update(
        {
            "maturity": "stable",
            "installation_tier": "core",
            "personal_preference": {
                "applies": True,
                "description": "Explains the user-specific workflow preference.",
            },
        }
    )
    validator.validate(substantive_description)

    whitespace_description = _valid_live_v6_module()
    whitespace_description.update(
        {
            "maturity": "stable",
            "installation_tier": "core",
            "personal_preference": {"applies": True, "description": " \t\n "},
        }
    )
    assert list(validator.iter_errors(whitespace_description)), "whitespace description"


def _canonical_errors(document: dict, name: str) -> list[str]:
    return [
        error.message
        for error in _live_v6_validator(name).iter_errors(document)
    ]


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


def test_frozen_v4_schema_closure_has_no_legacy_node_schema_reference() -> None:
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


def test_current_behavioral_source_requires_explicit_interface_facets() -> None:
    document = _valid_v4_behavioral_source()
    document["schema_version"] = 6
    document["maturity"] = "stable"
    interface = document["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]
    interface["content"] = [r"SKILL\.md"]
    interface["uses_interfaces"] = []

    assert _canonical_errors(document, "behavioral-source.schema.json") == []

    missing_content = deepcopy(document)
    del missing_content["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["content"]
    assert _canonical_errors(missing_content, "behavioral-source.schema.json")

    missing_uses = deepcopy(document)
    del missing_uses["interfaces"][
        "demo-skill.source.gateway.interface.default"
    ]["uses_interfaces"]
    assert _canonical_errors(missing_uses, "behavioral-source.schema.json")

    no_interfaces = deepcopy(document)
    no_interfaces["interfaces"] = {}
    assert _canonical_errors(no_interfaces, "behavioral-source.schema.json") == []


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
            },
            {
                "name": "short-flag",
                "min_positionals": 0,
                "max_positionals": 0,
                "required_flags": ["-a"],
                "allowed_flags": ["-a"],
                "flag_patterns": {"-a": "^.+$"},
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


def test_frozen_schema_routes_only_v4_nodes() -> None:
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


def test_node_hash_policy_schema_and_canonical_policy_history() -> None:
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
    valid_policy = {
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
    validator.validate(valid_policy)

    unexpected_exclude_match = deepcopy(valid_policy)
    unexpected_exclude_match["rules"][0]["require_match"] = False
    assert list(validator.iter_errors(unexpected_exclude_match)), "exclude match flag"

    escaped_pattern = deepcopy(valid_policy)
    escaped_pattern["rules"][1]["pattern"] = "../escape"
    assert list(validator.iter_errors(escaped_pattern)), "escaped include pattern"

    policy_path = CERTIFICATION_ROOT / "node-hash-policy.yaml"
    assert policy_path.is_file(), "node-hash-policy.yaml is absent"
    canonical_policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))

    validator.validate(canonical_policy)
    assert canonical_policy["rules"] == [
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
                "interface": "node-certify.interface.certify",
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


def test_current_certificate_accepts_versioned_dependency_and_facet_histories() -> None:
    validator = _live_v6_validator("certificate.schema.json")

    dependency_hash_certificate = _valid_v4_certificate()
    dependency_hash_certificate["payload"]["certificate_schema_version"] = 2
    dependency_hash_certificate["payload"]["dependencies"] = [
        {
            "relation": "uses-export",
            "target": "other-skill.source.gateway",
            "interface": "other-skill.interface.run",
            "version": 2,
            "interface_hash": "sha256:" + "c" * 64,
        }
    ]
    assert list(validator.iter_errors(dependency_hash_certificate)) == [], (
        "v2 interface dependency hash"
    )

    facet_certificate = _valid_v4_certificate()
    facet_certificate["payload"]["certificate_schema_version"] = 3
    facet_certificate["payload"]["facets"] = [
        {
            "id": "demo-skill.source.gateway.interface.run",
            "type": "interface",
            "local_hash": "sha256:" + "d" * 64,
            "input_manifest": [
                {
                    "path": "skills/demo-skill/SKILL.md",
                    "digest": "sha256:" + "e" * 64,
                    "git_provenance": "tracked",
                }
            ],
            "dependencies": [],
        }
    ]
    assert list(validator.iter_errors(facet_certificate)) == [], "v3 facet claims"


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
