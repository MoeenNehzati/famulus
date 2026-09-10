#!/usr/bin/env python3
"""Append one bounded milestone record for the current agent session."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


LOGS = Path(os.environ.get("ASSISTANT_LOGS") or Path.home() / ".assistant-logs").expanduser().resolve()


def session_id() -> str:
    """Whichever harness we are under names its session differently."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID"):
        value = os.environ.get(var)
        if value:
            return value
    return "unknown"


def agent_id() -> str:
    """Return the harness thread ID, or the shared-session fallback."""
    return os.environ.get("CODEX_THREAD_ID") or "session"


def log_path(session: str, agent: str) -> Path:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return LOGS / day / f"{session}.{agent}.jsonl"


RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def run_journal(run: str) -> Path:
    """Return the durable journal path for one safe run identifier."""
    if not RUN_ID.match(run):
        raise ValueError(
            f"unsafe run id {run!r}: use letters, digits, dot, dash or underscore "
            "(64 max) starting with a letter or digit"
        )
    return LOGS / "runs" / f"{run}.jsonl"


LINE_BUDGET = 3800
STRUCTURAL_OVERHEAD = 124

_VALUE_LIMITS = {
    "ts": 48, "role": 220, "cwd": 512, "doing": 220, "prev": 220,
    "run": 66, "session": 128, "agent": 128, "event": 80, "step": 24,
    "task": 128, "state": 64, "attempt": 24, "evidence": 1400,
}
_MAX_EVIDENCE_ENTRIES = 20
_MAX_EVIDENCE_ENTRY = 220


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


class _WriterParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, f"{self.prog}: error: {message}\n")


def _add_record_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--role", required=True, help="agent task label")
    parser.add_argument("--run", metavar="RUN", help="durable run identifier")
    parser.add_argument("--event", default="", help="typed event")
    parser.add_argument("--step", type=int, help="typed non-negative step")
    parser.add_argument("--task", default="", help="typed task identity")
    parser.add_argument("--state", default="", help="typed task state")
    parser.add_argument("--attempt", type=int, help="typed non-negative attempt")
    parser.add_argument("--evidence", action="append", default=[], metavar="PATH")


def _parser(operation: str, prog: str) -> _WriterParser:
    parser = _WriterParser(prog=prog)
    if operation == "record-progress":
        parser.add_argument("doing", metavar="DOING")
        parser.add_argument("prev", metavar="PREV", nargs="?", default="")
        _add_record_options(parser)
    elif operation == "record-completion":
        parser.add_argument("result", metavar="RESULT")
        _add_record_options(parser)
    elif operation == "session-path":
        pass
    elif operation == "run-path":
        parser.add_argument("run", metavar="RUN")
    else:
        raise ValueError(f"unknown milestone writer operation {operation!r}")
    return parser


def _require_within(parser: _WriterParser, label: str, value: object, limit: int) -> None:
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit:
        parser.error(f"{label} exceeds its {limit}-byte JSON value limit")


def _typed_record(parser: _WriterParser, args: argparse.Namespace) -> dict[str, object]:
    typed: dict[str, object] = {}
    for name in ("event", "task", "state", "step", "attempt"):
        value = getattr(args, name)
        if value in ("", None):
            continue
        if name in {"step", "attempt"} and value < 0:
            parser.error(f"--{name} cannot be negative")
        _require_within(parser, f"--{name}", value, _VALUE_LIMITS[name])
        typed[name] = value
    evidence = args.evidence
    if len(evidence) > _MAX_EVIDENCE_ENTRIES:
        parser.error(f"--evidence accepts at most {_MAX_EVIDENCE_ENTRIES} entries")
    for item in evidence:
        _require_within(parser, "--evidence entry", item, _MAX_EVIDENCE_ENTRY)
    if evidence:
        _require_within(parser, "--evidence", evidence, _VALUE_LIMITS["evidence"])
        typed["evidence"] = evidence
    return typed


def _record(operation: str, parser: _WriterParser, args: argparse.Namespace) -> int:
    progress = operation == "record-progress"
    doing, prev = (args.doing, args.prev) if progress else ("(done)", args.result)
    if not args.role:
        parser.error("--role must be non-empty")
    _require_within(parser, "--role", args.role, _VALUE_LIMITS["role"])
    _require_within(parser, "DOING" if progress else "RESULT", doing if progress else prev, _VALUE_LIMITS["doing"])
    _require_within(parser, "PREV", prev, _VALUE_LIMITS["prev"])

    timestamp, cwd = datetime.now().astimezone().isoformat(timespec="seconds"), os.getcwd()
    _require_within(parser, "timestamp", timestamp, _VALUE_LIMITS["ts"])
    _require_within(parser, "cwd", cwd, _VALUE_LIMITS["cwd"])
    record: dict[str, object] = {
        "ts": timestamp, "role": args.role, "cwd": cwd,
        "doing": doing, "prev": prev, **_typed_record(parser, args),
    }

    journal = None
    if args.run is not None:
        _require_within(parser, "--run", args.run, _VALUE_LIMITS["run"])
        try:
            journal = run_journal(args.run)
        except ValueError as exc:
            parser.error(str(exc))
        session, agent = session_id(), agent_id()
        _require_within(parser, "session", session, _VALUE_LIMITS["session"])
        _require_within(parser, "agent", agent, _VALUE_LIMITS["agent"])
        record.update(run=args.run, session=session, agent=agent)

    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    assert len(line) <= LINE_BUDGET, "independent record limits exceeded the line budget"
    try:
        path = log_path(session_id(), agent_id())
        targets = (path, journal) if journal else (path,)
        for target in targets:
            _append_line(target, line)
    except OSError as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    return 0


def main(operation: str, argv: list[str] | None = None, *, prog: str) -> int:
    parser = _parser(operation, prog)
    try:
        args = parser.parse_args(argv)
        if operation == "session-path":
            print(log_path(session_id(), agent_id()))
            return 0
        if operation == "run-path":
            _require_within(parser, "RUN", args.run, _VALUE_LIMITS["run"])
            try:
                print(run_journal(args.run))
            except ValueError as exc:
                parser.error(str(exc))
            return 0
        return _record(operation, parser, args)
    except SystemExit as exc:
        return int(exc.code)


assert STRUCTURAL_OVERHEAD + sum(_VALUE_LIMITS.values()) <= LINE_BUDGET


if __name__ == "__main__":
    raise SystemExit(main("record-progress", prog="record-progress"))
