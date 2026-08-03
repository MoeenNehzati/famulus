import json
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
SKILL_ROOT = Path(__file__).parent.parent
for path in (str(REPO_SRC), str(SKILL_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

if __package__ and __package__.count('.') >= 1:
    from .. import _oauth_tokens as oauth
else:
    from _rtx import _oauth_tokens as oauth  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_load_google_client_config_accepts_installed_json(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({
        "installed": {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "auth_uri": "https://auth.example.test",
            "token_uri": "https://token.example.test",
        }
    }))

    assert oauth.load_google_client_config(path) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "auth_uri": "https://auth.example.test",
        "token_uri": "https://token.example.test",
    }


def test_configure_gmail_oauth_stores_refresh_token_and_client_secret(monkeypatch):
    calls = []
    account = {}

    def store(namespace, key, secret):
        calls.append((namespace, key, secret))

    monkeypatch.setattr(oauth.secret_store, "store", store)

    oauth.configure_gmail_oauth(
        "work",
        account,
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "auth_uri": "https://auth.example.test",
            "token_uri": "https://token.example.test",
        },
        "refresh-token",
    )

    assert account["auth"] == "gmail-oauth"
    assert account["oauth"]["client_id"] == "client-id"
    assert calls == [
        ("email-client", "work:oauth:client-secret", "client-secret"),
        ("email-client", "work:oauth:refresh-token", "refresh-token"),
    ]


def test_refresh_google_access_token_posts_refresh_request(monkeypatch):
    required = {
        "work:oauth:client-secret": "client-secret",
        "work:oauth:refresh-token": "refresh-token",
    }
    captured = {}

    def require(namespace, key):
        assert namespace == "email-client"
        return required[key]

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse({"access_token": "access-token", "expires_in": 3600})

    monkeypatch.setattr(oauth.secret_store, "require", require)
    account = {"auth": "gmail-oauth", "oauth": {"client_id": "client-id", "token_uri": "https://token.example.test"}}

    assert oauth.refresh_google_access_token("work", account, urlopen=fake_urlopen) == "access-token"
    assert captured["url"] == "https://token.example.test"
    assert captured["timeout"] == 30
    body = dict(item.split("=", 1) for item in captured["body"].split("&"))
    assert body["client_id"] == "client-id"
    assert body["client_secret"] == "client-secret"
    assert body["refresh_token"] == "refresh-token"
    assert body["grant_type"] == "refresh_token"


def test_xoauth2_string_matches_gmail_sasl_shape():
    assert oauth.xoauth2_string("me@example.com", "token") == "user=me@example.com\x01auth=Bearer token\x01\x01"
    assert oauth.xoauth2_bytes("me@example.com", "token") == b"user=me@example.com\x01auth=Bearer token\x01\x01"


# ── get_gmail_access_token: shared credential vs. legacy per-account OAuth ──

def test_get_gmail_access_token_uses_shared_credential_when_credential_id_present(monkeypatch, tmp_path):
    import officina.common.google_credentials as google_credentials

    calls = {}

    def fake_refresh_access_token(credential_id, *, required_scopes, home, platform, urlopen=None, secret_backend=None):
        calls["credential_id"] = credential_id
        calls["required_scopes"] = required_scopes
        calls["home"] = home
        calls["platform"] = platform
        return "shared-access-token"

    monkeypatch.setattr(google_credentials, "refresh_access_token", fake_refresh_access_token)
    # Legacy path must not even be consulted when credential_id is present.
    monkeypatch.setattr(
        oauth, "refresh_google_access_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy OAuth path must not run when credential_id is set")),
    )

    account = {"auth": "gmail-oauth", "credential_id": "google:sub1", "email": "me@example.com"}
    token = oauth.get_gmail_access_token("work", account, home=tmp_path, platform="linux")

    assert token == "shared-access-token"
    assert calls["credential_id"] == "google:sub1"
    assert calls["required_scopes"] == google_credentials.SERVICE_SCOPES["gmail"]
    assert calls["home"] == tmp_path
    assert calls["platform"] == "linux"


def test_get_gmail_access_token_falls_back_to_legacy_oauth_without_credential_id(monkeypatch):
    calls = []

    def fake_legacy(nickname, account, *, urlopen=None):
        calls.append((nickname, account))
        return "legacy-access-token"

    monkeypatch.setattr(oauth, "refresh_google_access_token", fake_legacy)

    account = {
        "auth": "gmail-oauth",
        "oauth": {"client_id": "client-id", "token_uri": "https://token.example.test"},
        "email": "me@example.com",
    }
    token = oauth.get_gmail_access_token("work", account)

    assert token == "legacy-access-token"
    assert calls == [("work", account)]


def test_get_gmail_access_token_wraps_google_credential_error_as_oautherror(monkeypatch, tmp_path):
    import officina.common.google_credentials as google_credentials

    def fake_refresh_access_token(credential_id, *, required_scopes, home, platform, urlopen=None, secret_backend=None):
        raise google_credentials.GoogleCredentialError(f"credential {credential_id} lacks required scopes")

    monkeypatch.setattr(google_credentials, "refresh_access_token", fake_refresh_access_token)

    account = {"auth": "gmail-oauth", "credential_id": "google:sub1", "email": "me@example.com"}
    with pytest.raises(oauth.OAuthError, match="lacks required scopes"):
        oauth.get_gmail_access_token("work", account, home=tmp_path, platform="linux")


def test_exchange_authorization_code_requires_access_token_response():
    def fake_urlopen(request, timeout):
        return FakeResponse({"error": "invalid_grant", "error_description": "bad code"})

    with pytest.raises(oauth.OAuthError, match="bad code"):
        oauth.exchange_authorization_code(
            {"client_id": "client-id", "client_secret": "client-secret", "token_uri": "https://token.example.test"},
            code="code",
            redirect_uri="http://127.0.0.1/callback",
            code_verifier="verifier",
            urlopen=fake_urlopen,
        )
