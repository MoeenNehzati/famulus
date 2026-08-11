#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

from .. import _job_control as job_control

SCRIPTS = Path(__file__).parent.parent
MANAGE_JOB = SCRIPTS / "_job_control.py"
REPO_SRC = Path(__file__).resolve().parents[4] / "src"

JOBS_YAML = """\
jobs:
  - name: email-triage
    description: "Triage new emails into todo and potential-actions lists"
    command: "invoke-skill email-triage"
    schedule: "0 * * * *"
    enabled: true
"""

def run_script(command: str, name: str, jobs_path: str) -> int:
    """Call the package runtime directly for parser/dispatch assertions."""
    return job_control.main(
        [command, name, "--jobs-file", jobs_path, "--no-sync"]
    )


def run_script_smoke(command: str, name: str, jobs_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MANAGE_JOB), command, name, "--jobs-file", jobs_path, "--no-sync"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_SRC)},
    )

def test_disable(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(JOBS_YAML)
    run_script("disable", "email-triage", str(path))
    content = path.read_text()
    assert "enabled: false" in content, f"Expected 'enabled: false', got:\n{content}"
    print("PASS: disable sets enabled: false")


def test_disable_executable_smoke(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(JOBS_YAML)
    result = run_script_smoke("disable", "email-triage", str(path))
    assert result.returncode == 0, result.stderr
    assert "enabled: false" in path.read_text()

def test_enable(tmp_path):
    disabled = JOBS_YAML.replace("enabled: true", "enabled: false")
    path = tmp_path / "jobs.yaml"
    path.write_text(disabled)
    run_script("enable", "email-triage", str(path))
    content = path.read_text()
    assert "enabled: true" in content, f"Expected 'enabled: true', got:\n{content}"
    print("PASS: enable sets enabled: true")

def test_unknown_job_errors(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(JOBS_YAML)
    returncode = run_script("enable", "no-such-job", str(path))
    assert returncode != 0
    print("PASS: unknown job exits non-zero")

JOBS_TWO = """\
jobs:
  - name: email-triage
    description: "Email triage"
    command: "/usr/bin/echo email"
    schedule: "0 * * * *"
    enabled: true
  - name: email-archive
    description: "Email archive"
    command: "/usr/bin/echo archive"
    schedule: "0 2 * * *"
    enabled: true
"""

def test_disable_does_not_affect_other_jobs(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(JOBS_TWO)
    run_script("disable", "email-triage", str(path))
    content = path.read_text()
    lines = content.splitlines()
    triage_idx = next(i for i, l in enumerate(lines) if "email-triage" in l)
    archive_idx = next(i for i, l in enumerate(lines) if "email-archive" in l)
    triage_enabled = next(l for l in lines[triage_idx:archive_idx] if "enabled:" in l)
    archive_enabled = next(l for l in lines[archive_idx:] if "enabled:" in l)
    assert "false" in triage_enabled, f"email-triage should be disabled: {triage_enabled}"
    assert "true" in archive_enabled, f"email-archive should still be enabled: {archive_enabled}"
    print("PASS: disabling one job does not affect other jobs")

def test_prefix_name_no_cross_match(tmp_path):
    path = tmp_path / "jobs.yaml"
    path.write_text(JOBS_TWO)
    returncode = run_script("disable", "email", str(path))
    assert returncode != 0, "Prefix name 'email' should not match 'email-triage'"
    print("PASS: prefix name does not cross-match")

if __name__ == "__main__":
    test_disable()
    test_enable()
    test_unknown_job_errors()
    test_disable_does_not_affect_other_jobs()
    test_prefix_name_no_cross_match()
    print("\nAll tests passed.")
