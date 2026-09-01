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
    return globals_["PRODUCTION_BINDINGS"], globals_["PRODUCTION_DISPATCHES"]


def test_release_has_only_the_milestone_markdown_canary() -> None:
    """Catches bootstrap or parameterized production setup opting in to release one."""
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
    assert production_managed == {"milestone-logging.interface.setup"}
    assert graph.managed_setups["milestone-logging.interface.setup"].kind == "markdown"
    assert "setup-python-environment.interface.setup" not in graph.managed_setups

    parameterized_setups = {
        interface_id
        for interface_id, export in graph.exports.items()
        if interface_id.endswith(".interface.setup")
        and isinstance(export.declaration.get("contract"), dict)
        and export.declaration["contract"].get("arguments")
    }
    assert parameterized_setups.isdisjoint(production_managed)


def test_production_map_contains_only_the_canary_pair_and_verifiers() -> None:
    """Catches a missing canary route or an undeclared production lifecycle action."""
    bindings, dispatches = _setup_dispatches()
    binding = bindings["milestone-logging.interface.setup"]

    assert set(bindings) == {"milestone-logging.interface.setup"}
    assert {
        binding.setup_interface,
        binding.setup_verifier_interface,
        binding.teardown_interface,
        binding.teardown_verifier_interface,
    } == {
        "milestone-logging.interface.setup",
        "milestone-logging._rtx.interface.setup-status",
        "milestone-logging.interface.teardown",
        "milestone-logging._rtx.interface.teardown-status",
    }
    assert {
        key: call.target_interface_id
        for key, call in dispatches.items()
        if key != "setup-status-path"
    } == {
        "milestone-logging-setup": "milestone-logging.interface.setup",
        "milestone-logging-setup-status": "milestone-logging._rtx.interface.setup-status",
        "milestone-logging-teardown": "milestone-logging.interface.teardown",
        "milestone-logging-teardown-status": "milestone-logging._rtx.interface.teardown-status",
    }
