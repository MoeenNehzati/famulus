"""Shared immutable values and public operating projections for Rutter."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from math import isfinite
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeAlias


JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | Mapping[str, "JsonValue"]
    | tuple["JsonValue", ...]
    | list["JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]


class RutterError(Exception):
    """Base class carrying a stable machine-readable error category."""

    category = "rutter"


class RutterDefinitionError(RutterError):
    category = "definition"


class RutterStateError(RutterError):
    category = "state"


class RutterValidationError(RutterError):
    category = "validation"


class NotApplicable(RutterError):
    category = "not_applicable"


class RunBlocked(RutterError):
    category = "run_blocked"


class PreviewUnavailable(RutterError):
    category = "preview_unavailable"


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _require_id(value: object, label: str, error: type[RutterError]) -> str:
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}:
        raise error(f"{label} ID must be a nonempty stable token")
    return value


def _require_text(value: object, label: str, error: type[RutterError]) -> str:
    if type(value) is not str or not value.strip():
        raise error(f"{label} must be a nonempty string")
    return value


def _require_int(
    value: object,
    label: str,
    *,
    positive: bool = False,
    error: type[RutterError] = RutterDefinitionError,
) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise error(f"{label} must be an exact {qualifier} integer")
    return value


def _freeze_json(
    value: object, *, error: type[RutterError] = RutterDefinitionError
) -> JsonValue:
    if value is None or type(value) in {bool, str}:
        return value  # type: ignore[return-value]
    if type(value) is int:
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
    value: object,
    label: str,
    *,
    error: type[RutterError] = RutterDefinitionError,
) -> JsonObject:
    if not isinstance(value, Mapping):
        raise error(f"{label} must be a finite JSON object")
    frozen = _freeze_json(value, error=error)
    assert isinstance(frozen, Mapping)
    return frozen


def _exact_object(
    value: object, expected: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RutterStateError(f"{label} has invalid fields")
    return value


def _state_construct(factory: Callable[[], Any]) -> Any:
    try:
        return factory()
    except RutterStateError:
        raise
    except (RutterDefinitionError, TypeError, ValueError) as exc:
        raise RutterStateError(str(exc)) from exc


def _object_json(**items: object) -> JsonObject:
    return _freeze_object(items, "JSON projection")


@dataclass(frozen=True)
class Charter:
    data: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _freeze_object(self.data, "Charter"))

    def to_json(self) -> JsonObject:
        return _freeze_object(self.data, "Charter")

    @classmethod
    def from_json(cls, value: object) -> Charter:
        return _state_construct(lambda: cls(_freeze_object(value, "Charter", error=RutterStateError)))


@dataclass(frozen=True)
class AnswerSpec:
    outcomes: JsonObject

    def __post_init__(self) -> None:
        frozen = _freeze_object(self.outcomes, "AnswerSpec")
        for outcome in frozen:
            _require_id(outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "outcomes", frozen)

    def to_json(self) -> JsonObject:
        return _freeze_object(self.outcomes, "AnswerSpec")

    @classmethod
    def from_json(cls, value: object) -> AnswerSpec:
        return _state_construct(
            lambda: cls(_freeze_object(value, "AnswerSpec", error=RutterStateError))
        )


@dataclass(frozen=True)
class ValidationIssue:
    path: tuple[str | int, ...]
    code: str
    message: str

    def __post_init__(self) -> None:
        path = tuple(self.path)
        if any(type(part) not in {str, int} for part in path):
            raise RutterDefinitionError("validation issue path must contain strings or integers")
        object.__setattr__(self, "path", path)
        _require_id(self.code, "validation issue code", RutterDefinitionError)
        _require_text(self.message, "validation issue message", RutterDefinitionError)

    def to_json(self) -> JsonObject:
        return _object_json(path=self.path, code=self.code, message=self.message)

    @classmethod
    def from_json(cls, value: object) -> ValidationIssue:
        obj = _exact_object(value, {"path", "code", "message"}, "ValidationIssue")
        path = obj["path"]
        if not isinstance(path, (list, tuple)):
            raise RutterStateError("validation issue path must be an array")
        return _state_construct(lambda: cls(tuple(path), obj["code"], obj["message"]))


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise RutterDefinitionError("valid must be an exact Boolean")
        issues = tuple(self.issues)
        if any(not isinstance(issue, ValidationIssue) for issue in issues):
            raise RutterDefinitionError("issues must contain ValidationIssue values")
        if self.valid and issues:
            raise RutterDefinitionError("a valid report cannot contain issues")
        if not self.valid and not issues:
            raise RutterDefinitionError("an invalid report must contain at least one issue")
        object.__setattr__(self, "issues", issues)

    def to_json(self) -> JsonObject:
        return _object_json(
            valid=self.valid,
            issues=tuple(issue.to_json() for issue in self.issues),
        )

    @classmethod
    def from_json(cls, value: object) -> ValidationReport:
        obj = _exact_object(value, {"valid", "issues"}, "ValidationReport")
        issues = obj["issues"]
        if not isinstance(issues, (list, tuple)):
            raise RutterStateError("ValidationReport issues must be an array")
        return _state_construct(
            lambda: cls(obj["valid"], tuple(ValidationIssue.from_json(item) for item in issues))
        )


@dataclass(frozen=True)
class MachineInstruction:
    machine_id: str
    mode: str
    run: Callable[[], MachineResult]
    answer_format: JsonObject

    def __post_init__(self) -> None:
        _require_id(self.machine_id, "machine", RutterDefinitionError)
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterDefinitionError("MachineInstruction mode is invalid")
        if not callable(self.run):
            raise RutterDefinitionError("MachineInstruction run must be callable")
        try:
            signature(self.run).bind()
        except (TypeError, ValueError) as exc:
            raise RutterDefinitionError(
                "MachineInstruction run must accept zero arguments"
            ) from exc
        object.__setattr__(
            self,
            "answer_format",
            _freeze_object(self.answer_format, "MachineInstruction answer format"),
        )


def _validate_message_parts(instructions: object, data: object) -> tuple[JsonObject, JsonObject]:
    instruction_object = _freeze_object(instructions, "Message instructions")
    if set(instruction_object) != {"text", "answer"}:
        raise RutterDefinitionError("Message instructions has invalid fields")
    _require_text(instruction_object["text"], "Message instruction text", RutterDefinitionError)
    AnswerSpec(_freeze_object(instruction_object["answer"], "Message answer"))

    data_object = _freeze_object(data, "Message data")
    if set(data_object) != {"evolution", "payload"}:
        raise RutterDefinitionError("Message data has invalid fields")
    evolution = _freeze_object(data_object["evolution"], "Message evolution")
    if set(evolution) != {"id", "entry_id", "revision"}:
        raise RutterDefinitionError("Message evolution has invalid fields")
    _require_id(evolution["id"], "evolution", RutterDefinitionError)
    _require_id(evolution["entry_id"], "entry", RutterDefinitionError)
    _require_int(evolution["revision"], "revision")
    _freeze_object(data_object["payload"], "Message payload")
    return instruction_object, data_object


@dataclass(frozen=True)
class Message:
    instructions: JsonObject
    data: JsonObject

    def __post_init__(self) -> None:
        instructions, data = _validate_message_parts(self.instructions, self.data)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "data", data)

    def to_json(self) -> JsonObject:
        return _object_json(instructions=self.instructions, data=self.data)

    @classmethod
    def from_json(cls, value: object) -> Message:
        obj = _exact_object(value, {"instructions", "data"}, "Message")
        return _state_construct(lambda: cls(obj["instructions"], obj["data"]))


@dataclass(frozen=True)
class Response:
    revision: int
    outcome: str
    evidence: JsonObject

    def __post_init__(self) -> None:
        _require_int(self.revision, "revision")
        _require_id(self.outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "evidence", _freeze_object(self.evidence, "Response evidence"))

    def to_json(self) -> JsonObject:
        return _object_json(revision=self.revision, outcome=self.outcome, evidence=self.evidence)

    @classmethod
    def from_json(cls, value: object) -> Response:
        obj = _exact_object(value, {"revision", "outcome", "evidence"}, "Response")
        return _state_construct(lambda: cls(obj["revision"], obj["outcome"], obj["evidence"]))


@dataclass(frozen=True)
class MachineResult:
    outcome: str
    value: JsonValue

    def __post_init__(self) -> None:
        _require_id(self.outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_json(self) -> JsonObject:
        return _object_json(outcome=self.outcome, value=self.value)

    @classmethod
    def from_json(cls, value: object) -> MachineResult:
        obj = _exact_object(value, {"outcome", "value"}, "MachineResult")
        return _state_construct(lambda: cls(obj["outcome"], obj["value"]))


@dataclass(frozen=True)
class FaultSummary:
    category: str
    evolution_id: str | None
    evolution_entry_id: str | None
    target_evolution_id: str | None
    transition_hook_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transition_hook_ids",
            tuple(self.transition_hook_ids),
        )


@dataclass(frozen=True)
class VoyageResult:
    outcome: str
    value: JsonValue

    def __post_init__(self) -> None:
        _require_id(self.outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_json(self) -> JsonObject:
        return _object_json(outcome=self.outcome, value=self.value)

    @classmethod
    def from_json(cls, value: object) -> VoyageResult:
        obj = _exact_object(value, {"outcome", "value"}, "VoyageResult")
        return _state_construct(lambda: cls(obj["outcome"], obj["value"]))


@dataclass(frozen=True)
class EvolutionView:
    rutter_id: str
    definition_version: int
    evolution_id: str
    evolution_entry_id: str | None
    depth: int
    condition: str

    def __post_init__(self) -> None:
        _require_id(self.rutter_id, "Rutter", RutterDefinitionError)
        _require_int(self.definition_version, "definition version", positive=True)
        _require_id(self.evolution_id, "evolution", RutterDefinitionError)
        _require_int(self.depth, "nesting depth")
        if self.condition not in {
            "ready",
            "terminal",
            "fault",
            "uncertain",
            "preview",
        }:
            raise RutterDefinitionError("EvolutionView condition is invalid")
        if self.evolution_entry_id is None:
            if self.condition != "preview":
                raise RutterDefinitionError(
                    "EvolutionView entrance ID may be absent only for preview"
                )
        else:
            _require_id(self.evolution_entry_id, "evolution entrance", RutterDefinitionError)
            if self.condition == "preview":
                raise RutterDefinitionError(
                    "preview EvolutionView must not have an entrance ID"
                )



@dataclass(frozen=True)
class VoyageStatus:
    current_evolution: EvolutionView
    instruction: Message | MachineInstruction | None
    active_result: VoyageResult | None
    fault: FaultSummary | None

    def __post_init__(self) -> None:
        if not isinstance(self.current_evolution, EvolutionView):
            raise RutterDefinitionError(
                "VoyageStatus current_evolution must be an EvolutionView"
            )
        if self.instruction is not None and not isinstance(
            self.instruction, (Message, MachineInstruction)
        ):
            raise RutterDefinitionError(
                "VoyageStatus instruction must be a Message, MachineInstruction, or null"
            )
        if self.active_result is not None and not isinstance(
            self.active_result, VoyageResult
        ):
            raise RutterDefinitionError(
                "VoyageStatus active_result must be a VoyageResult or null"
            )
        if self.fault is not None and not isinstance(self.fault, FaultSummary):
            raise RutterDefinitionError(
                "VoyageStatus fault must be a FaultSummary or null"
            )


__all__ = (
    "AnswerSpec",
    "Charter",
    "EvolutionView",
    "FaultSummary",
    "JsonObject",
    "JsonValue",
    "MachineInstruction",
    "MachineResult",
    "Message",
    "NotApplicable",
    "PreviewUnavailable",
    "Response",
    "RunBlocked",
    "RutterDefinitionError",
    "RutterError",
    "RutterStateError",
    "RutterValidationError",
    "ValidationIssue",
    "ValidationReport",
    "VoyageResult",
    "VoyageStatus",
)
