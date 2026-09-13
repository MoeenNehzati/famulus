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
import site
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, Callable, Iterator

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
from officina.dispatcher.direct_blueprints import parse_interface_id
from officina.dispatcher.errors import (
    SetupBlocked,
    DISPATCHER_ERROR_SPECS,
    DispatcherError,
    InvocationError,
    InvalidRequestError,
    LaunchFailedError,
    RuntimeMisconfiguredError,
)
from officina.runtime.dispatch_trace import trace_process


# Every repo-owned launch that temporarily enables broad native-handle
# inheritance must share this lock so unrelated handles cannot leak.
_WINDOWS_DIAGNOSTIC_LAUNCH_LOCK = threading.Lock()
_DIAGNOSTIC_LIMIT = 16 * 1024
_SUBPROCESS = subprocess
# ponytail: one interpreter owns cwd, stdio, and import hooks; use persistent
# isolated workers if concurrent interface execution becomes a bottleneck.
_IN_PROCESS_EXECUTION_LOCK = threading.RLock()


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
def _registered_diagnosis(payload: bytes) -> DispatcherError | SetupBlocked | None:
    """Accept exactly one complete registered dispatcher payload."""

    if not payload or payload.strip() != payload:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if isinstance(value, dict) and set(value) == {"setup_blocked"}:
        try:
            if len(payload) >= _DIAGNOSTIC_LIMIT or json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") != payload:
                return None
            record = value["setup_blocked"]
            path, status, lifecycle = record["call_path"], record["status"], record["lifecycle"]
            if set(record) != {"call_path", "status", "lifecycle"} or type(path) is not list or not 1 <= len(path) <= 32:
                return None
            for target in path:
                parse_interface_id(target)
            if isinstance(status, dict) and lifecycle is None:
                return SetupBlocked(status, path)
            if status is None and type(lifecycle) is list and len(lifecycle) == 2 and lifecycle[1] in ("setup", "teardown"):
                parse_interface_id(lifecycle[0])
                return SetupBlocked(None, path, tuple(lifecycle))
        except (KeyError, TypeError, InvocationError, RecursionError):
            pass
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
    """One authorized route materialized for runtime execution.

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
    *,
    setup_preflight_authorized: bool = False,
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
        "--immediate-caller-module-id",
        metadata.caller_module_id,
        "--runtime-repo-root",
        configuration.repository_root.as_posix(),
        "--runtime-repository-config",
        configuration.config_path.as_posix(),
        *(
            ["--setup-preflight-authorized"]
            if setup_preflight_authorized
            else []
        ),
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
    setup_preflight_authorized: bool = False,
) -> ResolvedInvocation:
    """Compile one authorized route and construct its confined runner."""

    metadata = compile_direct_invocation(
        authorized,
        argv=argv,
        stdin_requested=stdin_requested,
    )
    return _materialize_metadata(
        authorized.repository.configuration,
        metadata,
        setup_preflight_authorized=setup_preflight_authorized,
    )


def _check_setup(authorized, *, setup_preflight_authorized=False):
    from officina.blueprints.direct_setup import load_direct_setup_projection
    def _exact_setup_value(actual, expected):
        return type(actual) is type(expected) and (set(actual) == set(expected) and all(_exact_setup_value(actual[key], item) for key, item in expected.items()) if isinstance(expected, dict) else actual == expected)
    caller, target, version = authorized.authorization.caller_module_id, authorized.export.interface_id, authorized.export.version
    if "setup-interface-manager._rtx" in (caller, authorized.authorization.terminal_module_id):
        return
    projection = load_direct_setup_projection(authorized.repository, authorized.target_modules, authorized.export)
    if not projection.graph.managed_setups:
        return
    if projection.lifecycle is not None:
        raise SetupBlocked(None, (target,), projection.lifecycle)
    if setup_preflight_authorized:
        return
    original = dict(caller=caller, interface=target, version=version)

    def declined(value):
        step = value.get("current_step") if isinstance(value, dict) else None
        valid_step = (
            type(step) is dict
            and set(step) == {"interface", "version", "kind", "action"}
            and isinstance(step["interface"], str) and step["interface"]
            and type(step["version"]) is int and step["version"] > 0
            and step["kind"] in {"markdown", "python"}
            and step["action"] == "run-setup"
        )
        return (
            type(value) is dict
            and set(value) in (
                {"schema_version", "flow_id", "operation", "state", "current_step", "original", "resume_original", "error", "error_code"},
                {"schema_version", "flow_id", "operation", "state", "current_step", "original", "resume_original", "error", "error_code", "clues"},
            )
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["operation"] == "authorize" and value["resume_original"] is False
            and value.get("clues", []) == [] and valid_step
            and (
                value["state"] == "failed" and value["flow_id"] is None
                and value["original"] == original
                and value["error_code"] == "setup.target_not_ready"
                and value["error"] == "The target requires setup before it can be authorized."
                or value["state"] == "busy" and isinstance(value["flow_id"], str)
                and value["flow_id"] and value["original"] is None
                and value["error_code"] == "setup.flow_busy"
                and value["error"] == "Another managed setup flow became active before authorization completed."
            )
        )

    def manager(operation, arguments, *, allow_authorize_decline=False):
        try:
            result = _run_resolved_invocation(_resolve_dispatch(caller_skill=caller, target=f"setup-interface-manager._rtx.interface.{operation}", args=arguments, target_version=1, repository_config=authorized.repository.configuration.config_path), text=True)
        except (InvocationError, OSError, ValueError) as exc:
            raise DispatcherError.from_spec("D56", operation=operation) from exc
        if result.returncode != 0 and not (
            allow_authorize_decline and result.returncode == 2
        ):
            raise DispatcherError.from_spec("D56", operation=operation)
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise DispatcherError.from_spec(
                "D57" if result.returncode == 0 else "D56",
                operation=operation,
            ) from exc
        if result.returncode == 0:
            if not isinstance(value, dict):
                raise DispatcherError.from_spec("D57", operation=operation)
            return value
        if (
            allow_authorize_decline
            and result.returncode == 2
            and declined(value)
        ):
            return None
        raise DispatcherError.from_spec("D56", operation=operation)

    def authorization_ready(value):
        return _exact_setup_value(value, dict(
            schema_version=1, flow_id=None, operation="authorize", state="ready",
            current_step=None, original=original, resume_original=True,
        ))

    response = manager(
        "authorize", [target, caller, target, str(version)],
        allow_authorize_decline=True,
    )
    if authorization_ready(response):
        return
    if response is not None:
        raise DispatcherError.from_spec("D60")
    status = manager("status", [target])
    if status.get("code") in ("setup_required", "setup_busy"):
        raise SetupBlocked(status, (target,))
    code, root = status.get("code"), status.get("root_setup_interface")
    if not _exact_setup_value(status, dict(schema_version=1, code=code, root_setup_interface=root, pending_stack=[], flow_id=None)) or not (code == "unmanaged" and root is None or code == "ready" and isinstance(root, str) and root):
        raise DispatcherError.from_spec("D58", operation="status")
    if code == "unmanaged":
        response = manager(
            "authorize", [target, caller, target, str(version)],
            allow_authorize_decline=True,
        )
        if authorization_ready(response):
            return
        raise DispatcherError.from_spec("D60")
    parse_interface_id(root)
    if not authorization_ready(manager(
        "authorize", [target, caller, target, str(version)],
        allow_authorize_decline=True,
    )):
        raise DispatcherError.from_spec("D60")


def _materialize(
    *,
    repository_config: Path,
    caller_module_id: str,
    target: str,
    args: list[str],
    stdin_requested: bool,
    target_version: int | None,
    host_caller: bool,
    check_setup: bool = False,
    setup_preflight_authorized: bool = False,
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
    if check_setup:
        _check_setup(
            authorized,
            setup_preflight_authorized=setup_preflight_authorized,
        )
    return materialize_authorized_invocation(
        authorized,
        argv=args,
        stdin_requested=stdin_requested,
        setup_preflight_authorized=setup_preflight_authorized,
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
    check_setup: bool = False,
    setup_preflight_authorized: bool = False,
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
        check_setup=check_setup,
        setup_preflight_authorized=setup_preflight_authorized,
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


@contextmanager
def _in_process_runtime_state(
    resolved: ResolvedInvocation,
    stdin: str | bytes | None,
    capture_output: bool,
) -> Iterator[tuple[TextIOWrapper | object, TextIOWrapper | object]]:
    """Apply one resolved runner's process globals for a bounded call."""

    saved_cwd = Path.cwd()
    saved_environment = os.environ.copy()
    saved_sys_path = list(sys.path)
    saved_meta_path = list(sys.meta_path)
    saved_stdin, saved_stdout, saved_stderr = sys.stdin, sys.stdout, sys.stderr
    stdout, stderr = (
        (
            TextIOWrapper(BytesIO(), encoding="utf-8", errors="strict", write_through=True),
            TextIOWrapper(BytesIO(), encoding="utf-8", errors="strict", write_through=True),
        )
        if capture_output
        else (saved_stdout, saved_stderr)
    )
    input_bytes = (
        b"" if stdin is None else stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    )
    input_stream = TextIOWrapper(BytesIO(input_bytes), encoding="utf-8", errors="strict")
    try:
        os.chdir(resolved.cwd)
        os.environ.clear()
        os.environ.update(resolved.env)
        import_paths = [
            Path(entry).resolve()
            for entry in resolved.env.get("PYTHONPATH", "").split(os.pathsep)
            if entry
        ]
        retained_paths = list(import_paths)
        retained_paths.extend(
            (
                Path(__file__).resolve().parents[2],
                Path(sys.prefix).resolve(),
                Path(sys.base_prefix).resolve(),
                Path(sys.exec_prefix).resolve(),
            )
        )
        retained_paths.extend(
            Path(entry).resolve()
            for entry in (*getattr(site, "getsitepackages", lambda: [])(), site.getusersitepackages())
        )
        confined_paths = [path.as_posix() for path in import_paths]
        retained_set = set(import_paths)
        for entry in saved_sys_path:
            if not entry:
                continue
            candidate = Path(entry).resolve()
            if (
                candidate not in retained_set
                and any(candidate == root or candidate.is_relative_to(root) for root in retained_paths)
            ):
                confined_paths.append(entry)
                retained_set.add(candidate)
        sys.path[:] = confined_paths
        sys.stdin, sys.stdout, sys.stderr = input_stream, stdout, stderr
        yield stdout, stderr
    finally:
        sys.stdin, sys.stdout, sys.stderr = saved_stdin, saved_stdout, saved_stderr
        sys.path[:] = saved_sys_path
        sys.meta_path[:] = saved_meta_path
        os.environ.clear()
        os.environ.update(saved_environment)
        os.chdir(saved_cwd)
        input_stream.close()


def _in_process_exit_code(error: SystemExit) -> int:
    """Match a child Python runner's ordinary ``sys.exit`` result."""

    return error.code if type(error.code) is int else 1


def _run_resolved_in_process(
    resolved: ResolvedInvocation,
    *,
    stdin: str | bytes | None,
    capture_output: bool,
    check: bool,
    text: bool | None,
) -> subprocess.CompletedProcess[Any]:
    """Run one Python gateway while the execution lock is held."""

    text_mode = text if text is not None else isinstance(stdin, str)
    if text_mode and isinstance(stdin, bytes):
        resolved.close()
        raise TypeError("text mode requires string stdin")
    if not text_mode and isinstance(stdin, str):
        resolved.close()
        raise TypeError("binary mode requires bytes stdin")

    diagnosis: DispatcherError | SetupBlocked | None = None

    def receive_diagnosis(
        entry_id: str | SetupBlocked,
        **context: object,
    ) -> int:
        nonlocal diagnosis
        diagnosis = (
            entry_id
            if isinstance(entry_id, SetupBlocked)
            else DispatcherError.from_spec(entry_id, **context)
        )
        return 70

    try:
        from officina.runtime import python_machine_interface_runner as runner

        with _in_process_runtime_state(
            resolved, stdin, capture_output
        ) as (
            stdout,
            stderr,
        ):
            try:
                exit_code = runner.main(
                    resolved.command[4:], diagnostic_handler=receive_diagnosis
                )
            except SystemExit as exc:
                exit_code = _in_process_exit_code(exc)
        if exit_code == 70:
            if isinstance(diagnosis, SetupBlocked) and len(diagnosis.call_path) < 32:
                raise SetupBlocked(
                    diagnosis.status,
                    (resolved.target, *diagnosis.call_path),
                    diagnosis.lifecycle,
                )
            if diagnosis is None or isinstance(diagnosis, SetupBlocked):
                raise DispatcherError.from_spec("D68")
            diagnosis.caller_module_id = resolved.caller_module_id
            diagnosis.target_module_id = resolved.target_module_id
            raise diagnosis

        if capture_output:
            assert isinstance(stdout, TextIOWrapper) and isinstance(stderr, TextIOWrapper)
            stdout.flush()
            stderr.flush()
            output_stdout, output_stderr = stdout.buffer.getvalue(), stderr.buffer.getvalue()
        else:
            output_stdout = output_stderr = None
        if text_mode and capture_output:
            try:
                output_stdout = output_stdout.decode("utf-8")
                output_stderr = output_stderr.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DispatcherError.from_spec(
                    "D71",
                    caller_module_id=resolved.caller_module_id,
                    target_module_id=resolved.target_module_id,
                    interface_id=resolved.target,
                ) from exc
        completed = _SUBPROCESS.CompletedProcess(
            resolved.command,
            exit_code,
            output_stdout,
            output_stderr,
        )
        if check and exit_code:
            raise DispatcherError.from_spec(
                "D70",
                caller_module_id=resolved.caller_module_id,
                target_module_id=resolved.target_module_id,
                interface_id=resolved.target,
                returncode=exit_code,
            )
        return completed
    finally:
        resolved.close()


@trace_process
def _run_resolved_invocation(
    resolved: ResolvedInvocation,
    *,
    stdin: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool | None = None,
    subprocess: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run one resolved command, optionally retaining subprocess isolation.

    The target module root becomes cwd and the precomputed confined environment
    replaces ambient repository import exposure. The default reuses the runner
    lifecycle in this process when its process-global state is available;
    ``subprocess=True`` (or another thread holding that state) retains
    child-process isolation and enforceable timeouts. In-process calls require
    cooperative interfaces: background threads and arbitrary process-global
    mutation cannot be isolated. Target exit failures are returned or raised
    according to ``check`` exactly as in ``subprocess.run``.
    """

    if not subprocess:
        if timeout is not None:
            resolved.close()
            raise ValueError("in-process execution cannot enforce a timeout; subprocess=True")
        if _IN_PROCESS_EXECUTION_LOCK.acquire(blocking=False):
            try:
                return _run_resolved_in_process(
                    resolved,
                    stdin=stdin,
                    capture_output=capture_output,
                    check=check,
                    text=text,
                )
            finally:
                _IN_PROCESS_EXECUTION_LOCK.release()

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
        "stdin": _SUBPROCESS.PIPE if stdin is not None else _SUBPROCESS.DEVNULL,
        "stdout": _SUBPROCESS.PIPE if capture_output else None,
        "stderr": _SUBPROCESS.PIPE if capture_output else None,
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
                startupinfo = _SUBPROCESS.STARTUPINFO()
                startupinfo.lpAttributeList = {"handle_list": [handle]}
                popen_kwargs.update(startupinfo=startupinfo, close_fds=True)
                with _WINDOWS_DIAGNOSTIC_LAUNCH_LOCK:
                    os.set_handle_inheritable(handle, True)
                    try:
                        process = _SUBPROCESS.Popen(command, **popen_kwargs)
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
                process = _SUBPROCESS.Popen(command, **popen_kwargs)
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
        except _SUBPROCESS.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=1)
            except _SUBPROCESS.TimeoutExpired:
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
        if process.returncode == 70:
            diagnosis = None if collector.is_alive() or overflow[0] else _registered_diagnosis(bytes(collected))
            if isinstance(diagnosis, SetupBlocked) and len(diagnosis.call_path) < 32:
                raise SetupBlocked(diagnosis.status, (resolved.target, *diagnosis.call_path), diagnosis.lifecycle)
            if diagnosis is None or isinstance(diagnosis, SetupBlocked):
                raise DispatcherError.from_spec("D68")
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
        completed = _SUBPROCESS.CompletedProcess(
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
