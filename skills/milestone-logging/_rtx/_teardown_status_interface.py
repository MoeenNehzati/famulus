"""Read-only verifier for milestone logging's retention-preserving teardown."""

from __future__ import annotations

import argparse
import json

from officina.runtime.python_machine_interface import PythonMachineInterface


class Interface(PythonMachineInterface):
    """Confirm the declared no-op teardown without touching external state.

    Intent
    ------
    Expose one read-only teardown verifier through the Dispatcher.
    Rationale
    ---------
    Milestone records are retained, so successful teardown has no external
    condition to inspect or mutate.
    Pseudocode
    ----------
    - set prog = `milestone-logging-teardown-status`
    Wraps
    -----
    - none
    """

    prog = "milestone-logging-teardown-status"

    def run(self, _arguments: argparse.Namespace) -> int:
        """Print the exact successful teardown-verifier object.

        Intent
        ------
        Confirm the no-op action using the manager's exact verifier shape.
        Rationale
        ---------
        Returning a fixed result prevents teardown verification from reading or
        changing retained milestone state.
        Pseudocode
        ----------
        - set result = `{"torn_down": true}`
        - return 0
        Wraps
        -----
        - none
        """
        print(json.dumps({"torn_down": True}))
        return 0


if __name__ == "__main__":
    raise SystemExit(Interface().main())
