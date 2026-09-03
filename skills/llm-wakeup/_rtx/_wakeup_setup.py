"""Argument-free managed setup action and verifier for the wakeup integration."""
from __future__ import annotations

import json

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._linux_osx_windows import (
    integration_installed,
    selected_integration_paths,
    setup_integration,
)


def reconcile() -> None:
    """Install or refresh the feature-owned integration from derived paths."""

    python, plugin_root, bin_dir, native_root = selected_integration_paths()
    setup_integration(
        python=python,
        plugin_root=plugin_root,
        bin_dir=bin_dir,
        native_root=native_root,
    )


class Interface(PythonArgvMachineInterface):
    """Dispatcher-compatible managed setup action for the wakeup feature."""

    prog = "llm-wakeup-setup"

    def run(self, argv: list[str]) -> int:
        """Reconcile the integration; the managed lifecycle passes no arguments."""

        if argv:
            raise SystemExit("llm-wakeup-setup takes no arguments")
        reconcile()
        return 0


class StatusInterface(PythonArgvMachineInterface):
    """Read-only managed setup verifier for the wakeup feature."""

    prog = "llm-wakeup-setup-status"

    def run(self, argv: list[str]) -> int:
        """Print the exact boolean payload the setup manager accepts."""

        if argv:
            raise SystemExit("llm-wakeup-setup-status takes no arguments")
        print(json.dumps({"set_up": integration_installed()}))
        return 0
