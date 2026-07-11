#!/usr/bin/env python3
"""Minimal Google Calendar CLI for the g-calendar skill.

This stdlib-only runtime replaces the previous shell implementation, whose
curl/jq/date dependencies were not guaranteed present on every platform. It
preserves the command/flag surface exposed through the scripts-gcal interface.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CREDS_FILE = Path.home() / ".config" / "g-calendar" / "credentials.json"
API_BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_CALENDAR_WORKERS = 8


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def get_timezone() -> str:
    """Best-effort local IANA timezone name; falls back to UTC."""
    env_tz = os.environ.get("TZ", "").strip()
    if env_tz:
        return env_tz

    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key

    zone = getattr(tzinfo, "zone", None)
    if isinstance(zone, str) and zone:
        return zone

    etc_tz = Path("/etc/timezone")
    if etc_tz.is_file():
        try:
            tz = etc_tz.read_text(encoding="utf-8").strip()
            if tz:
                return tz
        except OSError:
            pass

    return "UTC"


def local_midnight(base: datetime | None = None) -> datetime:
    now = base or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def get_access_token() -> str:
    if not CREDS_FILE.is_file():
        die(f"No credentials at {CREDS_FILE}. Run the setup-oauth interface first.")

    creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    data = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        die(
            "Failed to get access token. Response: "
            f"{detail}\nIf this says invalid_grant, re-run the setup-oauth interface."
        )
    except urllib.error.URLError as exc:
        die(f"Failed to get access token: {exc.reason}")

    token = str(payload.get("access_token", "")).strip()
    if not token:
        die(
            "Failed to get access token. Response: "
            f"{payload}\nIf this says invalid_grant, re-run the setup-oauth interface."
        )
    return token


def api_call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
) -> Any:
    """Call the Calendar API. Returns parsed JSON or None for empty responses."""
    token = token or get_access_token()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        die(f"API error (HTTP {exc.code}): {detail}")
    except urllib.error.URLError as exc:
        die(f"API error: {exc.reason}")

    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def urlpath_quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def urlquery(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params)


def list_events(
    cal: str,
    tmin: str,
    tmax: str,
    query: str = "",
    *,
    token: str | None = None,
) -> dict[str, Any]:
    params = {
        "timeMin": tmin,
        "timeMax": tmax,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "50",
    }
    if query:
        params["q"] = query
    path = f"/calendars/{urlpath_quote(cal)}/events?{urlquery(params)}"
    return api_call("GET", path, token=token) or {}


def format_event_line(item: dict[str, Any], cal_label: str) -> str:
    start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date", "")
    end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date", "")
    summary = item.get("summary") or "(no title)"
    return f"{start} -> {end}  {summary}  [calendar: {cal_label}, id: {item.get('id', '')}]"


def print_events(resp: dict[str, Any], cal: str) -> None:
    items = resp.get("items", [])
    if not items:
        print("(no events)")
        return
    for item in items:
        print(format_event_line(item, cal))


def print_events_multi(items: list[dict[str, Any]]) -> None:
    if not items:
        print("(no events)")
        return
    for item in items:
        print(format_event_line(item, item.get("_calName", "")))


def list_events_all(from_: str, to: str, query: str = "") -> list[dict[str, Any]]:
    """Fetch events from every calendar in parallel and merge the results."""
    token = get_access_token()
    cal_list = (api_call("GET", "/users/me/calendarList", token=token) or {}).get("items", [])
    if not cal_list:
        return []

    def fetch(cal: dict[str, Any]) -> list[dict[str, Any]]:
        cal_id = cal.get("id", "")
        cal_name = cal.get("summary", "")
        if not cal_id:
            return []
        try:
            resp = list_events(cal_id, from_, to, query, token=token)
        except SystemExit:
            return []
        items = resp.get("items", []) or []
        for item in items:
            item["_cal"] = cal_id
            item["_calName"] = cal_name
        return items

    with ThreadPoolExecutor(max_workers=min(len(cal_list), MAX_CALENDAR_WORKERS)) as pool:
        results = list(pool.map(fetch, cal_list))

    merged = [item for batch in results for item in batch]
    merged.sort(
        key=lambda item: item.get("start", {}).get("dateTime")
        or item.get("start", {}).get("date", "")
    )
    return merged


def cmd_token(_args: argparse.Namespace) -> None:
    print(get_access_token())


def cmd_calendars(_args: argparse.Namespace) -> None:
    items = (api_call("GET", "/users/me/calendarList") or {}).get("items", [])
    for item in items:
        print(
            f"{item.get('id', '')}  ({item.get('accessRole', '')})  {item.get('summary', '')}"
        )


def cmd_create_calendar(args: argparse.Namespace) -> None:
    tz = args.timezone or get_timezone()
    body: dict[str, Any] = {"summary": args.summary, "timeZone": tz}
    if args.description:
        body["description"] = args.description

    created = api_call("POST", "/calendars", body)
    cal_id = created.get("id", "")

    if args.color_id:
        api_call(
            "PATCH",
            f"/users/me/calendarList/{urlpath_quote(cal_id)}",
            {"colorId": args.color_id},
        )

    print(f"Created calendar: {args.summary}  [id: {cal_id}]")


def _resolve_range(
    from_: str | None,
    to: str | None,
    days: str | None,
    *,
    default_past_days: int = 0,
    default_future_days: int = 1,
) -> tuple[str, str]:
    if from_ is None:
        from_dt = local_midnight()
        if default_past_days:
            from_dt = from_dt - timedelta(days=default_past_days)
        from_ = iso(from_dt)
    from_dt = datetime.fromisoformat(from_)
    if to is None:
        span = int(days) if days else default_future_days
        to = iso(from_dt + timedelta(days=span))
    return from_, to


def cmd_agenda(args: argparse.Namespace) -> None:
    if args.all_calendars and args.calendar is not None:
        die("--all-calendars and --calendar are mutually exclusive")
    cal = args.calendar or "primary"
    from_, to = _resolve_range(
        args.from_,
        args.to,
        args.days,
        default_future_days=int(args.days or 1),
    )

    if args.all_calendars:
        print_events_multi(list_events_all(from_, to))
    else:
        print_events(list_events(cal, from_, to), cal)


def cmd_search(args: argparse.Namespace) -> None:
    if args.all_calendars and args.calendar is not None:
        die("--all-calendars and --calendar are mutually exclusive")
    cal = args.calendar or "primary"

    if args.days:
        from_, to = _resolve_range(args.from_, args.to, args.days)
    else:
        from_ = args.from_ or iso(local_midnight() - timedelta(days=7))
        to = args.to or iso(local_midnight() + timedelta(days=30))

    if args.all_calendars:
        print_events_multi(list_events_all(from_, to, args.query))
    else:
        print_events(list_events(cal, from_, to, args.query), cal)


def cmd_get(args: argparse.Namespace) -> None:
    item = api_call(
        "GET",
        f"/calendars/{urlpath_quote(args.calendar)}/events/{urlpath_quote(args.event_id)}",
    )
    lines = [format_event_line(item, args.calendar)]
    if item.get("location"):
        lines.append(f"Location: {item['location']}")
    if item.get("description"):
        lines.append(f"Description: {item['description']}")
    lines.append(f"Status: {item.get('status', '')}")
    lines.append(item.get("htmlLink", ""))
    print("\n".join(lines))


def cmd_create(args: argparse.Namespace) -> None:
    tz = args.timezone or get_timezone()
    if args.all_day:
        body: dict[str, Any] = {
            "summary": args.summary,
            "start": {"date": args.start},
            "end": {"date": args.end},
        }
    else:
        body = {
            "summary": args.summary,
            "start": {"dateTime": args.start, "timeZone": tz},
            "end": {"dateTime": args.end, "timeZone": tz},
        }
    if args.description:
        body["description"] = args.description
    if args.location:
        body["location"] = args.location

    created = api_call("POST", f"/calendars/{urlpath_quote(args.calendar)}/events", body)
    print(
        f"Created: {created.get('summary', '')}  [id: {created.get('id', '')}]\n"
        f"{created.get('htmlLink', '')}"
    )


def cmd_update(args: argparse.Namespace) -> None:
    tz = args.timezone or get_timezone()
    body: dict[str, Any] = {}
    if args.summary is not None:
        body["summary"] = args.summary
    if args.description is not None:
        body["description"] = args.description
    if args.location is not None:
        body["location"] = args.location
    if args.start is not None:
        body["start"] = {"dateTime": args.start, "timeZone": tz}
    if args.end is not None:
        body["end"] = {"dateTime": args.end, "timeZone": tz}

    if not body:
        die(
            "Nothing to update - pass at least one of "
            "--summary/--description/--location/--start/--end"
        )

    updated = api_call(
        "PATCH",
        f"/calendars/{urlpath_quote(args.calendar)}/events/{urlpath_quote(args.event_id)}",
        body,
    )
    print(
        f"Updated: {updated.get('summary', '')}  [id: {updated.get('id', '')}]\n"
        f"{updated.get('htmlLink', '')}"
    )


def cmd_delete(args: argparse.Namespace) -> None:
    api_call(
        "DELETE",
        f"/calendars/{urlpath_quote(args.calendar)}/events/{urlpath_quote(args.event_id)}",
    )
    print(f"Deleted event {args.event_id} from calendar {args.calendar}")


def cmd_move(args: argparse.Namespace) -> None:
    path = (
        f"/calendars/{urlpath_quote(args.from_)}/events/{urlpath_quote(args.event_id)}/move?"
        f"{urlquery({'destination': args.to})}"
    )
    moved = api_call("POST", path)
    print(f"Moved: {moved.get('summary', '')}  [id: {moved.get('id', '')}]  -> calendar {args.to}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts-gcal",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("token").set_defaults(func=cmd_token)
    sub.add_parser("calendars").set_defaults(func=cmd_calendars)

    p_cc = sub.add_parser("create-calendar")
    p_cc.add_argument("--summary", required=True)
    p_cc.add_argument("--description", default="")
    p_cc.add_argument("--color-id", default="")
    p_cc.add_argument("--timezone")
    p_cc.set_defaults(func=cmd_create_calendar)

    p_agenda = sub.add_parser("agenda")
    p_agenda.add_argument("--calendar")
    p_agenda.add_argument("--from", dest="from_")
    p_agenda.add_argument("--to")
    p_agenda.add_argument("--days", default="1")
    p_agenda.add_argument("--all-calendars", action="store_true")
    p_agenda.set_defaults(func=cmd_agenda)

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--calendar")
    p_search.add_argument("--from", dest="from_")
    p_search.add_argument("--to")
    p_search.add_argument("--days")
    p_search.add_argument("--all-calendars", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_get = sub.add_parser("get")
    p_get.add_argument("--calendar", default="primary")
    p_get.add_argument("--event-id", dest="event_id", required=True)
    p_get.set_defaults(func=cmd_get)

    p_create = sub.add_parser("create")
    p_create.add_argument("--calendar", default="primary")
    p_create.add_argument("--summary", required=True)
    p_create.add_argument("--start", required=True)
    p_create.add_argument("--end", required=True)
    p_create.add_argument("--description")
    p_create.add_argument("--location")
    p_create.add_argument("--all-day", action="store_true")
    p_create.add_argument("--timezone")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update")
    p_update.add_argument("--calendar", default="primary")
    p_update.add_argument("--event-id", dest="event_id", required=True)
    p_update.add_argument("--summary")
    p_update.add_argument("--description")
    p_update.add_argument("--location")
    p_update.add_argument("--start")
    p_update.add_argument("--end")
    p_update.add_argument("--timezone")
    p_update.set_defaults(func=cmd_update)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("--calendar", default="primary")
    p_delete.add_argument("--event-id", dest="event_id", required=True)
    p_delete.set_defaults(func=cmd_delete)

    p_move = sub.add_parser("move")
    p_move.add_argument("--event-id", dest="event_id", required=True)
    p_move.add_argument("--from", dest="from_", default="primary")
    p_move.add_argument("--to", required=True)
    p_move.set_defaults(func=cmd_move)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
