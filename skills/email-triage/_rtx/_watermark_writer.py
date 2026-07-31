#!/usr/bin/env python3
"""Advance the triage watermark — but only if the run wasn't marked as failed.

State lives under the shared Famulus state root (see
officina.common.famulus_paths), not next to this script, so it stays valid
even when the skill's installed tree is read-only. Overridable via
EMAIL_TRIAGE_STATE_DIR for tests/CI.

Safety: if _rtx/_failure_sentinel.py was called earlier in this run, a
status.json with result="error" will be present. In that case this script
refuses to advance the watermark (exit 1) so no emails are silently skipped
on the next run. On success it resets status.json to result="ok".
"""
import json
import sys
from datetime import datetime
from pathlib import Path
import os

try:
    from officina.runtime.python_machine_interface import PythonArgvMachineInterface
    HAS_OFFICINA = True
except ImportError:
    HAS_OFFICINA = False

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

    return resolve_famulus_paths(platform=sys.platform, home=home or Path.home()).email_triage_state_root


WATERMARK = default_state_dir() / "last_run"
STATUS_FILE = default_state_dir() / "status.json"

if HAS_OFFICINA:
    class Interface(PythonArgvMachineInterface):
        prog = "update_watermark.py"

        def run(self, argv: list[str]) -> int:
            return main(argv)


def main(_argv: list[str] | None = None) -> int:
    WATERMARK.parent.mkdir(parents=True, exist_ok=True)

    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            status = {}
        if status.get("result") == "error":
            print(
                "error: last triage run was marked failed "
                f"({status.get('message', 'no reason given')}). "
                "Watermark NOT advanced to avoid skipping emails.",
                file=sys.stderr,
            )
            return 1

    now = datetime.now().astimezone()
    WATERMARK.write_text(now.isoformat())

    # Preserve existing metrics and other fields, just update result and timestamp
    status["result"] = "ok"
    status["watermark_advanced_at"] = now.isoformat()

    STATUS_FILE.write_text(json.dumps(status, indent=2))
    print(f"Watermark updated: {now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
