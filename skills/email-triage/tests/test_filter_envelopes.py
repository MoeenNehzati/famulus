"""Unit + CLI tests for filter_envelopes.py's watermark/filtering logic.

Unit tests monkeypatch STATE_DIR/WATERMARK/STATUS_FILE directly. CLI tests
invoke the script as a subprocess with EMAIL_TRIAGE_STATE_DIR pointed at a
tmp_path. Either way, nothing here touches the real email-triage/state/ dir.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "_rtx" / "_envelope_gate.py"
MODULE_PATH = SCRIPT_PATH
spec = importlib.util.spec_from_file_location("filter_envelopes", MODULE_PATH)
fe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(fe)


def _isolate(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(fe, "STATE_DIR", state_dir)
    monkeypatch.setattr(fe, "WATERMARK", state_dir / "last_run")
    monkeypatch.setattr(fe, "STATUS_FILE", state_dir / "status.json")
    return state_dir


# ── load_cutoff ──────────────────────────────────────────────────────────────

def test_load_cutoff_missing_watermark_warns_and_records_status(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    cutoff_dt, warning = fe.load_cutoff()
    assert warning is not None and "WARNING" in warning
    status = json.loads((state_dir / "status.json").read_text())
    assert status["result"] == "warning"


def test_load_cutoff_reads_existing_watermark(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    (state_dir / "last_run").write_text("2026-07-04T22:08:47-04:00")
    cutoff_dt, warning = fe.load_cutoff()
    assert warning is None
    assert cutoff_dt.isoformat() == "2026-07-04T22:08:47-04:00"


def test_load_cutoff_legacy_date_only_watermark(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    (state_dir / "last_run").write_text("2026-07-04")
    cutoff_dt, warning = fe.load_cutoff()
    assert warning is None
    assert cutoff_dt.year == 2026 and cutoff_dt.month == 7 and cutoff_dt.day == 4


# ── clear_stale_error ────────────────────────────────────────────────────────

def test_clear_stale_error_resets_error_to_ok(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    (state_dir / "status.json").write_text(json.dumps({"result": "error", "message": "boom"}))
    fe.clear_stale_error()
    status = json.loads((state_dir / "status.json").read_text())
    assert status["result"] == "ok"


def test_clear_stale_error_leaves_ok_status_untouched(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    (state_dir / "status.json").write_text(json.dumps({"result": "ok", "message": "fine"}))
    fe.clear_stale_error()
    status = json.loads((state_dir / "status.json").read_text())
    assert status == {"result": "ok", "message": "fine"}


def test_clear_stale_error_noop_when_no_status_file(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    fe.clear_stale_error()
    assert not (state_dir / "status.json").exists()


def test_clear_stale_error_handles_corrupt_json(monkeypatch, tmp_path):
    state_dir = _isolate(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    (state_dir / "status.json").write_text("not valid json{{{")
    fe.clear_stale_error()  # should not raise


# ── CLI: end-to-end date filtering (subprocess, isolated state dir) ─────────

def run_cli(state_dir, *args, input_json):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True, text=True, input=json.dumps(input_json),
        env={"EMAIL_TRIAGE_STATE_DIR": str(state_dir)},
    )


def test_cli_drops_envelopes_at_or_before_watermark(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "last_run").write_text("2026-07-05T10:00:00-04:00")
    envelopes = [
        {"id": "1", "subject": "before", "date": "2026-07-05T09:00:00-04:00"},
        {"id": "2", "subject": "at cutoff exactly", "date": "2026-07-05T10:00:00-04:00"},
        {"id": "3", "subject": "after", "date": "2026-07-05T11:00:00-04:00"},
    ]
    result = run_cli(tmp_path, "-a", "work", input_json=envelopes)
    assert result.returncode == 0
    kept = json.loads(result.stdout)
    assert [e["id"] for e in kept] == ["3"]


def test_cli_no_new_emails_prints_placeholder(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "last_run").write_text("2026-07-05T10:00:00-04:00")
    result = run_cli(tmp_path, "-a", "work", input_json=[])
    assert result.returncode == 0
    assert "no new emails for work" in result.stdout


def test_cli_missing_watermark_still_filters_by_24h_default(tmp_path):
    result = run_cli(tmp_path, "-a", "work", input_json=[
        {"id": "1", "subject": "old", "date": "2020-01-01T00:00:00+00:00"},
    ])
    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert "no new emails" in result.stdout  # old envelope correctly dropped


def test_cli_envelope_without_date_is_kept_conservatively(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "last_run").write_text("2026-07-05T10:00:00-04:00")
    result = run_cli(tmp_path, "-a", "work", input_json=[{"id": "1", "subject": "no date field"}])
    kept = json.loads(result.stdout)
    assert [e["id"] for e in kept] == ["1"]
