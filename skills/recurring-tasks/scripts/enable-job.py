#!/usr/bin/env python3
"""
Usage: enable-job.py <name> [--jobs-file PATH] [--no-sync]
Sets enabled: true for the named job in jobs.yaml, then syncs crontab.
"""
import sys, re, subprocess
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR   = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
SYNC        = Path(__file__).parent / "sync-crontab.py"

def set_enabled(jobs_path: Path, name: str, value: str):
    text = jobs_path.read_text()
    # Find the job block for `name` and replace its `enabled:` line
    # Pattern: after `- name: <name>` (possibly with quotes), before the next `- name:`
    pattern = rf'(- name: ["\']?{re.escape(name)}["\']?.*?^\s+enabled:)\s+\S+'
    replacement = rf'\1 {value}'
    new, count = re.subn(pattern, replacement, text, flags=re.MULTILINE | re.DOTALL)
    if count == 0:
        print(f"Error: job '{name}' not found in {jobs_path}", file=sys.stderr)
        sys.exit(1)
    jobs_path.write_text(new)

def main():
    p = ArgumentParser()
    p.add_argument("name")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    p.add_argument("--no-sync", action="store_true")
    args = p.parse_args()

    set_enabled(Path(args.jobs_file), args.name, "true")
    print(f"Enabled '{args.name}'.")

    if not args.no_sync:
        subprocess.run(["python3", str(SYNC)], check=True)

if __name__ == "__main__":
    main()
