from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ensure_oauth


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
