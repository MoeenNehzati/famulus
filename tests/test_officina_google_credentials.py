from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from officina.common.google_credentials import (
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
    from officina.common.google_credentials import canonical_client_path, install_client

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
    from officina.common.google_credentials import install_client

    backend = FakeSecretBackend()
    payload = _desktop_client_payload()
    install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=backend)

    second = install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=FakeSecretBackend())
    assert second["status"] == "unchanged"


def test_install_client_refuses_different_client_without_replace(tmp_path):
    from officina.common.google_credentials import install_client

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
    from officina.common.google_credentials import install_client

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
    from officina.common.google_credentials import install_client

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
    from officina.common.google_credentials import install_client

    payload = _desktop_client_payload()
    del payload["installed"]["client_secret"]

    with pytest.raises(GoogleCredentialError):
        install_client(payload, home=tmp_path, platform="linux", replace=False, secret_backend=FakeSecretBackend())


def test_refresh_access_token_checks_scopes_before_network_call(tmp_path):
    from officina.common.google_credentials import (
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

    from officina.common import google_credentials as gc_module

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
    from officina.common.google_credentials import load_credential, store_google_credential

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
    from officina.common import google_credentials

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
    from officina.common import google_credentials

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
    from officina.common import google_credentials

    registry_path = google_credentials._credentials_registry_path(
        home=tmp_path, platform="linux"
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_bytes(b"\xff")

    with pytest.raises(GoogleCredentialError, match="invalid credential registry"):
        google_credentials.load_credential(
            "legacy-id", home=tmp_path, platform="linux"
        )


def test_refresh_access_token_exchanges_refresh_token(tmp_path):
    from officina.common.google_credentials import refresh_access_token, store_google_credential

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

        def read(self):
            return json.dumps({"access_token": "new-access-token"}).encode()

    def fake_urlopen(request):
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
    from officina.common.google_credentials import refresh_access_token, store_google_credential

    backend = FakeSecretBackend()
    store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=backend,
    )
    backend.store("connect-google", "oauth-client:abc:client-secret", "client-secret-value")

    def fake_urlopen(request):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, None
        )

    with pytest.raises(GoogleCredentialError):
        refresh_access_token(
            "google:sub1", required_scopes={"https://www.googleapis.com/auth/drive"},
            home=tmp_path, platform="linux", urlopen=fake_urlopen, secret_backend=backend,
        )


def test_exchange_authorization_code_wraps_http_error_as_google_credential_error(tmp_path):
    from officina.common.google_credentials import exchange_authorization_code

    backend = FakeSecretBackend()
    backend.store("connect-google", "oauth-client:abc:client-secret", "client-secret-value")

    def fake_urlopen(request):
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
