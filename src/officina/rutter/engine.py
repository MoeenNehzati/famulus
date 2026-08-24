"""Reduce bound Rutter voyages across durable LLMStep and Terminal operations."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from inspect import getdoc, signature
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Mapping, Protocol, TypeAlias
from uuid import uuid4

from officina.rutter.authoring import (
    Evolution,
    EvolutionContext,
    LLMResponseContext,
    LLMStep,
    MachineContext,
    MachineStep,
    Rutter,
    SubRutter,
    Terminal,
    TransitionContext,
    TransitionHook,
)
from officina.rutter.evaluation import (
    _RutterFault,
    build_llm_data,
    build_subrutter_charter,
    build_terminal_result,
    construct_transition_hook_rutter,
    evaluate_llm_route,
    evaluate_machine_route,
    evaluate_subrutter_route,
    run_machine,
    select_transition_hooks,
    assess_llm_response,
)
from officina.rutter.history import (
    ActiveChild,
    ActiveRun,
    EnteredEvolution,
    HistoryEntry,
    HistoryView,
    KnownFault,
    MachineRecord,
    OpaqueFault,
    Reckoning,
    SubRutterRecord,
    TerminalRecord,
    Transition,
    Turn,
    _EffectRecovery,
)
from officina.rutter.reducer import (
    ActiveLeaf,
    _replace_in_tree,
    _require_child_capacity,
    deepest_active_leaf,
    enter_child,
    enter_evolution,
    replace_active_run,
    return_active_child,
)
from officina.rutter.storage import ReckoningStore
from officina.rutter.values import (
    Charter,
    EvolutionView,
    FaultSummary,
    JsonObject,
    MachineInstruction,
    MachineResult,
    Message,
    NotApplicable,
    PreviewUnavailable,
    RunBlocked,
    RutterDefinitionError,
    RutterStateError,
    RutterValidationError,
    ValidationIssue,
    ValidationReport,
    VoyageResult,
    VoyageStatus,
    _freeze_object,
    _require_id,
)


_OPERATION_LIMIT = 100
_MACHINE_RESULT_FORMAT = {
    "outcome": "declared outcome",
    "value": {"type": "finite JSON"},
}


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


class _StoreIO(Protocol):
    def read(self) -> Reckoning: ...

    def create(self, reckoning: Reckoning) -> None: ...

    def transaction(self) -> AbstractContextManager[Reckoning]: ...

    def replace(self, previous: Reckoning, replacement: Reckoning) -> None: ...


class _SchemaValidator(Protocol):
    def iter_errors(self, instance: object) -> object: ...


@dataclass(frozen=True)
class _BoundDefinition:
    definition: Rutter
    rutter_id: str
    definition_version: int
    initial_evolution_id: str
    allow_multiple_hooks_per_transition: bool
    evolutions: Mapping[str, Evolution]
    transition_hooks: tuple[TransitionHook, ...]
    transition_hooks_by_id: Mapping[str, TransitionHook]
    response_validators: Mapping[str, _SchemaValidator]
    children: tuple[_BoundDefinition, ...]

    @property
    def identity(self) -> tuple[str, int]:
        return self.rutter_id, self.definition_version

    def require_current_metadata(self) -> None:
        current = (
            getattr(self.definition, "rutter_id", None),
            getattr(self.definition, "definition_version", None),
            getattr(self.definition, "initial_evolution_id", None),
            getattr(
                self.definition,
                "allow_multiple_hooks_per_transition",
                None,
            ),
        )
        frozen = (
            self.rutter_id,
            self.definition_version,
            self.initial_evolution_id,
            self.allow_multiple_hooks_per_transition,
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


_ContextualDefinitionBinder: TypeAlias = Callable[
    [
        Rutter,
        Mapping[tuple[str, int], _BoundDefinition],
        tuple[tuple[str, int], ...],
        tuple[str, int] | None,
    ],
    tuple[_BoundDefinition, Mapping[tuple[str, int], _BoundDefinition]],
]


@dataclass(frozen=True)
class BoundRun:
    run: ActiveRun
    definition: _BoundDefinition


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _leaf_definition(
    voyage: Voyage,
    leaf: ActiveLeaf,
) -> _BoundDefinition:
    identity = (leaf.run.rutter_id, leaf.run.definition_version)
    try:
        return voyage._definitions[identity]
    except KeyError as exc:
        raise RutterStateError("active Rutter definition is unavailable") from exc


def _condition(
    reckoning: Reckoning,
    evolution: Evolution,
    *,
    leaf: ActiveLeaf | None = None,
) -> str:
    if reckoning.fault is not None:
        return "fault"
    effect = reckoning.active_effect
    if effect is not None and effect.disposition == "uncertain":
        return "uncertain"
    if leaf is None:
        leaf = deepest_active_leaf(reckoning)
    if isinstance(evolution, Terminal):
        done = HistoryView(leaf.run.history, reckoning.completed_runs).terminal()
        if done is not None and (
            done.evolution_entry_id == leaf.run.entered_evolution.entry_id
            and done.evolution_id == leaf.run.entered_evolution.evolution_id
        ):
            return "terminal"
    return "ready"


def _node_view(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    evolution: Evolution,
    *,
    preview: bool = False,
    condition: str | None = None,
) -> EvolutionView:
    if preview:
        projected_condition = "preview"
    elif condition is None:
        projected_condition = _condition(reckoning, evolution)
    else:
        projected_condition = condition
    return EvolutionView(
        leaf.run.rutter_id,
        leaf.run.definition_version,
        leaf.run.entered_evolution.evolution_id,
        None if preview else leaf.run.entered_evolution.entry_id,
        leaf.depth,
        projected_condition,
    )


def _invalid(code: str, path: tuple[str | int, ...], message: str) -> ValidationReport:
    return ValidationReport(False, (ValidationIssue(path, code, message),))


def _prompt_turn(reckoning: Reckoning, run: ActiveRun) -> Turn:
    turn = HistoryView(run.history, reckoning.completed_runs).open_turn()
    if turn is None or turn.evolution_entry_id != run.entered_evolution.entry_id:
        raise RutterStateError("entered LLMStep has no matching open Turn")
    return turn


def _call_child_definition(
    voyage: Voyage,
    call: SubRutter,
) -> _BoundDefinition:
    identity = (
        getattr(call.child, "rutter_id", None),
        getattr(call.child, "definition_version", None),
    )
    try:
        return voyage._definitions[identity]
    except KeyError as exc:
        raise RutterStateError("SubRutter child definition is unavailable") from exc


def _push_call(
    reckoning: Reckoning,
    run_id: str,
    call: SubRutter,
    child_definition: _BoundDefinition,
) -> Reckoning:
    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may push a SubRutter child")
    _require_child_capacity(reckoning, run_id)
    child_charter = Charter(
        build_subrutter_charter(
            _evolution_context(leaf.run, reckoning),
            call,
        )
    )
    child_run = ActiveRun(
        _new_id("run"),
        child_definition.rutter_id,
        child_definition.definition_version,
        child_charter,
        EnteredEvolution(_new_id("entry"), child_definition.initial_evolution_id),
        (),
        None,
    )
    child_state = child_definition.evolutions[child_definition.initial_evolution_id]
    if isinstance(child_state, LLMStep):
        try:
            turn = _render_prompt(reckoning, child_run, child_state)
        except Exception as exc:
            raise _RutterFault("child-materialization") from exc
        child_run = replace(child_run, history=(turn,))
    pushed = enter_child(
        reckoning,
        run_id,
        ActiveChild(
            _new_id("call"),
            "explicit_call",
            leaf.run.entered_evolution.evolution_id,
            None,
            child_run,
        ),
    )
    if isinstance(child_state, MachineStep) and child_state.mode != "pure":
        child_leaf = deepest_active_leaf(pushed)
        pushed = replace(
            pushed,
            active_effect=_planned_effect(child_leaf.run, child_state),
        )
    return pushed


def _push_hook(
    reckoning: Reckoning,
    run_id: str,
    maker: TransitionHook,
    child_definition: _BoundDefinition,
    child_charter: Charter,
    transition: Transition,
) -> Reckoning:
    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may push an attached child")
    _require_child_capacity(reckoning, run_id)
    child_run = ActiveRun(
        _new_id("run"),
        child_definition.rutter_id,
        child_definition.definition_version,
        child_charter,
        EnteredEvolution(_new_id("entry"), child_definition.initial_evolution_id),
        (),
        None,
    )
    child_state = child_definition.evolutions[child_definition.initial_evolution_id]
    if isinstance(child_state, LLMStep):
        try:
            turn = _render_prompt(reckoning, child_run, child_state)
        except Exception as exc:
            raise _RutterFault("child-materialization") from exc
        child_run = replace(child_run, history=(turn,))
    pushed = enter_child(
        reckoning,
        run_id,
        ActiveChild(
            _new_id("call"),
            "attached_case",
            maker.id,
            transition.transition_id,
            child_run,
        ),
    )
    if isinstance(child_state, MachineStep) and child_state.mode != "pure":
        child_leaf = deepest_active_leaf(pushed)
        pushed = replace(
            pushed,
            active_effect=_planned_effect(child_leaf.run, child_state),
        )
    return pushed


def _accept_prompt(
    reckoning: Reckoning,
    response: JsonObject,
) -> Reckoning:
    """Fill the active LLMStep's exact open Turn and advance the global revision."""

    leaf = deepest_active_leaf(reckoning)
    turn = _prompt_turn(reckoning, leaf.run)
    history = tuple(
        replace(entry, response=response) if entry is turn else entry
        for entry in leaf.run.history
    )
    accepted_run = replace(leaf.run, history=history)
    return replace(
        replace_active_run(reckoning, accepted_run),
        global_revision=reckoning.global_revision + 1,
    )


def _accept_machine(
    reckoning: Reckoning,
    machine: MachineStep,
    machine_id: str,
    result: MachineResult,
) -> Reckoning:
    leaf = deepest_active_leaf(reckoning)
    record = MachineRecord(
        _new_id("record"),
        machine_id,
        leaf.run.entered_evolution.entry_id,
        leaf.run.entered_evolution.evolution_id,
        machine.mode,
        result,
    )
    accepted_run = replace(leaf.run, history=leaf.run.history + (record,))
    return replace(
        reckoning,
        root=_replace_in_tree(
            reckoning.root,
            accepted_run.run_id,
            accepted_run,
        ),
        global_revision=reckoning.global_revision + 1,
        active_effect=None,
    )


def _source_record(run: ActiveRun, entered_evolution: EnteredEvolution) -> HistoryEntry | None:
    for record in reversed(run.history):
        if (
            record.evolution_entry_id == entered_evolution.entry_id
            and not (
                isinstance(record, SubRutterRecord)
                and record.transition_hook_id is not None
            )
        ):
            return record
    return None


def _is_recorded_source(evolution: Evolution, record: HistoryEntry | None) -> bool:
    return (
        isinstance(evolution, LLMStep)
        and isinstance(record, Turn)
        and record.response is not None
    ) or (
        isinstance(evolution, MachineStep) and isinstance(record, MachineRecord)
    ) or (
        isinstance(evolution, SubRutter)
        and isinstance(record, SubRutterRecord)
        and record.origin_evolution_id is not None
    ) or (
        isinstance(evolution, Terminal) and isinstance(record, TerminalRecord)
    )


def _select_transition(
    bound_run: BoundRun,
    strict_prefix: HistoryView,
    record: HistoryEntry,
    *,
    call_result: VoyageResult | None = None,
) -> Transition:
    evolution_id = bound_run.run.entered_evolution.evolution_id
    evolution = bound_run.definition.evolutions[evolution_id]
    if isinstance(evolution, LLMStep) and isinstance(record, Turn):
        response = record.response
        if response is None:
            raise _RutterFault("routing")
        outcome_value = response.get("outcome")
        if type(outcome_value) is not str:
            raise _RutterFault("routing")
        outcome = outcome_value
    elif isinstance(evolution, MachineStep) and isinstance(record, MachineRecord):
        response = None
        outcome = record.result.outcome
    elif (
        isinstance(evolution, SubRutter)
        and isinstance(record, SubRutterRecord)
        and call_result is not None
    ):
        response = None
        outcome = call_result.outcome
    elif isinstance(evolution, Terminal) and isinstance(record, TerminalRecord):
        return Transition(
            record.record_id,
            bound_run.run.entered_evolution.entry_id,
            evolution_id,
            record.result.outcome,
            None,
        )
    else:
        raise _RutterFault("routing")
    routing = evolution.next_on_outcome
    target: object
    if type(routing) is str:
        target = routing
    elif isinstance(routing, Mapping):
        target = routing.get(outcome)
    else:
        choose_next = evolution.choose_next
        if not callable(choose_next):
            raise _RutterFault("routing")
        evolution_context = EvolutionContext(
            bound_run.run.charter,
            evolution_id,
            bound_run.run.entered_evolution.entry_id,
            strict_prefix,
        )
        if isinstance(evolution, LLMStep):
            assert isinstance(record, Turn) and response is not None
            target = evaluate_llm_route(
                LLMResponseContext(
                    evolution_context,
                    record.message,
                    response,
                ),
                choose_next,
            )
        elif isinstance(evolution, MachineStep):
            assert isinstance(record, MachineRecord)
            target = evaluate_machine_route(
                MachineContext(evolution_context, record.machine_id),
                record.result,
                choose_next,
            )
        else:
            assert call_result is not None
            target = evaluate_subrutter_route(
                evolution_context,
                call_result,
                choose_next,
            )
    if type(target) is not str or target not in bound_run.definition.evolutions:
        raise _RutterFault(
            "routing",
            target_evolution_id=target if type(target) is str else None,
        )
    return Transition(
        record.invocation_id if isinstance(record, SubRutterRecord) else record.record_id,
        bound_run.run.entered_evolution.entry_id,
        evolution_id,
        outcome,
        target,
    )


def _transition_context(
    run: ActiveRun,
    strict_prefix: HistoryView,
    transition: Transition,
    record: HistoryEntry,
) -> TransitionContext:
    return TransitionContext(
        EvolutionContext(
            run.charter,
            run.entered_evolution.evolution_id,
            run.entered_evolution.entry_id,
            strict_prefix,
        ),
        transition,
        record,
    )


def _continue_transition(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    transition: Transition,
    strict_prefix: HistoryView,
    record: HistoryEntry,
) -> Reckoning:
    context = _transition_context(leaf.run, strict_prefix, transition, record)
    selected = select_transition_hooks(
        context,
        transition,
        definition.transition_hooks,
    )
    if len(selected) > 1 and not definition.allow_multiple_hooks_per_transition:
        raise _RutterFault(
            "case-cardinality",
            transition_hook_ids=tuple(maker.id for maker, _ in selected),
        )
    completed = {
        (entry.transition_hook_id, entry.attached_to_transition_id)
        for entry in leaf.run.history
        if isinstance(entry, SubRutterRecord)
        and entry.transition_hook_id is not None
    }
    for maker, charter in selected:
        if (maker.id, transition.transition_id) in completed:
            continue
        return _push_hook(
            reckoning,
            leaf.run.run_id,
            maker,
            voyage._resolve_contextual_hook(
                reckoning,
                leaf.run,
                maker,
                context,
            ),
            charter,
            transition,
        )
    if transition.target is None:
        return reckoning
    return _enter_evolution(
        reckoning,
        leaf.run.run_id,
        transition.target,
        definition=definition,
    )


def _recorded_transition(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    record: HistoryEntry,
) -> tuple[Transition, HistoryView]:
    call_result: VoyageResult | None = None
    if isinstance(record, SubRutterRecord):
        try:
            call_result = reckoning.completed_runs[record.completed_voyage_instance_id].result
        except KeyError as exc:
            raise RutterStateError("SubRutterRecord completed run is unavailable") from exc
    history = HistoryView(leaf.run.history, reckoning.completed_runs)
    strict_prefix = history.strict_prefix(record)
    return (
        _select_transition(
            BoundRun(leaf.run, definition),
            strict_prefix,
            record,
            call_result=call_result,
        ),
        strict_prefix,
    )


def _continue_recorded_transition(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    record: HistoryEntry,
) -> Reckoning:
    transition, strict_prefix = _recorded_transition(reckoning, leaf, definition, record)
    return _continue_transition(
        voyage,
        reckoning,
        leaf,
        definition,
        transition,
        strict_prefix,
        record,
    )


def _enter_evolution(
    reckoning: Reckoning,
    run_id: str,
    target: str,
    *,
    definition: _BoundDefinition,
) -> Reckoning:
    """Enter one target; LLMStep entrance and open-Turn creation are atomic."""

    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may enter an evolution")
    try:
        target_state = definition.evolutions[target]
    except KeyError as exc:
        raise _RutterFault(
            "routing",
            target_evolution_id=target,
        ) from exc
    entered = enter_evolution(
        reckoning,
        run_id,
        EnteredEvolution(_new_id("entry"), target),
    )
    if isinstance(target_state, LLMStep):
        entered_leaf = deepest_active_leaf(entered)
        try:
            turn = _render_prompt(entered, entered_leaf.run, target_state)
        except Exception as exc:
            raise _RutterFault(
                "target-materialization",
                target_evolution_id=target,
            ) from exc
        entered_run = replace(
            entered_leaf.run,
            history=entered_leaf.run.history + (turn,),
        )
        entered = replace_active_run(entered, entered_run)
    elif isinstance(target_state, MachineStep) and target_state.mode != "pure":
        entered_leaf = deepest_active_leaf(entered)
        entered = replace(
            entered,
            active_effect=_planned_effect(entered_leaf.run, target_state),
        )
    return entered


def _settle_terminal(reckoning: Reckoning, run_id: str, result: VoyageResult) -> Reckoning:
    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may settle Terminal")
    record = TerminalRecord(
        _new_id("done"),
        leaf.run.entered_evolution.entry_id,
        leaf.run.entered_evolution.evolution_id,
        result,
    )
    settled = replace(leaf.run, history=leaf.run.history + (record,))
    return replace(
        replace_active_run(reckoning, settled),
        global_revision=reckoning.global_revision + 1,
    )


def _project_terminal(
    reckoning: Reckoning,
    run: ActiveRun,
    evolution: Terminal,
) -> VoyageResult:
    return build_terminal_result(
        _evolution_context(run, reckoning),
        evolution,
    )


def _fault_reckoning(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    category: str,
    *,
    target_evolution_id: str | None = None,
    transition_hook_ids: tuple[str, ...] = (),
) -> Reckoning:
    return replace(
        reckoning,
        fault=KnownFault(
            category,
            leaf.run.run_id,
            leaf.run.entered_evolution.evolution_id,
            leaf.run.entered_evolution.entry_id,
            target_evolution_id,
            transition_hook_ids,
        ),
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _path_sort_key(path: object) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, part) if type(part) is int else (1, str(part))
        for part in path  # type: ignore[union-attr]
    )


def _schema_error_sort_key(error: object) -> object:
    return (
        _path_sort_key(error.absolute_path),  # type: ignore[attr-defined]
        _path_sort_key(error.absolute_schema_path),  # type: ignore[attr-defined]
    )


def _validate_prompt(
    reckoning: Reckoning,
    run: ActiveRun,
    definition: _BoundDefinition,
    prompt: LLMStep,
    value: object,
    responding_to: str | None,
) -> ValidationReport:
    turn = _prompt_turn(reckoning, run)
    try:
        _require_id(responding_to, "responding_to", RutterStateError)
    except RutterStateError:
        return _invalid(
            "stale-entrance",
            (),
            "responding_to does not match the current evolution entrance",
        )
    if responding_to != turn.evolution_entry_id:
        return _invalid(
            "stale-entrance",
            (),
            "responding_to does not match the current evolution entrance",
        )
    if not isinstance(value, Mapping):
        return _invalid(
            "invalid-response",
            (),
            "response must be a finite JSON object",
        )
    try:
        response = _freeze_object(value, "response", error=RutterStateError)
    except RutterStateError:
        return _invalid(
            "nonfinite-response",
            (),
            "response must be a finite JSON object",
        )
    if "revision" in response:
        return _invalid(
            "reserved-metadata",
            ("revision",),
            "response field 'revision' is reserved for engine metadata",
        )
    outcome = response.get("outcome")
    try:
        _require_id(outcome, "outcome", RutterStateError)
    except RutterStateError:
        return _invalid(
            "invalid-outcome",
            ("outcome",),
            "response outcome must be a nonempty stable token",
        )
    validator = definition.response_validators.get(
        run.entered_evolution.evolution_id
    )
    if validator is not None:
        try:
            errors = sorted(
                validator.iter_errors(_plain_json(response)),
                key=_schema_error_sort_key,
            )
        except Exception as exc:
            raise RutterStateError(
                "LLMStep response schema evaluation failed"
            ) from exc
        if errors:
            return ValidationReport(
                False,
                tuple(
                    ValidationIssue(
                        tuple(error.absolute_path),
                        "response-schema",
                        "response does not satisfy the LLMStep response schema",
                    )
                    for error in errors
                ),
            )
    routing = prompt.next_on_outcome
    if isinstance(routing, Mapping) and outcome not in routing:
        return _invalid(
            "unknown-outcome",
            ("outcome",),
            "response outcome is not accepted by this LLMStep",
        )
    report = assess_llm_response(
        LLMResponseContext(
            _evolution_context(run, reckoning),
            turn.message,
            response,
        ),
        prompt,
    )
    return report


def _validate_machine_result(value: object) -> ValidationReport:
    if isinstance(value, MachineResult):
        return ValidationReport(True, ())
    if not isinstance(value, Mapping) or set(value) != {"outcome", "value"}:
        return _invalid(
            "invalid-envelope",
            (),
            "action result must contain exactly outcome and value",
        )
    try:
        MachineResult.from_json(value)
    except RutterStateError as exc:
        if "finite JSON" in str(exc):
            return _invalid(
                "nonfinite-value",
                ("value",),
                "action result value must be finite JSON",
            )
        return _invalid(
            "invalid-envelope",
            (),
            "action result must contain a stable outcome and finite JSON value",
        )
    return ValidationReport(True, ())


def _evolution_context(
    run: ActiveRun,
    reckoning: Reckoning,
    *,
    history: tuple[HistoryEntry, ...] | None = None,
) -> EvolutionContext:
    if history is None:
        current_entry = run.entered_evolution.entry_id
        boundary = next(
            (
                index
                for index, entry in enumerate(run.history)
                if entry.evolution_entry_id == current_entry
            ),
            len(run.history),
        )
        entries = run.history[:boundary]
    else:
        entries = history
    return EvolutionContext(
        run.charter,
        run.entered_evolution.evolution_id,
        run.entered_evolution.entry_id,
        HistoryView(entries, reckoning.completed_runs),
    )


def _machine_id(run: ActiveRun) -> str:
    return f"action-{run.entered_evolution.entry_id}"


def _planned_effect(run: ActiveRun, action: MachineStep) -> _EffectRecovery:
    return _EffectRecovery(
        _machine_id(run),
        run.run_id,
        run.entered_evolution.entry_id,
        run.entered_evolution.evolution_id,
        action.mode,
        "planned",
        None,
    )


def _machine_effect(reckoning: Reckoning) -> _EffectRecovery:
    effect = reckoning.active_effect
    if effect is None:
        raise RutterStateError("effectful MachineStep has no recovery authority")
    return effect


def _pure_machine_instruction(
    reckoning: Reckoning,
    run: ActiveRun,
    machine: MachineStep,
) -> MachineInstruction:
    machine_id = _machine_id(run)
    context = MachineContext(_evolution_context(run, reckoning), machine_id)

    def execute() -> MachineResult:
        return run_machine(context, machine)

    return MachineInstruction(
        machine_id,
        machine.mode,
        execute,
        _MACHINE_RESULT_FORMAT,
    )


def _effectful_machine_instruction(
    voyage: Voyage,
    machine_id: str,
    mode: str,
) -> MachineInstruction:
    def execute() -> MachineResult:
        with voyage._store.transaction() as reckoning:
            voyage._reckoning = reckoning
            leaf = deepest_active_leaf(reckoning)
            definition = _leaf_definition(voyage, leaf)
            evolution = definition.evolutions[leaf.run.entered_evolution.evolution_id]
            if not isinstance(evolution, MachineStep) or evolution.mode == "pure":
                raise RutterStateError("MachineStep instruction is stale")
            if _condition(reckoning, evolution) in {"fault", "uncertain"}:
                raise RunBlocked("the voyage is blocked")
            effect = _machine_effect(reckoning)
            if effect.machine_id != machine_id:
                raise RutterStateError("MachineStep instruction is stale")
            try:
                _, result = _run_effectful_machine(
                    voyage,
                    reckoning,
                    leaf,
                    evolution,
                    effect,
                )
            except _RutterFault as fault:
                current = voyage._reckoning
                _fault_and_publish(voyage, current, current, fault)
                raise RunBlocked("MachineStep execution failed") from None
            return result

    return MachineInstruction(
        machine_id,
        mode,
        execute,
        _MACHINE_RESULT_FORMAT,
    )


def _run_effectful_machine(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    machine: MachineStep,
    effect: _EffectRecovery,
) -> tuple[Reckoning, MachineResult]:
    if effect.disposition == "completed":
        assert effect.result is not None
        return reckoning, effect.result
    if effect.disposition != "planned":
        raise RunBlocked("the voyage is blocked")
    machine_id = effect.machine_id
    if machine.mode == "non-repeat-safe":
        uncertain_effect = replace(effect, disposition="uncertain")
        uncertain = replace(reckoning, active_effect=uncertain_effect)
        _publish(voyage, reckoning, uncertain)
        reckoning = uncertain
        effect = uncertain_effect
    result = run_machine(
        MachineContext(_evolution_context(leaf.run, reckoning), machine_id),
        machine,
    )
    completed_effect = replace(effect, disposition="completed", result=result)
    completed = replace(reckoning, active_effect=completed_effect)
    _publish(voyage, reckoning, completed)
    return completed, result


def _render_prompt(
    reckoning: Reckoning,
    run: ActiveRun,
    prompt: LLMStep,
) -> Turn:
    context = _evolution_context(run, reckoning)
    instructions: dict[str, object] = {"text": prompt.text}
    if prompt.response_schema is not None:
        instructions["response_schema"] = prompt.response_schema
    message = Message(
        instructions=instructions,
        data={
            "evolution": {
                "id": run.entered_evolution.evolution_id,
                "entry_id": run.entered_evolution.entry_id,
            },
            "payload": build_llm_data(context, prompt),
        },
    )
    return Turn(
        _new_id("turn"),
        run.entered_evolution.entry_id,
        run.entered_evolution.evolution_id,
        reckoning.global_revision,
        message,
        None,
    )


def _create_reckoning(
    definition: _BoundDefinition,
    charter: Charter,
) -> Reckoning:
    """Create one initial entrance, including a LLMStep's exact open Turn."""

    entered = EnteredEvolution(_new_id("entry"), definition.initial_evolution_id)
    run = ActiveRun(
        _new_id("run"),
        definition.rutter_id,
        definition.definition_version,
        charter,
        entered,
        (),
        None,
    )
    reckoning = Reckoning(3, 0, run, {}, None, None)
    evolution = definition.evolutions[definition.initial_evolution_id]
    if isinstance(evolution, LLMStep):
        try:
            turn = _render_prompt(reckoning, run, evolution)
        except Exception as exc:
            raise RutterStateError("LLMStep materialization failed") from exc
        run = ActiveRun(
            run.run_id,
            run.rutter_id,
            run.definition_version,
            run.charter,
            run.entered_evolution,
            (turn,),
            None,
        )
        reckoning = Reckoning(3, 0, run, {}, None, None)
    elif isinstance(evolution, MachineStep) and evolution.mode != "pure":
        reckoning = replace(reckoning, active_effect=_planned_effect(run, evolution))
    return reckoning


class Voyage:
    """Own one bound definition snapshot and its durable lifecycle."""

    compass_facing_methods: ClassVar[tuple[str, ...]] = (
        "get_status",
        "validate",
        "advance",
    )

    @classmethod
    def _open(
        cls,
        resolve_definition: Callable[[tuple[str, int]], _BoundDefinition],
        bind_contextual_definition: _ContextualDefinitionBinder,
        path: Path,
    ) -> Voyage:
        store = ReckoningStore(path)
        reckoning = store.read()
        identity = (reckoning.root.rutter_id, reckoning.root.definition_version)
        definition = resolve_definition(identity)
        return cls(
            definition,
            bind_contextual_definition,
            path,
            reckoning,
            create=False,
        )

    def __init__(
        self,
        definition: _BoundDefinition,
        bind_contextual_definition: _ContextualDefinitionBinder,
        path: Path,
        reckoning: Reckoning,
        *,
        create: bool,
    ) -> None:
        self._definition = definition
        self._definitions = dict(definition.reachable())
        self._bind_contextual_definition = bind_contextual_definition
        self._contextual_hook_children: dict[
            tuple[str, str, str], _BoundDefinition
        ] = {}
        self._reckoning = reckoning
        self._store: _StoreIO = ReckoningStore(
            path, semantic_validator=self._validate_reckoning
        )
        self._validate_reckoning(reckoning)
        if create:
            self._store.create(reckoning)

    @staticmethod
    def _transition_id_for_record(record: HistoryEntry) -> str:
        if isinstance(record, SubRutterRecord):
            return record.invocation_id
        return record.record_id

    def _active_ancestor_identities(
        self,
        reckoning: Reckoning,
        parent_run_id: str,
    ) -> tuple[tuple[str, int], ...]:
        identities: list[tuple[str, int]] = []
        run = reckoning.root
        while True:
            identities.append((run.rutter_id, run.definition_version))
            if run.run_id == parent_run_id:
                return tuple(identities)
            if run.active_child is None:
                break
            run = run.active_child.run
        raise RutterStateError("contextual hook parent is absent from active path")

    def _resolve_contextual_hook(
        self,
        reckoning: Reckoning,
        parent: ActiveRun,
        hook: TransitionHook,
        context: TransitionContext,
        *,
        expected_identity: tuple[str, int] | None = None,
        reopening: bool = False,
    ) -> _BoundDefinition:
        key = (parent.run_id, hook.id, context.transition.transition_id)
        cached = self._contextual_hook_children.get(key)
        if cached is not None:
            if expected_identity is not None and cached.identity != expected_identity:
                raise RutterStateError(
                    f"TransitionHook {hook.id!r} contextual child identity differs "
                    "from persisted identity"
                )
            return cached
        try:
            source = construct_transition_hook_rutter(context, hook)
            definition, closure = self._bind_contextual_definition(
                source,
                self._definitions,
                self._active_ancestor_identities(reckoning, parent.run_id),
                expected_identity,
            )
        except _RutterFault as exc:
            if not reopening:
                raise
            raise RutterStateError(
                f"TransitionHook {hook.id!r} contextual child construction failed"
            ) from exc
        except Exception as exc:
            if reopening:
                raise RutterStateError(
                    f"TransitionHook {hook.id!r} contextual child construction "
                    "or identity validation failed"
                ) from exc
            raise _RutterFault(
                "hook-construction",
                transition_hook_ids=(hook.id,),
            ) from exc
        self._definitions.update(closure)
        self._contextual_hook_children[key] = definition
        return definition

    def _active_hook_context(
        self,
        reckoning: Reckoning,
        parent: ActiveRun,
        definition: _BoundDefinition,
        child: ActiveChild,
        hook: TransitionHook,
        depth: int,
    ) -> TransitionContext:
        transition_id = child.attached_to_transition_id
        assert transition_id is not None
        records = tuple(
            record
            for record in parent.history
            if self._transition_id_for_record(record) == transition_id
        )
        if len(records) != 1:
            raise RutterStateError(
                f"TransitionHook {hook.id!r} attached transition is unavailable"
            )
        record = records[0]
        try:
            transition, strict_prefix = _recorded_transition(
                reckoning,
                ActiveLeaf(parent, depth),
                definition,
                record,
            )
        except Exception as exc:
            raise RutterStateError(
                f"TransitionHook {hook.id!r} context reconstruction failed"
            ) from exc
        if transition.transition_id != transition_id:
            raise RutterStateError(
                f"TransitionHook {hook.id!r} attached transition differs from history"
            )
        return _transition_context(parent, strict_prefix, transition, record)

    def _validate_reckoning(self, reckoning: Reckoning) -> None:
        active: list[tuple[ActiveRun, _BoundDefinition]] = []
        run = reckoning.root
        depth = 0
        while True:
            identity = (run.rutter_id, run.definition_version)
            definition = self._definitions.get(identity)
            if definition is None:
                raise RutterStateError(
                    f"active Rutter definition {identity!r} is unavailable"
                )
            definition.require_current_metadata()
            evolution = definition.evolutions.get(
                run.entered_evolution.evolution_id
            )
            if evolution is None:
                raise RutterStateError(
                    "active evolution is absent from its bound Rutter definition"
                )
            if isinstance(evolution, LLMStep):
                self._validate_llm_authority(
                    run,
                    evolution,
                    reckoning.global_revision,
                    reckoning.fault,
                    is_leaf=run.active_child is None,
                )
            active.append((run, definition))
            child = run.active_child
            if child is None:
                break
            if child.kind == "attached_case":
                hook = definition.transition_hooks_by_id.get(child.site)
                if hook is None:
                    raise RutterStateError(
                        "active attached child does not match a bound "
                        "TransitionHook"
                    )
                context = self._active_hook_context(
                    reckoning,
                    run,
                    definition,
                    child,
                    hook,
                    depth,
                )
                child_identity = (
                    child.run.rutter_id,
                    child.run.definition_version,
                )
                expected = self._resolve_contextual_hook(
                    reckoning,
                    run,
                    hook,
                    context,
                    expected_identity=child_identity,
                    reopening=True,
                )
                if child_identity != expected.identity:
                    raise RutterStateError(
                        "active child identity differs from its bound definition"
                    )
            run = child.run
            depth += 1

        leaf_run, leaf_definition = active[-1]
        leaf_evolution = leaf_definition.evolutions[
            leaf_run.entered_evolution.evolution_id
        ]
        self._validate_fault_authority(
            reckoning,
            leaf_run,
            leaf_definition,
        )
        self._validate_effect_authority(
            reckoning,
            leaf_run,
            leaf_evolution,
        )

        for parent, definition in active:
            child = parent.active_child
            if child is None:
                continue
            child_identity = (
                child.run.rutter_id,
                child.run.definition_version,
            )
            if child.kind == "explicit_call":
                site = definition.evolutions.get(child.site)
                if not isinstance(site, SubRutter):
                    raise RutterStateError(
                        "active explicit child does not match a bound "
                        "SubRutter evolution"
                    )
                expected = self._definition_identity(site.child)
            else:
                hook = definition.transition_hooks_by_id.get(child.site)
                if hook is None:
                    raise RutterStateError(
                        "active attached child does not match a bound "
                        "TransitionHook"
                    )
                transition_id = child.attached_to_transition_id
                assert transition_id is not None
                contextual = self._contextual_hook_children.get(
                    (parent.run_id, hook.id, transition_id)
                )
                if contextual is None:
                    raise RutterStateError(
                        "active attached child has no contextual definition"
                    )
                expected = contextual.identity
            if child_identity != expected:
                raise RutterStateError(
                    "active child identity differs from its bound definition"
                )

    @staticmethod
    def _validate_fault_authority(
        reckoning: Reckoning,
        leaf: ActiveRun,
        definition: _BoundDefinition,
    ) -> None:
        fault = reckoning.fault
        if fault is None or isinstance(fault, OpaqueFault):
            return
        if (
            fault.run_id != leaf.run_id
            or fault.evolution_id != leaf.entered_evolution.evolution_id
            or fault.evolution_entry_id != leaf.entered_evolution.entry_id
        ):
            raise RutterStateError(
                "known fault coordinates do not match the active evolution"
            )
        if (
            fault.target_evolution_id is not None
            and fault.target_evolution_id not in definition.evolutions
        ):
            raise RutterStateError(
                "known fault target is absent from its bound definition"
            )
        if any(
            hook_id not in definition.transition_hooks_by_id
            for hook_id in fault.transition_hook_ids
        ):
            raise RutterStateError(
                "known fault TransitionHook is absent from its bound definition"
            )

    @staticmethod
    def _validate_effect_authority(
        reckoning: Reckoning,
        leaf: ActiveRun,
        evolution: Evolution,
    ) -> None:
        effect = reckoning.active_effect
        if effect is not None:
            if not isinstance(evolution, MachineStep) or evolution.mode == "pure":
                raise RutterStateError(
                    "active effect recovery does not match the MachineStep"
                )
            if (
                effect.machine_id != _machine_id(leaf)
                or effect.mode != evolution.mode
            ):
                raise RutterStateError(
                    "active effect recovery does not match the MachineStep"
                )
            return
        if not isinstance(evolution, MachineStep) or evolution.mode == "pure":
            return
        source = _source_record(leaf, leaf.entered_evolution)
        if not isinstance(source, MachineRecord):
            raise RutterStateError(
                "effectful MachineStep has no recovery authority"
            )
        if (
            source.machine_id != _machine_id(leaf)
            or source.evolution_id != leaf.entered_evolution.evolution_id
            or source.mode != evolution.mode
        ):
            raise RutterStateError(
                "accepted effectful MachineRecord has invalid authority"
            )

    @staticmethod
    def _validate_llm_authority(
        run: ActiveRun,
        step: LLMStep,
        global_revision: int,
        fault: KnownFault | OpaqueFault | None,
        *,
        is_leaf: bool,
    ) -> None:
        entered = run.entered_evolution
        turns = tuple(
            entry
            for entry in run.history
            if isinstance(entry, Turn)
            and entry.evolution_entry_id == entered.entry_id
            and entry.evolution_id == entered.evolution_id
        )
        if len(turns) != 1:
            raise RutterStateError(
                "active LLMStep requires exactly one matching current Turn"
            )
        turn = turns[0]
        if (
            turn.message.text != step.text
            or turn.message.response_schema != step.response_schema
        ):
            raise RutterStateError(
                "active LLMStep Turn differs from the bound definition"
            )
        child = run.active_child
        if turn.response is None:
            if is_leaf and turn.revision != global_revision:
                raise RutterStateError(
                    "active LLMStep Turn revision differs from Reckoning revision"
                )
            if child is not None:
                raise RutterStateError(
                    "active LLMStep with an open Turn cannot own an active child"
                )
            return
        outcome = turn.response.get("outcome")
        if type(outcome) is not str:
            raise RutterStateError(
                "active LLMStep Turn has an invalid accepted outcome"
            )
        routing = step.next_on_outcome
        if isinstance(routing, Mapping) and outcome not in routing:
            raise RutterStateError(
                "active LLMStep Turn has an unaccepted outcome"
            )
        if child is None:
            if fault is None or isinstance(fault, OpaqueFault):
                return
            if (
                fault.run_id == run.run_id
                and fault.evolution_id == entered.evolution_id
                and fault.evolution_entry_id == entered.entry_id
            ):
                return
            raise RutterStateError(
                "accepted active LLMStep Turn has mismatched fault authority"
            )
        if (
            child.kind != "attached_case"
            or child.attached_to_transition_id != turn.record_id
        ):
            raise RutterStateError(
                "accepted active LLMStep Turn requires its matching "
                "attached child"
            )

    def _definition_identity(self, source: object) -> tuple[str, int]:
        for definition in self._definitions.values():
            if type(definition.definition) is source:
                return definition.identity
        raise RutterStateError(
            "active child source is absent from the bound graph"
        )

    def help(self) -> str:
        """Describe the public methods authorized for Compass operation."""

        entries: list[str] = []
        for name in self.compass_facing_methods:
            if not isinstance(name, str) or not name:
                raise RutterDefinitionError(
                    "Compass-facing method names must be nonempty strings"
                )
            if name.startswith("_"):
                raise RutterDefinitionError(
                    f"Compass-facing method {name!r} must not be private"
                )
            if not hasattr(self, name):
                raise RutterDefinitionError(
                    f"Compass-facing method {name!r} is missing"
                )
            method = getattr(self, name)
            if not callable(method):
                raise RutterDefinitionError(
                    f"Compass-facing method {name!r} must be callable"
                )
            documentation = getdoc(method)
            if not documentation:
                raise RutterDefinitionError(
                    f"Compass-facing method {name!r} requires a docstring"
                )
            try:
                bound_signature = signature(method)
            except (TypeError, ValueError) as exc:
                raise RutterDefinitionError(
                    f"Compass-facing method {name!r} requires an inspectable signature"
                ) from exc
            entries.append(f"{name}{bound_signature}\n{documentation}")
        return "\n\n".join(entries)

    def get_status(self) -> VoyageStatus:
        """Read one atomic status before deciding what the Voyage permits next.

        Classify the result through ``current_evolution.condition``. For a ready
        status whose ``instruction`` is a Message, perform
        ``text`` using ``payload`` and satisfy
        its optional ``response_schema`` with one flat response
        containing ``{"outcome": ...}``. Pass the Message's
        ``evolution_entry_id`` as
        ``responding_to``. If the instruction is absent or is a MachineInstruction, ask ``advance`` to
        settle it instead of executing it directly. A terminal status reports
        ``terminal_result`` and stops; fault reports ``fault`` and stops; uncertain
        stops for manual reconciliation. Treat any unknown condition or malformed
        instruction as a public-interface gap. Read a fresh status after every
        successful advance.
        """

        with self._store.transaction() as reckoning:
            self._reckoning = reckoning
            leaf = deepest_active_leaf(reckoning)
            definition = _leaf_definition(self, leaf)
            evolution = definition.evolutions[
                leaf.run.entered_evolution.evolution_id
            ]
            condition = _condition(reckoning, evolution, leaf=leaf)
            current = _node_view(
                reckoning,
                leaf,
                evolution,
                condition=condition,
            )
            instruction = _instruction_for(
                self,
                reckoning,
                leaf,
                evolution,
                condition,
            )
            terminal_result = None
            if condition == "terminal":
                terminal = HistoryView(
                    leaf.run.history,
                    reckoning.completed_runs,
                ).terminal()
                assert terminal is not None
                terminal_result = terminal.result
            fault = reckoning.fault
            if isinstance(fault, KnownFault):
                summary = FaultSummary(
                    fault.category,
                    fault.evolution_id,
                    fault.evolution_entry_id,
                    fault.target_evolution_id,
                    fault.transition_hook_ids,
                )
            elif isinstance(fault, OpaqueFault):
                summary = FaultSummary("opaque", None, None, None, ())
            else:
                summary = None
            return VoyageStatus(
                current,
                instruction,
                terminal_result,
                summary,
            )

    def validate(
        self,
        value: object,
        *,
        responding_to: str | None = None,
    ) -> ValidationReport:
        """Validate a proposed Message response without changing the Voyage.

        Before passing a response to ``advance``, require a valid report. Repair an
        invalid response only from its public issues and validate it again; stop
        with a public-interface gap if those issues cannot guide a valid repair.
        """

        return _validate(self, value, responding_to=responding_to)

    def advance(
        self,
        value: object = MISSING,
        *,
        responding_to: str | None = None,
        continue_: bool = True,
        dry_run: bool = False,
    ) -> EvolutionView:
        """Advance once, optionally settling automatic and nested work.

        Pass a Message response only after ``validate`` accepts that same value.
        With no response, continuation settles ready non-LLM work. Continuation
        returns only the final entered EvolutionView; durable history owns every
        intermediate traversal. ``dry_run=True`` previews only the immediate
        parent transition, performs no work, grants no authority, and is outside
        the normal Compass loop. Read ``get_status`` after a real advance.
        """

        return _advance(
            self,
            value,
            responding_to=responding_to,
            continue_=continue_,
            dry_run=dry_run,
        )


def _instruction_for(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    evolution: Evolution,
    condition: str,
) -> Message | MachineInstruction | None:
    if condition != "ready":
        return None
    source = _source_record(leaf.run, leaf.run.entered_evolution)
    if _is_recorded_source(evolution, source):
        return None
    if isinstance(evolution, LLMStep):
        return _prompt_turn(reckoning, leaf.run).message
    if isinstance(evolution, MachineStep) and evolution.mode == "pure":
        return _pure_machine_instruction(reckoning, leaf.run, evolution)
    if isinstance(evolution, MachineStep):
        effect = _machine_effect(reckoning)
        return _effectful_machine_instruction(
            voyage,
            effect.machine_id,
            evolution.mode,
        )
    return None


def _validate(
    voyage: Voyage,
    value: object,
    *,
    responding_to: str | None = None,
) -> ValidationReport:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = deepest_active_leaf(reckoning)
        evolution = _leaf_definition(voyage, leaf).evolutions[leaf.run.entered_evolution.evolution_id]
        condition = _condition(reckoning, evolution)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        source = _source_record(leaf.run, leaf.run.entered_evolution)
        if _is_recorded_source(evolution, source):
            raise NotApplicable("an accepted node does not accept another response")
        if isinstance(evolution, MachineStep):
            if responding_to is not None:
                return _invalid(
                    "unexpected-responding-to",
                    (),
                    "responding_to is valid only for an LLM response",
                )
            return _validate_machine_result(value)
        if not isinstance(evolution, LLMStep):
            raise NotApplicable("the current node does not accept a response")
        try:
            definition = _leaf_definition(voyage, leaf)
            return _validate_prompt(
                reckoning,
                leaf.run,
                definition,
                evolution,
                value,
                responding_to,
            )
        except _RutterFault:
            return _invalid(
                "contextual-validation-failed",
                (),
                "LLMStep contextual validation failed",
            )


def _is_missing(value: object) -> bool:
    return value is MISSING


def _publish(
    voyage: Voyage,
    previous: Reckoning,
    replacement: Reckoning,
) -> Reckoning:
    voyage._store.replace(previous, replacement)
    voyage._reckoning = replacement
    return replacement


def _fault_and_publish(
    voyage: Voyage,
    previous: Reckoning,
    fault_base: Reckoning,
    fault: _RutterFault,
) -> EvolutionView:
    anchored = replace(
        fault_base,
        global_revision=previous.global_revision,
    )
    leaf = deepest_active_leaf(anchored)
    faulted = _fault_reckoning(
        anchored,
        leaf,
        fault.category,
        target_evolution_id=fault.target_evolution_id,
        transition_hook_ids=fault.transition_hook_ids,
    )
    _publish(voyage, previous, faulted)
    evolution = _leaf_definition(voyage, leaf).evolutions[leaf.run.entered_evolution.evolution_id]
    return _node_view(faulted, leaf, evolution)


def _advance_call(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    evolution: SubRutter,
) -> Reckoning:
    record = _source_record(leaf.run, leaf.run.entered_evolution)
    if isinstance(record, SubRutterRecord):
        return _continue_recorded_transition(
            voyage,
            reckoning,
            leaf,
            definition,
            record,
        )
    child_definition = _call_child_definition(voyage, evolution)
    return _push_call(
        reckoning,
        leaf.run.run_id,
        evolution,
        child_definition,
    )


def _call_transition(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    evolution: SubRutter,
) -> Transition | None:
    record = _source_record(leaf.run, leaf.run.entered_evolution)
    if not isinstance(record, SubRutterRecord):
        return None
    transition, _ = _recorded_transition(reckoning, leaf, definition, record)
    return transition


def _preview_machine(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    action: MachineStep,
    response: object,
) -> EvolutionView:
    if action.mode == "pure":
        if _is_missing(response):
            raise PreviewUnavailable("MachineStep result is not supplied")
        machine_id = _machine_id(leaf.run)
    else:
        effect = _machine_effect(reckoning)
        if effect.disposition != "completed":
            raise PreviewUnavailable("MachineStep result is not yet available")
        machine_id = effect.machine_id
        assert effect.result is not None
        authority = effect.result
        if _is_missing(response):
            response = authority
    report = _validate_machine_result(response)
    if not report.valid:
        raise RutterValidationError("MachineStep result was rejected")
    result = (
        response
        if isinstance(response, MachineResult)
        else MachineResult.from_json(response)
    )
    if action.mode != "pure" and result != authority:
        raise RutterValidationError(
            "MachineStep result does not match completed recovery"
        )
    record = MachineRecord(
        f"preview-{machine_id}",
        machine_id,
        leaf.run.entered_evolution.entry_id,
        leaf.run.entered_evolution.evolution_id,
        action.mode,
        result,
    )
    preview_run = replace(leaf.run, history=leaf.run.history + (record,))
    history = HistoryView(preview_run.history, reckoning.completed_runs)
    try:
        transition = _select_transition(
            BoundRun(preview_run, definition),
            history.strict_prefix(record),
            record,
        )
    except _RutterFault as fault:
        raise RutterValidationError("MachineStep routing failed") from fault
    assert transition.target is not None
    return EvolutionView(
        leaf.run.rutter_id,
        leaf.run.definition_version,
        transition.target,
        None,
        leaf.depth,
        "preview",
    )


def _advance_machine(
    voyage: Voyage,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinition,
    action: MachineStep,
    response: object,
    *,
    dry_run: bool,
) -> tuple[Reckoning, EvolutionView | None]:
    if dry_run:
        return reckoning, _preview_machine(
            reckoning,
            leaf,
            definition,
            action,
            response,
        )
    omitted = _is_missing(response)
    if action.mode == "pure":
        machine_id = _machine_id(leaf.run)
        if omitted:
            try:
                response = _pure_machine_instruction(
                    reckoning,
                    leaf.run,
                    action,
                ).run()
            except _RutterFault as fault:
                view = _fault_and_publish(
                    voyage,
                    reckoning,
                    reckoning,
                    fault,
                )
                return voyage._reckoning, view
            if not isinstance(response, MachineResult):
                fault = _RutterFault("action-result")
                view = _fault_and_publish(
                    voyage,
                    reckoning,
                    reckoning,
                    fault,
                )
                return voyage._reckoning, view
    else:
        effect = _machine_effect(reckoning)
        machine_id = effect.machine_id
        if omitted:
            try:
                reckoning, response = _run_effectful_machine(
                    voyage,
                    reckoning,
                    leaf,
                    action,
                    effect,
                )
            except _RutterFault as fault:
                current = voyage._reckoning
                view = _fault_and_publish(
                    voyage,
                    current,
                    current,
                    fault,
                )
                return voyage._reckoning, view
            effect = _machine_effect(reckoning)
        if effect.disposition != "completed":
            raise RutterValidationError("completed MachineStep recovery is required")
    report = _validate_machine_result(response)
    if not report.valid:
        raise RutterValidationError("MachineStep result was rejected")
    normalized = (
        response
        if isinstance(response, MachineResult)
        else MachineResult.from_json(response)
    )
    if action.mode != "pure":
        assert effect.result is not None
        authority = effect.result
        if normalized != authority:
            raise RutterValidationError(
                "MachineStep result does not match completed recovery"
            )
        normalized = authority
    accepted = _accept_machine(reckoning, action, machine_id, normalized)
    accepted_leaf = deepest_active_leaf(accepted)
    record = _source_record(accepted_leaf.run, accepted_leaf.run.entered_evolution)
    assert isinstance(record, MachineRecord)
    history = HistoryView(accepted_leaf.run.history, accepted.completed_runs)
    try:
        transition = _select_transition(
            BoundRun(accepted_leaf.run, definition),
            history.strict_prefix(record),
            record,
        )
    except _RutterFault as fault:
        view = _fault_and_publish(voyage, reckoning, accepted, fault)
        return voyage._reckoning, view
    assert transition.target is not None
    try:
        entered = _continue_transition(
            voyage,
            accepted,
            accepted_leaf,
            definition,
            transition,
            history.strict_prefix(record),
            record,
        )
    except _RutterFault as fault:
        view = _fault_and_publish(voyage, reckoning, accepted, fault)
        return voyage._reckoning, view
    _publish(voyage, reckoning, entered)
    return entered, None


def _advance(
    voyage: Voyage,
    value: object = MISSING,
    *,
    responding_to: str | None = None,
    continue_: bool = True,
    dry_run: bool = False,
) -> EvolutionView:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = deepest_active_leaf(reckoning)
        definition = _leaf_definition(voyage, leaf)
        evolution = definition.evolutions[leaf.run.entered_evolution.evolution_id]
        condition = _condition(reckoning, evolution)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        if condition == "terminal":
            if responding_to is not None:
                raise RutterValidationError(
                    "responding_to is valid only for an LLM response"
                )
            if not _is_missing(value):
                raise NotApplicable("a terminal voyage does not accept a response")
            if dry_run:
                return _node_view(reckoning, leaf, evolution)

        source = _source_record(leaf.run, leaf.run.entered_evolution)
        recorded = _is_recorded_source(evolution, source)
        if recorded:
            if responding_to is not None:
                raise RutterValidationError(
                    "responding_to is valid only for an LLM response"
                )
            if not _is_missing(value):
                raise NotApplicable("an accepted node does not accept another response")
            if dry_run:
                assert source is not None
                try:
                    transition, _ = _recorded_transition(reckoning, leaf, definition, source)
                except _RutterFault as fault:
                    raise RutterValidationError("routing failed") from fault
                if transition.target is None:
                    return _node_view(reckoning, leaf, evolution)
                return EvolutionView(
                    leaf.run.rutter_id,
                    leaf.run.definition_version,
                    transition.target,
                    None,
                    leaf.depth,
                    "preview",
                )
        elif isinstance(evolution, LLMStep):
            if _is_missing(value):
                raise RutterValidationError("LLMStep response is required")
            try:
                report = _validate_prompt(
                    reckoning,
                    leaf.run,
                    definition,
                    evolution,
                    value,
                    responding_to,
                )
            except _RutterFault as fault:
                if dry_run:
                    raise RutterValidationError("LLMStep validation failed") from fault
                return _fault_and_publish(voyage, reckoning, reckoning, fault)
            if not report.valid:
                raise RutterValidationError("LLMStep response was rejected")
            normalized = _freeze_object(
                value,
                "response",
                error=RutterStateError,
            )
            accepted = _accept_prompt(reckoning, normalized)
            accepted_leaf = deepest_active_leaf(accepted)
            record = _source_record(accepted_leaf.run, accepted_leaf.run.entered_evolution)
            assert isinstance(record, Turn)
            history = HistoryView(
                accepted_leaf.run.history,
                accepted.completed_runs,
            )
            try:
                transition = _select_transition(
                    BoundRun(accepted_leaf.run, definition),
                    history.strict_prefix(record),
                    record,
                )
            except _RutterFault as fault:
                if dry_run:
                    raise RutterValidationError("LLMStep routing failed") from fault
                return _fault_and_publish(voyage, reckoning, accepted, fault)
            assert transition.target is not None
            if dry_run:
                return EvolutionView(
                    accepted_leaf.run.rutter_id,
                    accepted_leaf.run.definition_version,
                    transition.target,
                    None,
                    accepted_leaf.depth,
                    "preview",
                )

            try:
                entered = _continue_transition(
                    voyage,
                    accepted,
                    accepted_leaf,
                    definition,
                    transition,
                    history.strict_prefix(record),
                    record,
                )
            except _RutterFault as fault:
                return _fault_and_publish(voyage, reckoning, accepted, fault)
            _publish(voyage, reckoning, entered)
            reckoning = entered
            if not continue_:
                entered_leaf = deepest_active_leaf(reckoning)
                entered_definition = _leaf_definition(voyage, entered_leaf)
                entered_state = entered_definition.evolutions[
                    entered_leaf.run.entered_evolution.evolution_id
                ]
                return _node_view(reckoning, entered_leaf, entered_state)
        elif isinstance(evolution, SubRutter):
            if responding_to is not None:
                raise RutterValidationError(
                    "responding_to is valid only for an LLM response"
                )
            if not _is_missing(value):
                raise NotApplicable("SubRutter does not accept a response")
        elif isinstance(evolution, MachineStep):
            if responding_to is not None:
                raise RutterValidationError(
                    "responding_to is valid only for an LLM response"
                )
            reckoning, stopped = _advance_machine(
                voyage,
                reckoning,
                leaf,
                definition,
                evolution,
                value,
                dry_run=dry_run,
            )
            if stopped is not None:
                return stopped
            if not continue_:
                entered_leaf = deepest_active_leaf(reckoning)
                entered_definition = _leaf_definition(voyage, entered_leaf)
                entered_state = entered_definition.evolutions[
                    entered_leaf.run.entered_evolution.evolution_id
                ]
                return _node_view(reckoning, entered_leaf, entered_state)
        elif not isinstance(evolution, Terminal):
            raise NotApplicable("the current node does not accept a response")
        elif responding_to is not None:
            raise RutterValidationError(
                "responding_to is valid only for an LLM response"
            )
        elif not _is_missing(value):
            raise NotApplicable("Terminal does not accept a response")

        for _ in range(_OPERATION_LIMIT):
            leaf = deepest_active_leaf(reckoning)
            definition = _leaf_definition(voyage, leaf)
            evolution = definition.evolutions[leaf.run.entered_evolution.evolution_id]
            source = _source_record(leaf.run, leaf.run.entered_evolution)
            if _is_recorded_source(evolution, source):
                assert source is not None
                try:
                    advanced = _continue_recorded_transition(
                        voyage,
                        reckoning,
                        leaf,
                        definition,
                        source,
                    )
                except _RutterFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                if advanced is not reckoning:
                    _publish(voyage, reckoning, advanced)
                    reckoning = advanced
                    if not continue_:
                        advanced_leaf = deepest_active_leaf(reckoning)
                        advanced_state = _leaf_definition(
                            voyage, advanced_leaf
                        ).evolutions[advanced_leaf.run.entered_evolution.evolution_id]
                        return _node_view(reckoning, advanced_leaf, advanced_state)
                    continue
                if not isinstance(evolution, Terminal):
                    raise RutterStateError("recorded transition did not advance")
                if leaf.depth == 0:
                    return _node_view(reckoning, leaf, evolution)
                returned = return_active_child(reckoning, leaf.run.run_id)
                _publish(voyage, reckoning, returned)
                reckoning = returned
                continue
            if isinstance(evolution, LLMStep):
                return _node_view(reckoning, leaf, evolution)
            if isinstance(evolution, MachineStep):
                reckoning, stopped = _advance_machine(
                    voyage,
                    reckoning,
                    leaf,
                    definition,
                    evolution,
                    MISSING,
                    dry_run=False,
                )
                if stopped is not None:
                    return stopped
                if not continue_:
                    advanced_leaf = deepest_active_leaf(reckoning)
                    advanced_definition = _leaf_definition(voyage, advanced_leaf)
                    advanced_state = advanced_definition.evolutions[
                        advanced_leaf.run.entered_evolution.evolution_id
                    ]
                    return _node_view(reckoning, advanced_leaf, advanced_state)
                continue
            if isinstance(evolution, SubRutter):
                if dry_run:
                    try:
                        transition = _call_transition(reckoning, leaf, definition, evolution)
                    except _RutterFault as fault:
                        raise RutterValidationError("SubRutter routing failed") from fault
                    if transition is None:
                        raise PreviewUnavailable("SubRutter result is not yet available")
                    assert transition.target is not None
                    return EvolutionView(
                        leaf.run.rutter_id,
                        leaf.run.definition_version,
                        transition.target,
                        None,
                        leaf.depth,
                        "preview",
                    )
                try:
                    advanced = _advance_call(
                        voyage,
                        reckoning,
                        leaf,
                        definition,
                        evolution,
                    )
                except _RutterFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                _publish(voyage, reckoning, advanced)
                reckoning = advanced
                if not continue_:
                    advanced_leaf = deepest_active_leaf(reckoning)
                    advanced_state = _leaf_definition(
                        voyage, advanced_leaf
                    ).evolutions[advanced_leaf.run.entered_evolution.evolution_id]
                    return _node_view(reckoning, advanced_leaf, advanced_state)
                continue
            if not isinstance(evolution, Terminal):
                return _node_view(reckoning, leaf, evolution)
            try:
                result = _project_terminal(reckoning, leaf.run, evolution)
            except _RutterFault as fault:
                if dry_run:
                    raise RutterValidationError("Terminal projection failed") from fault
                return _fault_and_publish(voyage, reckoning, reckoning, fault)
            if dry_run:
                return _node_view(reckoning, leaf, evolution, preview=True)
            settled = _settle_terminal(reckoning, leaf.run.run_id, result)
            _publish(voyage, reckoning, settled)
            reckoning = settled
            settled_leaf = deepest_active_leaf(reckoning)
            if not continue_:
                return _node_view(reckoning, settled_leaf, evolution)
            if settled_leaf.depth == 0:
                source = _source_record(
                    settled_leaf.run, settled_leaf.run.entered_evolution
                )
                assert isinstance(source, TerminalRecord)
                try:
                    advanced = _continue_recorded_transition(
                        voyage,
                        reckoning,
                        settled_leaf,
                        definition,
                        source,
                    )
                except _RutterFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                if advanced is reckoning:
                    return _node_view(reckoning, settled_leaf, evolution)
                _publish(voyage, reckoning, advanced)
                reckoning = advanced
        raise RutterStateError("automatic continuation limit exhausted")



__all__ = ("MISSING", "Voyage")
