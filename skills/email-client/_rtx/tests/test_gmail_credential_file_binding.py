"""Gmail credential-file binding tests."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest


RTX_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = RTX_ROOT.parents[2] / "src"
for import_root in (SRC_ROOT, RTX_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, RTX_ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def accounts_module():
    return _load_module("email_client_credential_file_accounts", "_email_accounts.py")


@pytest.fixture(scope="module")
def oauth_tokens():
    return _load_module("email_client_credential_file_tokens", "_oauth_tokens.py")


@pytest.fixture
def account_registry(
    accounts_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config_dir = tmp_path / ".config" / "email-client"
    accounts_file = config_dir / "accounts.json"
    monkeypatch.setattr(accounts_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(accounts_module, "ACCOUNTS_FILE", accounts_file)
    record = {
        "email": "person@example.com",
        "display_name": "Person",
        "imap": {"host": "imap.gmail.com", "port": 993},
        "smtp": {"host": "smtp.gmail.com", "port": 465},
        "auth": "app-password",
        "future_field": "preserve-me",
    }
    config_dir.mkdir(parents=True)
    accounts_file.write_text(json.dumps({"work": record}), encoding="utf-8")
    return SimpleNamespace(
        home=tmp_path,
        path=accounts_file,
        read=lambda: json.loads(accounts_file.read_text(encoding="utf-8")),
    )


@pytest.fixture
def google_file_fakes(monkeypatch: pytest.MonkeyPatch):
    from officina.common import google_credentials

    descriptors: dict[Path, SimpleNamespace] = {}
    refresh_calls: list[Path] = []

    def add(
        path: Path,
        *,
        account: str = "person@example.com",
        subject: str = "google-subject",
        scoped: bool = True,
    ):
        resolved = path.resolve()
        descriptors[resolved] = SimpleNamespace(
            path=resolved,
            account=account,
            subject=subject,
            granted_scopes=(
                google_credentials.SERVICE_SCOPES["gmail"]
                if scoped
                else frozenset({"openid", "email"})
            ),
        )
        return resolved

    def load(path: Path):
        resolved = Path(path).resolve()
        if resolved not in descriptors:
            raise google_credentials.GoogleCredentialError(f"missing descriptor {resolved}")
        return descriptors[resolved]

    def refresh(path: Path, *, required_scopes, **_kwargs):
        ref = load(path)
        if not required_scopes <= ref.granted_scopes:
            raise google_credentials.GoogleCredentialError("lacks required scopes")
        refresh_calls.append(ref.path)
        return "gmail-access-token"

    monkeypatch.setattr(google_credentials, "load_credential_file", load)
    monkeypatch.setattr(google_credentials, "refresh_access_token_from_file", refresh)
    return SimpleNamespace(add=add, refresh_calls=refresh_calls)


@pytest.fixture
def successful_probe() -> tuple[Callable, list[object]]:
    requests: list[object] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b'{"emailAddress": "person@example.com"}'

    def urlopen(request, **_kwargs):
        requests.append(request)
        return Response()

    return urlopen, requests


def test_bind_named_account_probes_then_preserves_other_fields(
    accounts_module,
    account_registry,
    google_file_fakes,
    successful_probe,
) -> None:
    descriptor = google_file_fakes.add(
        account_registry.home / "credentials" / "gmail.json"
    )
    urlopen, requests = successful_probe

    result = accounts_module.accounts_use_google_credential_file(
        nickname="work",
        credential_file=descriptor,
        home=account_registry.home,
        urlopen=urlopen,
    )

    assert result == {
        "service": "gmail",
        "credential_file": str(descriptor),
        "account": "person@example.com",
        "bound": True,
        "verified": True,
    }
    record = account_registry.read()["work"]
    assert record["credential_file"] == str(descriptor)
    assert record["auth"] == "gmail-oauth"
    assert record["future_field"] == "preserve-me"
    assert google_file_fakes.refresh_calls == [descriptor]
    assert requests[0].full_url.endswith("/gmail/v1/users/me/profile")
    assert requests[0].headers["Authorization"] == "Bearer gmail-access-token"


def test_descriptor_account_must_match_configured_email_even_with_approval(
    accounts_module,
    account_registry,
    google_file_fakes,
    successful_probe,
) -> None:
    descriptor = google_file_fakes.add(
        account_registry.home / "credentials" / "other.json",
        account="other@example.com",
    )
    before = account_registry.path.read_bytes()
    urlopen, _requests = successful_probe

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=descriptor,
            home=account_registry.home,
            allow_account_change=True,
            urlopen=urlopen,
        )

    assert exc_info.value.code == "account-email-mismatch"
    assert account_registry.path.read_bytes() == before


def test_scope_or_probe_failure_never_writes(
    accounts_module,
    account_registry,
    google_file_fakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = google_file_fakes.add(
        account_registry.home / "credentials" / "wrong-scope.json", scoped=False
    )
    before = account_registry.path.read_bytes()

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=descriptor,
            home=account_registry.home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )
    assert exc_info.value.code == "insufficient-scope"
    assert account_registry.path.read_bytes() == before

    scoped = google_file_fakes.add(
        account_registry.home / "credentials" / "probe-fails.json"
    )

    def failed_probe(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=scoped,
            home=account_registry.home,
            urlopen=failed_probe,
        )
    assert exc_info.value.code == "live-check-failed"
    assert account_registry.path.read_bytes() == before

    monkeypatch.setattr(
        "officina.common.google_credentials.refresh_access_token_from_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret missing")),
    )
    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=scoped,
            home=account_registry.home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )
    assert exc_info.value.code == "live-check-failed"
    assert account_registry.path.read_bytes() == before


@pytest.mark.parametrize(
    "contents",
    ["{", "[]", '{"work": []}'],
    ids=["malformed-json", "non-object-registry", "non-object-account"],
)
def test_invalid_registry_returns_structured_api_and_cli_error(
    accounts_module,
    account_registry,
    capsys: pytest.CaptureFixture[str],
    contents: str,
) -> None:
    account_registry.path.write_text(contents, encoding="utf-8")
    before = account_registry.path.read_bytes()
    descriptor = account_registry.home / "credentials" / "unused.json"

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=descriptor,
            home=account_registry.home,
        )
    assert exc_info.value.code == "invalid-service-config"
    assert account_registry.path.read_bytes() == before

    exit_code = accounts_module.main(
        [
            "use-google-credential-file",
            "--nickname",
            "work",
            "--credential-file",
            str(descriptor),
            "--home",
            str(account_registry.home),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["error"]["code"] == "invalid-service-config"
    assert account_registry.path.read_bytes() == before


def test_unreadable_registry_returns_structured_error(
    accounts_module,
    account_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = account_registry.path.read_bytes()
    monkeypatch.setattr(
        accounts_module,
        "load",
        lambda: (_ for _ in ()).throw(PermissionError("registry unreadable")),
    )

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=account_registry.home / "credentials" / "unused.json",
            home=account_registry.home,
        )
    assert exc_info.value.code == "invalid-service-config"
    assert account_registry.path.read_bytes() == before


def test_different_subject_same_email_prior_binding_requires_approval(
    accounts_module,
    account_registry,
    google_file_fakes,
    successful_probe,
) -> None:
    data = account_registry.read()
    old_file = google_file_fakes.add(
        account_registry.home / "credentials" / "old.json",
        subject="old-google-subject",
    )
    new_file = google_file_fakes.add(
        account_registry.home / "credentials" / "new.json",
        subject="new-google-subject",
    )
    data["work"]["credential_file"] = str(old_file)
    account_registry.path.write_text(json.dumps(data), encoding="utf-8")
    urlopen, _requests = successful_probe

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=new_file,
            home=account_registry.home,
            urlopen=urlopen,
        )
    assert exc_info.value.code == "account-change-confirmation-required"

    result = accounts_module.accounts_use_google_credential_file(
        nickname="work",
        credential_file=new_file,
        home=account_registry.home,
        allow_account_change=True,
        urlopen=urlopen,
    )
    assert result["verified"] is True
    assert account_registry.read()["work"]["credential_file"] == str(new_file)


def test_same_subject_changed_email_rebind_needs_no_approval(
    accounts_module,
    account_registry,
    google_file_fakes,
    successful_probe,
) -> None:
    old_file = google_file_fakes.add(
        account_registry.home / "credentials" / "old.json",
        account="old@example.com",
    )
    new_file = google_file_fakes.add(
        account_registry.home / "credentials" / "new.json"
    )
    data = account_registry.read()
    data["work"]["credential_file"] = str(old_file)
    account_registry.path.write_text(json.dumps(data), encoding="utf-8")
    urlopen, _requests = successful_probe

    result = accounts_module.accounts_use_google_credential_file(
        nickname="work",
        credential_file=new_file,
        home=account_registry.home,
        urlopen=urlopen,
    )

    assert result["account"] == "person@example.com"
    assert account_registry.read()["work"]["credential_file"] == str(new_file)


def test_unprovable_legacy_identity_requires_approval_then_can_be_replaced(
    accounts_module,
    account_registry,
    google_file_fakes,
    successful_probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from officina.common import google_credentials

    descriptor = google_file_fakes.add(
        account_registry.home / "credentials" / "new.json"
    )
    data = account_registry.read()
    data["work"]["credential_id"] = "legacy-id"
    account_registry.path.write_text(json.dumps(data), encoding="utf-8")
    before = account_registry.path.read_bytes()
    monkeypatch.setattr(
        google_credentials,
        "load_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            google_credentials.GoogleCredentialError("invalid registry")
        ),
    )
    urlopen, _requests = successful_probe

    with pytest.raises(accounts_module.CredentialFileBindingError) as exc_info:
        accounts_module.accounts_use_google_credential_file(
            nickname="work",
            credential_file=descriptor,
            home=account_registry.home,
            urlopen=urlopen,
        )
    assert exc_info.value.code == "account-change-confirmation-required"
    assert account_registry.path.read_bytes() == before

    result = accounts_module.accounts_use_google_credential_file(
        nickname="work",
        credential_file=descriptor,
        home=account_registry.home,
        allow_account_change=True,
        urlopen=urlopen,
    )
    assert result["verified"] is True
    assert account_registry.read()["work"]["credential_file"] == str(descriptor)


@pytest.mark.parametrize("value", [None, "", "   ", 42, {}])
def test_present_invalid_file_binding_never_falls_back(
    oauth_tokens,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value,
) -> None:
    account = {
        "email": "person@example.com",
        "auth": "gmail-oauth",
        "credential_file": value,
        "credential_id": "legacy-id",
    }
    monkeypatch.setattr(
        oauth_tokens,
        "refresh_google_access_token",
        lambda *_args, **_kwargs: pytest.fail("legacy OAuth fallback must not run"),
    )
    monkeypatch.setattr(
        "officina.common.google_credentials.refresh_access_token",
        lambda *_args, **_kwargs: pytest.fail("credential ID fallback must not run"),
    )

    with pytest.raises(oauth_tokens.OAuthError, match="credential_file"):
        oauth_tokens.get_gmail_access_token("work", account, home=tmp_path)


def test_runtime_prefers_file_over_id_and_legacy(
    oauth_tokens,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from officina.common import google_credentials

    descriptor = (tmp_path / "gmail.json").resolve()
    account = {
        "email": "person@example.com",
        "auth": "gmail-oauth",
        "credential_file": str(descriptor),
        "credential_id": "legacy-id",
    }
    calls: list[Path] = []
    monkeypatch.setattr(
        google_credentials,
        "refresh_access_token_from_file",
        lambda path, **_kwargs: calls.append(Path(path)) or "fresh-token",
    )
    monkeypatch.setattr(
        google_credentials,
        "refresh_access_token",
        lambda *_args, **_kwargs: pytest.fail("credential ID fallback must not run"),
    )
    monkeypatch.setattr(
        oauth_tokens,
        "refresh_google_access_token",
        lambda *_args, **_kwargs: pytest.fail("legacy OAuth fallback must not run"),
    )

    assert (
        oauth_tokens.get_gmail_access_token("work", account, home=tmp_path)
        == "fresh-token"
    )
    assert calls == [descriptor]
