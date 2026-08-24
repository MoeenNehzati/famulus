"""Specify the LLMStep/Terminal lifecycle at the bound-voyage boundary."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable, Mapping

import pytest

import officina.rutter.engine as engine
import officina.rutter.reducer as reducer
from officina.rutter.model import (
    MachineStep,
    MachineContext,
    MachineRecord,
    MachineResult,
    ActiveChild,
    LLMResponseContext,
    SubRutter,
    SubRutterRecord,
    Charter,
    CompletedRun,
    Terminal,
    TerminalRecord,
    KnownFault,
    Message,
    EvolutionView,
    NotApplicable,
    PreviewUnavailable,
    LLMStep,
    MachineInstruction,
    Reckoning,
    Rutter,
    RunBlocked,
    RutterStateError,
    RutterValidationError,
    VoyageResult,
    EvolutionContext,
    Turn,
    ValidationIssue,
    ValidationReport,
    _EffectRecovery,
)
from officina.rutter.runtime import RutterRegistry
from test_support.rutter_fixtures import (
    DirectChildRutter,
    ExampleRutter,
    response_schema as _response_schema,
)


def _assert_effect(
    effect: _EffectRecovery | None,
    *,
    machine_id: str,
    owner_run_id: str,
    evolution_entry_id: str,
    evolution_id: str,
    mode: str,
    disposition: str,
    result: MachineResult | None,
) -> _EffectRecovery:
    assert effect is not None
    assert effect.machine_id == machine_id
    assert effect.owner_run_id == owner_run_id
    assert effect.evolution_entry_id == evolution_entry_id
    assert effect.evolution_id == evolution_id
    assert effect.mode == mode
    assert effect.disposition == disposition
    assert effect.result == result
    return effect


def _assert_fault(
    fault: KnownFault | object | None,
    *,
    category: str,
    run_id: str,
    evolution_id: str,
    evolution_entry_id: str,
    target_evolution_id: str | None = None,
    transition_hook_ids: tuple[str, ...] = (),
) -> KnownFault:
    assert isinstance(fault, KnownFault)
    assert fault.category == category
    assert fault.run_id == run_id
    assert fault.evolution_id == evolution_id
    assert fault.evolution_entry_id == evolution_entry_id
    assert fault.target_evolution_id == target_evolution_id
    assert fault.transition_hook_ids == transition_hook_ids
    return fault


def test_pure_action_accepts_supplied_result_without_callback(
    tmp_path: Path,
) -> None:
    """Losing accepted MachineStep work or invoking the author callback must fail."""

    executions: list[MachineContext] = []
    routes: list[tuple[MachineContext, MachineResult]] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context)
        return MachineResult("calculated", {"source": "callback"})

    def route(context: MachineContext, result: MachineResult) -> str:
        routes.append((context, result))
        return "done"

    class PureActionRutter(Rutter):
        rutter_id = "pure-result"
        definition_version = 1
        initial_evolution_id = "calculate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "calculate": MachineStep(execute, mode="pure", choose_next=route),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("pure-result.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)
    source_entry = voyage.get_status().current_evolution.evolution_entry_id
    supplied = MachineResult("calculated", {"source": "supplied"})

    entered = voyage.advance(supplied, continue_=False)
    persisted = registry.open(path)._store.read()

    assert entered.evolution_id == "done"
    assert entered.condition == "ready"
    assert executions == []
    assert len(routes) == 1
    route_context, route_result = routes[0]
    assert route_context.machine_id == instruction.machine_id
    assert route_context.evolution.history.entries() == ()
    assert route_result == supplied
    assert persisted.global_revision == 1
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, MachineRecord)
    assert record.machine_id == instruction.machine_id
    assert record.evolution_entry_id == source_entry
    assert record.evolution_id == "calculate"
    assert record.mode == "pure"
    assert record.result == supplied


def test_omitted_action_result_runs_callback_once(
    tmp_path: Path,
) -> None:
    """Requiring a supplied result or rerunning pure work after acceptance must fail."""

    executions: list[MachineContext] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context)
        return MachineResult("calculated", {"entrance": context.evolution.evolution_entry_id})

    class PureActionRutter(Rutter):
        rutter_id = "automatic-pure"
        definition_version = 1
        initial_evolution_id = "calculate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "calculate": MachineStep(execute, mode="pure", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("automatic-pure.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})

    terminal = voyage.advance()
    reopened = registry.open(path)

    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert reopened.advance() == terminal
    assert len(executions) == 1
    actions = reopened._store.read().root.history
    assert len(actions) == 2
    assert isinstance(actions[0], MachineRecord)
    assert actions[0].result == MachineResult(
        "calculated", {"entrance": executions[0].evolution.evolution_entry_id}
    )
    assert isinstance(actions[1], TerminalRecord)


def test_repeat_safe_instruction_persists_completed_recovery_before_return(
    tmp_path: Path,
) -> None:
    """Issuing without planned authority or returning before completion must fail."""

    executions: list[MachineContext] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context)
        return MachineResult("stored", {"action_id": context.machine_id})

    class RepeatSafeRutter(Rutter):
        rutter_id = "repeat-safe"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(execute, mode="repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("repeat-safe.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    planned = voyage._store.read()
    before = (tmp_path / path).read_bytes()

    first = voyage.get_status().instruction
    reopened_instruction = registry.open(path).get_status().instruction

    assert isinstance(first, MachineInstruction)
    assert isinstance(reopened_instruction, MachineInstruction)
    assert first.machine_id == reopened_instruction.machine_id
    assert first.mode == "repeat-safe"
    _assert_effect(
        planned.active_effect,
        machine_id=first.machine_id,
        owner_run_id=planned.root.run_id,
        evolution_entry_id=planned.root.entered_evolution.entry_id,
        evolution_id="store",
        mode="repeat-safe",
        disposition="planned",
        result=None,
    )
    assert executions == []
    assert (tmp_path / path).read_bytes() == before

    result = first.run()
    completed = registry.open(path)._store.read()

    assert result == MachineResult("stored", {"action_id": first.machine_id})
    assert len(executions) == 1
    assert completed.root.history == ()
    _assert_effect(
        completed.active_effect,
        machine_id=first.machine_id,
        owner_run_id=planned.root.run_id,
        evolution_entry_id=planned.root.entered_evolution.entry_id,
        evolution_id="store",
        mode="repeat-safe",
        disposition="completed",
        result=result,
    )
    assert reopened_instruction.run() == result
    assert len(executions) == 1


def test_effectful_supplied_result_requires_completed_recovery(
    tmp_path: Path,
) -> None:
    """Accepting planned or mismatched effect work must fail without mutation."""

    executions: list[str] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context.machine_id)
        return MachineResult("stored", {"sequence": 1})

    class RepeatSafeRutter(Rutter):
        rutter_id = "exact-authority"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(execute, mode="repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("exact-authority.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)
    planned_bytes = (tmp_path / path).read_bytes()

    with pytest.raises(RutterValidationError):
        voyage.advance(MachineResult("stored", {"sequence": 1}), continue_=False)
    assert executions == []
    assert (tmp_path / path).read_bytes() == planned_bytes

    completed_result = instruction.run()
    completed_bytes = (tmp_path / path).read_bytes()
    with pytest.raises(RutterValidationError):
        voyage.advance(MachineResult("stored", {"sequence": 2}), continue_=False)
    assert executions == [instruction.machine_id]
    assert (tmp_path / path).read_bytes() == completed_bytes

    entered = voyage.advance(completed_result, continue_=False)
    persisted = registry.open(path)._store.read()

    assert entered.evolution_id == "done"
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, MachineRecord)
    assert record.machine_id == instruction.machine_id
    assert record.mode == "repeat-safe"
    assert record.result == completed_result


def test_omitted_repeat_safe_action_runs_and_consumes_the_same_wrapper(
    tmp_path: Path,
) -> None:
    """Bypassing durable completion during automatic continuation must fail."""

    executions: list[str] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context.machine_id)
        return MachineResult("stored", {"action_id": context.machine_id})

    class RepeatSafeRutter(Rutter):
        rutter_id = "automatic-repeat"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(execute, mode="repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("automatic-repeat.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)

    terminal = voyage.advance()
    persisted = registry.open(path)._store.read()

    assert terminal.condition == "terminal"
    assert executions == [instruction.machine_id]
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 2
    action_record = persisted.root.history[0]
    assert isinstance(action_record, MachineRecord)
    assert action_record.machine_id == instruction.machine_id
    assert action_record.result == MachineResult(
        "stored", {"action_id": instruction.machine_id}
    )
    assert isinstance(persisted.root.history[1], TerminalRecord)


def test_non_repeat_safe_markers_precede_accepted_action(
    tmp_path: Path,
) -> None:
    """Calling non-repeat-safe author code while recovery is planned must fail."""

    path = Path("non-repeat-safe.reckoning.json")
    authority_path = tmp_path / path
    observed: list[str] = []

    def execute(context: MachineContext) -> MachineResult:
        del context
        mapping = json.loads(authority_path.read_text(encoding="utf-8"))
        observed.append(mapping["active_effect"]["disposition"])
        return MachineResult("sent", {"receipt": "one"})

    class NonRepeatSafeRutter(Rutter):
        rutter_id = "non-repeat-safe"
        definition_version = 1
        initial_evolution_id = "send"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "send": MachineStep(execute, mode="non-repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    registry = RutterRegistry({"send": NonRepeatSafeRutter}, tmp_path)
    voyage = registry.create("send", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)
    assert instruction.mode == "non-repeat-safe"
    planned_effect = voyage._store.read().active_effect
    assert planned_effect is not None
    assert planned_effect.disposition == "planned"

    result = instruction.run()
    completed = registry.open(path)._store.read()

    assert observed == ["uncertain"]
    assert result == MachineResult("sent", {"receipt": "one"})
    assert completed.active_effect is not None
    assert completed.active_effect.disposition == "completed"
    assert completed.active_effect.result == result


def test_non_repeat_safe_crash_windows_preserve_authoritative_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying uncertain work or losing completed work across a crash must fail."""

    class SimulatedCrash(BaseException):
        pass

    def create(
        name: str,
        execute: Callable[[MachineContext], MachineResult],
    ) -> tuple[RutterRegistry, object, Path]:
        class CrashRutter(Rutter):
            rutter_id = name
            definition_version = 1
            initial_evolution_id = "send"

            def define_evolutions(self) -> Mapping[str, object]:
                return {
                    "send": MachineStep(
                        execute,
                        mode="non-repeat-safe",
                        next_on_outcome="done",
                    ),
                    "done": Terminal(VoyageResult("complete", {})),
                }

        path = Path(f"{name}.reckoning.json")
        registry = RutterRegistry({name: CrashRutter}, tmp_path)
        return registry, registry.create(name, path, {}), path

    before_calls: list[str] = []

    def before_callback(context: MachineContext) -> MachineResult:
        before_calls.append(context.machine_id)
        return MachineResult("sent", {})

    before_registry, _, before_path = create("crash-before", before_callback)
    before_reopen = before_registry.open(before_path)
    before_effect = before_reopen._store.read().active_effect
    assert before_effect is not None
    assert before_effect.disposition == "planned"
    assert before_calls == []

    marker_calls: list[str] = []

    def marker_callback(context: MachineContext) -> MachineResult:
        marker_calls.append(context.machine_id)
        return MachineResult("sent", {})

    marker_registry, marker_voyage, marker_path = create(
        "crash-after-marker", marker_callback
    )
    marker_instruction = marker_voyage.get_status().instruction
    assert isinstance(marker_instruction, MachineInstruction)
    original_publish = engine._publish

    def crash_after_marker(
        voyage: object,
        previous: Reckoning,
        replacement: Reckoning,
    ) -> Reckoning:
        published = original_publish(voyage, previous, replacement)
        effect = replacement.active_effect
        if effect is not None and effect.disposition == "uncertain":
            raise SimulatedCrash
        return published

    monkeypatch.setattr(engine, "_publish", crash_after_marker)
    with pytest.raises(SimulatedCrash):
        marker_instruction.run()
    monkeypatch.setattr(engine, "_publish", original_publish)
    marker_reopen = marker_registry.open(marker_path)
    marker_effect = marker_reopen._store.read().active_effect
    assert marker_effect is not None
    assert marker_effect.disposition == "uncertain"
    assert marker_calls == []

    effect_calls: list[str] = []

    def crash_after_effect(context: MachineContext) -> MachineResult:
        effect_calls.append(context.machine_id)
        raise SimulatedCrash

    effect_registry, effect_voyage, effect_path = create(
        "crash-after-effect", crash_after_effect
    )
    effect_instruction = effect_voyage.get_status().instruction
    assert isinstance(effect_instruction, MachineInstruction)
    with pytest.raises(SimulatedCrash):
        effect_instruction.run()
    effect_reopen = effect_registry.open(effect_path)
    effect_recovery = effect_reopen._store.read().active_effect
    assert effect_recovery is not None
    assert effect_recovery.disposition == "uncertain"
    assert effect_calls == [effect_instruction.machine_id]
    assert effect_reopen.get_status().instruction is None
    assert effect_reopen.get_status().current_evolution.condition == "uncertain"
    with pytest.raises(RunBlocked):
        effect_reopen.validate(MachineResult("sent", {}))
    with pytest.raises(RunBlocked):
        effect_reopen.advance()

    completed_calls: list[str] = []

    def completed_callback(context: MachineContext) -> MachineResult:
        completed_calls.append(context.machine_id)
        return MachineResult("sent", {"receipt": "durable"})

    completed_registry, completed_voyage, completed_path = create(
        "crash-after-completed", completed_callback
    )
    completed_instruction = completed_voyage.get_status().instruction
    assert isinstance(completed_instruction, MachineInstruction)

    def crash_after_completed(
        voyage: object,
        previous: Reckoning,
        replacement: Reckoning,
    ) -> Reckoning:
        published = original_publish(voyage, previous, replacement)
        effect = replacement.active_effect
        if effect is not None and effect.disposition == "completed":
            raise SimulatedCrash
        return published

    monkeypatch.setattr(engine, "_publish", crash_after_completed)
    with pytest.raises(SimulatedCrash):
        completed_instruction.run()
    monkeypatch.setattr(engine, "_publish", original_publish)
    completed_reopen = completed_registry.open(completed_path)
    completed_effect = completed_reopen._store.read().active_effect
    assert completed_effect is not None
    assert completed_effect.disposition == "completed"
    recovered_instruction = completed_reopen.get_status().instruction
    assert isinstance(recovered_instruction, MachineInstruction)
    recovered = recovered_instruction.run()
    assert recovered == MachineResult("sent", {"receipt": "durable"})
    assert completed_calls == [completed_instruction.machine_id]
    completed_reopen.advance(recovered, continue_=False)
    consumed = completed_registry.open(completed_path)._store.read()
    assert consumed.active_effect is None
    assert len(consumed.root.history) == 1
    assert isinstance(consumed.root.history[0], MachineRecord)


@pytest.mark.parametrize("disposition", ("planned", "completed", "uncertain"))
@pytest.mark.parametrize("corruption", ("machine", "state", "mode", "subrutter"))
def test_reopen_rejects_recovery_that_differs_from_the_bound_action(
    tmp_path: Path,
    disposition: str,
    corruption: str,
) -> None:
    """Trusting structurally valid recovery over the bound state must fail."""

    class NonRepeatSafeRutter(Rutter):
        rutter_id = "corrupt-mode"
        definition_version = 1
        initial_evolution_id = "send"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "send": MachineStep(
                    lambda context: MachineResult("sent", {}),
                    mode="non-repeat-safe",
                    next_on_outcome="done",
                ),
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path(f"corrupt-{corruption}-{disposition}.reckoning.json")
    authority_path = tmp_path / path
    registry = RutterRegistry({"send": NonRepeatSafeRutter}, tmp_path)
    voyage = registry.create("send", path, {})
    if disposition == "completed":
        instruction = voyage.get_status().instruction
        assert isinstance(instruction, MachineInstruction)
        instruction.run()
    mapping = json.loads(authority_path.read_text(encoding="utf-8"))
    effect = mapping["active_effect"]
    if corruption == "machine":
        effect["action_id"] = "wrong-action"
    elif corruption == "mode":
        effect["mode"] = "repeat-safe"
    elif corruption == "subrutter":
        mapping["root"]["entered_node"]["state_id"] = "delegate"
        effect["state_id"] = "delegate"
    else:
        mapping["root"]["entered_node"]["state_id"] = "done"
        effect["state_id"] = "done"
    effect["disposition"] = disposition
    effect["result"] = (
        {"outcome": "sent", "value": {}} if disposition == "completed" else None
    )
    authority_path.write_text(
        json.dumps(mapping, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(RutterStateError, match="does not match the MachineStep"):
        registry.open(path)


def test_every_effectful_target_and_self_loop_entrance_allocates_fresh_recovery(
    tmp_path: Path,
) -> None:
    """Entering or re-entering an effectful MachineStep without fresh authority must fail."""

    def execute(context: MachineContext) -> MachineResult:
        return MachineResult("again", {"action_id": context.machine_id})

    class EffectLoopRutter(Rutter):
        rutter_id = "effect-loop"
        definition_version = 1
        initial_evolution_id = "start"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "start": LLMStep(
                    "Start.",
                    response_schema=_response_schema("go"),
                    next_on_outcome="store",
                ),
                "store": MachineStep(
                    execute,
                    mode="repeat-safe",
                    next_on_outcome={"again": "store", "done": "done"},
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("effect-loop.reckoning.json")
    registry = RutterRegistry({"loop": EffectLoopRutter}, tmp_path)
    voyage = registry.create("loop", path, {})
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    first_entry = voyage.advance(
        {"outcome": "go"},
        responding_to=message.evolution_entry_id,
        continue_=False,
    )
    first_recovery = registry.open(path)._store.read().active_effect
    first_instruction = registry.open(path).get_status().instruction

    assert first_entry.evolution_id == "store"
    assert first_recovery is not None
    assert isinstance(first_instruction, MachineInstruction)
    assert first_recovery.evolution_entry_id == first_entry.evolution_entry_id
    assert first_recovery.machine_id == first_instruction.machine_id
    assert first_recovery.disposition == "planned"

    result = first_instruction.run()
    second_entry = voyage.advance(result, continue_=False)
    second_recovery = registry.open(path)._store.read().active_effect
    second_instruction = registry.open(path).get_status().instruction

    assert second_entry.evolution_id == "store"
    assert second_entry.evolution_entry_id != first_entry.evolution_entry_id
    assert second_recovery is not None
    assert isinstance(second_instruction, MachineInstruction)
    assert second_recovery.evolution_entry_id == second_entry.evolution_entry_id
    assert second_recovery.machine_id == second_instruction.machine_id
    assert second_recovery.disposition == "planned"
    assert second_instruction.machine_id != first_instruction.machine_id
    records = registry.open(path)._store.read().root.history
    assert len(records) == 2
    assert isinstance(records[0], Turn)
    assert isinstance(records[1], MachineRecord)
    assert records[1].machine_id == first_instruction.machine_id


def test_nested_action_recovery_is_owned_by_the_deepest_leaf_across_reopen(
    tmp_path: Path,
) -> None:
    """Giving a parent or missing child the sole effect slot must fail."""

    executions: list[MachineContext] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context)
        return MachineResult("worked", {"depth": 1})

    class ActionChild(Rutter):
        rutter_id = "action-child"
        definition_version = 1
        initial_evolution_id = "work"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "work": MachineStep(execute, mode="repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("child-complete", {})),
            }

    class ActionParent(Rutter):
        rutter_id = "action-parent"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    ActionChild,
                    charter=lambda context: {},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("parent-complete", {})),
            }

    path = Path("nested-action.reckoning.json")
    registry = RutterRegistry({"parent": ActionParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    child_view = voyage.advance(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    instruction = reopened.get_status().instruction

    assert child_view.evolution_id == "work"
    assert child_view.depth == 1
    assert isinstance(instruction, MachineInstruction)
    assert persisted.root.active_child is not None
    child_run = persisted.root.active_child.run
    assert persisted.active_effect is not None
    assert persisted.active_effect.owner_run_id == child_run.run_id
    assert (
        persisted.active_effect.evolution_entry_id
        == child_run.entered_evolution.entry_id
    )
    assert persisted.active_effect.machine_id == instruction.machine_id

    result = instruction.run()
    terminal = reopened.advance(result)
    settled = registry.open(path)._store.read()

    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert executions[0].machine_id == instruction.machine_id
    assert settled.active_effect is None
    assert settled.root.active_child is None
    assert len(settled.completed_runs) == 1
    completed_child = next(iter(settled.completed_runs.values()))
    assert isinstance(completed_child.history[0], MachineRecord)
    assert completed_child.history[0].machine_id == instruction.machine_id


@pytest.mark.parametrize(
    ("mode", "disposition"),
    (("repeat-safe", "planned"), ("non-repeat-safe", "uncertain")),
)
def test_effectful_instruction_callback_failure_persists_only_stable_fault_data(
    tmp_path: Path,
    mode: str,
    disposition: str,
) -> None:
    """Leaking callback text or leaving the voyage apparently ready must fail."""

    def execute(context: MachineContext) -> MachineResult:
        del context
        raise RuntimeError("private credential detail")

    class FailingActionRutter(Rutter):
        rutter_id = f"failing-{mode}"
        definition_version = 1
        initial_evolution_id = "work"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "work": MachineStep(execute, mode=mode, next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path(f"failing-{mode}.reckoning.json")
    registry = RutterRegistry({"failure": FailingActionRutter}, tmp_path)
    voyage = registry.create("failure", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)

    with pytest.raises(RunBlocked, match="MachineStep execution failed") as caught:
        instruction.run()

    assert "private credential detail" not in str(caught.value)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    _assert_fault(
        persisted.fault,
        category="action-execution",
        run_id=persisted.root.run_id,
        evolution_id="work",
        evolution_entry_id=persisted.root.entered_evolution.entry_id,
    )
    assert "private credential detail" not in (tmp_path / path).read_text()
    assert persisted.active_effect is not None
    assert persisted.active_effect.disposition == disposition
    assert reopened.get_status().current_evolution.condition == "fault"
    assert reopened.get_status().instruction is None
    with pytest.raises(RunBlocked):
        reopened.advance()


def test_dry_run_supplied_result_records_nothing(
    tmp_path: Path,
) -> None:
    """Previewing by invoking, accepting, or consuming MachineStep work must fail."""

    pure_calls: list[str] = []

    class PurePreviewRutter(Rutter):
        rutter_id = "pure-preview"
        definition_version = 1
        initial_evolution_id = "calculate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "calculate": MachineStep(
                    lambda context: (
                        pure_calls.append(context.machine_id)
                        or MachineResult("ready", {})
                    ),
                    mode="pure",
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    pure_path = Path("pure-preview.reckoning.json")
    pure = RutterRegistry({"pure": PurePreviewRutter}, tmp_path).create(
        "pure", pure_path, {}
    )
    pure_before = (tmp_path / pure_path).read_bytes()

    pure_preview = pure.advance(
        MachineResult("ready", {}),
        dry_run=True,
    )

    assert pure_preview.evolution_id == "done"
    assert pure_preview.evolution_entry_id is None
    assert pure_preview.condition == "preview"
    assert pure_calls == []
    assert (tmp_path / pure_path).read_bytes() == pure_before
    with pytest.raises(PreviewUnavailable):
        pure.advance(dry_run=True)
    assert (tmp_path / pure_path).read_bytes() == pure_before

    effect_calls: list[str] = []

    class EffectPreviewRutter(Rutter):
        rutter_id = "effect-preview"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(
                    lambda context: (
                        effect_calls.append(context.machine_id)
                        or MachineResult("stored", {})
                    ),
                    mode="repeat-safe",
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    effect_path = Path("effect-preview.reckoning.json")
    effect = RutterRegistry({"effect": EffectPreviewRutter}, tmp_path).create(
        "effect", effect_path, {}
    )
    planned_before = (tmp_path / effect_path).read_bytes()
    with pytest.raises(PreviewUnavailable):
        effect.advance(MachineResult("stored", {}), dry_run=True)
    assert effect_calls == []
    assert (tmp_path / effect_path).read_bytes() == planned_before

    instruction = effect.get_status().instruction
    assert isinstance(instruction, MachineInstruction)
    completed_result = instruction.run()
    completed_before = (tmp_path / effect_path).read_bytes()

    completed_preview = effect.advance(dry_run=True)

    assert completed_preview.evolution_id == "done"
    assert completed_preview.evolution_entry_id is None
    assert completed_preview.condition == "preview"
    assert effect_calls == [instruction.machine_id]
    assert (tmp_path / effect_path).read_bytes() == completed_before
    completed_effect = effect._store.read().active_effect
    assert completed_effect is not None
    assert completed_effect.disposition == "completed"
    assert effect._store.read().root.history == ()
    assert completed_result == MachineResult("stored", {})


@pytest.mark.parametrize("mode", ("pure", "repeat-safe", "non-repeat-safe"))
def test_continue_true_executes_action_entered_from_prompt(
    tmp_path: Path,
    mode: str,
) -> None:
    """Stopping at an automatically executable MachineInstruction must fail."""

    executions: list[MachineContext] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context)
        return MachineResult("worked", {"mode": mode})

    class PromptActionRutter(Rutter):
        rutter_id = f"prompt-action-{mode}"
        definition_version = 1
        initial_evolution_id = "start"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "start": LLMStep(
                    "Start.",
                    response_schema=_response_schema("go"),
                    next_on_outcome="work",
                ),
                "work": MachineStep(execute, mode=mode, next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path(f"prompt-action-{mode}.reckoning.json")
    registry = RutterRegistry({"flow": PromptActionRutter}, tmp_path)
    voyage = registry.create("flow", path, {})
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    terminal = voyage.advance(
        {"outcome": "go"},
        responding_to=message.evolution_entry_id,
    )
    persisted = registry.open(path)._store.read()

    assert terminal.evolution_id == "done"
    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 3
    assert isinstance(persisted.root.history[0], Turn)
    action_record = persisted.root.history[1]
    assert isinstance(action_record, MachineRecord)
    assert action_record.mode == mode
    assert action_record.machine_id == executions[0].machine_id
    assert isinstance(persisted.root.history[2], TerminalRecord)


@pytest.mark.parametrize(
    ("failure", "category"),
    (("routing", "routing"), ("target", "target-materialization")),
)
def test_accepted_action_record_survives_later_callback_fault_without_replay(
    tmp_path: Path,
    failure: str,
    category: str,
) -> None:
    """Rolling back or requesting already accepted MachineStep work must fail."""

    executions: list[str] = []

    def execute(context: MachineContext) -> MachineResult:
        executions.append(context.machine_id)
        return MachineResult("stored", {"receipt": "one"})

    def route(context: MachineContext, result: MachineResult) -> str:
        del context, result
        if failure == "routing":
            raise RuntimeError("private routing detail")
        return "broken"

    def materialize(context: EvolutionContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private target detail")

    class AcceptedActionRutter(Rutter):
        rutter_id = f"accepted-action-{failure}"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(execute, mode="repeat-safe", choose_next=route),
                "broken": LLMStep(
                    "Broken.",
                    response_schema=_response_schema("ok"),
                    data=materialize,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path(f"accepted-action-{failure}.reckoning.json")
    registry = RutterRegistry({"accepted": AcceptedActionRutter}, tmp_path)
    voyage = registry.create("accepted", path, {})
    instruction = voyage.get_status().instruction
    assert isinstance(instruction, MachineInstruction)
    result = instruction.run()

    faulted = voyage.advance(result)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted.evolution_id == "store"
    assert faulted.condition == "fault"
    assert executions == [instruction.machine_id]
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, MachineRecord)
    assert record.machine_id == instruction.machine_id
    assert record.result == result
    _assert_fault(
        persisted.fault,
        category=category,
        run_id=persisted.root.run_id,
        evolution_id="store",
        evolution_entry_id=record.evolution_entry_id,
        target_evolution_id="broken" if failure == "target" else None,
    )
    assert "private" not in (tmp_path / path).read_text()
    assert reopened.get_status().instruction is None
    with pytest.raises(RunBlocked):
        reopened.advance(result)
    assert executions == [instruction.machine_id]


def test_repeat_safe_reopen_retries_planned_work_with_the_same_action_id(
    tmp_path: Path,
) -> None:
    """Allocating a new id or blocking a repeat-safe planned retry must fail."""

    class SimulatedCrash(BaseException):
        pass

    attempts: list[str] = []

    def execute(context: MachineContext) -> MachineResult:
        attempts.append(context.machine_id)
        if len(attempts) == 1:
            raise SimulatedCrash
        return MachineResult("stored", {"attempt": len(attempts)})

    class RetryRutter(Rutter):
        rutter_id = "repeat-retry"
        definition_version = 1
        initial_evolution_id = "store"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "store": MachineStep(execute, mode="repeat-safe", next_on_outcome="done"),
                "done": Terminal(VoyageResult("complete", {})),
            }

    path = Path("repeat-retry.reckoning.json")
    registry = RutterRegistry({"retry": RetryRutter}, tmp_path)
    voyage = registry.create("retry", path, {})
    first = voyage.get_status().instruction
    assert isinstance(first, MachineInstruction)

    with pytest.raises(SimulatedCrash):
        first.run()

    reopened = registry.open(path)
    planned = reopened._store.read().active_effect
    second = reopened.get_status().instruction
    assert planned is not None
    assert planned.disposition == "planned"
    assert isinstance(second, MachineInstruction)
    assert second.machine_id == first.machine_id

    result = second.run()

    assert result == MachineResult("stored", {"attempt": 2})
    assert attempts == [first.machine_id, first.machine_id]
    completed = registry.open(path)._store.read().active_effect
    assert completed is not None
    assert completed.disposition == "completed"
    assert completed.machine_id == first.machine_id


def test_create_atomically_enters_prompt_with_its_exact_open_turn(
    tmp_path: Path,
) -> None:
    """Removing LLMStep materialization from creation must leave no partial entrance."""

    root = tmp_path / "reckonings"
    path = Path("prompt.reckoning.json")
    voyage = RutterRegistry({"example": ExampleRutter}, root).create(
        "example",
        path,
        {"artifact": "draft.md"},
    )

    persisted = voyage._store.read()
    turn = persisted.root.history[-1]

    assert isinstance(turn, Turn)
    assert turn.response is None
    assert turn.evolution_entry_id == persisted.root.entered_evolution.entry_id
    assert turn.evolution_id == persisted.root.entered_evolution.evolution_id
    assert turn.revision == persisted.global_revision
    assert isinstance(turn.message, Message)
    assert turn.message.data["evolution"] == {
        "id": "report",
        "entry_id": persisted.root.entered_evolution.entry_id,
    }
    assert turn.message.instructions == {
        "text": "Report.",
        "response_schema": {
            "type": "object",
            "properties": {"outcome": {"enum": ("reported",)}},
            "required": ("outcome",),
        },
    }
    assert turn.message.data["payload"] == {"chunk": "A"}


def test_prompt_read_operations_return_stored_values_without_writing(
    tmp_path: Path,
) -> None:
    """Rerendering or replacing during either read operation is a regression."""

    root = tmp_path / "reckonings"
    path = Path("readonly.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, root)
    voyage = registry.create("example", path, {})
    before = (root / path).read_bytes()

    first = voyage.get_status().instruction
    second = voyage.get_status().instruction
    current = voyage.get_status().current_evolution
    reopened = registry.open(path)

    assert isinstance(first, Message)
    assert second == first
    assert reopened.get_status().instruction == first
    assert current == EvolutionView(
        "example",
        1,
        "report",
        voyage._store.read().root.entered_evolution.entry_id,
        0,
        "ready",
    )
    assert (root / path).read_bytes() == before


@pytest.mark.parametrize(
    ("response", "current_token", "code"),
    (
        ({}, True, "invalid-outcome"),
        (
            {"revision": 0, "outcome": "reported"},
            True,
            "reserved-metadata",
        ),
        (
            {"outcome": "reported"},
            False,
            "stale-entrance",
        ),
        (
            {"outcome": "unknown"},
            True,
            "response-schema",
        ),
        (
            {"outcome": "reported", "n": float("nan")},
            True,
            "nonfinite-response",
        ),
    ),
)
def test_invalid_prompt_response_is_reported_and_advance_preserves_exact_bytes(
    tmp_path: Path,
    response: object,
    current_token: bool,
    code: str,
) -> None:
    """Weakening any response gate must not let invalid work mutate authority."""

    root = tmp_path / "reckonings"
    path = Path("invalid.reckoning.json")
    voyage = RutterRegistry({"example": ExampleRutter}, root).create(
        "example", path, {}
    )
    before = (root / path).read_bytes()
    status = voyage.get_status()
    current = status.current_evolution
    message = status.instruction
    assert isinstance(message, Message)
    responding_to = message.evolution_entry_id if current_token else "stale-entry"

    report = voyage.validate(response, responding_to=responding_to)

    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == (code,)
    assert voyage.get_status().current_evolution == current
    assert (root / path).read_bytes() == before
    with pytest.raises(RutterValidationError):
        voyage.advance(response, responding_to=responding_to)
    assert voyage.get_status().current_evolution == current
    assert (root / path).read_bytes() == before


def test_contextual_prompt_validation_receives_frozen_current_context(
    tmp_path: Path,
) -> None:
    """Bypassing the authored validator must admit evidence it explicitly rejects."""

    seen: list[object] = []

    def reject(context: LLMResponseContext) -> ValidationReport:
        seen.append(context)
        return ValidationReport(
            False,
            (
                ValidationIssue(
                    ("approved",),
                    "not-approved",
                    "approval evidence is required",
                ),
            ),
        )

    class ContextualRutter(Rutter):
        rutter_id = "contextual"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "review": LLMStep(
                    "Review.",
                    response_schema=_response_schema("accepted"),
                    assess_response=reject,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    voyage = RutterRegistry({"contextual": ContextualRutter}, root).create(
        "contextual", Path("contextual.reckoning.json"), {}
    )
    before = (root / "contextual.reckoning.json").read_bytes()
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    report = voyage.validate(
        {"outcome": "accepted", "approved": False},
        responding_to=message.evolution_entry_id,
    )

    assert report == ValidationReport(
        False,
        (
            ValidationIssue(
                ("approved",),
                "not-approved",
                "approval evidence is required",
            ),
        ),
    )
    assert len(seen) == 1
    context = seen[0]
    assert context.evolution.history.entries() == ()
    assert context.message == voyage.get_status().instruction
    assert context.response["outcome"] == "accepted"
    assert (root / "contextual.reckoning.json").read_bytes() == before


def test_valid_prompt_response_fills_the_same_turn_and_enters_done(
    tmp_path: Path,
) -> None:
    """Appending a second Turn or losing the accepted response must fail this test."""

    root = tmp_path / "reckonings"
    path = Path("accepted.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, root)
    voyage = registry.create("example", path, {})
    source = voyage._store.read().root.history[0]
    assert isinstance(source, Turn)
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    entered = voyage.advance(
        {"outcome": "reported", "note": "ok"},
        responding_to=message.evolution_entry_id,
        continue_=False,
    )
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert entered == EvolutionView(
        "example",
        1,
        "complete",
        persisted.root.entered_evolution.entry_id,
        0,
        "ready",
    )
    assert persisted.global_revision == 1
    assert len(persisted.root.history) == 1
    accepted = persisted.root.history[0]
    assert isinstance(accepted, Turn)
    assert accepted.record_id == source.record_id
    assert accepted.message == source.message
    assert accepted.response is not None
    assert accepted.response == {"outcome": "reported", "note": "ok"}
    assert reopened.get_status().instruction is None


def test_prompt_self_loop_allocates_a_new_entrance_and_rerenders_from_history(
    tmp_path: Path,
) -> None:
    """Reusing the source entrance or stored Message across re-entry is a bug."""

    def payload(context: object) -> Mapping[str, object]:
        return {"accepted": len(context.history.turns())}

    class SelfLoopRutter(Rutter):
        rutter_id = "self-loop"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "ask": LLMStep(
                    "Again?",
                    response_schema=_response_schema("again"),
                    data=payload,
                    next_on_outcome="ask",
                )
            }

    root = tmp_path / "reckonings"
    path = Path("self-loop.reckoning.json")
    voyage = RutterRegistry({"loop": SelfLoopRutter}, root).create(
        "loop", path, {}
    )
    first_message = voyage.get_status().instruction
    first_entry = voyage._store.read().root.entered_evolution.entry_id

    second_node = voyage.advance(
        {"outcome": "again"},
        responding_to=first_message.evolution_entry_id,
        continue_=False,
    )
    second_message = voyage.get_status().instruction

    assert second_node.evolution_id == "ask"
    assert second_node.evolution_entry_id != first_entry
    assert second_message != first_message
    assert second_message.data["payload"] == {"accepted": 1}
    assert "revision" not in second_message.data["evolution"]
    persisted = voyage._store.read()
    assert len(persisted.root.history) == 2
    assert persisted.root.history[0].response is not None
    assert persisted.root.history[1].response is None


def test_target_prompt_render_failure_keeps_accepted_source_and_faults_in_place(
    tmp_path: Path,
) -> None:
    """A target-render exception must not erase accepted work or enter its target."""

    def fail_data(context: object) -> Mapping[str, object]:
        del context
        raise RuntimeError("private target detail")

    class RenderFailureRutter(Rutter):
        rutter_id = "render-failure"
        definition_version = 1
        initial_evolution_id = "source"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "source": LLMStep(
                    "Source.",
                    response_schema=_response_schema("go"),
                    next_on_outcome="target",
                ),
                "target": LLMStep(
                    "Target.",
                    response_schema=_response_schema("stop"),
                    data=fail_data,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("render-failure.reckoning.json")
    registry = RutterRegistry({"failure": RenderFailureRutter}, root)
    voyage = registry.create("failure", path, {})
    source_entry = voyage._store.read().root.entered_evolution.entry_id
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    faulted = voyage.advance(
        {"outcome": "go"},
        responding_to=message.evolution_entry_id,
        continue_=False,
    )
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    assert faulted.evolution_id == "source"
    assert faulted.evolution_entry_id == source_entry
    persisted = reopened._store.read()
    assert persisted.root.history[0].response is not None
    assert persisted.root.entered_evolution.entry_id == source_entry
    _assert_fault(
        persisted.fault,
        category="target-materialization",
        run_id=persisted.root.run_id,
        evolution_id="source",
        evolution_entry_id=source_entry,
        target_evolution_id="target",
    )
    assert b"private target detail" not in (root / path).read_bytes()
    assert reopened.get_status().instruction is None
    with pytest.raises(RunBlocked):
        reopened.validate(
            {"outcome": "go"}, responding_to=message.evolution_entry_id
        )
    with pytest.raises(RunBlocked):
        reopened.advance()


def test_prompt_routing_failure_preserves_the_accepted_turn_before_fault(
    tmp_path: Path,
) -> None:
    """Combining acceptance and routing must not roll back a valid Response."""

    def fail_route(context: LLMResponseContext) -> str:
        del context
        raise RuntimeError("private routing detail")

    class RoutingFailureRutter(Rutter):
        rutter_id = "routing-failure"
        definition_version = 1
        initial_evolution_id = "source"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "source": LLMStep(
                    "Source.",
                    response_schema=_response_schema("go"),
                    choose_next=fail_route,
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("routing-failure.reckoning.json")
    registry = RutterRegistry({"failure": RoutingFailureRutter}, root)
    voyage = registry.create("failure", path, {})
    source_entry = voyage._store.read().root.entered_evolution.entry_id
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    faulted = voyage.advance(
        {"outcome": "go"},
        responding_to=message.evolution_entry_id,
        continue_=False,
    )
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    persisted = reopened._store.read()
    assert persisted.root.entered_evolution.entry_id == source_entry
    assert persisted.root.history[0].response is not None
    assert isinstance(persisted.fault, KnownFault)
    assert persisted.fault.category == "routing"
    assert b"private routing detail" not in (root / path).read_bytes()


def test_continue_true_settles_done_once_and_terminal_advance_is_idempotent(
    tmp_path: Path,
) -> None:
    """Duplicating the Terminal authority or advancing terminal state must fail."""

    root = tmp_path / "reckonings"
    path = Path("terminal.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, root)
    voyage = registry.create("example", path, {})
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    terminal = voyage.advance(
        {"outcome": "reported"},
        responding_to=message.evolution_entry_id,
        continue_=True,
    )
    before = (root / path).read_bytes()
    again = voyage.advance()
    dry_again = voyage.advance(dry_run=True)
    reopened = registry.open(path)

    assert terminal.condition == "terminal"
    assert terminal.evolution_id == "complete"
    assert again == terminal
    assert dry_again == terminal
    assert reopened.get_status().current_evolution == terminal
    assert reopened.get_status().instruction is None
    with pytest.raises(NotApplicable):
        reopened.validate({})
    persisted = reopened._store.read()
    assert persisted.root.history[-1].result == VoyageResult("completed", {})
    assert sum(
        1
        for entry in persisted.root.history
        if isinstance(entry, TerminalRecord)
    ) == 1
    assert (root / path).read_bytes() == before


def test_prompt_and_done_dry_runs_preview_without_entering_or_writing(
    tmp_path: Path,
) -> None:
    """Persisting either preview or rendering its target LLMStep is a regression."""

    target_calls: list[None] = []

    def target_data(context: EvolutionContext) -> Mapping[str, object]:
        del context
        target_calls.append(None)
        return {"rendered": True}

    class PreviewRutter(Rutter):
        rutter_id = "preview"
        definition_version = 1
        initial_evolution_id = "source"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "source": LLMStep(
                    "Source.",
                    response_schema=_response_schema("go"),
                    next_on_outcome="target",
                ),
                "target": LLMStep(
                    "Target.",
                    response_schema=_response_schema("finish"),
                    data=target_data,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("preview.reckoning.json")
    voyage = RutterRegistry({"preview": PreviewRutter}, root).create(
        "preview", path, {}
    )
    before = (root / path).read_bytes()
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    preview = voyage.advance(
        {"outcome": "go"},
        responding_to=message.evolution_entry_id,
        continue_=True,
        dry_run=True,
    )

    assert preview == EvolutionView("preview", 1, "target", None, 0, "preview")
    assert target_calls == []
    assert voyage._store.read().root.history[0].response is None
    assert (root / path).read_bytes() == before

    done_path = Path("done-preview.reckoning.json")
    done = RutterRegistry({"child": DirectChildRutter}, root).create(
        "child", done_path, {}
    )
    done_before = (root / done_path).read_bytes()
    done_preview = done.advance(dry_run=True)

    assert done_preview == EvolutionView("direct-child", 1, "complete", None, 0, "preview")
    assert done._store.read().root.history == ()
    assert (root / done_path).read_bytes() == done_before


def test_done_projection_failure_faults_without_a_done_record(
    tmp_path: Path,
) -> None:
    """A failed projection must not fabricate completion authority."""

    def fail_result(context: EvolutionContext) -> VoyageResult:
        del context
        raise RuntimeError("private result detail")

    class FailingDoneRutter(Rutter):
        rutter_id = "failing-done"
        definition_version = 1
        initial_evolution_id = "done"

        def define_evolutions(self) -> Mapping[str, object]:
            return {"done": Terminal(fail_result)}

    root = tmp_path / "reckonings"
    path = Path("failing-done.reckoning.json")
    registry = RutterRegistry({"done": FailingDoneRutter}, root)
    voyage = registry.create("done", path, {})

    faulted = voyage.advance()
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    persisted = reopened._store.read()
    assert isinstance(persisted.fault, KnownFault)
    assert persisted.fault.category == "done-projection"
    assert persisted.root.history == ()
    assert b"private result detail" not in (root / path).read_bytes()


def test_call_push_keeps_parent_entered_and_exposes_the_child_leaf(
    tmp_path: Path,
) -> None:
    """Failing to attach one sealed child must leave the parent falsely visible."""

    charter_contexts: list[EvolutionContext] = []

    def child_charter(context: EvolutionContext) -> Mapping[str, object]:
        charter_contexts.append(context)
        return {"scope": context.charter.data["scope"]}

    class CallingRutter(Rutter):
        rutter_id = "calling"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=child_charter,
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("completed", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-push.reckoning.json")
    voyage = RutterRegistry({"calling": CallingRutter}, root).create(
        "calling", path, {"scope": "child"}
    )
    before = (root / path).read_bytes()
    parent_entry = voyage._store.read().root.entered_evolution.entry_id

    assert voyage.get_status().instruction is None
    with pytest.raises(NotApplicable):
        voyage.validate({})
    assert (root / path).read_bytes() == before

    child_start = voyage.advance(continue_=False)
    persisted = voyage._store.read()
    child = persisted.root.active_child

    assert isinstance(child, ActiveChild)
    assert child_start == EvolutionView(
        "direct-child",
        1,
        "complete",
        child.run.entered_evolution.entry_id,
        1,
        "ready",
    )
    assert voyage.get_status().current_evolution == child_start
    assert persisted.root.entered_evolution.evolution_id == "delegate"
    assert persisted.root.entered_evolution.entry_id == parent_entry
    assert persisted.root.history == ()
    assert persisted.global_revision == 0
    assert persisted.completed_runs == {}
    assert child.kind == "explicit_call"
    assert child.site == "delegate"
    assert child.attached_to_transition_id is None
    assert child.run.charter == Charter({"scope": "child"})
    assert len(charter_contexts) == 1
    assert charter_contexts[0].evolution_id == "delegate"
    assert charter_contexts[0].evolution_entry_id == parent_entry
    assert charter_contexts[0].history.entries() == ()


def test_active_leaf_rejects_child_from_another_call_entrance_before_mutation(
    tmp_path: Path,
) -> None:
    """Following a child from the wrong SubRutter entrance can settle it durably."""

    class CallingRutter(Rutter):
        rutter_id = "mismatched-call-site"
        definition_version = 1
        initial_evolution_id = "first"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "first": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "second": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("mismatched-call-site.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.advance(continue_=False)

    with voyage._store.transaction() as current:
        corrupted = replace(
            current,
            root=replace(
                current.root,
                    entered_evolution=replace(
                        current.root.entered_evolution,
                        evolution_id="second",
                    ),
            ),
        )
        voyage._store.replace(current, corrupted)

    reopened = registry.open(path)
    before = (root / path).read_bytes()

    with pytest.raises(
        RutterStateError,
        match="active explicit SubRutter child does not match the parent entered evolution",
    ):
        reopened.advance(continue_=False)

    persisted = reopened._store.read()
    assert persisted == corrupted
    assert persisted.global_revision == 0
    assert persisted.root.active_child is not None
    assert persisted.root.active_child.run.history == ()
    assert (root / path).read_bytes() == before


def test_call_push_atomically_materializes_a_prompt_child_across_reopen(
    tmp_path: Path,
) -> None:
    """Attaching a LLMStep child without its exact open Turn is an invalid push."""

    class PromptChild(Rutter):
        rutter_id = "prompt-child"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "ask": LLMStep(
                    "Child question.",
                    response_schema=_response_schema("answered"),
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "prompt-calling"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    PromptChild,
                    charter=lambda context: {"parent": context.evolution_id},
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("prompt-child.reckoning.json")
    registry = RutterRegistry({"calling": CallingRutter}, root)
    voyage = registry.create("calling", path, {})

    child_start = voyage.advance(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    child = persisted.root.active_child

    assert child is not None
    assert child_start == reopened.get_status().current_evolution
    assert child_start.rutter_id == "prompt-child"
    assert child_start.evolution_id == "ask"
    assert child_start.depth == 1
    assert child.run.charter == Charter({"parent": "delegate"})
    assert len(child.run.history) == 1
    turn = child.run.history[0]
    assert isinstance(turn, Turn)
    assert turn.response is None
    assert turn.revision == persisted.global_revision == 0
    assert turn.evolution_entry_id == child.run.entered_evolution.entry_id
    assert reopened.get_status().instruction == turn.message


def test_child_return_is_archived_before_the_parent_mapping_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combining return settlement with successor entrance loses its restart seam."""

    class CallingRutter(Rutter):
        rutter_id = "returning-parent"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"site": context.evolution_id},
                    next_on_outcome={"completed": "complete"},
                ),
                "complete": Terminal(VoyageResult("parent-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-return.reckoning.json")
    registry = RutterRegistry({"calling": CallingRutter}, root)
    voyage = registry.create("calling", path, {})
    parent_entry = voyage._store.read().root.entered_evolution.entry_id

    voyage.advance(continue_=False)
    active_call = voyage._store.read().root.active_child
    assert active_call is not None
    child_terminal = voyage.advance(continue_=False)
    assert child_terminal.condition == "terminal"

    reopened = registry.open(path)
    replacements: list[Reckoning] = []
    replace_authority = reopened._store.replace

    def record_replace(previous: Reckoning, replacement: Reckoning) -> None:
        replacements.append(replacement)
        replace_authority(previous, replacement)

    monkeypatch.setattr(reopened._store, "replace", record_replace)

    target = reopened.advance(continue_=False)

    assert len(replacements) == 2
    returned, entered = replacements
    assert isinstance(returned, Reckoning)
    assert isinstance(entered, Reckoning)
    assert returned.root.entered_evolution.entry_id == parent_entry
    assert returned.root.entered_evolution.evolution_id == "delegate"
    assert returned.root.active_child is None
    assert returned.global_revision == 2
    assert len(returned.completed_runs) == 1
    archived = returned.completed_runs[active_call.run.run_id]
    assert isinstance(archived, CompletedRun)
    assert archived.run_id == active_call.run.run_id
    assert archived.result == VoyageResult("completed", {})
    assert len(returned.root.history) == 1
    call_record = returned.root.history[0]
    assert isinstance(call_record, SubRutterRecord)
    assert call_record.invocation_id == active_call.invocation_id
    assert call_record.evolution_entry_id == parent_entry
    assert call_record.origin_evolution_id == "delegate"
    assert call_record.transition_hook_id is None
    assert call_record.attached_to_transition_id is None
    assert call_record.completed_voyage_instance_id == archived.run_id
    assert entered.root.history == returned.root.history
    assert entered.completed_runs == returned.completed_runs
    assert entered.root.entered_evolution.entry_id != parent_entry
    assert target == EvolutionView(
        "returning-parent",
        1,
        "complete",
        entered.root.entered_evolution.entry_id,
        0,
        "ready",
    )


def test_continue_true_recursively_settles_nested_calls_with_one_revision(
    tmp_path: Path,
) -> None:
    """Stopping at an internal child or using frame-local revisions breaks recursion."""

    class MiddleRutter(Rutter):
        rutter_id = "middle"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("middle-complete", {})),
            }

    class RootRutter(Rutter):
        rutter_id = "nested-root"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    MiddleRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("root-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-auto.reckoning.json")
    voyage = RutterRegistry({"root": RootRutter}, root).create("root", path, {})

    terminal = voyage.advance()
    persisted = voyage._store.read()

    assert terminal == voyage.get_status().current_evolution
    assert terminal == EvolutionView(
        "nested-root",
        1,
        "done",
        persisted.root.entered_evolution.entry_id,
        0,
        "terminal",
    )
    assert persisted.root.active_child is None
    assert persisted.global_revision == 5
    assert len(persisted.completed_runs) == 2

    root_call = persisted.root.history[0]
    root_done = persisted.root.history[1]
    assert isinstance(root_call, SubRutterRecord)
    assert isinstance(root_done, TerminalRecord)
    middle = persisted.completed_runs[root_call.completed_voyage_instance_id]
    middle_call = middle.history[0]
    middle_done = middle.history[1]
    assert isinstance(middle_call, SubRutterRecord)
    assert isinstance(middle_done, TerminalRecord)
    grandchild = persisted.completed_runs[middle_call.completed_voyage_instance_id]
    grandchild_done = grandchild.history[0]
    assert isinstance(grandchild_done, TerminalRecord)
    assert grandchild.result == VoyageResult("completed", {})
    assert middle.result == VoyageResult("middle-complete", {})

    entrance_ids = {
        persisted.root.entered_evolution.entry_id,
        root_call.evolution_entry_id,
        middle_call.evolution_entry_id,
        middle_done.evolution_entry_id,
        grandchild_done.evolution_entry_id,
    }
    assert len(entrance_ids) == 5
    assert len(
        {
            persisted.root.run_id,
            middle.run_id,
            grandchild.run_id,
        }
    ) == 3
    assert root_call.invocation_id != middle_call.invocation_id


def test_nested_prompt_self_loop_reopens_with_one_global_revision(
    tmp_path: Path,
) -> None:
    """A frame-local revision or reused LLMStep entrance would admit a stale answer."""

    class PromptLoopChild(Rutter):
        rutter_id = "prompt-loop-child"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "ask": LLMStep(
                    "Again?",
                    response_schema=_response_schema("again", "finish"),
                    next_on_outcome={"again": "ask", "finish": "done"},
                ),
                "done": Terminal(VoyageResult("child-complete", {})),
            }

    class RootRutter(Rutter):
        rutter_id = "prompt-loop-root"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    PromptLoopChild,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="after",
                ),
                "after": LLMStep(
                    "Parent question.",
                    response_schema=_response_schema("done"),
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("root-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-prompt-loop.reckoning.json")
    registry = RutterRegistry({"root": RootRutter}, root)
    voyage = registry.create("root", path, {})
    parent_entry = voyage._store.read().root.entered_evolution.entry_id

    first_child = voyage.advance(continue_=False)
    first_message = voyage.get_status().instruction
    assert isinstance(first_message, Message)
    second_child = voyage.advance(
        {"outcome": "again"},
        responding_to=first_message.evolution_entry_id,
        continue_=False,
    )
    reopened = registry.open(path)
    second_message = reopened.get_status().instruction

    assert isinstance(second_message, Message)
    assert first_child.depth == second_child.depth == 1
    assert first_child.evolution_entry_id != second_child.evolution_entry_id
    assert "revision" not in second_message.data["evolution"]
    stale = reopened.validate(
        {"outcome": "finish"}, responding_to=first_message.evolution_entry_id
    )
    assert stale.valid is False
    assert tuple(issue.code for issue in stale.issues) == ("stale-entrance",)

    child_done = reopened.advance(
        {"outcome": "finish"},
        responding_to=second_message.evolution_entry_id,
        continue_=False,
    )
    assert child_done.evolution_id == "done"
    assert child_done.depth == 1
    assert registry.open(path).advance(continue_=False).condition == "terminal"

    parent_prompt = registry.open(path).advance(continue_=False)
    final = registry.open(path)
    persisted = final._store.read()
    final_message = final.get_status().instruction

    assert parent_prompt.evolution_id == "after"
    assert parent_prompt.depth == 0
    assert isinstance(final_message, Message)
    assert "revision" not in final_message.data["evolution"]
    assert persisted.global_revision == 4
    assert persisted.root.entered_evolution.entry_id != parent_entry
    assert len(persisted.completed_runs) == 1
    archived = next(iter(persisted.completed_runs.values()))
    child_turns = tuple(
        entry for entry in archived.history if isinstance(entry, Turn)
    )
    assert len(child_turns) == 2
    assert child_turns[0].evolution_entry_id != child_turns[1].evolution_entry_id


def test_call_self_loop_allocates_a_fresh_entrance_child_and_call_id(
    tmp_path: Path,
) -> None:
    """Reusing any SubRutter coordinate makes a returned child ambiguous after restart."""

    class CallLoopRutter(Rutter):
        rutter_id = "call-loop"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"entry": context.evolution_entry_id},
                    next_on_outcome={"completed": "delegate"},
                )
            }

    root = tmp_path / "reckonings"
    path = Path("call-self-loop.reckoning.json")
    registry = RutterRegistry({"loop": CallLoopRutter}, root)
    voyage = registry.create("loop", path, {})
    first_parent_entry = voyage._store.read().root.entered_evolution.entry_id

    voyage.advance(continue_=False)
    first_child = voyage._store.read().root.active_child
    assert first_child is not None
    voyage.advance(continue_=False)
    second_parent = registry.open(path).advance(continue_=False)

    assert second_parent.evolution_id == "delegate"
    assert second_parent.evolution_entry_id != first_parent_entry
    assert second_parent.depth == 0

    reopened = registry.open(path)
    second_child_view = reopened.advance(continue_=False)
    persisted = reopened._store.read()
    second_child = persisted.root.active_child
    assert second_child is not None
    first_record = persisted.root.history[0]
    assert isinstance(first_record, SubRutterRecord)
    assert second_child_view.depth == 1
    assert second_child.run.run_id != first_child.run.run_id
    assert second_child.run.entered_evolution.entry_id != first_child.run.entered_evolution.entry_id
    assert second_child.invocation_id != first_record.invocation_id
    assert first_record.evolution_entry_id == first_parent_entry
    assert second_child.site == "delegate"


def test_call_depth_limit_rejects_before_charter_or_id_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking depth after child construction leaks callback work and identifiers."""

    charter_calls: list[None] = []

    def child_charter(context: EvolutionContext) -> Mapping[str, object]:
        del context
        charter_calls.append(None)
        return {}

    class CallingRutter(Rutter):
        rutter_id = "depth-root"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=child_charter,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("depth-limit.reckoning.json")
    voyage = RutterRegistry({"root": CallingRutter}, root).create("root", path, {})
    before = (root / path).read_bytes()
    allocated: list[str] = []
    allocate = engine._new_id

    def record_allocation(prefix: str) -> str:
        allocated.append(prefix)
        return allocate(prefix)

    monkeypatch.setattr(reducer, "_MAX_ACTIVE_DEPTH", 1)
    monkeypatch.setattr(engine, "_new_id", record_allocation)

    with pytest.raises(RutterStateError, match="depth"):
        voyage.advance(continue_=False)

    assert charter_calls == []
    assert allocated == []
    assert voyage._store.read().root.active_child is None
    assert (root / path).read_bytes() == before


def test_call_preview_without_a_returned_result_is_read_only_unavailable(
    tmp_path: Path,
) -> None:
    """A preview that starts the missing child is an advancing operation."""

    charter_calls: list[None] = []

    def child_charter(context: EvolutionContext) -> Mapping[str, object]:
        del context
        charter_calls.append(None)
        return {}

    class CallingRutter(Rutter):
        rutter_id = "preview-call"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=child_charter,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-preview-unavailable.reckoning.json")
    voyage = RutterRegistry({"root": CallingRutter}, root).create("root", path, {})
    before = (root / path).read_bytes()
    current = voyage.get_status().current_evolution

    with pytest.raises(PreviewUnavailable):
        voyage.advance(dry_run=True)

    assert charter_calls == []
    assert voyage.get_status().current_evolution == current
    assert voyage._store.read().root.active_child is None
    assert (root / path).read_bytes() == before


def test_call_preview_uses_a_durable_result_for_callable_routing_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the callable target during preview destroys the return restart seam."""

    routed: list[tuple[EvolutionContext, VoyageResult]] = []

    def route(context: EvolutionContext, result: VoyageResult) -> str:
        routed.append((context, result))
        return "done"

    class CallingRutter(Rutter):
        rutter_id = "callable-preview"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    choose_next=route,
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    class InjectedCrash(RuntimeError):
        pass

    root = tmp_path / "reckonings"
    path = Path("call-preview-result.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.advance(continue_=False)
    voyage.advance(continue_=False)

    returning = registry.open(path)
    replace_authority = returning._store.replace
    replacements = 0

    def crash_before_parent_route(
        previous: Reckoning,
        replacement: Reckoning,
    ) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise InjectedCrash("after return settlement")
        replace_authority(previous, replacement)

    with monkeypatch.context() as patch:
        patch.setattr(returning._store, "replace", crash_before_parent_route)
        with pytest.raises(InjectedCrash, match="return settlement"):
            returning.advance(continue_=False)

    at_call = registry.open(path)
    returned = at_call._store.read()
    assert returned.root.entered_evolution.evolution_id == "delegate"
    assert returned.root.active_child is None
    assert returned.global_revision == 2
    assert isinstance(returned.root.history[-1], SubRutterRecord)
    before = (root / path).read_bytes()
    routed.clear()

    preview = at_call.advance(dry_run=True)

    assert preview == EvolutionView(
        "callable-preview",
        1,
        "done",
        None,
        0,
        "preview",
    )
    assert len(routed) == 1
    context, result = routed[0]
    assert context.evolution_id == "delegate"
    assert context.evolution_entry_id == returned.root.entered_evolution.entry_id
    assert context.history.entries() == ()
    assert result == VoyageResult("completed", {})
    assert at_call.get_status().current_evolution.evolution_id == "delegate"
    assert (root / path).read_bytes() == before

    entered = at_call.advance(continue_=False)
    assert entered.evolution_id == "done"
    assert entered.condition == "ready"
    assert len(routed) == 2
    persisted = at_call._store.read()
    assert isinstance(persisted.root.history[-1], SubRutterRecord)
    assert persisted.completed_runs == returned.completed_runs


def test_call_charter_failure_faults_in_place_without_partial_child(
    tmp_path: Path,
) -> None:
    """Letting a Charter exception escape loses a durable failure coordinate."""

    def fail_charter(context: EvolutionContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private charter detail")

    class CallingRutter(Rutter):
        rutter_id = "charter-failure"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=fail_charter,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-charter-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    source = voyage.get_status().current_evolution

    faulted = voyage.advance(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted == EvolutionView(
        "charter-failure",
        1,
        "delegate",
        source.evolution_entry_id,
        0,
        "fault",
    )
    assert persisted.root.active_child is None
    assert persisted.root.history == ()
    assert persisted.completed_runs == {}
    assert persisted.global_revision == 0
    _assert_fault(
        persisted.fault,
        category="child-charter",
        run_id=persisted.root.run_id,
        evolution_id="delegate",
        evolution_entry_id=source.evolution_entry_id,
    )
    assert b"private charter detail" not in (root / path).read_bytes()
    assert reopened.get_status().current_evolution == faulted
    with pytest.raises(RunBlocked):
        reopened.advance()


def test_prompt_child_materialization_failure_leaves_no_partial_attachment(
    tmp_path: Path,
) -> None:
    """Persisting child IDs without its initial Turn violates atomic push."""

    def fail_data(context: EvolutionContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private child materialization detail")

    class PromptChild(Rutter):
        rutter_id = "failing-prompt-child"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "ask": LLMStep(
                    "Child question.",
                    response_schema=_response_schema("done"),
                    data=fail_data,
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "materialization-failure"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    PromptChild,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("child-materialization-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    source = voyage.get_status().current_evolution

    faulted = voyage.advance(continue_=False)
    persisted = registry.open(path)._store.read()

    assert faulted.condition == "fault"
    assert faulted.evolution_entry_id == source.evolution_entry_id
    assert persisted.root.active_child is None
    assert persisted.root.history == ()
    assert persisted.completed_runs == {}
    _assert_fault(
        persisted.fault,
        category="child-materialization",
        run_id=persisted.root.run_id,
        evolution_id="delegate",
        evolution_entry_id=source.evolution_entry_id,
    )
    assert b"private child materialization detail" not in (root / path).read_bytes()


def test_child_fault_retains_the_complete_active_parent_child_path(
    tmp_path: Path,
) -> None:
    """Detaching a faulted child destroys the recursive failure coordinate."""

    def fail_route(context: LLMResponseContext) -> str:
        del context
        raise RuntimeError("private child routing detail")

    class PromptChild(Rutter):
        rutter_id = "faulting-child"
        definition_version = 1
        initial_evolution_id = "ask"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "ask": LLMStep(
                    "Child question.",
                    response_schema=_response_schema("done"),
                    choose_next=fail_route,
                ),
                "done": Terminal(VoyageResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "fault-path-root"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    PromptChild,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("child-fault-path.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.advance(continue_=False)
    before_fault = voyage._store.read()
    child = before_fault.root.active_child
    assert child is not None
    message = voyage.get_status().instruction
    assert isinstance(message, Message)

    faulted = voyage.advance(
        {"outcome": "done"},
        responding_to=message.evolution_entry_id,
        continue_=False,
    )
    reopened = registry.open(path)
    persisted = reopened._store.read()
    active_child = persisted.root.active_child

    assert active_child is not None
    assert persisted.root.run_id == before_fault.root.run_id
    assert persisted.root.entered_evolution.evolution_id == "delegate"
    assert active_child.invocation_id == child.invocation_id
    assert active_child.run.run_id == child.run.run_id
    assert active_child.run.entered_evolution.evolution_id == "ask"
    accepted = active_child.run.history[-1]
    assert isinstance(accepted, Turn)
    assert accepted.response is not None
    _assert_fault(
        persisted.fault,
        category="routing",
        run_id=active_child.run.run_id,
        evolution_id="ask",
        evolution_entry_id=active_child.run.entered_evolution.entry_id,
    )
    assert faulted == EvolutionView(
        "faulting-child",
        1,
        "ask",
        active_child.run.entered_evolution.entry_id,
        1,
        "fault",
    )
    assert reopened.get_status().current_evolution == faulted
    assert b"private child routing detail" not in (root / path).read_bytes()


def test_returned_child_record_survives_later_parent_routing_failure(
    tmp_path: Path,
) -> None:
    """Rolling return back with a failed route would replay an accepted child."""

    def fail_route(context: EvolutionContext, result: VoyageResult) -> str:
        assert context.evolution_id == "delegate"
        assert result == VoyageResult("completed", {})
        raise RuntimeError("private parent routing detail")

    class CallingRutter(Rutter):
        rutter_id = "post-return-failure"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    choose_next=fail_route,
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("post-return-routing-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.advance(continue_=False)
    child = voyage._store.read().root.active_child
    assert child is not None
    voyage.advance(continue_=False)

    faulted = registry.open(path).advance(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted.condition == "fault"
    assert faulted.rutter_id == "post-return-failure"
    assert faulted.evolution_id == "delegate"
    assert faulted.depth == 0
    assert persisted.root.active_child is None
    assert persisted.global_revision == 2
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, SubRutterRecord)
    assert record.invocation_id == child.invocation_id
    assert record.completed_voyage_instance_id == child.run.run_id
    assert persisted.completed_runs[record.completed_voyage_instance_id].result == VoyageResult(
        "completed", {}
    )
    _assert_fault(
        persisted.fault,
        category="routing",
        run_id=persisted.root.run_id,
        evolution_id="delegate",
        evolution_entry_id=persisted.root.entered_evolution.entry_id,
    )
    assert b"private parent routing detail" not in (root / path).read_bytes()


def test_dry_run_at_nested_terminal_does_not_return_or_route_the_child(
    tmp_path: Path,
) -> None:
    """Settling a child return during dry-run mutates two durable authorities."""

    class CallingRutter(Rutter):
        rutter_id = "nested-terminal-preview"
        definition_version = 1
        initial_evolution_id = "delegate"

        def define_evolutions(self) -> Mapping[str, object]:
            return {
                "delegate": SubRutter(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.evolution_id},
                    next_on_outcome="done",
                ),
                "done": Terminal(VoyageResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-terminal-preview.reckoning.json")
    voyage = RutterRegistry({"root": CallingRutter}, root).create("root", path, {})
    voyage.advance(continue_=False)
    terminal = voyage.advance(continue_=False)
    before = (root / path).read_bytes()

    preview = voyage.advance(dry_run=True)

    assert preview == terminal
    assert preview.condition == "terminal"
    persisted = voyage._store.read()
    assert persisted.root.active_child is not None
    assert persisted.root.history == ()
    assert persisted.completed_runs == {}
    assert (root / path).read_bytes() == before


def test_root_done_settlement_does_not_spend_an_extra_continuation_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rechecking terminality after settlement incorrectly exhausts the budget."""

    voyage = RutterRegistry(
        {"child": DirectChildRutter}, tmp_path / "reckonings"
    ).create("child", Path("one-step-done.reckoning.json"), {})
    monkeypatch.setattr(engine, "_OPERATION_LIMIT", 1)

    terminal = voyage.advance()

    assert terminal.condition == "terminal"
    assert terminal.evolution_id == "complete"
    assert voyage._store.read().global_revision == 1
