#!/usr/bin/env python3
"""Append one milestone line for the current agent session.

Agents call this instead of composing a path and a JSON object themselves:
the identifiers live in environment variables that differ per harness, and
having each agent rebuild that path is where it silently went wrong before.

    milestone "what I am starting now" "how the previous piece ended"
    milestone --role "trace config loading" "locate the loader"
    milestone --done "have the answer"

A job that outlives the session that started it passes `--run`, which mirrors
the same record into one journal per run. That journal is what a later session
reads back, so anything a recovering agent must act on goes in a typed field
rather than in the human `doing` and `prev` prose.

    milestone --run nightly-01 --event task --task extract --state failed \
              --attempt 1 "retry the extract" "schema audit rejected the output"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Empty or relative would split writer and reader across working directories.
LOGS = Path(os.environ.get("ASSISTANT_LOGS") or Path.home() / ".assistant-logs").expanduser().resolve()


def session_id() -> str:
    """Whichever harness we are under names its session differently."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"):
        value = os.environ.get(var)
        if value:
            return value
    return "unknown"


def agent_id() -> str:
    """Per-agent file where the harness exposes a thread id.

    Claude Code exposes no per-agent identifier, and each tool call runs in a
    fresh shell, so an agent cannot carry a minted suffix between calls. There
    the whole session shares one file: single-line O_APPEND writes interleave
    safely, and `role` distinguishes the agents on read.
    """
    return os.environ.get("CODEX_THREAD_ID") or "session"


def log_path(session: str, agent: str) -> Path:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return LOGS / day / f"{session}.{agent}.jsonl"


# A run id reaches the filesystem as one path component and reaches the reader
# as a lookup key, so it is restricted to what is safe in both: no separator,
# no leading dot, and short enough to stay well inside any filename limit.
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def run_journal(run: str) -> Path:
    """The one file every session contributing to `run` appends to.

    Kept outside the day directories: a long run spans midnight, and splitting
    its journal by day would put recovery back in the business of merging
    files. Raises ValueError on an id that must not become a path component.
    """
    if not RUN_ID.match(run):
        raise ValueError(
            f"unsafe run id {run!r}: use letters, digits, dot, dash or underscore "
            "(64 max) starting with a letter or digit"
        )
    return LOGS / "runs" / f"{run}.jsonl"


# Bytes, not characters: the capped fields plus JSON overhead stay under a 4KB
# single write even when every character encodes to three.
LINE_BUDGET = 3800


def _append_line(target: Path, line: bytes) -> None:
    """Append one complete record without losing concurrent Windows writers."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(descriptor, line) != len(line):
                raise OSError(f"short append to {target}")
        finally:
            os.close(descriptor)
        return

    import msvcrt

    lock_path = target.with_name(target.name + ".lock")
    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            with target.open("ab") as handle:
                if handle.write(line) != len(line):
                    raise OSError(f"short append to {target}")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doing", nargs="?", default="", help="what you are starting now")
    ap.add_argument("prev", nargs="?", default="", help="how the previous piece ended")
    ap.add_argument("--role", default="", help="a few words naming your overall task")
    ap.add_argument("--done", metavar="PREV", help="final entry; PREV closes the last piece")
    ap.add_argument("--path", action="store_true", help="print the log path and exit")
    ap.add_argument("--run", metavar="ID", help="durable run id; also journals to that run")
    ap.add_argument("--event", default="", help="what kind of event this is, e.g. run-start, task")
    ap.add_argument("--step", type=int, help="current numbered algorithm step")
    ap.add_argument("--task", default="", help="identity of the task this concerns")
    ap.add_argument("--state", default="", help="state that task is now in, e.g. started, failed")
    ap.add_argument("--attempt", type=int, help="attempt or repair round for that task")
    ap.add_argument("--evidence", action="append", default=[], metavar="PATH",
                    help="path to supporting evidence; repeatable")
    args = ap.parse_args()

    typed = {
        "event": args.event[:60],
        "step": args.step,
        "task": args.task[:100],
        "state": args.state[:40],
        "attempt": args.attempt,
        "evidence": [str(item)[:200] for item in args.evidence[:20]],
    }
    typed = {key: value for key, value in typed.items() if value not in ("", None, [])}
    if typed and args.run is None:
        # Structured data outside a run journal is unrecoverable later, and
        # silently keeping it human-only would be the failure this guards.
        ap.error(f"--run is required with {', '.join('--' + key for key in sorted(typed))}")
    for key in ("step", "attempt"):
        if typed.get(key, 0) < 0:
            ap.error(f"--{key} cannot be negative")

    journal = None
    if args.run is not None:
        try:
            journal = run_journal(args.run)
        except ValueError as exc:
            ap.error(str(exc))

    session, agent = session_id(), agent_id()
    path = log_path(session, agent)
    if args.path:
        print(journal or path)
        return 0

    doing = "(done)" if args.done is not None else args.doing
    prev = args.done if args.done is not None else args.prev
    if not doing:
        ap.error("nothing to record: pass DOING, or --done PREV")

    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "role": args.role[:200],
        "cwd": os.getcwd()[:200],
        "doing": doing[:200],
        "prev": prev[:200],
    }
    if journal is not None:
        # The journal is read without its filename for context, so the record
        # names its own origin. Old records carry none of these keys.
        record.update(run=args.run, session=session, agent=agent, **typed)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    # Interleave safety rests on one line, one write, well under 4KB. Every
    # other field is individually capped; `--evidence` is the one a caller can
    # repeat, so it is what gives way — and it says so rather than going quiet.
    dropped = 0
    while len(line.encode("utf-8")) > LINE_BUDGET and record.get("evidence"):
        record["evidence"] = record["evidence"][:-1]
        dropped += 1
        record["evidence_dropped"] = dropped
        line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        for target in (path, journal) if journal else (path,):
            _append_line(target, line.encode("utf-8"))
    except OSError as exc:
        print(f"milestone: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
