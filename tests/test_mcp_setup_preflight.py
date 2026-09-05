"""Behavioral tests for the Famulus MCP managed-setup enforcement seam."""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from officina.blueprints.graph import ManagedSetup
from officina.dispatcher.errors import DirectBlueprintError


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("famulus_mcp_preflight", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server():
    """Load the immutable MCP module once per pytest isolation domain."""
    return _load_server()


def _arguments(server, *, secret: str = "original-secret"):
    return server.CompactArguments(
        positionals=[secret], options={"--token": secret}, stdin=secret
    )


def _managed() -> ManagedSetup:
    return ManagedSetup(
        setup_interface="root.interface.setup",
        setup_version=1,
        teardown_interface="root.interface.teardown",
        teardown_version=1,
        setup_verifier_interface="root.interface.setup-status",
        setup_verifier_version=1,
        teardown_verifier_interface="root.interface.teardown-status",
        teardown_verifier_version=1,
        kind="markdown",
    )


def _legacy_resolved(events: list[str], *, target: str = "root.interface.run"):
    class Resolved:
        def metadata(self):
            return SimpleNamespace(
                as_payload=lambda: {
                    "target": target,
                    "command": ["run"],
                    "stdin": True,
                }
            )

    events.append("resolve")
    return nullcontext(Resolved())


def _install_authorized_path(
    server,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    managed: bool,
    lifecycle: tuple[str, str] | None = None,
    interface: str = "root.interface.run",
    argv: list[str] | None = None,
    stdin_requested: bool = True,
) -> None:
    """Install one retained authorization context around the real MCP seam."""

    configuration = object()
    repository = object()
    target_modules = (object(),)
    export = object()
    authorized = SimpleNamespace(
        repository=repository,
        target_modules=target_modules,
        export=export,
    )
    projection = SimpleNamespace(
        graph=SimpleNamespace(
            managed_setups={"root.interface.setup": _managed()} if managed else {}
        ),
        lifecycle=lifecycle,
    )

    def load_configuration(path: Path):
        assert path == ROOT / "officina.toml"
        return configuration

    def authorize(**kwargs):
        events.append("authorize")
        assert kwargs == {
            "configuration": configuration,
            "caller_module_id": "root",
            "interface_id": interface,
            "interface_version": 1,
            "host_caller": True,
        }
        return authorized

    def load_projection(actual_repository, actual_modules, actual_export):
        assert actual_repository is repository
        assert actual_modules is target_modules
        assert actual_export is export
        return projection

    def materialize(actual_authorized, *, argv, stdin_requested):
        events.append("compile")
        assert actual_authorized is authorized
        assert argv == (
            ["original-secret", "--token", "original-secret"]
            if expected_argv is None
            else expected_argv
        )
        assert stdin_requested is expected_stdin_requested
        return _legacy_resolved([], target=interface).__enter__()

    expected_argv = argv
    expected_stdin_requested = stdin_requested

    monkeypatch.setattr(
        server, "load_repository_configuration", load_configuration, raising=False
    )
    monkeypatch.setattr(
        server, "authorize_direct_invocation", authorize, raising=False
    )
    monkeypatch.setattr(
        server, "load_direct_setup_projection", load_projection, raising=False
    )
    monkeypatch.setattr(
        server, "materialize_authorized_invocation", materialize, raising=False
    )


@pytest.mark.parametrize(
    ("interface", "operation"),
    [
        ("root.interface.setup", "setup"),
        ("root.interface.teardown", "teardown"),
    ],
)
def test_exact_managed_lifecycle_redirects_before_process_binding_and_redacts(
    server, monkeypatch: pytest.MonkeyPatch, interface: str, operation: str
) -> None:
    """Catches launching a managed setup/teardown or returning its secret payload."""
    events: list[str] = []
    _install_authorized_path(
        server,
        monkeypatch,
        events,
        managed=True,
        lifecycle=("root.interface.setup", operation),
        interface=interface,
    )
    real_setup_managed = server._setup_managed

    def setup_managed(*args):
        events.append("setup-managed")
        return real_setup_managed(*args)

    monkeypatch.setattr(server, "_setup_managed", setup_managed)
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("managed lifecycle was compiled"),
    )
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda *_args: pytest.fail("managed lifecycle reached the ledger manager"),
    )

    result = server.invoke(
        "root", interface, 1, _arguments(server), dry_run=False
    )

    assert result == {
        "code": "setup_managed",
        "operation": operation,
        "root_setup_interface": "root.interface.setup",
        "manager": {
            "interface": "setup-interface-manager._rtx.interface.begin",
            "version": 1,
            "arguments": {
                "positionals": [operation, "root.interface.setup", "root", interface, "1"],
                "options": {},
                "stdin": None,
            },
        },
        "original": {"caller": "root", "interface": interface, "version": 1},
    }
    assert events == ["authorize", "setup-managed"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


def test_pending_child_target_returns_pop_ordered_suffix_and_redacted_begin(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches reversing the suffix or exposing arguments in a setup refusal."""
    events: list[str] = []
    pending_stack = [
        {"interface": "root.interface.setup", "version": 1, "kind": "markdown", "action": "run-setup"},
        {"interface": "parent.interface.setup", "version": 1, "kind": "python", "action": "run-setup"},
    ]
    _install_authorized_path(
        server,
        monkeypatch,
        events,
        managed=True,
        interface="root.child.interface.run",
    )
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: (
            events.append(operation)
            or {
                "schema_version": 1,
                "code": "setup_required",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": pending_stack,
                "flow_id": None,
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("pending target was compiled"),
    )

    result = server.invoke(
        "root", "root.child.interface.run", 1, _arguments(server)
    )

    assert result == {
        "code": "setup_required",
        "root_setup_interface": "root.interface.setup",
        "pending_stack": pending_stack,
        "next_setup": pending_stack[-1],
        "manager": {
            "interface": "setup-interface-manager._rtx.interface.begin",
            "version": 1,
            "arguments": {
                "positionals": [
                    "setup",
                    "root.interface.setup",
                    "root",
                    "root.child.interface.run",
                    "1",
                ],
                "options": {},
                "stdin": None,
            },
        },
        "original": {
            "caller": "root",
            "interface": "root.child.interface.run",
            "version": 1,
        },
    }
    assert events == ["authorize", "status"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


def test_busy_refusal_returns_only_flow_and_argument_free_recovery_route(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches leaking the suspended call or inventing a recovery action."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: (
            events.append(operation)
            or {
                "schema_version": 1,
                "code": "setup_busy",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [],
                "flow_id": "flow-7",
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("busy target was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result == {
        "code": "setup_busy",
        "flow_id": "flow-7",
        "manager": {
            "interface": "setup-interface-manager._rtx.interface.recover",
            "version": 1,
        },
    }
    assert "arguments" not in result["manager"]
    assert events == ["authorize", "status"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize("flow_id", [None, ""])
def test_busy_refusal_requires_nonempty_flow_id(
    server, monkeypatch: pytest.MonkeyPatch, flow_id: object
) -> None:
    """Catches malformed busy state being returned as a recoverable setup flow."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: (
            events.append(operation)
            or {
                "schema_version": 1,
                "code": "setup_busy",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [],
                "flow_id": flow_id,
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("invalid busy status was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.runtime_misconfigured"
    assert events == ["authorize", "status"]


def test_real_manager_nonzero_status_is_a_redacted_refusal(
    server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an exit-2 recovery payload being mistaken for normal busy status."""
    secret = "manager-ledger-secret"
    plugin_data = tmp_path / "plugin-data"
    setup = plugin_data / "setup"
    setup.mkdir(parents=True, mode=0o700)
    ledger = setup / "status.json"
    ledger.write_text(f'{{"private":"{secret}"}}\n', encoding="utf-8")
    ledger.chmod(0o600)
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))

    with pytest.raises(server.RuntimeMisconfiguredError) as caught:
        server._manager_call(
            "cloud-files", "status", ["cloud-files.interface.default"]
        )

    assert secret not in str(caught.value)


def test_unmanaged_ordinary_call_uses_sparse_context_without_manager_or_full_graph(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches manager, ledger, or canonical-graph work on an unmanaged call."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=False)
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda *_args: pytest.fail("unmanaged call reached manager or ledger APIs"),
    )
    monkeypatch.setattr(
        server,
        "_repository_graph",
        lambda: pytest.fail("unmanaged call loaded the canonical graph"),
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "resolve_dispatch",
        lambda **_kwargs: pytest.fail("unmanaged call reauthorized through runtime"),
    )

    def launch(_resolved, **kwargs):
        events.append("launch")
        assert kwargs["stdin"] == "original-secret"
        return SimpleNamespace(returncode=0, stdout="ran\n", stderr="")

    monkeypatch.setattr(server, "_run_resolved_invocation", launch)

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 0
    assert events == ["authorize", "compile", "launch"]


def test_projection_direct_blueprint_failure_is_generic_and_redacted(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a projection lookup path or secret escaping in the MCP payload."""
    events: list[str] = []
    secret = "projection-ledger-secret"
    private_path = "/private/setup/projection/blueprint.yaml"
    _install_authorized_path(server, monkeypatch, events, managed=False)

    def fail_projection(*_args):
        raise DirectBlueprintError(
            f"foreign lifecycle export near {private_path}; token={secret}",
            code="dispatcher.source_not_found",
            target_module_id="root",
        )

    monkeypatch.setattr(server, "load_direct_setup_projection", fail_projection)
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("failed projection was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result == {
        "exit_code": 2,
        "stdout": "",
        "stderr": "",
        "dispatcher": {
            "schema_version": 1,
            "code": "dispatcher.runtime_misconfigured",
            "caller_module_id": "root",
            "target_module_id": "root",
            "message": "managed setup graph is unavailable",
        },
    }
    assert events == ["authorize"]
    encoded = json.dumps(result, sort_keys=True)
    assert private_path not in encoded
    assert secret not in encoded


def test_managed_ready_authorizes_atomically_before_compile_and_launch(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches compilation before the manager atomically authorizes readiness."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)

    def manager_call(_caller: str, operation: str, arguments: list[str]):
        if operation == "status":
            events.append("status")
            assert arguments == ["root.interface.run"]
            return {
                "schema_version": 1,
                "code": "ready",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [],
                "flow_id": None,
            }
        events.append("manager-authorize")
        assert operation == "authorize"
        assert arguments == [
            "root.interface.run", "root", "root.interface.run", "1"
        ]
        return {
            "schema_version": 1,
            "flow_id": None,
            "operation": "authorize",
            "state": "ready",
            "current_step": None,
            "original": {
                "caller": "root",
                "interface": "root.interface.run",
                "version": 1,
            },
            "resume_original": True,
        }

    monkeypatch.setattr(server, "_manager_call", manager_call)

    def launch(_resolved, **_kwargs):
        events.append("launch")
        return SimpleNamespace(returncode=0, stdout="ran\n", stderr="")

    monkeypatch.setattr(server, "_run_resolved_invocation", launch)

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 0
    assert events == [
        "authorize",
        "status",
        "manager-authorize",
        "compile",
        "launch",
    ]


def test_dry_run_and_manager_targets_do_not_activate_ordinary_preflight(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches recursive manager preflight or ledger mutation during dry-run."""
    events: list[str] = []
    monkeypatch.setattr(
        server,
        "resolve_dispatch",
        lambda **kwargs: _legacy_resolved(events, target=kwargs["target"]),
    )
    monkeypatch.setattr(
        server,
        "authorize_direct_invocation",
        lambda **_kwargs: pytest.fail("exempt call entered direct preflight"),
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda *_args: pytest.fail("exempt call reached manager"),
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda _resolved, **_kwargs: SimpleNamespace(returncode=0, stdout="manager\n", stderr=""),
    )

    dry = server.invoke("root", "root.interface.run", 1, _arguments(server), dry_run=True)
    manager = server.invoke(
        "root",
        "setup-interface-manager._rtx.interface.status",
        1,
        server.CompactArguments(
            positionals=["root.interface.run"], options={}, stdin=None
        ),
    )

    assert dry["target"] == "root.interface.run"
    assert manager["exit_code"] == 0


def test_generic_setup_words_do_not_activate_lifecycle_redirection(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches activation from argument prose instead of exact managed exports."""
    events: list[str] = []
    _install_authorized_path(
        server,
        monkeypatch,
        events,
        managed=True,
        argv=["please set up everything"],
        stdin_requested=False,
    )
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: (
            events.append(operation)
            or {
                "schema_version": 1,
                "code": "unmanaged",
                "root_setup_interface": None,
                "pending_stack": [],
                "flow_id": None,
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ordinary\n", stderr=""),
    )
    arguments = server.CompactArguments(
        positionals=["please set up everything"], options={}, stdin=None
    )

    result = server.invoke("root", "root.interface.run", 1, arguments)

    assert result["stdout"] == "ordinary\n"
    assert events == ["authorize", "status", "compile"]


def test_setup_flow_id_with_dry_run_is_rejected(server, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches setup_flow_id being used with dry_run."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda *_args: pytest.fail("dry_run + setup_flow_id should be rejected before manager call"),
    )

    result = server.invoke(
        "root",
        "root.interface.run",
        1,
        _arguments(server),
        dry_run=True,
        setup_flow_id="flow-1",
    )

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.runtime_misconfigured"


def test_setup_flow_id_with_manager_target_is_rejected(server, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches setup_flow_id being used with manager targets."""
    events: list[str] = []
    monkeypatch.setattr(
        server,
        "resolve_dispatch",
        lambda **kwargs: pytest.fail("manager target + setup_flow_id should be rejected before resolve"),
    )

    result = server.invoke(
        "root",
        "setup-interface-manager._rtx.interface.authorize-markdown-call",
        1,
        server.CompactArguments(positionals=["flow-1", "target", "1"], options={}, stdin=None),
        setup_flow_id="flow-1",
    )

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.runtime_misconfigured"


def test_setup_flow_id_with_successful_authorization_permits_execution(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches setup_flow_id authorization bypassing ordinary preflight when authorized."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)

    def manager_call(_caller: str, operation: str, _arguments: list[str]):
        events.append(operation)
        if operation == "authorize-markdown-call":
            return {
                "schema_version": 1,
                "flow_id": "flow-1",
                "operation": "setup",
                "state": "authorized-markdown-call",
                "interface": "target.interface.helper",
                "version": 1,
                "current_step": None,
                "original": None,
                "resume_original": False,
            }
        return pytest.fail("unexpected manager operation after authorization")

    monkeypatch.setattr(server, "_manager_call", manager_call)

    def launch(_resolved, **_kwargs):
        events.append("launch")
        return SimpleNamespace(returncode=0, stdout="ran\n", stderr="")

    monkeypatch.setattr(server, "_run_resolved_invocation", launch)

    result = server.invoke(
        "root",
        "root.interface.run",
        1,
        _arguments(server),
        setup_flow_id="flow-1",
    )

    assert result["exit_code"] == 0
    assert events == ["authorize", "authorize-markdown-call", "compile", "launch"]


def test_setup_flow_id_with_failed_authorization_returns_busy(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches setup_flow_id authorization failure not returning setup_busy."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)

    def manager_call(_caller: str, operation: str, _arguments: list[str]):
        events.append(operation)
        if operation == "authorize-markdown-call":
            return {
                "schema_version": 1,
                "flow_id": "flow-1",
                "operation": "setup",
                "state": "failed",
                "error": "not authorized",
            }
        return pytest.fail("unexpected manager operation")

    monkeypatch.setattr(server, "_manager_call", manager_call)
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: pytest.fail("failed authorization should not proceed to launch"),
    )

    result = server.invoke(
        "root",
        "root.interface.run",
        1,
        _arguments(server),
        setup_flow_id="flow-1",
    )

    assert result == {
        "code": "setup_busy",
        "flow_id": "flow-1",
        "manager": {
            "interface": "setup-interface-manager._rtx.interface.recover",
            "version": 1,
        },
    }
    assert events == ["authorize", "authorize-markdown-call"]


def test_setup_flow_id_absent_retains_ordinary_preflight_behavior(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches setup_flow_id absence changing ordinary preflight semantics."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)

    def manager_call(_caller: str, operation: str, _arguments: list[str]):
        events.append(operation)
        return {
            "schema_version": 1,
            "code": "setup_busy",
            "root_setup_interface": "root.interface.setup",
            "pending_stack": [],
            "flow_id": "flow-7",
        }

    monkeypatch.setattr(server, "_manager_call", manager_call)
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("busy target was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result == {
        "code": "setup_busy",
        "flow_id": "flow-7",
        "manager": {
            "interface": "setup-interface-manager._rtx.interface.recover",
            "version": 1,
        },
    }
    assert events == ["authorize", "status"]
