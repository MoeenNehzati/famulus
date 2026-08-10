from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_client_config.py"
SRC_ROOT = MODULE_PATH.parents[3] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SPEC = importlib.util.spec_from_file_location("connect_google_client_config", MODULE_PATH)
client_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(client_config)


def desktop_client(client_id: str = "cid") -> dict[str, object]:
    return {
        "installed": {
            "client_id": client_id,
            "project_id": "famulus-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "secret",
            "redirect_uris": ["http://localhost"],
        }
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def canonical(home: Path) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=sys.platform, home=home).config_root / "connect-google" / "client.json"


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


@pytest.fixture
def secret_backend() -> FakeSecretBackend:
    return FakeSecretBackend()


@pytest.fixture
def canonical_redacted_client(tmp_path: Path, secret_backend: FakeSecretBackend):
    home = tmp_path / "home"
    path = canonical(home)
    path.parent.mkdir(parents=True)
    payload = desktop_client()
    installed = payload["installed"]
    installed.pop("project_id")
    installed.pop("client_secret")
    installed["client_secret_ref"] = "custom-secret-ref"
    write_json(path, payload)
    secret_backend.store("connect-google", "custom-secret-ref", "secret")
    return home, path


def test_validate_accepts_desktop_client_and_token_uri() -> None:
    payload = desktop_client()
    assert client_config.validate_client_payload(payload) == payload


def _installed_without(field: str) -> dict[str, object]:
    return {key: value for key, value in desktop_client()["installed"].items() if key != field}


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"web": desktop_client()["installed"]}, "web"),
        ({"installed": []}, "installed"),
        ({"installed": {"client_id": "cid"}}, "auth_uri"),
        ({"installed": _installed_without("client_secret")}, "client_secret"),
        (
            {"installed": {**desktop_client()["installed"], "client_secret_ref": "oauth-client:cid:client-secret"}},
            "client_secret",
        ),
        (
            {"installed": {**desktop_client()["installed"], "redirect_uris": []}},
            "redirect_uris",
        ),
    ],
)
def test_validate_rejects_unsupported_or_incomplete_payload(
    payload: object, message: str
) -> None:
    with pytest.raises(client_config.ClientConfigError, match=message):
        client_config.validate_client_payload(payload)


@pytest.mark.parametrize("key", ["access_token", "refresh_token", "ACCESS_TOKEN"])
def test_validate_rejects_token_fields_recursively(key: str) -> None:
    payload = desktop_client()
    payload["installed"]["nested"] = [{key: "must-not-be-here"}]
    with pytest.raises(client_config.ClientConfigError, match="token credential"):
        client_config.validate_client_payload(payload)


def test_install_rejects_malformed_json_without_writing(tmp_path: Path) -> None:
    source = tmp_path / "download.json"
    source.write_text("{", encoding="utf-8")
    with pytest.raises(client_config.ClientConfigError, match="valid JSON"):
        client_config.install_client(source, tmp_path / "home", replace=False)
    assert not canonical(tmp_path / "home").exists()


def test_install_creates_private_canonical_copy_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "download.json"
    write_json(source, desktop_client())
    before = source.read_bytes()
    backend = FakeSecretBackend()

    result = client_config.install_client(source, tmp_path / "home", replace=False, secret_backend=backend)

    destination = canonical(tmp_path / "home")
    assert result == {
        "status": "installed",
        "client_type": "desktop",
        "path": str(destination),
    }
    installed = json.loads(destination.read_text(encoding="utf-8"))
    rendered = json.dumps(installed)
    assert "client_secret" not in installed["installed"]
    assert '"secret"' not in rendered
    assert installed["installed"]["client_secret_ref"] == "oauth-client:cid:client-secret"
    assert backend.stored == [("connect-google", "oauth-client:cid:client-secret", "secret")]
    assert source.read_bytes() == before
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_install_same_client_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_json(first, desktop_client())
    write_json(second, desktop_client())
    client_config.install_client(first, home, replace=False, secret_backend=FakeSecretBackend())

    result = client_config.install_client(second, home, replace=False, secret_backend=FakeSecretBackend())
    assert result["status"] == "unchanged"


def test_install_refuses_different_client_without_replace(tmp_path: Path) -> None:
    home = tmp_path / "home"
    old_source = tmp_path / "old.json"
    new_source = tmp_path / "new.json"
    write_json(old_source, desktop_client("old"))
    write_json(new_source, desktop_client("new"))
    client_config.install_client(old_source, home, replace=False, secret_backend=FakeSecretBackend())

    with pytest.raises(client_config.ClientConfigError, match="replace"):
        client_config.install_client(new_source, home, replace=False, secret_backend=FakeSecretBackend())
    assert json.loads(canonical(home).read_text(encoding="utf-8"))["installed"]["client_id"] == "old"


def test_install_replaces_different_client_when_explicit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    old_source = tmp_path / "old.json"
    new_source = tmp_path / "new.json"
    write_json(old_source, desktop_client("old"))
    write_json(new_source, desktop_client("new"))
    client_config.install_client(old_source, home, replace=False, secret_backend=FakeSecretBackend())

    result = client_config.install_client(new_source, home, replace=True, secret_backend=FakeSecretBackend())

    assert result["status"] == "replaced"
    assert json.loads(canonical(home).read_text(encoding="utf-8"))["installed"]["client_id"] == "new"


def test_client_status_reports_missing_valid_and_invalid_without_secrets(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = canonical(home)
    missing = client_config.client_status(home)
    assert missing == {"status": "missing", "client_type": "none", "path": str(path)}

    path.parent.mkdir(parents=True)
    payload = desktop_client()
    payload["installed"].pop("client_secret")
    payload["installed"]["client_secret_ref"] = "ref"
    write_json(path, payload)
    backend = FakeSecretBackend()
    backend.store("connect-google", "ref", "secret")
    valid = client_config.client_status(home, secret_backend=backend)
    assert valid == {"status": "valid", "client_type": "desktop", "path": str(path)}

    path.write_text("{", encoding="utf-8")
    invalid = client_config.client_status(home)
    rendered = json.dumps(invalid)
    assert invalid == {"status": "invalid", "client_type": "unknown", "path": str(path)}
    assert "client_secret" not in rendered
    assert "secret" not in rendered


def test_client_status_discovers_valid_legacy_service_clients_without_copying(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    drive = home / ".config" / "cloud-files" / "client.json"
    calendar = home / ".config" / "g-calendar" / "client.json"
    drive.parent.mkdir(parents=True)
    calendar.parent.mkdir(parents=True)
    write_json(drive, desktop_client("shared"))
    write_json(calendar, desktop_client("shared"))

    result = client_config.client_status(home)

    assert result["legacy_candidates"] == [
        {"service": "cloud-files", "path": str(drive)},
        {"service": "g-calendar", "path": str(calendar)},
    ]
    assert result["legacy_candidates_match"] is True
    assert not canonical(home).exists()
    assert "client_secret" not in json.dumps(result)


def test_client_status_reports_conflicting_legacy_clients_and_ignores_invalid(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    drive = home / ".config" / "cloud-files" / "client.json"
    calendar = home / ".config" / "g-calendar" / "client.json"
    drive.parent.mkdir(parents=True)
    calendar.parent.mkdir(parents=True)
    write_json(drive, desktop_client("drive"))
    write_json(calendar, desktop_client("calendar"))

    result = client_config.client_status(home)
    assert result["legacy_candidates_match"] is False

    calendar.write_text("{", encoding="utf-8")
    result = client_config.client_status(home)
    assert result["legacy_candidates"] == [
        {"service": "cloud-files", "path": str(drive)}
    ]
    assert "legacy_candidates_match" not in result


def test_load_authorization_client_accepts_redacted_client_without_project_id(
    canonical_redacted_client, secret_backend
) -> None:
    home, _ = canonical_redacted_client

    installed = client_config.load_authorization_client(
        home, secret_backend=secret_backend
    )

    assert installed == {"client_id": "cid", "client_secret_ref": "custom-secret-ref"}


def test_client_status_marks_plaintext_canonical_client_needs_migration(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = canonical(home)
    path.parent.mkdir(parents=True)
    write_json(path, desktop_client())

    result = client_config.client_status(home, secret_backend=FakeSecretBackend())

    assert result["status"] == "needs-migration"
    assert "--from-json" in result["remediation"]
    assert "--replace" in result["remediation"]


@pytest.mark.parametrize("secret_value", [None, ""])
def test_load_authorization_client_rejects_unresolved_secret_before_listener(
    canonical_redacted_client, secret_value
) -> None:
    home, _ = canonical_redacted_client
    backend = FakeSecretBackend()
    if secret_value:
        backend.store("connect-google", "custom-secret-ref", secret_value)

    with pytest.raises(client_config.ClientConfigError, match="secret"):
        client_config.load_authorization_client(home, secret_backend=backend)


def test_load_authorization_client_rejects_recursive_plaintext_client_secret(
    canonical_redacted_client, secret_backend
) -> None:
    home, path = canonical_redacted_client
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = {"client_secret": "plaintext-leak"}
    write_json(path, payload)

    with pytest.raises(client_config.ClientConfigError, match="plaintext"):
        client_config.load_authorization_client(home, secret_backend=secret_backend)


def test_downloaded_client_rejects_additional_nested_plaintext_client_secret() -> None:
    payload = desktop_client()
    payload["unexpected"] = {"client_secret": "plaintext-leak"}

    with pytest.raises(client_config.ClientConfigError, match="plaintext"):
        client_config.validate_client_payload(payload)


def test_plaintext_canonical_with_token_field_is_invalid_not_migration(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = canonical(home)
    path.parent.mkdir(parents=True)
    payload = desktop_client()
    payload["access_token"] = "must-not-be-accepted"
    write_json(path, payload)

    result = client_config.client_status(home, secret_backend=FakeSecretBackend())

    assert result["status"] == "invalid"
    assert "remediation" not in result


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_authorization_preflight_resolves_canonical_path_on_each_platform(
    tmp_path: Path, secret_backend: FakeSecretBackend, platform: str
) -> None:
    from officina.common.google_credentials import canonical_client_path

    path = canonical_client_path(home=tmp_path, platform=platform)
    path.parent.mkdir(parents=True)
    payload = desktop_client()
    payload["installed"].pop("client_secret")
    payload["installed"]["client_secret_ref"] = "portable-secret-ref"
    write_json(path, payload)
    secret_backend.store("connect-google", "portable-secret-ref", "secret")

    installed = client_config.load_authorization_client(
        tmp_path, platform=platform, secret_backend=secret_backend
    )

    assert installed == {
        "client_id": "cid",
        "client_secret_ref": "portable-secret-ref",
    }
