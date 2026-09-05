"""Finite setup/teardown orchestration and public machine interfaces."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
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
    FlowConflict,
    LedgerConflict,
    LedgerError,
    LedgerPathError,
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
        raise ManagerUsageError("stdin must be one JSON object") from exc
    if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
        raise ManagerUsageError("stdin must be one JSON object")
    declarations = {argument.name: argument for argument in arguments}
    unknown = set(request) - set(declarations)
    missing = {
        argument.name
        for argument in arguments
        if argument.required and argument.name not in request
    }
    if unknown or missing:
        raise ManagerUsageError("stdin contains undeclared or missing arguments")
    positional: dict[int, str] = {}
    options: list[str] = []
    for argument in arguments:
        if argument.name not in request:
            continue
        value = request[argument.name]
        if not isinstance(value, (str, int, bool)) or isinstance(value, float):
            raise ManagerUsageError("declared arguments must be scalar JSON values")
        if argument.position is not None:
            if isinstance(value, bool):
                raise ManagerUsageError("positional arguments cannot be Boolean")
            positional[argument.position] = str(value)
        else:
            assert argument.option is not None
            if isinstance(value, bool):
                if value:
                    options.append(argument.option)
            else:
                options.extend((argument.option, str(value)))
    if positional and sorted(positional) != list(range(len(positional))):
        raise ManagerUsageError("optional positional arguments cannot leave gaps")
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
    ) -> None:
        self.graph = graph
        self.store = store
        self._dispatch = dispatch
        self._bindings = dict(bindings)
        self._new_flow_id = new_flow_id or (lambda: str(uuid4()))

    def _binding(self, setup_interface: str) -> ManagedInterfaceBinding:
        try:
            binding = self._bindings[setup_interface]
        except KeyError as exc:
            raise ManagerDomainError("current managed interface has no finite dispatch binding") from exc
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
            raise ManagerDomainError("finite dispatch binding does not match managed metadata")
        return binding

    def _flow_step(
        self, ledger: SetupLedger
    ) -> tuple[ActiveFlow, SetupStep | TeardownStep, ManagedInterfaceBinding]:
        flow = ledger.active_flow
        if flow is None:
            raise ManagerDomainError("no active managed flow")
        managed = self.graph.managed_setups.get(flow.current_step)
        if managed is None:
            raise ManagerRecoveryError("active flow current step is no longer managed")
        try:
            binding = self._binding(flow.current_step)
        except ManagerDomainError as exc:
            if flow.operation != "teardown-all":
                raise
            raise ManagerRecoveryError("active teardown binding is no longer valid") from exc
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
                raise ManagerRecoveryError("active teardown no longer matches the live graph") from exc
            if not plan or plan[0].setup_interface != flow.current_step:
                raise ManagerRecoveryError("active teardown no longer matches the live plan")
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
            raise ManagerDomainError("call does not match the exact current step")
        if isinstance(step, TeardownStep) and step.action == "release-claim":
            raise ManagerDomainError("claim-only teardown step has no external action")
        return flow, step, binding

    def _known_active_context(
        self, flow_id: str
    ) -> tuple[ActiveFlow | None, SetupStep | TeardownStep | None]:
        """Recover only a still-readable matching flow for a redacted failure response."""
        try:
            flow, step, _binding = self._flow_step(self.store.read())
        except (LedgerError, FlowConflict, ManagerDomainError, ManagerRecoveryError):
            return None, None
        if flow.flow_id != flow_id:
            return None, None
        return flow, step

    def _dispatch_result(
        self, key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._dispatch(key, args=args, stdin=stdin)
        except Exception as exc:
            if not is_dispatch_invocation_error(exc):
                raise
            raise ManagerRecoveryError("declared dispatch completion is uncertain") from exc
        if not isinstance(result, subprocess.CompletedProcess):
            raise ManagerRecoveryError("declared dispatch returned an invalid process result")
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
        result = self._dispatch_result(key)
        if result.returncode != 0:
            return None
        try:
            decoded = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ManagerRecoveryError("declared verifier returned malformed JSON") from exc
        if not isinstance(decoded, dict) or any(type(value) is not bool for value in decoded.values()):
            raise ManagerRecoveryError("declared verifier returned an unsupported payload")
        if decoded == expected:
            return True
        false_value = {next(iter(expected)): False}
        if decoded == false_value:
            return False
        raise ManagerRecoveryError("declared verifier returned an unsupported payload")

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
        self, flow: ActiveFlow, step: SetupStep | TeardownStep
    ) -> tuple[ActiveFlow | None, SetupStep | TeardownStep | None]:
        if isinstance(step, SetupStep):
            result = record_setup_success(self.store, self.graph, flow.flow_id, step)
        elif flow.operation == "teardown-all":
            try:
                result = record_teardown_all_success(self.store, self.graph, flow.flow_id, step)
            except (BlueprintGraphError, FlowConflict, LedgerError) as exc:
                raise ManagerRecoveryError("global teardown settlement needs recovery") from exc
        else:
            result = record_teardown_success(self.store, self.graph, flow.flow_id, step)
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
        operation: str,
        message: str,
        *,
        state_name: str = "failed",
        flow: ActiveFlow | None = None,
        step: SetupStep | TeardownStep | None = None,
        original: ContinuationIdentity | None = None,
    ) -> tuple[int, dict[str, object]]:
        if state_name == "recovery-required" and operation == "teardown-all" and flow is not None:
            flow, step = self._known_active_context(flow.flow_id)
        return 2, _response(
            flow_id=None if flow is None else flow.flow_id,
            operation=operation,
            state=state_name,
            current_step=step,
            original=original,
            resume_original=False,
            error=message,
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
            action = self._dispatch_result(binding.teardown_dispatch_key)
            if action.returncode != 0:
                return self._domain_failure(
                    flow.operation, "declared action failed", flow=flow, step=step
                )
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation, "declared verifier reported incomplete state",
                        flow=flow, step=step,
                    )
            next_flow, next_step = self._settle_verified(flow, step)
            if next_step is None:
                return self._result_response(flow.operation, None, None, None)
            if next_flow is None or not isinstance(next_step, TeardownStep):
                raise ManagerRecoveryError("global teardown advanced without persisted state")
            flow, step = next_flow, next_step
    def teardown_all(self) -> tuple[int, dict[str, object]]:
        flow: ActiveFlow | None = None
        step: TeardownStep | None = None
        try:
            ledger = self.store.read()
            if ledger.active_flow is not None:
                active, active_step, _binding = self._flow_step(ledger)
                return self._domain_failure(
                    "teardown-all", "another managed flow is active", state_name="busy",
                    flow=active, step=active_step, original=None,
                )
            plan = teardown_all_plan(self.graph, ledger)
            if not plan:
                return self._result_response("teardown-all", None, None, None)
            for candidate in plan:
                binding = self._binding(candidate.setup_interface)
                if binding.arguments:
                    raise ManagerDomainError("global teardown requires zero-argument bindings")
            step = plan[0]
            flow = ActiveFlow(self._new_flow_id(), "teardown-all", None, step.setup_interface, (), None)
            def start(current: SetupLedger) -> SetupLedger:
                if current.active_flow is not None or teardown_all_plan(self.graph, current) != plan:
                    raise FlowConflict("managed teardown changed before the flow began")
                return begin_flow(current, flow)
            self.store.update(start)
            return self._run_teardown_all(flow, step)
        except (BlueprintGraphError, FlowConflict, ManagerDomainError, LedgerError) as exc:
            return self._domain_failure("teardown-all", str(exc))
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "teardown-all", str(exc), state_name="recovery-required",
                flow=flow, step=step,
            )
    def status(self, target_interface: str) -> tuple[int, dict[str, object]]:
        try:
            result = evaluate_target(self.graph, target_interface, self.store.read())
            return 0, {
                "schema_version": SCHEMA_VERSION,
                "code": result.code,
                "root_setup_interface": result.root_setup_interface,
                "pending_stack": [
                    _step_payload(step) for step in result.pending_stack
                ],
                "flow_id": result.flow_id,
            }
        except LedgerError as exc:
            return 2, {
                "schema_version": SCHEMA_VERSION,
                "code": "setup_busy",
                "root_setup_interface": None,
                "pending_stack": [],
                "flow_id": None,
                "error": str(exc),
            }

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
        except LedgerError as exc:
            return self._domain_failure("authorize", str(exc), original=original)
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
        return self._domain_failure(
            "authorize",
            f"target is not ready: {result.code}",
            state_name=state_name,
            step=step,
            original=original,
        )

    def begin(
        self,
        operation: str,
        root_setup_interface: str,
        original_caller: str,
        original_interface: str,
        original_version: int,
    ) -> tuple[int, dict[str, object]]:
        original = ContinuationIdentity(
            original_caller, original_interface, original_version
        )
        if operation not in {"setup", "teardown"}:
            return self._domain_failure("begin", "operation must be setup or teardown", original=original)
        try:
            ledger = self.store.read()
            if ledger.active_flow is not None:
                flow, step, _binding = self._flow_step(ledger)
                return self._domain_failure(
                    operation,
                    "another managed flow is active",
                    state_name="busy",
                    flow=flow,
                    step=step,
                    original=flow.continuation,
                )
            if root_setup_interface not in self.graph.managed_setups:
                raise ManagerDomainError("root setup interface is not managed")
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
                    raise ManagerDomainError("managed setup cannot begin in the current state")
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
                flow_id=self._new_flow_id(),
                operation=operation,  # type: ignore[arg-type]
                root=root_setup_interface,
                current_step=step.setup_interface,
                verified_steps=verified_steps,
                continuation=original,
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
        except (FlowConflict, ManagerDomainError, LedgerError) as exc:
            return self._domain_failure(operation, str(exc), original=original)
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                operation, str(exc), state_name="recovery-required", original=original
            )

    def authorize_markdown_call(
        self, flow_id: str, target_interface: str, target_version: int
    ) -> tuple[int, dict[str, object]]:
        try:
            ledger = self.store.read()
            flow = ledger.active_flow
            if flow is None:
                raise ManagerDomainError("no active managed flow")
            if flow.flow_id != flow_id:
                raise ManagerDomainError("call does not match the exact current flow")
            managed = self.graph.managed_setups.get(flow.current_step)
            if managed is None:
                raise ManagerRecoveryError("active flow current step is no longer managed")
            try:
                binding = self._binding(flow.current_step)
            except ManagerDomainError as exc:
                raise ManagerRecoveryError("active binding is no longer valid") from exc
            if binding.setup_kind != "markdown":
                raise ManagerDomainError("current step is not Markdown")
            if flow.operation != "setup":
                raise ManagerDomainError("only setup operations support markdown helper calls")
            if (target_interface, target_version) not in binding.helper_allowlist:
                raise ManagerDomainError("helper call not authorized for current step")
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
        except (ManagerDomainError, FlowConflict, LedgerError) as exc:
            return self._domain_failure("authorize-markdown-call", str(exc))
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "authorize-markdown-call", str(exc), state_name="recovery-required"
            )

    def run_markdown(self, flow_id: str, interface: str) -> tuple[int, dict[str, object]]:
        try:
            flow, step, binding = self._require_current(flow_id, interface)
            if binding.setup_kind != "markdown":
                raise ManagerDomainError("current step is not Markdown")
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
        except (ManagerDomainError, FlowConflict, LedgerError) as exc:
            return self._domain_failure("run-markdown", str(exc))
        except ManagerRecoveryError as exc:
            return self._domain_failure(
                "run-markdown", str(exc), state_name="recovery-required"
            )

    def run_python(
        self, flow_id: str, interface: str, stdin_request: str
    ) -> tuple[int, dict[str, object]]:
        try:
            flow, step, binding = self._require_current(flow_id, interface)
            if binding.setup_kind != "python":
                raise ManagerDomainError("current step is not Python")
            argv = _encode_arguments(stdin_request, binding.arguments)
            action_key = (
                binding.setup_dispatch_key
                if isinstance(step, SetupStep)
                else binding.teardown_dispatch_key
            )
            if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                raise ManagerDomainError("internal teardown step has no external action")
            action = self._dispatch_result(action_key, args=argv)
            if action.returncode != 0:
                return self._domain_failure(
                    flow.operation,
                    "declared action failed",
                    flow=flow,
                    step=step,
                    original=flow.continuation,
                )
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation,
                        "declared verifier reported incomplete state",
                        flow=flow,
                        step=step,
                        original=flow.continuation,
                    )
            next_flow, next_step = self._settle_verified(flow, step)
            return self._result_response(
                flow.operation, flow.continuation, next_flow, next_step
            )
        except ManagerUsageError as exc:
            return 64, _response(
                flow_id=None,
                operation="run-python",
                state="failed",
                current_step=None,
                original=None,
                error=str(exc),
            )
        except (ManagerDomainError, FlowConflict, LedgerError) as exc:
            return self._domain_failure("run-python", str(exc))
        except ManagerRecoveryError as exc:
            try:
                ledger = self.store.read()
                flow = ledger.active_flow
                step = self._flow_step(ledger)[1] if flow is not None else None
            except Exception:
                flow, step = None, None
            return self._domain_failure(
                "run-python",
                str(exc),
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
                raise ManagerDomainError("settle is only valid for Markdown steps")
            if isinstance(step, TeardownStep) and step.action in ("release-claim", "invalidate-receipt"):
                raise ManagerDomainError("internal teardown step has no external action")
            if self._has_verifier(step, binding):
                if not self._verify(flow, step, binding):
                    return self._domain_failure(
                        flow.operation,
                        "declared verifier reported incomplete state",
                        flow=flow,
                        step=step,
                        original=flow.continuation,
                    )
            next_flow, next_step = self._settle_verified(flow, step)
            if flow.operation == "teardown-all" and next_step is not None:
                assert next_flow is not None and isinstance(next_step, TeardownStep)
                return self._run_teardown_all(next_flow, next_step)
            return self._result_response(
                flow.operation, flow.continuation, next_flow, next_step
            )
        except (ManagerDomainError, FlowConflict, LedgerError) as exc:
            return self._domain_failure("settle", str(exc))
        except ManagerRecoveryError as exc:
            if flow is None or step is None:
                flow, step = self._known_active_context(flow_id)
            return self._domain_failure(
                "settle" if flow is None else flow.operation,
                str(exc),
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
                    "recover or cancel the active flow before invalidating",
                    state_name="busy",
                    flow=flow,
                    step=step,
                    original=flow.continuation,
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
        except (FlowConflict, LedgerError) as exc:
            return self._domain_failure("invalidate", str(exc))

    def recover(self, flow_id: str, action: str) -> tuple[int, dict[str, object]]:
        flow: ActiveFlow | None = None
        step: SetupStep | TeardownStep | None = None
        try:
            ledger = self.store.read()
            flow, step, binding = self._flow_step(ledger)
            if flow.flow_id != flow_id:
                raise ManagerDomainError("active flow does not match")
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
                        next_flow, next_step = self._settle_verified(flow, step)
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

            if flow.operation == "teardown-all":
                assert isinstance(step, TeardownStep)
                if self._has_verifier(step, binding):
                    outcome = self._verifier_outcome(flow, step, binding)
                    if outcome is None:
                        raise ManagerRecoveryError("declared verifier completion is uncertain")
                    if outcome:
                        try:
                            record_teardown_all_success(
                                self.store, self.graph, flow.flow_id, step, advance=False
                            )
                        except (BlueprintGraphError, FlowConflict, LedgerError) as exc:
                            raise ManagerRecoveryError("global teardown cancellation needs recovery") from exc
                    else:
                        def abandon(current: SetupLedger) -> SetupLedger:
                            active, live_step, _binding = self._flow_step(current)
                            if active != flow or live_step != step:
                                raise ManagerRecoveryError("global teardown changed before cancellation")
                            return clear_flow(current, flow_id)
                        self.store.update(abandon)
                else:
                    def abandon_unverified(current: SetupLedger) -> SetupLedger:
                        active, live_step, _binding = self._flow_step(current)
                        if active != flow or live_step != step:
                            raise ManagerRecoveryError("global teardown changed before cancellation")
                        interfaces = dict(current.interfaces)
                        interfaces.pop(step.setup_interface, None)
                        return SetupLedger(interfaces=interfaces, active_flow=None)
                    self.store.update(abandon_unverified)
                return self._result_response(flow.operation, None, None, None)

            if isinstance(step, SetupStep):
                if self._has_verifier(step, binding):
                    def cancel_verified(current: SetupLedger) -> SetupLedger:
                        active = current.active_flow
                        if active is None or active.flow_id != flow_id:
                            raise FlowConflict("active flow does not match")
                        interfaces = dict(current.interfaces)
                        for setup_interface in active.verified_steps:
                            receipt = interfaces.get(setup_interface)
                            if receipt is not None:
                                interfaces[setup_interface] = SetupReceipt(
                                    receipt.version, receipt.required_by - {active.root}
                                )
                        return SetupLedger(interfaces=interfaces, active_flow=None)
                    self.store.update(cancel_verified)
                else:
                    def cancel_unverified(current: SetupLedger) -> SetupLedger:
                        active = current.active_flow
                        if active is None or active.flow_id != flow_id:
                            raise FlowConflict("active flow does not match")
                        interfaces = dict(current.interfaces)
                        for setup_interface in active.verified_steps:
                            receipt = interfaces.get(setup_interface)
                            if receipt is not None:
                                interfaces[setup_interface] = SetupReceipt(
                                    receipt.version, receipt.required_by - {active.root}
                                )
                        return SetupLedger(interfaces=interfaces, active_flow=None)
                    self.store.update(cancel_unverified)
            else:
                if self._has_verifier(step, binding):
                    def cancel_teardown_verified(current: SetupLedger) -> SetupLedger:
                        active = current.active_flow
                        if active is None or active.flow_id != flow_id:
                            raise FlowConflict("active flow does not match")
                        interfaces = dict(current.interfaces)
                        for setup_interface in active.verified_steps:
                            receipt = interfaces.get(setup_interface)
                            if receipt is not None:
                                interfaces[setup_interface] = SetupReceipt(
                                    receipt.version, receipt.required_by - {active.root}
                                )
                        return SetupLedger(interfaces=interfaces, active_flow=None)
                    self.store.update(cancel_teardown_verified)
                else:
                    def cancel_teardown_unverified(current: SetupLedger) -> SetupLedger:
                        active = current.active_flow
                        if active is None or active.flow_id != flow_id:
                            raise FlowConflict("active flow does not match")
                        live_step = self._flow_step(current)[1]
                        if live_step != step:
                            raise FlowConflict("active teardown step does not match")
                        interfaces = dict(current.interfaces)
                        interfaces.pop(step.setup_interface, None)
                        for setup_interface in active.verified_steps:
                            receipt = interfaces.get(setup_interface)
                            if receipt is not None:
                                interfaces[setup_interface] = SetupReceipt(
                                    receipt.version, receipt.required_by - {active.root}
                                )
                        return SetupLedger(interfaces=interfaces, active_flow=None)
                    self.store.update(cancel_teardown_unverified)
            return 0, _response(
                flow_id=None,
                operation=flow.operation,
                state="ready",
                current_step=None,
                original=flow.continuation,
                resume_original=False,
            )
        except ManagerUsageError as exc:
            return 64, _response(
                flow_id=None,
                operation="recover",
                state="failed",
                current_step=None,
                original=None,
                error=str(exc),
            )
        except (ManagerDomainError, FlowConflict, LedgerError) as exc:
            return self._domain_failure("recover", str(exc))
        except ManagerRecoveryError as exc:
            if flow is None or step is None:
                flow, step = self._known_active_context(flow_id)
            return self._domain_failure(
                "recover" if flow is None else flow.operation,
                str(exc),
                state_name="recovery-required",
                flow=flow,
                step=step,
                original=None if flow is None else flow.continuation,
            )


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

    def build_graph(self, args: argparse.Namespace):
        context = runtime_dispatch_context(self)
        repo_root = Path(context.repo_root or REPO_ROOT)
        try:
            return self._graph_loader(repo_root)
        except (BlueprintGraphError, OSError) as exc:
            raise ManagerBootstrapError(
                "repository blueprint graph is unavailable"
            ) from exc

    def build_manager(self, args: argparse.Namespace) -> SetupManager:
        if self._manager_factory is not None:
            return self._manager_factory()
        try:
            getter = self.dispatch(GETTER_KEY, args=("setup-status",), text=True)
        except Exception as exc:
            if not is_dispatch_invocation_error(exc):
                raise
            raise ManagerBootstrapError("setup-status getter dispatch failed") from exc
        if not isinstance(getter, subprocess.CompletedProcess) or getter.returncode != 0:
            raise ManagerBootstrapError("setup-status getter dispatch failed")
        if not isinstance(getter.stdout, str):
            raise LedgerPathError("setup-status getter did not return text")
        lines = getter.stdout.splitlines()
        if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
            raise LedgerPathError("setup-status getter must return one absolute path")
        path = Path(lines[0])
        if not path.is_absolute():
            raise LedgerPathError("setup-status getter must return one absolute path")
        store = LedgerStore._from_atomic_files(path, _AtomicFilesAdapter())
        graph = self.build_graph(args)

        def dispatch(
            key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
        ) -> subprocess.CompletedProcess[str]:
            return self.dispatch(key, args=args, stdin=stdin, text=True)

        return SetupManager(
            graph=graph,
            store=store,
            dispatch=dispatch,
            bindings=self._bindings,
        )

    def _malformed(self, message: str) -> int:
        payload = _response(
            flow_id=None,
            operation=self.operation,
            state="failed",
            current_step=None,
            original=None,
            error=message,
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
            return self._emit(
                (2, _response(
                    flow_id=None,
                    operation=self.operation,
                    state="recovery-required",
                    current_step=None,
                    original=None,
                    error=str(exc),
                ))
            )

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        raise NotImplementedError


class _DirectPreflightInterface(_ManagerInterface):
    """Load only one parsed target's setup closure for read/authorize preflight."""

    def build_graph(self, args: argparse.Namespace):
        context = runtime_dispatch_context(self)
        if context.repository_config is None:
            raise ManagerBootstrapError("repository configuration is unavailable")
        try:
            configuration = load_repository_configuration(
                Path(context.repository_config)
            )
            return load_direct_setup_graph(configuration, args.target_interface)
        except (
            RepositoryConfigurationError,
            DirectBlueprintError,
            BlueprintGraphError,
            OSError,
        ) as exc:
            raise ManagerBootstrapError(
                "repository blueprint graph is unavailable"
            ) from exc


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
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.begin(
            args.operation,
            args.root_setup,
            args.original_caller,
            args.original_interface,
            args.original_version,
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

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.teardown_all()


class RecoverInterface(_ManagerInterface):
    operation = "recover"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("flow_id")
        parser.add_argument("action", choices=("retry", "cancel"))
        return parser

    def invoke(self, controller: SetupManager, args: argparse.Namespace):
        return controller.recover(args.flow_id, args.action)


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
