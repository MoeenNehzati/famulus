"""In-process state histories for the email-triage watermark helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
REPO_SRC = Path(__file__).resolve().parents[4] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name.removesuffix(".py"), SCRIPTS_DIR / module_name
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# Execute each implementation module once. Every mutating scenario below then
# rebinds its path globals to a fresh state root before calling main(argv).
WATERMARK_WRITER = _load_module("_watermark_writer.py")
WATERMARK_FLOOR = _load_module("_watermark_floor.py")
FAILURE_SENTINEL = _load_module("_failure_sentinel.py")
FAILURE_CLEARER = _load_module("_failure_clearer.py")
WRITE_METRICS = _load_module("_write_metrics.py")


def _status(state_root: Path) -> dict:
    return json.loads((state_root / "status.json").read_text())


def _metrics_args() -> list[str]:
    return [
        "--total-scanned",
        "3",
        "--added-todo",
        "0",
        "--added-triage",
        "0",
        "--skipped",
        "3",
        "--deduped",
        "0",
        "--accounts",
        "personal",
    ]


def test_writer_and_floor_state_histories(tmp_path, capsys):
    run_id_root = tmp_path / "run-id-and-replay"
    WATERMARK_WRITER.STATUS_FILE = run_id_root / "status.json"
    WATERMARK_WRITER.WATERMARK = run_id_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main(["--run-id", "run-a"]) == 0
    first_output = capsys.readouterr()
    first_status = _status(run_id_root)
    first_watermark = (run_id_root / "last_run").read_text()
    assert first_status["result"] == "ok"
    assert first_status["last_finalized_run_id"] == "run-a"
    assert "Watermark updated:" in first_output.out

    WATERMARK_WRITER.STATUS_FILE = run_id_root / "status.json"
    WATERMARK_WRITER.WATERMARK = run_id_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main(["--run-id", "run-a"]) == 0
    replay_output = capsys.readouterr()
    assert "no-op (replay-safe)" in replay_output.out
    assert _status(run_id_root) == first_status
    assert (run_id_root / "last_run").read_text() == first_watermark

    no_run_id_root = tmp_path / "repeated-no-run-id"
    WATERMARK_WRITER.STATUS_FILE = no_run_id_root / "status.json"
    WATERMARK_WRITER.WATERMARK = no_run_id_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main([]) == 0
    capsys.readouterr()
    first_without_id = (no_run_id_root / "last_run").read_text()

    WATERMARK_WRITER.STATUS_FILE = no_run_id_root / "status.json"
    WATERMARK_WRITER.WATERMARK = no_run_id_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main([]) == 0
    capsys.readouterr()
    second_without_id = (no_run_id_root / "last_run").read_text()
    assert second_without_id >= first_without_id
    assert "last_finalized_run_id" not in _status(no_run_id_root)

    stale_root = tmp_path / "stale-status-replacement"
    stale_root.mkdir()
    (stale_root / "status.json").write_text(
        json.dumps({"result": "pending", "message": "reset at start of new run"})
    )
    WATERMARK_WRITER.STATUS_FILE = stale_root / "status.json"
    WATERMARK_WRITER.WATERMARK = stale_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main([]) == 0
    capsys.readouterr()
    stale_status = _status(stale_root)
    assert stale_status["result"] == "ok"
    assert stale_status["message"] == "watermark advanced"

    cutoff_root = tmp_path / "writer-floor-interoperability"
    WATERMARK_WRITER.STATUS_FILE = cutoff_root / "status.json"
    WATERMARK_WRITER.WATERMARK = cutoff_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main([]) == 0
    capsys.readouterr()
    written_date = date.fromisoformat((cutoff_root / "last_run").read_text()[:10])

    WATERMARK_FLOOR.STATUS_FILE = cutoff_root / "status.json"
    WATERMARK_FLOOR.WATERMARK = cutoff_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_FLOOR.main([]) == 0
    cutoff_output = capsys.readouterr()
    assert cutoff_output.err == ""
    assert cutoff_output.out.strip() == (written_date - timedelta(days=1)).isoformat()


def test_failure_and_recovery_state_histories(tmp_path, capsys):
    default_reason_root = tmp_path / "default-failure-reason"
    FAILURE_SENTINEL.STATUS_FILE = default_reason_root / "status.json"
    capsys.readouterr()
    assert FAILURE_SENTINEL.main([]) == 0
    capsys.readouterr()
    default_status = _status(default_reason_root)
    assert default_status["result"] == "error"
    assert default_status["message"]

    blocked_root = tmp_path / "failure-blocks-watermark"
    FAILURE_SENTINEL.STATUS_FILE = blocked_root / "status.json"
    capsys.readouterr()
    assert FAILURE_SENTINEL.main(["something broke"]) == 0
    capsys.readouterr()

    WATERMARK_WRITER.STATUS_FILE = blocked_root / "status.json"
    WATERMARK_WRITER.WATERMARK = blocked_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_WRITER.main([]) != 0
    blocked_output = capsys.readouterr()
    assert "something broke" in blocked_output.err
    assert not (blocked_root / "last_run").exists()

    recovery_root = tmp_path / "clear-failure"
    FAILURE_SENTINEL.STATUS_FILE = recovery_root / "status.json"
    capsys.readouterr()
    assert FAILURE_SENTINEL.main(["credentials missing"]) == 0
    capsys.readouterr()

    FAILURE_CLEARER.STATUS_FILE = recovery_root / "status.json"
    capsys.readouterr()
    assert FAILURE_CLEARER.main(["OAuth restored"]) == 0
    capsys.readouterr()
    assert not (recovery_root / "last_run").exists()
    assert _status(recovery_root) == {
        "result": "ok",
        "message": "failure cleared: OAuth restored; watermark unchanged",
    }


def test_metrics_state_histories(tmp_path, capsys):
    stale_ok_root = tmp_path / "stale-ok"
    stale_ok_root.mkdir()
    (stale_ok_root / "status.json").write_text(
        json.dumps({"result": "ok", "message": "watermark advanced"})
    )
    WRITE_METRICS.STATUS_FILE = stale_ok_root / "status.json"
    capsys.readouterr()
    assert WRITE_METRICS.main(_metrics_args()) == 0
    capsys.readouterr()
    stale_ok_status = _status(stale_ok_root)
    assert stale_ok_status["result"] == "pending"
    assert stale_ok_status["metrics"]["total_scanned"] == 3

    latched_error_root = tmp_path / "latched-error"
    latched_error_root.mkdir()
    (latched_error_root / "status.json").write_text(
        json.dumps({"result": "error", "message": "upload failed"})
    )
    WRITE_METRICS.STATUS_FILE = latched_error_root / "status.json"
    capsys.readouterr()
    assert WRITE_METRICS.main(_metrics_args()) == 0
    capsys.readouterr()
    latched_error_status = _status(latched_error_root)
    assert latched_error_status["result"] == "error"
    assert latched_error_status["message"] == "upload failed"


def test_warning_history_preserves_replay_guard(tmp_path, capsys):
    warning_root = tmp_path / "warning-preserves-replay-guard"
    warning_root.mkdir()
    (warning_root / "status.json").write_text(
        json.dumps({"result": "ok", "last_finalized_run_id": "abc123"})
    )
    WATERMARK_FLOOR.STATUS_FILE = warning_root / "status.json"
    WATERMARK_FLOOR.WATERMARK = warning_root / "last_run"
    capsys.readouterr()
    assert WATERMARK_FLOOR.main([]) == 0
    warning_output = capsys.readouterr()
    status = _status(warning_root)
    assert "WARNING" in warning_output.err
    assert status["result"] == "warning"
    assert status["last_finalized_run_id"] == "abc123"


def test_state_dir_defaults_and_overrides_for_all_state_modules(monkeypatch, tmp_path):
    from officina.common.famulus_paths import resolve_famulus_paths

    modules = {
        "watermark-writer": WATERMARK_WRITER,
        "watermark-floor": WATERMARK_FLOOR,
        "failure-clearer": FAILURE_CLEARER,
        "failure-sentinel": FAILURE_SENTINEL,
    }
    for label, module in modules.items():
        home = tmp_path / label / "home"
        monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
        expected = resolve_famulus_paths(
            platform=sys.platform, home=home, environ=os.environ
        ).email_triage_state_root
        assert module.default_state_dir(home=home) == expected
        assert module.default_state_dir(home=home) != module.SKILL_DIR / "state"

        override = tmp_path / label / "explicit-state"
        monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(override))
        assert module.default_state_dir() == override
