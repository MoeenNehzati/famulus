#!/usr/bin/env python3
"""Transparent, bounded Google Desktop OAuth authorization.

Requests a single scope union covering every selected service in one Google
consent screen, then creates one immutable credential descriptor containing
exactly the services and scopes Google actually granted (a partial grant is
not rolled back). The client secret, refresh token, and PKCE code_verifier
never appear on stdout, in the authorization URL, or in any stored file: the
OAuth secrets are fetched from or written to the host secret store by
``officina.common.google_credentials.exchange_authorization_code`` and the
verifier lives only in this process's memory for the duration of one run.

The implementation uses one numeric IPv4 loopback path on every supported
host, emits a manual URL before browser launch, validates a strict callback,
and records only the service scopes actually granted. Browser launch runs in
an isolated helper process so it cannot corrupt the machine streams or block
callback handling.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TextIO

from officina.common.google_credentials import GoogleCredentialError
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

if __package__:
    from ._client_config import (
        ClientConfigError,
        ClientSecretStoreUnavailable,
        load_authorization_client,
    )
else:  # Direct-file loading is retained for focused tests and local diagnosis.
    from _client_config import (
        ClientConfigError,
        ClientSecretStoreUnavailable,
        load_authorization_client,
    )

CALLBACK_ADDRESS = "127.0.0.1"
CALLBACK_DEADLINE_SECONDS = 300
CALLBACK_MAX_REQUEST_BYTES = 16 * 1024
_GOOGLE_DENIAL_ERRORS = frozenset({"access_denied"})


class AuthorizationFailure(GoogleCredentialError):
    """Stable, secret-free terminal failure for one authorization phase."""

    def __init__(self, *, phase: str, code: str) -> None:
        self.phase = phase
        self.code = code
        super().__init__(f"{phase}: {code}")


@dataclass(frozen=True)
class CallbackResult:
    kind: Literal["code", "denied"]
    value: str


@dataclass(frozen=True)
class AuthorizationResult:
    account: str
    subject: str
    credential_file: str
    requested_services: tuple[str, ...]
    granted_services: tuple[str, ...]
    denied_services: tuple[str, ...]

    def as_payload(self) -> dict:
        return {
            "schema_version": 1,
            "account": self.account,
            "subject": self.subject,
            "credential_file": self.credential_file,
            "requested_services": list(self.requested_services),
            "granted_services": list(self.granted_services),
            "denied_services": list(self.denied_services),
        }


def _emit_diagnostic(stream: TextIO, event: str, status: str, **fields: object) -> None:
    print(
        json.dumps({"schema_version": 1, "event": event, "status": status, **fields}),
        file=stream,
        flush=True,
    )


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_auth_url(
    *, client_id: str, redirect_uri: str, scope: frozenset[str], state: str, code_challenge: str
) -> str:
    from officina.common.google_credentials import GOOGLE_AUTHORIZATION_URL

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(sorted(scope)),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTHORIZATION_URL}?{urllib.parse.urlencode(params)}"


def _parse_callback_request(*, method: str, target: str, expected_state: str) -> CallbackResult | None:
    if method != "GET":
        return None
    parsed = urllib.parse.urlsplit(target)
    if parsed.path != "/":
        return None
    values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    states = values.get("state", [])
    codes = values.get("code", [])
    errors = values.get("error", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], expected_state):
        return None
    if bool(codes) == bool(errors):
        return None
    if codes:
        return CallbackResult("code", codes[0]) if len(codes) == 1 and codes[0] else None
    if len(errors) == 1 and errors[0] in _GOOGLE_DENIAL_ERRORS:
        return CallbackResult("denied", errors[0])
    return None


_RESPONSE_HEADERS = (
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Cache-Control: no-store\r\n"
    b"Referrer-Policy: no-referrer\r\n"
    b"Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'\r\n"
    b"Connection: close\r\n"
)


def _send_callback_response(connection: socket.socket, *, terminal: bool) -> None:
    body = (
        b"<html><body>Authorization received. You can close this tab.</body></html>"
        if terminal
        else b"<html><body>Invalid callback. Return to the authorization command.</body></html>"
    )
    status = b"200 OK" if terminal else b"400 Bad Request"
    response = (
        b"HTTP/1.1 " + status + b"\r\n" + _RESPONSE_HEADERS
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
    )
    try:
        connection.sendall(response)
    except OSError:
        pass


def _open_listener(callback_port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((CALLBACK_ADDRESS, callback_port))
        listener.listen()
    except Exception:
        listener.close()
        raise
    return listener


def _wait_for_callback(
    listener: socket.socket,
    *,
    state: str,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> CallbackResult:
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AuthorizationFailure(phase="callback", code="callback_timeout")
        listener.settimeout(remaining)
        try:
            connection, _ = listener.accept()
        except TimeoutError as exc:
            raise AuthorizationFailure(phase="callback", code="callback_timeout") from exc
        with connection:
            data = bytearray()
            while b"\r\n\r\n" not in data:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise AuthorizationFailure(phase="callback", code="callback_timeout")
                connection.settimeout(min(1.0, remaining))
                try:
                    chunk = connection.recv(min(4096, CALLBACK_MAX_REQUEST_BYTES + 1 - len(data)))
                except TimeoutError:
                    continue
                except OSError:
                    break
                if monotonic() >= deadline:
                    raise AuthorizationFailure(
                        phase="callback", code="callback_timeout"
                    )
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > CALLBACK_MAX_REQUEST_BYTES:
                    break
            result = None
            if b"\r\n\r\n" in data and len(data) <= CALLBACK_MAX_REQUEST_BYTES:
                try:
                    request_line = bytes(data).split(b"\r\n", 1)[0].decode("ascii")
                    method, target, _version = request_line.split(" ", 2)
                except (UnicodeDecodeError, ValueError):
                    result = None
                else:
                    result = _parse_callback_request(
                        method=method, target=target, expected_state=state
                    )
            _send_callback_response(connection, terminal=result is not None)
            if result is not None:
                return result


def _start_browser_helper(url: str, *, popen: Callable = subprocess.Popen):
    helper = Path(__file__).with_name("_browser_helper.py")
    process = popen(
        [sys.executable, str(helper)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None
    try:
        process.stdin.write((url + "\n").encode("utf-8"))
        process.stdin.close()
    except Exception:
        try:
            process.stdin.close()
        except Exception:
            pass
        _stop_browser_helper(process)
        raise
    return process


def _stop_browser_helper(process) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            pass
    except OSError:
        pass


def _completed_browser_status(process) -> str | None:
    """Return the sanitized result of an already-exited browser helper."""
    if process is None:
        return None
    returncode = process.poll()
    if returncode is None:
        return None
    if returncode == 0:
        return "opened"
    if returncode == 1:
        return "unavailable"
    return "error"


def authorize_services(
    services,
    *,
    home: Path,
    account_hint: str | None,
    open_browser: Callable[[str], object] | None = None,
    browser_enabled: bool = True,
    callback_port: int = 0,
    callback_deadline_seconds: int = CALLBACK_DEADLINE_SECONDS,
    urlopen: Callable | None = None,
    platform: str = sys.platform,
    secret_backend=None,
    diagnostic_stream: TextIO | None = None,
) -> AuthorizationResult:
    from officina.common.google_credentials import (
        GOOGLE_USERINFO_URL,
        GOOGLE_TOKEN_URL,
        SERVICE_SCOPES,
        GoogleCredentialError,
        create_credential_file,
        _open_google_json,
        exchange_authorization_code,
        normalize_services,
        scope_union_for_services,
    )

    from officina.common import secret_store

    diagnostics = diagnostic_stream if diagnostic_stream is not None else sys.stderr
    listener = None
    helper = None
    terminal_emitted = False
    current_phase = "client"
    try:
        try:
            requested = normalize_services(services)
        except GoogleCredentialError as exc:
            raise AuthorizationFailure(phase="client", code="client_invalid") from exc
        try:
            installed = load_authorization_client(
                Path(home), platform=platform, secret_backend=secret_backend
            )
        except ClientSecretStoreUnavailable as exc:
            raise AuthorizationFailure(
                phase="client", code="secret_store_unavailable"
            ) from exc
        except ClientConfigError as exc:
            raise AuthorizationFailure(phase="client", code="client_invalid") from exc
        _emit_diagnostic(diagnostics, "oauth.client_ready", "ready", services=list(requested))

        current_phase = "listener"
        if not isinstance(callback_port, int) or not 0 <= callback_port <= 65535:
            raise AuthorizationFailure(phase="listener", code="listener_bind_failed")
        try:
            listener = _open_listener(callback_port)
        except (OSError, OverflowError, ValueError) as exc:
            raise AuthorizationFailure(phase="listener", code="listener_bind_failed") from exc
        port = int(listener.getsockname()[1])
        redirect_uri = f"http://{CALLBACK_ADDRESS}:{port}/"
        deadline = time.monotonic() + callback_deadline_seconds
        _emit_diagnostic(
            diagnostics,
            "oauth.listener_ready",
            "ready",
            address=CALLBACK_ADDRESS,
            port=port,
            callback_deadline_seconds=callback_deadline_seconds,
        )

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)
        auth_url = _build_auth_url(
            client_id=installed["client_id"],
            redirect_uri=redirect_uri,
            scope=scope_union_for_services(requested),
            state=state,
            code_challenge=code_challenge,
        )
        _emit_diagnostic(diagnostics, "oauth.authorization_url", "available", url=auth_url)
        tunnel = (
            "ssh -N -o ExitOnForwardFailure=yes "
            f"-L {CALLBACK_ADDRESS}:{port}:{CALLBACK_ADDRESS}:{port} user@remote-host"
        )
        _emit_diagnostic(diagnostics, "oauth.ssh_tunnel", "available", command=tunnel)

        if not browser_enabled:
            _emit_diagnostic(diagnostics, "oauth.browser_launch", "disabled")
        elif open_browser is not None:
            _emit_diagnostic(diagnostics, "oauth.browser_launch", "started")
            try:
                outcome = open_browser(auth_url)
            except Exception:
                _emit_diagnostic(diagnostics, "oauth.browser_result", "error")
            else:
                _emit_diagnostic(
                    diagnostics,
                    "oauth.browser_result",
                    "unavailable" if outcome is False else "opened",
                )
        else:
            try:
                helper = _start_browser_helper(auth_url)
            except Exception:
                _emit_diagnostic(diagnostics, "oauth.browser_launch", "failed")
            else:
                _emit_diagnostic(diagnostics, "oauth.browser_launch", "started")

        _emit_diagnostic(diagnostics, "oauth.awaiting_callback", "waiting")
        current_phase = "callback"
        callback = _wait_for_callback(listener, state=state, deadline=deadline)
        browser_status = _completed_browser_status(helper)
        if browser_status is not None:
            _emit_diagnostic(diagnostics, "oauth.browser_result", browser_status)
            helper = None
        _emit_diagnostic(diagnostics, "oauth.callback_received", callback.kind)
        if callback.kind == "denied":
            raise AuthorizationFailure(phase="callback", code="access_denied")

        listener.close()
        listener = None
        _stop_browser_helper(helper)
        helper = None
        _emit_diagnostic(diagnostics, "oauth.token_exchange", "started")
        current_phase = "token_exchange"
        try:
            token_payload = exchange_authorization_code(
                client_id=installed["client_id"],
                client_secret_ref=installed["client_secret_ref"],
                code=callback.value,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
                token_uri="",
                urlopen=urlopen,
                secret_backend=secret_backend,
            )
        except secret_store.SecretStoreError as exc:
            raise AuthorizationFailure(
                phase="token_exchange", code="secret_store_unavailable"
            ) from exc
        except GoogleCredentialError as exc:
            raise AuthorizationFailure(phase="token_exchange", code="token_exchange_failed") from exc

        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token or not isinstance(refresh_token, str) or not refresh_token:
            raise AuthorizationFailure(phase="token_exchange", code="token_exchange_failed")
        granted_scope_text = token_payload.get("scope")
        granted_scopes = frozenset(granted_scope_text.split()) if isinstance(granted_scope_text, str) else frozenset()

        _emit_diagnostic(diagnostics, "oauth.userinfo", "started")
        request = urllib.request.Request(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        current_phase = "userinfo"
        try:
            userinfo = _open_google_json(request, urlopen=urlopen)
        except GoogleCredentialError as exc:
            raise AuthorizationFailure(phase="userinfo", code="userinfo_failed") from exc
        email = userinfo.get("email")
        subject = userinfo.get("sub")
        if (
            userinfo.get("email_verified") is not True
            or not isinstance(email, str)
            or not email.strip()
            or not isinstance(subject, str)
            or not subject.strip()
        ):
            raise AuthorizationFailure(phase="userinfo", code="userinfo_failed")
        current_phase = "account_check"
        if account_hint and account_hint != email:
            raise AuthorizationFailure(phase="account_check", code="account_mismatch")

        granted_services = tuple(
            service for service in requested if SERVICE_SCOPES[service] <= granted_scopes
        )
        denied_services = tuple(service for service in requested if service not in granted_services)
        if not granted_services:
            raise AuthorizationFailure(
                phase="credential_publish", code="no_service_scope_granted"
            )

        _emit_diagnostic(diagnostics, "oauth.credential_publish", "started")
        current_phase = "credential_publish"
        try:
            ref = create_credential_file(
                subject=subject,
                account=email,
                client_id=installed["client_id"],
                client_secret_ref=installed["client_secret_ref"],
                token_uri=GOOGLE_TOKEN_URL,
                granted_services=granted_services,
                granted_scopes=granted_scopes,
                refresh_token=refresh_token,
                home=Path(home),
                platform=platform,
                secret_backend=secret_backend,
            )
        except secret_store.SecretStoreError as exc:
            raise AuthorizationFailure(
                phase="credential_publish", code="secret_store_unavailable"
            ) from exc
        except GoogleCredentialError as exc:
            raise AuthorizationFailure(
                phase="credential_publish", code="credential_publish_failed"
            ) from exc

        status = "authorized" if not denied_services else "partial_grant"
        _emit_diagnostic(
            diagnostics,
            "oauth.complete",
            status,
            granted_services=list(granted_services),
            denied_services=list(denied_services),
            warnings=[],
        )
        terminal_emitted = True
        return AuthorizationResult(
            account=email,
            subject=subject,
            credential_file=str(ref.path),
            requested_services=requested,
            granted_services=granted_services,
            denied_services=denied_services,
        )
    except AuthorizationFailure as exc:
        browser_status = _completed_browser_status(helper)
        if browser_status is not None:
            _emit_diagnostic(diagnostics, "oauth.browser_result", browser_status)
            helper = None
        if not terminal_emitted:
            _emit_diagnostic(
                diagnostics, "oauth.failed", "error", phase=exc.phase, code=exc.code
            )
        raise
    except Exception as exc:
        browser_status = _completed_browser_status(helper)
        if browser_status is not None:
            _emit_diagnostic(diagnostics, "oauth.browser_result", browser_status)
            helper = None
        failure = AuthorizationFailure(phase=current_phase, code="internal_error")
        _emit_diagnostic(
            diagnostics,
            "oauth.failed",
            "error",
            phase=failure.phase,
            code=failure.code,
        )
        raise failure from exc
    finally:
        if listener is not None:
            listener.close()
        _stop_browser_helper(helper)


def run_authorize_services(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="authorize-services")
    parser.add_argument("--services", required=True)
    parser.add_argument("--account-hint", default=None)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--callback-port", type=int, default=0)
    args = parser.parse_args(argv)
    services = [entry.strip() for entry in args.services.split(",") if entry.strip()]
    try:
        result = authorize_services(
            services,
            home=args.home,
            account_hint=args.account_hint,
            browser_enabled=not args.no_open_browser,
            callback_port=args.callback_port,
        )
    except AuthorizationFailure:
        return 2
    except Exception:
        _emit_diagnostic(
            sys.stderr,
            "oauth.failed",
            "error",
            phase="client",
            code="internal_error",
        )
        return 2
    print(json.dumps(result.as_payload()))
    return 0


class AuthorizeServicesInterface(PythonArgvMachineInterface):
    prog = "authorize-services"

    def run(self, argv: list[str]) -> int:
        return run_authorize_services(argv)


if __name__ == "__main__":
    raise SystemExit(run_authorize_services(sys.argv[1:]))
