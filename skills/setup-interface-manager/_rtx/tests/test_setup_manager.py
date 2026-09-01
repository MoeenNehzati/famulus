"""Behavioral tests for the finite managed-setup controller and public routes."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

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
)
from officina.runtime.python_machine_interface_runner import (
    load_interface,
    run_python_machine_interface,
)


SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPT_DIR.parents[2]
LOGICAL_PACKAGE = logical_python_package_name("setup-interface-manager._rtx")
LOGICAL_ENTRYPOINT = f"{LOGICAL_PACKAGE}._setup_manager"


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
    return state.LedgerStore._from_atomic_files(tmp_path / "private" / "ledger.json", AtomicFiles())


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

    class Runtime(manager.StatusInterface):
        def __init__(self, stdout: str) -> None:
            super().__init__(graph_loader=lambda _root: _graph())
            self.stdout = stdout
            self.calls = []

        def dispatch(self, key: str, **kwargs: object):
            self.calls.append((key, kwargs))
            return subprocess.CompletedProcess([], 0, self.stdout, "")

    ledger_path = tmp_path / "private" / "ledger.json"
    runtime = Runtime(f"{ledger_path}\n")
    built = runtime.build_manager()
    assert built.store.read() == state.SetupLedger.empty()
    assert runtime.calls == [
        (setup_dispatches.GETTER_KEY, {"args": ("setup-status",), "text": True})
    ]

    with pytest.raises(state.LedgerPathError):
        Runtime(f"{ledger_path}\n{tmp_path / 'other'}\n").build_manager()


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


def test_production_bindings_register_the_milestone_markdown_lifecycle() -> None:
    """Catches the production canary missing any fixed action or verifier route."""
    setup_interface = "milestone-logging.interface.setup"
    binding = setup_dispatches.PRODUCTION_BINDINGS[setup_interface]

    assert binding == setup_dispatches.ManagedInterfaceBinding(
        setup_interface=setup_interface,
        setup_version=1,
        setup_kind="markdown",
        setup_dispatch_key="milestone-logging-setup",
        setup_instructions=(
            "Invoke common.interface.famulus-paths-get@1 with logging-path, require "
            "one absolute path, then create that directory and missing parents "
            "idempotently. Do not read or write setup-status, change MCP "
            "configuration, or remove existing contents."
        ),
        setup_verifier_interface="milestone-logging._rtx.interface.setup-status",
        setup_verifier_version=1,
        setup_verifier_dispatch_key="milestone-logging-setup-status",
        teardown_interface="milestone-logging.interface.teardown",
        teardown_version=1,
        teardown_dispatch_key="milestone-logging-teardown",
        teardown_instructions=(
            "Perform no external mutation. Retain the logging directory, its "
            "contents, environment, and plugin state; proceed directly to settlement."
        ),
        teardown_verifier_interface="milestone-logging._rtx.interface.teardown-status",
        teardown_verifier_version=1,
        teardown_verifier_dispatch_key="milestone-logging-teardown-status",
    )
    assert {
        key: (call.target_module_id, call.interface)
        for key, call in setup_dispatches.PRODUCTION_ACTION_CALLS.items()
    } == {
        "milestone-logging-setup": ("milestone-logging", "setup"),
        "milestone-logging-setup-status": ("milestone-logging._rtx", "setup-status"),
        "milestone-logging-teardown": ("milestone-logging", "teardown"),
        "milestone-logging-teardown-status": ("milestone-logging._rtx", "teardown-status"),
    }
    assert set(setup_dispatches.PRODUCTION_DISPATCHES) == {
        setup_dispatches.GETTER_KEY,
        *setup_dispatches.PRODUCTION_ACTION_CALLS,
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
    ledger_path = tmp_path / "private" / "ledger.json"

    class Runtime(manager.StatusInterface):
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

    code = run_python_machine_interface(Runtime(), ["plain.interface.run"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 2
    assert output.count("\n") == 1
    assert secret not in output
    assert payload["operation"] == "status"
    assert payload["state"] == "recovery-required"
    assert payload["flow_id"] is None
    assert payload["current_step"] is None
    assert payload["original"] is None


def test_bootstrap_does_not_hide_an_arbitrary_programmer_error(tmp_path: Path) -> None:
    """Catches a broad exception handler converting code defects into domain JSON."""
    class Runtime(manager.StatusInterface):
        def __init__(self) -> None:
            super().__init__(graph_loader=self._load_graph)

        def _load_graph(self, _root: Path):
            raise RuntimeError("programmer defect")

        def dispatch(self, key: str, **kwargs: object):
            return subprocess.CompletedProcess(
                [], 0, str(tmp_path / "ledger.json") + "\n", ""
            )

    with pytest.raises(RuntimeError, match="programmer defect"):
        run_python_machine_interface(Runtime(), ["plain.interface.run"])


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
        "setup-interface-manager._rtx.interface.recover",
    }
    assert "setup-interface-manager._rtx" in graph.exports[
        "common.interface.atomic-files"
    ].export_declaration["access"]["allowed_callers"]
