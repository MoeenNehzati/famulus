"""Exercise the entrypoints the way the scheduler and cron actually invoke them.

Every recurring-tasks outage of Aug 5-11 2026 was in a path only production
took. The suite exercised a different mode of the same code: `_run_record.py`
called `sys.platform` without importing `sys` on a branch reachable only when
a scheduled email-triage run resolved its state directory, and the health check
re-derived its expectations from an ambient `PATH` no scheduled run has.

Both were verified by hand during design -- run it under `env -i`, watch it
work -- and neither verification became a test, which is why the same class
recurred for five days. These tests are those verifications.

The shape that matters: spawn the real entrypoint as a subprocess, under an
environment resembling the scheduler's (no inherited `PYTHONPATH`, no
`XDG_RUNTIME_DIR`, no `DBUS_SESSION_BUS_ADDRESS`, a minimal `PATH`, and `/` as
the working directory), and assert on what it produced. Anything that imports
the module in-process instead is testing a mode production never takes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_RTX_DIR = Path(__file__).resolve().parents[1]
_JOB_EXECUTOR = _RTX_DIR / "_job_executor.py"
_HEALTHCHECK_PROBE = _RTX_DIR / "_healthcheck_probe.py"

# The scheduler's environment, minus everything a developer shell adds. HOME is
# kept because resolving a state directory legitimately needs it.
_SCHEDULER_ENV = {
    "HOME": os.environ.get("HOME", "/root"),
    "PATH": "/usr/bin:/bin",
    # Spawning the real entrypoints would otherwise leave __pycache__ trees in
    # the skill directory, written by whichever interpreter ran. Tests should
    # not deposit artifacts in the tree they are testing.
    "PYTHONDONTWRITEBYTECODE": "1",
}


def _installed_resolver() -> Path | None:
    """Return the installed managed-runtime resolver, or None when absent."""
    home = os.environ.get("HOME")
    if not home:
        return None
    resolver = (
        Path(home)
        / ".local/share/famulus/runtime/bootstrap/resolvers/v1/launch.py"
    )
    return resolver if resolver.is_file() else None


def _write_jobs_file(tmp_path: Path) -> Path:
    """Write a jobs file whose one job is a harmless command."""
    jobs_file = tmp_path / "jobs.yaml"
    harmless_command = f'"{sys.executable}" -c "print(\'hello\')"'
    jobs_file.write_text(
        yaml.safe_dump(
            {
                "jobs": [
                    {
                        "name": "email-triage",
                        "description": "harmless stand-in for the real triage job",
                        "command": harmless_command,
                        "schedule": "0 * * * *",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return jobs_file


def _assert_record_written(log_dir: Path, result: subprocess.CompletedProcess):
    """Assert the run produced a successful outcome record."""
    assert result.returncode == 0, (
        f"executor exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    record_path = log_dir / "email-triage" / "latest.json"
    assert record_path.is_file(), (
        "the executor completed without writing an outcome record; "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["success"] is True, record
    assert record["job_name"] == "email-triage"
    assert record["finished_at"], record


def test_executor_writes_a_record_under_a_scheduler_environment(tmp_path):
    """The executor completes and records its outcome with a bare interpreter.

    This is the regression test for the `sys` NameError: the executor ran the
    job, then died before `write_run_record`, leaving a stale in-flight marker
    and no record at all. Asserting the record exists -- rather than that the
    process exited 0 -- is what catches that, since the crash happened after
    the work was done.
    """
    jobs_file = _write_jobs_file(tmp_path)
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            str(_JOB_EXECUTOR),
            "--jobs-file",
            str(jobs_file),
            "--job",
            "email-triage",
            "--log-dir",
            str(log_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_SCHEDULER_ENV,
        cwd="/",
    )

    _assert_record_written(log_dir, result)


# famulus-skip: category=capability-unavailable; reason=the managed runtime resolver is installed per host and is absent in generic CI; alternate=the bare-interpreter executor test covers the same import and record-writing contract without it
@pytest.mark.skipif(
    _installed_resolver() is None,
    reason="managed runtime resolver is not installed on this host",
)
def test_executor_writes_a_record_through_the_installed_resolver(tmp_path):
    """The executor works through the exact command the scheduler runs.

    The generated units invoke `<resolver> <_job_executor.py> --jobs-file ...`,
    so this is the production command with a throwaway jobs file. It skips
    where the runtime is not installed rather than asserting a weaker thing.
    """
    resolver = _installed_resolver()
    assert resolver is not None
    jobs_file = _write_jobs_file(tmp_path)
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            str(resolver),
            str(_JOB_EXECUTOR),
            "--jobs-file",
            str(jobs_file),
            "--job",
            "email-triage",
            "--log-dir",
            str(log_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_SCHEDULER_ENV,
        cwd="/",
    )

    _assert_record_written(log_dir, result)


# famulus-skip: category=capability-unavailable; reason=the managed runtime resolver is installed per host and is absent in generic CI; alternate=the bare-interpreter executor test covers the same import and record-writing contract without it
@pytest.mark.skipif(
    _installed_resolver() is None,
    reason="managed runtime resolver is not installed on this host",
)
def test_healthcheck_probe_starts_under_a_cron_environment():
    """The probe imports and runs its checks when cron invokes it.

    Invoked through the resolver because that is precisely what the installed
    cron line runs: `<resolver> <_healthcheck_probe.py>`. A bare interpreter is
    not a production mode for this file -- the resolver's interpreter is what
    supplies officina -- so testing one would assert a fiction.

    Deliberately does not assert the exit code: the probe reports on this
    host's real jobs, so a healthy or unhealthy system are both correct
    outcomes and asserting either would make the test depend on live state.
    What must hold is that it got far enough to report at all -- an import-time
    fault (the unguarded `sys.path` insert that made this module unreachable
    through the gateway) or a NameError would prevent that.

    `RECURRING_TASKS_HEALTHCHECK_CRON` is set for the same reason cron sets it:
    it suppresses the durable log write, so the test does not append to the
    real health-check log.
    """
    resolver = _installed_resolver()
    assert resolver is not None
    env = dict(_SCHEDULER_ENV)
    env["RECURRING_TASKS_HEALTHCHECK_CRON"] = "1"

    result = subprocess.run(
        [str(resolver), str(_HEALTHCHECK_PROBE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd="/",
    )

    assert "Traceback" not in result.stderr, result.stderr
    assert "ImportError" not in result.stderr, result.stderr
    assert "healthcheck start" in result.stdout, (
        f"probe produced no report\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
