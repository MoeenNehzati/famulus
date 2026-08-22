"""Reduce bound Rutter voyages across durable Prompt and Done operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Protocol
from uuid import uuid4

from officina.rutter.model import (
    ActiveRun,
    AnswerContext,
    Charter,
    Done,
    DoneRecord,
    EnteredNode,
    HistoryEntry,
    HistoryView,
    Message,
    NodeView,
    NotApplicable,
    Prompt,
    Reckoning,
    Response,
    RunBlocked,
    RunResult,
    RutterStateError,
    RutterValidationError,
    State,
    StateContext,
    Turn,
    ValidationIssue,
    ValidationReport,
)
from officina.rutter.runtime import _MISSING


_OPERATION_LIMIT = 100


class _BoundDefinitionLike(Protocol):
    rutter_id: str
    definition_version: int
    start_state: str
    states: Mapping[str, State]


class _StoreLike(Protocol):
    def transaction(self): ...


class _BoundVoyageLike(Protocol):
    _definition: _BoundDefinitionLike
    _definitions: Mapping[tuple[str, int], _BoundDefinitionLike]
    _reckoning: Reckoning
    _store: _StoreLike


@dataclass(frozen=True)
class ActiveLeaf:
    run: ActiveRun
    depth: int


@dataclass(frozen=True)
class BoundRun:
    run: ActiveRun
    definition: _BoundDefinitionLike


@dataclass(frozen=True)
class Edge:
    edge_id: str
    source_entry_id: str
    source: str
    outcome: str
    target: str | None


class _EngineFault(Exception):
    def __init__(self, category: str, *, target: str | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.target = target


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _active_leaf(reckoning: Reckoning) -> ActiveLeaf:
    run = reckoning.root
    depth = 0
    while run.active_child is not None:
        run = run.active_child.run
        depth += 1
    return ActiveLeaf(run, depth)


def _leaf_definition(
    voyage: _BoundVoyageLike,
    leaf: ActiveLeaf,
) -> _BoundDefinitionLike:
    identity = (leaf.run.rutter_id, leaf.run.definition_version)
    try:
        return voyage._definitions[identity]
    except KeyError as exc:
        raise RutterStateError("active Rutter definition is unavailable") from exc


def _condition(reckoning: Reckoning, state: State) -> str:
    if reckoning.fault is not None:
        return "fault"
    effect = reckoning.active_effect
    if effect is not None and effect.get("disposition") == "uncertain":
        return "uncertain"
    leaf = _active_leaf(reckoning)
    if isinstance(state, Done) and isinstance(
        _source_record(leaf.run, leaf.run.entered_node), DoneRecord
    ):
        return "terminal"
    return "ready"


def _node_view(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    state: State,
    *,
    preview: bool = False,
) -> NodeView:
    return NodeView(
        leaf.run.rutter_id,
        leaf.run.definition_version,
        leaf.run.entered_node.state_id,
        None if preview else leaf.run.entered_node.entry_id,
        leaf.depth,
        "preview" if preview else _condition(reckoning, state),
    )


def _invalid(code: str, path: tuple[str | int, ...], message: str) -> ValidationReport:
    return ValidationReport(False, (ValidationIssue(path, code, message),))


def _prompt_turn(reckoning: Reckoning, run: ActiveRun) -> Turn:
    turn = HistoryView(run.history, reckoning.completed_runs).open_turn()
    if turn is None or turn.node_entry_id != run.entered_node.entry_id:
        raise RutterStateError("entered Prompt has no matching open Turn")
    return turn


def _replace_active_run(
    run: ActiveRun, run_id: str, replacement: ActiveRun
) -> ActiveRun:
    if run.run_id == run_id:
        return replacement
    child = run.active_child
    if child is None:
        raise RutterStateError("active run is absent from the Reckoning")
    replaced_child = _replace_active_run(child.run, run_id, replacement)
    return replace(run, active_child=replace(child, run=replaced_child))


def _replace_run(
    reckoning: Reckoning, run_id: str, replacement: ActiveRun
) -> Reckoning:
    return replace(
        reckoning,
        root=_replace_active_run(reckoning.root, run_id, replacement),
    )


def _accept_prompt(
    reckoning: Reckoning,
    response: Response,
) -> Reckoning:
    """Fill the active Prompt's exact open Turn and advance the global revision."""

    leaf = _active_leaf(reckoning)
    turn = _prompt_turn(reckoning, leaf.run)
    history = tuple(
        replace(entry, response=response) if entry is turn else entry
        for entry in leaf.run.history
    )
    accepted_run = replace(leaf.run, history=history)
    return replace(
        _replace_run(reckoning, leaf.run.run_id, accepted_run),
        global_revision=reckoning.global_revision + 1,
    )


def _source_record(run: ActiveRun, entered_node: EnteredNode) -> HistoryEntry | None:
    for record in reversed(run.history):
        if record.node_entry_id == entered_node.entry_id:
            return record
    return None


def _select_edge(
    bound_run: BoundRun,
    strict_prefix: HistoryView,
    record: HistoryEntry,
) -> Edge:
    state_id = bound_run.run.entered_node.state_id
    state = bound_run.definition.states[state_id]
    if not isinstance(state, Prompt) or not isinstance(record, Turn):
        raise _EngineFault("routing")
    response = record.response
    if response is None:
        raise _EngineFault("routing")
    routing = state.then
    target: object
    if type(routing) is str:
        target = routing
    elif isinstance(routing, Mapping):
        target = routing.get(response.outcome)
    else:
        context = AnswerContext(
            StateContext(
                bound_run.run.charter,
                state_id,
                bound_run.run.entered_node.entry_id,
                strict_prefix,
            ),
            record.message,
            response,
        )
        try:
            target = routing(context)  # type: ignore[operator]
        except Exception as exc:
            raise _EngineFault("routing") from exc
    if type(target) is not str or target not in bound_run.definition.states:
        raise _EngineFault(
            "routing",
            target=target if type(target) is str else None,
        )
    return Edge(
        record.record_id,
        bound_run.run.entered_node.entry_id,
        state_id,
        response.outcome,
        target,
    )


def _enter_node(
    reckoning: Reckoning,
    run_id: str,
    target: str,
    *,
    definition: _BoundDefinitionLike,
) -> Reckoning:
    """Enter one target; Prompt entrance and open-Turn creation are atomic."""

    leaf = _active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may enter a node")
    try:
        target_state = definition.states[target]
    except KeyError as exc:
        raise _EngineFault("routing", target=target) from exc
    entered_run = replace(
        leaf.run,
        entered_node=EnteredNode(_new_id("entry"), target),
    )
    entered = _replace_run(reckoning, run_id, entered_run)
    if isinstance(target_state, Prompt):
        entered_leaf = _active_leaf(entered)
        try:
            turn = _render_prompt(entered, entered_leaf.run, target_state)
        except Exception as exc:
            raise _EngineFault("target-materialization", target=target) from exc
        entered_run = replace(
            entered_leaf.run,
            history=entered_leaf.run.history + (turn,),
        )
        entered = _replace_run(entered, run_id, entered_run)
    return entered


def _settle_done(reckoning: Reckoning, run_id: str, result: RunResult) -> Reckoning:
    leaf = _active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may settle Done")
    record = DoneRecord(
        _new_id("done"),
        leaf.run.entered_node.entry_id,
        leaf.run.entered_node.state_id,
        result,
    )
    settled = replace(leaf.run, history=leaf.run.history + (record,))
    return replace(
        _replace_run(reckoning, run_id, settled),
        global_revision=reckoning.global_revision + 1,
    )


def _project_done(reckoning: Reckoning, run: ActiveRun, state: Done) -> RunResult:
    if isinstance(state.result, RunResult):
        return state.result
    try:
        result = state.result(_state_context(run, reckoning))
    except Exception as exc:
        raise _EngineFault("done-projection") from exc
    if not isinstance(result, RunResult):
        raise _EngineFault("done-projection")
    return result


def _fault_reckoning(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    category: str,
    *,
    target: str | None = None,
) -> Reckoning:
    fault: dict[str, object] = {
        "category": category,
        "run_id": leaf.run.run_id,
        "state_id": leaf.run.entered_node.state_id,
        "node_entry_id": leaf.run.entered_node.entry_id,
    }
    if target is not None:
        fault["target_state_id"] = target
    return replace(reckoning, fault=fault)


def _validate_prompt(
    reckoning: Reckoning,
    run: ActiveRun,
    prompt: Prompt,
    value: object,
) -> ValidationReport:
    if not isinstance(value, Mapping) or set(value) != {
        "revision",
        "outcome",
        "evidence",
    }:
        return _invalid(
            "invalid-envelope",
            (),
            "response must contain exactly revision, outcome, and evidence",
        )
    revision = value["revision"]
    if type(revision) is not int or revision < 0:
        return _invalid(
            "invalid-envelope",
            ("revision",),
            "response revision must be a nonnegative integer",
        )
    if revision != reckoning.global_revision:
        return _invalid(
            "stale-revision",
            ("revision",),
            "response revision does not match the current revision",
        )
    outcome = value["outcome"]
    if type(outcome) is not str or outcome not in prompt.answer.outcomes:
        return _invalid(
            "unknown-outcome",
            ("outcome",),
            "response outcome is not declared by this Prompt",
        )
    if not isinstance(value["evidence"], Mapping):
        return _invalid(
            "invalid-envelope",
            ("evidence",),
            "response evidence must be an object",
        )
    try:
        response = Response.from_json(value)
    except RutterStateError:
        return _invalid(
            "nonfinite-evidence",
            ("evidence",),
            "response evidence must be finite JSON",
        )
    turn = _prompt_turn(reckoning, run)
    try:
        report = prompt.validate(
            AnswerContext(_state_context(run, reckoning), turn.message, response)
        )
    except Exception as exc:
        raise _EngineFault("contextual-validation") from exc
    if not isinstance(report, ValidationReport):
        return _invalid(
            "invalid-validator-result",
            (),
            "Prompt validator must return a ValidationReport",
        )
    return report


def _state_context(
    run: ActiveRun,
    reckoning: Reckoning,
    *,
    history: tuple[HistoryEntry, ...] | None = None,
) -> StateContext:
    if history is None:
        current_entry = run.entered_node.entry_id
        boundary = next(
            (
                index
                for index, entry in enumerate(run.history)
                if entry.node_entry_id == current_entry
            ),
            len(run.history),
        )
        entries = run.history[:boundary]
    else:
        entries = history
    return StateContext(
        run.charter,
        run.entered_node.state_id,
        run.entered_node.entry_id,
        HistoryView(entries, reckoning.completed_runs),
    )


def _render_prompt(
    reckoning: Reckoning,
    run: ActiveRun,
    prompt: Prompt,
) -> Turn:
    context = _state_context(run, reckoning)
    message = Message(
        instructions={"text": prompt.text, "answer": prompt.answer.outcomes},
        data={
            "state": {
                "id": run.entered_node.state_id,
                "entry_id": run.entered_node.entry_id,
                "revision": reckoning.global_revision,
            },
            "payload": prompt.data(context),
        },
    )
    return Turn(
        _new_id("turn"),
        run.entered_node.entry_id,
        run.entered_node.state_id,
        reckoning.global_revision,
        message,
        None,
    )


def _create_reckoning(
    definition: _BoundDefinitionLike,
    charter: Charter,
) -> Reckoning:
    """Create one initial entrance, including a Prompt's exact open Turn."""

    entered = EnteredNode(_new_id("entry"), definition.start_state)
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
    state = definition.states[definition.start_state]
    if isinstance(state, Prompt):
        try:
            turn = _render_prompt(reckoning, run, state)
        except Exception as exc:
            raise RutterStateError("Prompt materialization failed") from exc
        run = ActiveRun(
            run.run_id,
            run.rutter_id,
            run.definition_version,
            run.charter,
            run.entered_node,
            (turn,),
            None,
        )
        reckoning = Reckoning(3, 0, run, {}, None, None)
    return reckoning


def _get_instruction(voyage: _BoundVoyageLike) -> object | None:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        if _condition(reckoning, state) != "ready" or not isinstance(state, Prompt):
            return None
        return _prompt_turn(reckoning, leaf.run).message


def _validate(voyage: _BoundVoyageLike, response: object) -> ValidationReport:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        condition = _condition(reckoning, state)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        if not isinstance(state, Prompt):
            raise NotApplicable("the current node does not accept a response")
        try:
            return _validate_prompt(reckoning, leaf.run, state, response)
        except _EngineFault:
            return _invalid(
                "contextual-validation-failed",
                (),
                "Prompt contextual validation failed",
            )


def _is_missing(value: object) -> bool:
    return value is _MISSING


def _publish(
    voyage: _BoundVoyageLike,
    previous: Reckoning,
    replacement: Reckoning,
) -> Reckoning:
    voyage._store.replace(previous, replacement)  # type: ignore[attr-defined]
    voyage._reckoning = replacement
    return replacement


def _fault_and_publish(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    fault: _EngineFault,
) -> NodeView:
    leaf = _active_leaf(reckoning)
    faulted = _fault_reckoning(
        reckoning,
        leaf,
        fault.category,
        target=fault.target,
    )
    _publish(voyage, reckoning, faulted)
    state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
    return _node_view(faulted, leaf, state)


def _next(
    voyage: _BoundVoyageLike,
    response: object = _MISSING,
    *,
    continue_: bool = True,
    dry_run: bool = False,
) -> NodeView:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        definition = _leaf_definition(voyage, leaf)
        state = definition.states[leaf.run.entered_node.state_id]
        condition = _condition(reckoning, state)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        if condition == "terminal":
            if not _is_missing(response):
                raise NotApplicable("a terminal voyage does not accept a response")
            return _node_view(reckoning, leaf, state)

        if isinstance(state, Prompt):
            if _is_missing(response):
                raise RutterValidationError("Prompt response is required")
            try:
                report = _validate_prompt(reckoning, leaf.run, state, response)
            except _EngineFault as fault:
                if dry_run:
                    raise RutterValidationError("Prompt validation failed") from fault
                return _fault_and_publish(voyage, reckoning, fault)
            if not report.valid:
                raise RutterValidationError("Prompt response was rejected")
            normalized = Response.from_json(response)
            accepted = _accept_prompt(reckoning, normalized)
            accepted_leaf = _active_leaf(accepted)
            record = _source_record(accepted_leaf.run, accepted_leaf.run.entered_node)
            assert isinstance(record, Turn)
            history = HistoryView(
                accepted_leaf.run.history,
                accepted.completed_runs,
            )
            try:
                edge = _select_edge(
                    BoundRun(accepted_leaf.run, definition),
                    history.strict_prefix(record),
                    record,
                )
            except _EngineFault as fault:
                if dry_run:
                    raise RutterValidationError("Prompt routing failed") from fault
                _publish(voyage, reckoning, accepted)
                return _fault_and_publish(voyage, accepted, fault)
            assert edge.target is not None
            if dry_run:
                return NodeView(
                    accepted_leaf.run.rutter_id,
                    accepted_leaf.run.definition_version,
                    edge.target,
                    None,
                    accepted_leaf.depth,
                    "preview",
                )

            _publish(voyage, reckoning, accepted)
            try:
                entered = _enter_node(
                    accepted,
                    accepted_leaf.run.run_id,
                    edge.target,
                    definition=definition,
                )
            except _EngineFault as fault:
                return _fault_and_publish(voyage, accepted, fault)
            _publish(voyage, accepted, entered)
            reckoning = entered
            if not continue_:
                entered_leaf = _active_leaf(reckoning)
                entered_state = definition.states[
                    entered_leaf.run.entered_node.state_id
                ]
                return _node_view(reckoning, entered_leaf, entered_state)
        elif not isinstance(state, Done):
            raise NotApplicable("this lifecycle node is implemented by a later task")
        elif not _is_missing(response):
            raise NotApplicable("Done does not accept a response")

        for _ in range(_OPERATION_LIMIT):
            leaf = _active_leaf(reckoning)
            definition = _leaf_definition(voyage, leaf)
            state = definition.states[leaf.run.entered_node.state_id]
            if not isinstance(state, Done):
                return _node_view(reckoning, leaf, state)
            if isinstance(_source_record(leaf.run, leaf.run.entered_node), DoneRecord):
                return _node_view(reckoning, leaf, state)
            try:
                result = _project_done(reckoning, leaf.run, state)
            except _EngineFault as fault:
                if dry_run:
                    raise RutterValidationError("Done projection failed") from fault
                return _fault_and_publish(voyage, reckoning, fault)
            if dry_run:
                return _node_view(reckoning, leaf, state, preview=True)
            settled = _settle_done(reckoning, leaf.run.run_id, result)
            _publish(voyage, reckoning, settled)
            return _node_view(settled, _active_leaf(settled), state)
        raise RutterStateError("automatic continuation limit exhausted")


def _get_current_node(voyage: _BoundVoyageLike) -> NodeView:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        return _node_view(reckoning, leaf, state)
