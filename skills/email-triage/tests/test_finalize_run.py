"""CLI tests for _finalize_run.py — ordered, idempotent, replay-safe
finalization of a triage run (metrics write + watermark advance).

Isolated via EMAIL_TRIAGE_STATE_DIR so nothing here touches the real
state/ directory. Invoked with `python3 -m _rtx._finalize_run` (rather than
by path) because the module uses package-relative imports to compose the
real _write_metrics.py and _watermark_writer.py CLI entry points in-process.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = SKILL_ROOT / "_rtx"
REPO_SRC = Path(__file__).resolve().parents[3] / "src"

BASE_METRICS_ARGS = [
    "--total-scanned", "10",
    "--added-todo", "2",
    "--added-triage", "3",
    "--skipped", "5",
    "--deduped", "0",
    "--accounts", "work,personal",
]


def run_finalize(state_dir, *extra_args):
    env = os.environ.copy()
    env["EMAIL_TRIAGE_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = f"{REPO_SRC}{os.pathsep}{SKILL_ROOT}"
    return subprocess.run(
        [sys.executable, "-m", "_rtx._finalize_run", *BASE_METRICS_ARGS, *extra_args],
        capture_output=True, text=True, cwd=str(SKILL_ROOT), env=env,
    )


def run_script(script_name, state_dir, *args):
    env = os.environ.copy()
    env["EMAIL_TRIAGE_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = str(REPO_SRC)
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), *args],
        capture_output=True, text=True, env=env,
    )


def status(state_dir):
    return json.loads((state_dir / "status.json").read_text())


# ── ordering ──────────────────────────────────────────────────────────────


def test_finalize_writes_metrics_then_advances_watermark(tmp_path):
    result = run_finalize(tmp_path, "--run-id", "run-1")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "last_run").exists()
    st = status(tmp_path)
    assert st["result"] == "ok"
    assert st["metrics"]["total_scanned"] == 10
    assert st["metrics"]["added_todo"] == 2
    assert st["accounts_triaged"] == ["work", "personal"]
    assert "watermark_advanced_at" in st
    assert st["last_finalized_run_id"] == "run-1"


def test_finalize_does_not_advance_watermark_when_metrics_args_invalid(tmp_path):
    # Omit a required metrics flag (--total-scanned) so write_metrics.main()
    # fails argument parsing. The watermark step must never run.
    env = os.environ.copy()
    env["EMAIL_TRIAGE_STATE_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = f"{REPO_SRC}{os.pathsep}{SKILL_ROOT}"
    result = subprocess.run(
        [
            sys.executable, "-m", "_rtx._finalize_run",
            "--run-id", "run-bad",
            "--added-todo", "1", "--added-triage", "1", "--skipped", "1",
        ],
        capture_output=True, text=True, cwd=str(SKILL_ROOT), env=env,
    )

    assert result.returncode != 0
    assert not (tmp_path / "last_run").exists()
    assert not (tmp_path / "status.json").exists()


def test_finalize_does_not_advance_watermark_when_run_marked_failed(tmp_path):
    # Simulate a stale, unresolved failure latch from an earlier run.
    run_script("_failure_sentinel.py", tmp_path, "credentials expired")

    result = run_finalize(tmp_path, "--run-id", "run-2")

    assert result.returncode != 0
    assert not (tmp_path / "last_run").exists()
    st = status(tmp_path)
    # Existing error state must survive untouched — no corruption, no
    # silent flip to "ok", and the run-id must not be consumed.
    assert st["result"] == "error"
    assert st["message"] == "credentials expired"
    assert "last_finalized_run_id" not in st
    # write_metrics still ran (mirrors the pre-existing script's documented
    # behavior of merging metrics while preserving error state) — this
    # proves finalize didn't corrupt state, just correctly refused to
    # advance the watermark on top of it.
    assert st["metrics"]["total_scanned"] == 10


def test_rejected_call_leaves_valid_json_and_can_be_retried_after_fix(tmp_path):
    run_script("_failure_sentinel.py", tmp_path, "credentials expired")
    first = run_finalize(tmp_path, "--run-id", "run-3")
    assert first.returncode != 0

    # Operator fixes the problem and clears the latch.
    run_script("_failure_clearer.py", tmp_path, "credentials restored")

    # Same run-id retried — must now succeed since it was never consumed.
    second = run_finalize(tmp_path, "--run-id", "run-3")
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "last_run").exists()
    st = status(tmp_path)
    assert st["result"] == "ok"
    assert st["last_finalized_run_id"] == "run-3"


# ── idempotency / replay-safety ──────────────────────────────────────────


def test_replaying_same_run_id_does_not_double_apply(tmp_path):
    first = run_finalize(tmp_path, "--run-id", "run-4")
    assert first.returncode == 0
    watermark_after_first = (tmp_path / "last_run").read_text()
    status_after_first = status(tmp_path)

    # Replay with the SAME run-id but different metrics values — a real
    # accidental-double-call scenario (e.g. a retried tool call after an
    # ambiguous network error). Must be a true no-op, not just "same
    # timestamp by coincidence".
    second = run_finalize(
        tmp_path, "--run-id", "run-4",
        "--total-scanned", "999", "--added-todo", "999",
    )
    assert second.returncode == 0, second.stderr
    watermark_after_second = (tmp_path / "last_run").read_text()
    status_after_second = status(tmp_path)

    assert watermark_after_second == watermark_after_first
    assert status_after_second == status_after_first
    assert status_after_second["metrics"]["total_scanned"] == 10  # unchanged


def test_different_run_id_applies_again(tmp_path):
    run_finalize(tmp_path, "--run-id", "run-5")
    watermark_after_first = (tmp_path / "last_run").read_text()

    result = run_finalize(tmp_path, "--run-id", "run-6")
    assert result.returncode == 0, result.stderr
    watermark_after_second = (tmp_path / "last_run").read_text()

    assert watermark_after_second >= watermark_after_first
    st = status(tmp_path)
    assert st["last_finalized_run_id"] == "run-6"


def test_empty_run_id_rejected(tmp_path):
    result = run_finalize(tmp_path, "--run-id", "  ")
    assert result.returncode != 0
    assert not (tmp_path / "last_run").exists()


# ── backward compatibility: the two original CLI scripts still work ──────


def test_write_metrics_standalone_cli_unaffected(tmp_path):
    result = run_script(
        "_write_metrics.py", tmp_path,
        "--total-scanned", "7", "--added-todo", "1",
        "--added-triage", "1", "--skipped", "5",
    )
    assert result.returncode == 0, result.stderr
    st = status(tmp_path)
    assert st["metrics"]["total_scanned"] == 7
    assert "last_finalized_run_id" not in st


def test_watermark_writer_standalone_cli_unaffected(tmp_path):
    result = run_script("_watermark_writer.py", tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "last_run").exists()
    assert status(tmp_path)["result"] == "ok"


# ── crash safety: no double-advance if the process dies mid-finalize ─────


def _load_finalize_module(tmp_path):
    """Import _rtx._finalize_run (and the _write_metrics / _watermark_writer
    submodules it composes) fresh, in-process, wired to tmp_path — so the
    test below can monkeypatch a real write call to simulate a crash at an
    exact point, something a subprocess-based test can't do.
    """
    repo_src = str(REPO_SRC)
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    skill_path = str(SKILL_ROOT)
    if skill_path in sys.path:
        sys.path.remove(skill_path)
    sys.path.insert(0, skill_path)
    for name in tuple(sys.modules):
        if name == "_rtx" or name.startswith("_rtx."):
            sys.modules.pop(name, None)

    module = importlib.import_module("_rtx._finalize_run")

    status_file = tmp_path / "status.json"
    watermark_file = tmp_path / "last_run"
    module.STATUS_FILE = status_file
    module.write_metrics.STATUS_FILE = status_file
    module.watermark_writer.STATUS_FILE = status_file
    module.watermark_writer.WATERMARK = watermark_file
    return module


def test_crash_between_status_commit_and_watermark_file_write_is_safe_on_replay(
    tmp_path, monkeypatch
):
    """Simulate the process dying at the exact point _watermark_writer.py
    has just committed status.json (result + watermark timestamp + run id
    in one write) but has not yet written the watermark file itself. A
    replay with the same run id afterward must NOT advance the watermark a
    second time to a later timestamp — the file must be left exactly as the
    crash left it (untouched), and the replay must be recognized as a
    no-op, matching what already-committed status.json says.
    """
    module = _load_finalize_module(tmp_path)
    watermark_file = tmp_path / "last_run"

    # Never let the watermark file itself be written, for the whole test —
    # this proves not just that the first call crashes there, but that the
    # replay's success path genuinely never attempts that write again
    # (rather than merely happening to "win a race" against a one-shot
    # failure).
    real_write_text = Path.write_text

    def flaky_write_text(self, *args, **kwargs):
        if self == watermark_file:
            raise OSError("simulated crash while writing the watermark file")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError):
        module.main([
            "--run-id", "crash-run",
            "--total-scanned", "5", "--added-todo", "1",
            "--added-triage", "1", "--skipped", "3",
        ])

    # The "crash" fired — watermark file was never written, but status.json
    # (written just before it, in the same call) already committed the run.
    assert not watermark_file.exists()
    committed_status = json.loads((tmp_path / "status.json").read_text())
    assert committed_status["result"] == "ok"
    assert committed_status["last_finalized_run_id"] == "crash-run"

    # "Process restart": replay with the SAME run id and different metrics,
    # as a real retried caller would. The watermark file write is still
    # rigged to raise for the whole test, so if the replay's success path
    # tried to write it, this call would raise too instead of returning 0.
    rc = module.main([
        "--run-id", "crash-run",
        "--total-scanned", "999", "--added-todo", "999",
        "--added-triage", "999", "--skipped", "999",
    ])

    assert rc == 0
    # Must still not exist / not have been advanced by the replay — no
    # second, later timestamp was ever written.
    assert not watermark_file.exists()
    replayed_status = json.loads((tmp_path / "status.json").read_text())
    assert replayed_status == committed_status
