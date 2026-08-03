"""Resolve repository-blueprint visualization scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ...blueprint_graph import RepositoryBlueprintGraph


@dataclass(frozen=True)
class BlueprintScope:
    requested: tuple[str, ...]
    modules: frozenset[str]
    entity_ids: frozenset[str]


def module_descendants(graph: RepositoryBlueprintGraph, roots: Iterable[str]) -> set[str]:
    result: set[str] = set()
    stack = list(roots)
    while stack:
        module_id = stack.pop()
        if module_id in result:
            continue
        result.add(module_id)
        stack.extend(graph.module_children.get(module_id, ()))
    return result


def resolve_blueprint_scope(
    graph: RepositoryBlueprintGraph,
    skills: Iterable[str] | None,
) -> BlueprintScope:
    requested = tuple(dict.fromkeys(str(item).strip() for item in (skills or ()) if str(item).strip()))
    unknown = [item for item in requested if item not in graph.module_parents]
    if unknown:
        raise ValueError(f"Unknown blueprint skill/module id(s): {', '.join(unknown)}")
    modules = module_descendants(graph, requested) if requested else set(graph.module_parents)
    entity_ids = set(modules)
    entity_ids.update(source for source, module in graph.source_modules.items() if module in modules)
    entity_ids.update(
        interface_id
        for interface_id, interface in {**graph.source_interfaces, **graph.exports}.items()
        if interface.module_node_id in modules
    )
    return BlueprintScope(requested, frozenset(modules), frozenset(entity_ids))


def owning_module(graph: RepositoryBlueprintGraph, logical_id: str) -> str | None:
    if logical_id in graph.module_parents:
        return logical_id
    module_id = graph.source_modules.get(logical_id)
    if module_id is not None:
        return module_id
    interface = graph.exports.get(logical_id) or graph.source_interfaces.get(logical_id)
    return interface.module_node_id if interface is not None else None


def top_module(graph: RepositoryBlueprintGraph, module_id: str) -> str:
    current = module_id
    seen: set[str] = set()
    while graph.module_parents.get(current) is not None and current not in seen:
        seen.add(current)
        current = str(graph.module_parents[current])
    return current


__all__ = ["BlueprintScope", "owning_module", "resolve_blueprint_scope", "top_module"]
