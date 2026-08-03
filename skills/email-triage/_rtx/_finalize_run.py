#!/usr/bin/env python3
"""Single, ordered, idempotent finalization step for a triage run.

Wraps the two existing finalization primitives — _write_metrics.py and
_watermark_writer.py — behind one CLI call, so a caller cannot invoke them
out of order (accidentally advancing the watermark before metrics are
recorded) and so replaying finalization for the same run (e.g. a retried
tool call after an ambiguous error) cannot double-apply metrics or advance
the watermark twice.

_write_metrics.py and _watermark_writer.py remain independently invocable
CLI scripts with their existing argparse interfaces completely unchanged,
for any caller (manual recovery, older instructions) that does not opt into
this finalization step.

Usage:
  _finalize_run.py --run-id ID --total-scanned N --added-todo N \
      --added-triage N --skipped N [--deduped N] [--accounts a,b]

Ordering guarantee: this script calls write_metrics.main() and then, only
if that call succeeds, watermark_writer.main() — in-process, inside a
single Python function, so no external caller can reorder or interleave
the two steps through this interface.

Idempotency / replay-safety guarantee: a --run-id is required and is
forwarded to watermark_writer.main() as --run-id. watermark_writer commits
status.json — result, watermark timestamp, AND the run id — in one write,
BEFORE it writes the watermark file itself (see _watermark_writer.py for
why that order matters). That means the run id is durably recorded in the
SAME write that records the intended new watermark, with no separate
"finalize commits the run id afterward" step here that a crash could land
between: by the time watermark_writer.main() returns success, replay-safety
for this run id is already persisted. A repeat call with the SAME --run-id
short-circuits here (before even touching write_metrics) as soon as
status.json shows it already finalized. A call with a DIFFERENT --run-id is
a new run and proceeds normally. A rejected or failed call (e.g. the
watermark step refuses because an earlier failure is still latched) does
not consume its run-id, so the same run-id can safely be retried once the
underlying problem is fixed.

There is no existing "run id" concept anywhere else in email-triage state
(no run_id field, no per-run identifier of any kind) — the caller
(the triage instructions, followed by an LLM) mints one value once per
triage run and reuses it for every finalize call attempted within that run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from officina.runtime.python_machine_interface import PythonArgvMachineInterface

    HAS_OFFICINA = True
except ImportError:
    HAS_OFFICINA = False

from . import _write_metrics as write_metrics
from . import _watermark_writer as watermark_writer

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


STATUS_FILE = default_state_dir() / "status.json"


if HAS_OFFICINA:

    class Interface(PythonArgvMachineInterface):
        prog = "finalize_run.py"

        def run(self, argv: list[str]) -> int:
            return main(argv)


def _read_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ordered, idempotent metrics-write + watermark-advance for one triage run"
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Caller-chosen unique id for this triage run; replaying the same id is a safe no-op",
    )
    parser.add_argument("--total-scanned", type=int, required=True, help="Total emails scanned")
    parser.add_argument("--added-todo", type=int, required=True, help="Items added to todo")
    parser.add_argument("--added-triage", type=int, required=True, help="Items added to triage")
    parser.add_argument("--skipped", type=int, required=True, help="Emails skipped")
    parser.add_argument("--deduped", type=int, default=0, help="Deduped items (already exist)")
    parser.add_argument("--accounts", type=str, default="", help="Comma-separated list of accounts triaged")

    args = parser.parse_args(argv)

    if not args.run_id.strip():
        print("error: --run-id must not be empty", file=sys.stderr)
        return 1

    status = _read_status()
    if status.get("last_finalized_run_id") == args.run_id:
        print(f"finalize: run {args.run_id!r} already finalized; no-op (replay-safe)")
        return 0

    metrics_argv = [
        "--total-scanned", str(args.total_scanned),
        "--added-todo", str(args.added_todo),
        "--added-triage", str(args.added_triage),
        "--skipped", str(args.skipped),
        "--deduped", str(args.deduped),
        "--accounts", args.accounts,
    ]
    metrics_rc = write_metrics.main(metrics_argv)
    if metrics_rc != 0:
        print("finalize: metrics write failed; watermark NOT advanced", file=sys.stderr)
        return metrics_rc

    # watermark_writer commits status.json (result + watermark timestamp +
    # this run id) in one write before it writes the watermark file itself,
    # so there is no further bookkeeping to do here after it returns.
    watermark_rc = watermark_writer.main(["--run-id", args.run_id])
    if watermark_rc != 0:
        print(
            f"finalize: watermark advance refused (run {args.run_id!r} not consumed; "
            "safe to retry once resolved)",
            file=sys.stderr,
        )
        return watermark_rc

    print(f"finalize: run {args.run_id!r} committed (metrics written, watermark advanced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
