#!/usr/bin/env python3
"""
Print the coarse `--after` cutoff date for email triage.

Usage:
  get_cutoff.py            — print watermark date (or 2-day default with warning if none exists)
  get_cutoff.py --days N   — compute cutoff for N days back (ignores watermark)

The printed date is meant to be passed directly to email-client's
`mail-list --after <date>`. IMAP's SINCE (which --after maps to) is a
day-level filter, so the cutoff here is offset by 1 day for safety; the
precise sub-day cutoff is applied afterward by filter_envelopes.py.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path
import os

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

    return resolve_famulus_paths(platform=sys.platform, home=home or Path.home()).email_triage_state_root


WATERMARK = default_state_dir() / "last_run"
STATUS_FILE = default_state_dir() / "status.json"


def record_warning(message: str) -> None:
    """Record a non-fatal problem for whoever reads this run's state.

    Not a notification channel. The health check reads the recurring-tasks run
    record (logs/<job>/latest.json) and renders the reason captured from the
    run's own output; nothing reads this file's `message`. What status.json
    does decide is `result`, which the success contract compares against
    `require_inner_status`.

    Merges rather than overwrites: update_watermark records
    `last_finalized_run_id` here and _finalize_run reads it to refuse a
    double advance on replay, so a wholesale write would delete the replay
    guard as a side effect of raising a warning.
    """
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        status = json.loads(STATUS_FILE.read_text())
        if not isinstance(status, dict):
            status = {}
    except (json.JSONDecodeError, OSError):
        status = {}
    status.update(result="warning", message=message)
    STATUS_FILE.write_text(json.dumps(status, indent=2))


class Interface(PythonArgvMachineInterface):
    prog = "get_cutoff.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--days" in argv:
        idx = argv.index("--days")
        n = int(argv[idx + 1])
        cutoff = date.today() - timedelta(days=n + 1)
        print(cutoff.isoformat())
    elif WATERMARK.exists():
        with WATERMARK.open() as f:
            watermark_date = date.fromisoformat(f.read().strip()[:10])
        cutoff = watermark_date - timedelta(days=1)
        print(cutoff.isoformat())
    else:
        cutoff = date.today() - timedelta(days=2)
        msg = "No watermark found — defaulting to 2-day lookback."
        print(f"WARNING: {msg}", file=sys.stderr)
        record_warning(msg)
        print(cutoff.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
