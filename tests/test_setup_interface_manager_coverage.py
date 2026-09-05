"""Release-boundary coverage for canonical managed setup and dispatches."""
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
    """Verify all canonical setup exports are discovered and bound to production dispatches."""
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
    # With canonical setup, all .interface.setup exports are automatically managed:
    # Five Markdown setups (pre-admitted in Task 07) plus wakeup Python setup
    assert production_managed == {
        "connect-google.interface.setup",
        "online-calendar.interface.setup",
        "cloud-files.interface.setup",
        "email-client.interface.setup",
        "list-manager.interface.setup",
        "llm-wakeup._rtx.interface.setup",
    }
    assert "bootstrap-dispatcher-runtime.interface.setup" not in graph.managed_setups

    # Verify all public .interface.setup exports are managed
    all_setup_exports = {
        export_id
        for export_id in graph.exports
        if export_id.endswith(".interface.setup")
    }
    assert all_setup_exports == set(graph.managed_setups)

    # Verify bootstrap-dispatcher-runtime and install-launchers don't have .interface.setup
    for export_id in graph.exports:
        if export_id.endswith(".interface.setup"):
            module_id = export_id.split(".interface.", 1)[0]
            assert module_id not in ("bootstrap-dispatcher-runtime", "install-launchers")

    # Markdown setups can have arguments (ordinary gateway contract)
    # but Python setups must not have arguments
    parameterized_setups = {
        interface_id
        for interface_id, export in graph.exports.items()
        if interface_id.endswith(".interface.setup")
        and isinstance(export.declaration.get("contract"), dict)
        and export.declaration["contract"].get("arguments")
    }
    # Verify all parameterized setups are Markdown (not Python)
    for setup_interface in parameterized_setups:
        metadata = graph.managed_setups[setup_interface]
        assert metadata.kind == "markdown", f"{setup_interface} has arguments but is {metadata.kind} kind"

    route = "setup-interface-manager._rtx.interface.teardown-all"
    export = graph.exports[route]
    assert export.source_interface_id == "setup-interface-manager._rtx.source.rtx-manager.interface.teardown-all"
    assert export.declaration["contract"]["arguments"] == {}
    assert export.declaration["process_binding"]["patterns"] == [{"allow_stdin": False, "min_positionals": 0, "max_positionals": 0}]
    assert any(route == target for uses in graph.interface_uses.values() for target, _version in uses)
    assert route in (REPO_ROOT / "references/blueprint-schema/runtime_dependencies.json").read_text()


def test_production_bindings_include_all_canonical_setups() -> None:
    """Pre-admit all canonical production setups before activation."""
    bindings, action_calls, dispatches = _setup_dispatches()

    EXPECTED_CANONICAL = {
        "connect-google.interface.setup",
        "online-calendar.interface.setup",
        "cloud-files.interface.setup",
        "email-client.interface.setup",
        "list-manager.interface.setup",
        "llm-wakeup._rtx.interface.setup",
    }
    assert set(bindings) == EXPECTED_CANONICAL

    # Graph-derived managed setups must match bindings
    graph = load_repository_blueprint_graph(REPO_ROOT)
    fixture_managed = {
        interface_id
        for interface_id in graph.managed_setups
        if graph.nodes[
            graph.exports[interface_id].module_node_id
        ].module_root.resolve().is_relative_to(REPO_ROOT / "tests" / "fixtures" / "setup_interface_manager" / "repository")
    }
    production_managed = set(graph.managed_setups) - fixture_managed
    # Replace Task-07 transitional assertion with canonical check
    assert set(bindings) == production_managed

    # Verify every production binding matches graph metadata
    for setup_interface in production_managed:
        binding = bindings[setup_interface]
        graph_metadata = graph.managed_setups[setup_interface]

        # Setup interface and version must match
        assert binding.setup_interface == graph_metadata.setup_interface
        assert binding.setup_version == graph_metadata.setup_version
        assert binding.setup_kind == graph_metadata.kind

        # Verify optional verifier fields match
        assert (binding.setup_verifier_interface is None) == (graph_metadata.setup_verifier_interface is None)
        if graph_metadata.setup_verifier_interface:
            assert binding.setup_verifier_interface == graph_metadata.setup_verifier_interface
            assert binding.setup_verifier_version == graph_metadata.setup_verifier_version

        # Verify optional teardown fields match
        assert (binding.teardown_interface is None) == (graph_metadata.teardown_interface is None)
        if graph_metadata.teardown_interface:
            assert binding.teardown_interface == graph_metadata.teardown_interface
            assert binding.teardown_version == graph_metadata.teardown_version
            assert (binding.teardown_verifier_interface is None) == (graph_metadata.teardown_verifier_interface is None)
            if graph_metadata.teardown_verifier_interface:
                assert binding.teardown_verifier_interface == graph_metadata.teardown_verifier_interface
                assert binding.teardown_verifier_version == graph_metadata.teardown_verifier_version

    # Verify four Markdown bindings have no runtime dispatch keys
    for setup_interface in {
        "connect-google.interface.setup",
        "online-calendar.interface.setup",
        "cloud-files.interface.setup",
        "list-manager.interface.setup",
    }:
        binding = bindings[setup_interface]
        # Setup interface/version match
        assert binding.setup_interface == setup_interface
        assert binding.setup_version == 1
        # Markdown kind
        assert binding.setup_kind == "markdown"
        # Setup instructions are nonempty
        assert binding.setup_instructions and len(binding.setup_instructions) > 0
        # No verifier/teardown
        assert binding.setup_verifier_interface is None
        assert binding.setup_verifier_version is None
        assert binding.setup_verifier_dispatch_key is None
        assert binding.teardown_interface is None
        assert binding.teardown_version is None
        assert binding.teardown_dispatch_key is None
        assert binding.teardown_instructions is None
        assert binding.teardown_verifier_interface is None
        assert binding.teardown_verifier_version is None
        assert binding.teardown_verifier_dispatch_key is None

    # Verify wakeup binding is unchanged
    wakeup_binding = bindings["llm-wakeup._rtx.interface.setup"]
    assert wakeup_binding.setup_interface == "llm-wakeup._rtx.interface.setup"
    assert wakeup_binding.setup_version == 1
    assert wakeup_binding.setup_kind == "python"
    assert wakeup_binding.setup_dispatch_key == "wakeup-setup"
    assert wakeup_binding.setup_verifier_interface == "llm-wakeup._rtx.interface.setup-status"
    assert wakeup_binding.setup_verifier_version == 1
    assert wakeup_binding.setup_verifier_dispatch_key == "wakeup-setup-status"
    assert wakeup_binding.teardown_interface == "llm-wakeup._rtx.interface.teardown"
    assert wakeup_binding.teardown_version == 1
    assert wakeup_binding.teardown_dispatch_key == "wakeup-teardown"
    assert wakeup_binding.teardown_verifier_interface == "llm-wakeup._rtx.interface.teardown-status"
    assert wakeup_binding.teardown_verifier_version == 1
    assert wakeup_binding.teardown_verifier_dispatch_key == "wakeup-teardown-status"


def test_production_map_has_no_managed_setup_routes() -> None:
    """Catches publication drift or an owner route escaping blueprint review."""
    bindings, action_calls, dispatches = _setup_dispatches()

    # Action calls only include wakeup routes
    assert set(action_calls) == {
        "wakeup-setup",
        "wakeup-setup-status",
        "wakeup-teardown",
        "wakeup-teardown-status",
    }
    # Dispatches include getter plus only wakeup routes
    assert set(dispatches) == {
        "setup-status-path",
        "wakeup-setup",
        "wakeup-setup-status",
        "wakeup-teardown",
        "wakeup-teardown-status",
    }
    route = "setup-interface-manager._rtx.interface.teardown-all"
    assert "TeardownAllInterface" in getattr(_setup_dispatches, "runtime")
    assert "teardown-all" not in dispatches
    assert route in (REPO_ROOT / "skills/setup-interface-manager/SKILL.md").read_text()
    assert route in (REPO_ROOT / "docs/setup.md").read_text()
