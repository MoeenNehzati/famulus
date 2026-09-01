"""Read-only verifier for the getter-projected milestone logging directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface


GETTER_KEY = "logging-path"


class Interface(PythonMachineInterface):
    """Report whether the canonical hosted logging directory exists.

    Intent
    ------
    Expose one read-only setup verifier through the Dispatcher.
    Rationale
    ---------
    The verifier must resolve the selected path through the declared getter
    rather than infer it from environment or legacy readiness state.
    Pseudocode
    ----------
    - set prog = `milestone-logging-setup-status`
    Wraps
    -----
    - none
    """

    prog = "milestone-logging-setup-status"
    dispatches = {
        GETTER_KEY: DispatchCall(
            caller_module_id="milestone-logging._rtx",
            target_module_id="common",
            interface="famulus-paths-get",
            version=1,
            smoke_args=("logging-path",),
        )
    }

    def run(self, _arguments: argparse.Namespace) -> int:
        """Resolve only logging-path and print the exact verifier object.

        Intent
        ------
        Decide setup from the getter-projected logging directory alone.
        Rationale
        ---------
        Excluding the legacy setup-status record keeps MCP readiness separate
        from managed setup state.
        Pseudocode
        ----------
        - set result = logging-path dispatch result
        - return exact setup-verifier status
        Wraps
        -----
        - none
        """
        result = self.dispatch(GETTER_KEY, args=("logging-path",), text=True)
        if result.returncode != 0:
            return result.returncode
        lines = result.stdout.splitlines()
        if len(lines) != 1 or not Path(lines[0]).is_absolute():
            return 2
        print(json.dumps({"set_up": Path(lines[0]).is_dir()}))
        return 0


if __name__ == "__main__":
    raise SystemExit(Interface().main())
