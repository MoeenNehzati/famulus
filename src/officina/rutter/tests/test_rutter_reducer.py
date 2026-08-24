"""Exercise pure, definition-independent Reckoning reduction seams."""

from __future__ import annotations

from officina.rutter.history import (
    ActiveChild,
    ActiveRun,
    EnteredEvolution,
    Reckoning,
    SubRutterRecord,
    TerminalRecord,
)
from officina.rutter.reducer import (
    deepest_active_leaf,
    enter_child,
    enter_evolution,
    replace_active_run,
    return_active_child,
)
from officina.rutter.values import Charter, VoyageResult


def _run(
    run_id: str,
    evolution_id: str,
    *,
    history: tuple[object, ...] = (),
    child: ActiveChild | None = None,
) -> ActiveRun:
    return ActiveRun(
        run_id,
        "example",
        1,
        Charter({}),
        EnteredEvolution(f"entry-{run_id}", evolution_id),
        history,
        child,
    )


def test_leaf_selection_and_replacement_are_pure() -> None:
    child = _run("child", "work")
    root = _run(
        "root",
        "delegate",
        child=ActiveChild("invoke", "explicit_call", "delegate", None, child),
    )
    original = Reckoning(3, 0, root, {}, None, None)
    replacement = _run("child", "next")

    changed = replace_active_run(original, replacement)

    assert deepest_active_leaf(original).run is child
    assert deepest_active_leaf(changed).run == replacement
    assert original.root.active_child is not None
    assert original.root.active_child.run is child


def test_child_entry_and_return_publish_one_valid_replacement() -> None:
    root = _run("root", "delegate")
    child = _run(
        "child",
        "done",
        history=(
            TerminalRecord(
                "terminal-child",
                "entry-child",
                "done",
                VoyageResult("complete", {}),
            ),
        ),
    )
    original = Reckoning(3, 0, root, {}, None, None)
    entered = enter_child(
        original,
        "root",
        ActiveChild("invoke", "explicit_call", "delegate", None, child),
    )

    returned = return_active_child(entered, "child")

    assert original.root.active_child is None
    assert returned.root.active_child is None
    assert returned.global_revision == 1
    assert returned.completed_runs["child"].result == VoyageResult("complete", {})
    assert isinstance(returned.root.history[-1], SubRutterRecord)
    assert returned.root.history[-1].completed_voyage_instance_id == "child"


def test_evolution_entry_returns_a_new_tree() -> None:
    original = Reckoning(3, 0, _run("root", "draft"), {}, None, None)
    entered = EnteredEvolution("entry-review", "review")

    changed = enter_evolution(original, "root", entered)

    assert changed.root.entered_evolution == entered
    assert original.root.entered_evolution.evolution_id == "draft"
