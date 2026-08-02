"""Platform-neutral module entrypoint for the wakeup command."""

from __future__ import annotations

from . import cli_main


if __name__ == "__main__":
    raise SystemExit(cli_main())
