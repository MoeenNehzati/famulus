"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import sys
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
    """Expose host setup through the Python machine interface."""

    prog = "setup_runner.py"

    def run(self, argv: list[str]) -> int:
        """Delegate setup arguments to the module entry point."""
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run recurring-task host setup and return the managed status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-python", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_managed_control("setup", python=args.canonical_python, plugin_root=args.plugin_root)


if __name__ == "__main__":
    raise SystemExit(main())
