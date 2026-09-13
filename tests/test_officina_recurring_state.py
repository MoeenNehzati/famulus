from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

import pytest
import yaml

from officina.recurring import control, executor, native, runtime as recurring_runtime, state
from officina.recurring.jobs import validate_jobs_payload
from officina.recurring.records import RunRecord, write_record
from officina.recurring.runtime import ManagedSchedule
from officina.recurring.state import (
    LegacyStateConflict,
    cleanup_legacy_agent_environment,
    prepare_context_state,
)


@pytest.fixture(autouse=True)
def _portable_simulated_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(native.os, "getuid"):
        monkeypatch.setattr(native.os, "getuid", lambda: 1000, raising=False)


def _schedule(root: Path) -> ManagedSchedule:
    backend = Path(sys.executable).resolve()
    schedule = ManagedSchedule(
        descriptor_path=root / "config" / "schedule-descriptor.json",
        owner_id=str(root / "config"),
        python=backend,
        plugin_root=Path(__file__).resolve().parents[1],
        jobs_file=root / "config" / "jobs.yaml",
        log_root=root / "state" / "logs",
        config_root=root / "config",
        state_root=root / "state",
        native_registration_root=root / "native",
        default_backend="codex",
        backend_executables={"claude": backend, "codex": backend},
        environment={
            "HOME": str(root / "home"),
            "PATH": str(backend.parent),
            "CODEX_HOME": str(root / "codex"),
            "CLAUDE_CONFIG_DIR": str(root / "claude"),
        },
    )
    schedule.config_root.mkdir(parents=True, exist_ok=True)
    schedule.config_root.chmod(0o700)
    schedule.descriptor_path.write_text(json.dumps(recurring_runtime._payload(schedule)), encoding="utf-8")
    schedule.descriptor_path.chmod(0o600)
    return schedule


def test_linux_calendar_renders_stepped_hours_with_a_systemd_start_value() -> None:
    assert native.cron_to_systemd("0 */3 * * *") == "*-*-* 00/3:00:00"


def test_linux_calendar_collapses_a_full_minute_cycle_to_zero() -> None:
    assert native.cron_to_systemd("*/60 * * * *") == "*-*-* *:00:00"


def test_linux_calendar_collapses_a_full_hour_cycle_to_zero() -> None:
    assert native.cron_to_systemd("0 */24 * * *") == "*-*-* 0:00:00"


@pytest.mark.parametrize(
    ("cron", "calendar"),
    (
        ("*/15 9 * * *", "*-*-* 9:00/15:00"),
        ("* 9 * * *", "*-*-* 9:*:00"),
        ("*/15 */2 * * *", "*-*-* 00/2:00/15:00"),
        ("* */2 * * 0", "Sun *-*-* 00/2:*:00"),
        ("* */2 * * 7", "Sun *-*-* 00/2:*:00"),
    ),
)
def test_linux_calendar_renders_minute_and_hour_constraints_independently(
    cron: str, calendar: str
) -> None:
    assert native.cron_to_systemd(cron) == calendar


@pytest.mark.parametrize(
    "cron",
    (
        "0 * * *",
        "*/0 * * * *",
        "0 */-1 * * *",
        "*/one * * * *",
        "0 */1.5 * * *",
        "60 * * * *",
        "0 24 * * *",
        "1-2 * * * *",
        "1,2 * * * *",
        "0 1 1 * *",
        "0 1 * 1 *",
        "0 1 * * 8",
        "0 1 * * -1",
    ),
)
def test_linux_calendar_rejects_outside_the_managed_subset(cron: str) -> None:
    with pytest.raises(ValueError):
        native.cron_to_systemd(cron)


def test_healthcheck_sentinel_composes_the_exact_managed_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = ManagedSchedule(
        **{
            **_schedule(tmp_path / "root with spaces").__dict__,
            "environment": {"HOME": "/home/alice", "PATH": "/opt/famulus/bin"},
        }
    )
    monkeypatch.setattr(native.os, "getuid", lambda: 1234)
    health_root = schedule.log_root / "healthcheck"

    assert native._sentinel_script(schedule) == (
        f"/bin/mkdir -p {shlex.quote(str(health_root))}\n"
        "HOME=/home/alice PATH=/opt/famulus/bin "
        f"{shlex.quote(str(schedule.python))} -m officina.recurring.healthcheck "
        f"--plugin-root {shlex.quote(str(schedule.plugin_root))} "
        f"--descriptor {shlex.quote(str(schedule.descriptor_path))} "
        f"--log-root {shlex.quote(str(schedule.log_root))} "
        f"--cron >> {shlex.quote(str(health_root / 'run.log'))} 2>&1 || "
        "XDG_RUNTIME_DIR=/run/user/1234 "
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus "
        "/usr/bin/notify-send --urgency=critical 'Recurring tasks need attention' "
        f"\"$(cat {shlex.quote(str(health_root / 'last-failure.txt'))} "
        "2>/dev/null || echo 'The recurring health check could not run.')\" "
    )
    assert native._sentinel_line(schedule) == (
        "0 */4 * * * /bin/sh "
        f"{shlex.quote(str(schedule.native_registration_root / 'ai-recurring-healthcheck.sh'))} "
        "# ai-recurring-healthcheck"
    )


def test_healthcheck_crontab_entry_does_not_inline_owner_paths(tmp_path):
    schedule = _schedule(tmp_path.joinpath(*(["long-development-checkout"] * 20)))
    schedule = ManagedSchedule(**{**schedule.__dict__, "native_registration_root": Path("/home/alice/.config/systemd/user")})
    line = native._sentinel_line(schedule)
    assert str(schedule.descriptor_path) not in line
    assert len(line) < 300


def test_healthcheck_sentinel_replaces_once_and_preserves_unrelated_crontab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule = _schedule(tmp_path)
    current = {
        "value": (
            'MAILTO=""\n'
            "15 * * * * unrelated-command\n"
            "0 */4 * * * stale # ai-recurring-healthcheck\n"
        )
    }
    writes: list[str] = []

    monkeypatch.setattr(native, "_read_crontab", lambda: current["value"])

    def write_crontab(value: str) -> None:
        writes.append(value)
        current["value"] = value

    monkeypatch.setattr(native, "_write_crontab", write_crontab)

    native._update_sentinel(schedule, remove=False)
    expected = (
        'MAILTO=""\n'
        "15 * * * * unrelated-command\n"
        f"{native._sentinel_line(schedule)}\n"
    )
    assert current["value"] == expected

    native._update_sentinel(schedule, remove=False)
    assert current["value"] == expected
    assert writes == [expected]


def test_healthcheck_sentinel_refuses_to_overwrite_an_unreadable_crontab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def unreadable_crontab(argv: list[str], **_kwargs: object):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="crontab: user is not allowed to use this program",
        )

    monkeypatch.setattr(native.subprocess, "run", unreadable_crontab)

    with pytest.raises(RuntimeError, match="refusing to rewrite unreadable crontab"):
        native._update_sentinel(_schedule(tmp_path), remove=False)
    assert calls == [["crontab", "-l"]]


def test_linux_service_invokes_the_managed_executor_without_a_shell(
    tmp_path: Path,
) -> None:
    schedule = ManagedSchedule(
        **{
            **_schedule(tmp_path).__dict__,
            "descriptor_path": PureWindowsPath(
                "C:/Users/Alice/Famulus/config/schedule-descriptor.json"
            ),
            "python": PureWindowsPath("C:/Program Files/Famulus/python.exe"),
            "plugin_root": PureWindowsPath("C:/Famulus/plugin root"),
            "log_root": PureWindowsPath("C:/Users/Alice/Famulus/state/logs"),
            "environment": {},
        }
    )
    service = native.render_linux_service(
        schedule,
        {
            "name": "demo",
            "description": "Managed demo",
            "command": "invoke-skill demo",
            "schedule": "0 * * * *",
            "enabled": True,
        },
    )

    assert service.splitlines()[-1] == (
        r'ExecStart="C:\\Program Files\\Famulus\\python.exe" "-m" '
        r'"officina.recurring.executor" "--plugin-root" '
        r'"C:\\Famulus\\plugin root" "--descriptor" '
        r'"C:\\Users\\Alice\\Famulus\\config\\schedule-descriptor.json" '
        r'"--job" "demo" "--log-root" '
        r'"C:\\Users\\Alice\\Famulus\\state\\logs"'
    )
    assert all(
        fragment not in service
        for fragment in ("/bin/bash", "bash -c", '"bash" "-c"', ">>", "2>&1", " < ")
    )


def test_windows_wrapper_uses_crlf_for_environment_and_command_lines(
    tmp_path: Path,
) -> None:
    schedule = ManagedSchedule(
        **{
            **_schedule(tmp_path).__dict__,
            "python": Path("/opt/famulus/python.exe"),
            "environment": {"HOME": "/home/alice", "PATH": "/opt/famulus/bin"},
        }
    )
    wrapper = native.render_windows_wrapper(
        schedule,
        {
            "name": "demo",
            "command": "invoke-skill demo",
            "schedule": "0 * * * *",
            "enabled": True,
        },
    )
    lines = wrapper.splitlines(keepends=True)

    assert lines and all(line.endswith("\r\n") for line in lines)
    assert 'set "HOME=/home/alice"\r\n' in lines
    assert 'set "PATH=/opt/famulus/bin"\r\n' in lines
    assert (
        f'"{schedule.python}" "-m" "officina.recurring.executor" '
        f'"--plugin-root" "{schedule.plugin_root}" "--descriptor" '
        f'"{schedule.descriptor_path}" "--job" "demo" "--log-root" '
        f'"{schedule.log_root}" >> '
        f'"{schedule.log_root / "demo" / "scheduler.log"}" 2>&1\r\n'
    ) in lines
    assert lines[-1] == "exit /b %errorlevel%\r\n"


def _default_jobs(path: Path) -> Path:
    path.write_text(
        "jobs:\n- name: fresh\n  command: invoke-skill fresh\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "name",
    ("../escape", "nested/job", r"nested\\job", ".", "..", "CON", "bad:name", "line\nfeed", "snow-雪"),
)
def test_one_portable_job_schema_rejects_unsafe_identifiers(name):
    payload = {"jobs": [{
        "name": name,
        "command": "invoke-skill safe",
        "schedule": "0 * * * *",
        "enabled": True,
    }]}

    with pytest.raises(ValueError, match="job name"):
        validate_jobs_payload(payload)


@pytest.mark.parametrize("field", ("description", "command", "schedule"))
def test_one_portable_job_schema_rejects_crlf_in_rendered_text(field):
    job = {
        "name": "safe-job",
        "description": "safe",
        "command": "invoke-skill safe",
        "schedule": "0 * * * *",
        "enabled": True,
    }
    job[field] = "unsafe\r\nInjected=true"

    with pytest.raises(ValueError, match=field):
        validate_jobs_payload({"jobs": [job]})


def test_native_renderers_reject_traversal_before_touching_canary(tmp_path):
    schedule = _schedule(tmp_path)
    canary = tmp_path / "canary.bin"
    canary.write_bytes(b"unchanged\x00\xff")
    job = {"name": "../canary", "command": "invoke-skill safe", "schedule": "0 * * * *", "enabled": True}

    for render in (native.render_linux_service, native.render_linux_timer, native.render_macos_plist, native.render_windows_wrapper):
        with pytest.raises(ValueError, match="job name"):
            render(schedule, job)
    assert canary.read_bytes() == b"unchanged\x00\xff"


def test_sync_refuses_symlink_escape_before_overwriting_canary(tmp_path, monkeypatch):
    schedule = _schedule(tmp_path / "context")
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(
        "jobs:\n- name: safe\n  command: invoke-skill safe\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    schedule.native_registration_root.mkdir(parents=True)
    canary = tmp_path / "outside-canary.bin"
    canary.write_bytes(b"unchanged\x00\xff")
    service, _timer = native.linux_names("safe")
    (schedule.native_registration_root / service).symlink_to(canary)
    monkeypatch.setattr(native.sys, "platform", "linux")

    with pytest.raises(ValueError, match="escapes its owned root"):
        native.sync(schedule)

    assert canary.read_bytes() == b"unchanged\x00\xff"
    assert (schedule.state_root / "registrations.pending.json").exists()


def test_prepare_seeds_no_defaults_and_preserves_existing_jobs(tmp_path):
    schedule = _schedule(tmp_path / "context")
    defaults = _default_jobs(tmp_path / "default_jobs.yaml")

    prepare_context_state(schedule, default_jobs=defaults)
    schedule.jobs_file.write_text("jobs:\n- name: edited\n  command: invoke-skill edited\n  schedule: '0 * * * *'\n  enabled: true\n", encoding="utf-8")
    prepare_context_state(schedule, default_jobs=defaults)

    assert yaml.safe_load(schedule.jobs_file.read_text())["jobs"][0]["name"] == "edited"
    assert yaml.safe_load(defaults.read_text())["jobs"][0]["name"] == "fresh"
    assert schedule.log_root.is_dir()


def test_standard_migration_uses_recorded_owner_and_preserves_history(tmp_path):
    schedule = _schedule(tmp_path / "context")
    legacy = tmp_path / "old plugin" / "_rtx"
    legacy.mkdir(parents=True)
    (legacy / "jobs.yaml").write_text(
        "jobs:\n- name: old\n  command: ASSISTANT_DEFAULT=claude invoke-skill old\n"
        "  schedule: '0 * * * *'\n  enabled: false\n",
        encoding="utf-8",
    )
    old_log = legacy / "logs" / "old"
    old_log.mkdir(parents=True)
    (old_log / "latest.json").write_text('{"job_name":"old","success":true}\n', encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    (schedule.native_registration_root / "install-owner.json").write_text(
        json.dumps({"schema_version": 2, "installation_id": "standard", "source_path": str(legacy)}),
        encoding="utf-8",
    )

    prepare_context_state(schedule, default_jobs=_default_jobs(tmp_path / "defaults.yaml"))

    migrated = yaml.safe_load(schedule.jobs_file.read_text())["jobs"][0]
    assert migrated["command"] == "invoke-skill old"
    assert migrated["backend"] == "claude"
    assert migrated["enabled"] is False
    assert (schedule.log_root / "old" / "latest.json").read_bytes() == (old_log / "latest.json").read_bytes()
    assert (legacy / "jobs.yaml").exists() and old_log.exists()


def test_standard_migration_stops_when_canonical_and_legacy_jobs_differ(tmp_path):
    schedule = _schedule(tmp_path / "context")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _default_jobs(legacy / "jobs.yaml")
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    (schedule.native_registration_root / "install-owner.json").write_text(
        json.dumps({"schema_version": 2, "installation_id": "standard", "source_path": str(legacy)}), encoding="utf-8"
    )

    with pytest.raises(LegacyStateConflict) as caught:
        prepare_context_state(schedule, default_jobs=_default_jobs(tmp_path / "defaults.yaml"))

    assert str(schedule.jobs_file) in str(caught.value)
    assert str(legacy / "jobs.yaml") in str(caught.value)
    assert schedule.jobs_file.read_text() == "jobs: []\n"
    assert (legacy / "jobs.yaml").exists()


def test_interrupted_log_publication_preserves_source_and_retry_completes(tmp_path, monkeypatch):
    schedule = _schedule(tmp_path / "context")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _default_jobs(legacy / "jobs.yaml")
    (legacy / "logs" / "fresh").mkdir(parents=True)
    (legacy / "logs" / "fresh" / "latest.json").write_text('{"success":true}\n', encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    (schedule.native_registration_root / "install-owner.json").write_text(
        json.dumps({"schema_version": 2, "installation_id": "standard", "source_path": str(legacy)}), encoding="utf-8"
    )
    import officina.recurring.state as state
    real_replace = state._replace_directory
    monkeypatch.setattr(state, "_replace_directory", lambda source, target: (_ for _ in ()).throw(OSError("interrupted")))

    with pytest.raises(OSError, match="interrupted"):
        prepare_context_state(schedule, default_jobs=_default_jobs(tmp_path / "defaults.yaml"))
    assert (legacy / "logs" / "fresh" / "latest.json").exists()
    monkeypatch.setattr(state, "_replace_directory", real_replace)
    prepare_context_state(schedule, default_jobs=tmp_path / "defaults.yaml")
    assert (schedule.log_root / "fresh" / "latest.json").exists()


def test_executor_uses_structured_backend_override_and_rejects_inline_backend(tmp_path, monkeypatch):
    schedule = _schedule(tmp_path)
    resources = tmp_path / "resources" / "agents"
    resources.mkdir(parents=True)
    (resources / "background_run.md").write_text("---\ndescription: scheduled\n---\nrun", encoding="utf-8")
    schedule = ManagedSchedule(**{**schedule.__dict__, "plugin_root": resources.parent})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "command": "invoke-skill demo", "backend": "claude", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    observed = []
    monkeypatch.setattr(executor.subprocess, "run", lambda argv, **kwargs: observed.append(argv) or subprocess.CompletedProcess(argv, 0))

    assert executor.run_job(schedule=schedule, job_name="demo") == 0
    assert observed[0][0] == str(schedule.backend_executables["claude"])
    schedule.jobs_file.write_text(schedule.jobs_file.read_text().replace("invoke-skill demo", "ASSISTANT_DEFAULT=claude invoke-skill demo"), encoding="utf-8")
    with pytest.raises(ValueError, match="structured backend"):
        executor.run_job(schedule=schedule, job_name="demo")


def test_default_jobs_use_structured_backends_and_leave_wakeup_to_task10():
    default_path = Path(__file__).parents[1] / "src/officina/recurring/default_jobs.yaml"
    jobs = yaml.safe_load(default_path.read_text(encoding="utf-8"))["jobs"]
    email, daily = jobs
    assert email["command"] == "invoke-skill email-triage" and email["backend"] == "codex"
    assert daily["command"] == "invoke-skill daily-plan" and daily["backend"] == "claude"
    assert all(job["name"] != "llm-wakeup" for job in jobs)
    assert all("ASSISTANT_DEFAULT" not in job["command"] for job in jobs)


def test_cleanup_removes_only_exact_legacy_environment_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "sys_platform_linux", lambda: True)
    schedule = _schedule(tmp_path)
    env_file = Path(schedule.environment["HOME"]) / ".config/environment.d/20-ai-agent.conf"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("AI_AGENT_COMMAND_TEMPLATE=foreign {skill}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="AI_AGENT_COMMAND_TEMPLATE=foreign {skill}\n"))
    cleanup_legacy_agent_environment(schedule)
    assert env_file.exists()
    assert not any("unset-environment" in call for call in calls)


def test_cleanup_removes_exact_legacy_file_and_manager_value(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "sys_platform_linux", lambda: True)
    schedule = _schedule(tmp_path)
    env_file = Path(schedule.environment["HOME"]) / ".config/environment.d/20-ai-agent.conf"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n", encoding="utf-8")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        stdout = "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n" if "show-environment" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", run)
    cleanup_legacy_agent_environment(schedule)
    assert not env_file.exists()
    assert ["systemctl", "--user", "unset-environment", "AI_AGENT_COMMAND_TEMPLATE"] in calls


def test_sync_writes_exact_context_summary_owner_and_sentinel(tmp_path, monkeypatch):
    schedule = _schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(
        "jobs:\n- name: demo\n  command: invoke-skill demo\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    cron = {"value": "5 1 * * * backup # unrelated\n"}
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: cron["value"])
    monkeypatch.setattr(native, "_write_crontab", lambda value: cron.__setitem__("value", value))
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0))

    native.sync(schedule)

    summary = json.loads((schedule.state_root / "registrations.json").read_text())
    owner = json.loads((schedule.native_registration_root / "install-owner.json").read_text())
    assert summary == {"schema_version": 1, "owner_id": schedule.owner_id, "registrations": ["demo"]}
    assert not (schedule.state_root / "registrations.pending.json").exists()
    assert owner["owner_id"] == schedule.owner_id and owner["descriptor"] == str(schedule.descriptor_path)
    assert "backup # unrelated" in cron["value"]
    assert cron["value"].count("# ai-recurring-healthcheck") == 1


@pytest.mark.parametrize("platform", ("linux", "darwin", "win32"))
def test_sync_records_pending_before_native_mutation_and_keeps_it_on_interruption(
    tmp_path, monkeypatch, platform
):
    schedule = _schedule(tmp_path / platform)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(
        "jobs:\n- name: demo\n  command: invoke-skill demo\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(native.sys, "platform", platform)
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(
            RuntimeError("injected native interruption")
        ),
    )

    with pytest.raises(RuntimeError, match="injected native interruption"):
        native.sync(schedule)

    pending = json.loads(
        (schedule.state_root / "registrations.pending.json").read_text(encoding="utf-8")
    )
    assert pending == {
        "schema_version": 1,
        "owner_id": schedule.owner_id,
        "registrations": ["demo"],
        "publication_state": "pending",
    }


def test_nonowner_remove_context_preserves_the_shared_sentinel(tmp_path, monkeypatch):
    shared_native = tmp_path / "native"
    schedule = ManagedSchedule(**{
        **_schedule(tmp_path / "nonowner").__dict__,
        "native_registration_root": shared_native,
    })
    owner = ManagedSchedule(**{
        **_schedule(tmp_path / "owner").__dict__,
        "native_registration_root": shared_native,
    })
    shared_native.mkdir()
    native._write_owner(owner)
    cron = {
        "value": "\n".join([
            f"0 */4 * * * selected {native._sentinel_marker()}",
            "5 1 * * * backup # unrelated",
        ]) + "\n"
    }
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: cron["value"])
    monkeypatch.setattr(native, "_write_crontab", lambda value: cron.__setitem__("value", value))
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0))

    native.remove_context(schedule)

    assert native._sentinel_marker() in cron["value"]
    assert "backup # unrelated" in cron["value"]


def test_migration_rejects_malformed_legacy_run_record_without_switching_logs(tmp_path):
    schedule = _schedule(tmp_path / "context")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _default_jobs(legacy / "jobs.yaml")
    (legacy / "logs" / "fresh").mkdir(parents=True)
    (legacy / "logs" / "fresh" / "latest.json").write_text("not-json", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    (schedule.native_registration_root / "install-owner.json").write_text(
        json.dumps({"schema_version": 2, "installation_id": "standard", "source_path": str(legacy)}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="malformed recurring record"):
        prepare_context_state(schedule, default_jobs=_default_jobs(tmp_path / "defaults.yaml"))
    assert not schedule.log_root.exists()
    assert (legacy / "logs" / "fresh" / "latest.json").read_text() == "not-json"


def test_remove_context_clears_only_selected_native_state_and_preserves_mutable_state(tmp_path, monkeypatch):
    selected = _schedule(tmp_path / "one")
    other = _schedule(tmp_path / "two")
    for schedule in (selected, other):
        schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
        schedule.log_root.mkdir(parents=True)
        (schedule.log_root / "history").write_text("keep", encoding="utf-8")
        schedule.native_registration_root.mkdir(parents=True)
        (schedule.native_registration_root / native.linux_names("same")[0]).write_text("unit", encoding="utf-8")
    native._write_owner(selected)
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            3 if argv[:3] == ["systemctl", "--user", "is-active"] else
            1 if argv[:3] == ["systemctl", "--user", "is-enabled"] else 0,
        ),
    )

    native.remove_context(selected)

    assert not (selected.native_registration_root / native.linux_names("same")[0]).exists()
    assert (other.native_registration_root / native.linux_names("same")[0]).exists()
    assert (selected.log_root / "history").read_text() == "keep"
    assert selected.jobs_file.exists()
    assert json.loads((selected.state_root / "registrations.json").read_text())["registrations"] == []


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_remove_context_failure_preserves_fail_closed_state_and_retry_converges(
    tmp_path, monkeypatch, platform
):
    schedule = _schedule(tmp_path / platform)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(
        "jobs:\n- name: same\n  command: invoke-skill same\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    schedule.log_root.mkdir(parents=True)
    (schedule.log_root / "history").write_text("keep", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    native._write_owner(schedule)
    native.write_registration_summary(schedule, ["same"])
    failing = {"value": True}
    monkeypatch.setattr(native.sys, "platform", platform)

    if platform == "linux":
        service, timer = native.linux_names("same")
        (schedule.native_registration_root / service).write_text("service", encoding="utf-8")
        (schedule.native_registration_root / timer).write_text("timer", encoding="utf-8")
        cron = {"value": f"0 */4 * * * check {native._sentinel_marker()}\n"}
        monkeypatch.setattr(native, "_read_crontab", lambda: cron["value"])
        monkeypatch.setattr(native, "_write_crontab", lambda value: cron.__setitem__("value", value))
        active = {service, timer}
        enabled = {timer}

        def run(argv, **kwargs):
            if argv[:4] == ["systemctl", "--user", "disable", "--now"]:
                if failing["value"]:
                    return subprocess.CompletedProcess(argv, 1)
                active.discard(argv[4])
                enabled.discard(argv[4])
                return subprocess.CompletedProcess(argv, 0)
            if argv[:3] == ["systemctl", "--user", "stop"]:
                active.discard(argv[3])
                return subprocess.CompletedProcess(argv, 0)
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return subprocess.CompletedProcess(argv, 0 if argv[3] in active else 3)
            if argv[:3] == ["systemctl", "--user", "is-enabled"]:
                return subprocess.CompletedProcess(argv, 0 if argv[3] in enabled else 1)
            return subprocess.CompletedProcess(argv, 0, stdout="")

        monkeypatch.setattr(native.subprocess, "run", run)
        artifact = schedule.native_registration_root / timer
    elif platform == "darwin":
        path = schedule.native_registration_root / "ai-same.plist"
        path.write_bytes(native.render_macos_plist(schedule, {"name": "same", "command": "invoke-skill same", "schedule": "0 * * * *", "enabled": True}))

        def run(argv, **kwargs):
            if argv[:2] == ["launchctl", "bootout"]:
                return subprocess.CompletedProcess(argv, 1 if failing["value"] else 0)
            if argv[:2] == ["launchctl", "print"]:
                if failing["value"]:
                    return subprocess.CompletedProcess(argv, 0)
                return subprocess.CompletedProcess(
                    argv, 113, stderr="Could not find service"
                )
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(native.subprocess, "run", run)
        artifact = path
    else:
        task = native.windows_task_name("same")
        active = {task}
        wrapper = schedule.native_registration_root / native.windows_wrapper_name("same")
        wrapper.write_text("wrapper", encoding="utf-8")
        monkeypatch.setattr(
            native,
            "_windows_task_inventory",
            lambda: native._NativeInventory(True, tuple(sorted(active))),
        )

        def run(argv, **kwargs):
            if argv[:2] == ["schtasks", "/Delete"]:
                if failing["value"]:
                    return subprocess.CompletedProcess(argv, 1)
                active.discard(argv[3])
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(native.subprocess, "run", run)
        artifact = wrapper

    with pytest.raises(RuntimeError, match="retry recurring-tasks remove-context"):
        native.remove_context(schedule)
    assert artifact.exists()
    assert native._owner_path(schedule).exists()
    assert (schedule.state_root / "registrations.pending.json").exists()
    assert (schedule.log_root / "history").read_text() == "keep"

    failing["value"] = False
    native.remove_context(schedule)
    assert not artifact.exists()
    assert not native._owner_path(schedule).exists()
    assert json.loads((schedule.state_root / "registrations.json").read_text())["registrations"] == []


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_remove_context_fails_closed_when_native_inventory_is_unavailable(
    tmp_path, monkeypatch, platform
):
    schedule = _schedule(tmp_path / platform)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(
        "jobs:\n- name: same\n  command: codex run\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    schedule.native_registration_root.mkdir(parents=True)
    native._write_owner(schedule)
    native.write_registration_summary(schedule, ["same"])
    monkeypatch.setattr(native.sys, "platform", platform)
    if platform == "linux":
        service, timer = native.linux_names("same")
        (schedule.native_registration_root / service).write_text("service", encoding="utf-8")
        (schedule.native_registration_root / timer).write_text("timer", encoding="utf-8")
        monkeypatch.setattr(
            native.subprocess,
            "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 2, stderr="systemd query unavailable"
            ),
        )
    elif platform == "darwin":
        path = schedule.native_registration_root / "ai-same.plist"
        path.write_bytes(native.render_macos_plist(schedule, {"name": "same", "command": "invoke-skill same", "schedule": "0 * * * *", "enabled": True}))
        monkeypatch.setattr(
            native.subprocess,
            "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 1, stderr="launchd manager unavailable"
            ),
        )
    else:
        wrapper = schedule.native_registration_root / native.windows_wrapper_name("same")
        wrapper.write_text("wrapper", encoding="utf-8")
        monkeypatch.setattr(
            native.subprocess,
            "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Task Scheduler unavailable"
            ),
        )

    with pytest.raises(RuntimeError, match="retry recurring-tasks remove-context"):
        native.remove_context(schedule)
    assert native._owner_path(schedule).exists()
    assert (schedule.state_root / "registrations.pending.json").exists()


def test_linux_inactive_but_enabled_orphan_is_disabled_and_verified_before_clear(
    tmp_path, monkeypatch
):
    schedule = _schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    service, timer = native.linux_names("orphan")
    (schedule.native_registration_root / service).write_text("service", encoding="utf-8")
    (schedule.native_registration_root / timer).write_text("timer", encoding="utf-8")
    native._write_owner(schedule)
    native.write_registration_summary(schedule, [])
    enabled = {timer}
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(argv, 3)
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0 if argv[3] in enabled else 1)
        if argv[:4] == ["systemctl", "--user", "disable", "--now"]:
            enabled.discard(argv[4])
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native.subprocess, "run", run)
    native.remove_context(schedule)

    assert ["systemctl", "--user", "disable", "--now", timer] in calls
    assert not (schedule.native_registration_root / timer).exists()
    assert not (schedule.native_registration_root / service).exists()
    assert json.loads((schedule.state_root / "registrations.json").read_text())["registrations"] == []


def test_linux_inventory_removes_inactive_enabled_orphan_without_registration_file(
    tmp_path, monkeypatch
):
    schedule = _schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    service, timer = native.linux_names("orphan")
    native._write_owner(schedule)
    native.write_registration_summary(schedule, [])
    enabled = {timer}
    known = {timer, service}
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "list-unit-files"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(
                    f"{unit} {'enabled' if unit in enabled else 'static'}\n"
                    for unit in sorted(known)
                )
            )
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(argv, 3)
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0 if argv[3] in enabled else 1)
        if argv[:4] == ["systemctl", "--user", "disable", "--now"]:
            enabled.discard(argv[4])
            known.clear()
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(native.subprocess, "run", run)
    native.remove_context(schedule)

    assert ["systemctl", "--user", "disable", "--now", timer] in calls


def test_linux_inventory_treats_exit_one_without_output_as_an_empty_namespace(
    monkeypatch,
):
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr=""
        ),
    )

    inventory = native._systemd_unit_inventory(
        "ai-", native.linux_session_environment()
    )

    assert inventory.available
    assert inventory.entries == ()
    assert inventory.detail == ""


def test_linux_teardown_disables_independently_enabled_service_and_converges(
    tmp_path, monkeypatch
):
    schedule = _schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    service, timer = native.linux_names("orphan")
    (schedule.native_registration_root / service).write_text("service", encoding="utf-8")
    (schedule.native_registration_root / timer).write_text("timer", encoding="utf-8")
    native._write_owner(schedule)
    native.write_registration_summary(schedule, [])
    enabled = {service}
    fail_service = {service}
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "list-unit-files"]:
            return subprocess.CompletedProcess(argv, 0, stdout="")
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(argv, 3)
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0 if argv[3] in enabled else 1)
        if argv[:4] == ["systemctl", "--user", "disable", "--now"]:
            if argv[4] in fail_service:
                return subprocess.CompletedProcess(argv, 1)
            enabled.discard(argv[4])
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(native.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="retry recurring-tasks remove-context"):
        native.remove_context(schedule)
    assert schedule.native_registration_root.joinpath(service).exists()
    fail_service.clear()
    native.remove_context(schedule)
    assert ["systemctl", "--user", "disable", "--now", service] in calls
    assert not schedule.native_registration_root.joinpath(service).exists()


def test_macos_inventory_boots_out_loaded_orphan(tmp_path, monkeypatch):
    selected = _schedule(tmp_path / "selected")
    selected.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    selected.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    selected.native_registration_root.mkdir(parents=True)
    native._write_owner(selected)
    native.write_registration_summary(selected, [])
    orphan = native.launchd_label("orphan")
    loaded = {orphan}
    monkeypatch.setattr(native.sys, "platform", "darwin")

    def run(argv, **kwargs):
        if argv[:2] == ["launchctl", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="".join(f"-\t0\t{label}\n" for label in sorted(loaded))
            )
        if argv[:2] == ["launchctl", "print"]:
            label = argv[2].rsplit("/", 1)[-1]
            if label in loaded:
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 113, stderr="Could not find service")
        if argv[:2] == ["launchctl", "bootout"]:
            loaded.discard(argv[2].rsplit("/", 1)[-1])
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native.subprocess, "run", run)
    native.remove_context(selected)

    assert orphan not in loaded
    assert not loaded


def test_macos_inventory_failure_preserves_owner_and_summary(tmp_path, monkeypatch):
    schedule = _schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir(parents=True)
    native._write_owner(schedule)
    native.write_registration_summary(schedule, [])
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stderr="launchd unavailable"
        ),
    )

    with pytest.raises(RuntimeError, match="retry recurring-tasks remove-context"):
        native.remove_context(schedule)
    assert native._owner_path(schedule).exists()
    assert (schedule.state_root / "registrations.pending.json").exists()


def _retired_standard_and_two_development_contexts_complete_managed_lifecycle_without_cross_effects(
    tmp_path, monkeypatch
):
    shared_native = tmp_path / "host-native"
    identities = [
        "standard",
        "dev-0123456789abcdef0123456789abcdef",
        "dev-ffffffffffffffffffffffffffffffff",
    ]
    schedules = []
    for installation in identities:
        schedule = _schedule(tmp_path / installation, installation)
        schedule = ManagedSchedule(
            **{**schedule.__dict__, "native_registration_root": shared_native}
        )
        schedule.config_root.mkdir(parents=True)
        schedule.descriptor_path.write_text(
            json.dumps({"installation_id": installation}), encoding="utf-8"
        )
        schedule.state_root.mkdir(parents=True)
        (schedule.config_root / "context-canary.bin").write_bytes(
            f"config:{installation}".encode()
        )
        (schedule.state_root / "history-canary.bin").write_bytes(
            f"history:{installation}".encode()
        )
        schedules.append(schedule)

    schedules_by_descriptor = {
        schedule.descriptor_path: schedule for schedule in schedules
    }
    monkeypatch.setattr(
        control,
        "load_managed_schedule",
        lambda *, runtime_root, descriptor_path: schedules_by_descriptor[descriptor_path],
    )

    defaults = tmp_path / "default_jobs.yaml"
    defaults.write_text(
        "jobs:\n- name: same\n  command: codex run\n  backend: codex\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    legacy = tmp_path / "installed-skill" / "_rtx"
    legacy.mkdir(parents=True)
    legacy_jobs = legacy / "jobs.yaml"
    legacy_jobs.write_text(
        "jobs:\n- name: same\n  command: ASSISTANT_DEFAULT=codex codex run\n"
        "  schedule: '0 * * * *'\n  enabled: true\n",
        encoding="utf-8",
    )
    write_record(
        log_root=legacy / "logs",
        record=RunRecord(
            job_name="same", started_at="2025-12-31T23:00:00+00:00",
            finished_at="2025-12-31T23:00:01+00:00", process_exit_code=0,
            success=True, run_id="legacy-run",
        ),
    )
    legacy_before = {
        path.relative_to(legacy).as_posix(): path.read_bytes()
        for path in legacy.rglob("*") if path.is_file()
    }
    shared_native.mkdir(parents=True)
    (shared_native / "install-owner.json").write_text(
        json.dumps(
            {"schema_version": 2, "installation_id": "standard", "source_path": str(legacy)}
        ),
        encoding="utf-8",
    )

    active: set[str] = set()
    enabled: set[str] = set()
    service_schedules = {
        native.linux_names("same", schedule.installation_id)[0]: schedule
        for schedule in schedules
    }
    failed_disable: set[str] = set()
    cron = {"value": "5 1 * * * backup # unrelated\n"}
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: cron["value"])
    monkeypatch.setattr(
        native, "_write_crontab", lambda value: cron.__setitem__("value", value)
    )

    def run(argv, **kwargs):
        if argv[:4] == ["systemctl", "--user", "enable", "--now"]:
            active.add(argv[4])
            enabled.add(argv[4])
            return subprocess.CompletedProcess(argv, 0)
        if argv[:4] == ["systemctl", "--user", "disable", "--now"]:
            if argv[4] in failed_disable:
                return subprocess.CompletedProcess(argv, 1, stderr="injected failure")
            active.discard(argv[4])
            enabled.discard(argv[4])
            return subprocess.CompletedProcess(argv, 0)
        if argv[:3] == ["systemctl", "--user", "stop"]:
            active.discard(argv[3])
            return subprocess.CompletedProcess(argv, 0)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(argv, 0 if argv[3] in active else 3)
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0 if argv[3] in enabled else 1)
        if argv[:3] == ["systemctl", "--user", "start"]:
            schedule = service_schedules[argv[4]]
            write_record(
                log_root=schedule.log_root,
                record=RunRecord(
                    job_name="same", started_at="2026-01-01T00:00:00+00:00",
                    finished_at="2026-01-01T00:00:01+00:00",
                    process_exit_code=0, success=True,
                    run_id=f"run-{schedule.installation_id}",
                ),
            )
            return subprocess.CompletedProcess(argv, 0)
        if argv[:3] == ["systemctl", "--user", "list-timers"]:
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(sorted(active)))
        if argv[:3] == ["systemctl", "--user", "is-system-running"]:
            return subprocess.CompletedProcess(argv, 0, stdout="running")
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(native.subprocess, "run", run)

    def tree_bytes(root, *, excluded=()):
        excluded = set(excluded)
        return tuple(
            (path.relative_to(root).as_posix(), path.read_bytes())
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix() not in excluded
        )

    def context_bytes(schedule):
        prefix = f"ai-{native.registration_token(schedule.installation_id)}"
        native_files = []
        for path in sorted(shared_native.iterdir()):
            if path == native._owner_path(schedule):
                native_files.append((path.name, path.read_bytes()))
                continue
            suffix = ".timer" if path.name.endswith(".timer") else ".service"
            if native._context_job(
                path.name, prefix, suffix, schedule.installation_id
            ) is not None:
                native_files.append((path.name, path.read_bytes()))
        marker = native._sentinel_marker(schedule.installation_id)
        sentinel = tuple(
            line for line in cron["value"].splitlines() if line.rstrip().endswith(marker)
        )
        return (
            tree_bytes(schedule.config_root),
            tree_bytes(schedule.state_root),
            tuple(native_files),
            sentinel,
        )

    def mutable_bytes(schedule):
        return (
            tree_bytes(schedule.config_root),
            tree_bytes(
                schedule.state_root,
                excluded={"registrations.json", "registrations.pending.json"},
            ),
        )

    def isolated(selected, action):
        others = [schedule for schedule in schedules if schedule is not selected]
        before = {schedule.installation_id: context_bytes(schedule) for schedule in others}
        result = action()
        assert {schedule.installation_id: context_bytes(schedule) for schedule in others} == before
        return result

    for schedule in schedules:
        isolated(schedule, lambda schedule=schedule: prepare_context_state(schedule, default_jobs=defaults))
    assert yaml.safe_load(schedules[0].jobs_file.read_text())["jobs"][0]["backend"] == "codex"
    assert yaml.safe_load(schedules[0].jobs_file.read_text()) == yaml.safe_load(
        schedules[1].jobs_file.read_text()
    )
    assert (schedules[0].log_root / "same" / "latest.json").exists()
    assert {
        path.relative_to(legacy).as_posix(): path.read_bytes()
        for path in legacy.rglob("*") if path.is_file()
    } == legacy_before

    for schedule in schedules:
        (schedule.config_root / "unexpected" / "nested").mkdir(parents=True)
        (schedule.config_root / "unexpected" / "nested" / "config.bin").write_bytes(
            f"unexpected-config:{schedule.installation_id}".encode()
        )
        (schedule.state_root / "unexpected" / "nested").mkdir(parents=True)
        (schedule.state_root / "unexpected" / "nested" / "state.bin").write_bytes(
            f"unexpected-state:{schedule.installation_id}".encode()
        )
        (schedule.state_root / "outcomes").mkdir()
        (schedule.state_root / "outcomes" / "unexpected.bin").write_bytes(
            f"outcome:{schedule.installation_id}".encode()
        )
        (schedule.state_root / "in-flight").mkdir()
        (schedule.state_root / "in-flight" / "unexpected.bin").write_bytes(
            f"in-flight:{schedule.installation_id}".encode()
        )

    for schedule in schedules:
        assert isolated(
            schedule,
            lambda schedule=schedule: control.run_operation(
                schedule, operation="sync", name=None, lines=50
            ),
        ) == 0
    for schedule in schedules:
        (schedule.log_root / "same").mkdir(parents=True, exist_ok=True)
        (schedule.log_root / "same" / "run.log").write_bytes(
            f"run-log:{schedule.installation_id}".encode()
        )
        (schedule.log_root / "same" / "running.json").write_text(
            json.dumps({"job_name": "same", "run_id": f"in-flight-{schedule.installation_id}"}),
            encoding="utf-8",
        )
        (schedule.log_root / "healthcheck").mkdir(parents=True)
        (schedule.log_root / "healthcheck" / "run.log").write_bytes(
            f"health-log:{schedule.installation_id}".encode()
        )
        (schedule.log_root / "healthcheck" / "last-failure.txt").write_bytes(
            f"health-detail:{schedule.installation_id}".encode()
        )
    for schedule in schedules:
        for operation, name in (("status", None), ("test", "same"), ("healthcheck", None)):
            assert isolated(
                schedule,
                lambda schedule=schedule, operation=operation, name=name: control.run_operation(
                    schedule, operation=operation, name=name, lines=50
                ),
            ) == 0

    for selected in schedules:
        assert isolated(
            selected,
            lambda selected=selected: control.run_operation(
                selected, operation="disable", name="same", lines=50
            ),
        ) == 0
        assert isolated(
            selected,
            lambda selected=selected: control.run_operation(
                selected, operation="enable", name="same", lines=50
            ),
        ) == 0

    selected = schedules[1]
    failed_disable.add(native.linux_names("same", selected.installation_id)[1])
    selected_mutable = mutable_bytes(selected)
    def failed_remove():
        with pytest.raises(RuntimeError, match="retry recurring-tasks remove-context"):
            control.run_operation(selected, operation="remove-context", name=None, lines=50)
    isolated(selected, failed_remove)
    assert mutable_bytes(selected) == selected_mutable
    failed_disable.clear()
    assert isolated(
        selected,
        lambda: control.run_operation(selected, operation="remove-context", name=None, lines=50),
    ) == 0
    assert mutable_bytes(selected) == selected_mutable
    assert isolated(
        selected,
        lambda: control.run_operation(selected, operation="remove-context", name=None, lines=50),
    ) == 0
    assert mutable_bytes(selected) == selected_mutable
    assert selected.jobs_file.exists()
    assert (selected.state_root / "history-canary.bin").read_bytes().startswith(b"history:")
    assert (selected.log_root / "same" / "latest.json").exists()
    for selected in (schedules[0], schedules[2]):
        selected_mutable = mutable_bytes(selected)
        assert isolated(
            selected,
            lambda selected=selected: control.run_operation(
                selected, operation="remove-context", name=None, lines=50
            ),
        ) == 0
        assert mutable_bytes(selected) == selected_mutable
        assert isolated(
            selected,
            lambda selected=selected: control.run_operation(
                selected, operation="remove-context", name=None, lines=50
            ),
        ) == 0
        assert mutable_bytes(selected) == selected_mutable
        assert selected.jobs_file.exists()
        assert (selected.state_root / "history-canary.bin").exists()
        assert (selected.log_root / "same" / "latest.json").exists()
