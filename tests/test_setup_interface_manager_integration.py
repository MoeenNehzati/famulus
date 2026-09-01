"""End-to-end acceptance tests for the managed setup lifecycle."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Mapping

import pytest

from officina.blueprints.graph import ManagedSetup
from officina.common import atomic_files
from officina.runtime.python_machine_interface import logical_python_package_name
from officina.runtime.python_machine_interface_runner import (
    load_interface,
    run_python_machine_interface,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGER_ROOT = REPO_ROOT / "skills" / "setup-interface-manager" / "_rtx"
LOGICAL_PACKAGE = logical_python_package_name("setup-interface-manager._rtx")
LOGICAL_ENTRYPOINT = f"{LOGICAL_PACKAGE}._setup_manager"
MCP_SERVER = REPO_ROOT / "mcp_server.py"


def _load_manager_modules() -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    previous = Path.cwd()
    try:
        os.chdir(MANAGER_ROOT)
        status_interface = load_interface(
            "_setup_manager.py",
            "StatusInterface",
            logical_package=LOGICAL_PACKAGE,
            logical_entrypoint=LOGICAL_ENTRYPOINT,
        )
    finally:
        os.chdir(previous)
    globals_ = status_interface.__class__.run.__globals__
    return (
        SimpleNamespace(**globals_),
        SimpleNamespace(
            **globals_["ManagedInterfaceBinding"].__post_init__.__globals__
        ),
        SimpleNamespace(**globals_["LedgerStore"].__init__.__globals__),
    )


manager, setup_dispatches, state = _load_manager_modules()


def _load_mcp_server(name: str):
    spec = importlib.util.spec_from_file_location(name, MCP_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AtomicFiles:
    """Real confined filesystem operations behind the manager's ledger adapter."""

    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
        atomic_files.ensure_private_directory(path.parent, allowed_root=allowed_root)

    def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
        return atomic_files.exclusive_file_lock(
            path, allowed_root=allowed_root, mode=mode
        )

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
        return atomic_files.read_regular_file_bytes(path, allowed_root=allowed_root)

    def atomic_compare_and_replace_bytes(
        self, path: Path, data: bytes, **kwargs: object
    ) -> None:
        atomic_files.atomic_compare_and_replace_bytes(path, data, **kwargs)


def _managed(stem: str, *, kind: str = "python", version: int = 1) -> ManagedSetup:
    return ManagedSetup(
        setup_interface=f"{stem}.interface.setup",
        setup_version=version,
        teardown_interface=f"{stem}.interface.teardown",
        teardown_version=1,
        setup_verifier_interface=f"{stem}.interface.setup-status",
        setup_verifier_version=1,
        teardown_verifier_interface=f"{stem}.interface.teardown-status",
        teardown_verifier_version=1,
        kind=kind,  # type: ignore[arg-type]
    )


def _binding(
    item: ManagedSetup,
    *, arguments: tuple[object, ...] = (),
) -> object:
    stem = item.setup_interface.removesuffix(".interface.setup")
    return setup_dispatches.ManagedInterfaceBinding(
        setup_interface=item.setup_interface,
        setup_version=item.setup_version,
        setup_kind=item.kind,
        setup_dispatch_key=f"{stem}-setup",
        setup_instructions=f"Establish the declared state for {stem}.",
        setup_verifier_interface=item.setup_verifier_interface,
        setup_verifier_version=item.setup_verifier_version,
        setup_verifier_dispatch_key=f"{stem}-setup-status",
        teardown_interface=item.teardown_interface,
        teardown_version=item.teardown_version,
        teardown_dispatch_key=f"{stem}-teardown",
        teardown_instructions=f"Remove the declared state for {stem}.",
        teardown_verifier_interface=item.teardown_verifier_interface,
        teardown_verifier_version=item.teardown_verifier_version,
        teardown_verifier_dispatch_key=f"{stem}-teardown-status",
        arguments=arguments,
    )


def _graph(
    items: tuple[ManagedSetup, ...],
    requirements: Mapping[str, tuple[tuple[str, int], ...]],
    *,
    ordinary_targets: Mapping[str, str],
) -> SimpleNamespace:
    exports: dict[str, object] = {}
    parents: dict[str, str | None] = {}
    for item in items:
        module = item.setup_interface.removesuffix(".interface.setup")
        parents[module] = None
        for interface in (
            item.setup_interface,
            item.teardown_interface,
            item.setup_verifier_interface,
            item.teardown_verifier_interface,
        ):
            exports[interface] = SimpleNamespace(module_node_id=module)
    for interface, module in ordinary_targets.items():
        exports[interface] = SimpleNamespace(module_node_id=module)
        parents.setdefault(module, None)
    return SimpleNamespace(
        setup_requirements=dict(requirements),
        managed_setups={item.setup_interface: item for item in items},
        module_parents=parents,
        exports=exports,
    )


class DispatchBoundary:
    """Deterministic stand-in for declared external interfaces only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], str | None]] = []
        self.action_codes: dict[str, list[int]] = {}
        self.verifier_values: dict[str, list[str]] = {}

    def fail_action_once(self, key: str, code: int = 9) -> None:
        self.action_codes.setdefault(key, []).append(code)

    def verify_once(self, key: str, payload: str) -> None:
        self.verifier_values.setdefault(key, []).append(payload)

    def __call__(
        self, key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((key, tuple(args), stdin))
        if key.endswith("-setup-status"):
            default = '{"set_up":true}\n'
        elif key.endswith("-teardown-status"):
            default = '{"torn_down":true}\n'
        else:
            codes = self.action_codes.get(key, [])
            code = codes.pop(0) if codes else 0
            return subprocess.CompletedProcess([], code, "", "bounded failure" if code else "")
        values = self.verifier_values.get(key, [])
        return subprocess.CompletedProcess([], 0, values.pop(0) if values else default, "")


class Scenario:
    """Public-route driver whose controllers restart over one persisted ledger."""

    def __init__(
        self,
        tmp_path: Path,
        graph: SimpleNamespace,
        bindings: tuple[object, ...],
    ) -> None:
        self.path = tmp_path / "private" / "setup-status.json"
        self.graph = graph
        self.bindings = {binding.setup_interface: binding for binding in bindings}
        self.dispatch = DispatchBoundary()
        self._next_flow = 0
        self.controller = self.restart()

    def store(self):
        return state.LedgerStore._from_atomic_files(self.path, AtomicFiles())

    def restart(self):
        self._next_flow += 1
        controller = manager.SetupManager(
            graph=self.graph,
            store=self.store(),
            dispatch=self.dispatch,
            bindings=self.bindings,
            new_flow_id=lambda: f"flow-{self._next_flow}",
        )
        self.controller = controller
        return controller

    def invoke(
        self, interface_type: type, argv: list[str], *, stdin: str = ""
    ) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        previous_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(stdin)
            with redirect_stdout(output):
                code = run_python_machine_interface(
                    interface_type(manager_factory=lambda: self.controller), argv
                )
        finally:
            sys.stdin = previous_stdin
        rendered = output.getvalue()
        assert rendered.count("\n") == 1
        return code, json.loads(rendered)

    def status(self, target: str) -> tuple[int, dict[str, object]]:
        return self.invoke(manager.StatusInterface, [target])

    def authorize(self, target: str) -> tuple[int, dict[str, object]]:
        return self.invoke(
            manager.AuthorizeInterface,
            [target, "acceptance-caller", target, "1"],
        )

    def begin(self, operation: str, root: str) -> tuple[int, dict[str, object]]:
        lifecycle = root.removesuffix("setup") + (
            "setup" if operation == "setup" else "teardown"
        )
        return self.invoke(
            manager.BeginInterface,
            [operation, root, "acceptance-caller", lifecycle, "1"],
        )

    def run_current(
        self, payload: dict[str, object], *, stdin: str = "{}"
    ) -> tuple[int, dict[str, object]]:
        flow_id = payload["flow_id"]
        step = payload["current_step"]
        assert isinstance(flow_id, str) and isinstance(step, dict)
        interface = step["interface"]
        kind = step["kind"]
        assert isinstance(interface, str)
        if kind == "markdown":
            code, instructions = self.invoke(
                manager.RunMarkdownInterface, [flow_id, interface]
            )
            assert code == 0
            assert instructions["state"] == "awaiting-settlement"
            return self.invoke(manager.SettleInterface, [flow_id, interface])
        return self.invoke(
            manager.RunPythonInterface, [flow_id, interface], stdin=stdin
        )

    def finish(self, payload: dict[str, object]) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        while payload["state"] == "run-step":
            code, payload = self.run_current(payload)
            assert code == 0, payload
            results.append(payload)
        return results


def test_public_workflow_switches_dependency_first_and_resumes_once_after_restart(
    tmp_path: Path,
) -> None:
    """Catches lost order/arguments or any lifecycle response resuming the request."""
    leaf = _managed("leaf", kind="python")
    parent = _managed("parent", kind="markdown")
    root = _managed("root", kind="python")
    root_argument = setup_dispatches.ManagedArgument(
        name="profile", option="--profile", required=True
    )
    graph = _graph(
        (leaf, parent, root),
        {
            leaf.setup_interface: (),
            parent.setup_interface: ((leaf.setup_interface, 1),),
            root.setup_interface: ((parent.setup_interface, 1),),
        },
        ordinary_targets={
            "plain.interface.run": "plain",
            "root.interface.run": "root",
        },
    )
    scenario = Scenario(
        tmp_path,
        graph,
        (_binding(leaf), _binding(parent), _binding(root, arguments=(root_argument,))),
    )

    code, unmanaged = scenario.authorize("plain.interface.run")
    assert code == 0
    assert unmanaged["resume_original"] is True
    assert scenario.store().read().interfaces == {}

    code, status = scenario.status("root.interface.run")
    assert code == 0
    assert [step["interface"] for step in status["pending_stack"]] == [
        root.setup_interface,
        parent.setup_interface,
        leaf.setup_interface,
    ]
    code, refused = scenario.authorize("root.interface.run")
    assert code == 2
    assert refused["resume_original"] is False

    code, current = scenario.begin("setup", root.setup_interface)
    assert code == 0
    observed_order: list[str] = []
    resume_signals: list[bool] = []
    original_arguments = {"query": "caller-held-secret"}
    while current["state"] == "run-step":
        step = current["current_step"]
        assert isinstance(step, dict)
        observed_order.append(step["interface"])
        stdin = '{"profile":"release"}' if step["interface"] == root.setup_interface else "{}"
        _code, current = scenario.run_current(current, stdin=stdin)
        resume_signals.append(bool(current["resume_original"]))
        if step["interface"] == leaf.setup_interface:
            scenario.restart()

    assert observed_order == [
        leaf.setup_interface,
        parent.setup_interface,
        root.setup_interface,
    ]
    assert resume_signals == [False, False, False]
    assert ("root-setup", ("--profile", "release"), None) in scenario.dispatch.calls
    assert "caller-held-secret" not in scenario.path.read_text(encoding="utf-8")

    code, ready = scenario.status("root.interface.run")
    assert code == 0 and ready["code"] == "ready"
    code, authorized = scenario.authorize("root.interface.run")
    assert code == 0 and authorized["resume_original"] is True
    resumed: list[dict[str, str]] = []
    if authorized["resume_original"]:
        resumed.append(original_arguments)
    assert resumed == [{"query": "caller-held-secret"}]
    assert all(
        receipt.required_by == frozenset({root.setup_interface})
        for receipt in scenario.store().read().interfaces.values()
    )


def test_pending_mcp_call_crosses_real_routes_and_launches_original_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches MCP losing, leaking, mutating, or duplicating a suspended call."""
    plugin_data = tmp_path / "plugin-data"
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.delenv("ASSISTANT_LOGS", raising=False)
    server = _load_mcp_server("setup_manager_acceptance_mcp_before_restart")
    server.configure_plugin_persistence()

    secret = "pending-boundary-secret"
    target = "milestone-logging._rtx.interface.record"
    original_arguments = server.CompactArguments(
        positionals=[f"record {secret}"],
        options={"--role": f"acceptance-{secret}"},
        stdin=None,
    )
    original_snapshot = (
        list(original_arguments.positionals),
        dict(original_arguments.options),
        original_arguments.stdin,
    )
    manager_payloads: list[dict[str, object]] = []
    preflight_operations: list[str] = []
    original_launches: list[tuple[list[str], object]] = []

    def observe_boundaries(module) -> None:
        real_manager_call = module._manager_call
        real_run = module._run_resolved_invocation

        def observed_manager_call(
            caller: str, operation: str, arguments: list[str]
        ) -> dict[str, object]:
            preflight_operations.append(operation)
            payload = real_manager_call(caller, operation, arguments)
            manager_payloads.append(payload)
            return payload

        def observed_run(resolved, *args: object, **kwargs: object):
            if resolved.target == target:
                original_launches.append(
                    (list(resolved.metadata().command), kwargs.get("stdin"))
                )
            return real_run(resolved, *args, **kwargs)

        module._manager_call = observed_manager_call
        module._run_resolved_invocation = observed_run

    def invoke_manager_route(
        module, route: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, object]]:
        raw_arguments = route["arguments"]
        assert isinstance(raw_arguments, dict)
        arguments = module.CompactArguments(
            positionals=list(raw_arguments["positionals"]),
            options=dict(raw_arguments["options"]),
            stdin=raw_arguments["stdin"],
        )
        result = module.invoke(
            "milestone-logging",
            route["interface"],
            route["version"],
            arguments,
        )
        assert isinstance(result, dict) and result["exit_code"] == 0, result
        payload = json.loads(result["stdout"])
        assert isinstance(payload, dict)
        manager_payloads.append(payload)
        return result, payload

    observe_boundaries(server)
    pending = server.invoke(
        "milestone-logging", target, 1, original_arguments
    )
    assert isinstance(pending, dict)
    assert pending["code"] == "setup_required"
    assert original_launches == []
    assert (
        original_arguments.positionals,
        original_arguments.options,
        original_arguments.stdin,
    ) == original_snapshot
    _begin_result, begun = invoke_manager_route(server, pending["manager"])
    assert begun["state"] == "run-step"

    # A fresh MCP module simulates a host restart; the manager subprocesses are
    # already fresh per route and reconstruct from the getter-selected ledger.
    server = _load_mcp_server("setup_manager_acceptance_mcp_after_restart")
    observe_boundaries(server)
    step = begun["current_step"]
    assert isinstance(step, dict)
    run_route = {
        "interface": "setup-interface-manager._rtx.interface.run-markdown",
        "version": 1,
        "arguments": {
            "positionals": [begun["flow_id"], step["interface"]],
            "options": {},
            "stdin": None,
        },
    }
    _run_result, awaiting = invoke_manager_route(server, run_route)
    assert awaiting["state"] == "awaiting-settlement"
    settle_route = {
        "interface": "setup-interface-manager._rtx.interface.settle",
        "version": 1,
        "arguments": run_route["arguments"],
    }
    _settle_result, settled = invoke_manager_route(server, settle_route)
    assert settled["state"] == "ready"
    assert settled["resume_original"] is False

    resumed = server.invoke(
        "milestone-logging", target, 1, original_arguments
    )
    assert isinstance(resumed, dict) and resumed["exit_code"] == 0, resumed
    assert original_launches == [
        ([f"record {secret}", "--role", f"acceptance-{secret}"], None)
    ]
    assert preflight_operations == ["status", "status", "authorize"]
    assert (
        original_arguments.positionals,
        original_arguments.options,
        original_arguments.stdin,
    ) == original_snapshot

    encoded_boundaries = json.dumps(
        [pending, *manager_payloads], sort_keys=True
    )
    assert secret not in encoded_boundaries
    ledger = plugin_data / "setup" / "status.json"
    assert ledger.is_file()
    assert secret not in ledger.read_text(encoding="utf-8")


def test_stale_suffix_invalidation_and_reverse_teardown_use_persisted_state(
    tmp_path: Path,
) -> None:
    """Catches stale-prefix reruns, incomplete invalidation, or forward teardown."""
    leaf = _managed("leaf")
    parent = _managed("parent")
    root = _managed("root")
    graph = _graph(
        (leaf, parent, root),
        {
            leaf.setup_interface: (),
            parent.setup_interface: ((leaf.setup_interface, 1),),
            root.setup_interface: ((parent.setup_interface, 1),),
        },
        ordinary_targets={"root.interface.run": "root"},
    )
    scenario = Scenario(
        tmp_path, graph, tuple(_binding(item) for item in (leaf, parent, root))
    )
    _code, setup = scenario.begin("setup", root.setup_interface)
    scenario.finish(setup)

    before = scenario.store().read()
    stale_interfaces = dict(before.interfaces)
    stale_interfaces[parent.setup_interface] = state.SetupReceipt(
        9, stale_interfaces[parent.setup_interface].required_by
    )
    scenario.store().update(
        lambda _ledger: state.SetupLedger(
            interfaces=stale_interfaces, active_flow=None
        )
    )
    _code, stale = scenario.status("root.interface.run")
    assert [step["interface"] for step in stale["pending_stack"]] == [
        root.setup_interface,
        parent.setup_interface,
    ]
    _code, repair = scenario.begin("setup", root.setup_interface)
    repaired_steps = [repair["current_step"]["interface"]]
    while repair["state"] == "run-step":
        code, repair = scenario.run_current(repair)
        assert code == 0, repair
        if repair["current_step"] is not None:
            repaired_steps.append(repair["current_step"]["interface"])
    assert repaired_steps == [parent.setup_interface, root.setup_interface]

    code, invalidated = scenario.invoke(
        manager.InvalidateInterface, [leaf.setup_interface]
    )
    assert code == 0
    assert set(invalidated["removed"]) == {
        leaf.setup_interface,
        parent.setup_interface,
        root.setup_interface,
    }
    assert scenario.store().read().interfaces == {}

    _code, rebuilt = scenario.begin("setup", root.setup_interface)
    scenario.finish(rebuilt)
    _code, teardown = scenario.begin("teardown", root.setup_interface)
    teardown_order: list[str] = []
    while teardown["state"] == "run-step":
        step = teardown["current_step"]
        teardown_order.append(step["interface"])
        _code, teardown = scenario.run_current(teardown)
    assert teardown_order == [
        root.teardown_interface,
        parent.teardown_interface,
        leaf.teardown_interface,
    ]
    assert teardown["resume_original"] is False
    assert scenario.store().read().interfaces == {}


@pytest.mark.parametrize("first", ["left", "right"])
def test_both_shared_dependency_histories_release_then_remove_the_leaf(
    tmp_path: Path, first: str
) -> None:
    """Catches either root order tearing down a dependency still claimed by its peer."""
    leaf = _managed("leaf")
    left = _managed("left")
    right = _managed("right")
    items = {"leaf": leaf, "left": left, "right": right}
    graph = _graph(
        (leaf, left, right),
        {
            leaf.setup_interface: (),
            left.setup_interface: ((leaf.setup_interface, 1),),
            right.setup_interface: ((leaf.setup_interface, 1),),
        },
        ordinary_targets={
            "left.interface.run": "left",
            "right.interface.run": "right",
        },
    )
    scenario = Scenario(
        tmp_path, graph, tuple(_binding(item) for item in (leaf, left, right))
    )
    second = "right" if first == "left" else "left"
    for root_name in (first, second):
        root = items[root_name]
        _code, setup = scenario.begin("setup", root.setup_interface)
        scenario.finish(setup)
        code, authorized = scenario.authorize(f"{root_name}.interface.run")
        assert code == 0 and authorized["resume_original"] is True

    assert scenario.store().read().interfaces[leaf.setup_interface].required_by == {
        items[first].setup_interface,
        items[second].setup_interface,
    }
    first_teardown_key = f"{first}-teardown"
    leaf_teardown_key = "leaf-teardown"
    _code, teardown = scenario.begin("teardown", items[first].setup_interface)
    scenario.finish(teardown)
    assert any(call[0] == first_teardown_key for call in scenario.dispatch.calls)
    assert not any(call[0] == leaf_teardown_key for call in scenario.dispatch.calls)
    assert scenario.store().read().interfaces[leaf.setup_interface].required_by == {
        items[second].setup_interface
    }

    _code, teardown = scenario.begin("teardown", items[second].setup_interface)
    scenario.finish(teardown)
    assert [call[0] for call in scenario.dispatch.calls].count(leaf_teardown_key) == 1
    assert scenario.store().read().interfaces == {}


def test_failure_interruption_recovery_restart_and_malformed_ledger_fail_closed(
    tmp_path: Path,
) -> None:
    """Catches failed actions recording state or restart/recovery guessing completion."""
    item = _managed("canary")
    graph = _graph(
        (item,),
        {item.setup_interface: ()},
        ordinary_targets={"canary.interface.run": "canary"},
    )
    binding = _binding(item)
    scenario = Scenario(tmp_path, graph, (binding,))
    scenario.dispatch.fail_action_once(binding.setup_dispatch_key)
    _code, begun = scenario.begin("setup", item.setup_interface)
    flow_id = begun["flow_id"]
    assert isinstance(flow_id, str)
    code, failed = scenario.run_current(begun)
    assert code == 2 and failed["state"] == "failed"
    assert scenario.store().read().interfaces == {}
    assert scenario.store().read().active_flow is not None

    scenario.restart()
    code, busy = scenario.status("canary.interface.run")
    assert code == 0 and busy["code"] == "setup_busy"
    scenario.dispatch.verify_once(binding.setup_verifier_dispatch_key, '{"set_up":false}\n')
    code, retry = scenario.invoke(manager.RecoverInterface, [flow_id, "retry"])
    assert code == 0
    assert retry["state"] == "run-step"
    assert retry["current_step"]["interface"] == item.setup_interface
    code, completed = scenario.run_current(retry)
    assert code == 0 and completed["state"] == "ready"
    assert scenario.store().read().interfaces[item.setup_interface].version == 1

    scenario.path.write_bytes(b'{"schema_version":1,"interfaces":{},"active_flow":null}')
    scenario.restart()
    code, malformed = scenario.status("canary.interface.run")
    assert code == 2
    assert malformed["code"] == "setup_busy"
    assert "canonical" in malformed["error"]
