#!/usr/bin/env python3
"""Payload constructors for docstring-derived graph documents."""

from __future__ import annotations

from typing import Any, Iterable

from ...docstring import FunctionSpec, PipelineSpec
from ...docstring import parse_pseudocode_dependency_ref

Entity = dict[str, Any]


DOCSTRING_CATEGORY_STYLES = {
    "class": {
        "shape": "double-rect",
        "color": "#0b3d91",
        "label": "Class",
        "description": "Container object definitions.",
    },
    "method": {
        "shape": "roundrect",
        "color": "#1565c0",
        "label": "Method",
        "description": "Methods that belong to a class.",
    },
    "function": {
        "shape": "roundrect",
        "color": "#2471a3",
        "label": "Function",
        "description": "Module-level callable objects.",
    },
    "repo-call-target": {
        "shape": "ellipse",
        "color": "#d97706",
        "label": "Repo call target",
        "description": "Repo callable referenced from this module but defined elsewhere.",
    },
    "repo-product-target": {
        "shape": "parallelogram",
        "color": "#8e44ad",
        "label": "Repo product target",
        "description": "Value, error, copy, result, or container produced by a referenced dependency.",
    },
    "dispatch-interface": {
        "shape": "hexagon",
        "color": "#be123c",
        "label": "Dispatch interface",
        "description": "Runtime dispatch interface dependency.",
    },
}

DOCSTRING_EDGE_STYLES = {
    "call": {
        "stroke": "#2563eb",
    },
    "instantiation": {
        "stroke": "#c2410c",
        "dash": "9 5",
    },
    "wraps": {
        "stroke": "#0f766e",
        "dash": "2 4",
    },
    "dispatch": {
        "stroke": "#be123c",
        "dash": "12 4 2 4",
    },
    "pipeline-call": {
        "stroke": "#ea580c",
        "dash": "3 3",
    },
    "documented-call": {
        "stroke": "#2563eb",
    },
    "noninferable": {
        "stroke": "#92400e",
        "dash": "6 6",
    },
    "instantiate": {
        "stroke": "#c2410c",
        "dash": "9 5",
    },
    "reference": {
        "stroke": "#0f172a",
        "dash": "2 2",
    },
    "pipeline-phase": {
        "stroke": "#374151",
        "dash": "4 4",
    },
    "phase-member": {
        "stroke": "#4b5563",
        "dash": "3 3",
    },
    "inferred": {
        "stroke": "#64748b",
        "dash": "3 3",
    },
}


def _normalize_signature(explicit_signature: str | None, fallback: str) -> str:
    """Return a non-empty signature value for schema-compliant nodes."""
    if explicit_signature:
        return explicit_signature
    return fallback


def _build_entity(
    *,
    entity_id: str,
    entity_type: str,
    short_title: str,
    position: int,
    category: str | None = None,
    title: str | None = None,
    signature: str = "",
    source: str = "explicit",
    container: str | None = None,
    description: str | None = None,
    summary: str | None = None,
    role_bucket: str | None = None,
    children: list[str] | None = None,
) -> Entity:
    """Build one visual entity with a schema-compatible core."""
    entity: Entity = {
        "id": entity_id,
        "ref": entity_id,
        "type": entity_type,
        "category": category or entity_type,
        "short_title": short_title,
        "position": position,
        "signature": signature,
        "source": source,
    }
    if title:
        entity["title"] = title
    if container:
        entity["container"] = container
    if children:
        entity["children"] = children
    if description:
        entity["description"] = description
    if summary:
        entity["summary"] = summary
    if role_bucket:
        entity["role_bucket"] = role_bucket
    return entity


def _edge(
    to_node: str,
    edge_type: str,
    *,
    why: str = "",
    implicit: bool = False,
    source: str = "explicit",
    label: str | None = None,
    edge_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a typed, schema-compatible edge payload."""
    payload = {
        "to": to_node,
        "type": edge_type,
        "implicit": implicit,
        "source": source,
    }
    if label:
        payload["label"] = label
    if edge_label:
        payload["edge_label"] = edge_label
    if why:
        payload["description"] = why
    if metadata:
        payload["metadata"] = metadata
    return payload


def _add_edge(
    edges: list[dict[str, Any]],
    to_node: str,
    edge_type: str,
    *,
    why: str = "",
    implicit: bool = False,
    source: str = "explicit",
    label: str | None = None,
    edge_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Add an edge unless the same target and semantic type already exists."""
    if any(
        existing.get("to") == to_node and existing.get("type") == edge_type
        for existing in edges
    ):
        return
    edges.append(
        _edge(
            to_node,
            edge_type,
            why=why,
            implicit=implicit,
            source=source,
            label=label,
            edge_label=edge_label,
            metadata=metadata,
        )
    )


def _ensure_reference_node(
    ext_id: str,
    *,
    entities_by_id: dict[str, Entity],
    position: int,
    ext_kind: str,
    category: str | None = None,
    why: str | None = None,
) -> None:
    """Create one referenced entity node if absent."""
    if ext_id in entities_by_id:
        return
    entities_by_id[ext_id] = _build_entity(
        entity_id=ext_id,
        entity_type=ext_kind,
        category=category or ext_kind,
        short_title=ext_id,
        title=f"{ext_kind}: {ext_id}",
        position=position,
        description=(why or f"Referenced dependency: {ext_id}"),
        signature=_normalize_signature(None, f"{ext_id}()"),
        source="inferred",
    )


def _normalize_ref(raw_ref: str) -> tuple[str | None, str]:
    """Normalize a dependency marker into section/name parts."""
    parsed = parse_pseudocode_dependency_ref(raw_ref)
    if parsed is not None:
        section, name = parsed
        return section, name
    if ":" in raw_ref:
        section, name = [part.strip() for part in raw_ref.split(":", 1)]
        if section in {"CallsFromRepo", "InstantiationsFromRepo", "Dispatches"} and name:
            return section, name
    return None, raw_ref


def _resolve_declared_dependency(
    name: str,
    available: set[str],
) -> str | None:
    """Resolve name to callable ID when possible."""
    if name in available:
        return name
    suffix = name.rsplit(".", 1)[-1]
    matches = [callable_name for callable_name in available if callable_name.endswith(f".{suffix}")]
    if suffix in available:
        return suffix
    if len(matches) == 1:
        return matches[0]
    return None


def _dependency_ref_variants(name: str) -> set[str]:
    """Return accepted exact and suffix spellings for one declared dependency."""
    raw = (name or "").strip()
    if not raw:
        return set()
    variants = {raw}
    if raw.startswith("."):
        tail = raw.lstrip(".")
        variants.add(tail)
        variants.add(tail.rsplit(".", 1)[-1])
    if "." in raw:
        variants.add(raw.rsplit(".", 1)[-1])
    if raw.startswith("skills."):
        variants.add(raw[len("skills.") :])
    else:
        variants.add(f"skills.{raw}")
    return {variant for variant in variants if variant}


def _declared_dependency_index(
    *,
    spec: FunctionSpec,
    defined_by_name: set[str],
    entities_by_id: dict[str, Entity],
) -> dict[str, dict[str, str]]:
    """Index declared dependency refs by section and accepted pseudocode spellings."""
    index: dict[str, dict[str, str]] = {
        "CallsFromRepo": {},
        "InstantiationsFromRepo": {},
        "Dispatches": {},
    }

    def _add(section: str, declared: str, target: str) -> None:
        for variant in _dependency_ref_variants(declared) | _dependency_ref_variants(target):
            index[section].setdefault(variant, target)

    for dependency in spec.module_calls:
        resolved = _resolve_declared_dependency(dependency.name, defined_by_name) or dependency.name
        target = resolved if resolved in entities_by_id else dependency.name
        _add("CallsFromRepo", dependency.name, target)
    for dependency in spec.module_instantiates:
        resolved = _resolve_declared_dependency(dependency.name, defined_by_name) or dependency.name
        target = resolved if resolved in entities_by_id else dependency.name
        _add("InstantiationsFromRepo", dependency.name, target)
    for dependency in spec.dispatches:
        _add("Dispatches", dependency.id, dependency.id)
    return index


def _resolve_pseudocode_ref(
    *,
    section: str | None,
    name: str,
    dependency_index: dict[str, dict[str, str]],
) -> str:
    """Resolve one pseudocode dependency ref through declared dependency sections."""
    if not section:
        raise ValueError(f"Unscoped pseudocode dependency reference is not graphable: {name}")
    candidates = dependency_index.get(section, {})
    if name in candidates:
        return candidates[name]
    suffix_matches = {
        target
        for declared, target in candidates.items()
        if declared.rsplit(".", 1)[-1] == name
    }
    if len(suffix_matches) == 1:
        return next(iter(suffix_matches))
    if len(suffix_matches) > 1:
        raise ValueError(f"Ambiguous pseudocode dependency reference {section}:{name}")
    raise ValueError(f"Pseudocode dependency reference {section}:{name} is not declared")


def _build_graph_payload(
    *,
    module_path,
    pipeline: PipelineSpec,
    function_specs: dict[str, FunctionSpec],
    inferred_edges: dict[str, set[str]] | None = None,
    class_nodes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Assemble a render-ready graph payload from parsed doc metadata."""
    defined_by_name = set(function_specs.keys())
    class_names = set(class_nodes or ())
    class_names.update(
        callable_name.split(".", 1)[0]
        for callable_name in defined_by_name
        if "." in callable_name
    )

    entities_by_id: dict[str, Entity] = {}
    class_children: dict[str, list[str]] = {name: [] for name in class_names}

    for idx, class_name in enumerate(sorted(class_names), start=1):
        entities_by_id[class_name] = _build_entity(
            entity_id=class_name,
            entity_type="class",
            category="class",
            short_title=class_name,
            title=f"Class {class_name}",
            position=idx,
        )

    next_position = len(entities_by_id) + 1

    for callable_name in sorted(defined_by_name):
        spec = function_specs[callable_name]
        node_id = callable_name
        is_method = "." in callable_name
        is_class = (not is_method) and (callable_name in class_names)
        if is_class:
            short_title = callable_name
            entity = entities_by_id.setdefault(
                node_id,
                _build_entity(
                    entity_id=node_id,
                    entity_type="class",
                    category="class",
                    short_title=callable_name,
                    title=f"Class {callable_name}",
                    signature=f"{callable_name}()",
                    position=next_position,
                ),
            )
            entity["type"] = "class"
            entity["category"] = "class"
            entity["signature"] = spec.signature or f"{callable_name}()"
            if spec.summary:
                entity["title"] = spec.summary
                entity["description"] = spec.summary
            if spec.rationale:
                entity["summary"] = spec.rationale
            if spec.phase:
                entity["role_bucket"] = spec.phase
            continue

        short_title = callable_name.rsplit(".", 1)[-1]
        container = callable_name.split(".", 1)[0] if is_method else None
        if container and container in class_names:
            class_children[container].append(node_id)

        entities_by_id[node_id] = _build_entity(
            entity_id=node_id,
            entity_type="method" if is_method else "function",
            category="method" if is_method else "function",
            short_title=short_title,
            title=spec.summary or f"{callable_name}",
            signature=_normalize_signature(spec.signature, f"{callable_name}()"),
            description=spec.summary,
            summary=spec.rationale,
            role_bucket=spec.phase,
            position=next_position,
            container=container,
        )
        next_position += 1

    for phase_name in getattr(pipeline, "phases", []):
        if phase_name in entities_by_id:
            continue
        _ensure_reference_node(
            phase_name,
            entities_by_id=entities_by_id,
            position=next_position,
            ext_kind="phase",
            category="phase",
            why="Phase declared in GraphPipeline",
        )
        next_position += 1

    for source, target in getattr(pipeline, "noninferable_calls", []):
        if source not in entities_by_id:
            continue
        resolved_target = _resolve_declared_dependency(target, defined_by_name) or target
        if resolved_target not in entities_by_id:
            _ensure_reference_node(
                resolved_target,
                entities_by_id=entities_by_id,
                position=next_position,
                ext_kind="repo-call-target",
                category="repo-call-target",
            )
            next_position += 1
        entities_by_id[source]["connects_to"] = entities_by_id[source].get("connects_to", [])
        _add_edge(
            entities_by_id[source]["connects_to"],
            resolved_target,
            "pipeline-call",
            why="pipeline noninferable call",
        )

    for source_name, spec in function_specs.items():
        entity = entities_by_id.setdefault(
            source_name,
            _build_entity(
                entity_id=source_name,
                entity_type="function",
                category="function",
                short_title=source_name,
                title=source_name,
                position=next_position,
            ),
        )
        next_position = max(next_position, entity["position"] + 1)
        entity.setdefault("connects_to", [])

        def _resolved_dependency_node(target_name: str) -> str:
            resolved = _resolve_declared_dependency(target_name, defined_by_name) or target_name
            return resolved if resolved in entities_by_id else target_name

        wrapped_targets = {
            _resolved_dependency_node(wraps.target)
            for wraps in spec.wraps
            if wraps.is_wrapper and wraps.target
        }
        product_targets = {
            _resolved_dependency_node(call.name)
            for call in spec.module_instantiates
        }

        for src, dst in spec.noninferable_calls:
            if src == source_name:
                target = _resolve_declared_dependency(dst, defined_by_name) or dst
                if target not in entities_by_id:
                    _ensure_reference_node(
                        target,
                        entities_by_id=entities_by_id,
                        position=next_position,
                        ext_kind="repo-call-target",
                        category="repo-call-target",
                        why="Declared noninferable dependency",
                    )
                    next_position += 1
                _add_edge(
                    entity["connects_to"],
                    target,
                    "noninferable",
                    why="Explicit noninferable dependency",
                )

        for call in spec.module_calls:
            target_name = call.name
            dependency_node = _resolved_dependency_node(target_name)
            if dependency_node in wrapped_targets or dependency_node in product_targets:
                continue
            if dependency_node not in entities_by_id:
                _ensure_reference_node(
                    dependency_node,
                    entities_by_id=entities_by_id,
                    position=next_position,
                    ext_kind="repo-call-target",
                    category="repo-call-target",
                    why=call.why,
                )
                next_position += 1
            _add_edge(
                entity["connects_to"],
                dependency_node,
                "call",
                why=call.why,
                implicit=call.implicit,
                label="calls",
                metadata={
                    "declaration_section": "CallsFromRepo",
                    "declared_ref": target_name,
                    "resolved_ref": dependency_node,
                    "why": call.why,
                    "why_action": call.why_action,
                    "legacy_why_string": call.why_legacy_string,
                },
            )

        for call in spec.module_instantiates:
            target_name = call.name
            dependency_node = _resolved_dependency_node(target_name)
            if dependency_node in wrapped_targets:
                continue
            if dependency_node not in entities_by_id:
                _ensure_reference_node(
                    dependency_node,
                    entities_by_id=entities_by_id,
                    position=next_position,
                    ext_kind="repo-product-target",
                    category="repo-product-target",
                    why=call.why,
                )
                next_position += 1
            _add_edge(
                entity["connects_to"],
                dependency_node,
                "instantiation",
                why=call.why,
                implicit=call.implicit,
                label="produces",
                metadata={
                    "declaration_section": "InstantiationsFromRepo",
                    "declared_ref": target_name,
                    "resolved_ref": dependency_node,
                    "why": call.why,
                    "why_action": call.why_action,
                    "legacy_why_string": call.why_legacy_string,
                },
            )

        for dispatch in spec.dispatches:
            dispatch_id = dispatch.id
            if dispatch_id not in entities_by_id:
                _ensure_reference_node(
                    dispatch_id,
                    entities_by_id=entities_by_id,
                    position=next_position,
                    ext_kind="dispatch-interface",
                    category="dispatch-interface",
                    why=dispatch.why,
                )
                next_position += 1
            _add_edge(
                entity["connects_to"],
                dispatch_id,
                "dispatch",
                why=dispatch.why,
                label="dispatches",
                metadata={
                    "declaration_section": "Dispatches",
                    "declared_ref": dispatch_id,
                    "resolved_ref": dispatch_id,
                    "why": dispatch.why,
                    "why_action": dispatch.why_action,
                    "legacy_why_string": dispatch.why_legacy_string,
                },
            )

        for wraps in spec.wraps:
            if not wraps.is_wrapper or not wraps.target:
                continue
            target = _resolved_dependency_node(wraps.target)
            if target not in entities_by_id:
                _ensure_reference_node(
                    target,
                    entities_by_id=entities_by_id,
                    position=next_position,
                    ext_kind="repo-call-target",
                    category="repo-call-target",
                    why="Wrapper target",
                )
                next_position += 1
            wrap_description = " ".join(
                piece for piece in (wraps.preprocess, wraps.postprocess, wraps.fixed_arguments) if piece
            ).strip() or "Wrapper relationship declared in docstring"
            _add_edge(
                entity["connects_to"],
                target,
                "wraps",
                why=wrap_description,
                label="wraps",
                metadata={
                    "declaration_section": "Wraps",
                    "declared_ref": wraps.target,
                    "resolved_ref": target,
                    "preprocess": wraps.preprocess,
                    "postprocess": wraps.postprocess,
                    "fixed_arguments": wraps.fixed_arguments,
                },
            )

        dependency_index = _declared_dependency_index(
            spec=spec,
            defined_by_name=defined_by_name,
            entities_by_id=entities_by_id,
        )
        for ref in spec.pseudocode_dependency_refs:
            section, name = _normalize_ref(ref)
            _resolve_pseudocode_ref(
                section=section,
                name=name,
                dependency_index=dependency_index,
            )

    if inferred_edges:
        for source_name, targets in inferred_edges.items():
            entity = entities_by_id.get(source_name)
            if entity is None:
                continue
            entity.setdefault("connects_to", [])
            for target_name in sorted(targets):
                target = _resolve_declared_dependency(target_name, defined_by_name) or target_name
                if target not in entities_by_id:
                    _ensure_reference_node(
                        target,
                        entities_by_id=entities_by_id,
                        position=next_position,
                        ext_kind="inferred-call",
                        category="inferred-call",
                    )
                    next_position += 1
                _add_edge(
                    entity["connects_to"],
                    target,
                    "inferred",
                    source="inferred",
                    why="Inferred from AST call usage",
                )

    for class_id, children in class_children.items():
        if class_id not in entities_by_id:
            continue
        if children:
            entities_by_id[class_id]["children"] = sorted(set(children))

    for phase_name, members in getattr(pipeline, "phase_members", {}).items():
        for member in members:
            source = phase_name
            target = _resolve_declared_dependency(member, defined_by_name) or member
            if source not in entities_by_id or target not in entities_by_id:
                continue
            _add_edge(
                entities_by_id[source]["connects_to"],
                target,
                "phase-member",
                why="Declared GraphPipeline membership",
            )

    for source, target in getattr(pipeline, "phase_edges", []):
        if source not in entities_by_id or target not in entities_by_id:
            continue
        _add_edge(
            entities_by_id[source]["connects_to"],
            target,
            "pipeline-phase",
            why="Declared phase edge",
        )

    def _entity_sort_key(item: tuple[str, Entity]) -> tuple[int, str]:
        entity = item[1]
        return (int(entity.get("position", 10_000)), str(entity.get("id", "")))

    ordered_entities = [
        payload
        for _, payload in sorted(entities_by_id.items(), key=_entity_sort_key)
    ]

    for idx, entity in enumerate(ordered_entities):
        entity["position"] = idx

    categories = []
    used_categories = {
        str(entity.get("category") or entity.get("type", ""))
        for entity in ordered_entities
        if str(entity.get("category") or entity.get("type", ""))
    }
    for category in sorted(used_categories):
        defaults = DOCSTRING_CATEGORY_STYLES.get(category, {})
        category_payload: dict[str, Any] = {
            "id": category,
            "label": defaults.get("label", category.replace("-", " ").replace("_", " ").title()),
            "description": defaults.get(
                "description", f"Category for {category.replace('-', ' ').replace('_', ' ')} nodes."
            ),
        }
        if "shape" in defaults:
            category_payload["shape"] = defaults["shape"]
        if "color" in defaults:
            category_payload["color"] = defaults["color"]
        categories.append(category_payload)

    return {
        "schema_version": 1,
        "graph_kind": "docstring_dependency",
        "graph_id": str(module_path),
        "document": {
            "title": module_path.stem,
            "source_file": str(module_path),
            "generated_by": "officina.common.visualization.from_docstring.json_extractor",
            "render_profile": "flowchart",
        },
        "categories": categories,
        "render_modes": ["flowchart"],
        "default_mode": "flowchart",
        "ui": {
            "layout": {"rankdir": "TB"},
            "edge_styles": DOCSTRING_EDGE_STYLES,
        },
        "entities": ordered_entities,
    }


def to_dependency_json(
    module_path,
    pipeline: PipelineSpec | None = None,
    function_specs: dict[str, FunctionSpec] | None = None,
    inferred_edges: dict[str, set[str]] | None = None,
    infer_local_edges: bool = False,
    *,
    class_nodes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build graph payload from module, pipeline, and callable metadata."""
    from pathlib import Path as _Path
    from .parser import parse_docstring_module
    from .parser import parse_module
    from .parser import parse_function_graphs as _parse_function_graphs
    from .parser import infer_call_edges

    module_path = _Path(module_path)
    if not module_path.exists():
        raise FileNotFoundError(f"module not found: {module_path}")

    if pipeline is None or function_specs is None:
        parsed_pipeline, parsed_function_specs = parse_docstring_module(
            module_path,
            include_undocumented=True,
        )
        if pipeline is None:
            pipeline = parsed_pipeline
        if function_specs is None:
            function_specs = parsed_function_specs

    pipeline = pipeline if pipeline is not None else PipelineSpec()
    if function_specs is None:
        function_specs = _parse_function_graphs(parse_module(module_path))

    if infer_local_edges and inferred_edges is None:
        inferred_edges = infer_call_edges(module_path, function_specs=function_specs)

    return _build_graph_payload(
        module_path=module_path,
        pipeline=pipeline,
        function_specs=function_specs,
        inferred_edges=inferred_edges,
        class_nodes=class_nodes,
    )


__all__ = [
    "DOCSTRING_CATEGORY_STYLES",
    "DOCSTRING_EDGE_STYLES",
    "_build_graph_payload",
    "to_dependency_json",
]
