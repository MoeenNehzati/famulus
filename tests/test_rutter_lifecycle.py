"""Specify the Prompt/Done lifecycle at the bound-voyage boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from officina.rutter.model import (
    AnswerContext,
    AnswerSpec,
    Done,
    DoneRecord,
    Message,
    NodeView,
    NotApplicable,
    Prompt,
    Rutter,
    RunBlocked,
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
