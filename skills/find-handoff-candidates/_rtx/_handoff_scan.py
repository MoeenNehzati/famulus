#!/usr/bin/env python3
"""Scan today's session transcripts for handoff status, with zero LLM logic.

Per-host transcript location and format knowledge lives in dedicated
parser files under this same directory (see references/skill-guidelines.md,
guideline 13, for why this script itself must stay host-neutral): each
parser exposes a small shared interface (list_session_files,
extract_project, extract_session_id, resume_hint, opaque_field,
default_threshold, id). This script never names a specific host -- it just
loops over whatever __init__.py's `parsers` list provides.

Detection of handoff_status is a regex match against two sentinel comment
markers that the prepare-handoff skill is required to emit verbatim:
  <!-- HANDOFF-SENTINEL: STARTED -->
  <!-- HANDOFF-SENTINEL: COMPLETE -->
The exact HTML-comment wrapper (not just the bare words) is required so
ordinary prose that discusses this mechanism does not produce false
positives. No interpretation of message content beyond that regex match.

Flagging threshold: instead of raw transcript size, we measure how much
conversation has happened SINCE the last completed handoff (or since the
session started, if it was never handed off) -- "gap_net_chars". This is
the sum of each line's raw character length, minus the length of any JSON
field literally named by that host's `opaque_field` (an opaque crypto blob
each host attaches to hidden reasoning), found anywhere in the line via a
generic recursive walk. Excluding just that one known-opaque field name per
host removes the one systematic source of noise verified on both hosts,
without needing a full per-host content-field allowlist -- unknown future
fields are counted, not silently dropped, so the metric fails toward
over-flagging rather than hiding real unhandled-off work.

Reference calibration (measured against ~300 real sessions for one host and
~17 real sessions for the other, from the trailing 5 days on this machine):
one host's transcripts run roughly an order of magnitude bulkier per unit
of work than the other's (median net bytes/session ~460K vs ~74K; median
line count ~410 vs ~40), most likely because it logs extra per-turn
bookkeeping events the other doesn't. Because of that gap, the flagging
threshold is applied per host, not as one shared number -- see each
parser's own `default_threshold`.

Usage:
    scan.py [--min-gap-chars N] [--days N | --date YYYY-MM-DD]

By default, scans the trailing 2 days (today and yesterday, inclusive) --
not just today -- so a session touched yesterday still surfaces even if
this wasn't run yesterday. --days overrides that window size; --date pins
to one exact day instead (mutually exclusive with --days) and is mainly
useful for backtesting/calibration against a specific historical day.

Output: JSON array on stdout, one object per flagged session, sorted by
last_activity descending. Sessions whose gap_net_chars is below that
host's threshold are omitted entirely.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

from . import PARSERS

STARTED_RE = re.compile(r"<!--\s*HANDOFF-SENTINEL:\s*STARTED\s*-->")
COMPLETE_RE = re.compile(r"<!--\s*HANDOFF-SENTINEL:\s*COMPLETE\s*-->")

# Absolute floor on line count regardless of gap size, just to skip
# essentially-empty stub sessions where timestamps/byte counts are noise.
MIN_LINE_FLOOR = 3


def _file_mtime_date(path: str) -> str:
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")


def _read_lines(path: str):
    with open(path, "r", errors="replace") as f:
        return f.readlines()


def opaque_len(obj, field_name: str) -> int:
    """Recursively sum the string length of any field literally named field_name."""
    total = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == field_name and isinstance(v, str):
                total += len(v)
            else:
                total += opaque_len(v, field_name)
    elif isinstance(obj, list):
        for item in obj:
            total += opaque_len(item, field_name)
    return total


def _scan_file(path: str, host_parser, gap_threshold: int):
    lines = _read_lines(path)
    if len(lines) < MIN_LINE_FLOOR:
        return None

    first_obj = None
    project = None
    start_time = None
    last_activity = None
    started_at = None
    status = "none"
    gap_net_chars = 0  # resets to 0 every time a COMPLETE sentinel is seen

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if first_obj is None:
            first_obj = obj

        ts = obj.get("timestamp")
        if ts:
            if start_time is None:
                start_time = ts
            last_activity = ts

        if project is None:
            project = host_parser.extract_project(obj)

        if COMPLETE_RE.search(raw):
            status = "complete"
            gap_net_chars = 0
        elif STARTED_RE.search(raw) and status != "complete":
            status = "started"
            if started_at is None:
                started_at = ts
            gap_net_chars += len(raw) - opaque_len(obj, host_parser.opaque_field)
        else:
            gap_net_chars += len(raw) - opaque_len(obj, host_parser.opaque_field)

    if gap_net_chars < gap_threshold:
        return None

    session_id = host_parser.extract_session_id(path, first_obj)

    return {
        "session_id": session_id,
        "source": host_parser.id,
        "project": project,
        "start_time": start_time,
        "last_activity": last_activity,
        "line_count": len(lines),
        "gap_net_chars": gap_net_chars,
        "handoff_status": status,
        "handoff_started_at": started_at,
        "resume_hint": host_parser.resume_hint(session_id),
    }


def _normalize_dates(target_dates) -> set:
    """Accept either a single date string or an iterable of date strings."""
    if isinstance(target_dates, str):
        return {target_dates}
    return set(target_dates)


def scan(target_dates, gap_thresholds: dict, parsers=None):
    dates = _normalize_dates(target_dates)
    results = []
    for host_parser in (parsers if parsers is not None else PARSERS):
        threshold = gap_thresholds.get(host_parser.id, host_parser.default_threshold)
        for path in host_parser.list_session_files():
            try:
                if _file_mtime_date(path) not in dates:
                    continue
                rec = _scan_file(path, host_parser, threshold)
            except OSError:
                continue
            if rec:
                results.append(rec)

    results.sort(key=lambda r: r.get("last_activity") or "", reverse=True)
    return results


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument(
        "--min-gap-chars",
        type=int,
        default=None,
        help=(
            "Override every host's default gap threshold with a single "
            "shared value (net chars since last completed handoff)."
        ),
    )
    arg_parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Scan the trailing N days ending today, inclusive (default: 2). Ignored if --date is given.",
    )
    arg_parser.add_argument(
        "--date",
        default=None,
        help="Pin to one exact date YYYY-MM-DD in local time, instead of the trailing --days window.",
    )
    args = arg_parser.parse_args()

    if args.date:
        target_dates = {args.date}
    else:
        today = datetime.date.today()
        target_dates = {
            (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.days)
        }

    if args.min_gap_chars is not None:
        thresholds = {p.id: args.min_gap_chars for p in PARSERS}
    else:
        thresholds = {p.id: p.default_threshold for p in PARSERS}

    results = scan(target_dates, thresholds)
    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
