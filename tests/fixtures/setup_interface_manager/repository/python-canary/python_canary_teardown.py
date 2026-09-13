"""Effect-free teardown half of the setup-manager Python canary."""
from __future__ import annotations

import json

from officina.runtime.python_machine_interface import PythonMachineInterface


_torn_down = False


def reset_state() -> None:
    """Reset the test fixture between independent lifecycle cases."""
    global _torn_down
    _torn_down = False


class TeardownInterface(PythonMachineInterface):
    """Perform only the fixed teardown transition."""

    def run(self, _args) -> int:
        global _torn_down
        _torn_down = True
        return 0


class TeardownStatusInterface(PythonMachineInterface):
    """Report teardown success only after the teardown interface ran."""

    def run(self, _args) -> int:
        print(json.dumps({"torn_down": _torn_down}, separators=(",", ":")))
        return 0
