#!/usr/bin/env python3
import subprocess, tempfile, os
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = Path(__file__).parent / "sync-crontab.py"

def run(jobs_yaml: str, crontab: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as jf:
        jf.write(jobs_yaml)
        jobs_path = jf.name
    with tempfile.NamedTemporaryFile("w", suffix=".crontab", delete=False) as cf:
        cf.write(crontab)
        cron_path = cf.name
    try:
        subprocess.run(
            ["python3", str(SCRIPT), "--crontab-file", cron_path, "--jobs-file", jobs_path],
            check=True
        )
        return Path(cron_path).read_text()
    finally:
        os.unlink(jobs_path)
        os.unlink(cron_path)

JOBS_ONE_ENABLED = """\
jobs:
  - name: test-job
    description: "Test job"
    command: "/usr/bin/echo hello"
    schedule: "0 * * * *"
    enabled: true
"""

JOBS_ONE_DISABLED = """\
jobs:
  - name: test-job
    description: "Test job"
    command: "/usr/bin/echo hello"
    schedule: "0 * * * *"
    enabled: false
"""

BEGIN = "# --- claude-recurring BEGIN (managed by recurring-tasks skill — do not edit manually) ---"
END   = "# --- claude-recurring END ---"

def test_appends_block_to_empty_crontab():
    result = run(JOBS_ONE_ENABLED, "")
    assert BEGIN in result
    assert END in result
    assert "echo hello" in result
    print("PASS: appends block to empty crontab")

def test_preserves_content_before_and_after():
    existing = "MAILTO=\"\"\n# existing job\n15 * * * * /some/script.sh\n"
    result = run(JOBS_ONE_ENABLED, existing)
    assert "MAILTO" in result
    assert "/some/script.sh" in result
    assert BEGIN in result
    print("PASS: preserves surrounding crontab content")

def test_replaces_existing_block():
    old_block = f"MAILTO=\"\"\n{BEGIN}\nOLD ENTRY\n{END}\n# after\n"
    result = run(JOBS_ONE_ENABLED, old_block)
    assert "OLD ENTRY" not in result
    assert "echo hello" in result
    assert result.count(BEGIN) == 1
    print("PASS: replaces existing block")

def test_disabled_job_omitted():
    result = run(JOBS_ONE_DISABLED, "")
    assert BEGIN in result
    assert "echo hello" not in result
    print("PASS: disabled job omitted from block")

def test_idempotent():
    result1 = run(JOBS_ONE_ENABLED, "")
    result2 = run(JOBS_ONE_ENABLED, result1)
    assert result1.strip() == result2.strip()
    print("PASS: idempotent")

if __name__ == "__main__":
    test_appends_block_to_empty_crontab()
    test_preserves_content_before_and_after()
    test_replaces_existing_block()
    test_disabled_job_omitted()
    test_idempotent()
    print("\nAll tests passed.")
