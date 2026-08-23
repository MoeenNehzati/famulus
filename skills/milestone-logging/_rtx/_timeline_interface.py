"""Dispatcher gateway for the agent-timeline reader's stable argv contract."""
from __future__ import annotations

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._agent_timeline import main as _timeline_main


class Interface(PythonArgvMachineInterface):
    """Interface exposes the agent timeline argv boundary.

    Intent
    ------
    Provide the dispatcher-visible adapter for the stable timeline argument contract.

    Rationale
    ---------
    Keeping dispatch separate from the reader leaves timeline behavior in its dedicated implementation.

    Pseudocode
    ----------
    - set prog = `agent-timeline`
    - return interface

    Wraps
    -----
    - none
    """

    prog = "agent-timeline"

    def run(self, argv: list[str]) -> int:
        """Delegate dispatcher arguments to the timeline reader.

        Intent
        ------
        Preserve the reader's argument parsing, output, and exit status.

        Rationale
        ---------
        The dispatcher boundary must not reinterpret the stable CLI contract.

        Pseudocode
        ----------
        - return @._timeline_main(argv)

        Wraps
        -----
        _timeline_main -> preprocess: pass argv unchanged; postprocess: return the reader status unchanged; fixed_arguments: none
        """
        return _timeline_main(argv)


if __name__ == "__main__":
    raise SystemExit(Interface().main())
