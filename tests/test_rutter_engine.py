"""Specify the integrated Task 5 engine boundary and public cutover."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Mapping

import pytest

import officina.rutter as rutter_public
import officina.rutter.engine as engine
import officina.rutter.runtime as runtime
from officina.rutter.model import (
    Action,
    ActionContext,
    ActionResult,
    ActiveRun,
    AnswerSpec,
    CallRecord,
    Charter,
    CompletedRun,
    Done,
    DoneRecord,
    EnteredNode,
    Prompt,
    PythonInstruction,
    Reckoning,
    Rutter,
    RutterStateError,
    RunResult,
)
from officina.rutter.runtime import RutterRegistry
from test_support.rutter_fixtures import ExampleRutter


def test_pure_action_instruction_is_stable_read_only_and_zero_argument(
    tmp_path: Path,
) -> None:
    """Skipping Action instructions or invoking their callback during a read must fail."""

    seen: list[ActionContext] = []

    def run(context: ActionContext) -> ActionResult:
        seen.append(context)
        return ActionResult("calculated", {"count": len(context.state.history.actions())})

    class PureActionRutter(Rutter):
        rutter_id = "pure-action"
        definition_version = 1
        start_state = "calculate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "calculate": Action(run, mode="pure", then="done"),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("pure-action.reckoning.json")
    registry = RutterRegistry({"pure": PureActionRutter}, tmp_path)
    voyage = registry.create("pure", path, {})
    before = (tmp_path / path).read_bytes()

    first = voyage.get_instruction()
    second = voyage.get_instruction()
    reopened = registry.open(path).get_instruction()

    assert isinstance(first, PythonInstruction)
    assert isinstance(second, PythonInstruction)
    assert isinstance(reopened, PythonInstruction)
    assert first.action_id == second.action_id == reopened.action_id
    assert first.mode == "pure"
    assert tuple(inspect.signature(first.run).parameters) == ()
    assert first.answer_format == {
        "outcome": "declared outcome",
        "value": {"type": "finite JSON"},
    }
    assert seen == []
    assert first.run() == ActionResult("calculated", {"count": 0})
    assert len(seen) == 1
    assert seen[0].action_id == first.action_id
    assert seen[0].state.state_id == "calculate"
    assert seen[0].state.node_entry_id == voyage.get_current_node().node_entry_id
    assert (tmp_path / path).read_bytes() == before


@pytest.mark.parametrize(
    ("result", "valid", "code"),
    (
        (ActionResult("calculated", {"count": 1}), True, None),
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
    """Treating an Action as inapplicable or accepting a loose envelope must fail."""

    class PureActionRutter(Rutter):
        rutter_id = "validate-action"
        definition_version = 1
        start_state = "calculate"

        def define_states(self) -> Mapping[str, object]:
            return {
                "calculate": Action(
                    lambda context: ActionResult("calculated", {}),
                    mode="pure",
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
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


def test_public_cutover_exports_registry_and_only_the_four_voyage_operations(
    tmp_path: Path,
) -> None:
    """Dropping the new registry or restoring compatibility operations must fail."""

    assert rutter_public.RutterRegistry is RutterRegistry
    assert not hasattr(rutter_public, "BaseRutter")
    voyage = RutterRegistry({"example": ExampleRutter}, tmp_path).create(
        "example", Path("surface.reckoning.json"), {}
    )
    assert tuple(inspect.signature(voyage.get_instruction).parameters) == ()
    assert tuple(inspect.signature(voyage.validate).parameters) == ("response",)
    assert tuple(inspect.signature(voyage.next).parameters) == (
        "response",
        "continue_",
        "dry_run",
    )
    assert tuple(inspect.signature(voyage.get_current_node).parameters) == ()
    assert not hasattr(voyage, "advance")
    assert not hasattr(voyage, "reckoning")
    next_parameters = inspect.signature(engine._next).parameters
    assert tuple(next_parameters) == ("voyage", "response", "continue_", "dry_run")
    assert next_parameters["response"].default is runtime._MISSING


def test_done_remains_terminal_after_its_attached_case_child_returns() -> None:
    """A post-Done attached-case return must not reopen completion."""

    result = RunResult("complete", {})
    done = DoneRecord("done-root", "entry-root", "done", result)
    child = CompletedRun(
        "run-child",
        "child",
        1,
        Charter({}),
        (
            DoneRecord(
                "done-child",
                "entry-child",
                "done",
                result,
            ),
        ),
    )
    returned = CallRecord(
        "call-child",
        "entry-root",
        "attached_case",
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
            EnteredNode("entry-root", "done"),
            (done, returned),
            None,
        ),
        {child.run_id: child},
        None,
        None,
    )

    assert engine._condition(reckoning, Done(result)) == "terminal"


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

    assert stale.get_current_node() == terminal
    assert stale.get_instruction() is None
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
        start_state = "start"

        def define_states(self) -> Mapping[str, object]:
            return {
                "start": Prompt(
                    "Start.",
                    answer=AnswerSpec({"go": {}}),
                    data=fail_data,
                    then="done",
                ),
                "done": Done(RunResult("complete", {})),
            }

    path = Path("failing-start.reckoning.json")
    registry = RutterRegistry({"failure": FailingStartRutter}, tmp_path)

    with pytest.raises(RutterStateError, match="Prompt materialization failed"):
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
    assert reopened.get_current_node().state_id == "complete"
    assert reopened.get_current_node().condition == "ready"
    assert reopened._store.read().root.history[0].response is not None
    monkeypatch.setattr(engine, "_OPERATION_LIMIT", 100)
    assert reopened.next().condition == "terminal"
