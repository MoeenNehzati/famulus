"""Set up recurring-tasks scheduler state for this host."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import (
    PythonArgvMachineInterface,
    runtime_dispatch_context,
)

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
        """Set up recurring tasks from dispatcher-owned runtime identity."""
        plugin_root = runtime_dispatch_context(self).repo_root
        if plugin_root is None:
            raise RuntimeError("recurring setup requires the dispatcher plugin root")
        return main(
            argv,
            python=Path(sys.executable),
            plugin_root=plugin_root,
        )


def main(
    argv: list[str] | None = None,
    *,
    python: Path,
    plugin_root: Path,
) -> int:
    """Run recurring-task host setup and return the managed status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    return run_managed_control("setup", python=python, plugin_root=plugin_root)


if __name__ == "__main__":
    raise RuntimeError("recurring setup must be invoked through the dispatcher")
