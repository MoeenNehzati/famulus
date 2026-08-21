"""Tests for refusal evidence and the conditional wakeup level it gates.

Every provider record below is the shape observed on disk rather than an
invented one. The percentage-based fixtures that used to stand in for a
usage limit described a state that does not coincide with being stopped: over
2308 local Codex rollouts, ``used_percent: 100`` appeared in 162 files of
which 136 were never refused a turn.

Counts cited here and in the modules under test come from one local corpus
surveyed on 2026-08-19. They record what the providers did then; they are not
invariants, and a provider is free to change its format.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from officina.wakeup.claude_codex_cli import main
from officina.wakeup.claude_codex_cutoff import detect_cutoff
from officina.wakeup.claude_codex_monitor import monitor_usage
from officina.wakeup.claude_codex_service import run_due, schedule
from officina.wakeup.policies import (
    FORCE,
    INTERRUPTED,
    auto_schedule_level,
    set_auto_schedule,
)


CLAUDE_SESSION = "11111111-2222-4333-8444-555555555555"
CODEX_SESSION = "22222222-3333-4444-8555-666666666666"
RESET_EPOCH = 1_787_173_200  # 2026-08-19T21:00:00Z


@pytest.fixture(autouse=True)
def isolate_live_state(tmp_path: Path, monkeypatch) -> None:
    """Keep every test away from the developer's live sessions and queue."""

    monkeypatch.setenv("LLM_WAKEUP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LLM_WAKEUP_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("LLM_WAKEUP_CODEX_DIR", str(tmp_path / "codex"))


def _write(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


def _claude_turn(text: str, role: str = "assistant") -> dict:
    return {
        "type": role,
        "sessionId": CLAUDE_SESSION,
        "timestamp": "2026-08-19T18:20:00.000Z",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


def _claude_refusal(*, quota: bool = True) -> dict:
    """Return Claude's synthetic 429 row, optionally with quotaLimits.

    ``quotaLimits`` carries the authoritative reset epoch but arrived only in
    CLI 2.1.235; it is absent from 51 of the 52 refusals recorded locally, so
    both shapes have to keep working.
    """

    event = {
        "type": "assistant",
        "sessionId": CLAUDE_SESSION,
        "timestamp": "2026-08-19T18:25:30.690Z",
        "cwd": "/home/user/project",
        "message": {
            "model": "<synthetic>",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "You've hit your session limit · resets 5pm (America/New_York)",
                }
            ],
        },
        "error": "rate_limit",
        "isApiErrorMessage": True,
        "apiErrorStatus": 429,
    }
    if quota:
        event["quotaLimits"] = {
            "status": "rejected",
            "resetsAt": RESET_EPOCH,
            "rateLimitType": "five_hour",
        }
    return event


def _codex_refusal(*, completed_at: int = 1_787_176_807) -> dict:
    return {
        "timestamp": "2026-08-19T22:00:07.132Z",
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "last_agent_message": None,
            "error": {
                "message": (
                    "You've hit your usage limit. Visit "
                    "https://chatgpt.com/codex/settings/usage to purchase more "
                    "credits or try again at Aug 20th, 2026 12:16 PM."
                ),
                "codex_error_info": "usage_limit_exceeded",
            },
            "started_at": 1_787_176_804,
            "completed_at": completed_at,
        },
    }


def _codex_message(text: str) -> dict:
    return {
        "timestamp": "2026-08-19T22:30:00.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def test_claude_refusal_prefers_the_authoritative_reset_epoch(tmp_path: Path) -> None:
    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_turn("do the work", role="user"), _claude_refusal()],
    )

    cut = detect_cutoff("claude", transcript, CLAUDE_SESSION)

    assert cut is not None
    assert cut.reset_at == datetime.fromtimestamp(RESET_EPOCH, timezone.utc)
    assert cut.abandoned
    assert cut.wakeable


def test_claude_refusal_without_quota_limits_falls_back_to_the_message(
    tmp_path: Path,
) -> None:
    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_turn("do the work", role="user"), _claude_refusal(quota=False)],
    )

    cut = detect_cutoff("claude", transcript, CLAUDE_SESSION)

    assert cut is not None
    assert cut.reset_at == datetime.fromtimestamp(RESET_EPOCH, timezone.utc)


def test_a_session_that_kept_working_after_the_refusal_is_not_abandoned(
    tmp_path: Path,
) -> None:
    """The refusal row is itself an assistant turn, so it is not progress."""

    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [
            _claude_turn("do the work", role="user"),
            _claude_refusal(),
            _claude_turn("resumed by hand", role="user"),
            _claude_turn("finished it"),
        ],
    )

    cut = detect_cutoff("claude", transcript, CLAUDE_SESSION)

    assert cut is not None
    assert not cut.abandoned
    assert not cut.wakeable


def test_claude_arming_its_own_resume_suppresses_the_wakeup(tmp_path: Path) -> None:
    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [
            _claude_turn("do the work", role="user"),
            _claude_refusal(),
            {
                "type": "system",
                "subtype": "informational",
                "level": "notice",
                "content": "Usage limit reached · continuing automatically at 5pm · esc or type to cancel",
                "timestamp": "2026-08-19T18:25:30.692Z",
            },
        ],
    )

    cut = detect_cutoff("claude", transcript, CLAUDE_SESSION)

    assert cut is not None
    assert cut.abandoned
    assert cut.self_continuing
    assert not cut.wakeable


def test_claude_giving_up_on_its_own_resume_restores_the_wakeup(
    tmp_path: Path,
) -> None:
    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [
            _claude_turn("do the work", role="user"),
            _claude_refusal(),
            {
                "type": "system",
                "subtype": "informational",
                "level": "warning",
                "content": (
                    "Auto-continue stopped after repeated usage-limit hits · this "
                    "task will not resume on its own (/rate-limit-options to try again)"
                ),
                "timestamp": "2026-08-19T18:25:31.000Z",
            },
        ],
    )

    cut = detect_cutoff("claude", transcript, CLAUDE_SESSION)

    assert cut is not None
    assert not cut.self_continuing
    assert cut.wakeable


def test_codex_refusal_reads_its_reset_from_local_time_prose(tmp_path: Path) -> None:
    """Codex states the reset only as prose, in the machine's local zone.

    Cross-checked against five local refusals whose files also carried a
    numeric ``resets_at``: every one matched the prose to the minute.
    """

    transcript = _write(
        tmp_path / "codex" / f"rollout-{CODEX_SESSION}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": CODEX_SESSION}},
            _codex_message("working"),
            _codex_refusal(),
        ],
    )

    cut = detect_cutoff("codex", transcript, CODEX_SESSION)

    assert cut is not None
    assert cut.reset_at == datetime(2026, 8, 20, 12, 16).astimezone(timezone.utc)
    assert cut.abandoned


def test_a_forked_rollout_replaying_its_parent_is_not_a_fresh_refusal(
    tmp_path: Path,
) -> None:
    """Forks copy history under new wall-clock stamps but keep completed_at.

    32 of the 70 local ``usage_limit_exceeded`` records are such copies. Read
    naively they would schedule a wakeup for a limit that lapsed days ago.
    """

    transcript = _write(
        tmp_path / "codex" / f"rollout-{CODEX_SESSION}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": CODEX_SESSION}},
            _codex_refusal(completed_at=1_780_000_000),
        ],
    )

    assert detect_cutoff("codex", transcript, CODEX_SESSION) is None


def test_a_fully_consumed_codex_window_is_not_a_refusal(tmp_path: Path) -> None:
    transcript = _write(
        tmp_path / "codex" / f"rollout-{CODEX_SESSION}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": CODEX_SESSION}},
            {
                "timestamp": "2026-08-19T21:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 100.0, "resets_at": RESET_EPOCH},
                        "secondary": {"used_percent": 74.0, "resets_at": RESET_EPOCH},
                    },
                },
            },
            _codex_message("still working, still allowed"),
        ],
    )

    assert detect_cutoff("codex", transcript, CODEX_SESSION) is None


def test_a_policy_written_before_levels_existed_reads_as_conditional(
    tmp_path: Path,
) -> None:
    """Unconditional waking is what levels were introduced to stop."""

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "session-policies.json").write_text(
        json.dumps({f"claude:{CLAUDE_SESSION}": {"auto_schedule": True}}) + "\n"
    )

    assert auto_schedule_level("claude", CLAUDE_SESSION) == INTERRUPTED


def test_auto_force_records_the_unconditional_level(tmp_path: Path) -> None:
    transcript = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_turn("work", role="user")],
    )
    assert transcript.exists()

    assert main(["auto", "force", "claude", CLAUDE_SESSION]) == 0
    assert auto_schedule_level("claude", CLAUDE_SESSION) == FORCE

    assert main(["auto", "on", "claude", CLAUDE_SESSION]) == 0
    assert auto_schedule_level("claude", CLAUDE_SESSION) == INTERRUPTED


def test_monitor_schedules_a_conditional_session_only_once_it_was_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl"
    _write(path, [_claude_turn("do the work", role="user")])
    set_auto_schedule("claude", CLAUDE_SESSION, True, INTERRUPTED)
    now = datetime(2026, 8, 19, 18, 30, tzinfo=timezone.utc)

    def _fail(message: str) -> None:
        pytest.fail(f"a conditional session must not be nagged: {message}")

    assert monitor_usage(now=now, notifier=_fail) == []

    _write(path, [_claude_turn("do the work", role="user"), _claude_refusal()])
    actions = monitor_usage(now=now, notifier=_fail)

    assert [(action.kind, action.session_id) for action in actions] == [
        ("scheduled", CLAUDE_SESSION)
    ]
    assert actions[0].used_percentage is None
    jobs = json.loads((tmp_path / "state" / "jobs.json").read_text())
    assert [job["level"] for job in jobs] == [INTERRUPTED]

    # The same refusal must not schedule a second job on the next pass.
    assert monitor_usage(now=now, notifier=_fail) == []


def test_monitor_ignores_a_refusal_the_session_already_recovered_from(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [
            _claude_turn("do the work", role="user"),
            _claude_refusal(),
            _claude_turn("resumed by hand", role="user"),
        ],
    )
    set_auto_schedule("claude", CLAUDE_SESSION, True, INTERRUPTED)

    actions = monitor_usage(
        now=datetime(2026, 8, 19, 18, 30, tzinfo=timezone.utc),
        notifier=lambda _message: None,
    )

    assert actions == []


def test_delivery_drops_a_conditional_job_whose_evidence_no_longer_holds(
    tmp_path: Path, monkeypatch
) -> None:
    """The snapshot guard cannot see this on its own.

    A session refused, resumed, and refused again ends on the same last
    meaningful event it was scheduled with, so the state hash still matches
    while the reason for waking has changed.
    """

    path = _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_turn("do the work", role="user"), _claude_refusal()],
    )
    job = schedule(
        "claude",
        CLAUDE_SESSION,
        datetime(2026, 8, 19, 21, 1, tzinfo=timezone.utc),
        None,
        transcript_path=path,
        level=INTERRUPTED,
    )
    assert job["level"] == INTERRUPTED

    # Claude armed its own resume after the job was queued.
    _write(
        path,
        [
            _claude_turn("do the work", role="user"),
            _claude_refusal(),
            {
                "type": "system",
                "subtype": "informational",
                "content": "Usage limit reached · continuing automatically at 5pm",
                "timestamp": "2026-08-19T18:25:30.692Z",
            },
        ],
    )

    def _never_run(*args, **kwargs):
        pytest.fail("a superseded wakeup must not resume the provider")

    monkeypatch.setattr("officina.wakeup.claude_codex_service.subprocess.run", _never_run)
    monkeypatch.setenv("LLM_WAKEUP_NOW", "2026-08-19T21:02:00+00:00")

    run_due()

    assert json.loads((tmp_path / "state" / "jobs.json").read_text()) == []


def test_a_forced_session_is_woken_by_evidence_as_well_as_by_percentage(
    tmp_path: Path,
) -> None:
    """`force` is a superset, not a different trigger.

    A rejection can arrive while reported utilization is well below the
    near-limit threshold, and Claude instruments that case explicitly. A forced
    session that is refused without ever crossing 90% must still be woken.
    """

    _write(
        tmp_path / "claude" / "p" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_turn("do the work", role="user"), _claude_refusal()],
    )
    set_auto_schedule("claude", CLAUDE_SESSION, True, FORCE)

    actions = monitor_usage(
        now=datetime(2026, 8, 19, 18, 30, tzinfo=timezone.utc),
        notifier=lambda _message: None,
    )

    assert [(action.kind, action.session_id) for action in actions] == [
        ("scheduled", CLAUDE_SESSION)
    ]
    jobs = json.loads((tmp_path / "state" / "jobs.json").read_text())
    assert [job["level"] for job in jobs] == [FORCE]
