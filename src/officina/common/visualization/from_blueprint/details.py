"""Build human-oriented inspector data from canonical blueprint graph records.

This module owns the blueprint-specific choice of what a reader needs to know:
what a logical node does, where it sits in the repository, how it is exposed,
and which runtime artifact implements it. The generic HTML renderer only
renders the resulting sections and fields.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...blueprint_graph import InterfaceExport, RepositoryBlueprintGraph

Details = dict[str, Any]
Field = dict[str, Any]

_EDGE_LABELS = {
    "binds-interface": "Binds interface",
    "uses-interface": "Uses interface",
    "depends-on-source": "Depends on behavioral source",
    "helper-dependency": "Helper dependency",
    "exposes-child-interface": "Exposes child interface",
    "certificate-indirectly-depends": "Certificate indirectly depends",
}

_EDGE_SUMMARIES = {
    "binds-interface": "Connects an exported logical address to the interface that supplies its contract. The binding is declared as either a direct source-interface binding or a facade over a child module's export.",
    "uses-interface": "Records the exact repository interface named in a behavioral source's uses_interfaces declaration. The edge targets the declared logical contract rather than projecting the relationship onto its resolved implementation source.",
    "depends-on-source": "Records an explicit behavioral-source dependency declared by the source blueprint. Unlike an interface use, this relationship directly names another repository behavioral source as the dependency.",
    "helper-dependency": "Connects an interface contract's named helper binding to the repository interface that fulfills it. It represents explicit contract composition rather than an inferred call discovered from implementation code.",
    "exposes-child-interface": "Shows that a module makes an interface reachable through a nested module namespace. The relationship comes from namespace_exports and preserves the child ownership boundary while exposing the routed address.",
    "certificate-indirectly-depends": "Shows a derived certification dependency between certifiable behavioral sources reached through an interface declaration. It is separate from the authored uses-interface edge and describes certificate closure, not a direct architectural call.",
}


def _field(
    label: str,
    value: object,
    *,
    format: str = "text",
    target: str | None = None,
    copyable: bool = False,
) -> Field | None:
    """Return one nonempty generic inspector field."""
    if value is None or value == "" or value == [] or value == ():
        return None
    result: Field = {"label": label, "value": value, "format": format}
    if target is not None:
        result["target"] = target
    if copyable:
        result["copyable"] = True
    return result


def _section(title: str, fields: list[Field | None]) -> dict[str, Any] | None:
    """Return a section after removing fields with no useful value."""
    present = [field for field in fields if field is not None]
    return {"title": title, "fields": present} if present else None


def _relative_path(root: Path, path: Path) -> str:
    """Render repository-owned paths portably while retaining foreign paths."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _module_chain(graph: RepositoryBlueprintGraph, module_id: str) -> list[str]:
    """Return module ancestry from repository-facing root to the module."""
    chain: list[str] = []
    current: str | None = module_id
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        chain.append(current)
        current = graph.module_parents.get(current)
    return list(reversed(chain))


def _local_source_name(source_id: str) -> str:
    return source_id.split(".source.", 1)[-1]


def _logical_path(
    graph: RepositoryBlueprintGraph,
    logical_id: str,
    *,
    module_id: str,
    source_id: str | None = None,
    interface_name: str | None = None,
) -> str:
    parts = [graph.module_local_segments.get(item, item) for item in _module_chain(graph, module_id)]
    if source_id is not None:
        parts.append(_local_source_name(source_id))
    if interface_name is not None:
        parts.append(interface_name)
    return " > ".join(parts) if parts else logical_id


def _gateway_fields(declaration: Mapping[str, Any]) -> list[Field | None]:
    gateway = declaration.get("gateway")
    if not isinstance(gateway, Mapping):
        return []
    return [
        _field("Gateway language", gateway.get("language")),
        _field("Gateway path", gateway.get("path"), format="path", copyable=True),
    ]


def _dependency_labels(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, Mapping):
            name = item.get("name") or item.get("id") or item.get("kind")
            version = item.get("version")
            if name:
                labels.append(f"{name} ({version})" if version else str(name))
    return labels


def _interface_behavior_fields(interface: InterfaceExport) -> list[Field | None]:
    declaration = interface.declaration
    contract = declaration.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    arguments = contract.get("arguments")
    argument_names = sorted(str(item) for item in arguments) if isinstance(arguments, Mapping) else []
    outputs = contract.get("outputs")
    output_names = [
        str(item.get("id"))
        for item in outputs
        if isinstance(item, Mapping) and item.get("id")
    ] if isinstance(outputs, list) else []
    execution = contract.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    effects = execution.get("effects")
    effect_names = [
        str(item.get("id") or item.get("action"))
        for item in effects
        if isinstance(item, Mapping) and (item.get("id") or item.get("action"))
    ] if isinstance(effects, list) else []
    binding = declaration.get("process_binding")
    binding = binding if isinstance(binding, Mapping) else {}
    return [
        _field("Usage", declaration.get("usage"), format="code", copyable=True),
        _field("Arguments", argument_names, format="list"),
        _field("Outputs", output_names, format="list"),
        _field("Effects", effect_names, format="list"),
        _field("Lifecycle", execution.get("lifecycle")),
        _field("Process binding", binding.get("kind")),
    ]


def _access_fields(interface: InterfaceExport) -> list[Field | None]:
    declaration = interface.export_declaration
    access = declaration.get("access") if isinstance(declaration, Mapping) else None
    if not isinstance(access, Mapping):
        return []
    allow_all = access.get("allow_all_modules") is True
    callers = access.get("allowed_callers")
    caller_ids = [str(item) for item in callers] if isinstance(callers, list) else []
    return [
        _field("Access", "All modules" if allow_all else "Restricted"),
        _field("Allowed callers", caller_ids, format="reference-list"),
    ]


def _node_details(
    graph: RepositoryBlueprintGraph,
    root: Path,
    node_id: str,
) -> Details:
    node = graph.nodes[node_id]
    declaration = node.declaration
    is_module = node_id in graph.module_parents
    module_id = node_id if is_module else graph.source_modules[node_id]
    parent = graph.module_parents.get(module_id) if is_module else module_id
    position = _section(
        "Repository position",
        [
            _field("Logical path", _logical_path(graph, node_id, module_id=module_id, source_id=None if is_module else node_id)),
            _field("Logical ID", node_id, format="code", copyable=True),
            _field("Version", node.version),
            _field("Parent module" if is_module else "Owning module", parent, format="reference", target=parent),
            _field("Blueprint", _relative_path(root, node.blueprint_path), format="path", copyable=True),
        ],
    )
    if is_module:
        behavior_fields = [
            _field("Role", declaration.get("role")),
            _field("Kind", declaration.get("kind")),
            *_gateway_fields(declaration),
            _field("Child modules", list(graph.module_children.get(node_id, ())), format="reference-list"),
            _field("Behavioral sources", list(graph.module_sources.get(node_id, ())), format="reference-list"),
            _field(
                "Exported interfaces",
                sorted(key for key, value in graph.exports.items() if value.module_node_id == node_id),
                format="reference-list",
            ),
            _field("Owned content", declaration.get("content"), format="list"),
        ]
    else:
        behavior_fields = [
            *_gateway_fields(declaration),
            _field(
                "Source interfaces",
                sorted(key for key, value in graph.source_interfaces.items() if value.source_node_id == node_id),
                format="reference-list",
            ),
            _field(
                "Uses interfaces",
                [str(item.get("interface")) for item in declaration.get("uses_interfaces", []) if isinstance(item, Mapping) and item.get("interface")],
                format="reference-list",
            ),
            _field("Runtime dependencies", _dependency_labels(declaration.get("dependencies")), format="list"),
            _field("Owned content", declaration.get("content"), format="list"),
        ]
    sections = [position, _section("Behavior", behavior_fields)]
    return {
        "summary": str(declaration.get("description") or f"Blueprint {node.node_type} logical node."),
        "sections": [section for section in sections if section is not None],
    }


def _interface_details(
    graph: RepositoryBlueprintGraph,
    root: Path,
    interface_id: str,
) -> Details:
    interface = graph.exports.get(interface_id) or graph.source_interfaces[interface_id]
    is_export = interface_id in graph.exports
    source_id = interface.source_node_id
    source = graph.nodes.get(source_id) if source_id else None
    module_id = interface.module_node_id
    position = _section(
        "Repository position",
        [
            _field(
                "Logical path",
                _logical_path(
                    graph,
                    interface_id,
                    module_id=module_id,
                    source_id=None if is_export else source_id,
                    interface_name=interface.local_name,
                ),
            ),
            _field("Logical ID", interface_id, format="code", copyable=True),
            _field("Version", interface.version),
            _field("Owning module", module_id, format="reference", target=module_id),
            _field("Owning source", source_id, format="reference", target=source_id),
            _field("Blueprint", _relative_path(root, source.blueprint_path), format="path", copyable=True) if source else None,
            *(_gateway_fields(source.declaration) if source else []),
        ],
    )
    exposure_fields: list[Field | None]
    if is_export and interface.terminal_interface_id != interface_id:
        exposure_fields = [
            _field("Binding", "Facade"),
            _field("Facade target", interface.terminal_interface_id, format="reference", target=interface.terminal_interface_id),
            _field("Terminal source", interface.source_node_id, format="reference", target=interface.source_node_id),
            *_access_fields(interface),
        ]
    elif is_export:
        exposure_fields = [
            _field("Binding", "Direct"),
            _field("Source interface", interface.source_interface_id, format="reference", target=interface.source_interface_id),
            *_access_fields(interface),
        ]
    else:
        exported_as = sorted(
            export_id
            for export_id, export in graph.exports.items()
            if export.source_interface_id == interface_id
        )
        exposure_fields = [
            _field("Binding", "Source interface"),
            _field("Exported as", exported_as, format="reference-list"),
        ]
    sections = [
        position,
        _section("Behavior", _interface_behavior_fields(interface)),
        _section("Exposure", exposure_fields),
    ]
    return {
        "summary": str(interface.declaration.get("description") or "Blueprint interface contract."),
        "sections": [section for section in sections if section is not None],
    }


def build_blueprint_details(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    entity_id: str,
) -> Details:
    """Return blueprint-specific inspector sections for one canonical entity."""
    if entity_id in graph.nodes:
        return _node_details(graph, repo_root, entity_id)
    return _interface_details(graph, repo_root, entity_id)


def build_out_of_scope_details(outside_root: str, represented_ids: list[str]) -> Details:
    """Describe one compact proxy for repository nodes omitted by scope."""
    return {
        "summary": "Repository logical nodes referenced across the selected visualization scope.",
        "sections": [
            {
                "title": "Repository position",
                "fields": [
                    {"label": "Logical module", "value": outside_root, "format": "code", "copyable": True},
                    {"label": "Represented nodes", "value": represented_ids, "format": "list"},
                    {"label": "Represented count", "value": len(represented_ids), "format": "text"},
                ],
            }
        ],
    }


def _declaring_blueprint(
    graph: RepositoryBlueprintGraph,
    root: Path,
    source_id: str,
    relation: str,
) -> str | None:
    node = graph.nodes.get(source_id)
    if node is not None:
        return _relative_path(root, node.blueprint_path)
    export = graph.exports.get(source_id)
    if export is None:
        return None
    owner_id = (
        export.module_node_id
        if relation == "binds-interface"
        else export.source_node_id
    )
    owner = graph.nodes.get(owner_id) if owner_id else None
    return _relative_path(root, owner.blueprint_path) if owner is not None else None


def _declared_interfaces_for_resolved_edge(
    graph: RepositoryBlueprintGraph,
    source_id: str,
    target_id: str,
    relation: str,
) -> list[str]:
    if relation == "uses-interface":
        return [target_id]
    if relation not in {"uses-export", "uses-private-interface"}:
        return []
    source = graph.nodes.get(source_id)
    if source is None:
        return []
    raw_uses = source.declaration.get("uses_interfaces")
    if not isinstance(raw_uses, list):
        return []
    matches: list[str] = []
    for item in raw_uses:
        if not isinstance(item, Mapping) or not isinstance(item.get("interface"), str):
            continue
        interface_id = str(item["interface"])
        interface = graph.exports.get(interface_id) or graph.source_interfaces.get(interface_id)
        if interface is not None and interface.source_node_id == target_id:
            matches.append(interface_id)
    return sorted(set(matches))


def build_blueprint_edge_annotation(
    graph: RepositoryBlueprintGraph,
    repo_root: Path,
    *,
    source_id: str,
    target_id: str,
    relation: str,
    provenance: str,
    metadata: Mapping[str, Any] | None = None,
    scope_crossing: bool = False,
) -> dict[str, Any]:
    """Build an honest structured annotation for one blueprint relationship."""
    metadata = metadata or {}
    is_certification = relation.startswith("certification:")
    base_relation = relation.removeprefix("certification:")
    label = _EDGE_LABELS.get(base_relation, base_relation.replace("-", " ").title())
    summary = _EDGE_SUMMARIES.get(
        base_relation,
        "Connects two logical nodes according to the canonical repository blueprint graph.",
    )
    binding_kind = metadata.get("binding_kind")
    if base_relation == "binds-interface" and binding_kind == "source":
        summary = "Directly binds a module export to a source interface declared inside that module. The exported address supplies module-level exposure while the source interface owns the underlying contract."
    elif base_relation == "binds-interface" and binding_kind == "facade":
        summary = "Binds a parent-module export to an exported interface of its child module. The facade preserves the child contract while providing a stable address and access policy at the parent boundary."
    if is_certification:
        label = f"Certification: {label}"
        summary = f"Certification dependency mirroring this architectural relationship. {summary}"
    resolution_relation = (
        "uses-export"
        if base_relation == "certificate-indirectly-depends"
        else base_relation
    )
    declared_interfaces = _declared_interfaces_for_resolved_edge(
        graph,
        source_id,
        target_id,
        resolution_relation,
    )
    declaration_field = {
        "uses-interface": "uses_interfaces",
        "depends-on-source": "dependencies",
        "binds-interface": (
            "exports.*.facade_interface"
            if binding_kind == "facade"
            else "exports.*.source_interface"
        ),
        "helper-dependency": "contract.helpers",
        "exposes-child-interface": "namespace_exports",
        "certificate-indirectly-depends": "uses_interfaces and certification closure",
    }.get(base_relation)
    fields = [
        _field("Source", source_id, format="reference", target=source_id),
        _field("Target", target_id, format="reference", target=target_id),
        _field("Declared interface", declared_interfaces, format="reference-list"),
        _field("Required version", metadata.get("required_version") or metadata.get("target_version")),
        _field("Binding kind", binding_kind),
        _field("Certification dependency", metadata.get("certification_dependency")),
        _field("Certification target version", metadata.get("certification_target_version")),
        _field(
            "Declared by",
            _declaring_blueprint(graph, repo_root, source_id, base_relation),
            format="path",
            copyable=True,
        ),
        _field("Declaration field", declaration_field, format="code"),
        _field("Graph provenance", provenance, format="code"),
        _field("Crosses selected scope", scope_crossing),
    ]
    section = _section("Blueprint relationship", fields)
    return {
        "label": label,
        "description": summary,
        "details": {
            "summary": summary,
            "sections": [section] if section is not None else [],
        },
    }


__all__ = [
    "build_blueprint_details",
    "build_blueprint_edge_annotation",
    "build_out_of_scope_details",
]
