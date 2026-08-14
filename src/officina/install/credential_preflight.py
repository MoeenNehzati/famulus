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
import ntpath
import os
import posixpath
import secrets
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Callable

from officina.common import certificate_records, secret_store
from officina.install.credential_preflight_linux_osx_windows import (
    _ProcessContainment,
    _establish_child_containment,
    _prepare_parent_containment,
    _terminate_and_verify_tree,
    _terminate_direct_process,
    _terminate_direct_subprocess,
    _windows_create_kill_on_close_job,
    _windows_terminate_and_verify_job,
)


NATIVE_BACKENDS = secret_store.NATIVE_BACKENDS
_SCHEMA_VERSION = 1
_PROBE_NAMESPACE = "credential-preflight"
_MAX_TARGET_ATTEMPTS = 8
_MAX_CHILD_MESSAGE_BYTES = 1024
_WORKER_PROTOCOL_VERSION = 1
_MAX_WORKER_MESSAGE_BYTES = 4096
_WORKER_MODULE = "officina.install.credential_preflight"
_MAIN_TERMINATION_SECONDS = 0.5
_EMERGENCY_CLEANUP_SECONDS = 1.0
_SHUTDOWN_RESERVE_SECONDS = _MAIN_TERMINATION_SECONDS + _EMERGENCY_CLEANUP_SECONDS
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


class CredentialWorkerCode(str, Enum):
    """Closed parent-visible failures for the retained worker protocol."""

    INVALID_REQUEST = "invalid_request"
    INVALID_STATE = "invalid_state"
    TARGET_COLLISION = "target_collision"
    UNSUPPORTED_BACKEND = "unsupported_backend"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_LOCKED = "backend_locked"
    ROUNDTRIP_FAILED = "roundtrip_failed"
    CLEANUP_FAILED = "cleanup_failed"
    CERTIFICATE_VERIFICATION_FAILED = "certificate_verification_failed"
    TIMEOUT = "timeout"
    WORKER_FAILED = "worker_failed"


class CredentialWorkerError(RuntimeError):
    """Static, bounded failure raised by the managed-worker parent client."""

    def __init__(self, code: CredentialWorkerCode) -> None:
        self.code = code
        super().__init__(_WORKER_ERROR_MESSAGES[code])


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
class CredentialVerificationResult:
    """Secret-free evidence that the retained backend verified one active key."""

    verified: bool
    key_id: str

    def as_json(self) -> dict[str, object]:
        """Return the exact closed machine representation."""
        return {"verified": self.verified, "key_id": self.key_id}


class _AnonymousDuplexChannel:
    """Two anonymous pipes exposed as one bounded duplex byte channel."""

    def __init__(self, read_fd: int, write_fd: int) -> None:
        self.read_fd = read_fd
        self.write_fd = write_fd
        self._timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def sendall(self, value: bytes) -> None:
        if self._timeout is None:
            raise RuntimeError("channel timeout is not set")
        deadline = time.monotonic() + self._timeout
        view = memoryview(value)
        while view:
            if time.monotonic() >= deadline:
                raise TimeoutError
            try:
                written = os.write(self.write_fd, view)
            except BlockingIOError:
                time.sleep(0.001)
                continue
            view = view[written:]

    def recv(self, length: int) -> bytes:
        if self._timeout is None:
            raise RuntimeError("channel timeout is not set")
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                return os.read(self.read_fd, length)
            except BlockingIOError:
                time.sleep(0.001)
        raise TimeoutError

    def close(self) -> None:
        for descriptor in (self.read_fd, self.write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _anonymous_duplex_pair() -> tuple[_AnonymousDuplexChannel, tuple[int, int]]:
    parent_read, child_write = os.pipe()
    child_read, parent_write = os.pipe()
    os.set_blocking(parent_read, False)
    os.set_blocking(parent_write, False)
    os.set_blocking(child_read, False)
    os.set_blocking(child_write, False)
    return _AnonymousDuplexChannel(parent_read, parent_write), (child_read, child_write)


_WORKER_ERROR_MESSAGES = {
    CredentialWorkerCode.INVALID_REQUEST: "credential worker rejected the request",
    CredentialWorkerCode.INVALID_STATE: "credential worker rejected the operation order",
    CredentialWorkerCode.TARGET_COLLISION: "credential preflight target is occupied",
    CredentialWorkerCode.UNSUPPORTED_BACKEND: "credential backend is unsupported",
    CredentialWorkerCode.BACKEND_UNAVAILABLE: "credential backend is unavailable",
    CredentialWorkerCode.BACKEND_LOCKED: "credential backend is locked",
    CredentialWorkerCode.ROUNDTRIP_FAILED: "credential preflight roundtrip failed",
    CredentialWorkerCode.CLEANUP_FAILED: "credential preflight cleanup failed",
    CredentialWorkerCode.CERTIFICATE_VERIFICATION_FAILED: "certificate verification failed",
    CredentialWorkerCode.TIMEOUT: "credential worker deadline expired",
    CredentialWorkerCode.WORKER_FAILED: "credential worker failed",
}


@dataclass(frozen=True)
class _ChildOutcome:
    result: CredentialPreflightResult | None
    absent: bool | None
    abnormal: bool


class _ManagedCredentialWorkerState:
    """Child-only retained backend and closed command-state machine."""

    def __init__(
        self,
        *,
        backend_factory: Callable[[], secret_store.SecretBackend] = secret_store.KeyringSecretBackend,
        token_factory: Callable[[], str] = secrets.token_urlsafe,
    ) -> None:
        self._backend_factory = backend_factory
        self._token_factory = token_factory
        self._backend: secret_store.SecretBackend | None = None
        self._backend_name = "unknown"
        self._preflight_complete = False
        self._terminal = False

    def dispatch(self, request: object) -> dict[str, object]:
        """Validate then execute one allowed request; protocol faults are terminal."""
        request_id = "unknown"
        try:
            command, request_id, payload = _parse_worker_request(request)
        except CredentialWorkerError as exc:
            self._terminal = True
            return _worker_response(request_id, False, exc.code, None)
        if self._terminal:
            return _worker_response(
                request_id, False, CredentialWorkerCode.INVALID_STATE, None
            )
        if command == "preflight":
            if self._preflight_complete:
                self._terminal = True
                return _worker_response(
                    request_id, False, CredentialWorkerCode.INVALID_STATE, None
                )
            return self._preflight(request_id, payload["target_id"])
        if command == "verify_certificate":
            if not self._preflight_complete or self._backend is None:
                self._terminal = True
                return _worker_response(
                    request_id, False, CredentialWorkerCode.INVALID_STATE, None
                )
            return self._verify(request_id, payload)
        if command == "close":
            self._terminal = True
            return _worker_response(request_id, True, None, {})
        self._terminal = True
        return _worker_response(request_id, False, CredentialWorkerCode.INVALID_REQUEST, None)

    def _preflight(self, request_id: str, target_id: str) -> dict[str, object]:
        if self._backend is None:
            try:
                self._backend = self._backend_factory()
                self._backend_name = _retained_backend_identity(self._backend)
            except BaseException as exc:
                self._terminal = True
                preflight_code = _code_for_exception(exc)
                result = _result(False, preflight_code, "unknown")
                return _worker_response(
                    request_id,
                    False,
                    CredentialWorkerCode(preflight_code.value),
                    result.as_json(),
                )
        backend = self._backend
        backend_name = self._backend_name
        try:
            if backend.lookup(_PROBE_NAMESPACE, target_id) is not None:
                return _worker_response(
                    request_id, False, CredentialWorkerCode.TARGET_COLLISION, None
                )
            result = _probe(backend, target_id, backend_name, self._token_factory)
        except BaseException as exc:
            result = _result(False, _code_for_exception(exc), backend_name)
        if not result.ok:
            self._terminal = True
            return _worker_response(
                request_id,
                False,
                CredentialWorkerCode(result.code.value),
                result.as_json(),
            )
        self._preflight_complete = True
        return _worker_response(request_id, True, None, result.as_json())
    def _verify(
        self,
        request_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        platform = payload["platform"]
        home = Path(payload["home"])
        try:
            paths = certificate_records.certificate_state_paths(
                platform=platform,
                home=home,
            )
            loaded = certificate_records.load_certificate_signing_key(
                paths.public_key_root,
                secret_backend=self._backend,
                allow_non_atomic=False,
            )
        except BaseException:
            return _worker_response(
                request_id,
                False,
                CredentialWorkerCode.CERTIFICATE_VERIFICATION_FAILED,
                None,
            )
        result = CredentialVerificationResult(True, loaded.key_id)
        return _worker_response(request_id, True, None, result.as_json())


def _retained_backend_identity(backend: secret_store.SecretBackend) -> str:
    if type(backend) is secret_store.KeyringSecretBackend:
        return backend.backend_identity()
    return secret_store.native_backend_identity(backend)


class ManagedCredentialWorker:
    """Parent client for one retained backend in an explicit managed Python."""

    def __init__(
        self,
        managed_python: str | os.PathLike[str],
        *,
        command_timeout_seconds: float = 5.0,
        total_timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(managed_python, (str, os.PathLike)):
            raise TypeError("managed_python must be an explicit executable path")
        executable = os.fspath(managed_python)
        if (
            not executable
            or "\x00" in executable
            or len(executable.encode("utf-8")) > 4096
            or not _absolute_executable_path(executable)
        ):
            raise ValueError("managed_python must be a bounded absolute executable path")
        self._command_timeout = _positive_timeout(
            command_timeout_seconds, "command_timeout_seconds"
        )
        self._total_timeout = _positive_timeout(
            total_timeout_seconds, "total_timeout_seconds"
        )
        if self._command_timeout >= self._total_timeout:
            raise ValueError("command timeout must leave total-lifetime cleanup budget")
        self._managed_python = executable
        self._creator_pid = os.getpid()
        self._deadline: float | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._containment: object | None = None
        self._channel: _AnonymousDuplexChannel | None = None
        self._child_fds: tuple[int, int] | None = None
        self._request_pending = False
        self._closed = False
        self._failed = False
        self._preflight_complete = False

    def __enter__(self) -> "ManagedCredentialWorker":
        self.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def start(self) -> None:
        """Launch the isolated module with an inherited anonymous duplex channel."""
        self._assert_owner()
        if self._closed or self._failed or self._process is not None:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_STATE)
        self._deadline = time.monotonic() + self._total_timeout
        self._channel, child_fds = _anonymous_duplex_pair()
        self._child_fds = child_fds
        mode_flag, inherited_ids, spawn_kwargs = _subprocess_channel_arguments(child_fds)
        argv = [
            self._managed_python,
            "-I",
            "-m",
            _WORKER_MODULE,
            mode_flag,
            str(inherited_ids[0]),
            str(inherited_ids[1]),
        ]
        try:
            self._process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                **spawn_kwargs,
            )
            self._containment = _prepare_subprocess_containment(self._process)
            if self._containment is None:
                raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
            _close_descriptors(child_fds)
            self._child_fds = None
            _await_worker_ready(self._channel, self._command_deadline())
        except BaseException:
            self._failed = True
            self._force_cleanup()
            raise

    def preflight(self) -> CredentialPreflightResult:
        for _attempt in range(_MAX_TARGET_ATTEMPTS):
            target_id = _new_target_id()
            try:
                result = self._request(
                    "preflight",
                    {"target_id": target_id},
                    allowed_errors=frozenset(
                        CredentialWorkerCode(code.value)
                        for code in CredentialPreflightCode
                    ),
                )
            except CredentialWorkerError as exc:
                if exc.code is CredentialWorkerCode.TARGET_COLLISION:
                    continue
                cleaned = _run_managed_cleanup(
                    self._managed_python,
                    target_id,
                    self._cleanup_deadline(),
                )
                if not cleaned:
                    raise CredentialWorkerError(
                        CredentialWorkerCode.CLEANUP_FAILED
                    ) from None
                raise
            except BaseException:
                cleaned = _run_managed_cleanup(
                    self._managed_python,
                    target_id,
                    self._cleanup_deadline(),
                )
                if not cleaned:
                    raise CredentialWorkerError(
                        CredentialWorkerCode.CLEANUP_FAILED
                    ) from None
                raise
            parsed = _parse_child_payload(result, action="probe")
            if parsed.abnormal or parsed.result is None:
                cleaned = _run_managed_cleanup(
                    self._managed_python,
                    target_id,
                    self._cleanup_deadline(),
                )
                if not cleaned:
                    raise CredentialWorkerError(
                        CredentialWorkerCode.CLEANUP_FAILED
                    ) from None
                self._fail()
            self._preflight_complete = parsed.result.ok
            return parsed.result
        raise CredentialWorkerError(CredentialWorkerCode.ROUNDTRIP_FAILED)

    def verify_certificate(
        self,
        *,
        platform: str,
        home: Path,
    ) -> CredentialVerificationResult:
        if not self._preflight_complete:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_STATE)
        result = self._request(
            "verify_certificate",
            {"platform": platform, "home": str(Path(home).absolute())},
        )
        if set(result) != {"verified", "key_id"}:
            self._fail()
        verified = result["verified"]
        key_id = result["key_id"]
        if type(verified) is not bool or verified is not True or type(key_id) is not str:
            self._fail()
        return CredentialVerificationResult(verified, key_id)

    def close(self) -> None:
        """Request graceful close, then terminate and prove process-tree absence."""
        self._assert_owner()
        if self._closed:
            return
        try:
            if not self._failed and self._process is not None:
                self._request("close", {})
        finally:
            absent = self._force_cleanup()
            self._closed = True
        if not absent:
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)

    def _request(
        self,
        command: str,
        payload: dict[str, object],
        *,
        allowed_errors: frozenset[CredentialWorkerCode] = frozenset(),
    ) -> dict[str, object]:
        self._assert_owner()
        if self._closed or self._failed or self._process is None or self._channel is None:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_STATE)
        if self._request_pending:
            self._fail()
        self._request_pending = True
        request_id = uuid.uuid4().hex
        try:
            _send_frame(
                self._channel,
                _encode_worker_message({
                    "protocol_version": _WORKER_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "command": command,
                    "payload": payload,
                }),
                self._command_deadline(),
            )
            response = _recv_worker_response(self._channel, self._command_deadline())
        except KeyboardInterrupt:
            self._failed = True
            self._force_cleanup()
            raise
        except CredentialWorkerError as exc:
            self._failed = True
            self._force_cleanup()
            raise CredentialWorkerError(exc.code) from None
        except BaseException:
            self._failed = True
            self._force_cleanup()
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED) from None
        finally:
            self._request_pending = False
        if response["request_id"] != request_id:
            self._fail()
        if not response["ok"]:
            code = CredentialWorkerCode(response["code"])
            if code is CredentialWorkerCode.TARGET_COLLISION:
                raise CredentialWorkerError(code)
            if code in allowed_errors and type(response["result"]) is dict:
                self._failed = True
                self._force_cleanup()
                return response["result"]
            self._failed = True
            self._force_cleanup()
            raise CredentialWorkerError(code)
        return response["result"]

    def _command_deadline(self) -> float:
        assert self._deadline is not None
        request_deadline = time.monotonic() + self._command_timeout
        operational_end = self._deadline - _SHUTDOWN_RESERVE_SECONDS
        if operational_end <= time.monotonic():
            raise CredentialWorkerError(CredentialWorkerCode.TIMEOUT)
        return min(request_deadline, operational_end)

    def _cleanup_deadline(self) -> float:
        if self._deadline is None:
            raise CredentialWorkerError(CredentialWorkerCode.CLEANUP_FAILED)
        return min(
            self._deadline,
            time.monotonic() + _EMERGENCY_CLEANUP_SECONDS,
        )

    def _assert_owner(self) -> None:
        if os.getpid() != self._creator_pid:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_STATE)

    def _fail(self):
        self._failed = True
        self._force_cleanup()
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)

    def _force_cleanup(self) -> bool:
        if self._channel is not None:
            self._channel.close()
        if self._child_fds is not None:
            _close_descriptors(self._child_fds)
        self._channel = None
        self._child_fds = None
        process = self._process
        if process is None:
            return True
        containment = self._containment
        if containment is None:
            return _terminate_subprocess_direct(process)
        return _terminate_and_verify_subprocess_tree(containment, process)


def _positive_timeout(value: float, name: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def _absolute_executable_path(executable: str) -> bool:
    if os.name == "nt":
        if not ntpath.isabs(executable):
            return False
        return ".." not in executable.replace("/", "\\").split("\\")
    return posixpath.isabs(executable) and posixpath.normpath(executable) == executable


def _windows_prepare_pipe_handles(
    child_fds: tuple[int, int],
) -> tuple[tuple[int, int], subprocess.STARTUPINFO]:
    """Return explicitly inheritable pipe handles and a startup allowlist."""
    import msvcrt

    handles = tuple(msvcrt.get_osfhandle(descriptor) for descriptor in child_fds)
    for handle in handles:
        os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": list(handles)}
    return handles, startup


def _subprocess_channel_arguments(
    child_fds: tuple[int, int],
) -> tuple[str, tuple[int, int], dict[str, object]]:
    if os.name == "nt":
        handles, startup = _windows_prepare_pipe_handles(child_fds)
        return "--managed-worker-handles", handles, {"startupinfo": startup}
    return "--managed-worker-fds", child_fds, {"pass_fds": child_fds}


def _channel_from_inherited(
    read_id: int,
    write_id: int,
    *,
    windows_handles: bool,
) -> _AnonymousDuplexChannel:
    if windows_handles:
        import msvcrt

        read_fd = msvcrt.open_osfhandle(read_id, os.O_RDONLY)
        write_fd = msvcrt.open_osfhandle(write_id, os.O_WRONLY)
    else:
        read_fd, write_fd = read_id, write_id
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    return _AnonymousDuplexChannel(read_fd, write_fd)


def _close_descriptors(descriptors: tuple[int, int]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


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


def _worker_response(
    request_id: str,
    ok: bool,
    code: CredentialWorkerCode | None,
    result: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "protocol_version": _WORKER_PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": ok,
        "code": None if code is None else code.value,
        "result": result,
    }


def _encode_worker_message(payload: dict[str, object]) -> bytes:
    try:
        message = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST) from None
    if len(message) > _MAX_WORKER_MESSAGE_BYTES:
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    return message


def _decode_worker_json(message: bytes) -> object:
    if not isinstance(message, bytes) or len(message) > _MAX_WORKER_MESSAGE_BYTES:
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    try:
        text = message.decode("utf-8", errors="strict")
        decoder = json.JSONDecoder(
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        payload, end = decoder.raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST) from None
    if end != len(text):
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    return payload


def _decode_worker_request(message: bytes) -> dict[str, object]:
    payload = _decode_worker_json(message)
    _parse_worker_request(payload)
    return payload


def _parse_worker_request(
    request: object,
) -> tuple[str, str, dict[str, object]]:
    if not isinstance(request, dict) or set(request) != {
        "protocol_version",
        "request_id",
        "command",
        "payload",
    }:
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    if (
        type(request["protocol_version"]) is not int
        or request["protocol_version"] != _WORKER_PROTOCOL_VERSION
        or type(request["request_id"]) is not str
        or not request["request_id"]
        or len(request["request_id"].encode("utf-8")) > 128
        or type(request["command"]) is not str
        or type(request["payload"]) is not dict
    ):
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    command = request["command"]
    request_id = request["request_id"]
    payload = request["payload"]
    if command == "preflight":
        if set(payload) != {"target_id"} or not _valid_target_id(
            payload.get("target_id")
        ):
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    elif command == "close":
        if payload:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    elif command == "verify_certificate":
        if set(payload) != {"platform", "home"}:
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
        platform = payload["platform"]
        home = payload["home"]
        if (
            type(platform) is not str
            or platform not in NATIVE_BACKENDS
            or type(home) is not str
            or not home
            or "\x00" in home
            or len(home.encode("utf-8")) > 2048
            or not Path(home).is_absolute()
        ):
            raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    else:
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    return command, request_id, payload


def _valid_target_id(value: object) -> bool:
    if type(value) is not str or not value.startswith("native-preflight-"):
        return False
    suffix = value.removeprefix("native-preflight-")
    return len(suffix) == 32 and all(
        character in "0123456789abcdef" for character in suffix
    )


def _parse_worker_response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "protocol_version",
        "request_id",
        "ok",
        "code",
        "result",
    }:
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
    if (
        type(payload["protocol_version"]) is not int
        or payload["protocol_version"] != _WORKER_PROTOCOL_VERSION
        or type(payload["request_id"]) is not str
        or not payload["request_id"]
        or type(payload["ok"]) is not bool
    ):
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
    if payload["ok"]:
        if payload["code"] is not None or type(payload["result"]) is not dict:
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
    else:
        try:
            CredentialWorkerCode(payload["code"])
        except (TypeError, ValueError):
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED) from None
        if payload["result"] is not None and type(payload["result"]) is not dict:
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
    return payload


def _send_frame(channel: _AnonymousDuplexChannel, message: bytes, deadline: float) -> None:
    channel.settimeout(max(0.0, deadline - time.monotonic()))
    try:
        channel.sendall(struct.pack("!I", len(message)) + message)
    except TimeoutError:
        raise CredentialWorkerError(CredentialWorkerCode.TIMEOUT) from None
    except OSError:
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED) from None


def _recv_exact(channel: _AnonymousDuplexChannel, length: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        channel.settimeout(max(0.0, deadline - time.monotonic()))
        try:
            chunk = channel.recv(remaining)
        except TimeoutError:
            raise CredentialWorkerError(CredentialWorkerCode.TIMEOUT) from None
        except OSError:
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED) from None
        if not chunk:
            raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_frame(channel: _AnonymousDuplexChannel, deadline: float) -> bytes:
    length = struct.unpack("!I", _recv_exact(channel, 4, deadline))[0]
    if length == 0 or length > _MAX_WORKER_MESSAGE_BYTES:
        raise CredentialWorkerError(CredentialWorkerCode.INVALID_REQUEST)
    return _recv_exact(channel, length, deadline)


def _recv_worker_response(
    channel: _AnonymousDuplexChannel,
    deadline: float,
) -> dict[str, object]:
    try:
        payload = _decode_worker_json(_recv_frame(channel, deadline))
        return _parse_worker_response(payload)
    except CredentialWorkerError:
        raise
    except BaseException:
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED) from None


def _await_worker_ready(channel: _AnonymousDuplexChannel, deadline: float) -> None:
    if _recv_exact(channel, 1, deadline) != b"R":
        raise CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)


def _send_worker_ready(channel: _AnonymousDuplexChannel) -> None:
    channel.settimeout(1.0)
    channel.sendall(b"R")


def _managed_worker_entry(channel: _AnonymousDuplexChannel) -> int:
    if not _establish_child_containment() or not _discard_process_output():
        return 1
    state = _ManagedCredentialWorkerState()
    try:
        _send_worker_ready(channel)
        while True:
            try:
                request = _decode_worker_request(
                    _recv_frame(channel, time.monotonic() + 86400.0)
                )
            except CredentialWorkerError as exc:
                response = _worker_response(
                    "unknown", False, exc.code, None
                )
                try:
                    _send_frame(channel, _encode_worker_message(response), time.monotonic() + 1)
                except BaseException:
                    pass
                return 1
            response = state.dispatch(request)
            _send_frame(channel, _encode_worker_message(response), time.monotonic() + 1)
            if request["command"] == "close" or (
                not response["ok"]
                and response["code"] != CredentialWorkerCode.TARGET_COLLISION.value
            ):
                return 0 if response["ok"] else 1
    except BaseException:
        return 1
    finally:
        channel.close()


def _managed_cleanup_entry(channel: _AnonymousDuplexChannel) -> int:
    """Run exact-target cleanup in a fresh explicitly managed interpreter."""
    if not _establish_child_containment() or not _discard_process_output():
        return 1
    try:
        _send_worker_ready(channel)
        payload = _decode_worker_json(
            _recv_frame(channel, time.monotonic() + 86400.0)
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"protocol_version", "target_id"}
            or type(payload["protocol_version"]) is not int
            or payload["protocol_version"] != _WORKER_PROTOCOL_VERSION
            or not _valid_target_id(payload["target_id"])
        ):
            return 1
        backend = secret_store.KeyringSecretBackend()
        backend_name = _retained_backend_identity(backend)
        result = _cleanup(backend, payload["target_id"], backend_name)
        _send_frame(
            channel,
            _encode_worker_message(result.as_json()),
            time.monotonic() + 1.0,
        )
        return 0 if result.ok else 1
    except BaseException:
        return 1
    finally:
        channel.close()


def _run_managed_cleanup(
    managed_python: str,
    target_id: str,
    deadline: float,
) -> bool:
    """Clear one parent-known target and prove the cleanup process tree absent."""
    if time.monotonic() >= deadline or not _valid_target_id(target_id):
        return False
    parent_channel, child_fds = _anonymous_duplex_pair()
    mode_flag, child_ids, spawn_kwargs = _subprocess_channel_arguments(child_fds)
    mode_flag = mode_flag.replace("worker", "cleanup")
    process: subprocess.Popen[bytes] | None = None
    containment: _ProcessContainment | None = None
    result_ok = False
    try:
        process = subprocess.Popen(
            [
                managed_python,
                "-I",
                "-m",
                _WORKER_MODULE,
                mode_flag,
                str(child_ids[0]),
                str(child_ids[1]),
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **spawn_kwargs,
        )
        containment = _prepare_subprocess_containment(process)
        if containment is None:
            return False
        _close_descriptors(child_fds)
        child_fds = (-1, -1)
        _await_worker_ready(parent_channel, deadline)
        _send_frame(
            parent_channel,
            _encode_worker_message({
                "protocol_version": _WORKER_PROTOCOL_VERSION,
                "target_id": target_id,
            }),
            deadline,
        )
        outcome = _decode_child_message(
            _recv_frame(parent_channel, deadline), action="cleanup"
        )
        result_ok = (
            not outcome.abnormal
            and outcome.result is not None
            and outcome.result.ok
        )
    except BaseException:
        result_ok = False
    finally:
        parent_channel.close()
        _close_descriptors(child_fds)
        if process is not None:
            if containment is None:
                absent = _terminate_subprocess_direct(process)
            else:
                absent = _terminate_and_verify_subprocess_tree(
                    containment, process, deadline=deadline
                )
            result_ok = result_ok and absent
    return result_ok


def _prepare_subprocess_containment(process: subprocess.Popen[bytes]) -> _ProcessContainment | None:
    return _prepare_parent_containment(process)  # type: ignore[arg-type]


def _terminate_subprocess_direct(process: subprocess.Popen[bytes]) -> bool:
    return _terminate_direct_subprocess(process)


def _terminate_and_verify_subprocess_tree(
    containment: _ProcessContainment,
    process: subprocess.Popen[bytes],
    deadline: float | None = None,
) -> bool:
    return _terminate_and_verify_tree(
        containment, process, deadline=deadline
    )  # type: ignore[arg-type]


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
    parser.add_argument("--managed-worker-fds", nargs=2, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--managed-worker-handles", nargs=2, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--managed-cleanup-fds", nargs=2, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--managed-cleanup-handles", nargs=2, type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    channel_ids = [
        args.managed_worker_fds,
        args.managed_worker_handles,
        args.managed_cleanup_fds,
        args.managed_cleanup_handles,
    ]
    if sum(value is not None for value in channel_ids) > 1:
        return 1
    worker_channel_ids = (
        args.managed_worker_fds
        if args.managed_worker_fds is not None
        else args.managed_worker_handles
    )
    if worker_channel_ids is not None:
        try:
            channel = _channel_from_inherited(
                worker_channel_ids[0],
                worker_channel_ids[1],
                windows_handles=args.managed_worker_handles is not None,
            )
        except (OSError, ValueError):
            return 1
        return _managed_worker_entry(channel)
    cleanup_channel_ids = (
        args.managed_cleanup_fds
        if args.managed_cleanup_fds is not None
        else args.managed_cleanup_handles
    )
    if cleanup_channel_ids is not None:
        try:
            channel = _channel_from_inherited(
                cleanup_channel_ids[0],
                cleanup_channel_ids[1],
                windows_handles=args.managed_cleanup_handles is not None,
            )
        except (OSError, ValueError):
            return 1
        return _managed_cleanup_entry(channel)
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
