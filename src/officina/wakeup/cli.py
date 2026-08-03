"""Platform-neutral module entrypoint for the wakeup command."""

from __future__ import annotations

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from . import cli_main


class Interface(PythonArgvMachineInterface):
    """Dispatcher-compatible adapter for the existing wakeup argument parser."""

    prog = "llm-wakeup"

    def run(self, argv: list[str]) -> int:
        """Execute one compiled wakeup invocation."""

        return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(cli_main())
