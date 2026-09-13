from __future__ import annotations

import pytest

from officina.dispatcher import errors as dispatcher_errors
from officina.dispatcher import core as dispatcher_core
from officina.dispatcher.core import InvocationError
from officina.dispatcher.errors import (
    DirectBlueprintError,
    DispatcherError,
    InterfaceNotFoundError,
    ReducedCause,
    UnauthorizedCallerError,
)


def test_registered_error_rejects_unknown_entries_and_context():
    specs = getattr(dispatcher_errors, "DISPATCHER_ERROR_SPECS", {})
    assert specs["D56"].code == "dispatcher.manager_invocation_failed"
    with pytest.raises(KeyError):
        DispatcherError.from_spec("D999")
    with pytest.raises(ValueError, match="unsupported context"):
        DispatcherError.from_spec("D56", operation="status", secret="do-not-leak")
    with pytest.raises(ValueError, match="missing context"):
        DispatcherError.from_spec("D04", caller_module_id="caller", interface_id="x")


def test_registered_error_rejects_unregistered_enum_context_values():
    with pytest.raises(ValueError, match="unregistered value"):
        DispatcherError.from_spec("D27", reason="contains supplied caller text")
    with pytest.raises(ValueError, match="unregistered value"):
        DispatcherError.from_spec("R11", reason="contains child output")
    with pytest.raises(ValueError, match="unregistered value"):
        DispatcherError.from_spec(
            "D38",
            target_module_id="target",
            interface_id="target.interface.run",
            reason="arbitrary secret",
        )


@pytest.mark.parametrize(
    ("entry_id", "context"),
    [
        ("D03", {"caller_module_id": {"not": "a string"}, "interface_id": "x"}),
        ("D03", {"caller_module_id": "caller", "interface_id": ["x"]}),
        ("D01", {"schema_version": True}),
        ("D69", {"interface_id": "x", "timeout": "slow"}),
    ],
)
def test_registered_error_rejects_invalid_context_types(entry_id, context):
    with pytest.raises(ValueError, match="invalid value type"):
        DispatcherError.from_spec(entry_id, **context)


def test_registered_error_rejects_arbitrary_cause_objects():
    with pytest.raises(ValueError, match="registered cause"):
        DispatcherError.from_spec("D56", operation="status", cause=object())


def test_registered_error_emits_only_declared_module_identities():
    undeclared = DispatcherError.from_spec(
        "R01",
        caller_module_id="caller",
        target_module_id="target",
    ).as_payload()
    declared = DispatcherError.from_spec(
        "D03",
        caller_module_id="caller",
        target_module_id="target",
        interface_id="target.interface.run",
    ).as_payload()

    assert "caller_module_id" not in undeclared
    assert "target_module_id" not in undeclared
    assert declared["caller_module_id"] == "caller"
    assert "target_module_id" not in declared


@pytest.mark.parametrize("identity", ["caller_module_id", "target_module_id"])
def test_registered_error_rejects_non_string_optional_identities(identity):
    with pytest.raises(ValueError, match="invalid value type"):
        DispatcherError.from_spec(
            "D45",
            interface_id="target.interface.run",
            **{identity: {"secret": "do-not-emit"}},
        )


def test_reduced_cause_requires_allowlist_validation():
    with pytest.raises(TypeError, match="validated factory"):
        ReducedCause("setup.example", "arbitrary", {})
    with pytest.raises(ValueError, match="unregistered external cause"):
        ReducedCause._from_allowed(
            "setup.example",
            "arbitrary",
            allowed_messages={"setup.example": frozenset({"factual"})},
            allowed_clues={},
        )

    with pytest.raises(ValueError, match="unregistered external cause clue"):
        ReducedCause._from_allowed(
            "setup.example",
            "factual",
            clues=("Unsupported advice.",),
            allowed_messages={"setup.example": frozenset({"factual"})},
            allowed_clues={"setup.example": "Possible concurrency."},
        )

    cause = ReducedCause._from_allowed(
        "setup.example",
        "factual",
        clues=("Possible concurrency.",),
        allowed_messages={"setup.example": frozenset({"factual"})},
        allowed_clues={"setup.example": "Possible concurrency."},
    )
    assert cause.as_payload() == {
        "code": "setup.example",
        "message": "factual",
        "clues": ["Possible concurrency."],
    }


def test_dispatcher_registry_covers_the_complete_scoped_catalogue():
    expected = {f"D{index:02}" for index in range(1, 72)} - {"D67"}

    assert {
        entry_id
        for entry_id in dispatcher_errors.DISPATCHER_ERROR_SPECS
        if entry_id.startswith("D")
    } == expected


def test_registered_error_renders_exact_message_and_omits_empty_optionals():
    error = DispatcherError.from_spec("D56", operation="status")

    assert error.as_payload() == {
        "schema_version": 1,
        "code": "dispatcher.manager_invocation_failed",
        "message": "The dispatcher could not obtain a valid `status` result from the setup manager.",
    }


@pytest.mark.parametrize(
    ("entry_id", "code", "message", "context"),
    [
        ("R01", "dispatcher.runner_request_invalid", "Python interface runner requires a gateway path and process entry.", {}),
        ("R02", "dispatcher.runner_request_invalid", "Python interface runner option `--source-fd` is missing required arguments.", {"option": "--source-fd"}),
        ("R03", "dispatcher.runner_request_invalid", "Python interface runner option `--source-fd` was supplied more than once.", {"option": "--source-fd"}),
        ("R04", "dispatcher.runner_request_invalid", "Python interface runner option `--source-fd` requires an integer descriptor.", {"option": "--source-fd"}),
        ("R05", "dispatcher.runner_request_invalid", "Python interface runner requires package snapshot path and SHA-256 together.", {}),
        ("R06", "dispatcher.runner_request_invalid", "Python interface runner requires logical package and entry point together.", {}),
        ("R07", "dispatcher.runner_request_invalid", "Python interface runner physical package prefix is invalid for this request.", {}),
        ("R08", "dispatcher.runner_request_invalid", "Python interface runner cannot combine package snapshot and descriptor transports.", {}),
        ("R09", "dispatcher.runner_request_invalid", "Python interface runner confined-root request is inconsistent.", {}),
        ("R10", "dispatcher.runner_target_invalid", "Python interface runner received invalid gateway or process-entry metadata.", {}),
        ("R11", "dispatcher.runner_source_invalid", "Python interface runner received an invalid bound package source.", {"reason": "path-invalid"}),
        ("R12", "dispatcher.runner_snapshot_invalid", "Python interface runner rejected its package snapshot: the expected digest is invalid.", {"reason": "the expected digest is invalid"}),
        ("R13", "dispatcher.runner_source_invalid", "Python interface runner could not load the gateway source within its validated package boundary.", {}),
        ("R14", "dispatcher.runner_import_rejected", "Python interface runner rejected a confined import: the module is outside the validated package.", {"reason": "the module is outside the validated package"}),
        ("R15", "dispatcher.runner_import_failed", "Python interface runner could not load the resolved gateway module.", {}),
        ("R16", "dispatcher.runner_interface_invalid", "Resolved Python gateway has an invalid machine-interface entry: the entry is absent.", {"reason": "the entry is absent"}),
        ("R17", "dispatcher.invalid_request", "The Python interface request does not match the declared interface signature.", {}),
        ("R18", "dispatcher.runner_route_smoke_failed", "Python machine-interface route-smoke execution failed.", {}),
        ("R19", "dispatcher.runner_execution_failed", "Python machine-interface execution failed before returning an exit code; completion is unknown.", {}),
        ("R20", "dispatcher.runner_interface_invalid", "Python machine interface returned an unsupported result type.", {}),
        ("R21", "dispatcher.runner_source_invalid", "Python interface runner could not construct a validated package-source snapshot.", {}),
        ("R22", "dispatcher.runner_target_invalid", "Python interface runner received a confined gateway without a logical entry point.", {}),
        ("R23", "dispatcher.runner_interface_initialization_failed", "Python interface constructor failed before request handling began.", {}),
        ("R24", "dispatcher.runner_interface_initialization_failed", "Python machine interface did not provide a valid argument parser.", {}),
        ("R25", "dispatcher.runner_request_validation_failed", "Python machine interface failed while validating request arguments.", {}),
    ],
)
def test_runner_catalogue_entries_render_transportable_payloads(
    entry_id: str,
    code: str,
    message: str,
    context: dict[str, str],
) -> None:
    payload = DispatcherError.from_spec(entry_id, **context).as_payload()

    assert payload == {
        "schema_version": 1,
        "code": code,
        "message": message,
        **context,
    }


def test_registered_error_reduces_cause_to_one_hop():
    inner = DispatcherError.from_spec("D10", interface_id="lists.interface.read")
    middle = DispatcherError.from_spec(
        "D64",
        operation="status",
        setup_error="Managed-setup state changed while this operation was updating it.",
        setup_error_code="setup.ledger_conflict",
        cause=inner,
        clues=("Another setup-manager process may have updated the ledger concurrently.",),
    )
    outer = DispatcherError.from_spec("D56", operation="status", cause=middle)

    payload = outer.as_payload()
    assert payload["cause"]["code"] == "dispatcher.manager_operation_failed"
    assert "cause" not in payload["cause"]
    assert "schema_version" not in payload["cause"]
    assert payload["cause"]["clues"] == [
        "Another setup-manager process may have updated the ledger concurrently."
    ]
    assert "recovery" not in str(payload)


def test_registered_error_rejects_unregistered_clue_text():
    with pytest.raises(ValueError, match="unregistered clue"):
        DispatcherError.from_spec(
            "D64",
            operation="status",
            setup_error="Managed-setup state changed.",
            setup_error_code="setup.ledger_conflict",
            clues=("Try reinstalling Python.",),
        )
    with pytest.raises(ValueError, match="clues must be strings"):
        DispatcherError.from_spec(
            "D64",
            operation="status",
            setup_error="Managed-setup state changed.",
            setup_error_code="setup.ledger_conflict",
            clues=({"unsafe": "value"},),
        )


def test_trace_schema_mismatch_is_a_registered_factual_error(tmp_path):
    graph = type("Graph", (), {"schema_version": 5})()

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._resolve_dispatch_metadata_for_trace(
            caller_module_id="caller",
            target="target.interface.run",
            repo_root=tmp_path,
            graph=graph,
        )

    assert caught.value.as_payload()["code"] == "dispatcher.blueprint_schema_mismatch"
    assert str(caught.value) == (
        "Dispatcher metadata trace requires blueprint graph schema 6; received schema 5."
    )


def test_unauthorized_error_keeps_gate_out_of_the_message():
    error = UnauthorizedCallerError.from_spec(
        "D04",
        caller_module_id="caller",
        target_module_id="target",
        interface_id="target.interface.run",
        gate="terminal-export",
    )

    assert str(error) == (
        "Caller `caller` is not allowed to invoke `target.interface.run`."
    )
    assert error.as_payload()["gate"] == "terminal-export"


def test_dispatcher_error_has_schema_version_and_code():
    err = InterfaceNotFoundError.from_spec(
        "D38",
        caller_module_id="milestone-logging",
        target_module_id="install",
        interface_id="install.interface.does-not-exist",
        reason="was not found",
    )
    payload = err.as_payload()
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.interface_not_found"
    assert "caller_module_id" not in payload
    assert payload["target_module_id"] == "install"


def test_dispatcher_error_payload_never_contains_credentials():
    err = UnauthorizedCallerError.from_spec(
        "D04",
        caller_module_id="rogue-module",
        target_module_id="common",
        interface_id="common.interface.famulus-paths",
        gate="terminal-export",
    )
    payload = err.as_payload()
    dumped = str(payload)
    assert "token" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_dispatcher_error_is_still_an_invocation_error_subclass():
    err = InterfaceNotFoundError.from_spec(
        "D38", caller_module_id="a", target_module_id="b", interface_id="c",
        reason="was not found",
    )
    assert isinstance(err, InvocationError)


def test_dispatcher_error_str_is_a_readable_message():
    err = InterfaceNotFoundError.from_spec(
        "D38", caller_module_id="a", target_module_id="b",
        interface_id="b.interface.missing", reason="was not found",
    )
    assert "b.interface.missing" in str(err)


def test_dispatcher_error_has_no_arbitrary_message_constructor():
    with pytest.raises(TypeError, match="registered spec"):
        DispatcherError(
            "something went wrong",
            caller_module_id="caller",
            target_module_id="target",
        )


def test_direct_blueprint_error_has_no_arbitrary_message_constructor():
    with pytest.raises(TypeError):
        DirectBlueprintError("caller-selected secret")
    with pytest.raises(TypeError):
        DirectBlueprintError(
            "caller-selected secret",
            code="dispatcher.interface_not_found",
            target_module_id="target",
        )
