"""Behavioral tests for provider-neutral usage monitoring."""

from __future__ import annotations

import json
import io
import sys
import base64
from datetime import datetime, timezone
from pathlib import Path

import pytest

from officina.wakeup.claude_codex_monitor import monitor_usage
from officina.wakeup.claude_codex_cli import main
from officina.wakeup.deadlines import parse_deadline
from officina.wakeup.policies import FORCE, set_auto_schedule
from officina.wakeup.claude_codex_usage import (
    capture_claude_status,
    read_claude_exhaustion,
    read_codex_usage,
)


SESSION_ID = "11111111-2222-4333-8444-555555555555"


RESET_EPOCH = 1_786_294_800


@pytest.fixture(autouse=True)
def isolate_live_transcript_roots(tmp_path: Path, monkeypatch) -> None:
    """Keep monitor tests independent of the developer's live sessions."""

    monkeypatch.setenv(
        "LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "live-transcripts" / "claude")
    )
    monkeypatch.setenv(
        "LLM_WAKEUP_CODEX_DIR", str(tmp_path / "live-transcripts" / "codex")
    )


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


def test_claude_status_capture_persists_normalized_rate_limits(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    transcript = tmp_path / "claude" / "project" / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )

    snapshots = capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 91,
                    "resets_at": RESET_EPOCH,
                },
                "seven_day": {
                    "used_percentage": 34,
                    "resets_at": RESET_EPOCH + 86_400,
                },
            },
        }
    )

    assert [(item.window, item.used_percentage) for item in snapshots] == [
        ("five_hour", 91.0),
        ("seven_day", 34.0),
    ]
    records = [
        json.loads(path.read_text())
        for path in (tmp_path / "state" / "usage-snapshots").glob("*.json")
    ]
    five_hour = next(record for record in records if record["window"] == "five_hour")
    assert five_hour["resets_at"] == RESET_EPOCH


def test_codex_transcript_reader_normalizes_primary_and_secondary_limits(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / f"rollout-{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "session_meta", "payload": {"id": SESSION_ID}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 92,
                            "resets_at": RESET_EPOCH,
                        },
                        "secondary": {
                            "used_percent": 48,
                            "resets_at": RESET_EPOCH + 604_800,
                        },
                    },
                },
            },
        ],
    )

    snapshots = read_codex_usage(transcript, SESSION_ID)

    assert [(item.window, item.used_percentage) for item in snapshots] == [
        ("primary", 92.0),
        ("secondary", 48.0),
    ]


def test_codex_reader_uses_newest_valid_quota_record(tmp_path: Path) -> None:
    transcript = tmp_path / f"rollout-{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 20, "resets_at": RESET_EPOCH}
                    },
                },
            },
            {"type": "event_msg", "payload": {"type": "token_count"}},
            {"truncated": "not-json"},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 88, "resets_at": RESET_EPOCH}
                    },
                },
            },
        ],
    )

    snapshots = read_codex_usage(transcript, SESSION_ID)

    assert len(snapshots) == 1
    assert snapshots[0].used_percentage == 88


def test_deadline_parser_accepts_claude_dated_and_weekday_resets() -> None:
    now = datetime(2026, 8, 3, 15, 36, tzinfo=timezone.utc)

    dated = parse_deadline(
        "You've hit your weekly limit - resets Aug 6, 1am (America/New_York)",
        now=now,
        embedded=True,
    )
    weekday = parse_deadline(
        "weekly limit resets Thu 1am (America/New_York)",
        now=now,
        embedded=True,
    )

    expected = datetime(2026, 8, 6, 5, 0, tzinfo=timezone.utc)
    assert dated == expected
    assert weekday == expected


def test_claude_exhaustion_reader_normalizes_live_dated_reset(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "sessionId": SESSION_ID,
                "timestamp": "2026-08-03T15:36:00.215Z",
                "error": "rate_limit",
                "isApiErrorMessage": True,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "You've hit your weekly limit - resets Aug 6, 1am (America/New_York)",
                        }
                    ]
                },
            }
        ],
    )

    snapshot = read_claude_exhaustion(transcript, SESSION_ID)

    assert snapshot is not None
    assert snapshot.used_percentage == 100
    assert snapshot.resets_at == int(
        datetime(2026, 8, 6, 5, tzinfo=timezone.utc).timestamp()
    )


def test_monitor_schedules_auto_claude_session_from_exhaustion_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    transcript_root = tmp_path / "claude"
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(transcript_root))
    transcript = transcript_root / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "sessionId": SESSION_ID,
                "timestamp": "2026-08-03T15:36:00.215Z",
                "error": "rate_limit",
                "isApiErrorMessage": True,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "You've hit your weekly limit - resets Aug 6, 1am (America/New_York)",
                        }
                    ]
                },
            }
        ],
    )
    set_auto_schedule("claude", SESSION_ID, True, FORCE)

    actions = monitor_usage(
        now=datetime(2026, 8, 3, 16, tzinfo=timezone.utc),
        notifier=lambda _message: None,
    )

    assert [(action.kind, action.session_id) for action in actions] == [
        ("scheduled", SESSION_ID)
    ]
    jobs = json.loads((state / "jobs.json").read_text())
    expected = datetime(2026, 8, 6, 5, 1, tzinfo=timezone.utc)
    assert datetime.fromisoformat(jobs[0]["run_at"]) == expected


def test_monitor_schedules_auto_session_once_and_reminds_manual_session_once(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state))
    # This machine's own near-limit sessions are discovered from the real
    # Claude projects directory, so without this the assertions below depend on
    # whether the developer running them happens to be near a usage limit.
    import officina.wakeup.claude_codex_usage as usage

    monkeypatch.setattr(usage, "_observable_claude_exhaustions", lambda: [])
    transcript = tmp_path / "claude" / "project" / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 95,
                    "resets_at": RESET_EPOCH,
                }
            },
        }
    )
    now = datetime.fromtimestamp(RESET_EPOCH - 600, tz=timezone.utc)
    notices: list[str] = []

    first = monitor_usage(now=now, notifier=notices.append)
    second = monitor_usage(now=now, notifier=notices.append)

    assert [action.kind for action in first] == ["reminded"]
    assert second == []
    assert len(notices) == 1

    second_session = "22222222-3333-4444-8555-666666666666"
    second_transcript = tmp_path / "claude" / "project" / f"{second_session}.jsonl"
    _write_jsonl(
        second_transcript,
        [{"type": "user", "sessionId": second_session, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": second_session,
            "transcript_path": str(second_transcript),
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 96,
                    "resets_at": RESET_EPOCH,
                }
            },
        }
    )
    set_auto_schedule("claude", second_session, True, FORCE)

    actions = monitor_usage(now=now, notifier=notices.append)

    assert [(action.kind, action.session_id) for action in actions] == [
        ("scheduled", second_session)
    ]
    jobs = json.loads((state / "jobs.json").read_text())
    job = next(item for item in jobs if item["session_id"] == second_session)
    expected = datetime.fromtimestamp(RESET_EPOCH + 60, tz=timezone.utc)
    assert datetime.fromisoformat(job["run_at"]) == expected


def test_monitor_ignores_below_threshold_and_expired_windows(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    # This machine's own near-limit sessions are discovered from the real
    # Claude projects directory, so without this the assertions below depend on
    # whether the developer running them happens to be near a usage limit.
    import officina.wakeup.claude_codex_usage as usage

    monkeypatch.setattr(usage, "_observable_claude_exhaustions", lambda: [])
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {"used_percentage": 89, "resets_at": RESET_EPOCH},
                "seven_day": {
                    "used_percentage": 100,
                    "resets_at": RESET_EPOCH - 1_000,
                },
            },
        }
    )

    actions = monitor_usage(
        now=datetime.fromtimestamp(RESET_EPOCH - 500, tz=timezone.utc),
        notifier=lambda _message: pytest.fail("unexpected reminder"),
    )

    assert actions == []


def test_monitor_retries_when_notification_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    # This machine's own near-limit sessions are discovered from the real
    # Claude projects directory, so without this the assertions below depend on
    # whether the developer running them happens to be near a usage limit.
    import officina.wakeup.claude_codex_usage as usage

    monkeypatch.setattr(usage, "_observable_claude_exhaustions", lambda: [])
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {"used_percentage": 95, "resets_at": RESET_EPOCH}
            },
        }
    )
    now = datetime.fromtimestamp(RESET_EPOCH - 500, tz=timezone.utc)

    with pytest.raises(RuntimeError, match="notification failed"):
        monitor_usage(
            now=now,
            notifier=lambda _message: (_ for _ in ()).throw(
                RuntimeError("notification failed")
            ),
        )

    notices: list[str] = []
    actions = monitor_usage(now=now, notifier=notices.append)
    assert [action.kind for action in actions] == ["reminded"]
    assert len(notices) == 1


def test_monitor_uses_latest_reset_across_near_limit_windows(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state))
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {"used_percentage": 92, "resets_at": RESET_EPOCH},
                "seven_day": {
                    "used_percentage": 94,
                    "resets_at": RESET_EPOCH + 86_400,
                },
            },
        }
    )
    set_auto_schedule("claude", SESSION_ID, True, FORCE)

    monitor_usage(
        now=datetime.fromtimestamp(RESET_EPOCH - 500, tz=timezone.utc),
        notifier=lambda _message: None,
    )

    jobs = json.loads((state / "jobs.json").read_text())
    expected = datetime.fromtimestamp(RESET_EPOCH + 86_460, tz=timezone.utc)
    assert datetime.fromisoformat(jobs[0]["run_at"]) == expected


def test_cli_captures_claude_status_line_payload(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    payload = {
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / f"{SESSION_ID}.jsonl"),
        "rate_limits": {
            "five_hour": {
                "used_percentage": 93,
                "resets_at": RESET_EPOCH,
            }
        },
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert main(["capture-claude-usage"]) == 0

    output = capsys.readouterr().out
    assert "5h 93%" in output
    assert "lw auto on claude" in output


def test_claude_capture_preserves_an_existing_status_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    payload = {
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / f"{SESSION_ID}.jsonl"),
        "rate_limits": {
            "five_hour": {
                "used_percentage": 25,
                "resets_at": RESET_EPOCH,
            }
        },
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert main(
        ["capture-claude-usage", "--chain-command", "printf existing-status"]
    ) == 0

    assert capsys.readouterr().out == "existing-status\nClaude usage: 5h 25%\n"


def test_claude_capture_accepts_encoded_existing_status_command(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    payload = {
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / f"{SESSION_ID}.jsonl"),
        "rate_limits": {},
    }
    encoded = base64.b64encode(b"printf encoded-status").decode("ascii")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert main(
        ["capture-claude-usage", "--chain-command-base64", encoded]
    ) == 0

    assert capsys.readouterr().out == "encoded-status\n"


def test_run_due_worker_performs_monitor_pass_before_delivery(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("LLM_WAKEUP_HOME", str(state))
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user", "sessionId": SESSION_ID, "message": {"content": "work"}}],
    )
    capture_claude_status(
        {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 97,
                    "resets_at": RESET_EPOCH,
                }
            },
        }
    )
    set_auto_schedule("claude", SESSION_ID, True, FORCE)

    # Two things must be pinned or this test reaches the real desktop. It
    # discovered this machine's live near-limit sessions, and it left `notifier`
    # unset, which falls through to _default_notifier and its notify-send call
    # -- so every run of the suite raised a real popup about whatever session
    # the developer happened to be in.
    import officina.wakeup.claude_codex_usage as usage

    monkeypatch.setattr(usage, "_observable_claude_exhaustions", lambda: [])
    monkeypatch.setattr(
        "officina.wakeup.claude_codex_cli.monitor_usage",
        lambda: monitor_usage(
            now=datetime.fromtimestamp(RESET_EPOCH - 500, tz=timezone.utc),
            notifier=lambda _message: None,
        ),
    )

    assert main(["run-due"]) == 0

    jobs = json.loads((state / "jobs.json").read_text())
    assert len(jobs) == 1
    assert jobs[0]["session_id"] == SESSION_ID


def _snapshot(window: str, percentage: float, reset: int, transcript: Path):
    """Build one usage snapshot directly, bypassing provider discovery.

    Discovery reads exhaustion events only for the latest or auto-enabled
    session, from the real Claude projects directory. Driving monitor_usage
    through it would test the reader, not the deduplication these cases are
    about.
    """
    from officina.wakeup.claude_codex_usage import UsageSnapshot

    return UsageSnapshot(
        provider="claude",
        session_id=SESSION_ID,
        window=window,
        used_percentage=percentage,
        resets_at=reset,
        transcript_path=str(transcript),
        observed_at=datetime.fromtimestamp(reset - 900, tz=timezone.utc).isoformat(),
    )


def test_monitor_notifies_once_when_a_second_window_crosses(
    tmp_path: Path, monkeypatch
) -> None:
    """One session near its limit is one popup, however many windows cross.

    The deduplication key used to include the set of near-limit windows, so a
    session notified for `five_hour` was notified again the minute it also
    crossed `exhausted` -- same session, same reset, two popups a minute apart.
    Observed live: 6cdc732a was reminded at 21:05 for `five_hour` and again at
    21:06 for `exhausted,five_hour`.
    """
    import officina.wakeup.claude_codex_monitor as monitor

    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(transcript, [{"type": "user", "sessionId": SESSION_ID}])
    reset = RESET_EPOCH
    now = datetime.fromtimestamp(reset - 500, tz=timezone.utc)

    visible = [_snapshot("five_hour", 92, reset, transcript)]
    monkeypatch.setattr(
        monitor, "observable_usage_snapshots", lambda: list(visible)
    )

    notices: list[str] = []
    first = monitor.monitor_usage(now=now, notifier=notices.append)

    # The same session then hits the hard limit: a second near-limit window
    # appears with the same reset. Nothing new has happened to the user.
    visible.append(_snapshot("exhausted", 100, reset, transcript))
    second = monitor.monitor_usage(now=now, notifier=notices.append)

    assert [action.kind for action in first] == ["reminded"]
    assert second == []
    assert len(notices) == 1


def test_monitor_never_notifies_a_session_with_auto_scheduling_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    """An opted-in session is scheduled silently, across repeated passes."""
    import officina.wakeup.claude_codex_monitor as monitor

    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    _write_jsonl(transcript, [{"type": "user", "sessionId": SESSION_ID}])
    reset = RESET_EPOCH
    now = datetime.fromtimestamp(reset - 500, tz=timezone.utc)

    # Injected rather than discovered: discovery reads this machine's real
    # Claude transcripts, so a live session near its own limit would leak in
    # and add actions this test never asked about.
    visible = [_snapshot("five_hour", 92, reset, transcript)]
    monkeypatch.setattr(
        monitor, "observable_usage_snapshots", lambda: list(visible)
    )
    set_auto_schedule("claude", SESSION_ID, True, FORCE)

    def _fail(message: str) -> None:
        pytest.fail(f"auto-enabled session must not notify: {message}")

    first = monitor.monitor_usage(now=now, notifier=_fail)
    visible.append(_snapshot("exhausted", 100, reset, transcript))
    second = monitor.monitor_usage(now=now, notifier=_fail)

    assert [action.kind for action in first] == ["scheduled"]
    assert second == []
