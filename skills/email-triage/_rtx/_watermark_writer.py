#!/usr/bin/env python3
"""Advance the triage watermark — but only if the run wasn't marked as failed.

State lives under the shared Famulus state root (see
officina.common.famulus_paths), not next to this script, so it stays valid
even when the skill's installed tree is read-only. Overridable via
EMAIL_TRIAGE_STATE_DIR for tests/CI.

Safety: if _rtx/_failure_sentinel.py was called earlier in this run, a
status.json with result="error" will be present. In that case this script
refuses to advance the watermark (exit 1) so no emails are silently skipped
on the next run. On success it sets status.json to result="ok" with a
matching message -- and it is the only writer that may set "ok", since that
word is what the scheduler's require_inner_status contract reads.

Optional --run-id (used by _finalize_run.py, not required for standalone
use): when given, status.json is committed FIRST — result, the new
watermark timestamp, AND the run id, all in that one write — and only
afterward is the watermark file itself written. This ordering matters for
crash-safety: if the process dies between the two writes, status.json
already shows this run id as finalized, so a replay with the same run id
short-circuits as a no-op instead of advancing the watermark a second time
to a later timestamp. The only failure mode left is the safe direction —
the watermark file lags one commit behind what status.json claims, which
just makes the next run re-scan a bit more mail (caught by Step 4's dedup)
rather than silently skipping any. A later run with a fresh run id (or no
run id at all) advances normally and self-heals the lag.
"""
import argparse
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

    return resolve_famulus_paths(
        platform=sys.platform, home=home or Path.home(), environ=os.environ
    ).email_triage_state_root


WATERMARK = default_state_dir() / "last_run"
STATUS_FILE = default_state_dir() / "status.json"

if HAS_OFFICINA:
    class Interface(PythonArgvMachineInterface):
        prog = "update_watermark.py"

        def run(self, argv: list[str]) -> int:
            return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance the triage watermark")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional idempotency key (set by _finalize_run.py). A repeat call with "
        "the same run id that already committed is a no-op.",
    )
    args = parser.parse_args(argv)
    run_id = args.run_id

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
        if run_id is not None and status.get("last_finalized_run_id") == run_id:
            print(f"Watermark already advanced for run {run_id!r}; no-op (replay-safe)")
            return 0

    now = datetime.now().astimezone()

    # Preserve existing metrics and other fields, just update result and timestamp.
    # `message` is overwritten, not preserved: whatever is there belongs to an
    # earlier, now-superseded state -- the start-of-run reset, or a warning from
    # load_cutoff. Leaving it made status.json self-contradictory (result "ok"
    # next to "reset at start of new run"), which is misleading in exactly the
    # file someone opens to find out how the last run went.
    status["result"] = "ok"
    status["message"] = "watermark advanced"
    status["watermark_advanced_at"] = now.isoformat()
    if run_id is not None:
        status["last_finalized_run_id"] = run_id

    # Commit status.json (including the run id) BEFORE writing the watermark
    # file itself. If the process dies between these two writes, status.json
    # already records this run id as finalized, so a replay is recognized as
    # a no-op and never re-advances the watermark to a later timestamp — the
    # only possible casualty of a crash here is the watermark file lagging
    # one commit behind, which is self-healing and never causes skipped mail.
    STATUS_FILE.write_text(json.dumps(status, indent=2))
    WATERMARK.write_text(now.isoformat())
    print(f"Watermark updated: {now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
