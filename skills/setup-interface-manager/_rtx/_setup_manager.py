"""Finite setup/teardown orchestration and public machine interfaces."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from officina.blueprints.graph import (
    BlueprintGraphError,
    load_repository_blueprint_graph,
    managed_setup_order,
)
from officina.blueprints.direct_setup import (
    DirectBlueprintError,
    load_direct_setup_graph,
)
from officina.common import atomic_files
from officina.configuration.repository import (
    RepositoryConfigurationError,
    load_repository_configuration,
)
from officina.runtime.python_machine_interface import (
    PythonMachineInterface,
    is_dispatch_invocation_error,
    runtime_dispatch_context,
)
from officina.runtime.python_machine_interface_runner import run_python_machine_interface

from ._setup_dispatches import (
    GETTER_KEY,
    ManagedArgument,
    ManagedInterfaceBinding,
    PRODUCTION_BINDINGS,
    PRODUCTION_DECLARATION_INVALID,
    PRODUCTION_DISPATCHES,
)
from ._setup_evaluation import (
    SetupStep,
    TeardownStep,
    authorize_ready_root,
    evaluate_target,
    invalidate as invalidate_receipts,
    record_setup_success,
    record_teardown_all_success,
    record_teardown_success,
    teardown_all_plan,
    teardown_plan,
)
from ._setup_state import (
    ActiveFlow,
    ContinuationIdentity,
    FlowOwner,
    FlowConflict,
    LedgerConflict,
    LedgerCapabilityError,
    LedgerError,
    LedgerFormatError,
    LedgerPathError,
    LedgerWriteUncertain,
    LedgerStore,
    SetupLedger,
    SetupReceipt,
    begin_flow,
    claim_receipts,
    clear_flow,
)


SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[3]


class ManagerUsageError(ValueError):
    """The public call did not match its one declared signature."""


class ManagerDomainError(RuntimeError):
    """The call was valid but not legal in the current finite state."""


class ManagerRecoveryError(RuntimeError):
    """External completion is uncertain and requires explicit recovery."""


class ManagerBootstrapError(RuntimeError):
    """A stable external bootstrap boundary could not construct the manager."""

    def __init__(
        self, entry_id: str, *, cause: object | None = None, **context: object
    ) -> None:
        super().__init__(entry_id)
        self.entry_id = entry_id
        self.context = context
        self.cause = cause


@dataclass(frozen=True)
class SetupErrorSpec:
    """One closed, safe public setup-manager diagnosis."""

    code: str
    message: str
    context_fields: frozenset[str] = frozenset()
    allowed_setup_causes: frozenset[str] = frozenset()
    allow_dispatcher_cause: bool = False
    clue: str | None = None


_SETTLEMENT_CAUSES = frozenset(
    {"E10", "E20", "E20p", "E21", "E22", "E23", "E25", "E32", "E33", "E34"}
)
_CANCELLATION_CAUSES = _SETTLEMENT_CAUSES - {"E10"} | {"E36"}


def _reduced_dispatcher_cause(
    cause: object, *, require_direct: bool = False
) -> dict[str, object] | None:
    """Return only the safe first-level payload of one registered D/R error."""
    if not isinstance(cause, BaseException) or not is_dispatch_invocation_error(cause) or (
        require_direct and not isinstance(cause, DirectBlueprintError)
    ):
        return None
    entry_id = getattr(cause, "_entry_id", None)
    if (
        not isinstance(entry_id, str)
        or not entry_id.startswith(("D", "R"))
        or require_direct and not entry_id.startswith("D")
    ):
        return None
    context = getattr(cause, "_spec_context", None)
    clues = getattr(cause, "clues", None)
    caller_id = getattr(cause, "caller_module_id", None)
    target_id = getattr(cause, "target_module_id", None)
    factory = getattr(type(cause), "from_spec", None)
    render = getattr(cause, "as_payload", None)
    if (
        not isinstance(context, Mapping)
        or not isinstance(clues, tuple)
        or not isinstance(caller_id, str)
        or not isinstance(target_id, str)
        or not callable(factory)
        or not callable(render)
    ):
        return None
    try:
        candidate = factory(
            entry_id,
            caller_module_id=caller_id,
            target_module_id=target_id,
            clues=clues,
            **{
                key: value
                for key, value in context.items()
                if key not in {"caller_module_id", "target_module_id"}
            },
        )
        payload = render()
        candidate_payload = candidate.as_payload()
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(candidate_payload, dict):
        return None
    for field in ("schema_version", "cause", "recovery"):
        payload.pop(field, None)
        candidate_payload.pop(field, None)
    if payload != candidate_payload:
        return None
    return payload


SETUP_ERROR_SPECS: Mapping[str, SetupErrorSpec] = MappingProxyType({
    "E00": SetupErrorSpec("setup.flow_busy", "Another managed setup flow is active."),
    "E01": SetupErrorSpec("setup.request_invalid", "The setup-manager request does not match the declared interface signature."),
    "E02": SetupErrorSpec("setup.arguments_not_object", "Setup action input must be one JSON object."),
    "E03": SetupErrorSpec("setup.arguments_shape_invalid", "Setup action input has missing required fields or undeclared fields."),
    "E04": SetupErrorSpec("setup.argument_type_invalid", "Declared setup arguments must be string, integer, or Boolean JSON values."),
    "E05": SetupErrorSpec("setup.positional_argument_invalid", "Positional setup arguments cannot be Boolean."),
    "E06": SetupErrorSpec("setup.positional_arguments_noncontiguous", "Optional positional setup arguments cannot leave gaps."),
    "E08": SetupErrorSpec("setup.repository_configuration_missing", "The setup manager received no repository configuration."),
    "E09": SetupErrorSpec("setup.repository_configuration_invalid", "The repository configuration is invalid."),
    "E10": SetupErrorSpec("setup.graph_invalid", "Managed-setup metadata is invalid.", allow_dispatcher_cause=True),
    "E11": SetupErrorSpec("setup.graph_read_failed", "Managed-setup metadata could not be read."),
    "E11p": SetupErrorSpec("setup.permission_denied", "The setup manager was denied permission to access managed-setup metadata."),
    "E12": SetupErrorSpec("setup.binding_missing", "A required managed setup interface has no declared runtime binding."),
    "E13": SetupErrorSpec("setup.binding_mismatch", "The declared setup runtime binding does not match the live managed metadata."),
    "E14": SetupErrorSpec("setup.initialization_invalid", "Managed-setup runtime declarations are inconsistent."),
    "E15": SetupErrorSpec("setup.storage_capability_missing", "The setup manager has no configured atomic-ledger capability."),
    "E16": SetupErrorSpec("setup.status_path_dispatch_failed", "The setup-status path lookup dispatch failed; no ledger path was obtained.", allow_dispatcher_cause=True),
    "E17": SetupErrorSpec("setup.status_path_process_failed", "The setup-status path lookup returned nonzero process status {returncode}.", frozenset({"returncode"})),
    "E18": SetupErrorSpec("setup.status_path_result_invalid", "The setup-status path lookup returned an invalid process result."),
    "E19": SetupErrorSpec("setup.status_path_response_invalid", "The setup-status path lookup did not return {expected}.", frozenset({"expected"})),
    "E20": SetupErrorSpec("setup.ledger_access_failed", "Managed-setup state could not be accessed safely."),
    "E20p": SetupErrorSpec("setup.permission_denied", "The setup manager was denied permission to access its state ledger."),
    "E21": SetupErrorSpec("setup.ledger_invalid", "Managed-setup state is not valid canonical ledger data."),
    "E22": SetupErrorSpec("setup.ledger_conflict", "Managed-setup state changed while this operation was updating it.", clue="Another setup-manager process may have updated the ledger concurrently."),
    "E23": SetupErrorSpec("setup.ledger_write_uncertain", "The final managed-setup ledger state could not be confirmed after writing."),
    "E24": SetupErrorSpec("setup.flow_not_found", "No active managed setup flow matches this request."),
    "E25": SetupErrorSpec("setup.flow_mismatch", "The request does not match the active managed setup flow."),
    "E26": SetupErrorSpec("setup.operation_not_allowed", "This operation is not allowed for the active managed setup step."),
    "E27": SetupErrorSpec("setup.root_not_managed", "The requested root is not a managed setup interface."),
    "E28": SetupErrorSpec("setup.begin_state_invalid", "Managed setup cannot begin from the current evaluated state."),
    "E29": SetupErrorSpec("setup.target_not_ready", "The target requires setup before it can be authorized."),
    "E30": SetupErrorSpec("setup.flow_busy", "Another managed setup flow became active before authorization completed."),
    "E31": SetupErrorSpec("setup.begin_conflict", "Managed setup state changed before the new flow could begin.", clue="Another setup-manager operation may have changed the managed state concurrently."),
    "E32": SetupErrorSpec("setup.active_flow_stale", "The active setup flow no longer matches live state: {mismatch_subject}.", frozenset({"mismatch_subject"})),
    "E33": SetupErrorSpec("setup.active_flow_stale", "The active teardown flow no longer matches live state: {mismatch_subject}.", frozenset({"mismatch_subject"})),
    "E34": SetupErrorSpec("setup.ledger_graph_mismatch", "Stored setup receipts do not match the live managed-setup metadata."),
    "E35": SetupErrorSpec("setup.transition_state_invalid", "The managed setup transition did not produce the required persisted next state."),
    "E35a": SetupErrorSpec("setup.authorization_state_invalid", "Managed setup authorization did not produce an evaluated result."),
    "E36": SetupErrorSpec("setup.active_flow_changed", "The active managed setup flow changed before the requested operation could be applied."),
    "E37": SetupErrorSpec("setup.action_dispatch_failed", "The managed setup action dispatch failed; action completion is unknown.", allow_dispatcher_cause=True),
    "E38": SetupErrorSpec("setup.action_result_invalid", "The managed setup action dispatch returned an invalid process result; action completion is unknown."),
    "E39": SetupErrorSpec("setup.action_failed", "The managed {operation} action for `{interface}@{version}` returned nonzero process status {returncode}.", frozenset({"operation", "interface", "version", "returncode"})),
    "E40": SetupErrorSpec("setup.verifier_failed", "The verifier for `{interface}@{version}` returned nonzero process status {returncode}; the managed step's completion is unknown.", frozenset({"interface", "version", "returncode"})),
    "E41": SetupErrorSpec("setup.verification_incomplete", "The verifier reported that `{interface}@{version}` is incomplete.", frozenset({"interface", "version"})),
    "E42": SetupErrorSpec("setup.verifier_response_invalid", "The verifier returned {reason}; the managed step's completion is unknown.", frozenset({"reason"})),
    "E44": SetupErrorSpec("setup.settlement_failed", "The verifier confirmed external completion, but the setup manager could not record settlement.", allowed_setup_causes=_SETTLEMENT_CAUSES),
    "E45": SetupErrorSpec("setup.cancellation_failed", "The verifier reported the current step incomplete, but the setup manager could not cancel the flow.", allowed_setup_causes=_CANCELLATION_CAUSES),
    "E46": SetupErrorSpec("setup.cancellation_failed", "The setup manager could not cancel the flow; external completion remains unknown.", allowed_setup_causes=_CANCELLATION_CAUSES),
    "E47": SetupErrorSpec("setup.dispatch_declaration_invalid", "The managed setup runtime dispatch declaration is inconsistent."),
    "E48": SetupErrorSpec("setup.continuation_caller_mismatch", "The runtime caller does not match the continuation caller supplied to `begin`."),
    "E49": SetupErrorSpec("setup.recovery_owner_unverified", "The active flow has no verified owner for ordinary recovery."),
    "E50": SetupErrorSpec("setup.teardown_all_binding_invalid", "Global teardown cannot process a managed binding that declares arguments."),
    "E51": SetupErrorSpec("setup.settlement_failed", "The managed action exited successfully, but the setup manager could not record settlement.", allowed_setup_causes=_SETTLEMENT_CAUSES),
    "E52": SetupErrorSpec("setup.settlement_failed", "The setup manager could not record settlement after the current step was submitted as complete.", allowed_setup_causes=_SETTLEMENT_CAUSES),
    "E53": SetupErrorSpec("setup.verifier_dispatch_failed", "The verifier dispatch failed; the managed step's completion is unknown.", allow_dispatcher_cause=True),
    "E54": SetupErrorSpec("setup.verifier_result_invalid", "The verifier dispatch returned an invalid process result; the managed step's completion is unknown."),
    "E55": SetupErrorSpec("setup.owner_active", "The setup owner process still appears active."),
    "E56": SetupErrorSpec("setup.owner_unknown", "The active setup flow has no process owner metadata."),
})


class SetupFailure(RuntimeError):
    """Internal carrier for one registered setup diagnosis."""

    def __init__(
        self, entry_id: str, *, cause: object | None = None, **context: object
    ) -> None:
        super().__init__(entry_id)
        self.entry_id = entry_id
        self.context = context
        self.cause = cause


class _AtomicFilesAdapter:
    """Narrow adapter around the registered restricted common Python API."""

    @staticmethod
    def _translate(exc: atomic_files.AtomicWriteError) -> Exception:
        message = str(exc)
        if any(
            fragment in message
            for fragment in (
                "predecessor mismatch",
                "destination changed",
                "native replace collision",
            )
        ):
            return LedgerConflict(message)
        return LedgerPathError(message)

    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
        try:
            atomic_files.ensure_private_directory(path.parent, allowed_root=allowed_root)
        except atomic_files.AtomicWriteError as exc:
            raise self._translate(exc) from exc

    @contextmanager
    def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
        try:
            with atomic_files.exclusive_file_lock(
                path, allowed_root=allowed_root, mode=mode
            ):
                yield
        except atomic_files.AtomicWriteError as exc:
            raise self._translate(exc) from exc

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
        try:
            return atomic_files.read_regular_file_bytes(path, allowed_root=allowed_root)
        except FileNotFoundError:
            raise
        except atomic_files.AtomicWriteError as exc:
            raise self._translate(exc) from exc

    def atomic_compare_and_replace_bytes(
        self, path: Path, data: bytes, **kwargs: object
    ) -> None:
        try:
            atomic_files.atomic_compare_and_replace_bytes(path, data, **kwargs)
        except atomic_files.AtomicWriteError as exc:
            raise self._translate(exc) from exc


def _step_payload(step: SetupStep | TeardownStep | None) -> dict[str, object] | None:
    if step is None:
        return None
    if isinstance(step, SetupStep):
        return {
            "interface": step.setup_interface,
            "version": step.setup_version,
            "kind": step.kind,
            "action": "run-setup",
        }
    return {
        "interface": step.teardown_interface,
        "version": step.teardown_version,
        "kind": step.kind,
        "action": step.action,
    }


def _original_payload(continuation: ContinuationIdentity | None) -> dict[str, object] | None:
    if continuation is None:
        return None
    return {
        "caller": continuation.caller,
        "interface": continuation.interface,
        "version": continuation.version,
    }


def _response(
    *,
    flow_id: str | None,
    operation: str,
    state: str,
    current_step: SetupStep | TeardownStep | None,
    original: ContinuationIdentity | None,
    resume_original: bool = False,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "flow_id": flow_id,
        "operation": operation,
        "state": state,
        "current_step": _step_payload(current_step),
        "original": _original_payload(original),
        "resume_original": resume_original,
    }
    payload.update(extra)
    return payload


def _positive_version(value: str) -> int:
    try:
        version = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("version must be a positive integer") from exc
    if version < 1:
        raise argparse.ArgumentTypeError("version must be a positive integer")
    return version


def _encode_arguments(
    raw: str, arguments: tuple[ManagedArgument, ...]
) -> tuple[str, ...]:
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SetupFailure("E02") from exc
    if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
        raise SetupFailure("E02")
    declarations = {argument.name: argument for argument in arguments}
    unknown = set(request) - set(declarations)
    missing = {
        argument.name
        for argument in arguments
        if argument.required and argument.name not in request
    }
    if unknown or missing:
        raise SetupFailure("E03")
    positional: dict[int, str] = {}
    options: list[str] = []
    for argument in arguments:
        if argument.name not in request:
            continue
        value = request[argument.name]
        if not isinstance(value, (str, int, bool)) or isinstance(value, float):
            raise SetupFailure("E04")
        if argument.position is not None:
            if isinstance(value, bool):
                raise SetupFailure("E05")
            positional[argument.position] = str(value)
        else:
            assert argument.option is not None
            if isinstance(value, bool):
                if value:
                    options.append(argument.option)
            else:
                options.extend((argument.option, str(value)))
    if positional and sorted(positional) != list(range(len(positional))):
        raise SetupFailure("E06")
    return tuple(positional[index] for index in sorted(positional)) + tuple(options)


class SetupManager:
    """One finite controller over an injected graph, ledger, and dispatch map."""

    def __init__(
        self,
        *,
        graph,
        store: LedgerStore,
        dispatch: Callable[..., subprocess.CompletedProcess[str]],
        bindings: Mapping[str, ManagedInterfaceBinding],
        new_flow_id: Callable[[], str] | None = None,
        immediate_caller: str | None = None,
    ) -> None:
        self.graph = graph
        self.store = store
        self._dispatch = dispatch
        self._bindings = dict(bindings)
        self._new_flow_id = new_flow_id or (lambda: str(uuid4()))
        self._immediate_caller = immediate_caller

    def _binding(self, setup_interface: str) -> ManagedInterfaceBinding:
        try:
            binding = self._bindings[setup_interface]
        except KeyError as exc:
            raise SetupFailure("E12") from exc
        managed = self.graph.managed_setups.get(setup_interface)
        if managed is None or (
            binding.setup_interface != setup_interface
            or binding.setup_version != managed.setup_version
            or binding.setup_kind != managed.kind
            or binding.setup_verifier_interface != managed.setup_verifier_interface
            or binding.setup_verifier_version != managed.setup_verifier_version
            or binding.teardown_interface != managed.teardown_interface
            or binding.teardown_version != managed.teardown_version
            or binding.teardown_verifier_interface != managed.teardown_verifier_interface
            or binding.teardown_verifier_version != managed.teardown_verifier_version
        ):
            raise SetupFailure("E13")
        return binding

    def _flow_step(
        self, ledger: SetupLedger
    ) -> tuple[ActiveFlow, SetupStep | TeardownStep, ManagedInterfaceBinding]:
        flow = ledger.active_flow
        if flow is None:
            raise SetupFailure("E24")
        managed = self.graph.managed_setups.get(flow.current_step)
        if managed is None:
            raise SetupFailure("E32", mismatch_subject="current step")
        try:
            binding = self._binding(flow.current_step)
        except SetupFailure as exc:
            raise SetupFailure(
                "E32" if flow.operation == "setup" else "E33",
                mismatch_subject="binding",
            ) from exc
        if flow.operation == "setup":
            step: SetupStep | TeardownStep = SetupStep.from_managed(managed)
        else:
            try:
                plan = (
                    teardown_all_plan(self.graph, ledger)
                    if flow.operation == "teardown-all"
                    else teardown_plan(self.graph, flow.root, ledger)
                )
            except BlueprintGraphError as exc:
                raise SetupFailure("E33", mismatch_subject="metadata") from exc
            if not plan or plan[0].setup_interface != flow.current_step:
                raise SetupFailure("E33", mismatch_subject="plan")
            step = plan[0]
        return flow, step, binding

    @staticmethod
    def _expected_interface(step: SetupStep | TeardownStep) -> str:
        return (
            step.setup_interface
            if isinstance(step, SetupStep)
            else step.teardown_interface
        )

    def _require_current(
        self, flow_id: str, interface: str
    ) -> tuple[ActiveFlow, SetupStep | TeardownStep, ManagedInterfaceBinding]:
        flow, step, binding = self._flow_step(self.store.read())
        if flow.flow_id != flow_id or self._expected_interface(step) != interface:
            raise SetupFailure("E25")
        if isinstance(step, TeardownStep) and step.action == "release-claim":
            raise SetupFailure("E26")
        return flow, step, binding

    def _known_active_context(
        self, expected_flow: ActiveFlow, expected_step: SetupStep | TeardownStep
    ) -> tuple[ActiveFlow | None, SetupStep | TeardownStep | None]:
        """Recover only a freshly reconstructed flow owned by this caller."""
        try:
            ledger = self.store.read()
            flow, step, _binding = self._flow_step(ledger)
        except (LedgerError, FlowConflict, ManagerDomainError, ManagerRecoveryError, SetupFailure):
            return None, None
        if (
            flow != expected_flow
            or step != expected_step
            or ledger.schema_version < 2
            or not flow.owner_verified
            or flow.continuation is None
            or not self._immediate_caller
            or self._immediate_caller != flow.continuation.caller
        ):
            return None, None
        return flow, step

    def _dispatch_result(
        self, key: str, *, role: str, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if not isinstance(key, str) or not key:
            raise SetupFailure("E47")
        try:
            result = self._dispatch(key, args=args, stdin=stdin)
        except Exception as exc:
            if not is_dispatch_invocation_error(exc):
                raise
            raise SetupFailure(
                "E37" if role == "action" else "E53", cause=exc
            ) from exc
        if not isinstance(result, subprocess.CompletedProcess):
            raise SetupFailure("E38" if role == "action" else "E54")
        return result

    def _verifier_outcome(
        self,
        flow: ActiveFlow,
        step: SetupStep | TeardownStep,
        binding: ManagedInterfaceBinding,
    ) -> bool | None:
        if isinstance(step, SetupStep):
            key = binding.setup_verifier_dispatch_key
            expected = {"set_up": True}
        else:
            key = binding.teardown_verifier_dispatch_key
            expected = {"torn_down": True}
        if key is None:
            return None
        result = self._dispatch_result(key, role="verifier")
        if result.returncode != 0:
            raise SetupFailure(
                "E40", interface=self._expected_interface(step),
                version=step.setup_version if isinstance(step, SetupStep) else step.teardown_version,
                returncode=result.returncode,
            )
        try:
            decoded = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SetupFailure("E42", reason="malformed JSON") from exc
        if not isinstance(decoded, dict) or any(type(value) is not bool for value in decoded.values()):
            raise SetupFailure("E42", reason="an unsupported response")
        if decoded == expected:
            return True
        false_value = {next(iter(expected)): False}
        if decoded == false_value:
            return False
        raise SetupFailure("E42", reason="an unsupported response")

    def _has_verifier(
        self, step: SetupStep | TeardownStep, binding: ManagedInterfaceBinding
    ) -> bool:
        """Check if a verifier exists for the step."""
        if isinstance(step, SetupStep):
            return binding.setup_verifier_dispatch_key is not None
        else:
            return binding.teardown_verifier_dispatch_key is not None

    def _verify(self, flow: ActiveFlow, step: SetupStep | TeardownStep, binding: ManagedInterfaceBinding) -> bool:
        return self._verifier_outcome(flow, step, binding) is True
    def _settle_verified(
        self, flow: ActiveFlow, step: SetupStep | TeardownStep, entry_id: str
    ) -> tuple[ActiveFlow | None, SetupStep | TeardownStep | None]:
        try:
            if isinstance(step, SetupStep):
                result = record_setup_success(self.store, self.graph, flow.flow_id, step)
            elif flow.operation == "teardown-all":
                result = record_teardown_all_success(self.store, self.graph, flow.flow_id, step)
            else:
                result = record_teardown_success(self.store, self.graph, flow.flow_id, step)
        except BlueprintGraphError as exc:
            cause_id = "E10" if isinstance(step, SetupStep) else "E34"
            raise SetupFailure(entry_id, cause=SetupFailure(cause_id)) from exc
        except FlowConflict as exc:
            cause = (
                SetupFailure(exc.entry_id, **exc.context)
                if exc.entry_id is not None
                else None
            )
            raise SetupFailure(entry_id, cause=cause) from exc
        except LedgerError as exc:
            raise SetupFailure(entry_id, cause=exc) from exc
        except SetupFailure as exc:
            raise SetupFailure(entry_id, cause=exc) from exc
        if result.state == "ready":
            return None, None
        next_flow = self.store.read().active_flow
        if next_flow is None:
            raise ManagerRecoveryError("flow advanced without persisted active state")
        next_step = result.current_step
        if next_step is None:
            raise ManagerRecoveryError("flow advanced without a current step")
        if isinstance(next_step, TeardownStep) and next_step.action in ("release-claim", "invalidate-receipt"):
            return self._advance_internal_teardown(next_flow, next_step)
        return next_flow, next_step

    def _advance_internal_teardown(
        self, flow: ActiveFlow, step: TeardownStep
    ) -> tuple[ActiveFlow | None, SetupStep | TeardownStep | None]:
        """Advance internal teardown actions (release-claim, invalidate-receipt) without external dispatch.

        Uses the appropriate settlement function based on the flow operation type.
        Continues advancing while the current step is an internal action.
        """
        current_flow = flow
        current_step = step
        while (
            isinstance(current_step, TeardownStep)
            and current_step.action in ("release-claim", "invalidate-receipt")
        ):
            if flow.operation == "teardown-all":
                result = record_teardown_all_success(
                    self.store, self.graph, current_flow.flow_id, current_step
                )
            else:
                result = record_teardown_success(
                    self.store, self.graph, current_flow.flow_id, current_step
                )
            if result.state == "ready":
                return None, None
            persisted = self.store.read().active_flow
            if persisted is None or result.current_step is None:
                raise ManagerRecoveryError("internal teardown advanced without persisted state")
            current_flow = persisted
            current_step = result.current_step
        return current_flow, current_step

    def _result_response(
        self,
        operation: str,
        original: ContinuationIdentity | None,
        flow: ActiveFlow | None,
        step: SetupStep | TeardownStep | None,
    ) -> tuple[int, dict[str, object]]:
        state = "ready" if step is None else "run-step"
        return 0, _response(
            flow_id=None if flow is None else flow.flow_id,
            operation=operation,
            state=state,
            current_step=step,
            original=original,
            resume_original=False,
        )

    def _domain_failure(
        self,
        response_operation: str,
        entry_id: str | SetupFailure | LedgerError,
        *,
        state_name: str = "failed",
        flow: ActiveFlow | None = None,
        step: SetupStep | TeardownStep | None = None,
        original: ContinuationIdentity | None = None,
        cause: object | None = None,
        clue_evidence: bool = False,
        **context: object,
    ) -> tuple[int, dict[str, object]]:
        if isinstance(entry_id, SetupFailure):
            cause = entry_id.cause if cause is None else cause
            context = entry_id.context
            entry_id = entry_id.entry_id
        elif isinstance(entry_id, LedgerError):
            caught: BaseException | None = entry_id
            permission = False
            for _ in range(3):
                if isinstance(caught, PermissionError):
                    permission = True
                    break
                caught = caught.__cause__ if caught is not None else None
            if isinstance(entry_id, LedgerCapabilityError):
                entry_id = "E15"
            elif permission:
                entry_id = "E20p"
            elif isinstance(entry_id, FlowConflict):
                if entry_id.entry_id is None:
                    raise ValueError("unclassified managed flow conflict")
                context = entry_id.context
                entry_id = entry_id.entry_id
            elif isinstance(entry_id, LedgerFormatError):
                entry_id = "E21"
            elif isinstance(entry_id, LedgerWriteUncertain):
                entry_id = "E23"
            elif isinstance(entry_id, LedgerConflict):
                entry_id = "E22"
                clue_evidence = True
            else:
                entry_id = "E20"
        spec = SETUP_ERROR_SPECS[entry_id]
        if set(context) != set(spec.context_fields):
            raise ValueError(f"invalid context for setup diagnosis {entry_id}")
        if state_name == "recovery-required":
            if flow is None:
                state_name = "failed"
            else:
                if step is None:
                    flow = None
                else:
                    flow, step = self._known_active_context(flow, step)
                if flow is None:
                    state_name = "failed"
                    original = None
        diagnosis: dict[str, object] = {
            "error": spec.message.format(**context),
            "error_code": spec.code,
        }
        if clue_evidence and spec.clue is not None:
            diagnosis["clues"] = [spec.clue]
        if cause is not None:
            dispatcher_cause = _reduced_dispatcher_cause(
                cause, require_direct=entry_id == "E10"
            )
            if spec.allow_dispatcher_cause and dispatcher_cause is not None:
                diagnosis["cause"] = dispatcher_cause
            elif isinstance(cause, SetupFailure):
                if cause.entry_id in spec.allowed_setup_causes:
                    cause_spec = SETUP_ERROR_SPECS[cause.entry_id]
                    if set(cause.context) != set(cause_spec.context_fields):
                        raise ValueError(
                            f"invalid context for setup diagnosis {cause.entry_id}"
                        )
                    diagnosis["cause"] = {
                        "schema_version": SCHEMA_VERSION,
                        "code": cause_spec.code,
                        "message": cause_spec.message.format(**cause.context),
                    }
            elif isinstance(cause, LedgerError):
                caught: BaseException | None = cause
                denied = False
                for _ in range(3):
                    if isinstance(caught, PermissionError):
                        denied = True
                        break
                    caught = caught.__cause__ if caught is not None else None
                if isinstance(cause, FlowConflict):
                    cause_id = cause.entry_id
                    cause_context = cause.context
                elif denied:
                    cause_id = "E20p"
                    cause_context = {}
                elif isinstance(cause, LedgerFormatError):
                    cause_id = "E21"
                    cause_context = {}
                elif isinstance(cause, LedgerWriteUncertain):
                    cause_id = "E23"
                    cause_context = {}
                elif isinstance(cause, LedgerConflict):
                    cause_id = "E22"
                    cause_context = {}
                else:
                    cause_id = "E20"
                    cause_context = {}
                if cause_id in spec.allowed_setup_causes:
                    cause_spec = SETUP_ERROR_SPECS[cause_id]
                    if set(cause_context) != set(cause_spec.context_fields):
                        raise ValueError(
                            f"invalid context for setup diagnosis {cause_id}"
                        )
                    diagnosis["cause"] = {
                        "schema_version": SCHEMA_VERSION,
                        "code": cause_spec.code,
                        "message": cause_spec.message.format(**cause_context),
                    }
        if state_name == "recovery-required" and flow is not None:
            diagnosis["recovery"] = {
                "interface": "setup-interface-manager.interface.recover",
                "version": 1,
                "flow_id": flow.flow_id,
                "actions": ["retry", "cancel"],
            }
        return 2, _response(
            flow_id=None if flow is None else flow.flow_id,
            operation=response_operation,
            state=state_name,
            current_step=step,
            original=original,
            resume_original=False,
            **diagnosis,
        )

    def _run_teardown_all(
        self, flow: ActiveFlow, step: TeardownStep
    ) -> tuple[int, dict[str, object]]:
        while True:
            if (persisted := self._flow_step(self.store.read()))[:2] != (flow, step): raise ManagerRecoveryError("global teardown changed before dispatch")
            flow, step, binding = persisted
            if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                next_flow, next_step = self._advance_internal_teardown(flow, step)
                if next_step is None:
                    return self._result_response(flow.operation, None, None, None)
                if next_flow is None or not isinstance(next_step, TeardownStep):
                    raise ManagerRecoveryError("global teardown advanced without persisted state")
                flow, step = next_flow, next_step
                continue
            if binding.setup_kind == "markdown":
                return 0, _response(
                    flow_id=flow.flow_id, operation=flow.operation, state="awaiting-settlement",
                    current_step=step, original=None,
                    instructions=binding.teardown_instructions,
                )
            action = self._dispatch_result(binding.teardown_dispatch_key, role="action")
            if action.returncode != 0:
                return self._domain_failure(
                    flow.operation, "E39", state_name="recovery-required", flow=flow, step=step,
                    operation="teardown", interface=step.teardown_interface,
                    version=step.teardown_version, returncode=action.returncode,
                )
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation, "E41", state_name="recovery-required",
                        flow=flow, step=step, interface=step.teardown_interface,
                        version=step.teardown_version,
                    )
            next_flow, next_step = self._settle_verified(
                flow, step, "E44" if self._has_verifier(step, binding) else "E51"
            )
            if next_step is None:
                return self._result_response(flow.operation, None, None, None)
            if next_flow is None or not isinstance(next_step, TeardownStep):
                raise ManagerRecoveryError("global teardown advanced without persisted state")
            flow, step = next_flow, next_step
    def teardown_all(
        self, flow_id: str | None = None, owner: FlowOwner | None = None
    ) -> tuple[int, dict[str, object]]:
        flow: ActiveFlow | None = None
        step: TeardownStep | None = None
        try:
            ledger = self.store.read()
            if ledger.active_flow is not None:
                active, active_step, _binding = self._flow_step(ledger)
                return self._domain_failure(
                    "teardown-all", "E00", state_name="busy",
                    flow=active, step=active_step, original=None,
                )
            plan = teardown_all_plan(self.graph, ledger)
            if not plan:
                return self._result_response("teardown-all", None, None, None)
            for candidate in plan:
                binding = self._binding(candidate.setup_interface)
                if binding.arguments:
                    raise SetupFailure("E50")
            step = plan[0]
            flow = ActiveFlow(
                flow_id or self._new_flow_id(), "teardown-all", None,
                step.setup_interface, (), None, owner=owner,
            )
            def start(current: SetupLedger) -> SetupLedger:
                if current.active_flow is not None or teardown_all_plan(self.graph, current) != plan:
                    raise FlowConflict("managed teardown changed before the flow began")
                return begin_flow(current, flow)
            self.store.update(start)
            return self._run_teardown_all(flow, step)
        except SetupFailure as exc:
            return self._domain_failure("teardown-all", exc)
        except LedgerConflict as exc:
            if isinstance(exc, LedgerWriteUncertain):
                return self._domain_failure("teardown-all", exc)
            return self._domain_failure(
                "teardown-all", "E31", clue_evidence=True
            )
        except FlowConflict:
            return self._domain_failure(
                "teardown-all", "E31", clue_evidence=True
            )
        except LedgerError as exc:
            return self._domain_failure("teardown-all", exc)
        except (BlueprintGraphError, ManagerDomainError):
            return self._domain_failure("teardown-all", "E34")
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "teardown-all", "E36", state_name="recovery-required",
                flow=flow, step=step,
            )
    def status(self, target_interface: str) -> tuple[int, dict[str, object]]:
        try:
            ledger = self.store.read()
            result = evaluate_target(self.graph, target_interface, ledger)
            flow = ledger.active_flow if result.code == "setup_busy" else None
            payload: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "code": result.code,
                "root_setup_interface": (
                    result.root_setup_interface if flow is None else flow.root or flow.current_step
                ),
                "pending_stack": [
                    _step_payload(step) for step in result.pending_stack
                ],
                "flow_id": result.flow_id,
            }
            if flow is not None:
                payload["current_step"] = flow.current_step
                payload["owner"] = None if flow.owner is None else {
                    "host": flow.owner.host,
                    "pid": flow.owner.pid,
                    "started_at": flow.owner.started_at,
                }
            return 0, payload
        except FlowConflict:
            return self._domain_failure("status", "E10")
        except LedgerError as exc:
            return self._domain_failure("status", exc)

    def authorize(
        self,
        target_interface: str,
        original_caller: str,
        original_interface: str,
        original_version: int,
    ) -> tuple[int, dict[str, object]]:
        original = ContinuationIdentity(
            original_caller, original_interface, original_version
        )
        try:
            result = authorize_ready_root(self.store, self.graph, target_interface)
        except FlowConflict as exc:
            return self._domain_failure("authorize", exc, original=original)
        except LedgerError as exc:
            return self._domain_failure("authorize", exc, original=original)
        if result is None:
            return self._domain_failure("authorize", "E35a", original=original)
        if result.code in {"unmanaged", "ready"}:
            return 0, _response(
                flow_id=None,
                operation="authorize",
                state="ready",
                current_step=None,
                original=original,
                resume_original=True,
            )
        state_name = "busy" if result.code == "setup_busy" else "failed"
        step = result.pending_stack[-1] if result.pending_stack else None
        flow = None
        if result.code == "setup_busy":
            try:
                flow, step, _binding = self._flow_step(self.store.read())
            except (LedgerError, SetupFailure):
                flow, step = None, None
        return self._domain_failure(
            "authorize",
            "E30" if result.code == "setup_busy" else "E29",
            state_name=state_name,
            flow=flow,
            step=step,
            original=None if result.code == "setup_busy" else original,
        )

    def begin(
        self,
        operation: str,
        root_setup_interface: str,
        original_caller: str,
        original_interface: str,
        original_version: int,
        flow_id: str | None = None,
        owner: FlowOwner | None = None,
    ) -> tuple[int, dict[str, object]]:
        original = ContinuationIdentity(
            original_caller, original_interface, original_version
        )
        if operation not in {"setup", "teardown"}:
            return self._domain_failure("begin", "E01", original=original)
        if not self._immediate_caller or self._immediate_caller != original_caller:
            return self._domain_failure("begin", "E48")
        try:
            ledger = self.store.read()
            if ledger.active_flow is not None:
                flow, step, _binding = self._flow_step(ledger)
                return self._domain_failure(
                    operation,
                    "E00",
                    state_name="busy",
                    flow=flow,
                    step=step,
                    original=None,
                )
            if root_setup_interface not in self.graph.managed_setups:
                raise SetupFailure("E27")
            if operation == "setup":
                evaluation = evaluate_target(self.graph, root_setup_interface, ledger)
                if evaluation.code == "ready":
                    return 0, _response(
                        flow_id=None,
                        operation="setup",
                        state="ready",
                        current_step=None,
                        original=original,
                    )
                if evaluation.code != "setup_required" or not evaluation.pending_stack:
                    raise SetupFailure("E28")
                step: SetupStep | TeardownStep = evaluation.pending_stack[-1]
                setup_order = tuple(
                    managed.setup_interface
                    for managed in managed_setup_order(
                        self.graph, root_setup_interface
                    )
                )
                current_index = setup_order.index(step.setup_interface)
                verified_steps = setup_order[:current_index]
            else:
                plan = teardown_plan(self.graph, root_setup_interface, ledger)
                if not plan:
                    return 0, _response(
                        flow_id=None,
                        operation="teardown",
                        state="ready",
                        current_step=None,
                        original=original,
                    )
                step = plan[0]
                verified_steps = ()
            self._binding(step.setup_interface)
            flow = ActiveFlow(
                flow_id=flow_id or self._new_flow_id(),
                operation=operation,  # type: ignore[arg-type]
                root=root_setup_interface,
                current_step=step.setup_interface,
                verified_steps=verified_steps,
                continuation=original,
                owner_verified=bool(
                    self._immediate_caller
                    and self._immediate_caller == original_caller
                ),
                owner=owner,
            )

            def start(current: SetupLedger) -> SetupLedger:
                if operation == "setup":
                    live = evaluate_target(
                        self.graph, root_setup_interface, current
                    )
                    if (
                        live.code != "setup_required"
                        or not live.pending_stack
                        or live.pending_stack[-1] != step
                    ):
                        raise FlowConflict(
                            "managed setup changed before the flow began"
                        )
                    current = claim_receipts(
                        current, root_setup_interface, verified_steps
                    )
                return begin_flow(current, flow)

            self.store.update(start)
            if operation == "teardown":
                if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                    flow, step = self._advance_internal_teardown(flow, step)
            return self._result_response(operation, original, flow, step)
        except SetupFailure as exc:
            return self._domain_failure(operation, exc, original=original)
        except LedgerConflict as exc:
            if isinstance(exc, LedgerWriteUncertain):
                return self._domain_failure(operation, exc, original=original)
            return self._domain_failure(
                operation, "E31", original=original, clue_evidence=True
            )
        except FlowConflict as exc:
            if exc.entry_id == "E10":
                return self._domain_failure(operation, exc, original=original)
            return self._domain_failure(operation, "E31", original=original, clue_evidence=True)
        except LedgerError as exc:
            return self._domain_failure(operation, exc, original=original)
        except ManagerDomainError:
            return self._domain_failure(operation, "E31", original=original, clue_evidence=True)
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                operation, "E35", state_name="recovery-required", original=original
            )

    def authorize_markdown_call(
        self, flow_id: str, target_interface: str, target_version: int
    ) -> tuple[int, dict[str, object]]:
        try:
            ledger = self.store.read()
            flow = ledger.active_flow
            if flow is None:
                raise SetupFailure("E24")
            if flow.flow_id != flow_id:
                raise SetupFailure("E25")
            managed = self.graph.managed_setups.get(flow.current_step)
            if managed is None:
                raise SetupFailure("E32", mismatch_subject="current step")
            try:
                binding = self._binding(flow.current_step)
            except SetupFailure as exc:
                raise SetupFailure("E32", mismatch_subject="binding") from exc
            if binding.setup_kind != "markdown":
                raise SetupFailure("E26")
            if flow.operation != "setup":
                raise SetupFailure("E26")
            if (target_interface, target_version) not in binding.helper_allowlist:
                raise SetupFailure("E26")
            step: SetupStep | TeardownStep = SetupStep.from_managed(managed)
            return 0, _response(
                flow_id=flow.flow_id,
                operation=flow.operation,
                state="authorized-markdown-call",
                current_step=step,
                original=flow.continuation,
                interface=target_interface,
                version=target_version,
            )
        except SetupFailure as exc:
            return self._domain_failure("authorize-markdown-call", exc)
        except LedgerError as exc:
            return self._domain_failure("authorize-markdown-call", exc)
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "authorize-markdown-call", "E35"
            )

    def run_markdown(self, flow_id: str, interface: str) -> tuple[int, dict[str, object]]:
        try:
            flow, step, binding = self._require_current(flow_id, interface)
            if binding.setup_kind != "markdown":
                raise SetupFailure("E26")
            instructions = (
                binding.setup_instructions
                if isinstance(step, SetupStep)
                else binding.teardown_instructions
            )
            return 0, _response(
                flow_id=flow.flow_id,
                operation=flow.operation,
                state="awaiting-settlement",
                current_step=step,
                original=flow.continuation,
                instructions=instructions,
            )
        except SetupFailure as exc:
            return self._domain_failure("run-markdown", exc)
        except LedgerError as exc:
            return self._domain_failure("run-markdown", exc)
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "run-markdown", "E35"
            )

    def run_python(
        self, flow_id: str, interface: str, stdin_request: str
    ) -> tuple[int, dict[str, object]]:
        try:
            flow, step, binding = self._require_current(flow_id, interface)
            if binding.setup_kind != "python":
                raise SetupFailure("E26")
            argv = _encode_arguments(stdin_request, binding.arguments)
            action_key = (
                binding.setup_dispatch_key
                if isinstance(step, SetupStep)
                else binding.teardown_dispatch_key
            )
            if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                raise SetupFailure("E26")
            action = self._dispatch_result(action_key, role="action", args=argv)
            if action.returncode != 0:
                return self._domain_failure(
                    flow.operation,
                    "E39",
                    state_name="recovery-required",
                    flow=flow,
                    step=step,
                    original=flow.continuation,
                    operation="setup" if isinstance(step, SetupStep) else "teardown",
                    interface=self._expected_interface(step),
                    version=step.setup_version if isinstance(step, SetupStep) else step.teardown_version,
                    returncode=action.returncode,
                )
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation,
                        "E41",
                        state_name="recovery-required",
                        flow=flow,
                        step=step,
                        original=flow.continuation,
                        interface=self._expected_interface(step),
                        version=step.setup_version if isinstance(step, SetupStep) else step.teardown_version,
                    )
            next_flow, next_step = self._settle_verified(
                flow, step, "E44" if self._has_verifier(step, binding) else "E51"
            )
            return self._result_response(
                flow.operation, flow.continuation, next_flow, next_step
            )
        except SetupFailure as exc:
            if exc.entry_id in {"E02", "E03", "E04", "E05", "E06"}:
                spec = SETUP_ERROR_SPECS[exc.entry_id]
                return 64, _response(
                    flow_id=None, operation="run-python", state="failed",
                    current_step=None, original=None, error=spec.message,
                    error_code=spec.code,
                )
            recovery = exc.entry_id in {"E37", "E38", "E40", "E41", "E42", "E44", "E51", "E52", "E53", "E54"}
            return self._domain_failure(
                locals().get("flow").operation if locals().get("flow") else "run-python",
                exc, state_name="recovery-required" if recovery else "failed",
                flow=locals().get("flow"), step=locals().get("step"),
                original=locals().get("flow").continuation if locals().get("flow") else None,
            )
        except LedgerError as exc:
            return self._domain_failure("run-python", exc)
        except ManagerRecoveryError as exc:
            try:
                ledger = self.store.read()
                flow = ledger.active_flow
                step = self._flow_step(ledger)[1] if flow is not None else None
            except Exception:
                flow, step = None, None
            return self._domain_failure(
                "run-python",
                "E35",
                state_name="recovery-required",
                flow=flow,
                step=step,
                original=None if flow is None else flow.continuation,
            )

    def settle(self, flow_id: str, interface: str) -> tuple[int, dict[str, object]]:
        flow: ActiveFlow | None = None
        step: SetupStep | TeardownStep | None = None
        try:
            flow, step, binding = self._require_current(flow_id, interface)
            if binding.setup_kind != "markdown":
                raise SetupFailure("E26")
            if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                raise SetupFailure("E26")
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation,
                        "E41",
                        state_name="recovery-required",
                        flow=flow,
                        step=step,
                        original=flow.continuation,
                        interface=self._expected_interface(step),
                        version=step.setup_version if isinstance(step, SetupStep) else step.teardown_version,
                    )
            next_flow, next_step = self._settle_verified(
                flow, step, "E44" if self._has_verifier(step, binding) else "E52"
            )
            if flow.operation == "teardown-all" and next_step is not None:
                assert next_flow is not None and isinstance(next_step, TeardownStep)
                return self._run_teardown_all(next_flow, next_step)
            return self._result_response(
                flow.operation, flow.continuation, next_flow, next_step
            )
        except SetupFailure as exc:
            recovery = exc.entry_id in {"E40", "E41", "E42", "E44", "E51", "E52", "E53", "E54"}
            return self._domain_failure(
                flow.operation if flow is not None else "settle",
                exc, state_name="recovery-required" if recovery else "failed",
                flow=flow, step=step,
                original=None if flow is None else flow.continuation,
            )
        except LedgerError as exc:
            return self._domain_failure("settle", exc)
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "settle" if flow is None else flow.operation,
                "E35",
                state_name="recovery-required",
                flow=flow,
                step=step,
                original=None if flow is None else flow.continuation,
            )

    def invalidate(self, setup_interface: str) -> tuple[int, dict[str, object]]:
        try:
            if self.store.read().active_flow is not None:
                flow, step, _binding = self._flow_step(self.store.read())
                return self._domain_failure(
                    "invalidate",
                    "E00",
                    state_name="busy",
                    flow=flow,
                    step=step,
                    original=None,
                )
            removed = invalidate_receipts(self.store, self.graph, setup_interface)
            return 0, _response(
                flow_id=None,
                operation="invalidate",
                state="ready",
                current_step=None,
                original=None,
                removed=list(removed),
            )
        except FlowConflict as exc:
            return self._domain_failure("invalidate", exc)
        except LedgerError as exc:
            return self._domain_failure("invalidate", exc)

    def recover(
        self, flow_id: str, action: str, *, bypass_owner: bool = False
    ) -> tuple[int, dict[str, object]]:
        flow: ActiveFlow | None = None
        step: SetupStep | TeardownStep | None = None
        cancellation_failure: str | None = None
        try:
            ledger = self.store.read()
            flow, step, binding = self._flow_step(ledger)
            if flow.flow_id != flow_id:
                raise SetupFailure("E25")
            if not bypass_owner and (
                ledger.schema_version < 2
                or not flow.owner_verified
                or flow.continuation is None
                or not self._immediate_caller
                or self._immediate_caller != flow.continuation.caller
            ):
                raise SetupFailure("E49")
            if action == "retry":
                if (
                    isinstance(step, TeardownStep)
                    and step.action in ("release-claim", "invalidate-receipt")
                ):
                    next_flow, next_step = self._advance_internal_teardown(flow, step)
                    return self._result_response(
                        flow.operation, flow.continuation, next_flow, next_step
                    )
                if self._has_verifier(step, binding):
                    outcome = (
                        self._verifier_outcome(flow, step, binding)
                        if flow.operation == "teardown-all"
                        else self._verify(flow, step, binding)
                    )
                    if outcome is None:
                        raise ManagerRecoveryError("declared verifier completion is uncertain")
                    if outcome:
                        next_flow, next_step = self._settle_verified(flow, step, "E44")
                        if flow.operation == "teardown-all" and next_step is not None:
                            assert next_flow is not None and isinstance(next_step, TeardownStep)
                            return self._run_teardown_all(next_flow, next_step)
                        return self._result_response(
                            flow.operation, flow.continuation, next_flow, next_step
                        )
                    if flow.operation == "teardown-all":
                        assert isinstance(step, TeardownStep)
                        return self._run_teardown_all(flow, step)
                    extra = {}
                    if binding.setup_kind == "markdown":
                        extra["instructions"] = (
                            binding.setup_instructions
                            if isinstance(step, SetupStep)
                            else binding.teardown_instructions
                        )
                    return 0, _response(
                        flow_id=flow.flow_id,
                        operation=flow.operation,
                        state="run-step",
                        current_step=step,
                        original=flow.continuation,
                        **extra,
                    )
                else:
                    extra = {}
                    if binding.setup_kind == "markdown":
                        extra["instructions"] = (
                            binding.setup_instructions
                            if isinstance(step, SetupStep)
                            else binding.teardown_instructions
                        )
                    return 0, _response(
                        flow_id=flow.flow_id,
                        operation=flow.operation,
                        state="run-step",
                        current_step=step,
                        original=flow.continuation,
                        **extra,
                    )
            if action != "cancel":
                raise ManagerUsageError("recovery action must be retry or cancel")

            has_verifier = self._has_verifier(step, binding)
            outcome = self._verifier_outcome(flow, step, binding) if has_verifier else None
            if has_verifier and outcome is None:
                raise ManagerRecoveryError("declared verifier completion is uncertain")
            if flow.operation != "teardown-all" and outcome:
                next_flow, next_step = self._settle_verified(flow, step, "E44")
                return self._result_response(
                    flow.operation, flow.continuation, next_flow, next_step
                )
            cancellation_failure = "E45" if has_verifier and outcome is False else "E46"

            def cancel(current: SetupLedger) -> SetupLedger:
                try:
                    active, live_step, _binding = self._flow_step(current)
                except SetupFailure as exc:
                    raise FlowConflict(
                        "active flow changed before cancellation", entry_id="E36"
                    ) from exc
                if (
                    current.schema_version < 2
                    or active != flow
                    or live_step != step
                    or not bypass_owner and (
                        not active.owner_verified
                        or active.continuation is None
                        or not self._immediate_caller
                        or self._immediate_caller != active.continuation.caller
                    )
                ):
                    raise FlowConflict(
                        "active flow changed before cancellation", entry_id="E36"
                    )
                interfaces = dict(current.interfaces)
                if (
                    flow.operation == "teardown-all"
                    and outcome is not False
                ) or (
                    isinstance(step, TeardownStep) and not has_verifier
                ):
                    interfaces.pop(step.setup_interface, None)
                for setup_interface in active.verified_steps:
                    receipt = interfaces.get(setup_interface)
                    if receipt is not None:
                        interfaces[setup_interface] = SetupReceipt(
                            receipt.version, receipt.required_by - {active.root}
                        )
                return SetupLedger(
                    interfaces=interfaces,
                    active_flow=None,
                    schema_version=current.schema_version,
                )

            self.store.update(cancel)
            return 0, _response(
                flow_id=None,
                operation=flow.operation,
                state="ready",
                current_step=None,
                original=flow.continuation,
                resume_original=False,
            )
        except ManagerUsageError:
            return 64, _response(
                flow_id=None,
                operation="recover",
                state="failed",
                current_step=None,
                original=None,
                error=SETUP_ERROR_SPECS["E01"].message,
                error_code=SETUP_ERROR_SPECS["E01"].code,
            )
        except SetupFailure as exc:
            if exc.entry_id == "E49":
                return self._domain_failure("recover", exc)
            recovery = exc.entry_id in {"E40", "E41", "E42", "E44", "E45", "E46", "E51", "E52", "E53", "E54"}
            return self._domain_failure(
                flow.operation if flow is not None else "recover",
                exc, state_name="recovery-required" if recovery else "failed",
                flow=flow, step=step,
                original=None if flow is None else flow.continuation,
            )
        except LedgerError as exc:
            if cancellation_failure is not None and flow is not None:
                failure = SetupFailure(cancellation_failure, cause=exc)
                return self._domain_failure(
                    flow.operation, failure, state_name="recovery-required",
                    flow=flow, step=step, original=flow.continuation,
                )
            return self._domain_failure("recover", exc)
        except (ManagerDomainError, FlowConflict):
            if cancellation_failure is not None and flow is not None:
                failure = SetupFailure(
                    cancellation_failure, cause=SetupFailure("E36")
                )
                return self._domain_failure(
                    flow.operation, failure, state_name="recovery-required",
                    flow=flow, step=step, original=flow.continuation,
                )
            return self._domain_failure("recover", "E36")
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "recover" if flow is None else flow.operation,
                "E46",
                state_name="recovery-required",
                flow=flow,
                step=step,
                original=None if flow is None else flow.continuation,
            )

    def recover_busy(
        self, flow_id: str, *, force: bool = False
    ) -> tuple[int, dict[str, object]]:
        try:
            flow, _step, _binding = self._flow_step(self.store.read())
            if flow.flow_id != flow_id:
                raise SetupFailure("E25")
            if not force and flow.owner is None:
                raise SetupFailure("E56")
            if force:
                code, payload = self.recover(flow_id, "cancel", bypass_owner=True)
                payload["forced"] = True
                return code, payload
            path = self.store.flow_lock_path(flow_id)
            try:
                with atomic_files.exclusive_file_lock(
                    path, allowed_root=Path(path.anchor), mode=0o600, blocking=False
                ):
                    return self.recover(flow_id, "cancel", bypass_owner=True)
            except atomic_files.AtomicLockUnavailable:
                raise SetupFailure("E55") from None
        except SetupFailure as exc:
            return self._domain_failure("recover-busy", exc)
        except LedgerError as exc:
            return self._domain_failure("recover-busy", exc)
        except atomic_files.AtomicWriteError:
            return self._domain_failure("recover-busy", "E20")


class _StrictParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ManagerUsageError(message)


class _ManagerInterface(PythonMachineInterface):
    """Shared runtime wiring; subclasses expose one exact public signature."""

    parser_class = _StrictParser
    dispatches = PRODUCTION_DISPATCHES
    operation = "manager"

    def __init__(
        self,
        manager_factory: Callable[[], SetupManager] | None = None,
        graph_loader: Callable[[Path], object] = load_repository_blueprint_graph,
        bindings: Mapping[str, ManagedInterfaceBinding] = PRODUCTION_BINDINGS,
    ) -> None:
        self._manager_factory = manager_factory
        self._graph_loader = graph_loader
        self._bindings = dict(bindings)

    def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]):
        try:
            return parser.parse_args(argv)
        except ManagerUsageError as exc:
            return argparse.Namespace(_manager_usage_error=str(exc))

    def _build_direct_graph(self, target_interface: str):
        context = runtime_dispatch_context(self)
        if context.repository_config is None:
            raise ManagerBootstrapError("E08")
        try:
            configuration = load_repository_configuration(
                Path(context.repository_config)
            )
            return load_direct_setup_graph(configuration, target_interface)
        except RepositoryConfigurationError as exc:
            raise ManagerBootstrapError("E09") from exc
        except DirectBlueprintError as exc:
            raise ManagerBootstrapError("E10", cause=exc) from exc
        except BlueprintGraphError as exc:
            raise ManagerBootstrapError("E10") from exc
        except OSError as exc:
            caught: BaseException | None = exc
            denied = False
            for _ in range(3):
                if isinstance(caught, PermissionError):
                    denied = True
                    break
                caught = caught.__cause__ if caught is not None else None
            raise ManagerBootstrapError("E11p" if denied else "E11") from exc

    def build_graph(self, args: argparse.Namespace, store: LedgerStore | None = None):
        target = None
        if self._graph_loader is load_repository_blueprint_graph:
            flow = store.read().active_flow if store is not None else None
            if (
                self.operation in {
                    "run-markdown",
                    "run-python",
                    "settle",
                    "recover",
                    "recover-busy",
                    "authorize-markdown-call",
                }
                and flow is not None
                and flow.operation == "setup"
            ):
                target = flow.root
            elif (
                flow is None
                and self.operation == "begin"
                and getattr(args, "operation", None) == "setup"
            ):
                target = args.root_setup
        if target is not None:
            return self._build_direct_graph(target)
        context = runtime_dispatch_context(self)
        repo_root = Path(context.repo_root or REPO_ROOT)
        try:
            return self._graph_loader(repo_root)
        except BlueprintGraphError as exc:
            cause = exc if hasattr(exc, "as_payload") else None
            raise ManagerBootstrapError("E10", cause=cause) from exc
        except OSError as exc:
            caught: BaseException | None = exc
            denied = False
            for _ in range(3):
                if isinstance(caught, PermissionError):
                    denied = True
                    break
                caught = caught.__cause__ if caught is not None else None
            raise ManagerBootstrapError("E11p" if denied else "E11") from exc

    def build_manager(self, args: argparse.Namespace) -> SetupManager:
        if self._manager_factory is not None:
            return self._manager_factory()
        if PRODUCTION_DECLARATION_INVALID or any(
            key != binding.setup_interface
            for key, binding in self._bindings.items()
        ):
            raise ManagerBootstrapError("E14")
        try:
            getter = self.dispatch(GETTER_KEY, args=("setup-status",), text=True)
        except Exception as exc:
            if not is_dispatch_invocation_error(exc):
                raise
            cause = exc if hasattr(exc, "as_payload") else None
            raise ManagerBootstrapError("E16", cause=cause) from exc
        if not isinstance(getter, subprocess.CompletedProcess):
            raise ManagerBootstrapError("E18")
        if getter.returncode != 0:
            raise ManagerBootstrapError("E17", returncode=getter.returncode)
        if not isinstance(getter.stdout, str):
            raise ManagerBootstrapError("E19", expected="text")
        lines = getter.stdout.splitlines()
        if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
            raise ManagerBootstrapError("E19", expected="one absolute path")
        path = Path(lines[0])
        if not path.is_absolute():
            raise ManagerBootstrapError("E19", expected="one absolute path")
        store = LedgerStore._from_atomic_files(path, _AtomicFilesAdapter())
        graph = self.build_graph(args, store)

        def dispatch(
            key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            return self.dispatch(key, args=args, stdin=stdin, text=True)

        return SetupManager(
            graph=graph,
            store=store,
            dispatch=dispatch,
            bindings=self._bindings,
            immediate_caller=runtime_dispatch_context(self).immediate_caller_module_id,
        )

    def _malformed(self, message: str) -> int:
        payload = _response(
            flow_id=None,
            operation=self.operation,
            state="failed",
            current_step=None,
            original=None,
            error=SETUP_ERROR_SPECS["E01"].message,
            error_code=SETUP_ERROR_SPECS["E01"].code,
        )
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 64

    def _emit(self, result: tuple[int, dict[str, object]]) -> int:
        code, payload = result
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return code

    def run(self, args: argparse.Namespace) -> int:
        message = getattr(args, "_manager_usage_error", None)
        if isinstance(message, str):
            return self._malformed(message)
        try:
            return self._emit(self.invoke(self.build_manager(args), args))
        except ManagerUsageError as exc:
            return self._malformed(str(exc))
        except (LedgerError, ManagerBootstrapError) as exc:
            entry_id = getattr(exc, "entry_id", None)
            if entry_id is None:
                entry_id = "E20" if isinstance(exc, LedgerError) else "E10"
            spec = SETUP_ERROR_SPECS[entry_id]
            extra: dict[str, object] = {}
            nested = getattr(exc, "cause", None)
            reduced = _reduced_dispatcher_cause(
                nested, require_direct=entry_id == "E10"
            )
            if spec.allow_dispatcher_cause and reduced is not None:
                extra["cause"] = reduced
            return self._emit(
                (2, _response(
                    flow_id=None,
                    operation=self.operation,
                    state="failed",
                    current_step=None,
                    original=None,
                    error=spec.message.format(**getattr(exc, "context", {})),
                    error_code=spec.code,
                    **extra,
                ))
            )

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        raise NotImplementedError


class _DirectPreflightInterface(_ManagerInterface):
    """Load only one parsed target's setup closure for read/authorize preflight."""

    def build_graph(self, args: argparse.Namespace, store: LedgerStore | None = None):
        return self._build_direct_graph(args.target_interface)


class StatusInterface(_DirectPreflightInterface):
    operation = "status"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("target_interface")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.status(args.target_interface)


class AuthorizeInterface(_DirectPreflightInterface):
    operation = "authorize"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("target_interface")
        parser.add_argument("original_caller")
        parser.add_argument("original_interface")
        parser.add_argument("original_version", type=_positive_version)
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.authorize(
            args.target_interface,
            args.original_caller,
            args.original_interface,
            args.original_version,
        )


class BeginInterface(_ManagerInterface):
    operation = "begin"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("operation", choices=("setup", "teardown"))
        parser.add_argument("root_setup")
        parser.add_argument("original_caller")
        parser.add_argument("original_interface")
        parser.add_argument("original_version", type=_positive_version)
        parser.add_argument("flow_id", nargs="?")
        parser.add_argument("owner_host", nargs="?")
        parser.add_argument("owner_pid", nargs="?", type=_positive_version)
        parser.add_argument("owner_started_at", nargs="?")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        values = (args.flow_id, args.owner_host, args.owner_pid, args.owner_started_at)
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ManagerUsageError("begin owner metadata must be complete")
        owner = None if args.flow_id is None else FlowOwner(
            args.owner_host, args.owner_pid, args.owner_started_at
        )
        return controller.begin(
            args.operation,
            args.root_setup,
            args.original_caller,
            args.original_interface,
            args.original_version,
            args.flow_id,
            owner,
        )


class RunMarkdownInterface(_ManagerInterface):
    operation = "run-markdown"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("interface")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.run_markdown(args.flow_id, args.interface)


class RunPythonInterface(_ManagerInterface):
    operation = "run-python"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("interface")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.run_python(args.flow_id, args.interface, sys.stdin.read())


class SettleInterface(_ManagerInterface):
    operation = "settle"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("interface")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.settle(args.flow_id, args.interface)


class InvalidateInterface(_ManagerInterface):
    operation = "invalidate"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("setup_interface")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.invalidate(args.setup_interface)


class TeardownAllInterface(_ManagerInterface):
    operation = "teardown-all"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id", nargs="?")
        parser.add_argument("owner_host", nargs="?")
        parser.add_argument("owner_pid", nargs="?", type=_positive_version)
        parser.add_argument("owner_started_at", nargs="?")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        values = (args.flow_id, args.owner_host, args.owner_pid, args.owner_started_at)
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ManagerUsageError("teardown-all owner metadata must be complete")
        owner = None if args.flow_id is None else FlowOwner(
            args.owner_host, args.owner_pid, args.owner_started_at
        )
        return controller.teardown_all(args.flow_id, owner)


class RecoverInterface(_ManagerInterface):
    operation = "recover"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("action", choices=("retry", "cancel"))
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.recover(args.flow_id, args.action)


class RecoverBusyInterface(_ManagerInterface):
    operation = "recover-busy"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("--force", action="store_true")
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.recover_busy(args.flow_id, force=args.force)


class AuthorizeMarkdownCallInterface(_ManagerInterface):
    operation = "authorize-markdown-call"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("target_interface")
        parser.add_argument("target_version", type=_positive_version)
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.authorize_markdown_call(
            args.flow_id, args.target_interface, args.target_version
        )


def main(argv: Sequence[str] | None = None) -> int:
    """A direct entrypoint is intentionally not routable without one exact class."""
    return run_python_machine_interface(
        StatusInterface(), sys.argv[1:] if argv is None else argv
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AuthorizeInterface",
    "AuthorizeMarkdownCallInterface",
    "BeginInterface",
    "InvalidateInterface",
    "RecoverInterface",
    "RunMarkdownInterface",
    "RunPythonInterface",
    "SettleInterface",
    "SetupManager",
    "StatusInterface",
    "TeardownAllInterface",
]
