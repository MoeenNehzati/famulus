"""Shared stdio MCP adapter for the existing Famulus Dispatcher."""

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

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
from officina.dispatcher.direct_blueprints import parse_interface_id
from officina.dispatcher.direct_runtime import (
    _run_resolved_invocation,
    resolve_dispatch,
)
from officina.dispatcher.errors import (
    DISPATCHER_ERROR_SPECS,
    DirectBlueprintError,
    DispatcherError,
    InvocationError,
    ReducedCause,
    SetupBlocked,
    render_dispatcher_error,
)
from officina.blueprints.graph import BlueprintGraphError
from officina.common.famulus_paths import resolve_famulus_paths
from officina.common.atomic_files import ensure_private_directory, exclusive_file_lock


MANAGER_MODULE = "setup-interface-manager"
MANAGER_PREFIX = f"{MANAGER_MODULE}."
MANAGER_INTERFACES = {
    "status": "setup-interface-manager._rtx.interface.status",
    "authorize": "setup-interface-manager._rtx.interface.authorize",
    "authorize-markdown-call": "setup-interface-manager._rtx.interface.authorize-markdown-call",
    "begin": "setup-interface-manager._rtx.interface.begin",
    "recover": "setup-interface-manager._rtx.interface.recover",
    "recover-busy": "setup-interface-manager._rtx.interface.recover-busy",
}

_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
_FLOW_LEASES: dict[str, Any] = {}
_FLOW_LEASE_GUARD = Lock()

_STATUS_CODES = frozenset({"unmanaged", "ready", "setup_required", "setup_busy"})
_FLOW_SUCCESS_STATES = frozenset("ready run-step awaiting-settlement authorized-markdown-call".split())
_FLOW_FAILURE_STATES = frozenset({"busy", "failed", "recovery-required"})


_SETUP_ERROR_MESSAGES = {
    "setup.flow_busy": {"Another managed setup flow is active.", "Another managed setup flow became active before authorization completed."},
    "setup.request_invalid": {"The setup-manager request does not match the declared interface signature."},
    "setup.arguments_not_object": {"Setup action input must be one JSON object."},
    "setup.arguments_shape_invalid": {"Setup action input has missing required fields or undeclared fields."},
    "setup.argument_type_invalid": {"Declared setup arguments must be string, integer, or Boolean JSON values."},
    "setup.positional_argument_invalid": {"Positional setup arguments cannot be Boolean."},
    "setup.positional_arguments_noncontiguous": {"Optional positional setup arguments cannot leave gaps."},
    "setup.repository_configuration_missing": {"The setup manager received no repository configuration."},
    "setup.repository_configuration_invalid": {"The repository configuration is invalid."},
    "setup.graph_invalid": {"Managed-setup metadata is invalid."},
    "setup.graph_read_failed": {"Managed-setup metadata could not be read."},
    "setup.permission_denied": {"The setup manager was denied permission to access managed-setup metadata.", "The setup manager was denied permission to access its state ledger."},
    "setup.binding_missing": {"A required managed setup interface has no declared runtime binding."},
    "setup.binding_mismatch": {"The declared setup runtime binding does not match the live managed metadata."},
    "setup.initialization_invalid": {"Managed-setup runtime declarations are inconsistent."},
    "setup.storage_capability_missing": {"The setup manager has no configured atomic-ledger capability."},
    "setup.status_path_dispatch_failed": {"The setup-status path lookup dispatch failed; no ledger path was obtained."},
    "setup.status_path_result_invalid": {"The setup-status path lookup returned an invalid process result."},
    "setup.ledger_access_failed": {"Managed-setup state could not be accessed safely."},
    "setup.ledger_invalid": {"Managed-setup state is not valid canonical ledger data."},
    "setup.ledger_conflict": {"Managed-setup state changed while this operation was updating it."},
    "setup.ledger_write_uncertain": {"The final managed-setup ledger state could not be confirmed after writing."},
    "setup.flow_not_found": {"No active managed setup flow matches this request."},
    "setup.flow_mismatch": {"The request does not match the active managed setup flow."},
    "setup.operation_not_allowed": {"This operation is not allowed for the active managed setup step."},
    "setup.root_not_managed": {"The requested root is not a managed setup interface."},
    "setup.begin_state_invalid": {"Managed setup cannot begin from the current evaluated state."},
    "setup.target_not_ready": {"The target requires setup before it can be authorized."},
    "setup.begin_conflict": {"Managed setup state changed before the new flow could begin."},
    "setup.ledger_graph_mismatch": {"Stored setup receipts do not match the live managed-setup metadata."},
    "setup.transition_state_invalid": {"The managed setup transition did not produce the required persisted next state."},
    "setup.authorization_state_invalid": {"Managed setup authorization did not produce an evaluated result."},
    "setup.active_flow_changed": {"The active managed setup flow changed before the requested operation could be applied."},
    "setup.action_dispatch_failed": {"The managed setup action dispatch failed; action completion is unknown."},
    "setup.action_result_invalid": {"The managed setup action dispatch returned an invalid process result; action completion is unknown."},
    "setup.settlement_failed": {"The verifier confirmed external completion, but the setup manager could not record settlement.", "The managed action exited successfully, but the setup manager could not record settlement.", "The setup manager could not record settlement after the current step was submitted as complete."},
    "setup.cancellation_failed": {"The verifier reported the current step incomplete, but the setup manager could not cancel the flow.", "The setup manager could not cancel the flow; external completion remains unknown."},
    "setup.dispatch_declaration_invalid": {"The managed setup runtime dispatch declaration is inconsistent."},
    "setup.continuation_caller_mismatch": {"The runtime caller does not match the continuation caller supplied to `begin`."},
    "setup.recovery_owner_unverified": {"The active flow has no verified owner for ordinary recovery."},
    "setup.owner_active": {"The setup owner process still appears active."},
    "setup.owner_unknown": {"The active setup flow has no process owner metadata."},
    "setup.teardown_all_binding_invalid": {"Global teardown cannot process a managed binding that declares arguments."},
    "setup.verifier_dispatch_failed": {"The verifier dispatch failed; the managed step's completion is unknown."},
    "setup.verifier_result_invalid": {"The verifier dispatch returned an invalid process result; the managed step's completion is unknown."},
}
_SETUP_ERROR_PATTERNS = {
    "setup.status_path_process_failed": r"The setup-status path lookup returned nonzero process status -?[1-9][0-9]*\.",
    "setup.status_path_response_invalid": r"The setup-status path lookup did not return (?:text|one absolute path)\.",
    "setup.active_flow_stale": r"The active (?:setup flow no longer matches live state: (?:metadata|receipts|current step|binding)|teardown flow no longer matches live state: (?:metadata|plan|current step|binding))\.",
    "setup.action_failed": r"The managed (?:setup|teardown) action for `[A-Za-z_][A-Za-z0-9_.-]*@[1-9][0-9]*` returned nonzero process status -?[1-9][0-9]*\.",
    "setup.verifier_failed": r"The verifier for `[A-Za-z_][A-Za-z0-9_.-]*@[1-9][0-9]*` returned nonzero process status -?[1-9][0-9]*; the managed step's completion is unknown\.",
    "setup.verification_incomplete": r"The verifier reported that `[A-Za-z_][A-Za-z0-9_.-]*@[1-9][0-9]*` is incomplete\.",
    "setup.verifier_response_invalid": r"The verifier returned (?:malformed JSON|an unsupported response); the managed step's completion is unknown\.",
}
_SETUP_CLUES = {
    "setup.ledger_conflict": "Another setup-manager process may have updated the ledger concurrently.",
    "setup.begin_conflict": "Another setup-manager operation may have changed the managed state concurrently.",
}
_DISPATCHER_CAUSE_CODES = frozenset({"setup.graph_invalid", "setup.status_path_dispatch_failed", "setup.action_dispatch_failed", "setup.verifier_dispatch_failed"})
_SETUP_CAUSE_CODES = {
    "setup.settlement_failed": frozenset("setup.graph_invalid setup.ledger_access_failed setup.permission_denied setup.ledger_invalid setup.ledger_conflict setup.ledger_write_uncertain setup.flow_mismatch setup.active_flow_stale setup.ledger_graph_mismatch".split()),
    "setup.cancellation_failed": frozenset("setup.ledger_access_failed setup.permission_denied setup.ledger_invalid setup.ledger_conflict setup.ledger_write_uncertain setup.flow_mismatch setup.active_flow_stale setup.ledger_graph_mismatch setup.active_flow_changed".split()),
}
_RECOVERABLE_SETUP_CODES = frozenset("setup.ledger_write_uncertain setup.transition_state_invalid setup.active_flow_changed setup.action_dispatch_failed setup.action_result_invalid setup.action_failed setup.verifier_failed setup.verification_incomplete setup.verifier_response_invalid setup.settlement_failed setup.cancellation_failed setup.verifier_dispatch_failed setup.verifier_result_invalid".split())
_SETUP_CAUSE_MESSAGES = {
    "setup.graph_invalid": {"Managed-setup metadata is invalid."},
    "setup.ledger_access_failed": {"Managed-setup state could not be accessed safely."},
    "setup.permission_denied": {"The setup manager was denied permission to access its state ledger."},
    "setup.ledger_invalid": {"Managed-setup state is not valid canonical ledger data."},
    "setup.ledger_conflict": {"Managed-setup state changed while this operation was updating it."},
    "setup.ledger_write_uncertain": {"The final managed-setup ledger state could not be confirmed after writing."},
    "setup.flow_mismatch": {"The request does not match the active managed setup flow."},
    "setup.active_flow_stale": {f"The active {kind} flow no longer matches live state: {subject}." for kind, subjects in (("setup", ("metadata", "receipts", "current step", "binding")), ("teardown", ("metadata", "plan", "current step", "binding"))) for subject in subjects},
    "setup.ledger_graph_mismatch": {"Stored setup receipts do not match the live managed-setup metadata."},
    "setup.active_flow_changed": {"The active managed setup flow changed before the requested operation could be applied."},
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
        raise DispatcherError.from_spec(
            "D49",
            major=version[0],
            minor=version[1],
        )


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
    except OSError as exc:
        raise DispatcherError.from_spec("D51") from exc
    if child.is_symlink() or not child.is_dir() or not child.resolve().is_relative_to(root.resolve()):
        raise DispatcherError.from_spec("D50", kind="child directory")


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
    try:
        paths = resolve_famulus_paths(
            platform=sys.platform,
            home=Path.home(),
            environ=os.environ,
        )
        assert paths.plugin_data and paths.assistant_host and paths.logging_path
        paths.plugin_data.mkdir(parents=True, exist_ok=True)
        if paths.plugin_data.is_symlink() or not paths.plugin_data.is_dir():
            raise DispatcherError.from_spec("D50", kind="root")
        _confined_directory(paths.plugin_data, paths.logging_path)
    except DispatcherError:
        raise
    except OSError as exc:
        raise DispatcherError.from_spec("D51") from exc
    os.environ["ASSISTANT_LOGS"] = str(paths.logging_path)


def caller_argv(arguments: CompactArguments | OrderedArguments) -> list[str]:
    options = arguments.options
    if isinstance(options, list):
        if arguments.positionals:
            raise DispatcherError.from_spec("D54")
        return options
    argv = list(arguments.positionals)
    for name, value in options.items():
        if isinstance(value, list):
            raise DispatcherError.from_spec("D55")
        argv.append(name)
        if value is not True:
            argv.append(value)
    return argv


def _manager_call(caller: str, operation: str, arguments: list[str]) -> dict[str, Any]:
    """Invoke one fixed manager route and return only its JSON object."""

    target = MANAGER_INTERFACES[operation]
    try:
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
    except InvocationError as exc:
        cause = (
            exc
            if isinstance(exc, DispatcherError)
            and isinstance(getattr(exc, "_entry_id", None), str)
            and exc._entry_id in DISPATCHER_ERROR_SPECS
            and exc._entry_id.startswith(("D", "R"))
            else None
        )
        raise DispatcherError.from_spec("D56", operation=operation, cause=cause) from exc
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        entry_id = "D56" if result.returncode != 0 else "D57"
        raise DispatcherError.from_spec(entry_id, operation=operation) from None
    if not isinstance(payload, dict):
        entry_id = "D56" if result.returncode != 0 else "D57"
        raise DispatcherError.from_spec(entry_id, operation=operation)
    return _validate_manager_response(payload, operation, result.returncode)


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
    flow_id = str(uuid4())
    return _manager_route(
        "begin", [
            operation, root, caller, interface, str(version), flow_id,
            os.environ.get("FAMULUS_HOST", "unknown"), str(os.getpid()),
            _PROCESS_STARTED_AT,
        ]
    ) | {"caller": caller}


def _flow_lease_path(flow_id: str) -> tuple[Path, Path]:
    paths = resolve_famulus_paths(platform=sys.platform, home=Path.home(), environ=os.environ)
    if paths.plugin_data is None or paths.setup_status is None:
        raise DispatcherError.from_spec("D51")
    ensure_private_directory(paths.setup_status.parent, allowed_root=paths.plugin_data)
    return paths.setup_status.with_name(f".{paths.setup_status.name}.{sha256(flow_id.encode()).hexdigest()}.setup.lock"), paths.plugin_data


def _acquire_flow_lease(flow_id: str) -> None:
    with _FLOW_LEASE_GUARD:
        if flow_id in _FLOW_LEASES:
            return
        path, root = _flow_lease_path(flow_id)
        lease = exclusive_file_lock(path, allowed_root=root, mode=0o600, blocking=False)
        lease.__enter__()
        _FLOW_LEASES[flow_id] = lease


def _release_flow_lease(flow_id: str) -> None:
    with _FLOW_LEASE_GUARD:
        lease = _FLOW_LEASES.pop(flow_id, None)
    if lease is not None:
        lease.__exit__(None, None, None)


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
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"interface", "version", "kind", "action"}:
            raise ValueError("manager pending step is invalid")
        interface = raw.get("interface")
        version = raw.get("version")
        kind = raw.get("kind")
        action = raw.get("action")
        if (
            not isinstance(interface, str)
            or not interface
            or interface in seen
            or type(version) is not int
            or version < 1
            or not isinstance(kind, str)
            or kind not in {"markdown", "python"}
            or action != "run-setup"
        ):
            raise ValueError("manager pending step is invalid")
        seen.add(interface)
        safe.append(
            {
                "interface": interface,
                "version": version,
                "kind": kind,
                "action": action,
            }
        )
    return safe


def _validate_manager_response(
    payload: dict[str, Any], operation: str, returncode: int
) -> dict[str, Any]:
    """Validate one manager status or flow before exposing its public fields."""

    invalid = lambda: DispatcherError.from_spec("D58", operation=operation)
    if payload.get("schema_version") != 1:
        raise invalid()
    if "code" in payload:
        required = {"schema_version", "code", "root_setup_interface", "pending_stack", "flow_id"}
        if operation != "status" or not required.issubset(payload) or set(payload) - required - {"current_step", "owner"} or returncode != 0:
            raise invalid()
        try:
            payload["pending_stack"] = _safe_pending_stack(payload["pending_stack"])
        except ValueError:
            raise DispatcherError.from_spec("D59") from None
        code = payload["code"]
        if not isinstance(code, str) or code not in _STATUS_CODES:
            raise DispatcherError.from_spec("D63")
        root = payload["root_setup_interface"]
        flow_id = payload["flow_id"]
        if flow_id is not None and (not isinstance(flow_id, str) or not flow_id):
            if code == "setup_busy":
                raise DispatcherError.from_spec("D62")
            raise invalid()
        if code == "setup_required":
            if not isinstance(root, str) or not root or not payload["pending_stack"]:
                raise DispatcherError.from_spec("D61")
            if flow_id is not None:
                raise invalid()
        elif code == "unmanaged" and root is not None:
            raise invalid()
        elif code != "unmanaged" and (not isinstance(root, str) or not root):
            raise invalid()
        if code == "setup_busy":
            if not isinstance(flow_id, str):
                raise DispatcherError.from_spec("D62")
            current_step, owner = payload.get("current_step"), payload.get("owner")
            if payload["pending_stack"] or not isinstance(current_step, str) or not current_step:
                raise invalid()
            if owner is not None and (
                not isinstance(owner, dict) or set(owner) != {"host", "pid", "started_at"}
                or not isinstance(owner["host"], str) or not owner["host"]
                or isinstance(owner["pid"], bool) or not isinstance(owner["pid"], int) or owner["pid"] < 1
                or not isinstance(owner["started_at"], str) or not owner["started_at"].endswith("Z")
            ):
                raise invalid()
        elif code != "setup_required" and (payload["pending_stack"] or flow_id is not None):
            raise invalid()
        return payload

    base = {
        "schema_version", "flow_id", "operation", "state", "current_step",
        "original", "resume_original",
    }
    optional = {"error", "error_code", "clues", "cause", "recovery", "instructions", "interface", "version", "removed"}
    if not base.issubset(payload) or set(payload) - base - optional:
        raise invalid()
    response_operation = payload["operation"]
    allowed_operations = {
        "begin": {"setup", "teardown"},
        "authorize-markdown-call": {"setup"},
        "recover": {"setup", "teardown", "recover"},
    }.get(operation, {operation})
    if not isinstance(response_operation, str) or response_operation not in allowed_operations or type(payload["resume_original"]) is not bool:
        raise invalid()
    flow_id = payload["flow_id"]
    if flow_id is not None and (not isinstance(flow_id, str) or not flow_id):
        raise invalid()
    current_step = payload["current_step"]
    if current_step is not None:
        if not isinstance(current_step, dict) or set(current_step) != {"interface", "version", "kind", "action"}:
            raise invalid()
        interface, version = current_step["interface"], current_step["version"]
        kind, action = current_step["kind"], current_step["action"]
        actions = {"run-setup"} if response_operation == "setup" else {"run-teardown", "release-claim", "invalidate-receipt"}
        if not isinstance(interface, str) or not interface or type(version) is not int or version < 1 or not isinstance(kind, str) or kind not in {"markdown", "python"} or not isinstance(action, str) or action not in actions:
            raise invalid()
    original = payload["original"]
    if original is not None and (
        not isinstance(original, dict)
        or set(original) != {"caller", "interface", "version"}
        or not isinstance(original["caller"], str)
        or not original["caller"]
        or not isinstance(original["interface"], str)
        or not original["interface"]
        or type(original["version"]) is not int
        or original["version"] < 1
    ):
        raise invalid()
    state = payload["state"]
    if not isinstance(state, str):
        raise invalid()
    if state in _FLOW_SUCCESS_STATES:
        if returncode != 0 or set(payload) & {"error", "error_code", "clues", "cause", "recovery"}:
            raise invalid()
        expected_success = {
            "status": frozenset(),
            "authorize": frozenset({"ready"}),
            "begin": frozenset({"ready", "run-step"}),
            "authorize-markdown-call": frozenset({"authorized-markdown-call"}),
            "recover": frozenset({"ready", "run-step"}),
        }.get(operation, frozenset())
        if state not in expected_success:
            raise invalid()
        extras = set(payload) - base
        if state == "ready":
            if extras or flow_id is not None or current_step is not None:
                raise invalid()
            if operation != "authorize" and payload["resume_original"] is not False:
                raise invalid()
        elif state == "run-step":
            if extras - {"instructions"} or flow_id is None or current_step is None or original is None or payload["resume_original"]:
                raise invalid()
            if "instructions" in payload and (current_step["kind"] != "markdown" or not isinstance(payload["instructions"], str) or not payload["instructions"]):
                raise invalid()
        elif (
            extras != {"interface", "version"}
            or flow_id is None or current_step is None or original is None
            or payload["resume_original"]
            or not isinstance(payload["interface"], str) or not payload["interface"]
            or type(payload["version"]) is not int or payload["version"] < 1
        ):
            raise invalid()
        return payload
    if state not in _FLOW_FAILURE_STATES or (
        returncode != 2 and not (state == "failed" and returncode == 64)
    ):
        raise invalid()
    expected_failure = {
        "status": frozenset({"failed"}),
        "authorize": frozenset({"busy", "failed"}),
        "begin": frozenset({"busy", "failed"}),
        "authorize-markdown-call": frozenset({"failed"}),
        "recover": frozenset({"failed", "recovery-required"}),
    }.get(operation, frozenset())
    if state not in expected_failure or set(payload) - base - {"error", "error_code", "clues", "cause", "recovery"}:
        raise invalid()
    error = payload.get("error")
    error_code = payload.get("error_code")
    if (
        not isinstance(error, str)
        or not isinstance(error_code, str)
        or (
            error not in _SETUP_ERROR_MESSAGES.get(error_code, set())
            and not re.fullmatch(_SETUP_ERROR_PATTERNS.get(error_code, r"(?!)"), error)
        )
    ):
        raise invalid()
    clues = payload.get("clues", [])
    allowed_clue = _SETUP_CLUES.get(error_code)
    if clues not in ([], [allowed_clue] if allowed_clue is not None else []):
        raise invalid()
    cause = None
    raw_cause = payload.get("cause")
    if raw_cause is not None:
        if not isinstance(raw_cause, dict):
            raise invalid()
        if error_code in _DISPATCHER_CAUSE_CODES:
            for entry_id, spec in DISPATCHER_ERROR_SPECS.items():
                if (
                    spec.code != raw_cause.get("code")
                    or not entry_id.startswith(("D", "R"))
                    or error_code == "setup.graph_invalid"
                    and not entry_id.startswith("D")
                ):
                    continue
                caller_id = raw_cause.get("caller_module_id", "")
                target_id = raw_cause.get("target_module_id", "")
                if not isinstance(caller_id, str) or not isinstance(target_id, str):
                    continue
                context = {
                    name: raw_cause[name]
                    for name in spec.context_fields
                    if name in raw_cause
                }
                raw_clues = raw_cause.get("clues", [])
                if not isinstance(raw_clues, list) or any(
                    not isinstance(clue, str) for clue in raw_clues
                ):
                    continue
                operations = (
                    tuple(MANAGER_INTERFACES)
                    if "operation" in spec.context_fields
                    and "operation" not in context
                    else (None,)
                )
                for candidate_operation in operations:
                    candidate_context = dict(context)
                    if candidate_operation is not None:
                        candidate_context["operation"] = candidate_operation
                    if entry_id == "D64" and "setup_error" not in candidate_context:
                        prefix = f"The setup manager `{candidate_operation}` failed: "
                        message = raw_cause.get("message")
                        if not isinstance(message, str) or not message.startswith(prefix):
                            continue
                        candidate_context["setup_error"] = message.removeprefix(prefix)
                        setup_code = candidate_context.get("setup_error_code")
                        setup_error = candidate_context["setup_error"]
                        if not isinstance(setup_code, str) or (
                            setup_error not in _SETUP_ERROR_MESSAGES.get(setup_code, set())
                            and not re.fullmatch(
                                _SETUP_ERROR_PATTERNS.get(setup_code, r"(?!)"),
                                setup_error,
                            )
                        ):
                            continue
                    try:
                        candidate = DispatcherError.from_spec(
                            entry_id,
                            caller_module_id=caller_id,
                            target_module_id=target_id,
                            clues=tuple(raw_clues),
                            **{
                                key: value
                                for key, value in candidate_context.items()
                                if key not in {"caller_module_id", "target_module_id"}
                            },
                        )
                    except (KeyError, ValueError):
                        continue
                    reduced_candidate = candidate.as_payload()
                    for field in ("schema_version", "cause", "recovery"):
                        reduced_candidate.pop(field, None)
                    if reduced_candidate == raw_cause:
                        cause = candidate
                        break
                if cause is not None:
                    break
        cause_code = raw_cause.get("code")
        cause_message = raw_cause.get("message")
        cause_clues = raw_cause.get("clues", [])
        allowed_cause_clue = _SETUP_CLUES.get(cause_code) if isinstance(cause_code, str) else None
        if (
            cause is None
            and isinstance(cause_code, str)
            and isinstance(cause_message, str)
            and cause_code in _SETUP_CAUSE_CODES.get(error_code, frozenset())
            and raw_cause.get("schema_version") == 1
            and set(raw_cause) <= {"schema_version", "code", "message", "clues"}
            and cause_message in _SETUP_CAUSE_MESSAGES.get(cause_code, set())
            and cause_clues in ([], [allowed_cause_clue] if allowed_cause_clue else [])
        ):
            cause = ReducedCause._from_allowed(
                cause_code,
                cause_message,
                clues=tuple(cause_clues),
                allowed_messages=_SETUP_CAUSE_MESSAGES,
                allowed_clues=_SETUP_CLUES,
            )
        if cause is None:
            raise invalid()
    recovery = payload.get("recovery")
    if state == "busy" and (error_code != "setup.flow_busy" or flow_id is None or current_step is None):
        raise invalid()
    if state == "failed" and recovery is not None:
        raise invalid()
    if state == "recovery-required" and (
        error_code not in _RECOVERABLE_SETUP_CODES
        or flow_id is None or current_step is None or recovery is None
    ):
        raise invalid()
    if recovery is not None and (
        state != "recovery-required"
        or not isinstance(recovery, dict)
        or recovery != {
            "interface": "setup-interface-manager.interface.recover",
            "version": 1,
            "flow_id": flow_id,
            "actions": ["retry", "cancel"],
        }
    ):
        raise invalid()
    raise DispatcherError.from_spec(
        "D64", operation=operation, setup_error=error,
        setup_error_code=error_code, cause=cause, clues=tuple(clues),
    )


def _ordinary_preflight(
    caller: str, interface: str, version: int, status: dict[str, Any] | None = None
) -> dict[str, object] | None:
    """Return a redacted refusal, or ``None`` when launch is authorized."""

    status = _manager_call(caller, "status", [interface]) if status is None else status
    code = status.get("code")
    if code == "unmanaged":
        return None
    if code == "ready":
        authorized = _manager_call(
            caller, "authorize", [interface, caller, interface, str(version)]
        )
        if (
            authorized.get("state") == "ready"
            and authorized.get("resume_original") is True
        ):
            return None
        raise DispatcherError.from_spec("D60")
    if code == "setup_required":
        root = status.get("root_setup_interface")
        pending_stack = status["pending_stack"]
        return {
            "code": "setup_required",
            "root_setup_interface": root,
            "pending_stack": pending_stack,
            "next_setup": pending_stack[-1],
            "manager": _begin_route("setup", root, caller, interface, version),
            "original": _original(caller, interface, version),
        }
    if code == "setup_busy":
        flow_id, root = status["flow_id"], status["root_setup_interface"]
        owner = status.get("owner")
        if owner is None:
            message = f"Setup {root} is busy; its owner is unknown."
            options: dict[str, object] = {"--force": True}
        else:
            message = f"Setup {root} is busy by {owner['host']} process {owner['pid']}."
            options = {}
        return {
            "code": "setup_busy",
            "flow_id": flow_id,
            "root_setup_interface": root,
            "current_step": status["current_step"],
            "owner": owner,
            "message": message,
            "recovery": {
                "interface": MANAGER_INTERFACES["recover-busy"],
                "arguments": {"positionals": [flow_id], "options": options, "stdin": None},
            },
        }
    raise DispatcherError.from_spec("D63")


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
            raise DispatcherError.from_spec("D65")

        if dry_run or interface.startswith(MANAGER_PREFIX):
            argv = caller_argv(arguments)
            started_flow: str | None = None
            if not dry_run and interface == MANAGER_INTERFACES["begin"] and len(argv) == 9:
                started_flow = argv[5]
            elif not dry_run and interface == "setup-interface-manager._rtx.interface.teardown-all" and not argv:
                started_flow = str(uuid4())
                argv = [started_flow, os.environ.get("FAMULUS_HOST", "unknown"), str(os.getpid()), _PROCESS_STARTED_AT]
            if started_flow is not None:
                _acquire_flow_lease(started_flow)
            try:
                with resolve_dispatch(
                    caller_skill=caller,
                    target=interface,
                    target_version=version,
                    args=argv,
                    stdin_requested=arguments.stdin is not None,
                    repository_config=ROOT / "officina.toml",
                ) as resolved:
                    dispatcher = resolved.metadata().as_payload()
                    if dry_run:
                        return dispatcher
                    result = _run_resolved_invocation(
                        resolved, stdin=arguments.stdin, capture_output=True, text=True
                    )
            except Exception:
                if started_flow is not None:
                    _release_flow_lease(started_flow)
                raise
            candidate = started_flow
            if candidate is None and argv and interface.rsplit(".", 1)[-1] in {
                "run-markdown", "run-python", "settle", "recover", "recover-busy"
            }:
                candidate = argv[0]
            try:
                returned_flow = json.loads(result.stdout).get("flow_id")
            except (AttributeError, json.JSONDecodeError, TypeError):
                returned_flow = candidate
            finished = result.returncode == 0 and returned_flow is None
            if candidate is not None and (finished or started_flow is not None and returned_flow != started_flow):
                _release_flow_lease(candidate)
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "dispatcher": dispatcher,
            }

        try:
            configuration = load_repository_configuration(ROOT / "officina.toml")
        except RepositoryConfigurationError as exc:
            raise DispatcherError.from_spec("D07") from exc
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
            cause = (
                exc
                if isinstance(exc, DirectBlueprintError)
                and isinstance(exc._entry_id, str)
                and exc._entry_id in DISPATCHER_ERROR_SPECS
                and exc._entry_id.startswith("D")
                else None
            )
            raise DispatcherError.from_spec(
                "D66",
                caller_module_id=caller,
                target_module_id=interface.split(".interface.", 1)[0],
                interface_id=interface,
                cause=cause,
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
            if (
                authorize_result.get("state") != "authorized-markdown-call"
                or authorize_result.get("flow_id") != setup_flow_id
                or authorize_result.get("interface") != interface
                or type(authorize_result.get("version")) is not int
                or authorize_result.get("version") != version
            ):
                raise DispatcherError.from_spec(
                    "D58", operation="authorize-markdown-call"
                )
        elif projection.graph.managed_setups:
            refusal = _ordinary_preflight(caller, interface, version)
            if refusal is not None:
                return refusal

        resolved = materialize_authorized_invocation(
            authorized,
            argv=caller_argv(arguments),
            stdin_requested=arguments.stdin is not None,
            setup_preflight_authorized=setup_flow_id is not None,
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
    except (InvocationError, SetupBlocked) as error:
        if type(error) is SetupBlocked:
            try:
                if set(error.__dict__) != {"status", "call_path", "lifecycle"} or type(error.call_path) is not tuple or not 1 <= len(error.call_path) <= 32 or error.call_path[0] != interface: raise DispatcherError.from_spec("D68")
                for frame in error.call_path:
                    parse_interface_id(frame)
                if error.lifecycle is not None:
                    if error.status is not None or type(error.lifecycle) is not tuple or len(error.lifecycle) != 2: raise DispatcherError.from_spec("D68")
                    root, operation = error.lifecycle
                    if parse_interface_id(root)[1] != "setup" or operation not in {"setup", "teardown"}: raise DispatcherError.from_spec("D68")
                    response = _setup_managed(operation, root, caller, interface, version)
                else:
                    if type(error.status) is not dict: raise DispatcherError.from_spec("D68")
                    status = _validate_manager_response(error.status, "status", 0)
                    tuple(map(parse_interface_id, (status["root_setup_interface"], *(step["interface"] for step in status["pending_stack"]))))
                    if status["code"] not in {"setup_required", "setup_busy"}: raise DispatcherError.from_spec("D68")
                    response = _ordinary_preflight(caller, interface, version, status)
            except InvocationError as validation_error:
                error = validation_error
            else:
                response["call_path"] = list(error.call_path)
                return response
        diagnosis = (
            error
            if isinstance(error, DispatcherError)
            and isinstance(error._entry_id, str)
            and error._entry_id in DISPATCHER_ERROR_SPECS
            else DispatcherError.from_spec("D68")
        )
        payload = diagnosis.as_payload()
        return {"exit_code": 2, "stdout": "", "stderr": "", "dispatcher": payload}


def main() -> None:
    require_python()
    configure_plugin_persistence()
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        if exc.name == "mcp" or (exc.name or "").startswith("mcp."):
            raise DispatcherError.from_spec("D52", module_name="mcp") from exc
        raise DispatcherError.from_spec("D53") from exc
    except Exception as exc:
        raise DispatcherError.from_spec("D53") from exc
    try:
        server = FastMCP(CONTRACT["server"])
        server.tool()(invoke)
        try:
            server.run(transport="stdio")
        finally:
            for flow_id in tuple(_FLOW_LEASES):
                _release_flow_lease(flow_id)
    except Exception as exc:
        raise DispatcherError.from_spec("D53") from exc


def _main_entrypoint() -> int:
    """Render registered pre-tool failures without exposing their causes."""

    try:
        main()
    except DispatcherError as error:
        diagnosis = (
            error
            if isinstance(error._entry_id, str)
            and error._entry_id in DISPATCHER_ERROR_SPECS
            else DispatcherError.from_spec("D53")
        )
        for line in render_dispatcher_error(diagnosis):
            print(line, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_entrypoint())
