from __future__ import annotations

import importlib.util
import io
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_loopback_oauth.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SPEC = importlib.util.spec_from_file_location("connect_google_authorize_services", MODULE_PATH)
authorize_services_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = authorize_services_module
SPEC.loader.exec_module(authorize_services_module)

authorize_services = authorize_services_module.authorize_services

from officina.common.google_credentials import GoogleCredentialError, install_client, load_credential

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


class FakeResponse:
    """Minimal stand-in for the context manager http.client.HTTPResponse returns."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        return self._data if size < 0 else self._data[:size]


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

    def fake_urlopen(request, *, timeout=30.0):
        assert timeout == 30.0
        url = getattr(request, "full_url", request)
        if url == "https://oauth2.googleapis.com/token":
            return FakeResponse(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "scope": granted_scope,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        assert url == "https://openidconnect.googleapis.com/v1/userinfo"
        return FakeResponse({"email": email, "email_verified": email_verified, "sub": sub})

    return fake_urlopen


@pytest.fixture
def secret_backend() -> FakeSecretBackend:
    return FakeSecretBackend()


@pytest.fixture
def installed_drive_client(tmp_path: Path, secret_backend: FakeSecretBackend):
    installed = install_fake_client(tmp_path, secret_backend)
    return tmp_path, secret_backend, installed


@pytest.fixture
def loopback_listener():
    listener = authorize_services_module._open_listener(0)
    yield listener
    listener.close()


@pytest.fixture
def background_thread():
    threads: list[threading.Thread] = []

    def start(target) -> threading.Thread:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        threads.append(thread)
        return thread

    yield start

    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()


def forbidden_urlopen(*_args: object, **_kwargs: object):
    raise AssertionError("token/userinfo network call must not happen on this path")


def make_callback_opener(
    thread_starter,
    *,
    code: str | None = "fake-auth-code",
    state_override: str | None = None,
    error: str | None = None,
):
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
            try:
                with urllib.request.urlopen(callback_url) as response:
                    response.read()
            except urllib.error.HTTPError:
                # Invalid callback fixtures intentionally receive a static 400;
                # the authorization loop must remain alive until its deadline.
                pass

        thread_starter(hit)

    return opener


def test_authorize_services_builds_correct_scope_union(
    tmp_path: Path, background_thread
) -> None:
    backend = FakeSecretBackend()
    installed = install_fake_client(tmp_path, backend)
    captured: dict[str, str] = {}

    def capturing_opener(url: str) -> None:
        captured["url"] = url
        make_callback_opener(background_thread)(url)

    result = authorize_services(
        ["drive", "calendar", "gmail"],
        home=tmp_path,
        account_hint=None,
        open_browser=capturing_opener,
        urlopen=make_fake_urlopen(
            token_uri=installed["token_uri"],
            granted_scope=(
                "openid email https://www.googleapis.com/auth/drive "
                "https://www.googleapis.com/auth/calendar https://mail.google.com/"
            ),
        ),
        platform=PLATFORM,
        secret_backend=backend,
    )

    assert captured["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in captured["url"]
    assert "access_type=offline" in captured["url"]
    assert "code_challenge=" in captured["url"]
    # The PKCE verifier must never appear in the authorization URL -- only
    # its SHA-256 challenge may.
    assert "code_verifier" not in captured["url"]
    assert result.granted_services == ("drive", "calendar", "gmail")


def test_authorize_services_rejects_unknown_service(tmp_path: Path) -> None:
    diagnostics = io.StringIO()

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services(
            ["dropbox"],
            home=tmp_path,
            account_hint=None,
            platform=PLATFORM,
            diagnostic_stream=diagnostics,
        )

    assert (failure.value.phase, failure.value.code) == ("client", "client_invalid")
    assert [json.loads(line) for line in diagnostics.getvalue().splitlines()] == [
        {
            "schema_version": 1,
            "event": "oauth.failed",
            "status": "error",
            "phase": "client",
            "code": "client_invalid",
        }
    ]


def test_unavailable_secret_store_fails_before_listener_with_stable_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from officina.common import secret_store

    class UnavailableBackend(FakeSecretBackend):
        def lookup(self, namespace: str, key: str) -> str | None:
            raise secret_store.SecretStoreUnavailable("backend unavailable")

    backend = UnavailableBackend()
    install_fake_client(tmp_path, backend)
    monkeypatch.setattr(
        authorize_services_module,
        "_open_listener",
        lambda _port: (_ for _ in ()).throw(AssertionError("listener opened")),
    )
    diagnostics = io.StringIO()

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            platform=PLATFORM,
            secret_backend=backend,
            diagnostic_stream=diagnostics,
        )

    assert (failure.value.phase, failure.value.code) == (
        "client",
        "secret_store_unavailable",
    )
    assert json.loads(diagnostics.getvalue())["code"] == "secret_store_unavailable"


def test_unexpected_client_failure_is_sanitized_to_internal_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        authorize_services_module,
        "load_authorization_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private detail")),
    )
    diagnostics = io.StringIO()

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            platform=PLATFORM,
            diagnostic_stream=diagnostics,
        )

    assert (failure.value.phase, failure.value.code) == ("client", "internal_error")
    assert "private detail" not in diagnostics.getvalue()


def test_authorize_services_result_payload_shape(
    tmp_path: Path, background_thread
) -> None:
    backend = FakeSecretBackend()
    installed = install_fake_client(tmp_path, backend)

    result = authorize_services(
        ["drive"],
        home=tmp_path,
        account_hint=None,
        open_browser=make_callback_opener(background_thread),
        urlopen=make_fake_urlopen(
            token_uri=installed["token_uri"],
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=backend,
    )
    payload = result.as_payload()
    assert payload["schema_version"] == 1
    assert set(payload) == {
        "schema_version",
        "account",
        "credential_id",
        "requested_services",
        "granted_services",
        "denied_services",
    }


def test_custom_client_secret_reference_survives_authorize_and_refresh(
    tmp_path: Path, secret_backend: FakeSecretBackend, background_thread
) -> None:
    from officina.common import google_credentials as gc

    path = gc.canonical_client_path(home=tmp_path, platform=PLATFORM)
    path.parent.mkdir(parents=True)
    payload = desktop_client_payload()
    payload["installed"].pop("client_secret")
    payload["installed"]["client_secret_ref"] = "custom-client-secret-ref"
    path.write_text(json.dumps(payload), encoding="utf-8")
    secret_backend.store("connect-google", "custom-client-secret-ref", "secret")
    fake_urlopen = make_fake_urlopen(
        token_uri="https://oauth2.googleapis.com/token",
        granted_scope="openid email https://www.googleapis.com/auth/drive",
    )

    result = authorize_services(
        ["drive"],
        home=tmp_path,
        account_hint=None,
        open_browser=make_callback_opener(background_thread),
        urlopen=fake_urlopen,
        platform=PLATFORM,
        secret_backend=secret_backend,
    )
    loaded = gc.load_credential(
        result.credential_id, home=tmp_path, platform=PLATFORM
    )
    access_token = gc.refresh_access_token(
        result.credential_id,
        required_scopes={"openid"},
        home=tmp_path,
        platform=PLATFORM,
        urlopen=fake_urlopen,
        secret_backend=secret_backend,
    )

    assert loaded.client_secret_ref == "custom-client-secret-ref"
    assert access_token == "fake-access-token"
    assert secret_backend.lookup(
        "connect-google", "oauth-client:test-client-id:client-secret"
    ) is None


def test_authorize_services_partial_grant_still_stores_granted_subset(
    tmp_path: Path, background_thread
) -> None:
    backend = FakeSecretBackend()
    installed = install_fake_client(tmp_path, backend)

    result = authorize_services(
        ["drive", "gmail"],
        home=tmp_path,
        account_hint=None,
        open_browser=make_callback_opener(background_thread),
        urlopen=make_fake_urlopen(
            token_uri=installed["token_uri"],
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=backend,
    )

    assert result.granted_services == ("drive",)
    assert result.denied_services == ("gmail",)

    ref = load_credential(result.credential_id, home=tmp_path, platform=PLATFORM)
    assert "https://www.googleapis.com/auth/drive" in ref.granted_scopes
    assert "https://mail.google.com/" not in ref.granted_scopes


def test_authorize_services_state_mismatch_stores_no_credential(
    tmp_path: Path, background_thread
) -> None:
    backend = FakeSecretBackend()
    install_fake_client(tmp_path, backend)

    with pytest.raises(GoogleCredentialError):
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            open_browser=make_callback_opener(
                background_thread, state_override="wrong-state"
            ),
            urlopen=forbidden_urlopen,
            platform=PLATFORM,
            secret_backend=backend,
            callback_deadline_seconds=1,
        )

    from officina.common.famulus_paths import resolve_famulus_paths

    registry_path = resolve_famulus_paths(platform=PLATFORM, home=tmp_path).config_root / "connect-google" / "credentials.json"
    assert not registry_path.exists()


def test_authorize_services_account_hint_mismatch_stores_no_credential(
    tmp_path: Path, background_thread
) -> None:
    backend = FakeSecretBackend()
    installed = install_fake_client(tmp_path, backend)

    with pytest.raises(GoogleCredentialError):
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint="someone-else@example.test",
            open_browser=make_callback_opener(background_thread),
            urlopen=make_fake_urlopen(
                token_uri=installed["token_uri"],
                granted_scope="openid email https://www.googleapis.com/auth/drive",
                email="user@example.test",
            ),
            platform=PLATFORM,
            secret_backend=backend,
        )

    from officina.common.famulus_paths import resolve_famulus_paths

    registry_path = resolve_famulus_paths(platform=PLATFORM, home=tmp_path).config_root / "connect-google" / "credentials.json"
    assert not registry_path.exists()


def test_authorize_services_never_calls_real_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, background_thread
) -> None:
    def forbidden(*_args: object, **_kwargs: object):
        raise AssertionError("no real network call permitted in tests")

    monkeypatch.setattr("webbrowser.open", forbidden)

    backend = FakeSecretBackend()
    install_fake_client(tmp_path, backend)

    # Neither open_browser nor urlopen is passed explicitly: the defaults
    # for network access must be resolved dynamically from the now-patched
    # urllib module. Browser launch is supplied as the in-process callback
    # fixture because production browser launch is intentionally isolated in
    # another process and cannot inherit this monkeypatch.
    diagnostics = io.StringIO()
    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services(
            ["drive"],
            home=tmp_path,
            account_hint=None,
            open_browser=make_callback_opener(background_thread),
            urlopen=forbidden,
            platform=PLATFORM,
            secret_backend=backend,
            diagnostic_stream=diagnostics,
        )

    assert (failure.value.phase, failure.value.code) == (
        "token_exchange",
        "internal_error",
    )
    assert "no real network call permitted" not in diagnostics.getvalue()


@pytest.mark.parametrize(
    "request_target, expected_kind, expected_value",
    [
        ("/?state=expected&code=abc", "code", "abc"),
        ("/?state=expected&error=access_denied", "denied", "access_denied"),
        ("/?state=expected&error=temporarily_unavailable", None, None),
        ("/favicon.ico?state=expected&code=abc", None, None),
        ("/?state=wrong&code=abc", None, None),
        ("/?state=expected&code=a&code=b", None, None),
        ("/?state=expected&code=a&error=access_denied", None, None),
    ],
)
def test_parse_callback_request_is_exact_and_allowlists_only_access_denied(
    request_target, expected_kind, expected_value
) -> None:
    result = authorize_services_module._parse_callback_request(
        method="GET", target=request_target, expected_state="expected"
    )

    if expected_kind is None:
        assert result is None
    else:
        assert result.kind == expected_kind
        assert result.value == expected_value


def test_unknown_google_error_does_not_consume_callback_attempt(
    loopback_listener, background_thread
) -> None:
    port = loopback_listener.getsockname()[1]

    def send_callbacks() -> None:
        for target in (
            "/?state=expected&error=temporarily_unavailable",
            "/?state=expected&code=accepted-code",
        ):
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                client.sendall(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                client.recv(4096)

    background_thread(send_callbacks)
    result = authorize_services_module._wait_for_callback(
        loopback_listener,
        state="expected",
        deadline=time.monotonic() + 2,
    )

    assert result == authorize_services_module.CallbackResult(
        kind="code", value="accepted-code"
    )


def test_oversized_callback_is_ignored_before_valid_callback(
    loopback_listener, background_thread
) -> None:
    port = loopback_listener.getsockname()[1]

    def send_callbacks() -> None:
        oversized = (
            b"GET /?state=expected&code="
            + b"x" * authorize_services_module.CALLBACK_MAX_REQUEST_BYTES
            + b" HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        for request in (
            oversized,
            b"GET /?state=expected&code=accepted HTTP/1.1\r\n"
            b"Host: localhost\r\n\r\n",
        ):
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                client.sendall(request)
                client.recv(4096)

    background_thread(send_callbacks)
    result = authorize_services_module._wait_for_callback(
        loopback_listener,
        state="expected",
        deadline=time.monotonic() + 2,
    )

    assert result == authorize_services_module.CallbackResult("code", "accepted")


def test_callback_deadline_expires_when_no_client_connects(loopback_listener) -> None:
    started = time.monotonic()

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services_module._wait_for_callback(
            loopback_listener,
            state="expected",
            deadline=started + 0.05,
        )

    assert (failure.value.phase, failure.value.code) == (
        "callback",
        "callback_timeout",
    )
    assert time.monotonic() - started < 0.5


def test_slow_drip_callback_cannot_extend_global_deadline(
    loopback_listener, background_thread
) -> None:
    port = loopback_listener.getsockname()[1]
    request = b"GET /?state=expected&code=late HTTP/1.1\r\nHost: localhost\r\n\r\n"

    def drip_request() -> None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                for byte in request:
                    client.sendall(bytes([byte]))
                    time.sleep(0.02)
        except OSError:
            pass

    background_thread(drip_request)
    started = time.monotonic()
    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services_module._wait_for_callback(
            loopback_listener,
            state="expected",
            deadline=started + 0.1,
        )

    assert failure.value.phase == "callback"
    assert failure.value.code == "callback_timeout"
    assert time.monotonic() - started < 0.5


def test_complete_callback_received_after_absolute_deadline_is_rejected() -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return (
                b"GET /?state=expected&code=too-late HTTP/1.1\r\n"
                b"Host: localhost\r\n\r\n"
            )

        def sendall(self, _response):
            pass

    class Listener:
        def settimeout(self, _timeout):
            pass

        def accept(self):
            return Connection(), ("127.0.0.1", 1)

    times = iter((0.0, 0.0, 2.0))

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services_module._wait_for_callback(
            Listener(),
            state="expected",
            deadline=1.0,
            monotonic=lambda: next(times),
        )

    assert (failure.value.phase, failure.value.code) == (
        "callback",
        "callback_timeout",
    )


def test_reset_callback_connection_is_ignored_before_valid_callback() -> None:
    class Connection:
        def __init__(self, payload: bytes | None) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            if self.payload is None:
                raise ConnectionResetError("peer reset")
            payload, self.payload = self.payload, b""
            return payload

        def sendall(self, _response):
            pass

    class Listener:
        def __init__(self) -> None:
            self.connections = [
                Connection(None),
                Connection(
                    b"GET /?state=expected&code=accepted HTTP/1.1\r\n"
                    b"Host: localhost\r\n\r\n"
                ),
            ]

        def settimeout(self, _timeout):
            pass

        def accept(self):
            return self.connections.pop(0), ("127.0.0.1", 1)

    result = authorize_services_module._wait_for_callback(
        Listener(), state="expected", deadline=time.monotonic() + 1
    )

    assert result == authorize_services_module.CallbackResult("code", "accepted")


@pytest.mark.parametrize(
    ("email", "sub"),
    [("", "sub-12345"), ("   ", "sub-12345"), ("user@example.test", "")],
)
def test_blank_verified_identity_is_rejected_without_publication(
    installed_drive_client, background_thread, email: str, sub: str
) -> None:
    home, backend, _installed = installed_drive_client

    with pytest.raises(authorize_services_module.AuthorizationFailure) as failure:
        authorize_services(
            ["drive"],
            home=home,
            account_hint=None,
            open_browser=make_callback_opener(background_thread),
            urlopen=make_fake_urlopen(
                token_uri="https://oauth2.googleapis.com/token",
                granted_scope="openid email https://www.googleapis.com/auth/drive",
                email=email,
                sub=sub,
            ),
            platform=PLATFORM,
            secret_backend=backend,
        )

    assert (failure.value.phase, failure.value.code) == ("userinfo", "userinfo_failed")
    from officina.common import google_credentials as gc

    assert not gc._credentials_registry_path(home=home, platform=PLATFORM).exists()


def test_authorization_emits_manual_url_before_browser_attempt(
    installed_drive_client, capsys, background_thread
) -> None:
    home, backend, installed = installed_drive_client
    observed = {}

    def opener(url: str) -> None:
        observed["stderr_before_open"] = capsys.readouterr().err
        make_callback_opener(background_thread)(url)

    result = authorize_services(
        ["drive"],
        home=home,
        account_hint=None,
        open_browser=opener,
        urlopen=make_fake_urlopen(
            token_uri="https://oauth2.googleapis.com/token",
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=backend,
    )

    events = [json.loads(line) for line in observed["stderr_before_open"].splitlines()]
    assert result.granted_services == ("drive",)
    names = [event["event"] for event in events]
    assert names[-2:] == ["oauth.ssh_tunnel", "oauth.browser_launch"]
    assert names.index("oauth.authorization_url") < names.index("oauth.browser_launch")
    assert all("code_verifier" not in json.dumps(event) for event in events)


@pytest.fixture
def blocked_browser_process():
    class Stdin:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            self.data.extend(data)

        def close(self):
            pass

    class Process:
        def __init__(self):
            self.stdin = Stdin()
            self.terminated = False
            self.killed = False
            self.waits = 0

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, *, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("browser", timeout)
            return -9

    return Process()


def test_browser_helper_receives_url_on_stdin_not_command_line_and_is_reaped(
    blocked_browser_process
) -> None:
    observed = {}

    def popen(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return blocked_browser_process

    url = "https://accounts.google.test/auth?state=private-state"
    process = authorize_services_module._start_browser_helper(url, popen=popen)
    authorize_services_module._stop_browser_helper(process)

    assert url not in observed["argv"]
    assert bytes(process.stdin.data) == (url + "\n").encode()
    assert observed["kwargs"]["stdout"] is subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is subprocess.DEVNULL
    assert process.terminated is True
    assert process.killed is True
    assert process.waits == 2


def test_browser_helper_is_reaped_when_stdin_handoff_fails() -> None:
    class BrokenStdin:
        def write(self, _data):
            raise BrokenPipeError("helper exited")

        def close(self):
            pass

    class Process:
        def __init__(self) -> None:
            self.stdin = BrokenStdin()
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, *, timeout):
            return 1

    process = Process()

    with pytest.raises(BrokenPipeError):
        authorize_services_module._start_browser_helper(
            "https://accounts.google.test/auth", popen=lambda *_args, **_kwargs: process
        )

    assert process.terminated is True


@pytest.mark.parametrize(
    ("returncode", "expected_status"),
    [(0, "opened"), (1, "unavailable"), (2, "error"), (17, "error")],
)
def test_completed_browser_helper_emits_sanitized_result_before_token_exchange(
    installed_drive_client,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    expected_status: str,
) -> None:
    home, backend, _installed = installed_drive_client

    class CompletedProcess:
        def poll(self):
            return returncode

    monkeypatch.setattr(
        authorize_services_module, "_start_browser_helper", lambda _url: CompletedProcess()
    )
    monkeypatch.setattr(
        authorize_services_module,
        "_wait_for_callback",
        lambda *_args, **_kwargs: authorize_services_module.CallbackResult(
            kind="code", value="auth-code"
        ),
    )
    diagnostics = io.StringIO()

    authorize_services(
        ["drive"],
        home=home,
        account_hint=None,
        urlopen=make_fake_urlopen(
            token_uri="https://oauth2.googleapis.com/token",
            granted_scope="openid email https://www.googleapis.com/auth/drive",
        ),
        platform=PLATFORM,
        secret_backend=backend,
        diagnostic_stream=diagnostics,
    )

    events = [json.loads(line) for line in diagnostics.getvalue().splitlines()]
    browser_result = next(event for event in events if event["event"] == "oauth.browser_result")
    assert browser_result == {
        "schema_version": 1,
        "event": "oauth.browser_result",
        "status": expected_status,
    }
    assert events.index(browser_result) < next(
        index
        for index, event in enumerate(events)
        if event["event"] == "oauth.token_exchange"
    )


@pytest.mark.parametrize(
    ("helper_mode", "launch_status", "browser_result_status"),
    [
        ("returned", "started", "opened"),
        ("errored", "started", "error"),
        ("blocked", "started", None),
        ("launch-failed", "failed", None),
    ],
)
def test_exact_success_diagnostic_sequence_is_terminal_and_redacted(
    installed_drive_client,
    monkeypatch: pytest.MonkeyPatch,
    helper_mode: str,
    launch_status: str,
    browser_result_status: str | None,
) -> None:
    home, backend, _installed = installed_drive_client

    class CompletedHelper:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

        def poll(self):
            return self.returncode

    class BlockedHelper:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, *, timeout):
            return -15

    blocked = BlockedHelper()

    def start_helper(_url):
        if helper_mode == "returned":
            return CompletedHelper(0)
        if helper_mode == "errored":
            return CompletedHelper(2)
        if helper_mode == "blocked":
            return blocked
        raise OSError("browser launcher unavailable")

    monkeypatch.setattr(authorize_services_module, "_start_browser_helper", start_helper)
    monkeypatch.setattr(
        authorize_services_module,
        "_wait_for_callback",
        lambda *_args, **_kwargs: authorize_services_module.CallbackResult(
            "code", "AUTH_CODE_SENTINEL"
        ),
    )
    monkeypatch.setattr(
        authorize_services_module,
        "_generate_pkce",
        lambda: ("VERIFIER_SENTINEL", "CHALLENGE_SENTINEL"),
    )
    monkeypatch.setattr(
        authorize_services_module.secrets,
        "token_urlsafe",
        lambda _size: "STATE_SENTINEL",
    )
    diagnostics = io.StringIO()

    result = authorize_services(
        ["drive"],
        home=home,
        account_hint=None,
        urlopen=make_fake_urlopen(
            token_uri="https://oauth2.googleapis.com/token",
            granted_scope="openid email https://www.googleapis.com/auth/drive",
            access_token="ACCESS_TOKEN_SENTINEL",
            refresh_token="REFRESH_TOKEN_SENTINEL",
        ),
        platform=PLATFORM,
        secret_backend=backend,
        diagnostic_stream=diagnostics,
    )

    events = [json.loads(line) for line in diagnostics.getvalue().splitlines()]
    names = [event["event"] for event in events]
    expected_names = [
        "oauth.client_ready",
        "oauth.listener_ready",
        "oauth.authorization_url",
        "oauth.ssh_tunnel",
        "oauth.browser_launch",
        "oauth.awaiting_callback",
    ]
    if browser_result_status is not None:
        expected_names.append("oauth.browser_result")
    expected_names.extend(
        [
            "oauth.callback_received",
            "oauth.token_exchange",
            "oauth.userinfo",
            "oauth.credential_publish",
            "oauth.complete",
        ]
    )
    assert names == expected_names
    assert len(names) == len(set(names))
    expected_statuses = {
        "oauth.client_ready": "ready",
        "oauth.listener_ready": "ready",
        "oauth.authorization_url": "available",
        "oauth.ssh_tunnel": "available",
        "oauth.browser_launch": launch_status,
        "oauth.awaiting_callback": "waiting",
        "oauth.callback_received": "code",
        "oauth.token_exchange": "started",
        "oauth.userinfo": "started",
        "oauth.credential_publish": "started",
        "oauth.complete": "authorized",
    }
    if browser_result_status is not None:
        expected_statuses["oauth.browser_result"] = browser_result_status
    expected_extra_fields = {
        "oauth.client_ready": {"services"},
        "oauth.listener_ready": {
            "address",
            "port",
            "callback_deadline_seconds",
        },
        "oauth.authorization_url": {"url"},
        "oauth.ssh_tunnel": {"command"},
        "oauth.browser_launch": set(),
        "oauth.browser_result": set(),
        "oauth.awaiting_callback": set(),
        "oauth.callback_received": set(),
        "oauth.token_exchange": set(),
        "oauth.userinfo": set(),
        "oauth.credential_publish": set(),
        "oauth.complete": {"granted_services", "denied_services", "warnings"},
    }
    base_fields = {"schema_version", "event", "status"}
    for event in events:
        assert event["status"] == expected_statuses[event["event"]]
        assert set(event) == base_fields | expected_extra_fields[event["event"]]
    assert events[-1] == {
        "schema_version": 1,
        "event": "oauth.complete",
        "status": "authorized",
        "granted_services": ["drive"],
        "denied_services": [],
        "warnings": [],
    }
    assert all(event["schema_version"] == 1 for event in events)
    assert next(event for event in events if event["event"] == "oauth.browser_launch") == {
        "schema_version": 1,
        "event": "oauth.browser_launch",
        "status": launch_status,
    }
    result_events = [
        event for event in events if event["event"] == "oauth.browser_result"
    ]
    if browser_result_status is None:
        assert result_events == []
    else:
        assert result_events == [
            {
                "schema_version": 1,
                "event": "oauth.browser_result",
                "status": browser_result_status,
            }
        ]

    listener_event = events[1]
    port = listener_event["port"]
    assert listener_event == {
        "schema_version": 1,
        "event": "oauth.listener_ready",
        "status": "ready",
        "address": "127.0.0.1",
        "port": port,
        "callback_deadline_seconds": 300,
    }
    authorization_event = events[2]
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(authorization_event["url"]).query
    )
    assert query["state"] == ["STATE_SENTINEL"]
    assert query["redirect_uri"] == [f"http://127.0.0.1:{port}/"]
    assert events[3] == {
        "schema_version": 1,
        "event": "oauth.ssh_tunnel",
        "status": "available",
        "command": (
            "ssh -N -o ExitOnForwardFailure=yes "
            f"-L 127.0.0.1:{port}:127.0.0.1:{port} user@remote-host"
        ),
    }
    rendered = diagnostics.getvalue()
    assert rendered.count("STATE_SENTINEL") == 1
    for forbidden in (
        "VERIFIER_SENTINEL",
        "AUTH_CODE_SENTINEL",
        "shh-its-a-secret",
        "ACCESS_TOKEN_SENTINEL",
        "REFRESH_TOKEN_SENTINEL",
    ):
        assert forbidden not in rendered
    assert result.granted_services == ("drive",)
    assert blocked.terminated is (helper_mode == "blocked")


def test_no_open_browser_cli_disables_helper_and_keeps_success_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    observed = {}

    def fake_authorize(services, **kwargs):
        observed.update(kwargs)
        return authorize_services_module.AuthorizationResult(
            account="user@example.test",
            credential_id="google:sub1",
            requested_services=("drive",),
            granted_services=("drive",),
            denied_services=(),
        )

    monkeypatch.setattr(authorize_services_module, "authorize_services", fake_authorize)

    code = authorize_services_module.run_authorize_services(
        ["--services", "drive", "--no-open-browser", "--callback-port", "43123"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert observed["browser_enabled"] is False
    assert observed["callback_port"] == 43123
    assert payload["schema_version"] == 1
    assert captured.out == json.dumps(payload) + "\n"
    assert captured.err == ""


def test_cli_sanitizes_unexpected_failure_and_leaves_stdout_empty(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        authorize_services_module,
        "authorize_services",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private detail")),
    )

    code = authorize_services_module.run_authorize_services(
        ["--services", "drive", "--no-open-browser"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "schema_version": 1,
        "event": "oauth.failed",
        "status": "error",
        "phase": "client",
        "code": "internal_error",
    }
    assert "private detail" not in captured.err
