"""Bind stateless Rutter definitions to one durable Reckoning authority."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping, TypeAlias

from officina.rutter.model import (
    Action,
    ActiveRun,
    Call,
    Charter,
    Done,
    JsonValue,
    NodeView,
    Prompt,
    Reckoning,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    State,
    Turn,
    ValidationReport,
)
from officina.rutter.storage import _ReckoningStore, _confined_reckoning_path


__all__ = ("RutterRegistry",)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_MISSING = object()
_Registration: TypeAlias = type[Rutter] | Rutter | object
_RUN_STATE_NAMES = frozenset(
    {
        "store",
        "reckoning",
        "path",
        "revision",
        "run",
        "run_data",
        "charter",
        "fix",
    }
)


def _require_id(value: object, label: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value) or value in {".", ".."}:
        raise RutterDefinitionError(f"{label} must be a nonempty stable token")
    return value


def _require_callback(callback: object, arity: int, label: str) -> None:
    if not callable(callback):
        raise RutterDefinitionError(f"{label} must be callable")
    try:
        parameters = tuple(inspect.signature(callback).parameters.values())
    except (TypeError, ValueError) as exc:
        raise RutterDefinitionError(f"{label} must have an inspectable signature") from exc
    if (
        len(parameters) != arity
        or any(
            parameter.kind
            not in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        )
    ):
        noun = "argument" if arity == 1 else "arguments"
        raise RutterDefinitionError(f"{label} must accept exactly {arity} {noun}")


def _construct_definition(source: _Registration) -> Rutter:
    if isinstance(source, Rutter):
        return source
    if not callable(source):
        raise RutterDefinitionError(
            "Rutter registrants must be definitions or no-argument factories"
        )
    try:
        inspect.signature(source).bind()
    except (TypeError, ValueError) as exc:
        raise RutterDefinitionError(
            "Rutter definition factories must be callable without arguments"
        ) from exc
    try:
        definition = source()
    except Exception as exc:
        raise RutterDefinitionError("Rutter definition construction failed") from exc
    if not isinstance(definition, Rutter):
        raise RutterDefinitionError("Rutter definition factory must return a Rutter")
    return definition


@dataclass(frozen=True)
class _BoundDefinition:
    definition: Rutter
    rutter_id: str
    definition_version: int
    start_state: str
    allow_multiple_cases_at_once: bool
    states: Mapping[str, State]
    case_makers: tuple[object, ...]
    case_makers_by_id: Mapping[str, object]
    children: tuple[_BoundDefinition, ...]

    @property
    def identity(self) -> tuple[str, int]:
        return self.rutter_id, self.definition_version

    def require_current_metadata(self) -> None:
        current = (
            getattr(self.definition, "rutter_id", None),
            getattr(self.definition, "definition_version", None),
            getattr(self.definition, "start_state", None),
            getattr(self.definition, "allow_multiple_cases_at_once", None),
        )
        frozen = (
            self.rutter_id,
            self.definition_version,
            self.start_state,
            self.allow_multiple_cases_at_once,
        )
        if current != frozen:
            raise RutterDefinitionError("Rutter metadata changed after binding")

    def reachable(self) -> Mapping[tuple[str, int], _BoundDefinition]:
        found: dict[tuple[str, int], _BoundDefinition] = {}
        pending = [self]
        while pending:
            definition = pending.pop()
            if definition.identity in found:
                continue
            found[definition.identity] = definition
            pending.extend(definition.children)
        return MappingProxyType(found)


class _DefinitionBinder:
    """Construct and validate each definition once without executing callbacks."""

    def __init__(self) -> None:
        self._by_source: dict[int, tuple[object, _BoundDefinition]] = {}
        self._source_by_id: dict[str, tuple[object, int]] = {}
        self._visiting: list[int] = []

    def bind(self, source: _Registration) -> _BoundDefinition:
        source_id = id(source)
        cached = self._by_source.get(source_id)
        if cached is not None and cached[0] is source:
            return cached[1]
        if source_id in self._visiting:
            raise RutterDefinitionError("recursive definition-call cycle")

        self._visiting.append(source_id)
        try:
            definition = _construct_definition(source)
            metadata = self._metadata(definition)
            rutter_id, definition_version, start_state, allow_multiple = metadata
            existing = self._source_by_id.get(rutter_id)
            if existing is not None and existing[0] is not source:
                raise RutterDefinitionError(
                    f"Rutter child identity conflict for {rutter_id!r}"
                )
            if existing is not None and existing[1] != definition_version:
                raise RutterDefinitionError(
                    f"Rutter child identity conflict for {rutter_id!r}"
                )
            self._source_by_id[rutter_id] = (source, definition_version)

            states = self._freeze_states(definition)
            if start_state not in states:
                raise RutterDefinitionError(
                    "start_state must name one declared state"
                )
            case_makers, makers_by_id = self._freeze_case_makers(definition)
            child_sources = self._validate_graph(states, case_makers)
            children = tuple(self.bind(child) for child in child_sources)
            bound = _BoundDefinition(
                definition,
                rutter_id,
                definition_version,
                start_state,
                allow_multiple,
                states,
                case_makers,
                makers_by_id,
                children,
            )
            self._by_source[source_id] = (source, bound)
            return bound
        finally:
            self._visiting.pop()

    @staticmethod
    def _metadata(definition: Rutter) -> tuple[str, int, str, bool]:
        rutter_id = _require_id(getattr(definition, "rutter_id", None), "Rutter ID")
        definition_version = getattr(definition, "definition_version", None)
        if type(definition_version) is not int or definition_version < 1:
            raise RutterDefinitionError(
                "definition_version must be an exact positive integer"
            )
        start_state = _require_id(
            getattr(definition, "start_state", None), "start_state"
        )
        allow_multiple = getattr(
            definition, "allow_multiple_cases_at_once", False
        )
        if type(allow_multiple) is not bool:
            raise RutterDefinitionError(
                "allow_multiple_cases_at_once must be an exact Boolean"
            )
        for attribute in vars(definition):
            if attribute.lstrip("_") in _RUN_STATE_NAMES:
                raise RutterDefinitionError(
                    f"Rutter definition stores run state in {attribute!r}"
                )
        return rutter_id, definition_version, start_state, allow_multiple

    @staticmethod
    def _freeze_states(definition: Rutter) -> Mapping[str, State]:
        try:
            authored = definition.define_states()
        except Exception as exc:
            raise RutterDefinitionError("define_states() failed") from exc
        if not isinstance(authored, Mapping):
            raise RutterDefinitionError("define_states() must return a mapping")
        states: dict[str, State] = {}
        for state_id, state in authored.items():
            state_id = _require_id(state_id, "state ID")
            if state_id in states:
                raise RutterDefinitionError(f"duplicate state ID {state_id!r}")
            if not isinstance(state, (Prompt, Action, Call, Done)):
                raise RutterDefinitionError(
                    f"state {state_id!r} must be Prompt, Action, Call, or Done"
                )
            states[state_id] = state
        if not states:
            raise RutterDefinitionError("state mapping must not be empty")
        return MappingProxyType(states)

    @staticmethod
    def _freeze_case_makers(
        definition: Rutter,
    ) -> tuple[tuple[object, ...], Mapping[str, object]]:
        try:
            authored = definition.define_case_makers()
        except Exception as exc:
            raise RutterDefinitionError("define_case_makers() failed") from exc
        if type(authored) is not tuple:
            raise RutterDefinitionError("define_case_makers() must return a tuple")
        makers: dict[str, object] = {}
        for maker in authored:
            maker_id = _require_id(getattr(maker, "id", None), "CaseMaker ID")
            if maker_id in makers:
                raise RutterDefinitionError(f"duplicate CaseMaker ID {maker_id!r}")
            child = getattr(maker, "child", None)
            if not isinstance(child, type) or not issubclass(child, Rutter):
                raise RutterDefinitionError("CaseMaker child must be a Rutter class")
            _require_callback(
                getattr(maker, "charter", None), 1, "CaseMaker charter"
            )
            makers[maker_id] = maker
        return tuple(authored), MappingProxyType(makers)

    def _validate_graph(
        self,
        states: Mapping[str, State],
        case_makers: tuple[object, ...],
    ) -> tuple[_Registration, ...]:
        children: list[_Registration] = []
        for state in states.values():
            if isinstance(state, Prompt):
                if not state.answer.outcomes:
                    raise RutterDefinitionError(
                        "Prompt answer must declare at least one outcome"
                    )
                _require_callback(state.data, 1, "Prompt data")
                _require_callback(state.validate, 1, "Prompt validate")
                self._validate_then(
                    state.then,
                    states,
                    1,
                    "Prompt then",
                    outcomes=frozenset(state.answer.outcomes),
                )
            elif isinstance(state, Action):
                _require_callback(state.run, 1, "Action run")
                self._validate_then(state.then, states, 2, "Action then")
            elif isinstance(state, Call):
                _require_callback(state.charter, 1, "Call charter")
                self._validate_then(state.then, states, 2, "Call then")
                children.append(state.child)
            elif isinstance(state, Done) and callable(state.result):
                _require_callback(state.result, 1, "Done result")
        children.extend(getattr(maker, "child") for maker in case_makers)
        return tuple(children)

    @staticmethod
    def _validate_then(
        then: object,
        states: Mapping[str, State],
        callable_arity: int,
        label: str,
        *,
        outcomes: frozenset[str] | None = None,
    ) -> None:
        targets: tuple[object, ...]
        if type(then) is str:
            targets = (then,)
        elif isinstance(then, Mapping):
            if not then:
                raise RutterDefinitionError(f"{label} routes must not be empty")
            if outcomes is not None and set(then) != outcomes:
                raise RutterDefinitionError(
                    "Prompt routes must exactly match declared outcomes"
                )
            targets = tuple(then.values())
        elif callable(then):
            _require_callback(then, callable_arity, label)
            return
        else:
            raise RutterDefinitionError(f"{label} has invalid routing")
        for target in targets:
            if target not in states:
                raise RutterDefinitionError(
                    f"{label} names undeclared successor {target!r}"
                )


class _BoundVoyage:
    """Own one store/Reckoning pair and the Task 5 operating seam."""

    def __init__(
        self,
        definition: _BoundDefinition,
        path: Path,
        reckoning: Reckoning,
        *,
        create: bool,
    ) -> None:
        self._definition = definition
        self._definitions = definition.reachable()
        self._reckoning = reckoning
        self._store = _ReckoningStore(
            path,
            semantic_validator=self._validate_reckoning,
        )
        self._validate_reckoning(reckoning)
        if create:
            self._store.create(reckoning)

    def _validate_reckoning(self, reckoning: Reckoning) -> None:
        active: list[tuple[ActiveRun, _BoundDefinition]] = []
        run = reckoning.root
        while True:
            identity = (run.rutter_id, run.definition_version)
            definition = self._definitions.get(identity)
            if definition is None:
                raise RutterStateError(
                    f"active Rutter definition {identity!r} is unavailable"
                )
            definition.require_current_metadata()
            state = definition.states.get(run.entered_node.state_id)
            if state is None:
                raise RutterStateError(
                    "active state is absent from its bound Rutter definition"
                )
            if isinstance(state, Prompt):
                self._validate_prompt_authority(
                    run,
                    state,
                    reckoning.global_revision,
                    reckoning.fault,
                    is_leaf=run.active_child is None,
                )
            active.append((run, definition))
            if run.active_child is None:
                break
            run = run.active_child.run

        for parent, definition in active:
            active_child = parent.active_child
            if active_child is None:
                continue
            child_identity = (
                active_child.run.rutter_id,
                active_child.run.definition_version,
            )
            if active_child.kind == "explicit_call":
                site = definition.states.get(active_child.site)
                if not isinstance(site, Call):
                    raise RutterStateError(
                        "active explicit child does not match a bound Call state"
                    )
                expected = self._definition_identity(site.child)
            else:
                maker = definition.case_makers_by_id.get(active_child.site)
                if maker is None:
                    raise RutterStateError(
                        "active attached child does not match a bound CaseMaker"
                    )
                expected = self._definition_identity(getattr(maker, "child"))
            if child_identity != expected:
                raise RutterStateError(
                    "active child identity differs from its bound definition"
                )

    @staticmethod
    def _validate_prompt_authority(
        run: ActiveRun,
        prompt: Prompt,
        global_revision: int,
        fault: Mapping[str, JsonValue] | None,
        *,
        is_leaf: bool,
    ) -> None:
        entered = run.entered_node
        turns = tuple(
            entry
            for entry in run.history
            if isinstance(entry, Turn)
            and entry.node_entry_id == entered.entry_id
            and entry.state_id == entered.state_id
        )
        if len(turns) != 1:
            raise RutterStateError(
                "active Prompt requires exactly one matching current Turn"
            )
        turn = turns[0]
        if (
            turn.message.instructions["text"] != prompt.text
            or turn.message.instructions["answer"] != prompt.answer.outcomes
        ):
            raise RutterStateError(
                "active Prompt Turn differs from the bound Prompt definition"
            )
        child = run.active_child
        if turn.response is None:
            if is_leaf and turn.revision != global_revision:
                raise RutterStateError(
                    "active Prompt Turn revision differs from Reckoning revision"
                )
            if child is not None:
                raise RutterStateError(
                    "active Prompt with an open Turn cannot own an active child"
                )
            return
        if turn.response.outcome not in prompt.answer.outcomes:
            raise RutterStateError(
                "active Prompt Turn has an undeclared accepted outcome"
            )
        if child is None:
            if fault is None or (
                fault.get("run_id") == run.run_id
                and fault.get("state_id") == entered.state_id
                and fault.get("node_entry_id") == entered.entry_id
            ):
                return
            raise RutterStateError(
                "accepted active Prompt Turn has mismatched fault authority"
            )
        if (
            child.kind != "attached_case"
            or child.attached_to_edge_id != turn.record_id
        ):
            raise RutterStateError(
                "accepted active Prompt Turn requires its matching attached child"
            )

    def _definition_identity(self, source: object) -> tuple[str, int]:
        for definition in self._definitions.values():
            if type(definition.definition) is source:
                return definition.identity
        raise RutterStateError("active child source is absent from the bound graph")

    def get_instruction(self) -> object | None:
        engine = importlib.import_module("officina.rutter.engine")
        return engine._get_instruction(self)

    def validate(self, response: object) -> ValidationReport:
        engine = importlib.import_module("officina.rutter.engine")
        return engine._validate(self, response)

    def next(
        self,
        response: object = _MISSING,
        *,
        continue_: bool = True,
        dry_run: bool = False,
    ) -> NodeView:
        engine = importlib.import_module("officina.rutter.engine")
        return engine._next(
            self,
            response,
            continue_=continue_,
            dry_run=dry_run,
        )

    def get_current_node(self) -> NodeView:
        engine = importlib.import_module("officina.rutter.engine")
        return engine._get_current_node(self)


class RutterRegistry:
    """Freeze named stateless definitions and bind them to confined stores."""

    def __init__(
        self,
        rutters: Mapping[str, _Registration],
        reckoning_root: Path,
    ) -> None:
        if not isinstance(rutters, Mapping):
            raise RutterDefinitionError("rutters must be a mapping")
        if not isinstance(reckoning_root, Path):
            raise RutterDefinitionError("reckoning_root must be a Path")
        binder = _DefinitionBinder()
        by_name: dict[str, _BoundDefinition] = {}
        by_identity: dict[tuple[str, int], _BoundDefinition] = {}
        for name, source in rutters.items():
            if type(name) is not str or not name:
                raise RutterDefinitionError(
                    "Rutter registry name must be a non-empty string"
                )
            definition = binder.bind(source)
            if definition.identity in by_identity:
                raise RutterDefinitionError(
                    f"duplicate registered Rutter identity {definition.identity!r}"
                )
            by_name[name] = definition
            by_identity[definition.identity] = definition
        self._by_name = MappingProxyType(by_name)
        self._by_identity = MappingProxyType(by_identity)
        self._reckoning_root = reckoning_root.absolute()

    def _path(self, reckoning_path: Path) -> Path:
        return _confined_reckoning_path(self._reckoning_root, reckoning_path)

    def create(
        self,
        name: str,
        reckoning_path: Path,
        charter_data: Mapping[str, JsonValue],
    ) -> _BoundVoyage:
        if type(name) is not str or name not in self._by_name:
            raise RutterStateError(f"unknown Rutter {name!r}")
        definition = self._by_name[name]
        definition.require_current_metadata()
        charter = Charter(charter_data)
        engine = importlib.import_module("officina.rutter.engine")
        reckoning = engine._create_reckoning(definition, charter)
        return _BoundVoyage(
            definition,
            self._path(reckoning_path),
            reckoning,
            create=True,
        )

    def open(self, reckoning_path: Path) -> _BoundVoyage:
        path = self._path(reckoning_path)
        reckoning = _ReckoningStore(path).read()
        identity = (reckoning.root.rutter_id, reckoning.root.definition_version)
        definition = self._by_identity.get(identity)
        if definition is None:
            raise RutterStateError(f"unknown Rutter identity {identity!r}")
        definition.require_current_metadata()
        return _BoundVoyage(definition, path, reckoning, create=False)
