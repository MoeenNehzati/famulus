from __future__ import annotations

from pathlib import Path

import pytest

from officina.blueprints.graph import (
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
)
from officina.certification.dependency_dag import (
    DependencyDagError,
    build_dependency_dag,
    decode_dependency_dag,
)
from officina.certification.hashing import (
    CertificationFacetHashState,
    NodeHashState,
)


def _node(node_id: str, node_type: str) -> BlueprintNode:
    return BlueprintNode(
        node_id=node_id,
        node_type=node_type,
        version=1,
        module_root=Path("/repo"),
        blueprint_path=Path(f"/repo/{node_id}.yaml"),
        gateway_path=None,
        declaration={},
    )


def _graph() -> RepositoryBlueprintGraph:
    nodes = {
        "app": _node("app", "module"),
        "app.child": _node("app.child", "module"),
        "app.source.worker": _node("app.source.worker", "behavioral_source"),
        "provider": _node("provider", "module"),
        "provider.source.api": _node(
            "provider.source.api", "behavioral_source"
        ),
    }
    interface_id = "provider.source.api.interface.read"
    consumer_id = "app.source.worker.interface.run"
    return RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources={
            "app": ("app.source.worker",),
            "provider": ("provider.source.api",),
        },
        schema_version=6,
        source_modules={
            "app.source.worker": "app",
            "provider.source.api": "provider",
        },
        source_interfaces={
            interface_id: InterfaceExport(
                interface_id=interface_id,
                version=1,
                local_name="read",
                module_node_id="provider",
                declaration={},
                source_node_id="provider.source.api",
                source_interface_id=interface_id,
            ),
            consumer_id: InterfaceExport(
                interface_id=consumer_id,
                version=1,
                local_name="run",
                module_node_id="app",
                declaration={},
                source_node_id="app.source.worker",
                source_interface_id=consumer_id,
            ),
        },
        module_children={"app": ("app.child",)},
    )


def test_build_dependency_dag_projects_facets_and_structural_edges() -> None:
    graph = _graph()
    provider_interface = "provider.source.api.interface.read"
    consumer_interface = "app.source.worker.interface.run"
    states = {
        "app": NodeHashState(dependency_hashes=()),
        "app.child": NodeHashState(),
        "provider": NodeHashState(),
        "provider.source.api": NodeHashState(
            facets=(
                CertificationFacetHashState(
                    "provider.source.api", "remainder", "sha256:r"
                ),
                CertificationFacetHashState(
                    provider_interface, "interface", "sha256:i"
                ),
            )
        ),
        "app.source.worker": NodeHashState(
            dependency_hashes=(
                {
                    "relation": "uses-private-interface",
                    "target": "provider.source.api",
                    "interface": provider_interface,
                    "version": 1,
                    "interface_hash": "sha256:x",
                },
            ),
            facets=(
                CertificationFacetHashState(
                    "app.source.worker",
                    "remainder",
                    "sha256:r",
                    dependency_hashes=(),
                ),
                CertificationFacetHashState(
                    consumer_interface,
                    "interface",
                    "sha256:i",
                    dependency_hashes=(
                        {
                            "relation": "uses-private-interface",
                            "target": "provider.source.api",
                            "interface": provider_interface,
                            "version": 1,
                            "interface_hash": "sha256:x",
                        },
                    ),
                ),
            ),
        ),
    }

    payload = build_dependency_dag(graph, states, Path("/repo"))
    by_id = {node["id"]: node for node in payload["nodes"]}

    assert [node["id"] for node in payload["nodes"]] == sorted(by_id)
    assert by_id[consumer_interface]["dependencies"] == [provider_interface]
    assert by_id["app.source.worker"]["dependencies"] == [consumer_interface]
    assert by_id["app"]["dependencies"] == ["app.child", "app.source.worker"]
    assert by_id[provider_interface]["owner_node_id"] == "provider.source.api"


def test_decode_dependency_dag_rejects_unknown_targets_and_cycles() -> None:
    base = {
        "schema_version": "officina.certification-dependency-dag/v1",
        "repository": "/repo",
        "nodes": [
            {
                "id": "a",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": ["missing"],
            }
        ],
    }
    with pytest.raises(DependencyDagError, match="unknown dependency"):
        decode_dependency_dag(base)

    cyclic = {
        **base,
        "nodes": [
            {
                "id": "a",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": ["b"],
            },
            {
                "id": "b",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": ["a"],
            },
        ],
    }
    with pytest.raises(DependencyDagError, match="cycle"):
        decode_dependency_dag(cyclic)


def test_decode_dependency_dag_accepts_cross_host_absolute_repository() -> None:
    payload = {
        "schema_version": "officina.certification-dependency-dag/v1",
        "repository": r"C:\repo",
        "nodes": [],
    }

    decoded = decode_dependency_dag(payload)

    assert decoded["repository"] == r"C:\repo"


def test_decode_dependency_dag_rejects_unsorted_nodes_and_dependencies() -> None:
    payload = {
        "schema_version": "officina.certification-dependency-dag/v1",
        "repository": "/repo",
        "nodes": [
            {
                "id": "b",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": [],
            },
            {
                "id": "a",
                "kind": "module",
                "owner_node_id": None,
                "dependencies": [],
            },
        ],
    }
    with pytest.raises(DependencyDagError, match="sorted"):
        decode_dependency_dag(payload)


def test_decode_dependency_dag_requires_interface_before_owner_source() -> None:
    payload = {
        "schema_version": "officina.certification-dependency-dag/v1",
        "repository": "/repo",
        "nodes": [
            {
                "id": "a.interface.run",
                "kind": "interface",
                "owner_node_id": "a.source",
                "dependencies": [],
            },
            {
                "id": "a.source",
                "kind": "behavioral-source",
                "owner_node_id": None,
                "dependencies": [],
            },
        ],
    }

    with pytest.raises(DependencyDagError, match="owner source dependency"):
        decode_dependency_dag(payload)
