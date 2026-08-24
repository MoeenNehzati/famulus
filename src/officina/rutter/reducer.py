"""Pure, definition-independent Reckoning transformations."""

from __future__ import annotations

from dataclasses import dataclass, replace

from officina.rutter.history import (
    ActiveChild,
    ActiveRun,
    CompletedRun,
    EnteredEvolution,
    Reckoning,
    SubRutterRecord,
    _MAX_ACTIVE_DEPTH,
)
from officina.rutter.values import RutterStateError


@dataclass(frozen=True)
class ActiveLeaf:
    run: ActiveRun
    depth: int


def deepest_active_leaf(reckoning: Reckoning) -> ActiveLeaf:
    """Return the deepest active run without consulting definitions."""

    run = reckoning.root
    depth = 0
    while run.active_child is not None:
        child = run.active_child
        if (
            child.kind == "explicit_call"
            and child.site != run.entered_evolution.evolution_id
        ):
            raise RutterStateError(
                "active explicit SubRutter child does not match the parent "
                "entered evolution"
            )
        run = child.run
        depth += 1
    return ActiveLeaf(run, depth)


def _replace_in_tree(
    run: ActiveRun,
    run_id: str,
    replacement: ActiveRun,
) -> ActiveRun:
    if run.run_id == run_id:
        return replacement
    child = run.active_child
    if child is None:
        raise RutterStateError("active run is absent from the Reckoning")
    replaced_child = _replace_in_tree(child.run, run_id, replacement)
    return replace(run, active_child=replace(child, run=replaced_child))


def replace_active_run(
    reckoning: Reckoning,
    replacement: ActiveRun,
) -> Reckoning:
    """Replace one active run, selected by the replacement's identity."""

    return replace(
        reckoning,
        root=_replace_in_tree(
            reckoning.root,
            replacement.run_id,
            replacement,
        ),
    )


def enter_child(
    reckoning: Reckoning,
    parent_run_id: str,
    child: ActiveChild,
) -> Reckoning:
    """Attach one already constructed child to the active leaf."""

    leaf = _require_child_capacity(reckoning, parent_run_id)
    return replace_active_run(
        reckoning,
        replace(leaf.run, active_child=child),
    )


def _require_child_capacity(
    reckoning: Reckoning,
    parent_run_id: str,
) -> ActiveLeaf:
    """Validate child-entry capacity before any authored child work runs."""

    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != parent_run_id:
        raise RutterStateError("only the active leaf may enter a child")
    if leaf.run.active_child is not None:
        raise RutterStateError("active leaf already owns a child")
    if leaf.depth + 2 > _MAX_ACTIVE_DEPTH:
        raise RutterStateError("maximum active-child depth reached")
    return leaf


def return_active_child(
    reckoning: Reckoning,
    child_run_id: str,
) -> Reckoning:
    """Archive the terminal leaf and append its parent invocation record."""

    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != child_run_id or leaf.run.active_child is not None:
        raise RutterStateError("only the completed active leaf may return")

    parent = reckoning.root
    while parent.active_child is not None:
        child = parent.active_child
        if child.run.run_id == child_run_id:
            break
        parent = child.run
    else:
        raise RutterStateError("active child has no parent run")

    completed = CompletedRun(
        leaf.run.run_id,
        leaf.run.rutter_id,
        leaf.run.definition_version,
        leaf.run.charter,
        leaf.run.history,
    )
    record = SubRutterRecord(
        child.invocation_id,
        parent.entered_evolution.entry_id,
        child.site if child.kind == "explicit_call" else None,
        child.site if child.kind == "attached_case" else None,
        child.attached_to_transition_id,
        completed.run_id,
    )
    returned_parent = replace(
        parent,
        history=parent.history + (record,),
        active_child=None,
    )
    returned_root = _replace_in_tree(
        reckoning.root,
        returned_parent.run_id,
        returned_parent,
    )
    completed_runs = dict(reckoning.completed_runs)
    completed_runs[completed.run_id] = completed
    return replace(
        reckoning,
        root=returned_root,
        global_revision=reckoning.global_revision + 1,
        completed_runs=completed_runs,
    )


def enter_evolution(
    reckoning: Reckoning,
    run_id: str,
    entered: EnteredEvolution,
) -> Reckoning:
    """Replace the active leaf entrance, including frozen-transition resume."""

    leaf = deepest_active_leaf(reckoning)
    if leaf.run.run_id != run_id:
        raise RutterStateError("only the active leaf may enter an evolution")
    return replace_active_run(
        reckoning,
        replace(leaf.run, entered_evolution=entered),
    )


__all__ = (
    "ActiveLeaf",
    "deepest_active_leaf",
    "enter_child",
    "enter_evolution",
    "replace_active_run",
    "return_active_child",
)
