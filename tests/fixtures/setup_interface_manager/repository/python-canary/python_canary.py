"""In-process, effect-free lifecycle canary for setup-manager tests."""
from __future__ import annotations

import json

from officina.runtime.python_machine_interface import PythonMachineInterface


_state = "initial"


def reset_state() -> None:
    """Reset the test fixture between independent lifecycle cases."""
    global _state
    _state = "initial"


class SetupInterface(PythonMachineInterface):
    """Perform only the fixed setup transition."""

    def run(self, _args) -> int:
        global _state
        _state = "set-up"
        return 0


class SetupStatusInterface(PythonMachineInterface):
    """Report setup success only after the setup interface ran."""

    def run(self, _args) -> int:
        print(json.dumps({"set_up": _state == "set-up"}, separators=(",", ":")))
        return 0
