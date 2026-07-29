from __future__ import annotations

import json

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


def test_store_and_load_credential_round_trip(tmp_path):
    from officina.common.google_credentials import load_credential, store_google_credential

    ref = store_google_credential(
        subject="sub1", account="user@example.com", client_id="abc", token_uri="https://oauth2.googleapis.com/token",
        granted_scopes=frozenset({"openid", "email", "https://www.googleapis.com/auth/drive"}),
        refresh_token="rt", home=tmp_path, platform="linux", secret_backend=FakeSecretBackend(),
    )

    loaded = load_credential("google:sub1", home=tmp_path, platform="linux")
    assert loaded == ref


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
