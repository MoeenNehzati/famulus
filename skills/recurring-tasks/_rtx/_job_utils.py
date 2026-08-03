#!/usr/bin/env python3
"""Shared helpers for enable-job.py and disable-job.py."""
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

if __package__:
    from ._jobs_config import load_jobs, write_jobs
else:
    from _jobs_config import load_jobs, write_jobs


class Interface(PythonArgvMachineInterface):
    prog = "job_utils.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(f"error: unexpected arguments: {' '.join(argv)}", file=sys.stderr)
        return 2
    return 0


def set_enabled(jobs_path: Path, name: str, value: str) -> None:
    """Flip the enabled field for a named job. Raises SystemExit on failure."""
    if value not in {"true", "false"}:
        raise ValueError("enabled value must be 'true' or 'false'")
    jobs = load_jobs(jobs_path)
    for job in jobs:
        if job["name"] == name:
            job["enabled"] = value == "true"
            write_jobs(jobs_path, jobs)
            return
    print(f"Error: job '{name}' not found in {jobs_path}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
