from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[4] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

# cloud-files and g-calendar each have their own _rtx/_ensure_oauth.py.
# A bare `import ensure_oauth` after sys.path.insert would collide: whichever
# test module imports it first wins the sys.modules["ensure_oauth"] cache
# slot, silently reusing the wrong skill's file for the other's tests. Load
# by explicit file path under a unique module name instead.
_SPEC = importlib.util.spec_from_file_location(
    "cloud_files_ensure_oauth",
    Path(__file__).resolve().parents[1] / "_ensure_oauth.py",
)
ensure_oauth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ensure_oauth)


def test_already_configured_when_credentials_exist(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "cloud-files").mkdir(parents=True)
    (home / ".config" / "cloud-files" / "credentials.json").write_text("{}")

    status = ensure_oauth.run(home=home, dry_run=False, stdin_isatty=False)

    assert status == "already_configured"


def test_needs_client_json_when_missing_non_interactive(tmp_path, capsys):
    home = tmp_path / "home"
    (home / ".config" / "cloud-files").mkdir(parents=True)

    status = ensure_oauth.run(home=home, dry_run=False, stdin_isatty=False)

    assert status == "needs_client_json"
    assert "client.json" in capsys.readouterr().out


def test_write_config_writes_expected_json(tmp_path):
    home = tmp_path / "home"

    ensure_oauth.write_config(home, remote_llm_root="assistant/", dry_run=False)

    config_path = home / ".config" / "cloud-files" / "config.json"
    assert config_path.is_file()
    assert '"remote_llm_root": "assistant"' in config_path.read_text()


def test_write_config_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"

    ensure_oauth.write_config(home, remote_llm_root="assistant/", dry_run=True)

    assert not (home / ".config" / "cloud-files" / "config.json").exists()


def test_write_config_preserves_credentials_path(tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "cloud-files"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"remote_llm_root": "old", "timeout_seconds": 45, "credentials_path": "/custom/path.json"}'
    )

    ensure_oauth.write_config(home, remote_llm_root="assistant/", dry_run=False)

    payload = json.loads((config_dir / "config.json").read_text())
    assert payload["credentials_path"] == "/custom/path.json"
    assert payload["remote_llm_root"] == "assistant"


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


def _store_credential(home: Path, *, granted_drive_scope: bool) -> str:
    from officina.common.google_credentials import SERVICE_SCOPES, store_google_credential

    scopes = {"openid", "email"}
    if granted_drive_scope:
        scopes |= SERVICE_SCOPES["drive"]

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
def fake_registry_with_drive_scope(tmp_path):
    return _store_credential(tmp_path, granted_drive_scope=True)


@pytest.fixture
def fake_registry_missing_drive_scope(tmp_path):
    return _store_credential(tmp_path, granted_drive_scope=False)


def test_use_google_credential_stores_only_credential_id(tmp_path, fake_registry_with_drive_scope):
    credential_id = fake_registry_with_drive_scope

    ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)

    config_path = tmp_path / ".config" / "cloud-files" / "config.json"
    config = json.loads(config_path.read_text())
    assert config["credential_id"] == credential_id
    assert "client_secret" not in config
    assert "refresh_token" not in config


def test_use_google_credential_rejects_insufficient_scope(tmp_path, fake_registry_missing_drive_scope):
    credential_id = fake_registry_missing_drive_scope

    # Matches write_config's existing convention of converting its own
    # validation failure (normalize_llm_root's ValueError) into a clean
    # SystemExit for CLI use, rather than leaking a raw traceback.
    with pytest.raises(SystemExit):
        ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)

    config_path = tmp_path / ".config" / "cloud-files" / "config.json"
    assert not config_path.exists()


def test_use_google_credential_then_write_config_preserves_credential_id(tmp_path, fake_registry_with_drive_scope):
    # Regression test: write_config used to rebuild its payload from an
    # explicit allow-list (remote_llm_root, timeout_seconds, credentials_path),
    # silently dropping any field it didn't know about — including
    # credential_id written by use_google_credential. Both functions now
    # route through a shared merge-then-patch helper so this can't recur.
    credential_id = fake_registry_with_drive_scope

    ensure_oauth.use_google_credential(credential_id=credential_id, home=tmp_path, platform=PLATFORM)
    ensure_oauth.write_config(tmp_path, remote_llm_root="assistant/", dry_run=False)

    config_path = tmp_path / ".config" / "cloud-files" / "config.json"
    payload = json.loads(config_path.read_text())
    assert payload["credential_id"] == credential_id
    assert payload["remote_llm_root"] == "assistant"
