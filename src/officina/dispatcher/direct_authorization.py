"""Authorize and compile one route from only its relevant v6 blueprints.

The functions here implement hop-local namespace authorization. They load the
caller and target ancestry, evaluate only crossed target-side gates, enforce
the terminal export as an authority ceiling, load one implementing source, and
compile its process binding. Certification contributes warnings only. No
function in this module inventories the repository or mutates authored state.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from officina.blueprints.authorization import (
    AuthorizationRelation,
    AuthorizationResult,
    CertificateRequirement,
    CrossedNamespaceGate,
    EffectiveAuthorizationFilter,
    ResolvedCallerReference,
)
from officina.blueprints.process_binding import (
    ProcessBindingError,
    compile_gateway_invocation,
    compile_route_smoke_invocation,
    parse_caller_invocation,
)
from officina.configuration.repository import RepositoryConfiguration
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
    """Expand a dotted module ID from top-level owner through terminal child."""

    parts = module_id.split(".")
    return tuple(".".join(parts[:index]) for index in range(1, len(parts) + 1))


def _lca(left: tuple[str, ...], right: tuple[str, ...]) -> str | None:
    """Return the deepest shared module ID in two ordered ancestry chains."""

    common = None
    for left_id, right_id in zip(left, right, strict=False):
        if left_id != right_id:
            break
        common = left_id
    return common


def _resolve_relative_module_id(owner_module_id: str, reference: str) -> str:
    """Resolve one leading-dot caller reference against its policy owner."""

    level = len(reference) - len(reference.lstrip("."))
    suffix = reference[level:]
    if not suffix:
        raise DirectBlueprintError.from_spec(
            "D27",
            target_module_id=owner_module_id,
            reason="has no local suffix",
        )
    owner_parts = owner_module_id.split(".")
    ascents = level - 1
    if ascents >= len(owner_parts):
        raise DirectBlueprintError.from_spec(
            "D27",
            target_module_id=owner_module_id,
            reason="escapes its registration root",
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
    """Evaluate one owner-local access predicate and retain audit metadata.

    Every named caller is resolved through the direct repository first, so an
    allowlist cannot grant authority to a nonexistent or unregistered module.
    A named ancestor admits its registered descendants; self-access is always
    admitted by the owner-local predicate.
    """

    if not isinstance(access, Mapping):
        raise DirectBlueprintError.from_spec(
            "D28",
            target_module_id=owner_module_id,
            access_kind=kind,
            interface_id=interface_id,
        )
    allow_all = access.get("allow_all_modules") is True
    raw_callers = access.get("allowed_callers")
    if not isinstance(raw_callers, list) or any(
        not isinstance(reference, str) for reference in raw_callers
    ):
        raise DirectBlueprintError.from_spec(
            "D28",
            target_module_id=owner_module_id,
            access_kind=kind,
            interface_id=interface_id,
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


def _safe_relative_path(
    raw_path: object, *, field_name: str, module_id: str
) -> PurePosixPath:
    """Validate an authored module-relative path without touching the filesystem."""

    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise DirectBlueprintError.from_spec(
            "D29",
            field_name=field_name,
            target_module_id=module_id,
        )
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DirectBlueprintError.from_spec(
            "D29",
            field_name=field_name,
            target_module_id=module_id,
        )
    return path


def _require_regular_without_symlinks(path: Path, *, module_id: str) -> None:
    """Require every relevant source-path component to be non-symlinked."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DirectBlueprintError.from_spec(
                "D30",
                module_id=module_id,
                target_module_id=module_id,
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DirectBlueprintError.from_spec(
                "D31",
                module_id=module_id,
                reason="has a path containing a symbolic link",
                target_module_id=module_id,
            )
    try:
        is_regular = stat.S_ISREG(path.stat().st_mode)
    except OSError as exc:
        raise DirectBlueprintError.from_spec(
            "D30",
            module_id=module_id,
            target_module_id=module_id,
        ) from exc
    if not is_regular:
        raise DirectBlueprintError.from_spec(
            "D31",
            module_id=module_id,
            reason="is not a regular file",
            target_module_id=module_id,
        )


def _load_source(
    terminal: DirectModule,
    source_id: str,
    locator: object,
) -> tuple[DirectBlueprintNode, Mapping[str, object]]:
    """Load and minimally validate the one behavioral source selected by an export."""

    if not isinstance(locator, Mapping) or not isinstance(locator.get("blueprint"), Mapping):
        raise DirectBlueprintError.from_spec(
            "D32",
            target_module_id=terminal.module_id,
            reason="an invalid shape",
        )
    blueprint = locator["blueprint"]
    if blueprint.get("base") != "module-root":
        raise DirectBlueprintError.from_spec(
            "D32",
            target_module_id=terminal.module_id,
            reason="an unsupported base",
        )
    relative = _safe_relative_path(
        blueprint.get("path"),
        field_name="blueprint.path",
        module_id=terminal.module_id,
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
        raise DirectBlueprintError.from_spec(
            "D33",
            target_module_id=terminal.module_id,
        ) from exc
    if (
        not isinstance(declaration, Mapping)
        or declaration.get("schema_version") != 6
        or declaration.get("node_type") != "behavioral_source"
    ):
        raise DirectBlueprintError.from_spec(
            "D34",
            target_module_id=terminal.module_id,
        )
    if declaration.get("id") != source_id:
        raise DirectBlueprintError.from_spec(
            "D35",
            target_module_id=terminal.module_id,
        )
    gateway = declaration.get("gateway")
    if not isinstance(gateway, Mapping):
        raise DirectBlueprintError.from_spec(
            "D36",
            target_module_id=terminal.module_id,
            reason="no gateway declaration",
        )
    gateway_relative = _safe_relative_path(
        gateway.get("path"),
        field_name="gateway.path",
        module_id=terminal.module_id,
    )
    gateway_path = module_root.joinpath(*gateway_relative.parts)
    _require_regular_without_symlinks(gateway_path, module_id=terminal.module_id)
    version = declaration.get("version")
    if type(version) is not int or version < 1:
        raise DirectBlueprintError.from_spec(
            "D36",
            target_module_id=terminal.module_id,
            reason="an invalid version",
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
    """Return bounded warning-only certification status for the selected route."""

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


def _require_discoverable_host_caller(
    caller_modules: tuple[DirectModule, ...], caller_module_id: str
) -> None:
    """Enforce the public host identity rule on one loaded caller ancestry."""

    caller = caller_modules[-1]
    discovery = caller.declaration.get("discovery")
    if (
        len(caller_modules) != 1
        or not isinstance(discovery, Mapping)
        or discovery.get("mechanism") != "skill"
    ):
        raise DirectBlueprintError.from_spec(
            "D37",
            caller_module_id=caller_module_id,
            target_module_id=caller_module_id,
        )


def authorize_host_caller(
    *, configuration: RepositoryConfiguration, caller_module_id: str
) -> None:
    """Authorize one public host identity without resolving a target binding."""

    repository = DirectBlueprintRepository(configuration)
    caller_modules = repository.load_ancestry(caller_module_id)
    _require_discoverable_host_caller(caller_modules, caller_module_id)


@dataclass(frozen=True)
class AuthorizedDirectInvocation:
    """One direct route authorized without caller argument compilation."""

    repository: DirectBlueprintRepository
    caller_modules: tuple[DirectModule, ...]
    target_modules: tuple[DirectModule, ...]
    source: DirectBlueprintNode
    export: DirectInterfaceExport
    authorization: AuthorizationResult
    diagnostics: tuple[InvocationDiagnostic, ...]


def resolve_direct_export_from_module(
    module: DirectModule,
    interface_id: str,
    interface_version: int | None,
) -> tuple[DirectBlueprintNode, DirectInterfaceExport]:
    """Resolve one terminal export, including its source and version checks."""

    target_module_id = module.module_id
    interface_module_id, _local_name = parse_interface_id(interface_id)
    if interface_module_id != target_module_id:
        raise DirectBlueprintError.from_spec(
            "D38",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason=f"is not owned by {target_module_id}",
        )
    raw_export = module.declaration["exports"].get(interface_id)
    if not isinstance(raw_export, Mapping):
        raise DirectBlueprintError.from_spec(
            "D38",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason="was not found",
        )
    source_interface_id = raw_export.get("source_interface")
    if not isinstance(source_interface_id, str) or ".source." not in source_interface_id:
        raise DirectBlueprintError.from_spec(
            "D39",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason="is invalid",
        )
    source_id, marker, source_local_name = source_interface_id.rpartition(".interface.")
    if marker != ".interface." or not source_id.startswith(f"{target_module_id}.source."):
        raise DirectBlueprintError.from_spec(
            "D39",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason="has the wrong owner",
        )
    locator = module.declaration["sources"].get(source_id)
    source, source_declaration = _load_source(module, source_id, locator)
    interfaces = source_declaration.get("interfaces")
    raw_source_interface = (
        interfaces.get(source_interface_id) if isinstance(interfaces, Mapping) else None
    )
    if not isinstance(raw_source_interface, Mapping):
        raise DirectBlueprintError.from_spec(
            "D39",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason="is absent",
        )
    available_version = raw_source_interface.get("version")
    if type(available_version) is not int or available_version < 1:
        raise DirectBlueprintError.from_spec(
            "D39",
            target_module_id=target_module_id,
            interface_id=interface_id,
            reason="has an invalid version",
        )
    if interface_version is None:
        interface_version = available_version
    elif (
        type(interface_version) is not int
        or interface_version < 1
        or available_version != interface_version
    ):
        raise DirectBlueprintError.from_spec(
            "D40",
            target_module_id=target_module_id,
            interface_id=interface_id,
            requested_version=(
                interface_version if type(interface_version) is int else "invalid"
            ),
            available_version=available_version,
        )
    return source, DirectInterfaceExport(
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


def authorize_direct_invocation(
    *,
    configuration: RepositoryConfiguration,
    caller_module_id: str,
    interface_id: str,
    interface_version: int | None,
    certification_status: Mapping[str, object] | None = None,
    host_caller: bool = False,
) -> AuthorizedDirectInvocation:
    """Authorize one direct route without compiling caller arguments."""

    target_module_id, _local_name = parse_interface_id(interface_id)
    repository = DirectBlueprintRepository(configuration)
    caller_modules = repository.load_ancestry(caller_module_id)
    if host_caller:
        _require_discoverable_host_caller(caller_modules, caller_module_id)
    target_modules = repository.load_ancestry(target_module_id)
    caller_ancestry = tuple(module.module_id for module in caller_modules)
    target_ancestry = tuple(module.module_id for module in target_modules)
    terminal = target_modules[-1]
    source, export = resolve_direct_export_from_module(
        terminal,
        interface_id,
        interface_version,
    )
    interface_version = export.version
    raw_export = export.export_declaration
    assert raw_export is not None
    source_id = export.source_node_id
    source_interface_id = export.source_interface_id
    assert source_id is not None
    assert source_interface_id is not None

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
            raise DirectBlueprintError.from_spec(
                "D41",
                target_module_id=target_module_id,
                route_owner_module_id=route_owner.module_id,
                child_segment=local_segment,
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
            raise DirectBlueprintError.from_spec(
                "D42",
                target_module_id=target_module_id,
                child_module_id=child.module_id,
            )
        surface = route.get("surface")
        only = surface.get("only") if isinstance(surface, Mapping) else None
        surface_version = only.get(interface_id) if isinstance(only, Mapping) else None
        if (
            type(surface_version) is not int
            or surface_version < 1
            or surface_version != interface_version
        ):
            raise DirectBlueprintError.from_spec(
                "D43",
                target_module_id=target_module_id,
                interface_id=interface_id,
                interface_version=interface_version,
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
            raise UnauthorizedCallerError.from_spec(
                "D04",
                caller_module_id=caller_module_id,
                target_module_id=target_module_id,
                interface_id=interface_id,
                gate="namespace-route",
            )
        interface_access = route.get("interface_access")
        if interface_access is not None and not isinstance(interface_access, Mapping):
            raise DirectBlueprintError.from_spec(
                "D44",
                target_module_id=target_module_id,
                route_owner_module_id=route_owner.module_id,
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
                raise UnauthorizedCallerError.from_spec(
                    "D04",
                    caller_module_id=caller_module_id,
                    target_module_id=target_module_id,
                    interface_id=interface_id,
                    gate="namespace-interface",
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
        raise UnauthorizedCallerError.from_spec(
            "D04",
            caller_module_id=caller_module_id,
            target_module_id=target_module_id,
            interface_id=interface_id,
            gate="terminal-export",
        )

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
    return AuthorizedDirectInvocation(
        repository=repository,
        caller_modules=caller_modules,
        target_modules=target_modules,
        source=source,
        export=export,
        authorization=authorization,
        diagnostics=diagnostics,
    )


def compile_direct_invocation(
    authorized: AuthorizedDirectInvocation,
    *,
    argv: list[str],
    stdin_requested: bool,
) -> ResolvedInvocationMetadata:
    """Compile caller arguments and process metadata for an authorized route."""

    source = authorized.source
    export = authorized.export
    authorization = authorized.authorization
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
        raise ResolutionFailedError.from_spec(
            "D45",
            caller_module_id=authorization.caller_module_id,
            target_module_id=authorization.requested_owner_module_id,
            interface_id=export.interface_id,
        ) from exc

    gateway_relative = source.gateway_path.relative_to(source.module_root)
    logical_package = logical_python_package_name(authorization.requested_owner_module_id)
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
        raise ResolutionFailedError.from_spec(
            "D46",
            caller_module_id=authorization.caller_module_id,
            target_module_id=authorization.requested_owner_module_id,
            interface_id=export.interface_id,
        ) from exc
    return ResolvedInvocationMetadata(
        caller_module_id=authorization.caller_module_id,
        target_module_id=authorization.requested_owner_module_id,
        script_interface=export.source_interface_id or "",
        target=export.interface_id,
        pattern=plan.pattern_name or "",
        cwd=authorized.target_modules[-1].blueprint_path.parent,
        command=list(plan.argv),
        stdin=plan.stdin_argument_id is not None,
        python_target=python_target,
        caller_source_id=None,
        terminal_module_id=authorization.terminal_module_id,
        implementing_source_id=export.source_node_id,
        authorization=authorization,
        schema_version=6,
        diagnostics=authorized.diagnostics,
    )


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

    return compile_direct_invocation(
        authorize_direct_invocation(
            configuration=configuration,
            caller_module_id=caller_module_id,
            interface_id=interface_id,
            interface_version=interface_version,
            certification_status=certification_status,
            host_caller=host_caller,
        ),
        argv=argv,
        stdin_requested=stdin_requested,
    )


__all__ = [
    "authorize_direct_invocation",
    "authorize_host_caller",
    "compile_direct_invocation",
    "resolve_direct_invocation",
]
