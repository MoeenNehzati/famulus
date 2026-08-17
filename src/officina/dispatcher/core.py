"""Compatibility API backed exclusively by the direct v6 dispatcher path.

The live dispatcher does not inventory repositories, construct graphs, repair
blueprints, synchronize generated state, or cache routes.  Offline validators
own those responsibilities.  This module keeps established import locations
while delegating every resolution and launch to route-local v6 blueprints.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import officina.common.toml_io as toml_io
from officina.dispatcher.direct_models import (
    InvocationDiagnostic,
    ResolvedInvocationMetadata,
)
from officina.dispatcher.direct_runtime import (
    ResolvedInvocation,
    _dispatch_host,
    _resolve_dispatch,
    _resolve_host_dispatch_metadata,
    _run_resolved_invocation,
    dispatch,
    resolve_dispatch,
    resolve_dispatch_metadata,
)
from officina.dispatcher.errors import *  # noqa: F403


def load_repository_blueprint_graph(*args: Any, **kwargs: Any) -> Any:
    """Lazy offline compatibility seam; never used by live dispatch resolution."""

    from officina.blueprints.graph import load_repository_blueprint_graph as load

    return load(*args, **kwargs)


def collect_blueprints(*args: Any, **kwargs: Any) -> Any:
    """Lazy offline compatibility seam for inventory-focused consumers."""

    from officina.blueprints.inventory import collect_blueprints as collect

    return collect(*args, **kwargs)


def repository_certification_view(*args: Any, **kwargs: Any) -> Any:
    """Lazy offline compatibility seam; never used by live dispatch resolution."""

    from officina.certification.view import repository_certification_view as load

    return load(*args, **kwargs)


def get_repo_root(repo_root: Path | None = None) -> Path:
    """Return an explicit root, the managed ``AI`` root, or the source root."""

    if repo_root is not None:
        return Path(repo_root).resolve()
    configured = os.environ.get("AI")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _resolve_repository_dispatch_metadata(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repository_config: Path,
    target_version: int | None = None,
    **_ignored: object,
) -> ResolvedInvocationMetadata:
    """Resolve one explicit repository route without generated routing state."""

    return _resolve_host_dispatch_metadata(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repository_config=repository_config,
        target_version=target_version,
    )


def _resolve_dispatch_metadata_for_trace(
    *,
    caller_module_id: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    certification_view: Any | None = None,
    graph: Any | None = None,
    caller_source_id: str | None = None,
    **_offline_context: object,
) -> ResolvedInvocationMetadata:
    """Resolve offline traces without putting graph work on the live path."""

    repository_root = get_repo_root(repo_root)
    repository_config = repository_root / toml_io.repository_config_filename()
    graph_schema_version = getattr(graph, "schema_version", None)
    if (
        graph_schema_version in {4, 5}
        or not repository_config.is_file()
    ):
        return _resolve_legacy_trace_metadata(
            caller_module_id=caller_module_id,
            caller_source_id=caller_source_id,
            target=target,
            args=args or [],
            stdin_requested=stdin_requested,
            repo_root=repository_root,
            target_version=target_version,
            certification_view=certification_view,
            graph=graph,
        )

    return _resolve_dispatch(
        caller_skill=caller_module_id,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repository_config=repository_config,
        target_version=target_version,
        host_caller=False,
    ).metadata()


def _resolve_legacy_trace_metadata(
    *,
    caller_module_id: str,
    caller_source_id: str | None,
    target: str,
    args: list[str],
    stdin_requested: bool,
    repo_root: Path,
    target_version: int | None,
    certification_view: Any | None,
    graph: Any | None,
) -> ResolvedInvocationMetadata:
    """Compatibility-only v4/v5 graph resolver for validators and old fixtures."""

    from officina.blueprints.authorization import (
        AuthorizationRequest,
        resolve_interface_authorization,
    )
    from officina.blueprints.graph import resolve_export
    from officina.blueprints.process_binding import (
        compile_gateway_invocation,
        compile_route_smoke_invocation,
        parse_caller_invocation,
    )
    from officina.runtime.python_machine_interface import PythonProcessTarget

    selected_graph = graph or load_repository_blueprint_graph(repo_root)
    module, source, export = resolve_export(
        selected_graph,
        target,
        None if selected_graph.schema_version == 5 else target_version,
    )
    authorization = None
    terminal_module_id = module.node_id
    implementing_source_id = source.node_id
    if selected_graph.schema_version == 5:
        authorization = resolve_interface_authorization(
            selected_graph,
            AuthorizationRequest(
                caller_module_id=caller_module_id,
                caller_source_id=caller_source_id,
                interface_id=export.interface_id,
                version=export.version if target_version is None else target_version,
            ),
        )
        if not authorization.allowed:
            raise InvocationError(
                f"{target}: authorization rejected [{authorization.diagnostic}]"
            )
        terminal_module_id = authorization.terminal_module_id or module.node_id
        implementing_source_id = authorization.implementing_source_id or source.node_id
    else:
        caller = selected_graph.nodes.get(caller_module_id)
        if caller is None or caller.node_type != "module":
            raise CallerNotFoundError(f"caller module `{caller_module_id}` does not exist")
        if caller_module_id != module.node_id:
            declared = any(
                isinstance(use, dict)
                and use.get("interface") == export.interface_id
                and use.get("version") == export.version
                for source_id in selected_graph.module_sources.get(caller_module_id, ())
                for use in selected_graph.nodes[source_id].declaration.get("uses_interfaces", [])
            )
            if not declared:
                raise InterfaceUseUndeclaredError(
                    f"caller module `{caller_module_id}` does not declare use of `{target}`"
                )
            access = export.export_declaration.get("access", {})
            if (
                access.get("allow_all_modules") is not True
                and caller_module_id not in access.get("allowed_callers", [])
            ):
                raise UnauthorizedCallerError(
                    caller_module_id=caller_module_id,
                    target_module_id=module.node_id,
                    interface_id=target,
                )
    compiled = (
        compile_route_smoke_invocation(source, export)
        if args == ["--route-smoke"] and not stdin_requested
        else compile_gateway_invocation(
            source,
            export,
            parse_caller_invocation(export, args, stdin_requested=stdin_requested),
        )
    )
    gateway = source.declaration.get("gateway", {})
    relative_gateway = source.gateway_path.relative_to(source.module_root)
    diagnostics: tuple[InvocationDiagnostic, ...] = ()
    if certification_view is not None:
        decision = certification_view.check_export(
            module.node_id,
            export.interface_id,
            export.version,
            export.source_node_id,
        )
        if not decision.certified:
            diagnostics = (
                InvocationDiagnostic(
                    severity="warning",
                    code=decision.code,
                    message=decision.message,
                    subject=export.interface_id,
                ),
            )
    return ResolvedInvocationMetadata(
        caller_module_id=caller_module_id,
        target_module_id=module.node_id,
        script_interface=export.local_name,
        target=export.interface_id,
        pattern=compiled.pattern_name or export.local_name,
        cwd=source.module_root,
        command=list(compiled.argv),
        stdin=compiled.stdin_argument_id is not None,
        python_target=PythonProcessTarget(relative_gateway, compiled.entry),
        caller_source_id=caller_source_id,
        terminal_module_id=terminal_module_id,
        implementing_source_id=implementing_source_id,
        authorization=authorization,
        schema_version=selected_graph.schema_version,
        diagnostics=diagnostics,
    )


def _dispatch_repository(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin: str | bytes | None = None,
    repository_config: Path,
    **kwargs: Any,
):
    """Execute one explicit repository route through the direct dispatcher."""

    return _dispatch_host(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin=stdin,
        repository_config=repository_config,
        **kwargs,
    )


__all__ = [
    "InvocationDiagnostic",
    "InvocationError",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "_dispatch_host",
    "_dispatch_repository",
    "_resolve_dispatch",
    "_resolve_dispatch_metadata_for_trace",
    "_resolve_host_dispatch_metadata",
    "_resolve_repository_dispatch_metadata",
    "_run_resolved_invocation",
    "dispatch",
    "get_repo_root",
    "load_repository_blueprint_graph",
    "repository_certification_view",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]
