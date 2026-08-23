"""Dispatcher gateway for the milestone writer's stable argv contract."""
from __future__ import annotations

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from ._milestone_writer import main as _writer_main


class Interface(PythonArgvMachineInterface):
    """Interface exposes the milestone writer argv boundary.

    Intent
    ------
    Provide the dispatcher-visible adapter for the stable milestone argument contract.

    Rationale
    ---------
    Keeping dispatch separate from the writer lets the compatibility CLI remain a small focused implementation.

    Pseudocode
    ----------
    - set prog = `milestone`
    - return interface

    Wraps
    -----
    - none
    """

    prog = "milestone"

    def run(self, argv: list[str]) -> int:
        """Delegate dispatcher arguments to the milestone writer.

        Intent
        ------
        Preserve the writer's argument parsing, output, and exit status.

        Rationale
        ---------
        The dispatcher boundary must not reinterpret the stable CLI contract.

        Pseudocode
        ----------
        - return @._writer_main(argv)

        Wraps
        -----
        _writer_main -> preprocess: pass argv unchanged; postprocess: return the writer status unchanged; fixed_arguments: none
        """
        return _writer_main(argv)


if __name__ == "__main__":
    raise SystemExit(Interface().main())
