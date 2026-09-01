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


def _managed_graph() -> SimpleNamespace:
    item = _managed()
    return SimpleNamespace(managed_setups={item.setup_interface: item})


def _resolved(events: list[str], *, target: str = "root.interface.run"):
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
    host_authorization_calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(server, "_repository_graph", lambda: _managed_graph())
    monkeypatch.setattr(
        server,
        "resolve_export",
        lambda _graph, _target, _version: (SimpleNamespace(node_id="root"), None, None),
    )
    monkeypatch.setattr(
        server,
        "resolve_interface_authorization",
        lambda _graph, _request: SimpleNamespace(allowed=True, diagnostic="authorized"),
    )
    monkeypatch.setattr(
        server,
        "authorize_host_caller",
        lambda *, caller_skill, repository_config: host_authorization_calls.append(
            (caller_skill, repository_config)
        ),
    )
    monkeypatch.setattr(
        server,
        "resolve_dispatch",
        lambda **_kwargs: pytest.fail("managed lifecycle reached process binding"),
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
    assert host_authorization_calls == [("root", ROOT / "officina.toml")]
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
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: _resolved(events))
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
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: pytest.fail("pending target launched"),
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
    assert events == ["resolve", "status"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


def test_busy_refusal_returns_only_flow_and_argument_free_recovery_route(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches leaking the suspended call or inventing a recovery action."""
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: _resolved([]))
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, _operation, _arguments: {
            "schema_version": 1,
            "code": "setup_busy",
            "root_setup_interface": "root.interface.setup",
            "pending_stack": [],
            "flow_id": "flow-7",
        },
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
    assert "original-secret" not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize("flow_id", [None, ""])
def test_busy_refusal_requires_nonempty_flow_id(
    server, monkeypatch: pytest.MonkeyPatch, flow_id: object
) -> None:
    """Catches malformed busy state being returned as a recoverable setup flow."""
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: _resolved([]))
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, _operation, _arguments: {
            "schema_version": 1,
            "code": "setup_busy",
            "root_setup_interface": "root.interface.setup",
            "pending_stack": [],
            "flow_id": flow_id,
        },
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: pytest.fail("invalid busy status launched target"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.runtime_misconfigured"


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


@pytest.mark.parametrize("status_code", ["unmanaged", "ready"])
def test_ordinary_call_authorizes_ready_only_immediately_before_launch(
    server, monkeypatch: pytest.MonkeyPatch, status_code: str
) -> None:
    """Catches authorization on unmanaged state or launch before ready claims."""
    events: list[str] = []
    resolved_context = _resolved(events)
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: resolved_context)

    def manager_call(_caller: str, operation: str, arguments: list[str]):
        events.append(operation)
        if operation == "status":
            assert arguments == ["root.interface.run"]
            return {
                "schema_version": 1,
                "code": status_code,
                "root_setup_interface": None if status_code == "unmanaged" else "root.interface.setup",
                "pending_stack": [],
                "flow_id": None,
            }
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
            "original": {"caller": "root", "interface": "root.interface.run", "version": 1},
            "resume_original": True,
        }

    monkeypatch.setattr(server, "_manager_call", manager_call)

    def launch(_resolved, **kwargs):
        events.append("launch")
        assert kwargs["stdin"] == "original-secret"
        return SimpleNamespace(returncode=0, stdout="ran\n", stderr="")

    monkeypatch.setattr(server, "_run_resolved_invocation", launch)

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 0
    assert events == (
        ["resolve", "status", "launch"]
        if status_code == "unmanaged"
        else ["resolve", "status", "authorize", "launch"]
    )


def test_dry_run_and_manager_targets_do_not_activate_ordinary_preflight(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches recursive manager preflight or ledger mutation during dry-run."""
    events: list[str] = []
    monkeypatch.setattr(server, "resolve_dispatch", lambda **kwargs: _resolved(events, target=kwargs["target"]))
    monkeypatch.setattr(
        server,
        "_repository_graph",
        lambda: pytest.fail("dry-run loaded the managed setup graph"),
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
    monkeypatch.setattr(server, "_repository_graph", lambda: _managed_graph())
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: _resolved(events))
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
    assert events == ["resolve", "status"]
