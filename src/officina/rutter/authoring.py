"""Author-facing Rutter definitions, contexts, and transition hooks."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from types import MappingProxyType
from typing import Callable, Mapping, TypeAlias

from officina.rutter.history import (
    HistoryEntry,
    HistoryView,
    MachineRecord,
    SubRutterRecord,
    TerminalRecord,
    Transition,
    Turn,
)
from officina.rutter.values import (
    AnswerSpec,
    Charter,
    JsonObject,
    MachineResult,
    Message,
    Response,
    RutterDefinitionError,
    ValidationReport,
    VoyageResult,
    _freeze_object,
    _require_id,
    _require_text,
)


def accept(context: LLMResponseContext) -> ValidationReport:
    del context
    return ValidationReport(True)


def empty_data(context: EvolutionContext) -> JsonObject:
    del context
    return MappingProxyType({})


class Rutter:
    rutter_id: str
    definition_version: int
    initial_evolution_id: str
    allow_multiple_hooks_per_transition: bool = False

    def define_evolutions(self) -> Mapping[str, LLMStep | MachineStep | SubRutter | Terminal]:
        raise NotImplementedError

    def define_transition_hooks(self) -> tuple[TransitionHook, ...]:
        return ()


def _freeze_then(value: object) -> object:
    if callable(value):
        return value
    if type(value) is str:
        return _require_id(value, "successor", RutterDefinitionError)
    if isinstance(value, Mapping):
        routes: dict[str, str] = {}
        for outcome, target in value.items():
            routes[_require_id(outcome, "outcome", RutterDefinitionError)] = _require_id(
                target, "successor", RutterDefinitionError
            )
        return MappingProxyType(routes)
    raise RutterDefinitionError("then must be a evolution ID, outcome mapping, or callable")


@dataclass(frozen=True, init=False)
class LLMStep:
    text: str
    answer: AnswerSpec
    data: Callable[[EvolutionContext], JsonObject]
    validate: Callable[[LLMResponseContext], ValidationReport]
    then: object

    def __init__(
        self,
        text: str,
        *,
        answer: AnswerSpec,
        data: Callable[[EvolutionContext], JsonObject] = empty_data,
        validate: Callable[[LLMResponseContext], ValidationReport] = accept,
        then: object,
    ) -> None:
        object.__setattr__(
            self,
            "text",
            _require_text(text, "LLMStep text", RutterDefinitionError),
        )
        if not isinstance(answer, AnswerSpec):
            raise RutterDefinitionError("LLMStep answer must be an AnswerSpec")
        if not callable(data) or not callable(validate):
            raise RutterDefinitionError("LLMStep data and validate must be callable")
        object.__setattr__(self, "answer", answer)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "validate", validate)
        object.__setattr__(self, "then", _freeze_then(then))


@dataclass(frozen=True)
class MachineStep:
    run: Callable[[MachineContext], MachineResult]
    _: KW_ONLY
    mode: str
    then: object

    def __post_init__(self) -> None:
        if not callable(self.run):
            raise RutterDefinitionError("MachineStep run must be callable")
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterDefinitionError("MachineStep mode is invalid")
        object.__setattr__(self, "then", _freeze_then(self.then))


@dataclass(frozen=True)
class SubRutter:
    child: type[Rutter]
    _: KW_ONLY
    charter: Callable[[EvolutionContext], JsonObject]
    then: object

    def __post_init__(self) -> None:
        if not isinstance(self.child, type) or not issubclass(self.child, Rutter):
            raise RutterDefinitionError("SubRutter child must be a Rutter class")
        if not callable(self.charter):
            raise RutterDefinitionError("SubRutter charter must be callable")
        object.__setattr__(self, "then", _freeze_then(self.then))


@dataclass(frozen=True)
class Terminal:
    result: VoyageResult | Callable[[EvolutionContext], VoyageResult]

    def __post_init__(self) -> None:
        if not isinstance(self.result, VoyageResult) and not callable(self.result):
            raise RutterDefinitionError("Terminal result must be a VoyageResult or callable")


Evolution: TypeAlias = LLMStep | MachineStep | SubRutter | Terminal


@dataclass(frozen=True)
class EvolutionContext:
    charter: Charter
    evolution_id: str
    evolution_entry_id: str
    history: HistoryView

    def __post_init__(self) -> None:
        if not isinstance(self.charter, Charter):
            raise RutterDefinitionError("EvolutionContext charter must be a Charter")
        _require_id(self.evolution_id, "evolution", RutterDefinitionError)
        _require_id(
            self.evolution_entry_id,
            "evolution entry",
            RutterDefinitionError,
        )
        if not isinstance(self.history, HistoryView):
            raise RutterDefinitionError("EvolutionContext history must be a HistoryView")


@dataclass(frozen=True)
class LLMResponseContext:
    evolution: EvolutionContext
    message: Message
    response: Response


@dataclass(frozen=True)
class MachineContext:
    evolution: EvolutionContext
    machine_id: str

    def __post_init__(self) -> None:
        _require_id(self.machine_id, "machine", RutterDefinitionError)


@dataclass(frozen=True)
class TransitionContext:
    evolution: EvolutionContext
    transition: JsonObject
    record: HistoryEntry

    def __post_init__(self) -> None:
        object.__setattr__(self, "transition", _freeze_object(self.transition, "transition"))
        if not isinstance(self.record, (Turn, MachineRecord, SubRutterRecord, TerminalRecord)):
            raise RutterDefinitionError("TransitionContext record must be a history entry")


@dataclass(frozen=True)
class TransitionMatch:
    source: str | None = None
    outcome: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.source, "transition source"),
            (self.outcome, "transition outcome"),
            (self.target, "transition target"),
        ):
            if value is not None:
                _require_id(value, label, RutterDefinitionError)

    def matches(self, transition: Transition) -> bool:
        return all(
            expected is None or expected == actual
            for expected, actual in (
                (self.source, transition.source),
                (self.outcome, transition.outcome),
                (self.target, transition.target),
            )
        )


@dataclass(frozen=True)
class TransitionHook:
    id: str
    _: KW_ONLY
    on: TransitionMatch
    child: type[Rutter]
    charter: Callable[[TransitionContext], JsonObject | None]

    def __post_init__(self) -> None:
        _require_id(self.id, "TransitionHook", RutterDefinitionError)
        if not isinstance(self.on, TransitionMatch):
            raise RutterDefinitionError("TransitionHook on must be a TransitionMatch")
        if not isinstance(self.child, type) or not issubclass(self.child, Rutter):
            raise RutterDefinitionError("TransitionHook child must be a Rutter class")
        if not callable(self.charter):
            raise RutterDefinitionError("TransitionHook charter must be callable")


def after(source: str) -> TransitionMatch:
    return TransitionMatch(source=source)


def before(target: str) -> TransitionMatch:
    return TransitionMatch(target=target)


def on_transition(
    *,
    source: str | None = None,
    outcome: str | None = None,
    target: str | None = None,
) -> TransitionMatch:
    return TransitionMatch(source=source, outcome=outcome, target=target)



__all__ = (
    "Evolution",
    "EvolutionContext",
    "LLMResponseContext",
    "LLMStep",
    "MachineContext",
    "MachineStep",
    "Rutter",
    "SubRutter",
    "Terminal",
    "TransitionContext",
    "TransitionHook",
    "TransitionMatch",
    "accept",
    "after",
    "before",
    "empty_data",
    "on_transition",
)
