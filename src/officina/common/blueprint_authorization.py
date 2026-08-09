"""Pure version 5 interface authorization over a loaded blueprint graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .blueprint_graph import RepositoryBlueprintGraph


_RELATION_NAMES = frozenset(
    {
        "contains-module",
        "routes-child-namespace",
        "routes-terminal-module",
        "facades-child-export",
        "facades-implementing-source",
    }
)


@dataclass(frozen=True)
class AuthorizationRequest:
    caller_module_id: str
    caller_source_id: str | None
    interface_id: str
    version: int


@dataclass(frozen=True, order=True)
class AuthorizationRelation:
    relation: str
    source_node_id: str
    target_node_id: str
    target_version: int


@dataclass(frozen=True, order=True)
class CertificateRequirement:
    node_id: str
    version: int


@dataclass(frozen=True)
class ResolvedCallerReference:
    owner_module_id: str
    reference: str
    module_id: str


@dataclass(frozen=True)
class EffectiveAuthorizationFilter:
    kind: str
    owner_module_id: str
    interface_id: str
    allow_all_modules: bool
    resolved_callers: tuple[str, ...]
    caller_is_self: bool
    admits_caller: bool


@dataclass(frozen=True)
class CrossedNamespaceGate:
    route_owner_id: str
    child_module_id: str
    interface_id: str
    interface_version: int
    terminal_module_id: str


@dataclass(frozen=True)
class AuthorizationResult:
    caller_module_id: str
    caller_source_id: str | None
    requested_interface_id: str
    requested_version: int
    requested_owner_module_id: str | None
    terminal_interface_id: str | None
    terminal_version: int | None
    terminal_module_id: str | None
    implementing_source_id: str | None
    caller_ancestry: tuple[str, ...]
    target_ancestry: tuple[str, ...]
    terminal_ancestry: tuple[str, ...]
    lca_module_id: str | None
    crossed_namespace_gates: tuple[CrossedNamespaceGate, ...]
    resolved_callers: tuple[ResolvedCallerReference, ...]
    effective_filters: tuple[EffectiveAuthorizationFilter, ...]
    allowed: bool
    diagnostic: str
    relations: tuple[AuthorizationRelation, ...]
    required_certificates: frozenset[CertificateRequirement]


@dataclass(frozen=True)
class _ResolvedAccessPolicy:
    allow_all_modules: bool
    caller_ids: frozenset[str]
    resolved_callers: tuple[ResolvedCallerReference, ...]
    relations: tuple[AuthorizationRelation, ...]


class _ResolutionFailure(ValueError):
    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic)


def _module_relation(
    graph: RepositoryBlueprintGraph,
    parent_id: str,
    child_id: str,
) -> AuthorizationRelation:
    return AuthorizationRelation(
        "contains-module",
        parent_id,
        child_id,
        graph.nodes[child_id].version,
    )


def _ancestry_relations(
    graph: RepositoryBlueprintGraph,
    ancestry: tuple[str, ...],
) -> tuple[AuthorizationRelation, ...]:
    return tuple(
        _module_relation(graph, parent_id, child_id)
        for parent_id, child_id in zip(ancestry, ancestry[1:], strict=False)
    )


def _resolve_caller_reference(
    graph: RepositoryBlueprintGraph,
    owner_module_id: str,
    reference: str,
) -> tuple[str, tuple[AuthorizationRelation, ...]]:
    if not reference.startswith("."):
        if reference not in graph.module_parents:
            raise _ResolutionFailure(
                f"unknown-caller-reference:{owner_module_id}:{reference}"
            )
        return reference, ()

    level = len(reference) - len(reference.lstrip("."))
    suffix = reference[level:]
    if not suffix:
        raise _ResolutionFailure(
            f"invalid-relative-caller:{owner_module_id}:{reference}:empty-suffix"
        )
    cursor = owner_module_id
    relations: list[AuthorizationRelation] = []
    for _ in range(level - 1):
        parent = graph.module_parents.get(cursor)
        if parent is None:
            raise _ResolutionFailure(
                f"invalid-relative-caller:{owner_module_id}:{reference}:"
                "above-registration-root"
            )
        relations.append(_module_relation(graph, parent, cursor))
        cursor = parent

    for segment in suffix.split("."):
        matches = tuple(
            child_id
            for child_id in graph.module_children.get(cursor, ())
            if graph.module_local_segments.get(child_id) == segment
        )
        if len(matches) != 1:
            raise _ResolutionFailure(
                f"invalid-relative-caller:{owner_module_id}:{reference}:"
                f"unknown-local-segment:{segment}"
            )
        child_id = matches[0]
        relations.append(_module_relation(graph, cursor, child_id))
        cursor = child_id
    return cursor, tuple(relations)


def _access_mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _ResolutionFailure(f"invalid-access:{context}")
    return value


def _evaluate_access(
    graph: RepositoryBlueprintGraph,
    *,
    caller_module_id: str,
    owner_module_id: str,
    interface_id: str,
    kind: str,
    access: object,
) -> tuple[
    EffectiveAuthorizationFilter,
    tuple[ResolvedCallerReference, ...],
    tuple[AuthorizationRelation, ...],
]:
    policy = _resolve_access_policy(
        graph,
        owner_module_id=owner_module_id,
        interface_id=interface_id,
        kind=kind,
        access=access,
    )
    caller_is_self = caller_module_id == owner_module_id
    caller_ancestry = frozenset(graph.module_ancestry[caller_module_id])
    admits = (
        caller_is_self
        or policy.allow_all_modules
        or bool(caller_ancestry & policy.caller_ids)
    )
    return (
        EffectiveAuthorizationFilter(
            kind=kind,
            owner_module_id=owner_module_id,
            interface_id=interface_id,
            allow_all_modules=policy.allow_all_modules,
            resolved_callers=tuple(sorted(policy.caller_ids)),
            caller_is_self=caller_is_self,
            admits_caller=admits,
        ),
        policy.resolved_callers,
        policy.relations,
    )


def _resolve_access_policy(
    graph: RepositoryBlueprintGraph,
    *,
    owner_module_id: str,
    interface_id: str,
    kind: str,
    access: object,
) -> _ResolvedAccessPolicy:
    declaration = _access_mapping(
        access,
        context=f"{kind}:{owner_module_id}:{interface_id}",
    )
    allow_all = declaration.get("allow_all_modules") is True
    raw_callers = declaration.get("allowed_callers", [])
    if not isinstance(raw_callers, list):
        raise _ResolutionFailure(
            f"invalid-access:{kind}:{owner_module_id}:{interface_id}"
        )

    resolved: list[ResolvedCallerReference] = []
    relations: list[AuthorizationRelation] = []
    for reference in raw_callers:
        if not isinstance(reference, str):
            raise _ResolutionFailure(
                f"invalid-access:{kind}:{owner_module_id}:{interface_id}"
            )
        module_id, proof = _resolve_caller_reference(
            graph,
            owner_module_id,
            reference,
        )
        resolved.append(
            ResolvedCallerReference(
                owner_module_id=owner_module_id,
                reference=reference,
                module_id=module_id,
            )
        )
        relations.extend(proof)

    return _ResolvedAccessPolicy(
        allow_all_modules=allow_all,
        caller_ids=frozenset(item.module_id for item in resolved),
        resolved_callers=tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.owner_module_id,
                    item.reference,
                    item.module_id,
                ),
            )
        ),
        relations=tuple(sorted(set(relations))),
    )


def _lowest_common_ancestor(
    caller_ancestry: tuple[str, ...],
    target_ancestry: tuple[str, ...],
) -> str | None:
    common: str | None = None
    for caller_id, target_id in zip(
        caller_ancestry,
        target_ancestry,
        strict=False,
    ):
        if caller_id != target_id:
            break
        common = caller_id
    return common


def _certificate(
    graph: RepositoryBlueprintGraph,
    node_id: str,
) -> CertificateRequirement:
    return CertificateRequirement(node_id, graph.nodes[node_id].version)


def _result(
    graph: RepositoryBlueprintGraph,
    request: AuthorizationRequest,
    *,
    requested_owner_module_id: str | None = None,
    terminal_interface_id: str | None = None,
    terminal_version: int | None = None,
    terminal_module_id: str | None = None,
    implementing_source_id: str | None = None,
    caller_ancestry: tuple[str, ...] = (),
    target_ancestry: tuple[str, ...] = (),
    terminal_ancestry: tuple[str, ...] = (),
    lca_module_id: str | None = None,
    crossed_namespace_gates: tuple[CrossedNamespaceGate, ...] = (),
    resolved_callers: tuple[ResolvedCallerReference, ...] = (),
    effective_filters: tuple[EffectiveAuthorizationFilter, ...] = (),
    allowed: bool,
    diagnostic: str,
    relations: tuple[AuthorizationRelation, ...] = (),
    required_node_ids: frozenset[str] = frozenset(),
) -> AuthorizationResult:
    normalized_relations = tuple(
        sorted(
            {
                relation
                for relation in relations
                if relation.relation in _RELATION_NAMES
            }
        )
    )
    required = frozenset(
        _certificate(graph, node_id)
        for node_id in required_node_ids
        if node_id in graph.nodes
    )
    return AuthorizationResult(
        caller_module_id=request.caller_module_id,
        caller_source_id=request.caller_source_id,
        requested_interface_id=request.interface_id,
        requested_version=request.version,
        requested_owner_module_id=requested_owner_module_id,
        terminal_interface_id=terminal_interface_id,
        terminal_version=terminal_version,
        terminal_module_id=terminal_module_id,
        implementing_source_id=implementing_source_id,
        caller_ancestry=caller_ancestry,
        target_ancestry=target_ancestry,
        terminal_ancestry=terminal_ancestry,
        lca_module_id=lca_module_id,
        crossed_namespace_gates=crossed_namespace_gates,
        resolved_callers=tuple(
            sorted(
                set(resolved_callers),
                key=lambda item: (
                    item.owner_module_id,
                    item.reference,
                    item.module_id,
                ),
            )
        ),
        effective_filters=effective_filters,
        allowed=allowed,
        diagnostic=diagnostic,
        relations=normalized_relations,
        required_certificates=required,
    )


def resolve_interface_authorization(
    graph: RepositoryBlueprintGraph,
    request: AuthorizationRequest,
) -> AuthorizationResult:
    """Resolve one v5 interface request without filesystem or certificate I/O."""

    if graph.schema_version not in {5, 6}:
        return _result(
            graph,
            request,
            allowed=False,
            diagnostic=f"unsupported-graph-version:{graph.schema_version}",
        )
    if request.caller_module_id not in graph.module_parents:
        return _result(
            graph,
            request,
            allowed=False,
            diagnostic=f"unknown-caller-module:{request.caller_module_id}",
        )

    caller_ancestry = graph.module_ancestry[request.caller_module_id]
    requested = graph.exports.get(request.interface_id)
    if requested is None:
        diagnostic = (
            f"private-interface:{request.interface_id}"
            if request.interface_id in graph.source_interfaces
            else f"unknown-interface:{request.interface_id}"
        )
        return _result(
            graph,
            request,
            caller_ancestry=caller_ancestry,
            allowed=False,
            diagnostic=diagnostic,
        )
    if requested.version != request.version:
        return _result(
            graph,
            request,
            requested_owner_module_id=requested.module_node_id,
            terminal_interface_id=requested.terminal_interface_id,
            terminal_version=requested.version,
            terminal_module_id=requested.terminal_module_node_id,
            implementing_source_id=requested.source_node_id,
            caller_ancestry=caller_ancestry,
            target_ancestry=graph.module_ancestry[requested.module_node_id],
            allowed=False,
            diagnostic=(
                f"version-mismatch:{request.interface_id}:"
                f"requested={request.version}:available={requested.version}"
            ),
        )

    terminal_interface_id = (
        requested.terminal_interface_id or requested.interface_id
    )
    terminal = graph.exports.get(terminal_interface_id)
    if terminal is None or terminal.source_node_id is None:
        return _result(
            graph,
            request,
            requested_owner_module_id=requested.module_node_id,
            caller_ancestry=caller_ancestry,
            target_ancestry=graph.module_ancestry[requested.module_node_id],
            allowed=False,
            diagnostic=f"invalid-terminal-export:{terminal_interface_id}",
        )
    terminal_module_id = (
        terminal.terminal_module_node_id or terminal.module_node_id
    )
    target_ancestry = graph.module_ancestry[requested.module_node_id]
    terminal_ancestry = graph.module_ancestry[terminal_module_id]
    lca_module_id = _lowest_common_ancestor(caller_ancestry, target_ancestry)

    relations: list[AuthorizationRelation] = []
    for ancestry in (caller_ancestry, target_ancestry, terminal_ancestry):
        relations.extend(_ancestry_relations(graph, ancestry))
    required_node_ids: set[str] = {
        terminal_module_id,
        terminal.source_node_id,
    }
    required_node_ids.update(
        relation.source_node_id
        for relation in relations
        if relation.relation == "contains-module"
    )

    required_node_ids.add(request.caller_module_id)

    crossed: list[CrossedNamespaceGate] = []
    resolved_callers: list[ResolvedCallerReference] = []
    effective_filters: list[EffectiveAuthorizationFilter] = []
    immediate_caller_module_id = request.caller_module_id

    def deny(diagnostic: str) -> AuthorizationResult:
        return _result(
            graph,
            request,
            requested_owner_module_id=requested.module_node_id,
            terminal_interface_id=terminal_interface_id,
            terminal_version=terminal.version,
            terminal_module_id=terminal_module_id,
            implementing_source_id=terminal.source_node_id,
            caller_ancestry=caller_ancestry,
            target_ancestry=target_ancestry,
            terminal_ancestry=terminal_ancestry,
            lca_module_id=lca_module_id,
            crossed_namespace_gates=tuple(crossed),
            resolved_callers=tuple(resolved_callers),
            effective_filters=tuple(effective_filters),
            allowed=False,
            diagnostic=diagnostic,
            relations=tuple(relations),
            required_node_ids=frozenset(required_node_ids),
        )

    for route_owner_id, child_module_id in zip(
        target_ancestry,
        target_ancestry[1:],
        strict=False,
    ):
        if route_owner_id in caller_ancestry:
            continue
        route = graph.namespace_routes.get((route_owner_id, child_module_id))
        if route is None:
            return deny(
                f"missing-namespace-route:{route_owner_id}->{child_module_id}"
            )
        materialized = next(
            (
                item
                for item in route.materialized_interfaces
                if item.interface_id == request.interface_id
                and item.version == request.version
            ),
            None,
        )
        if materialized is None:
            return deny(
                f"route-surface-excludes-interface:{route_owner_id}->"
                f"{child_module_id}:{request.interface_id}@{request.version}"
            )
        crossed.append(
            CrossedNamespaceGate(
                route_owner_id=route_owner_id,
                child_module_id=child_module_id,
                interface_id=request.interface_id,
                interface_version=request.version,
                terminal_module_id=materialized.terminal_module_id,
            )
        )
        relations.extend(
            (
                AuthorizationRelation(
                    "routes-child-namespace",
                    route_owner_id,
                    child_module_id,
                    graph.nodes[child_module_id].version,
                ),
                AuthorizationRelation(
                    "routes-terminal-module",
                    route_owner_id,
                    materialized.terminal_module_id,
                    graph.nodes[materialized.terminal_module_id].version,
                ),
            )
        )
        required_node_ids.add(route_owner_id)

        try:
            route_filter, route_callers, route_relations = _evaluate_access(
                graph,
                caller_module_id=immediate_caller_module_id,
                owner_module_id=route_owner_id,
                interface_id=request.interface_id,
                kind="namespace-route",
                access=route.declaration.get("access"),
            )
        except _ResolutionFailure as exc:
            return deny(exc.diagnostic)
        effective_filters.append(route_filter)
        resolved_callers.extend(route_callers)
        relations.extend(route_relations)
        required_node_ids.update(
            relation.source_node_id
            for relation in route_relations
            if relation.relation == "contains-module"
        )
        if not route_filter.admits_caller:
            return deny(
                f"caller-filtered:namespace-route:{route_owner_id}->"
                f"{child_module_id}"
            )

        raw_interface_access = route.declaration.get("interface_access", {})
        if isinstance(raw_interface_access, Mapping):
            interface_access = raw_interface_access.get(request.interface_id)
        else:
            interface_access = None
        if interface_access is not None:
            try:
                interface_filter, callers, proof = _evaluate_access(
                    graph,
                    caller_module_id=immediate_caller_module_id,
                    owner_module_id=route_owner_id,
                    interface_id=request.interface_id,
                    kind="namespace-interface",
                    access=interface_access,
                )
            except _ResolutionFailure as exc:
                return deny(exc.diagnostic)
            effective_filters.append(interface_filter)
            resolved_callers.extend(callers)
            relations.extend(proof)
            required_node_ids.update(
                relation.source_node_id
                for relation in proof
                if relation.relation == "contains-module"
            )
            if not interface_filter.admits_caller:
                return deny(
                    f"caller-filtered:namespace-interface:{route_owner_id}:"
                    f"{request.interface_id}"
                )
        immediate_caller_module_id = route_owner_id

    is_facade = terminal_interface_id != requested.interface_id
    if is_facade:
        relations.extend(
            (
                AuthorizationRelation(
                    "facades-child-export",
                    requested.module_node_id,
                    terminal_module_id,
                    graph.nodes[terminal_module_id].version,
                ),
                AuthorizationRelation(
                    "facades-implementing-source",
                    requested.module_node_id,
                    terminal.source_node_id,
                    graph.nodes[terminal.source_node_id].version,
                ),
            )
        )
        required_node_ids.add(requested.module_node_id)
        try:
            facade_filter, callers, proof = _evaluate_access(
                graph,
                caller_module_id=immediate_caller_module_id,
                owner_module_id=requested.module_node_id,
                interface_id=request.interface_id,
                kind="facade-export",
                access=(
                    requested.export_declaration.get("access")
                    if isinstance(requested.export_declaration, Mapping)
                    else None
                ),
            )
        except _ResolutionFailure as exc:
            return deny(exc.diagnostic)
        effective_filters.append(facade_filter)
        resolved_callers.extend(callers)
        relations.extend(proof)
        required_node_ids.update(
            relation.source_node_id
            for relation in proof
            if relation.relation == "contains-module"
        )
        if not facade_filter.admits_caller:
            return deny(
                f"caller-filtered:facade-export:{request.interface_id}"
            )
        immediate_caller_module_id = requested.module_node_id

    try:
        terminal_filter, callers, proof = _evaluate_access(
            graph,
            caller_module_id=immediate_caller_module_id,
            owner_module_id=terminal_module_id,
            interface_id=terminal_interface_id,
            kind="terminal-export",
            access=(
                terminal.export_declaration.get("access")
                if isinstance(terminal.export_declaration, Mapping)
                else None
            ),
        )
    except _ResolutionFailure as exc:
        return deny(exc.diagnostic)
    effective_filters.append(terminal_filter)
    resolved_callers.extend(callers)
    relations.extend(proof)
    required_node_ids.update(
        relation.source_node_id
        for relation in proof
        if relation.relation == "contains-module"
    )
    if not terminal_filter.admits_caller:
        return deny(
            f"caller-filtered:terminal-export:{terminal_interface_id}"
        )

    return _result(
        graph,
        request,
        requested_owner_module_id=requested.module_node_id,
        terminal_interface_id=terminal_interface_id,
        terminal_version=terminal.version,
        terminal_module_id=terminal_module_id,
        implementing_source_id=terminal.source_node_id,
        caller_ancestry=caller_ancestry,
        target_ancestry=target_ancestry,
        terminal_ancestry=terminal_ancestry,
        lca_module_id=lca_module_id,
        crossed_namespace_gates=tuple(crossed),
        resolved_callers=tuple(resolved_callers),
        effective_filters=tuple(effective_filters),
        allowed=True,
        diagnostic="authorized",
        relations=tuple(relations),
        required_node_ids=frozenset(required_node_ids),
    )


def _validate_authorization_declarations(
    graph: RepositoryBlueprintGraph,
) -> None:
    """Resolve every authored caller reference once during graph validation."""

    if graph.schema_version not in {5, 6}:
        return
    declarations: list[tuple[str, str, str, object]] = []
    for interface_id, export in graph.exports.items():
        access = (
            export.export_declaration.get("access")
            if isinstance(export.export_declaration, Mapping)
            else None
        )
        declarations.append(
            (
                "export",
                export.module_node_id,
                interface_id,
                access,
            )
        )
    for route in graph.namespace_routes.values():
        declarations.append(
            (
                "namespace-route",
                route.route_owner_id,
                route.child_module_id,
                route.declaration.get("access"),
            )
        )
        interface_access = route.declaration.get("interface_access", {})
        if isinstance(interface_access, Mapping):
            for interface_id, access in interface_access.items():
                if isinstance(interface_id, str):
                    declarations.append(
                        (
                            "namespace-interface",
                            route.route_owner_id,
                            interface_id,
                            access,
                        )
                    )

    for kind, owner_id, subject_id, access in declarations:
        _resolve_access_policy(
            graph,
            owner_module_id=owner_id,
            interface_id=subject_id,
            kind=kind,
            access=access,
        )

    for export in graph.exports.values():
        terminal_interface_id = (
            export.terminal_interface_id or export.interface_id
        )
        if terminal_interface_id == export.interface_id:
            continue
        terminal = graph.exports[terminal_interface_id]
        result = resolve_interface_authorization(
            graph,
            AuthorizationRequest(
                caller_module_id=export.module_node_id,
                caller_source_id=None,
                interface_id=terminal_interface_id,
                version=terminal.version,
            ),
        )
        if not result.allowed:
            raise _ResolutionFailure(
                f"facade owner {export.module_node_id} cannot call "
                f"{terminal_interface_id}: {result.diagnostic}"
            )

    for route in graph.namespace_routes.values():
        for routed in route.materialized_interfaces:
            result = resolve_interface_authorization(
                graph,
                AuthorizationRequest(
                    caller_module_id=route.route_owner_id,
                    caller_source_id=None,
                    interface_id=routed.interface_id,
                    version=routed.version,
                ),
            )
            if not result.allowed:
                raise _ResolutionFailure(
                    f"namespace route owner {route.route_owner_id} cannot call "
                    f"{routed.interface_id}: {result.diagnostic}"
                )


__all__ = [
    "AuthorizationRequest",
    "AuthorizationResult",
    "resolve_interface_authorization",
]
