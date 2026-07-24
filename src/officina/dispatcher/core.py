"""Shared resolution and execution logic for skill dispatcher interfaces."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from officina.common.blueprint_graph import (
    BlueprintGraphError,
    RepositoryBlueprintGraph,
    RuntimeFileBinding,
    load_repository_blueprint_graph,
    open_runtime_python_package,
    resolve_export,
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

class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


_EXPORT_TARGET_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*\.interface\.[a-z0-9]+(?:-[a-z0-9]+)*$"
)


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
    env: dict[str, str] | None = None
    runtime_bindings: tuple[RuntimeFileBinding, ...] = ()

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(binding.fd for binding in self.runtime_bindings if binding.fd >= 0)

    def close(self) -> None:
        for binding in self.runtime_bindings:
            binding.close()

    def __enter__(self) -> "ResolvedInvocation":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _logical_command(self) -> list[str]:
        command = list(self.command)
        if self.runtime_bindings:
            binding = self.runtime_bindings[0]
            if len(command) >= 4 and command[3] in {"--source-fd", "--package-file"}:
                index = 3
                while index < len(command):
                    if command[index] == "--source-fd" and index + 1 < len(command):
                        del command[index : index + 2]
                    elif command[index] == "--package-file" and index + 2 < len(command):
                        del command[index : index + 3]
                    else:
                        break
            elif command and command[0].startswith("/proc/self/fd/"):
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


def _build_python_runtime(
    module_id: str,
    interface_id: str,
    gateway: dict[str, Any],
    script_args: list[str],
    repo_root: Path | None = None,
) -> tuple[
    Path,
    list[str],
    dict[str, str] | None,
    tuple[RuntimeFileBinding, ...],
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
    entrypoint = f"{path}:{symbol}"
    root = get_repo_root(repo_root)
    skill_root = root / "skills" / module_id
    args_prefix = gateway.get("args_prefix", [])
    if not isinstance(args_prefix, list) or not all(
        isinstance(token, str) and token for token in args_prefix
    ):
        raise InvocationError(
            f"{interface_id}: Python gateway needs string list `args_prefix`"
        )
    env = os.environ.copy()
    src_root = root / "src"
    entries = [str(skill_root), str(src_root)]
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(entries + ([current] if current else []))
    env["PYTHONIOENCODING"] = "utf-8:strict"
    module_text, separator, class_name = entrypoint.partition(":")
    module_path = Path(module_text)
    if (
        separator != ":"
        or not module_text
        or not class_name
        or module_path.is_absolute()
        or ".." in module_path.parts
        or not module_path.parts
        or module_path.parts[0] != "_rtx"
    ):
        raise InvocationError(
            f"{interface_id}: entrypoint must be `_rtx/path.py:ClassName` "
            "without parent traversal"
        )
    try:
        package_bindings = open_runtime_python_package(
            skill_root / "_rtx",
            skill_root,
            root,
        )
    except BlueprintGraphError as exc:
        raise InvocationError(f"{interface_id}: {exc}") from exc
    source_path = Path(os.path.abspath(skill_root / module_path))
    source_binding = next(
        (binding for binding in package_bindings if binding.path == source_path),
        None,
    )
    if source_binding is None:
        for binding in package_bindings:
            binding.close()
        raise InvocationError(
            f"{interface_id}: entrypoint is not a regular Python package source: "
            f"{module_text}"
        )
    package_arguments = [
        token
        for binding in package_bindings
        for token in (
            "--package-file",
            str(binding.fd),
            binding.path.relative_to(skill_root).as_posix(),
        )
    ]
    return (
        skill_root,
        [
            sys.executable,
            "-m",
            "officina.runtime.python_machine_interface_runner",
            "--source-fd",
            str(source_binding.fd),
            *package_arguments,
            entrypoint,
            *args_prefix,
            *script_args,
        ],
        env,
        package_bindings,
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
        module, source, export = resolve_export(graph, target, target_version)
        caller_module = graph.nodes.get(caller_skill)
        if caller_module is None or caller_module.node_type != "module":
            raise InvocationError(
                f"caller module `{caller_skill}` does not exist"
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
            raise InvocationError(
                f"caller module `{caller_skill}` does not declare use of "
                f"`{export.interface_id}` version {export.version} in a contained source"
            )
        access = export.export_declaration.get("access") if export.export_declaration else None
        if not isinstance(access, dict):
            raise InvocationError(f"{target}: export access is missing")
        allowed = access.get("allowed_callers", [])
        if (
            caller_skill != module.node_id
            and access.get("allow_all_modules") is not True
            and caller_skill not in allowed
        ):
            raise InvocationError(
                f"caller module `{caller_skill}` is not allowed to call `{target}`"
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
            raise InvocationError(
                f"{export.interface_id}: certification rejected "
                f"[{decision.code}]: {decision.message}"
            )
    except (BlueprintGraphError, ProcessBindingError) as exc:
        raise InvocationError(str(exc)) from exc

    target_skill = module.skill_root.name
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
        gateway_path = source.gateway_path.relative_to(source.skill_root).as_posix()
    except ValueError as exc:
        raise InvocationError(
            f"{export.interface_id}: gateway must remain inside its module"
        ) from exc
    cwd, command, env, runtime_bindings = _build_python_runtime(
        target_skill,
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
        env=env,
        runtime_bindings=runtime_bindings,
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
        target=target,
        args=args,
        stdin_requested=stdin_requested,
        target_version=target_version,
        certification_view=certification_view,
        graph=graph,
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
            raise InvocationError(
                f"{resolved.target}: launch failed: {exc}"
            ) from exc
    finally:
        resolved.close()
