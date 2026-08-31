"""Run the recurring-tasks healthcheck through recurring-owned control."""

from __future__ import annotations

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
    """Expose the health checker through the Python machine interface."""

    prog = "healthcheck_probe.py"

    def run(self, argv: list[str]) -> int:
        """Delegate healthcheck arguments to the module entry point."""
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the managed recurring-task healthcheck and return its status."""
    if argv:
        print(f"error: unexpected arguments: {' '.join(argv)}", file=sys.stderr)
        return 2
    return run_managed_control("healthcheck")


if __name__ == "__main__":
    raise SystemExit(main())
