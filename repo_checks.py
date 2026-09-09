#!/usr/bin/env python3
"""Run named repository test and conformance-check suites."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "remote":
        from officina.repository.checks import remote

        return remote.main(arguments[1:])

    from officina.repository.checks.runner import main as runner_main

    return runner_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
