from __future__ import annotations

import importlib.util
from pathlib import Path

# g-calendar and cloud-files each have their own _rtx/_ensure_oauth.py.
# A bare `import ensure_oauth` after sys.path.insert would collide: whichever
# test module imports it first wins the sys.modules["ensure_oauth"] cache
# slot, silently reusing the wrong skill's file for the other's tests. Load
# by explicit file path under a unique module name instead.
_SPEC = importlib.util.spec_from_file_location(
    "g_calendar_ensure_oauth",
    Path(__file__).resolve().parents[1] / "_rtx" / "_ensure_oauth.py",
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
