"""Release-boundary coverage for managed setup opt-ins and dispatches."""
from __future__ import annotations

import os
from pathlib import Path

from officina.blueprints.graph import load_repository_blueprint_graph
from officina.runtime.python_machine_interface_runner import load_interface


REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGER_RUNTIME = REPO_ROOT / "skills" / "setup-interface-manager" / "_rtx"
TEST_FIXTURE_REPOSITORY = (
    REPO_ROOT / "tests" / "fixtures" / "setup_interface_manager" / "repository"
).resolve()


def _setup_dispatches():
    previous = Path.cwd()
    try:
        os.chdir(MANAGER_RUNTIME)
        interface = load_interface(
            "_setup_manager.py",
            "StatusInterface",
            logical_package="_setup_interface_manager_coverage",
            logical_entrypoint="_setup_interface_manager_coverage._setup_manager",
        )
    finally:
        os.chdir(previous)
    globals_ = interface.__class__.run.__globals__
    _setup_dispatches.runtime = globals_  # type: ignore[attr-defined]
    dispatch_globals = globals_["ManagedInterfaceBinding"].__post_init__.__globals__
    return globals_["PRODUCTION_BINDINGS"], dispatch_globals["PRODUCTION_ACTION_CALLS"], globals_["PRODUCTION_DISPATCHES"]


def test_release_has_no_production_managed_setups() -> None:
    """Catches any managed setup opting in without an explicit release decision."""
    graph = load_repository_blueprint_graph(REPO_ROOT)

    fixture_managed = {
        interface_id
        for interface_id in graph.managed_setups
        if graph.nodes[
            graph.exports[interface_id].module_node_id
        ].module_root.resolve().is_relative_to(TEST_FIXTURE_REPOSITORY)
    }
    production_managed = set(graph.managed_setups) - fixture_managed

    assert fixture_managed == {"python-canary.interface.setup"}
    assert production_managed == set()
    assert "setup-dispatcher-runtime.interface.setup" not in graph.managed_setups

    parameterized_setups = {
        interface_id
        for interface_id, export in graph.exports.items()
        if interface_id.endswith(".interface.setup")
        and isinstance(export.declaration.get("contract"), dict)
        and export.declaration["contract"].get("arguments")
    }
    assert parameterized_setups.isdisjoint(production_managed)

    route = "setup-interface-manager._rtx.interface.teardown-all"
    export = graph.exports[route]
    assert export.source_interface_id == "setup-interface-manager._rtx.source.rtx-manager.interface.teardown-all"
    assert export.declaration["contract"]["arguments"] == {}
    assert export.declaration["process_binding"]["patterns"] == [{"allow_stdin": False, "min_positionals": 0, "max_positionals": 0}]
    assert any(route == target for uses in graph.interface_uses.values() for target, _version in uses)
    assert route in (REPO_ROOT / "references/blueprint-schema/runtime_dependencies.json").read_text()


def test_production_map_has_no_managed_setup_routes() -> None:
    """Catches publication drift or an owner route escaping blueprint review."""
    bindings, action_calls, dispatches = _setup_dispatches()

    assert set(bindings) == set()
    assert set(action_calls) == set()
    assert set(dispatches) == {"setup-status-path"}
    route = "setup-interface-manager._rtx.interface.teardown-all"
    assert "TeardownAllInterface" in getattr(_setup_dispatches, "runtime")
    assert "teardown-all" not in dispatches
    assert route in (REPO_ROOT / "skills/setup-interface-manager/SKILL.md").read_text()
    assert route in (REPO_ROOT / "docs/setup.md").read_text()
