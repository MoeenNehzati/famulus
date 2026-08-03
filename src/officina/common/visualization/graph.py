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

        category_ids = self._validate_category_catalog(
            graph_json.get("categories", []), "categories", "category"
        )
        edge_category_ids = self._validate_category_catalog(
            graph_json.get("edge_categories", []), "edge_categories", "edge category"
        )
        detail_levels = graph_json.get("detail_levels", [])
        if not isinstance(detail_levels, list):
            raise ValueError("'detail_levels' must be a list when present.")
        detail_level_ids: set[str] = set()
        for level in detail_levels:
            if not isinstance(level, dict) or not isinstance(level.get("id"), str):
                raise ValueError("Each detail level must have a string 'id'.")
            level_id = level["id"]
            if level_id in detail_level_ids:
                raise ValueError(f"Duplicate detail level id: {level_id}")
            detail_level_ids.add(level_id)

        if category_ids:
            for entity in graph_json["entities"]:
                if not isinstance(entity, dict):
                    continue
                category_id = entity.get("category")
                if category_id is not None and category_id not in category_ids:
                    raise ValueError(
                        f"Entity {entity.get('id')!r} references unknown category {category_id!r}."
                    )

        document_meta = graph_json.get("document", {})
        if document_meta and not isinstance(document_meta, dict):
            raise ValueError("'document' must be an object when present.")
        if "mathjax_macros" in document_meta:
            raise ValueError(
                "'document.mathjax_macros' is obsolete; declare MathJax in "
                "'renderer_dependencies' and place macros in its configuration."
            )
        renderer_dependencies = graph_json.get("renderer_dependencies", [])
        if not isinstance(renderer_dependencies, list):
            raise ValueError("'renderer_dependencies' must be a list when present.")

        seen_ids: set[str] = set()
        containers: dict[str, str] = {}
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
            if entity.get("children"):
                raise ValueError(
                    f"Entity '{entity_id}' uses noncanonical 'children'; "
                    "declare containment with each child's canonical 'container' field."
                )
            container = entity.get("container")
            if container is not None:
                if not isinstance(container, str) or not container.strip():
                    raise ValueError(f"Entity '{entity_id}' has invalid 'container'.")
                containers[entity_id] = container.strip()
            if not isinstance(entity.get("type"), str) or not entity.get("type"):
                raise ValueError(f"Entity '{entity_id}' has invalid 'type'.")
            if not isinstance(entity.get("position"), int):
                raise ValueError(f"Entity '{entity_id}' has invalid 'position'.")
            if entity.get("ref") is not None and not isinstance(entity.get("ref"), str):
                raise ValueError(f"Entity '{entity_id}' has invalid 'ref'.")
            entity_level = entity.get("detail_level")
            if detail_level_ids and entity_level not in detail_level_ids:
                raise ValueError(
                    f"Entity '{entity_id}' references unknown detail level {entity_level!r}."
                )

        for entity_id, container_id in containers.items():
            if container_id not in seen_ids:
                raise ValueError(
                    f"Entity '{entity_id}' references unknown container '{container_id}'."
                )
            if container_id == entity_id:
                raise ValueError(f"Entity '{entity_id}' cannot contain itself.")

        for entity_id in containers:
            seen_chain: set[str] = set()
            current = entity_id
            while current in containers:
                if current in seen_chain:
                    raise ValueError(f"Entity '{entity_id}' participates in a containment cycle.")
                seen_chain.add(current)
                current = containers[current]

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
                projection_target = dep.get("projection_target")
                if projection_target is not None and str(projection_target) not in seen_ids:
                    raise ValueError(
                        f"Dependency on '{source_id}' references unknown projection target "
                        f"{projection_target!r}."
                    )
                edge_type = str(dep["type"])
                if edge_category_ids and edge_type not in edge_category_ids:
                    raise ValueError(
                        f"Dependency type {edge_type!r} referenced by {source_id!r} "
                        "is absent from 'edge_categories'."
                    )

        self._validate_relation_semantics(
            graph_json.get("relation_semantics", {}), edge_category_ids
        )

        self._validate_ui_references(
            graph_json,
            entity_ids=seen_ids,
            container_ids=set(containers.values()),
            category_ids=category_ids or {
                str(entity.get("category") or entity.get("type") or "unknown")
                for entity in graph_json["entities"]
            },
            edge_type_ids=edge_category_ids or {
                str(dep["type"])
                for entity in graph_json["entities"]
                for dep in entity.get("connects_to", [])
                if isinstance(dep, dict) and "type" in dep
            },
        )
        self._validate_detail_references(graph_json["entities"], seen_ids)
        selected_level = (
            (graph_json.get("ui", {}) or {}).get("visibility", {}) or {}
        ).get("detail_level")
        if selected_level is not None and selected_level not in detail_level_ids:
            raise ValueError(
                f"ui.visibility.detail_level references unknown level {selected_level!r}."
            )

    def _validate_relation_semantics(self, raw: object, edge_types: set[str]) -> None:
        """Validate the finite relation transducer declared by an adapter."""
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError("'relation_semantics' must be an object when present.")
        transformations = raw.get("transformations", {}) or {}
        if not isinstance(transformations, dict):
            raise ValueError("'relation_semantics.transformations' must be an object.")
        node_omission = transformations.get("node_omission", {}) or {}
        if not isinstance(node_omission, dict):
            raise ValueError("'relation_semantics.transformations.node_omission' must be an object.")
        allowed_causes = {"user-hidden", "filter-hidden", "detail-hidden"}
        rule_ids: set[str] = set()
        cells: set[tuple[str, str, str]] = set()
        rules = node_omission.get("rules", []) or []
        if not isinstance(rules, list):
            raise ValueError("'relation_semantics.transformations.node_omission.rules' must be a list.")
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                raise ValueError("Each relation-transformation rule requires a string 'id'.")
            rule_id = rule["id"]
            if not rule_id or rule_id in rule_ids:
                raise ValueError(f"Duplicate or empty relation-transformation id: {rule_id!r}")
            rule_ids.add(rule_id)
            causes = rule.get("causes")
            left_types = rule.get("left_types")
            right_types = rule.get("right_types")
            outcomes = rule.get("outcomes")
            arrays = (causes, left_types, right_types)
            if not all(isinstance(values, list) and values for values in arrays):
                raise ValueError(f"Relation transformation {rule_id!r} requires non-empty matcher lists.")
            if any(len(values) != len(set(map(str, values))) for values in arrays):
                raise ValueError(f"Relation transformation {rule_id!r} contains duplicate matchers.")
            if not isinstance(outcomes, list) or not outcomes:
                raise ValueError(f"Relation transformation {rule_id!r} requires outcomes.")
            outcome_types: set[str] = set()
            for outcome in outcomes:
                if not isinstance(outcome, dict) or not isinstance(outcome.get("type"), str):
                    raise ValueError(f"Relation transformation {rule_id!r} has an invalid outcome.")
                outcome_type = outcome["type"]
                if outcome_type in outcome_types:
                    raise ValueError(f"Relation transformation {rule_id!r} repeats outcome {outcome_type!r}.")
                outcome_types.add(outcome_type)
                if outcome.get("fidelity") not in {"exact", "degraded"}:
                    raise ValueError(f"Relation transformation {rule_id!r} has invalid fidelity.")
            unknown_causes = set(map(str, causes)) - allowed_causes
            referenced_types = {
                *map(str, left_types), *map(str, right_types), *outcome_types
            }
            if unknown_causes:
                raise ValueError(f"Relation transformation {rule_id!r} has unknown causes: {sorted(unknown_causes)}")
            if edge_types and not referenced_types <= edge_types:
                raise ValueError(
                    f"Relation transformation {rule_id!r} references unknown edge types: "
                    f"{sorted(referenced_types - edge_types)}"
                )
            for cause in map(str, causes):
                for left_type in map(str, left_types):
                    for right_type in map(str, right_types):
                        cell = (cause, left_type, right_type)
                        if cell in cells:
                            raise ValueError(f"Duplicate relation-transformation cell {cell!r}.")
                        cells.add(cell)

        subsumptions = raw.get("subsumptions", []) or []
        if not isinstance(subsumptions, list):
            raise ValueError("'relation_semantics.subsumptions' must be a list.")
        weaker_by_type: dict[str, set[str]] = {}
        direct_pairs: set[tuple[str, str]] = set()
        for rule in subsumptions:
            if not isinstance(rule, dict) or not isinstance(rule.get("stronger_type"), str):
                raise ValueError("Each relation subsumption requires 'stronger_type'.")
            weaker = rule.get("weaker_types")
            if not isinstance(weaker, list) or not weaker:
                raise ValueError("Each relation subsumption requires 'weaker_types'.")
            stronger = rule["stronger_type"]
            referenced = {stronger, *(str(value) for value in weaker)}
            if edge_types and not referenced <= edge_types:
                raise ValueError(
                    "Relation subsumption references unknown edge types: "
                    f"{sorted(referenced - edge_types)}"
                )
            for weaker_type in map(str, weaker):
                pair = (stronger, weaker_type)
                if stronger == weaker_type or pair in direct_pairs:
                    raise ValueError(f"Duplicate or reflexive relation subsumption {pair!r}.")
                direct_pairs.add(pair)
                weaker_by_type.setdefault(stronger, set()).add(weaker_type)

        def reaches(start: str, target: str) -> bool:
            pending = list(weaker_by_type.get(start, ()))
            seen: set[str] = set()
            while pending:
                current = pending.pop()
                if current == target:
                    return True
                if current not in seen:
                    seen.add(current)
                    pending.extend(weaker_by_type.get(current, ()))
            return False

        if any(reaches(edge_type, edge_type) for edge_type in weaker_by_type):
            raise ValueError("'relation_semantics.subsumptions' must be acyclic.")

    def _validate_category_catalog(
        self, raw: object, field: str, label: str
    ) -> set[str]:
        """Validate one category hierarchy and return its identifiers."""
        if not isinstance(raw, list):
            raise ValueError(f"'{field}' must be a list when present.")
        parents: dict[str, str] = {}
        identifiers: set[str] = set()
        for category in raw:
            if not isinstance(category, dict) or not isinstance(category.get("id"), str):
                raise ValueError(f"Each {label} must be an object with a string 'id'.")
            identifier = category["id"]
            if identifier in identifiers:
                raise ValueError(f"Duplicate {label} id: {identifier}")
            identifiers.add(identifier)
            parent = category.get("parent")
            if parent is not None:
                if not isinstance(parent, str) or not parent:
                    raise ValueError(f"{label.title()} {identifier!r} has an invalid parent.")
                parents[identifier] = parent
        for identifier, parent in parents.items():
            if parent not in identifiers:
                raise ValueError(
                    f"{label.title()} {identifier!r} references unknown parent {parent!r}."
                )
            seen = {identifier}
            current = parent
            while current in parents:
                if current in seen:
                    raise ValueError(f"{label.title()} hierarchy contains a cycle.")
                seen.add(current)
                current = parents[current]
        return identifiers

    def _validate_ui_references(
        self,
        graph_json: GraphPayload,
        *,
        entity_ids: set[str],
        container_ids: set[str],
        category_ids: set[str],
        edge_type_ids: set[str],
    ) -> None:
        """Reject initial UI state that cannot be represented by this graph."""
        ui = graph_json.get("ui", {}) or {}
        visibility = ui.get("visibility", {}) or {}
        for field, allowed in (
            ("hidden_nodes", entity_ids),
            ("collapsed_containers", container_ids),
            ("hidden_types", category_ids),
            ("hidden_edge_types", edge_type_ids),
        ):
            for value in visibility.get(field, []) or []:
                if str(value) not in allowed:
                    raise ValueError(f"ui.visibility.{field} references unknown id {value!r}.")
        selected = (ui.get("focus", {}) or {}).get("selected_node_id")
        if selected is not None and str(selected) not in entity_ids:
            raise ValueError(f"ui.focus.selected_node_id references unknown id {selected!r}.")

    def _validate_detail_references(
        self, entities: list[GraphPayload], entity_ids: set[str]
    ) -> None:
        """Validate structured inspector references as internal graph references."""
        for entity in entities:
            details = entity.get("details") or {}
            for section in details.get("sections", []) if isinstance(details, dict) else []:
                for field in section.get("fields", []) if isinstance(section, dict) else []:
                    if not isinstance(field, dict):
                        continue
                    value_format = field.get("format")
                    if value_format == "reference":
                        targets = [field.get("target", field.get("value"))]
                    elif value_format == "reference-list":
                        value = field.get("value", [])
                        targets = value if isinstance(value, list) else [value]
                    else:
                        continue
                    for target in targets:
                        if str(target) not in entity_ids:
                            raise ValueError(
                                f"Entity {entity.get('id')!r} has inspector reference "
                                f"to unknown entity {target!r}."
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
