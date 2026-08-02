"""Command-line interface for guarded Claude and Codex wakeups.

``schedule`` is the explicit user interface. ``infer`` accepts timeout output
through ``--text`` or stdin and, when neither supplies text, scans structured
provider logs. ``run-due`` is intentionally omitted from public help because
it is the systemd-facing worker entrypoint rather than a user workflow.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import DEFAULT_MESSAGE, WakeupError
from .deadlines import parse_deadline
from .claude_codex_service import run_due, schedule
from .claude_codex_sessions import (
    infer_provider,
    infer_session_token,
    latest_rate_limit,
    latest_session,
)


def _parser() -> argparse.ArgumentParser:
    """Build the public argument parser without exposing worker internals."""
    parser = argparse.ArgumentParser(
        prog="llm-wakeup",
        description="Schedule a guarded wakeup for a rate-limited Claude or Codex session.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    explicit = commands.add_parser(
        "schedule",
        help="schedule with explicit session and time",
    )
    explicit.add_argument("provider", choices=("claude", "codex"))
    explicit.add_argument("session_id", help="session UUID or provider session alias")
    explicit.add_argument("time", help="duration, clock time, or ISO date-time")
    explicit.add_argument("--message", default=DEFAULT_MESSAGE)
    explicit.set_defaults(handler=_schedule)

    inferred = commands.add_parser(
        "infer",
        help="infer provider, session, and time from timeout text",
    )
    inferred.add_argument("--text", help="timeout output; stdin is used when omitted")
    inferred.add_argument("--message", default=DEFAULT_MESSAGE)
    inferred.set_defaults(handler=_infer)
    return parser


def _print_scheduled(job: dict) -> None:
    """Print the stable scheduling receipt using the user's local timezone."""
    local_time = datetime.fromisoformat(job["run_at"]).astimezone()
    print(
        f"Scheduled {job['provider']} wakeup for {job['session_id']} at "
        f"{local_time.isoformat(timespec='seconds')} (id={job['id']})"
    )


def _schedule(args: argparse.Namespace) -> None:
    """Handle explicit provider, session, deadline, and optional message input."""
    job = schedule(
        args.provider,
        args.session_id,
        parse_deadline(args.time),
        args.message,
    )
    _print_scheduled(job)


def _infer(args: argparse.Namespace) -> None:
    """Schedule from timeout text or the latest structured rate-limit event.

    Reading stdin only when it is not a terminal avoids blocking the common
    no-argument invocation after the user exits an interactive provider CLI.
    """
    if args.text is not None:
        text = args.text
    elif sys.stdin.isatty():
        text = ""
    else:
        text = sys.stdin.read()
    if text.strip():
        provider = infer_provider(text)
        token = infer_session_token(provider, text)
        session_id = token or latest_session(provider)[0]
    else:
        provider, session_id, text = latest_rate_limit()
    job = schedule(
        provider,
        session_id,
        parse_deadline(text, embedded=True),
        args.message,
        context=text,
    )
    _print_scheduled(job)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit status.

    Exit status 0 means the requested operation completed. Expected inference,
    scheduling, persistence, and delivery setup errors return 2.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] == "run-due":
            internal = argparse.ArgumentParser(prog="llm-wakeup run-due")
            internal.add_argument("--verbose", action="store_true")
            internal.parse_args(arguments[1:])
            run_due()
            return 0
        args = _parser().parse_args(arguments)
        args.handler(args)
    except WakeupError as error:
        print(f"llm-wakeup: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
