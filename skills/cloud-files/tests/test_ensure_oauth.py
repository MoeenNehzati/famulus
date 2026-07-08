from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ensure_oauth


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

    import json
    payload = json.loads((config_dir / "config.json").read_text())
    assert payload["credentials_path"] == "/custom/path.json"
    assert payload["remote_llm_root"] == "assistant"
