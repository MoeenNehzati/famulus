"""Specify the Prompt/Done lifecycle at the bound-voyage boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping

import pytest

import officina.rutter.engine as engine
from officina.rutter.model import (
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
