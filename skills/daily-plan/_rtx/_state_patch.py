#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._day_model import (
        DISPATCHES,
        PlanError,
        mutate_plan,
        normalize_plan_date,
        parse_indices,
        set_dispatch_interface,
    )
except ImportError:
    from _day_model import (
        DISPATCHES,
        PlanError,
        mutate_plan,
        normalize_plan_date,
        parse_indices,
        set_dispatch_interface,
    )


class Interface(PythonArgvMachineInterface):
    dispatches = DISPATCHES
    prog = "state_patch.py"

    def run(self, argv: list[str]) -> int:
        set_dispatch_interface(self)
        return main(argv)


def extract_date_arg(argv: list[str]) -> tuple[list[str], str | None]:
    cleaned: list[str] = []
    date_value: str | None = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            cleaned.extend(argv[i:])
            break
        if token == "--date":
            if date_value is not None:
                raise PlanError("--date may only be provided once")
            if i + 1 >= len(argv):
                raise PlanError("--date requires a value")
            date_value = argv[i + 1]
            i += 2
            continue
        if token.startswith("--date="):
            if date_value is not None:
                raise PlanError("--date may only be provided once")
            date_value = token.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned, date_value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mutate a rendered daily plan and show the refreshed result."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("hide", "show", "keep", "remove", "mark-done", "reject"):
        p = sub.add_parser(name)
        p.add_argument("section", choices=["actions", "triage"])
        p.add_argument("indices", help="comma-separated visible indices")

    p_deadline = sub.add_parser("set-deadline")
    p_deadline.add_argument("section", choices=["actions", "triage"])
    p_deadline.add_argument("indices", help="comma-separated visible indices")
    p_deadline.add_argument("deadline")

    p_add = sub.add_parser("add")
    p_add.add_argument("section", choices=["actions", "triage"])
    p_add.add_argument("item_id")

    try:
        raw_argv = sys.argv[1:] if argv is None else argv
        cleaned_argv, requested_date = extract_date_arg(list(raw_argv))
        args = parser.parse_args(cleaned_argv)
        date_key = normalize_plan_date(requested_date)
        if args.command == "add":
            result = mutate_plan(date_key, "add", section=args.section, item_id=args.item_id)
        elif args.command == "set-deadline":
            result = mutate_plan(date_key, "set-deadline", section=args.section, indices=parse_indices(args.indices), value=args.deadline)
        else:
            result = mutate_plan(date_key, args.command, section=args.section, indices=parse_indices(args.indices))
        print(result, end="")
        return 0
    except PlanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
