"""Freeze the immutable public Rutter value model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from math import inf, nan
from types import MappingProxyType

import pytest

import officina.rutter as rutter_api
from officina.rutter.model import (
    Action,
    ActionContext,
    ActionRecord,
    ActionResult,
    ActiveChild,
    ActiveRun,
    AnswerContext,
    AnswerSpec,
    Call,
    CallRecord,
    Charter,
    CompletedRun,
    Done,
    DoneRecord,
    EdgeContext,
    EnteredNode,
    HistoryView,
    Message,
    NodeView,
    Prompt,
    PythonInstruction,
    Reckoning,
    Response,
    RunResult,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    RutterValidationError,
    StateContext,
    Turn,
    ValidationIssue,
    ValidationReport,
)
from test_support.rutter_fixtures import ExampleRutter, example_message


def _done_record(
    *, record_id: str = "done-1", node_entry_id: str = "entry-1"
) -> DoneRecord:
    return DoneRecord(
        record_id=record_id,
        node_entry_id=node_entry_id,
        state_id="complete",
        result=RunResult("completed", {"artifact": "draft.md"}),
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
        node_entry_id="entry-report",
        state_id="report",
        revision=1,
        message=example_message(),
        response=Response(1, "reported", {"ok": True}),
    )


def _call_record(*, call_id: str = "call-1") -> CallRecord:
    return CallRecord(
        call_id=call_id,
        node_entry_id="entry-call",
        site_kind="explicit_call",
        site_id="delegate",
        attached_to_edge_id=None,
        completed_run_id="child-run",
    )


def _attached_call_record(
    *, call_id: str = "call-attached", node_entry_id: str = "entry-1"
) -> CallRecord:
    return CallRecord(
        call_id=call_id,
        node_entry_id=node_entry_id,
        site_kind="attached_case",
        site_id="maker-1",
        attached_to_edge_id="done-edge-1",
        completed_run_id="nested-run",
    )


def test_message_has_exact_instruction_and_data_parts() -> None:
    message = Message(
        instructions={"text": "Report.", "answer": {"reported": {}}},
        data={
            "state": {"id": "report", "entry_id": "e1", "revision": 1},
            "payload": {"chunk": "A"},
        },
    )

    assert set(message.to_json()) == {"instructions", "data"}
    assert message.to_json() == {
        "instructions": {"text": "Report.", "answer": {"reported": {}}},
        "data": {
            "state": {"id": "report", "entry_id": "e1", "revision": 1},
            "payload": {"chunk": "A"},
        },
    }


def test_active_run_has_one_entered_node_and_recursive_child() -> None:
    assert fields(ActiveRun)[4].name == "entered_node"
    assert tuple(field.name for field in fields(ActiveRun)) == (
        "run_id",
        "rutter_id",
        "definition_version",
        "charter",
        "entered_node",
        "history",
        "active_child",
    )


def test_active_run_can_remain_entered_at_its_settled_done_node() -> None:
    done = DoneRecord(
        "done-terminal",
        "entry-terminal",
        "complete",
        RunResult("completed", {"artifact": "draft.md"}),
    )
    active = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredNode("entry-terminal", "complete"),
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
        EnteredNode("entry-1", "complete"),
        (done, attached),
        None,
    )

    assert HistoryView(active.history).done() == done


@pytest.mark.parametrize(
    "done",
    (
        DoneRecord(
            "done-wrong-entry",
            "other-entry",
            "complete",
            RunResult("completed", {}),
        ),
        DoneRecord(
            "done-wrong-state",
            "entry-terminal",
            "other-state",
            RunResult("completed", {}),
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
            EnteredNode("entry-terminal", "complete"),
            (done,),
            None,
        )


def test_active_done_cannot_have_an_active_child_at_task_five_boundary() -> None:
    child = ActiveRun(
        "child-run",
        "child",
        1,
        Charter({}),
        EnteredNode("entry-child", "start"),
        (),
        None,
    )
    active_child = ActiveChild(
        "call-active",
        "explicit_call",
        "delegate",
        None,
        child,
    )

    with pytest.raises(RutterStateError, match="active child"):
        ActiveRun(
            "root-run",
            "example",
            1,
            Charter({}),
            EnteredNode("entry-terminal", "complete"),
            (
                DoneRecord(
                    "done-terminal",
                    "entry-terminal",
                    "complete",
                    RunResult("completed", {}),
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
                "instructions": {"text": "Report.", "answer": {"reported": {}}},
                "data": {
                    "state": {"id": "report", "entry_id": "e1", "revision": 1},
                    "payload": {},
                },
                "extra": None,
            },
        ),
        (Response.from_json, {"revision": 1, "outcome": "ok"}),
        (RunResult.from_json, {"outcome": "ok", "value": {}, "extra": None}),
        (
            EnteredNode.from_json,
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
        RunResult("done", invalid)
    with pytest.raises(RutterDefinitionError, match="finite JSON"):
        ActionResult("done", invalid)


@pytest.mark.parametrize(
    "construct",
    (
        lambda: EnteredNode("", "ready"),
        lambda: EnteredNode("../entry", "ready"),
        lambda: EnteredNode("entry-1", "bad/state"),
        lambda: ActiveRun(
            "run 1", "example", 1, Charter({}), EnteredNode("entry-1", "ready"), (), None
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
    with pytest.raises(RutterStateError, match="DoneRecord"):
        CompletedRun("run-1", "example", 1, Charter({}), ())
    with pytest.raises(RutterStateError, match="DoneRecord"):
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

    assert completed.result == RunResult("completed", {"artifact": "draft.md"})
    assert completed.history == (done, attached)
    assert CompletedRun.from_json(completed.to_json()) == completed
    assert HistoryView((done, attached)).done() == done


@pytest.mark.parametrize(
    "post_done",
    (
        _accepted_turn(),
        ActionRecord(
            "action-after-done",
            "save",
            "entry-1",
            "save",
            "pure",
            ActionResult("saved", {}),
        ),
        _call_record(call_id="explicit-after-done"),
        _attached_call_record(
            call_id="attached-wrong-entry",
            node_entry_id="other-entry",
        ),
        _done_record(record_id="done-2"),
    ),
)
def test_history_rejects_invalid_records_after_done(post_done) -> None:
    with pytest.raises(RutterStateError, match="DoneRecord"):
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
                EnteredNode("entry-child", "start"),
                (),
                None,
            ),
        ),
        lambda: CallRecord(
            "call-1",
            "entry-call",
            "attached_case",
            "maker-1",
            None,
            "child-run",
        ),
    ),
)
def test_child_provenance_must_match_site_kind(construct) -> None:
    with pytest.raises(RutterStateError, match="provenance"):
        construct()


def test_reckoning_rejects_active_and_completed_run_id_overlap() -> None:
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredNode("entry-root", "report"),
        (),
        None,
    )
    completed = CompletedRun(
        "root-run", "example", 1, Charter({}), (_done_record(),)
    )

    with pytest.raises(RutterStateError, match="active and completed run IDs"):
        Reckoning(1, 0, root, {"root-run": completed}, None, None)


def test_answer_spec_preserves_none_empty_and_shaped_guidance() -> None:
    spec = AnswerSpec(
        {"skip": None, "empty": {}, "report": {"summary": "text"}}
    )

    assert spec.to_json() == {
        "skip": None,
        "empty": {},
        "report": {"summary": "text"},
    }


def test_definition_values_keep_callbacks_in_process_only() -> None:
    prompt = ExampleRutter().define_states()["report"]
    action = Action(lambda context: ActionResult("ok", {}), mode="pure", then="done")
    call = Call(ExampleRutter, charter=lambda context: {}, then="done")
    done = Done(RunResult("completed", {}))
    instruction = PythonInstruction("action-1", action.run)

    assert isinstance(prompt, Prompt)
    assert prompt.text == "Report."
    assert action.mode == "pure"
    assert call.child is ExampleRutter
    assert done.result == RunResult("completed", {})
    assert instruction.action_id == "action-1"
    assert not hasattr(prompt, "to_json")
    assert not hasattr(action, "to_json")
    assert not hasattr(call, "to_json")
    assert not hasattr(done, "to_json")
    assert not hasattr(instruction, "to_json")


def test_rutter_author_boundary_has_stable_identity_and_empty_case_makers() -> None:
    definition = ExampleRutter()

    assert definition.rutter_id == "example"
    assert definition.definition_version == 1
    assert definition.start_state == "report"
    assert definition.allow_multiple_cases_at_once is False
    assert definition.define_case_makers() == ()
    assert set(definition.define_states()) == {"report", "complete"}


def test_json_round_trips_preserve_exact_persisted_values() -> None:
    message = example_message()
    response = Response(1, "reported", {"items": ["A", "B"]})
    action_result = ActionResult("stored", {"count": 2})
    run_result = RunResult("completed", {"artifact": "draft.md"})
    entered = EnteredNode("entry-report", "report")
    turn = Turn("turn-1", "entry-report", "report", 1, message, response)
    action_record = ActionRecord(
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
            EnteredNode("entry-child", "start"),
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
        (AnswerSpec, AnswerSpec({"reported": {}})),
        (ValidationIssue, ValidationIssue(("evidence", 0), "missing", "required")),
        (ValidationReport, ValidationReport(False, (ValidationIssue((), "x", "x"),))),
        (Message, message),
        (Response, response),
        (ActionResult, action_result),
        (RunResult, run_result),
        (EnteredNode, entered),
        (Turn, turn),
        (ActionRecord, action_record),
        (CallRecord, call_record),
        (DoneRecord, done_record),
        (CompletedRun, completed),
        (ActiveChild, active_child),
        (ActiveRun, active),
        (Reckoning, reckoning),
    )
    for value_type, value in pairs:
        assert value_type.from_json(value.to_json()) == value


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
            {"text": "Next.", "answer": {"continued": {}}},
            {
                "state": {"id": "next", "entry_id": "entry-next", "revision": 2},
                "payload": {},
            },
        ),
        None,
    )
    action = ActionRecord(
        "action-1",
        "save",
        "entry-save",
        "save",
        "pure",
        ActionResult("saved", {"id": 7}),
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
    assert history.actions() == (action,)
    assert history.actions("save") == (action,)
    assert history.calls() == (history.latest_call(),)
    assert history.calls("delegate") == (history.latest_call("delegate"),)
    assert history.calls("missing") == ()
    assert history.done() is None
    assert complete_history.done() == done
    assert history.latest_turn() == _accepted_turn()
    assert history.latest_turn("missing") is None
    assert history.latest_action() == action
    assert history.latest_action("missing") is None
    assert history.latest_call() is not None
    assert history.latest_call().call_id == "call-1"
    assert history.latest_call().site == "delegate"
    assert history.latest_call().completed.run_id == "child-run"
    assert history.latest_call().result == RunResult(
        "completed", {"artifact": "draft.md"}
    )
    assert history.latest_call("missing") is None
    assert history.require_latest_turn() == _accepted_turn()
    assert history.require_latest_action() == action
    assert history.require_latest_call().call_id == "call-1"


def test_history_view_absence_behavior_is_stable() -> None:
    history = HistoryView(())

    assert history.entries() == ()
    assert history.turns() == ()
    assert history.open_turn() is None
    assert history.actions() == ()
    assert history.calls() == ()
    assert history.done() is None
    assert history.latest_turn() is None
    assert history.latest_action() is None
    assert history.latest_call() is None
    with pytest.raises(RutterDefinitionError, match="history has no matching Turn") as error:
        history.require_latest_turn()
    assert error.value.category == "definition"
    with pytest.raises(RutterDefinitionError, match="history has no matching ActionRecord"):
        history.require_latest_action()
    with pytest.raises(RutterDefinitionError, match="history has no matching CallRecord"):
        history.require_latest_call()


def test_history_prefix_excludes_the_source_record_and_later_entries() -> None:
    turn = _accepted_turn()
    action = ActionRecord(
        "action-1", "save", "entry-save", "save", "pure", ActionResult("saved", {})
    )
    call = _call_record()
    full = HistoryView((turn, action, call), {"child-run": _completed_run()})

    prefix = full.strict_prefix(action)

    assert prefix.entries() == (turn,)
    assert prefix.latest_turn() == turn
    assert prefix.latest_action() is None
    assert prefix.latest_call() is None
    assert full.entries() == (turn, action, call)


def test_contexts_are_frozen_and_share_immutable_history() -> None:
    history = HistoryView((_accepted_turn(),))
    state = StateContext(Charter({"artifact": "draft.md"}), "report", "entry-report", history)
    answer = AnswerContext(state, example_message(), Response(1, "reported", {}))
    action = ActionContext(state, "save")
    edge = EdgeContext(state, {"edge_id": "edge-1"}, _accepted_turn())

    assert answer.state is state
    assert action.state is state
    assert edge.state.history.entries() == (_accepted_turn(),)
    with pytest.raises(FrozenInstanceError):
        state.state_id = "other"


def test_node_view_directly_identifies_an_entered_nested_node() -> None:
    node = NodeView(
        rutter_id="example",
        definition_version=2,
        state_id="report",
        node_entry_id="entry-report",
        depth=1,
        condition="ready",
    )

    assert tuple(field.name for field in fields(NodeView)) == (
        "rutter_id",
        "definition_version",
        "state_id",
        "node_entry_id",
        "depth",
        "condition",
    )
    assert node.state_id == "report"
    assert node.node_entry_id == "entry-report"
    assert node.depth == 1
    assert not hasattr(node, "history")
    with pytest.raises(FrozenInstanceError):
        node.condition = "terminal"


def test_node_view_allows_missing_entrance_only_for_preview() -> None:
    preview = NodeView("example", 2, "report", None, 0, "preview")

    assert preview.node_entry_id is None
    assert preview.condition == "preview"
    with pytest.raises(RutterDefinitionError, match="entrance"):
        NodeView("example", 2, "report", None, 0, "ready")
    with pytest.raises(RutterDefinitionError, match="entrance"):
        NodeView("example", 2, "report", "entry-report", 0, "preview")


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
        NodeView(*values)


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


def test_validation_error_categories_are_stable() -> None:
    assert RutterDefinitionError("x").category == "definition"
    assert RutterStateError("x").category == "state"
    assert RutterValidationError("x").category == "validation"
