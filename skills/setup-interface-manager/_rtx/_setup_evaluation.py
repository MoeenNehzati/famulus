"""Graph-derived setup status, claims, settlement, and teardown planning."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from officina.blueprints.graph import BlueprintGraphError, ManagedSetup, managed_setup_order

from ._setup_state import (
    ActiveFlow,
    FlowConflict,
    LedgerStore,
    SetupLedger,
    SetupReceipt,
    claim_receipts,
    clear_flow,
)


@dataclass(frozen=True)
class SetupStep:
    """One declared setup operation in dependency-first execution order."""

    setup_interface: str
    setup_version: int
    setup_verifier_interface: str | None
    setup_verifier_version: int | None
    kind: Literal["markdown", "python"]

    @classmethod
    def from_managed(cls, managed: ManagedSetup) -> SetupStep:
        return cls(
            setup_interface=managed.setup_interface,
            setup_version=managed.setup_version,
            setup_verifier_interface=managed.setup_verifier_interface,
            setup_verifier_version=managed.setup_verifier_version,
            kind=managed.kind,
        )


@dataclass(frozen=True)
class TeardownStep:
    """One declared teardown operation and its claim-release decision."""

    setup_interface: str
    setup_version: int
    teardown_interface: str | None
    teardown_version: int | None
    teardown_verifier_interface: str | None
    teardown_verifier_version: int | None
    kind: Literal["markdown", "python"]
    action: Literal["run-teardown", "release-claim", "invalidate-receipt"]

    @classmethod
    def from_managed(
        cls, managed: ManagedSetup, action: Literal["run-teardown", "release-claim", "invalidate-receipt"]
    ) -> TeardownStep:
        return cls(
            setup_interface=managed.setup_interface,
            setup_version=managed.setup_version,
            teardown_interface=managed.teardown_interface,
            teardown_version=managed.teardown_version,
            teardown_verifier_interface=managed.teardown_verifier_interface,
            teardown_verifier_version=managed.teardown_verifier_version,
            kind=managed.kind,
            action=action,
        )


@dataclass(frozen=True)
class SetupEvaluation:
    """Read-only status for a target and its managed setup closure."""

    code: Literal["unmanaged", "ready", "setup_required", "setup_busy"]
    root_setup_interface: str | None
    pending_stack: tuple[SetupStep, ...] = ()
    flow_id: str | None = None
    resume_original: bool = False


@dataclass(frozen=True)
class FlowResult:
    """The exact next action after one successful managed settlement."""

    flow_id: str
    operation: Literal["setup", "teardown", "teardown-all"]
    state: Literal["run-step", "ready"]
    current_step: SetupStep | TeardownStep | None


def _owner_setup_interface(graph, target_interface: str) -> str | None:
    """Resolve a target's nearest managed module owner through graph parents."""
    target = graph.exports.get(target_interface)
    if target is None:
        return None
    managed_by_module: dict[str, str] = {}
    for setup_interface in graph.managed_setups:
        setup_export = graph.exports.get(setup_interface)
        if setup_export is not None:
            managed_by_module[setup_export.module_node_id] = setup_interface
    seen: set[str] = set()
    module_id = target.module_node_id
    while module_id not in seen:
        seen.add(module_id)
        setup_interface = managed_by_module.get(module_id)
        if setup_interface is not None:
            return setup_interface
        parent = graph.module_parents.get(module_id)
        if parent is None:
            return None
        module_id = parent
    raise FlowConflict("module parent graph contains a cycle")


def _setup_steps(graph, root_setup_interface: str) -> tuple[SetupStep, ...]:
    return tuple(
        SetupStep.from_managed(managed)
        for managed in managed_setup_order(graph, root_setup_interface)
    )


def _exact_receipt(ledger: SetupLedger, step: SetupStep) -> bool:
    receipt = ledger.interfaces.get(step.setup_interface)
    return receipt is not None and receipt.version == step.setup_version


def evaluate_target(graph, target_interface: str, ledger: SetupLedger) -> SetupEvaluation:
    """Classify a target without changing receipts or claims."""
    root_setup_interface = _owner_setup_interface(graph, target_interface)
    if root_setup_interface is None:
        return SetupEvaluation("unmanaged", None)
    if ledger.active_flow is not None:
        return SetupEvaluation(
            "setup_busy", root_setup_interface, flow_id=ledger.active_flow.flow_id
        )
    steps = _setup_steps(graph, root_setup_interface)
    for index, step in enumerate(steps):
        if not _exact_receipt(ledger, step):
            return SetupEvaluation(
                "setup_required",
                root_setup_interface,
                pending_stack=tuple(reversed(steps[index:])),
            )
    return SetupEvaluation("ready", root_setup_interface)


def authorize_ready_root(store: LedgerStore, graph, target_interface: str) -> SetupEvaluation:
    """Atomically claim every ready closure receipt for the target's root."""
    initial = evaluate_target(graph, target_interface, store.read())
    if initial.code == "unmanaged":
        return initial

    result: SetupEvaluation | None = None

    def claim(ledger: SetupLedger) -> SetupLedger:
        nonlocal result
        current = evaluate_target(graph, target_interface, ledger)
        if current.code != "ready":
            result = current
            return ledger
        root = current.root_setup_interface
        if root is None:
            raise FlowConflict("ready managed target lacks an owner")
        claimed = claim_receipts(
            ledger, root, tuple(step.setup_interface for step in _setup_steps(graph, root))
        )
        result = replace(current, resume_original=True)
        return claimed

    store.update(claim)
    if result is None:
        raise FlowConflict("authorization did not evaluate the ledger")
    return result


def _active_flow(ledger: SetupLedger, flow_id: str, operation: str) -> ActiveFlow:
    flow = ledger.active_flow
    if flow is None or flow.flow_id != flow_id:
        raise FlowConflict("active flow does not match")
    if flow.operation != operation:
        raise FlowConflict("active flow operation does not match")
    return flow


def _validated_setup_position(
    graph, ledger: SetupLedger, flow: ActiveFlow
) -> tuple[tuple[SetupStep, ...], int]:
    """Require the suspended flow to match the live closure before recording it."""
    steps = _setup_steps(graph, flow.root)
    for index, step in enumerate(steps):
        if step.setup_interface == flow.current_step:
            expected_prefix = tuple(
                expected.setup_interface for expected in steps[:index]
            )
            if flow.verified_steps != expected_prefix:
                raise FlowConflict("setup flow no longer matches the live graph prefix")
            for expected in steps[:index]:
                receipt = ledger.interfaces.get(expected.setup_interface)
                if (
                    receipt is None
                    or receipt.version != expected.setup_version
                    or flow.root not in receipt.required_by
                ):
                    raise FlowConflict(
                        "setup flow no longer matches verified live graph receipts"
                    )
            return steps, index
    raise FlowConflict("setup flow current step is outside the live graph closure")


def record_setup_success(
    store: LedgerStore, graph, flow_id: str, step: SetupStep
) -> FlowResult:
    """Record only the active verified setup step, then select its successor."""
    result: FlowResult | None = None

    def settle(ledger: SetupLedger) -> SetupLedger:
        nonlocal result
        flow = _active_flow(ledger, flow_id, "setup")
        steps, index = _validated_setup_position(graph, ledger, flow)
        if flow.current_step != step.setup_interface:
            raise FlowConflict("setup settlement does not match the current step")
        managed = graph.managed_setups.get(step.setup_interface)
        if managed is None or SetupStep.from_managed(managed) != step:
            raise FlowConflict("setup settlement does not match declared metadata")
        receipt = ledger.interfaces.get(step.setup_interface)
        roots = {flow.root} if receipt is None or receipt.version != step.setup_version else receipt.required_by | {flow.root}
        interfaces = dict(ledger.interfaces)
        interfaces[step.setup_interface] = SetupReceipt(step.setup_version, frozenset(roots))
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        if next_step is None:
            result = FlowResult(flow_id, "setup", "ready", None)
            return SetupLedger(interfaces=interfaces, active_flow=None)
        next_flow = replace(
            flow,
            current_step=next_step.setup_interface,
            verified_steps=flow.verified_steps + (step.setup_interface,),
        )
        result = FlowResult(flow_id, "setup", "run-step", next_step)
        return SetupLedger(interfaces=interfaces, active_flow=next_flow)

    store.update(settle)
    if result is None:
        raise FlowConflict("setup settlement did not produce a result")
    return result


def teardown_plan(
    graph, root_setup_interface: str, ledger: SetupLedger
) -> tuple[TeardownStep, ...]:
    """Return claimed current receipts in reverse setup order."""
    plan: list[TeardownStep] = []
    for managed in reversed(managed_setup_order(graph, root_setup_interface)):
        receipt = ledger.interfaces.get(managed.setup_interface)
        if (
            receipt is None
            or receipt.version != managed.setup_version
            or root_setup_interface not in receipt.required_by
        ):
            continue
        action: Literal["run-teardown", "release-claim", "invalidate-receipt"]
        if receipt.required_by - {root_setup_interface}:
            action = "release-claim"
        elif managed.teardown_interface is not None:
            action = "run-teardown"
        else:
            action = "invalidate-receipt"
        plan.append(TeardownStep.from_managed(managed, action))
    return tuple(plan)


def teardown_all_plan(graph, ledger: SetupLedger) -> tuple[TeardownStep, ...]:
    """Return every exact receipt once in deterministic reverse setup order."""
    ordered: list[ManagedSetup] = []
    seen: set[str] = set()
    for setup_interface in sorted(ledger.interfaces):
        receipt = ledger.interfaces[setup_interface]
        managed = graph.managed_setups.get(setup_interface)
        if managed is None:
            raise BlueprintGraphError(
                f"{setup_interface!r} is not a public managed setup interface"
            )
        if receipt.version != managed.setup_version:
            raise BlueprintGraphError(
                f"{setup_interface!r} receipt version does not match live metadata"
            )
        for candidate in managed_setup_order(graph, setup_interface):
            if candidate.setup_interface not in seen:
                seen.add(candidate.setup_interface)
                ordered.append(candidate)
    plan: list[TeardownStep] = []
    for managed in reversed(ordered):
        if managed.setup_interface in ledger.interfaces:
            action: Literal["run-teardown", "invalidate-receipt"]
            action = "run-teardown" if managed.teardown_interface is not None else "invalidate-receipt"
            plan.append(TeardownStep.from_managed(managed, action))
    return tuple(plan)


def record_teardown_success(
    store: LedgerStore, graph, flow_id: str, step: TeardownStep
) -> FlowResult:
    """Apply one verified teardown or claim release and advance in reverse order."""
    return _record_teardown_success(store, graph, flow_id, step, "teardown", True)


def record_teardown_all_success(
    store: LedgerStore, graph, flow_id: str, step: TeardownStep, *, advance: bool = True,
) -> FlowResult:
    """Apply one verified global teardown, optionally clearing after settlement."""
    return _record_teardown_success(
        store, graph, flow_id, step, "teardown-all", advance
    )


def _record_teardown_success(
    store: LedgerStore, graph, flow_id: str, step: TeardownStep,
    operation: Literal["teardown", "teardown-all"], advance: bool,
) -> FlowResult:
    """Settle one exact teardown step using operation-specific planning."""
    result: FlowResult | None = None

    def settle(ledger: SetupLedger) -> SetupLedger:
        nonlocal result
        flow = _active_flow(ledger, flow_id, operation)
        if operation == "teardown":
            if flow.root is None:
                raise FlowConflict("ordinary teardown flow lacks a root")
            plan = teardown_plan(graph, flow.root, ledger)
        else:
            plan = teardown_all_plan(graph, ledger)
        if not plan or flow.current_step != step.setup_interface or plan[0] != step:
            raise FlowConflict("teardown settlement does not match the current step")
        interfaces = dict(ledger.interfaces)
        receipt = interfaces[step.setup_interface]
        if operation == "teardown" and step.action == "release-claim":
            interfaces[step.setup_interface] = SetupReceipt(
                receipt.version, receipt.required_by - {flow.root}
            )
        else:
            del interfaces[step.setup_interface]
        intermediate = SetupLedger(interfaces=interfaces, active_flow=flow)
        if not advance:
            result = FlowResult(flow_id, operation, "ready", None)
            return clear_flow(intermediate, flow_id)
        if operation == "teardown":
            assert flow.root is not None
            remaining = teardown_plan(graph, flow.root, intermediate)
        else:
            remaining = teardown_all_plan(graph, intermediate)
        if not remaining:
            result = FlowResult(flow_id, operation, "ready", None)
            return clear_flow(intermediate, flow_id)
        next_step = remaining[0]
        result = FlowResult(flow_id, operation, "run-step", next_step)
        return SetupLedger(
            interfaces=interfaces,
            active_flow=replace(
                flow,
                current_step=next_step.setup_interface,
                verified_steps=(
                    flow.verified_steps + (step.setup_interface,)
                    if operation == "teardown"
                    else ()
                ),
            ),
        )

    store.update(settle)
    if result is None:
        raise FlowConflict("teardown settlement did not produce a result")
    return result


def invalidate(store: LedgerStore, graph, setup_interface: str) -> tuple[str, ...]:
    """Drop a selected receipt and managed dependents while preserving all orphans."""
    removed: tuple[str, ...] = ()

    def remove(ledger: SetupLedger) -> SetupLedger:
        nonlocal removed
        if ledger.active_flow is not None:
            raise FlowConflict("recover or cancel the active flow before invalidating")
        dependent_interfaces = {setup_interface}
        for root_setup_interface in graph.managed_setups:
            if any(
                managed.setup_interface == setup_interface
                for managed in managed_setup_order(graph, root_setup_interface)
            ):
                dependent_interfaces.add(root_setup_interface)
        removed = tuple(sorted(set(ledger.interfaces) & dependent_interfaces))
        return SetupLedger(
            interfaces={
                interface: receipt
                for interface, receipt in ledger.interfaces.items()
                if interface not in dependent_interfaces
            },
            active_flow=ledger.active_flow,
        )

    store.update(remove)
    return removed
