"""Standard diagnostic values and ordinary Rutter compositions."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import KW_ONLY, dataclass, field
from inspect import Parameter, signature
from math import isfinite
import re
from types import MappingProxyType
from typing import Callable, Mapping

from officina.rutter.hooks import CaseMaker, EdgeMatch
from officina.rutter.model import (
    Action,
    ActionContext,
    ActionResult,
    AnswerContext,
    AnswerSpec,
    Call,
    Done,
    EdgeContext,
    JsonObject,
    JsonValue,
    Prompt,
    RunResult,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    StateContext,
    ValidationIssue,
    ValidationReport,
)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class _HistoryInconsistency(RutterStateError):
    category = "history-inconsistency"


def _require_id(value: object, label: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}:
        raise RutterDefinitionError(f"{label} must be a nonempty stable token")
    return value


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise RutterDefinitionError(f"{label} must be a nonempty string")
    return value


def _require_callable_arity(callback: object, arity: int, label: str) -> None:
    if not callable(callback):
        raise RutterDefinitionError(f"{label} must be callable")
    try:
        parameters = tuple(signature(callback).parameters.values())
    except (TypeError, ValueError) as exc:
        raise RutterDefinitionError(
            f"{label} must have an inspectable signature"
        ) from exc
    if (
        len(parameters) != arity
        or any(
            parameter.kind
            not in {
                Parameter.POSITIONAL_ONLY,
                Parameter.POSITIONAL_OR_KEYWORD,
            }
            or parameter.default is not Parameter.empty
            for parameter in parameters
        )
    ):
        noun = "argument" if arity == 1 else "arguments"
        raise RutterDefinitionError(
            f"{label} must accept exactly {arity} {noun}"
        )


def _freeze_json(
    value: object, *, error: type[Exception] = RutterDefinitionError
) -> JsonValue:
    if value is None or type(value) in {bool, int, str}:
        return value  # type: ignore[return-value]
    if type(value) is float:
        if not isfinite(value):
            raise error("value must be finite JSON")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise error("finite JSON object keys must be strings")
            frozen[key] = _freeze_json(item, error=error)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, error=error) for item in value)
    raise error("value must be finite JSON")


def _freeze_object(
    value: object, label: str, *, error: type[Exception] = RutterDefinitionError
) -> JsonObject:
    if not isinstance(value, Mapping):
        raise error(f"{label} must be a finite JSON object")
    frozen = _freeze_json(value, error=error)
    assert isinstance(frozen, Mapping)
    return frozen


@dataclass(frozen=True)
class QuestionCase:
    case_id: str
    enquiry: str
    expected_answer: str
    _: KW_ONLY
    format_hint: JsonValue = None
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.case_id, "case_id")
        _require_text(self.enquiry, "enquiry")
        _require_text(self.expected_answer, "expected_answer")
        object.__setattr__(self, "format_hint", _freeze_json(self.format_hint))
        object.__setattr__(self, "metadata", _freeze_object(self.metadata, "metadata"))

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "case_id": self.case_id,
                "enquiry": self.enquiry,
                "expected_answer": self.expected_answer,
                "format_hint": self.format_hint,
                "metadata": self.metadata,
            },
            "QuestionCase",
        )

    @classmethod
    def from_json(cls, value: object) -> QuestionCase:
        expected = {
            "case_id",
            "enquiry",
            "expected_answer",
            "format_hint",
            "metadata",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RutterStateError("QuestionCase has invalid fields")
        try:
            return cls(
                value["case_id"],
                value["enquiry"],
                value["expected_answer"],
                format_hint=value["format_hint"],
                metadata=value["metadata"],
            )
        except (RutterDefinitionError, TypeError, ValueError) as exc:
            raise RutterStateError(str(exc)) from exc


@dataclass(frozen=True)
class DiagnosisCase:
    question: QuestionCase
    actual_answer: str
    precomputed_verdict: bool | None = None
    ask_for_fix: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.question, QuestionCase):
            raise RutterDefinitionError("question must be a QuestionCase")
        if type(self.actual_answer) is not str:
            raise RutterDefinitionError("actual_answer must be an exact string")
        if self.precomputed_verdict is not None and type(
            self.precomputed_verdict
        ) is not bool:
            raise RutterDefinitionError(
                "precomputed_verdict must be an exact Boolean or null"
            )
        if type(self.ask_for_fix) is not bool:
            raise RutterDefinitionError("ask_for_fix must be an exact Boolean")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "question": self.question.to_json(),
                "actual_answer": self.actual_answer,
                "precomputed_verdict": self.precomputed_verdict,
                "ask_for_fix": self.ask_for_fix,
            },
            "DiagnosisCase",
        )

    @classmethod
    def from_json(cls, value: object) -> DiagnosisCase:
        expected = {
            "question",
            "actual_answer",
            "precomputed_verdict",
            "ask_for_fix",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RutterStateError("DiagnosisCase has invalid fields")
        try:
            return cls(
                QuestionCase.from_json(value["question"]),
                value["actual_answer"],
                value["precomputed_verdict"],
                value["ask_for_fix"],
            )
        except (RutterDefinitionError, TypeError, ValueError) as exc:
            raise RutterStateError(str(exc)) from exc


@dataclass(frozen=True)
class DiagnosisDetail:
    mistake: str
    reason: str
    minimal_fix: str

    def __post_init__(self) -> None:
        _require_text(self.mistake, "mistake")
        _require_text(self.reason, "reason")
        _require_text(self.minimal_fix, "minimal_fix")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "mistake": self.mistake,
                "reason": self.reason,
                "minimal_fix": self.minimal_fix,
            },
            "DiagnosisDetail",
        )

    @classmethod
    def from_json(cls, value: object) -> DiagnosisDetail:
        expected = {"mistake", "reason", "minimal_fix"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RutterStateError("DiagnosisDetail has invalid fields")
        try:
            return cls(value["mistake"], value["reason"], value["minimal_fix"])
        except (RutterDefinitionError, TypeError, ValueError) as exc:
            raise RutterStateError(str(exc)) from exc


class DiagnoseAnswer(Rutter):
    rutter_id = "diagnose-answer"
    definition_version = 2
    start_state = "route"

    @staticmethod
    def _route(context: ActionContext) -> ActionResult:
        case = DiagnosisCase.from_json(context.state.charter.data)
        if case.precomputed_verdict is True:
            return ActionResult("equal", None)
        if case.precomputed_verdict is False:
            return ActionResult("different", None)
        return ActionResult("compare", None)

    @staticmethod
    def _prompt_data(context: StateContext) -> JsonObject:
        case = DiagnosisCase.from_json(context.charter.data)
        return _freeze_object(
            {
                "enquiry": case.question.enquiry,
                "actual_answer": case.actual_answer,
                "expected_answer": case.question.expected_answer,
                "format_hint": case.question.format_hint,
                "metadata": case.question.metadata,
                "ask_for_fix": case.ask_for_fix,
            },
            "diagnostic payload",
        )

    @staticmethod
    def _validate_detail(context: AnswerContext) -> ValidationReport:
        try:
            DiagnosisDetail.from_json(context.response.evidence)
        except RutterStateError:
            return ValidationReport(
                False,
                (
                    ValidationIssue(
                        ("evidence",),
                        "invalid-diagnosis",
                        "diagnosis evidence must contain nonempty mistake, reason, and minimal_fix strings",
                    ),
                ),
            )
        return ValidationReport(True)

    @staticmethod
    def _equal_evaluator(context: StateContext) -> RunResult:
        case = DiagnosisCase.from_json(context.charter.data)
        return RunResult(
            "equal",
            {
                "case_id": case.question.case_id,
                "actual_answer": case.actual_answer,
                "expected_answer": case.question.expected_answer,
                "decided_by": "evaluator",
                "detail": None,
            },
        )

    @staticmethod
    def _equal_llm(context: StateContext) -> RunResult:
        case = DiagnosisCase.from_json(context.charter.data)
        return RunResult(
            "equal",
            {
                "case_id": case.question.case_id,
                "actual_answer": case.actual_answer,
                "expected_answer": case.question.expected_answer,
                "decided_by": "llm",
                "detail": None,
            },
        )

    @staticmethod
    def _different(context: StateContext) -> RunResult:
        case = DiagnosisCase.from_json(context.charter.data)
        turn = context.history.require_latest_turn("explain")
        assert turn.response is not None
        detail = DiagnosisDetail.from_json(turn.response.evidence)
        return RunResult(
            "different",
            {
                "case_id": case.question.case_id,
                "actual_answer": case.actual_answer,
                "expected_answer": case.question.expected_answer,
                "decided_by": (
                    "evaluator"
                    if case.precomputed_verdict is not None
                    else "llm"
                ),
                "detail": detail.to_json(),
            },
        )

    def define_states(self) -> Mapping[str, object]:
        return {
            "route": Action(
                self._route,
                mode="pure",
                then={
                    "equal": "complete-equal-evaluator",
                    "different": "explain",
                    "compare": "compare",
                },
            ),
            "compare": Prompt(
                (
                    "Decide whether the actual and expected answers are "
                    "semantically the same. Reply with explicit yes or no."
                ),
                answer=AnswerSpec({"yes": {}, "no": {}}),
                data=self._prompt_data,
                then={"yes": "complete-equal-llm", "no": "explain"},
            ),
            "explain": Prompt(
                (
                    "Explain the difference using separate mistake, reason, and "
                    "minimal_fix fields. The minimal_fix must satisfy the governing "
                    "instructions. If ask_for_fix is true, return the complete "
                    "corrected answer in minimal_fix, preserving the requested "
                    "format."
                ),
                answer=AnswerSpec(
                    {
                        "diagnosed": {
                            "mistake": "nonempty string",
                            "reason": "nonempty string",
                            "minimal_fix": "nonempty string",
                        }
                    }
                ),
                data=self._prompt_data,
                validate=self._validate_detail,
                then="complete-different",
            ),
            "complete-equal-evaluator": Done(self._equal_evaluator),
            "complete-equal-llm": Done(self._equal_llm),
            "complete-different": Done(self._different),
        }


class AskAndDiagnose(Rutter):
    rutter_id = "ask-and-diagnose"
    definition_version = 2
    start_state = "ask"
    evaluator: Callable[[str, str, StateContext], bool] | None = None

    @staticmethod
    def _ask_data(context: StateContext) -> JsonObject:
        question = QuestionCase.from_json(context.charter.data)
        return _freeze_object(
            {
                "enquiry": question.enquiry,
                "format_hint": question.format_hint,
                "metadata": question.metadata,
            },
            "ask payload",
        )

    @staticmethod
    def _validate_answer(context: AnswerContext) -> ValidationReport:
        evidence = context.response.evidence
        if set(evidence) == {"answer"} and type(evidence["answer"]) is str:
            return ValidationReport(True)
        return ValidationReport(
            False,
            (
                ValidationIssue(
                    ("evidence",),
                    "invalid-answer",
                    "answer evidence must contain exactly one string field named answer",
                ),
            ),
        )

    def _diagnosis_charter(self, context: StateContext) -> JsonObject:
        question = QuestionCase.from_json(context.charter.data)
        turn = context.history.require_latest_turn("ask")
        response = turn.response
        if (
            response is None
            or response.outcome != "answered"
            or set(response.evidence) != {"answer"}
            or type(response.evidence["answer"]) is not str
        ):
            raise RutterDefinitionError(
                "diagnose Call requires the latest accepted ask/answered Turn"
            )
        actual_answer = response.evidence["answer"]
        verdict = None
        if self.evaluator is not None:
            verdict = self.evaluator(
                actual_answer,
                question.expected_answer,
                context,
            )
        return DiagnosisCase(question, actual_answer, verdict).to_json()

    @staticmethod
    def _forward_result(context: StateContext) -> RunResult:
        return context.history.require_latest_call("diagnose").result

    def define_states(self) -> Mapping[str, object]:
        owner = type(self)
        if self.evaluator is not None and (
            "rutter_id" not in owner.__dict__
            or "definition_version" not in owner.__dict__
        ):
            raise RutterDefinitionError(
                "an evaluator subclass must own rutter_id and definition_version"
            )
        if self.evaluator is not None:
            _require_callable_arity(
                self.evaluator,
                3,
                "AskAndDiagnose evaluator",
            )
        return {
            "ask": Prompt(
                "Answer the enquiry using the optional format hint.",
                answer=AnswerSpec({"answered": {"answer": "string"}}),
                data=self._ask_data,
                validate=self._validate_answer,
                then="diagnose",
            ),
            "diagnose": Call(
                DiagnoseAnswer,
                charter=self._diagnosis_charter,
                then="complete",
            ),
            "complete": Done(self._forward_result),
        }


def diagnose_answer_on(
    *,
    id: str,
    on: EdgeMatch,
    question: QuestionCase | Callable[[EdgeContext], QuestionCase],
    actual_answer: Callable[[EdgeContext], str],
    evaluator: Callable[[str, str, EdgeContext], bool] | None = None,
    ask_for_fix: bool = False,
) -> CaseMaker:
    if type(ask_for_fix) is not bool:
        raise RutterDefinitionError("ask_for_fix must be an exact Boolean")
    if isinstance(question, QuestionCase):
        fixed_question = QuestionCase.from_json(question.to_json())

        def resolve_question(context: EdgeContext) -> QuestionCase:
            del context
            return fixed_question

    else:
        _require_callable_arity(question, 1, "question provider")
        resolve_question = question
    _require_callable_arity(actual_answer, 1, "actual_answer provider")
    if evaluator is not None:
        _require_callable_arity(evaluator, 3, "diagnostic evaluator")

    def build(context: EdgeContext) -> JsonObject:
        resolved = resolve_question(context)
        if not isinstance(resolved, QuestionCase):
            raise RutterDefinitionError(
                "question provider must return a QuestionCase"
            )
        actual = actual_answer(context)
        if type(actual) is not str:
            raise RutterDefinitionError(
                "actual_answer provider must return an exact string"
            )
        verdict = None
        if evaluator is not None:
            verdict = evaluator(actual, resolved.expected_answer, context)
            if type(verdict) is not bool:
                raise RutterDefinitionError(
                    "diagnostic evaluator must return an exact Boolean"
                )
        return DiagnosisCase(
            resolved,
            actual,
            verdict,
            ask_for_fix=ask_for_fix,
        ).to_json()

    return CaseMaker(id, on=on, child=DiagnoseAnswer, charter=build)


def ask_and_diagnose_on(
    *,
    id: str,
    on: EdgeMatch,
    question: QuestionCase | Callable[[EdgeContext], QuestionCase],
    child: type[AskAndDiagnose] = AskAndDiagnose,
) -> CaseMaker:
    if not isinstance(child, type) or not issubclass(child, AskAndDiagnose):
        raise RutterDefinitionError(
            "ask_and_diagnose_on child must be an AskAndDiagnose class"
        )
    if isinstance(question, QuestionCase):
        fixed_question = QuestionCase.from_json(question.to_json())

        def resolve_question(context: EdgeContext) -> QuestionCase:
            del context
            return fixed_question

    else:
        _require_callable_arity(question, 1, "question provider")
        resolve_question = question

    def build(context: EdgeContext) -> JsonObject:
        resolved = resolve_question(context)
        if not isinstance(resolved, QuestionCase):
            raise RutterDefinitionError(
                "question provider must return a QuestionCase"
            )
        return resolved.to_json()

    return CaseMaker(id, on=on, child=child, charter=build)


def case_sequence_after(
    *,
    id: str,
    after_states: Collection[str],
    items: Sequence[JsonObject],
    child: type[Rutter],
    charter: Callable[[JsonObject, EdgeContext], JsonObject] | None = None,
) -> CaseMaker:
    _require_id(id, "CaseMaker")
    if isinstance(after_states, (str, bytes)) or not isinstance(
        after_states, Collection
    ):
        raise RutterDefinitionError("after_states must be a collection of state IDs")
    frozen_states = frozenset(
        _require_id(state, "after state") for state in after_states
    )
    if not frozen_states:
        raise RutterDefinitionError("after_states must not be empty")
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise RutterDefinitionError("items must be a sequence of JSON objects")
    frozen_items = tuple(
        _freeze_object(item, "sequence item") for item in items
    )
    if not frozen_items:
        raise RutterDefinitionError("items must not be empty")
    if not isinstance(child, type) or not issubclass(child, Rutter):
        raise RutterDefinitionError("sequence child must be a Rutter class")
    if charter is not None:
        _require_callable_arity(charter, 2, "sequence Charter builder")

    def build(context: EdgeContext) -> JsonObject | None:
        source = context.edge.get("source")
        if source not in frozen_states:
            return None
        attached = context.state.history.attached_calls(case_maker_id=id)
        edge_ids = tuple(call.attached_to_edge_id for call in attached)
        if len(set(edge_ids)) != len(edge_ids):
            raise _HistoryInconsistency(
                "history-inconsistency: duplicate CaseMaker and edge identity"
            )
        index = len(attached)
        if index > len(frozen_items):
            raise _HistoryInconsistency(
                "history-inconsistency: sequence attachment count exceeds item count"
            )
        if index == len(frozen_items):
            return None
        selected = frozen_items[index]
        if charter is None:
            return _freeze_object(selected, "sequence Charter")
        return _freeze_object(
            charter(selected, context),
            "sequence Charter",
        )

    return CaseMaker(id, on=EdgeMatch(), child=child, charter=build)


__all__ = (
    "AskAndDiagnose",
    "DiagnoseAnswer",
    "DiagnosisCase",
    "DiagnosisDetail",
    "QuestionCase",
    "ask_and_diagnose_on",
    "case_sequence_after",
    "diagnose_answer_on",
)
