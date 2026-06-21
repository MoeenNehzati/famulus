#!/usr/bin/env python3
"""
Usage: enable-job.py <name> [--jobs-file PATH] [--no-sync]
Sets enabled: true for the named job in jobs.yaml, then syncs systemd units.
"""
import subprocess, sys
from pathlib import Path
from argparse import ArgumentParser
sys.path.insert(0, str(Path(__file__).parent))
from _job_utils import set_enabled

SKILL_DIR    = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
SYNC         = Path(__file__).parent / "sync-units.py"

def main():
    p = ArgumentParser()
    p.add_argument("name")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    p.add_argument("--no-sync", action="store_true")
    args = p.parse_args()

    set_enabled(Path(args.jobs_file), args.name, "true")
    print(f"Enabled '{args.name}'.")

    if not args.no_sync:
        subprocess.run(["python3", str(SYNC), "--jobs-file", args.jobs_file], check=True)

if __name__ == "__main__":
    main()
