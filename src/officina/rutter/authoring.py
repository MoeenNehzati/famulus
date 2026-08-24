"""Author-facing Rutter definitions, contexts, and transition hooks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass
from inspect import Parameter, signature
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
    Charter,
    JsonObject,
    MachineResult,
    Message,
    RutterDefinitionError,
    ValidationReport,
    VoyageResult,
    _freeze_object,
    _require_id,
    _require_text,
)


def _accept_response(context: LLMResponseContext) -> ValidationReport:
    del context
    return ValidationReport(True)


def empty_data(context: EvolutionContext) -> JsonObject:
    del context
    return MappingProxyType({})


_OMITTED = object()


class Rutter:
    rutter_id: str
    definition_version: int
    initial_evolution_id: str
    allow_multiple_hooks_per_transition: bool = False

    def __init__(
        self,
        *,
        id: str | object = _OMITTED,
        version: int | object = _OMITTED,
        start: str | object = _OMITTED,
        evolutions: Mapping[
            str, LLMStep | MachineStep | SubRutter | Terminal
        ]
        | object = _OMITTED,
        hooks: Sequence[TransitionHook] | object = _OMITTED,
        allow_multiple_hooks_per_transition: bool | object = _OMITTED,
    ) -> None:
        arguments = (
            id,
            version,
            start,
            evolutions,
            hooks,
            allow_multiple_hooks_per_transition,
        )
        if type(self) is not Rutter:
            if any(argument is not _OMITTED for argument in arguments):
                raise RutterDefinitionError(
                    "direct definition arguments are unavailable to Rutter subclasses"
                )
            return
        if all(argument is _OMITTED for argument in arguments):
            raise RutterDefinitionError("Rutter requires a complete direct definition")
        if any(
            argument is _OMITTED for argument in (id, version, start, evolutions)
        ):
            raise RutterDefinitionError(
                "direct Rutter construction requires id, version, start, and evolutions"
            )
        if not isinstance(evolutions, Mapping):
            raise RutterDefinitionError("evolutions must be a mapping")
        if hooks is _OMITTED:
            hooks = ()
        if isinstance(hooks, (str, bytes)) or not isinstance(hooks, Sequence):
            raise RutterDefinitionError("hooks must be a sequence")
        if allow_multiple_hooks_per_transition is _OMITTED:
            allow_multiple_hooks_per_transition = False
        if type(allow_multiple_hooks_per_transition) is not bool:
            raise RutterDefinitionError(
                "allow_multiple_hooks_per_transition must be an exact Boolean"
            )

        self.rutter_id = id  # type: ignore[assignment]
        self.definition_version = version  # type: ignore[assignment]
        self.initial_evolution_id = start  # type: ignore[assignment]
        self.allow_multiple_hooks_per_transition = (
            allow_multiple_hooks_per_transition
        )
        self._evolutions = MappingProxyType(dict(evolutions))
        self._transition_hooks = tuple(hooks)

    def define_evolutions(self) -> Mapping[str, LLMStep | MachineStep | SubRutter | Terminal]:
        try:
            return self._evolutions
        except AttributeError as exc:
            raise NotImplementedError from exc

    def define_transition_hooks(self) -> tuple[TransitionHook, ...]:
        return getattr(self, "_transition_hooks", ())


def _freeze_next_on_outcome(value: str | Mapping[str, str]) -> str | Mapping[str, str]:
    if type(value) is str:
        return _require_id(value, "successor", RutterDefinitionError)
    if isinstance(value, Mapping):
        routes: dict[str, str] = {}
        for outcome, target in value.items():
            routes[_require_id(outcome, "outcome", RutterDefinitionError)] = _require_id(
                target, "successor", RutterDefinitionError
            )
        return MappingProxyType(routes)
    raise RutterDefinitionError(
        "next_on_outcome must be an evolution ID or outcome mapping"
    )


def _routing_modes(
    next_on_outcome: str | Mapping[str, str] | None,
    choose_next: Callable[..., str] | None,
) -> tuple[str | Mapping[str, str] | None, Callable[..., str] | None]:
    if (next_on_outcome is None) == (choose_next is None):
        raise RutterDefinitionError("exactly one routing mode is required")
    if next_on_outcome is not None:
        return _freeze_next_on_outcome(next_on_outcome), None
    if not callable(choose_next):
        raise RutterDefinitionError("choose_next must be callable")
    return None, choose_next


def _require_callback_arity(callback: object, arity: int, label: str) -> None:
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


@dataclass(frozen=True, init=False)
class LLMStep:
    text: str
    response_schema: JsonObject | None
    data: Callable[[EvolutionContext], JsonObject]
    assess_response: Callable[[LLMResponseContext], ValidationReport]
    next_on_outcome: str | Mapping[str, str] | None
    choose_next: Callable[..., str] | None

    def __init__(
        self,
        text: str,
        *,
        response_schema: JsonObject | None = None,
        data: Callable[[EvolutionContext], JsonObject] = empty_data,
        assess_response: Callable[[LLMResponseContext], ValidationReport] = _accept_response,
        next_on_outcome: str | Mapping[str, str] | None = None,
        choose_next: Callable[..., str] | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "text",
            _require_text(text, "LLMStep text", RutterDefinitionError),
        )
        if response_schema is not None:
            response_schema = _freeze_object(
                response_schema,
                "LLMStep response_schema",
            )
        if not callable(data) or not callable(assess_response):
            raise RutterDefinitionError(
                "LLMStep data and assess_response must be callable"
            )
        object.__setattr__(self, "response_schema", response_schema)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "assess_response", assess_response)
        next_on_outcome, choose_next = _routing_modes(next_on_outcome, choose_next)
        object.__setattr__(self, "next_on_outcome", next_on_outcome)
        object.__setattr__(self, "choose_next", choose_next)


@dataclass(frozen=True)
class MachineStep:
    run: Callable[[MachineContext], MachineResult]
    _: KW_ONLY
    mode: str
    next_on_outcome: str | Mapping[str, str] | None = None
    choose_next: Callable[..., str] | None = None

    def __post_init__(self) -> None:
        if not callable(self.run):
            raise RutterDefinitionError("MachineStep run must be callable")
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterDefinitionError("MachineStep mode is invalid")
        next_on_outcome, choose_next = _routing_modes(
            self.next_on_outcome, self.choose_next
        )
        object.__setattr__(self, "next_on_outcome", next_on_outcome)
        object.__setattr__(self, "choose_next", choose_next)


@dataclass(frozen=True)
class SubRutter:
    """A contextual explicit child call with replayable Rutter construction.

    For the same immutable Charter, evolution entry, and history prefix,
    ``rutter_constructor`` must return an equivalent Rutter identity. Within
    one Voyage, repeated resolution must return the same definition instance;
    a fresh registry may return a fresh equivalent object.
    """

    rutter_constructor: Callable[[EvolutionContext], Rutter]
    _: KW_ONLY
    charter_constructor: Callable[[EvolutionContext], JsonObject]
    next_on_outcome: str | Mapping[str, str] | None = None
    choose_next: Callable[..., str] | None = None

    def __post_init__(self) -> None:
        if not callable(self.rutter_constructor):
            raise RutterDefinitionError(
                "SubRutter rutter_constructor must be callable"
            )
        if not callable(self.charter_constructor):
            raise RutterDefinitionError(
                "SubRutter charter_constructor must be callable"
            )
        next_on_outcome, choose_next = _routing_modes(
            self.next_on_outcome, self.choose_next
        )
        object.__setattr__(self, "next_on_outcome", next_on_outcome)
        object.__setattr__(self, "choose_next", choose_next)


@dataclass(frozen=True)
class Terminal:
    _: KW_ONLY
    result: VoyageResult | None = None
    result_constructor: Callable[[EvolutionContext], VoyageResult] | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.result_constructor is None):
            raise RutterDefinitionError("Terminal requires exactly one result mode")
        if self.result is not None and not isinstance(self.result, VoyageResult):
            raise RutterDefinitionError("Terminal result must be a VoyageResult")
        if self.result_constructor is not None:
            _require_callback_arity(
                self.result_constructor,
                1,
                "Terminal result_constructor",
            )


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
    response: JsonObject

    def __post_init__(self) -> None:
        if not isinstance(self.evolution, EvolutionContext):
            raise RutterDefinitionError(
                "LLMResponseContext evolution must be an EvolutionContext"
            )
        if not isinstance(self.message, Message):
            raise RutterDefinitionError("LLMResponseContext message must be a Message")
        object.__setattr__(
            self,
            "response",
            _freeze_object(self.response, "LLMResponseContext response"),
        )


@dataclass(frozen=True)
class MachineContext:
    evolution: EvolutionContext
    machine_id: str

    def __post_init__(self) -> None:
        _require_id(self.machine_id, "machine", RutterDefinitionError)


@dataclass(frozen=True)
class TransitionContext:
    evolution: EvolutionContext
    transition: Transition
    record: HistoryEntry

    def __post_init__(self) -> None:
        if type(self.transition) is not Transition:
            raise RutterDefinitionError(
                "TransitionContext transition must be a Transition"
            )
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
    """A matching hook with replayable Charter and Rutter constructors.

    ``None`` declines selection; a JSON object constructs the selected child's
    Charter.  For the same immutable Charter, transition, record, and history
    prefix, ``rutter_constructor`` must return an equivalent Rutter identity.
    Within one Voyage, repeated selection of that identity must return the same
    definition instance; a fresh registry may return a fresh equivalent object.
    Constructors must not depend on mutable voyage state or external effects.
    """

    id: str
    _: KW_ONLY
    on: TransitionMatch
    rutter_constructor: Callable[[TransitionContext], Rutter]
    charter_constructor: Callable[[TransitionContext], JsonObject | None]

    def __post_init__(self) -> None:
        _require_id(self.id, "TransitionHook", RutterDefinitionError)
        if not isinstance(self.on, TransitionMatch):
            raise RutterDefinitionError("TransitionHook on must be a TransitionMatch")
        if not callable(self.rutter_constructor):
            raise RutterDefinitionError(
                "TransitionHook rutter_constructor must be callable"
            )
        if not callable(self.charter_constructor):
            raise RutterDefinitionError(
                "TransitionHook charter_constructor must be callable"
            )


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
    "after",
    "before",
    "empty_data",
    "on_transition",
)
