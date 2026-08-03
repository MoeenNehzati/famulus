"""CLI entrypoint for the shared skill dispatcher."""

from __future__ import annotations

import argparse
import json
import sys

from .core import (
    InvocationDiagnostic,
    InvocationError,
    _dispatch_host,
    _resolve_host_dispatch_metadata,
)


def _print_warning(diagnostic: InvocationDiagnostic) -> None:
    subject = f" [{diagnostic.subject}]" if diagnostic.subject is not None else ""
    print(
        f"warning: {diagnostic.code}: {diagnostic.message}{subject}",
        file=sys.stderr,
    )


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke a skill machine interface declared in blueprint.yaml.",
        epilog=(
            "Examples:\n"
            "  dispatcher --dry-run --caller-skill daily-plan "
            "list-manager.interface.read-list /tmp/todo.yaml state=incomplete\n"
            "  dispatcher --caller-skill daily-plan list-manager.interface.update-list "
            "/tmp/todo.yaml --file /tmp/todo-updates.yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser.add_argument("target_or_skill")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
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
        )
    except InvocationError as exc:
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
