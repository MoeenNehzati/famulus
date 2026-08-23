"""Immutable definitions, persisted values, and callback views for Rutter."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
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


def accept(context: AnswerContext) -> ValidationReport:
    del context
    return ValidationReport(True)


def empty_data(context: StateContext) -> JsonObject:
    del context
    return MappingProxyType({})


class Rutter:
    rutter_id: str
    definition_version: int
    start_state: str
    allow_multiple_cases_at_once: bool = False

    def define_states(self) -> Mapping[str, Prompt | Action | Call | Done]:
        raise NotImplementedError

    def define_case_makers(self) -> tuple[Any, ...]:
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
    raise RutterDefinitionError("then must be a state ID, outcome mapping, or callable")


@dataclass(frozen=True, init=False)
class Prompt:
    text: str
    answer: AnswerSpec
    data: Callable[[StateContext], JsonObject]
    validate: Callable[[AnswerContext], ValidationReport]
    then: object

    def __init__(
        self,
        text: str,
        *,
        answer: AnswerSpec,
        data: Callable[[StateContext], JsonObject] = empty_data,
        validate: Callable[[AnswerContext], ValidationReport] = accept,
        then: object,
    ) -> None:
        object.__setattr__(self, "text", _require_text(text, "Prompt text", RutterDefinitionError))
        if not isinstance(answer, AnswerSpec):
            raise RutterDefinitionError("Prompt answer must be an AnswerSpec")
        if not callable(data) or not callable(validate):
            raise RutterDefinitionError("Prompt data and validate must be callable")
        object.__setattr__(self, "answer", answer)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "validate", validate)
        object.__setattr__(self, "then", _freeze_then(then))


@dataclass(frozen=True)
class Action:
    run: Callable[[ActionContext], ActionResult]
    _: KW_ONLY
    mode: str
    then: object

    def __post_init__(self) -> None:
        if not callable(self.run):
            raise RutterDefinitionError("Action run must be callable")
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterDefinitionError("Action mode is invalid")
        object.__setattr__(self, "then", _freeze_then(self.then))


@dataclass(frozen=True)
class Call:
    child: type[Rutter]
    _: KW_ONLY
    charter: Callable[[StateContext], JsonObject]
    then: object

    def __post_init__(self) -> None:
        if not isinstance(self.child, type) or not issubclass(self.child, Rutter):
            raise RutterDefinitionError("Call child must be a Rutter class")
        if not callable(self.charter):
            raise RutterDefinitionError("Call charter must be callable")
        object.__setattr__(self, "then", _freeze_then(self.then))


@dataclass(frozen=True)
class Done:
    result: RunResult | Callable[[StateContext], RunResult]

    def __post_init__(self) -> None:
        if not isinstance(self.result, RunResult) and not callable(self.result):
            raise RutterDefinitionError("Done result must be a RunResult or callable")


@dataclass(frozen=True)
class PythonInstruction:
    action_id: str
    mode: str
    run: Callable[[], ActionResult]
    answer_format: JsonObject

    def __post_init__(self) -> None:
        _require_id(self.action_id, "action", RutterDefinitionError)
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterDefinitionError("PythonInstruction mode is invalid")
        if not callable(self.run):
            raise RutterDefinitionError("PythonInstruction run must be callable")
        try:
            signature(self.run).bind()
        except (TypeError, ValueError) as exc:
            raise RutterDefinitionError(
                "PythonInstruction run must accept zero arguments"
            ) from exc
        object.__setattr__(
            self,
            "answer_format",
            _freeze_object(self.answer_format, "PythonInstruction answer format"),
        )


State: TypeAlias = Prompt | Action | Call | Done


def _validate_message_parts(instructions: object, data: object) -> tuple[JsonObject, JsonObject]:
    instruction_object = _freeze_object(instructions, "Message instructions")
    if set(instruction_object) != {"text", "answer"}:
        raise RutterDefinitionError("Message instructions has invalid fields")
    _require_text(instruction_object["text"], "Message instruction text", RutterDefinitionError)
    AnswerSpec(_freeze_object(instruction_object["answer"], "Message answer"))

    data_object = _freeze_object(data, "Message data")
    if set(data_object) != {"state", "payload"}:
        raise RutterDefinitionError("Message data has invalid fields")
    state = _freeze_object(data_object["state"], "Message state")
    if set(state) != {"id", "entry_id", "revision"}:
        raise RutterDefinitionError("Message state has invalid fields")
    _require_id(state["id"], "state", RutterDefinitionError)
    _require_id(state["entry_id"], "entry", RutterDefinitionError)
    _require_int(state["revision"], "revision")
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
class ActionResult:
    outcome: str
    value: JsonValue

    def __post_init__(self) -> None:
        _require_id(self.outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_json(self) -> JsonObject:
        return _object_json(outcome=self.outcome, value=self.value)

    @classmethod
    def from_json(cls, value: object) -> ActionResult:
        obj = _exact_object(value, {"outcome", "value"}, "ActionResult")
        return _state_construct(lambda: cls(obj["outcome"], obj["value"]))


@dataclass(frozen=True)
class RunResult:
    outcome: str
    value: JsonValue

    def __post_init__(self) -> None:
        _require_id(self.outcome, "outcome", RutterDefinitionError)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_json(self) -> JsonObject:
        return _object_json(outcome=self.outcome, value=self.value)

    @classmethod
    def from_json(cls, value: object) -> RunResult:
        obj = _exact_object(value, {"outcome", "value"}, "RunResult")
        return _state_construct(lambda: cls(obj["outcome"], obj["value"]))


@dataclass(frozen=True)
class EnteredNode:
    entry_id: str
    state_id: str

    def __post_init__(self) -> None:
        _require_id(self.entry_id, "entry", RutterStateError)
        _require_id(self.state_id, "state", RutterStateError)

    def to_json(self) -> JsonObject:
        return _object_json(entry_id=self.entry_id, state_id=self.state_id)

    @classmethod
    def from_json(cls, value: object) -> EnteredNode:
        obj = _exact_object(value, {"entry_id", "state_id"}, "EnteredNode")
        return cls(obj["entry_id"], obj["state_id"])


@dataclass(frozen=True)
class Turn:
    record_id: str
    node_entry_id: str
    state_id: str
    revision: int
    message: Message
    response: Response | None

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.node_entry_id, "node entry", RutterStateError)
        _require_id(self.state_id, "state", RutterStateError)
        _require_int(self.revision, "revision", error=RutterStateError)
        if not isinstance(self.message, Message):
            raise RutterStateError("Turn message must be a Message")
        if self.response is not None and not isinstance(self.response, Response):
            raise RutterStateError("Turn response must be a Response or null")
        state = self.message.data["state"]
        assert isinstance(state, Mapping)
        if (
            state["id"] != self.state_id
            or state["entry_id"] != self.node_entry_id
            or state["revision"] != self.revision
        ):
            raise RutterStateError("Turn message coordinates do not match its record")
        if self.response is not None and self.response.revision != self.revision:
            raise RutterStateError("Turn response revision does not match its record")

    def to_json(self) -> JsonObject:
        return _object_json(
            record_id=self.record_id,
            node_entry_id=self.node_entry_id,
            state_id=self.state_id,
            revision=self.revision,
            message=self.message.to_json(),
            response=None if self.response is None else self.response.to_json(),
        )

    @classmethod
    def from_json(cls, value: object) -> Turn:
        expected = {
            "record_id",
            "node_entry_id",
            "state_id",
            "revision",
            "message",
            "response",
        }
        obj = _exact_object(value, expected, "Turn")
        response = obj["response"]
        return cls(
            obj["record_id"],
            obj["node_entry_id"],
            obj["state_id"],
            obj["revision"],
            Message.from_json(obj["message"]),
            None if response is None else Response.from_json(response),
        )


@dataclass(frozen=True)
class ActionRecord:
    record_id: str
    action_id: str
    node_entry_id: str
    state_id: str
    mode: str
    result: ActionResult

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.action_id, "action", RutterStateError)
        _require_id(self.node_entry_id, "node entry", RutterStateError)
        _require_id(self.state_id, "state", RutterStateError)
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterStateError("ActionRecord mode is invalid")
        if not isinstance(self.result, ActionResult):
            raise RutterStateError("ActionRecord result must be an ActionResult")

    def to_json(self) -> JsonObject:
        return _object_json(
            record_id=self.record_id,
            action_id=self.action_id,
            node_entry_id=self.node_entry_id,
            state_id=self.state_id,
            mode=self.mode,
            result=self.result.to_json(),
        )

    @classmethod
    def from_json(cls, value: object) -> ActionRecord:
        expected = {
            "record_id",
            "action_id",
            "node_entry_id",
            "state_id",
            "mode",
            "result",
        }
        obj = _exact_object(value, expected, "ActionRecord")
        return cls(
            obj["record_id"],
            obj["action_id"],
            obj["node_entry_id"],
            obj["state_id"],
            obj["mode"],
            ActionResult.from_json(obj["result"]),
        )


def _validate_child_provenance(
    kind: object, site: object, attached_to_edge_id: object
) -> None:
    if kind not in {"explicit_call", "attached_case"}:
        raise RutterStateError("child provenance kind is invalid")
    _require_id(site, "child site", RutterStateError)
    if kind == "explicit_call" and attached_to_edge_id is not None:
        raise RutterStateError("explicit-call child provenance cannot name an edge")
    if kind == "attached_case" and attached_to_edge_id is None:
        raise RutterStateError("attached-case child provenance must name an edge")
    if attached_to_edge_id is not None:
        _require_id(attached_to_edge_id, "attached edge", RutterStateError)


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    node_entry_id: str
    site_kind: str
    site_id: str
    attached_to_edge_id: str | None
    completed_run_id: str

    def __post_init__(self) -> None:
        _require_id(self.call_id, "call", RutterStateError)
        _require_id(self.node_entry_id, "node entry", RutterStateError)
        _validate_child_provenance(self.site_kind, self.site_id, self.attached_to_edge_id)
        _require_id(self.completed_run_id, "completed run", RutterStateError)

    def to_json(self) -> JsonObject:
        return _object_json(
            call_id=self.call_id,
            node_entry_id=self.node_entry_id,
            site_kind=self.site_kind,
            site_id=self.site_id,
            attached_to_edge_id=self.attached_to_edge_id,
            completed_run_id=self.completed_run_id,
        )

    @classmethod
    def from_json(cls, value: object) -> CallRecord:
        expected = {
            "call_id",
            "node_entry_id",
            "site_kind",
            "site_id",
            "attached_to_edge_id",
            "completed_run_id",
        }
        obj = _exact_object(value, expected, "CallRecord")
        return cls(
            obj["call_id"],
            obj["node_entry_id"],
            obj["site_kind"],
            obj["site_id"],
            obj["attached_to_edge_id"],
            obj["completed_run_id"],
        )


@dataclass(frozen=True)
class DoneRecord:
    record_id: str
    node_entry_id: str
    state_id: str
    result: RunResult

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.node_entry_id, "node entry", RutterStateError)
        _require_id(self.state_id, "state", RutterStateError)
        if not isinstance(self.result, RunResult):
            raise RutterStateError("DoneRecord result must be a RunResult")

    def to_json(self) -> JsonObject:
        return _object_json(
            record_id=self.record_id,
            node_entry_id=self.node_entry_id,
            state_id=self.state_id,
            result=self.result.to_json(),
        )

    @classmethod
    def from_json(cls, value: object) -> DoneRecord:
        expected = {"record_id", "node_entry_id", "state_id", "result"}
        obj = _exact_object(value, expected, "DoneRecord")
        return cls(
            obj["record_id"],
            obj["node_entry_id"],
            obj["state_id"],
            RunResult.from_json(obj["result"]),
        )


HistoryEntry: TypeAlias = Turn | ActionRecord | CallRecord | DoneRecord


def _record_id(record: HistoryEntry) -> str:
    return record.call_id if isinstance(record, CallRecord) else record.record_id


def _history_entry_from_json(value: object) -> HistoryEntry:
    if not isinstance(value, Mapping):
        raise RutterStateError("history entries must be objects")
    keys = set(value)
    if "message" in keys:
        return Turn.from_json(value)
    if "action_id" in keys:
        return ActionRecord.from_json(value)
    if "call_id" in keys:
        return CallRecord.from_json(value)
    if "result" in keys:
        return DoneRecord.from_json(value)
    raise RutterStateError("history entry has invalid fields")


def _validate_history(entries: tuple[HistoryEntry, ...]) -> None:
    if any(not isinstance(entry, (Turn, ActionRecord, CallRecord, DoneRecord)) for entry in entries):
        raise RutterStateError("history contains an invalid entry")
    record_ids = tuple(_record_id(entry) for entry in entries)
    if len(record_ids) != len(set(record_ids)):
        raise RutterStateError("duplicate history record ID")
    open_turns = [entry for entry in entries if isinstance(entry, Turn) and entry.response is None]
    if len(open_turns) > 1:
        raise RutterStateError("history contains more than one open Turn")
    if open_turns and entries[-1] is not open_turns[0]:
        raise RutterStateError("an open Turn must be the final history entry")
    done_indexes = [
        index for index, entry in enumerate(entries) if isinstance(entry, DoneRecord)
    ]
    if len(done_indexes) > 1:
        raise RutterStateError("history may contain only one DoneRecord")
    if done_indexes:
        done_index = done_indexes[0]
        done = entries[done_index]
        assert isinstance(done, DoneRecord)
        for entry in entries[done_index + 1 :]:
            if (
                not isinstance(entry, CallRecord)
                or entry.site_kind != "attached_case"
                or entry.node_entry_id != done.node_entry_id
            ):
                raise RutterStateError(
                    "entries after a DoneRecord must be attached-case CallRecords "
                    "from the same node entry"
                )


@dataclass(frozen=True)
class CompletedRun:
    run_id: str
    rutter_id: str
    definition_version: int
    charter: Charter
    history: tuple[HistoryEntry, ...]

    def __post_init__(self) -> None:
        _require_id(self.run_id, "run", RutterStateError)
        _require_id(self.rutter_id, "Rutter", RutterStateError)
        _require_int(
            self.definition_version,
            "definition version",
            positive=True,
            error=RutterStateError,
        )
        if not isinstance(self.charter, Charter):
            raise RutterStateError("CompletedRun charter must be a Charter")
        history = tuple(self.history)
        _validate_history(history)
        if sum(isinstance(entry, DoneRecord) for entry in history) != 1:
            raise RutterStateError("CompletedRun requires one DoneRecord authority")
        object.__setattr__(self, "history", history)

    @property
    def result(self) -> RunResult:
        return next(
            entry.result for entry in self.history if isinstance(entry, DoneRecord)
        )

    def to_json(self) -> JsonObject:
        return _object_json(
            run_id=self.run_id,
            rutter_id=self.rutter_id,
            definition_version=self.definition_version,
            charter=self.charter.to_json(),
            history=tuple(entry.to_json() for entry in self.history),
        )

    @classmethod
    def from_json(cls, value: object) -> CompletedRun:
        expected = {"run_id", "rutter_id", "definition_version", "charter", "history"}
        obj = _exact_object(value, expected, "CompletedRun")
        history = obj["history"]
        if not isinstance(history, (list, tuple)):
            raise RutterStateError("CompletedRun history must be an array")
        return cls(
            obj["run_id"],
            obj["rutter_id"],
            obj["definition_version"],
            Charter.from_json(obj["charter"]),
            tuple(_history_entry_from_json(item) for item in history),
        )


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    rutter_id: str
    definition_version: int
    charter: Charter
    entered_node: EnteredNode
    history: tuple[HistoryEntry, ...]
    active_child: ActiveChild | None

    def __post_init__(self) -> None:
        _require_id(self.run_id, "run", RutterStateError)
        _require_id(self.rutter_id, "Rutter", RutterStateError)
        _require_int(
            self.definition_version,
            "definition version",
            positive=True,
            error=RutterStateError,
        )
        if not isinstance(self.charter, Charter):
            raise RutterStateError("ActiveRun charter must be a Charter")
        if not isinstance(self.entered_node, EnteredNode):
            raise RutterStateError("ActiveRun entered_node must be an EnteredNode")
        history = tuple(self.history)
        _validate_history(history)
        if self.active_child is not None and not isinstance(self.active_child, ActiveChild):
            raise RutterStateError("ActiveRun active_child must be an ActiveChild or null")
        done = next(
            (entry for entry in history if isinstance(entry, DoneRecord)),
            None,
        )
        if done is not None:
            if (
                done.node_entry_id != self.entered_node.entry_id
                or done.state_id != self.entered_node.state_id
            ):
                raise RutterStateError(
                    "ActiveRun DoneRecord must match the current entered node"
                )
            if self.active_child is not None and (
                self.active_child.kind != "attached_case"
                or self.active_child.attached_to_edge_id != done.record_id
            ):
                raise RutterStateError(
                    "ActiveRun DoneRecord child must be an attached case bound "
                    "to that DoneRecord"
                )
        object.__setattr__(self, "history", history)

    def to_json(self) -> JsonObject:
        return _object_json(
            run_id=self.run_id,
            rutter_id=self.rutter_id,
            definition_version=self.definition_version,
            charter=self.charter.to_json(),
            entered_node=self.entered_node.to_json(),
            history=tuple(entry.to_json() for entry in self.history),
            active_child=None if self.active_child is None else self.active_child.to_json(),
        )

    @classmethod
    def from_json(cls, value: object) -> ActiveRun:
        expected = {
            "run_id",
            "rutter_id",
            "definition_version",
            "charter",
            "entered_node",
            "history",
            "active_child",
        }
        obj = _exact_object(value, expected, "ActiveRun")
        history = obj["history"]
        if not isinstance(history, (list, tuple)):
            raise RutterStateError("ActiveRun history must be an array")
        active_child = obj["active_child"]
        return cls(
            obj["run_id"],
            obj["rutter_id"],
            obj["definition_version"],
            Charter.from_json(obj["charter"]),
            EnteredNode.from_json(obj["entered_node"]),
            tuple(_history_entry_from_json(item) for item in history),
            None if active_child is None else ActiveChild.from_json(active_child),
        )


@dataclass(frozen=True)
class ActiveChild:
    call_id: str
    kind: str
    site: str
    attached_to_edge_id: str | None
    run: ActiveRun

    def __post_init__(self) -> None:
        _require_id(self.call_id, "call", RutterStateError)
        _validate_child_provenance(self.kind, self.site, self.attached_to_edge_id)
        if not isinstance(self.run, ActiveRun):
            raise RutterStateError("ActiveChild run must be an ActiveRun")

    def to_json(self) -> JsonObject:
        return _object_json(
            call_id=self.call_id,
            kind=self.kind,
            site=self.site,
            attached_to_edge_id=self.attached_to_edge_id,
            run=self.run.to_json(),
        )

    @classmethod
    def from_json(cls, value: object) -> ActiveChild:
        expected = {"call_id", "kind", "site", "attached_to_edge_id", "run"}
        obj = _exact_object(value, expected, "ActiveChild")
        return cls(
            obj["call_id"],
            obj["kind"],
            obj["site"],
            obj["attached_to_edge_id"],
            ActiveRun.from_json(obj["run"]),
        )


def _active_run_ids(root: ActiveRun) -> tuple[str, ...]:
    result: list[str] = []
    current = root
    while True:
        result.append(current.run_id)
        if current.active_child is None:
            return tuple(result)
        current = current.active_child.run


@dataclass(frozen=True)
class Reckoning:
    storage_version: int
    global_revision: int
    root: ActiveRun
    completed_runs: Mapping[str, CompletedRun]
    active_effect: JsonObject | None
    fault: JsonObject | None

    def __post_init__(self) -> None:
        _require_int(
            self.storage_version,
            "storage version",
            positive=True,
            error=RutterStateError,
        )
        _require_int(self.global_revision, "global revision", error=RutterStateError)
        if not isinstance(self.root, ActiveRun):
            raise RutterStateError("Reckoning root must be an ActiveRun")
        if not isinstance(self.completed_runs, Mapping):
            raise RutterStateError("completed_runs must be an object")
        completed: dict[str, CompletedRun] = {}
        for run_id, run in self.completed_runs.items():
            _require_id(run_id, "completed run", RutterStateError)
            if not isinstance(run, CompletedRun) or run.run_id != run_id:
                raise RutterStateError("completed run key must match its run ID")
            completed[run_id] = run
        active_ids = _active_run_ids(self.root)
        if len(active_ids) != len(set(active_ids)):
            raise RutterStateError("active run IDs must be unique")
        if set(active_ids) & set(completed):
            raise RutterStateError("active and completed run IDs must be disjoint")
        object.__setattr__(self, "completed_runs", MappingProxyType(completed))
        if self.active_effect is not None:
            object.__setattr__(
                self,
                "active_effect",
                _freeze_object(self.active_effect, "active effect", error=RutterStateError),
            )
        if self.fault is not None:
            object.__setattr__(
                self,
                "fault",
                _freeze_object(self.fault, "fault", error=RutterStateError),
            )

    def to_json(self) -> JsonObject:
        return _object_json(
            storage_version=self.storage_version,
            global_revision=self.global_revision,
            root=self.root.to_json(),
            completed_runs={
                run_id: run.to_json() for run_id, run in self.completed_runs.items()
            },
            active_effect=self.active_effect,
            fault=self.fault,
        )

    @classmethod
    def from_json(cls, value: object) -> Reckoning:
        expected = {
            "storage_version",
            "global_revision",
            "root",
            "completed_runs",
            "active_effect",
            "fault",
        }
        obj = _exact_object(value, expected, "Reckoning")
        completed = obj["completed_runs"]
        if not isinstance(completed, Mapping):
            raise RutterStateError("Reckoning completed_runs must be an object")
        return cls(
            obj["storage_version"],
            obj["global_revision"],
            ActiveRun.from_json(obj["root"]),
            {run_id: CompletedRun.from_json(run) for run_id, run in completed.items()},
            obj["active_effect"],
            obj["fault"],
        )


@dataclass(frozen=True)
class CompletedRunView:
    run_id: str
    rutter_id: str
    definition_version: int
    history: HistoryView
    result: RunResult


@dataclass(frozen=True)
class CallRecordView:
    call_id: str
    site: str
    attached_to_edge_id: str | None
    completed: CompletedRunView
    result: RunResult


@dataclass(frozen=True, init=False)
class HistoryView:
    _entries: tuple[HistoryEntry, ...] = field(repr=False)
    _completed_runs: Mapping[str, CompletedRun] = field(repr=False)

    def __init__(
        self,
        entries: tuple[HistoryEntry, ...],
        completed_runs: Mapping[str, CompletedRun] | None = None,
    ) -> None:
        frozen_entries = tuple(entries)
        _validate_history(frozen_entries)
        completed = {} if completed_runs is None else dict(completed_runs)
        for run_id, run in completed.items():
            _require_id(run_id, "completed run", RutterStateError)
            if not isinstance(run, CompletedRun) or run.run_id != run_id:
                raise RutterStateError("completed run key must match its run ID")
        object.__setattr__(self, "_entries", frozen_entries)
        object.__setattr__(self, "_completed_runs", MappingProxyType(completed))

    def entries(self) -> tuple[HistoryEntry, ...]:
        return self._entries

    def turns(self, state_id: str | None = None) -> tuple[Turn, ...]:
        return tuple(
            entry
            for entry in self._entries
            if isinstance(entry, Turn)
            and entry.response is not None
            and (state_id is None or entry.state_id == state_id)
        )

    def open_turn(self) -> Turn | None:
        return next(
            (
                entry
                for entry in self._entries
                if isinstance(entry, Turn) and entry.response is None
            ),
            None,
        )

    def actions(self, state_id: str | None = None) -> tuple[ActionRecord, ...]:
        return tuple(
            entry
            for entry in self._entries
            if isinstance(entry, ActionRecord)
            and (state_id is None or entry.state_id == state_id)
        )

    def _call_view(self, record: CallRecord) -> CallRecordView:
        try:
            completed = self._completed_runs[record.completed_run_id]
        except KeyError as exc:
            raise RutterStateError(
                f"CallRecord references missing completed run ID {record.completed_run_id!r}"
            ) from exc
        completed_history = HistoryView(completed.history, self._completed_runs)
        completed_view = CompletedRunView(
            completed.run_id,
            completed.rutter_id,
            completed.definition_version,
            completed_history,
            completed.result,
        )
        return CallRecordView(
            record.call_id,
            record.site_id,
            record.attached_to_edge_id,
            completed_view,
            completed.result,
        )

    def calls(self, site: str | None = None) -> tuple[CallRecordView, ...]:
        return tuple(
            self._call_view(entry)
            for entry in self._entries
            if isinstance(entry, CallRecord)
            and (site is None or entry.site_id == site)
        )

    def attached_calls(
        self,
        case_maker_id: str | None = None,
        edge_id: str | None = None,
    ) -> tuple[CallRecordView, ...]:
        return tuple(
            self._call_view(entry)
            for entry in self._entries
            if isinstance(entry, CallRecord)
            and entry.site_kind == "attached_case"
            and (case_maker_id is None or entry.site_id == case_maker_id)
            and (edge_id is None or entry.attached_to_edge_id == edge_id)
        )

    def done(self) -> DoneRecord | None:
        return next(
            (entry for entry in self._entries if isinstance(entry, DoneRecord)),
            None,
        )

    def latest_turn(self, state_id: str | None = None) -> Turn | None:
        values = self.turns(state_id)
        return values[-1] if values else None

    def latest_action(self, state_id: str | None = None) -> ActionRecord | None:
        values = self.actions(state_id)
        return values[-1] if values else None

    def latest_call(self, site: str | None = None) -> CallRecordView | None:
        values = self.calls(site)
        return values[-1] if values else None

    def require_latest_turn(self, state_id: str | None = None) -> Turn:
        value = self.latest_turn(state_id)
        if value is None:
            raise RutterDefinitionError("history has no matching Turn")
        return value

    def require_latest_action(self, state_id: str | None = None) -> ActionRecord:
        value = self.latest_action(state_id)
        if value is None:
            raise RutterDefinitionError("history has no matching ActionRecord")
        return value

    def require_latest_call(self, site: str | None = None) -> CallRecordView:
        value = self.latest_call(site)
        if value is None:
            raise RutterDefinitionError("history has no matching CallRecord")
        return value

    def strict_prefix(self, source: HistoryEntry) -> HistoryView:
        for index, entry in enumerate(self._entries):
            if entry is source:
                return HistoryView(self._entries[:index], self._completed_runs)
        try:
            index = self._entries.index(source)
        except ValueError as exc:
            raise RutterDefinitionError("source record is absent from history") from exc
        return HistoryView(self._entries[:index], self._completed_runs)


@dataclass(frozen=True)
class StateContext:
    charter: Charter
    state_id: str
    node_entry_id: str
    history: HistoryView

    def __post_init__(self) -> None:
        if not isinstance(self.charter, Charter):
            raise RutterDefinitionError("StateContext charter must be a Charter")
        _require_id(self.state_id, "state", RutterDefinitionError)
        _require_id(self.node_entry_id, "node entry", RutterDefinitionError)
        if not isinstance(self.history, HistoryView):
            raise RutterDefinitionError("StateContext history must be a HistoryView")


@dataclass(frozen=True)
class AnswerContext:
    state: StateContext
    message: Message
    response: Response


@dataclass(frozen=True)
class ActionContext:
    state: StateContext
    action_id: str

    def __post_init__(self) -> None:
        _require_id(self.action_id, "action", RutterDefinitionError)


@dataclass(frozen=True)
class EdgeContext:
    state: StateContext
    edge: JsonObject
    record: HistoryEntry

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge", _freeze_object(self.edge, "edge"))
        if not isinstance(self.record, (Turn, ActionRecord, CallRecord, DoneRecord)):
            raise RutterDefinitionError("EdgeContext record must be a history entry")


@dataclass(frozen=True)
class NodeView:
    rutter_id: str
    definition_version: int
    state_id: str
    node_entry_id: str | None
    depth: int
    condition: str

    def __post_init__(self) -> None:
        _require_id(self.rutter_id, "Rutter", RutterDefinitionError)
        _require_int(self.definition_version, "definition version", positive=True)
        _require_id(self.state_id, "state", RutterDefinitionError)
        _require_int(self.depth, "nesting depth")
        if self.condition not in {
            "ready",
            "terminal",
            "fault",
            "uncertain",
            "preview",
        }:
            raise RutterDefinitionError("NodeView condition is invalid")
        if self.node_entry_id is None:
            if self.condition != "preview":
                raise RutterDefinitionError(
                    "NodeView entrance ID may be absent only for preview"
                )
        else:
            _require_id(self.node_entry_id, "node entrance", RutterDefinitionError)
            if self.condition == "preview":
                raise RutterDefinitionError(
                    "preview NodeView must not have an entrance ID"
                )
