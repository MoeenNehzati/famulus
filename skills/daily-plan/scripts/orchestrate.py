#!/usr/bin/env python3
"""Orchestrate daily plan generation without LLM involvement.

This script coordinates all dependencies (g-calendar, list-manager, get-weather,
cloud-files) via the dispatcher to build and persist a daily plan for today.

Invocation:
  orchestrate.py produce    # Generate and save plan
  orchestrate.py output     # Show existing plan (or generate if missing)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "skills"


class OrchestratorError(Exception):
    """Raised when orchestration fails."""


def run_dispatcher(
    target_skill: str,
    script_interface: str,
    *args: str,
    stdin: str | None = None,
) -> str:
    """Call a dependency via the dispatcher.

    Args:
        target_skill: Skill to invoke
        script_interface: Interface name
        *args: Arguments to pass to the interface
        stdin: Optional stdin to pass

    Returns:
        stdout from the command

    Raises:
        OrchestratorError if the dispatcher call fails
    """
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "invoke_skill_export.py"),
        "--caller-skill", "daily-plan",
        target_skill,
        script_interface,
        *args,
    ]

    try:
        result = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OrchestratorError(
                f"dispatcher failed for {target_skill}:{script_interface}: {result.stderr}"
            )
        return result.stdout
    except Exception as e:
        raise OrchestratorError(f"Failed to invoke {target_skill}:{script_interface}: {e}")


def get_today_date() -> str:
    """Get today's date as M-D-YY format for plan filename."""
    today = datetime.now()
    return today.strftime("%-m-%-d-%y").lstrip("0")


def plan_exists(date_key: str) -> bool:
    """Check if plan file exists in cloud storage."""
    try:
        run_dispatcher("cloud-files", "plans-read", f"plans/{date_key}.md")
        return True
    except OrchestratorError:
        return False


def read_plan(date_key: str) -> str:
    """Read existing plan from cloud storage."""
    return run_dispatcher("cloud-files", "plans-read", f"plans/{date_key}.md")


def write_plan(date_key: str, content: str) -> None:
    """Write plan to cloud storage."""
    run_dispatcher("cloud-files", "plans-write", f"plans/{date_key}.md", stdin=content)


def get_calendar_today() -> list[str]:
    """Get today's calendar events."""
    output = run_dispatcher("g-calendar", "scripts-gcal", "agenda", "--all-calendars")
    return output.strip().split("\n") if output.strip() else []


def get_calendar_week() -> list[str]:
    """Get next 7 days of calendar events."""
    output = run_dispatcher("g-calendar", "scripts-gcal", "agenda", "--all-calendars", "--days", "7")
    return output.strip().split("\n") if output.strip() else []


def get_weather() -> str:
    """Get today's weather."""
    return run_dispatcher("get-weather", "scripts-weather").strip()


def read_list(list_name: str) -> dict[str, Any]:
    """Read a list from list-manager via cloud-files."""
    yaml_data = run_dispatcher("list-manager", "read-list", f"lists/{list_name}.yaml")

    # Parse YAML manually (simple parser for this case)
    result = {"items": []}
    current_section = None

    for line in yaml_data.split("\n"):
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        # Very basic YAML parsing - sufficient for our lists
        if line.startswith("- "):
            result["items"].append(line[2:])

    return result


def parse_calendar_line(line: str) -> dict[str, str] | None:
    """Parse a calendar event line from gcal output.

    Format: <start> -> <end>  <title>  [calendar: <name>, id: <id>]
    """
    if not line.strip() or line.startswith("No"):
        return None

    # Extract calendar name
    calendar_match = re.search(r"\[calendar: ([^,]+),", line)
    calendar = calendar_match.group(1) if calendar_match else "Unknown"

    # Extract title (between times and bracket)
    bracket_pos = line.rfind("[")
    if bracket_pos > 0:
        parts = line[:bracket_pos].split()
        if len(parts) >= 4:  # start -> end title...
            title = " ".join(parts[3:])
            # Extract times
            time_part = parts[0]
            return {
                "time": time_part,
                "title": title,
                "calendar": calendar,
                "line": line,
            }

    return None


def calculate_free_time(events: list[dict]) -> tuple[int, str]:
    """Calculate free time budget from calendar events.

    Returns: (free_hours, description)
    """
    # Simple implementation: 10-hour work budget minus event durations
    # In a full implementation, this would parse times and calculate durations

    total_hours = 10
    busy_hours = 0
    commute_hours = 1  # Default estimate

    for event in events:
        # Very rough: assume ~1.5 hours per event on average
        busy_hours += 1.5

    free_hours = max(0, total_hours - busy_hours - commute_hours)
    return int(free_hours), f"{int(busy_hours)}h activities + {commute_hours}h commute"


def build_plan(date_key: str, today_date: str) -> str:
    """Build the daily plan from all gathered data in parallel."""
    lines = [f"# Plan: {today_date}\n"]

    try:
        # Gather all data in parallel using thread pool
        with ThreadPoolExecutor(max_workers=4) as executor:
            # Submit all independent tasks
            futures = {
                "calendar_today": executor.submit(get_calendar_today),
                "calendar_week": executor.submit(get_calendar_week),
                "weather": executor.submit(get_weather),
                "todo": executor.submit(lambda: read_list("todo")),
            }

            # Collect results as they complete
            results = {}
            for name, future in futures.items():
                try:
                    results[name] = future.result()
                except OrchestratorError as e:
                    results[name] = None
                    print(f"⚠️ Failed to fetch {name}: {e}", file=sys.stderr)

        # Build plan from parallel results
        # Calendar section
        lines.append("## Calendar")
        calendar_events = results.get("calendar_today") or []
        if calendar_events:
            for line in calendar_events:
                parsed = parse_calendar_line(line)
                if parsed:
                    lines.append(f"- {parsed['time']}: {parsed['title']}")
        else:
            lines.append("(no events today)")

        # Calculate free time
        free_hours, breakdown = calculate_free_time(calendar_events)
        lines.append(f"\nFree time: ~{free_hours}h (10h budget - {breakdown})\n")

        # Weather section
        lines.append("## The Day")
        weather = results.get("weather") or ""
        if weather:
            # Ensure 2 sentences
            sentences = weather.split(". ")
            weather_text = ". ".join(sentences[:2])
            if not weather_text.endswith("."):
                weather_text += "."
            lines.append(weather_text)
        else:
            lines.append("(weather unavailable)")
        lines.append("")

        # Upcoming events section
        lines.append("## Upcoming")
        all_events = results.get("calendar_week") or []
        upcoming = []
        for line in all_events:
            parsed = parse_calendar_line(line)
            if parsed and "birthday" in parsed.get("calendar", "").lower():
                upcoming.append(f"- {parsed['title']}")

        if upcoming:
            lines.extend(upcoming)
        else:
            lines.append("(none this week)")
        lines.append("")

        # Actions section
        lines.append("## Actions (suggestions)")
        todo = results.get("todo")
        if todo and todo.get("items"):
            for idx, item in enumerate(todo["items"][:5], 1):
                lines.append(f"{idx}. [ ] {item}")
        elif todo is None:
            lines.append("(todo list unavailable)")
        else:
            lines.append("(nothing on the todo list)")

        lines.append("\n→ Tell me which items you're keeping and I'll finalize the plan.\n")

    except Exception as e:
        lines.append(f"\n⚠️ Unexpected error: {e}\n")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrate daily plan generation without LLM involvement."
    )
    parser.add_argument(
        "mode",
        choices=["produce", "output"],
        help="produce: generate and save new plan; output: show existing plan",
    )
    args = parser.parse_args()

    try:
        date_key = get_today_date()

        # Check if plan exists
        exists = plan_exists(date_key)

        if args.mode == "produce":
            if exists:
                print(f"Plan for today ({date_key}) already exists.")
                print(read_plan(date_key))
                return 0

            # Generate plan
            today_str = datetime.now().strftime("%B %d, %Y")
            plan = build_plan(date_key, today_str)
            write_plan(date_key, plan)
            print(plan)
            return 0

        else:  # output mode
            if not exists:
                print("No plan found. Generating...")
                today_str = datetime.now().strftime("%B %d, %Y")
                plan = build_plan(date_key, today_str)
                write_plan(date_key, plan)
                print(plan)
                return 0

            # Show existing plan
            print(read_plan(date_key))
            return 0

    except OrchestratorError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
