"""CLI tests for update_watermark.py / mark_failure.py, isolated via
EMAIL_TRIAGE_STATE_DIR so nothing here touches the real state/ directory.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "_rtx"
REPO_SRC = Path(__file__).resolve().parents[3] / "src"
SCRIPT_NAMES = {
    "update_watermark.py": "_watermark_writer.py",
    "mark_failure.py": "_failure_sentinel.py",
    "clear_failure.py": "_failure_clearer.py",
    "get_cutoff.py": "_watermark_floor.py",
}


def run(script, state_dir, *args, input=None):
    env = os.environ.copy()
    env["EMAIL_TRIAGE_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = str(REPO_SRC)
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / SCRIPT_NAMES[script]), *args],
        capture_output=True, text=True, input=input,
        env=env,
    )


def test_update_watermark_advances_on_clean_run(tmp_path):
    result = run("update_watermark.py", tmp_path)
    assert result.returncode == 0
    assert (tmp_path / "last_run").exists()
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["result"] == "ok"


def test_mark_failure_blocks_subsequent_watermark_update(tmp_path):
    run("mark_failure.py", tmp_path, "something broke")
    result = run("update_watermark.py", tmp_path)
    assert result.returncode != 0
    assert "something broke" in result.stderr
    assert not (tmp_path / "last_run").exists()


def test_mark_failure_default_reason_when_none_given(tmp_path):
    result = run("mark_failure.py", tmp_path)
    assert result.returncode == 0
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["result"] == "error"
    assert status["message"]  # non-empty default reason


def test_clear_failure_does_not_advance_watermark(tmp_path):
    run("mark_failure.py", tmp_path, "credentials missing")

    result = run("clear_failure.py", tmp_path, "OAuth restored")

    assert result.returncode == 0
    assert not (tmp_path / "last_run").exists()
    status = json.loads((tmp_path / "status.json").read_text())
    assert status == {
        "result": "ok",
        "message": "failure cleared: OAuth restored; watermark unchanged",
    }


def test_clear_failure_allows_fresh_successful_update(tmp_path):
    run("mark_failure.py", tmp_path, "credentials missing")
    run("clear_failure.py", tmp_path, "OAuth restored")

    result = run("update_watermark.py", tmp_path)

    assert result.returncode == 0
    assert (tmp_path / "last_run").exists()
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["result"] == "ok"


def test_watermark_survives_across_two_clean_runs(tmp_path):
    run("update_watermark.py", tmp_path)
    first = (tmp_path / "last_run").read_text()
    run("update_watermark.py", tmp_path)
    second = (tmp_path / "last_run").read_text()
    assert second >= first  # timestamp advanced, not reset


def test_get_cutoff_reads_watermark_written_by_update_watermark(tmp_path):
    run("update_watermark.py", tmp_path)
    result = run("get_cutoff.py", tmp_path)
    assert result.returncode == 0
    # Should print today's or yesterday's date, not the 2-day-back default
    # that only kicks in when no watermark exists at all.
    assert "WARNING" not in result.stderr
