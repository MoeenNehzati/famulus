"""Specify the ordinary-Rutter diagnostic library."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import officina.rutter as rutter


class SequenceChild(rutter.Rutter):
    rutter_id = "sequence-child"
    definition_version = 1
    start_state = "done"

    def define_states(self) -> dict[str, object]:
        return {"done": rutter.Done(rutter.RunResult("checked", {}))}


def _question() -> object:
    return rutter.QuestionCase(
        "sum-check",
        "What is one plus one?",
        "2",
        format_hint={"answer": "integer"},
        metadata={"topic": "arithmetic"},
    )


def _completed_result(voyage: object) -> rutter.RunResult:
    reckoning = voyage._store.read()
    done = rutter.HistoryView(
        reckoning.root.history, reckoning.completed_runs
    ).done()
    assert done is not None
    return done.result


def _edge_context(
    *,
    source: str = "answer",
    outcome: str = "answered",
    target: str | None = "done",
    answer: str = "2",
    history: rutter.HistoryView | None = None,
) -> rutter.EdgeContext:
    message = rutter.Message(
        instructions={"text": "Answer.", "answer": {"answered": {"answer": "string"}}},
        data={
            "state": {"id": source, "entry_id": "entry-answer", "revision": 0},
            "payload": {},
        },
    )
    turn = rutter.Turn(
        "edge-answer",
        "entry-answer",
        source,
        0,
        message,
        rutter.Response(0, outcome, {"answer": answer}),
    )
    return rutter.EdgeContext(
        rutter.StateContext(
            rutter.Charter({}),
            source,
            "entry-answer",
            rutter.HistoryView(()) if history is None else history,
        ),
        {
            "edge_id": turn.record_id,
            "source_entry_id": turn.node_entry_id,
            "source": source,
            "outcome": outcome,
            "target": target,
        },
        turn,
    )


def _sequence_history(
    attached_count: int,
    *,
    include_explicit_collision: bool = False,
    duplicate_edge: bool = False,
) -> rutter.HistoryView:
    entries: list[object] = []
    completed: dict[str, rutter.CompletedRun] = {}
    for index in range(attached_count):
        source = rutter.ActionRecord(
            f"edge-{index}",
            f"action-{index}",
            f"entry-{index}",
            "answer",
            "pure",
            rutter.ActionResult("answered", {}),
        )
        run_id = f"run-{index}"
        entries.extend(
            (
                source,
                rutter.CallRecord(
                    f"call-{index}",
                    source.node_entry_id,
                    "attached_case",
                    "progressive-checks",
                    "edge-0" if duplicate_edge and index > 0 else source.record_id,
                    run_id,
                ),
            )
        )
        completed[run_id] = rutter.CompletedRun(
            run_id,
            SequenceChild.rutter_id,
            SequenceChild.definition_version,
            rutter.Charter({"step": index}),
            (
                rutter.DoneRecord(
                    f"done-{index}",
                    f"child-entry-{index}",
                    "done",
                    rutter.RunResult("checked", {}),
                ),
            ),
        )
    if include_explicit_collision:
        run_id = "run-explicit"
        entries.append(
            rutter.CallRecord(
                "call-explicit",
                "entry-explicit",
                "explicit_call",
                "progressive-checks",
                None,
                run_id,
            )
        )
        completed[run_id] = rutter.CompletedRun(
            run_id,
            SequenceChild.rutter_id,
            SequenceChild.definition_version,
            rutter.Charter({}),
            (
                rutter.DoneRecord(
                    "done-explicit",
                    "entry-child-explicit",
                    "done",
                    rutter.RunResult("checked", {}),
                ),
            ),
        )
    return rutter.HistoryView(tuple(entries), completed)


def test_question_case_defaults_and_projection_are_immutable() -> None:
    """Dropping optional fields or retaining mutable input aliases must fail."""

    case = rutter.QuestionCase("sum-check", "What is one plus one?", "2")

    assert case.to_json() == {
        "case_id": "sum-check",
        "enquiry": "What is one plus one?",
        "expected_answer": "2",
        "format_hint": None,
        "metadata": {},
    }
    with pytest.raises(FrozenInstanceError):
        case.enquiry = "Changed"
    with pytest.raises(TypeError):
        case.to_json()["case_id"] = "changed"


def test_question_case_snapshots_nested_json_and_round_trips_exactly() -> None:
    """Aliasing nested source JSON or omitting an exact decoder must fail."""

    format_hint = {"answer": ["integer"]}
    metadata = {"topic": {"name": "arithmetic"}}
    case = rutter.QuestionCase(
        "sum-check",
        "What is one plus one?",
        "2",
        format_hint=format_hint,
        metadata=metadata,
    )
    format_hint["answer"].append("fraction")
    metadata["topic"]["name"] = "changed"

    expected = {
        "case_id": "sum-check",
        "enquiry": "What is one plus one?",
        "expected_answer": "2",
        "format_hint": {"answer": ("integer",)},
        "metadata": {"topic": {"name": "arithmetic"}},
    }
    assert case.to_json() == expected
    assert rutter.QuestionCase.from_json(expected) == case
    with pytest.raises(TypeError):
        case.to_json()["metadata"]["topic"]["name"] = "changed"


@pytest.mark.parametrize(
    "value",
    (
        {
            "case_id": "sum-check",
            "enquiry": "Question?",
            "expected_answer": "answer",
            "format_hint": None,
        },
        {
            "case_id": "sum-check",
            "enquiry": "Question?",
            "expected_answer": "answer",
            "format_hint": None,
            "metadata": {},
            "extra": None,
        },
        {
            "case_id": "bad id",
            "enquiry": "Question?",
            "expected_answer": "answer",
            "format_hint": None,
            "metadata": {},
        },
        {
            "case_id": "sum-check",
            "enquiry": 3,
            "expected_answer": "answer",
            "format_hint": None,
            "metadata": {},
        },
        {
            "case_id": "sum-check",
            "enquiry": "Question?",
            "expected_answer": False,
            "format_hint": None,
            "metadata": {},
        },
        {
            "case_id": "sum-check",
            "enquiry": "Question?",
            "expected_answer": "answer",
            "format_hint": float("nan"),
            "metadata": {},
        },
        {
            "case_id": "sum-check",
            "enquiry": "Question?",
            "expected_answer": "answer",
            "format_hint": None,
            "metadata": [],
        },
    ),
)
def test_question_case_decoder_rejects_nonexact_or_nonfinite_values(
    value: object,
) -> None:
    """Accepting missing, extra, coerced, or nonfinite fields must fail."""

    with pytest.raises(rutter.RutterStateError):
        rutter.QuestionCase.from_json(value)


@pytest.mark.parametrize("verdict", (None, True, False))
def test_diagnosis_case_has_exact_nullable_boolean_contract(
    verdict: bool | None,
) -> None:
    """Coercing a mechanical verdict or flattening its question must fail."""

    question = rutter.QuestionCase("sum-check", "One plus one?", "2")
    case = rutter.DiagnosisCase(question, "2", verdict)
    expected = {
        "question": question.to_json(),
        "actual_answer": "2",
        "precomputed_verdict": verdict,
    }

    assert case.to_json() == expected
    assert rutter.DiagnosisCase.from_json(expected) == case
    with pytest.raises(rutter.RutterDefinitionError):
        rutter.DiagnosisCase(question, "2", 1)


def test_diagnosis_detail_requires_three_nonempty_exact_fields() -> None:
    """Collapsing, omitting, or accepting an empty diagnosis field must fail."""

    detail = rutter.DiagnosisDetail(
        "It adds three numbers.",
        "The enquiry asks for two addends.",
        "Add only one and one.",
    )
    expected = {
        "mistake": "It adds three numbers.",
        "reason": "The enquiry asks for two addends.",
        "minimal_fix": "Add only one and one.",
    }

    assert detail.to_json() == expected
    assert rutter.DiagnosisDetail.from_json(expected) == detail
    with pytest.raises(rutter.RutterDefinitionError):
        rutter.DiagnosisDetail("mistake", " ", "fix")
    with pytest.raises(rutter.RutterStateError):
        rutter.DiagnosisDetail.from_json({"mistake": "m", "reason": "r"})
    with pytest.raises(rutter.RutterStateError):
        rutter.DiagnosisDetail.from_json(
            {"mistake": "m", "reason": "r", "minimal_fix": "f", "extra": "x"}
        )


def test_diagnose_answer_true_verdict_completes_without_a_prompt(
    tmp_path: Path,
) -> None:
    """Routing a mechanical equality verdict through an LLM turn must fail."""

    case = rutter.DiagnosisCase(_question(), "2", True)
    voyage = rutter.RutterRegistry(
        {"diagnose": rutter.DiagnoseAnswer}, tmp_path
    ).create("diagnose", Path("equal.reckoning.json"), case.to_json())

    terminal = voyage.next(continue_=True)

    assert terminal.state_id == "complete-equal-evaluator"
    assert terminal.condition == "terminal"
    assert _completed_result(voyage) == rutter.RunResult(
        "equal",
        {
            "case_id": "sum-check",
            "actual_answer": "2",
            "expected_answer": "2",
            "decided_by": "evaluator",
            "detail": None,
        },
    )
    assert rutter.HistoryView(voyage._store.read().root.history).turns() == ()


def test_diagnose_answer_false_verdict_requests_exact_three_field_detail(
    tmp_path: Path,
) -> None:
    """Comparing again or completing inequality without full detail must fail."""

    case = rutter.DiagnosisCase(_question(), "3", False)
    voyage = rutter.RutterRegistry(
        {"diagnose": rutter.DiagnoseAnswer}, tmp_path
    ).create("diagnose", Path("different.reckoning.json"), case.to_json())

    explain = voyage.next(continue_=True)
    message = voyage.get_instruction()

    assert explain.state_id == "explain"
    assert message.instructions == {
        "text": (
            "Explain the difference using separate mistake, reason, and "
            "minimal_fix fields. The minimal_fix must satisfy the governing "
            "instructions."
        ),
        "answer": {
            "diagnosed": {
                "mistake": "nonempty string",
                "reason": "nonempty string",
                "minimal_fix": "nonempty string",
            }
        },
    }
    assert message.data["payload"] == {
        "enquiry": "What is one plus one?",
        "actual_answer": "3",
        "expected_answer": "2",
        "format_hint": {"answer": "integer"},
        "metadata": {"topic": "arithmetic"},
    }
    revision = message.data["state"]["revision"]
    terminal = voyage.next(
        {
            "revision": revision,
            "outcome": "diagnosed",
            "evidence": {
                "mistake": "The answer is too large.",
                "reason": "One plus one equals two.",
                "minimal_fix": "Replace 3 with 2.",
            },
        },
        continue_=True,
    )

    assert terminal.state_id == "complete-different"
    assert terminal.condition == "terminal"
    assert _completed_result(voyage) == rutter.RunResult(
        "different",
        {
            "case_id": "sum-check",
            "actual_answer": "3",
            "expected_answer": "2",
            "decided_by": "evaluator",
            "detail": {
                "mistake": "The answer is too large.",
                "reason": "One plus one equals two.",
                "minimal_fix": "Replace 3 with 2.",
            },
        },
    )


def test_diagnose_answer_without_evaluator_asks_exact_comparison_then_finishes_yes(
    tmp_path: Path,
) -> None:
    """Guessing semantic equality or combining compare and explain must fail."""

    case = rutter.DiagnosisCase(_question(), "two", None)
    voyage = rutter.RutterRegistry(
        {"diagnose": rutter.DiagnoseAnswer}, tmp_path
    ).create("diagnose", Path("compare-yes.reckoning.json"), case.to_json())

    compare = voyage.next(continue_=True)
    message = voyage.get_instruction()

    assert compare.state_id == "compare"
    assert message.instructions == {
        "text": (
            "Decide whether the actual and expected answers are semantically "
            "the same. Reply with explicit yes or no."
        ),
        "answer": {"yes": {}, "no": {}},
    }
    assert message.data["payload"] == {
        "enquiry": "What is one plus one?",
        "actual_answer": "two",
        "expected_answer": "2",
        "format_hint": {"answer": "integer"},
        "metadata": {"topic": "arithmetic"},
    }
    terminal = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "yes",
            "evidence": {},
        },
        continue_=True,
    )

    assert terminal.state_id == "complete-equal-llm"
    assert terminal.condition == "terminal"
    assert _completed_result(voyage) == rutter.RunResult(
        "equal",
        {
            "case_id": "sum-check",
            "actual_answer": "two",
            "expected_answer": "2",
            "decided_by": "llm",
            "detail": None,
        },
    )


def test_diagnose_answer_no_flow_preserves_turns_across_invalid_replies_and_reopen(
    tmp_path: Path,
) -> None:
    """Mutating an accepted prefix on either invalid reply or reopen must fail."""

    case = rutter.DiagnosisCase(_question(), "3", None)
    path = Path("compare-no.reckoning.json")
    registry = rutter.RutterRegistry({"diagnose": rutter.DiagnoseAnswer}, tmp_path)
    voyage = registry.create("diagnose", path, case.to_json())
    voyage.next(continue_=True)
    compare_message = voyage.get_instruction()
    before_invalid_compare = (tmp_path / path).read_bytes()
    invalid_compare = {
        "revision": compare_message.data["state"]["revision"],
        "outcome": "maybe",
        "evidence": {},
    }

    assert voyage.validate(invalid_compare).valid is False
    with pytest.raises(rutter.RutterValidationError):
        voyage.next(invalid_compare)
    assert (tmp_path / path).read_bytes() == before_invalid_compare

    voyage = registry.open(path)
    explain = voyage.next(
        {
            "revision": compare_message.data["state"]["revision"],
            "outcome": "no",
            "evidence": {},
        },
        continue_=True,
    )
    assert explain.state_id == "explain"
    explain_message = voyage.get_instruction()
    incomplete = {
        "revision": explain_message.data["state"]["revision"],
        "outcome": "diagnosed",
        "evidence": {"mistake": "Wrong.", "reason": "Not equal."},
    }
    before_incomplete = (tmp_path / path).read_bytes()

    report = voyage.validate(incomplete)
    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == ("invalid-diagnosis",)
    with pytest.raises(rutter.RutterValidationError):
        voyage.next(incomplete)
    assert (tmp_path / path).read_bytes() == before_incomplete

    voyage = registry.open(path)
    entered_done = voyage.next(
        {
            "revision": explain_message.data["state"]["revision"],
            "outcome": "diagnosed",
            "evidence": {
                "mistake": "The answer is too large.",
                "reason": "One plus one equals two.",
                "minimal_fix": "Replace 3 with 2.",
            },
        },
        continue_=False,
    )
    assert entered_done.state_id == "complete-different"
    assert entered_done.condition == "ready"

    voyage = registry.open(path)
    terminal = voyage.next(continue_=False)
    assert terminal.condition == "terminal"
    assert _completed_result(voyage).value["decided_by"] == "llm"
    history = rutter.HistoryView(voyage._store.read().root.history)
    assert tuple(turn.state_id for turn in history.turns()) == ("compare", "explain")


def test_ask_and_diagnose_has_exact_ask_envelope_and_answer_validation(
    tmp_path: Path,
) -> None:
    """Leaking gold or accepting a non-string answer at the ask boundary must fail."""

    path = Path("ask-envelope.reckoning.json")
    voyage = rutter.RutterRegistry(
        {"ask": rutter.AskAndDiagnose}, tmp_path
    ).create("ask", path, _question().to_json())
    message = voyage.get_instruction()

    assert rutter.AskAndDiagnose.evaluator is None
    assert message.instructions == {
        "text": "Answer the enquiry using the optional format hint.",
        "answer": {"answered": {"answer": "string"}},
    }
    assert message.data["payload"] == {
        "enquiry": "What is one plus one?",
        "format_hint": {"answer": "integer"},
        "metadata": {"topic": "arithmetic"},
    }
    invalid = {
        "revision": message.data["state"]["revision"],
        "outcome": "answered",
        "evidence": {"answer": 2},
    }
    before = (tmp_path / path).read_bytes()

    report = voyage.validate(invalid)

    assert report.valid is False
    assert tuple(issue.code for issue in report.issues) == ("invalid-answer",)
    with pytest.raises(rutter.RutterValidationError):
        voyage.next(invalid)
    assert (tmp_path / path).read_bytes() == before


def test_ask_and_diagnose_builds_turn_based_child_call_and_forwards_result(
    tmp_path: Path,
) -> None:
    """Hiding the Call or sourcing the answer outside the accepted Turn must fail."""

    path = Path("ask-call.reckoning.json")
    registry = rutter.RutterRegistry({"ask": rutter.AskAndDiagnose}, tmp_path)
    voyage = registry.create("ask", path, _question().to_json())
    ask_message = voyage.get_instruction()
    parent_call = voyage.next(
        {
            "revision": ask_message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "two"},
        },
        continue_=False,
    )
    assert parent_call.state_id == "diagnose"
    assert parent_call.depth == 0

    child = registry.open(path).next(continue_=False)
    persisted = registry.open(path)._store.read()
    assert child.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child.state_id == "route"
    assert child.depth == 1
    assert persisted.root.active_child is not None
    assert persisted.root.active_child.kind == "explicit_call"
    assert persisted.root.active_child.site == "diagnose"
    assert persisted.root.active_child.run.charter == rutter.Charter(
        rutter.DiagnosisCase(_question(), "two", None).to_json()
    )

    voyage = registry.open(path)
    compare = voyage.next(continue_=True)
    assert compare.state_id == "compare"
    compare_message = voyage.get_instruction()
    terminal = voyage.next(
        {
            "revision": compare_message.data["state"]["revision"],
            "outcome": "yes",
            "evidence": {},
        },
        continue_=True,
    )

    assert terminal.rutter_id == rutter.AskAndDiagnose.rutter_id
    assert terminal.state_id == "complete"
    assert terminal.condition == "terminal"
    result = _completed_result(voyage)
    assert result == rutter.RunResult(
        "equal",
        {
            "case_id": "sum-check",
            "actual_answer": "two",
            "expected_answer": "2",
            "decided_by": "llm",
            "detail": None,
        },
    )
    root_history = rutter.HistoryView(
        voyage._store.read().root.history,
        voyage._store.read().completed_runs,
    )
    assert root_history.require_latest_call("diagnose").result == result


def test_ask_and_diagnose_concrete_subclass_seals_exact_evaluator_verdict(
    tmp_path: Path,
) -> None:
    """Ignoring an application-owned evaluator or persisting it outside Charter must fail."""

    seen: list[tuple[str, str, rutter.StateContext]] = []

    class MechanicalAsk(rutter.AskAndDiagnose):
        rutter_id = "mechanical-ask"
        definition_version = 1

        @staticmethod
        def evaluator(
            actual_answer: str,
            expected_answer: str,
            context: rutter.StateContext,
        ) -> bool:
            seen.append((actual_answer, expected_answer, context))
            return actual_answer == expected_answer

    voyage = rutter.RutterRegistry({"ask": MechanicalAsk}, tmp_path).create(
        "ask", Path("mechanical-ask.reckoning.json"), _question().to_json()
    )
    message = voyage.get_instruction()
    terminal = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "2"},
        },
        continue_=True,
    )

    assert terminal.rutter_id == MechanicalAsk.rutter_id
    assert terminal.condition == "terminal"
    assert len(seen) == 1
    assert seen[0][0:2] == ("2", "2")
    assert seen[0][2].state_id == "diagnose"
    assert _completed_result(voyage).value["decided_by"] == "evaluator"
    reckoning = voyage._store.read()
    call = rutter.HistoryView(
        reckoning.root.history, reckoning.completed_runs
    ).require_latest_call("diagnose")
    assert call.completed.history.turns() == ()
    assert call.completed.history.entries()[0].result.outcome == "equal"


@pytest.mark.parametrize("own_id", (False, True))
def test_evaluator_subclass_must_own_both_identity_fields(
    tmp_path: Path,
    own_id: bool,
) -> None:
    """Borrowing either library identity field for changed policy must fail."""

    attributes = {
        "evaluator": staticmethod(lambda actual, expected, context: True),
    }
    if own_id:
        attributes["rutter_id"] = "partly-owned-evaluator"
    else:
        attributes["definition_version"] = 2
    BorrowedIdentity = type(
        "BorrowedIdentity",
        (rutter.AskAndDiagnose,),
        attributes,
    )

    with pytest.raises(rutter.RutterDefinitionError) as caught:
        rutter.RutterRegistry({"bad": BorrowedIdentity}, tmp_path)
    assert caught.value.__cause__ is not None
    assert "own rutter_id and definition_version" in str(caught.value.__cause__)


def test_evaluator_subclass_requires_exact_three_argument_signature(
    tmp_path: Path,
) -> None:
    """Deferring an unusable evaluator signature until child push must fail."""

    class WrongSignature(rutter.AskAndDiagnose):
        rutter_id = "wrong-evaluator-signature"
        definition_version = 1

        @staticmethod
        def evaluator(actual_answer: str, expected_answer: str) -> bool:
            return actual_answer == expected_answer

    with pytest.raises(rutter.RutterDefinitionError) as caught:
        rutter.RutterRegistry({"bad": WrongSignature}, tmp_path)
    assert caught.value.__cause__ is not None
    assert "exactly 3 arguments" in str(caught.value.__cause__)


def test_truthy_evaluator_fault_preserves_the_accepted_ask_turn(
    tmp_path: Path,
) -> None:
    """Coercing a truthy evaluator value or rolling back the parent Turn must fail."""

    class TruthyEvaluator(rutter.AskAndDiagnose):
        rutter_id = "truthy-evaluator"
        definition_version = 1

        @staticmethod
        def evaluator(actual: str, expected: str, context: rutter.StateContext) -> int:
            del actual, expected, context
            return 1

    path = Path("truthy-evaluator.reckoning.json")
    registry = rutter.RutterRegistry({"ask": TruthyEvaluator}, tmp_path)
    voyage = registry.create("ask", path, _question().to_json())
    message = voyage.get_instruction()

    fault = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "2"},
        },
        continue_=True,
    )

    assert fault.state_id == "diagnose"
    assert fault.condition == "fault"
    persisted = registry.open(path)._store.read()
    assert persisted.fault["category"] == "child-charter"
    assert persisted.root.active_child is None
    assert persisted.completed_runs == {}
    turns = rutter.HistoryView(persisted.root.history).turns("ask")
    assert len(turns) == 1
    assert turns[0].response.evidence == {"answer": "2"}


def test_ask_and_diagnose_reopens_at_child_push_done_and_return_boundaries(
    tmp_path: Path,
) -> None:
    """Combining any nested-call restart seam or replaying the evaluator must fail."""

    evaluations: list[str] = []

    class ReopenAsk(rutter.AskAndDiagnose):
        rutter_id = "reopen-ask"
        definition_version = 1

        @staticmethod
        def evaluator(actual: str, expected: str, context: rutter.StateContext) -> bool:
            del expected, context
            evaluations.append(actual)
            return True

    path = Path("ask-reopen.reckoning.json")
    registry = rutter.RutterRegistry({"ask": ReopenAsk}, tmp_path)
    voyage = registry.create("ask", path, _question().to_json())
    message = voyage.get_instruction()
    at_call = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "2"},
        },
        continue_=False,
    )
    assert at_call.state_id == "diagnose"

    child_route = registry.open(path).next(continue_=False)
    assert child_route.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child_route.state_id == "route"
    assert evaluations == ["2"]

    child_done = registry.open(path).next(continue_=False)
    assert child_done.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child_done.state_id == "complete-equal-evaluator"
    assert child_done.condition == "ready"

    child_terminal = registry.open(path).next(continue_=False)
    assert child_terminal.rutter_id == rutter.DiagnoseAnswer.rutter_id
    assert child_terminal.condition == "terminal"

    parent_done = registry.open(path).next(continue_=False)
    assert parent_done.rutter_id == ReopenAsk.rutter_id
    assert parent_done.state_id == "complete"
    assert parent_done.condition == "ready"
    assert evaluations == ["2"]

    terminal = registry.open(path).next(continue_=False)
    assert terminal.rutter_id == ReopenAsk.rutter_id
    assert terminal.condition == "terminal"
    assert evaluations == ["2"]


def test_diagnose_answer_on_seals_extracted_answer_and_exact_evaluator_verdict() -> None:
    """Deferring extraction/evaluation into the child or hiding parent work must fail."""

    seen: list[rutter.EdgeContext] = []

    def actual_answer(context: rutter.EdgeContext) -> str:
        assert isinstance(context.record, rutter.Turn)
        assert context.record.response is not None
        return context.record.response.evidence["answer"]

    def evaluator(
        actual: str,
        expected: str,
        context: rutter.EdgeContext,
    ) -> bool:
        seen.append(context)
        return actual == expected

    maker = rutter.diagnose_answer_on(
        id="answer-check",
        on=rutter.after("answer"),
        question=_question(),
        actual_answer=actual_answer,
        evaluator=evaluator,
    )
    context = _edge_context()

    assert maker.id == "answer-check"
    assert maker.on == rutter.after("answer")
    assert maker.child is rutter.DiagnoseAnswer
    assert maker.charter(context) == rutter.DiagnosisCase(
        _question(), "2", True
    ).to_json()
    assert seen == [context]


def test_ask_and_diagnose_on_resolves_question_into_explicit_child_charter() -> None:
    """Using the base child or resolving the question after child start must fail."""

    seen: list[rutter.EdgeContext] = []

    class HookAsk(rutter.AskAndDiagnose):
        rutter_id = "hook-ask"
        definition_version = 1

        @staticmethod
        def evaluator(actual: str, expected: str, context: rutter.StateContext) -> bool:
            del context
            return actual == expected

    def question(context: rutter.EdgeContext) -> object:
        seen.append(context)
        return _question()

    maker = rutter.ask_and_diagnose_on(
        id="fresh-check",
        on=rutter.after("answer"),
        question=question,
        child=HookAsk,
    )
    context = _edge_context()

    assert maker.id == "fresh-check"
    assert maker.child is HookAsk
    assert maker.charter(context) == _question().to_json()
    assert seen == [context]


def test_case_sequence_after_snapshots_configuration_and_filters_source_state() -> None:
    """Reading mutable sources later or scheduling after an unselected state must fail."""

    states = {"answer", "revise"}
    items = [{"step": [1]}, {"step": [2]}]
    maker = rutter.case_sequence_after(
        id="progressive-checks",
        after_states=states,
        items=items,
        child=SequenceChild,
    )
    states.clear()
    items[0]["step"].append(99)
    items.append({"step": [3]})

    assert maker.id == "progressive-checks"
    assert maker.on == rutter.EdgeMatch()
    assert maker.child is SequenceChild
    assert maker.charter(_edge_context(source="answer")) == {"step": (1,)}
    assert maker.charter(_edge_context(source="other")) is None
    with pytest.raises(TypeError):
        maker.charter(_edge_context(source="answer"))["step"] += (2,)


def test_case_sequence_after_derives_position_exhaustion_and_overrun_from_attached_calls() -> None:
    """Counting explicit calls, repeating exhausted items, or hiding overrun must fail."""

    maker = rutter.case_sequence_after(
        id="progressive-checks",
        after_states={"answer"},
        items=({"step": 1}, {"step": 2}),
        child=SequenceChild,
    )

    assert maker.charter(
        _edge_context(history=_sequence_history(1))
    ) == {"step": 2}
    assert maker.charter(
        _edge_context(history=_sequence_history(0, include_explicit_collision=True))
    ) == {"step": 1}
    assert maker.charter(_edge_context(history=_sequence_history(2))) is None
    with pytest.raises(
        rutter.RutterStateError, match="history-inconsistency"
    ) as overrun:
        maker.charter(_edge_context(history=_sequence_history(3)))
    assert overrun.value.category == "history-inconsistency"


def test_case_sequence_after_rejects_duplicate_maker_edge_history() -> None:
    """Treating duplicate maker/edge results as two consumed items must fail."""

    maker = rutter.case_sequence_after(
        id="progressive-checks",
        after_states={"answer"},
        items=({"step": 1}, {"step": 2}, {"step": 3}),
        child=SequenceChild,
    )

    with pytest.raises(
        rutter.RutterStateError, match="history-inconsistency"
    ) as duplicate:
        maker.charter(
            _edge_context(history=_sequence_history(2, duplicate_edge=True))
        )
    assert duplicate.value.category == "history-inconsistency"


def test_case_sequence_after_custom_builder_receives_frozen_item_and_rejects_malformed_charter() -> None:
    """Passing mutable items or allowing nonfinite resolved Charter output must fail."""

    seen: list[tuple[object, rutter.EdgeContext]] = []

    def build(item: object, context: rutter.EdgeContext) -> object:
        seen.append((item, context))
        return {"selected": item, "edge": context.edge["edge_id"]}

    context = _edge_context(source="answer")
    maker = rutter.case_sequence_after(
        id="custom-sequence",
        after_states={"answer"},
        items=({"step": [1]},),
        child=SequenceChild,
        charter=build,
    )

    assert maker.charter(context) == {
        "selected": {"step": (1,)},
        "edge": "edge-answer",
    }
    assert seen == [({"step": (1,)}, context)]
    with pytest.raises(TypeError):
        seen[0][0]["step"] += (2,)

    malformed = rutter.case_sequence_after(
        id="malformed-sequence",
        after_states={"answer"},
        items=({},),
        child=SequenceChild,
        charter=lambda item, edge: {"unsupported": object()},
    )
    with pytest.raises(rutter.RutterDefinitionError, match="finite JSON"):
        malformed.charter(context)


def test_non_diagnostic_sequence_advances_once_per_completed_attachment_across_reopen(
    tmp_path: Path,
) -> None:
    """Persisting an index or repeating/skipping a child across restart must fail."""

    maker = rutter.case_sequence_after(
        id="progressive-checks",
        after_states={"first", "second"},
        items=({"step": 1}, {"step": 2}),
        child=SequenceChild,
    )

    class SequencedParent(rutter.Rutter):
        rutter_id = "sequenced-parent"
        definition_version = 1
        start_state = "first"

        def define_states(self) -> dict[str, object]:
            return {
                "first": rutter.Action(
                    lambda context: rutter.ActionResult("advanced", 1),
                    mode="pure",
                    then="second",
                ),
                "second": rutter.Action(
                    lambda context: rutter.ActionResult("advanced", 2),
                    mode="pure",
                    then="complete",
                ),
                "complete": rutter.Done(rutter.RunResult("finished", {})),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (maker,)

    path = Path("non-diagnostic-sequence.reckoning.json")
    registry = rutter.RutterRegistry({"parent": SequencedParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    first_child = voyage.next(continue_=False)
    assert first_child.rutter_id == SequenceChild.rutter_id
    assert registry.open(path)._store.read().root.active_child.run.charter == rutter.Charter(
        {"step": 1}
    )
    assert registry.open(path).next(continue_=False).condition == "terminal"

    second_parent = registry.open(path).next(continue_=False)
    assert second_parent.rutter_id == SequencedParent.rutter_id
    assert second_parent.state_id == "second"
    second_child = registry.open(path).next(continue_=False)
    assert second_child.rutter_id == SequenceChild.rutter_id
    assert registry.open(path)._store.read().root.active_child.run.charter == rutter.Charter(
        {"step": 2}
    )
    assert registry.open(path).next(continue_=False).condition == "terminal"

    parent_done = registry.open(path).next(continue_=False)
    assert parent_done.state_id == "complete"
    assert parent_done.condition == "ready"
    terminal = registry.open(path).next(continue_=False)
    assert terminal.condition == "terminal"
    reckoning = registry.open(path)._store.read()
    history = rutter.HistoryView(reckoning.root.history, reckoning.completed_runs)
    assert tuple(call.site for call in history.attached_calls()) == (
        "progressive-checks",
        "progressive-checks",
    )


def test_fresh_question_sequence_uses_application_evaluator_subclass(
    tmp_path: Path,
) -> None:
    """Replacing the selected evaluator child with base LLM comparison must fail."""

    class FreshEvaluatorAsk(rutter.AskAndDiagnose):
        rutter_id = "fresh-evaluator-ask"
        definition_version = 1

        @staticmethod
        def evaluator(actual: str, expected: str, context: rutter.StateContext) -> bool:
            del context
            return actual == expected

    maker = rutter.case_sequence_after(
        id="fresh-questions",
        after_states={"prepare"},
        items=(_question().to_json(),),
        child=FreshEvaluatorAsk,
    )

    class FreshQuestionParent(rutter.Rutter):
        rutter_id = "fresh-question-parent"
        definition_version = 1
        start_state = "prepare"

        def define_states(self) -> dict[str, object]:
            return {
                "prepare": rutter.Action(
                    lambda context: rutter.ActionResult("prepared", {}),
                    mode="pure",
                    then="complete",
                ),
                "complete": rutter.Done(rutter.RunResult("finished", {})),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (maker,)

    path = Path("fresh-question-sequence.reckoning.json")
    registry = rutter.RutterRegistry({"parent": FreshQuestionParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    child_prompt = voyage.next(continue_=True)
    message = voyage.get_instruction()
    assert child_prompt.rutter_id == FreshEvaluatorAsk.rutter_id
    assert child_prompt.state_id == "ask"
    assert "expected_answer" not in message.data["payload"]

    terminal = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "2"},
        },
        continue_=True,
    )
    assert terminal.rutter_id == FreshQuestionParent.rutter_id
    assert terminal.condition == "terminal"
    reckoning = voyage._store.read()
    attached = rutter.HistoryView(
        reckoning.root.history, reckoning.completed_runs
    ).attached_calls(case_maker_id="fresh-questions")
    assert len(attached) == 1
    assert attached[0].result.value["decided_by"] == "evaluator"


@pytest.mark.parametrize("failure", ("extractor", "evaluator", "truthy"))
def test_diagnose_answer_on_failure_preserves_accepted_source_turn(
    tmp_path: Path,
    failure: str,
) -> None:
    """Rolling back accepted parent work after provider failure must fail."""

    def actual_answer(context: rutter.EdgeContext) -> str:
        if failure == "extractor":
            raise RuntimeError("private extractor detail")
        assert isinstance(context.record, rutter.Turn)
        assert context.record.response is not None
        return context.record.response.evidence["answer"]

    def evaluator(actual: str, expected: str, context: rutter.EdgeContext) -> object:
        del context
        if failure == "evaluator":
            raise RuntimeError("private evaluator detail")
        if failure == "truthy":
            return 1
        return actual == expected

    maker = rutter.diagnose_answer_on(
        id="accepted-answer-check",
        on=rutter.after("answer"),
        question=_question(),
        actual_answer=actual_answer,
        evaluator=evaluator,
    )

    class FailingProviderParent(rutter.Rutter):
        rutter_id = f"failing-provider-{failure}"
        definition_version = 1
        start_state = "answer"

        def define_states(self) -> dict[str, object]:
            return {
                "answer": rutter.Prompt(
                    "Answer.",
                    answer=rutter.AnswerSpec({"answered": {"answer": "string"}}),
                    then="complete",
                ),
                "complete": rutter.Done(rutter.RunResult("finished", {})),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (maker,)

    path = Path(f"provider-{failure}.reckoning.json")
    registry = rutter.RutterRegistry({"parent": FailingProviderParent}, tmp_path)
    voyage = registry.create("parent", path, {})
    message = voyage.get_instruction()

    fault = voyage.next(
        {
            "revision": message.data["state"]["revision"],
            "outcome": "answered",
            "evidence": {"answer": "2"},
        },
        continue_=True,
    )

    assert fault.state_id == "answer"
    assert fault.condition == "fault"
    persisted = registry.open(path)._store.read()
    assert persisted.fault["category"] == "case-charter"
    assert persisted.root.active_child is None
    assert persisted.completed_runs == {}
    turns = rutter.HistoryView(persisted.root.history).turns("answer")
    assert len(turns) == 1
    assert turns[0].response.evidence == {"answer": "2"}
