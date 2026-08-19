#!/usr/bin/env python3
"""Merge agent milestone logs with harness transcripts into one timeline.

Two half-logs exist for any session: the harness records every tool call with
real timestamps but no intent, while the milestone log records intent with no
mechanical detail. This joins them on session id.

Milestone logs are harness-neutral, so they drive the lookup; the mechanical
trace is then pulled from whichever store has it — Claude Code's project
transcripts or Codex's session rollouts.
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Empty or relative would split writer and reader across working directories.
LOGS = Path(os.environ.get("ASSISTANT_LOGS") or Path.home() / ".assistant-logs").expanduser().resolve()
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"


def parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()


def oneline(value, limit: int = 70) -> str:
    """Collapse whitespace so multi-line tool payloads stay on one row."""
    return " ".join(str(value).split())[:limit]


def iter_json(path: Path):
    """Stream JSON records; transcripts reach tens of MB, so never slurp."""
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            if line.strip():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec


def at(rec: dict, key: str) -> datetime | None:
    """Timestamp or None — one malformed row must not kill the whole run."""
    raw = rec.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return parse_ts(raw)
    except ValueError:
        return None


# ── milestone logs (both harnesses) ──────────────────────────────────────────

def milestone_files(session: str | None = None) -> list[Path]:
    pattern = f"*/{_glob.escape(session)}.*.jsonl" if session else "*/*.jsonl"
    return sorted(LOGS.glob(pattern))


def read_milestones(session: str) -> tuple[list[dict], set[str]]:
    events, agent_ids = [], set()
    for path in milestone_files(session):
        stem = path.stem.split(".", 1)
        agent = stem[1] if len(stem) > 1 else path.stem
        agent_ids.add(agent)
        label = agent
        for rec in iter_json(path):
            label = rec.get("role") or label  # agents often repeat it; last wins
            ts = at(rec, "ts")
            if ts is None:
                continue
            events.append(
                {
                    "ts": ts,
                    "agent": label,
                    "kind": "milestone",
                    "text": oneline(rec.get("doing", ""), 200),
                    "prev": oneline(rec.get("prev", ""), 200),
                }
            )
    return events, agent_ids


# ── Claude Code transcripts ──────────────────────────────────────────────────

def claude_events(session: str) -> list[dict]:
    events = []
    for project in CLAUDE_PROJECTS.glob("*"):
        main = project / f"{session}.jsonl"
        if not main.exists():
            continue
        events += claude_file(main, "main")  # one project dir owns a session
        for path in sorted((project / session / "subagents").glob("agent-*.jsonl")):
            name = path.stem.replace("agent-", "")[:8]
            meta = path.with_suffix(".meta.json")
            if meta.exists():
                try:
                    name = (json.loads(meta.read_text()).get("description") or name)[:28]
                except (OSError, json.JSONDecodeError):
                    pass
            events += claude_file(path, name)
        break
    return events


def claude_file(path: Path, agent: str) -> list[dict]:
    events = []
    for rec in iter_json(path):
        content = (rec.get("message") or {}).get("content")
        ts = at(rec, "timestamp")
        if not isinstance(content, list) or ts is None:
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            label = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
            label = oneline(label)
            events.append(
                {
                    "ts": ts,
                    "agent": agent,
                    "kind": "tool",
                    "text": f"{block.get('name', '?')}  {label}",
                }
            )
    return events


# ── Codex rollouts ───────────────────────────────────────────────────────────

CODEX_CALLS = {"custom_tool_call", "function_call"}


def codex_events(ids: set[str]) -> list[dict]:
    """Rollouts are named by thread id, not session id (they coincide only for
    the root thread), so match every thread id seen in the milestone logs."""
    events = []
    for ident in ids:
        for path in CODEX_SESSIONS.glob(f"**/rollout-*-{_glob.escape(ident)}.jsonl"):
            agent = ident[:8]
            for rec in iter_json(path):
                payload = rec.get("payload") or {}
                ts = at(rec, "timestamp")
                if payload.get("type") not in CODEX_CALLS or ts is None:
                    continue
                name = payload.get("name") or payload.get("tool_name") or "?"
                args = oneline(payload.get("arguments") or payload.get("input") or "")
                events.append(
                    {
                        "ts": ts,
                        "agent": agent,
                        "kind": "tool",
                        "text": f"{name}  {args}",
                    }
                )
    return events


# ── session discovery ────────────────────────────────────────────────────────

def list_sessions() -> list[tuple[str, datetime, int]]:
    seen: dict[str, list[Path]] = {}
    for path in milestone_files():
        seen.setdefault(path.stem.split(".", 1)[0], []).append(path)
    rows = []
    for session, paths in seen.items():
        try:
            newest = max(p.stat().st_mtime for p in paths)
        except OSError:
            continue
        rows.append((session, datetime.fromtimestamp(newest).astimezone(), len(paths)))
    return sorted(rows, key=lambda r: r[1])


# ── rendering ────────────────────────────────────────────────────────────────

def render(events: list[dict], slow: float) -> None:
    if not events:
        print("no events found for that session")
        return
    events.sort(key=lambda e: (e["ts"], e["kind"] != "milestone"))
    start = events[0]["ts"]
    width = min(30, max(len(e["agent"]) for e in events))
    prev_ts = None
    for ev in events:
        gap = (ev["ts"] - prev_ts).total_seconds() if prev_ts else 0.0
        prev_ts = ev["ts"]
        mark = " «" if gap >= slow else ""
        clock = ev["ts"].strftime("%H:%M:%S")
        offset = f"+{int((ev['ts'] - start).total_seconds()):>5}s"
        agent = ev["agent"][:width].ljust(width)
        lead = "▸ " if ev["kind"] == "milestone" else "  "
        print(f"{clock} {offset}  {agent}  {lead}{ev['text']}{mark}")
        if ev["kind"] == "milestone" and ev.get("prev"):
            print(f"{' ' * (len(clock) + len(offset) + width + 6)}prev: {ev['prev']}")
    span = (events[-1]["ts"] - start).total_seconds()
    tools = sum(1 for e in events if e["kind"] == "tool")
    print(f"\n{len(events)} events over {span:.0f}s — {tools} tool calls, {len(events) - tools} milestones")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", nargs="?", help="session id (default: most recent)")
    ap.add_argument("-l", "--list", action="store_true", help="list known sessions")
    ap.add_argument("--slow", type=float, default=10.0, help="flag gaps at least this long")
    args = ap.parse_args()

    if not LOGS.is_dir():
        print(f"no milestone logs under {LOGS}", file=sys.stderr)
        return 1
    sessions = list_sessions()
    if args.list:
        for session, when, files in sessions:
            print(f"{when:%Y-%m-%d %H:%M}  {session}  ({files} agent{'s' if files > 1 else ''})")
        return 0
    if not sessions and not args.session:
        print(f"no milestone logs under {LOGS}", file=sys.stderr)
        return 1
    session = args.session or sessions[-1][0]

    events, agent_ids = read_milestones(session)
    events += claude_events(session)
    events += codex_events({session} | agent_ids)
    print(f"session {session}\n{'-' * 70}")
    render(events, args.slow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
