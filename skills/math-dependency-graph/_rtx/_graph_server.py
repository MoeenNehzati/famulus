#!/usr/bin/env python3
"""Serve current files from one directory over threaded, no-cache HTTP.

The process emits readiness JSON and then delegates static-file requests to the
standard library. It does not parse request bodies or graph JSON, mutate graph
artifacts, or restrict service to graph file types.
"""

from __future__ import annotations

import argparse
import functools
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


class NoCacheRequestHandler(SimpleHTTPRequestHandler):
    """Add cache-disabling headers to the standard static-file handler.

    Intent
    ------
    Override only header completion; inherit request routing unchanged.

    Rationale
    ---------
    The inherited handler may render directory listings. Its path translation
    blocks lexical parent traversal but follows symlinks, so the served directory
    is not a containment boundary for symlink targets.

    Pseudocode
    ----------
    - set end_headers = cache directives followed by inherited header completion

    Wraps
    -----
    - none
    """

    def end_headers(self) -> None:
        """Append three cache directives before emitting buffered headers.

        Intent
        ------
        Disable browser caching for responses that reach this hook.

        Rationale
        ---------
        One completion override covers files, listings, redirects, and errors.

        Pseudocode
        ----------
        - set buffered_headers = existing headers plus Cache-Control Pragma and Expires
        - return super().end_headers()

        Wraps
        -----
        - none
        """
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    """Enable address reuse on the inherited threaded HTTP server.

    Intent
    ------
    Allow a recently closed listening address to be rebound.

    Rationale
    ---------
    Thread creation and daemon-thread behavior remain entirely inherited; this
    subclass changes only the socket address-reuse class attribute.

    Pseudocode
    ----------
    - set allow_reuse_address = true

    Wraps
    -----
    - none
    """

    allow_reuse_address = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse ``--directory``, ``--host``, and integer ``--port`` options.

    Intent
    ------
    Return the three values used by ``main`` to construct the server.

    Rationale
    ---------
    Direct and passthrough execution share this parser. It checks option syntax
    and integer conversion, but not directory existence or port bounds; later
    path and socket operations determine whether the values work.

    Pseudocode
    ----------
    - set directory = option --directory default dot
    - set host = option --host default 127.0.0.1
    - set port = integer option --port default 8765
    - return parsed namespace

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(
        description="Serve graph HTML from a local directory with no-cache headers."
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="Directory to serve. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host/interface to bind. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind. Defaults to 8765.",
    )
    return parser.parse_args(argv)


class Interface(PythonArgvMachineInterface):
    """Name the argv-preserving machine interface for this server.

    Intent
    ------
    Supply the program label used by the inherited interface lifecycle.

    Rationale
    ---------
    Argument passthrough is inherited and ``run`` owns delegation; the class body
    contributes only the stable program name.

    Pseudocode
    ----------
    - set prog = graph_server.py

    Wraps
    -----
    - none
    """

    prog = "graph_server.py"

    def run(self, argv: list[str]) -> int:
        """Delegate unchanged argv to ``main`` and return zero on completion.

        Intent
        ------
        Adapt the CLI lifecycle to the machine interface's integer result.

        Rationale
        ---------
        Zero is reached only when ``main`` returns; escaping failures remain unchanged.

        Pseudocode
        ----------
        - return 0 after main(argv)

        Wraps
        -----
        .main -> preprocess: forwards the received argv unchanged; postprocess: returns zero after main completes; fixed_arguments: none
        """
        main(argv)
        return 0


def main(argv: list[str] | None = None) -> None:
    """Run the long-lived static-file server from parsed CLI values.

    Intent
    ------
    Own bind, readiness, serving, interrupt suppression, and close ordering.

    Rationale
    ---------
    Non-strict path resolution lets a missing directory reach the server and
    produce request-time 404 responses. No direct or passthrough validation
    enforces the blueprint's directory or port declarations.

    Construction binds before readiness output and before the ``try`` block.
    Readiness reports requested host and port, not the bound address, so port
    zero is announced as zero. Only ``KeyboardInterrupt`` from
    ``serve_forever`` is suppressed. Other failures propagate, except that
    unguarded ``server_close`` in ``finally`` can replace an active exception;
    failures before the ``try`` do not reach that explicit cleanup.

    Pseudocode
    ----------
    - parsed_args = parse_args(argv)
    - set directory = Path(parsed_args.directory).resolve()
    - set handler = functools.partial(NoCacheRequestHandler directory)
    - server = ReusableThreadingHTTPServer(parsed_args host port handler)
    - set readiness_json = json.dumps serving directory requested URL and disabled cache
    - set stdout = print readiness_json with flush true
    - set serve_forever = server.serve_forever until return or exception
    - if KeyboardInterrupt:
      - set suppression = pass
    - set finally_close = server.server_close()
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .parse_args:
      why:
        constructs: "Builds the parsed directory and requested bind values consumed by the server lifecycle."
    .ReusableThreadingHTTPServer:
      why:
        constructs: "Builds the bound threaded listener that owns request handling until shutdown."
    """
    args = parse_args(argv)
    directory = Path(args.directory).resolve()
    handler = functools.partial(NoCacheRequestHandler, directory=str(directory))
    server = ReusableThreadingHTTPServer((args.host, args.port), handler)
    print(
        json.dumps(
            {
                "serving": str(directory),
                "url": f"http://{args.host}:{args.port}/",
                "cache": "disabled",
            },
            indent=2,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
