"""Standard diagnostic values and ordinary Rutter compositions."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import KW_ONLY, dataclass, field
from inspect import Parameter, signature
from math import isfinite
import re
from types import MappingProxyType
from typing import Callable, Mapping

from officina.rutter.authoring import (
    EvolutionContext,
    LLMStep,
    LLMResponseContext,
    MachineContext,
    MachineStep,
    Rutter,
    SubRutter,
    Terminal,
    TransitionContext,
    TransitionHook,
    TransitionMatch,
)
from officina.rutter.values import (
    JsonObject,
    JsonValue,
    MachineResult,
    RutterDefinitionError,
    RutterStateError,
    ValidationIssue,
    ValidationReport,
    VoyageResult,
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
    definition_version = 4
    initial_evolution_id = "route"

    @staticmethod
    def _route(context: MachineContext) -> MachineResult:
        case = DiagnosisCase.from_json(context.evolution.charter.data)
        if case.precomputed_verdict is True:
            return MachineResult("equal", None)
        if case.precomputed_verdict is False:
            return MachineResult("different", None)
        return MachineResult("compare", None)

    @staticmethod
    def _prompt_data(context: EvolutionContext) -> JsonObject:
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
    def _validate_detail(context: LLMResponseContext) -> ValidationReport:
        try:
            DiagnosisDetail.from_json(
                {
                    key: value
                    for key, value in context.response.items()
                    if key != "outcome"
                }
            )
        except RutterStateError:
            return ValidationReport(
                False,
                (
                    ValidationIssue(
                        (),
                        "invalid-diagnosis",
                        "diagnosis must contain nonempty mistake, reason, and minimal_fix strings",
                    ),
                ),
            )
        return ValidationReport(True)

    @staticmethod
    def _equal_evaluator(context: EvolutionContext) -> VoyageResult:
        case = DiagnosisCase.from_json(context.charter.data)
        return VoyageResult(
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
    def _equal_llm(context: EvolutionContext) -> VoyageResult:
        case = DiagnosisCase.from_json(context.charter.data)
        return VoyageResult(
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
    def _different(context: EvolutionContext) -> VoyageResult:
        case = DiagnosisCase.from_json(context.charter.data)
        turn = context.history.require_latest_turn("explain")
        assert turn.response is not None
        detail = DiagnosisDetail.from_json(
            {
                key: value
                for key, value in turn.response.items()
                if key != "outcome"
            }
        )
        return VoyageResult(
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

    def define_evolutions(self) -> Mapping[str, object]:
        return {
            "route": MachineStep(
                self._route,
                mode="pure",
                next_on_outcome={
                    "equal": "complete-equal-evaluator",
                    "different": "explain",
                    "compare": "compare",
                },
            ),
            "compare": LLMStep(
                (
                    "Decide whether the actual and expected answers are "
                    "semantically the same. Reply with explicit yes or no."
                ),
                response_schema={
                    "type": "object",
                    "properties": {"outcome": {"enum": ["yes", "no"]}},
                    "required": ["outcome"],
                    "additionalProperties": False,
                },
                data=self._prompt_data,
                next_on_outcome={"yes": "complete-equal-llm", "no": "explain"},
            ),
            "explain": LLMStep(
                (
                    "Explain the difference using separate mistake, reason, and "
                    "minimal_fix fields. The minimal_fix must satisfy the governing "
                    "instructions. If ask_for_fix is true, treat expected_answer as "
                    "the revealed truth and adjust your subsequent reasoning and work "
                    "path accordingly. Do not return that adjustment; return only the "
                    "three diagnostic fields."
                ),
                response_schema={
                    "type": "object",
                    "properties": {
                        "outcome": {"const": "diagnosed"},
                        "mistake": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                        "minimal_fix": {"type": "string", "minLength": 1},
                    },
                    "required": ["outcome", "mistake", "reason", "minimal_fix"],
                    "additionalProperties": False,
                },
                data=self._prompt_data,
                assess_response=self._validate_detail,
                next_on_outcome="complete-different",
            ),
            "complete-equal-evaluator": Terminal(
                result_constructor=self._equal_evaluator
            ),
            "complete-equal-llm": Terminal(result_constructor=self._equal_llm),
            "complete-different": Terminal(result_constructor=self._different),
        }


class AskAndDiagnose(Rutter):
    rutter_id = "ask-and-diagnose"
    definition_version = 3
    initial_evolution_id = "ask"
    evaluator: Callable[[str, str, EvolutionContext], bool] | None = None

    @staticmethod
    def _ask_data(context: EvolutionContext) -> JsonObject:
        question = QuestionCase.from_json(context.charter.data)
        return _freeze_object(
            {
                "enquiry": question.enquiry,
                "format_hint": question.format_hint,
                "metadata": question.metadata,
            },
            "ask payload",
        )

    def _diagnosis_charter(self, context: EvolutionContext) -> JsonObject:
        question = QuestionCase.from_json(context.charter.data)
        turn = context.history.require_latest_turn("ask")
        response = turn.response
        if (
            response is None
            or response.get("outcome") != "answered"
            or set(response) != {"outcome", "answer"}
            or type(response["answer"]) is not str
        ):
            raise RutterDefinitionError(
                "diagnose SubRutter requires the latest accepted ask/answered Turn"
            )
        actual_answer = response["answer"]
        verdict = None
        if self.evaluator is not None:
            verdict = self.evaluator(
                actual_answer,
                question.expected_answer,
                context,
            )
        return DiagnosisCase(question, actual_answer, verdict).to_json()

    @staticmethod
    def _forward_result(context: EvolutionContext) -> VoyageResult:
        return context.history.require_latest_subrutter(
            origin_evolution_id="diagnose"
        ).result

    def define_evolutions(self) -> Mapping[str, object]:
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
            "ask": LLMStep(
                "Answer the enquiry using the optional format hint.",
                response_schema={
                    "type": "object",
                    "properties": {
                        "outcome": {"const": "answered"},
                        "answer": {"type": "string"},
                    },
                    "required": ["outcome", "answer"],
                    "additionalProperties": False,
                },
                data=self._ask_data,
                next_on_outcome="diagnose",
            ),
            "diagnose": SubRutter(
                DiagnoseAnswer,
                charter_constructor=self._diagnosis_charter,
                next_on_outcome="complete",
            ),
            "complete": Terminal(result_constructor=self._forward_result),
        }


def diagnose_answer_on(
    *,
    id: str,
    on: TransitionMatch,
    question: QuestionCase | Callable[[TransitionContext], QuestionCase],
    actual_answer: Callable[[TransitionContext], str],
    evaluator: Callable[[str, str, TransitionContext], bool] | None = None,
    ask_for_fix: bool = False,
) -> TransitionHook:
    if type(ask_for_fix) is not bool:
        raise RutterDefinitionError("ask_for_fix must be an exact Boolean")
    if isinstance(question, QuestionCase):
        fixed_question = QuestionCase.from_json(question.to_json())

        def resolve_question(context: TransitionContext) -> QuestionCase:
            del context
            return fixed_question

    else:
        _require_callable_arity(question, 1, "question provider")
        resolve_question = question
    _require_callable_arity(actual_answer, 1, "actual_answer provider")
    if evaluator is not None:
        _require_callable_arity(evaluator, 3, "diagnostic evaluator")

    def build(context: TransitionContext) -> JsonObject:
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

    diagnostic = DiagnoseAnswer()

    def construct(context: TransitionContext) -> Rutter:
        del context
        return diagnostic

    return TransitionHook(
        id,
        on=on,
        rutter_constructor=construct,
        charter_constructor=build,
    )


def ask_and_diagnose_on(
    *,
    id: str,
    on: TransitionMatch,
    question: QuestionCase | Callable[[TransitionContext], QuestionCase],
    rutter_constructor: Callable[[TransitionContext], Rutter] | None = None,
) -> TransitionHook:
    if rutter_constructor is None:
        child = AskAndDiagnose()

        def construct(context: TransitionContext) -> Rutter:
            del context
            return child

    else:
        _require_callable_arity(
            rutter_constructor,
            1,
            "ask_and_diagnose_on Rutter constructor",
        )
        construct = rutter_constructor
    if isinstance(question, QuestionCase):
        fixed_question = QuestionCase.from_json(question.to_json())

        def resolve_question(context: TransitionContext) -> QuestionCase:
            del context
            return fixed_question

    else:
        _require_callable_arity(question, 1, "question provider")
        resolve_question = question

    def build(context: TransitionContext) -> JsonObject:
        resolved = resolve_question(context)
        if not isinstance(resolved, QuestionCase):
            raise RutterDefinitionError(
                "question provider must return a QuestionCase"
            )
        return resolved.to_json()

    return TransitionHook(
        id,
        on=on,
        rutter_constructor=construct,
        charter_constructor=build,
    )


def hook_sequence_after(
    *,
    id: str,
    after_evolutions: Collection[str],
    items: Sequence[JsonObject],
    rutter_constructor: Callable[[TransitionContext], Rutter],
    charter_constructor: (
        Callable[[JsonObject, TransitionContext], JsonObject] | None
    ) = None,
) -> TransitionHook:
    _require_id(id, "TransitionHook")
    if isinstance(after_evolutions, (str, bytes)) or not isinstance(
        after_evolutions, Collection
    ):
        raise RutterDefinitionError("after_evolutions must be a collection of evolution IDs")
    frozen_evolutions = frozenset(
        _require_id(evolution, "after evolution")
        for evolution in after_evolutions
    )
    if not frozen_evolutions:
        raise RutterDefinitionError("after_evolutions must not be empty")
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise RutterDefinitionError("items must be a sequence of JSON objects")
    frozen_items = tuple(
        _freeze_object(item, "sequence item") for item in items
    )
    if not frozen_items:
        raise RutterDefinitionError("items must not be empty")
    _require_callable_arity(
        rutter_constructor,
        1,
        "sequence Rutter constructor",
    )
    if charter_constructor is not None:
        _require_callable_arity(
            charter_constructor,
            2,
            "sequence Charter constructor",
        )

    def build(context: TransitionContext) -> JsonObject | None:
        source = context.transition.source
        if source not in frozen_evolutions:
            return None
        attached = context.evolution.history.subrutters(transition_hook_id=id)
        transition_ids = tuple(call.attached_to_transition_id for call in attached)
        if len(set(transition_ids)) != len(transition_ids):
            raise _HistoryInconsistency(
                "history-inconsistency: duplicate TransitionHook and transition identity"
            )
        index = len(attached)
        if index > len(frozen_items):
            raise _HistoryInconsistency(
                "history-inconsistency: sequence attachment count exceeds item count"
            )
        if index == len(frozen_items):
            return None
        selected = frozen_items[index]
        if charter_constructor is None:
            return _freeze_object(selected, "sequence Charter")
        return _freeze_object(
            charter_constructor(selected, context),
            "sequence Charter",
        )

    return TransitionHook(
        id,
        on=TransitionMatch(),
        rutter_constructor=rutter_constructor,
        charter_constructor=build,
    )


__all__ = (
    "AskAndDiagnose",
    "DiagnoseAnswer",
    "DiagnosisCase",
    "DiagnosisDetail",
    "QuestionCase",
    "ask_and_diagnose_on",
    "hook_sequence_after",
    "diagnose_answer_on",
)
