"""User-facing CLI for direct version-6 dispatch.

The stable resolver injects ``--repository-config`` before this module starts.
The option is hidden because it is trusted launcher state, not a user choice.
This layer formats results and warnings; routing, authorization, and execution
remain in :mod:`officina.dispatcher.direct_runtime`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .direct_runtime import (
    InvocationDiagnostic,
    InvocationError,
    _dispatch_host,
    _resolve_host_dispatch_metadata,
)


def _print_warning(diagnostic: InvocationDiagnostic) -> None:
    """Render one advisory diagnostic without changing the route outcome."""

    subject = f" [{diagnostic.subject}]" if diagnostic.subject is not None else ""
    print(
        f"warning: {diagnostic.code}: {diagnostic.message}{subject}",
        file=sys.stderr,
    )


def parse_cli() -> argparse.Namespace:
    """Parse the public dispatcher command while retaining opaque target argv."""

    parser = argparse.ArgumentParser(
        prog="dispatcher",
        description="Invoke a skill machine interface declared in blueprint.yaml.",
        epilog=(
            "Examples:\n"
            "  dispatcher --dry-run --caller-skill daily-plan "
            "list-manager._rtx.interface.read-list /tmp/todo.yaml state=incomplete\n"
            "  dispatcher --caller-skill daily-plan list-manager._rtx.interface.update-list "
            "/tmp/todo.yaml --file /tmp/todo-updates.yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repository-config",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--caller-skill",
        required=True,
        help="Owning skill requesting the invocation.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read stdin and forward it to the target command. Fails if the matched surface disallows stdin.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved invocation as JSON instead of executing it.",
    )
    parser.add_argument(
        "--error-format",
        choices=["text", "json"],
        default="text",
        help=(
            "Format for a dispatcher failure printed to stderr. `text` (default) "
            "prints `error: <message>`; `json` prints one schema-versioned JSON "
            "object with a stable machine-readable `code`."
        ),
    )
    parser.add_argument("target_or_skill")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    """Run a dry resolution or execute one interface and preserve its exit code."""

    args = parse_cli()
    script_args = list(args.rest)
    target = args.target_or_skill
    if ".interface." not in target:
        print(
            "error: target must be a fully qualified `<module>.interface.<name>` export",
            file=sys.stderr,
        )
        return 2

    try:
        if args.dry_run:
            payload = _resolve_host_dispatch_metadata(
                caller_skill=args.caller_skill,
                target=target,
                args=script_args,
                stdin_requested=args.stdin,
                repository_config=args.repository_config,
            ).as_payload()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        stdin = sys.stdin.buffer.read() if args.stdin else None
        completed = _dispatch_host(
            caller_skill=args.caller_skill,
            target=target,
            args=script_args,
            stdin=stdin,
            capture_output=True,
            check=False,
            warning_handler=_print_warning,
            repository_config=args.repository_config,
        )
    except InvocationError as exc:
        if args.error_format == "json" and hasattr(exc, "as_payload"):
            print(json.dumps(exc.as_payload()), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    if completed.stdout:
        if isinstance(completed.stdout, str):
            sys.stdout.write(completed.stdout)
        else:
            sys.stdout.buffer.write(completed.stdout)
    if completed.stderr:
        if isinstance(completed.stderr, str):
            sys.stderr.write(completed.stderr)
        else:
            sys.stderr.buffer.write(completed.stderr)
    return completed.returncode

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
