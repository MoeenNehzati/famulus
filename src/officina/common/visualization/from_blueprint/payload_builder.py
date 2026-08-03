"""Project canonical repository blueprint graphs into visualization payloads."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ...blueprint_graph import RepositoryBlueprintGraph
from .catalog import (
    DETAIL_LEVELS,
    EDGE_STYLES,
    build_edge_categories,
    build_node_categories,
    build_relation_semantics,
    category_id,
)
from .details import (
    build_blueprint_details,
    build_blueprint_edge_annotation,
    build_out_of_scope_details,
)
from .scope import owning_module, resolve_blueprint_scope, top_module

Entity = dict[str, Any]
EdgeRecord = tuple[str, str, str, str, dict[str, Any]]


def _scope_detail_references(details: object, visible_ids: set[str]) -> None:
    """Keep inspector navigation honest after scope projection.

    References to rendered entities remain navigable. Logical ids represented
    only by an out-of-scope boundary stay visible as copyable code or list data
    rather than becoming dead inspector buttons.
    """
    if not isinstance(details, dict):
        return
    for section in details.get("sections", []):
        if not isinstance(section, dict):
            continue
        scoped_fields: list[dict[str, Any]] = []
        for field in section.get("fields", []):
            if not isinstance(field, dict):
                continue
            scoped = dict(field)
            field_format = scoped.get("format")
            if field_format == "reference":
                target = str(scoped.get("target", scoped.get("value", "")))
                if target not in visible_ids:
                    scoped["format"] = "code"
                    scoped["copyable"] = True
                    scoped.pop("target", None)
            elif field_format == "reference-list":
                raw_values = scoped.get("value", [])
                values = raw_values if isinstance(raw_values, list) else [raw_values]
                visible = [value for value in values if str(value) in visible_ids]
                outside = [value for value in values if str(value) not in visible_ids]
                if visible:
                    scoped["value"] = visible
                    scoped_fields.append(scoped)
                if outside:
                    outside_field = dict(scoped)
                    outside_field["label"] = (
                        f"{scoped.get('label', 'References')} (out of scope)"
                        if visible
                        else scoped.get("label", "References")
                    )
                    outside_field["value"] = outside
                    outside_field["format"] = "list"
                    outside_field.pop("target", None)
                    scoped_fields.append(outside_field)
                continue
            scoped_fields.append(scoped)
        section["fields"] = scoped_fields

def _normalize_gateway_language(value: object) -> str:
    """Return an open-ended stable kind token from a gateway language value."""
    if not isinstance(value, str) or not value.strip():
        return "unspecified"
    normalized = re.sub(r"[^a-z0-9.+_-]+", "-", value.strip().lower()).strip("-")
    return normalized or "unspecified"


def _source_gateway_language(
    graph: RepositoryBlueprintGraph,
    source_id: str | None,
) -> tuple[str, str | None]:
    if source_id is None or source_id not in graph.nodes:
        return "unspecified", None
    declaration = graph.nodes[source_id].declaration
    gateway = declaration.get("gateway")
    raw = gateway.get("language") if isinstance(gateway, dict) else None
    return _normalize_gateway_language(raw), raw if isinstance(raw, str) else None


def _module_gateway_language(
    graph: RepositoryBlueprintGraph,
    module_id: str,
) -> tuple[str, list[str]]:
    raw_languages: set[str] = set()
    kinds: set[str] = set()
    for source_id in graph.module_sources.get(module_id, ()):
        kind, raw = _source_gateway_language(graph, source_id)
        kinds.add(kind)
        if raw is not None:
            raw_languages.add(raw)
    if not kinds:
        return "structural", []
    return "+".join(sorted(kinds)), sorted(raw_languages, key=str.casefold)


def _relationship_records(graph: RepositoryBlueprintGraph) -> list[EdgeRecord]:
    records: list[EdgeRecord] = []
    architectural_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    resolved_interface_uses: dict[
        tuple[str, str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    for edge in graph.node_edges:
        if edge.relation in {"contains-module", "contains-source"}:
            continue
        if edge.relation in {"uses-export", "uses-private-interface"}:
            continue
        relation = (
            "depends-on-source" if edge.relation == "uses-source" else edge.relation
        )
        metadata = {"required_version": edge.required_version}
        records.append(
            (edge.source_id, edge.target_id, relation, "node_edges", metadata)
        )
        architectural_records[(edge.source_id, edge.target_id, edge.relation)] = metadata
    for source_id in sorted(graph.source_modules):
        source = graph.nodes[source_id]
        raw_uses = source.declaration.get("uses_interfaces", [])
        if not isinstance(raw_uses, list):
            continue
        for use in raw_uses:
            if not isinstance(use, Mapping):
                continue
            interface_id = use.get("interface")
            version = use.get("version")
            if not isinstance(interface_id, str):
                continue
            interface = graph.exports.get(interface_id) or graph.source_interfaces.get(
                interface_id
            )
            if interface is None:
                continue
            metadata = {
                "required_version": version,
                "resolved_source_id": interface.source_node_id,
                "interface_exposure": (
                    "exported" if interface_id in graph.exports else "source"
                ),
            }
            records.append(
                (source_id, interface_id, "uses-interface", "uses_interfaces", metadata)
            )
            resolved_relation = (
                "uses-export"
                if interface_id in graph.exports
                else "uses-private-interface"
            )
            if interface.source_node_id is not None:
                resolved_interface_uses[
                    (source_id, interface.source_node_id, resolved_relation)
                ].append(metadata)
    for edge in graph.helper_edges:
        records.append(
            (
                edge.source_export_id,
                edge.target_interface_id,
                "helper-dependency",
                "helper_edges",
                {
                    "local_helper_id": edge.local_helper_id,
                    "target_version": edge.target_version,
                    "binding": dict(edge.binding),
                },
            )
        )
    for edge in graph.certification_edges:
        if edge.relation in {"contains-module", "contains-source"}:
            continue
        resolved_uses = resolved_interface_uses.get(
            (edge.source_node_id, edge.target_node_id, edge.relation)
        )
        if resolved_uses:
            for metadata in resolved_uses:
                metadata["certification_dependency"] = "Direct"
                metadata["certification_target_version"] = edge.target_version
            continue
        architectural = architectural_records.get(
            (edge.source_node_id, edge.target_node_id, edge.relation)
        )
        if architectural is not None:
            architectural["certification_dependency"] = True
            architectural["certification_target_version"] = edge.target_version
            continue
        certification_relation = (
            "certificate-indirectly-depends"
            if edge.relation == "uses-export"
            else f"certification:{edge.relation}"
        )
        records.append(
            (
                edge.source_node_id,
                edge.target_node_id,
                certification_relation,
                "certification_edges",
                {"target_version": edge.target_version},
            )
        )
    for route in graph.routed_interfaces:
        records.append(
            (
                route.interface_id,
                route.terminal_module_id,
                "exposes-child-interface",
                "routed_interfaces",
                {
                    "route_owner_id": route.route_owner_id,
                    "child_module_id": route.child_module_id,
                    "terminal_module_version": route.terminal_module_version,
                },
            )
        )
    return records


def _add_edge(entity: Entity, target: str, relation: str, **payload: Any) -> None:
    edge = {"to": target, "type": relation, **payload}
    key = (target, relation, str(edge.get("metadata", {})))
    existing = {
        (item.get("to"), item.get("type"), str(item.get("metadata", {})))
        for item in entity["connects_to"]
    }
    if key not in existing:
        entity["connects_to"].append(edge)


def build_payload_from_repository_graph(
    graph: RepositoryBlueprintGraph,
    *,
    repo_root: Path,
    skills: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Map one loaded repository graph into a scoped hierarchical payload."""
    root = Path(repo_root).resolve()
    scope = resolve_blueprint_scope(graph, skills)
    requested = scope.requested
    included_modules = set(scope.modules)
    included_ids = set(scope.entity_ids)
    interfaces = {**graph.source_interfaces, **graph.exports}

    entities: dict[str, Entity] = {}

    for node_id in sorted(included_ids & set(graph.nodes)):
        node = graph.nodes[node_id]
        if node_id in graph.module_parents:
            parent = graph.module_parents.get(node_id)
            container = parent if parent in included_modules else None
            role = "module"
            kind, raw_languages = _module_gateway_language(graph, node_id)
        else:
            container = graph.source_modules.get(node_id)
            role = "behavioral_source"
            kind, raw_language = _source_gateway_language(graph, node_id)
            raw_languages = [raw_language] if raw_language is not None else []
        entities[node_id] = {
            "id": node_id,
            "type": role,
            "kind": kind,
            "category": category_id(role, kind),
            "presentation": {
                "form": "container",
                "tone": "subtle" if role == "module" else "strong",
            },
            "short_title": graph.module_local_segments.get(node_id, node_id.rsplit(".", 1)[-1]),
            "title": node_id,
            "ref": node_id,
            "description": str(
                node.declaration.get("description")
                or f"Blueprint {node.node_type} logical node."
            ),
            **({"container": container} if container else {}),
            "position": len(entities),
            "connects_to": [],
            "metadata": {
                "version": node.version,
                "blueprint_node_type": node.node_type,
                "gateway_languages": raw_languages,
                "blueprint_path": node.blueprint_path.as_posix(),
            },
        }

    for interface_id in sorted(included_ids & set(interfaces)):
        interface = interfaces[interface_id]
        is_export = interface_id in graph.exports
        role = "interface-export" if is_export else "private-interface"
        kind, raw_language = _source_gateway_language(graph, interface.source_node_id)
        entities[interface_id] = {
            "id": interface_id,
            "type": role,
            "kind": kind,
            "category": category_id(role, kind),
            "presentation": {"form": "node", "tone": "strong"},
            "short_title": interface.local_name,
            "title": interface_id,
            "ref": interface_id,
            "description": str(
                interface.declaration.get("description")
                or ("Public interface export." if is_export else "Source interface contract.")
            ),
            "container": (
                interface.module_node_id
                if is_export
                else interface.source_node_id or interface.module_node_id
            ),
            "position": len(entities),
            "connects_to": [],
            "metadata": {
                "version": interface.version,
                "gateway_language": raw_language,
                "source_node_id": interface.source_node_id,
                "terminal_interface_id": interface.terminal_interface_id,
                "terminal_module_node_id": interface.terminal_module_node_id,
            },
        }
        if (
            is_export
            and interface.source_interface_id
            and interface.terminal_interface_id == interface_id
        ):
            annotation = build_blueprint_edge_annotation(
                graph,
                root,
                source_id=interface_id,
                target_id=interface.source_interface_id,
                relation="binds-interface",
                provenance="export_declaration",
                metadata={
                    "target_version": interface.version,
                    "binding_kind": "source",
                },
            )
            entities[interface_id]["connects_to"].append(
                {
                    "to": interface.source_interface_id,
                    "type": "binds-interface",
                    **annotation,
                    "source": "explicit",
                    "metadata": {
                        "binding_kind": "source",
                        "implementing_source_id": interface.source_node_id,
                    },
                }
            )
        elif (
            is_export
            and interface.terminal_interface_id
            and interface.terminal_interface_id != interface_id
        ):
            annotation = build_blueprint_edge_annotation(
                graph,
                root,
                source_id=interface_id,
                target_id=interface.terminal_interface_id,
                relation="binds-interface",
                provenance="export_declaration",
                metadata={
                    "target_version": interface.version,
                    "binding_kind": "facade",
                },
            )
            entities[interface_id]["connects_to"].append(
                {
                    "to": interface.terminal_interface_id,
                    "type": "binds-interface",
                    **annotation,
                    "source": "explicit",
                    "metadata": {
                        "binding_kind": "facade",
                        "implementing_source_id": interface.source_node_id,
                        "terminal_module_node_id": interface.terminal_module_node_id,
                    },
                }
            )

    boundary_members: dict[str, set[str]] = defaultdict(set)
    records = _relationship_records(graph)
    for source, target, relation, provenance, metadata in records:
        if relation.removeprefix("certification:") in {
            "facades-child-export",
            "facades-implementing-source",
        }:
            continue
        source_inside = source in entities
        target_inside = target in entities
        if not source_inside and not target_inside:
            continue

        mapped_source = source
        mapped_target = target
        outside_id: str | None = None
        if source_inside != target_inside:
            outside_id = target if source_inside else source
            owner = owning_module(graph, outside_id)
            outside_root = top_module(graph, owner) if owner is not None else outside_id
            boundary_id = f"boundary:{outside_root}"
            boundary_members[boundary_id].add(outside_id)
            if source_inside:
                mapped_target = boundary_id
            else:
                mapped_source = boundary_id
        elif not requested:
            pass

        if mapped_source not in entities and mapped_source.startswith("boundary:"):
            outside_root = mapped_source.removeprefix("boundary:")
            entities[mapped_source] = {
                "id": mapped_source,
                "type": "out-of-scope",
                "kind": "out-of-scope",
                "category": "out-of-scope",
                "short_title": outside_root,
                "title": f"Outside scope: {outside_root}",
                "ref": outside_root,
                "description": "Truncated relationships to an unexpanded module outside the selected scope.",
                "position": len(entities),
                "connects_to": [],
                "metadata": {"boundary": True, "outside_root": outside_root},
            }
        if mapped_target not in entities and mapped_target.startswith("boundary:"):
            outside_root = mapped_target.removeprefix("boundary:")
            entities[mapped_target] = {
                "id": mapped_target,
                "type": "out-of-scope",
                "kind": "out-of-scope",
                "category": "out-of-scope",
                "short_title": outside_root,
                "title": f"Outside scope: {outside_root}",
                "ref": outside_root,
                "description": "Truncated relationships to an unexpanded module outside the selected scope.",
                "position": len(entities),
                "connects_to": [],
                "metadata": {"boundary": True, "outside_root": outside_root},
            }
        if mapped_source not in entities or mapped_target not in entities:
            continue

        edge_metadata = {"provenance": provenance, "relation": relation, **metadata}
        if outside_id is not None:
            edge_metadata = {
                "boundary": True,
                "outside_id": outside_id,
                "outside_root": (mapped_source if mapped_source.startswith("boundary:") else mapped_target).removeprefix("boundary:"),
                "provenance": provenance,
                "relation": relation,
                **metadata,
            }
        annotation = build_blueprint_edge_annotation(
            graph,
            root,
            source_id=source,
            target_id=target,
            relation=relation,
            provenance=provenance,
            metadata=metadata,
            scope_crossing=outside_id is not None,
        )
        _add_edge(
            entities[mapped_source],
            mapped_target,
            relation,
            **annotation,
            implicit=outside_id is not None,
            **(
                {"projection_target": str(metadata["resolved_source_id"])}
                if relation == "uses-interface"
                and metadata.get("resolved_source_id") in entities
                else {}
            ),
            metadata=edge_metadata,
        )

    for boundary_id, members in boundary_members.items():
        if boundary_id in entities:
            entities[boundary_id]["metadata"]["represented_ids"] = sorted(members)
            entities[boundary_id]["metadata"]["represented_count"] = len(members)

    for entity_id, entity in entities.items():
        if entity_id.startswith("boundary:"):
            represented = list(entity["metadata"].get("represented_ids", []))
            entity["details"] = build_out_of_scope_details(
                str(entity["metadata"]["outside_root"]),
                represented,
            )
        else:
            entity["details"] = build_blueprint_details(graph, root, entity_id)
        entity["detail_level"] = {
            "module": "module",
            "out-of-scope": "module",
            "behavioral_source": "source",
            "interface-export": "interface",
            "private-interface": "interface",
        }.get(str(entity.get("type")), "interface")

    visible_ids = set(entities)
    for entity in entities.values():
        _scope_detail_references(entity.get("details"), visible_ids)
        for edge in entity.get("connects_to", []):
            if isinstance(edge, dict):
                _scope_detail_references(edge.get("details"), visible_ids)

    ordered = sorted(entities.values(), key=lambda item: (int(item["position"]), str(item["id"])))
    for position, entity in enumerate(ordered):
        entity["position"] = position
        entity["connects_to"].sort(key=lambda edge: (str(edge["type"]), str(edge["to"])))

    collapsed: list[str] = []
    certification_types = sorted(
        {record[2] for record in records if record[2].startswith("certification:")}
    )

    categories = build_node_categories(str(entity["category"]) for entity in ordered)
    edge_categories = build_edge_categories(ordered)
    canonical_edge_types = {
        str(edge["type"])
        for entity in ordered
        for edge in entity.get("connects_to", [])
        if isinstance(edge, Mapping) and "type" in edge
    }
    relation_semantics = build_relation_semantics(canonical_edge_types)
    return {
        "schema_version": 2,
        "graph_kind": "repository_blueprint",
        "graph_id": root.as_posix() if not requested else f"{root.as_posix()}::{','.join(requested)}",
        "document": {
            "title": "Repository blueprint" if not requested else f"Blueprint scope: {', '.join(requested)}",
            "source_file": root.as_posix(),
            "generated_by": "officina.common.visualization.from_blueprint",
            "render_profile": "architecture",
        },
        "metadata": {"scope": "repository" if not requested else "skills", "skills": list(requested)},
        "renderer_dependencies": [],
        "detail_levels": DETAIL_LEVELS,
        "categories": categories,
        "edge_categories": edge_categories,
        "relation_semantics": relation_semantics,
        "render_modes": ["architecture"],
        "default_mode": "architecture",
        "ui": {
            "edge_styles": EDGE_STYLES,
            "layout": {"rankdir": "LR"},
            "visibility": {
                "detail_level": "module",
                "collapsed_containers": collapsed,
                "hidden_edge_types": certification_types,
            },
        },
        "entities": ordered,
    }


__all__ = ["build_payload_from_repository_graph"]
