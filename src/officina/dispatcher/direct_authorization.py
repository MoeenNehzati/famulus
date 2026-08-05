"""Direct v6 authorization and deterministic process-binding compilation."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import yaml

from officina.common.blueprint_authorization import (
    AuthorizationRelation,
    AuthorizationResult,
    CertificateRequirement,
    CrossedNamespaceGate,
    EffectiveAuthorizationFilter,
    ResolvedCallerReference,
)
from officina.common.process_binding_compiler import (
    ProcessBindingError,
    compile_gateway_invocation,
    compile_route_smoke_invocation,
    parse_caller_invocation,
)
from officina.common.repository_configuration import RepositoryConfiguration
from officina.dispatcher.direct_models import (
    DirectBlueprintNode,
    DirectInterfaceExport,
    InvocationDiagnostic,
    ResolvedInvocationMetadata,
)
from officina.dispatcher.direct_blueprints import (
    DirectBlueprintError,
    DirectBlueprintRepository,
    DirectModule,
    parse_interface_id,
)
from officina.dispatcher.errors import ResolutionFailedError, UnauthorizedCallerError
from officina.runtime.python_machine_interface import (
    PythonProcessTarget,
    PythonProcessTargetError,
    logical_python_package_name,
)


def _ancestry_ids(module_id: str) -> tuple[str, ...]:
    parts = module_id.split(".")
    return tuple(".".join(parts[:index]) for index in range(1, len(parts) + 1))


def _lca(left: tuple[str, ...], right: tuple[str, ...]) -> str | None:
    common = None
    for left_id, right_id in zip(left, right, strict=False):
        if left_id != right_id:
            break
        common = left_id
    return common


def _resolve_relative_module_id(owner_module_id: str, reference: str) -> str:
    level = len(reference) - len(reference.lstrip("."))
    suffix = reference[level:]
    if not suffix:
        raise DirectBlueprintError(
            f"relative caller has no local suffix: {reference}",
            code="dispatcher.invalid_caller_reference",
            target_module_id=owner_module_id,
        )
    owner_parts = owner_module_id.split(".")
    ascents = level - 1
    if ascents >= len(owner_parts):
        raise DirectBlueprintError(
            f"relative caller escapes its registration root: {reference}",
            code="dispatcher.invalid_caller_reference",
            target_module_id=owner_module_id,
        )
    return ".".join([*owner_parts[: len(owner_parts) - ascents], *suffix.split(".")])


def _evaluate_access(
    repository: DirectBlueprintRepository,
    *,
    caller_module_id: str,
    owner_module_id: str,
    interface_id: str,
    kind: str,
    access: object,
) -> tuple[EffectiveAuthorizationFilter, tuple[ResolvedCallerReference, ...]]:
    if not isinstance(access, Mapping):
        raise DirectBlueprintError(
            f"missing access declaration for {kind} {interface_id}",
            code="dispatcher.access_invalid",
            target_module_id=owner_module_id,
        )
    allow_all = access.get("allow_all_modules") is True
    raw_callers = access.get("allowed_callers")
    if not isinstance(raw_callers, list) or any(
        not isinstance(reference, str) for reference in raw_callers
    ):
        raise DirectBlueprintError(
            f"invalid allowed_callers for {kind} {interface_id}",
            code="dispatcher.access_invalid",
            target_module_id=owner_module_id,
        )
    resolved = []
    for reference in raw_callers:
        module_id = (
            _resolve_relative_module_id(owner_module_id, reference)
            if reference.startswith(".")
            else reference
        )
        repository.load_module(module_id)
        resolved.append(ResolvedCallerReference(owner_module_id, reference, module_id))
    resolved_ids = tuple(sorted(item.module_id for item in resolved))
    caller_ancestry = set(_ancestry_ids(caller_module_id))
    caller_is_self = caller_module_id == owner_module_id
    admits = caller_is_self or allow_all or bool(caller_ancestry.intersection(resolved_ids))
    return (
        EffectiveAuthorizationFilter(
            kind=kind,
            owner_module_id=owner_module_id,
            interface_id=interface_id,
            allow_all_modules=allow_all,
            resolved_callers=resolved_ids,
            caller_is_self=caller_is_self,
            admits_caller=admits,
        ),
        tuple(resolved),
    )


def _safe_relative_path(raw_path: object, *, context: str) -> PurePosixPath:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise DirectBlueprintError(context, code="dispatcher.unsafe_blueprint_path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DirectBlueprintError(context, code="dispatcher.unsafe_blueprint_path")
    return path


def _require_regular_without_symlinks(path: Path, *, module_id: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DirectBlueprintError(
                f"relevant source path is unavailable: {current}",
                code="dispatcher.source_not_found",
                target_module_id=module_id,
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DirectBlueprintError(
                f"relevant source path contains a symlink: {current}",
                code="dispatcher.unsafe_blueprint_path",
                target_module_id=module_id,
            )
    if not stat.S_ISREG(path.stat().st_mode):
        raise DirectBlueprintError(
            f"relevant source is not a regular file: {path}",
            code="dispatcher.unsafe_blueprint_path",
            target_module_id=module_id,
        )


def _load_source(
    terminal: DirectModule,
    source_id: str,
    locator: object,
) -> tuple[DirectBlueprintNode, Mapping[str, object]]:
    if not isinstance(locator, Mapping) or not isinstance(locator.get("blueprint"), Mapping):
        raise DirectBlueprintError(
            f"invalid source locator: {source_id}",
            code="dispatcher.source_locator_invalid",
            target_module_id=terminal.module_id,
        )
    blueprint = locator["blueprint"]
    if blueprint.get("base") != "module-root":
        raise DirectBlueprintError(
            f"unsupported source locator base: {source_id}",
            code="dispatcher.source_locator_invalid",
            target_module_id=terminal.module_id,
        )
    relative = _safe_relative_path(
        blueprint.get("path"), context=f"unsafe source blueprint path: {source_id}"
    )
    module_root = terminal.blueprint_path.parent
    path = module_root.joinpath(*relative.parts)
    _require_regular_without_symlinks(path, module_id=terminal.module_id)
    try:
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("source blueprint changed type")
            declaration = yaml.load(stream, Loader=yaml.CSafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise DirectBlueprintError(
            f"malformed source blueprint: {source_id}",
            code="dispatcher.blueprint_malformed",
            target_module_id=terminal.module_id,
        ) from exc
    if (
        not isinstance(declaration, Mapping)
        or declaration.get("schema_version") != 6
        or declaration.get("node_type") != "behavioral_source"
    ):
        raise DirectBlueprintError(
            f"direct dispatch requires a v6 behavioral source: {source_id}",
            code="dispatcher.blueprint_schema_mismatch",
            target_module_id=terminal.module_id,
        )
    if declaration.get("id") != source_id:
        raise DirectBlueprintError(
            f"source blueprint identity mismatch: {source_id}",
            code="dispatcher.blueprint_identity_mismatch",
            target_module_id=terminal.module_id,
        )
    gateway = declaration.get("gateway")
    if not isinstance(gateway, Mapping):
        raise DirectBlueprintError(
            f"source gateway is missing: {source_id}",
            code="dispatcher.blueprint_malformed",
            target_module_id=terminal.module_id,
        )
    gateway_relative = _safe_relative_path(
        gateway.get("path"), context=f"unsafe source gateway path: {source_id}"
    )
    gateway_path = module_root.joinpath(*gateway_relative.parts)
    _require_regular_without_symlinks(gateway_path, module_id=terminal.module_id)
    version = declaration.get("version")
    if type(version) is not int or version < 1:
        raise DirectBlueprintError(
            f"source version is invalid: {source_id}",
            code="dispatcher.blueprint_malformed",
            target_module_id=terminal.module_id,
        )
    return (
        DirectBlueprintNode(
            node_id=source_id,
            node_type="behavioral_source",
            version=version,
            module_root=module_root,
            blueprint_path=path,
            gateway_path=gateway_path,
            declaration=dict(declaration),
        ),
        declaration,
    )


def _certification_diagnostics(
    node_ids: tuple[str, ...],
    status: Mapping[str, object] | None,
) -> tuple[InvocationDiagnostic, ...]:
    if status is None:
        return (
            InvocationDiagnostic(
                "warning",
                "certification-status-unavailable",
                "precomputed certification status is unavailable",
            ),
        )
    diagnostics = []
    for node_id in node_ids:
        value = status.get(node_id, "unavailable")
        state = value if isinstance(value, str) else "malformed"
        if state != "current":
            diagnostics.append(
                InvocationDiagnostic(
                    "warning",
                    f"certification-{state}",
                    f"certification status for {node_id} is {state}",
                    node_id,
                )
            )
    return tuple(diagnostics)


def resolve_direct_invocation(
    *,
    configuration: RepositoryConfiguration,
    caller_module_id: str,
    interface_id: str,
    interface_version: int | None,
    argv: list[str],
    stdin_requested: bool,
    certification_status: Mapping[str, object] | None = None,
    host_caller: bool = False,
) -> ResolvedInvocationMetadata:
    """Authorize and compile one direct route using only relevant blueprints."""

    target_module_id, _local_name = parse_interface_id(interface_id)
    repository = DirectBlueprintRepository(configuration)
    caller_modules = repository.load_ancestry(caller_module_id)
    if host_caller:
        caller = caller_modules[-1]
        discovery = caller.declaration.get("discovery")
        if (
            len(caller_modules) != 1
            or not isinstance(discovery, Mapping)
            or discovery.get("mechanism") != "skill"
        ):
            raise DirectBlueprintError(
                f"host caller must be a discoverable top-level skill: {caller_module_id}",
                code="dispatcher.host_caller_invalid",
                target_module_id=caller_module_id,
            )
    target_modules = repository.load_ancestry(target_module_id)
    caller_ancestry = tuple(module.module_id for module in caller_modules)
    target_ancestry = tuple(module.module_id for module in target_modules)
    terminal = target_modules[-1]
    raw_export = terminal.declaration["exports"].get(interface_id)
    if not isinstance(raw_export, Mapping):
        raise DirectBlueprintError(
            f"interface not found: {interface_id}",
            code="dispatcher.interface_not_found",
            target_module_id=target_module_id,
        )

    source_interface_id = raw_export.get("source_interface")
    if not isinstance(source_interface_id, str) or ".source." not in source_interface_id:
        raise DirectBlueprintError(
            f"invalid source interface for {interface_id}",
            code="dispatcher.source_interface_invalid",
            target_module_id=target_module_id,
        )
    source_id, marker, source_local_name = source_interface_id.rpartition(".interface.")
    if marker != ".interface." or not source_id.startswith(f"{target_module_id}.source."):
        raise DirectBlueprintError(
            f"source interface is not owned by {target_module_id}: {source_interface_id}",
            code="dispatcher.source_interface_invalid",
            target_module_id=target_module_id,
        )
    locator = terminal.declaration["sources"].get(source_id)
    source, source_declaration = _load_source(terminal, source_id, locator)
    interfaces = source_declaration.get("interfaces")
    raw_source_interface = (
        interfaces.get(source_interface_id) if isinstance(interfaces, Mapping) else None
    )
    if not isinstance(raw_source_interface, Mapping):
        raise DirectBlueprintError(
            f"source interface not found: {source_interface_id}",
            code="dispatcher.source_interface_invalid",
            target_module_id=target_module_id,
        )
    available_version = raw_source_interface.get("version")
    if type(available_version) is not int or available_version < 1:
        raise DirectBlueprintError(
            f"source interface version is invalid: {source_interface_id}",
            code="dispatcher.source_interface_invalid",
            target_module_id=target_module_id,
        )
    if interface_version is None:
        interface_version = available_version
    elif (
        type(interface_version) is not int
        or interface_version < 1
        or available_version != interface_version
    ):
        raise DirectBlueprintError(
            f"version mismatch for {interface_id}: requested {interface_version}, available {available_version}",
            code="dispatcher.interface_version_mismatch",
            target_module_id=target_module_id,
        )

    crossed = []
    filters = []
    resolved_callers = []
    immediate_caller = caller_module_id
    for route_owner, child in zip(target_modules, target_modules[1:], strict=False):
        if route_owner.module_id in caller_ancestry:
            continue
        local_segment = child.module_id.rsplit(".", 1)[-1]
        route = route_owner.declaration["namespace_exports"].get(local_segment)
        if not isinstance(route, Mapping):
            raise DirectBlueprintError(
                f"missing namespace export {route_owner.module_id}->{local_segment}",
                code="dispatcher.namespace_route_missing",
                target_module_id=target_module_id,
            )
        route_version = route.get("version")
        child_version = child.declaration.get("version")
        if (
            type(route_version) is not int
            or type(child_version) is not int
            or route_version < 1
            or child_version < 1
            or route_version != child_version
        ):
            raise DirectBlueprintError(
                f"namespace version does not match child {child.module_id}",
                code="dispatcher.namespace_version_mismatch",
                target_module_id=target_module_id,
            )
        surface = route.get("surface")
        only = surface.get("only") if isinstance(surface, Mapping) else None
        surface_version = only.get(interface_id) if isinstance(only, Mapping) else None
        if (
            type(surface_version) is not int
            or surface_version < 1
            or surface_version != interface_version
        ):
            raise DirectBlueprintError(
                f"namespace surface excludes {interface_id}@{interface_version}",
                code="dispatcher.namespace_surface_excludes_interface",
                target_module_id=target_module_id,
            )
        route_filter, route_callers = _evaluate_access(
            repository,
            caller_module_id=immediate_caller,
            owner_module_id=route_owner.module_id,
            interface_id=interface_id,
            kind="namespace-route",
            access=route.get("access"),
        )
        filters.append(route_filter)
        resolved_callers.extend(route_callers)
        if not route_filter.admits_caller:
            raise UnauthorizedCallerError(
                caller_module_id=caller_module_id,
                target_module_id=target_module_id,
                interface_id=interface_id,
                diagnostic=f"caller-filtered:namespace-route:{route_owner.module_id}",
            )
        interface_access = route.get("interface_access")
        if interface_access is not None and not isinstance(interface_access, Mapping):
            raise DirectBlueprintError(
                f"invalid interface_access for namespace route {route_owner.module_id}",
                code="dispatcher.access_invalid",
                target_module_id=target_module_id,
            )
        if isinstance(interface_access, Mapping) and interface_id in interface_access:
            narrow_filter, narrow_callers = _evaluate_access(
                repository,
                caller_module_id=immediate_caller,
                owner_module_id=route_owner.module_id,
                interface_id=interface_id,
                kind="namespace-interface",
                access=interface_access[interface_id],
            )
            filters.append(narrow_filter)
            resolved_callers.extend(narrow_callers)
            if not narrow_filter.admits_caller:
                raise UnauthorizedCallerError(
                    caller_module_id=caller_module_id,
                    target_module_id=target_module_id,
                    interface_id=interface_id,
                    diagnostic=f"caller-filtered:namespace-interface:{route_owner.module_id}",
                )
        crossed.append(
            CrossedNamespaceGate(
                route_owner.module_id,
                child.module_id,
                interface_id,
                interface_version,
                target_module_id,
            )
        )
        immediate_caller = route_owner.module_id

    terminal_filter, terminal_callers = _evaluate_access(
        repository,
        caller_module_id=immediate_caller,
        owner_module_id=target_module_id,
        interface_id=interface_id,
        kind="terminal-export",
        access=raw_export.get("access"),
    )
    filters.append(terminal_filter)
    resolved_callers.extend(terminal_callers)
    if not terminal_filter.admits_caller:
        raise UnauthorizedCallerError(
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
            interface_id=interface_id,
            diagnostic=f"caller-filtered:terminal-export:{interface_id}",
        )

    export = DirectInterfaceExport(
        interface_id=interface_id,
        version=interface_version,
        local_name=source_local_name,
        module_node_id=target_module_id,
        declaration=raw_source_interface,
        source_node_id=source_id,
        source_interface_id=source_interface_id,
        export_declaration=raw_export,
        terminal_interface_id=interface_id,
        terminal_module_node_id=target_module_id,
    )
    try:
        if argv == ["--route-smoke"] and not stdin_requested:
            plan = compile_route_smoke_invocation(source, export)
        else:
            parsed = parse_caller_invocation(
                export,
                argv,
                stdin_requested=stdin_requested,
            )
            plan = compile_gateway_invocation(source, export, parsed)
    except ProcessBindingError as exc:
        raise ResolutionFailedError(
            f"cannot compile {interface_id}: {exc}",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        ) from exc

    relations = tuple(
        AuthorizationRelation(
            "contains-module",
            parent.module_id,
            child.module_id,
            int(child.declaration["version"]),
        )
        for ancestry in (caller_modules, target_modules)
        for parent, child in zip(ancestry, ancestry[1:], strict=False)
    )
    node_versions = {
        module.module_id: int(module.declaration["version"])
        for module in (*caller_modules, *target_modules)
    }
    node_versions[source_id] = source.version
    authorization = AuthorizationResult(
        caller_module_id=caller_module_id,
        caller_source_id=None,
        requested_interface_id=interface_id,
        requested_version=interface_version,
        requested_owner_module_id=target_module_id,
        terminal_interface_id=interface_id,
        terminal_version=interface_version,
        terminal_module_id=target_module_id,
        implementing_source_id=source_id,
        caller_ancestry=caller_ancestry,
        target_ancestry=target_ancestry,
        terminal_ancestry=target_ancestry,
        lca_module_id=_lca(caller_ancestry, target_ancestry),
        crossed_namespace_gates=tuple(crossed),
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
        effective_filters=tuple(filters),
        allowed=True,
        diagnostic="authorized",
        relations=tuple(sorted(set(relations))),
        required_certificates=frozenset(
            CertificateRequirement(node_id, version)
            for node_id, version in node_versions.items()
        ),
    )
    diagnostics = _certification_diagnostics(tuple(node_versions), certification_status)
    gateway_relative = source.gateway_path.relative_to(source.module_root)
    logical_package = logical_python_package_name(target_module_id)
    physical_parts = (
        gateway_relative.parent.parts
        if gateway_relative.name == "__init__.py"
        else (*gateway_relative.parent.parts, gateway_relative.stem)
    )
    suffix = ".".join(part for part in physical_parts if part not in {"", "."})
    logical_entrypoint = logical_package if not suffix else f"{logical_package}.{suffix}"
    try:
        python_target = PythonProcessTarget(
            gateway_relative,
            plan.entry or "",
            logical_package=logical_package,
            logical_entrypoint=logical_entrypoint,
        )
    except PythonProcessTargetError as exc:
        raise ResolutionFailedError(
            f"cannot build Python target for {interface_id}: {exc}",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
        ) from exc
    return ResolvedInvocationMetadata(
        caller_module_id=caller_module_id,
        target_module_id=target_module_id,
        script_interface=source_interface_id,
        target=interface_id,
        pattern=plan.pattern_name or "",
        cwd=terminal.blueprint_path.parent,
        command=list(plan.argv),
        stdin=plan.stdin_argument_id is not None,
        python_target=python_target,
        caller_source_id=None,
        terminal_module_id=target_module_id,
        implementing_source_id=source_id,
        authorization=authorization,
        schema_version=6,
        diagnostics=diagnostics,
    )


__all__ = ["resolve_direct_invocation"]
