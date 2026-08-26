"""Append email-triage classification decisions to triage.log."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from officina.runtime.python_machine_interface import PythonMachineInterface


SKILL_DIR = Path(__file__).resolve().parent
LEGACY_LOG_FILE = SKILL_DIR / "triage.log"


def triage_log_path(*, home: Path | None = None) -> Path:
    """Return the managed email-triage log location without process overrides."""
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(
        platform=sys.platform, home=home or Path.home(), environ=os.environ
    ).email_triage_state_root / "triage.log"


def unmanaged_triage_log_path(*, home: Path | None = None) -> Path:
    """Return the explicit standalone/test log path, honoring its override."""
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override) / "triage.log"
    return triage_log_path() if home is None else triage_log_path(home=home)


def _migrate_legacy_log(
    destination: Path, *, legacy_file: Path | None = None
) -> None:
    if os.environ.get("EMAIL_TRIAGE_STATE_DIR"):
        return
    source = LEGACY_LOG_FILE if legacy_file is None else legacy_file
    if destination.exists() or not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


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
        log_file = unmanaged_triage_log_path()
        _migrate_legacy_log(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
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
