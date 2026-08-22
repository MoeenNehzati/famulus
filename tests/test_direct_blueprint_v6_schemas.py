"""Closed-schema tests for direct-routing blueprint version 6."""

from __future__ import annotations

from copy import deepcopy
from functools import cache
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from officina.blueprints.graph import load_repository_blueprint_graph


ROOT = Path(__file__).resolve().parents[1]
V6_ROOT = ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v6"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "blueprint_v6" / "direct-routing"


@cache
def _validator() -> jsonschema.Draft7Validator:
    """Reuse the immutable v6 schema validator across document cases."""

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
        "maturity": "stable",
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": {"_rtx": {}},
        "namespace_exports": {
            "_rtx": {
                "version": 1,
                "access": {"allow_all_modules": True, "allowed_callers": []},
                "surface": {"only": {"demo-skill._rtx.interface.execute": 3}},
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
        "maturity": "stable",
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
        "maturity": "stable",
        "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
        "content": [r"runtime\.py"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {
            "demo-skill._rtx.source.runtime.interface.execute": {
                "version": 3,
                "content": [r"runtime\.py"],
                "uses_interfaces": [],
            }
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


def test_v6_source_export_accepts_setup_requirements() -> None:
    document = _child()
    document["exports"] = {
        "demo-skill._rtx.interface.setup": {
            "source_interface": "demo-skill._rtx.source.runtime.interface.execute",
            "access": {"allow_all_modules": True, "allowed_callers": []},
            "setup_requires_setup_of": [
                {"interface": "dependency.interface.setup", "version": 1}
            ],
        }
    }

    assert _errors(document) == []


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


def _write_v6_graph_fixture(repo: Path) -> None:
    placements = {
        "root.v6.yaml": "skills/root/blueprint.yaml",
        "alpha.v6.yaml": "skills/root/alpha/blueprint.yaml",
        "beta.v6.yaml": "skills/root/beta/blueprint.yaml",
        "leaf.v6.yaml": "skills/root/alpha/leaf/blueprint.yaml",
        "source.v6.yaml": "skills/root/alpha/leaf/blueprints/runtime.yaml",
        "unrelated.v6.yaml": "skills/unrelated/blueprint.yaml",
    }
    for fixture, relative in placements.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURE_ROOT / fixture).read_bytes())
    beta_path = repo / "skills/root/beta/blueprint.yaml"
    beta = yaml.safe_load(beta_path.read_text(encoding="utf-8"))
    beta["content"].append(r"inspect\.py")
    beta["sources"] = {
        "root.beta.source.inspector": {
            "blueprint": {"base": "module-root", "path": "blueprints/inspector.yaml"}
        }
    }
    beta["exports"]["root.beta.interface.inspect"]["access"]["allowed_callers"] = [
        "root",
        "..alpha",
    ]
    beta_path.write_text(yaml.safe_dump(beta, sort_keys=False), encoding="utf-8")
    leaf_path = repo / "skills/root/alpha/leaf/blueprint.yaml"
    leaf = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    leaf["content"].append(r"runtime\.py")
    leaf["exports"]["root.alpha.leaf.interface.execute"]["access"][
        "allowed_callers"
    ] = ["root.alpha", "root.beta"]
    leaf_path.write_text(yaml.safe_dump(leaf, sort_keys=False), encoding="utf-8")
    inspector = repo / "skills/root/beta/blueprints/inspector.yaml"
    inspector.parent.mkdir(parents=True, exist_ok=True)
    inspector.write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "root.beta.source.inspector",
                "version": 1,
                "maturity": "stable",
                "gateway": {"path": "inspect.py", "language": "Python>=3.11"},
                "content": [r"inspect\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    "root.beta.source.inspector.interface.inspect": {
                        "version": 1,
                        "content": [r"inspect\.py"],
                        "uses_interfaces": [],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for relative in (
        "skills/root/SKILL.md",
        "skills/root/alpha/__init__.py",
        "skills/root/beta/__init__.py",
        "skills/root/beta/inspect.py",
        "skills/root/alpha/leaf/__init__.py",
        "skills/root/alpha/leaf/runtime.py",
        "skills/unrelated/SKILL.md",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")


def test_v6_offline_graph_derives_direct_topology_without_facades(tmp_path: Path) -> None:
    _write_v6_graph_fixture(tmp_path)

    graph = load_repository_blueprint_graph(
        tmp_path,
        expected_schema_version=6,
        schema_root=V6_ROOT,
    )

    assert graph.schema_version == 6
    assert graph.module_parents["root.alpha.leaf"] == "root.alpha"
    assert graph.module_local_segments["root.alpha.leaf"] == "leaf"
    assert "root.alpha.leaf.interface.execute" in graph.exports
    assert not any(edge.relation.startswith("facades-") for edge in graph.node_edges)
    assert ("root", "root.alpha") in graph.namespace_routes


def test_v6_offline_inventory_rejects_unregistered_physical_child(tmp_path: Path) -> None:
    _write_v6_graph_fixture(tmp_path)
    root_path = tmp_path / "skills" / "root" / "blueprint.yaml"
    document = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    del document["children"]["alpha"]
    del document["namespace_exports"]["alpha"]
    root_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(Exception, match="unregistered nested module"):
        load_repository_blueprint_graph(
            tmp_path,
            expected_schema_version=6,
            schema_root=V6_ROOT,
        )


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


def _write_v6_graph_fixture(repo: Path) -> None:
    placements = {
        "root.v6.yaml": "skills/root/blueprint.yaml",
        "alpha.v6.yaml": "skills/root/alpha/blueprint.yaml",
        "beta.v6.yaml": "skills/root/beta/blueprint.yaml",
        "leaf.v6.yaml": "skills/root/alpha/leaf/blueprint.yaml",
        "source.v6.yaml": "skills/root/alpha/leaf/blueprints/runtime.yaml",
        "unrelated.v6.yaml": "skills/unrelated/blueprint.yaml",
    }
    for fixture, relative in placements.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURE_ROOT / fixture).read_bytes())
    beta_path = repo / "skills/root/beta/blueprint.yaml"
    beta = yaml.safe_load(beta_path.read_text(encoding="utf-8"))
    beta["content"].append(r"inspect\.py")
    beta["sources"] = {
        "root.beta.source.inspector": {
            "blueprint": {"base": "module-root", "path": "blueprints/inspector.yaml"}
        }
    }
    beta["exports"]["root.beta.interface.inspect"]["access"]["allowed_callers"] = [
        "root",
        "..alpha",
    ]
    beta_path.write_text(yaml.safe_dump(beta, sort_keys=False), encoding="utf-8")
    leaf_path = repo / "skills/root/alpha/leaf/blueprint.yaml"
    leaf = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    leaf["content"].append(r"runtime\.py")
    leaf["exports"]["root.alpha.leaf.interface.execute"]["access"][
        "allowed_callers"
    ] = ["root.alpha", "root.beta"]
    leaf_path.write_text(yaml.safe_dump(leaf, sort_keys=False), encoding="utf-8")
    inspector = repo / "skills/root/beta/blueprints/inspector.yaml"
    inspector.parent.mkdir(parents=True, exist_ok=True)
    inspector.write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "root.beta.source.inspector",
                "version": 1,
                "maturity": "stable",
                "gateway": {"path": "inspect.py", "language": "Python>=3.11"},
                "content": [r"inspect\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    "root.beta.source.inspector.interface.inspect": {
                        "version": 1,
                        "content": [r"inspect\.py"],
                        "uses_interfaces": [],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for relative in (
        "skills/root/SKILL.md",
        "skills/root/alpha/__init__.py",
        "skills/root/beta/__init__.py",
        "skills/root/beta/inspect.py",
        "skills/root/alpha/leaf/__init__.py",
        "skills/root/alpha/leaf/runtime.py",
        "skills/unrelated/SKILL.md",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")


def test_v6_offline_graph_derives_direct_topology_without_facades(
    tmp_path: Path,
) -> None:
    _write_v6_graph_fixture(tmp_path)

    graph = load_repository_blueprint_graph(
        tmp_path,
        expected_schema_version=6,
        schema_root=V6_ROOT,
    )

    assert graph.schema_version == 6
    assert graph.module_parents["root.alpha.leaf"] == "root.alpha"
    assert graph.module_local_segments["root.alpha.leaf"] == "leaf"
    assert "root.alpha.leaf.interface.execute" in graph.exports
    assert not any(edge.relation.startswith("facades-") for edge in graph.node_edges)
    assert ("root", "root.alpha") in graph.namespace_routes


def test_v6_offline_inventory_rejects_unregistered_physical_child(
    tmp_path: Path,
) -> None:
    _write_v6_graph_fixture(tmp_path)
    root_path = tmp_path / "skills/root/blueprint.yaml"
    document = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    del document["children"]["alpha"]
    del document["namespace_exports"]["alpha"]
    root_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(Exception, match="unregistered nested module"):
        load_repository_blueprint_graph(
            tmp_path,
            expected_schema_version=6,
            schema_root=V6_ROOT,
        )
