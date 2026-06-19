#!/usr/bin/env python3
import subprocess, tempfile, os
from pathlib import Path

SCRIPTS = Path(__file__).parent

JOBS_YAML = """\
jobs:
  - name: email-triage
    description: "Triage new emails into todo and potential-actions lists"
    command: "/home/moeen/.local/bin/claude -p \\"/email-triage\\""
    schedule: "0 * * * *"
    enabled: true
"""

def run_script(script: str, name: str, jobs_path: str):
    subprocess.run(
        ["python3", str(SCRIPTS / script), name, "--jobs-file", jobs_path, "--no-sync"],
        check=True
    )

def test_disable():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(JOBS_YAML)
        path = f.name
    try:
        run_script("disable-job.py", "email-triage", path)
        content = Path(path).read_text()
        assert "enabled: false" in content, f"Expected 'enabled: false', got:\n{content}"
        print("PASS: disable sets enabled: false")
    finally:
        os.unlink(path)

def test_enable():
    disabled = JOBS_YAML.replace("enabled: true", "enabled: false")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(disabled)
        path = f.name
    try:
        run_script("enable-job.py", "email-triage", path)
        content = Path(path).read_text()
        assert "enabled: true" in content, f"Expected 'enabled: true', got:\n{content}"
        print("PASS: enable sets enabled: true")
    finally:
        os.unlink(path)

def test_unknown_job_errors():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(JOBS_YAML)
        path = f.name
    try:
        r = subprocess.run(
            ["python3", str(SCRIPTS / "enable-job.py"), "no-such-job", "--jobs-file", path, "--no-sync"],
            capture_output=True, text=True
        )
        assert r.returncode != 0
        print("PASS: unknown job exits non-zero")
    finally:
        os.unlink(path)

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

def test_disable_does_not_affect_other_jobs():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(JOBS_TWO)
        path = f.name
    try:
        run_script("disable-job.py", "email-triage", path)
        content = Path(path).read_text()
        lines = content.splitlines()
        triage_idx = next(i for i, l in enumerate(lines) if "email-triage" in l)
        archive_idx = next(i for i, l in enumerate(lines) if "email-archive" in l)
        triage_enabled = next(l for l in lines[triage_idx:archive_idx] if "enabled:" in l)
        archive_enabled = next(l for l in lines[archive_idx:] if "enabled:" in l)
        assert "false" in triage_enabled, f"email-triage should be disabled: {triage_enabled}"
        assert "true" in archive_enabled, f"email-archive should still be enabled: {archive_enabled}"
        print("PASS: disabling one job does not affect other jobs")
    finally:
        os.unlink(path)

def test_prefix_name_no_cross_match():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(JOBS_TWO)
        path = f.name
    try:
        r = subprocess.run(
            ["python3", str(SCRIPTS / "disable-job.py"), "email", "--jobs-file", path, "--no-sync"],
            capture_output=True, text=True
        )
        assert r.returncode != 0, "Prefix name 'email' should not match 'email-triage'"
        print("PASS: prefix name does not cross-match")
    finally:
        os.unlink(path)

if __name__ == "__main__":
    test_disable()
    test_enable()
    test_unknown_job_errors()
    test_disable_does_not_affect_other_jobs()
    test_prefix_name_no_cross_match()
    print("\nAll tests passed.")
