"""Closed-schema tests for direct-routing blueprint version 6."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
V6_ROOT = ROOT / "references" / "blueprint" / "migrations" / "v6"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "blueprint_v6" / "direct-routing"


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads((V6_ROOT / "schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    resolver = jsonschema.RefResolver(
        base_uri=V6_ROOT.resolve().as_uri() + "/",
        referrer=schema,
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _errors(document: dict[str, Any]) -> list[str]:
    return [error.message for error in _validator().iter_errors(document)]


def _parent() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "node_type": "module",
        "id": "demo-skill",
        "version": 1,
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {"_rtx": {}},
        "namespace_exports": {
            "_rtx": {
                "version": 1,
                "access": {"allow_all_modules": True, "allowed_callers": []},
                "surface": {
                    "only": {"demo-skill._rtx.interface.execute": 3}
                },
            }
        },
        "exports": {},
    }


def _child() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "node_type": "module",
        "id": "demo-skill._rtx",
        "version": 1,
        "gateway": {"path": "__init__.py", "language": "Python"},
        "content": [r"__init__\.py"],
        "authority": {"owns_filesystem": []},
        "sources": {
            "demo-skill._rtx.source.runtime": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/runtime.yaml",
                }
            }
        },
        "children": {},
        "namespace_exports": {},
        "exports": {
            "demo-skill._rtx.interface.execute": {
                "source_interface": (
                    "demo-skill._rtx.source.runtime.interface.execute"
                ),
                "access": {
                    "allow_all_modules": False,
                    "allowed_callers": ["demo-skill", "..sibling", "._rtx"],
                },
            }
        },
    }


def _source() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "node_type": "behavioral_source",
        "id": "demo-skill._rtx.source.runtime",
        "version": 1,
        "gateway": {"path": "runtime.py", "language": "Python"},
        "content": [r"runtime\.py"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            "demo-skill._rtx.source.runtime.interface.execute": {"version": 3}
        },
    }


@pytest.mark.parametrize("document", [_parent(), _child(), _source()])
def test_v6_accepts_dotted_direct_routing_documents(
    document: dict[str, Any],
) -> None:
    assert _errors(document) == []


def test_v6_child_registration_is_an_empty_local_segment_record() -> None:
    locator = _parent()
    locator["children"]["_rtx"] = {
        "base": "module-root",
        "path": "_rtx/blueprint.yaml",
    }
    assert _errors(locator)

    global_key = _parent()
    global_key["children"] = {"demo-skill._rtx": {}}
    assert _errors(global_key)


def test_v6_rejects_facades_and_legacy_child_ids() -> None:
    facade = _child()
    facade["exports"]["demo-skill._rtx.interface.execute"] = {
        "facade_interface": {
            "interface": "other.interface.execute",
            "version": 1,
        },
        "access": {"allow_all_modules": True, "allowed_callers": []},
    }
    assert _errors(facade)

    legacy = _child()
    legacy["id"] = "demo-skill-rtx"
    legacy["sources"] = {}
    legacy["exports"] = {}
    assert _errors(legacy)


@pytest.mark.parametrize(
    "surface",
    [
        {"all": True},
        {"only": {}},
        {"only": {"demo-skill._rtx.interface.execute": 3}, "all": True},
    ],
)
def test_v6_requires_explicit_nonempty_only_surface(
    surface: dict[str, object],
) -> None:
    document = _parent()
    document["namespace_exports"]["_rtx"]["surface"] = surface
    assert _errors(document)


@pytest.mark.parametrize(
    "module_id",
    [
        "root.alpha.leaf",
        "root.alpha._rtx",
        "root-with-hyphen.child-with-hyphen",
    ],
)
def test_v6_accepts_canonical_dotted_module_ids(module_id: str) -> None:
    document = _child()
    document["id"] = module_id
    document["sources"] = {}
    document["exports"] = {}
    assert _errors(document) == []


@pytest.mark.parametrize(
    "module_id",
    [
        "_rtx",
        "root.interface.child",
        "root.source.child",
        "root..child",
        "root._private",
        "Root.child",
    ],
)
def test_v6_rejects_unsafe_or_reserved_module_ids(module_id: str) -> None:
    document = _child()
    document["id"] = module_id
    document["sources"] = {}
    document["exports"] = {}
    assert _errors(document)


def test_v6_export_is_source_owned_and_closed() -> None:
    document = _child()
    export = document["exports"]["demo-skill._rtx.interface.execute"]
    export["version"] = 3
    assert _errors(document)


def test_v6_relative_caller_references_remain_supported() -> None:
    for caller in ("._rtx", "..sibling", "...leaf"):
        document = _child()
        document["exports"]["demo-skill._rtx.interface.execute"]["access"][
            "allowed_callers"
        ] = [caller]
        assert _errors(document) == [], caller


def test_v6_documents_require_explicit_children_and_namespace_exports() -> None:
    for field in ("children", "namespace_exports"):
        document = deepcopy(_child())
        document.pop(field)
        assert _errors(document), field


def test_v6_direct_routing_fixtures_validate() -> None:
    fixture_paths = sorted(FIXTURE_ROOT.glob("*.v6.yaml"))
    assert [path.name for path in fixture_paths] == [
        "alpha.v6.yaml",
        "beta.v6.yaml",
        "leaf.v6.yaml",
        "root.v6.yaml",
        "source.v6.yaml",
        "unrelated.v6.yaml",
    ]
    for path in fixture_paths:
        assert _errors(yaml.safe_load(path.read_text(encoding="utf-8"))) == [], path
