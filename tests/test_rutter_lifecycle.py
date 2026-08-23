"""Specify the Prompt/Done lifecycle at the bound-voyage boundary."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Callable, Mapping

import pytest

import officina.rutter.engine as engine
from officina.rutter.model import (
    Action,
    ActionContext,
    ActionRecord,
    ActionResult,
    ActiveChild,
    AnswerContext,
    AnswerSpec,
    Call,
    CallRecord,
    Charter,
    CompletedRun,
    Done,
    DoneRecord,
    Message,
    NodeView,
    NotApplicable,
    PreviewUnavailable,
    Prompt,
    PythonInstruction,
    Reckoning,
    Rutter,
    RunBlocked,
    RutterStateError,
    RutterValidationError,
    RunResult,
    StateContext,
    Turn,
    ValidationIssue,
    ValidationReport,
)
from officina.rutter.runtime import RutterRegistry
from test_support.rutter_fixtures import DirectChildRutter, ExampleRutter


def test_supplied_pure_action_result_is_recorded_before_callable_routing(
    tmp_path: Path,
) -> None:
    """Losing accepted Action work or invoking the author callback must fail."""

    executions: list[ActionContext] = []
    routes: list[tuple[ActionContext, ActionResult]] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context)
        return ActionResult("calculated", {"source": "callback"})

    def route(context: ActionContext, result: ActionResult) -> str:
        routes.append((context, result))
        return "done"

    class PureActionRutter(Rutter):
        rutter_id = "pure-result"
        definition_version = 1
        start_state = "calculate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "calculate": Action(execute, mode="pure", then=route),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("pure-result.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)
    source_entry = voyage.get_current_node().node_entry_id
    supplied = ActionResult("calculated", {"source": "supplied"})

    entered = voyage.next(supplied, continue_=False)
    persisted = registry.open(path)._store.read()

    assert entered.state_id == "done"
    assert entered.condition == "ready"
    assert executions == []
    assert len(routes) == 1
    route_context, route_result = routes[0]
    assert route_context.action_id == instruction.action_id
    assert route_context.state.history.entries() == ()
    assert route_result == supplied
    assert persisted.global_revision == 1
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, ActionRecord)
    assert record.action_id == instruction.action_id
    assert record.node_entry_id == source_entry
    assert record.state_id == "calculate"
    assert record.mode == "pure"
    assert record.result == supplied


def test_omitted_pure_action_result_runs_once_and_continues_to_terminal(
    tmp_path: Path,
) -> None:
    """Requiring a supplied result or rerunning pure work after acceptance must fail."""

    executions: list[ActionContext] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context)
        return ActionResult("calculated", {"entrance": context.state.node_entry_id})

    class PureActionRutter(Rutter):
        rutter_id = "automatic-pure"
        definition_version = 1
        start_state = "calculate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "calculate": Action(execute, mode="pure", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("automatic-pure.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})

    terminal = voyage.next()
    reopened = registry.open(path)

    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert reopened.next() == terminal
    assert len(executions) == 1
    actions = reopened._store.read().root.history
    assert len(actions) == 2
    assert isinstance(actions[0], ActionRecord)
    assert actions[0].result == ActionResult(
        "calculated", {"entrance": executions[0].state.node_entry_id}
    )
    assert isinstance(actions[1], DoneRecord)


def test_repeat_safe_instruction_persists_completed_recovery_before_return(
    tmp_path: Path,
) -> None:
    """Issuing without planned authority or returning before completion must fail."""

    executions: list[ActionContext] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context)
        return ActionResult("stored", {"action_id": context.action_id})

    class RepeatSafeRutter(Rutter):
        rutter_id = "repeat-safe"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(execute, mode="repeat-safe", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("repeat-safe.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    planned = voyage._store.read()
    before = (tmp_path / path).read_bytes()

    first = voyage.get_instruction()
    reopened_instruction = registry.open(path).get_instruction()

    assert isinstance(first, PythonInstruction)
    assert isinstance(reopened_instruction, PythonInstruction)
    assert first.action_id == reopened_instruction.action_id
    assert first.mode == "repeat-safe"
    assert planned.active_effect == {
        "action_id": first.action_id,
        "owner_run_id": planned.root.run_id,
        "node_entry_id": planned.root.entered_node.entry_id,
        "state_id": "store",
        "mode": "repeat-safe",
        "disposition": "planned",
        "result": None,
    }
    assert executions == []
    assert (tmp_path / path).read_bytes() == before

    result = first.run()
    completed = registry.open(path)._store.read()

    assert result == ActionResult("stored", {"action_id": first.action_id})
    assert len(executions) == 1
    assert completed.root.history == ()
    assert completed.active_effect == {
        **planned.active_effect,
        "disposition": "completed",
        "result": result.to_json(),
    }
    assert reopened_instruction.run() == result
    assert len(executions) == 1


def test_effectful_next_consumes_only_the_exact_completed_authority(
    tmp_path: Path,
) -> None:
    """Accepting planned or mismatched effect work must fail without mutation."""

    executions: list[str] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context.action_id)
        return ActionResult("stored", {"sequence": 1})

    class RepeatSafeRutter(Rutter):
        rutter_id = "exact-authority"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(execute, mode="repeat-safe", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("exact-authority.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)
    planned_bytes = (tmp_path / path).read_bytes()

    with pytest.raises(RutterValidationError):
        voyage.next(ActionResult("stored", {"sequence": 1}), continue_=False)
    assert executions == []
    assert (tmp_path / path).read_bytes() == planned_bytes

    completed_result = instruction.run()
    completed_bytes = (tmp_path / path).read_bytes()
    with pytest.raises(RutterValidationError):
        voyage.next(ActionResult("stored", {"sequence": 2}), continue_=False)
    assert executions == [instruction.action_id]
    assert (tmp_path / path).read_bytes() == completed_bytes

    entered = voyage.next(completed_result, continue_=False)
    persisted = registry.open(path)._store.read()

    assert entered.state_id == "done"
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, ActionRecord)
    assert record.action_id == instruction.action_id
    assert record.mode == "repeat-safe"
    assert record.result == completed_result


def test_omitted_repeat_safe_action_runs_and_consumes_the_same_wrapper(
    tmp_path: Path,
) -> None:
    """Bypassing durable completion during automatic continuation must fail."""

    executions: list[str] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context.action_id)
        return ActionResult("stored", {"action_id": context.action_id})

    class RepeatSafeRutter(Rutter):
        rutter_id = "automatic-repeat"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(execute, mode="repeat-safe", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("automatic-repeat.reckoning.json")
    registry = RutterRegistry({"repeat": RepeatSafeRutter}, tmp_path)
    voyage = registry.create("repeat", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)

    terminal = voyage.next()
    persisted = registry.open(path)._store.read()

    assert terminal.condition == "terminal"
    assert executions == [instruction.action_id]
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 2
    action_record = persisted.root.history[0]
    assert isinstance(action_record, ActionRecord)
    assert action_record.action_id == instruction.action_id
    assert action_record.result == ActionResult(
        "stored", {"action_id": instruction.action_id}
    )
    assert isinstance(persisted.root.history[1], DoneRecord)


def test_non_repeat_safe_wrapper_persists_uncertain_before_author_code(
    tmp_path: Path,
) -> None:
    """Calling non-repeat-safe author code while recovery is planned must fail."""

    path = Path("non-repeat-safe.reckoning.json")
    authority_path = tmp_path / path
    observed: list[str] = []

    def execute(context: ActionContext) -> ActionResult:
        del context
        mapping = json.loads(authority_path.read_text(encoding="utf-8"))
        observed.append(mapping["active_effect"]["disposition"])
        return ActionResult("sent", {"receipt": "one"})

    class NonRepeatSafeRutter(Rutter):
        rutter_id = "non-repeat-safe"
        definition_version = 1
        start_state = "send"

        def define_states(self) -> Mapping[str, object]:
            return {
                "send": Action(execute, mode="non-repeat-safe", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    registry = RutterRegistry({"send": NonRepeatSafeRutter}, tmp_path)
    voyage = registry.create("send", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)
    assert instruction.mode == "non-repeat-safe"
    assert voyage._store.read().active_effect["disposition"] == "planned"

    result = instruction.run()
    completed = registry.open(path)._store.read()

    assert observed == ["uncertain"]
    assert result == ActionResult("sent", {"receipt": "one"})
    assert completed.active_effect["disposition"] == "completed"
    assert completed.active_effect["result"] == result.to_json()


def test_non_repeat_safe_crash_windows_preserve_authoritative_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying uncertain work or losing completed work across a crash must fail."""

    class SimulatedCrash(BaseException):
        pass

    def create(
        name: str,
        execute: Callable[[ActionContext], ActionResult],
    ) -> tuple[RutterRegistry, object, Path]:
        class CrashRutter(Rutter):
            rutter_id = name
            definition_version = 1
            start_state = "send"

            def define_states(self) -> Mapping[str, object]:
                return {
                    "send": Action(
                        execute,
                        mode="non-repeat-safe",
                        then="done",
                    ),
                    "done": Done(RunResult("complete", {})),
                }

        path = Path(f"{name}.reckoning.json")
        registry = RutterRegistry({name: CrashRutter}, tmp_path)
        return registry, registry.create(name, path, {}), path

    before_calls: list[str] = []

    def before_callback(context: ActionContext) -> ActionResult:
        before_calls.append(context.action_id)
        return ActionResult("sent", {})

    before_registry, _, before_path = create("crash-before", before_callback)
    before_reopen = before_registry.open(before_path)
    before_effect = before_reopen._store.read().active_effect
    assert before_effect is not None
    assert before_effect["disposition"] == "planned"
    assert before_calls == []

    marker_calls: list[str] = []

    def marker_callback(context: ActionContext) -> ActionResult:
        marker_calls.append(context.action_id)
        return ActionResult("sent", {})

    marker_registry, marker_voyage, marker_path = create(
        "crash-after-marker", marker_callback
    )
    marker_instruction = marker_voyage.get_instruction()
    assert isinstance(marker_instruction, PythonInstruction)
    original_publish = engine._publish

    def crash_after_marker(
        voyage: object,
        previous: Reckoning,
        replacement: Reckoning,
    ) -> Reckoning:
        published = original_publish(voyage, previous, replacement)
        effect = replacement.active_effect
        if effect is not None and effect["disposition"] == "uncertain":
            raise SimulatedCrash
        return published

    monkeypatch.setattr(engine, "_publish", crash_after_marker)
    with pytest.raises(SimulatedCrash):
        marker_instruction.run()
    monkeypatch.setattr(engine, "_publish", original_publish)
    marker_reopen = marker_registry.open(marker_path)
    marker_effect = marker_reopen._store.read().active_effect
    assert marker_effect is not None
    assert marker_effect["disposition"] == "uncertain"
    assert marker_calls == []

    effect_calls: list[str] = []

    def crash_after_effect(context: ActionContext) -> ActionResult:
        effect_calls.append(context.action_id)
        raise SimulatedCrash

    effect_registry, effect_voyage, effect_path = create(
        "crash-after-effect", crash_after_effect
    )
    effect_instruction = effect_voyage.get_instruction()
    assert isinstance(effect_instruction, PythonInstruction)
    with pytest.raises(SimulatedCrash):
        effect_instruction.run()
    effect_reopen = effect_registry.open(effect_path)
    effect_recovery = effect_reopen._store.read().active_effect
    assert effect_recovery is not None
    assert effect_recovery["disposition"] == "uncertain"
    assert effect_calls == [effect_instruction.action_id]
    assert effect_reopen.get_instruction() is None
    assert effect_reopen.get_current_node().condition == "uncertain"
    with pytest.raises(RunBlocked):
        effect_reopen.validate(ActionResult("sent", {}))
    with pytest.raises(RunBlocked):
        effect_reopen.next()

    completed_calls: list[str] = []

    def completed_callback(context: ActionContext) -> ActionResult:
        completed_calls.append(context.action_id)
        return ActionResult("sent", {"receipt": "durable"})

    completed_registry, completed_voyage, completed_path = create(
        "crash-after-completed", completed_callback
    )
    completed_instruction = completed_voyage.get_instruction()
    assert isinstance(completed_instruction, PythonInstruction)

    def crash_after_completed(
        voyage: object,
        previous: Reckoning,
        replacement: Reckoning,
    ) -> Reckoning:
        published = original_publish(voyage, previous, replacement)
        effect = replacement.active_effect
        if effect is not None and effect["disposition"] == "completed":
            raise SimulatedCrash
        return published

    monkeypatch.setattr(engine, "_publish", crash_after_completed)
    with pytest.raises(SimulatedCrash):
        completed_instruction.run()
    monkeypatch.setattr(engine, "_publish", original_publish)
    completed_reopen = completed_registry.open(completed_path)
    completed_effect = completed_reopen._store.read().active_effect
    assert completed_effect is not None
    assert completed_effect["disposition"] == "completed"
    recovered_instruction = completed_reopen.get_instruction()
    assert isinstance(recovered_instruction, PythonInstruction)
    recovered = recovered_instruction.run()
    assert recovered == ActionResult("sent", {"receipt": "durable"})
    assert completed_calls == [completed_instruction.action_id]
    completed_reopen.next(recovered, continue_=False)
    consumed = completed_registry.open(completed_path)._store.read()
    assert consumed.active_effect is None
    assert len(consumed.root.history) == 1
    assert isinstance(consumed.root.history[0], ActionRecord)


@pytest.mark.parametrize("disposition", ("planned", "completed", "uncertain"))
@pytest.mark.parametrize("corruption", ("state", "mode"))
def test_reopen_rejects_recovery_that_differs_from_the_bound_action(
    tmp_path: Path,
    disposition: str,
    corruption: str,
) -> None:
    """Trusting structurally valid recovery over the bound state must fail."""

    class NonRepeatSafeRutter(Rutter):
        rutter_id = "corrupt-mode"
        definition_version = 1
        start_state = "send"

        def define_states(self) -> Mapping[str, object]:
            return {
                "send": Action(
                    lambda context: ActionResult("sent", {}),
                    mode="non-repeat-safe",
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    path = Path(f"corrupt-{corruption}-{disposition}.reckoning.json")
    authority_path = tmp_path / path
    registry = RutterRegistry({"send": NonRepeatSafeRutter}, tmp_path)
    voyage = registry.create("send", path, {})
    if disposition == "completed":
        instruction = voyage.get_instruction()
        assert isinstance(instruction, PythonInstruction)
        instruction.run()
    mapping = json.loads(authority_path.read_text(encoding="utf-8"))
    effect = mapping["active_effect"]
    if corruption == "mode":
        effect["mode"] = "repeat-safe"
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

    reopened = registry.open(path)
    with pytest.raises(RutterStateError, match="does not match the Action"):
        reopened.get_current_node()


def test_every_effectful_target_and_self_loop_entrance_allocates_fresh_recovery(
    tmp_path: Path,
) -> None:
    """Entering or re-entering an effectful Action without fresh authority must fail."""

    def execute(context: ActionContext) -> ActionResult:
        return ActionResult("again", {"action_id": context.action_id})

    class EffectLoopRutter(Rutter):
        rutter_id = "effect-loop"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, object]:
            return {
                "start": Prompt(
                    "Start.",
                    answer=AnswerSpec({"go": {}}),
                    then="store",
                ),
                "store": Action(
                    execute,
                    mode="repeat-safe",
                    then={"again": "store", "done": "done"},
                ),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("effect-loop.reckoning.json")
    registry = RutterRegistry({"loop": EffectLoopRutter}, tmp_path)
    voyage = registry.create("loop", path, {})

    first_entry = voyage.next(
        {"revision": 0, "outcome": "go", "evidence": {}},
        continue_=False,
    )
    first_recovery = registry.open(path)._store.read().active_effect
    first_instruction = registry.open(path).get_instruction()

    assert first_entry.state_id == "store"
    assert first_recovery is not None
    assert isinstance(first_instruction, PythonInstruction)
    assert first_recovery["node_entry_id"] == first_entry.node_entry_id
    assert first_recovery["action_id"] == first_instruction.action_id
    assert first_recovery["disposition"] == "planned"

    result = first_instruction.run()
    second_entry = voyage.next(result, continue_=False)
    second_recovery = registry.open(path)._store.read().active_effect
    second_instruction = registry.open(path).get_instruction()

    assert second_entry.state_id == "store"
    assert second_entry.node_entry_id != first_entry.node_entry_id
    assert second_recovery is not None
    assert isinstance(second_instruction, PythonInstruction)
    assert second_recovery["node_entry_id"] == second_entry.node_entry_id
    assert second_recovery["action_id"] == second_instruction.action_id
    assert second_recovery["disposition"] == "planned"
    assert second_instruction.action_id != first_instruction.action_id
    records = registry.open(path)._store.read().root.history
    assert len(records) == 2
    assert isinstance(records[0], Turn)
    assert isinstance(records[1], ActionRecord)
    assert records[1].action_id == first_instruction.action_id


def test_nested_action_recovery_is_owned_by_the_deepest_leaf_across_reopen(
    tmp_path: Path,
) -> None:
    """Giving a parent or missing child the sole effect slot must fail."""

    executions: list[ActionContext] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context)
        return ActionResult("worked", {"depth": 1})

    class ActionChild(Rutter):
        rutter_id = "action-child"
        definition_version = 1
        start_state = "work"

        def define_states(self) -> Mapping[str, object]:
            return {
                "work": Action(execute, mode="repeat-safe", then="done"),
                "done": Done(RunResult("child-complete", {})),
            }

    class ActionParent(Rutter):
        rutter_id = "action-parent"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    ActionChild,
                    charter=lambda context: {},
                    then="done",
                ),
                "done": Done(RunResult("parent-complete", {})),
            }

    path = Path("nested-action.reckoning.json")
    registry = RutterRegistry({"parent": ActionParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    child_view = voyage.next(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    instruction = reopened.get_instruction()

    assert child_view.state_id == "work"
    assert child_view.depth == 1
    assert isinstance(instruction, PythonInstruction)
    assert persisted.root.active_child is not None
    child_run = persisted.root.active_child.run
    assert persisted.active_effect is not None
    assert persisted.active_effect["owner_run_id"] == child_run.run_id
    assert persisted.active_effect["node_entry_id"] == child_run.entered_node.entry_id
    assert persisted.active_effect["action_id"] == instruction.action_id

    result = instruction.run()
    terminal = reopened.next(result)
    settled = registry.open(path)._store.read()

    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert executions[0].action_id == instruction.action_id
    assert settled.active_effect is None
    assert settled.root.active_child is None
    assert len(settled.completed_runs) == 1
    completed_child = next(iter(settled.completed_runs.values()))
    assert isinstance(completed_child.history[0], ActionRecord)
    assert completed_child.history[0].action_id == instruction.action_id


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

    def execute(context: ActionContext) -> ActionResult:
        del context
        raise RuntimeError("private credential detail")

    class FailingActionRutter(Rutter):
        rutter_id = f"failing-{mode}"
        definition_version = 1
        start_state = "work"

        def define_states(self) -> Mapping[str, object]:
            return {
                "work": Action(execute, mode=mode, then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path(f"failing-{mode}.reckoning.json")
    registry = RutterRegistry({"failure": FailingActionRutter}, tmp_path)
    voyage = registry.create("failure", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)

    with pytest.raises(RunBlocked, match="Action execution failed") as caught:
        instruction.run()

    assert "private credential detail" not in str(caught.value)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    assert persisted.fault == {
        "category": "action-execution",
        "run_id": persisted.root.run_id,
        "state_id": "work",
        "node_entry_id": persisted.root.entered_node.entry_id,
    }
    assert "private credential detail" not in json.dumps(dict(persisted.fault))
    assert persisted.active_effect is not None
    assert persisted.active_effect["disposition"] == disposition
    assert reopened.get_current_node().condition == "fault"
    assert reopened.get_instruction() is None
    with pytest.raises(RunBlocked):
        reopened.next()


def test_action_dry_run_uses_only_supplied_or_completed_authority_without_writes(
    tmp_path: Path,
) -> None:
    """Previewing by invoking, accepting, or consuming Action work must fail."""

    pure_calls: list[str] = []

    class PurePreviewRutter(Rutter):
        rutter_id = "pure-preview"
        definition_version = 1
        start_state = "calculate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "calculate": Action(
                    lambda context: (
                        pure_calls.append(context.action_id)
                        or ActionResult("ready", {})
                    ),
                    mode="pure",
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    pure_path = Path("pure-preview.reckoning.json")
    pure = RutterRegistry({"pure": PurePreviewRutter}, tmp_path).create(
        "pure", pure_path, {}
    )
    pure_before = (tmp_path / pure_path).read_bytes()

    pure_preview = pure.next(
        ActionResult("ready", {}),
        dry_run=True,
    )

    assert pure_preview.state_id == "done"
    assert pure_preview.node_entry_id is None
    assert pure_preview.condition == "preview"
    assert pure_calls == []
    assert (tmp_path / pure_path).read_bytes() == pure_before
    with pytest.raises(PreviewUnavailable):
        pure.next(dry_run=True)
    assert (tmp_path / pure_path).read_bytes() == pure_before

    effect_calls: list[str] = []

    class EffectPreviewRutter(Rutter):
        rutter_id = "effect-preview"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(
                    lambda context: (
                        effect_calls.append(context.action_id)
                        or ActionResult("stored", {})
                    ),
                    mode="repeat-safe",
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    effect_path = Path("effect-preview.reckoning.json")
    effect = RutterRegistry({"effect": EffectPreviewRutter}, tmp_path).create(
        "effect", effect_path, {}
    )
    planned_before = (tmp_path / effect_path).read_bytes()
    with pytest.raises(PreviewUnavailable):
        effect.next(ActionResult("stored", {}), dry_run=True)
    assert effect_calls == []
    assert (tmp_path / effect_path).read_bytes() == planned_before

    instruction = effect.get_instruction()
    assert isinstance(instruction, PythonInstruction)
    completed_result = instruction.run()
    completed_before = (tmp_path / effect_path).read_bytes()

    completed_preview = effect.next(dry_run=True)

    assert completed_preview.state_id == "done"
    assert completed_preview.node_entry_id is None
    assert completed_preview.condition == "preview"
    assert effect_calls == [instruction.action_id]
    assert (tmp_path / effect_path).read_bytes() == completed_before
    assert effect._store.read().active_effect["disposition"] == "completed"
    assert effect._store.read().root.history == ()
    assert completed_result == ActionResult("stored", {})


@pytest.mark.parametrize("mode", ("pure", "repeat-safe", "non-repeat-safe"))
def test_continue_true_executes_action_entered_from_prompt(
    tmp_path: Path,
    mode: str,
) -> None:
    """Stopping at an automatically executable PythonInstruction must fail."""

    executions: list[ActionContext] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context)
        return ActionResult("worked", {"mode": mode})

    class PromptActionRutter(Rutter):
        rutter_id = f"prompt-action-{mode}"
        definition_version = 1
        start_state = "start"

        def define_states(self) -> Mapping[str, object]:
            return {
                "start": Prompt(
                    "Start.",
                    answer=AnswerSpec({"go": {}}),
                    then="work",
                ),
                "work": Action(execute, mode=mode, then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path(f"prompt-action-{mode}.reckoning.json")
    registry = RutterRegistry({"flow": PromptActionRutter}, tmp_path)
    voyage = registry.create("flow", path, {})

    terminal = voyage.next(
        {"revision": 0, "outcome": "go", "evidence": {}},
    )
    persisted = registry.open(path)._store.read()

    assert terminal.state_id == "done"
    assert terminal.condition == "terminal"
    assert len(executions) == 1
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 3
    assert isinstance(persisted.root.history[0], Turn)
    action_record = persisted.root.history[1]
    assert isinstance(action_record, ActionRecord)
    assert action_record.mode == mode
    assert action_record.action_id == executions[0].action_id
    assert isinstance(persisted.root.history[2], DoneRecord)


@pytest.mark.parametrize(
    ("failure", "category"),
    (("routing", "routing"), ("target", "target-materialization")),
)
def test_accepted_action_record_survives_later_callback_fault_without_replay(
    tmp_path: Path,
    failure: str,
    category: str,
) -> None:
    """Rolling back or requesting already accepted Action work must fail."""

    executions: list[str] = []

    def execute(context: ActionContext) -> ActionResult:
        executions.append(context.action_id)
        return ActionResult("stored", {"receipt": "one"})

    def route(context: ActionContext, result: ActionResult) -> str:
        del context, result
        if failure == "routing":
            raise RuntimeError("private routing detail")
        return "broken"

    def materialize(context: StateContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private target detail")

    class AcceptedActionRutter(Rutter):
        rutter_id = f"accepted-action-{failure}"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(execute, mode="repeat-safe", then=route),
                "broken": Prompt(
                    "Broken.",
                    answer=AnswerSpec({"ok": {}}),
                    data=materialize,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    path = Path(f"accepted-action-{failure}.reckoning.json")
    registry = RutterRegistry({"accepted": AcceptedActionRutter}, tmp_path)
    voyage = registry.create("accepted", path, {})
    instruction = voyage.get_instruction()
    assert isinstance(instruction, PythonInstruction)
    result = instruction.run()

    faulted = voyage.next(result)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted.state_id == "store"
    assert faulted.condition == "fault"
    assert executions == [instruction.action_id]
    assert persisted.active_effect is None
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, ActionRecord)
    assert record.action_id == instruction.action_id
    assert record.result == result
    assert persisted.fault["category"] == category
    assert persisted.fault["state_id"] == "store"
    assert persisted.fault["node_entry_id"] == record.node_entry_id
    if failure == "target":
        assert persisted.fault["target_state_id"] == "broken"
    assert "private" not in json.dumps(dict(persisted.fault))
    assert reopened.get_instruction() is None
    with pytest.raises(RunBlocked):
        reopened.next(result)
    assert executions == [instruction.action_id]


def test_repeat_safe_reopen_retries_planned_work_with_the_same_action_id(
    tmp_path: Path,
) -> None:
    """Allocating a new id or blocking a repeat-safe planned retry must fail."""

    class SimulatedCrash(BaseException):
        pass

    attempts: list[str] = []

    def execute(context: ActionContext) -> ActionResult:
        attempts.append(context.action_id)
        if len(attempts) == 1:
            raise SimulatedCrash
        return ActionResult("stored", {"attempt": len(attempts)})

    class RetryRutter(Rutter):
        rutter_id = "repeat-retry"
        definition_version = 1
        start_state = "store"

        def define_states(self) -> Mapping[str, object]:
            return {
                "store": Action(execute, mode="repeat-safe", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("repeat-retry.reckoning.json")
    registry = RutterRegistry({"retry": RetryRutter}, tmp_path)
    voyage = registry.create("retry", path, {})
    first = voyage.get_instruction()
    assert isinstance(first, PythonInstruction)

    with pytest.raises(SimulatedCrash):
        first.run()

    reopened = registry.open(path)
    planned = reopened._store.read().active_effect
    second = reopened.get_instruction()
    assert planned is not None
    assert planned["disposition"] == "planned"
    assert isinstance(second, PythonInstruction)
    assert second.action_id == first.action_id

    result = second.run()

    assert result == ActionResult("stored", {"attempt": 2})
    assert attempts == [first.action_id, first.action_id]
    completed = registry.open(path)._store.read().active_effect
    assert completed is not None
    assert completed["disposition"] == "completed"
    assert completed["action_id"] == first.action_id


def test_create_atomically_enters_prompt_with_its_exact_open_turn(
    tmp_path: Path,
) -> None:
    """Removing Prompt materialization from creation must leave no partial entrance."""

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
    assert turn.node_entry_id == persisted.root.entered_node.entry_id
    assert turn.state_id == persisted.root.entered_node.state_id
    assert turn.revision == persisted.global_revision
    assert isinstance(turn.message, Message)
    assert turn.message.data["state"] == {
        "id": "report",
        "entry_id": persisted.root.entered_node.entry_id,
        "revision": 0,
    }
    assert turn.message.instructions == {
        "text": "Report.",
        "answer": {"reported": {}},
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

    first = voyage.get_instruction()
    second = voyage.get_instruction()
    current = voyage.get_current_node()
    reopened = registry.open(path)

    assert isinstance(first, Message)
    assert second == first
    assert reopened.get_instruction() == first
    assert current == NodeView(
        "example",
        1,
        "report",
        voyage._store.read().root.entered_node.entry_id,
        0,
        "ready",
    )
    assert (root / path).read_bytes() == before


@pytest.mark.parametrize(
    ("response", "code"),
    (
        ({"revision": 0, "outcome": "reported"}, "invalid-envelope"),
        (
            {
                "revision": 0,
                "outcome": "reported",
                "evidence": {},
                "extra": None,
            },
            "invalid-envelope",
        ),
        (
            {"revision": 1, "outcome": "reported", "evidence": {}},
            "stale-revision",
        ),
        (
            {"revision": 0, "outcome": "unknown", "evidence": {}},
            "unknown-outcome",
        ),
        (
            {"revision": 0, "outcome": "reported", "evidence": {"n": float("nan")}},
            "nonfinite-evidence",
        ),
    ),
)
def test_invalid_prompt_response_is_reported_and_next_preserves_exact_bytes(
    tmp_path: Path,
    response: object,
    code: str,
) -> None:
    """Weakening any envelope gate must not let invalid work mutate authority."""

    root = tmp_path / "reckonings"
    path = Path("invalid.reckoning.json")
    voyage = RutterRegistry({"example": ExampleRutter}, root).create(
        "example", path, {}
    )
    before = (root / path).read_bytes()
    current = voyage.get_current_node()

    report = voyage.validate(response)

    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == (code,)
    assert voyage.get_current_node() == current
    assert (root / path).read_bytes() == before
    with pytest.raises(RutterValidationError):
        voyage.next(response)
    assert voyage.get_current_node() == current
    assert (root / path).read_bytes() == before


def test_contextual_prompt_validation_receives_frozen_current_context(
    tmp_path: Path,
) -> None:
    """Bypassing the authored validator must admit evidence it explicitly rejects."""

    seen: list[object] = []

    def reject(context: AnswerContext) -> ValidationReport:
        seen.append(context)
        return ValidationReport(
            False,
            (
                ValidationIssue(
                    ("evidence", "approved"),
                    "not-approved",
                    "approval evidence is required",
                ),
            ),
        )

    class ContextualRutter(Rutter):
        rutter_id = "contextual"
        definition_version = 1
        start_state = "review"

        def define_states(self) -> Mapping[str, object]:
            return {
                "review": Prompt(
                    "Review.",
                    answer=AnswerSpec({"accepted": {}}),
                    validate=reject,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    voyage = RutterRegistry({"contextual": ContextualRutter}, root).create(
        "contextual", Path("contextual.reckoning.json"), {}
    )
    before = (root / "contextual.reckoning.json").read_bytes()

    report = voyage.validate(
        {"revision": 0, "outcome": "accepted", "evidence": {"approved": False}}
    )

    assert report == ValidationReport(
        False,
        (
            ValidationIssue(
                ("evidence", "approved"),
                "not-approved",
                "approval evidence is required",
            ),
        ),
    )
    assert len(seen) == 1
    context = seen[0]
    assert context.state.history.entries() == ()
    assert context.message == voyage.get_instruction()
    assert context.response.outcome == "accepted"
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

    entered = voyage.next(
        {"revision": 0, "outcome": "reported", "evidence": {"note": "ok"}},
        continue_=False,
    )
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert entered == NodeView(
        "example",
        1,
        "complete",
        persisted.root.entered_node.entry_id,
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
    assert accepted.response.to_json() == {
        "revision": 0,
        "outcome": "reported",
        "evidence": {"note": "ok"},
    }
    assert reopened.get_instruction() is None


def test_prompt_self_loop_allocates_a_new_entrance_and_rerenders_from_history(
    tmp_path: Path,
) -> None:
    """Reusing the source entrance or stored Message across re-entry is a bug."""

    def payload(context: object) -> Mapping[str, object]:
        return {"accepted": len(context.history.turns())}

    class SelfLoopRutter(Rutter):
        rutter_id = "self-loop"
        definition_version = 1
        start_state = "ask"

        def define_states(self) -> Mapping[str, object]:
            return {
                "ask": Prompt(
                    "Again?",
                    answer=AnswerSpec({"again": {}}),
                    data=payload,
                    then="ask",
                )
            }

    root = tmp_path / "reckonings"
    path = Path("self-loop.reckoning.json")
    voyage = RutterRegistry({"loop": SelfLoopRutter}, root).create(
        "loop", path, {}
    )
    first_message = voyage.get_instruction()
    first_entry = voyage._store.read().root.entered_node.entry_id

    second_node = voyage.next(
        {"revision": 0, "outcome": "again", "evidence": {}},
        continue_=False,
    )
    second_message = voyage.get_instruction()

    assert second_node.state_id == "ask"
    assert second_node.node_entry_id != first_entry
    assert second_message != first_message
    assert second_message.data["payload"] == {"accepted": 1}
    assert second_message.data["state"]["revision"] == 1
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
        start_state = "source"

        def define_states(self) -> Mapping[str, object]:
            return {
                "source": Prompt(
                    "Source.",
                    answer=AnswerSpec({"go": {}}),
                    then="target",
                ),
                "target": Prompt(
                    "Target.",
                    answer=AnswerSpec({"stop": {}}),
                    data=fail_data,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("render-failure.reckoning.json")
    registry = RutterRegistry({"failure": RenderFailureRutter}, root)
    voyage = registry.create("failure", path, {})
    source_entry = voyage._store.read().root.entered_node.entry_id

    faulted = voyage.next(
        {"revision": 0, "outcome": "go", "evidence": {}},
        continue_=False,
    )
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    assert faulted.state_id == "source"
    assert faulted.node_entry_id == source_entry
    persisted = reopened._store.read()
    assert persisted.root.history[0].response is not None
    assert persisted.root.entered_node.entry_id == source_entry
    assert persisted.fault == {
        "category": "target-materialization",
        "run_id": persisted.root.run_id,
        "state_id": "source",
        "node_entry_id": source_entry,
        "target_state_id": "target",
    }
    assert b"private target detail" not in (root / path).read_bytes()
    assert reopened.get_instruction() is None
    with pytest.raises(RunBlocked):
        reopened.validate({"revision": 0, "outcome": "go", "evidence": {}})
    with pytest.raises(RunBlocked):
        reopened.next()


def test_prompt_routing_failure_preserves_the_accepted_turn_before_fault(
    tmp_path: Path,
) -> None:
    """Combining acceptance and routing must not roll back a valid Response."""

    def fail_route(context: AnswerContext) -> str:
        del context
        raise RuntimeError("private routing detail")

    class RoutingFailureRutter(Rutter):
        rutter_id = "routing-failure"
        definition_version = 1
        start_state = "source"

        def define_states(self) -> Mapping[str, object]:
            return {
                "source": Prompt(
                    "Source.",
                    answer=AnswerSpec({"go": {}}),
                    then=fail_route,
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("routing-failure.reckoning.json")
    registry = RutterRegistry({"failure": RoutingFailureRutter}, root)
    voyage = registry.create("failure", path, {})
    source_entry = voyage._store.read().root.entered_node.entry_id

    faulted = voyage.next(
        {"revision": 0, "outcome": "go", "evidence": {}},
        continue_=False,
    )
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    persisted = reopened._store.read()
    assert persisted.root.entered_node.entry_id == source_entry
    assert persisted.root.history[0].response is not None
    assert persisted.fault["category"] == "routing"
    assert b"private routing detail" not in (root / path).read_bytes()


def test_continue_true_settles_done_once_and_terminal_next_is_idempotent(
    tmp_path: Path,
) -> None:
    """Duplicating the Done authority or advancing terminal state must fail."""

    root = tmp_path / "reckonings"
    path = Path("terminal.reckoning.json")
    registry = RutterRegistry({"example": ExampleRutter}, root)
    voyage = registry.create("example", path, {})

    terminal = voyage.next(
        {"revision": 0, "outcome": "reported", "evidence": {}},
        continue_=True,
    )
    before = (root / path).read_bytes()
    again = voyage.next()
    dry_again = voyage.next(dry_run=True)
    reopened = registry.open(path)

    assert terminal.condition == "terminal"
    assert terminal.state_id == "complete"
    assert again == terminal
    assert dry_again == terminal
    assert reopened.get_current_node() == terminal
    assert reopened.get_instruction() is None
    with pytest.raises(NotApplicable):
        reopened.validate({})
    persisted = reopened._store.read()
    assert persisted.root.history[-1].result == RunResult("completed", {})
    assert sum(
        1
        for entry in persisted.root.history
        if isinstance(entry, DoneRecord)
    ) == 1
    assert (root / path).read_bytes() == before


def test_prompt_and_done_dry_runs_preview_without_entering_or_writing(
    tmp_path: Path,
) -> None:
    """Persisting either preview or rendering its target Prompt is a regression."""

    target_calls: list[None] = []

    def target_data(context: StateContext) -> Mapping[str, object]:
        del context
        target_calls.append(None)
        return {"rendered": True}

    class PreviewRutter(Rutter):
        rutter_id = "preview"
        definition_version = 1
        start_state = "source"

        def define_states(self) -> Mapping[str, object]:
            return {
                "source": Prompt(
                    "Source.",
                    answer=AnswerSpec({"go": {}}),
                    then="target",
                ),
                "target": Prompt(
                    "Target.",
                    answer=AnswerSpec({"finish": {}}),
                    data=target_data,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("preview.reckoning.json")
    voyage = RutterRegistry({"preview": PreviewRutter}, root).create(
        "preview", path, {}
    )
    before = (root / path).read_bytes()

    preview = voyage.next(
        {"revision": 0, "outcome": "go", "evidence": {}},
        continue_=True,
        dry_run=True,
    )

    assert preview == NodeView("preview", 1, "target", None, 0, "preview")
    assert target_calls == []
    assert voyage._store.read().root.history[0].response is None
    assert (root / path).read_bytes() == before

    done_path = Path("done-preview.reckoning.json")
    done = RutterRegistry({"child": DirectChildRutter}, root).create(
        "child", done_path, {}
    )
    done_before = (root / done_path).read_bytes()
    done_preview = done.next(dry_run=True)

    assert done_preview == NodeView("direct-child", 1, "complete", None, 0, "preview")
    assert done._store.read().root.history == ()
    assert (root / done_path).read_bytes() == done_before


def test_done_projection_failure_faults_without_a_done_record(
    tmp_path: Path,
) -> None:
    """A failed projection must not fabricate completion authority."""

    def fail_result(context: StateContext) -> RunResult:
        del context
        raise RuntimeError("private result detail")

    class FailingDoneRutter(Rutter):
        rutter_id = "failing-done"
        definition_version = 1
        start_state = "done"

        def define_states(self) -> Mapping[str, object]:
            return {"done": Done(fail_result)}

    root = tmp_path / "reckonings"
    path = Path("failing-done.reckoning.json")
    registry = RutterRegistry({"done": FailingDoneRutter}, root)
    voyage = registry.create("done", path, {})

    faulted = voyage.next()
    reopened = registry.open(path)

    assert faulted.condition == "fault"
    persisted = reopened._store.read()
    assert persisted.fault["category"] == "done-projection"
    assert persisted.root.history == ()
    assert b"private result detail" not in (root / path).read_bytes()


def test_call_push_keeps_parent_entered_and_exposes_the_child_leaf(
    tmp_path: Path,
) -> None:
    """Failing to attach one sealed child must leave the parent falsely visible."""

    charter_contexts: list[StateContext] = []

    def child_charter(context: StateContext) -> Mapping[str, object]:
        charter_contexts.append(context)
        return {"scope": context.charter.data["scope"]}

    class CallingRutter(Rutter):
        rutter_id = "calling"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=child_charter,
                    then="complete",
                ),
                "complete": Done(RunResult("completed", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-push.reckoning.json")
    voyage = RutterRegistry({"calling": CallingRutter}, root).create(
        "calling", path, {"scope": "child"}
    )
    before = (root / path).read_bytes()
    parent_entry = voyage._store.read().root.entered_node.entry_id

    assert voyage.get_instruction() is None
    with pytest.raises(NotApplicable):
        voyage.validate({})
    assert (root / path).read_bytes() == before

    child_start = voyage.next(continue_=False)
    persisted = voyage._store.read()
    child = persisted.root.active_child

    assert isinstance(child, ActiveChild)
    assert child_start == NodeView(
        "direct-child",
        1,
        "complete",
        child.run.entered_node.entry_id,
        1,
        "ready",
    )
    assert voyage.get_current_node() == child_start
    assert persisted.root.entered_node.state_id == "delegate"
    assert persisted.root.entered_node.entry_id == parent_entry
    assert persisted.root.history == ()
    assert persisted.global_revision == 0
    assert persisted.completed_runs == {}
    assert child.kind == "explicit_call"
    assert child.site == "delegate"
    assert child.attached_to_edge_id is None
    assert child.run.charter == Charter({"scope": "child"})
    assert len(charter_contexts) == 1
    assert charter_contexts[0].state_id == "delegate"
    assert charter_contexts[0].node_entry_id == parent_entry
    assert charter_contexts[0].history.entries() == ()


def test_active_leaf_rejects_child_from_another_call_entrance_before_mutation(
    tmp_path: Path,
) -> None:
    """Following a child from the wrong Call entrance can settle it durably."""

    class CallingRutter(Rutter):
        rutter_id = "mismatched-call-site"
        definition_version = 1
        start_state = "first"

        def define_states(self) -> Mapping[str, object]:
            return {
                "first": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "second": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("mismatched-call-site.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.next(continue_=False)

    with voyage._store.transaction() as current:
        corrupted = replace(
            current,
            root=replace(
                current.root,
                entered_node=replace(current.root.entered_node, state_id="second"),
            ),
        )
        voyage._store.replace(current, corrupted)

    reopened = registry.open(path)
    before = (root / path).read_bytes()

    with pytest.raises(
        RutterStateError,
        match="active explicit Call child does not match the parent entered state",
    ):
        reopened.next(continue_=False)

    persisted = reopened._store.read()
    assert persisted == corrupted
    assert persisted.global_revision == 0
    assert persisted.root.active_child is not None
    assert persisted.root.active_child.run.history == ()
    assert (root / path).read_bytes() == before


def test_call_push_atomically_materializes_a_prompt_child_across_reopen(
    tmp_path: Path,
) -> None:
    """Attaching a Prompt child without its exact open Turn is an invalid push."""

    class PromptChild(Rutter):
        rutter_id = "prompt-child"
        definition_version = 1
        start_state = "ask"

        def define_states(self) -> Mapping[str, object]:
            return {
                "ask": Prompt(
                    "Child question.",
                    answer=AnswerSpec({"answered": {}}),
                    then="done",
                ),
                "done": Done(RunResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "prompt-calling"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    PromptChild,
                    charter=lambda context: {"parent": context.state_id},
                    then="complete",
                ),
                "complete": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("prompt-child.reckoning.json")
    registry = RutterRegistry({"calling": CallingRutter}, root)
    voyage = registry.create("calling", path, {})

    child_start = voyage.next(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()
    child = persisted.root.active_child

    assert child is not None
    assert child_start == reopened.get_current_node()
    assert child_start.rutter_id == "prompt-child"
    assert child_start.state_id == "ask"
    assert child_start.depth == 1
    assert child.run.charter == Charter({"parent": "delegate"})
    assert len(child.run.history) == 1
    turn = child.run.history[0]
    assert isinstance(turn, Turn)
    assert turn.response is None
    assert turn.revision == persisted.global_revision == 0
    assert turn.node_entry_id == child.run.entered_node.entry_id
    assert reopened.get_instruction() == turn.message


def test_child_return_is_archived_before_the_parent_mapping_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combining return settlement with successor entrance loses its restart seam."""

    class CallingRutter(Rutter):
        rutter_id = "returning-parent"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"site": context.state_id},
                    then={"completed": "complete"},
                ),
                "complete": Done(RunResult("parent-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-return.reckoning.json")
    registry = RutterRegistry({"calling": CallingRutter}, root)
    voyage = registry.create("calling", path, {})
    parent_entry = voyage._store.read().root.entered_node.entry_id

    voyage.next(continue_=False)
    active_call = voyage._store.read().root.active_child
    assert active_call is not None
    child_terminal = voyage.next(continue_=False)
    assert child_terminal.condition == "terminal"

    reopened = registry.open(path)
    replacements: list[Reckoning] = []
    replace_authority = reopened._store.replace

    def record_replace(previous: Reckoning, replacement: Reckoning) -> None:
        replacements.append(replacement)
        replace_authority(previous, replacement)

    monkeypatch.setattr(reopened._store, "replace", record_replace)

    target = reopened.next(continue_=False)

    assert len(replacements) == 2
    returned, entered = replacements
    assert isinstance(returned, Reckoning)
    assert isinstance(entered, Reckoning)
    assert returned.root.entered_node.entry_id == parent_entry
    assert returned.root.entered_node.state_id == "delegate"
    assert returned.root.active_child is None
    assert returned.global_revision == 2
    assert len(returned.completed_runs) == 1
    archived = returned.completed_runs[active_call.run.run_id]
    assert isinstance(archived, CompletedRun)
    assert archived.run_id == active_call.run.run_id
    assert archived.result == RunResult("completed", {})
    assert len(returned.root.history) == 1
    call_record = returned.root.history[0]
    assert isinstance(call_record, CallRecord)
    assert call_record.call_id == active_call.call_id
    assert call_record.node_entry_id == parent_entry
    assert call_record.site_kind == "explicit_call"
    assert call_record.site_id == "delegate"
    assert call_record.attached_to_edge_id is None
    assert call_record.completed_run_id == archived.run_id
    assert entered.root.history == returned.root.history
    assert entered.completed_runs == returned.completed_runs
    assert entered.root.entered_node.entry_id != parent_entry
    assert target == NodeView(
        "returning-parent",
        1,
        "complete",
        entered.root.entered_node.entry_id,
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
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("middle-complete", {})),
            }

    class RootRutter(Rutter):
        rutter_id = "nested-root"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    MiddleRutter,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("root-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-auto.reckoning.json")
    voyage = RutterRegistry({"root": RootRutter}, root).create("root", path, {})

    terminal = voyage.next()
    persisted = voyage._store.read()

    assert terminal == voyage.get_current_node()
    assert terminal == NodeView(
        "nested-root",
        1,
        "done",
        persisted.root.entered_node.entry_id,
        0,
        "terminal",
    )
    assert persisted.root.active_child is None
    assert persisted.global_revision == 5
    assert len(persisted.completed_runs) == 2

    root_call = persisted.root.history[0]
    root_done = persisted.root.history[1]
    assert isinstance(root_call, CallRecord)
    assert isinstance(root_done, DoneRecord)
    middle = persisted.completed_runs[root_call.completed_run_id]
    middle_call = middle.history[0]
    middle_done = middle.history[1]
    assert isinstance(middle_call, CallRecord)
    assert isinstance(middle_done, DoneRecord)
    grandchild = persisted.completed_runs[middle_call.completed_run_id]
    grandchild_done = grandchild.history[0]
    assert isinstance(grandchild_done, DoneRecord)
    assert grandchild.result == RunResult("completed", {})
    assert middle.result == RunResult("middle-complete", {})

    entrance_ids = {
        persisted.root.entered_node.entry_id,
        root_call.node_entry_id,
        middle_call.node_entry_id,
        middle_done.node_entry_id,
        grandchild_done.node_entry_id,
    }
    assert len(entrance_ids) == 5
    assert len(
        {
            persisted.root.run_id,
            middle.run_id,
            grandchild.run_id,
        }
    ) == 3
    assert root_call.call_id != middle_call.call_id


def test_nested_prompt_self_loop_reopens_with_one_global_revision(
    tmp_path: Path,
) -> None:
    """A frame-local revision or reused Prompt entrance would admit a stale answer."""

    class PromptLoopChild(Rutter):
        rutter_id = "prompt-loop-child"
        definition_version = 1
        start_state = "ask"

        def define_states(self) -> Mapping[str, object]:
            return {
                "ask": Prompt(
                    "Again?",
                    answer=AnswerSpec({"again": {}, "finish": {}}),
                    then={"again": "ask", "finish": "done"},
                ),
                "done": Done(RunResult("child-complete", {})),
            }

    class RootRutter(Rutter):
        rutter_id = "prompt-loop-root"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    PromptLoopChild,
                    charter=lambda context: {"from": context.state_id},
                    then="after",
                ),
                "after": Prompt(
                    "Parent question.",
                    answer=AnswerSpec({"done": {}}),
                    then="done",
                ),
                "done": Done(RunResult("root-complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-prompt-loop.reckoning.json")
    registry = RutterRegistry({"root": RootRutter}, root)
    voyage = registry.create("root", path, {})
    parent_entry = voyage._store.read().root.entered_node.entry_id

    first_child = voyage.next(continue_=False)
    first_message = voyage.get_instruction()
    assert isinstance(first_message, Message)
    second_child = voyage.next(
        {"revision": 0, "outcome": "again", "evidence": {}},
        continue_=False,
    )
    reopened = registry.open(path)
    second_message = reopened.get_instruction()

    assert isinstance(second_message, Message)
    assert first_child.depth == second_child.depth == 1
    assert first_child.node_entry_id != second_child.node_entry_id
    assert second_message.data["state"]["revision"] == 1
    stale = reopened.validate(
        {"revision": 0, "outcome": "finish", "evidence": {}}
    )
    assert stale.valid is False
    assert tuple(issue.code for issue in stale.issues) == ("stale-revision",)

    child_done = reopened.next(
        {"revision": 1, "outcome": "finish", "evidence": {}},
        continue_=False,
    )
    assert child_done.state_id == "done"
    assert child_done.depth == 1
    assert registry.open(path).next(continue_=False).condition == "terminal"

    parent_prompt = registry.open(path).next(continue_=False)
    final = registry.open(path)
    persisted = final._store.read()
    final_message = final.get_instruction()

    assert parent_prompt.state_id == "after"
    assert parent_prompt.depth == 0
    assert isinstance(final_message, Message)
    assert final_message.data["state"]["revision"] == 4
    assert persisted.global_revision == 4
    assert persisted.root.entered_node.entry_id != parent_entry
    assert len(persisted.completed_runs) == 1
    archived = next(iter(persisted.completed_runs.values()))
    child_turns = tuple(
        entry for entry in archived.history if isinstance(entry, Turn)
    )
    assert len(child_turns) == 2
    assert child_turns[0].node_entry_id != child_turns[1].node_entry_id


def test_call_self_loop_allocates_a_fresh_entrance_child_and_call_id(
    tmp_path: Path,
) -> None:
    """Reusing any Call coordinate makes a returned child ambiguous after restart."""

    class CallLoopRutter(Rutter):
        rutter_id = "call-loop"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"entry": context.node_entry_id},
                    then={"completed": "delegate"},
                )
            }

    root = tmp_path / "reckonings"
    path = Path("call-self-loop.reckoning.json")
    registry = RutterRegistry({"loop": CallLoopRutter}, root)
    voyage = registry.create("loop", path, {})
    first_parent_entry = voyage._store.read().root.entered_node.entry_id

    voyage.next(continue_=False)
    first_child = voyage._store.read().root.active_child
    assert first_child is not None
    voyage.next(continue_=False)
    second_parent = registry.open(path).next(continue_=False)

    assert second_parent.state_id == "delegate"
    assert second_parent.node_entry_id != first_parent_entry
    assert second_parent.depth == 0

    reopened = registry.open(path)
    second_child_view = reopened.next(continue_=False)
    persisted = reopened._store.read()
    second_child = persisted.root.active_child
    assert second_child is not None
    first_record = persisted.root.history[0]
    assert isinstance(first_record, CallRecord)
    assert second_child_view.depth == 1
    assert second_child.run.run_id != first_child.run.run_id
    assert second_child.run.entered_node.entry_id != first_child.run.entered_node.entry_id
    assert second_child.call_id != first_record.call_id
    assert first_record.node_entry_id == first_parent_entry
    assert second_child.site == "delegate"


def test_call_depth_limit_rejects_before_charter_or_id_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking depth after child construction leaks callback work and identifiers."""

    charter_calls: list[None] = []

    def child_charter(context: StateContext) -> Mapping[str, object]:
        del context
        charter_calls.append(None)
        return {}

    class CallingRutter(Rutter):
        rutter_id = "depth-root"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=child_charter,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
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

    monkeypatch.setattr(engine, "_MAX_ACTIVE_DEPTH", 1, raising=False)
    monkeypatch.setattr(engine, "_new_id", record_allocation)

    with pytest.raises(RutterStateError, match="depth"):
        voyage.next(continue_=False)

    assert charter_calls == []
    assert allocated == []
    assert voyage._store.read().root.active_child is None
    assert (root / path).read_bytes() == before


def test_call_preview_without_a_returned_result_is_read_only_unavailable(
    tmp_path: Path,
) -> None:
    """A preview that starts the missing child is an advancing operation."""

    charter_calls: list[None] = []

    def child_charter(context: StateContext) -> Mapping[str, object]:
        del context
        charter_calls.append(None)
        return {}

    class CallingRutter(Rutter):
        rutter_id = "preview-call"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=child_charter,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-preview-unavailable.reckoning.json")
    voyage = RutterRegistry({"root": CallingRutter}, root).create("root", path, {})
    before = (root / path).read_bytes()
    current = voyage.get_current_node()

    with pytest.raises(PreviewUnavailable):
        voyage.next(dry_run=True)

    assert charter_calls == []
    assert voyage.get_current_node() == current
    assert voyage._store.read().root.active_child is None
    assert (root / path).read_bytes() == before


def test_call_preview_uses_a_durable_result_for_callable_routing_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the callable target during preview destroys the return restart seam."""

    routed: list[tuple[StateContext, RunResult]] = []

    def route(context: StateContext, result: RunResult) -> str:
        routed.append((context, result))
        return "done"

    class CallingRutter(Rutter):
        rutter_id = "callable-preview"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then=route,
                ),
                "done": Done(RunResult("complete", {})),
            }

    class InjectedCrash(RuntimeError):
        pass

    root = tmp_path / "reckonings"
    path = Path("call-preview-result.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.next(continue_=False)
    voyage.next(continue_=False)

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
            returning.next(continue_=False)

    at_call = registry.open(path)
    returned = at_call._store.read()
    assert returned.root.entered_node.state_id == "delegate"
    assert returned.root.active_child is None
    assert returned.global_revision == 2
    assert isinstance(returned.root.history[-1], CallRecord)
    before = (root / path).read_bytes()
    routed.clear()

    preview = at_call.next(dry_run=True)

    assert preview == NodeView(
        "callable-preview",
        1,
        "done",
        None,
        0,
        "preview",
    )
    assert len(routed) == 1
    context, result = routed[0]
    assert context.state_id == "delegate"
    assert context.node_entry_id == returned.root.entered_node.entry_id
    assert context.history.entries() == ()
    assert result == RunResult("completed", {})
    assert at_call.get_current_node().state_id == "delegate"
    assert (root / path).read_bytes() == before

    entered = at_call.next(continue_=False)
    assert entered.state_id == "done"
    assert entered.condition == "ready"
    assert len(routed) == 2
    persisted = at_call._store.read()
    assert isinstance(persisted.root.history[-1], CallRecord)
    assert persisted.completed_runs == returned.completed_runs


def test_call_charter_failure_faults_in_place_without_partial_child(
    tmp_path: Path,
) -> None:
    """Letting a Charter exception escape loses a durable failure coordinate."""

    def fail_charter(context: StateContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private charter detail")

    class CallingRutter(Rutter):
        rutter_id = "charter-failure"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=fail_charter,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("call-charter-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    source = voyage.get_current_node()

    faulted = voyage.next(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted == NodeView(
        "charter-failure",
        1,
        "delegate",
        source.node_entry_id,
        0,
        "fault",
    )
    assert persisted.root.active_child is None
    assert persisted.root.history == ()
    assert persisted.completed_runs == {}
    assert persisted.global_revision == 0
    assert persisted.fault == {
        "category": "child-charter",
        "run_id": persisted.root.run_id,
        "state_id": "delegate",
        "node_entry_id": source.node_entry_id,
    }
    assert b"private charter detail" not in (root / path).read_bytes()
    assert reopened.get_current_node() == faulted
    with pytest.raises(RunBlocked):
        reopened.next()


def test_prompt_child_materialization_failure_leaves_no_partial_attachment(
    tmp_path: Path,
) -> None:
    """Persisting child IDs without its initial Turn violates atomic push."""

    def fail_data(context: StateContext) -> Mapping[str, object]:
        del context
        raise RuntimeError("private child materialization detail")

    class PromptChild(Rutter):
        rutter_id = "failing-prompt-child"
        definition_version = 1
        start_state = "ask"

        def define_states(self) -> Mapping[str, object]:
            return {
                "ask": Prompt(
                    "Child question.",
                    answer=AnswerSpec({"done": {}}),
                    data=fail_data,
                    then="done",
                ),
                "done": Done(RunResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "materialization-failure"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    PromptChild,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("child-materialization-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    source = voyage.get_current_node()

    faulted = voyage.next(continue_=False)
    persisted = registry.open(path)._store.read()

    assert faulted.condition == "fault"
    assert faulted.node_entry_id == source.node_entry_id
    assert persisted.root.active_child is None
    assert persisted.root.history == ()
    assert persisted.completed_runs == {}
    assert persisted.fault == {
        "category": "child-materialization",
        "run_id": persisted.root.run_id,
        "state_id": "delegate",
        "node_entry_id": source.node_entry_id,
    }
    assert b"private child materialization detail" not in (root / path).read_bytes()


def test_child_fault_retains_the_complete_active_parent_child_path(
    tmp_path: Path,
) -> None:
    """Detaching a faulted child destroys the recursive failure coordinate."""

    def fail_route(context: AnswerContext) -> str:
        del context
        raise RuntimeError("private child routing detail")

    class PromptChild(Rutter):
        rutter_id = "faulting-child"
        definition_version = 1
        start_state = "ask"

        def define_states(self) -> Mapping[str, object]:
            return {
                "ask": Prompt(
                    "Child question.",
                    answer=AnswerSpec({"done": {}}),
                    then=fail_route,
                ),
                "done": Done(RunResult("child-complete", {})),
            }

    class CallingRutter(Rutter):
        rutter_id = "fault-path-root"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    PromptChild,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("child-fault-path.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.next(continue_=False)
    before_fault = voyage._store.read()
    child = before_fault.root.active_child
    assert child is not None

    faulted = voyage.next(
        {"revision": 0, "outcome": "done", "evidence": {}},
        continue_=False,
    )
    reopened = registry.open(path)
    persisted = reopened._store.read()
    active_child = persisted.root.active_child

    assert active_child is not None
    assert persisted.root.run_id == before_fault.root.run_id
    assert persisted.root.entered_node.state_id == "delegate"
    assert active_child.call_id == child.call_id
    assert active_child.run.run_id == child.run.run_id
    assert active_child.run.entered_node.state_id == "ask"
    accepted = active_child.run.history[-1]
    assert isinstance(accepted, Turn)
    assert accepted.response is not None
    assert persisted.fault == {
        "category": "routing",
        "run_id": active_child.run.run_id,
        "state_id": "ask",
        "node_entry_id": active_child.run.entered_node.entry_id,
    }
    assert faulted == NodeView(
        "faulting-child",
        1,
        "ask",
        active_child.run.entered_node.entry_id,
        1,
        "fault",
    )
    assert reopened.get_current_node() == faulted
    assert b"private child routing detail" not in (root / path).read_bytes()


def test_returned_child_record_survives_later_parent_routing_failure(
    tmp_path: Path,
) -> None:
    """Rolling return back with a failed route would replay an accepted child."""

    def fail_route(context: StateContext, result: RunResult) -> str:
        assert context.state_id == "delegate"
        assert result == RunResult("completed", {})
        raise RuntimeError("private parent routing detail")

    class CallingRutter(Rutter):
        rutter_id = "post-return-failure"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then=fail_route,
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("post-return-routing-failure.reckoning.json")
    registry = RutterRegistry({"root": CallingRutter}, root)
    voyage = registry.create("root", path, {})
    voyage.next(continue_=False)
    child = voyage._store.read().root.active_child
    assert child is not None
    voyage.next(continue_=False)

    faulted = registry.open(path).next(continue_=False)
    reopened = registry.open(path)
    persisted = reopened._store.read()

    assert faulted.condition == "fault"
    assert faulted.rutter_id == "post-return-failure"
    assert faulted.state_id == "delegate"
    assert faulted.depth == 0
    assert persisted.root.active_child is None
    assert persisted.global_revision == 2
    assert len(persisted.root.history) == 1
    record = persisted.root.history[0]
    assert isinstance(record, CallRecord)
    assert record.call_id == child.call_id
    assert record.completed_run_id == child.run.run_id
    assert persisted.completed_runs[record.completed_run_id].result == RunResult(
        "completed", {}
    )
    assert persisted.fault == {
        "category": "routing",
        "run_id": persisted.root.run_id,
        "state_id": "delegate",
        "node_entry_id": persisted.root.entered_node.entry_id,
    }
    assert b"private parent routing detail" not in (root / path).read_bytes()


def test_dry_run_at_nested_terminal_does_not_return_or_route_the_child(
    tmp_path: Path,
) -> None:
    """Settling a child return during dry-run mutates two durable authorities."""

    class CallingRutter(Rutter):
        rutter_id = "nested-terminal-preview"
        definition_version = 1
        start_state = "delegate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "delegate": Call(
                    DirectChildRutter,
                    charter=lambda context: {"from": context.state_id},
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    root = tmp_path / "reckonings"
    path = Path("nested-terminal-preview.reckoning.json")
    voyage = RutterRegistry({"root": CallingRutter}, root).create("root", path, {})
    voyage.next(continue_=False)
    terminal = voyage.next(continue_=False)
    before = (root / path).read_bytes()

    preview = voyage.next(dry_run=True)

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

    terminal = voyage.next()

    assert terminal.condition == "terminal"
    assert terminal.state_id == "complete"
    assert voyage._store.read().global_revision == 1
