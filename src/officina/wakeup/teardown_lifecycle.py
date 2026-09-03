"""Argument-free managed teardown action and verifier for the wakeup integration."""
from __future__ import annotations

import json

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .linux_osx_windows import (
    integration_installed,
    selected_integration_paths,
    teardown_integration,
)


def reconcile() -> None:
    """Remove the feature-owned integration from derived paths."""

    _, _, bin_dir, native_root = selected_integration_paths()
    teardown_integration(native_root=native_root, bin_dir=bin_dir)


class Interface(PythonArgvMachineInterface):
    """Dispatcher-compatible managed teardown action for the wakeup feature."""

    prog = "llm-wakeup-teardown"

    def run(self, argv: list[str]) -> int:
        """Remove the integration; the managed lifecycle passes no arguments."""

        if argv:
            raise SystemExit("llm-wakeup-teardown takes no arguments")
        reconcile()
        return 0


class StatusInterface(PythonArgvMachineInterface):
    """Read-only managed teardown verifier for the wakeup feature."""

    prog = "llm-wakeup-teardown-status"

    def run(self, argv: list[str]) -> int:
        """Print the exact boolean payload the setup manager accepts."""

        if argv:
            raise SystemExit("llm-wakeup-teardown-status takes no arguments")
        print(json.dumps({"torn_down": not integration_installed()}))
        return 0
