"""Bind stateless Rutter definitions and construct durable Voyages."""

from __future__ import annotations

import inspect
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping, TypeAlias

from officina.rutter.authoring import (
    Evolution,
    LLMStep,
    MachineStep,
    Rutter,
    SubRutter,
    Terminal,
    TransitionHook,
)
from officina.rutter.engine import Voyage, _BoundDefinition, _create_reckoning
from officina.rutter.storage import _confined_reckoning_path
from officina.rutter.values import (
    Charter,
    JsonValue,
    RutterDefinitionError,
    RutterStateError,
)


__all__ = ("RutterRegistry",)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_Registration: TypeAlias = type[Rutter] | Rutter | object
_RUN_STATE_NAMES = frozenset(
    {
        "store",
        "reckoning",
        "path",
        "revision",
        "run",
        "run_data",
        "voyage",
        "voyage_data",
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


class _DefinitionBinder:
    """Construct and validate each definition once without running authored work."""

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
            (
                rutter_id,
                definition_version,
                initial_evolution_id,
                allow_multiple,
            ) = metadata
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

            evolutions = self._freeze_evolutions(definition)
            if initial_evolution_id not in evolutions:
                raise RutterDefinitionError(
                    "initial_evolution_id must name one declared evolution"
                )
            hooks, hooks_by_id = self._freeze_transition_hooks(definition)
            child_sources = self._validate_graph(evolutions, hooks)
            children = tuple(self.bind(child) for child in child_sources)
            bound = _BoundDefinition(
                definition,
                rutter_id,
                definition_version,
                initial_evolution_id,
                allow_multiple,
                evolutions,
                hooks,
                hooks_by_id,
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
        initial_evolution_id = _require_id(
            getattr(definition, "initial_evolution_id", None),
            "initial_evolution_id",
        )
        allow_multiple = getattr(
            definition,
            "allow_multiple_hooks_per_transition",
            False,
        )
        if type(allow_multiple) is not bool:
            raise RutterDefinitionError(
                "allow_multiple_hooks_per_transition must be an exact Boolean"
            )
        for attribute in vars(definition):
            if attribute.lstrip("_") in _RUN_STATE_NAMES:
                raise RutterDefinitionError(
                    f"Rutter definition stores voyage state in {attribute!r}"
                )
        return rutter_id, definition_version, initial_evolution_id, allow_multiple

    @staticmethod
    def _freeze_evolutions(definition: Rutter) -> Mapping[str, Evolution]:
        try:
            authored = definition.define_evolutions()
        except Exception as exc:
            raise RutterDefinitionError("define_evolutions() failed") from exc
        if not isinstance(authored, Mapping):
            raise RutterDefinitionError("define_evolutions() must return a mapping")
        evolutions: dict[str, Evolution] = {}
        for evolution_id, evolution in authored.items():
            evolution_id = _require_id(evolution_id, "evolution ID")
            if evolution_id in evolutions:
                raise RutterDefinitionError(
                    f"duplicate evolution ID {evolution_id!r}"
                )
            if not isinstance(evolution, (LLMStep, MachineStep, SubRutter, Terminal)):
                raise RutterDefinitionError(
                    f"evolution {evolution_id!r} must be LLMStep, MachineStep, "
                    "SubRutter, or Terminal"
                )
            evolutions[evolution_id] = evolution
        if not evolutions:
            raise RutterDefinitionError("evolution mapping must not be empty")
        return MappingProxyType(evolutions)

    @staticmethod
    def _freeze_transition_hooks(
        definition: Rutter,
    ) -> tuple[tuple[TransitionHook, ...], Mapping[str, TransitionHook]]:
        try:
            authored = definition.define_transition_hooks()
        except Exception as exc:
            raise RutterDefinitionError("define_transition_hooks() failed") from exc
        if type(authored) is not tuple:
            raise RutterDefinitionError("define_transition_hooks() must return a tuple")
        hooks: dict[str, TransitionHook] = {}
        for hook in authored:
            if not isinstance(hook, TransitionHook):
                raise RutterDefinitionError(
                    "define_transition_hooks() entries must be TransitionHook values"
                )
            hook_id = _require_id(hook.id, "TransitionHook ID")
            if hook_id in hooks:
                raise RutterDefinitionError(
                    f"duplicate TransitionHook ID {hook_id!r}"
                )
            _require_callback(hook.charter, 1, "TransitionHook charter")
            hooks[hook_id] = hook
        return tuple(authored), MappingProxyType(hooks)

    def _validate_graph(
        self,
        evolutions: Mapping[str, Evolution],
        hooks: tuple[TransitionHook, ...],
    ) -> tuple[_Registration, ...]:
        children: list[_Registration] = []
        for evolution in evolutions.values():
            if isinstance(evolution, LLMStep):
                if not evolution.answer.outcomes:
                    raise RutterDefinitionError(
                        "LLMStep answer must declare at least one outcome"
                    )
                _require_callback(evolution.data, 1, "LLMStep data")
                _require_callback(evolution.validate, 1, "LLMStep validate")
                self._validate_next_on_outcome(
                    evolution.next_on_outcome,
                    evolutions,
                    "LLMStep next_on_outcome",
                    outcomes=frozenset(evolution.answer.outcomes),
                )
                if evolution.choose_next is not None:
                    _require_callback(evolution.choose_next, 1, "LLMStep choose_next")
            elif isinstance(evolution, MachineStep):
                _require_callback(evolution.run, 1, "MachineStep run")
                self._validate_next_on_outcome(
                    evolution.next_on_outcome,
                    evolutions,
                    "MachineStep next_on_outcome",
                )
                if evolution.choose_next is not None:
                    _require_callback(evolution.choose_next, 2, "MachineStep choose_next")
            elif isinstance(evolution, SubRutter):
                _require_callback(evolution.charter, 1, "SubRutter charter")
                self._validate_next_on_outcome(
                    evolution.next_on_outcome,
                    evolutions,
                    "SubRutter next_on_outcome",
                )
                if evolution.choose_next is not None:
                    _require_callback(evolution.choose_next, 2, "SubRutter choose_next")
                children.append(evolution.child)
            elif isinstance(evolution, Terminal) and callable(evolution.result):
                _require_callback(evolution.result, 1, "Terminal result")
        children.extend(hook.child for hook in hooks)
        return tuple(children)

    @staticmethod
    def _validate_next_on_outcome(
        next_on_outcome: str | Mapping[str, str] | None,
        evolutions: Mapping[str, Evolution],
        label: str,
        *,
        outcomes: frozenset[str] | None = None,
    ) -> None:
        targets: tuple[object, ...]
        if type(next_on_outcome) is str:
            targets = (next_on_outcome,)
        elif isinstance(next_on_outcome, Mapping):
            if not next_on_outcome:
                raise RutterDefinitionError(f"{label} routes must not be empty")
            if outcomes is not None and set(next_on_outcome) != outcomes:
                raise RutterDefinitionError(
                    "LLMStep routes must exactly match declared outcomes"
                )
            targets = tuple(next_on_outcome.values())
        elif next_on_outcome is None:
            return
        else:
            raise RutterDefinitionError(f"{label} has invalid routing")
        for target in targets:
            if target not in evolutions:
                raise RutterDefinitionError(
                    f"{label} names undeclared successor {target!r}"
                )


class RutterRegistry:
    """Freeze named stateless definitions and construct confined Voyages."""

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

    def _definition_for_identity(
        self,
        identity: tuple[str, int],
    ) -> _BoundDefinition:
        definition = self._by_identity.get(identity)
        if definition is None:
            raise RutterStateError(f"unknown Rutter identity {identity!r}")
        definition.require_current_metadata()
        return definition

    def create(
        self,
        name: str,
        reckoning_path: Path,
        charter_data: Mapping[str, JsonValue],
    ) -> Voyage:
        if type(name) is not str or name not in self._by_name:
            raise RutterStateError(f"unknown Rutter {name!r}")
        definition = self._by_name[name]
        definition.require_current_metadata()
        charter = Charter(charter_data)
        reckoning = _create_reckoning(definition, charter)
        return Voyage(
            definition,
            self._path(reckoning_path),
            reckoning,
            create=True,
        )

    def open(self, reckoning_path: Path) -> Voyage:
        path = self._path(reckoning_path)
        return Voyage._open(self._definition_for_identity, path)
