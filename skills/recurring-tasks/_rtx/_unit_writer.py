#!/usr/bin/env python3
"""Regenerate host scheduler entries from jobs.yaml."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

RTX_DIR = Path(__file__).resolve().parent
if not __package__ and str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

if __package__:
    from ._managed_control import run as run_managed_control
else:
    from _managed_control import run as run_managed_control  # noqa: E402


class Interface(PythonArgvMachineInterface):
    prog = "unit_writer.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    return run_managed_control("sync")


if __name__ == "__main__":
    raise SystemExit(main())
