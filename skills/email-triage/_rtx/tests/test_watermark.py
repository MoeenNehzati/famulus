"""CLI tests for update_watermark.py / mark_failure.py, isolated via
EMAIL_TRIAGE_STATE_DIR so nothing here touches the real state/ directory.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent
REPO_SRC = Path(__file__).resolve().parents[4] / "src"
SCRIPT_NAMES = {
    "update_watermark.py": "_watermark_writer.py",
    "mark_failure.py": "_failure_sentinel.py",
    "clear_failure.py": "_failure_clearer.py",
    "get_cutoff.py": "_watermark_floor.py",
}

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_module(script_name):
    spec = importlib.util.spec_from_file_location(
        script_name, SCRIPTS_DIR / script_name
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_update_watermark_with_run_id_records_it_alongside_result(tmp_path):
    result = run("update_watermark.py", tmp_path, "--run-id", "run-a")
    assert result.returncode == 0
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["result"] == "ok"
    assert status["last_finalized_run_id"] == "run-a"


def test_update_watermark_replay_with_same_run_id_does_not_readvance(tmp_path):
    first = run("update_watermark.py", tmp_path, "--run-id", "run-b")
    assert first.returncode == 0
    watermark_after_first = (tmp_path / "last_run").read_text()

    second = run("update_watermark.py", tmp_path, "--run-id", "run-b")
    assert second.returncode == 0
    watermark_after_second = (tmp_path / "last_run").read_text()

    assert watermark_after_second == watermark_after_first


def test_update_watermark_without_run_id_still_advances_every_call(tmp_path):
    # Backward compatible standalone behavior: with no --run-id there is no
    # idempotency key, so each clean call advances again (matches the
    # pre-existing test_watermark_survives_across_two_clean_runs below).
    run("update_watermark.py", tmp_path)
    first = (tmp_path / "last_run").read_text()
    run("update_watermark.py", tmp_path)
    second = (tmp_path / "last_run").read_text()
    assert second >= first
    status = json.loads((tmp_path / "status.json").read_text())
    assert "last_finalized_run_id" not in status


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


# ── default_state_dir ────────────────────────────────────────────────────────

STATE_DIR_MODULES = [
    "_watermark_writer.py",
    "_watermark_floor.py",
    "_failure_clearer.py",
    "_failure_sentinel.py",
]


@pytest.mark.parametrize("module_name", STATE_DIR_MODULES)
def test_state_dir_defaults_to_famulus_state_root_not_skill_dir(monkeypatch, tmp_path, module_name):
    monkeypatch.delenv("EMAIL_TRIAGE_STATE_DIR", raising=False)
    from officina.common.famulus_paths import resolve_famulus_paths

    expected = resolve_famulus_paths(
        platform=sys.platform, home=tmp_path
    ).email_triage_state_root

    module = _load_module(module_name)
    assert module.default_state_dir(home=tmp_path) == expected
    assert module.default_state_dir(home=tmp_path) != module.SKILL_DIR / "state"


@pytest.mark.parametrize("module_name", STATE_DIR_MODULES)
def test_state_dir_honors_explicit_env_override(monkeypatch, tmp_path, module_name):
    override = tmp_path / "custom-state"
    monkeypatch.setenv("EMAIL_TRIAGE_STATE_DIR", str(override))

    module = _load_module(module_name)
    assert module.default_state_dir() == override
