#!/usr/bin/env python3
"""Generate or show today's daily plan."""
from __future__ import annotations

import argparse
import sys

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._day_model import (
        DISPATCHES,
        PlanError,
        PlanNotFound,
        generate_plan,
        get_today_date,
        plan_path,
        refresh_rendered_plan,
        set_dispatch_interface,
    )
except ImportError:
    from _day_model import (
        DISPATCHES,
        PlanError,
        PlanNotFound,
        generate_plan,
        get_today_date,
        plan_path,
        refresh_rendered_plan,
        set_dispatch_interface,
    )


class Interface(PythonArgvMachineInterface):
    dispatches = DISPATCHES
    prog = "plan_orchestrate.py"

    def run(self, argv: list[str]) -> int:
        set_dispatch_interface(self)
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or show today's daily plan.")
    parser.add_argument("--forced", action="store_true", help="Regenerate the plan even if it already exists")
    args = parser.parse_args(argv)

    try:
        date_key = get_today_date()
        if args.forced:
            print(generate_plan(date_key), end="")
        else:
            try:
                print(refresh_rendered_plan(date_key), end="")
            except PlanNotFound as exc:
                if exc.path != plan_path(date_key):
                    raise
                print(generate_plan(date_key), end="")
        return 0
    except PlanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
