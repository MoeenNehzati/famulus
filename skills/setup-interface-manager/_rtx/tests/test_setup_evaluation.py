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


def test_teardown_all_plan_is_deterministic_deduplicated_and_dependents_first() -> None:
    """Catches root order or diamond overlap changing global teardown order."""
    graph = _graph({
        "right.interface.setup": (("leaf.interface.setup", 1),),
        "left.interface.setup": (("leaf.interface.setup", 1),),
        "leaf.interface.setup": (),
    })
    ledger = state.SetupLedger(
        interfaces={name: _receipt("foreign.root") for name in reversed(graph.managed_setups)},
        active_flow=None,
    )

    assert [step.setup_interface for step in evaluation.teardown_all_plan(graph, ledger)] == [
        "right.interface.setup", "left.interface.setup", "leaf.interface.setup"
    ]
    assert evaluation.teardown_all_plan(graph, state.SetupLedger.empty()) == ()


@pytest.mark.parametrize("receipt", ["unknown.interface.setup", "root.interface.setup"])
def test_teardown_all_plan_rejects_unknown_or_stale_receipts(receipt: str) -> None:
    """Catches silently dropping inventory that cannot be safely dispatched."""
    graph = _graph({"root.interface.setup": ()})
    version = 1 if receipt.startswith("unknown") else 9
    ledger = state.SetupLedger(interfaces={receipt: _receipt(version=version)}, active_flow=None)

    with pytest.raises(evaluation.BlueprintGraphError, match="(managed setup|version)"):
        evaluation.teardown_all_plan(graph, ledger)
    assert ledger.interfaces == {receipt: _receipt(version=version)}


def test_teardown_all_plan_filters_missing_prerequisite_receipts() -> None:
    """Catches inventing teardown work for a prerequisite absent from the ledger."""
    graph = _graph({
        "root.interface.setup": (("leaf.interface.setup", 1),),
        "leaf.interface.setup": (),
    })
    ledger = state.SetupLedger(interfaces={"root.interface.setup": _receipt()}, active_flow=None)

    assert [step.setup_interface for step in evaluation.teardown_all_plan(graph, ledger)] == [
        "root.interface.setup"
    ]


def test_global_settlement_advances_then_can_remove_and_cancel(tmp_path: Path) -> None:
    """Catches global settlement retaining a receipt or adding flow history."""
    store = _store(tmp_path)
    graph = _graph({"root.interface.setup": (("middle.interface.setup", 1),),
                    "middle.interface.setup": (("leaf.interface.setup", 1),), "leaf.interface.setup": ()})
    ledger = state.SetupLedger(
        interfaces={name: _receipt() for name in graph.managed_setups},
        active_flow=state.ActiveFlow("all", "teardown-all", None, "root.interface.setup", (), None),
    )
    _seed(store, ledger)
    first, second, _later = plan = evaluation.teardown_all_plan(graph, ledger)
    assert [step.setup_interface for step in plan] == ["root.interface.setup", "middle.interface.setup", "leaf.interface.setup"]

    result = evaluation.record_teardown_all_success(store, graph, "all", first)
    assert result.current_step == second
    assert store.read().active_flow.verified_steps == ()
    result = evaluation.record_teardown_all_success(store, graph, "all", second, advance=False)
    assert result.state == "ready"
    assert set(store.read().interfaces) == {"leaf.interface.setup"}
    assert store.read().active_flow is None


def test_setup_step_with_optional_verifier_absent() -> None:
    """Catches SetupStep with no verifier when verifier becomes optional."""
    graph = _graph({"root.interface.setup": ()})
    managed = graph.managed_setups["root.interface.setup"]
    managed_without_verifier = replace(
        managed,
        setup_verifier_interface=None,
        setup_verifier_version=None,
    )
    graph.managed_setups = {"root.interface.setup": managed_without_verifier}

    step = evaluation.SetupStep.from_managed(managed_without_verifier)

    assert step.setup_interface == "root.interface.setup"
    assert step.setup_version == 1
    assert step.setup_verifier_interface is None
    assert step.setup_verifier_version is None
    assert step.kind == "python"


def test_no_teardown_managed_setup_plans_invalidation() -> None:
    """Catches planning invalidation for managed setups that have no teardown."""
    graph = _graph({"root.interface.setup": ()})
    managed = graph.managed_setups["root.interface.setup"]
    managed_no_teardown = replace(
        managed,
        teardown_interface=None,
        teardown_version=None,
        teardown_verifier_interface=None,
        teardown_verifier_version=None,
    )
    graph.managed_setups = {"root.interface.setup": managed_no_teardown}
    ledger = state.SetupLedger(
        interfaces={"root.interface.setup": _receipt("root.interface.setup")},
        active_flow=None,
    )

    plan = evaluation.teardown_plan(graph, "root.interface.setup", ledger)

    assert len(plan) == 1
    assert plan[0].setup_interface == "root.interface.setup"
    assert plan[0].action == "invalidate-receipt"
    assert plan[0].teardown_interface is None


def test_mixed_teardown_all_orders_real_teardown_and_invalidation_correctly() -> None:
    """Catches mixing real teardown and invalidation in teardown-all planning."""
    graph = _graph({
        "with_teardown.interface.setup": (),
        "without_teardown.interface.setup": (),
    })
    with_td = graph.managed_setups["with_teardown.interface.setup"]
    without_td = replace(
        graph.managed_setups["without_teardown.interface.setup"],
        teardown_interface=None,
        teardown_version=None,
        teardown_verifier_interface=None,
        teardown_verifier_version=None,
    )
    graph.managed_setups = {
        "with_teardown.interface.setup": with_td,
        "without_teardown.interface.setup": without_td,
    }
    ledger = state.SetupLedger(
        interfaces={
            "with_teardown.interface.setup": _receipt("foreign.root"),
            "without_teardown.interface.setup": _receipt("foreign.root"),
        },
        active_flow=None,
    )

    plan = evaluation.teardown_all_plan(graph, ledger)

    assert len(plan) == 2
    actions = {step.setup_interface: step.action for step in plan}
    assert actions["with_teardown.interface.setup"] == "run-teardown"
    assert actions["without_teardown.interface.setup"] == "invalidate-receipt"


def test_shared_no_teardown_receipt_releases_claim() -> None:
    """Catches planning invalidation for shared no-teardown setup."""
    graph = _graph({
        "left.interface.setup": (("leaf.interface.setup", 1),),
        "right.interface.setup": (("leaf.interface.setup", 1),),
        "leaf.interface.setup": (),
    })
    managed = graph.managed_setups["leaf.interface.setup"]
    managed_no_teardown = replace(
        managed,
        teardown_interface=None,
        teardown_version=None,
        teardown_verifier_interface=None,
        teardown_verifier_version=None,
    )
    graph.managed_setups["leaf.interface.setup"] = managed_no_teardown
    ledger = state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt("left.interface.setup", "right.interface.setup"),
            "left.interface.setup": _receipt("left.interface.setup"),
        },
        active_flow=None,
    )

    plan = evaluation.teardown_plan(graph, "left.interface.setup", ledger)

    assert len(plan) == 2
    assert plan[0].setup_interface == "left.interface.setup"
    assert plan[0].action == "run-teardown"
    assert plan[1].setup_interface == "leaf.interface.setup"
    assert plan[1].action == "release-claim"


def test_invalidation_settlement_removes_receipt_and_advances(tmp_path: Path) -> None:
    """Catches settlement of invalidation removing receipt without claiming teardown."""
    store = _store(tmp_path)
    graph = _graph({"root.interface.setup": (("leaf.interface.setup", 1),), "leaf.interface.setup": ()})
    managed = graph.managed_setups["leaf.interface.setup"]
    managed_no_teardown = replace(
        managed,
        teardown_interface=None,
        teardown_version=None,
        teardown_verifier_interface=None,
        teardown_verifier_version=None,
    )
    graph.managed_setups["leaf.interface.setup"] = managed_no_teardown
    _seed(store, state.begin_flow(state.SetupLedger(
        interfaces={
            "leaf.interface.setup": _receipt("root.interface.setup"),
            "root.interface.setup": _receipt("root.interface.setup"),
        },
        active_flow=None,
    ), _flow("flow", "teardown", "root.interface.setup", "root.interface.setup")))

    plan = evaluation.teardown_plan(graph, "root.interface.setup", store.read())
    assert plan[0].action == "run-teardown"
    assert plan[1].action == "invalidate-receipt"

    result = evaluation.record_teardown_success(store, graph, "flow", plan[0])
    assert result.current_step == plan[1]
    result = evaluation.record_teardown_success(store, graph, "flow", plan[1])
    assert result.state == "ready"
    assert "leaf.interface.setup" not in store.read().interfaces
    assert store.read().active_flow is None
