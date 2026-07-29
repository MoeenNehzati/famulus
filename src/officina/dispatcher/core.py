"""Shared resolution and execution logic for skill dispatcher interfaces."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from officina.common.blueprint_graph import (
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    RuntimeFileBinding,
    descriptor_safe_open_supported,
    encode_runtime_python_package_snapshot,
    load_repository_blueprint_graph,
    open_runtime_python_package,
    resolve_export,
    snapshot_runtime_python_package,
)
from officina.common.certification_view import (
    CertificationView,
    RejectingCertificationView,
    RepositoryCertificationError,
    repository_certification_view,
)
from officina.common.process_binding_compiler import (
    ProcessBindingError,
    compile_gateway_invocation,
    compile_route_smoke_invocation,
    gateway_language_name,
    parse_caller_invocation,
)
from officina.common.blueprint_inventory import BlueprintInventoryError, collect_blueprints
from officina.common.repository_paths import (
    RepositoryPathError,
    equivalent_root_relative_path,
)
from officina.runtime.python_machine_interface import (
    PythonProcessTarget,
    PythonProcessTargetError,
)
from officina.dispatcher.errors import (
    BlueprintInvalidError,
    CallerNotFoundError,
    CertificationRejectedError,
    ExportAccessMissingError,
    GatewayOutsideModuleError,
    InterfaceNotFoundError,
    InterfaceUseUndeclaredError,
    InvalidRequestError,
    InvocationError,
    LaunchFailedError,
    ModuleNotCallableError,
    ResolutionFailedError,
    RuntimeInvalidError,
    RuntimeMisconfiguredError,
    UnauthorizedCallerError,
    UnsupportedLanguageError,
)

# Re-exported for existing consumers that import `InvocationError` (and the
# typed subclasses) from `officina.dispatcher.core` directly; the classes
# themselves are defined in `errors.py`, which has no dependency on this
# module, so there is no import cycle.


def _target_module_id(target: str) -> str:
    """Best-effort module id parsed from a `<module>.interface.<name>` target."""
    return target.split(".interface.", 1)[0] if ".interface." in target else target


_EXPORT_TARGET_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*\.interface\.[a-z0-9]+(?:-[a-z0-9]+)*$"
)


class _RuntimeSnapshotTransport:
    """One private snapshot file owned by a resolved invocation."""

    def __init__(self, path: Path, sha256: str) -> None:
        self.path = path
        self.sha256 = sha256
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self.path.unlink(missing_ok=True)
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


@dataclass(frozen=True)
class ResolvedInvocationMetadata:
    """Descriptor-free result for policy inspection, dry-run, and tracing."""

    caller_skill: str
    target_skill: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool
    python_target: PythonProcessTarget | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "caller_skill": self.caller_skill,
            "target_skill": self.target_skill,
            "script_interface": self.script_interface,
            "target": self.target,
            "pattern": self.pattern,
            "cwd": str(self.cwd),
            "command": list(self.command),
            "stdin": self.stdin,
            "python_target": (
                {
                    "gateway_path": self.python_target.gateway_path.as_posix(),
                    "process_entry": self.python_target.process_entry,
                }
                if self.python_target is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ResolvedInvocation:
    """Concrete invocation selected from a skill blueprint."""

    caller_skill: str
    target_skill: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool
    python_target: PythonProcessTarget | None = None
    env: dict[str, str] | None = None
    runtime_bindings: tuple[RuntimeFileBinding, ...] = ()
    runtime_snapshots: tuple[_RuntimeSnapshotTransport, ...] = ()

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(binding.fd for binding in self.runtime_bindings if binding.fd >= 0)

    def close(self) -> None:
        first_error: OSError | None = None
        for resource in (*self.runtime_bindings, *self.runtime_snapshots):
            try:
                resource.close()
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "ResolvedInvocation":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _logical_command(self) -> list[str]:
        command = list(self.command)
        private_options = {
            "--source-fd": 1,
            "--package-file": 2,
            "--package-snapshot": 1,
            "--package-snapshot-sha256": 1,
        }
        if len(command) >= 4:
            index = 3
            while index < len(command) and command[index] in private_options:
                width = private_options[command[index]]
                del command[index : index + width + 1]
        if self.runtime_bindings:
            binding = self.runtime_bindings[0]
            if command and command[0].startswith("/proc/self/fd/"):
                command[0] = str(binding.path)
        return command

    def metadata(self) -> ResolvedInvocationMetadata:
        return ResolvedInvocationMetadata(
            caller_skill=self.caller_skill,
            target_skill=self.target_skill,
            script_interface=self.script_interface,
            target=self.target,
            pattern=self.pattern,
            cwd=self.cwd,
            command=self._logical_command(),
            stdin=self.stdin,
            python_target=self.python_target,
        )

    def as_payload(self) -> dict[str, Any]:
        return self.metadata().as_payload()


def get_repo_root(repo_root: Path | None = None) -> Path:
    """Resolve the AI repo root, preferring the installer-managed AI env var."""
    if repo_root is not None:
        return repo_root.resolve()

    env_root = os.environ.get("AI")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "skills").is_dir() and (candidate / "src").is_dir():
            return candidate

    return Path(__file__).resolve().parents[3]


def _create_runtime_snapshot_transport(
    snapshots: tuple[tuple[Path, bytes], ...],
    module_root: Path,
) -> _RuntimeSnapshotTransport:
    payload = encode_runtime_python_package_snapshot(snapshots, module_root)
    digest = hashlib.sha256(payload).hexdigest()
    descriptor, raw_path = tempfile.mkstemp(
        prefix="officina-python-snapshot-",
        suffix=".json",
    )
    path = Path(raw_path)
    try:
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return _RuntimeSnapshotTransport(path, digest)


def _build_python_runtime(
    module_root: Path,
    interface_id: str,
    gateway: dict[str, Any],
    script_args: list[str],
    repo_root: Path | None = None,
) -> tuple[
    Path,
    list[str],
    dict[str, str] | None,
    tuple[RuntimeFileBinding, ...],
    tuple[_RuntimeSnapshotTransport, ...],
    PythonProcessTarget,
]:
    path = gateway.get("path")
    symbol = gateway.get("symbol")
    if not isinstance(path, str) or not path.strip():
        raise RuntimeInvalidError(
            f"{interface_id}: Python gateway needs non-empty `path`",
            target_module_id=_target_module_id(interface_id),
        )
    if not isinstance(symbol, str) or not symbol.strip():
        raise RuntimeInvalidError(
            f"{interface_id}: Python gateway needs non-empty `symbol`",
            target_module_id=_target_module_id(interface_id),
        )
    try:
        python_target = PythonProcessTarget(Path(path), symbol)
    except PythonProcessTargetError as exc:
        raise RuntimeInvalidError(
            f"{interface_id}: {exc}",
            target_module_id=_target_module_id(interface_id),
        ) from exc
    root = get_repo_root(repo_root)
    args_prefix = gateway.get("args_prefix", [])
    if not isinstance(args_prefix, list) or not all(
        isinstance(token, str) and token for token in args_prefix
    ):
        raise RuntimeInvalidError(
            f"{interface_id}: Python gateway needs string list `args_prefix`",
            target_module_id=_target_module_id(interface_id),
        )
    env = os.environ.copy()
    src_root = root / "src"
    entries = [str(module_root), str(src_root)]
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(entries + ([current] if current else []))
    env["PYTHONIOENCODING"] = "utf-8:strict"
    module_path = python_target.gateway_path
    source_path = Path(os.path.abspath(module_root / module_path))
    package_bindings: tuple[RuntimeFileBinding, ...] = ()
    snapshot_transports: tuple[_RuntimeSnapshotTransport, ...] = ()
    package_arguments: list[str] = []
    source_arguments: list[str] = []
    try:
        if descriptor_safe_open_supported():
            package_bindings = open_runtime_python_package(
                module_root / "_rtx",
                module_root,
                root,
            )
            source_binding = next(
                (binding for binding in package_bindings if binding.path == source_path),
                None,
            )
            if source_binding is None:
                for binding in package_bindings:
                    binding.close()
                raise RuntimeInvalidError(
                    f"{interface_id}: gateway is not a regular Python package source: "
                    f"{module_path.as_posix()}",
                    target_module_id=_target_module_id(interface_id),
                )
            source_arguments = ["--source-fd", str(source_binding.fd)]
            package_arguments = [
                token
                for binding in package_bindings
                for token in (
                    "--package-file",
                    str(binding.fd),
                    equivalent_root_relative_path(
                        binding.path,
                        module_root,
                    ).as_posix(),
                )
            ]
        else:
            snapshots = snapshot_runtime_python_package(
                module_root / "_rtx",
                module_root,
                root,
                allow_non_atomic=False,
            )
            if not any(path == source_path for path, _source in snapshots):
                raise RuntimeInvalidError(
                    f"{interface_id}: gateway is not a regular Python package source: "
                    f"{module_path.as_posix()}",
                    target_module_id=_target_module_id(interface_id),
                )
            transport = _create_runtime_snapshot_transport(snapshots, module_root)
            snapshot_transports = (transport,)
            package_arguments = [
                "--package-snapshot",
                str(transport.path),
                "--package-snapshot-sha256",
                transport.sha256,
            ]
    except (BlueprintGraphError, OSError) as exc:
        raise RuntimeInvalidError(
            f"{interface_id}: {exc}",
            target_module_id=_target_module_id(interface_id),
        ) from exc
    return (
        module_root,
        [
            sys.executable,
            "-m",
            "officina.runtime.python_machine_interface_runner",
            *source_arguments,
            *package_arguments,
            python_target.gateway_path.as_posix(),
            python_target.process_entry,
            *args_prefix,
            *script_args,
        ],
        env,
        package_bindings,
        snapshot_transports,
        python_target,
    )


def _resolve_export_dispatch(
    *,
    root: Path,
    caller_skill: str,
    target: str,
    args: list[str],
    stdin_requested: bool,
    target_version: int | None,
    certification_view: CertificationView | None,
    graph: RepositoryBlueprintGraph | None = None,
) -> ResolvedInvocation | None:
    """Resolve one v4 repository-graph export."""

    if graph is None:
        diagnostic_inventory = collect_blueprints(root, skip_parse_errors=True)
        target_is_module = False
        target_is_export = False
        for document in diagnostic_inventory.documents:
            schema_version = document.declaration.get("schema_version")
            if schema_version == 4 and document.node_type == "module":
                if document.node_id == target:
                    target_is_module = True
                raw_exports = document.declaration.get("exports", {})
                if isinstance(raw_exports, dict) and target in raw_exports:
                    target_is_export = True
        if not target_is_module and not target_is_export:
            return None
        try:
            graph = load_repository_blueprint_graph(root)
        except BlueprintInventoryError as exc:
            first = exc.issues[0]
            raise BlueprintInvalidError(
                f"{root / first.relative_path}: cannot load blueprint YAML: {first.message}",
                caller_module_id=caller_skill,
                target_module_id=_target_module_id(target),
            ) from exc
        except BlueprintGraphError as exc:
            raise BlueprintInvalidError(
                f"repository blueprint graph is invalid: {exc}",
                caller_module_id=caller_skill,
                target_module_id=_target_module_id(target),
            ) from exc
    if target in graph.nodes and graph.nodes[target].node_type == "module":
        raise ModuleNotCallableError(
            f"module id `{target}` is not callable",
            caller_module_id=caller_skill,
            target_module_id=target,
        )
    if target not in graph.exports:
        return None
    try:
        export = graph.exports[target]
        module, source, export = resolve_export(graph, target, target_version)
        caller_module = graph.nodes.get(caller_skill)
        if caller_module is None or caller_module.node_type != "module":
            raise CallerNotFoundError(
                f"caller module `{caller_skill}` does not exist",
                caller_module_id=caller_skill,
                target_module_id=_target_module_id(target),
            )
        declares_exact_use = False
        for source_id in graph.module_sources.get(caller_skill, ()):
            caller_source = graph.nodes.get(source_id)
            uses = (
                caller_source.declaration.get("uses_interfaces", [])
                if caller_source is not None
                else []
            )
            if not isinstance(uses, list):
                continue
            if any(
                isinstance(use, dict)
                and use.get("interface") == export.interface_id
                and use.get("version") == export.version
                for use in uses
            ):
                declares_exact_use = True
                break
        if caller_skill != module.node_id and not declares_exact_use:
            raise InterfaceUseUndeclaredError(
                f"caller module `{caller_skill}` does not declare use of "
                f"`{export.interface_id}` version {export.version} in a contained source",
                caller_module_id=caller_skill,
                target_module_id=module.node_id,
            )
        access = export.export_declaration.get("access") if export.export_declaration else None
        if not isinstance(access, dict):
            raise ExportAccessMissingError(
                f"{target}: export access is missing",
                caller_module_id=caller_skill,
                target_module_id=module.node_id,
            )
        allowed = access.get("allowed_callers", [])
        if (
            caller_skill != module.node_id
            and access.get("allow_all_modules") is not True
            and caller_skill not in allowed
        ):
            raise UnauthorizedCallerError(
                caller_module_id=caller_skill,
                target_module_id=module.node_id,
                interface_id=target,
            )
        if args == ["--route-smoke"] and not stdin_requested:
            compiled = compile_route_smoke_invocation(source, export)
        else:
            parsed = parse_caller_invocation(
                export,
                args,
                stdin_requested=stdin_requested,
            )
            compiled = compile_gateway_invocation(source, export, parsed)
        selected_view: CertificationView
        if certification_view is not None:
            selected_view = certification_view
        else:
            try:
                selected_view = repository_certification_view(root)
            except RepositoryCertificationError:
                selected_view = RejectingCertificationView()
        decision = selected_view.check_export(
            module.node_id,
            export.interface_id,
            export.version,
            export.source_node_id,
        )
        if not decision.certified:
            check_bootstrap = getattr(selected_view, "check_bootstrap", None)
            if callable(check_bootstrap):
                decision = check_bootstrap(
                    caller_module_id=caller_skill,
                    interface_id=export.interface_id,
                    pattern_name=compiled.pattern_name,
                    argv=compiled.argv,
                )
        if not decision.certified:
            raise CertificationRejectedError(
                f"{export.interface_id}: certification rejected "
                f"[{decision.code}]: {decision.message}",
                caller_module_id=caller_skill,
                target_module_id=module.node_id,
            )
    except (BlueprintGraphError, ProcessBindingError) as exc:
        raise ResolutionFailedError(
            str(exc),
            caller_module_id=caller_skill,
            target_module_id=_target_module_id(target),
        ) from exc

    target_skill = module.skill_root.name
    gateway = source.declaration.get("gateway")
    language = gateway.get("language") if isinstance(gateway, dict) else None
    language_name = gateway_language_name(language) if isinstance(language, str) else None
    if language_name != "Python":
        raise UnsupportedLanguageError(
            f"{export.interface_id}: unsupported process binding language {language!r}",
            caller_module_id=caller_skill,
            target_module_id=module.node_id,
        )
    if source.gateway_path is None or compiled.entry is None:
        raise RuntimeMisconfiguredError(
            f"{export.interface_id}: Python process binding requires a gateway and entry",
            caller_module_id=caller_skill,
            target_module_id=module.node_id,
        )
    try:
        gateway_path = equivalent_root_relative_path(
            source.gateway_path,
            source.skill_root,
        ).as_posix()
    except RepositoryPathError as exc:
        raise GatewayOutsideModuleError(
            f"{export.interface_id}: gateway must remain inside its module",
            caller_module_id=caller_skill,
            target_module_id=module.node_id,
        ) from exc
    (
        cwd,
        command,
        env,
        runtime_bindings,
        runtime_snapshots,
        python_target,
    ) = _build_python_runtime(
        source.skill_root,
        export.interface_id,
        {
            "path": gateway_path,
            "symbol": compiled.entry,
            "args_prefix": [],
        },
        list(compiled.argv),
        repo_root=root,
    )
    return ResolvedInvocation(
        caller_skill=caller_skill,
        target_skill=target_skill,
        script_interface=export.local_name,
        target=export.interface_id,
        pattern=compiled.pattern_name or export.local_name,
        cwd=cwd,
        command=command,
        stdin=compiled.stdin_argument_id is not None,
        python_target=python_target,
        env=env,
        runtime_bindings=runtime_bindings,
        runtime_snapshots=runtime_snapshots,
    )


def _resolve_dispatch(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    certification_view: CertificationView | None = None,
    graph: RepositoryBlueprintGraph | None = None,
) -> ResolvedInvocation:
    args = args or []
    if not caller_skill.strip():
        raise InvalidRequestError(
            "caller_skill must be a non-empty string",
        )
    caller_skill = caller_skill.strip()

    root = get_repo_root(repo_root)
    if not isinstance(target, str) or _EXPORT_TARGET_RE.fullmatch(target) is None:
        raise InvalidRequestError(
            "target must have form `<module>.interface.<name>`",
            caller_module_id=caller_skill,
        )
    resolved = _resolve_export_dispatch(
        root=root,
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
    )
    if resolved is None:
        raise InterfaceNotFoundError(
            caller_module_id=caller_skill,
            target_module_id=_target_module_id(target),
            interface_id=target,
        )
    return resolved


def resolve_dispatch(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
) -> ResolvedInvocation:
    """Resolve one certified, fully qualified v4 module export."""

    return _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
        certification_view=None,
    )


def _resolve_dispatch_metadata_for_trace(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    certification_view: CertificationView,
    graph: RepositoryBlueprintGraph | None = None,
) -> ResolvedInvocationMetadata:
    """Private route-smoke resolver with a trace-only certification view."""

    with _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
    ) as resolved:
        return resolved.metadata()


def resolve_dispatch_metadata(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
) -> ResolvedInvocationMetadata:
    """Resolve policy and return metadata after deterministically closing bindings."""

    with resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
    ) as resolved:
        return resolved.metadata()


def dispatch(
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
) -> subprocess.CompletedProcess[Any]:
    """Resolve and execute a declared skill interface."""
    resolved = resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args or [],
        stdin_requested=stdin is not None,
        repo_root=repo_root,
        target_version=target_version,
    )

    run_kwargs: dict[str, Any] = {
        "cwd": resolved.cwd,
        "capture_output": capture_output,
        "check": check,
    }
    if resolved.env is not None:
        run_kwargs["env"] = resolved.env
    if resolved.pass_fds:
        run_kwargs["pass_fds"] = resolved.pass_fds
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if stdin is not None:
        run_kwargs["input"] = stdin
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
                caller_module_id=caller_skill,
                target_module_id=_target_module_id(resolved.target),
            ) from exc
    finally:
        resolved.close()
