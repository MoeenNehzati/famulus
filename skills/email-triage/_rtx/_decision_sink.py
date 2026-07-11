"""Append email-triage classification decisions to triage.log."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from officina.runtime.python_machine_interface import PythonMachineInterface


SKILL_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = SKILL_DIR / "triage.log"


class Interface(PythonMachineInterface):
    prog = "log-decision"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument("account")
        parser.add_argument("message_id")
        parser.add_argument("sender")
        parser.add_argument("subject")
        parser.add_argument("decision")
        parser.add_argument("reason")
        return parser

    def run(self, args: argparse.Namespace) -> int:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{timestamp}] [{args.account}] [ID:{args.message_id}] "
                f"{args.sender} | {args.subject} -> {args.decision}: {args.reason}\n"
            )
        return 0


def main(argv: list[str] | None = None) -> int:
    interface = Interface()
    parser = interface.build_parser()
    return interface.run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
