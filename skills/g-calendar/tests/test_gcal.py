from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "g_calendar_gcal",
    Path(__file__).resolve().parents[1] / "scripts" / "gcal.py",
)
gcal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gcal)
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
GCAL_SHELL = SCRIPT_DIR / "gcal.sh"


def test_resolve_range_defaults_from_local_midnight(monkeypatch):
    base = datetime(2026, 7, 9, 15, 30, tzinfo=timezone(timedelta(hours=-4)))
    monkeypatch.setattr(gcal, "local_midnight", lambda: base.replace(hour=0, minute=0, second=0, microsecond=0))

    from_, to = gcal._resolve_range(None, None, "3")

    assert from_ == "2026-07-09T00:00:00-04:00"
    assert to == "2026-07-12T00:00:00-04:00"


def test_list_events_all_merges_sorts_and_keeps_calendar_metadata(monkeypatch):
    monkeypatch.setattr(gcal, "get_access_token", lambda: "token")

    def fake_api_call(method, path, body=None, *, token=None):
        assert method == "GET"
        assert path == "/users/me/calendarList"
        assert token == "token"
        return {
            "items": [
                {"id": "work", "summary": "Work"},
                {"id": "personal", "summary": "Personal"},
                {"id": "broken", "summary": "Broken"},
            ]
        }

    def fake_list_events(cal, tmin, tmax, query="", *, token=None):
        assert token == "token"
        if cal == "work":
            return {
                "items": [
                    {
                        "id": "2",
                        "summary": "Standup",
                        "start": {"dateTime": "2026-07-09T10:00:00-04:00"},
                        "end": {"dateTime": "2026-07-09T10:15:00-04:00"},
                    }
                ]
            }
        if cal == "personal":
            return {
                "items": [
                    {
                        "id": "1",
                        "summary": "Breakfast",
                        "start": {"dateTime": "2026-07-09T08:00:00-04:00"},
                        "end": {"dateTime": "2026-07-09T08:30:00-04:00"},
                    }
                ]
            }
        raise SystemExit(1)

    monkeypatch.setattr(gcal, "api_call", fake_api_call)
    monkeypatch.setattr(gcal, "list_events", fake_list_events)

    items = gcal.list_events_all(
        "2026-07-09T00:00:00-04:00",
        "2026-07-10T00:00:00-04:00",
    )

    assert [item["id"] for item in items] == ["1", "2"]
    assert items[0]["_cal"] == "personal"
    assert items[0]["_calName"] == "Personal"
    assert items[1]["_cal"] == "work"
    assert items[1]["_calName"] == "Work"


def test_list_events_all_returns_empty_without_spawning_workers(monkeypatch):
    monkeypatch.setattr(gcal, "get_access_token", lambda: "token")
    monkeypatch.setattr(
        gcal,
        "api_call",
        lambda method, path, body=None, *, token=None: {"items": []},
    )

    class UnexpectedExecutor:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("ThreadPoolExecutor should not be constructed for an empty calendar list")

    monkeypatch.setattr(gcal, "ThreadPoolExecutor", UnexpectedExecutor)

    assert gcal.list_events_all("2026-07-09T00:00:00-04:00", "2026-07-10T00:00:00-04:00") == []


def test_list_events_all_caps_worker_count(monkeypatch):
    monkeypatch.setattr(gcal, "get_access_token", lambda: "token")

    monkeypatch.setattr(
        gcal,
        "api_call",
        lambda method, path, body=None, *, token=None: {
            "items": [{"id": f"cal-{i}", "summary": f"Calendar {i}"} for i in range(20)]
        },
    )
    monkeypatch.setattr(gcal, "list_events", lambda *args, **kwargs: {"items": []})

    seen = {}

    class FakeExecutor:
        def __init__(self, *, max_workers):
            seen["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def map(self, fn, iterable):
            return [fn(item) for item in iterable]

    monkeypatch.setattr(gcal, "ThreadPoolExecutor", FakeExecutor)

    gcal.list_events_all("2026-07-09T00:00:00-04:00", "2026-07-10T00:00:00-04:00")

    assert seen["max_workers"] == gcal.MAX_CALENDAR_WORKERS


def test_cmd_create_preserves_location_description_and_timezone(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(gcal, "get_timezone", lambda: "America/New_York")

    def fake_api_call(method, path, body=None, *, token=None):
        calls.append((method, path, body))
        return {"summary": "Meeting", "id": "evt-1", "htmlLink": "https://example.test/event"}

    monkeypatch.setattr(gcal, "api_call", fake_api_call)

    args = argparse.Namespace(
        calendar="primary",
        summary="Meeting",
        start="2026-07-09T13:00:00-04:00",
        end="2026-07-09T14:00:00-04:00",
        description="Discuss roadmap",
        location="Room 101",
        all_day=False,
        timezone=None,
    )

    gcal.cmd_create(args)

    assert calls == [
        (
            "POST",
            "/calendars/primary/events",
            {
                "summary": "Meeting",
                "start": {
                    "dateTime": "2026-07-09T13:00:00-04:00",
                    "timeZone": "America/New_York",
                },
                "end": {
                    "dateTime": "2026-07-09T14:00:00-04:00",
                    "timeZone": "America/New_York",
                },
                "description": "Discuss roadmap",
                "location": "Room 101",
            },
        )
    ]
    assert (
        capsys.readouterr().out
        == "Created: Meeting  [id: evt-1]\nhttps://example.test/event\n"
    )


def test_cmd_get_prints_retained_event_fields(monkeypatch, capsys):
    monkeypatch.setattr(
        gcal,
        "api_call",
        lambda method, path, body=None, *, token=None: {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-07-09T13:00:00-04:00"},
            "end": {"dateTime": "2026-07-09T14:00:00-04:00"},
            "location": "Room 101",
            "description": "Discuss roadmap",
            "status": "confirmed",
            "htmlLink": "https://example.test/event",
        },
    )

    gcal.cmd_get(argparse.Namespace(calendar="primary", event_id="evt-1"))

    assert capsys.readouterr().out == (
        "2026-07-09T13:00:00-04:00 -> 2026-07-09T14:00:00-04:00  "
        "Meeting  [calendar: primary, id: evt-1]\n"
        "Location: Room 101\n"
        "Description: Discuss roadmap\n"
        "Status: confirmed\n"
        "https://example.test/event\n"
    )


def test_cmd_update_patches_only_provided_fields(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(gcal, "get_timezone", lambda: "America/New_York")

    def fake_api_call(method, path, body=None, *, token=None):
        calls.append((method, path, body))
        return {"summary": "Updated meeting", "id": "evt-1", "htmlLink": "https://example.test/event"}

    monkeypatch.setattr(gcal, "api_call", fake_api_call)

    args = argparse.Namespace(
        calendar="primary",
        event_id="evt-1",
        summary="Updated meeting",
        description=None,
        location="Room 202",
        start=None,
        end="2026-07-09T15:00:00-04:00",
        timezone=None,
    )

    gcal.cmd_update(args)

    assert calls == [
        (
            "PATCH",
            "/calendars/primary/events/evt-1",
            {
                "summary": "Updated meeting",
                "location": "Room 202",
                "end": {
                    "dateTime": "2026-07-09T15:00:00-04:00",
                    "timeZone": "America/New_York",
                },
            },
        )
    ]
    assert (
        capsys.readouterr().out
        == "Updated: Updated meeting  [id: evt-1]\nhttps://example.test/event\n"
    )


def test_cmd_delete_reports_deleted_event(monkeypatch, capsys):
    calls = []

    def fake_api_call(method, path, body=None, *, token=None):
        calls.append((method, path, body))
        return None

    monkeypatch.setattr(gcal, "api_call", fake_api_call)

    gcal.cmd_delete(argparse.Namespace(calendar="work", event_id="evt-9"))

    assert calls == [("DELETE", "/calendars/work/events/evt-9", None)]
    assert capsys.readouterr().out == "Deleted event evt-9 from calendar work\n"


def test_cmd_move_uses_destination_query(monkeypatch, capsys):
    calls = []

    def fake_api_call(method, path, body=None, *, token=None):
        calls.append((method, path, body))
        return {"summary": "Meeting", "id": "evt-1"}

    monkeypatch.setattr(gcal, "api_call", fake_api_call)

    gcal.cmd_move(argparse.Namespace(from_="primary", event_id="evt-1", to="team/calendar"))

    assert calls == [
        (
            "POST",
            "/calendars/primary/events/evt-1/move?destination=team%2Fcalendar",
            None,
        )
    ]
    assert capsys.readouterr().out == "Moved: Meeting  [id: evt-1]  -> calendar team/calendar\n"


def test_gcal_shell_wrapper_preserves_help_surface():
    result = subprocess.run(
        [str(GCAL_SHELL), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage: gcal.sh" in result.stdout
    assert "{token,calendars,create-calendar,agenda,search,get,create,update,delete,move}" in result.stdout
    assert result.stderr == ""
