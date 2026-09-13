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
LIVE_V6_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint-schema"
CERTIFICATION_ROOT = REPO_ROOT / "references" / "certification-policy"


@dataclass(frozen=True)
class _SchemaBundle:
    """One worker-local, immutable parsed schema closure."""

    root: Path
    version: int
    documents: Mapping[str, object]


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

    global _LIVE_V6_BUNDLE
    previous_v6 = _LIVE_V6_BUNDLE
    _LIVE_V6_BUNDLE = _parsed_schema_bundle(
        LIVE_V6_SCHEMA_ROOT,
        6,
        sorted(LIVE_V6_SCHEMA_ROOT.glob("*.json")),
    )
    try:
        yield
    finally:
        _LIVE_V6_BUNDLE = previous_v6


def _schema_bundle(
    bundle: _SchemaBundle | None, root: Path, version: int
) -> _SchemaBundle:
    assert bundle is not None, "worker-scoped schema bundle is not initialized"
    assert bundle.root == root.resolve()
    assert bundle.version == version
    return bundle


def _schema_store(bundle: _SchemaBundle) -> dict[str, object]:
    return {name: _thaw(document) for name, document in bundle.documents.items()}


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


def test_live_v6_schema_rejects_old_blueprint_versions() -> None:
    document = _valid_live_v6_module()
    document["schema_version"] = 5

    assert list(_live_v6_validator("module.schema.json").iter_errors(document))


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


def test_current_behavioral_source_requires_explicit_interface_facets() -> None:
    document = _valid_live_v6_behavioral_source()
    document["maturity"] = "stable"
    document["interfaces"] = {
        "demo-skill.source.gateway.interface.default": {
            "version": 1,
            "content": [r"SKILL\.md"],
            "uses_interfaces": [],
        }
    }
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


def _valid_certificate() -> dict:
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


def test_current_certificate_accepts_versioned_dependency_and_facet_histories() -> None:
    validator = _live_v6_validator("certificate.schema.json")

    dependency_hash_certificate = _valid_certificate()
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

    facet_certificate = _valid_certificate()
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
