from __future__ import annotations

from officina.dispatcher.core import InvocationError
from officina.dispatcher.errors import (
    DispatcherError,
    InterfaceNotFoundError,
    UnauthorizedCallerError,
)


def test_dispatcher_error_has_schema_version_and_code():
    err = InterfaceNotFoundError(
        caller_module_id="install-assistant-tools",
        target_module_id="install",
        interface_id="install.interface.does-not-exist",
    )
    payload = err.as_payload()
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.interface_not_found"
    assert payload["caller_module_id"] == "install-assistant-tools"
    assert payload["target_module_id"] == "install"


def test_dispatcher_error_payload_never_contains_credentials():
    err = UnauthorizedCallerError(
        caller_module_id="rogue-module",
        target_module_id="common",
        interface_id="common.interface.famulus-paths",
    )
    payload = err.as_payload()
    dumped = str(payload)
    assert "token" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_dispatcher_error_is_still_an_invocation_error_subclass():
    err = InterfaceNotFoundError(
        caller_module_id="a", target_module_id="b", interface_id="c",
    )
    assert isinstance(err, InvocationError)


def test_dispatcher_error_str_is_a_readable_message():
    err = InterfaceNotFoundError(
        caller_module_id="a", target_module_id="b", interface_id="b.interface.missing",
    )
    assert "b.interface.missing" in str(err)


def test_dispatcher_error_base_class_carries_module_ids():
    err = DispatcherError(
        "something went wrong",
        caller_module_id="caller",
        target_module_id="target",
    )
    payload = err.as_payload()
    assert payload == {
        "schema_version": 1,
        "code": "dispatcher.error",
        "caller_module_id": "caller",
        "target_module_id": "target",
        "message": "something went wrong",
    }
