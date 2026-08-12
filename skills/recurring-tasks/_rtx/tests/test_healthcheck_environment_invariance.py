#!/usr/bin/env python3
"""Health-check verdicts must not depend on the caller's environment.

The rest of the health-check suite drives the probe through ``mock.Mock()``
backends, which stubs out precisely the boundary where the checker touches
its environment. That leaves the suite green while the scheduled (cron)
invocation fails 100% of the time, because cron supplies neither
``XDG_RUNTIME_DIR`` nor ``~/.local/bin`` on ``PATH``.

These tests pin the property the mocks cannot see: the same host state must
produce the same verdict regardless of who invokes the checker. They assert
*invariance*, not success -- if the jobs are genuinely broken, both
environments must agree that they are broken.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

SKILL_DIR = Path(__file__).parent.parent
REPO_SRC = SKILL_DIR.parents[2] / "src"
RTX_DIR = SKILL_DIR
PROBE = SKILL_DIR / "_healthcheck_probe.py"

sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RTX_DIR))

if __package__ and __package__.count(".") >= 1:
    from .._schedule_backend import ScheduleContext, ScheduleJob
    from .._schedule_backend import _linux_backend as lb
    from .._schedule_backend._linux_registration_check import check_job_configuration
else:
    from _schedule_backend import ScheduleContext, ScheduleJob  # noqa: E402
    from _schedule_backend import _linux_backend as lb  # noqa: E402
    from _schedule_backend._linux_registration_check import (  # noqa: E402
        check_job_configuration,
    )

# Reach through the module object rather than re-importing names: the two
# import shims above bind different module objects, so patching one by dotted
# string would silently miss the other.
PREFIX = lb.PREFIX
LinuxScheduleBackend = lb.LinuxScheduleBackend
_session_env = lb._session_env
cron_to_systemd_calendar = lb.cron_to_systemd_calendar
service_content = lb.service_content
timer_content = lb.timer_content


JOB = {
    "name": "demo",
    "description": "Demo job",
    "command": "invoke-skill demo",
    "schedule": "0 3 * * *",
    "enabled": True,
}


def _launcher_on_path(tmp_path: Path) -> Path:
    """Create a discoverable ``invoke-skill`` and return its directory."""
    bin_dir = tmp_path / "rich-bin"
    bin_dir.mkdir()
    launcher = bin_dir / "invoke-skill"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    return bin_dir


def _empty_dir(tmp_path: Path) -> Path:
    """Return a directory guaranteed not to contain ``invoke-skill``."""
    bare = tmp_path / "poor-bin"
    bare.mkdir()
    return bare


def _context(tmp_path: Path, unit_dir: Path) -> ScheduleContext:
    return ScheduleContext(
        skill_dir=tmp_path / "skill",
        jobs_file=tmp_path / "skill" / "jobs.yaml",
        log_dir=tmp_path / "logs",
        unit_dir=unit_dir,
        runtime_resolver=tmp_path / "runtime" / "launch.py",
    )


def test_session_env_derives_bus_when_cron_supplies_none(monkeypatch):
    """cron gets no pam_systemd, so the session variables must be derived."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    env = _session_env()
    uid = getattr(os, "getuid", lambda: 0)()

    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"
    # The caller's own environment must not be touched.
    assert "XDG_RUNTIME_DIR" not in os.environ


def test_session_env_ignores_an_unusable_inherited_runtime_dir(monkeypatch, tmp_path):
    """A stale or foreign XDG_RUNTIME_DIR must not beat the derivable one.

    su/sudo -u and leaked container values point at a bus this user cannot
    reach; honouring them produces worse diagnostics than deriving.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    uid = getattr(os, "getuid", lambda: 0)()
    assert _session_env()["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"


def test_session_env_keeps_a_usable_inherited_runtime_dir(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    env = _session_env()
    assert env["XDG_RUNTIME_DIR"] == str(runtime)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime}/bus"


# famulus-skip: category=platform-contract; reason=systemd unit PATH rendering is a POSIX contract; alternate=Windows scheduler path tests cover native command resolution
@pytest.mark.skipif(os.name == "nt", reason="systemd PATH contract")
def test_job_search_dirs_match_the_units_own_path():
    """The checker's search path and the unit's PATH= must not drift apart.

    They are rendered from one ordered list for exactly this reason; this test
    fails if a future edit adds an entry to one and not the other.
    """
    resolver = Path("/opt/famulus/runtime/bootstrap/resolvers/v1/launch.py")
    service = service_content(
        "demo", "Demo", Path("/x/jobs.yaml"), Path("/x/_job_executor.py"), resolver
    )
    path_line = next(
        line for line in service.splitlines() if line.startswith('Environment="PATH=')
    )
    unit_dirs = path_line.split("PATH=", 1)[1].rstrip('"').split(":")

    home = Path.home().as_posix()
    expanded = [entry.replace("%h", home) for entry in unit_dirs]

    with mock.patch.object(lb, "_default_runtime_resolver", return_value=resolver):
        probe_dirs = [str(part) for part in LinuxScheduleBackend().job_search_dirs()]

    assert probe_dirs == expanded, (
        "the health check searches different directories than the unit's PATH"
    )


def test_rendered_unit_does_not_depend_on_caller_path(tmp_path, monkeypatch):
    """The expected unit text is a fact about the install, not about PATH.

    ``_launcher_bin_dir()`` resolves ``invoke-skill`` through ``shutil.which``
    at render time, so a caller whose PATH lacks the launcher renders a
    different ``PATH=`` line than one whose PATH has it.
    """
    rich_bin = _launcher_on_path(tmp_path)
    poor_bin = _empty_dir(tmp_path)
    args = (
        JOB["name"],
        JOB["description"],
        tmp_path / "jobs.yaml",
        tmp_path / "_job_executor.py",
        tmp_path / "launch.py",
    )

    monkeypatch.setenv("PATH", str(rich_bin))
    rich_render = service_content(*args)

    monkeypatch.setenv("PATH", str(poor_bin))
    poor_render = service_content(*args)

    assert poor_render == rich_render, (
        "service unit rendering changed with the caller's PATH; the expected "
        "unit text must describe what was installed, not what the current "
        "process can find on PATH"
    )


def test_registration_check_agrees_across_environments(tmp_path, monkeypatch):
    """Units installed by a rich-PATH sync must not read as stale under cron.

    This is the production failure in miniature: the units on disk are
    correct, but the checker re-renders its expectation from the ambient
    PATH and byte-compares, so a cron-invoked check reports drift that does
    not exist.
    """
    rich_bin = _launcher_on_path(tmp_path)
    poor_bin = _empty_dir(tmp_path)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    context = _context(tmp_path, unit_dir)
    job = ScheduleJob.from_mapping(JOB)

    # Install the units exactly as a rich-PATH sync would write them.
    monkeypatch.setenv("PATH", str(rich_bin))
    service_name = f"{PREFIX}{job.name}.service"
    (unit_dir / service_name).write_text(
        service_content(
            job.name,
            job.description,
            context.jobs_file,
            context.skill_dir / "_job_executor.py",
            context.runtime_resolver,
        ),
        encoding="utf-8",
    )
    (unit_dir / f"{PREFIX}{job.name}.timer").write_text(
        timer_content(
            job.description,
            cron_to_systemd_calendar(job.schedule),
            service_name,
        ),
        encoding="utf-8",
    )

    installed_verdict = check_job_configuration(
        backend_name="linux-systemd", job=job, context=context
    )
    assert installed_verdict is None, (
        f"freshly installed units already read as drifted: {installed_verdict}"
    )

    # Same units, same host state, cron's PATH.
    monkeypatch.setenv("PATH", str(poor_bin))
    cron_verdict = check_job_configuration(
        backend_name="linux-systemd", job=job, context=context
    )

    assert cron_verdict == installed_verdict, (
        f"registration check disagrees with itself across environments: "
        f"rich PATH said {installed_verdict!r}, cron PATH said {cron_verdict!r}"
    )


# ── live end-to-end invariance ────────────────────────────────────────────────
# Needs this host's real units and systemd user manager, so it is opt-in via
# the same switch the scheduler smoke tests use.

# famulus-skip: category=live-smoke-opt-in; reason=this case runs the real probe against the host's installed units and systemd user manager; alternate=the hermetic invariance tests above cover the same property without host state
live = pytest.mark.skipif(
    os.environ.get("FAMULUS_RUN_SCHEDULER_SMOKE") != "1"
    or platform.system() != "Linux",
    reason="live health-check invariance requires FAMULUS_RUN_SCHEDULER_SMOKE=1 on Linux",
)


def _run_probe(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        env=env,
    )


def _verdict_lines(stdout: str) -> list[str]:
    return [
        line.strip()
        for line in stdout.splitlines()
        if line.strip().startswith(("OK:", "FAIL:", "WARN:"))
    ]


@live
def test_live_probe_verdict_is_invariant_to_invocation_environment():
    """The real probe must reach the same verdict from a login shell and from cron."""
    home = os.path.expanduser("~")
    base = {
        "HOME": home,
        "USER": os.environ.get("USER", ""),
        "LOGNAME": os.environ.get("LOGNAME", ""),
        "SHELL": "/bin/sh",
        # Suppress the probe's only file write so the test cannot append to
        # the real health-check report.
        "RECURRING_TASKS_HEALTHCHECK_CRON": "1",
    }
    rich_env = {
        **base,
        "PATH": os.environ.get("PATH", ""),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
    }
    if "DBUS_SESSION_BUS_ADDRESS" in os.environ:
        rich_env["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]

    # Exactly what cron provides: the crontab's own PATH, no session vars.
    cron_env = {**base, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    rich = _run_probe(rich_env)
    cron = _run_probe(cron_env)

    assert _verdict_lines(cron.stdout) == _verdict_lines(rich.stdout), (
        "health check reports different problems depending on how it was "
        f"invoked.\n--- login shell (exit {rich.returncode}) ---\n{rich.stdout}"
        f"\n--- cron (exit {cron.returncode}) ---\n{cron.stdout}"
    )
    assert cron.returncode == rich.returncode, (
        f"exit status depends on invocation environment: login shell "
        f"{rich.returncode}, cron {cron.returncode}"
    )
