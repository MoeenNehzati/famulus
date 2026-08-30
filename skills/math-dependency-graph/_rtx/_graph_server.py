#!/usr/bin/env python3
"""Expose the shared no-cache visualization server as a skill interface."""

from __future__ import annotations

import json
import sys

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.visualization.server import (
    NoCacheRequestHandler,
    ReusableThreadingHTTPServer,
    parse_args,
    start_graph_server,
    valid_port,
)


class Interface(PythonArgvMachineInterface):
    prog = "graph_server.py"

    def run(self, argv: list[str]) -> int:
        args = parse_args(argv)
        server = start_graph_server(args.directory, host=args.host, port=args.port)
        print(json.dumps({
            "serving": str(server.directory),
            "host": server.host,
            "port": server.port,
            "url": server.url,
            "cache": "disabled",
            "pid": server.process.pid,
        }))
        return 0


if __name__ == "__main__":
    raise SystemExit(Interface().run(sys.argv[1:]))
