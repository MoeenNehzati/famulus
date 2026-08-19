#!/usr/bin/env python3
"""Append one milestone line for the current agent session.

Agents call this instead of composing a path and a JSON object themselves:
the identifiers live in environment variables that differ per harness, and
having each agent rebuild that path is where it silently went wrong before.

    milestone "what I am starting now" "how the previous piece ended"
    milestone --role "trace config loading" "locate the loader"
    milestone --done "have the answer"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

LOGS = Path(os.environ.get("ASSISTANT_LOGS", Path.home() / ".assistant-logs"))


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doing", nargs="?", default="", help="what you are starting now")
    ap.add_argument("prev", nargs="?", default="", help="how the previous piece ended")
    ap.add_argument("--role", default="", help="a few words naming your overall task")
    ap.add_argument("--done", metavar="PREV", help="final entry; PREV closes the last piece")
    ap.add_argument("--path", action="store_true", help="print the log path and exit")
    args = ap.parse_args()

    session, agent = session_id(), agent_id()
    path = log_path(session, agent)
    if args.path:
        print(path)
        return 0

    doing = "(done)" if args.done is not None else args.doing
    prev = args.done if args.done is not None else args.prev
    if not doing:
        ap.error("nothing to record: pass DOING, or --done PREV")

    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "role": args.role[:200],
        "cwd": os.getcwd(),
        "doing": doing[:200],
        "prev": prev[:200],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # One line, one O_APPEND write: concurrent agents interleave safely.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"milestone: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
