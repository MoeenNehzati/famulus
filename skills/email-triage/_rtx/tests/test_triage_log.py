from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent.parent
REPO_SRC = Path(__file__).resolve().parents[4] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load(script_name: str):
    spec = importlib.util.spec_from_file_location(script_name, SCRIPTS_DIR / script_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_xdg(monkeypatch) -> None:
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)


def test_decision_log_uses_canonical_state_and_ignores_process_override(
    monkeypatch, tmp_path: Path
) -> None:
    from officina.common.famulus_paths import resolve_famulus_paths

    sink = _load("_decision_sink.py")
    _without_xdg(monkeypatch)
    monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(tmp_path / "hostile"))
    expected = resolve_famulus_paths(
        platform=sys.platform, home=tmp_path, environ=os.environ
    ).email_triage_state_root / "triage.log"

    assert sink.triage_log_path(home=tmp_path) == expected


def test_unmanaged_triage_log_api_honors_the_explicit_process_override(
    monkeypatch, tmp_path: Path
) -> None:
    override = tmp_path / "unmanaged-state"
    monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(override))
    sink = _load("_decision_sink.py")
    compactor = _load("_log_compactor.py")

    assert sink.unmanaged_triage_log_path(home=tmp_path) == override / "triage.log"
    assert compactor.unmanaged_triage_log_path(home=tmp_path) == override / "triage.log"


def test_decision_sink_entrypoint_writes_to_explicit_unmanaged_override(
    monkeypatch, tmp_path: Path
) -> None:
    override = tmp_path / "override"
    monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(override))
    sink = _load("_decision_sink.py")
    legacy = tmp_path / "skill" / "triage.log"
    legacy.parent.mkdir()
    legacy.write_text("real legacy\n", encoding="utf-8")
    monkeypatch.setattr(sink, "LEGACY_LOG_FILE", legacy)

    assert sink.Interface().run(
        argparse.Namespace(
            account="work",
            message_id="id-override",
            sender="sender@example.com",
            subject="subject",
            decision="archive",
            reason="done",
        )
    ) == 0

    result = (override / "triage.log").read_text(encoding="utf-8")
    assert "id-override" in result
    assert "real legacy" not in result


def test_log_compactor_subprocess_prunes_explicit_unmanaged_override(
    tmp_path: Path,
) -> None:
    override = tmp_path / "override"
    override.mkdir()
    old = datetime.now(timezone.utc) - timedelta(days=31)
    (override / "triage.log").write_text(
        f"[{old.isoformat()}] old\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env["EMAIL_TRIAGE_STATE_DIR"] = str(override)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_SRC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "_log_compactor.py")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (override / "triage.log").read_text(encoding="utf-8") == ""


def test_log_compactor_migrates_and_prunes_legacy_log_before_any_decision(
    monkeypatch, tmp_path: Path
) -> None:
    compactor = _load("_log_compactor.py")
    _without_xdg(monkeypatch)
    monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
    legacy = tmp_path / "skill" / "triage.log"
    legacy.parent.mkdir()
    old = datetime.now(timezone.utc) - timedelta(days=31)
    legacy.write_text(f"[{old.isoformat()}] old\n", encoding="utf-8")
    destination = compactor.triage_log_path(home=tmp_path)
    monkeypatch.setattr(compactor, "LEGACY_LOG_FILE", legacy, raising=False)
    monkeypatch.setattr(compactor, "triage_log_path", lambda **_kwargs: destination)

    assert compactor.main() == 0

    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == ""


def test_decision_log_copies_legacy_log_only_when_canonical_log_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    sink = _load("_decision_sink.py")
    _without_xdg(monkeypatch)
    legacy = tmp_path / "installed-skill" / "triage.log"
    legacy.parent.mkdir()
    legacy.write_text("legacy\n", encoding="utf-8")
    destination = sink.triage_log_path(home=tmp_path)
    monkeypatch.setattr(sink, "LEGACY_LOG_FILE", legacy)
    monkeypatch.setattr(sink, "triage_log_path", lambda: destination)

    sink.Interface().run(
        argparse.Namespace(
            account="work",
            message_id="id-1",
            sender="sender@example.com",
            subject="subject",
            decision="archive",
            reason="done",
        )
    )

    assert destination.read_text(encoding="utf-8").startswith("legacy\n")

    destination.write_text("canonical\n", encoding="utf-8")
    legacy.write_text("legacy changed\n", encoding="utf-8")
    sink.Interface().run(
        argparse.Namespace(
            account="work",
            message_id="id-2",
            sender="sender@example.com",
            subject="subject",
            decision="archive",
            reason="done",
        )
    )

    assert destination.read_text(encoding="utf-8").startswith("canonical\n")


def test_log_compactor_reads_the_canonical_triage_log(monkeypatch, tmp_path: Path) -> None:
    compactor = _load("_log_compactor.py")
    canonical = tmp_path / "state" / "triage.log"
    canonical.parent.mkdir(parents=True)
    old = datetime.now(timezone.utc) - timedelta(days=31)
    canonical.write_text(f"[{old.isoformat()}] old\n", encoding="utf-8")
    monkeypatch.setattr(compactor, "triage_log_path", lambda: canonical)

    assert compactor.main() == 0
    assert canonical.read_text(encoding="utf-8") == ""
