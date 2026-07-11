"""Base contract for Python implementations of machine interfaces."""
from __future__ import annotations

import argparse
from typing import Any


class PythonMachineInterface:
    """Base class for Python bindings of dispatcher machine interfaces.

    A skill-specific machine interface subclasses this class and implements
    parser construction plus normal execution. The shared runner owns the
    process lifecycle and route-smoke behavior, so individual skills do not
    copy that control flow.
    """

    description: str = ""
    parser_class: type[argparse.ArgumentParser] = argparse.ArgumentParser
    formatter_class: type[argparse.HelpFormatter] | None = None
    prog: str | None = None
    usage: str | None = None
    add_help: bool = True

    def build_parser(self) -> argparse.ArgumentParser:
        """Build and return the parser for this interface.

        The base parser includes shared runtime flags. Subclasses should call
        ``super().build_parser()`` and add only interface-specific arguments.
        This method should not read credentials, contact external services,
        write files, or perform the interface's real work.
        """

        kwargs: dict[str, Any] = {
            "prog": self.prog,
            "usage": self.usage,
            "description": self.description or None,
            "add_help": self.add_help,
        }
        if self.formatter_class is not None:
            kwargs["formatter_class"] = self.formatter_class
        parser = self.parser_class(**kwargs)
        parser.add_argument(
            "--route-smoke",
            action="store_true",
            help=argparse.SUPPRESS,
        )
        return parser

    def route_smoke(self) -> None:
        """Run optional local checks for dispatcher route-smoke mode.

        Reaching this hook already proves the subprocess launched, the module
        imported, the interface object was constructed, and the parser was
        built. Override only for cheap side-effect-free checks, such as import
        aliases or local binary presence.
        """

        return None

    def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]) -> Any:
        """Parse normal-mode argv before ``run``.

        Most interfaces should use the default argparse behavior. Legacy CLI
        adapters can override this to pass argv through while still sharing the
        standard route-smoke lifecycle.
        """

        return parser.parse_args(argv)

    def run(self, args: argparse.Namespace) -> int | None:
        """Execute the interface's real behavior in normal mode."""

        raise NotImplementedError


class PythonArgvMachineInterface(PythonMachineInterface):
    """Adapter base for existing Python CLIs that already own argv parsing.

    Subclasses implement ``run(argv)`` and delegate to the existing CLI entry
    point. This is a migration bridge: route-smoke still imports the module,
    constructs the interface object, and builds the shared parser, while normal
    execution preserves the script's current parser behavior.
    """

    def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
        """Return argv unchanged so the legacy CLI parser can handle it."""

        return argv


def coerce_exit_code(value: Any) -> int:
    """Convert a machine-interface return value into a process exit code."""

    if value is None:
        return 0
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int):
        return value
    raise TypeError(f"machine interface returned unsupported value {value!r}")
