"""OAuth helpers for email-client host credential flows."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from officina.common import secret_store

SECRET_NAMESPACE = "email-client"
AUTH_APP_PASSWORD = "app-password"
AUTH_GMAIL_OAUTH = "gmail-oauth"
GMAIL_SCOPE = "https://mail.google.com/"
DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
OAUTH_TIMEOUT_SECONDS = 300


class OAuthError(RuntimeError):
    """Raised when OAuth setup or token refresh fails."""


def account_auth_mode(account: dict) -> str:
    return account.get("auth") or AUTH_APP_PASSWORD


def is_gmail_oauth(account: dict) -> bool:
    return account_auth_mode(account) == AUTH_GMAIL_OAUTH


def oauth_secret_key(nickname: str, name: str) -> str:
    return f"{nickname}:oauth:{name}"


def load_google_client_config(path: Path) -> dict:
    raw = json.loads(path.read_text())
    config = raw.get("installed") or raw.get("web") or raw
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    if not client_id:
        raise OAuthError("OAuth client config is missing client_id")
    if not client_secret:
        raise OAuthError("OAuth client config is missing client_secret")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": config.get("auth_uri") or DEFAULT_AUTH_URI,
        "token_uri": config.get("token_uri") or DEFAULT_TOKEN_URI,
    }


def configure_gmail_oauth(
    nickname: str,
    account: dict,
    client_config: dict,
    refresh_token: str,
    scope: str = GMAIL_SCOPE,
) -> None:
    account["auth"] = AUTH_GMAIL_OAUTH
    account["oauth"] = {
        "provider": "google",
        "client_id": client_config["client_id"],
        "auth_uri": client_config.get("auth_uri") or DEFAULT_AUTH_URI,
        "token_uri": client_config.get("token_uri") or DEFAULT_TOKEN_URI,
        "scope": scope,
    }
    secret_store.store(SECRET_NAMESPACE, oauth_secret_key(nickname, "client-secret"), client_config["client_secret"])
    secret_store.store(SECRET_NAMESPACE, oauth_secret_key(nickname, "refresh-token"), refresh_token)


def clear_oauth_credentials(nickname: str) -> None:
    for name in ("client-secret", "refresh-token"):
        secret_store.clear(SECRET_NAMESPACE, oauth_secret_key(nickname, name))


def refresh_google_access_token(
    nickname: str,
    account: dict,
    *,
    urlopen: Callable = urllib.request.urlopen,
) -> str:
    oauth = account.get("oauth") or {}
    client_id = oauth.get("client_id")
    token_uri = oauth.get("token_uri") or DEFAULT_TOKEN_URI
    if not client_id:
        raise OAuthError(f"account '{nickname}' is missing OAuth client_id")

    try:
        client_secret = secret_store.require(SECRET_NAMESPACE, oauth_secret_key(nickname, "client-secret"))
        refresh_token = secret_store.require(SECRET_NAMESPACE, oauth_secret_key(nickname, "refresh-token"))
    except secret_store.SecretStoreError as exc:
        raise OAuthError(f"could not read OAuth credential for account '{nickname}': {exc}") from exc

    response = _post_form(
        token_uri,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        urlopen=urlopen,
    )
    access_token = response.get("access_token")
    if not access_token:
        raise OAuthError(f"token refresh for account '{nickname}' did not return an access token")
    return access_token


def xoauth2_string(email_address: str, access_token: str) -> str:
    return f"user={email_address}\x01auth=Bearer {access_token}\x01\x01"


def xoauth2_bytes(email_address: str, access_token: str) -> bytes:
    return xoauth2_string(email_address, access_token).encode("utf-8")


def run_google_oauth_setup(
    nickname: str,
    account: dict,
    client_config_path: Path,
    *,
    open_browser: bool = True,
    timeout_seconds: int = OAUTH_TIMEOUT_SECONDS,
) -> None:
    client_config = load_google_client_config(client_config_path)
    server = _CallbackServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    server.timeout = timeout_seconds
    state = secrets.token_urlsafe(24)
    code_verifier = _code_verifier()
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2/callback"
    auth_url = _authorization_url(client_config, redirect_uri, state, code_verifier)

    print("Open this URL to authorize email-client:")
    print(auth_url)
    if open_browser:
        webbrowser.open(auth_url)

    server.handle_request()
    if server.error:
        raise OAuthError(f"OAuth authorization failed: {server.error}")
    if not server.code:
        raise OAuthError("OAuth authorization timed out before a code was received")
    if server.state != state:
        raise OAuthError("OAuth authorization returned an unexpected state")

    token_response = exchange_authorization_code(
        client_config,
        code=server.code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )
    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        raise OAuthError("OAuth token response did not include a refresh token; rerun setup and approve offline access")
    scope = token_response.get("scope") or GMAIL_SCOPE
    if GMAIL_SCOPE not in scope.split():
        raise OAuthError(f"OAuth token response is missing required scope {GMAIL_SCOPE}")
    configure_gmail_oauth(nickname, account, client_config, refresh_token, scope=scope)


def exchange_authorization_code(
    client_config: dict,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    urlopen: Callable = urllib.request.urlopen,
) -> dict:
    return _post_form(
        client_config.get("token_uri") or DEFAULT_TOKEN_URI,
        {
            "client_id": client_config["client_id"],
            "client_secret": client_config["client_secret"],
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        urlopen=urlopen,
    )


def _authorization_url(client_config: dict, redirect_uri: str, state: str, code_verifier: str) -> str:
    params = {
        "client_id": client_config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": _code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{client_config.get('auth_uri') or DEFAULT_AUTH_URI}?{urllib.parse.urlencode(params)}"


def _post_form(uri: str, form: dict, *, urlopen: Callable = urllib.request.urlopen) -> dict:
    data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        uri,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OAuthError(f"OAuth token endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"OAuth token endpoint failed: {exc}") from exc

    parsed = json.loads(payload.decode("utf-8"))
    if "error" in parsed:
        description = parsed.get("error_description") or parsed["error"]
        raise OAuthError(f"OAuth token endpoint returned error: {description}")
    return parsed


def _code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class _CallbackServer(HTTPServer):
    code: str | None = None
    state: str | None = None
    error: str | None = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.code = _first(params.get("code"))
        self.server.state = _first(params.get("state"))
        self.server.error = _first(params.get("error"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorization received. Return to your assistant.</body></html>")

    def log_message(self, format: str, *args) -> None:
        return


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]
