"""Neutral certification dependency DAG projection and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..blueprints.graph import RepositoryBlueprintGraph
from .hashing import EVIDENCE_ONLY_RELATIONS, NodeHashState


DAG_SCHEMA_VERSION = "officina.certification-dependency-dag/v1"
_KINDS = {"module", "behavioral-source", "interface"}


class DependencyDagError(ValueError):
    """Raised when a certification dependency DAG is invalid."""


def _dependency_target(
    graph: RepositoryBlueprintGraph,
    dependency: Mapping[str, Any],
) -> str:
    interface_id = dependency.get("interface")
    if isinstance(interface_id, str):
        if interface_id in graph.source_interfaces:
            return interface_id
        exported = graph.exports.get(interface_id)
        if exported is not None and isinstance(exported.source_interface_id, str):
            terminal = exported.source_interface_id
            if terminal in graph.source_interfaces:
                return terminal
        raise DependencyDagError(f"unresolved interface dependency: {interface_id}")
    target = dependency.get("target")
    if not isinstance(target, str) or target not in graph.nodes:
        raise DependencyDagError(f"unknown node dependency: {target!r}")
    return target


def _ordering_dependencies(
    graph: RepositoryBlueprintGraph,
    dependencies: tuple[dict[str, Any], ...],
) -> set[str]:
    result: set[str] = set()
    for dependency in dependencies:
        if dependency.get("relation") in EVIDENCE_ONLY_RELATIONS:
            continue
        result.add(_dependency_target(graph, dependency))
    return result


def build_dependency_dag(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    repository: Path,
) -> dict[str, Any]:
    """Project final canonical node/facet dependencies into a neutral DAG."""

    missing_states = sorted(set(graph.nodes) - set(states))
    if missing_states:
        raise DependencyDagError(
            "missing node hash state: " + ", ".join(missing_states)
        )

    records: dict[str, dict[str, Any]] = {}
    for node_id, node in sorted(graph.nodes.items()):
        state = states[node_id]
        if node.node_type == "module":
            dependencies = _ordering_dependencies(graph, state.dependency_hashes)
            dependencies.update(graph.module_sources.get(node_id, ()))
            dependencies.update(graph.module_children.get(node_id, ()))
            kind = "module"
        elif node.node_type == "behavioral_source":
            remainder = next(
                (facet for facet in state.facets if facet.facet_type == "remainder"),
                None,
            )
            if remainder is None:
                raise DependencyDagError(f"missing remainder facet: {node_id}")
            dependencies = _ordering_dependencies(
                graph, remainder.dependency_hashes
            )
            owned_interfaces = sorted(
                interface_id
                for interface_id, interface in graph.source_interfaces.items()
                if interface.source_node_id == node_id
            )
            dependencies.update(owned_interfaces)
            kind = "behavioral-source"
        else:
            raise DependencyDagError(f"unsupported node type: {node.node_type}")
        records[node_id] = {
            "id": node_id,
            "kind": kind,
            "owner_node_id": None,
            "dependencies": sorted(dependencies),
        }

    for interface_id, interface in sorted(graph.source_interfaces.items()):
        owner_id = interface.source_node_id
        if not isinstance(owner_id, str) or owner_id not in graph.nodes:
            raise DependencyDagError(f"invalid interface owner: {interface_id}")
        state = states[owner_id]
        facet = next(
            (
                candidate
                for candidate in state.facets
                if candidate.facet_type == "interface"
                and candidate.facet_id == interface_id
            ),
            None,
        )
        if facet is None:
            raise DependencyDagError(f"missing interface facet: {interface_id}")
        records[interface_id] = {
            "id": interface_id,
            "kind": "interface",
            "owner_node_id": owner_id,
            "dependencies": sorted(
                _ordering_dependencies(graph, facet.dependency_hashes)
            ),
        }

    payload = {
        "schema_version": DAG_SCHEMA_VERSION,
        "repository": str(Path(repository).resolve()),
        "nodes": [records[node_id] for node_id in sorted(records)],
    }
    decode_dependency_dag(payload)
    return payload


def decode_dependency_dag(payload: object) -> dict[str, Any]:
    """Validate and normalize one v1 dependency DAG payload."""

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "repository",
        "nodes",
    }:
        raise DependencyDagError("invalid dependency DAG object")
    if payload["schema_version"] != DAG_SCHEMA_VERSION:
        raise DependencyDagError("unsupported dependency DAG schema")
    repository = payload["repository"]
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise DependencyDagError("repository must be an absolute path")
    raw_nodes = payload["nodes"]
    if not isinstance(raw_nodes, list):
        raise DependencyDagError("nodes must be an array")
    ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "kind",
            "owner_node_id",
            "dependencies",
        }:
            raise DependencyDagError("invalid dependency DAG node")
        node_id = raw["id"]
        kind = raw["kind"]
        owner = raw["owner_node_id"]
        dependencies = raw["dependencies"]
        if not isinstance(node_id, str) or not node_id:
            raise DependencyDagError("node id must be nonempty")
        if kind not in _KINDS:
            raise DependencyDagError(f"invalid node kind: {kind!r}")
        if (kind == "interface") != isinstance(owner, str):
            raise DependencyDagError(f"invalid owner for {node_id}")
        if kind != "interface" and owner is not None:
            raise DependencyDagError(f"invalid owner for {node_id}")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            raise DependencyDagError(f"invalid dependencies for {node_id}")
        if dependencies != sorted(set(dependencies)):
            raise DependencyDagError(f"dependencies must be sorted and unique: {node_id}")
        ids.append(node_id)
        normalized.append(dict(raw))
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise DependencyDagError("nodes must be sorted and unique")
    known = set(ids)
    by_id = {node["id"]: node for node in normalized}
    for node in normalized:
        owner = node["owner_node_id"]
        if owner is not None and (
            owner not in by_id or by_id[owner]["kind"] != "behavioral-source"
        ):
            raise DependencyDagError(f"invalid interface owner: {node['id']}")
        if owner is not None and node["id"] not in by_id[owner]["dependencies"]:
            raise DependencyDagError(
                f"missing owner source dependency: {node['id']}"
            )
        for dependency in node["dependencies"]:
            if dependency not in known:
                raise DependencyDagError(
                    f"unknown dependency for {node['id']}: {dependency}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise DependencyDagError(f"dependency cycle at {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id]["dependencies"]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in ids:
        visit(node_id)
    return {
        "schema_version": DAG_SCHEMA_VERSION,
        "repository": repository,
        "nodes": normalized,
    }
