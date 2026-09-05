"""Shared stdio MCP adapter for the existing Famulus Dispatcher."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "src"))
os.environ["PYTHONPATH"] = str(ROOT / "src")
from officina.configuration.repository import (
    RepositoryConfigurationError,
    load_repository_configuration,
)
from officina.dispatcher import (
    authorize_direct_invocation,
    load_direct_setup_projection,
    materialize_authorized_invocation,
)
from officina.dispatcher.direct_runtime import (
    _run_resolved_invocation,
    resolve_dispatch,
)
from officina.dispatcher.errors import (
    DirectBlueprintError,
    InvocationError,
    RuntimeMisconfiguredError,
)
from officina.blueprints.graph import BlueprintGraphError
from officina.common.famulus_paths import resolve_famulus_paths


MANAGER_MODULE = "setup-interface-manager"
MANAGER_PREFIX = f"{MANAGER_MODULE}."
MANAGER_INTERFACES = {
    "status": "setup-interface-manager._rtx.interface.status",
    "authorize": "setup-interface-manager._rtx.interface.authorize",
    "authorize-markdown-call": "setup-interface-manager._rtx.interface.authorize-markdown-call",
    "begin": "setup-interface-manager._rtx.interface.begin",
    "recover": "setup-interface-manager._rtx.interface.recover",
}


@dataclass
class CompactArguments:
    positionals: list[str]
    options: dict[str, str | Literal[True]]
    stdin: str | None


@dataclass
class OrderedArguments:
    positionals: tuple[()]
    options: list[str]
    stdin: str | None


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    dispatcher: dict[str, Any]


def require_python(version: tuple[int, int] = sys.version_info[:2]) -> None:
    if version < (3, 11):
        raise RuntimeError("Famulus MCP requires python >=3.11")


def _confined_directory(root: Path, child: Path) -> None:
    """Create one plugin-data child only when it remains confined.

    Intent
    ------
    Reject aliases and non-directories before startup publishes their paths.
    Rationale
    ---------
    Explicit post-creation checks keep writes inside the client-owned data root.
    Pseudocode
    ----------
    - set child = created directory or existing filesystem entry
    - if child is unsafe:
      - raise runtime error
    Wraps
    -----
    - none
    """
    try:
        child.mkdir()
    except FileExistsError:
        pass
    if child.is_symlink() or not child.is_dir() or not child.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f"unsafe plugin-data directory: {child}")


def configure_plugin_persistence() -> None:
    """Project client-owned plugin data into this MCP subprocess.

    Intent
    ------
    Prepare the private milestone path from explicit plugin provenance.
    Rationale
    ---------
    MCP startup must not claim or overwrite the manager-owned setup ledger.
    Pseudocode
    ----------
    - persistence_paths = resolve_famulus_paths(platform, home, environment)
    - @_confined_directory(plugin_root, logging_root)
    - set ASSISTANT_LOGS = logging root
    Wraps
    -----
    - none
    CallsFromRepo
    -------------
    ._confined_directory:
      why:
        validates: "Rejects an unsafe logging root before publishing ASSISTANT_LOGS."
    InstantiationsFromRepo
    ----------------------
    .officina.common.famulus_paths.resolve_famulus_paths:
      why:
        constructs: "Builds the explicit host-scoped paths consumed by persistence startup."
    """
    if "FAMULUS_HOST" not in os.environ and "FAMULUS_PLUGIN_DATA" not in os.environ:
        return
    paths = resolve_famulus_paths(platform=sys.platform, home=Path.home(), environ=os.environ)
    assert paths.plugin_data and paths.assistant_host and paths.logging_path
    paths.plugin_data.mkdir(parents=True, exist_ok=True)
    if paths.plugin_data.is_symlink() or not paths.plugin_data.is_dir():
        raise RuntimeError(f"unsafe plugin-data directory: {paths.plugin_data}")
    _confined_directory(paths.plugin_data, paths.logging_path)
    os.environ["ASSISTANT_LOGS"] = str(paths.logging_path)


def caller_argv(arguments: CompactArguments | OrderedArguments) -> list[str]:
    options = arguments.options
    if isinstance(options, list):
        if arguments.positionals:
            raise ValueError("ordered options require empty positionals")
        return options
    argv = list(arguments.positionals)
    for name, value in options.items():
        if isinstance(value, list):
            raise ValueError("compact options cannot contain lists; use ordered options")
        argv.append(name)
        if value is not True:
            argv.append(value)
    return argv


def _manager_call(caller: str, operation: str, arguments: list[str]) -> dict[str, Any]:
    """Invoke one fixed manager route and return only its JSON object."""

    target = MANAGER_INTERFACES[operation]
    with resolve_dispatch(
        caller_skill=caller,
        target=target,
        target_version=1,
        args=arguments,
        stdin_requested=False,
        repository_config=ROOT / "officina.toml",
    ) as resolved:
        result = _run_resolved_invocation(
            resolved, capture_output=True, text=True
        )
    if result.returncode != 0:
        raise RuntimeMisconfiguredError(
            "setup manager invocation failed",
            caller_module_id=caller,
            target_module_id=MANAGER_MODULE,
        )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeMisconfiguredError(
            "setup manager returned malformed JSON",
            caller_module_id=caller,
            target_module_id=MANAGER_MODULE,
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeMisconfiguredError(
            "setup manager returned a non-object response",
            caller_module_id=caller,
            target_module_id=MANAGER_MODULE,
        )
    return payload


def _original(caller: str, interface: str, version: int) -> dict[str, object]:
    return {"caller": caller, "interface": interface, "version": version}


def _manager_route(operation: str, positionals: list[str]) -> dict[str, object]:
    return {
        "interface": MANAGER_INTERFACES[operation],
        "version": 1,
        "arguments": {"positionals": positionals, "options": {}, "stdin": None},
    }


def _begin_route(
    operation: str, root: str, caller: str, interface: str, version: int
) -> dict[str, object]:
    return _manager_route(
        "begin", [operation, root, caller, interface, str(version)]
    )


def _setup_managed(
    operation: str, root: str, caller: str, interface: str, version: int
) -> dict[str, object]:
    return {
        "code": "setup_managed",
        "operation": operation,
        "root_setup_interface": root,
        "manager": _begin_route(operation, root, caller, interface, version),
        "original": _original(caller, interface, version),
    }


def _safe_pending_stack(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("manager pending stack is invalid")
    safe = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("manager pending step is invalid")
        interface = raw.get("interface")
        version = raw.get("version")
        kind = raw.get("kind")
        action = raw.get("action")
        if (
            not isinstance(interface, str)
            or type(version) is not int
            or version < 1
            or kind not in {"markdown", "python"}
            or action != "run-setup"
        ):
            raise ValueError("manager pending step is invalid")
        safe.append(
            {
                "interface": interface,
                "version": version,
                "kind": kind,
                "action": action,
            }
        )
    return safe


def _ordinary_preflight(
    caller: str, interface: str, version: int
) -> dict[str, object] | None:
    """Return a redacted refusal, or ``None`` when launch is authorized."""

    status = _manager_call(caller, "status", [interface])
    code = status.get("code")
    if code == "unmanaged":
        return None
    if code == "ready":
        authorized = _manager_call(
            caller,
            "authorize",
            [interface, caller, interface, str(version)],
        )
        if (
            authorized.get("state") == "ready"
            and authorized.get("resume_original") is True
        ):
            return None
        raise RuntimeMisconfiguredError(
            "setup manager refused ready authorization",
            caller_module_id=caller,
            target_module_id=MANAGER_MODULE,
        )
    if code == "setup_required":
        root = status.get("root_setup_interface")
        try:
            pending_stack = _safe_pending_stack(status.get("pending_stack"))
        except ValueError as exc:
            raise RuntimeMisconfiguredError(
                str(exc),
                caller_module_id=caller,
                target_module_id=MANAGER_MODULE,
            ) from exc
        if not isinstance(root, str) or not root or not pending_stack:
            raise RuntimeMisconfiguredError(
                "setup manager returned an incomplete setup requirement",
                caller_module_id=caller,
                target_module_id=MANAGER_MODULE,
            )
        return {
            "code": "setup_required",
            "root_setup_interface": root,
            "pending_stack": pending_stack,
            "next_setup": pending_stack[-1],
            "manager": _begin_route("setup", root, caller, interface, version),
            "original": _original(caller, interface, version),
        }
    if code == "setup_busy":
        flow_id = status.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id:
            raise RuntimeMisconfiguredError(
                "setup manager returned an incomplete busy status",
                caller_module_id=caller,
                target_module_id=MANAGER_MODULE,
            )
        return {
            "code": "setup_busy",
            "flow_id": flow_id,
            "manager": {
                "interface": MANAGER_INTERFACES["recover"],
                "version": 1,
            },
        }
    raise RuntimeMisconfiguredError(
        "setup manager returned an unsupported status",
        caller_module_id=caller,
        target_module_id=MANAGER_MODULE,
    )


def invoke(
    caller: str,
    interface: str,
    version: int,
    arguments: CompactArguments | OrderedArguments,
    dry_run: bool = False,
    setup_flow_id: str | None = None,
) -> dict[str, Any] | ExecutionResult:
    """Invoke one authorized Famulus interface through the existing Dispatcher."""
    try:
        if setup_flow_id is not None and (dry_run or interface.startswith(MANAGER_PREFIX)):
            raise RuntimeMisconfiguredError(
                "setup_flow_id cannot be used with dry_run or manager targets",
                caller_module_id=caller,
                target_module_id=interface.split(".interface.", 1)[0] if ".interface." in interface else interface,
            )

        if dry_run or interface.startswith(MANAGER_PREFIX):
            with resolve_dispatch(
                caller_skill=caller,
                target=interface,
                target_version=version,
                args=caller_argv(arguments),
                stdin_requested=arguments.stdin is not None,
                repository_config=ROOT / "officina.toml",
            ) as resolved:
                dispatcher = resolved.metadata().as_payload()
                if dry_run:
                    return dispatcher
                result = _run_resolved_invocation(
                    resolved,
                    stdin=arguments.stdin,
                    capture_output=True,
                    text=True,
                )
                return {
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "dispatcher": dispatcher,
                }

        try:
            configuration = load_repository_configuration(ROOT / "officina.toml")
        except RepositoryConfigurationError as exc:
            raise RuntimeMisconfiguredError(
                str(exc),
                caller_module_id=caller,
                target_module_id=interface.split(".interface.", 1)[0],
            ) from exc
        authorized = authorize_direct_invocation(
            configuration=configuration,
            caller_module_id=caller,
            interface_id=interface,
            interface_version=version,
            host_caller=True,
        )
        try:
            projection = load_direct_setup_projection(
                authorized.repository,
                authorized.target_modules,
                authorized.export,
            )
        except (BlueprintGraphError, DirectBlueprintError, OSError) as exc:
            raise RuntimeMisconfiguredError(
                "managed setup graph is unavailable",
                caller_module_id=caller,
                target_module_id=interface.split(".interface.", 1)[0],
            ) from exc
        if projection.lifecycle is not None:
            root, operation = projection.lifecycle
            return _setup_managed(operation, root, caller, interface, version)

        if setup_flow_id is not None:
            authorize_result = _manager_call(
                caller,
                "authorize-markdown-call",
                [setup_flow_id, interface, str(version)],
            )
            if authorize_result.get("state") != "authorized-markdown-call":
                return {
                    "code": "setup_busy",
                    "flow_id": setup_flow_id,
                    "manager": {
                        "interface": MANAGER_INTERFACES["recover"],
                        "version": 1,
                    },
                }
        elif projection.graph.managed_setups:
            refusal = _ordinary_preflight(caller, interface, version)
            if refusal is not None:
                return refusal

        resolved = materialize_authorized_invocation(
            authorized,
            argv=caller_argv(arguments),
            stdin_requested=arguments.stdin is not None,
        )
        dispatcher = resolved.metadata().as_payload()
        result = _run_resolved_invocation(
            resolved,
            stdin=arguments.stdin,
            capture_output=True,
            text=True,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "dispatcher": dispatcher,
        }
    except InvocationError as error:
        payload = error.as_payload() if hasattr(error, "as_payload") else {"message": str(error)}
        return {"exit_code": 2, "stdout": "", "stderr": "", "dispatcher": payload}


def main() -> None:
    require_python()
    configure_plugin_persistence()
    from mcp.server.fastmcp import FastMCP
    server = FastMCP(CONTRACT["server"])
    server.tool()(invoke)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
