"""Public CLI for explicit and inferred Claude and Codex wakeups."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import DEFAULT_MESSAGE, WakeupError
from ._claude_codex_service import run_due, schedule
from ._claude_codex_sessions import (
    infer_provider,
    infer_session_token,
    latest_rate_limit,
    latest_session,
    resolve_session,
)
from ._wakeup_deadlines import parse_deadline, parse_delay
from ._wakeup_doctor import render_diagnostics
from ._claude_codex_monitor import NEAR_LIMIT_PERCENT, monitor_usage
from ._wakeup_policies import FORCE, INTERRUPTED, auto_schedule_level, set_auto_schedule
from ._wakeup_providers import provider_names
from ._claude_codex_usage import capture_claude_status


def _parser() -> argparse.ArgumentParser:
    """Construct the documented public command grammar."""

    parser = argparse.ArgumentParser(prog="llm-wakeup", description="Schedule a guarded wakeup for a rate-limited LLM session.")
    commands = parser.add_subparsers(dest="command", required=True)
    explicit = commands.add_parser("schedule", help="schedule with explicit session and reset time")
    explicit.add_argument("provider", choices=provider_names())
    explicit.add_argument("session_id", help="session UUID or provider session alias")
    explicit.add_argument("time", help="duration, clock time, or ISO reset time")
    explicit.add_argument("--message", default=DEFAULT_MESSAGE)
    explicit.add_argument("--delay", default="1 minute", help="wait after reset (default: 1 minute)")
    explicit.set_defaults(handler=_schedule)
    inferred = commands.add_parser("infer", help="infer provider, session, and reset time")
    inferred.add_argument("--text", help="timeout output; stdin or provider logs when omitted")
    inferred.add_argument("--message", default=DEFAULT_MESSAGE)
    inferred.add_argument("--delay", default="1 minute", help="wait after reset (default: 1 minute)")
    inferred.set_defaults(handler=_infer)
    automatic = commands.add_parser(
        "auto",
        help="manage automatic scheduling for one session",
        description=(
            "on: wake only when the provider actually refused a turn and the "
            "session stopped there. force: wake at reset whenever usage is "
            "near the limit, refused or not."
        ),
    )
    automatic.add_argument("action", choices=("on", "force", "off", "status"))
    automatic.add_argument("provider", nargs="?", choices=provider_names())
    automatic.add_argument("session_id", nargs="?", help="session UUID or provider alias")
    automatic.set_defaults(handler=_auto)
    capture = commands.add_parser(
        "capture-claude-usage",
        help="record a Claude Code status-line payload from stdin",
    )
    capture.add_argument(
        "--chain-command",
        help="run and preserve an existing status-line command before usage output",
    )
    capture.add_argument(
        "--chain-command-base64",
        help="base64 form of an existing status-line command",
    )
    capture.set_defaults(handler=_capture_claude_usage)
    monitor = commands.add_parser("monitor", help="run one local usage monitor pass")
    monitor.set_defaults(handler=_monitor)
    doctor = commands.add_parser("doctor", help="inspect local wakeup capabilities")
    doctor.set_defaults(handler=_doctor)
    for name, handler in (("setup", _setup), ("teardown", _teardown)):
        integration = commands.add_parser(name, help=f"{name} feature-owned wakeup integration")
        integration.add_argument("--bin-dir", type=Path, required=True)
        integration.add_argument("--native-root", type=Path, required=True)
        if name == "setup":
            integration.add_argument("--canonical-python", type=Path, required=True)
            integration.add_argument("--plugin-root", type=Path, required=True)
        integration.set_defaults(handler=handler)
    return parser


def _setup(args: argparse.Namespace) -> None:
    from ._linux_osx_windows import setup_integration

    setup_integration(
        python=args.canonical_python,
        plugin_root=args.plugin_root,
        bin_dir=args.bin_dir,
        native_root=args.native_root,
    )


def _teardown(args: argparse.Namespace) -> None:
    from ._linux_osx_windows import teardown_integration

    teardown_integration(
        bin_dir=args.bin_dir,
        native_root=args.native_root,
    )


def _print_scheduled(job: dict) -> None:
    """Render a scheduled job using the operator's local timezone."""

    local_time = datetime.fromisoformat(job["run_at"]).astimezone()
    clock = local_time.strftime("%I:%M %p").lstrip("0")
    zone = local_time.tzname() or local_time.strftime("%z")
    display_time = f"{local_time.strftime('%b')} {local_time.day}, {local_time.year}, {clock} {zone}"
    action = "Already scheduled" if job.get("deduplicated") else "Scheduled"
    print(f"{action} {job['provider']} wakeup for {job['session_id']} at {display_time} (id={job['id']})")


def _schedule(args: argparse.Namespace) -> None:
    """Handle explicit provider, session, reset, delay, and message input."""

    job = schedule(args.provider, args.session_id, parse_deadline(args.time) + parse_delay(args.delay), args.message)
    _print_scheduled(job)


def _infer(args: argparse.Namespace) -> None:
    """Infer a wakeup from supplied text, stdin, or recent provider logs."""

    text = args.text if args.text is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
    if text.strip():
        provider = infer_provider(text)
        session_id = infer_session_token(provider, text) or latest_session(provider)[0]
        reset_at = parse_deadline(text, embedded=True)
        context = text
    else:
        detected = latest_rate_limit()
        provider, session_id, reset_at, context = detected.provider, detected.session_id, detected.reset_at, detected.context
    job = schedule(provider, session_id, reset_at + parse_delay(args.delay), args.message, context=context)
    _print_scheduled(job)


def _doctor(args: argparse.Namespace) -> None:
    """Render capability diagnostics and return their aggregate exit status."""

    raise SystemExit(render_diagnostics())


def _auto_target(provider: str | None, session: str | None) -> tuple[str, str]:
    """Resolve an explicit or newest unambiguous policy target."""

    if provider and session:
        return provider, resolve_session(provider, session)[0]
    if provider:
        return provider, latest_session(provider)[0]
    candidates: list[tuple[float, str, str]] = []
    for name in provider_names():
        try:
            session_id, transcript = latest_session(name)
        except WakeupError:
            continue
        candidates.append((transcript.stat().st_mtime, name, session_id))
    candidates.sort(reverse=True)
    if not candidates:
        raise WakeupError("could not find a recent Claude or Codex session")
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 30:
        raise WakeupError("recent session is ambiguous; include provider and session ID")
    _, name, session_id = candidates[0]
    return name, session_id


_LEVEL_DESCRIPTION = {
    INTERRUPTED: "enabled (only when a usage limit stopped the session)",
    FORCE: "enabled (whenever usage nears the limit)",
}


def _auto(args: argparse.Namespace) -> None:
    """Enable at a level, disable, or inspect scheduling for one session."""

    provider, session_id = _auto_target(args.provider, args.session_id)
    if args.action in ("on", "force"):
        level = FORCE if args.action == "force" else INTERRUPTED
        set_auto_schedule(provider, session_id, True, level)
    elif args.action == "off":
        set_auto_schedule(provider, session_id, False)
    level = auto_schedule_level(provider, session_id)
    state = _LEVEL_DESCRIPTION.get(level, "disabled")
    print(f"Auto-scheduling is {state} for {provider} session {session_id}")


def _capture_claude_usage(args: argparse.Namespace) -> None:
    """Record stdin quota data and render a compact Claude status segment."""

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError) as error:
        raise WakeupError(f"invalid Claude status payload: {error}") from error
    if not isinstance(payload, dict):
        raise WakeupError("invalid Claude status payload: expected a JSON object")
    chain_command = args.chain_command
    if args.chain_command_base64:
        try:
            chain_command = base64.b64decode(
                args.chain_command_base64, validate=True
            ).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise WakeupError("invalid encoded status-line command") from error
    if chain_command:
        chained = subprocess.run(
            chain_command,
            input=raw,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        sys.stdout.write(chained.stdout)
        if chained.stdout and not chained.stdout.endswith("\n"):
            sys.stdout.write("\n")
        sys.stderr.write(chained.stderr)
    snapshots = capture_claude_status(payload)
    labels = {"five_hour": "5h", "seven_day": "7d"}
    usage = " ".join(
        f"{labels.get(item.window, item.window)} {item.used_percentage:g}%"
        for item in snapshots
    )
    if not usage:
        return
    near = any(item.used_percentage >= NEAR_LIMIT_PERCENT for item in snapshots)
    level = auto_schedule_level("claude", str(payload.get("session_id") or ""))
    reminder = ""
    if near and level is None:
        reminder = " | nearing limit: lw auto on claude (or force)"
    elif near and level == INTERRUPTED:
        reminder = " | wakeup armed if the limit stops this session"
    elif near:
        reminder = " | automatic wakeup enabled"
    print(f"Claude usage: {usage}{reminder}")


def _monitor(args: argparse.Namespace) -> None:
    """Run one quota-monitor pass and print journald-friendly actions."""

    for action in monitor_usage():
        used = (
            "cutoff"
            if action.used_percentage is None
            else f"{action.used_percentage:g}"
        )
        print(
            f"usage-{action.kind} provider={action.provider} "
            f"session={action.session_id} used={used} "
            f"resets_at={action.resets_at}"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and convert expected operational failures to exit status 2.

    An empty argument vector is intentionally equivalent to ``infer`` so the
    short ``lw`` command is sufficient immediately after leaving a limited
    Claude or Codex terminal session. ``run-due`` remains an internal systemd
    worker surface and is therefore omitted from public argparse help.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    # The common post-timeout workflow needs no explicit subcommand: inference
    # discovers the provider, session, and reset deadline from structured logs.
    if not arguments:
        arguments = ["infer"]
    try:
        if arguments and arguments[0] == "run-due":
            argparse.ArgumentParser(prog="llm-wakeup run-due").parse_args(arguments[1:])
            try:
                _monitor(argparse.Namespace())
            except Exception as error:
                print(f"usage-monitor-error detail={error}", file=sys.stderr, flush=True)
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
