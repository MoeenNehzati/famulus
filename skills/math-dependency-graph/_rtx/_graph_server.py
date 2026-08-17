#!/usr/bin/env python3
"""Expose the shared no-cache visualization server as a skill interface."""

from __future__ import annotations

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.visualization.server import (
    NoCacheRequestHandler,
    ReusableThreadingHTTPServer,
    main,
    parse_args,
    valid_port,
)


class Interface(PythonArgvMachineInterface):
    prog = "graph_server.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
