"""Calendar credential-file binding tests."""

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
def ensure_oauth():
    return _load_module("g_calendar_credential_file_ensure", "_ensure_oauth.py")


@pytest.fixture(scope="module")
def gcal_client():
    return _load_module("g_calendar_credential_file_client", "_gcal_client.py")


@pytest.fixture
def google_file_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
                google_credentials.SERVICE_SCOPES["calendar"]
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
        return "calendar-access-token"

    monkeypatch.setattr(google_credentials, "load_credential_file", load)
    monkeypatch.setattr(google_credentials, "refresh_access_token_from_file", refresh)
    return SimpleNamespace(add=add, refresh_calls=refresh_calls, home=tmp_path)


@pytest.fixture
def successful_probe() -> tuple[Callable, list[object]]:
    requests: list[object] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b'{"items": []}'

    def urlopen(request, **_kwargs):
        requests.append(request)
        return Response()

    return urlopen, requests


def test_bind_normalizes_probes_then_preserves_config(
    ensure_oauth,
    google_file_fakes,
    successful_probe,
) -> None:
    home = google_file_fakes.home
    config_dir = home / ".config" / "g-calendar"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps({"calendar": "work"}), encoding="utf-8")
    descriptor = google_file_fakes.add(home / "credentials" / "calendar.json")
    urlopen, requests = successful_probe

    result = ensure_oauth.use_google_credential_file(
        credential_file=descriptor,
        home=home,
        urlopen=urlopen,
    )

    assert result == {
        "service": "calendar",
        "credential_file": str(descriptor),
        "account": "person@example.com",
        "bound": True,
        "verified": True,
    }
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "calendar": "work",
        "credential_file": str(descriptor),
    }
    assert google_file_fakes.refresh_calls == [descriptor]
    assert requests[0].full_url.endswith("/users/me/calendarList?maxResults=1")
    assert requests[0].headers["Authorization"] == "Bearer calendar-access-token"


def test_scope_or_probe_failure_never_writes(
    ensure_oauth,
    google_file_fakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = google_file_fakes.home
    descriptor = google_file_fakes.add(
        home / "credentials" / "wrong-scope.json", scoped=False
    )
    config_path = home / ".config" / "g-calendar" / "config.json"

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=descriptor,
            home=home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )

    assert exc_info.value.code == "insufficient-scope"
    assert not config_path.exists()

    scoped = google_file_fakes.add(home / "credentials" / "probe-fails.json")

    def failed_probe(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=scoped,
            home=home,
            urlopen=failed_probe,
        )

    assert exc_info.value.code == "live-check-failed"
    assert not config_path.exists()

    monkeypatch.setattr(
        "officina.common.google_credentials.refresh_access_token_from_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("secret missing")),
    )
    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=scoped,
            home=home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )
    assert exc_info.value.code == "live-check-failed"
    assert not config_path.exists()


def test_different_subject_same_email_rebind_requires_explicit_approval(
    ensure_oauth,
    google_file_fakes,
    successful_probe,
) -> None:
    home = google_file_fakes.home
    old_file = google_file_fakes.add(
        home / "credentials" / "old.json", subject="old-google-subject"
    )
    new_file = google_file_fakes.add(
        home / "credentials" / "new.json", subject="new-google-subject"
    )
    config_dir = home / ".config" / "g-calendar"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps({"credential_file": str(old_file)}), encoding="utf-8"
    )
    urlopen, _requests = successful_probe

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=new_file,
            home=home,
            urlopen=urlopen,
        )
    assert exc_info.value.code == "account-change-confirmation-required"
    assert json.loads(config_path.read_text())["credential_file"] == str(old_file)

    result = ensure_oauth.use_google_credential_file(
        credential_file=new_file,
        home=home,
        allow_account_change=True,
        urlopen=urlopen,
    )
    assert result["account"] == "person@example.com"
    assert json.loads(config_path.read_text())["credential_file"] == str(new_file)


def test_same_subject_changed_email_rebind_needs_no_approval(
    ensure_oauth,
    google_file_fakes,
    successful_probe,
) -> None:
    home = google_file_fakes.home
    old_file = google_file_fakes.add(
        home / "credentials" / "old.json", account="old@example.com"
    )
    new_file = google_file_fakes.add(
        home / "credentials" / "new.json", account="new@example.com"
    )
    config_path = home / ".config" / "g-calendar" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"credential_file": str(old_file)}), encoding="utf-8"
    )
    urlopen, _requests = successful_probe

    result = ensure_oauth.use_google_credential_file(
        credential_file=new_file,
        home=home,
        urlopen=urlopen,
    )

    assert result["account"] == "new@example.com"
    assert json.loads(config_path.read_text())["credential_file"] == str(new_file)


def test_unprovable_legacy_identity_requires_approval_then_can_be_replaced(
    ensure_oauth,
    google_file_fakes,
    successful_probe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from officina.common import google_credentials

    home = google_file_fakes.home
    descriptor = google_file_fakes.add(home / "credentials" / "new.json")
    config_path = home / ".config" / "g-calendar" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"credential_id": "legacy-id"}), encoding="utf-8")
    before = config_path.read_bytes()
    monkeypatch.setattr(
        google_credentials,
        "load_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            google_credentials.GoogleCredentialError("invalid registry")
        ),
    )
    urlopen, _requests = successful_probe

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=descriptor, home=home, urlopen=urlopen
        )
    assert exc_info.value.code == "account-change-confirmation-required"
    assert config_path.read_bytes() == before

    result = ensure_oauth.use_google_credential_file(
        credential_file=descriptor,
        home=home,
        allow_account_change=True,
        urlopen=urlopen,
    )
    assert result["verified"] is True
    assert json.loads(config_path.read_text())["credential_file"] == str(descriptor)


def test_cli_failure_is_stable_json(
    ensure_oauth,
    google_file_fakes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = (google_file_fakes.home / "credentials" / "missing.json").resolve()

    exit_code = ensure_oauth.main(
        [
            "use-google-credential-file",
            "--credential-file",
            str(missing),
            "--home",
            str(google_file_fakes.home),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output == {
        "service": "calendar",
        "credential_file": str(missing),
        "bound": False,
        "verified": False,
        "error": {
            "code": "invalid-credential-file",
            "message": f"missing descriptor {missing}",
        },
    }


@pytest.mark.parametrize("value", [None, "", "   ", 42, {}])
def test_present_invalid_file_binding_never_falls_back(
    gcal_client,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value,
) -> None:
    config_dir = tmp_path / ".config" / "g-calendar"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"credential_file": value, "credential_id": "legacy-id"}),
        encoding="utf-8",
    )
    (config_dir / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "officina.common.google_credentials.refresh_access_token",
        lambda *_args, **_kwargs: pytest.fail("credential ID fallback must not run"),
    )

    with pytest.raises(Exception, match="credential_file"):
        gcal_client.get_access_token(home=tmp_path)


@pytest.mark.parametrize(
    "contents",
    [b"{bad", b"\xff"],
    ids=["malformed-json", "invalid-utf8"],
)
def test_malformed_service_config_is_terminal(
    ensure_oauth,
    gcal_client,
    google_file_fakes,
    capsys: pytest.CaptureFixture[str],
    contents: bytes,
) -> None:
    home = google_file_fakes.home
    descriptor = google_file_fakes.add(home / "credentials" / "calendar.json")
    config_dir = home / ".config" / "g-calendar"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_bytes(contents)
    before = config_path.read_bytes()

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=descriptor,
            home=home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )
    assert exc_info.value.code == "invalid-service-config"
    assert config_path.read_bytes() == before

    exit_code = ensure_oauth.main(
        [
            "use-google-credential-file",
            "--credential-file",
            str(descriptor),
            "--home",
            str(home),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["error"]["code"] == "invalid-service-config"
    assert config_path.read_bytes() == before

    with pytest.raises(Exception, match="config.json"):
        gcal_client.get_access_token(home=home)


@pytest.mark.parametrize("kind", ["directory", "dangling-symlink"])
def test_present_non_file_service_config_is_terminal(
    ensure_oauth,
    gcal_client,
    google_file_fakes,
    kind: str,
) -> None:
    home = google_file_fakes.home
    descriptor = google_file_fakes.add(home / "credentials" / "calendar.json")
    config_path = home / ".config" / "g-calendar" / "config.json"
    config_path.parent.mkdir(parents=True)
    if kind == "directory":
        config_path.mkdir()
    else:
        config_path.symlink_to(home / "missing-config-target.json")

    with pytest.raises(ensure_oauth.CredentialFileBindingError) as exc_info:
        ensure_oauth.use_google_credential_file(
            credential_file=descriptor,
            home=home,
            urlopen=lambda *_args, **_kwargs: pytest.fail("probe must not run"),
        )
    assert exc_info.value.code == "invalid-service-config"

    with pytest.raises(RuntimeError, match="regular file"):
        gcal_client.get_access_token(home=home)
