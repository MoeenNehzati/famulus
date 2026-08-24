"""Bind stateless Rutter definitions and construct durable Voyages."""

from __future__ import annotations

import inspect
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping, TypeAlias

from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import RefResolutionError, SchemaError

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


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


_REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef"})


def _validate_self_contained_references(schema: Mapping[str, object]) -> None:
    def visit(value: object, resource: Mapping[str, object]) -> None:
        if isinstance(value, Mapping):
            current_resource = value if "$id" in value else resource
            for key, item in value.items():
                if key in _REFERENCE_KEYWORDS:
                    if type(item) is not str or not item.startswith("#"):
                        raise RutterDefinitionError(
                            "LLMStep response_schema must be self-contained"
                        )
                    try:
                        RefResolver.from_schema(
                            current_resource,
                            id_of=Draft202012Validator.ID_OF,
                        ).resolve(item)
                    except RefResolutionError as exc:
                        raise RutterDefinitionError(
                            "LLMStep response_schema must be self-contained"
                        ) from exc
                visit(item, current_resource)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, resource)

    visit(schema, schema)


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

    def fork(self) -> _DefinitionBinder:
        forked = _DefinitionBinder()
        forked._by_source = dict(self._by_source)
        forked._source_by_id = dict(self._source_by_id)
        forked._visiting = list(self._visiting)
        return forked

    def adopt(self, forked: _DefinitionBinder) -> None:
        self._by_source = forked._by_source
        self._source_by_id = forked._source_by_id
        self._visiting = forked._visiting

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
            child_sources = self._validate_graph(evolutions)
            response_validators = self._prepare_response_validators(evolutions)
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
                response_validators,
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
            _require_callback(
                hook.rutter_constructor,
                1,
                "TransitionHook rutter_constructor",
            )
            _require_callback(
                hook.charter_constructor,
                1,
                "TransitionHook charter_constructor",
            )
            hooks[hook_id] = hook
        return tuple(authored), MappingProxyType(hooks)

    def _validate_graph(
        self,
        evolutions: Mapping[str, Evolution],
    ) -> tuple[_Registration, ...]:
        children: list[_Registration] = []
        for evolution in evolutions.values():
            if isinstance(evolution, LLMStep):
                _require_callback(evolution.data, 1, "LLMStep data")
                _require_callback(
                    evolution.assess_response,
                    1,
                    "LLMStep assess_response",
                )
                self._validate_next_on_outcome(
                    evolution.next_on_outcome,
                    evolutions,
                    "LLMStep next_on_outcome",
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
                _require_callback(
                    evolution.rutter_constructor,
                    1,
                    "SubRutter rutter_constructor",
                )
                _require_callback(
                    evolution.charter_constructor,
                    1,
                    "SubRutter charter_constructor",
                )
                self._validate_next_on_outcome(
                    evolution.next_on_outcome,
                    evolutions,
                    "SubRutter next_on_outcome",
                )
                if evolution.choose_next is not None:
                    _require_callback(evolution.choose_next, 2, "SubRutter choose_next")
            elif (
                isinstance(evolution, Terminal)
                and evolution.result_constructor is not None
            ):
                _require_callback(
                    evolution.result_constructor,
                    1,
                    "Terminal result_constructor",
                )
        return tuple(children)

    @staticmethod
    def _prepare_response_validators(
        evolutions: Mapping[str, Evolution],
    ) -> Mapping[str, Draft202012Validator]:
        validators: dict[str, Draft202012Validator] = {}
        for evolution_id, evolution in evolutions.items():
            if not isinstance(evolution, LLMStep) or evolution.response_schema is None:
                continue
            schema = _plain_json(evolution.response_schema)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise RutterDefinitionError(
                    "LLMStep response_schema must be valid Draft 2020-12 JSON Schema"
                ) from exc
            _validate_self_contained_references(schema)
            validators[evolution_id] = Draft202012Validator(schema)
        return MappingProxyType(validators)

    @staticmethod
    def _validate_next_on_outcome(
        next_on_outcome: str | Mapping[str, str] | None,
        evolutions: Mapping[str, Evolution],
        label: str,
    ) -> None:
        targets: tuple[object, ...]
        if type(next_on_outcome) is str:
            targets = (next_on_outcome,)
        elif isinstance(next_on_outcome, Mapping):
            if not next_on_outcome:
                raise RutterDefinitionError(f"{label} routes must not be empty")
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
        self._binder = binder
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

    def _bind_contextual_definition(
        self,
        source: Rutter,
        definitions: Mapping[tuple[str, int], _BoundDefinition],
        active_ancestor_identities: tuple[tuple[str, int], ...],
        expected_identity: tuple[str, int] | None,
    ) -> tuple[_BoundDefinition, Mapping[tuple[str, int], _BoundDefinition]]:
        return self._bind_contextual_definition_with(
            self._binder,
            source,
            definitions,
            active_ancestor_identities,
            expected_identity,
        )

    @staticmethod
    def _bind_contextual_definition_with(
        binder: _DefinitionBinder,
        source: Rutter,
        definitions: Mapping[tuple[str, int], _BoundDefinition],
        active_ancestor_identities: tuple[tuple[str, int], ...],
        expected_identity: tuple[str, int] | None,
    ) -> tuple[_BoundDefinition, Mapping[tuple[str, int], _BoundDefinition]]:
        if not isinstance(source, Rutter):
            raise RutterDefinitionError(
                "contextual Rutter constructor must return a Rutter"
            )
        forked = binder.fork()
        definition = forked.bind(source)
        closure = definition.reachable()
        if expected_identity is not None and definition.identity != expected_identity:
            raise RutterDefinitionError(
                "contextual Rutter identity differs from persisted identity"
            )
        for identity, candidate in closure.items():
            existing = definitions.get(identity)
            if (
                existing is not None
                and existing.definition is not candidate.definition
            ):
                raise RutterDefinitionError(
                    f"Rutter child identity conflict for {identity[0]!r}"
                )
        if any(identity in closure for identity in active_ancestor_identities):
            raise RutterDefinitionError("recursive definition-call cycle")
        binder.adopt(forked)
        return definition, closure

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
            self._bind_contextual_definition,
            self._path(reckoning_path),
            reckoning,
            create=True,
        )

    def open(self, reckoning_path: Path) -> Voyage:
        path = self._path(reckoning_path)
        staged_binder = self._binder.fork()

        def bind_contextual_definition(
            source: Rutter,
            definitions: Mapping[tuple[str, int], _BoundDefinition],
            active_ancestor_identities: tuple[tuple[str, int], ...],
            expected_identity: tuple[str, int] | None,
        ) -> tuple[
            _BoundDefinition,
            Mapping[tuple[str, int], _BoundDefinition],
        ]:
            return self._bind_contextual_definition_with(
                staged_binder,
                source,
                definitions,
                active_ancestor_identities,
                expected_identity,
            )

        voyage = Voyage._open(
            self._definition_for_identity,
            bind_contextual_definition,
            path,
        )
        self._binder.adopt(staged_binder)
        voyage._bind_contextual_definition = self._bind_contextual_definition
        return voyage
