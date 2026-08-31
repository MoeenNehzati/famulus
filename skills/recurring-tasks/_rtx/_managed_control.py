from __future__ import annotations

import subprocess
import os

from officina.recurring.runtime import (
    build_managed_schedule,
    load_public_schedule,
    write_managed_schedule,
)
from officina.recurring.control import run_operation


def command(operation: str, arguments: list[str] | None = None) -> list[str]:
    schedule = load_public_schedule(environ=os.environ)
    return [
        str(schedule.python),
        "-m", "officina.recurring.control",
        "--plugin-root", str(schedule.plugin_root),
        "--descriptor", str(schedule.descriptor_path),
        operation,
        *(arguments or []),
    ]


def run(operation: str, arguments: list[str] | None = None, **selected) -> int:
    if operation == "setup":
        schedule = build_managed_schedule(
            python=selected["python"],
            plugin_root=selected["plugin_root"],
            environ=os.environ,
        )
        result = run_operation(schedule, operation="setup", name=None, lines=50)
        if result == 0:
            write_managed_schedule(
                python=schedule.python,
                plugin_root=schedule.plugin_root,
                environ=os.environ,
            )
        return result
    return subprocess.run(command(operation, arguments), check=False).returncode


__all__ = ["command", "run"]
