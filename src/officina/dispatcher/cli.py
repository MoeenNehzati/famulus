"""CLI entrypoint for the shared skill dispatcher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .direct_runtime import (
    InvocationDiagnostic,
    InvocationError,
    _dispatch_host,
    _resolve_host_dispatch_metadata,
)


_VERSION_MISMATCH_CODE = "dispatcher.interface_version_mismatch"


def _print_warning(diagnostic: InvocationDiagnostic) -> None:
    subject = f" [{diagnostic.subject}]" if diagnostic.subject is not None else ""
    print(
        f"warning: {diagnostic.code}: {diagnostic.message}{subject}",
        file=sys.stderr,
    )


def _split_target_version(raw: str) -> tuple[str, int | None]:
    """Split an optional trailing ``@<version>`` pin off a target id.

    Generated interface blocks in SKILL.md spell pinned uses as
    ``<module>.interface.<name>@<version>``, so accept that form directly.
    A target with no pin, or with a suffix that is not a positive decimal
    integer, is returned unchanged for the normal id validation to reject.
    """

    base, separator, suffix = raw.rpartition("@")
    if not separator or not (suffix.isascii() and suffix.isdigit()):
        return raw, None
    version = int(suffix)
    if version < 1:
        return raw, None
    return base, version


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dispatcher",
        description="Invoke a skill machine interface declared in blueprint.yaml.",
        epilog=(
            "Examples:\n"
            "  dispatcher --dry-run --caller-skill daily-plan "
            "list-manager._rtx.interface.read-list /tmp/todo.yaml state=incomplete\n"
            "  dispatcher --caller-skill daily-plan list-manager._rtx.interface.update-list "
            "/tmp/todo.yaml --file /tmp/todo-updates.yaml\n"
            "  dispatcher --caller-skill daily-plan "
            "daily-plan._rtx.interface.orchestrate@1"
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
    parser.add_argument(
        "target_or_skill",
        help=(
            "Target export as `<module>.interface.<name>`, with an optional "
            "`@<version>` pin. The pin is checked against the interface version "
            "the source declares; a stale pin warns and resolution continues."
        ),
    )
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_cli()
    script_args = list(args.rest)
    target, requested_version = _split_target_version(args.target_or_skill)
    if ".interface." not in target:
        print(
            "error: target must be a fully qualified `<module>.interface.<name>` export",
            file=sys.stderr,
        )
        return 2

    # Read stdin once: a stale pin retries resolution, and the buffer is empty
    # the second time through.
    stdin = sys.stdin.buffer.read() if args.stdin and not args.dry_run else None

    def attempt(version: int | None) -> Any:
        if args.dry_run:
            payload = _resolve_host_dispatch_metadata(
                caller_skill=args.caller_skill,
                target=target,
                args=script_args,
                stdin_requested=args.stdin,
                target_version=version,
                repository_config=args.repository_config,
            ).as_payload()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return None
        return _dispatch_host(
            caller_skill=args.caller_skill,
            target=target,
            args=script_args,
            stdin=stdin,
            capture_output=False,
            check=False,
            target_version=version,
            warning_handler=_print_warning,
            repository_config=args.repository_config,
        )

    try:
        try:
            completed = attempt(requested_version)
        except InvocationError as exc:
            if (
                requested_version is None
                or getattr(exc, "code", None) != _VERSION_MISMATCH_CODE
            ):
                raise
            _print_warning(
                InvocationDiagnostic(
                    severity="warning",
                    code="interface-version-pin-stale",
                    message=str(exc),
                    subject=target,
                )
            )
            completed = attempt(None)
    except InvocationError as exc:
        if args.error_format == "json" and hasattr(exc, "as_payload"):
            print(json.dumps(exc.as_payload()), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    if completed is None:
        return 0
    return completed.returncode

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
