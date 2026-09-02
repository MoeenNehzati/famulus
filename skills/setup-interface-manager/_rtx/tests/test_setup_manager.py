"""Behavioral tests for the finite managed-setup controller and public routes."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from officina.blueprints.graph import (
    BlueprintGraphError,
    ManagedSetup,
    load_repository_blueprint_graph,
)
from officina.common import atomic_files
from officina.dispatcher.errors import InvocationError
from officina.runtime.python_machine_interface import (
    DispatchCall,
    logical_python_package_name,
    set_runtime_dispatch_context,
)
from officina.runtime.python_machine_interface_runner import (
    load_interface,
    run_python_machine_interface,
)


SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[2]
LOGICAL_PACKAGE = logical_python_package_name("setup-interface-manager._rtx")
LOGICAL_ENTRYPOINT = f"{LOGICAL_PACKAGE}._setup_manager"
FIXTURE_REPO = (
    REPO_ROOT / "tests" / "fixtures" / "setup_interface_manager" / "repository"
)
FIXTURE_MODULE_PATH = FIXTURE_REPO / "python-canary" / "python_canary.py"
FIXTURE_TEARDOWN_MODULE_PATH = (
    FIXTURE_REPO / "python-canary" / "python_canary_teardown.py"
)


def _load_runtime_modules():
    previous = Path.cwd()
    try:
        os.chdir(SCRIPT_DIR)
        interface = load_interface(
            "_setup_manager.py",
            "StatusInterface",
            logical_package=LOGICAL_PACKAGE,
            logical_entrypoint=LOGICAL_ENTRYPOINT,
        )
    finally:
        os.chdir(previous)
    manager_globals = interface.__class__.run.__globals__
    return (
        interface,
        SimpleNamespace(
            **manager_globals["ManagedInterfaceBinding"].__post_init__.__globals__
        ),
        SimpleNamespace(
            **manager_globals["SetupStep"].from_managed.__func__.__globals__
        ),
        SimpleNamespace(**manager_globals),
        SimpleNamespace(**manager_globals["LedgerStore"].__init__.__globals__),
    )


(
    LOGICAL_STATUS_INTERFACE,
    setup_dispatches,
    evaluation,
    manager,
    state,
) = _load_runtime_modules()


class AtomicFiles:
    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None:
        atomic_files.ensure_private_directory(path.parent, allowed_root=allowed_root)

    def exclusive_file_lock(self, path: Path, *, allowed_root: Path, mode: int):
        return atomic_files.exclusive_file_lock(path, allowed_root=allowed_root, mode=mode)

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes:
        return atomic_files.read_regular_file_bytes(path, allowed_root=allowed_root)

    def atomic_compare_and_replace_bytes(self, path: Path, data: bytes, **kwargs: object) -> None:
        try:
            atomic_files.atomic_compare_and_replace_bytes(path, data, **kwargs)
        except atomic_files.AtomicWriteError as exc:
            if "predecessor mismatch" in str(exc) or "changed" in str(exc):
                raise state.LedgerConflict(str(exc)) from exc
            raise state.LedgerPathError(str(exc)) from exc


def _store(tmp_path: Path) -> state.LedgerStore:
    return state.LedgerStore._from_atomic_files(
        tmp_path / "private" / "state" / "ledger.json", AtomicFiles()
    )


def _managed(stem: str, *, kind: str = "python") -> ManagedSetup:
    return ManagedSetup(
        setup_interface=f"{stem}.interface.setup",
        setup_version=1,
        teardown_interface=f"{stem}.interface.teardown",
        teardown_version=1,
        setup_verifier_interface=f"{stem}.interface.setup-status",
        setup_verifier_version=1,
        teardown_verifier_interface=f"{stem}.interface.teardown-status",
        teardown_verifier_version=1,
        kind=kind,  # type: ignore[arg-type]
    )


def _graph(*managed: ManagedSetup) -> SimpleNamespace:
    requirements = {
        item.setup_interface: ()
        for item in managed
    }
    exports = {}
    parents = {}
    for item in managed:
        module = item.setup_interface.removesuffix(".interface.setup")
        parents[module] = None
        for interface in (
            item.setup_interface,
            item.teardown_interface,
            item.setup_verifier_interface,
            item.teardown_verifier_interface,
            f"{module}.interface.run",
        ):
            exports[interface] = SimpleNamespace(module_node_id=module)
    return SimpleNamespace(
        setup_requirements=requirements,
        managed_setups={item.setup_interface: item for item in managed},
        module_parents=parents,
        exports=exports,
    )


def _binding(item: ManagedSetup) -> setup_dispatches.ManagedInterfaceBinding:
    stem = item.setup_interface.removesuffix(".interface.setup")
    return setup_dispatches.ManagedInterfaceBinding(
        setup_interface=item.setup_interface,
        setup_version=item.setup_version,
        setup_kind=item.kind,
        setup_dispatch_key=f"{stem}-setup",
        setup_instructions=f"Follow the exact setup instructions for {stem}.",
        setup_verifier_interface=item.setup_verifier_interface,
        setup_verifier_version=item.setup_verifier_version,
        setup_verifier_dispatch_key=f"{stem}-setup-status",
        teardown_interface=item.teardown_interface,
        teardown_version=item.teardown_version,
        teardown_dispatch_key=f"{stem}-teardown",
        teardown_instructions=f"Follow the exact teardown instructions for {stem}.",
        teardown_verifier_interface=item.teardown_verifier_interface,
        teardown_verifier_version=item.teardown_verifier_version,
        teardown_verifier_dispatch_key=f"{stem}-teardown-status",
        arguments=(),
    )


PYTHON_CANARY_BINDING = setup_dispatches.ManagedInterfaceBinding(
    setup_interface="python-canary.interface.setup",
    setup_version=1,
    setup_kind="python",
    setup_dispatch_key="python-canary-setup",
    setup_instructions="",
    setup_verifier_interface="python-canary.interface.setup-status",
    setup_verifier_version=1,
    setup_verifier_dispatch_key="python-canary-setup-status",
    teardown_interface="python-canary.interface.teardown",
    teardown_version=1,
    teardown_dispatch_key="python-canary-teardown",
    teardown_instructions="",
    teardown_verifier_interface="python-canary.interface.teardown-status",
    teardown_verifier_version=1,
    teardown_verifier_dispatch_key="python-canary-teardown-status",
)
PYTHON_CANARY_CALLS = {
    key: DispatchCall(
        caller_module_id="setup-interface-manager._rtx",
        target_module_id="python-canary",
        interface=interface,
        smoke_args=(),
    )
    for key, interface in (
        ("python-canary-setup", "setup"),
        ("python-canary-setup-status", "setup-status"),
        ("python-canary-teardown", "teardown"),
        ("python-canary-teardown-status", "teardown-status"),
    )
}


def _load_python_canary(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Python canary fixture")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


python_canary = _load_python_canary(
    "setup_interface_manager_python_canary", FIXTURE_MODULE_PATH
)
python_canary_teardown = _load_python_canary(
    "setup_interface_manager_python_canary_teardown", FIXTURE_TEARDOWN_MODULE_PATH
)


class FixtureRuntime(manager.BeginInterface):
    """Runtime using a registered graph and one fixed test-only dispatch map."""

    dispatches = {
        setup_dispatches.GETTER_KEY: setup_dispatches.GETTER_CALL,
        **PYTHON_CANARY_CALLS,
    }

    def __init__(self, ledger_path: Path) -> None:
        super().__init__(
            graph_loader=lambda _root: load_repository_blueprint_graph(FIXTURE_REPO),
            bindings={PYTHON_CANARY_BINDING.setup_interface: PYTHON_CANARY_BINDING},
        )
        self.ledger_path = ledger_path
        self.overrides: dict[str, subprocess.CompletedProcess[str]] = {}
        self.action_calls: list[str] = []
        self.on_dispatch = lambda _key: None

    def dispatch(self, key: str, **_kwargs: object):
        if key == setup_dispatches.GETTER_KEY:
            return subprocess.CompletedProcess([], 0, f"{self.ledger_path}\n", "")
        self.action_calls.append(key)
        self.on_dispatch(key)
        if key in self.overrides:
            return self.overrides[key]
        entry = {
            "setup": python_canary.SetupInterface,
            "setup-status": python_canary.SetupStatusInterface,
            "teardown": python_canary_teardown.TeardownInterface,
            "teardown-status": python_canary_teardown.TeardownStatusInterface,
        }[self.dispatches[key].interface]
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_python_machine_interface(entry(), [])
        return subprocess.CompletedProcess([], code, output.getvalue(), "")


def _fixture_controller(tmp_path: Path) -> tuple[manager.SetupManager, FixtureRuntime]:
    python_canary.reset_state()
    python_canary_teardown.reset_state()
    runtime = FixtureRuntime(tmp_path / "private" / "state" / "ledger.json")
    return runtime.build_manager(argparse.Namespace(target_interface="unused")), runtime


class DispatchHarness:
    """Specific external-boundary fake; response queues mirror CompletedProcess."""

    def __init__(self) -> None:
        self.responses: dict[str, list[subprocess.CompletedProcess[str]]] = {}
        self.calls: list[tuple[str, tuple[str, ...], str | None]] = []

    def queue(self, key: str, stdout: str, *, returncode: int = 0, stderr: str = "") -> None:
        self.responses.setdefault(key, []).append(
            subprocess.CompletedProcess([], returncode, stdout, stderr)
        )

    def __call__(
        self, key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((key, tuple(args), stdin))
        return self.responses[key].pop(0)


def _controller(
    tmp_path: Path,
    graph: SimpleNamespace,
    dispatch: DispatchHarness,
    *bindings: setup_dispatches.ManagedInterfaceBinding,
) -> manager.SetupManager:
    return manager.SetupManager(
        graph=graph,
        store=_store(tmp_path),
        dispatch=dispatch,
        bindings={binding.setup_interface: binding for binding in bindings},
        new_flow_id=lambda: "flow-1",
    )


def _begin_setup(controller: manager.SetupManager, item: ManagedSetup) -> dict[str, object]:
    code, payload = controller.begin(
        "setup",
        item.setup_interface,
        "original-caller",
        item.setup_interface.removesuffix("setup") + "run",
        1,
    )
    assert code == 0
    return payload


def _seed_ready(store: state.LedgerStore, item: ManagedSetup, *roots: str) -> None:
    store.update(
        lambda _ledger: state.SetupLedger(
            interfaces={
                item.setup_interface: state.SetupReceipt(1, frozenset(roots))
            },
            active_flow=None,
        )
    )


def _seed_all(store: state.LedgerStore, *items: ManagedSetup, schema_version: int = 2) -> None:
    store.update(lambda _: state.SetupLedger({item.setup_interface: state.SetupReceipt(1, frozenset()) for item in items}, None, schema_version))

def test_teardown_all_preflights_then_dispatches_dependents_first(tmp_path: Path) -> None:
    leaf, notes, root = _managed("leaf"), _managed("notes", kind="markdown"), _managed("root")
    graph = _graph(leaf, notes, root)
    graph.setup_requirements[root.setup_interface] = ((notes.setup_interface, 1),)
    graph.setup_requirements[notes.setup_interface] = ((leaf.setup_interface, 1),)
    bindings = (_binding(leaf), _binding(notes), _binding(root))
    dispatch = DispatchHarness()
    for binding in reversed(bindings):
        if binding.setup_kind == "python":
            dispatch.queue(binding.teardown_dispatch_key, "")
        dispatch.queue(binding.teardown_verifier_dispatch_key, '{"torn_down":true}\n')
    controller = _controller(tmp_path, graph, dispatch, *bindings)
    _seed_all(controller.store, leaf, notes, root)
    code, payload = controller.teardown_all()
    assert (code, payload["state"], payload["instructions"]) == (0, "awaiting-settlement", bindings[1].teardown_instructions)
    assert controller.settle("flow-1", notes.teardown_interface)[1]["state"] == "ready"
    assert [call[0] for call in dispatch.calls] == ["root-teardown", "root-teardown-status", "notes-teardown-status", "leaf-teardown", "leaf-teardown-status"]
    assert controller.store.read() == state.SetupLedger.empty()
    assert _controller(tmp_path / "empty", _graph(), DispatchHarness()).teardown_all()[0] == 0

@pytest.mark.parametrize("case", ["unknown", "stale", "missing", "arguments"])
@pytest.mark.parametrize("schema_version", [1, 2])
def test_teardown_all_preflight_failures_preserve_exact_bytes(tmp_path: Path, case: str, schema_version: int) -> None:
    item = _managed("canary")
    graph, binding = _graph(item), _binding(item)
    if case == "unknown":
        graph = _graph()
    elif case == "stale":
        item = replace(item, setup_version=2)
        graph = _graph(item)
    elif case == "arguments":
        binding = replace(binding, arguments=(setup_dispatches.ManagedArgument("x", position=0),))
    bindings = () if case == "missing" else (binding,)
    controller = _controller(tmp_path, graph, DispatchHarness(), *bindings)
    receipt = state.SetupReceipt(1, frozenset())
    controller.store.update(lambda _: state.SetupLedger({"canary.interface.setup": receipt}, None, schema_version))
    path = tmp_path / "private" / "state" / "ledger.json"
    before = path.read_bytes()
    code, payload = controller.teardown_all()
    assert (code, payload["state"]) == (2, "failed")
    assert path.read_bytes() == before
    assert controller._dispatch.calls == []

@pytest.mark.parametrize(("action_code", "verifier", "expected"), [
    (7, None, "failed"), (0, '{"torn_down":false}\n', "failed"), (0, '{"torn_down":1}\n', "recovery-required"), (0, "conflict", "recovery-required")
])
def test_teardown_all_failures_retain_the_current_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action_code: int, verifier: str | None, expected: str) -> None:
    item, dispatch = _managed("canary"), DispatchHarness()
    binding = _binding(item)
    dispatch.queue(binding.teardown_dispatch_key, "", returncode=action_code)
    if verifier == "conflict":
        monkeypatch.setitem(manager.SetupManager._settle_verified.__globals__, "record_teardown_all_success", lambda *_args: (_ for _ in ()).throw(state.LedgerConflict("race")))
    if verifier is not None:
        dispatch.queue(binding.teardown_verifier_dispatch_key, '{"torn_down":true}\n' if verifier == "conflict" else verifier)
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _seed_all(controller.store, item)
    code, payload = controller.teardown_all()
    assert (code, payload["state"]) == (2, expected)
    assert set(controller.store.read().interfaces) == {item.setup_interface}
    assert controller.store.read().active_flow is not None

def test_teardown_all_retry_verifies_first_and_cancel_is_tri_state(tmp_path: Path) -> None:
    item, later, dispatch, binding, later_binding = _managed("canary"), _managed("later"), DispatchHarness(), _binding(_managed("canary")), _binding(_managed("later"))
    for current, verifier in ((later_binding, '{"torn_down":true}\n'), (binding, "bad")):
        dispatch.queue(current.teardown_dispatch_key, ""); dispatch.queue(current.teardown_verifier_dispatch_key, verifier)
    dispatch.queue(binding.teardown_verifier_dispatch_key, '{"torn_down":true}\n')
    controller = _controller(tmp_path, _graph(item, later), dispatch, binding, later_binding); _seed_all(controller.store, item, later)
    code, interrupted = controller.teardown_all()
    assert (code, interrupted["state"], interrupted["current_step"]["interface"], controller.store.read().active_flow.current_step) == (2, "recovery-required", item.teardown_interface, item.setup_interface)
    assert controller.recover("flow-1", "retry")[1]["state"] == "ready"
    assert [call[0] for call in dispatch.calls].count("canary-teardown") == 1
    outcomes = (('{"torn_down":true}\n', True, False, "", "cancel"), ('{"torn_down":false}\n', False, False, "", "cancel"), ('{"torn_down":0}\n', False, True, "", "cancel"), ('{"torn_down":true}\n', False, True, "", "cancel"), ("unused", False, True, "graph", "retry"), ("unused", False, True, "binding", "cancel"), ("unused", False, True, "graph", "settle"))
    for index, (verifier, removed, active, mutation, action) in enumerate(outcomes):
        case_dispatch = DispatchHarness()
        case_dispatch.queue(binding.teardown_verifier_dispatch_key, verifier)
        case = _controller(tmp_path / str(index), _graph(item), case_dispatch, binding)
        _seed_all(case.store, item)
        flow = state.ActiveFlow("flow-1", "teardown-all", None, item.setup_interface, (), None)
        case.store.update(lambda ledger: state.begin_flow(ledger, flow))
        if index == 3:
            original = case.store.update
            def race(transform):
                original(lambda ledger: state.SetupLedger({**ledger.interfaces, "foreign.interface.setup": state.SetupReceipt(1, frozenset())}, ledger.active_flow))
                return original(transform)
            case.store.update = race
        if mutation: (case.graph.managed_setups if mutation == "graph" else case._bindings).clear()
        code, payload = case.settle("flow-1", item.teardown_interface) if action == "settle" else case.recover("flow-1", action)
        assert (item.setup_interface not in case.store.read().interfaces) is removed
        assert (case.store.read().active_flow is not None) is active
        assert payload["state"] == ("recovery-required" if active else "ready")
def test_teardown_all_revalidates_races_and_marks_stale_recovery(tmp_path: Path) -> None:
    item, later = _managed("canary"), _managed("later")
    dispatch = DispatchHarness()
    controller = _controller(tmp_path, _graph(item, later), dispatch, _binding(item))
    _seed_all(controller.store, item)
    original_update = controller.store.update
    def racing_update(transform):
        receipts = {owned.setup_interface: state.SetupReceipt(1, frozenset()) for owned in (item, later)}
        original_update(lambda _: state.SetupLedger(receipts, None))
        return original_update(transform)
    controller.store.update = racing_update
    assert controller.teardown_all()[1]["state"] == "failed"
    assert dispatch.calls == []
    controller.store.update = original_update
    controller._bindings[later.setup_interface] = _binding(later)
    original_update(lambda ledger: state.begin_flow(ledger, state.ActiveFlow("ordinary", "setup", item.setup_interface, later.setup_interface, (), state.ContinuationIdentity("caller", "target", 1))))
    busy = controller.teardown_all()[1]; assert (busy["state"], busy["original"], busy["resume_original"]) == ("busy", None, False); original_update(lambda ledger: state.SetupLedger(ledger.interfaces, replace(ledger.active_flow, flow_id="flow-1", operation="teardown-all", root=None, continuation=None)))
    controller._dispatch = lambda *_args, **_kwargs: (original_update(lambda ledger: state.SetupLedger(ledger.interfaces, replace(ledger.active_flow, current_step=item.setup_interface))), subprocess.CompletedProcess([], 0, '{"torn_down":false}\n', ""))[1]
    assert controller.recover("flow-1", "retry")[1]["state"] == "recovery-required"


def test_status_and_authorize_are_read_only_then_ready_only_claiming(tmp_path: Path) -> None:
    """Catches status mutating claims or authorization resuming a pending target."""
    item = _managed("canary")
    dispatch = DispatchHarness()
    controller = _controller(tmp_path, _graph(item), dispatch, _binding(item))

    code, pending = controller.status("canary.interface.run")
    assert code == 0
    assert pending == {
        "schema_version": 1,
        "code": "setup_required",
        "root_setup_interface": "canary.interface.setup",
        "pending_stack": [
            {
                "interface": "canary.interface.setup",
                "version": 1,
                "kind": "python",
                "action": "run-setup",
            }
        ],
        "flow_id": None,
    }
    code, refused = controller.authorize(
        "canary.interface.run", "caller", "canary.interface.run", 1
    )
    assert code == 2
    assert refused["state"] == "failed"
    assert refused["resume_original"] is False

    _seed_ready(controller.store, item)
    code, ready = controller.authorize(
        "canary.interface.run", "caller", "canary.interface.run", 1
    )
    assert code == 0
    assert ready["state"] == "ready"
    assert ready["resume_original"] is True
    assert controller.store.read().interfaces[item.setup_interface].required_by == {
        item.setup_interface
    }


def test_unmanaged_authorization_resumes_without_writing_a_claim(tmp_path: Path) -> None:
    """Catches the manager blocking an ordinary interface that never opted in."""
    graph = _graph()
    graph.exports["plain.interface.run"] = SimpleNamespace(module_node_id="plain")
    graph.module_parents["plain"] = None
    controller = _controller(tmp_path, graph, DispatchHarness())

    code, payload = controller.authorize(
        "plain.interface.run", "caller", "plain.interface.run", 1
    )

    assert code == 0
    assert payload["state"] == "ready"
    assert payload["resume_original"] is True
    assert controller.store.read().interfaces == {}


def test_begin_enforces_one_active_flow_and_redacts_request_data(tmp_path: Path) -> None:
    """Catches parallel flows or secret request data entering the ledger/response."""
    item = _managed("canary")
    controller = _controller(tmp_path, _graph(item), DispatchHarness(), _binding(item))

    first = _begin_setup(controller, item)
    code, busy = controller.begin(
        "setup", item.setup_interface, "other", "other.interface.run", 1
    )

    assert first["state"] == "run-step"
    assert first["current_step"]["interface"] == item.setup_interface
    assert first["original"] == {
        "caller": "original-caller",
        "interface": "canary.interface.run",
        "version": 1,
    }
    assert code == 2
    assert busy["state"] == "busy"
    raw = state.encode_ledger(controller.store.read())
    assert b"secret" not in raw
    assert b"stdin" not in raw


def test_begin_setup_claims_the_exact_ready_prefix_before_suffix_settlement(
    tmp_path: Path,
) -> None:
    """Catches a shared or stale-prefix setup flow becoming impossible to settle."""
    leaf = _managed("leaf")
    root = _managed("root")
    graph = _graph(leaf, root)
    graph.setup_requirements[root.setup_interface] = ((leaf.setup_interface, 1),)
    dispatch = DispatchHarness()
    root_binding = _binding(root)
    dispatch.queue(root_binding.setup_dispatch_key, "")
    dispatch.queue(root_binding.setup_verifier_dispatch_key, '{"set_up":true}\n')
    controller = _controller(
        tmp_path, graph, dispatch, _binding(leaf), root_binding
    )
    _seed_ready(controller.store, leaf, "other.interface.setup")

    begun = _begin_setup(controller, root)

    assert begun["current_step"]["interface"] == root.setup_interface
    active = controller.store.read().active_flow
    assert active is not None
    assert active.verified_steps == (leaf.setup_interface,)
    assert controller.store.read().interfaces[leaf.setup_interface].required_by == {
        "other.interface.setup",
        root.setup_interface,
    }

    code, completed = controller.run_python(
        "flow-1", root.setup_interface, "{}"
    )
    assert code == 0
    assert completed["state"] == "ready"


def test_python_run_requires_exact_step_runs_verifier_then_records(tmp_path: Path) -> None:
    """Catches interface substitution or receipt writes before verifier success."""
    item = _managed("canary")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.setup_dispatch_key, "runner output\n")
    dispatch.queue(binding.setup_verifier_dispatch_key, '{"set_up":true}\n')
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _begin_setup(controller, item)

    code, wrong = controller.run_python("flow-1", "other.interface.setup", "{}")
    assert code == 2
    assert wrong["state"] == "failed"
    assert controller.store.read().interfaces == {}

    code, completed = controller.run_python("flow-1", item.setup_interface, "{}")
    assert code == 0
    assert completed["state"] == "ready"
    assert controller.store.read().interfaces[item.setup_interface] == state.SetupReceipt(
        1, frozenset({item.setup_interface})
    )
    assert dispatch.calls == [
        (binding.setup_dispatch_key, (), None),
        (binding.setup_verifier_dispatch_key, (), None),
    ]


@pytest.mark.parametrize(
    ("runner_code", "verifier_stdout", "expected_state"),
    [
        (7, '{"set_up":true}\n', "failed"),
        (0, '{"set_up":false}\n', "failed"),
        (0, '{"set_up":true,"extra":1}\n', "recovery-required"),
    ],
)
def test_python_failure_never_records_a_receipt(
    tmp_path: Path, runner_code: int, verifier_stdout: str, expected_state: str
) -> None:
    """Catches nonzero, false, or malformed evidence being treated as completion."""
    item = _managed("canary")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.setup_dispatch_key, "", returncode=runner_code)
    if runner_code == 0:
        dispatch.queue(binding.setup_verifier_dispatch_key, verifier_stdout)
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _begin_setup(controller, item)

    code, payload = controller.run_python("flow-1", item.setup_interface, "{}")

    assert code == 2
    assert payload["state"] == expected_state
    assert controller.store.read().interfaces == {}
    assert controller.store.read().active_flow is not None


def test_registered_python_fixture_has_fixed_action_and_verifier_bindings() -> None:
    """Catches an unregistered canary or a caller-selectable lifecycle action."""
    graph = load_repository_blueprint_graph(FIXTURE_REPO)

    assert graph.managed_setups == {
        "python-canary.interface.setup": ManagedSetup(
            setup_interface="python-canary.interface.setup",
            setup_version=1,
            teardown_interface="python-canary.interface.teardown",
            teardown_version=1,
            setup_verifier_interface="python-canary.interface.setup-status",
            setup_verifier_version=1,
            teardown_verifier_interface="python-canary.interface.teardown-status",
            teardown_verifier_version=1,
            kind="python",
        )
    }
    assert {
        interface_id: export.declaration["process_binding"]["entry"]
        for interface_id, export in graph.exports.items()
    } == {
        "python-canary.interface.setup": "SetupInterface",
        "python-canary.interface.setup-status": "SetupStatusInterface",
        "python-canary.interface.teardown": "TeardownInterface",
        "python-canary.interface.teardown-status": "TeardownStatusInterface",
    }
    assert {
        key: call.target_interface_id for key, call in PYTHON_CANARY_CALLS.items()
    } == {
        "python-canary-setup": "python-canary.interface.setup",
        "python-canary-setup-status": "python-canary.interface.setup-status",
        "python-canary-teardown": "python-canary.interface.teardown",
        "python-canary-teardown-status": "python-canary.interface.teardown-status",
    }


def test_registered_python_fixture_runs_and_verifies_before_receipt_mutation(
    tmp_path: Path,
) -> None:
    """Catches opposite-action dispatch or receipt mutation before verification."""
    controller, runtime = _fixture_controller(tmp_path)
    item = controller.graph.managed_setups["python-canary.interface.setup"]
    begun = _begin_setup(controller, item)
    flow_id = begun["flow_id"]
    assert isinstance(flow_id, str)
    observed_setup_receipts: list[dict[str, state.SetupReceipt]] = []
    observed_teardown_receipts: list[dict[str, state.SetupReceipt]] = []

    def observe_receipts(key: str) -> None:
        if key == "python-canary-setup-status":
            observed_setup_receipts.append(
                dict(controller.store.read().interfaces)
            )
        if key == "python-canary-teardown-status":
            observed_teardown_receipts.append(
                dict(controller.store.read().interfaces)
            )

    runtime.on_dispatch = observe_receipts

    code, wrong = controller.run_python(
        flow_id, "python-canary.interface.teardown", "{}"
    )
    assert code == 2
    assert wrong["state"] == "failed"
    assert runtime.action_calls == []
    assert controller.store.read().interfaces == {}

    code, completed = controller.run_python(
        flow_id, "python-canary.interface.setup", "{}"
    )
    assert code == 0
    assert completed["state"] == "ready"
    assert observed_setup_receipts == [{}]
    assert controller.store.read().interfaces == {
        "python-canary.interface.setup": state.SetupReceipt(
            1, frozenset({"python-canary.interface.setup"})
        )
    }
    assert runtime.action_calls == [
        "python-canary-setup",
        "python-canary-setup-status",
    ]

    code, begun = controller.begin(
        "teardown",
        item.setup_interface,
        "fixture-caller",
        item.teardown_interface,
        1,
    )
    assert code == 0
    assert begun["current_step"]["interface"] == item.teardown_interface
    teardown_flow_id = begun["flow_id"]
    assert isinstance(teardown_flow_id, str)
    code, wrong = controller.run_python(
        teardown_flow_id, item.setup_interface, "{}"
    )
    assert code == 2
    assert wrong["state"] == "failed"
    assert runtime.action_calls == [
        "python-canary-setup",
        "python-canary-setup-status",
    ]
    assert controller.store.read().interfaces == {
        "python-canary.interface.setup": state.SetupReceipt(
            1, frozenset({"python-canary.interface.setup"})
        )
    }

    code, completed = controller.run_python(
        teardown_flow_id, item.teardown_interface, "{}"
    )
    assert code == 0
    assert completed["state"] == "ready"
    assert observed_teardown_receipts == [
        {
            "python-canary.interface.setup": state.SetupReceipt(
                1, frozenset({"python-canary.interface.setup"})
            )
        }
    ]
    assert controller.store.read().interfaces == {}
    assert runtime.action_calls == [
        "python-canary-setup",
        "python-canary-setup-status",
        "python-canary-teardown",
        "python-canary-teardown-status",
    ]


@pytest.mark.parametrize(
    ("override_key", "result", "expected_state", "expected_calls"),
    [
        (
            "python-canary-setup",
            subprocess.CompletedProcess([], 9, "", "bounded failure"),
            "failed",
            ["python-canary-setup"],
        ),
        (
            "python-canary-setup-status",
            subprocess.CompletedProcess([], 0, '{"set_up":false}\n', ""),
            "failed",
            ["python-canary-setup", "python-canary-setup-status"],
        ),
        (
            "python-canary-setup-status",
            subprocess.CompletedProcess([], 0, "not-json\n", ""),
            "recovery-required",
            ["python-canary-setup", "python-canary-setup-status"],
        ),
    ],
)
def test_registered_python_fixture_failures_never_record_receipts(
    tmp_path: Path,
    override_key: str,
    result: subprocess.CompletedProcess[str],
    expected_state: str,
    expected_calls: list[str],
) -> None:
    """Catches nonzero or invalid verifier evidence mutating readiness state."""
    controller, runtime = _fixture_controller(tmp_path)
    item = controller.graph.managed_setups["python-canary.interface.setup"]
    runtime.overrides[override_key] = result
    begun = _begin_setup(controller, item)
    flow_id = begun["flow_id"]
    assert isinstance(flow_id, str)

    code, payload = controller.run_python(flow_id, item.setup_interface, "{}")

    assert code == 2
    assert payload["state"] == expected_state
    assert runtime.action_calls == expected_calls
    assert controller.store.read().interfaces == {}
    assert controller.store.read().active_flow is not None


def test_action_dispatch_invocation_error_is_redacted_recovery(
    tmp_path: Path,
) -> None:
    """Catches a real dispatch failure escaping the recovery protocol."""
    item = _managed("canary")
    binding = _binding(item)
    secret = "do-not-echo-action-dispatch-secret"

    def failing_dispatch(
        key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        raise InvocationError(secret)

    controller = _controller(tmp_path, _graph(item), failing_dispatch, binding)
    _begin_setup(controller, item)

    code, payload = controller.run_python("flow-1", item.setup_interface, "{}")

    assert code == 2
    assert payload["state"] == "recovery-required"
    assert secret not in json.dumps(payload)
    assert controller.store.read().active_flow is not None


def test_action_dispatch_does_not_hide_an_arbitrary_programmer_error(
    tmp_path: Path,
) -> None:
    """Catches broad action-boundary translation hiding a code defect."""
    item = _managed("canary")
    binding = _binding(item)

    def failing_dispatch(
        key: str, *, args: tuple[str, ...] = (), stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("action dispatch programmer defect")

    controller = _controller(tmp_path, _graph(item), failing_dispatch, binding)
    _begin_setup(controller, item)

    with pytest.raises(RuntimeError, match="action dispatch programmer defect"):
        controller.run_python("flow-1", item.setup_interface, "{}")


def test_markdown_returns_exact_instructions_then_settle_verifies(tmp_path: Path) -> None:
    """Catches Markdown completion from caller assertion instead of its verifier."""
    item = _managed("notes", kind="markdown")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.setup_verifier_dispatch_key, '{"set_up":true}\n')
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _begin_setup(controller, item)

    code, instruction = controller.run_markdown("flow-1", item.setup_interface)
    assert code == 0
    assert instruction["state"] == "awaiting-settlement"
    assert instruction["instructions"] == binding.setup_instructions
    assert controller.store.read().interfaces == {}

    code, settled = controller.settle("flow-1", item.setup_interface)
    assert code == 0
    assert settled["state"] == "ready"
    assert controller.store.read().interfaces[item.setup_interface].version == 1


def test_teardown_verifies_before_removal_and_finishes_without_resume(tmp_path: Path) -> None:
    """Catches deleting a claim before teardown verification or resuming an ordinary call."""
    item = _managed("canary")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.teardown_dispatch_key, "")
    dispatch.queue(binding.teardown_verifier_dispatch_key, '{"torn_down":true}\n')
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _seed_ready(controller.store, item, item.setup_interface)

    code, begun = controller.begin(
        "teardown", item.setup_interface, "caller", item.teardown_interface, 1
    )
    assert code == 0
    assert begun["current_step"]["interface"] == item.teardown_interface

    code, done = controller.run_python("flow-1", item.teardown_interface, "{}")
    assert code == 0
    assert done["state"] == "ready"
    assert done["resume_original"] is False
    assert item.setup_interface not in controller.store.read().interfaces


def test_shared_teardown_releases_claim_without_running_external_action(tmp_path: Path) -> None:
    """Catches tearing down a dependency which another root still claims."""
    item = _managed("shared")
    binding = _binding(item)
    controller = _controller(tmp_path, _graph(item), DispatchHarness(), binding)
    _seed_ready(controller.store, item, item.setup_interface, "other.interface.setup")

    code, payload = controller.begin(
        "teardown", item.setup_interface, "caller", item.teardown_interface, 1
    )

    assert code == 0
    assert payload["state"] == "ready"
    assert controller.store.read().interfaces[item.setup_interface].required_by == {
        "other.interface.setup"
    }


def test_recover_retry_verifies_first_and_cancel_drops_ghost_claim(tmp_path: Path) -> None:
    """Catches retry rerunning verified work or cancel retaining an incomplete root claim."""
    leaf = _managed("leaf")
    root = _managed("root")
    graph = _graph(leaf, root)
    graph.setup_requirements[root.setup_interface] = ((leaf.setup_interface, 1),)
    leaf_binding = _binding(leaf)
    root_binding = _binding(root)
    dispatch = DispatchHarness()
    dispatch.queue(leaf_binding.setup_verifier_dispatch_key, '{"set_up":true}\n')
    controller = _controller(tmp_path, graph, dispatch, leaf_binding, root_binding)
    _begin_setup(controller, root)

    code, retried = controller.recover("flow-1", "retry")
    assert code == 0
    assert retried["state"] == "run-step"
    assert retried["current_step"]["interface"] == root.setup_interface
    assert controller.store.read().interfaces[leaf.setup_interface].required_by == {
        root.setup_interface
    }

    code, cancelled = controller.recover("flow-1", "cancel")
    assert code == 0
    assert cancelled["state"] == "ready"
    assert cancelled["resume_original"] is False
    assert controller.store.read().interfaces[leaf.setup_interface].required_by == frozenset()
    assert controller.store.read().active_flow is None


def test_recover_retry_finishes_an_interrupted_claim_only_teardown_without_verifier(
    tmp_path: Path,
) -> None:
    """Catches recovery dispatching teardown for a step that only releases a shared claim."""
    item = _managed("shared")
    binding = _binding(item)
    controller = _controller(tmp_path, _graph(item), DispatchHarness(), binding)
    _seed_ready(controller.store, item, item.setup_interface, "other.interface.setup")
    interrupted = state.ActiveFlow(
        flow_id="flow-1",
        operation="teardown",
        root=item.setup_interface,
        current_step=item.setup_interface,
        verified_steps=(),
        continuation=state.ContinuationIdentity(
            "caller", item.teardown_interface, 1
        ),
    )
    controller.store.update(lambda ledger: state.begin_flow(ledger, interrupted))

    code, payload = controller.recover("flow-1", "retry")

    assert code == 0
    assert payload["state"] == "ready"
    assert controller.store.read().interfaces[item.setup_interface].required_by == {
        "other.interface.setup"
    }
    assert controller.store.read().active_flow is None


def test_invalidate_reports_removed_receipts_and_refuses_while_busy(tmp_path: Path) -> None:
    """Catches invalidation racing a flow or silently retaining the selected receipt."""
    item = _managed("canary")
    controller = _controller(tmp_path, _graph(item), DispatchHarness(), _binding(item))
    _seed_ready(controller.store, item, item.setup_interface)

    code, invalidated = controller.invalidate(item.setup_interface)
    assert code == 0
    assert invalidated["removed"] == [item.setup_interface]
    assert controller.store.read().interfaces == {}

    _begin_setup(controller, item)
    code, refused = controller.invalidate(item.setup_interface)
    assert code == 2
    assert refused["state"] == "busy"


@pytest.mark.parametrize(
    ("interface_type", "argv"),
    [
        (manager.StatusInterface, []),
        (manager.AuthorizeInterface, ["target", "caller", "interface", "zero"]),
        (manager.BeginInterface, ["invalid", "root", "caller", "interface", "1"]),
        (manager.RunMarkdownInterface, ["flow"]),
        (manager.RunPythonInterface, ["flow", "interface", "extra"]),
        (manager.SettleInterface, ["flow"]),
        (manager.InvalidateInterface, []),
        (manager.RecoverInterface, ["flow", "accept-verified"]),
    ],
)
def test_public_routes_return_exit_64_for_every_malformed_signature(
    capsys: pytest.CaptureFixture[str], interface_type: type, argv: list[str]
) -> None:
    """Catches argparse's exit 2 leaking instead of the manager's malformed-call code."""
    code = run_python_machine_interface(interface_type(), argv)
    payload = json.loads(capsys.readouterr().out)

    assert code == 64
    assert payload["state"] == "failed"
    assert payload["resume_original"] is False


def test_run_python_rejects_malformed_or_undeclared_stdin_without_echoing_it(tmp_path: Path) -> None:
    """Catches arbitrary runner arguments or secret stdin reaching output/state."""
    item = _managed("canary")
    controller = _controller(tmp_path, _graph(item), DispatchHarness(), _binding(item))
    _begin_setup(controller, item)

    for raw in ('{"secret":"do-not-echo"}', "not-json"):
        code, payload = controller.run_python("flow-1", item.setup_interface, raw)
        encoded = json.dumps(payload)
        assert code == 64
        assert "do-not-echo" not in encoded
        assert "secret" not in encoded
    assert controller.store.read().interfaces == {}


def test_runtime_getter_is_canonical_and_captures_one_absolute_path(tmp_path: Path) -> None:
    """Catches a public path seam, arbitrary getter dispatch, or multi-line path capture."""
    assert setup_dispatches.GETTER_CALL.caller_module_id == "setup-interface-manager._rtx"
    assert setup_dispatches.GETTER_CALL.target_module_id == "common"
    assert setup_dispatches.GETTER_CALL.interface == "famulus-paths-get"
    assert setup_dispatches.GETTER_CALL.smoke_args == ("setup-status",)

    class Runtime(manager.BeginInterface):
        def __init__(self, stdout: str) -> None:
            super().__init__(graph_loader=lambda _root: _graph())
            self.stdout = stdout
            self.calls = []

        def dispatch(self, key: str, **kwargs: object):
            self.calls.append((key, kwargs))
            return subprocess.CompletedProcess([], 0, self.stdout, "")

    ledger_path = tmp_path / "private" / "state" / "ledger.json"
    runtime = Runtime(f"{ledger_path}\n")
    built = runtime.build_manager(argparse.Namespace())
    assert built.store.read() == state.SetupLedger.empty()
    assert runtime.calls == [
        (setup_dispatches.GETTER_KEY, {"args": ("setup-status",), "text": True})
    ]

    with pytest.raises(state.LedgerPathError):
        Runtime(f"{ledger_path}\n{tmp_path / 'other'}\n").build_manager(
            argparse.Namespace(target_interface="unused")
        )


@pytest.mark.parametrize(
    ("interface_type", "argv", "expected_code"),
    [
        (manager.StatusInterface, ["canary.interface.run"], 0),
        (
            manager.AuthorizeInterface,
            ["canary.interface.run", "caller", "canary.interface.run", "1"],
            2,
        ),
    ],
)
def test_status_and_authorize_load_one_route_local_graph_from_parsed_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    interface_type: type,
    argv: list[str],
    expected_code: int,
) -> None:
    """Catches either hot path loading the canonical graph or using unparsed input."""
    item = _managed("canary")
    sparse_graph = _graph(item)
    configuration = object()
    config_path = tmp_path / "repository" / "officina.toml"
    config_calls: list[Path] = []
    direct_calls: list[tuple[object, str]] = []

    def load_configuration(path: Path) -> object:
        config_calls.append(path)
        return configuration

    def load_direct(config: object, target_interface: str):
        direct_calls.append((config, target_interface))
        return sparse_graph

    graph_globals = manager.StatusInterface.build_graph.__globals__
    monkeypatch.setitem(
        graph_globals, "load_repository_configuration", load_configuration
    )
    monkeypatch.setitem(graph_globals, "load_direct_setup_graph", load_direct)

    class Runtime(interface_type):
        def __init__(self) -> None:
            super().__init__(
                graph_loader=lambda _root: pytest.fail(
                    "hot path called canonical graph loader"
                ),
                bindings={item.setup_interface: _binding(item)},
            )

        def dispatch(self, key: str, **_kwargs: object):
            assert key == setup_dispatches.GETTER_KEY
            return subprocess.CompletedProcess(
                [], 0, str(tmp_path / "private" / "state" / "ledger.json") + "\n", ""
            )

    runtime = Runtime()
    set_runtime_dispatch_context(
        runtime,
        repo_root=tmp_path / "wrong-root",
        repository_config=config_path,
    )

    code = run_python_machine_interface(runtime, argv)
    payload = json.loads(capsys.readouterr().out)

    assert code == expected_code
    if interface_type is manager.StatusInterface:
        assert payload["code"] == "setup_required"
    else:
        assert payload["state"] == "failed"
    assert config_calls == [config_path]
    assert direct_calls == [(configuration, "canary.interface.run")]


@pytest.mark.parametrize(
    ("interface_type", "argv"),
    [
        (
            manager.BeginInterface,
            ["setup", "canary.interface.setup", "caller", "target", "1"],
        ),
        (manager.InvalidateInterface, ["canary.interface.setup"]),
    ],
)
def test_lifecycle_routes_retain_the_canonical_full_graph_loader(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    interface_type: type,
    argv: list[str],
) -> None:
    """Catches sparse projection leaking into mutation and lifecycle operations."""
    item = _managed("canary")
    full_graph = _graph(item)
    repo_root = tmp_path / "canonical-repository"
    calls: list[Path] = []

    def load_full(path: Path):
        calls.append(path)
        return full_graph

    graph_globals = manager.StatusInterface.build_graph.__globals__
    monkeypatch.setitem(
        graph_globals,
        "load_direct_setup_graph",
        lambda *_args: pytest.fail("lifecycle route called direct graph loader"),
    )

    class Runtime(interface_type):
        def __init__(self) -> None:
            super().__init__(
                graph_loader=load_full,
                bindings={item.setup_interface: _binding(item)},
            )

        def dispatch(self, key: str, **_kwargs: object):
            assert key == setup_dispatches.GETTER_KEY
            return subprocess.CompletedProcess(
                [], 0, str(tmp_path / "private" / "state" / "ledger.json") + "\n", ""
            )

    runtime = Runtime()
    set_runtime_dispatch_context(
        runtime,
        repo_root=repo_root,
        repository_config=tmp_path / "must-not-be-used.toml",
    )

    code = run_python_machine_interface(runtime, argv)
    json.loads(capsys.readouterr().out)

    assert code == 0
    assert calls == [repo_root]


@pytest.mark.parametrize("route", ["status", "authorize"])
def test_direct_hot_paths_parse_before_selecting_a_graph(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    """Catches malformed calls touching repository state before exit 64."""
    graph_globals = manager.StatusInterface.build_graph.__globals__
    monkeypatch.setitem(
        graph_globals,
        "load_direct_setup_graph",
        lambda *_args: pytest.fail("malformed call selected a graph"),
    )
    interface = (
        manager.StatusInterface()
        if route == "status"
        else manager.AuthorizeInterface()
    )
    argv = [] if route == "status" else ["target", "caller", "interface", "zero"]

    code = run_python_machine_interface(interface, argv)

    assert code == 64
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_direct_hot_path_fails_closed_on_unavailable_repository_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """Catches ambient-root fallback or raw configuration diagnostics escaping."""
    item = _managed("canary")
    secret = "do-not-echo-config-secret"
    config_path = tmp_path / "officina.toml"
    config_path.write_text(
        f'schema_version = 1\n{secret.replace("-", "_")} = true\n',
        encoding="utf-8",
    )

    class Runtime(manager.StatusInterface):
        def __init__(self) -> None:
            super().__init__(
                graph_loader=lambda _root: _graph(item),
                bindings={item.setup_interface: _binding(item)},
            )

        def dispatch(self, key: str, **_kwargs: object):
            assert key == setup_dispatches.GETTER_KEY
            return subprocess.CompletedProcess(
                [], 0, str(tmp_path / "private" / "state" / "ledger.json") + "\n", ""
            )

    runtime = Runtime()
    if failure == "invalid":
        set_runtime_dispatch_context(runtime, repository_config=config_path)

    code = run_python_machine_interface(runtime, ["canary.interface.run"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 2
    assert payload["state"] == "recovery-required"
    assert payload["error"] == (
        "repository configuration is unavailable"
        if failure == "missing"
        else "repository blueprint graph is unavailable"
    )
    assert secret not in output


def test_logical_package_route_smoke_loads_the_real_manager(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Catches sibling imports that work only when `_rtx` is injected into sys.path."""
    code = run_python_machine_interface(LOGICAL_STATUS_INTERFACE, ["--route-smoke"])

    assert code == 0
    assert capsys.readouterr().out == "route-smoke ok\n"


def test_production_dispatch_map_includes_every_finite_binding_and_is_immutable() -> None:
    """Catches Task 7 bindings being invisible unless `_setup_manager.py` is edited too."""
    item = _managed("canary")
    binding = _binding(item)
    calls = {
        key: DispatchCall(
            caller_module_id="setup-interface-manager._rtx",
            target_module_id="canary",
            interface=interface,
            smoke_args=(),
        )
        for key, interface in (
            (binding.setup_dispatch_key, "setup"),
            (binding.setup_verifier_dispatch_key, "setup-status"),
            (binding.teardown_dispatch_key, "teardown"),
            (binding.teardown_verifier_dispatch_key, "teardown-status"),
        )
    }

    dispatches = setup_dispatches.production_dispatches(
        bindings={binding.setup_interface: binding}, action_calls=calls
    )

    assert dispatches == {setup_dispatches.GETTER_KEY: setup_dispatches.GETTER_CALL, **calls}
    with pytest.raises(TypeError):
        dispatches["arbitrary"] = setup_dispatches.GETTER_CALL
    assert manager._ManagerInterface.dispatches is setup_dispatches.PRODUCTION_DISPATCHES


def test_markdown_binding_dispatch_map_exposes_only_machine_run_verifiers() -> None:
    """Catches route smoke compiling Markdown instructions as process calls."""
    binding = _binding(_managed("canary", kind="markdown"))
    calls = {
        key: DispatchCall(
            caller_module_id="setup-interface-manager._rtx",
            target_module_id="canary",
            interface=interface,
            smoke_args=(),
        )
        for key, interface in (
            (binding.setup_dispatch_key, "setup"),
            (binding.setup_verifier_dispatch_key, "setup-status"),
            (binding.teardown_dispatch_key, "teardown"),
            (binding.teardown_verifier_dispatch_key, "teardown-status"),
        )
    }

    dispatches = setup_dispatches.production_dispatches(
        bindings={binding.setup_interface: binding}, action_calls=calls
    )

    assert set(dispatches) == {
        setup_dispatches.GETTER_KEY,
        binding.setup_verifier_dispatch_key,
        binding.teardown_verifier_dispatch_key,
    }


@pytest.mark.parametrize(
    "failure",
    ["getter-invocation-error", "getter-nonzero", "path", "graph"],
)
def test_bootstrap_domain_failures_return_one_redacted_exit_2_object(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """Catches bootstrap exceptions escaping as tracebacks or leaking boundary details."""
    secret = "do-not-echo-bootstrap-secret"
    ledger_path = tmp_path / "private" / "state" / "ledger.json"

    class Runtime(manager.BeginInterface):
        def __init__(self) -> None:
            super().__init__(graph_loader=self._load_graph)

        def _load_graph(self, _root: Path):
            if failure == "graph":
                raise BlueprintGraphError(secret)
            return _graph()

        def dispatch(self, key: str, **kwargs: object):
            if failure == "getter-invocation-error":
                raise InvocationError(secret)
            stdout = secret if failure == "path" else str(ledger_path)
            return subprocess.CompletedProcess(
                [], 9 if failure == "getter-nonzero" else 0, stdout + "\n", secret
            )

    code = run_python_machine_interface(
        Runtime(), ["setup", "canary.interface.setup", "caller", "target", "1"]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 2
    assert output.count("\n") == 1
    assert secret not in output
    assert payload["operation"] == "begin"
    assert payload["state"] == "recovery-required"
    assert payload["flow_id"] is None
    assert payload["current_step"] is None
    assert payload["original"] is None


def test_bootstrap_does_not_hide_an_arbitrary_programmer_error(tmp_path: Path) -> None:
    """Catches a broad exception handler converting code defects into domain JSON."""
    class Runtime(manager.BeginInterface):
        def __init__(self) -> None:
            super().__init__(graph_loader=self._load_graph)

        def _load_graph(self, _root: Path):
            raise RuntimeError("programmer defect")

        def dispatch(self, key: str, **kwargs: object):
            return subprocess.CompletedProcess(
                [], 0, str(tmp_path / "ledger.json") + "\n", ""
            )

    with pytest.raises(RuntimeError, match="programmer defect"):
        run_python_machine_interface(
            Runtime(),
            ["setup", "canary.interface.setup", "caller", "target", "1"],
        )


def test_bootstrap_does_not_hide_an_arbitrary_dispatch_programmer_error() -> None:
    """Catches broad dispatch exception translation hiding a code defect."""
    class Runtime(manager.StatusInterface):
        def dispatch(self, key: str, **kwargs: object):
            raise RuntimeError("dispatch programmer defect")

    with pytest.raises(RuntimeError, match="dispatch programmer defect"):
        run_python_machine_interface(Runtime(), ["plain.interface.run"])


@pytest.mark.parametrize("route", ["settle", "recover"])
def test_verifier_recovery_preserves_the_known_active_flow_context(
    tmp_path: Path, route: str
) -> None:
    """Catches recovery responses erasing the active step needed for retry or cancel."""
    item = _managed("notes", kind="markdown")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.setup_verifier_dispatch_key, '{"unexpected":true}\n')
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _begin_setup(controller, item)

    if route == "settle":
        code, payload = controller.settle("flow-1", item.setup_interface)
    else:
        code, payload = controller.recover("flow-1", "retry")

    assert code == 2
    assert payload["state"] == "recovery-required"
    assert payload["flow_id"] == "flow-1"
    assert payload["operation"] == "setup"
    assert payload["current_step"] == {
        "interface": item.setup_interface,
        "version": 1,
        "kind": "markdown",
        "action": "run-setup",
    }
    assert payload["original"] == {
        "caller": "original-caller",
        "interface": "notes.interface.run",
        "version": 1,
    }
    assert controller.store.read().active_flow is not None


def test_public_interface_output_is_one_json_object_and_stdin_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catches raw stdin appearing in the public machine response."""
    item = _managed("canary")
    binding = _binding(item)
    dispatch = DispatchHarness()
    dispatch.queue(binding.setup_dispatch_key, "", returncode=9)
    controller = _controller(tmp_path, _graph(item), dispatch, binding)
    _begin_setup(controller, item)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"password":"redacted"}'))

    interface = manager.RunPythonInterface(manager_factory=lambda: controller)
    code = run_python_machine_interface(interface, ["flow-1", item.setup_interface])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 64
    assert output.count("\n") == 1
    assert "password" not in output
    assert "redacted" not in output
    assert payload["state"] == "failed"


def test_teardown_all_interface_is_exact_zero_argument_adapter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    controller = _controller(tmp_path, _graph(), DispatchHarness())
    interface = manager.TeardownAllInterface(manager_factory=lambda: controller)
    assert run_python_machine_interface(interface, []) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["original"], payload["resume_original"]) == (None, False)
    assert run_python_machine_interface(interface, ["unexpected"]) == 64


def test_registered_manager_is_hidden_and_only_workflow_activated() -> None:
    """Catches generic setup prose or user requests activating the hidden controller."""
    graph = load_repository_blueprint_graph(REPO_ROOT)
    node = graph.nodes["setup-interface-manager"]

    assert node.declaration["discovery"] == {
        "mechanism": "skill",
        "catalog": {
            "domain": "assistant-operations",
            "topics": ["task-automation"],
            "visibility": "hidden",
        },
        "activated_by": ["skill-workflow"],
        "persistent_modifier": False,
    }
    assert set(
        graph.namespace_routes[
            ("setup-interface-manager", "setup-interface-manager._rtx")
        ].declaration[
            "surface"
        ]["only"]
    ) == {
        "setup-interface-manager._rtx.interface.status",
        "setup-interface-manager._rtx.interface.authorize",
        "setup-interface-manager._rtx.interface.begin",
        "setup-interface-manager._rtx.interface.run-markdown",
        "setup-interface-manager._rtx.interface.run-python",
        "setup-interface-manager._rtx.interface.settle",
        "setup-interface-manager._rtx.interface.invalidate",
        "setup-interface-manager._rtx.interface.teardown-all",
        "setup-interface-manager._rtx.interface.recover",
    }
    assert "setup-interface-manager._rtx" in graph.exports[
        "common.interface.atomic-files"
    ].export_declaration["access"]["allowed_callers"]
