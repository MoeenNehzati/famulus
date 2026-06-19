#!/usr/bin/env python3
"""
Usage: test-job.py <job-name>

Schedules the named job to run 1 minute from now via a temporary cron block,
waits ~90 seconds, checks logs and cron system log, removes the temp block,
and reports pass/fail.
"""
import sys, subprocess, time, yaml
from datetime import datetime, timedelta
from pathlib import Path
from argparse import ArgumentParser

SKILL_DIR  = Path(__file__).parent.parent
DEFAULT_JOBS = SKILL_DIR / "jobs.yaml"
LOG_DIR    = SKILL_DIR / "logs"
TEST_BEGIN = "# --- claude-recurring TEST BEGIN (temporary) ---"
TEST_END   = "# --- claude-recurring TEST END ---"

# ── helpers exposed for testing ──────────────────────────────────────────────

def inject_test_block(crontab: str, time_spec: str, command: str, log: str) -> str:
    """Insert a TEST block at the end of crontab text. Replaces any existing TEST block."""
    lines = remove_test_block(crontab).rstrip("\n").splitlines()
    block = [TEST_BEGIN, f"{time_spec} * * * {command} >> {log} 2>&1", TEST_END]
    return "\n".join(lines + [""] + block) + "\n"

def remove_test_block(crontab: str) -> str:
    """Remove TEST block from crontab text, returning the rest unchanged."""
    lines = crontab.splitlines(keepends=True)
    try:
        i = next(n for n, l in enumerate(lines) if l.rstrip() == TEST_BEGIN)
        j = next(n for n, l in enumerate(lines) if l.rstrip() == TEST_END)
        return "".join(lines[:i] + lines[j + 1:])
    except StopIteration:
        return crontab

# ── live crontab helpers ──────────────────────────────────────────────────────

def read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""

def write_crontab(text: str):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)

def load_job(name: str, jobs_path: Path) -> dict:
    jobs = yaml.safe_load(jobs_path.read_text()).get("jobs", [])
    matches = [j for j in jobs if j["name"] == name]
    if not matches:
        print(f"Error: job '{name}' not found in {jobs_path}", file=sys.stderr)
        sys.exit(1)
    return matches[0]

def check_syslog(fire_at: datetime, command: str) -> str:
    """Return cron syslog lines that fired around the test time."""
    since_str = (fire_at - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    until_str = (fire_at + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    r = subprocess.run(
        ["journalctl", "-u", "cron", "--since", since_str, "--until", until_str, "--no-pager"],
        capture_output=True, text=True
    )
    if r.returncode == 0 and r.stdout:
        return r.stdout
    # Fallback: grep syslog for lines around the test time
    r2 = subprocess.run(["grep", "-a", "CRON", "/var/log/syslog"],
                        capture_output=True, text=True)
    # Filter to lines near fire_at (match HH:MM from fire_at)
    fire_minute = fire_at.strftime("%H:%M")
    matching = [l for l in r2.stdout.splitlines()
                if fire_minute in l and ("CMD" in l or command.split()[-1] in l)]
    return "\n".join(matching)

def main():
    p = ArgumentParser()
    p.add_argument("name")
    p.add_argument("--jobs-file", default=str(DEFAULT_JOBS))
    args = p.parse_args()

    job       = load_job(args.name, Path(args.jobs_file))
    log_path  = LOG_DIR / args.name / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    now       = datetime.now()
    fire_at   = now + timedelta(minutes=1)
    time_spec = f"{fire_at.minute} {fire_at.hour}"

    original = read_crontab()  # capture before injection for C2 fallback
    print(f"Scheduling test run at {fire_at.strftime('%H:%M')} …")
    injected = inject_test_block(original, time_spec, job["command"], str(log_path))
    write_crontab(injected)

    print("Waiting 90 seconds …")
    log_lines_after = []
    syslog = ""
    try:
        time.sleep(90)

        log_content = log_path.read_text() if log_path.exists() else ""
        log_lines_after = [l for l in log_content.splitlines() if l]
        syslog = check_syslog(fire_at, job["command"])

    finally:
        current2 = read_crontab()
        write_crontab(remove_test_block(current2 if current2 else original))
        print("Test block removed.")

    # Report
    print("\n── Run log (last 20 lines) ──")
    print("\n".join(log_lines_after[-20:]) or "(empty)")
    print("\n── Cron system log (test window) ──")
    print(syslog or "(nothing found)")

    launched   = job["command"].split()[-1] in syslog or ("CMD" in syslog and bool(syslog.strip()))
    has_output = bool(log_lines_after)

    if launched and has_output:
        print("\n✓ PASS — cron launched the job and it produced output.")
    elif not launched:
        print("\n✗ FAIL — no evidence cron launched the job (check PATH, command, syslog).")
    else:
        print("\n✗ FAIL — cron launched the job but it produced no log output.")

if __name__ == "__main__":
    main()
