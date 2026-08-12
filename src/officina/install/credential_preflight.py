"""Bounded native credential-store preflight with a closed child protocol.

The parent owns only a random, non-secret target identifier. Every probe value
is generated inside a disposable child process, and the child returns only a
small installer-authored status record. Child stdout and stderr are discarded,
so backend exception text and accidental prints cannot become diagnostics.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import secrets
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from multiprocessing.connection import Connection
from typing import Callable

from officina.common import secret_store
from officina.install.credential_preflight_linux_osx_windows import (
    _ProcessContainment,
    _establish_child_containment,
    _prepare_parent_containment,
    _terminate_and_verify_tree,
    _terminate_direct_process,
    _windows_create_kill_on_close_job,
    _windows_terminate_and_verify_job,
)


NATIVE_BACKENDS = secret_store.NATIVE_BACKENDS
_SCHEMA_VERSION = 1
_PROBE_NAMESPACE = "credential-preflight"
_MAX_TARGET_ATTEMPTS = 8
_MAX_CHILD_MESSAGE_BYTES = 1024
_VALID_BACKEND_IDENTITIES = frozenset(
    {identity for identities in NATIVE_BACKENDS.values() for identity in identities}
)


class CredentialPreflightCode(str, Enum):
    """Closed failure codes consumed by installer-authored guidance."""

    UNSUPPORTED_BACKEND = "unsupported_backend"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_LOCKED = "backend_locked"
    ROUNDTRIP_FAILED = "roundtrip_failed"
    CLEANUP_FAILED = "cleanup_failed"


@dataclass(frozen=True)
class CredentialPreflightResult:
    """Secret-free result returned by the parent process."""

    schema_version: int
    ok: bool
    code: CredentialPreflightCode | None
    backend: str

    def as_json(self) -> dict[str, object]:
        """Return the exact closed machine representation."""
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "code": None if self.code is None else self.code.value,
            "backend": self.backend,
        }


@dataclass(frozen=True)
class _ChildOutcome:
    result: CredentialPreflightResult | None
    absent: bool | None
    abnormal: bool


def probe_native_store(
    *,
    backend: secret_store.SecretBackend | None = None,
    token_factory: Callable[[], str] = secrets.token_urlsafe,
    timeout_seconds: float = 5.0,
) -> CredentialPreflightResult:
    """Prove that one audited native backend can store, read, and remove a value.

    ``backend`` and ``token_factory`` are injectable for deterministic tests.
    Production callers omit both. Unsupported injected backends are rejected in
    the parent before any child or store operation begins.
    """
    if (
        not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    if not callable(token_factory):
        raise TypeError("token_factory must be callable")

    known_backend = "unknown"
    if backend is not None:
        try:
            known_backend = secret_store.native_backend_identity(backend)
        except secret_store.SecretStoreUnsupportedBackend:
            return _result(False, CredentialPreflightCode.UNSUPPORTED_BACKEND, _identity(backend))
        except secret_store.SecretStoreUnavailable:
            return _result(False, CredentialPreflightCode.BACKEND_UNAVAILABLE, _identity(backend))

    for _attempt in range(_MAX_TARGET_ATTEMPTS):
        target_id = _new_target_id()
        checked = _run_child(
            action="check",
            target_id=target_id,
            backend=backend,
            token_factory=token_factory,
            timeout_seconds=float(timeout_seconds),
        )
        if checked.abnormal or checked.result is None:
            cleaned = _run_child(
                action="cleanup",
                target_id=target_id,
                backend=backend,
                token_factory=token_factory,
                timeout_seconds=float(timeout_seconds),
            )
            if cleaned.abnormal or cleaned.result is None or not cleaned.result.ok:
                return _result(False, CredentialPreflightCode.CLEANUP_FAILED, known_backend)
            return _result(
                False,
                CredentialPreflightCode.BACKEND_UNAVAILABLE,
                cleaned.result.backend,
            )
        known_backend = checked.result.backend
        if not checked.result.ok:
            return checked.result
        if checked.absent:
            break
    else:
        return _result(False, CredentialPreflightCode.ROUNDTRIP_FAILED, known_backend)

    probed = _run_child(
        action="probe",
        target_id=target_id,
        backend=backend,
        token_factory=token_factory,
        timeout_seconds=float(timeout_seconds),
    )
    if not probed.abnormal and probed.result is not None:
        return probed.result

    cleaned = _run_child(
        action="cleanup",
        target_id=target_id,
        backend=backend,
        token_factory=token_factory,
        timeout_seconds=float(timeout_seconds),
    )
    if cleaned.abnormal or cleaned.result is None or not cleaned.result.ok:
        return _result(False, CredentialPreflightCode.CLEANUP_FAILED, known_backend)
    return _result(False, CredentialPreflightCode.ROUNDTRIP_FAILED, cleaned.result.backend)


def _new_target_id() -> str:
    """Generate the parent's collision-checked, non-secret target identifier."""
    return f"native-preflight-{uuid.uuid4().hex}"


def _run_child(
    *,
    action: str,
    target_id: str,
    backend: secret_store.SecretBackend | None,
    token_factory: Callable[[], str],
    timeout_seconds: float,
) -> _ChildOutcome:
    context = _process_context(backend)
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=_child_entry,
        args=(child_connection, action, target_id, backend, token_factory),
        daemon=False,
    )
    try:
        process.start()
    except BaseException:
        parent_connection.close()
        child_connection.close()
        return _ChildOutcome(None, None, True)
    child_connection.close()

    deadline = time.monotonic() + timeout_seconds
    containment = _prepare_parent_containment(process)
    if containment is None:
        parent_connection.close()
        _terminate_direct_process(process)
        return _ChildOutcome(None, None, True)
    if not _receive_control_message(parent_connection, b"R", deadline):
        parent_connection.close()
        _terminate_and_verify_tree(containment, process)
        return _ChildOutcome(None, None, True)
    try:
        parent_connection.send_bytes(b"G")
    except (BrokenPipeError, EOFError, OSError):
        parent_connection.close()
        _terminate_and_verify_tree(containment, process)
        return _ChildOutcome(None, None, True)

    outcome: _ChildOutcome | None = None
    try:
        remaining = max(0.0, deadline - time.monotonic())
        if parent_connection.poll(remaining):
            outcome = _receive_child_message(parent_connection, action=action)
        else:
            _terminate_and_verify_tree(containment, process)
            return _ChildOutcome(None, None, True)
    finally:
        parent_connection.close()

    if not _terminate_and_verify_tree(containment, process):
        return _ChildOutcome(None, None, True)
    if process.exitcode != 0:
        return _ChildOutcome(None, None, True)
    return outcome if outcome is not None else _ChildOutcome(None, None, True)


def _process_context(backend: secret_store.SecretBackend | None):
    """Use spawn in production; POSIX injected doubles use a forked test boundary."""
    if backend is not None and "fork" in multiprocessing.get_all_start_methods():
        return multiprocessing.get_context("fork")
    return multiprocessing.get_context("spawn")


def _receive_control_message(
    connection: Connection,
    expected: bytes,
    deadline: float,
) -> bool:
    remaining = max(0.0, deadline - time.monotonic())
    try:
        if not connection.poll(remaining):
            return False
        return connection.recv_bytes(maxlength=len(expected)) == expected
    except (EOFError, OSError, ValueError):
        return False


def _child_entry(
    connection: Connection,
    action: str,
    target_id: str,
    backend: secret_store.SecretBackend | None,
    token_factory: Callable[[], str],
) -> None:
    if not _establish_child_containment():
        connection.close()
        return
    try:
        connection.send_bytes(b"R")
        if connection.recv_bytes(maxlength=1) != b"G":
            connection.close()
            return
    except (BrokenPipeError, EOFError, OSError, ValueError):
        connection.close()
        return
    if not _discard_process_output():
        try:
            connection.send_bytes(
                _encode_child_message(_result(
                    False,
                    CredentialPreflightCode.BACKEND_UNAVAILABLE,
                    "unknown",
                ).as_json())
            )
        finally:
            connection.close()
        return
    try:
        payload = _execute_child_action(
            action=action,
            target_id=target_id,
            backend=backend,
            token_factory=token_factory,
        )
        connection.send_bytes(_encode_child_message(payload))
    except BaseException:
        try:
            connection.send_bytes(
                _encode_child_message(_result(
                    False,
                    CredentialPreflightCode.BACKEND_UNAVAILABLE,
                    "unknown",
                ).as_json())
            )
        except BaseException:
            pass
    finally:
        connection.close()


def _discard_process_output() -> bool:
    """Redirect both Python and file-descriptor output away from the parent."""
    try:
        devnull_fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        if devnull_fd not in (1, 2):
            os.close(devnull_fd)
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except BaseException:
        return False
    return True


def _execute_child_action(
    *,
    action: str,
    target_id: str,
    backend: secret_store.SecretBackend | None,
    token_factory: Callable[[], str],
) -> dict[str, object]:
    try:
        selected, backend_name = _resolve_backend(backend)
    except BaseException as exc:
        return _result(False, _code_for_exception(exc), _identity(backend)).as_json()

    if action == "check":
        try:
            absent = selected.lookup(_PROBE_NAMESPACE, target_id) is None
        except BaseException as exc:
            return _result(False, _code_for_exception(exc), backend_name).as_json()
        payload = _result(True, None, backend_name).as_json()
        payload["absent"] = absent
        return payload
    if action == "cleanup":
        return _cleanup(selected, target_id, backend_name).as_json()
    if action == "probe":
        return _probe(selected, target_id, backend_name, token_factory).as_json()
    return _result(False, CredentialPreflightCode.BACKEND_UNAVAILABLE, backend_name).as_json()


def _resolve_backend(
    backend: secret_store.SecretBackend | None,
) -> tuple[secret_store.SecretBackend, str]:
    if backend is None:
        selected = secret_store.KeyringSecretBackend()
        return selected, selected.backend_identity()
    return backend, secret_store.native_backend_identity(backend)


def _probe(
    backend: secret_store.SecretBackend,
    target_id: str,
    backend_name: str,
    token_factory: Callable[[], str],
) -> CredentialPreflightResult:
    failure: CredentialPreflightCode | None = None
    store_attempted = False
    try:
        probe_secret = token_factory()
        if not isinstance(probe_secret, str) or not probe_secret:
            return _result(False, CredentialPreflightCode.ROUNDTRIP_FAILED, backend_name)
        store_attempted = True
        backend.store(_PROBE_NAMESPACE, target_id, probe_secret)
        if backend.lookup(_PROBE_NAMESPACE, target_id) != probe_secret:
            failure = CredentialPreflightCode.ROUNDTRIP_FAILED
    except BaseException as exc:
        failure = _code_for_exception(exc)

    if store_attempted:
        cleanup = _cleanup(backend, target_id, backend_name)
        if not cleanup.ok:
            return cleanup
    if failure is not None:
        return _result(False, failure, backend_name)
    return _result(True, None, backend_name)


def _cleanup(
    backend: secret_store.SecretBackend,
    target_id: str,
    backend_name: str,
) -> CredentialPreflightResult:
    try:
        if backend.lookup(_PROBE_NAMESPACE, target_id) is None:
            return _result(True, None, backend_name)
        if backend.clear(_PROBE_NAMESPACE, target_id) is not True:
            return _result(False, CredentialPreflightCode.CLEANUP_FAILED, backend_name)
        if backend.lookup(_PROBE_NAMESPACE, target_id) is not None:
            return _result(False, CredentialPreflightCode.CLEANUP_FAILED, backend_name)
    except BaseException:
        return _result(False, CredentialPreflightCode.CLEANUP_FAILED, backend_name)
    return _result(True, None, backend_name)


def _code_for_exception(exc: BaseException) -> CredentialPreflightCode:
    if isinstance(exc, secret_store.SecretStoreUnsupportedBackend):
        return CredentialPreflightCode.UNSUPPORTED_BACKEND
    if isinstance(exc, secret_store.SecretStoreUnavailable):
        return CredentialPreflightCode.BACKEND_UNAVAILABLE
    if isinstance(exc, secret_store.SecretStoreLocked):
        return CredentialPreflightCode.BACKEND_LOCKED
    name = type(exc).__name__.lower()
    if name in {"keyringlocked", "lockedexception"}:
        return CredentialPreflightCode.BACKEND_LOCKED
    if name in {
        "initerror",
        "nokeyringerror",
        "secretservicenotavailableexception",
        "dbuserror",
        "dbusexception",
        "serviceunknown",
        "connectionerror",
    }:
        return CredentialPreflightCode.BACKEND_UNAVAILABLE
    return CredentialPreflightCode.ROUNDTRIP_FAILED


class _DuplicateKeyError(ValueError):
    """Raised when a child JSON object repeats a field name."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _encode_child_message(payload: dict[str, object]) -> bytes:
    """Encode one bounded, canonical UTF-8 JSON result frame."""
    message = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(message) > _MAX_CHILD_MESSAGE_BYTES:
        raise ValueError("child message exceeds protocol bound")
    return message


def _receive_child_message(connection: Connection, *, action: str) -> _ChildOutcome:
    """Receive bytes under the protocol bound without invoking pickle."""
    try:
        message = connection.recv_bytes(maxlength=_MAX_CHILD_MESSAGE_BYTES)
    except (EOFError, OSError, ValueError):
        return _ChildOutcome(None, None, True)
    return _decode_child_message(message, action=action)


def _decode_child_message(message: bytes, *, action: str) -> _ChildOutcome:
    """Decode and strictly validate one complete child JSON frame."""
    if not isinstance(message, bytes) or len(message) > _MAX_CHILD_MESSAGE_BYTES:
        return _ChildOutcome(None, None, True)
    try:
        text = message.decode("utf-8", errors="strict")
        decoder = json.JSONDecoder(
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        payload, end = decoder.raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _ChildOutcome(None, None, True)
    if end != len(text):
        return _ChildOutcome(None, None, True)
    return _parse_child_payload(payload, action=action)


def _parse_child_payload(payload: object, *, action: str) -> _ChildOutcome:
    if not isinstance(payload, dict):
        return _ChildOutcome(None, None, True)
    base_fields = {"schema_version", "ok", "code", "backend"}
    if not base_fields.issubset(payload):
        return _ChildOutcome(None, None, True)
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != _SCHEMA_VERSION:
        return _ChildOutcome(None, None, True)
    ok = payload.get("ok")
    backend = payload.get("backend")
    code_value = payload.get("code")
    if type(ok) is not bool or type(backend) is not str:
        return _ChildOutcome(None, None, True)
    if backend not in _VALID_BACKEND_IDENTITIES | {"unknown"}:
        return _ChildOutcome(None, None, True)
    if code_value is None:
        code = None
    else:
        try:
            code = CredentialPreflightCode(code_value)
        except (TypeError, ValueError):
            return _ChildOutcome(None, None, True)
    if ok != (code is None):
        return _ChildOutcome(None, None, True)
    if ok and backend == "unknown":
        return _ChildOutcome(None, None, True)
    absent: bool | None = None
    expected_fields = set(base_fields)
    if action == "check" and ok:
        expected_fields.add("absent")
        absent_value = payload.get("absent")
        if type(absent_value) is not bool:
            return _ChildOutcome(None, None, True)
        absent = absent_value
    if set(payload) != expected_fields:
        return _ChildOutcome(None, None, True)
    return _ChildOutcome(
        CredentialPreflightResult(_SCHEMA_VERSION, ok, code, backend),
        absent,
        False,
    )


def _identity(backend: object | None) -> str:
    if backend is None:
        return "unknown"
    backend_type = type(backend)
    return f"{backend_type.__module__}.{backend_type.__name__}"


def _result(
    ok: bool,
    code: CredentialPreflightCode | None,
    backend: str,
) -> CredentialPreflightResult:
    return CredentialPreflightResult(_SCHEMA_VERSION, ok, code, backend)


_STATIC_GUIDANCE = {
    CredentialPreflightCode.UNSUPPORTED_BACKEND: "The selected credential backend is unsupported.",
    CredentialPreflightCode.BACKEND_UNAVAILABLE: "The native credential service is unavailable.",
    CredentialPreflightCode.BACKEND_LOCKED: "The native credential store is locked.",
    CredentialPreflightCode.ROUNDTRIP_FAILED: "The native credential roundtrip failed.",
    CredentialPreflightCode.CLEANUP_FAILED: "Credential probe cleanup could not be verified.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit closed machine JSON")
    args = parser.parse_args(argv)
    result = probe_native_store()
    if args.json:
        print(json.dumps(result.as_json(), separators=(",", ":"), sort_keys=True))
    elif result.ok:
        print("Native credential storage is available.")
    else:
        assert result.code is not None
        print(_STATIC_GUIDANCE[result.code])
    return 0 if result.ok else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
