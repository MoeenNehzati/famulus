from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_client_config.py"
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
    return home / ".config" / "connect-google" / "client.json"


def test_validate_accepts_desktop_client_and_token_uri() -> None:
    payload = desktop_client()
    assert client_config.validate_client_payload(payload) == payload


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"web": desktop_client()["installed"]}, "web"),
        ({"installed": []}, "installed"),
        ({"installed": {"client_id": "cid"}}, "client_secret"),
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

    result = client_config.install_client(source, tmp_path / "home", replace=False)

    destination = canonical(tmp_path / "home")
    assert result == {
        "status": "installed",
        "client_type": "desktop",
        "path": str(destination),
    }
    assert json.loads(destination.read_text(encoding="utf-8")) == desktop_client()
    assert source.read_bytes() == before
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_install_same_client_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_json(first, desktop_client())
    write_json(second, desktop_client())
    client_config.install_client(first, home, replace=False)

    assert client_config.install_client(second, home, replace=False)["status"] == "unchanged"


def test_install_refuses_different_client_without_replace(tmp_path: Path) -> None:
    home = tmp_path / "home"
    old_source = tmp_path / "old.json"
    new_source = tmp_path / "new.json"
    write_json(old_source, desktop_client("old"))
    write_json(new_source, desktop_client("new"))
    client_config.install_client(old_source, home, replace=False)

    with pytest.raises(client_config.ClientConfigError, match="--replace"):
        client_config.install_client(new_source, home, replace=False)
    assert json.loads(canonical(home).read_text(encoding="utf-8"))["installed"]["client_id"] == "old"


def test_install_replaces_different_client_when_explicit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    old_source = tmp_path / "old.json"
    new_source = tmp_path / "new.json"
    write_json(old_source, desktop_client("old"))
    write_json(new_source, desktop_client("new"))
    client_config.install_client(old_source, home, replace=False)

    result = client_config.install_client(new_source, home, replace=True)

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
    write_json(path, desktop_client())
    valid = client_config.client_status(home)
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
