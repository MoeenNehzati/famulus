from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
V5_ROOT = ROOT / "references" / "blueprint" / "migrations" / "v5"
V4_ROOT = ROOT / "references" / "blueprint" / "migrations" / "v4"
FIXTURES = ROOT / "tests" / "fixtures" / "blueprint_v5"

SCHEMA_BUNDLE = {
    "schema.json",
    "module.schema.json",
    "behavioral-source.schema.json",
    "common.schema.json",
    "caller-contract.schema.json",
    "direct-io.schema.json",
    "certificate.schema.json",
    "interface-projection.schema.json",
    "pooled-review.schema.json",
    "schema-meta.json",
    "schema.annotated-draft.json",
}
V4_BUNDLE = SCHEMA_BUNDLE | {"template.yaml"}
V4_DIGESTS = {
    "behavioral-source.schema.json": "03a79263d89ad43753b25889f20c9a1ab47b49fcef60ea3f443cb088f2430923",
    "caller-contract.schema.json": "0c1132d14ee09455f5eac44fd4980810c9014534cef61918102c1e5d6f881c61",
    "certificate.schema.json": "1e054b802790dd6f1b903d39848306b0972ab5f410b563396699f0bc920d804a",
    "common.schema.json": "5a3504bbbeaf232cc016931059a8fa73723bfa5b711a7985daf91e3b85e3e1e2",
    "direct-io.schema.json": "2b6bce718706d1a57cffcace63006d3b583c3c314806c1063fe08a73497c82bd",
    "interface-projection.schema.json": "499cf54563c13acf494120ff9307bca095ff1bf6c172ce1fc89aa8183a418690",
    "module.schema.json": "30d7eba02d40326127abb9a7679308fffdd945b9a409a3c23b46c683ec9b2a22",
    "pooled-review.schema.json": "2b7c5fbcc3934c0407e79a2cdbd69dd96daf5706945b591b53389ff8268501af",
    "schema-meta.json": "b5af3472fbaa95168dddfd75de333c284fa9ff461852328c572241a5beef6f46",
    "schema.annotated-draft.json": "0ced82cbb593068650ca2d820adcee40b07c46d9e6b5f525e8f287342f27debf",
    "schema.json": "996a62d7956ee5ec5c38ac0845838ef2776bcaf0ca344401f2f71648eac08e35",
    "template.yaml": "852be1592f89a1ce6a1581a14f77a6c7e03c38732c12107fda79e4ce87984c13",
}


def _load_json(root: Path, name: str) -> dict[str, Any]:
    return json.loads((root / name).read_text(encoding="utf-8"))


def _load_fixture(name: str) -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def _validator(name: str) -> jsonschema.Draft7Validator:
    schema = _load_json(V5_ROOT, name)
    jsonschema.Draft7Validator.check_schema(schema)
    resolver = jsonschema.RefResolver(
        base_uri=V5_ROOT.resolve().as_uri() + "/",
        referrer=schema,
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _errors(document: dict[str, Any], name: str = "schema.json") -> list[str]:
    return [error.message for error in _validator(name).iter_errors(document)]


def _collect_file_refs(value: object) -> set[str]:
    references: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        reference = item.get("$ref")
        if isinstance(reference, str):
            target = reference.split("#", 1)[0]
            if target:
                references.add(target)
        for child in item.values():
            visit(child)

    visit(value)
    return references


def _valid_certificate(version: int) -> dict[str, Any]:
    digest = "sha256:" + "a" * 64
    return {
        "payload": {
            "certificate_schema_version": version,
            "subject": {
                "id": "demo-skill-rtx.source.runtime",
                "node_type": "behavioral_source",
                "version": 1,
                "blueprint_path": "skills/demo-skill/_rtx/blueprints/runtime.yaml",
                "gateway_path": "skills/demo-skill/_rtx/runtime.py",
            },
            "node_hash": digest,
            "source_commit": "b" * 40,
            "input_manifest": [
                {
                    "path": "skills/demo-skill/_rtx/runtime.py",
                    "digest": digest,
                    "git_provenance": "tracked",
                }
            ],
            "dependencies": [
                {
                    "relation": (
                        "facades-child-export" if version == 2 else "uses-export"
                    ),
                    "target": "demo-skill-rtx",
                    "version": 1,
                    "node_hash": "sha256:" + "c" * 64,
                }
            ],
            "certification_basis_hash": "sha256:" + "d" * 64,
            "certifier": {
                "interface": "skill-certifier.interface.certify",
                "version": 1,
                "node_hash": "sha256:" + "e" * 64,
                "source_commit": "f" * 40,
            },
            "checks": [
                {
                    "id": (
                        "v5-deterministic" if version == 2 else "schema-valid"
                    ),
                    "version": 1,
                    "passed": True,
                    "findings": [],
                }
            ],
            "key_id": "sha256:" + "1" * 64,
            "previous_entry_hash": None,
            "certified_at": "2026-07-25T12:00:00Z",
        },
        "signature": {"scheme": "ed25519", "value": "base64:YWJjZA=="},
    }


def test_v5_bundle_is_closed_and_every_schema_is_draft7_valid() -> None:
    assert {
        path.name
        for path in V5_ROOT.iterdir()
        if path.is_file() and not path.name.startswith(".")
    } == (
        SCHEMA_BUNDLE | {"config.yaml", "template.yaml"}
    )

    for name in SCHEMA_BUNDLE:
        document = _load_json(V5_ROOT, name)
        jsonschema.Draft7Validator.check_schema(document)
        for reference in _collect_file_refs(document):
            assert not reference.startswith(("http://", "https://", "file:")), (
                name,
                reference,
            )
            assert (V5_ROOT / reference).is_file(), (name, reference)


def test_frozen_v4_bundle_is_complete_and_byte_faithful() -> None:
    assert {path.name for path in V4_ROOT.iterdir() if path.is_file()} == V4_BUNDLE
    assert {
        name: hashlib.sha256((V4_ROOT / name).read_bytes()).hexdigest()
        for name in sorted(V4_BUNDLE)
    } == V4_DIGESTS


def test_v5_parent_child_facade_and_source_documents_validate() -> None:
    for name in ("parent.yaml", "child.yaml", "source.yaml"):
        assert _errors(_load_fixture(name)) == [], name


def test_v5_module_requires_explicit_topology_fields() -> None:
    parent = _load_fixture("parent.yaml")
    for field in ("children", "namespace_exports"):
        invalid = deepcopy(parent)
        invalid.pop(field)
        assert _errors(invalid), field


def test_v5_child_registration_is_module_root_relative() -> None:
    invalid = _load_fixture("parent.yaml")
    invalid["children"]["demo-skill-rtx"]["base"] = "repository-root"
    assert _errors(invalid)

    invalid = _load_fixture("parent.yaml")
    invalid["children"]["demo-skill-rtx"]["path"] = "_rtx/child.yaml"
    assert _errors(invalid)


def test_v5_exports_are_a_closed_source_or_facade_choice() -> None:
    parent = _load_fixture("parent.yaml")
    export = parent["exports"]["demo-skill.interface.execute"]
    export["source_interface"] = "demo-skill.source.gateway.interface.execute"
    assert _errors(parent)

    child = _load_fixture("child.yaml")
    export = child["exports"]["demo-skill-rtx.interface.execute"]
    export["facade_interface"] = {
        "interface": "demo-skill-rtx.interface.execute",
        "version": 3,
    }
    assert _errors(child)


@pytest.mark.parametrize(
    "caller",
    ["trusted-client", "demo-skill-rtx", "._rtx", ".parser", "..parser", "...leaf"],
)
def test_v5_access_accepts_exact_and_relative_caller_references(
    caller: str,
) -> None:
    document = _load_fixture("child.yaml")
    document["exports"]["demo-skill-rtx.interface.execute"]["access"][
        "allowed_callers"
    ] = [caller]
    assert _errors(document) == []


@pytest.mark.parametrize(
    "caller",
    ["", ".", "..", "...", ".Parser", "._private", ".child..leaf", "child.leaf"],
)
def test_v5_access_rejects_malformed_relative_caller_references(
    caller: str,
) -> None:
    document = _load_fixture("child.yaml")
    document["exports"]["demo-skill-rtx.interface.execute"]["access"][
        "allowed_callers"
    ] = [caller]
    assert _errors(document), caller


def test_v5_namespace_surface_is_exactly_all_or_nonempty_only() -> None:
    parent = _load_fixture("parent.yaml")
    route = parent["namespace_exports"]["demo-skill-rtx"]

    all_surface = deepcopy(parent)
    all_surface["namespace_exports"]["demo-skill-rtx"]["surface"] = {"all": True}
    assert _errors(all_surface) == []

    for invalid_surface in (
        {"all": False},
        {"only": {}},
        {"all": True, "only": {"demo-skill-rtx.interface.execute": 3}},
        {"only": {"not-an-interface": 3}},
        {"all": True, "extra": True},
    ):
        invalid = deepcopy(parent)
        invalid["namespace_exports"]["demo-skill-rtx"]["surface"] = invalid_surface
        assert _errors(invalid), invalid_surface

    assert route["surface"] == {
        "only": {"demo-skill-rtx.interface.execute": 3}
    }


def test_v5_certificate_accepts_historical_v1_and_current_v2_payloads() -> None:
    validator = _validator("certificate.schema.json")
    validator.validate(_valid_certificate(1))
    validator.validate(_valid_certificate(2))

    invalid_v1 = _valid_certificate(1)
    invalid_v1["payload"]["dependencies"][0]["relation"] = (
        "facades-child-export"
    )
    assert list(validator.iter_errors(invalid_v1))

    invalid_version = _valid_certificate(2)
    invalid_version["payload"]["certificate_schema_version"] = 3
    assert list(validator.iter_errors(invalid_version))


def test_v5_certificate_rejects_contains_module_as_a_dependency() -> None:
    document = _valid_certificate(2)
    document["payload"]["dependencies"][0]["relation"] = "contains-module"

    assert list(_validator("certificate.schema.json").iter_errors(document))


def test_v5_authoring_entry_points_select_only_v5() -> None:
    selector = _load_json(V5_ROOT, "schema.json")
    assert selector["description"] == (
        "Canonical runtime gateway for version-5 modules and behavioral sources."
    )
    assert selector["x-famulus"]["authoring"].startswith(
        "Author schema version 5"
    )

    annotated = _load_json(V5_ROOT, "schema.annotated-draft.json")
    assert annotated["$ref"] == "schema.json"
    assert annotated["x-famulus"]["schema_version"] == 5

    template = yaml.safe_load(
        (V5_ROOT / "template.yaml").read_text(encoding="utf-8")
    )
    assert template == {
        "schema_version": 5,
        "node_type": "module",
        "id": "example-skill",
        "version": 1,
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {},
        "namespace_exports": {},
        "exports": {},
    }
