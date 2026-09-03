from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from .. import DEFAULT_MESSAGE
from .. import _wakeup_locking
from .._wakeup_deadlines import DEFAULT_DELAY, parse_delay
from .._claude_codex_cli import main
from .._claude_codex_service import run_due, schedule
from .._wakeup_doctor import collect_diagnostics
from .._wakeup_locking import LockUnavailable, locked_file
from .._wakeup_policies import auto_scheduled_sessions
from .._wakeup_providers import provider_for
from .._claude_codex_sessions import latest_rate_limit
from .._wakeup_store import append_job


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
    # Verbatim shape of a real refusal: Codex marks the limit on the
    # task_complete record, not on a token_count percentage.
    codex_event = {
        "timestamp": "2026-08-19T22:00:07.132Z",
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "01a01c09-e0c4-7f51-a9d3-56966c738bbc",
            "last_agent_message": None,
            "error": {
                "message": (
                    "You've hit your usage limit. Visit "
                    "https://chatgpt.com/codex/settings/usage to purchase more "
                    "credits or try again at Aug 20th, 2026 12:16 PM."
                ),
                "codex_error_info": "usage_limit_exceeded",
            },
            "started_at": 1787176804,
            "completed_at": 1787176807,
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
    # 12:16 PM in the machine's local zone, stated only as English prose.
    assert codex_limit.reset_at == datetime(
        2026, 8, 20, 12, 16
    ).astimezone(timezone.utc)
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


def test_latest_rate_limit_understands_real_codex_refusal_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    transcript = tmp_path / "codex" / f"rollout-{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    events = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": str(tmp_path)}},
        # A fully consumed window is not a refusal: this one ran on afterwards.
        {
            "timestamp": "2026-08-19T21:00:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 100.0, "resets_at": 1781297184},
                    "secondary": {"used_percent": 74.0, "resets_at": 1781359431},
                },
            },
        },
        {
            "timestamp": "2026-08-19T22:00:07.132Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "last_agent_message": None,
                "error": {
                    "message": (
                        "You've hit your usage limit. Visit "
                        "https://chatgpt.com/codex/settings/usage to purchase "
                        "more credits or try again at Aug 20th, 2026 12:16 PM."
                    ),
                    "codex_error_info": "usage_limit_exceeded",
                },
                "completed_at": 1787176807,
            },
        },
    ]
    transcript.write_text("".join(json.dumps(e) + "\n" for e in events))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))

    limit = latest_rate_limit()

    assert limit.provider == "codex"
    assert limit.session_id == session_id
    assert limit.reset_at == datetime(
        2026, 8, 20, 12, 16
    ).astimezone(timezone.utc)


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


def test_nonblocking_windows_lock_maps_setup_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DeniedHandle:
        def __enter__(self) -> DeniedHandle:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def seek(self, offset: int) -> None:
            del offset

        def read(self, size: int) -> bytes:
            del size
            raise PermissionError("locked by another Windows handle")

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: DeniedHandle())
    monkeypatch.setattr(_wakeup_locking.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace())

    with pytest.raises(LockUnavailable):
        with locked_file(tmp_path / "queue.lock", blocking=False):
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


@pytest.mark.parametrize(
    ("provider", "documented_name", "legacy_name"),
    [
        ("claude", "CLAUDE_EXECUTABLE", "LLM_WAKEUP_CLAUDE_BIN"),
        ("codex", "CODEX_EXECUTABLE", "LLM_WAKEUP_CODEX_BIN"),
    ],
)
def test_documented_provider_executable_overrides_are_honored(
    provider: str,
    documented_name: str,
    legacy_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(legacy_name, raising=False)
    monkeypatch.setenv(documented_name, "/documented/provider")

    assert provider_for(provider).executable_override() == "/documented/provider"


def test_doctor_rejects_an_unavailable_configured_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript_root = tmp_path / "claude"
    transcript_root.mkdir()
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(transcript_root))
    monkeypatch.setenv("CLAUDE_EXECUTABLE", str(tmp_path / "missing-claude"))

    diagnostic = next(
        item
        for item in collect_diagnostics()
        if item.name == "provider:claude"
    )

    assert not diagnostic.ok
    assert "configured executable unavailable" in diagnostic.detail


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


def _directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        # famulus-skip: category=capability-unavailable; reason=the host cannot create directory symlinks; alternate=ordinary auto-status tests cover the same read-only storage behavior without symlink traversal
        pytest.skip(f"directory symlinks are unavailable: {error}")


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


@pytest.mark.parametrize(
    ("initial_contents", "expected_state"),
    [
        (None, "disabled"),
        ('{"claude:11111111-2222-4333-8444-555555555555":{"auto_schedule":true}}\n', "enabled"),
    ],
)
def test_cli_auto_status_leaves_policy_storage_unchanged(
    initial_contents: str | None,
    expected_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    state_root = tmp_path / "state"
    policy_path = state_root / "session-policies.json"
    before: dict[str, bytes] | None = None
    if initial_contents is not None:
        state_root.mkdir(parents=True)
        policy_path.write_text(initial_contents, encoding="utf-8")
        (state_root / "unrelated-state.bin").write_bytes(b"preserve exactly\x00")
        before = {
            str(path.relative_to(state_root)): path.read_bytes()
            for path in state_root.rglob("*")
            if path.is_file()
        }

    assert main(["auto", "status", "claude", session_id]) == 0

    assert expected_state in capsys.readouterr().out
    if initial_contents is None:
        assert not state_root.exists()
    else:
        after = {
            str(path.relative_to(state_root)): path.read_bytes()
            for path in state_root.rglob("*")
            if path.is_file()
        }
        assert after == before


def test_cli_auto_status_preserves_non_directory_state_root_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    state_root = tmp_path / "state"
    state_root.write_bytes(b"not a directory")
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state_root))

    with pytest.raises(FileExistsError):
        main(["auto", "status", "claude", session_id])

    assert state_root.read_bytes() == b"not a directory"


def test_cli_auto_status_reads_through_directory_symlink_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    target_root = tmp_path / "real-state"
    target_root.mkdir()
    (target_root / "session-policies.json").write_text(
        f'{{"claude:{session_id}":{{"auto_schedule":true}}}}\n',
        encoding="utf-8",
    )
    (target_root / "unrelated-state.bin").write_bytes(b"preserve exactly\x00")
    state_root = tmp_path / "state"
    _directory_symlink_or_skip(state_root, target_root)
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state_root))
    before = {
        str(path.relative_to(target_root)): path.read_bytes()
        for path in target_root.rglob("*")
        if path.is_file()
    }

    assert main(["auto", "status", "claude", session_id]) == 0

    assert "enabled" in capsys.readouterr().out
    after = {
        str(path.relative_to(target_root)): path.read_bytes()
        for path in target_root.rglob("*")
        if path.is_file()
    }
    assert state_root.is_symlink()
    assert after == before


def test_cli_auto_status_preserves_dangling_state_root_symlink_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    _claude_transcript(tmp_path / "claude", session_id, tmp_path)
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    missing_target = tmp_path / "missing-state"
    state_root = tmp_path / "state"
    _directory_symlink_or_skip(state_root, missing_target)
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state_root))

    with pytest.raises(FileExistsError):
        main(["auto", "status", "claude", session_id])

    assert state_root.is_symlink()
    assert not missing_target.exists()


def test_auto_scheduled_sessions_does_not_create_policy_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state_root))

    assert auto_scheduled_sessions("claude") == ()
    assert not state_root.exists()


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_due_worker_delivers_through_each_provider_adapter(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        # famulus-skip: category=platform-contract; reason=this adapter integration fixture is a POSIX shell executable; alternate=provider command construction tests cover Windows-safe argv generation
        pytest.skip("POSIX provider fixture")
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
        None,
    )
    run_due()

    assert DEFAULT_MESSAGE in output.read_text()
    assert json.loads((tmp_path / "state" / "jobs.json").read_text()) == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_due_worker_suppresses_delivery_after_session_progress(
    provider: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "11111111-2222-4333-8444-555555555555"
    if provider == "claude":
        transcript = _claude_transcript(tmp_path / "claude", session_id, tmp_path)
        progress = {
            "type": "assistant",
            "sessionId": session_id,
            "message": {"role": "assistant", "content": "continued"},
        }
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
        progress = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": "continued",
            },
        }
        monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_NOW", "2026-08-02T12:00:00+00:00")
    schedule(
        provider,
        session_id,
        datetime(2026, 8, 2, 11, 59, tzinfo=timezone.utc),
        DEFAULT_MESSAGE,
    )
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(progress) + "\n")

    run_due()

    assert json.loads((tmp_path / "state" / "jobs.json").read_text()) == []
