#!/usr/bin/env python3
"""Combined OAuth 2.0 PKCE authorization for Drive, Calendar, and Gmail scopes.

Requests a single scope union covering every selected service in one Google
consent screen, then creates one immutable credential descriptor containing
exactly the services and scopes Google actually granted (a partial grant is
not rolled back). The client secret, refresh token, and PKCE code_verifier
never appear on stdout, in the authorization URL, or in any stored file: the
OAuth secrets are fetched from or written to the host secret store by
``officina.common.google_credentials.exchange_authorization_code`` and the
verifier lives only in this process's memory for the duration of one run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


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


def _load_installed_client(home: Path, platform: str):
    from officina.common.google_credentials import GoogleCredentialError, canonical_client_path

    path = canonical_client_path(home=home, platform=platform)
    if not path.exists():
        raise GoogleCredentialError(f"no canonical Google client installed at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleCredentialError(f"cannot read canonical client at {path}: {exc}") from exc
    installed = payload.get("installed") if isinstance(payload, dict) else None
    if not isinstance(installed, dict):
        raise GoogleCredentialError(f"canonical client at {path} is missing an installed object")
    for field in ("client_id", "auth_uri", "token_uri", "client_secret_ref"):
        value = installed.get(field)
        if not isinstance(value, str) or not value.strip():
            raise GoogleCredentialError(f"canonical client at {path} is missing installed.{field}")
    return installed


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge). The verifier has 512 bits of
    entropy (64 random bytes, url-safe base64 encoded) -- far above the
    43-character RFC 7636 minimum -- and is never itself transmitted in the
    authorization URL, only its SHA-256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_auth_url(
    *, auth_uri: str, client_id: str, redirect_uri: str, scope: frozenset[str], state: str, code_challenge: str
) -> str:
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
    return f"{auth_uri}?{urllib.parse.urlencode(params)}"


def _start_loopback_server(state: str) -> tuple[http.server.HTTPServer, dict[str, str]]:
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if qs.get("state", [""])[0] != state:
                result["error"] = "state_mismatch"
                self.wfile.write(b"<html><body>State mismatch. You can close this tab.</body></html>")
                return
            if "error" in qs:
                result["error"] = qs["error"][0]
                self.wfile.write(b"<html><body>Authorization denied. You can close this tab.</body></html>")
                return
            code = qs.get("code", [None])[0]
            if code:
                result["code"] = code
            self.wfile.write(b"<html><body>Authorization complete. You can close this tab.</body></html>")

        def log_message(self, *args: object) -> None:  # noqa: D401
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    return server, result


def authorize_services(
    services,
    *,
    home: Path,
    account_hint: str | None,
    open_browser: Callable[[str], object] | None = None,
    urlopen: Callable | None = None,
    platform: str = sys.platform,
    secret_backend=None,
) -> AuthorizationResult:
    # webbrowser.open / urllib.request.urlopen are resolved dynamically (not
    # as default-parameter values) so tests can monkeypatch the real module
    # attributes and prove this function never falls back to a real network
    # call or a real browser launch when a caller omits both overrides.
    import urllib.request
    import webbrowser

    from officina.common.google_credentials import (
        SERVICE_SCOPES,
        GoogleCredentialError,
        create_credential_file,
        exchange_authorization_code,
        normalize_services,
        scope_union_for_services,
    )

    open_browser = open_browser or webbrowser.open
    urlopen = urlopen or urllib.request.urlopen
    home = Path(home)

    requested = normalize_services(services)
    requested_scope_union = scope_union_for_services(requested)

    installed = _load_installed_client(home, platform)
    client_id = installed["client_id"]
    auth_uri = installed["auth_uri"]
    token_uri = installed["token_uri"]

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(24)

    server, callback_result = _start_loopback_server(state)
    try:
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}"
        auth_url = _build_auth_url(
            auth_uri=auth_uri,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=requested_scope_union,
            state=state,
            code_challenge=code_challenge,
        )
        open_browser(auth_url)
        # Exactly one callback is ever accepted: handle_request() services a
        # single connection and returns, and the server socket is closed in
        # the finally block below regardless of outcome, so no port or
        # thread is left behind across test runs or repeated invocations.
        server.handle_request()
    finally:
        server.server_close()

    if callback_result.get("error"):
        raise GoogleCredentialError(f"authorization failed: {callback_result['error']}")
    code = callback_result.get("code")
    if not code:
        raise GoogleCredentialError("no authorization code received")

    token_payload = exchange_authorization_code(
        client_id=client_id,
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        token_uri=token_uri,
        urlopen=urlopen,
        secret_backend=secret_backend,
    )

    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    if not access_token:
        raise GoogleCredentialError("token exchange returned no access_token")
    if not refresh_token:
        raise GoogleCredentialError("token exchange returned no refresh_token")
    granted_scope_text = token_payload.get("scope") or ""
    granted_scopes = frozenset(granted_scope_text.split()) if granted_scope_text else frozenset()

    userinfo_request = urllib.request.Request(
        USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    with urlopen(userinfo_request) as response:
        userinfo = json.loads(response.read())

    if userinfo.get("email_verified") is not True:
        raise GoogleCredentialError("Google account email is not verified")
    email = userinfo.get("email")
    subject = userinfo.get("sub")
    if not email or not subject:
        raise GoogleCredentialError("UserInfo response is missing email or sub")
    if account_hint and account_hint != email:
        raise GoogleCredentialError(
            f"authorized account {email!r} does not match expected account {account_hint!r}"
        )

    granted_services = tuple(
        service for service in requested if SERVICE_SCOPES[service] <= granted_scopes
    )
    denied_services = tuple(service for service in requested if service not in granted_services)

    ref = create_credential_file(
        subject=subject,
        account=email,
        client_id=client_id,
        token_uri=token_uri,
        granted_services=granted_services,
        granted_scopes=granted_scopes,
        refresh_token=refresh_token,
        home=home,
        platform=platform,
        secret_backend=secret_backend,
    )

    return AuthorizationResult(
        account=email,
        subject=subject,
        credential_file=str(ref.path),
        requested_services=requested,
        granted_services=granted_services,
        denied_services=denied_services,
    )


def run_authorize_services(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="authorize-services")
    parser.add_argument("--services", required=True, help="comma-separated service list, e.g. drive,calendar,gmail")
    parser.add_argument("--account-hint", default=None)
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)

    from officina.common.google_credentials import GoogleCredentialError

    services = [entry.strip() for entry in args.services.split(",") if entry.strip()]
    try:
        result = authorize_services(services, home=args.home, account_hint=args.account_hint)
    except GoogleCredentialError as exc:
        parser.error(str(exc))
        return 2
    # Only the result payload is ever printed: no token, refresh token, or
    # client secret is present in AuthorizationResult.as_payload().
    print(json.dumps(result.as_payload()))
    return 0


class AuthorizeServicesInterface(PythonArgvMachineInterface):
    prog = "authorize-services"

    def run(self, argv: list[str]) -> int:
        return run_authorize_services(argv)


if __name__ == "__main__":
    raise SystemExit(run_authorize_services(sys.argv[1:]))
