from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from officina.credentials.google import (
    GoogleCredentialError,
    IDENTITY_SCOPES,
    SERVICE_SCOPES,
    normalize_services,
    scope_union_for_services,
)


class FakeSecretBackend:
    def __init__(self):
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


class JsonResponse:
    def __init__(self, payload: object) -> None:
        self.data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.data if size < 0 else self.data[:size]


class BytesResponse(JsonResponse):
    def __init__(self, data: bytes) -> None:
        self.data = data


class MutableSecretBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.cleared: list[tuple[str, str]] = []

    def store(self, namespace: str, key: str, value: str) -> None:
        self.values[(namespace, key)] = value

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        self.cleared.append((namespace, key))
        return self.values.pop((namespace, key), None) is not None


@pytest.fixture
def mutable_secret_backend() -> MutableSecretBackend:
    """Fast in-memory backend with observable cleanup for publication faults."""

    return MutableSecretBackend()


@pytest.fixture
def registry_writer(tmp_path):
    import officina.credentials.google as gc
    from officina.credentials.oauth import write_oauth_json

    path = gc._credentials_registry_path(home=tmp_path, platform="linux")

    def write(credentials: dict[str, object]) -> None:
        write_oauth_json(
            path,
            {"schema_version": 2, "credentials": credentials},
        )

    return write


def test_normalize_services_preserves_order_and_dedupes():
    assert normalize_services(["drive", "calendar", "drive"]) == ("drive", "calendar")


def test_normalize_services_rejects_empty():
    with pytest.raises(GoogleCredentialError):
        normalize_services([])


def test_normalize_services_rejects_unknown_service():
    with pytest.raises(GoogleCredentialError):
        normalize_services(["dropbox"])


def test_scope_union_for_services_includes_identity_scopes():
    scopes = scope_union_for_services(["drive"])
    assert scopes == SERVICE_SCOPES["drive"] | IDENTITY_SCOPES


def _desktop_client_payload(client_id: str = "abc.apps.googleusercontent.com") -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "project_id": "famulus-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "sekret-value",
            "redirect_uris": ["http://localhost"],
        }
    }


def test_install_client_stores_secret_and_strips_it_from_disk(tmp_path):
    from officina.credentials.google import canonical_client_path, install_client

    backend = FakeSecretBackend()
    result = install_client(
        _desktop_client_payload(), home=tmp_path, platform="linux", replace=False, secret_backend=backend
    )

    assert result["status"] == "installed"
    assert backend.stored == [
        ("connect-google", "oauth-client:abc.apps.googleusercontent.com:client-secret", "sekret-value")
    ]

    installed_path = canonical_client_path(home=tmp_path, platform="linux")
    installed = json.loads(installed_path.read_text())
    rendered = json.dumps(installed)
    assert "client_secret" not in rendered or "client_secret_ref" in installed["installed"]
    assert "sekret-value" not in rendered
    assert "client_secret" not in installed["installed"]
    assert (
        installed["installed"]["client_secret_ref"]
        == "oauth-client:abc.apps.googleusercontent.com:client-secret"
    )


def test_install_client_is_idempotent_when_unchanged(tmp_path):
    from officina.credentials.google import install_client

    backend = FakeSecretBackend()
    payload = _desktop_client_payload()
    install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=backend)

    second = install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=FakeSecretBackend())
    assert second["status"] == "unchanged"


def test_install_client_refuses_different_client_without_replace(tmp_path):
    from officina.credentials.google import install_client

    install_client(
        _desktop_client_payload("old-client"), home=tmp_path, platform="linux", replace=False,
        secret_backend=FakeSecretBackend(),
    )

    with pytest.raises(GoogleCredentialError):
        install_client(
            _desktop_client_payload("new-client"), home=tmp_path, platform="linux", replace=False,
            secret_backend=FakeSecretBackend(),
        )


def test_install_client_rejected_reinstall_stores_no_new_secret(tmp_path):
    """A rejected reinstall (conflict, no replace) must not leave an orphaned
    secret in the secret store: install_client must check for the conflict
    before ever calling backend.store()."""
    from officina.credentials.google import install_client

    backend = FakeSecretBackend()
    install_client(
        _desktop_client_payload("old-client"), home=tmp_path, platform="linux", replace=False,
        secret_backend=backend,
    )
    stored_after_first_install = list(backend.stored)

    with pytest.raises(GoogleCredentialError):
        install_client(
            _desktop_client_payload("new-client"), home=tmp_path, platform="linux", replace=False,
            secret_backend=backend,
        )

    assert backend.stored == stored_after_first_install


def test_install_client_rotates_secret_when_client_id_unchanged(tmp_path):
    """Regression test (confirmed failing via direct execution): reinstalling
    the SAME client_id with a DIFFERENT (rotated) client_secret must
    genuinely store the new secret, even though the redacted on-disk JSON
    (client_secret replaced by a client_secret_ref derived solely from
    client_id) looks identical to what's already there. Before the fix,
    `current == payload` was True in this scenario and install_client
    returned status="unchanged" without ever calling backend.store(), so the
    old, possibly-revoked secret stayed in the secret store.

    Uses the SAME backend instance across both calls, matching real
    production usage (secret_backend defaults to the shared secret_store
    module, not a fresh instance per call).
    """
    from officina.credentials.google import install_client

    backend = FakeSecretBackend()
    client_id = "abc.apps.googleusercontent.com"
    install_client(
        _desktop_client_payload(client_id), home=tmp_path, platform="linux", replace=False, secret_backend=backend
    )
    assert backend.lookup("connect-google", f"oauth-client:{client_id}:client-secret") == "sekret-value"

    rotated_payload = _desktop_client_payload(client_id)
    rotated_payload["installed"]["client_secret"] = "rotated-sekret-value"

    install_client(rotated_payload, home=tmp_path, platform="linux", replace=True, secret_backend=backend)

    assert backend.lookup("connect-google", f"oauth-client:{client_id}:client-secret") == "rotated-sekret-value"


def test_install_client_rejects_missing_client_secret(tmp_path):
    from officina.credentials.google import install_client

    payload = _desktop_client_payload()
    del payload["installed"]["client_secret"]

    with pytest.raises(GoogleCredentialError):
        install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=FakeSecretBackend())


def test_refresh_access_token_checks_scopes_before_network_call(tmp_path):
    from officina.credentials.google import (
        refresh_access_token,
        store_google_credential,
    )

    store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=FakeSecretBackend(),
    )

    def fail_if_called(*a, **k):
        raise AssertionError("urlopen should not be called when required scopes are missing")

    with pytest.raises(GoogleCredentialError):
        refresh_access_token(
            "google:sub1", required_scopes={"https://mail.google.com/"},
            home=tmp_path, platform="linux", urlopen=fail_if_called, secret_backend=FakeSecretBackend(),
        )


def test_store_google_credential_concurrent_writes_dont_lose_entries(tmp_path, monkeypatch):
    """Genuinely concurrent race (two real threads alive at the same time,
    not a sequential simulation): writer1 is started and, while genuinely
    INSIDE store_google_credential's locked critical section -- past its own
    read of the registry, before its write -- writer2 is started and
    attempts to enter. Without a lock serializing them, writer2's
    read-modify-write would interleave with writer1's and its os.replace()
    would silently overwrite the registry with a copy missing writer1's
    newly-stored credential entry (even though writer1's refresh token was
    already durably written to the secret store) -- an orphaned secret and
    an unresolvable credential_id for the lost account.

    Proves the file lock closes it: writer2 cannot even read the registry
    until writer1 releases the lock (i.e. has already written), so both
    credential entries survive.
    """
    import threading

    import officina.credentials.google as gc_module

    home = tmp_path
    platform = "linux"

    writer1_in_critical_section = threading.Event()
    release_writer1 = threading.Event()

    def delayed_hook(subject: str) -> None:
        if subject == "sub1":
            writer1_in_critical_section.set()
            release_writer1.wait(timeout=10)

    monkeypatch.setattr(gc_module, "_test_race_delay", delayed_hook)

    def run_writer1():
        gc_module.store_google_credential(
            subject="sub1", account="a1@example.com", client_id="c1",
            token_uri="https://oauth2.googleapis.com/token",
            granted_scopes=frozenset({"openid"}), refresh_token="rt1",
            home=home, platform=platform, secret_backend=FakeSecretBackend(),
        )

    writer2_attempting = threading.Event()

    def run_writer2():
        writer2_attempting.set()
        gc_module.store_google_credential(
            subject="sub2", account="a2@example.com", client_id="c2",
            token_uri="https://oauth2.googleapis.com/token",
            granted_scopes=frozenset({"openid"}), refresh_token="rt2",
            home=home, platform=platform, secret_backend=FakeSecretBackend(),
        )

    t1 = threading.Thread(target=run_writer1)
    t1.start()
    assert writer1_in_critical_section.wait(timeout=10), "writer1 never entered its critical section"

    t2 = threading.Thread(target=run_writer2)
    t2.start()
    assert writer2_attempting.wait(timeout=10)
    # Give writer2 a moment to genuinely attempt (and, pre-fix, succeed at)
    # acquiring/racing past the registry before writer1 finishes.
    import time as _time
    _time.sleep(0.2)

    release_writer1.set()
    t1.join(timeout=10)
    t2.join(timeout=10)

    registry_path = gc_module._credentials_registry_path(home=home, platform=platform)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "google:sub1" in registry["credentials"], "writer1's entry was lost to the race"
    assert "google:sub2" in registry["credentials"], "writer2's entry was lost to the race"


def test_store_and_load_credential_round_trip(tmp_path):
    from officina.credentials.google import load_credential, store_google_credential

    ref = store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=FakeSecretBackend(),
    )

    loaded = load_credential("google:sub1", home=tmp_path, platform="linux")
    assert loaded == ref


@pytest.mark.parametrize(
    "payload",
    ["{", "[]", '{"credentials": {"legacy-id": {}}}'],
    ids=["malformed-json", "non-object", "invalid-record"],
)
def test_load_credential_rejects_malformed_legacy_registry(tmp_path, payload):
    import officina.credentials.google as google_credentials

    registry_path = google_credentials._credentials_registry_path(
        home=tmp_path, platform="linux"
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(payload, encoding="utf-8")

    with pytest.raises(GoogleCredentialError, match="invalid credential registry"):
        google_credentials.load_credential(
            "legacy-id", home=tmp_path, platform="linux"
        )


def test_load_credential_rejects_unreadable_legacy_registry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    import officina.credentials.google as google_credentials

    registry_path = google_credentials._credentials_registry_path(
        home=tmp_path, platform="linux"
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text

    def unreadable(path: Path, *args, **kwargs):
        if path == registry_path:
            raise PermissionError("unreadable registry")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(GoogleCredentialError, match="invalid credential registry"):
        google_credentials.load_credential(
            "legacy-id", home=tmp_path, platform="linux"
        )


def test_load_credential_rejects_invalid_utf8_legacy_registry(tmp_path):
    import officina.credentials.google as google_credentials

    registry_path = google_credentials._credentials_registry_path(
        home=tmp_path, platform="linux"
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(b"\xff")

    with pytest.raises(GoogleCredentialError, match="invalid credential registry"):
        google_credentials.load_credential(
            "legacy-id", home=tmp_path, platform="linux"
        )


def test_schema_2_preserves_custom_client_secret_ref_for_later_refresh(tmp_path):
    import officina.credentials.google as gc

    backend = FakeSecretBackend()
    backend.store("connect-google", "custom-client-secret-ref", "client-secret")
    ref = gc.store_google_credential(
        subject="sub1",
        account="user@example.com",
        client_id="abc",
        client_secret_ref="custom-client-secret-ref",
        token_uri="https://attacker.invalid/token",
        granted_scopes=frozenset({"openid", "email"}),
        refresh_token="rt",
        home=tmp_path,
        platform="linux",
        secret_backend=backend,
    )

    def fake_urlopen(request, *, timeout):
        assert request.full_url == "https://oauth2.googleapis.com/token"
        assert timeout == 30.0
        return JsonResponse({"access_token": "fresh"})

    loaded = gc.load_credential(ref.credential_id, home=tmp_path, platform="linux")
    token = gc.refresh_access_token(
        ref.credential_id,
        required_scopes={"openid"},
        home=tmp_path,
        platform="linux",
        urlopen=fake_urlopen,
        secret_backend=backend,
    )

    assert loaded.client_secret_ref == "custom-client-secret-ref"
    assert token == "fresh"


def test_refresh_access_token_exchanges_refresh_token(tmp_path):
    from officina.credentials.google import refresh_access_token, store_google_credential

    backend = FakeSecretBackend()
    store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=backend,
    )
    backend.store("connect-google", "oauth-client:abc:client-secret", "client-secret-value")

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            data = json.dumps({"access_token": "new-access-token"}).encode()
            return data if size < 0 else data[:size]

    def fake_urlopen(request, *, timeout):
        assert timeout == 30.0
        calls.append(request)
        return FakeResponse()

    token = refresh_access_token(
        "google:sub1", required_scopes={"https://www.googleapis.com/auth/drive"},
        home=tmp_path, platform="linux", urlopen=fake_urlopen, secret_backend=backend,
    )
    assert token == "new-access-token"
    assert len(calls) == 1


def test_refresh_access_token_wraps_http_error_as_google_credential_error(tmp_path):
    """Regression test: a revoked/expired refresh token is a routine
    occurrence -- Google's token endpoint returns HTTP 400 for it, and
    urlopen() raises urllib.error.HTTPError. Every sibling OAuth path in
    this repo (e.g. _oauth_tokens.py's _post_form) converts that into a
    clean domain error instead of letting the raw urllib exception escape;
    _exchange_refresh_token must do the same.
    """
    from officina.credentials.google import refresh_access_token, store_google_credential

    backend = FakeSecretBackend()
    store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=backend,
    )
    backend.store("connect-google", "oauth-client:abc:client-secret", "client-secret-value")

    def fake_urlopen(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, None
        )

    with pytest.raises(GoogleCredentialError):
        refresh_access_token(
            "google:sub1", required_scopes={"https://www.googleapis.com/auth/drive"},
            home=tmp_path, platform="linux", urlopen=fake_urlopen, secret_backend=backend,
        )


def test_refresh_default_path_uses_redirect_rejecting_opener(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import officina.credentials.google as gc

    backend = FakeSecretBackend()
    gc.store_google_credential(
        subject="sub1",
        account="user@example.com",
        client_id="abc",
        token_uri="https://attacker.invalid/token",
        granted_scopes=frozenset({"openid", "email"}),
        refresh_token="rt",
        home=tmp_path,
        platform="linux",
        secret_backend=backend,
    )
    backend.store("connect-google", "oauth-client:abc:client-secret", "secret")
    observed = []

    def hardened_open(request, *, timeout):
        observed.append((request.full_url, timeout))
        return JsonResponse({"access_token": "fresh"})

    def raw_urlopen(*_args, **_kwargs):
        raise AssertionError("raw urllib opener bypassed redirect rejection")

    monkeypatch.setattr(gc, "_default_urlopen", hardened_open)
    monkeypatch.setattr(gc.urllib.request, "urlopen", raw_urlopen)

    token = gc.refresh_access_token(
        "google:sub1",
        required_scopes={"openid"},
        home=tmp_path,
        platform="linux",
        secret_backend=backend,
    )

    assert token == "fresh"
    assert observed == [("https://oauth2.googleapis.com/token", 30.0)]

def test_exchange_authorization_code_wraps_http_error_as_google_credential_error(tmp_path):
    from officina.credentials.google import exchange_authorization_code

    backend = FakeSecretBackend()
    backend.store("connect-google", "oauth-client:abc:client-secret", "client-secret-value")

    def fake_urlopen(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, None
        )

    with pytest.raises(GoogleCredentialError):
        exchange_authorization_code(
            client_id="abc",
            code="auth-code",
            code_verifier="verifier",
            redirect_uri="http://localhost:1234/callback",
            token_uri="https://oauth2.googleapis.com/token",
            urlopen=fake_urlopen,
            secret_backend=backend,
        )


def test_exchange_uses_pinned_endpoint_timeout_and_exact_client_secret_ref():
    from officina.credentials.google import exchange_authorization_code

    backend = FakeSecretBackend()
    backend.store("connect-google", "custom-client-secret-reference", "client-secret-value")
    seen = []

    def fake_urlopen(request, *, timeout):
        seen.append((request.full_url, timeout))
        return JsonResponse({"access_token": "at", "refresh_token": "rt", "scope": "openid email"})

    result = exchange_authorization_code(
        client_id="abc",
        client_secret_ref="custom-client-secret-reference",
        code="auth-code",
        code_verifier="verifier",
        redirect_uri="http://127.0.0.1:43123/",
        token_uri="https://attacker.invalid/token",
        urlopen=fake_urlopen,
        secret_backend=backend,
    )

    assert result["refresh_token"] == "rt"
    assert seen == [("https://oauth2.googleapis.com/token", 30.0)]


@pytest.mark.parametrize("payload", [[], "text", 17, None])
def test_google_http_boundary_rejects_non_object_json(payload) -> None:
    import officina.credentials.google as gc

    with pytest.raises(GoogleCredentialError, match="non-object"):
        gc._open_google_json(
            urllib.request.Request("https://oauth2.googleapis.com/token"),
            urlopen=lambda *_args, **_kwargs: JsonResponse(payload),
        )


def test_google_http_boundary_rejects_oversized_body_without_echoing_it() -> None:
    import officina.credentials.google as gc

    marker = b"sensitive-response-marker"
    body = marker + b"x" * gc.GOOGLE_HTTP_MAX_BODY_BYTES

    with pytest.raises(GoogleCredentialError) as failure:
        gc._open_google_json(
            urllib.request.Request("https://oauth2.googleapis.com/token"),
            urlopen=lambda *_args, **_kwargs: BytesResponse(body),
        )

    assert "65536" in str(failure.value)
    assert marker.decode() not in str(failure.value)


def test_google_http_boundary_discards_bounded_http_error_body() -> None:
    import officina.credentials.google as gc

    marker = b"private-google-error-body"

    def failing_open(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(marker + b"x" * gc.GOOGLE_HTTP_MAX_BODY_BYTES),
        )

    with pytest.raises(GoogleCredentialError) as failure:
        gc._open_google_json(
            urllib.request.Request("https://oauth2.googleapis.com/token"),
            urlopen=failing_open,
        )

    assert str(failure.value) == "Google endpoint returned HTTP 400"
    assert marker.decode() not in str(failure.value)


def test_production_google_opener_installs_redirect_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import officina.credentials.google as gc

    observed = {}

    class RedirectingOpener:
        def __init__(self, handler) -> None:
            self.handler = handler

        def open(self, request, *, timeout):
            observed["timeout"] = timeout
            return self.handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {"Location": "https://attacker.invalid/token"},
                "https://attacker.invalid/token",
            )

    def build_opener(handler):
        observed["handler"] = handler
        return RedirectingOpener(handler)

    monkeypatch.setattr(gc.urllib.request, "build_opener", build_opener)

    with pytest.raises(GoogleCredentialError, match="redirect refused"):
        gc._open_google_json(
            urllib.request.Request("https://oauth2.googleapis.com/token")
        )

    assert isinstance(observed["handler"], gc._RejectRedirects)
    assert observed["timeout"] == 30.0


def test_load_schema_1_derives_legacy_refresh_reference_and_ignores_token_uri(tmp_path):
    import officina.credentials.google as gc

    path = gc._credentials_registry_path(home=tmp_path, platform="linux")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "credentials": {
                    "google:sub1": {
                        "subject": "sub1",
                        "account": "user@example.test",
                        "client_id": "abc",
                        "token_uri": "https://attacker.invalid/token",
                        "granted_scopes": ["openid", "email"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    ref = gc.load_credential("google:sub1", home=tmp_path, platform="linux")

    assert ref.refresh_secret_ref == "google:sub1:refresh-token"
    assert ref.token_uri == "https://oauth2.googleapis.com/token"


def test_publication_failure_before_replace_clears_new_secret_and_keeps_registry_absent(
    tmp_path, mutable_secret_backend
):
    import officina.credentials.google as gc

    def fail_before_replace(path, payload):
        raise OSError("before replace")

    with pytest.raises(GoogleCredentialError, match="publication failed"):
        gc.store_google_credential(
            subject="sub1",
            account="user@example.test",
            client_id="abc",
            token_uri="https://attacker.invalid/token",
            granted_scopes=frozenset({"openid", "email"}),
            refresh_token="rt",
            home=tmp_path,
            platform="linux",
            secret_backend=mutable_secret_backend,
            registry_writer=fail_before_replace,
            refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000001",
        )

    assert mutable_secret_backend.values == {}
    assert mutable_secret_backend.cleared == [
        ("connect-google", "google-refresh:00000000000000000000000000000001")
    ]
    assert not gc._credentials_registry_path(home=tmp_path, platform="linux").exists()


def test_registry_load_failure_clears_new_secret(
    tmp_path, mutable_secret_backend
) -> None:
    import officina.credentials.google as gc

    path = gc._credentials_registry_path(home=tmp_path, platform="linux")
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    with pytest.raises(GoogleCredentialError, match="cannot read"):
        gc.store_google_credential(
            subject="sub1",
            account="user@example.test",
            client_id="abc",
            token_uri="https://oauth2.googleapis.com/token",
            granted_scopes=frozenset({"openid"}),
            refresh_token="rt",
            home=tmp_path,
            platform="linux",
            secret_backend=mutable_secret_backend,
            refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000011",
        )

    assert mutable_secret_backend.values == {}
    assert mutable_secret_backend.cleared[-1][1].endswith("11")


def test_secret_store_failure_leaves_registry_absent(tmp_path) -> None:
    import officina.credentials.google as gc
    import officina.credentials.secret_store as secret_store

    class FailingBackend(MutableSecretBackend):
        def store(self, namespace: str, key: str, value: str) -> None:
            raise secret_store.SecretStoreUnavailable("unavailable")

    with pytest.raises(secret_store.SecretStoreUnavailable):
        gc.store_google_credential(
            subject="sub1",
            account="user@example.test",
            client_id="abc",
            token_uri="https://oauth2.googleapis.com/token",
            granted_scopes=frozenset({"openid"}),
            refresh_token="rt",
            home=tmp_path,
            platform="linux",
            secret_backend=FailingBackend(),
        )

    assert not gc._credentials_registry_path(home=tmp_path, platform="linux").exists()


def test_failed_replace_keeps_previous_record_and_secret(
    tmp_path, mutable_secret_backend, registry_writer
) -> None:
    import officina.credentials.google as gc

    old_ref = "google-refresh:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    previous = {
        "subject": "sub1",
        "account": "old@example.test",
        "client_id": "abc",
        "client_secret_ref": "custom-client-ref",
        "granted_scopes": ["openid"],
        "refresh_secret_ref": old_ref,
    }
    registry_writer({"google:sub1": previous})
    mutable_secret_backend.store("connect-google", old_ref, "old-token")

    with pytest.raises(GoogleCredentialError, match="publication failed"):
        gc.store_google_credential(
            subject="sub1",
            account="new@example.test",
            client_id="abc",
            token_uri="https://oauth2.googleapis.com/token",
            granted_scopes=frozenset({"openid"}),
            refresh_token="new-token",
            home=tmp_path,
            platform="linux",
            secret_backend=mutable_secret_backend,
            registry_writer=lambda *_args: (_ for _ in ()).throw(OSError("failed")),
            refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000012",
        )

    assert mutable_secret_backend.lookup("connect-google", old_ref) == "old-token"
    assert mutable_secret_backend.lookup(
        "connect-google", "google-refresh:00000000000000000000000000000012"
    ) is None
    loaded = gc.load_credential("google:sub1", home=tmp_path, platform="linux")
    assert loaded.account == "old@example.test"


def test_post_replace_error_retains_new_secret_and_reports_durability_warning(
    tmp_path, mutable_secret_backend
):
    import officina.credentials.google as gc
    from officina.credentials.oauth import write_oauth_json

    def replace_then_fail(path, payload):
        write_oauth_json(path, payload)
        raise OSError("directory fsync failed")

    ref = gc.store_google_credential(
        subject="sub1",
        account="user@example.test",
        client_id="abc",
        token_uri="https://attacker.invalid/token",
        granted_scopes=frozenset({"openid", "email"}),
        refresh_token="rt",
        home=tmp_path,
        platform="linux",
        secret_backend=mutable_secret_backend,
        registry_writer=replace_then_fail,
        refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000002",
    )

    assert ref.publication_warnings == ("registry_durability_warning",)
    assert mutable_secret_backend.lookup("connect-google", ref.refresh_secret_ref) == "rt"
    assert mutable_secret_backend.cleared == []


def test_post_replace_error_with_previous_record_retains_both_secrets(
    tmp_path, mutable_secret_backend, registry_writer
) -> None:
    import officina.credentials.google as gc
    from officina.credentials.oauth import write_oauth_json

    old_ref = "google-refresh:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    registry_writer(
        {
            "google:sub1": {
                "subject": "sub1",
                "account": "old@example.test",
                "client_id": "abc",
                "granted_scopes": ["openid"],
                "refresh_secret_ref": old_ref,
            }
        }
    )
    mutable_secret_backend.store("connect-google", old_ref, "old-token")

    def replace_then_fail(path, payload):
        write_oauth_json(path, payload)
        raise OSError("directory fsync failed")

    ref = gc.store_google_credential(
        subject="sub1",
        account="new@example.test",
        client_id="abc",
        token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid"}),
        refresh_token="new-token",
        home=tmp_path,
        platform="linux",
        secret_backend=mutable_secret_backend,
        registry_writer=replace_then_fail,
        refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000013",
    )

    assert ref.publication_warnings == ("registry_durability_warning",)
    assert mutable_secret_backend.lookup("connect-google", old_ref) == "old-token"
    assert mutable_secret_backend.lookup(
        "connect-google", ref.refresh_secret_ref
    ) == "new-token"


@pytest.mark.parametrize(
    ("mode", "expected_warnings", "expected_clear"),
    [
        ("normal", (), True),
        ("clear-fails", ("secret_cleanup_warning",), True),
        ("shared", (), False),
    ],
)
def test_previous_refresh_secret_cleanup_is_safe_and_nonfatal(
    tmp_path, registry_writer, mode, expected_warnings, expected_clear
) -> None:
    import officina.credentials.google as gc

    class CleanupBackend(MutableSecretBackend):
        def clear(self, namespace: str, key: str) -> bool:
            self.cleared.append((namespace, key))
            if mode == "clear-fails":
                raise RuntimeError("cleanup failed")
            return self.values.pop((namespace, key), None) is not None

    backend = CleanupBackend()
    old_ref = "google-refresh:cccccccccccccccccccccccccccccccc"
    current = {
        "subject": "sub1",
        "account": "old@example.test",
        "client_id": "abc",
        "granted_scopes": ["openid"],
        "refresh_secret_ref": old_ref,
    }
    credentials = {"google:sub1": current}
    if mode == "shared":
        credentials["google:other"] = {
            **current,
            "subject": "other",
            "account": "other@example.test",
        }
    registry_writer(credentials)
    backend.store("connect-google", old_ref, "old-token")

    ref = gc.store_google_credential(
        subject="sub1",
        account="new@example.test",
        client_id="abc",
        token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid"}),
        refresh_token="new-token",
        home=tmp_path,
        platform="linux",
        secret_backend=backend,
        refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000014",
    )

    assert ref.publication_warnings == expected_warnings
    assert (("connect-google", old_ref) in backend.cleared) is expected_clear
    if mode == "normal":
        assert backend.lookup("connect-google", old_ref) is None
    else:
        assert backend.lookup("connect-google", old_ref) == "old-token"


def test_ambiguous_post_replace_state_raises_typed_uncertainty_and_retains_secret(
    tmp_path, mutable_secret_backend
):
    import officina.credentials.google as gc
    from officina.credentials.oauth import write_oauth_json

    def publish_other_state_then_fail(path, payload):
        other = json.loads(json.dumps(payload))
        other["credentials"]["google:sub1"]["account"] = "other@example.test"
        write_oauth_json(path, other)
        raise OSError("uncertain")

    with pytest.raises(gc.GoogleCredentialPublicationUncertain):
        gc.store_google_credential(
            subject="sub1",
            account="user@example.test",
            client_id="abc",
            token_uri="https://attacker.invalid/token",
            granted_scopes=frozenset({"openid", "email"}),
            refresh_token="rt",
            home=tmp_path,
            platform="linux",
            secret_backend=mutable_secret_backend,
            registry_writer=publish_other_state_then_fail,
            refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000003",
        )

    assert mutable_secret_backend.lookup(
        "connect-google", "google-refresh:00000000000000000000000000000003"
    ) == "rt"
    assert mutable_secret_backend.cleared == []


@pytest.mark.parametrize("malformed_ref", ["", 17, {"unexpected": "mapping"}])
def test_malformed_unrelated_refresh_reference_does_not_break_successful_publish(
    tmp_path, mutable_secret_backend, malformed_ref
) -> None:
    import officina.credentials.google as gc
    from officina.credentials.oauth import write_oauth_json

    registry_path = gc._credentials_registry_path(home=tmp_path, platform="linux")
    write_oauth_json(
        registry_path,
        {
            "schema_version": 2,
            "credentials": {
                "google:sub1": {
                    "subject": "sub1",
                    "account": "old@example.test",
                    "client_id": "abc",
                    "granted_scopes": ["openid"],
                    "refresh_secret_ref": "google-refresh:ffffffffffffffffffffffffffffffff",
                },
                "google:other": {
                    "subject": "other",
                    "account": "other@example.test",
                    "client_id": "abc",
                    "granted_scopes": ["openid"],
                    "refresh_secret_ref": malformed_ref,
                }
            },
        },
    )
    mutable_secret_backend.store(
        "connect-google",
        "google-refresh:ffffffffffffffffffffffffffffffff",
        "old-refresh",
    )

    ref = gc.store_google_credential(
        subject="sub1",
        account="user@example.test",
        client_id="abc",
        token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid"}),
        refresh_token="rt",
        home=tmp_path,
        platform="linux",
        secret_backend=mutable_secret_backend,
        refresh_ref_factory=lambda: "google-refresh:00000000000000000000000000000004",
    )

    assert ref.credential_id == "google:sub1"
    assert gc.load_credential(ref.credential_id, home=tmp_path, platform="linux") == ref
