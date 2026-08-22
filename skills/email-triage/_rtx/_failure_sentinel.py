#!/usr/bin/env python3
"""Record that this triage run failed, so update_watermark.py refuses to advance.

Call this as soon as a list-manager add/update fails (Step 4). Do not call
scripts-update-watermark afterward in the same run — this file is the guard
that makes that mistake safe even if the instruction is skipped.

Usage: mark_failure.py "<reason>"
"""
import json
import os
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

SKILL_DIR = Path(__file__).resolve().parent.parent


def default_state_dir(*, home: Path | None = None) -> Path:
    """Resolve the mutable state root for email-triage.

    Defaults to the shared Famulus state root (not SKILL_DIR/state, which may
    be a read-only installed/plugin tree). Overridable via
    EMAIL_TRIAGE_STATE_DIR so tests and CI can point at a tmp_path instead of
    the real state directory.
    """
    override = os.environ.get("EMAIL_TRIAGE_STATE_DIR")
    if override:
        return Path(override)
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(
        platform=sys.platform, home=home or Path.home(), environ=os.environ
    ).email_triage_state_root


STATUS_FILE = default_state_dir() / "status.json"

class Interface(PythonArgvMachineInterface):
    prog = "mark_failure.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reason = argv[0] if argv else "triage run failed (no reason given)"

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({"result": "error", "message": reason}, indent=2))
    print(f"Triage marked as failed: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
