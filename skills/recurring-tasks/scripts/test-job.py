#!/usr/bin/env python3
"""
Usage: test-job.py <job-name>

Starts the named job immediately via 'systemctl --user start --wait',
checks log output and journal, and reports pass/fail.
"""
import sys, subprocess, time, yaml
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR    = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
LOG_DIR      = SKILL_DIR / "logs"
PREFIX       = "claude-"
TIMEOUT_SEC  = 360


def load_job(name: str, jobs_path: Path) -> dict:
    jobs = yaml.safe_load(jobs_path.read_text()).get("jobs", [])
    matches = [j for j in jobs if j["name"] == name]
    if not matches:
        print(f"Error: job '{name}' not found in {jobs_path}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def main() -> None:
    p = ArgumentParser()
    p.add_argument("name")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    args = p.parse_args()

    load_job(args.name, Path(args.jobs_file))  # validates job exists in yaml
    log_path = LOG_DIR / args.name / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    service = f"{PREFIX}{args.name}.service"

    r = subprocess.run(
        ["systemctl", "--user", "cat", service],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(
            f"Error: service unit '{service}' not found. Run sync-units.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    log_size_before = log_path.stat().st_size if log_path.exists() else 0
    print(f"Starting '{service}' (timeout {TIMEOUT_SEC}s) …")

    start = time.time()
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "--wait", service],
            timeout=TIMEOUT_SEC,
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - start
        print(f"Service exited after {elapsed:.1f}s (exit code {result.returncode})")
    except subprocess.TimeoutExpired:
        print(f"\n✗ FAIL — service did not complete within {TIMEOUT_SEC}s.")
        subprocess.run(["systemctl", "--user", "stop", service], capture_output=True)
        sys.exit(1)

    log_size_after = log_path.stat().st_size if log_path.exists() else 0
    new_output = log_size_after > log_size_before

    journal = subprocess.run(
        ["journalctl", "--user", "-u", service, "--since", "-10min", "--no-pager"],
        capture_output=True, text=True,
    )

    print("\n── Run log (last 20 lines) ──")
    if log_path.exists():
        lines = log_path.read_text().splitlines()
        print("\n".join(lines[-20:]) or "(empty)")
    else:
        print("(no log file)")

    print("\n── Journal (last 10 min) ──")
    print(journal.stdout or "(nothing found)")

    if result.returncode == 0 and new_output:
        print("\n✓ PASS — service succeeded and produced log output.")
    elif result.returncode != 0:
        print(f"\n✗ FAIL — service exited with code {result.returncode}.")
        sys.exit(1)
    else:
        print("\n✗ FAIL — service succeeded but produced no log output.")
        sys.exit(1)


if __name__ == "__main__":
    main()
