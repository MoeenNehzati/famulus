#!/usr/bin/env python3
"""
Regenerate the claude-recurring cron block from jobs.yaml.
Usage:
  sync-crontab.py                                    # live crontab
  sync-crontab.py --crontab-file PATH               # file-based (for testing)
  sync-crontab.py --jobs-file PATH                  # override jobs.yaml location
"""
import sys, subprocess
import yaml
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR   = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
LOG_DIR     = SKILL_DIR / "logs"
BEGIN = "# --- claude-recurring BEGIN (managed by recurring-tasks skill — do not edit manually) ---"
END   = "# --- claude-recurring END ---"

def load_jobs(jobs_path: Path) -> list:
    with open(jobs_path) as f:
        return (yaml.safe_load(f) or {}).get("jobs", [])

def cron_line(job: dict) -> str:
    log = LOG_DIR / job["name"] / "run.log"
    return f"{job['schedule']} {job['command']} >> {log} 2>&1"

def generate_block(jobs: list) -> list[str]:
    enabled = [j for j in jobs if str(j.get("enabled", "false")).lower() == "true"]
    return [BEGIN] + [cron_line(j) for j in enabled] + [END]

def splice(lines: list[str], block: list[str]) -> list[str]:
    try:
        i = next(n for n, l in enumerate(lines) if l.rstrip() == BEGIN)
        j = next(n for n, l in enumerate(lines) if l.rstrip() == END)
        if i >= j:
            raise ValueError(
                f"Crontab sentinel order is corrupted: END (line {j+1}) appears before or at BEGIN (line {i+1}). "
                "Edit your crontab manually to fix the sentinel order."
            )
        return lines[:i] + block + lines[j + 1:]
    except StopIteration:
        sep = [""] if lines and lines[-1].strip() else []
        return lines + sep + block

def main():
    p = ArgumentParser()
    p.add_argument("--crontab-file", default=None)
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    args = p.parse_args()

    if args.crontab_file:
        raw = Path(args.crontab_file).read_text()
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        raw = r.stdout if r.returncode == 0 else ""

    lines = raw.splitlines()
    jobs  = load_jobs(Path(args.jobs_file))
    block = generate_block(jobs)
    new   = splice(lines, block)
    out   = "\n".join(new) + "\n"

    if args.crontab_file:
        Path(args.crontab_file).write_text(out)
    else:
        subprocess.run(["crontab", "-"], input=out, text=True, check=True)
        print("Crontab updated.")

if __name__ == "__main__":
    main()
