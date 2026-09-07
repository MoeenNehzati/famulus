"""Materialize and execute one already-bounded version-6 dispatch request.

This module is the live boundary between authorization metadata and subprocess
execution. It accepts an exact repository configuration path, delegates
route-local lookup and authorization to :mod:`direct_authorization`, constructs
the confined Python runner command, and launches it. It deliberately contains
no repository discovery, graph construction, certification work, route cache,
repair, synchronization, or routing-state writes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
    DISPATCHER_ERROR_SPECS,
    DispatcherError,
    InvocationError,
    InvalidRequestError,
    LaunchFailedError,
    RuntimeMisconfiguredError,
)


# Every repo-owned launch that temporarily enables broad native-handle
# inheritance must share this lock so unrelated handles cannot leak.
_WINDOWS_DIAGNOSTIC_LAUNCH_LOCK = threading.Lock()
_DIAGNOSTIC_LIMIT = 16 * 1024


def _collect_diagnostic(
    reader: int,
    collected: bytearray,
    overflow: list[bool],
) -> None:
    """Continuously drain the private pipe; callers only join for a bound."""

    while True:
        try:
            chunk = os.read(reader, 4096)
        except OSError:
            break
        if not chunk:
            break
        remaining = _DIAGNOSTIC_LIMIT + 1 - len(collected)
        if remaining > 0:
            collected.extend(chunk[:remaining])
        if len(collected) > _DIAGNOSTIC_LIMIT or len(chunk) > remaining:
            overflow[0] = True
def _registered_diagnosis(payload: bytes) -> DispatcherError | None:
    """Accept exactly one complete registered dispatcher payload."""

    if not payload or payload.strip() != payload:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None
    context = {
        key: item
        for key, item in value.items()
        if key not in {"schema_version", "code", "message"}
    }
    for entry_id, spec in DISPATCHER_ERROR_SPECS.items():
        if not entry_id.startswith("R") or value.get("code") != spec.code:
            continue
        if set(context) - spec.context_fields:
            continue
        try:
            error = DispatcherError.from_spec(entry_id, **context)
        except (KeyError, ValueError):
            continue
        if str(error) == value.get("message"):
            return error
    return None


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
        raise RuntimeMisconfiguredError.from_spec(
            "D08",
            caller_module_id=metadata.caller_module_id,
            target_module_id=metadata.target_module_id,
            interface_id=metadata.target,
        )
    logical_package = python_target.logical_package
    logical_entrypoint = python_target.logical_entrypoint
    if logical_package is None or logical_entrypoint is None:
        raise RuntimeMisconfiguredError.from_spec(
            "D09",
            caller_module_id=metadata.caller_module_id,
            target_module_id=metadata.target_module_id,
            interface_id=metadata.target,
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
    raise RuntimeMisconfiguredError.from_spec(
        "D06",
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
        raise RuntimeMisconfiguredError.from_spec(
            "D07",
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
        raise InvalidRequestError.from_spec("D05")
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
        raise InvalidRequestError.from_spec("D05")
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

    text_mode = text if text is not None else isinstance(stdin, str)
    input_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    try:
        reader, writer = os.pipe()
    except OSError as exc:
        resolved.close()
        raise LaunchFailedError.from_spec(
            "D10",
            caller_module_id=resolved.caller_module_id,
            target_module_id=resolved.target_module_id,
            interface_id=resolved.target,
        ) from exc
    collected = bytearray()
    overflow = [False]
    collector = threading.Thread(
        target=_collect_diagnostic,
        args=(reader, collected, overflow),
        daemon=True,
    )
    try:
        collector.start()
    except BaseException:
        os.close(writer)
        os.close(reader)
        resolved.close()
        raise

    command = list(resolved.command)
    command[4:4] = ["--diagnostic-writer", str(writer)]
    popen_kwargs: dict[str, Any] = {
        "cwd": resolved.cwd,
        "env": resolved.env,
        "stdin": subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE if capture_output else None,
        "stderr": subprocess.PIPE if capture_output else None,
    }
    windows_writer: int | None = None
    try:
        if text_mode and isinstance(stdin, bytes):
            raise TypeError("text mode requires string stdin")
        if not text_mode and isinstance(stdin, str):
            raise TypeError("binary mode requires bytes stdin")
        try:
            if os.name == "nt":
                import msvcrt

                windows_writer = os.dup(writer)
                os.close(writer)
                writer = -1
                handle = msvcrt.get_osfhandle(windows_writer)
                command[5] = str(handle)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.lpAttributeList = {"handle_list": [handle]}
                popen_kwargs.update(startupinfo=startupinfo, close_fds=True)
                with _WINDOWS_DIAGNOSTIC_LAUNCH_LOCK:
                    os.set_handle_inheritable(handle, True)
                    try:
                        process = subprocess.Popen(command, **popen_kwargs)
                    finally:
                        try:
                            os.set_handle_inheritable(handle, False)
                        except OSError:
                            # Closing the duplicated parent descriptor is the
                            # safe fallback when inheritance cannot be reset.
                            try:
                                os.close(windows_writer)
                            except OSError:
                                pass
                            windows_writer = None
            else:
                popen_kwargs["pass_fds"] = (writer,)
                process = subprocess.Popen(command, **popen_kwargs)
        except OSError as exc:
            raise LaunchFailedError.from_spec(
                "D10",
                caller_module_id=resolved.caller_module_id,
                target_module_id=resolved.target_module_id,
                interface_id=resolved.target,
            ) from exc

        if writer >= 0:
            os.close(writer)
            writer = -1
        if windows_writer is not None:
            os.close(windows_writer)
            windows_writer = None
        try:
            stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise DispatcherError.from_spec(
                "D69",
                caller_module_id=resolved.caller_module_id,
                target_module_id=resolved.target_module_id,
                interface_id=resolved.target,
                timeout=timeout,
            )

        collector.join(timeout=0.25)
        if process.returncode == 70 and not collector.is_alive() and not overflow[0]:
            diagnosis = _registered_diagnosis(bytes(collected))
            if diagnosis is not None:
                diagnosis.caller_module_id = resolved.caller_module_id
                diagnosis.target_module_id = resolved.target_module_id
                raise diagnosis

        if text_mode:
            try:
                stdout = None if stdout is None else stdout.decode("utf-8")
                stderr = None if stderr is None else stderr.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DispatcherError.from_spec(
                    "D71",
                    caller_module_id=resolved.caller_module_id,
                    target_module_id=resolved.target_module_id,
                    interface_id=resolved.target,
                ) from exc
        completed = subprocess.CompletedProcess(
            resolved.command,
            process.returncode,
            stdout,
            stderr,
        )
        if check and process.returncode:
            raise DispatcherError.from_spec(
                "D70",
                caller_module_id=resolved.caller_module_id,
                target_module_id=resolved.target_module_id,
                interface_id=resolved.target,
                returncode=process.returncode,
            )
        return completed
    finally:
        if writer >= 0:
            os.close(writer)
        if windows_writer is not None:
            os.close(windows_writer)
        collector.join(timeout=0.25)
        try:
            os.close(reader)
        except OSError:
            pass
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
