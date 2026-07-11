#!/usr/bin/env python3
"""Shared helpers for enable-job.py and disable-job.py."""
import re, sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


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
    text = jobs_path.read_text()
    # Match from `- name: <name>` (with word boundary) through its own `enabled:` line,
    # stopping at any subsequent `- name:` entry. No DOTALL — line-by-line only.
    pattern = (
        rf'(- name: ["\']?{re.escape(name)}["\']?\b'
        rf'(?:\n(?![ \t]*- name:).*)*?\n[ \t]+enabled:)\s+\S+'
    )
    new, count = re.subn(pattern, rf'\1 {value}', text, flags=re.MULTILINE)
    if count == 0:
        print(f"Error: job '{name}' not found in {jobs_path}", file=sys.stderr)
        sys.exit(1)
    jobs_path.write_text(new)


if __name__ == "__main__":
    raise SystemExit(main())
