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
    """Analyze graph JSON payload structure independently of renderer output.

    Intent
    ------
    Provide schema-aware validation and graph operations for renderer payloads.

    Rationale
    ---------
    Keeping graph semantics outside the HTML renderer makes them reusable and
    testable without a browser.

    Pseudocode
    ----------
    - set operations = validation traversal and reduction behavior
    - return operations

    Wraps
    -----
    - none
    """

    def validate_graph(self, graph_json: GraphPayload) -> None:
        """Validate that a payload has the shape required by renderers.

        Intent
        ------
        Reject malformed canonical graph, presentation, relation, and UI data.

        Rationale
        ---------
        Renderers can stay simple when invalid cross-references and unsupported
        values fail at the Python boundary.

        Pseudocode
        ----------
        - set validated_graph = catalogs entities containment edges views and UI
        - return validated_graph

        Wraps
        -----
        - none
        """
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

        presentation_node_ids = self._validate_presentation_nodes(
            graph_json,
            entity_ids=seen_ids,
            contained_ids=set(containers),
        )

        for entity in graph_json["entities"]:
            source_id = str(entity["id"])
            for dep in entity.get("connects_to", []):
                if not isinstance(dep, dict):
                    raise ValueError(f"Dependency entry on '{source_id}' must be an object.")
                if "to" not in dep:
                    raise ValueError(f"Dependency entry on '{source_id}' is missing 'to'.")
                if "type" not in dep:
                    raise ValueError(f"Dependency entry on '{source_id}' is missing 'type'.")
                if str(dep["to"]) in presentation_node_ids:
                    raise ValueError(
                        "canonical edge cannot target presentation node: "
                        f"{dep['to']!r}"
                    )
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

    def _validate_presentation_nodes(
        self,
        graph_json: GraphPayload,
        *,
        entity_ids: set[str],
        contained_ids: set[str],
    ) -> set[str]:
        """Validate first-class view nodes without assigning graph semantics.

        Intent
        ------
        Enforce presentation-node identity, membership, rendering, and controls.

        Rationale
        ---------
        View nodes may overlap but must never become canonical graph owners or
        edge targets.

        Pseudocode
        ----------
        - set presentation_ids = validated view nodes and controls
        - return all presentation node ids

        Wraps
        -----
        - none
        """
        raw_nodes = graph_json.get("presentation_nodes", [])
        if not isinstance(raw_nodes, list):
            raise ValueError("'presentation_nodes' must be a list when present.")

        presentation_ids: set[str] = set()
        for index, node in enumerate(raw_nodes, start=1):
            if not isinstance(node, dict):
                raise ValueError(f"Presentation node {index} must be an object.")
            for key in (
                "id",
                "type",
                "short_title",
                "position",
                "member_ids",
                "presentation",
                "interaction",
            ):
                if key not in node:
                    raise ValueError(
                        f"Presentation node {index} is missing required key {key!r}."
                    )
            node_id = node["id"]
            if not isinstance(node_id, str) or not node_id:
                raise ValueError(f"Presentation node {index} has invalid id.")
            if node_id in presentation_ids:
                raise ValueError(f"duplicate presentation node id: {node_id!r}")
            if node_id in entity_ids:
                raise ValueError(
                    "presentation node id conflicts with canonical entity: "
                    f"{node_id!r}"
                )
            presentation_ids.add(node_id)

            members = node["member_ids"]
            if not isinstance(members, list) or not members:
                raise ValueError(
                    f"Presentation node {node_id!r} requires nonempty member_ids."
                )
            seen_members: set[str] = set()
            for member in members:
                member_id = str(member)
                if member_id in seen_members:
                    raise ValueError(
                        f"duplicate member {member_id!r} in presentation node {node_id!r}"
                    )
                seen_members.add(member_id)
                if member_id not in entity_ids:
                    raise ValueError(
                        f"unknown presentation node member: {member_id!r}"
                    )
                if member_id in contained_ids:
                    raise ValueError(
                        "presentation node member must be a root entity: "
                        f"{member_id!r}"
                    )

            presentation = node["presentation"]
            if not isinstance(presentation, dict):
                raise ValueError(
                    f"Presentation node {node_id!r} has invalid presentation."
                )
            if presentation.get("form") != "supernode":
                raise ValueError(
                    f"Presentation node {node_id!r} has unsupported form."
                )
            if presentation.get("tone") not in {"subtle", "strong"}:
                raise ValueError(
                    f"Presentation node {node_id!r} has unsupported tone."
                )
            if presentation.get("default_visibility") not in {"visible", "hidden"}:
                raise ValueError(
                    f"Presentation node {node_id!r} has invalid default visibility."
                )

            interaction = node["interaction"]
            if not isinstance(interaction, dict):
                raise ValueError(
                    f"Presentation node {node_id!r} has invalid interaction."
                )
            if not isinstance(interaction.get("selectable"), bool):
                raise ValueError(
                    f"Presentation node {node_id!r} has invalid selectable setting."
                )
            if not isinstance(interaction.get("inspectable"), bool):
                raise ValueError(
                    f"Presentation node {node_id!r} has invalid inspectable setting."
                )
            if interaction.get("inspectable") and not interaction.get("selectable"):
                raise ValueError(
                    "inspectable presentation node must be selectable: "
                    f"{node_id!r}"
                )
            if interaction.get("draggable") not in {"none", "self", "members"}:
                raise ValueError(
                    f"Presentation node {node_id!r} has unsupported draggable effect."
                )
            if interaction.get("collapse_effect") not in {"none", "self"}:
                raise ValueError(
                    f"Presentation node {node_id!r} has unsupported collapse effect."
                )

        ui = graph_json.get("ui", {}) or {}
        if not isinstance(ui, dict):
            return presentation_ids
        controls = ui.get("presentation_node_controls", [])
        if not isinstance(controls, list):
            raise ValueError("ui.presentation_node_controls must be a list.")

        control_ids: set[str] = set()
        global_facet_ids: set[str] = set()
        owner_by_node: dict[str, str] = {}
        for control in controls:
            if not isinstance(control, dict) or not isinstance(control.get("id"), str):
                raise ValueError("Each presentation node control requires a string id.")
            control_id = control["id"]
            if not control_id or control_id in control_ids:
                raise ValueError(
                    f"duplicate presentation node control id: {control_id!r}"
                )
            control_ids.add(control_id)
            facets = control.get("facets", [])
            if not isinstance(facets, list):
                raise ValueError(
                    f"Presentation node control {control_id!r} has non-list facets."
                )
            facet_ids: set[str] = set()
            for facet in facets:
                if not isinstance(facet, dict) or not isinstance(facet.get("id"), str):
                    raise ValueError("Each presentation node facet requires a string id.")
                facet_id = facet["id"]
                if not facet_id or facet_id in global_facet_ids:
                    raise ValueError(
                        f"duplicate presentation node facet id: {facet_id!r}"
                    )
                facet_ids.add(facet_id)
                global_facet_ids.add(facet_id)
                if facet.get("activation") not in {"all", "multiple"}:
                    raise ValueError(
                        f"Presentation node facet {facet_id!r} has invalid activation."
                    )
                node_ids = facet.get("node_ids", [])
                if not isinstance(node_ids, list):
                    raise ValueError(
                        f"Presentation node facet {facet_id!r} has non-list node_ids."
                    )
                for raw_node_id in node_ids:
                    node_id = str(raw_node_id)
                    if node_id not in presentation_ids:
                        raise ValueError(
                            f"unknown presentation node reference: {node_id!r}"
                        )
                    previous_owner = owner_by_node.get(node_id)
                    if previous_owner is not None and previous_owner != control_id:
                        raise ValueError(
                            "presentation node has multiple control owners: "
                            f"{node_id!r}"
                        )
                    owner_by_node[node_id] = control_id
            default_facet = control.get("default_facet")
            if default_facet is not None and str(default_facet) not in facet_ids:
                raise ValueError(
                    f"unknown default presentation node facet: {default_facet!r}"
                )

        return presentation_ids

    def _validate_relation_semantics(self, raw: object, edge_types: set[str]) -> None:
        """Validate the finite relation transducer declared by an adapter.

        Intent
        ------
        Check omission transformations and relation-subsumption declarations.

        Rationale
        ---------
        Projection behavior must be finite, deterministic, typed, and acyclic.

        Pseudocode
        ----------
        - set transformations = validated matcher cells and outcomes
        - set subsumptions = validated typed acyclic relation ordering
        - return validated relation semantics

        Wraps
        -----
        - none
        """
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
            """Return whether one relation type transitively subsumes another.

            Intent
            ------
            Detect reachability in the local subsumption declaration.

            Rationale
            ---------
            A bounded traversal detects cycles without exposing helper state.

            Pseudocode
            ----------
            - set pending = directly weaker relation types
            - while pending contains a relation type:
              - set current = next pending relation type
            - return true on the target or false after exhaustion

            Wraps
            -----
            - none
            """
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
        """Validate one category hierarchy and return its identifiers.

        Intent
        ------
        Check category records, uniqueness, parent references, and acyclicity.

        Rationale
        ---------
        Validated identifiers support later entity and UI reference checks.

        Pseudocode
        ----------
        - set identifiers = unique validated category ids
        - set parents = validated acyclic parent links
        - return the identifier set

        Wraps
        -----
        - none
        """
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
        """Reject initial UI state that cannot be represented by this graph.

        Intent
        ------
        Validate visibility and focus references against available graph ids.

        Rationale
        ---------
        Initial UI state must not point to entities, containers, or types that
        the renderer cannot represent.

        Pseudocode
        ----------
        - set visibility = validated visibility references
        - set selected = validated optional focus entity
        - return validated UI state

        Wraps
        -----
        - none
        """
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
        self._validate_edge_presentation(ui.get("edge_presentation", {}) or {})

    def _validate_edge_presentation(self, presentation: GraphPayload) -> None:
        """Validate identities and field paths for declarative edge facets.

        Intent
        ------
        Reject ambiguous facet catalogs that JSON Schema cannot compare across
        array entries.

        Rationale
        ---------
        Stable facet and variant identities are used by renderer composition,
        bundling, and legend rows. Field access is deliberately limited to
        canonical scalar edge properties and one metadata level.

        Pseudocode
        ----------
        - set facet ids = unique declared facet identities
        - set variant ids = unique within each facet
        - validate each field against the bounded edge-field grammar
        - return validated presentation catalog

        Wraps
        -----
        - none
        """
        facets = presentation.get("facets", [])
        seen_facets: set[str] = set()
        canonical_fields = {
            "type",
            "implicit",
            "confidence",
            "phase",
            "weight",
            "source",
            "projection_target",
        }
        style_property_owner: dict[str, str] = {}
        for facet in facets:
            facet_id = str(facet.get("id", ""))
            if facet_id in seen_facets:
                raise ValueError(
                    f"duplicate edge presentation facet id: {facet_id}"
                )
            seen_facets.add(facet_id)
            field = str(facet.get("field", ""))
            metadata_key = field.removeprefix("metadata.")
            metadata_field = (
                field.startswith("metadata.")
                and metadata_key
                and "." not in metadata_key
                and all(character.isalnum() or character in "_-" for character in metadata_key)
            )
            if field not in canonical_fields and not metadata_field:
                raise ValueError(f"unsupported edge presentation field: {field!r}")
            seen_variants: set[str] = set()
            facet_style_properties: set[str] = set()
            for variant in facet.get("variants", []):
                variant_id = str(variant.get("id", ""))
                if variant_id in seen_variants:
                    raise ValueError(
                        "duplicate edge presentation variant id "
                        f"{variant_id!r} in facet {facet_id!r}"
                    )
                seen_variants.add(variant_id)
                facet_style_properties.update((variant.get("style") or {}).keys())
            for property_name in facet_style_properties:
                owner = style_property_owner.get(property_name)
                if owner is not None:
                    raise ValueError(
                        "edge presentation style property "
                        f"{property_name!r} is written by multiple facets: "
                        f"{owner!r} and {facet_id!r}"
                    )
                style_property_owner[property_name] = facet_id

    def _validate_detail_references(
        self, entities: list[GraphPayload], entity_ids: set[str]
    ) -> None:
        """Validate structured inspector references as internal graph references.

        Intent
        ------
        Ensure inspector reference fields target canonical entities.

        Rationale
        ---------
        Reference controls should never render dead navigation targets.

        Pseudocode
        ----------
        - set targets = scalar and list references from structured details
        - set references = targets validated against entity ids
        - return validated inspector details

        Wraps
        -----
        - none
        """
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
        """Return well-formed payload entities as a list.

        Intent
        ------
        Normalize the entity collection for graph operations.

        Rationale
        ---------
        Traversals should ignore malformed loose entries defensively.

        Pseudocode
        ----------
        - set entities = graph entities value
        - return empty when it is not a list
        - return dictionary entities

        Wraps
        -----
        - none
        """
        entities = graph_json.get("entities", [])
        if not isinstance(entities, list):
            return []
        return [entity for entity in entities if isinstance(entity, dict)]

    def build_adjacency(self, graph_json: GraphPayload) -> dict[str, set[str]]:
        """Build source-to-target adjacency for the given graph.

        Intent
        ------
        Index valid canonical edges by source id.

        Rationale
        ---------
        Reachability and reduction need a compact target lookup.

        Pseudocode
        ----------
        - set adjacency = nonempty dependency targets grouped by source
        - return an ordinary dictionary

        Wraps
        -----
        - none

        """
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
        """Check directed reachability, optionally within one edge type.

        Intent
        ------
        Determine whether a directed path connects two canonical entity ids.

        Rationale
        ---------
        Iterative traversal avoids recursion limits and supports typed queries.

        Pseudocode
        ----------
        - return true for identical endpoints
        - set adjacency = typed or untyped adjacency for the request
        - set reachable = iterative traversal result
        - return reachable

        Wraps
        -----
        - none

        """
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
        """Build edge-type-indexed source-to-target adjacency for the graph.

        Intent
        ------
        Index valid canonical edges first by relation type and then source id.

        Rationale
        ---------
        Transitive reduction must compare paths only within one relation type.

        Pseudocode
        ----------
        - set adjacency_by_type = nonempty targets grouped by type and source
        - return ordinary nested dictionaries

        Wraps
        -----
        - none

        """
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
        """Apply transitive-reduction-like cleanup on the rendered graph view.

        Intent
        ------
        Remove typed edges that have an alternate path of the same type.

        Rationale
        ---------
        A deep-copied reduced view can simplify rendering without mutating or
        losing the source payload and removed-edge evidence.

        Pseudocode
        ----------
        - set reduced = deep copy of the payload
        - set removed = edges having alternate same-type paths
        - return the reduced payload and removal records

        Wraps
        -----
        - none

        """
        reduced = copy.deepcopy(graph_json)
        entities = reduced.get("entities")
        if not isinstance(entities, list):
            return reduced, []

        entity_map = {str(entity.get("id", "")): entity for entity in entities if isinstance(entity, dict)}
        removed: list[dict[str, Any]] = []

        adjacency_by_type = self.build_adjacency_by_type(reduced)

        def has_alternate_path(source_id: str, target_id: str, edge_type: str) -> bool:
            """Return whether a same-type path exists without the direct edge.

            Intent
            ------
            Test one candidate edge for typed transitive redundancy.

            Rationale
            ---------
            Excluding the direct target from the initial frontier tests only
            genuine alternate paths.

            Pseudocode
            ----------
            - set pending = successors other than the direct target
            - set reachable = iterative traversal result
            - return reachable

            Wraps
            -----
            - none
            """
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
