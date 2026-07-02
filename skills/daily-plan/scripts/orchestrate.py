#!/usr/bin/env python3
"""Generate or show today's daily plan."""
from __future__ import annotations

import argparse
import sys

from plan_runtime import PlanError, generate_plan, get_today_date, plan_exists, refresh_rendered_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or show today's daily plan.")
    parser.add_argument("--forced", action="store_true", help="Regenerate the plan even if it already exists")
    args = parser.parse_args()

    try:
        date_key = get_today_date()
        if args.forced or not plan_exists(date_key):
            print(generate_plan(date_key), end="")
        else:
            print(refresh_rendered_plan(date_key), end="")
        return 0
    except PlanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
