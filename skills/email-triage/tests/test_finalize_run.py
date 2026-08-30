"""CLI tests for _finalize_run.py — ordered, idempotent, replay-safe
finalization of a triage run (metrics write + watermark advance).

Isolated via EMAIL_TRIAGE_STATE_DIR so nothing here touches the real
state/ directory. Invoked with `python3 -m _rtx._finalize_run` (rather than
by path) because the module uses package-relative imports to compose the
real _write_metrics.py and _watermark_writer.py CLI entry points in-process.
"""
import importlib.util
import json
import os
import subprocess
import sys
import types
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


_FINALIZE_PACKAGE = "_task_41_finalize_run"


def _load_finalize_module():
    """Load finalize under a test-private package alias for relative imports."""
    package = types.ModuleType(_FINALIZE_PACKAGE)
    package.__path__ = [str(SCRIPTS_DIR)]
    sys.modules[_FINALIZE_PACKAGE] = package
    spec = importlib.util.spec_from_file_location(
        f"{_FINALIZE_PACKAGE}._finalize_run", SCRIPTS_DIR / "_finalize_run.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def finalize_module():
    """Keep one alias-scoped import, then remove only its private modules."""
    try:
        yield _load_finalize_module()
    finally:
        for name in tuple(sys.modules):
            if name == _FINALIZE_PACKAGE or name.startswith(f"{_FINALIZE_PACKAGE}."):
                sys.modules.pop(name, None)


def _bind_state(module, state_dir):
    status_file = state_dir / "status.json"
    module.STATUS_FILE = status_file
    module.write_metrics.STATUS_FILE = status_file
    module.watermark_writer.STATUS_FILE = status_file
    module.watermark_writer.WATERMARK = state_dir / "last_run"


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


def test_invalid_arguments_exit_without_writing_state(tmp_path, capsys, finalize_module):
    _bind_state(finalize_module, tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        finalize_module.main([
            "--run-id", "run-bad",
            "--added-todo", "1", "--added-triage", "1", "--skipped", "1",
        ])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith(
        "error: the following arguments are required: --total-scanned\n"
    )
    assert not (tmp_path / "last_run").exists()
    assert not (tmp_path / "status.json").exists()

    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "  "]) == 1
    assert capsys.readouterr().err == "error: --run-id must not be empty\n"
    assert not (tmp_path / "last_run").exists()
    assert not (tmp_path / "status.json").exists()


def test_latched_failure_refuses_then_same_run_id_retries_after_recovery(
    tmp_path, finalize_module
):
    """The sentinel/clearer own their writes in test_watermark; exercise the
    finalize composition against their persisted states here.
    """
    refused_root = tmp_path / "refused"
    _bind_state(finalize_module, refused_root)
    refused_root.mkdir()
    (refused_root / "status.json").write_text(json.dumps({
        "result": "error", "message": "credentials expired"
    }))

    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-2"]) != 0
    assert not (refused_root / "last_run").exists()
    st = status(refused_root)
    assert st["result"] == "error"
    assert st["message"] == "credentials expired"
    assert "last_finalized_run_id" not in st
    assert st["metrics"]["total_scanned"] == 10

    recovery_root = tmp_path / "recovery"
    _bind_state(finalize_module, recovery_root)
    recovery_root.mkdir()
    (recovery_root / "status.json").write_text(json.dumps({
        "result": "error", "message": "credentials expired"
    }))
    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-2"]) != 0
    assert not (recovery_root / "last_run").exists()

    (recovery_root / "status.json").write_text(json.dumps({
        "result": "ok",
        "message": "failure cleared: credentials restored; watermark unchanged",
    }))
    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-2"]) == 0
    assert (recovery_root / "last_run").exists()
    st = status(recovery_root)
    assert st["result"] == "ok"
    assert st["last_finalized_run_id"] == "run-2"


# ── idempotency / replay-safety ──────────────────────────────────────────


def test_replay_is_a_noop_and_different_run_id_applies_again(tmp_path, finalize_module):
    _bind_state(finalize_module, tmp_path)
    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-4"]) == 0
    watermark_after_first = (tmp_path / "last_run").read_text()
    status_after_first = status(tmp_path)

    # Replay with the SAME run-id but different metrics values — a real
    # accidental-double-call scenario (e.g. a retried tool call after an
    # ambiguous network error). Must be a true no-op, not just "same
    # timestamp by coincidence".
    assert finalize_module.main([
        *BASE_METRICS_ARGS, "--run-id", "run-4",
        "--total-scanned", "999", "--added-todo", "999",
    ]) == 0
    watermark_after_second = (tmp_path / "last_run").read_text()
    status_after_second = status(tmp_path)

    assert watermark_after_second == watermark_after_first
    assert status_after_second == status_after_first
    assert status_after_second["metrics"]["total_scanned"] == 10  # unchanged

    watermark_after_second = (tmp_path / "last_run").read_text()
    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-5"]) == 0
    watermark_after_third = (tmp_path / "last_run").read_text()
    assert finalize_module.main([*BASE_METRICS_ARGS, "--run-id", "run-6"]) == 0
    watermark_after_fourth = (tmp_path / "last_run").read_text()

    assert watermark_after_third >= watermark_after_second
    assert watermark_after_fourth >= watermark_after_third
    st = status(tmp_path)
    assert st["last_finalized_run_id"] == "run-6"


# ── backward compatibility: the two original CLI scripts still work ──────


def test_standalone_metrics_and_watermark_clis_are_unaffected(tmp_path):
    metrics_root = tmp_path / "metrics"
    result = run_script(
        "_write_metrics.py", metrics_root,
        "--total-scanned", "7", "--added-todo", "1",
        "--added-triage", "1", "--skipped", "5",
    )
    assert result.returncode == 0, result.stderr
    st = status(metrics_root)
    assert st["metrics"]["total_scanned"] == 7
    assert "last_finalized_run_id" not in st

    watermark_root = tmp_path / "watermark"
    result = run_script("_watermark_writer.py", watermark_root)
    assert result.returncode == 0, result.stderr
    assert (watermark_root / "last_run").exists()
    assert status(watermark_root)["result"] == "ok"


# ── crash safety: no double-advance if the process dies mid-finalize ─────


def test_crash_between_status_commit_and_watermark_file_write_is_safe_on_replay(
    tmp_path, monkeypatch, finalize_module
):
    """Simulate the process dying at the exact point _watermark_writer.py
    has just committed status.json (result + watermark timestamp + run id
    in one write) but has not yet written the watermark file itself. A
    replay with the same run id afterward must NOT advance the watermark a
    second time to a later timestamp — the file must be left exactly as the
    crash left it (untouched), and the replay must be recognized as a
    no-op, matching what already-committed status.json says.
    """
    module = finalize_module
    _bind_state(module, tmp_path)
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
