"""Reduce bound Rutter voyages across durable Prompt and Done operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol, cast
from uuid import uuid4

from officina.rutter.model import (
    Action,
    ActionContext,
    ActionRecord,
    ActionResult,
    ActiveChild,
    ActiveRun,
    AnswerContext,
    Charter,
    Call,
    CallRecord,
    CompletedRun,
    Done,
    DoneRecord,
    EdgeContext,
    EnteredNode,
    HistoryEntry,
    HistoryView,
    Message,
    NodeView,
    NotApplicable,
    Prompt,
    PreviewUnavailable,
    PythonInstruction,
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
from officina.rutter.storage import _MAX_ACTIVE_DEPTH


_OPERATION_LIMIT = 100
_ACTION_RESULT_FORMAT = {
    "outcome": "declared outcome",
    "value": {"type": "finite JSON"},
}


class _BoundDefinitionLike(Protocol):
    rutter_id: str
    definition_version: int
    start_state: str
    allow_multiple_cases_at_once: bool
    states: Mapping[str, State]
    case_makers: tuple[object, ...]


class _EdgeMatchLike(Protocol):
    def matches(self, edge: object) -> bool: ...


class _CaseMakerLike(Protocol):
    id: str
    on: _EdgeMatchLike
    child: type
    charter: Callable[[EdgeContext], Mapping[str, object] | None]


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
    def __init__(
        self,
        category: str,
        *,
        target: str | None = None,
        case_maker_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(category)
        self.category = category
        self.target = target
        self.case_maker_ids = case_maker_ids


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _active_leaf(reckoning: Reckoning) -> ActiveLeaf:
    run = reckoning.root
    depth = 0
    while run.active_child is not None:
        child = run.active_child
        if child.kind == "explicit_call" and child.site != run.entered_node.state_id:
            raise RutterStateError(
                "active explicit Call child does not match the parent entered state"
            )
        run = child.run
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
    if isinstance(state, Done):
        done = HistoryView(leaf.run.history, reckoning.completed_runs).done()
        if done is not None and (
            done.node_entry_id == leaf.run.entered_node.entry_id
            and done.state_id == leaf.run.entered_node.state_id
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


def _call_child_definition(
    voyage: _BoundVoyageLike,
    call: Call,
) -> _BoundDefinitionLike:
    identity = (
        getattr(call.child, "rutter_id", None),
        getattr(call.child, "definition_version", None),
    )
    try:
        return voyage._definitions[identity]
    except KeyError as exc:
        raise RutterStateError("Call child definition is unavailable") from exc


def _case_child_definition(
    voyage: _BoundVoyageLike,
    maker: _CaseMakerLike,
) -> _BoundDefinitionLike:
    identity = (
        getattr(maker.child, "rutter_id", None),
        getattr(maker.child, "definition_version", None),
    )
    try:
        return voyage._definitions[identity]
    except KeyError as exc:
        raise RutterStateError("CaseMaker child definition is unavailable") from exc


def _push_call(
    reckoning: Reckoning,
    run_id: str,
    call: Call,
    child_definition: _BoundDefinitionLike,
) -> Reckoning:
    leaf = _active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may push a Call child")
    if leaf.depth + 2 > _MAX_ACTIVE_DEPTH:
        raise RutterStateError("maximum active-child depth reached")
    try:
        child_charter = Charter(
            call.charter(_state_context(leaf.run, reckoning))
        )
    except Exception as exc:
        raise _EngineFault("child-charter") from exc
    child_run = ActiveRun(
        _new_id("run"),
        child_definition.rutter_id,
        child_definition.definition_version,
        child_charter,
        EnteredNode(_new_id("entry"), child_definition.start_state),
        (),
        None,
    )
    child_state = child_definition.states[child_definition.start_state]
    if isinstance(child_state, Prompt):
        try:
            turn = _render_prompt(reckoning, child_run, child_state)
        except Exception as exc:
            raise _EngineFault("child-materialization") from exc
        child_run = replace(child_run, history=(turn,))
    parent = replace(
        leaf.run,
        active_child=ActiveChild(
            _new_id("call"),
            "explicit_call",
            leaf.run.entered_node.state_id,
            None,
            child_run,
        ),
    )
    pushed = _replace_run(reckoning, run_id, parent)
    if isinstance(child_state, Action) and child_state.mode != "pure":
        child_leaf = _active_leaf(pushed)
        pushed = replace(
            pushed,
            active_effect=_planned_effect(child_leaf.run, child_state),
        )
    return pushed


def _push_case(
    reckoning: Reckoning,
    run_id: str,
    maker: _CaseMakerLike,
    child_definition: _BoundDefinitionLike,
    child_charter: Charter,
    edge: Edge,
) -> Reckoning:
    leaf = _active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may push an attached child")
    if leaf.depth + 2 > _MAX_ACTIVE_DEPTH:
        raise RutterStateError("maximum active-child depth reached")
    child_run = ActiveRun(
        _new_id("run"),
        child_definition.rutter_id,
        child_definition.definition_version,
        child_charter,
        EnteredNode(_new_id("entry"), child_definition.start_state),
        (),
        None,
    )
    child_state = child_definition.states[child_definition.start_state]
    if isinstance(child_state, Prompt):
        try:
            turn = _render_prompt(reckoning, child_run, child_state)
        except Exception as exc:
            raise _EngineFault("child-materialization") from exc
        child_run = replace(child_run, history=(turn,))
    parent = replace(
        leaf.run,
        active_child=ActiveChild(
            _new_id("call"),
            "attached_case",
            maker.id,
            edge.edge_id,
            child_run,
        ),
    )
    pushed = _replace_run(reckoning, run_id, parent)
    if isinstance(child_state, Action) and child_state.mode != "pure":
        child_leaf = _active_leaf(pushed)
        pushed = replace(
            pushed,
            active_effect=_planned_effect(child_leaf.run, child_state),
        )
    return pushed


def _parent_of_run(root: ActiveRun, run_id: str) -> tuple[ActiveRun, ActiveChild]:
    parent = root
    while parent.active_child is not None:
        child = parent.active_child
        if child.run.run_id == run_id:
            return parent, child
        parent = child.run
    raise RutterStateError("active child has no parent run")


def _return_call(reckoning: Reckoning, run_id: str) -> Reckoning:
    leaf = _active_leaf(reckoning)
    if leaf.run.run_id != run_id or leaf.run.active_child is not None:
        raise RutterStateError("only the completed active leaf may return")
    parent, child = _parent_of_run(reckoning.root, run_id)
    completed = CompletedRun(
        leaf.run.run_id,
        leaf.run.rutter_id,
        leaf.run.definition_version,
        leaf.run.charter,
        leaf.run.history,
    )
    record = CallRecord(
        child.call_id,
        parent.entered_node.entry_id,
        child.kind,
        child.site,
        child.attached_to_edge_id,
        completed.run_id,
    )
    returned_parent = replace(
        parent,
        history=parent.history + (record,),
        active_child=None,
    )
    returned = _replace_run(reckoning, parent.run_id, returned_parent)
    completed_runs = dict(returned.completed_runs)
    completed_runs[completed.run_id] = completed
    return replace(
        returned,
        global_revision=returned.global_revision + 1,
        completed_runs=completed_runs,
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


def _accept_action(
    reckoning: Reckoning,
    action: Action,
    action_id: str,
    result: ActionResult,
) -> Reckoning:
    leaf = _active_leaf(reckoning)
    record = ActionRecord(
        _new_id("record"),
        action_id,
        leaf.run.entered_node.entry_id,
        leaf.run.entered_node.state_id,
        action.mode,
        result,
    )
    accepted_run = replace(leaf.run, history=leaf.run.history + (record,))
    return replace(
        _replace_run(reckoning, leaf.run.run_id, accepted_run),
        global_revision=reckoning.global_revision + 1,
        active_effect=None,
    )


def _source_record(run: ActiveRun, entered_node: EnteredNode) -> HistoryEntry | None:
    for record in reversed(run.history):
        if (
            record.node_entry_id == entered_node.entry_id
            and not (
                isinstance(record, CallRecord)
                and record.site_kind == "attached_case"
            )
        ):
            return record
    return None


def _is_recorded_source(state: State, record: HistoryEntry | None) -> bool:
    return (
        isinstance(state, Prompt)
        and isinstance(record, Turn)
        and record.response is not None
    ) or (
        isinstance(state, Action) and isinstance(record, ActionRecord)
    ) or (
        isinstance(state, Call)
        and isinstance(record, CallRecord)
        and record.site_kind == "explicit_call"
    ) or (
        isinstance(state, Done) and isinstance(record, DoneRecord)
    )


def _select_edge(
    bound_run: BoundRun,
    strict_prefix: HistoryView,
    record: HistoryEntry,
    *,
    call_result: RunResult | None = None,
) -> Edge:
    state_id = bound_run.run.entered_node.state_id
    state = bound_run.definition.states[state_id]
    if isinstance(state, Prompt) and isinstance(record, Turn):
        response = record.response
        if response is None:
            raise _EngineFault("routing")
        outcome = response.outcome
    elif isinstance(state, Action) and isinstance(record, ActionRecord):
        response = None
        outcome = record.result.outcome
    elif (
        isinstance(state, Call)
        and isinstance(record, CallRecord)
        and call_result is not None
    ):
        response = None
        outcome = call_result.outcome
    elif isinstance(state, Done) and isinstance(record, DoneRecord):
        return Edge(
            record.record_id,
            bound_run.run.entered_node.entry_id,
            state_id,
            record.result.outcome,
            None,
        )
    else:
        raise _EngineFault("routing")
    routing = state.then
    target: object
    if type(routing) is str:
        target = routing
    elif isinstance(routing, Mapping):
        target = routing.get(outcome)
    else:
        state_context = StateContext(
            bound_run.run.charter,
            state_id,
            bound_run.run.entered_node.entry_id,
            strict_prefix,
        )
        try:
            if isinstance(state, Prompt):
                assert isinstance(record, Turn) and response is not None
                target = routing(  # type: ignore[operator]
                    AnswerContext(state_context, record.message, response)
                )
            elif isinstance(state, Action):
                assert isinstance(record, ActionRecord)
                target = routing(  # type: ignore[operator]
                    ActionContext(state_context, record.action_id),
                    record.result,
                )
            else:
                target = routing(state_context, call_result)  # type: ignore[operator]
        except Exception as exc:
            raise _EngineFault("routing") from exc
    if type(target) is not str or target not in bound_run.definition.states:
        raise _EngineFault(
            "routing",
            target=target if type(target) is str else None,
        )
    return Edge(
        record.call_id if isinstance(record, CallRecord) else record.record_id,
        bound_run.run.entered_node.entry_id,
        state_id,
        outcome,
        target,
    )


def _edge_context(
    run: ActiveRun,
    strict_prefix: HistoryView,
    edge: Edge,
    record: HistoryEntry,
) -> EdgeContext:
    return EdgeContext(
        StateContext(
            run.charter,
            run.entered_node.state_id,
            run.entered_node.entry_id,
            strict_prefix,
        ),
        {
            "edge_id": edge.edge_id,
            "source_entry_id": edge.source_entry_id,
            "source": edge.source,
            "outcome": edge.outcome,
            "target": edge.target,
        },
        record,
    )


def _selected_cases(
    definition: _BoundDefinitionLike,
    context: EdgeContext,
    edge: Edge,
) -> tuple[tuple[_CaseMakerLike, Charter], ...]:
    selected: list[tuple[_CaseMakerLike, Charter]] = []
    for authored in definition.case_makers:
        maker = cast(_CaseMakerLike, authored)
        try:
            matches = maker.on.matches(edge)
        except Exception as exc:
            raise _EngineFault(
                "case-matcher", case_maker_ids=(maker.id,)
            ) from exc
        if not matches:
            continue
        try:
            charter = maker.charter(context)
            if charter is not None:
                selected.append((maker, Charter(charter)))
        except Exception as exc:
            raise _EngineFault(
                "case-charter", case_maker_ids=(maker.id,)
            ) from exc
    return tuple(selected)


def _continue_edge(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    edge: Edge,
    strict_prefix: HistoryView,
    record: HistoryEntry,
) -> Reckoning:
    context = _edge_context(leaf.run, strict_prefix, edge, record)
    selected = _selected_cases(definition, context, edge)
    if len(selected) > 1 and not definition.allow_multiple_cases_at_once:
        raise _EngineFault(
            "case-cardinality",
            case_maker_ids=tuple(maker.id for maker, _ in selected),
        )
    completed = {
        (entry.site_id, entry.attached_to_edge_id)
        for entry in leaf.run.history
        if isinstance(entry, CallRecord) and entry.site_kind == "attached_case"
    }
    for maker, charter in selected:
        if (maker.id, edge.edge_id) in completed:
            continue
        return _push_case(
            reckoning,
            leaf.run.run_id,
            maker,
            _case_child_definition(voyage, maker),
            charter,
            edge,
        )
    if edge.target is None:
        return reckoning
    return _enter_node(
        reckoning,
        leaf.run.run_id,
        edge.target,
        definition=definition,
    )


def _recorded_edge(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    record: HistoryEntry,
) -> tuple[Edge, HistoryView]:
    call_result: RunResult | None = None
    if isinstance(record, CallRecord):
        try:
            call_result = reckoning.completed_runs[record.completed_run_id].result
        except KeyError as exc:
            raise RutterStateError("CallRecord completed run is unavailable") from exc
    history = HistoryView(leaf.run.history, reckoning.completed_runs)
    strict_prefix = history.strict_prefix(record)
    return (
        _select_edge(
            BoundRun(leaf.run, definition),
            strict_prefix,
            record,
            call_result=call_result,
        ),
        strict_prefix,
    )


def _continue_recorded_edge(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    record: HistoryEntry,
) -> Reckoning:
    edge, strict_prefix = _recorded_edge(reckoning, leaf, definition, record)
    return _continue_edge(
        voyage,
        reckoning,
        leaf,
        definition,
        edge,
        strict_prefix,
        record,
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
    elif isinstance(target_state, Action) and target_state.mode != "pure":
        entered_leaf = _active_leaf(entered)
        entered = replace(
            entered,
            active_effect=_planned_effect(entered_leaf.run, target_state),
        )
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
    case_maker_ids: tuple[str, ...] = (),
) -> Reckoning:
    fault: dict[str, object] = {
        "category": category,
        "run_id": leaf.run.run_id,
        "state_id": leaf.run.entered_node.state_id,
        "node_entry_id": leaf.run.entered_node.entry_id,
    }
    if target is not None:
        fault["target_state_id"] = target
    if case_maker_ids:
        fault["case_maker_ids"] = case_maker_ids
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


def _validate_action_result(value: object) -> ValidationReport:
    if isinstance(value, ActionResult):
        return ValidationReport(True, ())
    if not isinstance(value, Mapping) or set(value) != {"outcome", "value"}:
        return _invalid(
            "invalid-envelope",
            (),
            "action result must contain exactly outcome and value",
        )
    try:
        ActionResult.from_json(value)
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


def _action_id(run: ActiveRun) -> str:
    return f"action-{run.entered_node.entry_id}"


def _planned_effect(run: ActiveRun, action: Action) -> dict[str, object]:
    return {
        "action_id": _action_id(run),
        "owner_run_id": run.run_id,
        "node_entry_id": run.entered_node.entry_id,
        "state_id": run.entered_node.state_id,
        "mode": action.mode,
        "disposition": "planned",
        "result": None,
    }


def _action_effect(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    action: Action,
) -> Mapping[str, object]:
    effect = reckoning.active_effect
    if effect is None:
        raise RutterStateError("effectful Action has no recovery authority")
    if (
        effect["owner_run_id"] != leaf.run.run_id
        or effect["node_entry_id"] != leaf.run.entered_node.entry_id
        or effect["state_id"] != leaf.run.entered_node.state_id
        or effect["mode"] != action.mode
    ):
        raise RutterStateError("active effect recovery does not match the Action")
    return effect


def _validate_action_authority(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    state: State,
) -> None:
    if reckoning.active_effect is not None:
        if not isinstance(state, Action) or state.mode == "pure":
            raise RutterStateError(
                "active effect recovery does not match the Action"
            )
        _action_effect(reckoning, leaf, state)
        return
    if isinstance(state, Action) and state.mode != "pure":
        record = _source_record(leaf.run, leaf.run.entered_node)
        if not isinstance(record, ActionRecord):
            raise RutterStateError("effectful Action has no recovery authority")


def _pure_action_instruction(
    reckoning: Reckoning,
    run: ActiveRun,
    action: Action,
) -> PythonInstruction:
    action_id = _action_id(run)
    context = ActionContext(_state_context(run, reckoning), action_id)

    def execute() -> ActionResult:
        return action.run(context)

    return PythonInstruction(
        action_id,
        action.mode,
        execute,
        _ACTION_RESULT_FORMAT,
    )


def _effectful_action_instruction(
    voyage: _BoundVoyageLike,
    action_id: str,
    mode: str,
) -> PythonInstruction:
    def execute() -> ActionResult:
        with voyage._store.transaction() as reckoning:
            voyage._reckoning = reckoning
            leaf = _active_leaf(reckoning)
            definition = _leaf_definition(voyage, leaf)
            state = definition.states[leaf.run.entered_node.state_id]
            if not isinstance(state, Action) or state.mode == "pure":
                raise RutterStateError("Action instruction is stale")
            if _condition(reckoning, state) in {"fault", "uncertain"}:
                raise RunBlocked("the voyage is blocked")
            effect = _action_effect(reckoning, leaf, state)
            if effect["action_id"] != action_id:
                raise RutterStateError("Action instruction is stale")
            try:
                _, result = _run_effectful_action(
                    voyage,
                    reckoning,
                    leaf,
                    state,
                    effect,
                )
            except _EngineFault as fault:
                current = voyage._reckoning
                _fault_and_publish(voyage, current, current, fault)
                raise RunBlocked("Action execution failed") from None
            return result

    return PythonInstruction(
        action_id,
        mode,
        execute,
        _ACTION_RESULT_FORMAT,
    )


def _run_effectful_action(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    action: Action,
    effect: Mapping[str, object],
) -> tuple[Reckoning, ActionResult]:
    if effect["disposition"] == "completed":
        return reckoning, ActionResult.from_json(effect["result"])
    if effect["disposition"] != "planned":
        raise RunBlocked("the voyage is blocked")
    action_id = str(effect["action_id"])
    if action.mode == "non-repeat-safe":
        uncertain_effect = dict(effect)
        uncertain_effect["disposition"] = "uncertain"
        uncertain = replace(reckoning, active_effect=uncertain_effect)
        _publish(voyage, reckoning, uncertain)
        reckoning = uncertain
        effect = uncertain_effect
    try:
        result = action.run(
            ActionContext(_state_context(leaf.run, reckoning), action_id)
        )
    except Exception:
        raise _EngineFault("action-execution") from None
    if not isinstance(result, ActionResult):
        raise _EngineFault("action-result")
    completed_effect = dict(effect)
    completed_effect["disposition"] = "completed"
    completed_effect["result"] = result.to_json()
    completed = replace(reckoning, active_effect=completed_effect)
    _publish(voyage, reckoning, completed)
    return completed, result


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
    elif isinstance(state, Action) and state.mode != "pure":
        reckoning = replace(reckoning, active_effect=_planned_effect(run, state))
    return reckoning


def _get_instruction(voyage: _BoundVoyageLike) -> object | None:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        _validate_action_authority(reckoning, leaf, state)
        if _condition(reckoning, state) != "ready":
            return None
        source = _source_record(leaf.run, leaf.run.entered_node)
        if _is_recorded_source(state, source):
            return None
        if isinstance(state, Prompt):
            return _prompt_turn(reckoning, leaf.run).message
        if isinstance(state, Action) and state.mode == "pure":
            return _pure_action_instruction(reckoning, leaf.run, state)
        if isinstance(state, Action):
            effect = _action_effect(reckoning, leaf, state)
            return _effectful_action_instruction(
                voyage,
                str(effect["action_id"]),
                state.mode,
            )
        return None


def _validate(voyage: _BoundVoyageLike, response: object) -> ValidationReport:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        _validate_action_authority(reckoning, leaf, state)
        condition = _condition(reckoning, state)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        source = _source_record(leaf.run, leaf.run.entered_node)
        if _is_recorded_source(state, source):
            raise NotApplicable("an accepted node does not accept another response")
        if isinstance(state, Action):
            return _validate_action_result(response)
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
    previous: Reckoning,
    fault_base: Reckoning,
    fault: _EngineFault,
) -> NodeView:
    anchored = replace(
        fault_base,
        global_revision=previous.global_revision,
    )
    leaf = _active_leaf(anchored)
    faulted = _fault_reckoning(
        anchored,
        leaf,
        fault.category,
        target=fault.target,
        case_maker_ids=fault.case_maker_ids,
    )
    _publish(voyage, previous, faulted)
    state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
    return _node_view(faulted, leaf, state)


def _advance_call(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    state: Call,
) -> Reckoning:
    record = _source_record(leaf.run, leaf.run.entered_node)
    if isinstance(record, CallRecord):
        return _continue_recorded_edge(
            voyage,
            reckoning,
            leaf,
            definition,
            record,
        )
    child_definition = _call_child_definition(voyage, state)
    return _push_call(
        reckoning,
        leaf.run.run_id,
        state,
        child_definition,
    )


def _call_edge(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    state: Call,
) -> Edge | None:
    record = _source_record(leaf.run, leaf.run.entered_node)
    if not isinstance(record, CallRecord):
        return None
    edge, _ = _recorded_edge(reckoning, leaf, definition, record)
    return edge


def _preview_action(
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    action: Action,
    response: object,
) -> NodeView:
    if action.mode == "pure":
        if _is_missing(response):
            raise PreviewUnavailable("Action result is not supplied")
        action_id = _action_id(leaf.run)
    else:
        effect = _action_effect(reckoning, leaf, action)
        if effect["disposition"] != "completed":
            raise PreviewUnavailable("Action result is not yet available")
        action_id = str(effect["action_id"])
        authority = ActionResult.from_json(effect["result"])
        if _is_missing(response):
            response = authority
    report = _validate_action_result(response)
    if not report.valid:
        raise RutterValidationError("Action result was rejected")
    result = (
        response
        if isinstance(response, ActionResult)
        else ActionResult.from_json(response)
    )
    if action.mode != "pure" and result != authority:
        raise RutterValidationError(
            "Action result does not match completed recovery"
        )
    record = ActionRecord(
        f"preview-{action_id}",
        action_id,
        leaf.run.entered_node.entry_id,
        leaf.run.entered_node.state_id,
        action.mode,
        result,
    )
    preview_run = replace(leaf.run, history=leaf.run.history + (record,))
    history = HistoryView(preview_run.history, reckoning.completed_runs)
    try:
        edge = _select_edge(
            BoundRun(preview_run, definition),
            history.strict_prefix(record),
            record,
        )
    except _EngineFault as fault:
        raise RutterValidationError("Action routing failed") from fault
    assert edge.target is not None
    return NodeView(
        leaf.run.rutter_id,
        leaf.run.definition_version,
        edge.target,
        None,
        leaf.depth,
        "preview",
    )


def _advance_action(
    voyage: _BoundVoyageLike,
    reckoning: Reckoning,
    leaf: ActiveLeaf,
    definition: _BoundDefinitionLike,
    action: Action,
    response: object,
    *,
    dry_run: bool,
) -> tuple[Reckoning, NodeView | None]:
    if dry_run:
        return reckoning, _preview_action(
            reckoning,
            leaf,
            definition,
            action,
            response,
        )
    omitted = _is_missing(response)
    if action.mode == "pure":
        action_id = _action_id(leaf.run)
        if omitted:
            try:
                response = _pure_action_instruction(
                    reckoning,
                    leaf.run,
                    action,
                ).run()
            except Exception:
                fault = _EngineFault("action-execution")
                view = _fault_and_publish(
                    voyage,
                    reckoning,
                    reckoning,
                    fault,
                )
                return voyage._reckoning, view
            if not isinstance(response, ActionResult):
                fault = _EngineFault("action-result")
                view = _fault_and_publish(
                    voyage,
                    reckoning,
                    reckoning,
                    fault,
                )
                return voyage._reckoning, view
    else:
        effect = _action_effect(reckoning, leaf, action)
        action_id = str(effect["action_id"])
        if omitted:
            try:
                reckoning, response = _run_effectful_action(
                    voyage,
                    reckoning,
                    leaf,
                    action,
                    effect,
                )
            except _EngineFault as fault:
                current = voyage._reckoning
                view = _fault_and_publish(
                    voyage,
                    current,
                    current,
                    fault,
                )
                return voyage._reckoning, view
            effect = _action_effect(reckoning, leaf, action)
        if effect["disposition"] != "completed":
            raise RutterValidationError("completed Action recovery is required")
    report = _validate_action_result(response)
    if not report.valid:
        raise RutterValidationError("Action result was rejected")
    normalized = (
        response
        if isinstance(response, ActionResult)
        else ActionResult.from_json(response)
    )
    if action.mode != "pure":
        authority = ActionResult.from_json(effect["result"])
        if normalized != authority:
            raise RutterValidationError(
                "Action result does not match completed recovery"
            )
        normalized = authority
    accepted = _accept_action(reckoning, action, action_id, normalized)
    accepted_leaf = _active_leaf(accepted)
    record = _source_record(accepted_leaf.run, accepted_leaf.run.entered_node)
    assert isinstance(record, ActionRecord)
    history = HistoryView(accepted_leaf.run.history, accepted.completed_runs)
    try:
        edge = _select_edge(
            BoundRun(accepted_leaf.run, definition),
            history.strict_prefix(record),
            record,
        )
    except _EngineFault as fault:
        view = _fault_and_publish(voyage, reckoning, accepted, fault)
        return voyage._reckoning, view
    assert edge.target is not None
    try:
        entered = _continue_edge(
            voyage,
            accepted,
            accepted_leaf,
            definition,
            edge,
            history.strict_prefix(record),
            record,
        )
    except _EngineFault as fault:
        view = _fault_and_publish(voyage, reckoning, accepted, fault)
        return voyage._reckoning, view
    _publish(voyage, reckoning, entered)
    return entered, None


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
        _validate_action_authority(reckoning, leaf, state)
        condition = _condition(reckoning, state)
        if condition in {"fault", "uncertain"}:
            raise RunBlocked("the voyage is blocked")
        if condition == "terminal":
            if not _is_missing(response):
                raise NotApplicable("a terminal voyage does not accept a response")
            if dry_run:
                return _node_view(reckoning, leaf, state)

        source = _source_record(leaf.run, leaf.run.entered_node)
        recorded = _is_recorded_source(state, source)
        if recorded:
            if not _is_missing(response):
                raise NotApplicable("an accepted node does not accept another response")
            if dry_run:
                assert source is not None
                try:
                    edge, _ = _recorded_edge(reckoning, leaf, definition, source)
                except _EngineFault as fault:
                    raise RutterValidationError("routing failed") from fault
                if edge.target is None:
                    return _node_view(reckoning, leaf, state)
                return NodeView(
                    leaf.run.rutter_id,
                    leaf.run.definition_version,
                    edge.target,
                    None,
                    leaf.depth,
                    "preview",
                )
        elif isinstance(state, Prompt):
            if _is_missing(response):
                raise RutterValidationError("Prompt response is required")
            try:
                report = _validate_prompt(reckoning, leaf.run, state, response)
            except _EngineFault as fault:
                if dry_run:
                    raise RutterValidationError("Prompt validation failed") from fault
                return _fault_and_publish(voyage, reckoning, reckoning, fault)
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
                return _fault_and_publish(voyage, reckoning, accepted, fault)
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

            try:
                entered = _continue_edge(
                    voyage,
                    accepted,
                    accepted_leaf,
                    definition,
                    edge,
                    history.strict_prefix(record),
                    record,
                )
            except _EngineFault as fault:
                return _fault_and_publish(voyage, reckoning, accepted, fault)
            _publish(voyage, reckoning, entered)
            reckoning = entered
            if not continue_:
                entered_leaf = _active_leaf(reckoning)
                entered_definition = _leaf_definition(voyage, entered_leaf)
                entered_state = entered_definition.states[
                    entered_leaf.run.entered_node.state_id
                ]
                return _node_view(reckoning, entered_leaf, entered_state)
        elif isinstance(state, Call):
            if not _is_missing(response):
                raise NotApplicable("Call does not accept a response")
        elif isinstance(state, Action):
            reckoning, stopped = _advance_action(
                voyage,
                reckoning,
                leaf,
                definition,
                state,
                response,
                dry_run=dry_run,
            )
            if stopped is not None:
                return stopped
            if not continue_:
                entered_leaf = _active_leaf(reckoning)
                entered_definition = _leaf_definition(voyage, entered_leaf)
                entered_state = entered_definition.states[
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
            _validate_action_authority(reckoning, leaf, state)
            source = _source_record(leaf.run, leaf.run.entered_node)
            if _is_recorded_source(state, source):
                assert source is not None
                try:
                    advanced = _continue_recorded_edge(
                        voyage,
                        reckoning,
                        leaf,
                        definition,
                        source,
                    )
                except _EngineFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                if advanced is not reckoning:
                    _publish(voyage, reckoning, advanced)
                    reckoning = advanced
                    if not continue_:
                        advanced_leaf = _active_leaf(reckoning)
                        advanced_state = _leaf_definition(
                            voyage, advanced_leaf
                        ).states[advanced_leaf.run.entered_node.state_id]
                        return _node_view(reckoning, advanced_leaf, advanced_state)
                    continue
                if not isinstance(state, Done):
                    raise RutterStateError("recorded edge did not advance")
                if leaf.depth == 0:
                    return _node_view(reckoning, leaf, state)
                returned = _return_call(reckoning, leaf.run.run_id)
                _publish(voyage, reckoning, returned)
                reckoning = returned
                continue
            if isinstance(state, Prompt):
                return _node_view(reckoning, leaf, state)
            if isinstance(state, Action):
                reckoning, stopped = _advance_action(
                    voyage,
                    reckoning,
                    leaf,
                    definition,
                    state,
                    _MISSING,
                    dry_run=False,
                )
                if stopped is not None:
                    return stopped
                if not continue_:
                    advanced_leaf = _active_leaf(reckoning)
                    advanced_definition = _leaf_definition(voyage, advanced_leaf)
                    advanced_state = advanced_definition.states[
                        advanced_leaf.run.entered_node.state_id
                    ]
                    return _node_view(reckoning, advanced_leaf, advanced_state)
                continue
            if isinstance(state, Call):
                if dry_run:
                    try:
                        edge = _call_edge(reckoning, leaf, definition, state)
                    except _EngineFault as fault:
                        raise RutterValidationError("Call routing failed") from fault
                    if edge is None:
                        raise PreviewUnavailable("Call result is not yet available")
                    assert edge.target is not None
                    return NodeView(
                        leaf.run.rutter_id,
                        leaf.run.definition_version,
                        edge.target,
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
                        state,
                    )
                except _EngineFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                _publish(voyage, reckoning, advanced)
                reckoning = advanced
                if not continue_:
                    advanced_leaf = _active_leaf(reckoning)
                    advanced_state = _leaf_definition(
                        voyage, advanced_leaf
                    ).states[advanced_leaf.run.entered_node.state_id]
                    return _node_view(reckoning, advanced_leaf, advanced_state)
                continue
            if not isinstance(state, Done):
                return _node_view(reckoning, leaf, state)
            try:
                result = _project_done(reckoning, leaf.run, state)
            except _EngineFault as fault:
                if dry_run:
                    raise RutterValidationError("Done projection failed") from fault
                return _fault_and_publish(voyage, reckoning, reckoning, fault)
            if dry_run:
                return _node_view(reckoning, leaf, state, preview=True)
            settled = _settle_done(reckoning, leaf.run.run_id, result)
            _publish(voyage, reckoning, settled)
            reckoning = settled
            settled_leaf = _active_leaf(reckoning)
            if not continue_:
                return _node_view(reckoning, settled_leaf, state)
            if settled_leaf.depth == 0:
                source = _source_record(
                    settled_leaf.run, settled_leaf.run.entered_node
                )
                assert isinstance(source, DoneRecord)
                try:
                    advanced = _continue_recorded_edge(
                        voyage,
                        reckoning,
                        settled_leaf,
                        definition,
                        source,
                    )
                except _EngineFault as fault:
                    return _fault_and_publish(voyage, reckoning, reckoning, fault)
                if advanced is reckoning:
                    return _node_view(reckoning, settled_leaf, state)
                _publish(voyage, reckoning, advanced)
                reckoning = advanced
        raise RutterStateError("automatic continuation limit exhausted")


def _get_current_node(voyage: _BoundVoyageLike) -> NodeView:
    with voyage._store.transaction() as reckoning:
        voyage._reckoning = reckoning
        leaf = _active_leaf(reckoning)
        state = _leaf_definition(voyage, leaf).states[leaf.run.entered_node.state_id]
        _validate_action_authority(reckoning, leaf, state)
        return _node_view(reckoning, leaf, state)
