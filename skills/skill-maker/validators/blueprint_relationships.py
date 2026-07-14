"""Validate relationships across legacy and typed blueprint graphs."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintEdge,
    BlueprintGraphError,
    BlueprintNode,
    SkillBlueprintGraph,
    edge_key,
    graph_contract_errors,
    load_repository_blueprint_graphs,
)


def _edge_context(edge: BlueprintEdge, nodes: dict[str, BlueprintNode]) -> str:
    source = nodes.get(edge.source_id)
    path = source.blueprint_path if source is not None else edge.source_id
    return f"{path}: {edge.source_id} {edge.relation}"

def _index_nodes(
    graphs: dict[str, SkillBlueprintGraph],
) -> tuple[dict[str, BlueprintNode], list[str]]:
    nodes: dict[str, BlueprintNode] = {}
    errors: list[str] = []
    for graph in graphs.values():
        for node_id, node in graph.nodes.items():
            existing = nodes.get(node_id)
            if existing is not None and existing.blueprint_path != node.blueprint_path:
                errors.append(
                    f"duplicate node id `{node_id}` in {existing.blueprint_path} and "
                    f"{node.blueprint_path}"
                )
                continue
            nodes[node_id] = node
    return nodes, errors


def validate_graphs(
    graphs: dict[str, SkillBlueprintGraph],
    *,
    schema_root: Path | None = None,
) -> list[str]:
    """Validate graph-wide identity, version, type, access, and cycle rules."""

    nodes, errors = _index_nodes(graphs)
    if not graphs:
        return errors
    seen_edges: set[tuple[str, str, str, int, str | None]] = set()
    unique_edges: list[BlueprintEdge] = []
    for graph in graphs.values():
        graph_edges: set[tuple[str, str, str, int, str | None]] = set()
        for edge in graph.edges:
            identity = edge_key(edge)
            if identity in graph_edges:
                errors.append(
                    f"{_edge_context(edge, nodes)} duplicates an existing relationship"
                )
                continue
            graph_edges.add(identity)
            if identity in seen_edges:
                continue
            seen_edges.add(identity)
            unique_edges.append(edge)
    first_graph = next(iter(graphs.values()))
    combined = SkillBlueprintGraph(
        first_graph.skill_root,
        first_graph.root,
        nodes,
        tuple(unique_edges),
        tuple(sorted(graphs)),
    )
    errors.extend(
        graph_contract_errors(
            combined,
            schema_root or (_REPO_ROOT / "references" / "blueprint"),
        )
    )
    return errors


def validate(repo_root: Path) -> list[str]:
    """Load all canonical roots and validate their reachable graph relationships."""

    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return []
    try:
        graphs = load_repository_blueprint_graphs(repo_root)
    except BlueprintGraphError as exc:
        return [str(exc)]

    errors: list[str] = []
    for graph in graphs.values():
        if "depends_on" in graph.root.declaration:
            errors.append(
                f"{graph.root.blueprint_path}: top-level `depends_on` has been removed; "
                "use `uses_interfaces`"
            )
    errors.extend(validate_graphs(graphs))
    return errors


def main() -> int:
    errors = validate(_REPO_ROOT)
    if errors:
        print("error: invalid blueprint relationships.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
