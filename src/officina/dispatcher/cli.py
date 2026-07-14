"""CLI entrypoint for the shared skill dispatcher."""

from __future__ import annotations

import argparse
import json
import sys

from .core import InvocationError, dispatch, resolve_dispatch_metadata


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke a skill machine interface declared in blueprint.yaml.",
        epilog=(
            "Examples:\n"
            "  dispatcher --dry-run --caller-skill daily-plan "
            "list-manager.machine.read-list /tmp/todo.yaml state=incomplete\n"
            "  dispatcher --caller-skill daily-plan list-manager.machine.update-list "
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
    target: str | None = None
    target_skill: str | None = None
    script_interface: str | None = None
    script_args = list(args.rest)

    if ".machine." in args.target_or_skill or ".llm." in args.target_or_skill:
        target = args.target_or_skill
    else:
        if not script_args:
            print("error: shorthand invocation requires <target-skill> <machine-interface>", file=sys.stderr)
            return 2
        target_skill = args.target_or_skill
        script_interface = script_args.pop(0)

    try:
        if args.dry_run:
            payload = resolve_dispatch_metadata(
                caller_skill=args.caller_skill,
                target=target,
                target_skill=target_skill,
                script_interface=script_interface,
                args=script_args,
                stdin_requested=args.stdin,
            ).as_payload()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        stdin = sys.stdin.buffer.read() if args.stdin else None
        completed = dispatch(
            caller_skill=args.caller_skill,
            target=target,
            target_skill=target_skill,
            script_interface=script_interface,
            args=script_args,
            stdin=stdin,
            capture_output=True,
            check=False,
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
