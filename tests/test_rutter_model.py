"""Freeze the immutable public Rutter value model."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from math import inf, nan
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import pytest

import officina.rutter as rutter_api
import officina.rutter.model as model_module
from officina.rutter.model import (
    MachineStep,
    MachineContext,
    MachineRecord,
    MachineResult,
    ActiveChild,
    ActiveRun,
    LLMResponseContext,
    SubRutter,
    SubRutterRecord,
    Charter,
    CompletedRun,
    Terminal,
    TerminalRecord,
    Transition,
    TransitionContext,
    EnteredEvolution,
    HistoryView,
    Message,
    EvolutionView,
    LLMStep,
    MachineInstruction,
    Reckoning,
    VoyageResult,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    RutterValidationError,
    EvolutionContext,
    Turn,
    ValidationIssue,
    ValidationReport,
    VoyageStatus,
)
from test_support.rutter_fixtures import (
    ExampleRutter,
    example_message,
    response_schema as _response_schema,
)


def _done_record(
    *, record_id: str = "done-1", evolution_entry_id: str = "entry-1"
) -> TerminalRecord:
    return TerminalRecord(
        record_id=record_id,
        evolution_entry_id=evolution_entry_id,
        evolution_id="complete",
        result=VoyageResult("completed", {"artifact": "draft.md"}),
    )


def _completed_run(*, run_id: str = "child-run") -> CompletedRun:
    return CompletedRun(
        run_id=run_id,
        rutter_id="child",
        definition_version=1,
        charter=Charter({"item": "A"}),
        history=(_done_record(),),
    )


def _accepted_turn(*, record_id: str = "turn-1") -> Turn:
    return Turn(
        record_id=record_id,
        evolution_entry_id="entry-report",
        evolution_id="report",
        revision=1,
        message=example_message(),
        response={"outcome": "reported", "ok": True},
    )


def _call_record(*, invocation_id: str = "call-1") -> SubRutterRecord:
    return SubRutterRecord(
        invocation_id=invocation_id,
        evolution_entry_id="entry-call",
        origin_evolution_id="delegate",
        transition_hook_id=None,
        attached_to_transition_id=None,
        completed_voyage_instance_id="child-run",
    )


def _attached_call_record(
    *,
    invocation_id: str = "call-attached",
    evolution_entry_id: str = "entry-1",
) -> SubRutterRecord:
    return SubRutterRecord(
        invocation_id=invocation_id,
        evolution_entry_id=evolution_entry_id,
        origin_evolution_id=None,
        transition_hook_id="maker-1",
        attached_to_transition_id="done-edge-1",
        completed_voyage_instance_id="nested-run",
    )


def test_message_has_exact_instruction_and_data_parts() -> None:
    message = Message(
        instructions={
            "text": "Report.",
            "response_schema": _response_schema("reported"),
        },
        data={
            "evolution": {"id": "report", "entry_id": "e1"},
            "payload": {"chunk": "A"},
        },
    )

    assert set(message.to_json()) == {"instructions", "data"}
    assert message.to_json() == {
        "instructions": {
            "text": "Report.",
            "response_schema": {
                "type": "object",
                "properties": {"outcome": {"enum": ("reported",)}},
                "required": ("outcome",),
            },
        },
        "data": {
            "evolution": {"id": "report", "entry_id": "e1"},
            "payload": {"chunk": "A"},
        },
    }


def test_active_run_has_one_entered_evolution_and_recursive_child() -> None:
    assert fields(ActiveRun)[4].name == "entered_evolution"
    assert tuple(field.name for field in fields(ActiveRun)) == (
        "run_id",
        "rutter_id",
        "definition_version",
        "charter",
        "entered_evolution",
        "history",
        "active_child",
    )


def test_active_run_can_remain_entered_at_its_settled_done_node() -> None:
    done = TerminalRecord(
        "done-terminal",
        "entry-terminal",
        "complete",
        VoyageResult("completed", {"artifact": "draft.md"}),
    )
    active = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-terminal", "complete"),
        (done,),
        None,
    )

    assert active.history == (done,)
    assert ActiveRun.from_json(active.to_json()) == active


def test_active_done_history_preserves_settled_attached_calls() -> None:
    done = _done_record()
    attached = _attached_call_record()

    active = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-1", "complete"),
        (done, attached),
        None,
    )

    assert HistoryView(active.history).terminal() == done


@pytest.mark.parametrize(
    "done",
    (
        TerminalRecord(
            "done-wrong-entry",
            "other-entry",
            "complete",
            VoyageResult("completed", {}),
        ),
        TerminalRecord(
            "done-wrong-state",
            "entry-terminal",
            "other-state",
            VoyageResult("completed", {}),
        ),
    ),
)
def test_active_done_must_match_current_entrance(done) -> None:
    with pytest.raises(RutterStateError, match="current entered node"):
        ActiveRun(
            "root-run",
            "example",
            1,
            Charter({}),
            EnteredEvolution("entry-terminal", "complete"),
            (done,),
            None,
        )


def test_active_done_can_own_attached_child_bound_to_done_record() -> None:
    child = ActiveRun(
        "child-run",
        "child",
        1,
        Charter({}),
        EnteredEvolution("entry-child", "start"),
        (),
        None,
    )
    active_child = ActiveChild(
        "call-active",
        "attached_case",
        "maker-1",
        "done-terminal",
        child,
    )

    active = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-terminal", "complete"),
        (
            TerminalRecord(
                "done-terminal",
                "entry-terminal",
                "complete",
                VoyageResult("completed", {}),
            ),
        ),
        active_child,
    )

    assert active.active_child == active_child
    assert ActiveRun.from_json(active.to_json()) == active


@pytest.mark.parametrize(
    "active_child",
    (
        ActiveChild(
            "call-explicit",
            "explicit_call",
            "delegate",
            None,
            ActiveRun(
                "explicit-child",
                "child",
                1,
                Charter({}),
                EnteredEvolution("entry-explicit", "start"),
                (),
                None,
            ),
        ),
        ActiveChild(
            "call-wrong-edge",
            "attached_case",
            "maker-1",
            "other-edge",
            ActiveRun(
                "wrong-edge-child",
                "child",
                1,
                Charter({}),
                EnteredEvolution("entry-attached", "start"),
                (),
                None,
            ),
        ),
    ),
)
def test_active_done_rejects_nonattached_or_wrong_edge_child(active_child) -> None:
    with pytest.raises(RutterStateError, match="TerminalRecord"):
        ActiveRun(
            "root-run",
            "example",
            1,
            Charter({}),
            EnteredEvolution("entry-terminal", "complete"),
            (
                TerminalRecord(
                    "done-terminal",
                    "entry-terminal",
                    "complete",
                    VoyageResult("completed", {}),
                ),
            ),
            active_child,
        )


def test_validation_issue_path_accepts_string_and_integer_segments() -> None:
    issue = ValidationIssue(("evidence", "nodes", 0), "missing", "required")

    assert issue.path[-1] == 0
    assert issue.to_json() == {
        "path": ("evidence", "nodes", 0),
        "code": "missing",
        "message": "required",
    }


@pytest.mark.parametrize(
    ("factory", "payload"),
    (
        (
            Message.from_json,
            {
                "instructions": {
                    "text": "Report.",
                    "response_schema": _response_schema("reported"),
                },
                "data": {
                    "evolution": {"id": "report", "entry_id": "e1"},
                    "payload": {},
                },
                "extra": None,
            },
        ),
        (VoyageResult.from_json, {"outcome": "ok", "value": {}, "extra": None}),
        (
            EnteredEvolution.from_json,
            {"entry_id": "entry-1", "state_id": "ready", "extra": False},
        ),
    ),
)
def test_json_decoders_reject_extra_or_missing_fields(factory, payload) -> None:
    with pytest.raises(RutterStateError, match="fields"):
        factory(payload)


@pytest.mark.parametrize(
    "construct",
    (
        lambda: ValidationReport(valid=1),
        lambda: ValidationReport(valid=0, issues=(ValidationIssue((), "x", "x"),)),
    ),
)
def test_validation_report_requires_exact_boolean(construct) -> None:
    with pytest.raises(RutterDefinitionError, match="exact Boolean"):
        construct()


@pytest.mark.parametrize("invalid", (nan, inf, -inf, object()))
def test_public_json_values_reject_nonfinite_or_non_json_data(invalid: object) -> None:
    with pytest.raises(RutterDefinitionError, match="finite JSON"):
        Charter({"invalid": invalid})
    with pytest.raises(RutterDefinitionError, match="finite JSON"):
        VoyageResult("done", invalid)
    with pytest.raises(RutterDefinitionError, match="finite JSON"):
        MachineResult("done", invalid)


@pytest.mark.parametrize(
    "construct",
    (
        lambda: EnteredEvolution("", "ready"),
        lambda: EnteredEvolution("../entry", "ready"),
        lambda: EnteredEvolution("entry-1", "bad/state"),
        lambda: ActiveRun(
            "run 1", "example", 1, Charter({}), EnteredEvolution("entry-1", "ready"), (), None
        ),
        lambda: CompletedRun(
            "child", "../child", 1, Charter({}), (_done_record(),)
        ),
    ),
)
def test_identifiers_are_nonempty_stable_tokens(construct) -> None:
    with pytest.raises((RutterDefinitionError, RutterStateError), match="ID"):
        construct()


def test_history_rejects_duplicate_record_ids() -> None:
    with pytest.raises(RutterStateError, match="duplicate history record ID"):
        HistoryView((_accepted_turn(), _accepted_turn()))


def test_completed_run_requires_one_final_done_authority() -> None:
    with pytest.raises(RutterStateError, match="TerminalRecord"):
        CompletedRun("run-1", "example", 1, Charter({}), ())
    with pytest.raises(RutterStateError, match="TerminalRecord"):
        CompletedRun(
            "run-1",
            "example",
            1,
            Charter({}),
            (_done_record(), _accepted_turn()),
        )


def test_completed_run_projects_sole_done_before_attached_calls() -> None:
    done = _done_record()
    attached = _attached_call_record()

    completed = CompletedRun(
        "run-1",
        "example",
        1,
        Charter({}),
        (done, attached),
    )

    assert completed.result == VoyageResult("completed", {"artifact": "draft.md"})
    assert completed.history == (done, attached)
    assert CompletedRun.from_json(completed.to_json()) == completed
    assert HistoryView((done, attached)).terminal() == done


@pytest.mark.parametrize(
    "post_done",
    (
        _accepted_turn(),
        MachineRecord(
            "action-after-done",
            "save",
            "entry-1",
            "save",
            "pure",
            MachineResult("saved", {}),
        ),
        _call_record(invocation_id="explicit-after-done"),
        _attached_call_record(
            invocation_id="attached-wrong-entry",
            evolution_entry_id="other-entry",
        ),
        _done_record(record_id="done-2"),
    ),
)
def test_history_rejects_invalid_records_after_done(post_done) -> None:
    with pytest.raises(RutterStateError, match="TerminalRecord"):
        HistoryView((_done_record(), post_done))


@pytest.mark.parametrize(
    "construct",
    (
        lambda: ActiveChild(
            "call-1",
            "explicit_call",
            "delegate",
            "edge-1",
            ActiveRun(
                "child-run",
                "child",
                1,
                Charter({}),
                EnteredEvolution("entry-child", "start"),
                (),
                None,
            ),
        ),
        lambda: SubRutterRecord(
            "call-1",
            "entry-call",
            None,
            "maker-1",
            None,
            "child-run",
        ),
    ),
)
def test_child_provenance_must_match_site_kind(construct) -> None:
    with pytest.raises(RutterStateError, match="provenance|transition"):
        construct()


def test_reckoning_rejects_active_and_completed_run_id_overlap() -> None:
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "report"),
        (),
        None,
    )
    completed = CompletedRun(
        "root-run", "example", 1, Charter({}), (_done_record(),)
    )

    with pytest.raises(RutterStateError, match="active and completed run IDs"):
        Reckoning(1, 0, root, {"root-run": completed}, None, None)


def test_llm_step_snapshots_none_empty_and_shaped_response_schemas() -> None:
    source = {"type": "object", "properties": {"summary": {"type": "string"}}}

    absent = LLMStep("Report.", next_on_outcome="done")
    empty = LLMStep("Report.", response_schema={}, next_on_outcome="done")
    shaped = LLMStep("Report.", response_schema=source, next_on_outcome="done")
    source["properties"]["summary"]["type"] = "integer"

    assert absent.response_schema is None
    assert empty.response_schema == {}
    assert shaped.response_schema == {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
    }


@pytest.mark.parametrize(
    ("construct_static", "construct_callback"),
    (
        (
            lambda: LLMStep(
                "Report.",
                response_schema=_response_schema("reported"),
                next_on_outcome="done",
            ),
            lambda: LLMStep(
                "Report.",
                response_schema=_response_schema("reported"),
                choose_next=lambda context: "done",
            ),
        ),
        (
            lambda: MachineStep(
                lambda context: MachineResult("reported", {}),
                mode="pure",
                next_on_outcome="done",
            ),
            lambda: MachineStep(
                lambda context: MachineResult("reported", {}),
                mode="pure",
                choose_next=lambda context, result: "done",
            ),
        ),
        (
            lambda: SubRutter(
                ExampleRutter,
                charter_constructor=lambda context: {},
                next_on_outcome="done",
            ),
            lambda: SubRutter(
                ExampleRutter,
                charter_constructor=lambda context: {},
                choose_next=lambda context, result: "done",
            ),
        ),
    ),
)
def test_evolution_constructors_separate_static_and_callback_routing(
    construct_static, construct_callback
) -> None:
    static = construct_static()
    callback = construct_callback()

    assert static.next_on_outcome == "done"
    assert static.choose_next is None
    assert callback.next_on_outcome is None
    assert callable(callback.choose_next)


@pytest.mark.parametrize(
    "construct",
    (
        lambda: LLMStep("Report.", response_schema=_response_schema("reported")),
        lambda: LLMStep(
            "Report.",
            response_schema=_response_schema("reported"),
            next_on_outcome="done",
            choose_next=lambda context: "done",
        ),
        lambda: MachineStep(lambda context: MachineResult("reported", {}), mode="pure"),
        lambda: MachineStep(
            lambda context: MachineResult("reported", {}),
            mode="pure",
            next_on_outcome="done",
            choose_next=lambda context, result: "done",
        ),
        lambda: SubRutter(ExampleRutter, charter_constructor=lambda context: {}),
        lambda: SubRutter(
            ExampleRutter,
            charter_constructor=lambda context: {},
            next_on_outcome="done",
            choose_next=lambda context, result: "done",
        ),
    ),
)
def test_evolution_constructors_require_exactly_one_routing_mode(construct) -> None:
    with pytest.raises(RutterDefinitionError, match="exactly one routing mode"):
        construct()


def test_terminal_construction_names_fixed_and_contextual_result_modes() -> None:
    result = VoyageResult("completed", {"artifact": "draft.md"})

    fixed = Terminal(result=result)
    contextual = Terminal(result_constructor=lambda context: result)

    assert fixed.result is result
    assert fixed.result_constructor is None
    assert contextual.result is None
    assert contextual.result_constructor is not None
    with pytest.raises(RutterDefinitionError, match="exactly one"):
        Terminal()
    with pytest.raises(RutterDefinitionError, match="exactly one"):
        Terminal(result=result, result_constructor=lambda context: result)
    with pytest.raises(RutterDefinitionError, match="exactly 1 argument"):
        Terminal(result_constructor=lambda: result)
    with pytest.raises(TypeError):
        Terminal(result)


def test_child_definitions_name_only_their_charter_constructors() -> None:
    call = SubRutter(
        ExampleRutter,
        charter_constructor=lambda context: {"artifact": "draft.md"},
        next_on_outcome="done",
    )

    assert callable(call.charter_constructor)
    assert not hasattr(call, "charter")


def test_definition_values_keep_callbacks_in_process_only() -> None:
    prompt = ExampleRutter().define_evolutions()["report"]
    action = MachineStep(lambda context: MachineResult("ok", {}), mode="pure", next_on_outcome="done")
    call = SubRutter(
        ExampleRutter,
        charter_constructor=lambda context: {},
        next_on_outcome="done",
    )
    done = Terminal(result=VoyageResult("completed", {}))

    def execute_action() -> MachineResult:
        return MachineResult("ok", {})

    answer_format = {
        "outcome": "declared outcome",
        "value": {"type": "finite JSON"},
    }
    instruction = MachineInstruction(
        "action-1",
        "pure",
        execute_action,
        answer_format,
    )
    answer_format["value"]["type"] = "changed"  # type: ignore[index]

    assert isinstance(prompt, LLMStep)
    assert prompt.text == "Report."
    assert action.mode == "pure"
    assert call.child is ExampleRutter
    assert done.result == VoyageResult("completed", {})
    assert instruction.machine_id == "action-1"
    assert instruction.mode == "pure"
    assert instruction.run() == MachineResult("ok", {})
    assert instruction.answer_format == {
        "outcome": "declared outcome",
        "value": {"type": "finite JSON"},
    }
    assert isinstance(instruction.answer_format, MappingProxyType)
    assert isinstance(instruction.answer_format["value"], MappingProxyType)
    assert not hasattr(prompt, "to_json")
    assert not hasattr(action, "to_json")
    assert not hasattr(call, "to_json")
    assert not hasattr(done, "to_json")
    assert not hasattr(instruction, "to_json")


@pytest.mark.parametrize(
    "construct",
    (
        lambda: MachineInstruction(
            "action-1",
            "sometimes",
            lambda: MachineResult("ok", {}),
            {"outcome": "string", "value": {}},
        ),
        lambda: MachineInstruction(
            "action-1",
            "pure",
            lambda context: MachineResult("ok", {}),
            {"outcome": "string", "value": {}},
        ),
        lambda: MachineInstruction(
            "action-1",
            "pure",
            lambda: MachineResult("ok", {}),
            {"outcome": "string", "value": nan},
        ),
    ),
)
def test_python_instruction_rejects_invalid_exact_values(construct) -> None:
    with pytest.raises(RutterDefinitionError):
        construct()


def test_rutter_constructor_exposes_one_frozen_definition() -> None:
    evolutions = {"done": Terminal(result=VoyageResult("complete", {}))}
    definition = Rutter(
        id="direct",
        version=1,
        start="done",
        evolutions=evolutions,
    )
    evolutions.clear()

    assert definition.rutter_id == "direct"
    assert definition.definition_version == 1
    assert definition.initial_evolution_id == "done"
    assert set(definition.define_evolutions()) == {"done"}
    assert isinstance(definition.define_evolutions(), MappingProxyType)
    assert definition.define_transition_hooks() == ()


def test_legacy_no_argument_subclass_definition_remains_supported() -> None:
    definition = ExampleRutter()

    assert definition.rutter_id == "example"
    assert definition.definition_version == 1
    assert definition.initial_evolution_id == "report"
    assert definition.allow_multiple_hooks_per_transition is False
    assert definition.define_transition_hooks() == ()
    assert set(definition.define_evolutions()) == {"report", "complete"}


def test_rutter_constructor_snapshots_hook_sequence() -> None:
    hooks = []
    definition = Rutter(
        id="direct",
        version=1,
        start="done",
        evolutions={"done": Terminal(result=VoyageResult("complete", {}))},
        hooks=hooks,
    )
    hooks.append(object())

    assert definition.define_transition_hooks() == ()


def test_constructor_modes_are_disjoint() -> None:
    with pytest.raises(RutterDefinitionError):
        Rutter()
    with pytest.raises(RutterDefinitionError):
        ExampleRutter(
            id="hybrid",
            version=1,
            start="done",
            evolutions={"done": Terminal(result=VoyageResult("complete", {}))},
        )


@pytest.mark.parametrize(
    "arguments",
    (
        {"id": "partial"},
        {"id": "bad", "version": 1, "start": "done", "evolutions": []},
        {
            "id": "bad",
            "version": 1,
            "start": "done",
            "evolutions": {
                "done": Terminal(result=VoyageResult("complete", {}))
            },
            "hooks": "audit",
        },
        {
            "id": "bad",
            "version": 1,
            "start": "done",
            "evolutions": {
                "done": Terminal(result=VoyageResult("complete", {}))
            },
            "allow_multiple_hooks_per_transition": 1,
        },
    ),
)
def test_rutter_constructor_rejects_invalid_definition_shape(arguments) -> None:
    with pytest.raises(RutterDefinitionError):
        Rutter(**arguments)


def test_json_round_trips_preserve_exact_persisted_values() -> None:
    message = example_message()
    response = {"outcome": "reported", "items": ["A", "B"]}
    action_result = MachineResult("stored", {"count": 2})
    run_result = VoyageResult("completed", {"artifact": "draft.md"})
    entered = EnteredEvolution("entry-report", "report")
    turn = Turn("turn-1", "entry-report", "report", 1, message, response)
    action_record = MachineRecord(
        "action-record-1",
        "save",
        "entry-2",
        "save",
        "repeat-safe",
        action_result,
    )
    call_record = _call_record()
    done_record = _done_record()
    completed = _completed_run()
    active_child = ActiveChild(
        "call-active",
        "explicit_call",
        "delegate",
        None,
        ActiveRun(
            "active-child",
            "child",
            1,
            Charter({"item": "B"}),
            EnteredEvolution("entry-child", "start"),
            (),
            None,
        ),
    )
    active = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({"artifact": "draft.md"}),
        entered,
        (turn, action_record, call_record),
        active_child,
    )
    reckoning = Reckoning(
        1,
        3,
        active,
        {"child-run": completed},
        None,
        None,
    )

    pairs = (
        (Charter, Charter({"items": ["A"]})),
        (ValidationIssue, ValidationIssue(("evidence", 0), "missing", "required")),
        (ValidationReport, ValidationReport(False, (ValidationIssue((), "x", "x"),))),
        (Message, message),
        (MachineResult, action_result),
        (VoyageResult, run_result),
        (EnteredEvolution, entered),
        (Turn, turn),
        (MachineRecord, action_record),
        (SubRutterRecord, call_record),
        (TerminalRecord, done_record),
        (CompletedRun, completed),
        (ActiveChild, active_child),
        (ActiveRun, active),
        (Reckoning, reckoning),
    )
    for value_type, value in pairs:
        assert value_type.from_json(value.to_json()) == value


def test_private_effect_recovery_retains_all_seven_typed_fields() -> None:
    """Dropping or reverting any v3 recovery authority field must fail."""

    result = MachineResult("stored", {"count": 2})
    recovery = model_module._EffectRecovery(
        "action-1",
        "run-1",
        "entry-1",
        "save",
        "repeat-safe",
        "completed",
        result,
    )

    assert tuple(field.name for field in fields(recovery)) == (
        "machine_id",
        "owner_run_id",
        "evolution_entry_id",
        "evolution_id",
        "mode",
        "disposition",
        "result",
    )
    assert recovery.result is result
    with pytest.raises(FrozenInstanceError):
        recovery.disposition = "planned"


def test_fault_values_freeze_opaque_wire_and_export_only_safe_summary() -> None:
    """Raw legacy authority stays private while its safe summary is public."""

    source = {"legacy": {"items": ["A"]}}

    opaque = model_module.OpaqueFault(source)
    summary = model_module.FaultSummary("opaque", None, None, None, ())
    source["legacy"]["items"].append("late")

    assert opaque.wire == {"legacy": {"items": ("A",)}}
    assert summary.category == "opaque"
    assert rutter_api.FaultSummary is model_module.FaultSummary
    with pytest.raises(FrozenInstanceError):
        summary.category = "routing"

    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-review", "review"),
        (),
        None,
    )
    with pytest.raises(RutterStateError, match="opaque fault wire is private"):
        Reckoning(3, 0, root, {}, None, opaque).to_json()


@pytest.mark.parametrize(
    "construct",
    (
        lambda: model_module.FaultSummary("", "review", "entry-review", None, ()),
        lambda: model_module.FaultSummary(
            "bad category", "review", "entry-review", None, ()
        ),
        lambda: model_module.FaultSummary("routing", "bad id", "entry-review", None, ()),
        lambda: model_module.FaultSummary("routing", "review", "bad id", None, ()),
        lambda: model_module.FaultSummary(
            "routing", "review", "entry-review", "bad id", ()
        ),
        lambda: model_module.FaultSummary(
            "routing", "review", "entry-review", None, ("bad id",)
        ),
        lambda: model_module.FaultSummary(
            "routing", "review", "entry-review", None, "hook-1"
        ),
        lambda: model_module.FaultSummary(
            "routing", "review", "entry-review", None, b"hook-1"
        ),
        lambda: model_module.FaultSummary(
            "routing", "review", "entry-review", None, ["hook-1", "bad id"]
        ),
        lambda: model_module.FaultSummary("routing", "review", None, None, ()),
        lambda: model_module.FaultSummary("routing", None, "entry-review", None, ()),
        lambda: model_module.FaultSummary("routing", None, None, None, ()),
        lambda: model_module.FaultSummary("opaque", "review", "entry-review", None, ()),
        lambda: model_module.FaultSummary("opaque", None, None, "review", ()),
        lambda: model_module.FaultSummary("opaque", None, None, None, ("hook-1",)),
    ),
)
def test_fault_summary_rejects_malformed_public_coordinates(construct) -> None:
    with pytest.raises(RutterDefinitionError):
        construct()


def test_fault_summary_freezes_and_validates_hook_id_iterables() -> None:
    summary = model_module.FaultSummary(
        "routing", "review", "entry-review", None, ["hook-1"]
    )

    assert summary.transition_hook_ids == ("hook-1",)


def test_json_views_are_independent_and_deeply_immutable() -> None:
    source = {"items": ["A"], "nested": {"ok": True}}
    charter = Charter(source)
    first = charter.to_json()
    second = charter.to_json()
    source["items"].append("late")
    source["nested"]["ok"] = False

    assert first == {"items": ("A",), "nested": {"ok": True}}
    assert first is not second
    assert isinstance(first, MappingProxyType)
    assert isinstance(first["nested"], MappingProxyType)
    with pytest.raises(TypeError):
        first["new"] = True


def test_history_view_exposes_complete_immutable_query_contract() -> None:
    open_turn = Turn(
        "turn-open",
        "entry-next",
        "next",
        2,
        Message(
            {"text": "Next.", "response_schema": _response_schema("continued")},
            {
                "evolution": {"id": "next", "entry_id": "entry-next"},
                "payload": {},
            },
        ),
        None,
    )
    action = MachineRecord(
        "action-1",
        "save",
        "entry-save",
        "save",
        "pure",
        MachineResult("saved", {"id": 7}),
    )
    call = _call_record()
    history = HistoryView(
        (_accepted_turn(), action, call, open_turn),
        {"child-run": _completed_run()},
    )
    done = _done_record(record_id="done-root")
    complete_history = HistoryView(
        (_accepted_turn(), action, call, done),
        {"child-run": _completed_run()},
    )

    assert history.entries() == (_accepted_turn(), action, call, open_turn)
    assert history.turns() == (_accepted_turn(),)
    assert history.turns("report") == (_accepted_turn(),)
    assert history.turns("missing") == ()
    assert history.open_turn() == open_turn
    assert history.machines() == (action,)
    assert history.machines("save") == (action,)
    assert history.subrutters() == (history.latest_subrutter(),)
    assert history.subrutters(origin_evolution_id="delegate") == (
        history.latest_subrutter(origin_evolution_id="delegate"),
    )
    assert history.subrutters(origin_evolution_id="missing") == ()
    assert history.terminal() is None
    assert complete_history.terminal() == done
    assert history.latest_turn() == _accepted_turn()
    assert history.latest_turn("missing") is None
    assert history.latest_machine() == action
    assert history.latest_machine("missing") is None
    assert history.latest_subrutter() is not None
    assert history.latest_subrutter().invocation_id == "call-1"
    assert history.latest_subrutter().origin_evolution_id == "delegate"
    assert history.latest_subrutter().transition_hook_id is None
    assert history.latest_subrutter().completed.voyage_instance_id == "child-run"
    assert history.latest_subrutter().result == VoyageResult(
        "completed", {"artifact": "draft.md"}
    )
    assert history.latest_subrutter(origin_evolution_id="missing") is None
    assert history.require_latest_turn() == _accepted_turn()
    assert history.require_latest_machine() == action
    assert history.require_latest_subrutter().invocation_id == "call-1"


def test_history_view_absence_behavior_is_stable() -> None:
    history = HistoryView(())

    assert history.entries() == ()
    assert history.turns() == ()
    assert history.open_turn() is None
    assert history.machines() == ()
    assert history.subrutters() == ()
    assert history.terminal() is None
    assert history.latest_turn() is None
    assert history.latest_machine() is None
    assert history.latest_subrutter() is None
    with pytest.raises(RutterDefinitionError, match="history has no matching Turn") as error:
        history.require_latest_turn()
    assert error.value.category == "definition"
    with pytest.raises(RutterDefinitionError, match="history has no matching MachineRecord"):
        history.require_latest_machine()
    with pytest.raises(RutterDefinitionError, match="history has no matching SubRutterRecord"):
        history.require_latest_subrutter()


def test_history_view_rejects_mutually_exclusive_subrutter_filters() -> None:
    history = HistoryView(())

    with pytest.raises(RutterDefinitionError, match="mutually exclusive"):
        history.subrutters(
            origin_evolution_id="delegate",
            transition_hook_id="hook-1",
        )


def test_history_prefix_excludes_the_source_record_and_later_entries() -> None:
    turn = _accepted_turn()
    action = MachineRecord(
        "action-1", "save", "entry-save", "save", "pure", MachineResult("saved", {})
    )
    call = _call_record()
    full = HistoryView((turn, action, call), {"child-run": _completed_run()})

    prefix = full.strict_prefix(action)

    assert prefix.entries() == (turn,)
    assert prefix.latest_turn() == turn
    assert prefix.latest_machine() is None
    assert prefix.latest_subrutter() is None
    assert full.entries() == (turn, action, call)


def test_contexts_are_frozen_and_share_immutable_history() -> None:
    history = HistoryView((_accepted_turn(),))
    state = EvolutionContext(Charter({"artifact": "draft.md"}), "report", "entry-report", history)
    answer = LLMResponseContext(state, example_message(), {"outcome": "reported"})
    action = MachineContext(state, "save")
    transition = Transition(
        "transition-1",
        "entry-report",
        "report",
        "reported",
        "complete",
    )
    edge = TransitionContext(
        state,
        transition,
        _accepted_turn(),
    )

    assert answer.evolution is state
    assert action.evolution is state
    assert edge.evolution.history.entries() == (_accepted_turn(),)
    assert edge.transition is transition
    assert (
        edge.transition.source,
        edge.transition.outcome,
        edge.transition.target,
        edge.transition.transition_id,
    ) == ("report", "reported", "complete", "transition-1")
    with pytest.raises(RutterDefinitionError, match="Transition"):
        TransitionContext(state, transition.to_json(), _accepted_turn())

    class DerivedTransition(Transition):
        pass

    with pytest.raises(RutterDefinitionError, match="Transition"):
        TransitionContext(
            state,
            DerivedTransition(
                "transition-2",
                "entry-report",
                "report",
                "reported",
                "complete",
            ),
            _accepted_turn(),
        )
    with pytest.raises(FrozenInstanceError):
        state.evolution_id = "other"


def test_node_view_directly_identifies_an_entered_nested_node() -> None:
    node = EvolutionView(
        rutter_id="example",
        definition_version=2,
        evolution_id="report",
        evolution_entry_id="entry-report",
        depth=1,
        condition="ready",
    )

    assert tuple(field.name for field in fields(EvolutionView)) == (
        "rutter_id",
        "definition_version",
        "evolution_id",
        "evolution_entry_id",
        "depth",
        "condition",
    )
    assert node.evolution_id == "report"
    assert node.evolution_entry_id == "entry-report"
    assert node.depth == 1
    assert not hasattr(node, "history")
    with pytest.raises(FrozenInstanceError):
        node.condition = "terminal"


def test_node_view_allows_missing_entrance_only_for_preview() -> None:
    preview = EvolutionView("example", 2, "report", None, 0, "preview")

    assert preview.evolution_entry_id is None
    assert preview.condition == "preview"
    with pytest.raises(RutterDefinitionError, match="entrance"):
        EvolutionView("example", 2, "report", None, 0, "ready")
    with pytest.raises(RutterDefinitionError, match="entrance"):
        EvolutionView("example", 2, "report", "entry-report", 0, "preview")


@pytest.mark.parametrize(
    "current_evolution",
    (
        EvolutionView("example", 2, "report", "entry-report", 0, "ready"),
        EvolutionView("example", 2, "report", "entry-report", 0, "fault"),
        EvolutionView("example", 2, "report", "entry-report", 0, "uncertain"),
        EvolutionView("example", 2, "report", None, 0, "preview"),
    ),
)
def test_voyage_status_rejects_terminal_result_for_nonterminal_condition(
    current_evolution: EvolutionView,
) -> None:
    with pytest.raises(RutterDefinitionError, match="terminal_result"):
        VoyageStatus(
            current_evolution,
            None,
            VoyageResult("complete", {}),
            None,
        )


@pytest.mark.parametrize(
    ("response_schema", "public_instructions", "wire_answer"),
    (
        (None, {"text": "Report."}, None),
        ({}, {"text": "Report.", "response_schema": {}}, {}),
        (
            {"type": "object", "required": ("outcome",)},
            {
                "text": "Report.",
                "response_schema": {"type": "object", "required": ["outcome"]},
            },
            {"type": "object", "required": ("outcome",)},
        ),
    ),
)
def test_turn_v3_adapter_injects_revision_and_round_trips_response_schema(
    response_schema: object,
    public_instructions: Mapping[str, object],
    wire_answer: object,
) -> None:
    """Losing the None/empty/schema distinction or exposing revision breaks v3."""

    message = Message(
        public_instructions,
        {
            "evolution": {"id": "report", "entry_id": "entry-report"},
            "payload": {"chunk": "A"},
        },
    )
    turn = Turn(
        "turn-1",
        "entry-report",
        "report",
        7,
        message,
        {"outcome": "reported", "items": ["A", "B"]},
    )

    wire = turn.to_json()

    assert message.text == "Report."
    assert message.response_schema == response_schema
    assert message.payload == {"chunk": "A"}
    assert message.evolution_id == "report"
    assert message.evolution_entry_id == "entry-report"
    assert not hasattr(message, "revision")
    assert "revision" not in message.data["evolution"]
    assert wire["message"]["instructions"]["answer"] == wire_answer
    assert wire["message"]["data"]["state"] == {
        "id": "report",
        "entry_id": "entry-report",
        "revision": 7,
    }
    assert wire["response"] == {
        "revision": 7,
        "outcome": "reported",
        "evidence": {"items": ("A", "B")},
    }
    assert Turn.from_json(wire) == turn
    assert set(turn.response) == {"outcome", "items"}


@pytest.mark.parametrize("reserved", ("outcome", "revision"))
def test_turn_v3_rejects_legacy_evidence_reserved_key_collisions(
    reserved: str,
) -> None:
    """Flattening colliding legacy evidence would silently lose one value."""

    wire = {
        "record_id": "turn-1",
        "node_entry_id": "entry-report",
        "state_id": "report",
        "revision": 3,
        "message": {
            "instructions": {"text": "Report.", "answer": {}},
            "data": {
                "state": {
                    "id": "report",
                    "entry_id": "entry-report",
                    "revision": 3,
                },
                "payload": {},
            },
        },
        "response": {
            "revision": 3,
            "outcome": "reported",
            "evidence": {reserved: "collision"},
        },
    }

    with pytest.raises(
        RutterStateError,
        match="^Turn response evidence contains reserved flat-response fields$",
    ):
        Turn.from_json(wire)


@pytest.mark.parametrize(
    ("coordinate", "replacement"),
    (("id", "other"), ("entry_id", "other-entry"), ("revision", 4)),
)
def test_turn_v3_rejects_duplicated_message_coordinate_mismatches(
    coordinate: str,
    replacement: object,
) -> None:
    """Trusting duplicated wire coordinates would admit contradictory authority."""

    wire = {
        "record_id": "turn-1",
        "node_entry_id": "entry-report",
        "state_id": "report",
        "revision": 3,
        "message": {
            "instructions": {"text": "Report.", "answer": {}},
            "data": {
                "state": {
                    "id": "report",
                    "entry_id": "entry-report",
                    "revision": 3,
                },
                "payload": {},
            },
        },
        "response": None,
    }
    wire["message"]["data"]["state"][coordinate] = replacement

    with pytest.raises(RutterStateError, match="message coordinates"):
        Turn.from_json(wire)


@pytest.mark.parametrize(
    "values",
    (
        ("bad/rutter", 1, "report", "entry-1", 0, "ready"),
        ("example", True, "report", "entry-1", 0, "ready"),
        ("example", 1, "bad/state", "entry-1", 0, "ready"),
        ("example", 1, "report", "bad/entry", 0, "ready"),
        ("example", 1, "report", "entry-1", True, "ready"),
        ("example", 1, "report", "entry-1", -1, "ready"),
        ("example", 1, "report", "entry-1", 0, "waiting"),
    ),
)
def test_node_view_rejects_invalid_exact_values(values) -> None:
    with pytest.raises(RutterDefinitionError):
        EvolutionView(*values)


def test_public_operating_errors_have_stable_exports_and_categories() -> None:
    expected = {
        "NotApplicable": "not_applicable",
        "RunBlocked": "run_blocked",
        "PreviewUnavailable": "preview_unavailable",
    }

    for name, category in expected.items():
        error_type = getattr(rutter_api, name)
        error = error_type("unavailable")
        assert isinstance(error, rutter_api.RutterError)
        assert error.category == category
        assert name in rutter_api.__all__


def test_public_package_has_the_exact_narrow_new_vocabulary_surface() -> None:
    assert rutter_api.__all__ == (
        "AskAndDiagnose",
        "Charter",
        "CompletedVoyageView",
        "DiagnoseAnswer",
        "DiagnosisCase",
        "DiagnosisDetail",
        "Evolution",
        "EvolutionContext",
        "EvolutionView",
        "FaultSummary",
        "HistoryView",
        "JsonObject",
        "JsonValue",
        "LLMResponseContext",
        "LLMStep",
        "MachineContext",
        "MachineInstruction",
        "MachineRecord",
        "MachineResult",
        "MachineStep",
        "Message",
        "NotApplicable",
        "PreviewUnavailable",
        "QuestionCase",
        "RunBlocked",
        "Rutter",
        "RutterDefinitionError",
        "RutterError",
        "RutterRegistry",
        "RutterStateError",
        "RutterValidationError",
        "SubRutter",
        "SubRutterRecordView",
        "Terminal",
        "TerminalRecord",
        "TransitionContext",
        "TransitionHook",
        "TransitionMatch",
        "Turn",
        "ValidationIssue",
        "ValidationReport",
        "Voyage",
        "VoyageResult",
        "VoyageStatus",
        "after",
        "ask_and_diagnose_on",
        "before",
        "diagnose_answer_on",
        "empty_data",
        "hook_sequence_after",
        "on_transition",
    )
    for private_name in (
        "ActiveChild",
        "ActiveRun",
        "KnownFault",
        "OpaqueFault",
        "Reckoning",
        "ReckoningStore",
        "_EffectRecovery",
    ):
        assert not hasattr(rutter_api, private_name)


def test_focused_model_sources_preserve_one_way_import_boundaries() -> None:
    source_root = Path(model_module.__file__).parent

    def sibling_imports(name: str) -> set[str]:
        tree = ast.parse((source_root / f"{name}.py").read_text(encoding="utf-8"))
        return {
            node.module.removeprefix("officina.rutter.")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("officina.rutter.")
        }

    assert sibling_imports("values") == set()
    assert sibling_imports("history") == {"values"}
    assert sibling_imports("authoring") == {"history", "values"}
    assert sibling_imports("model") == {"authoring", "history", "values"}

    facade = ast.parse((source_root / "model.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in facade.body
    )


def test_validation_error_categories_are_stable() -> None:
    assert RutterDefinitionError("x").category == "definition"
    assert RutterStateError("x").category == "state"
    assert RutterValidationError("x").category == "validation"
