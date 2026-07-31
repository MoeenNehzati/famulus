#!/usr/bin/env python3
"""Graph-level analysis primitives for visualization payloads.

The renderer should own output-oriented behavior. This module owns the graph-
specific semantics that do not depend on the presentation layer:

- structural validation
- edge traversal
- graph-theoretic transitive-reduction
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

GraphPayload = dict[str, Any]


class Graph:
    """Analyze graph JSON payload structure independently of renderer output."""

    def validate_graph(self, graph_json: GraphPayload) -> None:
        """Validate that a payload has the minimal shape required by renderers."""
        if not isinstance(graph_json, dict):
            raise ValueError("Top-level JSON must be an object.")
        if "entities" not in graph_json or not isinstance(graph_json["entities"], list):
            raise ValueError("JSON must contain an 'entities' list.")

        document_meta = graph_json.get("document", {})
        if document_meta and not isinstance(document_meta, dict):
            raise ValueError("'document' must be an object when present.")
        mathjax_macros = document_meta.get("mathjax_macros", {})
        if mathjax_macros and not isinstance(mathjax_macros, dict):
            raise ValueError("'document.mathjax_macros' must be an object when present.")

        seen_ids: set[str] = set()
        for idx, entity in enumerate(graph_json["entities"], start=1):
            if not isinstance(entity, dict):
                raise ValueError(f"Entity {idx} must be an object.")
            for key in ("id", "type", "short_title", "position"):
                if key not in entity:
                    raise ValueError(f"Entity {idx} is missing required key '{key}'.")
            entity_id = str(entity["id"])
            if entity_id in seen_ids:
                raise ValueError(f"Duplicate entity id: {entity['id']}")
            seen_ids.add(entity_id)
            connects_to = entity.get("connects_to", [])
            if not isinstance(connects_to, list):
                raise ValueError(f"Entity '{entity_id}' has non-list 'connects_to'.")
            for child in entity.get("children", []):
                if not isinstance(child, str) or not child.strip():
                    raise ValueError(f"Entity '{entity_id}' has invalid child in 'children'.")
            if not isinstance(entity.get("type"), str) or not entity.get("type"):
                raise ValueError(f"Entity '{entity_id}' has invalid 'type'.")
            if not isinstance(entity.get("position"), int):
                raise ValueError(f"Entity '{entity_id}' has invalid 'position'.")
            if entity.get("ref") is not None and not isinstance(entity.get("ref"), str):
                raise ValueError(f"Entity '{entity_id}' has invalid 'ref'.")

        for entity in graph_json["entities"]:
            source_id = str(entity["id"])
            for dep in entity.get("connects_to", []):
                if not isinstance(dep, dict):
                    raise ValueError(f"Dependency entry on '{source_id}' must be an object.")
                if "to" not in dep:
                    raise ValueError(f"Dependency entry on '{source_id}' is missing 'to'.")
                if "type" not in dep:
                    raise ValueError(f"Dependency entry on '{source_id}' is missing 'type'.")
                if str(dep["to"]) not in seen_ids:
                    raise ValueError(
                        f"Dependency '{dep['to']}' referenced by '{source_id}' is not defined."
                    )

    def iter_entities(self, graph_json: GraphPayload) -> list[GraphPayload]:
        """Return payload entities as a list."""
        entities = graph_json.get("entities", [])
        if not isinstance(entities, list):
            return []
        return [entity for entity in entities if isinstance(entity, dict)]

    def build_adjacency(self, graph_json: GraphPayload) -> dict[str, set[str]]:
        """Build source→target adjacency for the given graph."""
        adjacency: dict[str, set[str]] = defaultdict(set)
        for entity in self.iter_entities(graph_json):
            source = str(entity["id"])
            for dep in entity.get("connects_to", []):
                if not isinstance(dep, dict):
                    continue
                to = str(dep.get("to", ""))
                if not to:
                    continue
                adjacency[source].add(to)
        return dict(adjacency)

    def has_path(
        self,
        graph_json: GraphPayload,
        source: str,
        target: str,
        *,
        edge_type: str | None = None,
    ) -> bool:
        """Check directed reachability in O(E+V), optionally within one edge type."""
        if source == target:
            return True
        adjacency = self.build_adjacency_by_type(graph_json).get(edge_type, {}) if edge_type else self.build_adjacency(graph_json)
        visited = {source}
        stack = [source]
        while stack:
            current = stack.pop()
            for next_node in adjacency.get(current, ()):
                if next_node == target:
                    return True
                if next_node in visited:
                    continue
                visited.add(next_node)
                stack.append(next_node)
        return False

    def build_adjacency_by_type(self, graph_json: GraphPayload) -> dict[str, dict[str, set[str]]]:
        """Build edge-type-indexed source→target adjacency for the given graph."""
        adjacency_by_type: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for entity in self.iter_entities(graph_json):
            source = str(entity["id"])
            for dep in entity.get("connects_to", []):
                if not isinstance(dep, dict):
                    continue
                to = str(dep.get("to", ""))
                edge_type = str(dep.get("type", ""))
                if not to or not edge_type:
                    continue
                adjacency_by_type[edge_type][source].add(to)
        return {
            edge_type: dict(adjacency)
            for edge_type, adjacency in adjacency_by_type.items()
        }

    def reduce_transitive_edges(self, graph_json: GraphPayload) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply transitive-reduction-like cleanup on the rendered graph view."""
        reduced = copy.deepcopy(graph_json)
        entities = reduced.get("entities")
        if not isinstance(entities, list):
            return reduced, []

        entity_map = {str(entity.get("id", "")): entity for entity in entities if isinstance(entity, dict)}
        removed: list[dict[str, Any]] = []

        adjacency_by_type = self.build_adjacency_by_type(reduced)

        def has_alternate_path(source_id: str, target_id: str, edge_type: str) -> bool:
            if source_id == target_id:
                return False
            adjacency = adjacency_by_type.get(edge_type, {})
            stack = [child for child in adjacency.get(source_id, set()) if child != target_id]
            seen = {source_id}
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                if current == target_id:
                    return True
                seen.add(current)
                stack.extend(adjacency.get(current, set()))
            return False

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            original_edges = entity.get("connects_to", [])
            if not isinstance(original_edges, list):
                continue
            kept = []
            for dep in original_edges:
                if not isinstance(dep, dict):
                    continue
                dep_target = str(dep.get("to", ""))
                source_id = str(entity.get("id", ""))
                if not dep_target or source_id == dep_target:
                    kept.append(dep)
                    continue
                dep_type = str(dep.get("type", ""))
                if dep_type and has_alternate_path(source_id, dep_target, dep_type):
                    removed.append(
                        {
                            "source": source_id,
                            "target": dep_target,
                            "source_label": entity_map.get(source_id, {}).get(
                                "short_title", source_id
                            ),
                            "target_label": entity_map.get(dep_target, {}).get(
                                "short_title", dep_target
                            ),
                            "dependency": dep,
                        }
                    )
                else:
                    kept.append(dep)
            entity["connects_to"] = kept

        return reduced, removed


BaseGraph = Graph

__all__ = ["BaseGraph", "Graph", "GraphPayload"]
