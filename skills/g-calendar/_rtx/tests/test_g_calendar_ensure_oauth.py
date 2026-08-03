from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

# g-calendar and cloud-files each have their own _rtx/_ensure_oauth.py.
# A bare `import ensure_oauth` after sys.path.insert would collide: whichever
# test module imports it first wins the sys.modules["ensure_oauth"] cache
# slot, silently reusing the wrong skill's file for the other's tests. Load
# by explicit file path under a unique module name instead.
_SPEC = importlib.util.spec_from_file_location(
    "g_calendar_ensure_oauth",
    Path(__file__).resolve().parents[1] / "_ensure_oauth.py",
)
ensure_oauth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ensure_oauth)


def test_already_configured_when_credentials_exist(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "g-calendar").mkdir(parents=True)
    (home / ".config" / "g-calendar" / "credentials.json").write_text("{}")

    status = ensure_oauth.run(home=home, dry_run=False, stdin_isatty=False)

    assert status == "already_configured"


def test_needs_client_json_when_missing_non_interactive(tmp_path, capsys):
    home = tmp_path / "home"
    (home / ".config" / "g-calendar").mkdir(parents=True)

    status = ensure_oauth.run(home=home, dry_run=False, stdin_isatty=False)

    assert status == "needs_client_json"
    assert "client.json" in capsys.readouterr().out


class FakeSecretBackend:
    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}

    def store(self, namespace: str, key: str, value: str) -> None:
        self.stored[(namespace, key)] = value

    def lookup(self, namespace: str, key: str) -> str | None:
        return self.stored.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        return self.stored.pop((namespace, key), None) is not None


PLATFORM = "linux"


def _store_credential(home: Path, *, granted_calendar_scope: bool) -> str:
    from officina.common.google_credentials import SERVICE_SCOPES, store_google_credential

    scopes = {"openid", "email"}
    if granted_calendar_scope:
        scopes |= SERVICE_SCOPES["calendar"]

    ref = store_google_credential(
        subject="sub1",
        account="user@example.com",
        client_id="test-client-id",
        token_uri="https://oauth2.example.test/token",
        granted_scopes=frozenset(scopes),
        refresh_token="refresh-token-value",
        home=home,
        platform=PLATFORM,
        secret_backend=FakeSecretBackend(),
    )
    return ref.credential_id


@pytest.fixture
def fake_registry_with_calendar_scope(tmp_path):
    return _store_credential(tmp_path, granted_calendar_scope=True)


@pytest.fixture
def fake_registry_missing_calendar_scope(tmp_path):
    return _store_credential(tmp_path, granted_calendar_scope=False)


def test_use_google_credential_stores_only_credential_id(tmp_path, fake_registry_with_calendar_scope):
    credential_id = fake_registry_with_calendar_scope

    ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)

    config_path = tmp_path / ".config" / "g-calendar" / "config.json"
    config = json.loads(config_path.read_text())
    assert config["credential_id"] == credential_id
    assert "client_secret" not in config
    assert "refresh_token" not in config


def test_use_google_credential_rejects_insufficient_scope(tmp_path, fake_registry_missing_calendar_scope):
    credential_id = fake_registry_missing_calendar_scope

    # Matches this module's existing convention (see run()/client_setup_lines
    # and cloud-files' sibling implementation) of converting validation
    # failures into a clean SystemExit for CLI use, rather than leaking a raw
    # traceback.
    with pytest.raises(SystemExit):
        ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)

    config_path = tmp_path / ".config" / "g-calendar" / "config.json"
    assert not config_path.exists()


def test_use_google_credential_preserves_unrelated_config_fields(tmp_path, fake_registry_with_calendar_scope):
    # Regression test for the bug class cloud-files' review caught: two
    # config-writing functions using inconsistent merge strategies (one
    # rebuilding its payload from an explicit allow-list) can silently drop
    # fields the other wrote. g-calendar only has one config field today
    # (credential_id), so simulate a second/future writer having already put
    # an unrelated field in config.json and confirm use_google_credential's
    # merge-based _merge_and_write_config preserves it rather than replacing
    # the whole payload.
    credential_id = fake_registry_with_calendar_scope
    config_dir = tmp_path / ".config" / "g-calendar"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"some_future_field": "keep-me"}))

    ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)

    payload = json.loads((config_dir / "config.json").read_text())
    assert payload["credential_id"] == credential_id
    assert payload["some_future_field"] == "keep-me"
