"""Dispatcher adapter for finite public Famulus path selection."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from . import FAMULUS_PATH_FIELDS, FamulusPaths


def main(argv: list[str] | None = None) -> int:
    """Print one explicitly named Famulus path for Dispatcher callers.

    Intent
    ------
    Parse the finite command contract and emit its selected absolute path.
    Rationale
    ---------
    A narrow argv adapter keeps arbitrary path lookup outside the interface.
    Pseudocode
    ----------
    - set selected_name = parsed finite positional argument
    - selected_path = @FamulusPaths.get(selected_name, platform, home, environment)
    - return successful status after printing selected_path
    Wraps
    -----
    - none
    CallsFromRepo
    -------------
    ..FamulusPaths.get:
      why:
        computes: "Selects the public path emitted by this finite command boundary."
    """
    parser = argparse.ArgumentParser(prog="famulus-paths-get")
    parser.add_argument("name", choices=FAMULUS_PATH_FIELDS)
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    print(FamulusPaths.get(
        arguments.name, platform=sys.platform, home=Path.home(), environ=os.environ
    ))
    return 0


class Interface(PythonArgvMachineInterface):
    """Expose the finite path getter through Dispatcher.

    Intent
    ------
    Bind the stable getter program name to the Python argv interface contract.
    Rationale
    ---------
    Keeping dispatch separate preserves the command module's finite semantics.
    Pseudocode
    ----------
    - set prog = `famulus-paths-get`
    - return interface
    Wraps
    -----
    - none
    """

    prog = "famulus-paths-get"

    def run(self, argv: list[str]) -> int:
        """Delegate Dispatcher arguments to the finite path command.

        Intent
        ------
        Preserve command parsing, output, failures, and exit status unchanged.
        Rationale
        ---------
        The Dispatcher boundary must not reinterpret the public getter contract.
        Pseudocode
        ----------
        - return @.main(argv)
        Wraps
        -----
        .main -> preprocess: pass argv unchanged; postprocess: return command status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
