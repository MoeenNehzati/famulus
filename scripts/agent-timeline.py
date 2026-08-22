#!/usr/bin/env python3
"""Merge agent milestone logs with harness transcripts into one timeline.

Two half-logs exist for any session: the harness records every tool call with
real timestamps but no intent, while the milestone log records intent with no
mechanical detail. This joins them on session id.

Milestone logs are harness-neutral, so they drive the lookup; the mechanical
trace is then pulled from whichever store has it — Claude Code's project
transcripts or Codex's session rollouts.

`--run` reads the other axis. A job that outlived the session that started it
has its milestones spread over every session that worked on it, so the writer
mirrors those into one journal per run; this reads that journal back whole, in
append order, without joining a transcript to it.
"""

from __future__ import annotations

import argparse
import glob as _glob
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Empty or relative would split writer and reader across working directories.
LOGS = Path(os.environ.get("ASSISTANT_LOGS") or Path.home() / ".assistant-logs").expanduser().resolve()
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
RUNS_DIR = "runs"


def _writer():
    """The writer owns the run-id rule; importing it keeps one definition.

    Both helpers are symlinked onto PATH, so `__file__` is the link and only
    the resolved path finds its sibling.
    """
    source = Path(__file__).resolve().parent / "milestone.py"
    spec = importlib.util.spec_from_file_location("milestone_writer", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    # `runs/` sits beside the day directories and holds journals, not sessions.
    if session is None:
        return sorted(p for p in LOGS.glob("*/*.jsonl") if p.parent.name != RUNS_DIR)
    escaped = _glob.escape(session)
    # An id carrying its own agent segment (the "unknown" case) names one file.
    pattern = f"*/{escaped}.jsonl" if "." in session else f"*/{escaped}.*.jsonl"
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
                    "cwd": rec.get("cwd", ""),
                }
            )
    return events, agent_ids


# ── run journals ─────────────────────────────────────────────────────────────

def read_run(run: str) -> dict:
    """Every event recorded for one run, in the order it was appended.

    Append order is the record: one file, one writer per line, so no timestamp
    comparison is needed to order events that several sessions contributed.
    Anything that does not parse as a milestone record is reported rather than
    dropped — a recovering agent must not mistake a damaged journal for a short
    one. Raises ValueError for an unusable id and FileNotFoundError for a run
    that was never logged.
    """
    # The writer owns the id rule; the reader owns its own log root, so take
    # the validated filename and place it under the root this reader resolved.
    path = LOGS / RUNS_DIR / _writer().run_journal(run).name
    if not path.is_file():
        raise FileNotFoundError(path)

    events: list[dict] = []
    malformed: list[dict] = []
    sessions: list[str] = []
    agents: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append({"line": number, "reason": f"not JSON: {exc.msg}"})
                continue
            if not isinstance(rec, dict):
                malformed.append({"line": number, "reason": "not a JSON object"})
                continue
            if at(rec, "ts") is None:
                malformed.append({"line": number, "reason": "missing or unreadable ts"})
                continue
            events.append(rec)
            for value, seen in ((rec.get("session"), sessions), (rec.get("agent"), agents)):
                if isinstance(value, str) and value and value not in seen:
                    seen.append(value)
    return {
        "run": run,
        "path": str(path),
        "sessions": sessions,
        "agents": agents,
        "events": events,
        "malformed": malformed,
    }


def render_run(reconstructed: dict) -> None:
    events = reconstructed["events"]
    print(f"run {reconstructed['run']}\n{'-' * 70}")
    origins = [oneline(f"{rec.get('session', '?')}/{rec.get('agent', '?')}", 24) for rec in events]
    width = max((len(origin) for origin in origins), default=0)
    indent = " " * (width + 22)
    start = at(events[0], "ts") if events else None
    for rec, origin in zip(events, origins):
        ts = at(rec, "ts")
        clock = ts.strftime("%H:%M:%S")
        offset = f"+{int((ts - start).total_seconds()):>6}s"
        print(f"{clock} {offset}  {origin.ljust(width)}  > {oneline(rec.get('doing', ''), 200)}")
        # The typed line comes first: it is what recovery reads, and `prev` is
        # the human gloss on it.
        tags = " ".join(
            f"{key}={oneline(rec[key], 40)}"
            for key in ("event", "step", "task", "state", "attempt")
            if rec.get(key) not in (None, "")
        )
        detail = [tags] if tags else []
        if rec.get("prev"):
            detail.append(f"prev: {oneline(rec['prev'], 200)}")
        detail += [f"evidence: {oneline(item, 120)}" for item in rec.get("evidence") or []]
        for line in detail:
            print(indent + line)
    print(
        f"\n{len(events)} events from {len(reconstructed['sessions'])} session(s)"
        f" - {reconstructed['path']}"
    )
    if reconstructed["malformed"]:
        # Part of the report, not an operational error: a damaged journal is a
        # finding the reader must see next to what did survive.
        print(f"{len(reconstructed['malformed'])} malformed line(s) - journal is damaged")
        for bad in reconstructed["malformed"]:
            print(f"  line {bad['line']}: {bad['reason']}")


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
        session = path.stem.split(".", 1)[0]
        # Unrelated runs share the "unknown" id, so key those by whole filename.
        seen.setdefault(path.stem if session == "unknown" else session, []).append(path)
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
        mark = " [slow]" if gap >= slow else ""
        clock = ev["ts"].strftime("%H:%M:%S")
        offset = f"+{int((ev['ts'] - start).total_seconds()):>5}s"
        agent = ev["agent"][:width].ljust(width)
        lead = "> " if ev["kind"] == "milestone" else "  "
        print(f"{clock} {offset}  {agent}  {lead}{ev['text']}{mark}")
        if ev["kind"] == "milestone" and ev.get("prev"):
            print(f"{' ' * (len(clock) + len(offset) + width + 6)}prev: {ev['prev']}")
    span = (events[-1]["ts"] - start).total_seconds()
    tools = sum(1 for e in events if e["kind"] == "tool")
    print(f"\n{len(events)} events over {span:.0f}s - {tools} tool calls, {len(events) - tools} milestones")
    roots = sorted({e["cwd"] for e in events if e.get("cwd")})
    if roots:
        print("worked in: " + ", ".join(roots))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", nargs="?", help="session id (default: most recent)")
    ap.add_argument("-l", "--list", action="store_true", help="list known sessions")
    ap.add_argument("--slow", type=float, default=10.0, help="flag gaps at least this long")
    ap.add_argument("--run", metavar="ID", help="read one run's journal instead of a session")
    ap.add_argument("--json", action="store_true", help="with --run, dump the reconstruction")
    args = ap.parse_args()

    if not LOGS.is_dir():
        print(f"no milestone logs under {LOGS}", file=sys.stderr)
        return 1
    if args.run is not None:
        try:
            reconstructed = read_run(args.run)
        except ValueError as exc:
            print(f"agent-timeline: {exc}", file=sys.stderr)
            return 2
        except FileNotFoundError:
            print(f"no journal for run {args.run!r} under {LOGS / RUNS_DIR}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(reconstructed, ensure_ascii=False, indent=2))
        else:
            render_run(reconstructed)
        return 0
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
