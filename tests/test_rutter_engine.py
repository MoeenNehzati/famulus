"""Specify the integrated Task 5 engine boundary and public cutover."""

from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
from pathlib import Path
from typing import Mapping

import pytest

import officina.rutter as rutter_public
import officina.rutter.engine as engine
import officina.rutter.runtime as runtime
from officina.rutter.model import (
    MachineStep,
    MachineContext,
    MachineRecord,
    MachineResult,
    ActiveChild,
    ActiveRun,
    AnswerSpec,
    SubRutter,
    SubRutterRecord,
    Charter,
    CompletedRun,
    Terminal,
    TerminalRecord,
    EnteredEvolution,
    FaultSummary,
    KnownFault,
    Message,
    OpaqueFault,
    LLMStep,
    MachineInstruction,
    Reckoning,
    Response,
    Rutter,
    RutterDefinitionError,
    RunBlocked,
    RutterStateError,
    RutterValidationError,
    ValidationReport,
    VoyageResult,
    VoyageStatus,
    Turn,
    _EffectRecovery,
)
from officina.rutter.runtime import RutterRegistry
from officina.rutter.storage import ReckoningStore
from test_support.rutter_fixtures import ExampleRutter


@dataclass(frozen=True)
class _CurrentModelScenario:
    """One in-memory v3-shaped situation whose condition must remain stable."""

    reckoning: Reckoning
    evolution: object
    condition: str


@pytest.fixture
def current_model_scenarios() -> Mapping[str, _CurrentModelScenario]:
    """Provide in-memory current-model scenarios for later status projections."""

    result = VoyageResult("complete", {})
    prompt = LLMStep("Review.", answer=AnswerSpec({"approved": {}}), next_on_outcome="done")
    pure = MachineStep(
        lambda context: MachineResult("calculated", {}),
        mode="pure",
        next_on_outcome="done",
    )
    effectful = MachineStep(
        lambda context: MachineResult("stored", {}),
        mode="repeat-safe",
        next_on_outcome="done",
    )
    done = Terminal(result)

    def run(
        run_id: str,
        entry_id: str,
        state_id: str,
        history: tuple[object, ...] = (),
        active_child: ActiveChild | None = None,
        *,
        rutter_id: str = "root",
    ) -> ActiveRun:
        return ActiveRun(
            run_id,
            rutter_id,
            1,
            Charter({}),
            EnteredEvolution(entry_id, state_id),
            history,
            active_child,
        )

    message = Message(
        {"text": "Review.", "answer": {"approved": {}}},
        {
            "evolution": {
                "id": "review",
                "entry_id": "entry-review",
                "revision": 0,
            },
            "payload": {},
        },
    )
    open_turn = Turn("turn-review", "entry-review", "review", 0, message, None)
    terminal = TerminalRecord("done-root", "entry-done", "done", result)
    explicit_child = CompletedRun(
        "completed-explicit",
        "unavailable-explicit-child",
        1,
        Charter({}),
        (TerminalRecord("done-explicit", "entry-explicit", "done", result),),
    )
    attached_child = CompletedRun(
        "completed-attached",
        "unavailable-attached-child",
        1,
        Charter({}),
        (TerminalRecord("done-attached", "entry-attached", "done", result),),
    )
    archived_history = (
        Turn(
            "turn-accepted",
            "entry-review",
            "review",
            0,
            message,
            Response(0, "approved", {}),
        ),
        MachineRecord(
            "action-pure",
            "action-pure",
            "entry-action",
            "action",
            "pure",
            MachineResult("calculated", {}),
        ),
        SubRutterRecord(
            "call-explicit",
            "entry-call",
            "delegate",
            None,
            None,
            explicit_child.run_id,
        ),
        terminal,
        SubRutterRecord(
            "call-attached",
            "entry-done",
            None,
            "terminal-check",
            terminal.record_id,
            attached_child.run_id,
        ),
    )
    nested_child = ActiveChild(
        "call-nested",
        "explicit_call",
        "delegate",
        None,
        run(
            "nested-child",
            "entry-child-done",
            "done",
            (TerminalRecord("done-child", "entry-child-done", "done", result),),
            rutter_id="status-child",
        ),
    )

    return {
        "ready_llm": _CurrentModelScenario(
            Reckoning(
                3,
                0,
                run("root-llm", "entry-review", "review", (open_turn,)),
                {},
                None,
                None,
            ),
            prompt,
            "ready",
        ),
        "ready_machine": _CurrentModelScenario(
            Reckoning(
                3,
                0,
                run("root-machine", "entry-action", "action"),
                {},
                None,
                None,
            ),
            pure,
            "ready",
        ),
        "nested_terminal_child": _CurrentModelScenario(
            Reckoning(
                3,
                1,
                run(
                    "root-nested",
                    "entry-delegate",
                    "delegate",
                    active_child=nested_child,
                ),
                {},
                None,
                None,
            ),
            done,
            "terminal",
        ),
        "uncertain_effect": _CurrentModelScenario(
            Reckoning(
                3,
                0,
                run("root-uncertain", "entry-store", "store"),
                {},
                _EffectRecovery(
                    "action-entry-store",
                    "root-uncertain",
                    "entry-store",
                    "store",
                    "repeat-safe",
                    "uncertain",
                    None,
                ),
                None,
            ),
            effectful,
            "uncertain",
        ),
        "known_fault": _CurrentModelScenario(
            Reckoning(
                3,
                0,
                run("root-known", "entry-review", "review", (open_turn,)),
                {},
                None,
                KnownFault(
                    "routing",
                    "root-known",
                    "review",
                    "entry-review",
                    None,
                    (),
                ),
            ),
            prompt,
            "fault",
        ),
        "opaque_fault": _CurrentModelScenario(
            Reckoning(
                3,
                0,
                run("root-opaque", "entry-review", "review", (open_turn,)),
                {},
                None,
                OpaqueFault({"legacy": {"detail": "opaque"}}),
            ),
            prompt,
            "fault",
        ),
        "fault_with_stale_terminal": _CurrentModelScenario(
            Reckoning(
                3,
                1,
                run("root-stale", "entry-done", "done", (terminal,)),
                {},
                None,
                KnownFault(
                    "routing",
                    "root-stale",
                    "done",
                    "entry-done",
                    None,
                    (),
                ),
            ),
            done,
            "fault",
        ),
        "archived_unavailable_children": _CurrentModelScenario(
            Reckoning(
                3,
                2,
                run("root-archived", "entry-done", "done", archived_history),
                {
                    explicit_child.run_id: explicit_child,
                    attached_child.run_id: attached_child,
                },
                None,
                None,
            ),
            done,
            "terminal",
        ),
    }


def test_current_model_scenarios_preserve_condition_priority_and_history_kinds(
    current_model_scenarios: Mapping[str, _CurrentModelScenario],
) -> None:
    """Changing condition precedence or durable record provenance must fail."""

    assert {
        name: engine._condition(scenario.reckoning, scenario.evolution)
        for name, scenario in current_model_scenarios.items()
    } == {
        name: scenario.condition for name, scenario in current_model_scenarios.items()
    }
    archived = current_model_scenarios["archived_unavailable_children"].reckoning
    assert tuple(type(record) for record in archived.root.history) == (
        Turn,
        MachineRecord,
        SubRutterRecord,
        TerminalRecord,
        SubRutterRecord,
    )
    assert tuple(archived.completed_runs) == (
        "completed-explicit",
        "completed-attached",
    )


class _CountingStatusStore:
    """Count the complete store boundary while delegating real persistence."""

    def __init__(self, backing: ReckoningStore) -> None:
        self.backing = backing
        self.reads = 0
        self.transactions = 0

    def read(self) -> Reckoning:
        self.reads += 1
        return self.backing.read()

    def create(self, reckoning: Reckoning) -> None:
        self.backing.create(reckoning)

    def transaction(self):
        self.transactions += 1
        return self.backing.transaction()

    def replace(self, previous: Reckoning, replacement: Reckoning) -> None:
        self.backing.replace(previous, replacement)


@pytest.mark.parametrize(
    (
        "scenario_name",
        "evolution_id",
        "depth",
        "condition",
        "instruction_kind",
        "active_result",
        "fault",
    ),
    (
        ("ready_llm", "review", 0, "ready", "llm", None, None),
        ("ready_machine", "action", 0, "ready", "machine", None, None),
        (
            "nested_terminal_child",
            "done",
            1,
            "terminal",
            None,
            VoyageResult("complete", {}),
            None,
        ),
        ("uncertain_effect", "store", 0, "uncertain", None, None, None),
        (
            "known_fault",
            "review",
            0,
            "fault",
            None,
            None,
            FaultSummary("routing", "review", "entry-review", None, ()),
        ),
        (
            "opaque_fault",
            "review",
            0,
            "fault",
            None,
            None,
            FaultSummary("opaque", None, None, None, ()),
        ),
        (
            "fault_with_stale_terminal",
            "done",
            0,
            "fault",
            None,
            None,
            FaultSummary("routing", "done", "entry-done", None, ()),
        ),
        (
            "archived_unavailable_children",
            "done",
            0,
            "terminal",
            None,
            VoyageResult("complete", {}),
            None,
        ),
    ),
)
def test_get_status_projects_one_coherent_read_without_authored_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_model_scenarios: Mapping[str, _CurrentModelScenario],
    scenario_name: str,
    evolution_id: str,
    depth: int,
    condition: str,
    instruction_kind: str | None,
    active_result: VoyageResult | None,
    fault: FaultSummary | None,
) -> None:
    authored: list[str] = []

    def note(name: str, value: object) -> object:
        authored.append(name)
        return value

    class StatusChild(Rutter):
        rutter_id = "status-child"
        definition_version = 1
        initial_evolution_id = "done"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "done": Terminal(
                    lambda context: note("child-terminal", VoyageResult("complete", {}))
                )
            }

    class StatusRoot(Rutter):
        rutter_id = "root"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    answer=AnswerSpec({"approved": {}}),
                    data=lambda context: note("llm-data", {}),
                    validate=lambda context: note("llm-validate", ValidationReport(True)),
                    next_on_outcome="done",
                ),
                "action": MachineStep(
                    lambda context: note("pure-machine", MachineResult("calculated", {})),
                    mode="pure",
                    next_on_outcome="done",
                ),
                "store": MachineStep(
                    lambda context: note("effect-machine", MachineResult("stored", {})),
                    mode="repeat-safe",
                    next_on_outcome="done",
                ),
                "delegate": SubRutter(
                    StatusChild,
                    charter=lambda context: note("subrutter-charter", {}),
                    next_on_outcome="done",
                ),
                "done": Terminal(
                    lambda context: note("root-terminal", VoyageResult("complete", {}))
                ),
            }

    scenario = current_model_scenarios[scenario_name]
    voyage = RutterRegistry({"status": StatusRoot}, tmp_path).create(
        "status",
        Path(f"{scenario_name}.reckoning.json"),
        {},
    )
    backing = voyage._store
    assert isinstance(backing, ReckoningStore)
    with backing.transaction() as initial:
        backing.replace(initial, scenario.reckoning)
    counting = _CountingStatusStore(backing)
    voyage._store = counting
    authored.clear()

    leaf_calls = 0
    condition_calls = 0
    real_leaf = engine.deepest_active_leaf
    real_condition = engine._condition

    def count_leaf(reckoning: Reckoning):
        nonlocal leaf_calls
        leaf_calls += 1
        return real_leaf(reckoning)

    def count_condition(reckoning: Reckoning, evolution: object, **kwargs):
        nonlocal condition_calls
        condition_calls += 1
        return real_condition(reckoning, evolution, **kwargs)

    monkeypatch.setattr(engine, "deepest_active_leaf", count_leaf)
    monkeypatch.setattr(engine, "_condition", count_condition)

    status = voyage.get_status()

    assert isinstance(status, VoyageStatus)
    assert (
        status.current_evolution.rutter_id,
        status.current_evolution.definition_version,
        status.current_evolution.evolution_id,
        status.current_evolution.depth,
        status.current_evolution.condition,
    ) == ("status-child" if depth else "root", 1, evolution_id, depth, condition)
    if instruction_kind == "llm":
        assert status.instruction == Message(
            {"text": "Review.", "answer": {"approved": {}}},
            {
                "evolution": {
                    "id": "review",
                    "entry_id": "entry-review",
                    "revision": 0,
                },
                "payload": {},
            },
        )
    elif instruction_kind == "machine":
        assert isinstance(status.instruction, MachineInstruction)
        assert (status.instruction.machine_id, status.instruction.mode) == (
            "action-entry-action",
            "pure",
        )
    else:
        assert status.instruction is None
    assert status.active_result == active_result
    assert status.fault == fault
    assert (counting.transactions, counting.reads) == (1, 0)
    assert (leaf_calls, condition_calls) == (1, 1)
    assert authored == []


def test_public_operations_use_only_the_transaction_bound_validation(
    tmp_path: Path,
) -> None:
    voyage = RutterRegistry({"example": ExampleRutter}, tmp_path).create(
        "example",
        Path("single-bound-validation.reckoning.json"),
        {},
    )
    store = voyage._store
    assert isinstance(store, ReckoningStore)
    bound_validations = 0
    real_bound_validation = voyage._validate_reckoning

    def count_bound_validation(reckoning: Reckoning) -> None:
        nonlocal bound_validations
        bound_validations += 1
        real_bound_validation(reckoning)

    store._semantic_validator = count_bound_validation
    response = {"revision": 0, "outcome": "reported", "evidence": {}}

    before = bound_validations
    voyage.get_status()
    assert bound_validations == before + 1

    before = bound_validations
    assert voyage.validate(response).valid is True
    assert bound_validations == before + 1

    before = bound_validations
    with pytest.raises(RutterValidationError, match="response is required"):
        voyage.next()
    assert bound_validations == before + 1


@pytest.mark.parametrize("operation", ("get_status", "validate", "next"))
def test_malformed_recovery_is_rejected_at_transaction_decode_before_callbacks(
    tmp_path: Path,
    operation: str,
) -> None:
    authored: list[str] = []

    class CallbackProbe(Rutter):
        rutter_id = "callback-probe"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    answer=AnswerSpec({"approved": {}}),
                    data=lambda context: {},
                    validate=lambda context: (
                        authored.append("validate") or ValidationReport(True)
                    ),
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path(f"malformed-recovery-{operation}.reckoning.json")
    voyage = RutterRegistry({"probe": CallbackProbe}, tmp_path).create(
        "probe",
        path,
        {},
    )
    current = voyage._reckoning
    malformed = replace(
        current,
        active_effect=_EffectRecovery(
            f"action-{current.root.entered_evolution.entry_id}",
            current.root.run_id,
            current.root.entered_evolution.entry_id,
            current.root.entered_evolution.evolution_id,
            "repeat-safe",
            "planned",
            None,
        ),
    )
    bare_store = ReckoningStore((tmp_path / path).absolute())
    with bare_store.transaction() as persisted:
        bare_store.replace(persisted, malformed)

    store = voyage._store
    assert isinstance(store, ReckoningStore)
    bound_validations = 0
    real_bound_validation = voyage._validate_reckoning

    def count_bound_validation(reckoning: Reckoning) -> None:
        nonlocal bound_validations
        bound_validations += 1
        real_bound_validation(reckoning)

    store._semantic_validator = count_bound_validation
    response = {"revision": 0, "outcome": "approved", "evidence": {}}

    with pytest.raises(RutterStateError, match="active effect recovery"):
        if operation == "get_status":
            voyage.get_status()
        elif operation == "validate":
            voyage.validate(response)
        else:
            voyage.next(response)

    assert bound_validations == 1
    assert authored == []


@pytest.fixture
def inactive_child_metadata_scenario() -> tuple[type[Rutter], type[Rutter]]:
    """Return a root with a declared child that remains inactive on reopen."""

    class InactiveChild(Rutter):
        rutter_id = "inactive-child"
        definition_version = 1
        initial_evolution_id = "done"

        def define_evolutions(self) -> Mapping[str, object]:
            return {"done": Terminal(VoyageResult("child", {}))}

    class RootWithInactiveChild(Rutter):
        rutter_id = "root-with-inactive-child"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    answer=AnswerSpec({"approved": {}}),
                    next_on_outcome="done",
                ),
                "delegate": SubRutter(
                    InactiveChild,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("root", {})),
            }

    return RootWithInactiveChild, InactiveChild


def test_reopen_ignores_inactive_child_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inactive_child_metadata_scenario: tuple[type[Rutter], type[Rutter]],
) -> None:
    """Resolving inactive child metadata must not be required to reopen."""

    root, child = inactive_child_metadata_scenario
    path = Path("inactive-child.reckoning.json")
    registry = RutterRegistry({"root": root}, tmp_path)
    registry.create("root", path, {})
    before = (tmp_path / path).read_bytes()
    monkeypatch.setattr(child, "definition_version", 2)

    reopened = registry.open(path)

    assert reopened.get_status().current_evolution.evolution_id == "review"
    assert reopened.get_status().current_evolution.condition == "ready"
    assert (tmp_path / path).read_bytes() == before


def test_reopen_rejects_malformed_known_fault_coordinates(tmp_path: Path) -> None:
    """Accepting a known fault outside its durable LLMStep coordinates must fail."""

    def fail_route(context: object) -> str:
        del context
        raise RuntimeError("private route detail")

    class FaultingPrompt(Rutter):
        rutter_id = "faulting-prompt"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    answer=AnswerSpec({"approved": {}}),
                    choose_next=fail_route,
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("malformed-known-fault.reckoning.json")
    registry = RutterRegistry({"faulting": FaultingPrompt}, tmp_path)
    voyage = registry.create("faulting", path, {})
    message = voyage.get_status().instruction
    faulted = voyage.next(
        {
            "revision": message.data["evolution"]["revision"],
            "outcome": "approved",
            "evidence": {},
        },
        continue_=False,
    )
    assert faulted.condition == "fault"
    assert type(voyage._store.read().fault).__name__ == "KnownFault"

    unbound_store = ReckoningStore(tmp_path / path)
    with unbound_store.transaction() as current:
        unbound_store.replace(
            current,
            replace(
                current,
                fault=KnownFault(
                    "routing",
                    current.root.run_id,
                    "other-state",
                    current.root.entered_evolution.entry_id,
                    None,
                    (),
                ),
            ),
        )

    with pytest.raises(RutterStateError, match="mismatched fault authority"):
        registry.open(path)


def test_opaque_legacy_fault_reopens_and_remains_blocked(tmp_path: Path) -> None:
    """Discarding an opaque legacy fault or treating it as ready must fail."""

    class PromptRutter(Rutter):
        rutter_id = "opaque-fault-prompt"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    answer=AnswerSpec({"approved": {}}),
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("opaque-fault.reckoning.json")
    registry = RutterRegistry({"prompt": PromptRutter}, tmp_path)
    voyage = registry.create("prompt", path, {})
    with voyage._store.transaction() as current:
        voyage._store.replace(
            current,
            replace(current, fault=OpaqueFault({"legacy": {"detail": "opaque"}})),
        )

    reopened = registry.open(path)

    assert type(reopened._reckoning.fault).__name__ == "OpaqueFault"
    assert reopened.get_status().current_evolution.condition == "fault"
    with pytest.raises(RunBlocked):
        reopened.next()


def test_pure_action_instruction_is_stable_read_only_and_zero_argument(
    tmp_path: Path,
) -> None:
    """Skipping MachineStep instructions or invoking their callback during a read must fail."""

    seen: list[MachineContext] = []

    def run(context: MachineContext) -> MachineResult:
        seen.append(context)
        return MachineResult("calculated", {"count": len(context.evolution.history.machines())})

    class PureActionRutter(Rutter):
        rutter_id = "pure-action"
        definition_version = 1
        initial_evolution_id = "calculate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "calculate": MachineStep(run, mode="pure", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("pure-action.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})
    before = (tmp_path / path).read_bytes()

    first = voyage.get_status().instruction
    second = voyage.get_status().instruction
    reopened = registry.open(path).get_status().instruction

    assert isinstance(first, MachineInstruction)
    assert isinstance(second, MachineInstruction)
    assert isinstance(reopened, MachineInstruction)
    assert first.machine_id == second.machine_id == reopened.machine_id
    assert first.mode == "pure"
    assert tuple(inspect.signature(first.run).parameters) == ()
    assert first.answer_format == {
        "outcome": "declared outcome",
        "value": {"type": "finite JSON"},
    }
    assert seen == []
    assert first.run() == MachineResult("calculated", {"count": 0})
    assert len(seen) == 1
    assert seen[0].machine_id == first.machine_id
    assert seen[0].evolution.evolution_id == "calculate"
    assert seen[0].evolution.evolution_entry_id == voyage.get_status().current_evolution.evolution_entry_id
    assert (tmp_path / path).read_bytes() == before


@pytest.mark.parametrize(
    ("result", "valid", "code"),
    (
        (MachineResult("calculated", {"count": 1}), True, None),
        ({"outcome": "calculated", "value": {"count": 1}}, True, None),
        ({"outcome": "calculated"}, False, "invalid-envelope"),
        (
            {"outcome": "calculated", "value": {}, "extra": None},
            False,
            "invalid-envelope",
        ),
        (
            {"outcome": "calculated", "value": float("nan")},
            False,
            "nonfinite-value",
        ),
    ),
)
def test_action_validation_requires_the_exact_action_result_envelope(
    tmp_path: Path,
    result: object,
    valid: bool,
    code: str | None,
) -> None:
    """Treating an MachineStep as inapplicable or accepting a loose envelope must fail."""

    class PureActionRutter(Rutter):
        rutter_id = "validate-action"
        definition_version = 1
        initial_evolution_id = "calculate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "calculate": MachineStep(
                    lambda context: MachineResult("calculated", {}),
                    mode="pure",
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("validate-action.reckoning.json")
    voyage = RutterRegistry({"pure": PureActionRutter}, tmp_path).create(
        "pure", path, {}
    )
    before = (tmp_path / path).read_bytes()

    report = voyage.validate(result)

    assert report.valid is valid
    assert tuple(issue.code for issue in report.issues) == (() if code is None else (code,))
    assert (tmp_path / path).read_bytes() == before


def test_public_cutover_exports_voyage_and_self_describing_operating_methods(
    tmp_path: Path,
) -> None:
    """Dropping the new registry or restoring compatibility operations must fail."""

    assert rutter_public.RutterRegistry is RutterRegistry
    assert rutter_public.Voyage is engine.Voyage
    assert not hasattr(rutter_public, "BaseRutter")
    voyage = RutterRegistry({"example": ExampleRutter}, tmp_path).create(
        "example", Path("surface.reckoning.json"), {}
    )
    assert tuple(inspect.signature(voyage.get_status).parameters) == ()
    assert tuple(inspect.signature(voyage.validate).parameters) == ("response",)
    assert tuple(inspect.signature(voyage.next).parameters) == (
        "response",
        "continue_",
        "dry_run",
    )
    assert tuple(inspect.signature(voyage.help).parameters) == ()
    assert voyage.compass_facing_methods == ("get_status", "validate", "next")
    assert not hasattr(voyage, "get_current_node")
    assert not hasattr(voyage, "get_instruction")
    assert not hasattr(voyage, "advance")
    assert not hasattr(voyage, "reckoning")
    next_parameters = inspect.signature(engine._next).parameters
    assert tuple(next_parameters) == ("voyage", "response", "continue_", "dry_run")
    assert next_parameters["response"].default is engine.MISSING


def test_done_remains_terminal_after_its_attached_case_child_returns() -> None:
    """A post-Terminal attached-case return must not reopen completion."""

    result = VoyageResult("complete", {})
    done = TerminalRecord("done-root", "entry-root", "done", result)
    child = CompletedRun(
        "run-child",
        "child",
        1,
        Charter({}),
        (
            TerminalRecord(
                "done-child",
                "entry-child",
                "done",
                result,
            ),
        ),
    )
    returned = SubRutterRecord(
        "call-child",
        "entry-root",
        None,
        "terminal-check",
        done.record_id,
        child.run_id,
    )
    reckoning = Reckoning(
        3,
        1,
        ActiveRun(
            "run-root",
            "root",
            1,
            Charter({}),
            EnteredEvolution("entry-root", "done"),
            (done, returned),
            None,
        ),
        {child.run_id: child},
        None,
        None,
    )

    assert engine._condition(reckoning, Terminal(result)) == "terminal"


def test_every_operation_reloads_authoritative_reckoning(
    tmp_path: Path,
) -> None:
    """Using a stale in-memory Reckoning must not hide another handle's advance."""

    path = Path("reload.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, tmp_path)
    first = registry.create("example", path, {})
    stale = registry.open(path)

    terminal = first.next(
        {"revision": 0, "outcome": "reported", "evidence": {}},
        continue_=True,
    )

    assert stale.get_status().current_evolution == terminal
    assert stale.get_status().instruction is None
    assert stale.next() == terminal


def test_initial_prompt_render_failure_creates_no_partial_authority(
    tmp_path: Path,
) -> None:
    """Persisting an entrance without its open Turn violates atomic creation."""

    def fail_data(context: object) -> Mapping[str, object]:
        del context
        raise RuntimeError("private initial detail")

    class FailingStartRutter(Rutter):
        rutter_id = "failing-start"
        definition_version = 1
        initial_evolution_id = "start"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "start": LLMStep(
                    "Start.",
                    answer=AnswerSpec({"go": {}}),
                    data=fail_data,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("failing-start.reckoning.json")
    registry = RutterRegistry({"failure": FailingStartRutter}, tmp_path)

    with pytest.raises(RutterStateError, match="LLMStep materialization failed"):
        registry.create("failure", path, {})

    assert not (tmp_path / path).exists()


def test_continuation_limit_leaves_entered_done_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted yield phase or rollback at the operation limit must fail."""

    path = Path("limit.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, tmp_path)
    voyage = registry.create("example", path, {})
    monkeypatch.setattr(engine, "_OPERATION_LIMIT", 0)

    with pytest.raises(RutterStateError, match="continuation limit"):
        voyage.next(
            {"revision": 0, "outcome": "reported", "evidence": {}},
            continue_=True,
        )

    reopened = registry.open(path)
    assert reopened.get_status().current_evolution.evolution_id == "complete"
    assert reopened.get_status().current_evolution.condition == "ready"
    assert reopened._store.read().root.history[0].response is not None
    monkeypatch.setattr(engine, "_OPERATION_LIMIT", 100)
    assert reopened.next().condition == "terminal"
