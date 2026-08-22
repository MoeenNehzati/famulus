from __future__ import annotations

import subprocess
import os

from officina.recurring.runtime import (
    discover_runtime_root,
    load_public_schedule,
    write_managed_schedule,
)


def command(operation: str, arguments: list[str] | None = None) -> list[str]:
    runtime_root = discover_runtime_root()
    schedule = (
        write_managed_schedule(runtime_root=runtime_root, environ=os.environ)
        if operation in {"setup", "remove-context"}
        else load_public_schedule(runtime_root=runtime_root, environ=os.environ)
    )
    prefix = [str(schedule.runtime_resolver)]
    if schedule.bootstrap_python is not None:
        prefix.insert(0, str(schedule.bootstrap_python))
    return [
        *prefix,
        "-m", "officina.recurring.control",
        "--descriptor", str(schedule.descriptor_path),
        operation,
        *(arguments or []),
    ]


def run(operation: str, arguments: list[str] | None = None) -> int:
    return subprocess.run(command(operation, arguments), check=False).returncode


__all__ = ["command", "run"]
