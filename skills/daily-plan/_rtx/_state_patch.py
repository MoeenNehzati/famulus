#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._day_model import (
        DISPATCHES,
        PlanError,
        get_today_date,
        mutate_plan,
        parse_indices,
        set_dispatch_interface,
    )
except ImportError:
    from _day_model import (
        DISPATCHES,
        PlanError,
        get_today_date,
        mutate_plan,
        parse_indices,
        set_dispatch_interface,
    )


class Interface(PythonArgvMachineInterface):
    dispatches = DISPATCHES
    prog = "state_patch.py"

    def run(self, argv: list[str]) -> int:
        set_dispatch_interface(self)
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mutate today's rendered daily plan and show the refreshed result.")
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

    args = parser.parse_args(argv)
    try:
        date_key = get_today_date()
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
