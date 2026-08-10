from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_loopback_oauth.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SPEC = importlib.util.spec_from_file_location("connect_google_authorize_services", MODULE_PATH)
authorize_services_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = authorize_services_module
SPEC.loader.exec_module(authorize_services_module)

authorize_services = authorize_services_module.authorize_services

from officina.common.google_credentials import (
    GoogleCredentialError,
    install_client,
    load_credential_file,
)

PLATFORM = "linux"


class FakeSecretBackend:
    def __init__(self) -> None:
        self.stored: list[tuple[str, str, str]] = []

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored.append((namespace, key, value))

    def lookup(self, namespace: str, key: str) -> str | None:
        for stored_namespace, stored_key, value in reversed(self.stored):
            if stored_namespace == namespace and stored_key == key:
                return value
        return None

    def clear(self, namespace: str, key: str) -> bool:
        return False


def desktop_client_payload(
    client_id: str = "test-client-id",
    auth_uri: str = "https://oauth2.example.test/auth",
    token_uri: str = "https://oauth2.example.test/token",
) -> dict[str, object]:
    return {
        "installed": {
            "client_id": client_id,
            "project_id": "famulus-test",
            "auth_uri": auth_uri,
            "token_uri": token_uri,
            "client_secret": "shh-its-a-secret",
            "redirect_uris": ["http://localhost"],
        }
    }


def install_fake_client(home: Path, backend: FakeSecretBackend, **kwargs: object) -> dict[str, object]:
    payload = desktop_client_payload(**kwargs)
    install_client(payload, home=home, platform=PLATFORM, replace=False, secret_backend=backend)
    return payload["installed"]


@pytest.fixture
def secret_backend() -> FakeSecretBackend:
    """Keep mutable secret state isolated while centralizing setup."""
    return FakeSecretBackend()


@pytest.fixture
def installed_client(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
) -> dict[str, object]:
    return install_fake_client(tmp_path, secret_backend)


class FakeResponse:
    """Minimal stand-in for the context manager http.client.HTTPResponse returns."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def make_fake_urlopen(
    *,
    token_uri: str,
    granted_scope: str,
    email: str = "user@example.test",
    email_verified: bool = True,
    sub: str = "sub-12345",
    access_token: str = "fake-access-token",
    refresh_token: str = "fake-refresh-token",
):
    """Build a fake urlopen dispatching on the request's target URL: the
    token endpoint gets a token-exchange response, anything else (the
    UserInfo endpoint) gets an identity response."""

    def fake_urlopen(request):
        url = getattr(request, "full_url", request)
        if url == token_uri:
            return FakeResponse(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "scope": granted_scope,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        return FakeResponse({"email": email, "email_verified": email_verified, "sub": sub})

    return fake_urlopen


def forbidden_urlopen(*_args: object, **_kwargs: object):
    raise AssertionError("token/userinfo network call must not happen on this path")


def make_callback_opener(*, code: str | None = "fake-auth-code", state_override: str | None = None, error: str | None = None):
    """A test double for open_browser: instead of launching a real browser,
    it fires a real HTTP GET at the *local loopback* redirect_uri the
    implementation just started listening on -- simulating the browser
    redirect Google would perform after the user consents. This never
    reaches the real network; it only talks to 127.0.0.1."""

    def opener(url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        redirect_uri = qs["redirect_uri"][0]
        state = state_override if state_override is not None else qs["state"][0]
        params: dict[str, str] = {"state": state}
        if error is not None:
            params["error"] = error
        elif code is not None:
            params["code"] = code
        callback_url = f"{redirect_uri}?{urllib.parse.urlencode(params)}"

        def hit() -> None:
            with urllib.request.urlopen(callback_url) as response:
                response.read()

        threading.Thread(target=hit, daemon=True).start()

    return opener


def test_authorize_services_builds_correct_scope_union(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    captured: dict[str, str] = {}

    def capturing_opener(url: str) -> None:
        captured["url"] = url
        make_callback_opener()(url)

    result = authorize_services(
        ["drive", "calendar", "gmail"],
        home=tmp_path,
        account_hint=None,
        open_browser=capturing_opener,
        urlopen=make_fake_urlopen(
            token_uri=installed_client["token_uri"],
            granted_scope=(
                "openid email https://www.googleapis.com/auth/drive "
                "https://www.googleapis.com/auth/calendar https://mail.google.com/"
            ),
        ),
        platform=PLATFORM,
        secret_backend=secret_backend,
    )

    assert "code_challenge_method=S256" in captured["url"]
    assert "access_type=offline" in captured["url"]
    assert "code_challenge=" in captured["url"]
    # The PKCE verifier must never appear in the authorization URL -- only
    # its SHA-256 challenge may.
    assert "code_verifier" not in captured["url"]
    assert result.granted_services == ("drive", "calendar", "gmail")


def test_authorize_services_rejects_unknown_service(tmp_path: Path) -> None:
    with pytest.raises(GoogleCredentialError):
        authorize_services(["dropbox"], home=tmp_path, account_hint=None, platform=PLATFORM)


def test_authorize_services_result_payload_shape(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    result = authorize_services(
        ["drive"],
        home=tmp_path,
        account_hint=None,
        open_browser=make_callback_opener(),
        urlopen=make_fake_urlopen(
            token_uri=installed_client["token_uri"],
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=secret_backend,
    )
    payload = result.as_payload()
    assert payload["schema_version"] == 1
    assert set(payload) == {
        "schema_version",
        "account",
        "subject",
        "credential_file",
        "requested_services",
        "granted_services",
        "denied_services",
    }
    assert Path(payload["credential_file"]).is_absolute()
    assert "credential_id" not in payload


def test_authorize_services_partial_grant_still_stores_granted_subset(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    result = authorize_services(
        ["drive", "gmail"],
        home=tmp_path,
        account_hint=None,
        open_browser=make_callback_opener(),
        urlopen=make_fake_urlopen(
            token_uri=installed_client["token_uri"],
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=secret_backend,
    )

    assert result.granted_services == ("drive",)
    assert result.denied_services == ("gmail",)

    ref = load_credential_file(Path(result.credential_file))
    assert "https://www.googleapis.com/auth/drive" in ref.granted_scopes
    assert "https://mail.google.com/" not in ref.granted_scopes
    assert ref.granted_services == ("drive",)


def test_authorize_services_state_mismatch_stores_no_credential(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    with pytest.raises(GoogleCredentialError):
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            open_browser=make_callback_opener(state_override="wrong-state"),
            urlopen=forbidden_urlopen,
            platform=PLATFORM,
            secret_backend=secret_backend,
        )

    from officina.common.famulus_paths import resolve_famulus_paths

    registry_path = resolve_famulus_paths(platform=PLATFORM, home=tmp_path).config_root / "connect-google" / "credentials.json"
    assert not registry_path.exists()
    descriptor_dir = registry_path.parent / "credentials"
    assert not descriptor_dir.exists() or not tuple(descriptor_dir.iterdir())


def test_authorize_services_account_hint_mismatch_stores_no_credential(
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    with pytest.raises(GoogleCredentialError):
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint="someone-else@example.test",
            open_browser=make_callback_opener(),
            urlopen=make_fake_urlopen(
                token_uri=installed_client["token_uri"],
                granted_scope="openid email https://www.googleapis.com/auth/drive",
                email="user@example.test",
            ),
            platform=PLATFORM,
            secret_backend=secret_backend,
        )

    from officina.common.famulus_paths import resolve_famulus_paths

    registry_path = resolve_famulus_paths(platform=PLATFORM, home=tmp_path).config_root / "connect-google" / "credentials.json"
    assert not registry_path.exists()
    descriptor_dir = registry_path.parent / "credentials"
    assert not descriptor_dir.exists() or not tuple(descriptor_dir.iterdir())


def test_authorize_services_never_calls_real_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    secret_backend: FakeSecretBackend,
    installed_client: dict[str, object],
) -> None:
    def forbidden(*_args: object, **_kwargs: object):
        raise AssertionError("no real network call permitted in tests")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("webbrowser.open", forbidden)

    # Neither open_browser nor urlopen is passed explicitly: the defaults
    # must be resolved dynamically from the (now-patched) webbrowser/urllib
    # modules at call time, not captured as stale default-parameter values
    # bound to the real functions at import time.
    with pytest.raises(AssertionError, match="no real network call permitted"):
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            platform=PLATFORM,
            secret_backend=secret_backend,
        )
