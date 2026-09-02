"""Behavioral tests for managed setup evaluation and teardown planning."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from officina.common import atomic_files
from officina.blueprints.graph import ManagedSetup
from officina.runtime.python_machine_interface import logical_python_package_name
from officina.runtime.python_machine_interface_runner import load_interface


SCRIPT_DIR = Path(__file__).resolve().parents[1]
LOGICAL_PACKAGE = logical_python_package_name("setup-interface-manager._rtx")
previous_cwd = Path.cwd()
try:
    os.chdir(SCRIPT_DIR)
    _status_interface = load_interface(
        "_setup_manager.py",
        "StatusInterface",
        logical_package=LOGICAL_PACKAGE,
        logical_entrypoint=f"{LOGICAL_PACKAGE}._setup_manager",
    )
finally:
    os.chdir(previous_cwd)
_manager_globals = _status_interface.__class__.run.__globals__
evaluation = SimpleNamespace(
    **_manager_globals["SetupStep"].from_managed.__func__.__globals__
)
state = SimpleNamespace(**_manager_globals["LedgerStore"].__init__.__globals__)


class AtomicFiles:
    """A real confined adapter so tests exercise LedgerStore's CAS boundary."""

    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
        atomic_files.ensure_private_directory(path.parent, allowed_root=allowed_root)

    def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
        return atomic_files.exclusive_file_lock(path, allowed_root=allowed_root, mode=mode)

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
        return atomic_files.read_regular_file_bytes(path, allowed_root=allowed_root)

    def atomic_compare_and_replace_bytes(self, path: Path, data: bytes, **kwargs: object) -> None:
        atomic_files.atomic_compare_and_replace_bytes(path, data, **kwargs)


def _store(tmp_path: Path) -> state.LedgerStore:
    return state.LedgerStore._from_atomic_files(
        tmp_path / "private" / "state" / "ledger.json", AtomicFiles()
    )


def _managed(setup: str) -> ManagedSetup:
    stem = setup.removesuffix(".interface.setup")
    return ManagedSetup(
        setup_interface=setup,
        setup_version=1,
        teardown_interface=f"{stem}.interface.teardown",
        teardown_version=1,
        setup_verifier_interface=f"{stem}.interface.setup-status",
        setup_verifier_version=1,
        teardown_verifier_interface=f"{stem}.interface.teardown-status",
        teardown_verifier_version=1,
        kind="python",
    )


def _graph(
    requirements: dict[str, tuple[tuple[str, int], ...]],
    *,
    parents: dict[str, str | None] | None = None,
    target_modules: dict[str, str] | None = None,
) -> SimpleNamespace:
    managed = {setup: _managed(setup) for setup in requirements}
    exports = {
        setup: SimpleNamespace(module_node_id=setup.removesuffix(".interface.setup"))
        for setup in requirements
    }
    for target, module in (target_modules or {}).items():
        exports[target] = SimpleNamespace(module_node_id=module)
    return SimpleNamespace(
        setup_requirements=requirements,
        managed_setups=managed,
        module_parents=parents or {module.module_node_id: None for module in exports.values()},
        exports=exports,
    )


def _flow(flow_id: str, operation: str, root: str, current: str) -> state.ActiveFlow:
    return state.ActiveFlow(
        flow_id=flow_id,
        operation=operation,  # type: ignore[arg-type]
        root=root,
        current_step=current,
        verified_steps=(),
        continuation=state.ContinuationIdentity("caller", "caller.interface.run", 1),
    )


def _receipt(*roots: str, version: int = 1) -> state.SetupReceipt:
    return state.SetupReceipt(version=version, required_by=frozenset(roots))


def _seed(store: state.LedgerStore, ledger: state.SetupLedger) -> None:
    store.update(lambda _previous: ledger)


def test_unmanaged_target_is_not_claimed_or_blocked_by_an_unrelated_flow(tmp_path: Path) -> None:
    """Catches treating every target as managed while another lifecycle action runs."""
    graph = _graph({}, target_modules={"plain.interface.run": "plain"})
    ledger = state.SetupLedger(
        interfaces={}, active_flow=_flow("busy", "setup", "other.interface.setup", "other.interface.setup")
    )

    result = evaluation.evaluate_target(graph, "plain.interface.run", ledger)

    assert result.code == "unmanaged"
    assert result.root_setup_interface is None
    assert result.pending_stack == ()


def test_child_target_resolves_its_managed_module_owner_and_returns_pop_stack(tmp_path: Path) -> None:
    """Catches bypassing a managed parent when a child interface is invoked."""
    graph = _graph(
        {
            "root.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        },
        parents={"root": None, "root.child": "root", "leaf": None},
        target_modules={"root.child.interface.run": "root.child"},
    )

    result = evaluation.evaluate_target(graph, "root.child.interface.run", state.SetupLedger.empty())

    assert result.code == "setup_required"
    assert result.root_setup_interface == "root.interface.setup"
    assert tuple(step.setup_interface for step in result.pending_stack) == (
        "root.interface.setup",
        "leaf.interface.setup",
    )


def test_first_stale_receipt_requires_the_dependent_suffix_only() -> None:
    """Catches accepting a current root receipt after an older prerequisite changed."""
    graph = _graph(
        {
            "root.interface.setup": (("parent.interface.setup", 1),),
            "parent.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )
    ledger = state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt(),
            "parent.interface.setup": _receipt(version=9),
            "root.interface.setup": _receipt(),
        },
        active_flow=None,
    )

    result = evaluation.evaluate_target(graph, "root.interface.setup", ledger)

    assert result.code == "setup_required"
    assert tuple(step.setup_interface for step in result.pending_stack) == (
        "root.interface.setup",
        "parent.interface.setup",
    )


def test_diamond_closure_deduplicates_the_shared_dependency() -> None:
    """Catches a duplicated setup action for a diamond's common prerequisite."""
    graph = _graph(
        {
            "root.interface.setup": (("left.interface.setup", 1), ("right.interface.setup", 1)),
            "left.interface.setup": (("leaf.interface.setup", 1),),
            "right.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )

    result = evaluation.evaluate_target(graph, "root.interface.setup", state.SetupLedger.empty())

    assert [step.setup_interface for step in reversed(result.pending_stack)] == [
        "leaf.interface.setup", "left.interface.setup", "right.interface.setup", "root.interface.setup"
    ]


def test_ready_authorization_claims_every_closure_receipt_atomically(tmp_path: Path) -> None:
    """Catches claiming only the root and losing shared-dependency provenance."""
    store = _store(tmp_path)
    graph = _graph(
        {
            "root.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )
    _seed(store, state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt(), "root.interface.setup": _receipt()},
        active_flow=None,
    ))

    result = evaluation.authorize_ready_root(store, graph, "root.interface.setup")

    assert result.code == "ready"
    assert result.resume_original is True
    assert store.read().interfaces == {
        "leaf.interface.setup": _receipt("root.interface.setup"),
        "root.interface.setup": _receipt("root.interface.setup"),
    }


def test_setup_settlement_requires_the_exact_active_step_and_advances(tmp_path: Path) -> None:
    """Catches out-of-order settlement recording a receipt for an unrun setup action."""
    store = _store(tmp_path)
    graph = _graph(
        {
            "root.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )
    _seed(store, state.begin_flow(state.SetupLedger.empty(), _flow(
        "flow", "setup", "root.interface.setup", "leaf.interface.setup"
    )))
    root_step = evaluation.SetupStep.from_managed(graph.managed_setups["root.interface.setup"])
    leaf_step = evaluation.SetupStep.from_managed(graph.managed_setups["leaf.interface.setup"])

    with pytest.raises(state.FlowConflict, match="current"):
        evaluation.record_setup_success(store, graph, "flow", root_step)

    result = evaluation.record_setup_success(store, graph, "flow", leaf_step)

    assert result.state == "run-step"
    assert result.current_step == evaluation.SetupStep.from_managed(graph.managed_setups["root.interface.setup"])
    assert store.read().interfaces["leaf.interface.setup"] == _receipt("root.interface.setup")


def test_terminal_setup_settlement_clears_the_flow(tmp_path: Path) -> None:
    """Catches leaving a completed setup flow busy after its last receipt."""
    store = _store(tmp_path)
    graph = _graph({"root.interface.setup": ()})
    _seed(store, state.begin_flow(state.SetupLedger.empty(), _flow(
        "flow", "setup", "root.interface.setup", "root.interface.setup"
    )))

    result = evaluation.record_setup_success(
        store, graph, "flow", evaluation.SetupStep.from_managed(graph.managed_setups["root.interface.setup"])
    )

    assert result.state == "ready"
    assert result.current_step is None
    assert store.read().active_flow is None


@pytest.mark.parametrize(
    ("changed_requirements", "changed_version"),
    [
        (
            {
                "root.interface.setup": (
                    ("leaf.interface.setup", 1),
                    ("middle.interface.setup", 1),
                ),
                "leaf.interface.setup": (),
                "middle.interface.setup": (),
            },
            None,
        ),
        (
            {"root.interface.setup": (), "leaf.interface.setup": ()},
            None,
        ),
        (
            {
                "root.interface.setup": (("middle.interface.setup", 1),),
                "leaf.interface.setup": (),
                "middle.interface.setup": (),
            },
            None,
        ),
        (
            {
                "root.interface.setup": (("leaf.interface.setup", 1),),
                "leaf.interface.setup": (),
            },
            2,
        ),
    ],
    ids=("new-dependency", "removed-dependency", "interface-drift", "version-drift"),
)
def test_setup_settlement_rejects_a_suspended_flow_when_its_closure_changes(
    tmp_path: Path,
    changed_requirements: dict[str, tuple[tuple[str, int], ...]],
    changed_version: int | None,
) -> None:
    """Catches recording a suspended flow after its closure no longer matches it."""
    store = _store(tmp_path)
    suspended = replace(
        _flow("flow", "setup", "root.interface.setup", "root.interface.setup"),
        verified_steps=("leaf.interface.setup",),
    )
    before = state.SetupLedger(
        interfaces={"leaf.interface.setup": _receipt("root.interface.setup")},
        active_flow=suspended,
    )
    _seed(store, before)
    changed = _graph(changed_requirements)
    if changed_version is not None:
        changed.managed_setups["leaf.interface.setup"] = replace(
            changed.managed_setups["leaf.interface.setup"], setup_version=changed_version
        )
    root_step = evaluation.SetupStep.from_managed(changed.managed_setups["root.interface.setup"])

    with pytest.raises(state.FlowConflict, match="graph"):
        evaluation.record_setup_success(store, changed, "flow", root_step)

    assert store.read() == before


def test_setup_settlement_rejects_a_reordered_suspended_flow_prefix(tmp_path: Path) -> None:
    """Catches accepting a current step whose verified prefix changed order."""
    store = _store(tmp_path)
    suspended = replace(
        _flow("flow", "setup", "root.interface.setup", "right.interface.setup"),
        verified_steps=("leaf.interface.setup", "left.interface.setup"),
    )
    before = state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt("root.interface.setup"),
            "left.interface.setup": _receipt("root.interface.setup"),
        },
        active_flow=suspended,
    )
    _seed(store, before)
    changed = _graph(
        {
            "root.interface.setup": (
                ("left.interface.setup", 1),
                ("right.interface.setup", 1),
            ),
            "left.interface.setup": (),
            "right.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )
    right_step = evaluation.SetupStep.from_managed(changed.managed_setups["right.interface.setup"])

    with pytest.raises(state.FlowConflict, match="graph"):
        evaluation.record_setup_success(store, changed, "flow", right_step)

    assert store.read() == before


def test_invalidation_removes_selected_and_managed_dependents_but_keeps_orphans(tmp_path: Path) -> None:
    """Catches invalidation retaining a dependent receipt or deleting unrelated state."""
    store = _store(tmp_path)
    graph = _graph(
        {
            "root.interface.setup": (("parent.interface.setup", 1),),
            "parent.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
            "independent.interface.setup": (),
        }
    )
    _seed(store, state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt("root.interface.setup"),
            "parent.interface.setup": _receipt("root.interface.setup"),
            "root.interface.setup": _receipt("root.interface.setup"),
            "independent.interface.setup": _receipt("independent.interface.setup"),
            "orphan.interface.setup": _receipt("other.root", version=7),
        },
        active_flow=None,
    ))

    removed = evaluation.invalidate(store, graph, "parent.interface.setup")

    assert removed == ("parent.interface.setup", "root.interface.setup")
    assert store.read().interfaces == {
        "leaf.interface.setup": _receipt("root.interface.setup"),
        "independent.interface.setup": _receipt("independent.interface.setup"),
        "orphan.interface.setup": _receipt("other.root", version=7),
    }


def test_invalidation_rejects_an_active_flow_without_changing_the_ledger(tmp_path: Path) -> None:
    """Catches invalidation racing an in-progress lifecycle mutation."""
    store = _store(tmp_path)
    graph = _graph({"root.interface.setup": (("leaf.interface.setup", 1),), "leaf.interface.setup": ()})
    before = state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt("root.interface.setup"),
            "root.interface.setup": _receipt("root.interface.setup"),
        },
        active_flow=_flow("flow", "setup", "root.interface.setup", "leaf.interface.setup"),
    )
    _seed(store, before)

    with pytest.raises(state.FlowConflict, match="recover"):
        evaluation.invalidate(store, graph, "leaf.interface.setup")

    assert store.read() == before


@pytest.mark.parametrize("first_root", ["left.interface.setup", "right.interface.setup"])
def test_shared_dependency_teardown_releases_first_claim_then_tears_down_last_claim(
    tmp_path: Path, first_root: str
) -> None:
    """Catches either shared-dependency history tearing down a still-required receipt."""
    store = _store(tmp_path)
    graph = _graph(
        {
            "left.interface.setup": (("leaf.interface.setup", 1),),
            "right.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        }
    )
    other_root = "right.interface.setup" if first_root == "left.interface.setup" else "left.interface.setup"
    _seed(store, state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt(first_root, other_root),
            first_root: _receipt(first_root),
            other_root: _receipt(other_root),
        },
        active_flow=None,
    ))

    first_plan = evaluation.teardown_plan(graph, first_root, store.read())
    assert [(step.setup_interface, step.action) for step in first_plan] == [
        (first_root, "run-teardown"),
        ("leaf.interface.setup", "release-claim"),
    ]
    _seed(store, state.begin_flow(store.read(), _flow(
        "first", "teardown", first_root, first_root
    )))
    first_result = evaluation.record_teardown_success(store, graph, "first", first_plan[0])
    evaluation.record_teardown_success(store, graph, "first", first_result.current_step)
    assert store.read().interfaces["leaf.interface.setup"] == _receipt(other_root)

    second_plan = evaluation.teardown_plan(graph, other_root, store.read())
    assert [(step.setup_interface, step.action) for step in second_plan] == [
        (other_root, "run-teardown"),
        ("leaf.interface.setup", "run-teardown"),
    ]
