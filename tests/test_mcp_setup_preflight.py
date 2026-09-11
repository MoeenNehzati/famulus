"""Behavioral tests for the Famulus MCP managed-setup enforcement seam."""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace
import sys

import pytest
import yaml

from officina.blueprints.graph import ManagedSetup
from officina.common.atomic_files import (
    atomic_replace_bytes,
    ensure_private_directory,
)
from officina.dispatcher.errors import (
    DirectBlueprintError,
    DispatcherError,
    InvocationError,
    SetupBlocked,
)


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


def _without_trace(result: dict[str, object]) -> dict[str, object]:
    trace_id = result.pop("trace_id")
    assert isinstance(trace_id, str) and len(trace_id) == 32
    return result


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


def _install_manager_process(
    server,
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int,
    stdout: str,
) -> None:
    monkeypatch.setattr(server, "resolve_dispatch", lambda **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda _resolved, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="private-manager-stderr",
        ),
    )


def _recovery_diagnosis(recovery: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "flow_id": "flow-1",
        "operation": "setup",
        "state": "recovery-required",
        "current_step": {
            "interface": "root.interface.setup",
            "version": 1,
            "kind": "python",
            "action": "run-setup",
        },
        "original": {
            "caller": "root",
            "interface": "root.interface.run",
            "version": 1,
        },
        "resume_original": False,
        "error_code": "setup.action_dispatch_failed",
        "error": (
            "The managed setup action dispatch failed; action completion is unknown."
        ),
        "recovery": recovery,
    }


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

    def materialize(
        actual_authorized,
        *,
        argv,
        stdin_requested,
        setup_preflight_authorized=False,
    ):
        events.append(
            "compile-authorized" if setup_preflight_authorized else "compile"
        )
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

    assert _without_trace(result) == {
        "code": "setup_managed",
        "operation": operation,
        "root_setup_interface": "root.interface.setup",
        "manager": {
            "caller": "root",
            "interface": "setup-interface-manager._rtx.interface.begin",
            "version": 1,
            "arguments": {
                    "positionals": result["manager"]["arguments"]["positionals"],
                "options": {},
                "stdin": None,
            },
        },
        "original": {"caller": "root", "interface": interface, "version": 1},
    }
    assert result["manager"]["arguments"]["positionals"][:5] == [operation, "root.interface.setup", "root", interface, "1"]
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

    assert _without_trace(result) == {
        "code": "setup_required",
        "root_setup_interface": "root.interface.setup",
        "pending_stack": pending_stack,
        "next_setup": pending_stack[-1],
        "manager": {
            "caller": "root",
            "interface": "setup-interface-manager._rtx.interface.begin",
            "version": 1,
            "arguments": {
                    "positionals": result["manager"]["arguments"]["positionals"],
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
    assert result["manager"]["arguments"]["positionals"][:5] == ["setup", "root.interface.setup", "root", "root.child.interface.run", "1"]
    assert events == ["authorize", "status"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


def test_busy_refusal_identifies_owner_and_recovery_route(
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
                "current_step": "leaf.interface.setup",
                "owner": {"host": "codex", "pid": 1234, "started_at": "2026-09-09T12:00:00Z"},
            }
        ),
    )
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("busy target was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert _without_trace(result) == {
        "code": "setup_busy",
        "flow_id": "flow-7",
        "root_setup_interface": "root.interface.setup",
        "current_step": "leaf.interface.setup",
        "owner": {"host": "codex", "pid": 1234, "started_at": "2026-09-09T12:00:00Z"},
        "message": "Setup root.interface.setup is busy by codex process 1234.",
        "recovery": {
            "interface": "setup-interface-manager._rtx.interface.recover-busy",
            "arguments": {"positionals": ["flow-7"], "options": {}, "stdin": None},
        },
    }
    assert events == ["authorize", "status"]
    assert "original-secret" not in json.dumps(result, sort_keys=True)


def test_busy_validation_error_is_returned_by_mcp(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validated manager-boundary error reaches the MCP carrier unchanged."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)

    def invalid_busy(_caller: str, operation: str, _arguments: list[str]):
        events.append(operation)
        raise DispatcherError.from_spec("D62")

    monkeypatch.setattr(
        server,
        "_manager_call",
        invalid_busy,
    )
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("invalid busy status was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 2
    assert result["dispatcher"] == DispatcherError.from_spec("D62").as_payload()
    assert events == ["authorize", "status"]


def test_real_manager_nonzero_status_is_a_redacted_refusal(
    server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an exit-2 recovery payload being mistaken for normal busy status."""
    secret = "manager-ledger-secret"
    plugin_data = tmp_path / "plugin-data"
    setup = plugin_data / "setup"
    ensure_private_directory(setup, allowed_root=tmp_path)
    ledger = setup / "status.json"
    atomic_replace_bytes(
        ledger,
        f'{{"private":"{secret}"}}\n'.encode(),
        allowed_root=plugin_data,
        mode=0o600,
    )
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))

    with pytest.raises(DispatcherError) as caught:
        server._manager_call(
            "cloud-files", "status", ["cloud-files.interface.default"]
        )

    assert caught.value.as_payload()["code"] == "dispatcher.manager_operation_failed"
    assert caught.value.as_payload()["setup_error_code"] == "setup.ledger_invalid"
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    ("returncode", "stdout", "entry_id"),
    [
        (2, "not-json manager-secret", "D56"),
        (0, "not-json manager-secret", "D57"),
        (0, '"manager-secret"', "D57"),
        (0, '{"schema_version":1,"private":"manager-secret"}', "D58"),
        (
            2,
            '{"schema_version":1,"code":"ready","root_setup_interface":null,'
            '"pending_stack":[],"flow_id":null}',
            "D58",
        ),
        (
            2,
            '{"schema_version":1,"flow_id":null,"operation":"status",'
            '"state":"failed","current_step":null,"original":null,'
            '"resume_original":false,"error":"manager-secret",'
            '"error_code":["setup.ledger_invalid"]}',
            "D58",
        ),
        (
            2,
            '{"schema_version":1,"flow_id":null,"operation":"status",'
            '"state":"failed","current_step":null,"original":null,'
            '"resume_original":false,"error":"manager-secret",'
            '"error_code":"setup.settlement_failed","cause":'
            '{"schema_version":1,"code":["setup.ledger_invalid"],'
            '"message":"manager-secret"}}',
            "D58",
        ),
    ],
)
def test_manager_adapter_classifies_json_before_process_status_and_redacts(
    server,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    entry_id: str,
) -> None:
    """Break caught: exit-first classification or raw manager output exposure."""
    _install_manager_process(
        server, monkeypatch, returncode=returncode, stdout=stdout
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "status", ["root.interface.run"])

    expected = DispatcherError.from_spec(entry_id, operation="status")
    assert caught.value.as_payload() == expected.as_payload()
    assert "manager-secret" not in json.dumps(caught.value.as_payload())
    assert "private-manager-stderr" not in json.dumps(caught.value.as_payload())
    assert "setup" not in str(caught.value).lower().replace("setup manager", "")
    assert "bootstrap" not in str(caught.value).lower()


def test_manager_adapter_preserves_allowlisted_setup_diagnosis_semantics(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a valid manager diagnosis is flattened, embellished, or leaked."""
    diagnosis = {
        "schema_version": 1,
        "flow_id": None,
        "operation": "status",
        "state": "failed",
        "current_step": None,
        "original": None,
        "resume_original": False,
        "error_code": "setup.ledger_conflict",
        "error": "Managed-setup state changed while this operation was updating it.",
        "clues": [
            "Another setup-manager process may have updated the ledger concurrently."
        ],
    }
    _install_manager_process(
        server,
        monkeypatch,
        returncode=2,
        stdout=json.dumps(diagnosis),
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "status", ["root.interface.run"])

    assert caught.value.as_payload() == {
        "schema_version": 1,
        "code": "dispatcher.manager_operation_failed",
        "message": (
            "The setup manager `status` failed: Managed-setup state changed "
            "while this operation was updating it."
        ),
        "setup_error_code": "setup.ledger_conflict",
        "clues": [
            "Another setup-manager process may have updated the ledger concurrently."
        ],
    }


def test_manager_adapter_preserves_registered_typed_invocation_cause(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a confirmed dispatcher cause is dropped or exposed recursively."""
    cause = DispatcherError.from_spec(
        "D10", interface_id="setup-interface-manager._rtx.interface.status"
    )

    def fail_resolve(**_kwargs):
        raise cause

    monkeypatch.setattr(server, "resolve_dispatch", fail_resolve)

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "status", ["root.interface.run"])

    reduced_cause = cause.as_payload()
    reduced_cause.pop("schema_version")
    assert caught.value.as_payload() == {
        "schema_version": 1,
        "code": "dispatcher.manager_invocation_failed",
        "message": (
            "The dispatcher could not obtain a valid `status` result from the "
            "setup manager."
        ),
        "cause": reduced_cause,
    }


def test_manager_adapter_rejects_mutated_unregistered_invocation_cause(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    cause = DispatcherError.from_spec("D10", interface_id="private.interface.run")
    cause._entry_id = "D999"
    monkeypatch.setattr(
        server,
        "resolve_dispatch",
        lambda **_kwargs: (_ for _ in ()).throw(cause),
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "status", ["root.interface.run"])

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D56", operation="status"
    ).as_payload()


def test_manager_validator_accepts_only_registered_dispatcher_cause_clues(
    server,
) -> None:
    cause = DispatcherError.from_spec(
        "D64",
        operation="status",
        setup_error="Managed-setup state changed while this operation was updating it.",
        setup_error_code="setup.ledger_conflict",
        clues=(
            "Another setup-manager process may have updated the ledger concurrently.",
        ),
    ).as_payload()
    cause.pop("schema_version")
    base = {
        "schema_version": 1, "flow_id": None, "operation": "status",
        "state": "failed", "current_step": None, "original": None,
        "resume_original": False, "error_code": "setup.graph_invalid",
        "error": "Managed-setup metadata is invalid.",
    }

    with pytest.raises(DispatcherError) as accepted:
        server._validate_manager_response({**base, "cause": cause}, "status", 2)
    assert accepted.value.as_payload()["cause"]["clues"] == cause["clues"]

    cause["clues"] = ["Try reinstalling Python."]
    with pytest.raises(DispatcherError) as rejected:
        server._validate_manager_response({**base, "cause": cause}, "status", 2)
    assert rejected.value.as_payload() == DispatcherError.from_spec(
        "D58", operation="status"
    ).as_payload()

    runner_cause = DirectBlueprintError.from_spec("R01").as_payload()
    runner_cause.pop("schema_version")
    with pytest.raises(DispatcherError) as wrong_family:
        server._validate_manager_response(
            {**base, "cause": runner_cause}, "status", 2
        )
    assert wrong_family.value.as_payload() == DispatcherError.from_spec(
        "D58", operation="status"
    ).as_payload()


def test_manager_adapter_rejects_flow_state_for_wrong_operation(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: structural flow validation ignores operation semantics."""
    payload = {
        "schema_version": 1,
        "flow_id": "flow-1",
        "operation": "authorize",
        "state": "run-step",
        "current_step": {
            "interface": "root.interface.setup",
            "version": 1,
            "kind": "markdown",
            "action": "run-setup",
        },
        "original": None,
        "resume_original": False,
    }
    _install_manager_process(
        server, monkeypatch, returncode=0, stdout=json.dumps(payload)
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "authorize", ["root.interface.run"])

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D58", operation="authorize"
    ).as_payload()


def test_manager_adapter_preserves_allowlisted_setup_cause(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an E-row's confirmed setup cause is dropped or nested."""
    payload = {
        "schema_version": 1,
        "flow_id": None,
        "operation": "status",
        "state": "failed",
        "current_step": None,
        "original": None,
        "resume_original": False,
        "error_code": "setup.settlement_failed",
        "error": (
            "The verifier confirmed external completion, but the setup manager "
            "could not record settlement."
        ),
        "cause": {
            "schema_version": 1,
            "code": "setup.ledger_invalid",
            "message": "Managed-setup state is not valid canonical ledger data.",
        },
    }
    _install_manager_process(
        server, monkeypatch, returncode=2, stdout=json.dumps(payload)
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "status", ["root.interface.run"])

    assert caught.value.as_payload()["cause"] == {
        "code": "setup.ledger_invalid",
        "message": "Managed-setup state is not valid canonical ledger data.",
    }


def test_manager_validator_accepts_exact_teardown_begin_flow(server) -> None:
    payload = {
        "schema_version": 1,
        "flow_id": "flow-1",
        "operation": "teardown",
        "state": "run-step",
        "current_step": {
            "interface": "root.interface.teardown",
            "version": 1,
            "kind": "python",
            "action": "run-teardown",
        },
        "original": {"caller": "root", "interface": "root.interface.run", "version": 1},
        "resume_original": False,
    }

    assert server._validate_manager_response(payload, "begin", 0) == payload


def test_manager_validator_rejects_mismatched_setup_message(server) -> None:
    payload = {
        "schema_version": 1, "flow_id": None, "operation": "status",
        "state": "failed", "current_step": None, "original": None,
        "resume_original": False, "error_code": "setup.ledger_invalid",
        "error": "Install Python to repair setup.",
    }

    with pytest.raises(DispatcherError) as caught:
        server._validate_manager_response(payload, "status", 2)
    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D58", operation="status"
    ).as_payload()


def test_manager_validator_enforces_cause_and_recovery_owners(server) -> None:
    base = {
        "schema_version": 1, "flow_id": None, "operation": "status",
        "state": "failed", "current_step": None, "original": None,
        "resume_original": False,
    }
    invalid = [
        {
            **base, "error_code": "setup.graph_invalid",
            "error": "Managed-setup metadata is invalid.",
            "cause": {
                "schema_version": 1, "code": "setup.ledger_invalid",
                "message": "Managed-setup state is not valid canonical ledger data.",
            },
        },
        {
            **base, "flow_id": "flow-1", "state": "recovery-required",
            "error_code": "setup.ledger_invalid",
            "error": "Managed-setup state is not valid canonical ledger data.",
            "recovery": {
                "interface": "setup-interface-manager.interface.recover",
                "version": 1, "flow_id": "flow-1", "actions": ["retry", "cancel"],
            },
        },
    ]

    for payload in invalid:
        with pytest.raises(DispatcherError):
            server._validate_manager_response(payload, "status", 2)


def test_manager_process_accepts_only_the_exact_recovery_authority(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery = {
        "interface": "setup-interface-manager.interface.recover",
        "version": 1,
        "flow_id": "flow-1",
        "actions": ["retry", "cancel"],
    }
    _install_manager_process(
        server,
        monkeypatch,
        returncode=2,
        stdout=json.dumps(_recovery_diagnosis(recovery)),
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "recover", ["flow-1", "retry"])

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D64",
        operation="recover",
        setup_error=(
            "The managed setup action dispatch failed; action completion is unknown."
        ),
        setup_error_code="setup.action_dispatch_failed",
    ).as_payload()
    assert "recovery" not in caught.value.as_payload()


def test_manager_process_rejects_altered_recovery_authority(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    altered = {
        "interface": "other.interface.recover",
        "version": 1,
        "flow_id": "other-flow",
        "actions": ["cancel", "retry"],
    }
    _install_manager_process(
        server,
        monkeypatch,
        returncode=2,
        stdout=json.dumps(_recovery_diagnosis(altered)),
    )

    with pytest.raises(DispatcherError) as caught:
        server._manager_call("root", "recover", ["flow-1", "retry"])

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D58", operation="recover"
    ).as_payload()
    assert "other-flow" not in json.dumps(caught.value.as_payload())


def test_manager_validator_contains_unhashable_discriminators(server) -> None:
    status = {
        "schema_version": 1, "code": [], "root_setup_interface": None,
        "pending_stack": [], "flow_id": None,
    }
    flow = {
        "schema_version": 1, "flow_id": None, "operation": "status",
        "state": [], "current_step": None, "original": None,
        "resume_original": False,
    }
    pending = {
        "schema_version": 1, "code": "setup_required",
        "root_setup_interface": "root.interface.setup",
        "pending_stack": [{
            "interface": "root.interface.setup", "version": 1,
            "kind": [], "action": "run-setup",
        }], "flow_id": None,
    }

    for payload in (status, flow, pending):
        with pytest.raises(DispatcherError):
            server._validate_manager_response(payload, "status", 0)


def test_manager_validator_rejects_success_fields_from_another_state(server) -> None:
    payload = {
        "schema_version": 1, "flow_id": None, "operation": "authorize",
        "state": "ready", "current_step": None,
        "original": {"caller": "root", "interface": "root.interface.run", "version": 1},
        "resume_original": True, "instructions": "not valid for ready",
    }

    with pytest.raises(DispatcherError):
        server._validate_manager_response(payload, "authorize", 0)


@pytest.mark.parametrize(
    ("status", "entry_id"),
    [
        (
            {
                "schema_version": 1,
                "code": "setup_required",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [
                    {
                        "interface": "root.interface.setup",
                        "version": 1,
                        "kind": "markdown",
                        "action": "run-setup",
                    },
                    {
                        "interface": "root.interface.setup",
                        "version": 1,
                        "kind": "markdown",
                        "action": "run-setup",
                    },
                ],
                "flow_id": None,
            },
            "D59",
        ),
        (
            {
                "schema_version": 1,
                "code": "setup_required",
                "root_setup_interface": "",
                "pending_stack": [{
                    "interface": "root.interface.setup", "version": 1,
                    "kind": "markdown", "action": "run-setup",
                }],
                "flow_id": None,
            },
            "D61",
        ),
        (
            {
                "schema_version": 1, "code": "unmanaged",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [], "flow_id": None,
            },
            "D58",
        ),
        (
            {
                "schema_version": 1, "code": "ready",
                "root_setup_interface": None,
                "pending_stack": [], "flow_id": None,
            },
            "D58",
        ),
        (
            {
                "schema_version": 1, "code": "setup_busy",
                "root_setup_interface": None,
                "pending_stack": [], "flow_id": "flow-1",
            },
            "D58",
        ),
        (
            {
                "schema_version": 1,
                "code": "setup_busy",
                "root_setup_interface": None,
                "pending_stack": [],
                "flow_id": "",
            },
            "D62",
        ),
        (
            {
                "schema_version": 1,
                "code": "manager-secret",
                "root_setup_interface": None,
                "pending_stack": [],
                "flow_id": None,
            },
            "D63",
        ),
    ],
)
def test_status_validator_assigns_exact_error(
    server,
    status: dict[str, object],
    entry_id: str,
) -> None:
    """Break caught: distinct invalid status predicates collapse to one error."""
    with pytest.raises(DispatcherError) as caught:
        server._validate_manager_response(status, "status", 0)

    expected_context = {"operation": "status"} if entry_id == "D58" else {}
    assert caught.value.as_payload() == DispatcherError.from_spec(
        entry_id, **expected_context
    ).as_payload()
    assert "manager-secret" not in json.dumps(caught.value.as_payload())


def test_ready_authorization_requires_exact_confirmation(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a success-shaped authorization is accepted without confirmation."""
    responses = iter(
        (
            {
                "schema_version": 1,
                "code": "ready",
                "root_setup_interface": "root.interface.setup",
                "pending_stack": [],
                "flow_id": None,
            },
            {
                "schema_version": 1,
                "flow_id": None,
                "operation": "authorize",
                "state": "ready",
                "current_step": None,
                "original": None,
                "resume_original": False,
            },
        )
    )
    monkeypatch.setattr(server, "_manager_call", lambda *_args: next(responses))

    with pytest.raises(DispatcherError) as caught:
        server._ordinary_preflight("root", "root.interface.run", 1)

    assert caught.value.as_payload() == DispatcherError.from_spec("D60").as_payload()


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


def _nested_status(code: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "code": code,
        "root_setup_interface": "child.interface.setup",
        "pending_stack": (
            [{"interface": "child.interface.setup", "version": 1, "kind": "python", "action": "run-setup"}]
            if code == "setup_required"
            else []
        ),
        "flow_id": "flow-7" if code == "setup_busy" else None,
        **({
            "current_step": "child.interface.setup",
            "owner": {"host": "codex", "pid": 1234, "started_at": "2026-09-09T12:00:00Z"},
        } if code == "setup_busy" else {}),
    }


def _with_private_field(signal: SetupBlocked) -> SetupBlocked:
    signal.private = "nested-private-secret"
    return signal


@pytest.mark.parametrize("code", ["setup_required", "setup_busy"])
def test_nested_setup_refusal_rebinds_to_outer_mcp_invocation(
    server, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Catches exposing a child continuation or recovery arguments at MCP."""
    secret = "nested-private-secret"
    _install_authorized_path(
        server,
        monkeypatch,
        [],
        managed=False,
        argv=[secret, "--token", secret],
    )
    signal = SetupBlocked(
        _nested_status(code),
        ("root.interface.run", "child.interface.run"),
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server, secret=secret))

    assert result["call_path"] == ["root.interface.run", "child.interface.run"]
    if code == "setup_required":
        assert result["original"] == {"caller": "root", "interface": "root.interface.run", "version": 1}
        assert result["manager"]["caller"] == result["original"]["caller"]
        assert result["manager"]["arguments"]["positionals"][:5] == [
            "setup", "child.interface.setup", "root", "root.interface.run", "1"
        ]
    else:
        assert result["code"] == "setup_busy" and result["flow_id"] == "flow-7"
        assert result["owner"]["pid"] == 1234
        assert result["recovery"]["interface"].endswith(".recover-busy")
    assert secret not in json.dumps(result)


def test_nested_managed_lifecycle_rebinds_to_outer_mcp_invocation(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches validating a nested lifecycle classification as manager status."""
    _install_authorized_path(server, monkeypatch, [], managed=False)
    signal = SetupBlocked(
        None,
        ("root.interface.run", "child.interface.setup"),
        ("child.interface.setup", "setup"),
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["code"] == "setup_managed"
    assert result["original"] == {"caller": "root", "interface": "root.interface.run", "version": 1}
    assert result["manager"]["caller"] == result["original"]["caller"]
    assert result["manager"]["arguments"]["positionals"][:5] == [
        "setup", "child.interface.setup", "root", "root.interface.run", "1"
    ]
    assert result["call_path"] == ["root.interface.run", "child.interface.setup"]


@pytest.mark.parametrize(
    ("signal", "expected_code"),
    [
        (SetupBlocked(_nested_status("setup_required"), ()), "dispatcher.error"),
        (SetupBlocked(_nested_status("setup_required"), ("other.interface.run",)), "dispatcher.error"),
        (SetupBlocked(_nested_status("setup_required"), ("root.interface.run", "not canonical")), "dispatcher.invalid_interface_id"),
        (SetupBlocked(_nested_status("ready"), ("root.interface.run",)), "dispatcher.error"),
        (SetupBlocked({"code": "setup_required"}, ("root.interface.run",)), "dispatcher.manager_response_invalid"),
        (SetupBlocked(_nested_status("setup_required"), ("root.interface.run",) * 33), "dispatcher.error"),
        (_with_private_field(SetupBlocked(_nested_status("setup_required"), ("root.interface.run",))), "dispatcher.error"),
        (SetupBlocked({}, ("root.interface.run",), ("child.interface.run", "setup")), "dispatcher.error"),
        (SetupBlocked(None, ("root.interface.run",), ("child.interface.setup", "invalid")), "dispatcher.error"),
    ],
)
def test_nested_setup_signal_is_strictly_validated_before_exposure(
    server, monkeypatch: pytest.MonkeyPatch, signal: SetupBlocked, expected_code: str
) -> None:
    """Catches malformed private setup transport becoming a public refusal."""
    _install_authorized_path(server, monkeypatch, [], managed=False)
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == expected_code
    assert "nested-private-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "secret"),
    [
        (
            {
                "schema_version": 1,
                "code": "setup_required",
                "root_setup_interface": "root-secret/interface.setup",
                "pending_stack": [{"interface": "child.interface.setup", "version": 1, "kind": "python", "action": "run-setup"}],
                "flow_id": None,
            },
            "root-secret",
        ),
        (
            {
                "schema_version": 1,
                "code": "setup_required",
                "root_setup_interface": "child.interface.setup",
                "pending_stack": [{"interface": "pending-secret/interface.setup", "version": 1, "kind": "python", "action": "run-setup"}],
                "flow_id": None,
            },
            "pending-secret",
        ),
    ],
)
def test_nested_status_interface_ids_are_canonical_before_exposure(
    server,
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, object],
    secret: str,
) -> None:
    """Catches child-controlled status interface IDs entering MCP output."""
    _install_authorized_path(server, monkeypatch, [], managed=False)
    signal = SetupBlocked(status, ("root.interface.run", "child.interface.run"))
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.invalid_interface_id"
    assert secret not in json.dumps(result)


def test_projection_direct_blueprint_failure_is_generic_and_redacted(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a projection lookup path or secret escaping in the MCP payload."""
    events: list[str] = []
    secret = "projection-ledger-secret"
    private_path = "/private/setup/projection/blueprint.yaml"
    _install_authorized_path(server, monkeypatch, events, managed=False)

    def fail_projection(*_args):
        error = DirectBlueprintError.from_spec(
            "D30",
            target_module_id="root",
            module_id="root",
        )
        raise error from RuntimeError(f"{private_path}; token={secret}")

    monkeypatch.setattr(server, "load_direct_setup_projection", fail_projection)
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("failed projection was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert _without_trace(result) == {
        "exit_code": 2,
        "stdout": "",
        "stderr": "",
        "dispatcher": {
            "schema_version": 1,
            "code": "dispatcher.setup_projection_unavailable",
            "message": (
                "MCP preflight could not evaluate the managed-setup projection "
                "for `root.interface.run`."
            ),
            "interface_id": "root.interface.run",
            "cause": {
                "code": "dispatcher.source_not_found",
                "message": (
                    "The dispatcher could not inspect the source path for module `root`."
                ),
                "module_id": "root",
            },
        },
    }
    assert events == ["authorize"]
    encoded = json.dumps(result, sort_keys=True)
    assert private_path not in encoded
    assert secret not in encoded


def test_projection_rejects_direct_blueprint_error_with_runner_row(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=False)

    def fail_projection(*_args):
        raise DirectBlueprintError.from_spec("R01")

    monkeypatch.setattr(server, "load_direct_setup_projection", fail_projection)
    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["dispatcher"]["code"] == "dispatcher.setup_projection_unavailable"
    assert "cause" not in result["dispatcher"]


def test_projection_rejects_mutated_unregistered_direct_blueprint_error(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=False)
    error = DirectBlueprintError.from_spec("D30", module_id="root")
    error._entry_id = "D999"
    monkeypatch.setattr(
        server,
        "load_direct_setup_projection",
        lambda *_args: (_ for _ in ()).throw(error),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert result["dispatcher"]["code"] == "dispatcher.setup_projection_unavailable"
    assert "cause" not in result["dispatcher"]


def test_mcp_contains_plain_invocation_error_as_registered_d68(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    secret = "plain-invocation-private-detail"
    _install_authorized_path(server, monkeypatch, events, managed=False)
    unregistered = DispatcherError.from_spec(
        "D10", interface_id="private.interface.run"
    )
    unregistered._entry_id = "D999"
    unregistered.args = (secret,)

    for error in (InvocationError(secret), unregistered):
        monkeypatch.setattr(
            server,
            "authorize_direct_invocation",
            lambda **_kwargs: (_ for _ in ()).throw(error),
        )
        result = server.invoke("root", "root.interface.run", 1, _arguments(server))

        assert _without_trace(result) == {
            "exit_code": 2,
            "stdout": "",
            "stderr": "",
            "dispatcher": DispatcherError.from_spec("D68").as_payload(),
        }
        assert secret not in json.dumps(result)


def test_mcp_persistence_rejects_unsafe_plugin_data_root(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    plugin_data = tmp_path / "plugin-data"
    try:
        plugin_data.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlinks may be unavailable; alternate=child-directory persistence safety is covered without symlinks
        pytest.skip(f"directory symlinks unavailable: {exc}")
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setattr(
        server,
        "resolve_famulus_paths",
        lambda **_kwargs: SimpleNamespace(
            plugin_data=plugin_data,
            assistant_host="codex",
            logging_path=plugin_data / "milestones",
        ),
    )

    with pytest.raises(DispatcherError) as caught:
        server.configure_plugin_persistence()

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D50", kind="root"
    ).as_payload()


def test_mcp_persistence_creation_failure_is_registered_and_redacted(
    server, tmp_path: Path
) -> None:
    child = tmp_path / "private-parent-name" / "milestones"

    with pytest.raises(DispatcherError) as caught:
        server._confined_directory(tmp_path, child)

    assert caught.value.as_payload() == DispatcherError.from_spec("D51").as_payload()
    assert str(child) not in json.dumps(caught.value.as_payload())


def test_ordered_arguments_with_positionals_emit_d54(server) -> None:
    arguments = server.OrderedArguments(
        positionals=("unexpected",), options=[], stdin=None
    )

    with pytest.raises(DispatcherError) as caught:
        server.caller_argv(arguments)

    assert caught.value.as_payload() == DispatcherError.from_spec("D54").as_payload()


def test_main_classifies_missing_declared_mcp_package(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "require_python", lambda: None)
    monkeypatch.setattr(server, "configure_plugin_persistence", lambda: None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)

    with pytest.raises(DispatcherError) as caught:
        server.main()

    assert caught.value.as_payload() == DispatcherError.from_spec(
        "D52", module_name="mcp"
    ).as_payload()


def test_main_classifies_transitive_server_import_failure_as_d53(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenFastMCP(ModuleType):
        def __getattr__(self, _name: str):
            raise ModuleNotFoundError("private transitive import", name="private_dependency")

    monkeypatch.setattr(server, "require_python", lambda: None)
    monkeypatch.setattr(server, "configure_plugin_persistence", lambda: None)
    monkeypatch.setitem(
        sys.modules, "mcp.server.fastmcp", BrokenFastMCP("mcp.server.fastmcp")
    )

    with pytest.raises(DispatcherError) as caught:
        server.main()

    assert caught.value.as_payload() == DispatcherError.from_spec("D53").as_payload()
    assert "private_dependency" not in json.dumps(caught.value.as_payload())


@pytest.mark.parametrize("stage", ["construct", "run"])
def test_main_classifies_server_startup_failures_as_d53(
    server, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    class FailingFastMCP:
        def __init__(self, _name: str) -> None:
            if stage == "construct":
                raise RuntimeError("private constructor failure")

        def tool(self):
            return lambda function: function

        def run(self, *, transport: str) -> None:
            assert transport == "stdio"
            raise RuntimeError("private run failure")

    module = ModuleType("mcp.server.fastmcp")
    module.FastMCP = FailingFastMCP
    monkeypatch.setattr(server, "require_python", lambda: None)
    monkeypatch.setattr(server, "configure_plugin_persistence", lambda: None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", module)

    with pytest.raises(DispatcherError) as caught:
        server.main()

    assert caught.value.as_payload() == DispatcherError.from_spec("D53").as_payload()
    assert "private" not in json.dumps(caught.value.as_payload())


@pytest.mark.parametrize(
    ("entry_id", "context"),
    [
        ("D49", {"major": 3, "minor": 10}),
        ("D50", {"kind": "root"}),
        ("D51", {}),
        ("D52", {"module_name": "mcp"}),
        ("D53", {}),
    ],
)
def test_mcp_executable_boundary_redacts_registered_startup_causes(
    entry_id: str, context: dict[str, object]
) -> None:
    script = """
import json
import sys
import mcp_server
from officina.dispatcher.errors import DispatcherError

entry_id, context = json.loads(sys.argv[1])
def fail():
    raise DispatcherError.from_spec(entry_id, **context) from RuntimeError("private-secret")
mcp_server.main = fail
raise SystemExit(mcp_server._main_entrypoint())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps([entry_id, context])],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    expected = DispatcherError.from_spec(entry_id, **context)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"error: {expected}\n"
    assert "private-secret" not in result.stderr
    assert "Traceback" not in result.stderr


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
    assert result["dispatcher"]["code"] == "dispatcher.invalid_request"


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
    assert result["dispatcher"]["code"] == "dispatcher.invalid_request"


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
                "interface": "root.interface.run",
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
    assert events == [
        "authorize",
        "authorize-markdown-call",
        "compile-authorized",
        "launch",
    ]


@pytest.mark.parametrize(
    ("field", "mismatch"),
    [
        ("flow_id", "flow-2"),
        ("interface", "other.interface.run"),
        ("version", 2),
    ],
)
def test_setup_authorization_response_must_match_requested_identity(
    server,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    mismatch: object,
) -> None:
    """Catches a mismatched manager response minting a transitive setup grant."""
    events: list[str] = []
    _install_authorized_path(server, monkeypatch, events, managed=True)
    authorization = {
        "schema_version": 1,
        "flow_id": "flow-1",
        "operation": "setup",
        "state": "authorized-markdown-call",
        "interface": "root.interface.run",
        "version": 1,
        "current_step": None,
        "original": None,
        "resume_original": False,
    }
    authorization[field] = mismatch
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: (
            events.append(operation) or authorization
        ),
    )
    monkeypatch.setattr(
        server,
        "_run_resolved_invocation",
        lambda *_args, **_kwargs: pytest.fail(
            "mismatched setup authorization launched the target"
        ),
    )

    result = server.invoke(
        "root",
        "root.interface.run",
        1,
        _arguments(server),
        setup_flow_id="flow-1",
    )

    assert _without_trace(result) == {
        "exit_code": 2,
        "stdout": "",
        "stderr": "",
        "dispatcher": DispatcherError.from_spec(
            "D58", operation="authorize-markdown-call"
        ).as_payload(),
    }
    assert events == ["authorize", "authorize-markdown-call"]


def _write_setup_authorization_chain_module(
    repository: Path,
    module_id: str,
    target_module_id: str | None,
) -> None:
    from tests.test_dispatcher_direct_setup import _clone_module

    module = _clone_module(repository, module_id, managed=False)
    module_blueprint_path = module / "blueprint.yaml"
    module_blueprint = yaml.safe_load(
        module_blueprint_path.read_text(encoding="utf-8")
    )
    module_blueprint["discovery"] = {"mechanism": "skill"}
    module_blueprint_path.write_text(
        yaml.safe_dump(module_blueprint, sort_keys=False),
        encoding="utf-8",
    )
    target_interface = (
        None
        if target_module_id is None
        else f"{target_module_id}.interface.execute"
    )
    uses_interfaces = (
        []
        if target_interface is None
        else [{"interface": target_interface, "version": 1}]
    )
    blueprint_path = module / "blueprints" / "lifecycle.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["uses_interfaces"] = uses_interfaces
    source_interface = blueprint["interfaces"][
        f"{module_id}.source.setup.interface.setup"
    ]
    source_interface["uses_interfaces"] = uses_interfaces
    blueprint_path.write_text(
        yaml.safe_dump(blueprint, sort_keys=False),
        encoding="utf-8",
    )
    if target_module_id is None:
        body = (
            "        context = runtime_dispatch_context(self)\n"
            "        print(json.dumps({'setup_preflight_authorized': "
            "context.setup_preflight_authorized}))\n"
            "        return 0\n"
        )
        imports = "import json\n"
        dispatches = ""
    else:
        body = (
            "        result = self.dispatch('next', text=True)\n"
            "        print(result.stdout, end='')\n"
            "        return result.returncode\n"
        )
        imports = ""
        dispatches = (
            "    dispatches = {'next': DispatchCall(\n"
            f"        caller_module_id='{module_id}',\n"
            f"        target_module_id='{target_module_id}',\n"
            "        interface='execute',\n"
            "    )}\n"
        )
    (module / "python_canary.py").write_text(
        "from __future__ import annotations\n"
        f"{imports}"
        "from officina.runtime.python_machine_interface import (\n"
        "    DispatchCall, PythonMachineInterface, runtime_dispatch_context,\n"
        ")\n"
        "class SetupInterface(PythonMachineInterface):\n"
        f"{dispatches}"
        "    def run(self, _args):\n"
        f"{body}",
        encoding="utf-8",
    )


def test_setup_authorization_crosses_two_real_subprocess_dispatches(
    server,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches any seam dropping the setup grant before a grandchild runs."""
    from tests.test_dispatcher_direct_setup import _configuration

    configuration = _configuration(tmp_path / "repository")
    repository = configuration.repository_root
    _write_setup_authorization_chain_module(repository, "root", "middle")
    _write_setup_authorization_chain_module(repository, "middle", "leaf")
    _write_setup_authorization_chain_module(repository, "leaf", None)
    monkeypatch.setattr(server, "ROOT", repository)
    monkeypatch.setattr(
        server,
        "_manager_call",
        lambda _caller, operation, _arguments: {
            "schema_version": 1,
            "flow_id": "flow-1",
            "operation": "setup",
            "state": "authorized-markdown-call",
            "interface": "root.interface.execute",
            "version": 1,
            "current_step": None,
            "original": None,
            "resume_original": False,
        }
        if operation == "authorize-markdown-call"
        else pytest.fail(f"unexpected manager operation: {operation}"),
    )

    result = server.invoke(
        "root",
        "root.interface.execute",
        1,
        server.CompactArguments(positionals=[], options={}, stdin=None),
        setup_flow_id="flow-1",
    )

    assert result["exit_code"] == 0, result
    assert json.loads(result["stdout"]) == {
        "setup_preflight_authorized": True
    }


def test_setup_flow_id_with_unvalidated_authorization_result_is_invalid(
    server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches coercing an authorization-flow failure into a status result."""
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

    assert _without_trace(result) == {
        "exit_code": 2,
        "stdout": "",
        "stderr": "",
        "dispatcher": DispatcherError.from_spec(
            "D58", operation="authorize-markdown-call"
        ).as_payload(),
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
            "current_step": "leaf.interface.setup",
            "owner": None,
        }

    monkeypatch.setattr(server, "_manager_call", manager_call)
    monkeypatch.setattr(
        server,
        "materialize_authorized_invocation",
        lambda *_args, **_kwargs: pytest.fail("busy target was compiled"),
    )

    result = server.invoke("root", "root.interface.run", 1, _arguments(server))

    assert _without_trace(result) == {
        "code": "setup_busy",
        "flow_id": "flow-7",
        "root_setup_interface": "root.interface.setup",
        "current_step": "leaf.interface.setup",
        "owner": None,
        "message": "Setup root.interface.setup is busy; its owner is unknown.",
        "recovery": {
            "interface": "setup-interface-manager._rtx.interface.recover-busy",
            "arguments": {"positionals": ["flow-7"], "options": {"--force": True}, "stdin": None},
        },
    }
    assert events == ["authorize", "status"]
