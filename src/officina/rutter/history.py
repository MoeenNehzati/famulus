"""Durable Rutter history, run trees, and structural validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, TypeAlias

from officina.rutter.values import (
    Charter,
    JsonObject,
    MachineResult,
    Message,
    RutterDefinitionError,
    RutterStateError,
    VoyageResult,
    _exact_object,
    _freeze_object,
    _object_json,
    _require_id,
    _require_int,
    _state_construct,
)


_MAX_ACTIVE_DEPTH = 64


def _encode_message_v3(message: Message, revision: int) -> JsonObject:
    """Project the public evolution envelope onto the unchanged v3 wire."""

    data = message.data
    instructions = message.instructions
    evolution = data["evolution"]
    assert isinstance(evolution, Mapping)
    return _freeze_object(
        {
            "instructions": {
                "text": instructions["text"],
                "answer": instructions.get("response_schema"),
            },
            "data": {
                "state": {
                    "id": evolution["id"],
                    "entry_id": evolution["entry_id"],
                    "revision": revision,
                },
                "payload": data["payload"],
            },
        },
        "Message v3",
        error=RutterStateError,
    )


def _decode_message_v3(
    value: object,
    evolution_id: object,
    evolution_entry_id: object,
    revision: object,
) -> Message:
    """Decode the unchanged v3 state envelope into the public projection."""

    obj = _exact_object(value, {"instructions", "data"}, "Message")
    instructions = _exact_object(
        obj["instructions"], {"text", "answer"}, "Message instructions"
    )
    data = _exact_object(obj["data"], {"state", "payload"}, "Message data")
    state = _exact_object(
        data["state"], {"id", "entry_id", "revision"}, "Message state"
    )
    state_id = _require_id(state["id"], "evolution", RutterStateError)
    state_entry_id = _require_id(state["entry_id"], "entry", RutterStateError)
    state_revision = _require_int(
        state["revision"], "revision", error=RutterStateError
    )
    if {
        "id": state_id,
        "entry_id": state_entry_id,
        "revision": state_revision,
    } != {
        "id": evolution_id,
        "entry_id": evolution_entry_id,
        "revision": revision,
    }:
        raise RutterStateError("Turn message coordinates do not match its record")
    public_instructions: dict[str, object] = {"text": instructions["text"]}
    if instructions["answer"] is not None:
        public_instructions["response_schema"] = instructions["answer"]
    return _state_construct(
        lambda: Message(
            public_instructions,
            {
                "evolution": {
                    "id": evolution_id,
                    "entry_id": evolution_entry_id,
                },
                "payload": data["payload"],
            },
        )
    )


def _encode_response_v3(response: JsonObject, revision: int) -> JsonObject:
    outcome = response["outcome"]
    return _freeze_object(
        {
            "revision": revision,
            "outcome": outcome,
            "evidence": {
                key: item for key, item in response.items() if key != "outcome"
            },
        },
        "Turn response v3",
        error=RutterStateError,
    )


def _decode_response_v3(value: object, revision: int) -> JsonObject:
    obj = _exact_object(value, {"revision", "outcome", "evidence"}, "Turn response")
    response_revision = _require_int(
        obj["revision"], "revision", error=RutterStateError
    )
    if response_revision != revision:
        raise RutterStateError("Turn response revision does not match its record")
    _require_id(obj["outcome"], "outcome", RutterStateError)
    evidence = _freeze_object(
        obj["evidence"], "Turn response evidence", error=RutterStateError
    )
    if set(evidence) & {"outcome", "revision"}:
        raise RutterStateError(
            "Turn response evidence contains reserved flat-response fields"
        )
    return _freeze_object(
        {"outcome": obj["outcome"], **evidence},
        "Turn response",
        error=RutterStateError,
    )


@dataclass(frozen=True)
class _EffectRecovery:
    machine_id: str
    owner_run_id: str
    evolution_entry_id: str
    evolution_id: str
    mode: str
    disposition: str
    result: MachineResult | None

    def __post_init__(self) -> None:
        for value in (
            self.machine_id,
            self.owner_run_id,
            self.evolution_entry_id,
            self.evolution_id,
        ):
            if type(value) is not str or not value:
                raise RutterStateError(
                    "active effect recovery has invalid identifiers"
                )
        if self.mode not in {"repeat-safe", "non-repeat-safe"}:
            raise RutterStateError("active effect recovery has invalid mode")
        if self.disposition not in {"planned", "completed", "uncertain"}:
            raise RutterStateError(
                "active effect recovery has invalid disposition"
            )
        if (self.disposition == "completed") != (self.result is not None):
            raise RutterStateError(
                "active effect recovery has inconsistent result"
            )
        if self.result is not None and not isinstance(self.result, MachineResult):
            raise RutterStateError("active effect recovery has invalid result")


@dataclass(frozen=True)
class KnownFault:
    category: str
    run_id: str
    evolution_id: str
    evolution_entry_id: str
    target_evolution_id: str | None
    transition_hook_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        required = (
            self.category,
            self.run_id,
            self.evolution_id,
            self.evolution_entry_id,
        )
        if any(type(value) is not str or not value for value in required):
            raise RutterStateError("known fault has invalid identifiers")
        if self.target_evolution_id is not None and (
            type(self.target_evolution_id) is not str or not self.target_evolution_id
        ):
            raise RutterStateError("known fault has invalid target state")
        maker_ids = tuple(self.transition_hook_ids)
        if any(type(maker_id) is not str or not maker_id for maker_id in maker_ids):
            raise RutterStateError("known fault has invalid case maker IDs")
        object.__setattr__(self, "transition_hook_ids", maker_ids)


@dataclass(frozen=True)
class OpaqueFault:
    wire: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "wire",
            _freeze_object(self.wire, "opaque fault", error=RutterStateError),
        )


@dataclass(frozen=True)
class EnteredEvolution:
    entry_id: str
    evolution_id: str

    def __post_init__(self) -> None:
        _require_id(self.entry_id, "entry", RutterStateError)
        _require_id(self.evolution_id, "state", RutterStateError)

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {"entry_id": self.entry_id, "state_id": self.evolution_id},
            "EnteredEvolution v3",
            error=RutterStateError,
        )

    @classmethod
    def from_json(cls, value: object) -> EnteredEvolution:
        obj = _exact_object(value, {"entry_id", "state_id"}, "EnteredEvolution")
        return cls(obj["entry_id"], obj["state_id"])


@dataclass(frozen=True)
class Turn:
    record_id: str
    evolution_entry_id: str
    evolution_id: str
    revision: int
    message: Message
    response: JsonObject | None

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.evolution_entry_id, "node entry", RutterStateError)
        _require_id(self.evolution_id, "state", RutterStateError)
        _require_int(self.revision, "revision", error=RutterStateError)
        if not isinstance(self.message, Message):
            raise RutterStateError("Turn message must be a Message")
        if self.response is not None:
            response = _freeze_object(
                self.response,
                "Turn response",
                error=RutterStateError,
            )
            if "revision" in response:
                raise RutterStateError(
                    "Turn response field 'revision' is reserved for engine metadata"
                )
            if "outcome" not in response:
                raise RutterStateError("Turn response must contain outcome")
            _require_id(response["outcome"], "outcome", RutterStateError)
            object.__setattr__(self, "response", response)
        evolution = self.message.data["evolution"]
        assert isinstance(evolution, Mapping)
        if (
            evolution["id"] != self.evolution_id
            or evolution["entry_id"] != self.evolution_entry_id
        ):
            raise RutterStateError("Turn message coordinates do not match its record")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "record_id": self.record_id,
                "node_entry_id": self.evolution_entry_id,
                "state_id": self.evolution_id,
                "revision": self.revision,
                "message": _encode_message_v3(self.message, self.revision),
                "response": (
                    None
                    if self.response is None
                    else _encode_response_v3(self.response, self.revision)
                ),
            },
            "Turn v3",
            error=RutterStateError,
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
            _decode_message_v3(
                obj["message"],
                obj["state_id"],
                obj["node_entry_id"],
                obj["revision"],
            ),
            None
            if response is None
            else _decode_response_v3(response, obj["revision"]),
        )


@dataclass(frozen=True)
class MachineRecord:
    record_id: str
    machine_id: str
    evolution_entry_id: str
    evolution_id: str
    mode: str
    result: MachineResult

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.machine_id, "action", RutterStateError)
        _require_id(self.evolution_entry_id, "node entry", RutterStateError)
        _require_id(self.evolution_id, "state", RutterStateError)
        if self.mode not in {"pure", "repeat-safe", "non-repeat-safe"}:
            raise RutterStateError("MachineRecord mode is invalid")
        if not isinstance(self.result, MachineResult):
            raise RutterStateError("MachineRecord result must be an MachineResult")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "record_id": self.record_id,
                "action_id": self.machine_id,
                "node_entry_id": self.evolution_entry_id,
                "state_id": self.evolution_id,
                "mode": self.mode,
                "result": self.result.to_json(),
            },
            "MachineRecord v3",
            error=RutterStateError,
        )

    @classmethod
    def from_json(cls, value: object) -> MachineRecord:
        expected = {
            "record_id",
            "action_id",
            "node_entry_id",
            "state_id",
            "mode",
            "result",
        }
        obj = _exact_object(value, expected, "MachineRecord")
        return cls(
            obj["record_id"],
            obj["action_id"],
            obj["node_entry_id"],
            obj["state_id"],
            obj["mode"],
            MachineResult.from_json(obj["result"]),
        )


def _validate_child_provenance(
    kind: object, site: object, attached_to_transition_id: object
) -> None:
    if kind not in {"explicit_call", "attached_case"}:
        raise RutterStateError("child provenance kind is invalid")
    _require_id(site, "child site", RutterStateError)
    if kind == "explicit_call" and attached_to_transition_id is not None:
        raise RutterStateError("explicit-call child provenance cannot name an edge")
    if kind == "attached_case" and attached_to_transition_id is None:
        raise RutterStateError("attached-case child provenance must name an edge")
    if attached_to_transition_id is not None:
        _require_id(attached_to_transition_id, "attached edge", RutterStateError)


@dataclass(frozen=True)
class SubRutterRecord:
    invocation_id: str
    evolution_entry_id: str
    origin_evolution_id: str | None
    transition_hook_id: str | None
    attached_to_transition_id: str | None
    completed_voyage_instance_id: str

    def __post_init__(self) -> None:
        _require_id(self.invocation_id, "invocation", RutterStateError)
        _require_id(self.evolution_entry_id, "evolution entry", RutterStateError)
        if (self.origin_evolution_id is None) == (self.transition_hook_id is None):
            raise RutterStateError(
                "SubRutterRecord requires exactly one origin"
            )
        if self.origin_evolution_id is not None:
            _require_id(
                self.origin_evolution_id,
                "origin evolution",
                RutterStateError,
            )
            if self.attached_to_transition_id is not None:
                raise RutterStateError(
                    "explicit SubRutter origin cannot name a transition"
                )
        else:
            assert self.transition_hook_id is not None
            _require_id(
                self.transition_hook_id,
                "transition hook",
                RutterStateError,
            )
            if self.attached_to_transition_id is None:
                raise RutterStateError(
                    "transition-hook origin must name a transition"
                )
            _require_id(
                self.attached_to_transition_id,
                "attached transition",
                RutterStateError,
            )
        _require_id(self.completed_voyage_instance_id, "completed run", RutterStateError)

    def to_json(self) -> JsonObject:
        site_kind = (
            "explicit_call"
            if self.origin_evolution_id is not None
            else "attached_case"
        )
        site_id = (
            self.origin_evolution_id
            if self.origin_evolution_id is not None
            else self.transition_hook_id
        )
        return _freeze_object(
            {
                "call_id": self.invocation_id,
                "node_entry_id": self.evolution_entry_id,
                "site_kind": site_kind,
                "site_id": site_id,
                "attached_to_edge_id": self.attached_to_transition_id,
                "completed_run_id": self.completed_voyage_instance_id,
            },
            "SubRutterRecord v3",
            error=RutterStateError,
        )

    @classmethod
    def from_json(cls, value: object) -> SubRutterRecord:
        expected = {
            "call_id",
            "node_entry_id",
            "site_kind",
            "site_id",
            "attached_to_edge_id",
            "completed_run_id",
        }
        obj = _exact_object(value, expected, "SubRutterRecord")
        kind = obj["site_kind"]
        site = obj["site_id"]
        _validate_child_provenance(kind, site, obj["attached_to_edge_id"])
        return cls(
            obj["call_id"],
            obj["node_entry_id"],
            site if kind == "explicit_call" else None,
            site if kind == "attached_case" else None,
            obj["attached_to_edge_id"],
            obj["completed_run_id"],
        )


@dataclass(frozen=True)
class TerminalRecord:
    record_id: str
    evolution_entry_id: str
    evolution_id: str
    result: VoyageResult

    def __post_init__(self) -> None:
        _require_id(self.record_id, "record", RutterStateError)
        _require_id(self.evolution_entry_id, "node entry", RutterStateError)
        _require_id(self.evolution_id, "state", RutterStateError)
        if not isinstance(self.result, VoyageResult):
            raise RutterStateError("TerminalRecord result must be a VoyageResult")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "record_id": self.record_id,
                "node_entry_id": self.evolution_entry_id,
                "state_id": self.evolution_id,
                "result": self.result.to_json(),
            },
            "TerminalRecord v3",
            error=RutterStateError,
        )

    @classmethod
    def from_json(cls, value: object) -> TerminalRecord:
        expected = {"record_id", "node_entry_id", "state_id", "result"}
        obj = _exact_object(value, expected, "TerminalRecord")
        return cls(
            obj["record_id"],
            obj["node_entry_id"],
            obj["state_id"],
            VoyageResult.from_json(obj["result"]),
        )


HistoryEntry: TypeAlias = Turn | MachineRecord | SubRutterRecord | TerminalRecord


def _record_id(record: HistoryEntry) -> str:
    return record.invocation_id if isinstance(record, SubRutterRecord) else record.record_id


def _history_entry_from_json(value: object) -> HistoryEntry:
    if not isinstance(value, Mapping):
        raise RutterStateError("history entries must be objects")
    keys = set(value)
    if "message" in keys:
        return Turn.from_json(value)
    if "action_id" in keys:
        return MachineRecord.from_json(value)
    if "call_id" in keys:
        return SubRutterRecord.from_json(value)
    if "result" in keys:
        return TerminalRecord.from_json(value)
    raise RutterStateError("history entry has invalid fields")


def _validate_history(entries: tuple[HistoryEntry, ...]) -> None:
    if any(not isinstance(entry, (Turn, MachineRecord, SubRutterRecord, TerminalRecord)) for entry in entries):
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
        index for index, entry in enumerate(entries) if isinstance(entry, TerminalRecord)
    ]
    if len(done_indexes) > 1:
        raise RutterStateError("history may contain only one TerminalRecord")
    if done_indexes:
        done_index = done_indexes[0]
        done = entries[done_index]
        assert isinstance(done, TerminalRecord)
        for entry in entries[done_index + 1 :]:
            if (
                not isinstance(entry, SubRutterRecord)
                or entry.transition_hook_id is None
                or entry.evolution_entry_id != done.evolution_entry_id
            ):
                raise RutterStateError(
                    "entries after a TerminalRecord must be attached-case CallRecords "
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
        if sum(isinstance(entry, TerminalRecord) for entry in history) != 1:
            raise RutterStateError("CompletedRun requires one TerminalRecord authority")
        object.__setattr__(self, "history", history)

    @property
    def result(self) -> VoyageResult:
        return next(
            entry.result for entry in self.history if isinstance(entry, TerminalRecord)
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
    entered_evolution: EnteredEvolution
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
        if not isinstance(self.entered_evolution, EnteredEvolution):
            raise RutterStateError("ActiveRun entered_evolution must be an EnteredEvolution")
        history = tuple(self.history)
        _validate_history(history)
        if self.active_child is not None and not isinstance(self.active_child, ActiveChild):
            raise RutterStateError("ActiveRun active_child must be an ActiveChild or null")
        done = next(
            (entry for entry in history if isinstance(entry, TerminalRecord)),
            None,
        )
        if done is not None:
            if (
                done.evolution_entry_id != self.entered_evolution.entry_id
                or done.evolution_id != self.entered_evolution.evolution_id
            ):
                raise RutterStateError(
                    "ActiveRun TerminalRecord must match the current entered node"
                )
            if self.active_child is not None and (
                self.active_child.kind != "attached_case"
                or self.active_child.attached_to_transition_id != done.record_id
            ):
                raise RutterStateError(
                    "ActiveRun TerminalRecord child must be an attached case bound "
                    "to that TerminalRecord"
                )
        object.__setattr__(self, "history", history)

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "run_id": self.run_id,
                "rutter_id": self.rutter_id,
                "definition_version": self.definition_version,
                "charter": self.charter.to_json(),
                "entered_node": self.entered_evolution.to_json(),
                "history": tuple(entry.to_json() for entry in self.history),
                "active_child": (
                    None
                    if self.active_child is None
                    else self.active_child.to_json()
                ),
            },
            "ActiveRun v3",
            error=RutterStateError,
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
            EnteredEvolution.from_json(obj["entered_node"]),
            tuple(_history_entry_from_json(item) for item in history),
            None if active_child is None else ActiveChild.from_json(active_child),
        )


@dataclass(frozen=True)
class ActiveChild:
    invocation_id: str
    kind: str
    site: str
    attached_to_transition_id: str | None
    run: ActiveRun

    def __post_init__(self) -> None:
        _require_id(self.invocation_id, "call", RutterStateError)
        _validate_child_provenance(self.kind, self.site, self.attached_to_transition_id)
        if not isinstance(self.run, ActiveRun):
            raise RutterStateError("ActiveChild run must be an ActiveRun")

    def to_json(self) -> JsonObject:
        return _freeze_object(
            {
                "call_id": self.invocation_id,
                "kind": self.kind,
                "site": self.site,
                "attached_to_edge_id": self.attached_to_transition_id,
                "run": self.run.to_json(),
            },
            "ActiveChild v3",
            error=RutterStateError,
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
    active_effect: _EffectRecovery | None
    fault: KnownFault | OpaqueFault | None

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
        if self.active_effect is not None and not isinstance(
            self.active_effect, _EffectRecovery
        ):
            raise RutterStateError(
                "Reckoning active_effect must be typed recovery authority or null"
            )
        if self.fault is not None and not isinstance(
            self.fault, (KnownFault, OpaqueFault)
        ):
            raise RutterStateError(
                "Reckoning fault must be typed fault authority or null"
            )
        self.validate()

    def validate(self) -> None:
        """Validate definition-independent run-tree and history invariants."""

        active_runs: list[ActiveRun] = []
        active_invocation_ids: list[str] = []
        current = self.root
        while True:
            active_runs.append(current)
            if len(active_runs) > _MAX_ACTIVE_DEPTH:
                raise RutterStateError(
                    "Reckoning active-child nesting is too deep"
                )
            child = current.active_child
            if child is None:
                break
            active_invocation_ids.append(child.invocation_id)
            current = child.run

        run_ids = [run.run_id for run in active_runs]
        run_ids.extend(self.completed_runs)
        if len(run_ids) != len(set(run_ids)):
            raise RutterStateError("duplicate run IDs")

        entrance_authorities: dict[str, tuple[str, str]] = {}

        def bind_entrance(entry_id: str, evolution_id: str, owner_id: str) -> None:
            authority = entrance_authorities.get(entry_id)
            if authority is None:
                entrance_authorities[entry_id] = (owner_id, evolution_id)
                return
            authority_owner, authority_state = authority
            if authority_owner != owner_id:
                raise RutterStateError("entrance owner is not unique")
            if authority_state != evolution_id:
                raise RutterStateError(
                    "entrance state identity is inconsistent"
                )

        for run in active_runs:
            bind_entrance(
                run.entered_evolution.entry_id,
                run.entered_evolution.evolution_id,
                run.run_id,
            )

        invocation_ids = set(active_invocation_ids)
        if len(invocation_ids) != len(active_invocation_ids):
            raise RutterStateError("duplicate call ID")
        history_ids: set[str] = set()
        machine_ids: set[str] = set()
        references = {run_id: 0 for run_id in self.completed_runs}
        graph = {run_id: set() for run_id in self.completed_runs}
        attachment_authorities: set[tuple[str, str]] = set()

        owners: list[tuple[str, tuple[HistoryEntry, ...], bool]] = [
            (run.run_id, run.history, False) for run in active_runs
        ]
        owners.extend(
            (run_id, run.history, True)
            for run_id, run in self.completed_runs.items()
        )
        active_by_id = {run.run_id: run for run in active_runs}
        for owner_id, history, owner_completed in owners:
            seen_done = False
            edge_sources: dict[
                str, Turn | MachineRecord | SubRutterRecord | TerminalRecord
            ] = {}
            for entry in history:
                identity = _record_id(entry)
                if identity in history_ids:
                    raise RutterStateError("duplicate history record ID")
                history_ids.add(identity)
                if isinstance(entry, (Turn, MachineRecord, TerminalRecord)):
                    bind_entrance(
                        entry.evolution_entry_id,
                        entry.evolution_id,
                        owner_id,
                    )
                if isinstance(entry, TerminalRecord):
                    seen_done = True
                    edge_sources[entry.record_id] = entry
                    continue
                if seen_done and not (
                    isinstance(entry, SubRutterRecord)
                    and entry.transition_hook_id is not None
                ):
                    raise RutterStateError(
                        "non-attached record follows TerminalRecord"
                    )
                if isinstance(entry, MachineRecord):
                    if entry.machine_id in machine_ids:
                        raise RutterStateError("duplicate action ID")
                    machine_ids.add(entry.machine_id)
                    edge_sources[entry.record_id] = entry
                    continue
                if isinstance(entry, Turn):
                    edge_sources[entry.record_id] = entry
                    continue
                if not isinstance(entry, SubRutterRecord):
                    continue
                if entry.invocation_id in invocation_ids:
                    raise RutterStateError("duplicate call ID")
                invocation_ids.add(entry.invocation_id)
                if entry.completed_voyage_instance_id not in references:
                    raise RutterStateError(
                        "SubRutterRecord references unknown completed run"
                    )
                references[entry.completed_voyage_instance_id] += 1
                if owner_completed:
                    graph[owner_id].add(entry.completed_voyage_instance_id)
                if entry.transition_hook_id is not None:
                    assert entry.attached_to_transition_id is not None
                    source = edge_sources.get(entry.attached_to_transition_id)
                    if source is None:
                        raise RutterStateError(
                            "attached edge source must name exactly one earlier "
                            "record in the same run"
                        )
                    if source.evolution_entry_id != entry.evolution_entry_id:
                        raise RutterStateError(
                            "attached SubRutterRecord must share its source entrance"
                        )
                    authority = (
                        entry.transition_hook_id,
                        entry.attached_to_transition_id,
                    )
                    if authority in attachment_authorities:
                        raise RutterStateError(
                            "duplicate attachment authority"
                        )
                    attachment_authorities.add(authority)
                else:
                    assert entry.origin_evolution_id is not None
                    bind_entrance(
                        entry.evolution_entry_id,
                        entry.origin_evolution_id,
                        owner_id,
                    )
                    edge_sources[entry.invocation_id] = entry

            active_owner = active_by_id.get(owner_id)
            if active_owner is None or active_owner.active_child is None:
                continue
            active_child = active_owner.active_child
            if active_child.kind != "attached_case":
                continue
            assert active_child.attached_to_transition_id is not None
            source = edge_sources.get(active_child.attached_to_transition_id)
            if source is None:
                raise RutterStateError(
                    "active attached edge source must name exactly one prior "
                    "record in the same parent run"
                )
            if source.evolution_entry_id != active_owner.entered_evolution.entry_id:
                raise RutterStateError(
                    "active attached child must share its source entrance"
                )
            authority = (
                active_child.site,
                active_child.attached_to_transition_id,
            )
            if authority in attachment_authorities:
                raise RutterStateError("duplicate attachment authority")
            attachment_authorities.add(authority)

        if any(count != 1 for count in references.values()):
            raise RutterStateError(
                "every completed run must be referenced by exactly one SubRutterRecord"
            )

        indegree = {run_id: 0 for run_id in graph}
        for children in graph.values():
            for child_id in children:
                indegree[child_id] += 1
        ready = [run_id for run_id, count in indegree.items() if count == 0]
        visited = 0
        while ready:
            run_id = ready.pop()
            visited += 1
            for child_id in graph[run_id]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)
        if visited != len(graph):
            raise RutterStateError("completed-run references are cyclic")

        effect = self.active_effect
        if effect is None:
            return
        leaf = active_runs[-1]
        if effect.owner_run_id != leaf.run_id:
            raise RutterStateError(
                "active effect owner must be the deepest active run"
            )
        if (
            effect.evolution_entry_id != leaf.entered_evolution.entry_id
            or effect.evolution_id != leaf.entered_evolution.evolution_id
        ):
            raise RutterStateError(
                "active effect recovery has stale node coordinates"
            )
        if effect.machine_id in machine_ids:
            raise RutterStateError(
                "active effect action ID was already consumed"
            )

    def to_json(self) -> JsonObject:
        if isinstance(self.fault, OpaqueFault):
            raise RutterStateError("opaque fault wire is private")
        if self.active_effect is not None or self.fault is not None:
            raise RutterStateError(
                "typed recovery and fault authority require the private storage codec"
            )
        return _object_json(
            storage_version=self.storage_version,
            global_revision=self.global_revision,
            root=self.root.to_json(),
            completed_runs={
                run_id: run.to_json() for run_id, run in self.completed_runs.items()
            },
            active_effect=None,
            fault=None,
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
        if obj["active_effect"] is not None or obj["fault"] is not None:
            raise RutterStateError(
                "typed recovery and fault authority require the private storage codec"
            )
        return cls(
            obj["storage_version"],
            obj["global_revision"],
            ActiveRun.from_json(obj["root"]),
            {run_id: CompletedRun.from_json(run) for run_id, run in completed.items()},
            None,
            None,
        )


@dataclass(frozen=True)
class CompletedVoyageView:
    voyage_instance_id: str
    rutter_id: str
    definition_version: int
    history: HistoryView
    result: VoyageResult


@dataclass(frozen=True)
class Transition:
    transition_id: str
    source_entry_id: str
    source: str
    outcome: str
    target: str | None

    def __post_init__(self) -> None:
        _require_id(self.transition_id, "transition", RutterStateError)
        _require_id(self.source_entry_id, "source entry", RutterStateError)
        _require_id(self.source, "transition source", RutterStateError)
        _require_id(self.outcome, "transition outcome", RutterStateError)
        if self.target is not None:
            _require_id(self.target, "transition target", RutterStateError)

    def to_json(self) -> JsonObject:
        return _object_json(
            transition_id=self.transition_id,
            source_entry_id=self.source_entry_id,
            source=self.source,
            outcome=self.outcome,
            target=self.target,
        )


@dataclass(frozen=True)
class SubRutterRecordView:
    invocation_id: str
    origin_evolution_id: str | None
    transition_hook_id: str | None
    attached_to_transition_id: str | None
    completed: CompletedVoyageView
    result: VoyageResult


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

    def turns(self, evolution_id: str | None = None) -> tuple[Turn, ...]:
        return tuple(
            entry
            for entry in self._entries
            if isinstance(entry, Turn)
            and entry.response is not None
            and (evolution_id is None or entry.evolution_id == evolution_id)
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

    def machines(self, evolution_id: str | None = None) -> tuple[MachineRecord, ...]:
        return tuple(
            entry
            for entry in self._entries
            if isinstance(entry, MachineRecord)
            and (evolution_id is None or entry.evolution_id == evolution_id)
        )

    def _subrutter_view(
        self,
        record: SubRutterRecord,
    ) -> SubRutterRecordView:
        try:
            completed = self._completed_runs[record.completed_voyage_instance_id]
        except KeyError as exc:
            raise RutterStateError(
                f"SubRutterRecord references missing completed run ID {record.completed_voyage_instance_id!r}"
            ) from exc
        completed_history = HistoryView(completed.history, self._completed_runs)
        completed_view = CompletedVoyageView(
            completed.run_id,
            completed.rutter_id,
            completed.definition_version,
            completed_history,
            completed.result,
        )
        return SubRutterRecordView(
            record.invocation_id,
            record.origin_evolution_id,
            record.transition_hook_id,
            record.attached_to_transition_id,
            completed_view,
            completed.result,
        )

    def subrutters(
        self,
        *,
        origin_evolution_id: str | None = None,
        transition_hook_id: str | None = None,
    ) -> tuple[SubRutterRecordView, ...]:
        if origin_evolution_id is not None and transition_hook_id is not None:
            raise RutterDefinitionError(
                "sub-Rutter origin filters are mutually exclusive"
            )
        return tuple(
            self._subrutter_view(entry)
            for entry in self._entries
            if isinstance(entry, SubRutterRecord)
            and (
                origin_evolution_id is None
                or entry.origin_evolution_id == origin_evolution_id
            )
            and (
                transition_hook_id is None
                or entry.transition_hook_id == transition_hook_id
            )
        )

    def hook_runs(
        self,
        *,
        transition_hook_id: str,
        transition_id: str,
    ) -> tuple[SubRutterRecordView, ...]:
        _require_id(
            transition_hook_id,
            "transition hook",
            RutterDefinitionError,
        )
        _require_id(transition_id, "transition", RutterDefinitionError)
        return tuple(
            self._subrutter_view(entry)
            for entry in self._entries
            if isinstance(entry, SubRutterRecord)
            and entry.transition_hook_id == transition_hook_id
            and entry.attached_to_transition_id == transition_id
        )

    def terminal(self) -> TerminalRecord | None:
        return next(
            (entry for entry in self._entries if isinstance(entry, TerminalRecord)),
            None,
        )

    def latest_turn(self, evolution_id: str | None = None) -> Turn | None:
        values = self.turns(evolution_id)
        return values[-1] if values else None

    def latest_machine(self, evolution_id: str | None = None) -> MachineRecord | None:
        values = self.machines(evolution_id)
        return values[-1] if values else None

    def latest_subrutter(
        self,
        *,
        origin_evolution_id: str | None = None,
        transition_hook_id: str | None = None,
    ) -> SubRutterRecordView | None:
        values = self.subrutters(
            origin_evolution_id=origin_evolution_id,
            transition_hook_id=transition_hook_id,
        )
        return values[-1] if values else None

    def require_latest_turn(self, evolution_id: str | None = None) -> Turn:
        value = self.latest_turn(evolution_id)
        if value is None:
            raise RutterDefinitionError("history has no matching Turn")
        return value

    def require_latest_machine(self, evolution_id: str | None = None) -> MachineRecord:
        value = self.latest_machine(evolution_id)
        if value is None:
            raise RutterDefinitionError("history has no matching MachineRecord")
        return value

    def require_latest_subrutter(
        self,
        *,
        origin_evolution_id: str | None = None,
        transition_hook_id: str | None = None,
    ) -> SubRutterRecordView:
        value = self.latest_subrutter(
            origin_evolution_id=origin_evolution_id,
            transition_hook_id=transition_hook_id,
        )
        if value is None:
            raise RutterDefinitionError("history has no matching SubRutterRecord")
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


__all__ = (
    "CompletedVoyageView",
    "EnteredEvolution",
    "HistoryEntry",
    "HistoryView",
    "MachineRecord",
    "SubRutterRecord",
    "SubRutterRecordView",
    "TerminalRecord",
    "Transition",
    "Turn",
)
