#!/usr/bin/env python3
from __future__ import annotations

import sys

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from . import _drive_gateway as cloud_files
except ImportError:
    import _drive_gateway as cloud_files


class Interface(PythonArgvMachineInterface):
    prog = "delete_llm_file.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    return cloud_files.run_entrypoint(
        cloud_files.delete_entrypoint,
        sys.argv[1:] if argv is None else argv,
        use_llm_root=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
