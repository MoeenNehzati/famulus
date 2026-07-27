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
    BlueprintNode,
    RepositoryBlueprintGraph,
    RuntimeFileBinding,
    descriptor_safe_open_supported,
    encode_runtime_python_package_snapshot,
    load_repository_blueprint_graph,
    open_runtime_python_package,
    resolve_export,
    snapshot_runtime_python_package,
)
from officina.common.blueprint_authorization import (
    AuthorizationRequest,
    AuthorizationResult,
    resolve_interface_authorization,
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
    logical_python_package_name,
)

class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


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


def _compatible_module_id(
    canonical: str | None,
    legacy: str | None,
    *,
    label: str,
) -> str:
    if canonical is not None and legacy is not None and canonical != legacy:
        raise TypeError(f"{label} and its legacy alias disagree")
    selected = canonical if canonical is not None else legacy
    if not isinstance(selected, str):
        raise TypeError(f"{label} is required")
    return selected


@dataclass(frozen=True, init=False)
class ResolvedInvocationMetadata:
    """Descriptor-free result for policy inspection, dry-run, and tracing."""

    caller_module_id: str
    target_module_id: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool
    python_target: PythonProcessTarget | None = None
    caller_source_id: str | None = None
    terminal_module_id: str | None = None
    implementing_source_id: str | None = None
    authorization: AuthorizationResult | None = None
    schema_version: int = 5

    def __init__(
        self,
        caller_module_id: str | None = None,
        target_module_id: str | None = None,
        script_interface: str = "",
        target: str = "",
        pattern: str = "",
        cwd: Path = Path(),
        command: list[str] | None = None,
        stdin: bool = False,
        python_target: PythonProcessTarget | None = None,
        caller_source_id: str | None = None,
        terminal_module_id: str | None = None,
        implementing_source_id: str | None = None,
        authorization: AuthorizationResult | None = None,
        schema_version: int = 5,
        *,
        caller_skill: str | None = None,
        target_skill: str | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "caller_module_id",
            _compatible_module_id(
                caller_module_id,
                caller_skill,
                label="caller_module_id",
            ),
        )
        object.__setattr__(
            self,
            "target_module_id",
            _compatible_module_id(
                target_module_id,
                target_skill,
                label="target_module_id",
            ),
        )
        for name, value in (
            ("script_interface", script_interface),
            ("target", target),
            ("pattern", pattern),
            ("cwd", cwd),
            ("command", [] if command is None else command),
            ("stdin", stdin),
            ("python_target", python_target),
            ("caller_source_id", caller_source_id),
            ("terminal_module_id", terminal_module_id),
            ("implementing_source_id", implementing_source_id),
            ("authorization", authorization),
            ("schema_version", schema_version),
        ):
            object.__setattr__(self, name, value)

    @property
    def caller_skill(self) -> str:
        """Temporary compatibility for v4 consumers."""

        return self.caller_module_id

    @property
    def target_skill(self) -> str:
        """Temporary compatibility for v4 consumers."""

        return self.target_module_id

    def as_payload(self) -> dict[str, Any]:
        payload = {
            "script_interface": self.script_interface,
            "target": self.target,
            "pattern": self.pattern,
            "cwd": str(self.cwd),
            "command": list(self.command),
            "stdin": self.stdin,
            "python_target": (
                (
                    {
                    "gateway_path": self.python_target.gateway_path.as_posix(),
                    "process_entry": self.python_target.process_entry,
                    }
                    | (
                        {
                            "logical_package": self.python_target.logical_package,
                            "logical_entrypoint": self.python_target.logical_entrypoint,
                        }
                        if self.python_target.logical_package is not None
                        and self.python_target.logical_entrypoint is not None
                        else {}
                    )
                )
                if self.python_target is not None
                else None
            ),
        }
        if self.schema_version == 5:
            payload.update(
                {
                    "caller_module_id": self.caller_module_id,
                    "caller_source_id": self.caller_source_id,
                    "target_module_id": self.target_module_id,
                    "terminal_module_id": self.terminal_module_id,
                    "implementing_source_id": self.implementing_source_id,
                }
            )
        else:
            payload.update(
                {
                    "caller_skill": self.caller_module_id,
                    "target_skill": self.target_module_id,
                }
            )
        return payload


@dataclass(frozen=True, init=False)
class ResolvedInvocation:
    """Concrete invocation selected from a skill blueprint."""

    caller_module_id: str
    target_module_id: str
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
    caller_source_id: str | None = None
    terminal_module_id: str | None = None
    implementing_source_id: str | None = None
    authorization: AuthorizationResult | None = None
    schema_version: int = 5

    def __init__(
        self,
        caller_module_id: str | None = None,
        target_module_id: str | None = None,
        script_interface: str = "",
        target: str = "",
        pattern: str = "",
        cwd: Path = Path(),
        command: list[str] | None = None,
        stdin: bool = False,
        python_target: PythonProcessTarget | None = None,
        env: dict[str, str] | None = None,
        runtime_bindings: tuple[RuntimeFileBinding, ...] = (),
        runtime_snapshots: tuple[_RuntimeSnapshotTransport, ...] = (),
        caller_source_id: str | None = None,
        terminal_module_id: str | None = None,
        implementing_source_id: str | None = None,
        authorization: AuthorizationResult | None = None,
        schema_version: int = 5,
        *,
        caller_skill: str | None = None,
        target_skill: str | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "caller_module_id",
            _compatible_module_id(
                caller_module_id,
                caller_skill,
                label="caller_module_id",
            ),
        )
        object.__setattr__(
            self,
            "target_module_id",
            _compatible_module_id(
                target_module_id,
                target_skill,
                label="target_module_id",
            ),
        )
        for name, value in (
            ("script_interface", script_interface),
            ("target", target),
            ("pattern", pattern),
            ("cwd", cwd),
            ("command", [] if command is None else command),
            ("stdin", stdin),
            ("python_target", python_target),
            ("env", env),
            ("runtime_bindings", runtime_bindings),
            ("runtime_snapshots", runtime_snapshots),
            ("caller_source_id", caller_source_id),
            ("terminal_module_id", terminal_module_id),
            ("implementing_source_id", implementing_source_id),
            ("authorization", authorization),
            ("schema_version", schema_version),
        ):
            object.__setattr__(self, name, value)

    @property
    def caller_skill(self) -> str:
        return self.caller_module_id

    @property
    def target_skill(self) -> str:
        return self.target_module_id

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
            "--logical-package": 1,
            "--logical-entrypoint": 1,
            "--physical-package-prefix": 1,
            "--runtime-caller-module-id": 1,
            "--runtime-caller-source-id": 1,
            "--runtime-repo-root": 1,
        }
        try:
            index = command.index(
                "officina.runtime.python_machine_interface_runner"
            ) + 1
        except ValueError:
            index = len(command)
        if index < len(command):
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
            caller_module_id=self.caller_module_id,
            target_module_id=self.target_module_id,
            script_interface=self.script_interface,
            target=self.target,
            pattern=self.pattern,
            cwd=self.cwd,
            command=self._logical_command(),
            stdin=self.stdin,
            python_target=self.python_target,
            caller_source_id=self.caller_source_id,
            terminal_module_id=self.terminal_module_id,
            implementing_source_id=self.implementing_source_id,
            authorization=self.authorization,
            schema_version=self.schema_version,
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
    target_module_id: str,
    schema_version: int,
    interface_id: str,
    gateway: dict[str, Any],
    script_args: list[str],
    repo_root: Path | None = None,
    runtime_caller_module_id: str | None = None,
    runtime_caller_source_id: str | None = None,
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
        raise InvocationError(
            f"{interface_id}: Python gateway needs non-empty `path`"
        )
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvocationError(
            f"{interface_id}: Python gateway needs non-empty `symbol`"
        )
    module_path = Path(path)
    root = get_repo_root(repo_root)
    args_prefix = gateway.get("args_prefix", [])
    if not isinstance(args_prefix, list) or not all(
        isinstance(token, str) and token for token in args_prefix
    ):
        raise InvocationError(
            f"{interface_id}: Python gateway needs string list `args_prefix`"
        )
    env = os.environ.copy()
    src_root = root / "src"
    current = env.get("PYTHONPATH")
    if schema_version == 4:
        entries = [str(module_root), str(src_root)]
        env["PYTHONPATH"] = os.pathsep.join(
            entries + ([current] if current else [])
        )
    else:
        physical_root = module_root.resolve()
        inherited_entries = (
            current.split(os.pathsep) if current is not None else []
        )

        def outside_module_root(entry: str) -> bool:
            candidate = Path(entry)
            if not entry or not candidate.is_absolute():
                return False
            try:
                resolved = candidate.resolve()
            except OSError:
                return False
            return not (
                resolved == physical_root
                or resolved.is_relative_to(physical_root)
            )

        env["PYTHONPATH"] = os.pathsep.join(
            [str(src_root.resolve())]
            + [
                entry
                for entry in inherited_entries
                if outside_module_root(entry)
            ]
        )
    env["PYTHONIOENCODING"] = "utf-8:strict"
    source_path = Path(os.path.abspath(module_root / module_path))
    package_bindings: tuple[RuntimeFileBinding, ...] = ()
    snapshot_transports: tuple[_RuntimeSnapshotTransport, ...] = ()
    package_arguments: list[str] = []
    source_arguments: list[str] = []
    runtime_context_arguments: list[str] = []
    logical_package: str | None = None
    logical_entrypoint: str | None = None
    physical_package_prefix: str | None = None
    package_root = module_root / "_rtx"
    snapshot_root = module_root
    if schema_version == 5:
        logical_package = logical_python_package_name(target_module_id)
        physical_parts = (
            module_path.parent.parts
            if module_path.name == "__init__.py"
            else (*module_path.parent.parts, module_path.stem)
        )
        suffix = ".".join(
            part for part in physical_parts if part not in {"", "."}
        )
        logical_entrypoint = (
            logical_package
            if not suffix
            else f"{logical_package}.{suffix}"
        )
        physical_package_prefix = module_root.name
        package_root = module_root
        snapshot_root = module_root.parent
        if runtime_caller_module_id is not None:
            runtime_context_arguments.extend(
                [
                    "--runtime-caller-module-id",
                    runtime_caller_module_id,
                ]
            )
        if runtime_caller_source_id is not None:
            runtime_context_arguments.extend(
                [
                    "--runtime-caller-source-id",
                    runtime_caller_source_id,
                ]
            )
        runtime_context_arguments.extend(
            [
                "--runtime-repo-root",
                root.as_posix(),
            ]
        )
    try:
        python_target = PythonProcessTarget(
            module_path,
            symbol,
            logical_package=logical_package,
            logical_entrypoint=logical_entrypoint,
        )
    except PythonProcessTargetError as exc:
        raise InvocationError(f"{interface_id}: {exc}") from exc
    try:
        if descriptor_safe_open_supported():
            package_bindings = open_runtime_python_package(
                package_root,
                snapshot_root,
                root,
            )
            source_binding = next(
                (binding for binding in package_bindings if binding.path == source_path),
                None,
            )
            if source_binding is None:
                for binding in package_bindings:
                    binding.close()
                raise InvocationError(
                    f"{interface_id}: gateway is not a regular Python package source: "
                    f"{module_path.as_posix()}"
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
                        snapshot_root,
                    ).as_posix(),
                )
            ]
        else:
            snapshots = snapshot_runtime_python_package(
                package_root,
                snapshot_root,
                root,
                allow_non_atomic=False,
            )
            if not any(path == source_path for path, _source in snapshots):
                raise InvocationError(
                    f"{interface_id}: gateway is not a regular Python package source: "
                    f"{module_path.as_posix()}"
                )
            transport = _create_runtime_snapshot_transport(
                snapshots,
                snapshot_root,
            )
            snapshot_transports = (transport,)
            package_arguments = [
                "--package-snapshot",
                str(transport.path),
                "--package-snapshot-sha256",
                transport.sha256,
            ]
    except (BlueprintGraphError, OSError) as exc:
        raise InvocationError(f"{interface_id}: {exc}") from exc
    return (
        module_root,
        [
            sys.executable,
            *(["-P"] if schema_version == 5 else []),
            "-m",
            "officina.runtime.python_machine_interface_runner",
            *(
                [
                    "--logical-package",
                    logical_package,
                    "--logical-entrypoint",
                    logical_entrypoint,
                    "--physical-package-prefix",
                    physical_package_prefix,
                ]
                if logical_package is not None
                and logical_entrypoint is not None
                and physical_package_prefix is not None
                else []
            ),
            *source_arguments,
            *package_arguments,
            *runtime_context_arguments,
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


def _host_gateway_source_id(
    graph: RepositoryBlueprintGraph,
    module: BlueprintNode,
) -> str:
    """Return the one behavioral source that owns a host skill gateway."""

    matches = tuple(
        source_id
        for source_id in graph.module_sources.get(module.node_id, ())
        if graph.nodes[source_id].gateway_path == module.gateway_path
    )
    if len(matches) != 1:
        raise InvocationError(
            f"caller module `{module.node_id}` has {len(matches)} host gateway "
            "sources; expected exactly one"
        )
    return matches[0]


def _resolve_export_dispatch(
    *,
    root: Path,
    caller_skill: str,
    caller_source_id: str | None,
    target: str,
    args: list[str],
    stdin_requested: bool,
    target_version: int | None,
    certification_view: CertificationView | None,
    graph: RepositoryBlueprintGraph | None = None,
    host_caller: bool = False,
) -> ResolvedInvocation | None:
    """Resolve one v4 repository-graph export."""

    if graph is None:
        diagnostic_inventory = collect_blueprints(root, skip_parse_errors=True)
        target_is_module = False
        target_is_export = False
        for document in diagnostic_inventory.documents:
            schema_version = document.declaration.get("schema_version")
            if schema_version in {4, 5} and document.node_type == "module":
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
            raise InvocationError(
                f"{root / first.relative_path}: cannot load blueprint YAML: {first.message}"
            ) from exc
        except BlueprintGraphError as exc:
            raise InvocationError(f"repository blueprint graph is invalid: {exc}") from exc
    if target in graph.nodes and graph.nodes[target].node_type == "module":
        raise InvocationError(f"module id `{target}` is not callable")
    if target not in graph.exports:
        return None
    try:
        export = graph.exports[target]
        module, source, export = resolve_export(
            graph,
            target,
            None if graph.schema_version == 5 else target_version,
        )
        caller_module = graph.nodes.get(caller_skill)
        if caller_module is None or caller_module.node_type != "module":
            raise InvocationError(
                f"caller module `{caller_skill}` does not exist"
            )
        if graph.schema_version == 5 and host_caller:
            discovery = caller_module.declaration.get("discovery")
            if (
                not isinstance(discovery, dict)
                or discovery.get("mechanism") != "skill"
            ):
                raise InvocationError(
                    f"caller module `{caller_skill}` is not a discoverable host skill"
                )
            if caller_source_id is None:
                caller_source_id = _host_gateway_source_id(
                    graph,
                    caller_module,
                )
        terminal_module_id = module.node_id
        implementing_source_id = source.node_id
        authorization: AuthorizationResult | None = None
        if graph.schema_version == 5:
            requested_version = (
                export.version if target_version is None else target_version
            )
            authorization = resolve_interface_authorization(
                graph,
                AuthorizationRequest(
                    caller_module_id=caller_skill,
                    caller_source_id=caller_source_id,
                    interface_id=export.interface_id,
                    version=requested_version,
                ),
            )
            if not authorization.allowed:
                raise InvocationError(
                    f"{target}: authorization rejected "
                    f"[{authorization.diagnostic}]"
                )
            if (
                authorization.terminal_module_id is None
                or authorization.implementing_source_id is None
            ):
                raise InvocationError(
                    f"{target}: authorization returned no runtime target"
                )
            terminal_module_id = authorization.terminal_module_id
            implementing_source_id = authorization.implementing_source_id
        else:
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
                raise InvocationError(
                    f"caller module `{caller_skill}` does not declare use of "
                    f"`{export.interface_id}` version {export.version} "
                    "in a contained source"
                )
            access = (
                export.export_declaration.get("access")
                if export.export_declaration
                else None
            )
            if not isinstance(access, dict):
                raise InvocationError(f"{target}: export access is missing")
            allowed = access.get("allowed_callers", [])
            if (
                caller_skill != module.node_id
                and access.get("allow_all_modules") is not True
                and caller_skill not in allowed
            ):
                raise InvocationError(
                    f"caller module `{caller_skill}` is not allowed to call "
                    f"`{target}`"
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
        check_authorization = getattr(
            selected_view,
            "check_authorization",
            None,
        )
        if authorization is not None and callable(check_authorization):
            decision = check_authorization(authorization)
        else:
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
                    target_module_id=module.node_id,
                    terminal_module_id=(
                        authorization.terminal_module_id
                        if authorization is not None
                        else module.node_id
                    ),
                    interface_id=export.interface_id,
                    pattern_name=compiled.pattern_name,
                    argv=compiled.argv,
                )
        if not decision.certified:
            raise InvocationError(
                f"{export.interface_id}: certification rejected "
                f"[{decision.code}]: {decision.message}"
            )
    except (BlueprintGraphError, ProcessBindingError) as exc:
        raise InvocationError(str(exc)) from exc

    target_module_id = module.node_id
    gateway = source.declaration.get("gateway")
    language = gateway.get("language") if isinstance(gateway, dict) else None
    language_name = gateway_language_name(language) if isinstance(language, str) else None
    if language_name != "Python":
        raise InvocationError(
            f"{export.interface_id}: unsupported process binding language {language!r}"
        )
    if source.gateway_path is None or compiled.entry is None:
        raise InvocationError(
            f"{export.interface_id}: Python process binding requires a gateway and entry"
        )
    try:
        gateway_path = equivalent_root_relative_path(
            source.gateway_path,
            source.module_root,
        ).as_posix()
    except RepositoryPathError as exc:
        raise InvocationError(
            f"{export.interface_id}: gateway must remain inside its module"
        ) from exc
    (
        cwd,
        command,
        env,
        runtime_bindings,
        runtime_snapshots,
        python_target,
    ) = _build_python_runtime(
        source.module_root,
        terminal_module_id,
        graph.schema_version,
        export.interface_id,
        {
            "path": gateway_path,
            "symbol": compiled.entry,
            "args_prefix": [],
        },
        list(compiled.argv),
        repo_root=root,
        runtime_caller_module_id=terminal_module_id,
        runtime_caller_source_id=implementing_source_id,
    )
    return ResolvedInvocation(
        caller_module_id=caller_skill,
        target_module_id=target_module_id,
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
        caller_source_id=caller_source_id,
        terminal_module_id=terminal_module_id,
        implementing_source_id=implementing_source_id,
        authorization=authorization,
        schema_version=graph.schema_version,
    )


def _resolve_dispatch(
    *,
    caller_skill: str,
    caller_source_id: str | None = None,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    certification_view: CertificationView | None = None,
    graph: RepositoryBlueprintGraph | None = None,
    host_caller: bool = False,
) -> ResolvedInvocation:
    args = args or []
    if not caller_skill.strip():
        raise InvocationError("caller_skill must be a non-empty string")
    caller_skill = caller_skill.strip()

    root = get_repo_root(repo_root)
    if not isinstance(target, str) or _EXPORT_TARGET_RE.fullmatch(target) is None:
        raise InvocationError(
            "target must have form `<module>.interface.<name>`"
        )
    resolved = _resolve_export_dispatch(
        root=root,
        caller_skill=caller_skill,
        caller_source_id=caller_source_id,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
        host_caller=host_caller,
    )
    if resolved is None:
        raise InvocationError(f"unknown exported interface `{target}`")
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
    """Resolve one certified host-skill request."""

    return _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
        certification_view=None,
        host_caller=True,
    )


def _resolve_dispatch_metadata_for_trace(
    *,
    caller_module_id: str,
    caller_source_id: str | None = None,
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
        caller_skill=caller_module_id,
        caller_source_id=caller_source_id,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
    ) as resolved:
        return resolved.metadata()


def _resolve_host_dispatch_metadata(
    *,
    caller_skill: str,
    target: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
    target_version: int | None = None,
    certification_view: CertificationView | None = None,
    graph: RepositoryBlueprintGraph | None = None,
) -> ResolvedInvocationMetadata:
    """Resolve one host request, admitting only discoverable v5 parents."""

    with _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        repo_root=repo_root,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
        host_caller=True,
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


def _run_resolved_invocation(
    resolved: ResolvedInvocation,
    *,
    stdin: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool | None = None,
) -> subprocess.CompletedProcess[Any]:
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
            raise InvocationError(
                f"{resolved.target}: launch failed: {exc}"
            ) from exc
    finally:
        resolved.close()


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
    """Resolve and execute a declared module interface."""

    resolved = resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args or [],
        stdin_requested=stdin is not None,
        repo_root=repo_root,
        target_version=target_version,
    )
    return _run_resolved_invocation(
        resolved,
        stdin=stdin,
        timeout=timeout,
        capture_output=capture_output,
        check=check,
        text=text,
    )


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
) -> subprocess.CompletedProcess[Any]:
    """Resolve and execute a host request from a discoverable parent skill."""

    resolved = _resolve_dispatch(
        caller_skill=caller_skill,
        target=target,
        args=args or [],
        stdin_requested=stdin is not None,
        repo_root=repo_root,
        target_version=target_version,
        host_caller=True,
    )
    return _run_resolved_invocation(
        resolved,
        stdin=stdin,
        timeout=timeout,
        capture_output=capture_output,
        check=check,
        text=text,
    )
