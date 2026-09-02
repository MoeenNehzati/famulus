"""Materialize and execute one already-bounded version-6 dispatch request.

This module is the live boundary between authorization metadata and subprocess
execution. It accepts an exact repository configuration path, delegates
route-local lookup and authorization to :mod:`direct_authorization`, constructs
the confined Python runner command, and launches it. It deliberately contains
no repository discovery, graph construction, certification work, route cache,
repair, synchronization, or routing-state writes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from officina.configuration.repository import (
    RepositoryConfiguration,
    RepositoryConfigurationError,
    load_repository_configuration,
)
from officina.dispatcher.direct_authorization import (
    AuthorizedDirectInvocation,
    authorize_direct_invocation,
    authorize_host_caller as authorize_direct_host_caller,
    compile_direct_invocation,
    resolve_direct_invocation,
)
from officina.dispatcher.direct_models import (
    InvocationDiagnostic,
    ResolvedInvocationMetadata,
)
from officina.dispatcher.errors import (
    InvocationError,
    InvalidRequestError,
    LaunchFailedError,
    RuntimeMisconfiguredError,
)


def _target_module_id(target: str) -> str:
    """Return the module prefix used to identify malformed-request failures."""

    return target.split(".interface.", 1)[0] if ".interface." in target else target


@dataclass(frozen=True)
class ResolvedInvocation:
    """One authorized route materialized for subprocess execution.

    ``metadata_value`` is the immutable authorization/compilation result.
    ``command`` and ``env`` are the only launch inputs added here. The object
    owns no descriptors, snapshots, locks, or generated state; ``close`` exists
    solely for compatibility with the earlier context-manager API.
    """

    metadata_value: ResolvedInvocationMetadata
    command: list[str]
    env: dict[str, str]

    @property
    def caller_module_id(self) -> str:
        return self.metadata_value.caller_module_id

    @property
    def target_module_id(self) -> str:
        return self.metadata_value.target_module_id

    @property
    def target(self) -> str:
        return self.metadata_value.target

    @property
    def cwd(self) -> Path:
        return self.metadata_value.cwd

    @property
    def diagnostics(self) -> tuple[InvocationDiagnostic, ...]:
        return self.metadata_value.diagnostics

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return ()

    def metadata(self) -> ResolvedInvocationMetadata:
        return self.metadata_value

    def as_payload(self) -> dict[str, Any]:
        return self.metadata_value.as_payload()

    def close(self) -> None:
        """Direct v6 routes own no generated snapshot or descriptor state."""

    def __enter__(self) -> "ResolvedInvocation":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def _confined_environment(
    configuration: RepositoryConfiguration,
    module_root: Path,
) -> dict[str, str]:
    """Remove ambient import paths that expose repository-owned modules."""

    env = os.environ.copy()
    physical_root = Path(os.path.abspath(module_root))
    runtime_import_root = Path(__file__).resolve().parents[2]
    configured_roots = tuple(root.resolve() for root in configuration.module_roots)
    inherited = env.get("PYTHONPATH", "").split(os.pathsep)
    retained = []
    for entry in inherited:
        if not entry:
            continue
        try:
            candidate = Path(entry).resolve()
        except OSError:
            continue
        if candidate == runtime_import_root:
            retained.append(candidate.as_posix())
            continue
        overlaps_repository_modules = any(
            candidate == root
            or candidate.is_relative_to(root)
            or root.is_relative_to(candidate)
            for root in configured_roots
        )
        exposes_target = candidate == physical_root or candidate.is_relative_to(
            physical_root
        )
        if not overlaps_repository_modules and not exposes_target:
            retained.append(candidate.as_posix())
    if retained:
        env["PYTHONPATH"] = os.pathsep.join(retained)
    else:
        env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8:strict"
    return env


def _materialize_metadata(
    configuration: RepositoryConfiguration,
    metadata: ResolvedInvocationMetadata,
) -> ResolvedInvocation:
    """Construct the confined Python runner command for compiled metadata.

    The selected source must produce a complete logical Python target. Missing
    runner metadata is reported as authored runtime misconfiguration before a
    subprocess exists. No gateway module is imported in the dispatcher.
    """

    python_target = metadata.python_target
    if python_target is None:
        raise RuntimeMisconfiguredError(
            f"{metadata.target}: direct route has no Python target",
            caller_module_id=metadata.caller_module_id,
            target_module_id=metadata.target_module_id,
        )
    logical_package = python_target.logical_package
    logical_entrypoint = python_target.logical_entrypoint
    if logical_package is None or logical_entrypoint is None:
        raise RuntimeMisconfiguredError(
            f"{metadata.target}: direct route has no logical Python package",
            caller_module_id=metadata.caller_module_id,
            target_module_id=metadata.target_module_id,
        )
    command = [
        sys.executable,
        "-P",
        "-m",
        "officina.runtime.python_machine_interface_runner",
        "--logical-package",
        logical_package,
        "--logical-entrypoint",
        logical_entrypoint,
        "--physical-package-prefix",
        metadata.cwd.name,
        "--confined-module-root",
        metadata.cwd.as_posix(),
        "--runtime-caller-module-id",
        metadata.terminal_module_id or metadata.target_module_id,
        "--runtime-caller-source-id",
        metadata.implementing_source_id or "",
        "--runtime-repo-root",
        configuration.repository_root.as_posix(),
        "--runtime-repository-config",
        configuration.config_path.as_posix(),
        python_target.gateway_path.as_posix(),
        python_target.process_entry,
        *metadata.command,
    ]
    return ResolvedInvocation(
        metadata_value=metadata,
        command=command,
        env=_confined_environment(configuration, metadata.cwd),
    )


def materialize_authorized_invocation(
    authorized: AuthorizedDirectInvocation,
    *,
    argv: list[str],
    stdin_requested: bool,
) -> ResolvedInvocation:
    """Compile one authorized route and construct its confined runner."""

    metadata = compile_direct_invocation(
        authorized,
        argv=argv,
        stdin_requested=stdin_requested,
    )
    return _materialize_metadata(authorized.repository.configuration, metadata)


def _materialize(
    *,
    repository_config: Path,
    caller_module_id: str,
    target: str,
    args: list[str],
    stdin_requested: bool,
    target_version: int | None,
    host_caller: bool,
) -> ResolvedInvocation:
    """Authorize one route and construct its confined Python runner command."""

    configuration = _load_configuration(
        repository_config,
        caller_module_id=caller_module_id,
        target=target,
    )
    authorized = authorize_direct_invocation(
        configuration=configuration,
        caller_module_id=caller_module_id,
        interface_id=target,
        interface_version=target_version,
        host_caller=host_caller,
    )
    return materialize_authorized_invocation(
        authorized,
        argv=args,
        stdin_requested=stdin_requested,
    )


def _config_path(
    repository_config: Path | None,
    repo_root: Path | None,
    *,
    caller_module_id: str,
    target: str,
) -> Path:
    """Require the launcher's exact config path; never derive one from a root."""

    if repository_config is not None:
        return Path(repository_config)
    raise RuntimeMisconfiguredError(
        "dispatcher requires the exact repository configuration path",
        caller_module_id=caller_module_id,
        target_module_id=_target_module_id(target),
    )


def _load_configuration(
    path: Path,
    *,
    caller_module_id: str,
    target: str,
) -> RepositoryConfiguration:
    """Translate configuration failures into the dispatcher error contract."""

    try:
        return load_repository_configuration(path)
    except RepositoryConfigurationError as exc:
        raise RuntimeMisconfiguredError(
            str(exc),
            caller_module_id=caller_module_id,
            target_module_id=_target_module_id(target),
        ) from exc


def _resolve_dispatch(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    repository_config: Path | None = None,
    host_caller: bool = False,
    **_legacy: object,
) -> ResolvedInvocation:
    """Internal resolver shared by host and trusted nested callers.

    ``host_caller`` selects the stricter top-level discovery check. Legacy
    keyword arguments remain accepted only to keep old Python call sites from
    failing at import boundaries; they do not restore graph-based routing.
    """

    caller = caller_skill.strip()
    if not caller:
        raise InvalidRequestError("caller_skill must be a non-empty string")
    return _materialize(
        repository_config=_config_path(
            repository_config,
            repo_root,
            caller_module_id=caller,
            target=target,
        ),
        caller_module_id=caller,
        target=target,
        args=list(args or []),
        stdin_requested=stdin_requested,
        target_version=target_version,
        host_caller=host_caller,
    )


def _resolve_host_dispatch_metadata(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    repository_config: Path | None = None,
    **_legacy: object,
) -> ResolvedInvocationMetadata:
    """Authorize and compile a host call without materializing a subprocess."""

    configuration = _load_configuration(
        _config_path(
            repository_config,
            repo_root,
            caller_module_id=caller_skill,
            target=target,
        ),
        caller_module_id=caller_skill,
        target=target,
    )
    return resolve_direct_invocation(
        configuration=configuration,
        caller_module_id=caller_skill,
        interface_id=target,
        interface_version=target_version,
        argv=list(args or []),
        stdin_requested=stdin_requested,
        host_caller=True,
    )


def authorize_host_caller(
    *, caller_skill: str, repository_config: Path
) -> None:
    """Authorize one public host identity without resolving process binding."""

    caller = caller_skill.strip()
    if not caller:
        raise InvalidRequestError("caller_skill must be a non-empty string")
    configuration = _load_configuration(
        _config_path(
            repository_config,
            None,
            caller_module_id=caller,
            target=caller,
        ),
        caller_module_id=caller,
        target=caller,
    )
    authorize_direct_host_caller(
        configuration=configuration,
        caller_module_id=caller,
    )


def resolve_dispatch(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    target_version: int | None = None,
    repository_config: Path | None = None,
) -> ResolvedInvocation:
    """Resolve a public host request using one exact repository config path."""

    return _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        target_version=target_version,
        repository_config=repository_config,
        host_caller=True,
    )


def resolve_dispatch_metadata(**kwargs: Any) -> ResolvedInvocationMetadata:
    """Return the descriptor-free metadata for a public host request."""

    with resolve_dispatch(**kwargs) as resolved:
        return resolved.metadata()


def _run_resolved_invocation(
    resolved: ResolvedInvocation,
    *,
    stdin: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Launch one resolved command and preserve subprocess I/O semantics.

    The target module root becomes cwd and the precomputed confined environment
    replaces ambient repository import exposure. Python launch failures are
    translated to the structured dispatcher contract; target exit failures are
    returned or raised according to ``check`` exactly as in ``subprocess.run``.
    """

    run_kwargs: dict[str, Any] = {
        "cwd": resolved.cwd,
        "env": resolved.env,
        "capture_output": capture_output,
        "check": check,
    }
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if stdin is not None:
        run_kwargs["input"] = stdin
    else:
        run_kwargs["stdin"] = subprocess.DEVNULL
    if text is not None:
        run_kwargs["text"] = text
    elif isinstance(stdin, str):
        run_kwargs["text"] = True
    if run_kwargs.get("text"):
        run_kwargs["encoding"] = "utf-8"
        run_kwargs["errors"] = "strict"
    try:
        try:
            return subprocess.run(resolved.command, **run_kwargs)
        except OSError as exc:
            raise LaunchFailedError(
                f"{resolved.target}: launch failed: {exc}",
                caller_module_id=resolved.caller_module_id,
                target_module_id=resolved.target_module_id,
            ) from exc
    finally:
        resolved.close()


def _dispatch_host(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool | None = None,
    repo_root: Path | None = None,
    target_version: int | None = None,
    warning_handler: Callable[[InvocationDiagnostic], None] | None = None,
    repository_config: Path | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Resolve, report advisory warnings, and execute one public host call."""

    resolved = _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin is not None,
        repo_root=repo_root,
        target_version=target_version,
        repository_config=repository_config,
        host_caller=True,
    )
    if warning_handler is not None:
        for diagnostic in resolved.diagnostics:
            warning_handler(diagnostic)
    return _run_resolved_invocation(
        resolved,
        stdin=stdin,
        timeout=timeout,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def dispatch(**kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Public convenience API for host dispatch."""

    return _dispatch_host(**kwargs)


__all__ = [
    "InvocationError",
    "InvocationDiagnostic",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "_dispatch_host",
    "_resolve_dispatch",
    "_resolve_host_dispatch_metadata",
    "_run_resolved_invocation",
    "authorize_host_caller",
    "dispatch",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]
