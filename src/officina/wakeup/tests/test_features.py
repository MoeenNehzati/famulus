from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from officina.wakeup.deadlines import DEFAULT_DELAY, parse_delay
from officina.wakeup.claude_codex_cli import main
from officina.wakeup.claude_codex_service import run_due, schedule
from officina.wakeup.doctor import collect_diagnostics
from officina.wakeup.locking import LockUnavailable, locked_file
from officina.wakeup.providers import provider_for
from officina.wakeup.claude_codex_sessions import latest_rate_limit
from officina.wakeup.store import append_job


def test_provider_adapters_cover_discovery_progress_resume_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))
    claude = provider_for("claude")
    codex = provider_for("codex")

    claude_event = {
        "type": "assistant",
        "sessionId": "11111111-2222-4333-8444-555555555555",
        "error": "rate_limit",
        "isApiErrorMessage": True,
        "timestamp": "2026-08-02T18:00:00Z",
        "cwd": str(tmp_path),
        "message": {
            "role": "assistant",
            "content": "You've hit your session limit; resets 6:50pm (America/New_York)",
        },
    }
    codex_event = {
        "timestamp": "2026-06-12T19:25:35Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {"used_percent": 100.0, "resets_at": 1781297184},
                "secondary": {"used_percent": 74.0, "resets_at": 1781359431},
            },
        },
    }

    assert claude.session_id(claude_event) == "11111111-2222-4333-8444-555555555555"
    assert claude.rate_limit(claude_event) is not None
    assert claude.meaningful(claude_event)
    assert claude.cwd(claude_event) == tmp_path
    assert claude.resume_command("/bin/provider", "id", "continue") == [
        "/bin/provider",
        "--print",
        "--permission-mode",
        "auto",
        "--allowedTools",
        "WebFetch,WebSearch",
        "--resume",
        "id",
        "continue",
    ]
    codex_limit = codex.rate_limit(codex_event)
    assert codex_limit is not None
    assert codex_limit.reset_at == datetime.fromtimestamp(1781297184, timezone.utc)
    assert codex.resume_command("/bin/provider", "id", "continue") == [
        "/bin/provider",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "--config",
        "sandbox_workspace_write.network_access=true",
        "--search",
        "exec",
        "resume",
        "id",
        "continue",
    ]


def test_latest_rate_limit_understands_real_codex_token_count_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    transcript = tmp_path / "codex" / f"rollout-{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    events = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": str(tmp_path)}},
        {
            "timestamp": "2026-06-12T19:25:35Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 100.0, "resets_at": 1781297184},
                    "secondary": {"used_percent": 74.0, "resets_at": 1781359431},
                },
            },
        },
    ]
    transcript.write_text("".join(json.dumps(e) + "\n" for e in events))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))

    limit = latest_rate_limit()

    assert limit.provider == "codex"
    assert limit.session_id == session_id
    assert limit.reset_at == datetime.fromtimestamp(1781297184, timezone.utc)


def test_default_delay_is_one_minute_and_can_be_overridden() -> None:
    assert DEFAULT_DELAY.total_seconds() == 60
    assert parse_delay("0 seconds").total_seconds() == 0
    assert parse_delay("5 minutes").total_seconds() == 300


def test_duplicate_jobs_in_same_timer_minute_are_coalesced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path))
    base = {
        "id": "first",
        "provider": "provider",
        "session_id": "session",
        "run_at": "2026-08-02T19:30:05+00:00",
        "message": "continue",
    }
    first, created = append_job(base)
    second, created_again = append_job(
        {**base, "id": "second", "run_at": "2026-08-02T19:30:55+00:00"}
    )
    assert created
    assert not created_again
    assert second["id"] == first["id"] == "first"


def test_nonblocking_portable_lock_reports_contention(tmp_path: Path) -> None:
    path = tmp_path / "queue.lock"
    with locked_file(path):
        with pytest.raises(LockUnavailable):
            with locked_file(path, blocking=False):
                pass


def test_doctor_reports_provider_queue_lock_and_scheduler_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))
    diagnostics = collect_diagnostics()
    names = {item.name for item in diagnostics}
    assert {"queue", "locking", "scheduler"}.issubset(names)
    assert any(item.name.startswith("provider:") for item in diagnostics)


def _claude_transcript(root: Path, session_id: str, cwd: Path) -> Path:
    path = root / "project" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "message": {"role": "user", "content": "work"},
                "cwd": str(cwd),
            }
        )
        + "\n"
    )
    return path


def test_cli_applies_default_delay_and_coalesces_duplicate_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_NOW", "2026-08-02T12:00:00-04:00")

    argv = ["schedule", "claude", session_id, "1 hour"]
    assert main(argv) == 0
    assert main(argv) == 0

    jobs = json.loads((tmp_path / "state" / "jobs.json").read_text())
    assert len(jobs) == 1
    assert jobs[0]["run_at"] == "2026-08-02T17:01:00+00:00"
    assert "Already scheduled" in capsys.readouterr().out


def test_cli_can_enable_check_and_disable_auto_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))

    target = ["claude", session_id]
    assert main(["auto", "on", *target]) == 0
    assert main(["auto", "status", *target]) == 0
    assert "enabled" in capsys.readouterr().out

    policies = json.loads((tmp_path / "state" / "session-policies.json").read_text())
    assert policies[f"claude:{session_id}"]["auto_schedule"] is True

    assert main(["auto", "off", *target]) == 0
    assert main(["auto", "status", *target]) == 0
    assert "disabled" in capsys.readouterr().out
    assert json.loads((tmp_path / "state" / "session-policies.json").read_text()) == {}


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_due_worker_delivers_through_each_provider_adapter(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    if provider == "claude":
        _claude_transcript(tmp_path / "claude", session_id, tmp_path)
        monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    else:
        transcript = tmp_path / "codex" / f"rollout-{session_id}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": session_id, "cwd": str(tmp_path)},
                }
            )
            + "\n"
        )
        monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))
    output = tmp_path / f"{provider}.received"
    executable = tmp_path / f"{provider}-bin"
    executable.write_text(f"#!/bin/sh\nprintf '%s' \"$*\" > {output}\n")
    executable.chmod(0o755)
    monkeypatch.setenv(f"LLM_WAKEUP_{provider.upper()}_BIN", str(executable))
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_NOW", "2026-08-02T12:00:00+00:00")

    schedule(
        provider,
        session_id,
        datetime(2026, 8, 2, 11, 59, tzinfo=timezone.utc),
        "continue now",
    )
    run_due()

    assert "continue now" in output.read_text()
    assert json.loads((tmp_path / "state" / "jobs.json").read_text()) == []
