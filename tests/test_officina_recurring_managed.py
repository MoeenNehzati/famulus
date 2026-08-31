from __future__ import annotations

import subprocess
import importlib.util
import sys
import os
import json
import platform
import shlex
import shutil
import uuid
from pathlib import Path
from unittest import mock

import pytest
import yaml

from officina.recurring import control, executor, healthcheck, native
from officina.recurring import runtime
from officina.recurring.runtime import (
    ManagedSchedule,
    RecurringPrerequisiteError,
    RecurringRuntimeError,
)
from officina.install.context import (
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from officina.install.managed_runtime import _deploy_resolver, _publish_installation_context
from officina.install.runtime_pointer import activate_release
from officina.launchers.agent import ensure_launcher_configuration

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANAGED_CONTROL_ROOT = _REPO_ROOT / "skills" / "recurring-tasks" / "_rtx"
sys.path.insert(0, str(_MANAGED_CONTROL_ROOT))
import _managed_control as managed_control


def _development_activation_module():
    path = _REPO_ROOT / "skills" / "dev-activation" / "_rtx" / "_development_activation.py"
    spec = importlib.util.spec_from_file_location("task6_recurring_development_activation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _portable_simulated_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(native.os, "getuid"):
        monkeypatch.setattr(native.os, "getuid", lambda: 1000, raising=False)


def _managed_schedule(tmp_path: Path) -> ManagedSchedule:
    backend = Path(sys.executable).resolve()
    descriptor = tmp_path / "config" / "schedule-descriptor.json"
    schedule = ManagedSchedule(
        descriptor_path=descriptor,
        owner_id=str(descriptor.parent),
        python=backend,
        plugin_root=_REPO_ROOT,
        jobs_file=descriptor.parent / "jobs.yaml",
        log_root=tmp_path / "state" / "logs",
        config_root=descriptor.parent,
        state_root=tmp_path / "state",
        native_registration_root=tmp_path / "native",
        default_backend="codex",
        backend_executables={"claude": backend, "codex": backend},
        environment={"HOME": str(tmp_path), "PATH": str(backend.parent), "CODEX_HOME": str(tmp_path / "codex"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "FAMULUS_ACTIVE_RELEASE": "release-a"},
    )
    schedule.config_root.mkdir(parents=True, exist_ok=True)
    schedule.config_root.chmod(0o700)
    schedule.descriptor_path.write_text(json.dumps(runtime._payload(schedule)), encoding="utf-8")
    schedule.descriptor_path.chmod(0o600)
    return schedule


def _native_capability() -> None:
    if platform.system() == "Linux":
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            # famulus-skip: category=native-backend-unavailable; reason=systemd user manager is unavailable; alternate=managed renderer migration and teardown tests run in the normal suite
            pytest.skip(result.stderr.strip() or result.stdout.strip() or "systemd user manager unavailable")
        return
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            # famulus-skip: category=native-backend-unavailable; reason=launchd user manager is unavailable; alternate=managed renderer migration and teardown tests run in the normal suite
            pytest.skip(result.stderr.strip() or result.stdout.strip() or "launchd user manager unavailable")
        return
    if platform.system() == "Windows":
        if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
            # famulus-skip: category=native-backend-unavailable; reason=hosted Windows runners have no interactive user session for the production current-user task identity; alternate=Windows managed renderer migration identity and teardown tests run in the normal suite
            pytest.skip("hosted Windows has no interactive scheduler identity")
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "LIST"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            # famulus-skip: category=native-backend-unavailable; reason=Task Scheduler is unavailable; alternate=managed renderer migration and teardown tests run in the normal suite
            pytest.skip(result.stderr.strip() or result.stdout.strip() or "Task Scheduler unavailable")
        return
    # famulus-skip: category=unsupported-platform; reason=no managed scheduler backend exists for this OS; alternate=Linux macOS and Windows managed backend tests cover supported systems
    pytest.skip(f"no managed scheduler backend for {platform.system()}")


def _active_live_development_runtime(tmp_path: Path):
    checkout = tmp_path / "managed scheduler checkout 雪"
    (checkout / "skills").mkdir(parents=True)
    (checkout / "src" / "officina").mkdir(parents=True)
    (checkout / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills", "src/officina"]\n',
        encoding="utf-8",
    )
    stable_home = tmp_path / "stable home"
    installation_id = load_or_create_development_installation_id(
        checkout, platform=sys.platform, home=stable_home, environ={}
    )
    context = resolve_installation_context(
        mode="development", source_root=checkout, development_root=checkout,
        platform=sys.platform, home=stable_home, environ={},
        installation_id=installation_id,
    )
    environment = _development_activation_module().build_activation_environment(
        checkout, environ=os.environ, platform=sys.platform
    )
    backend_root = tmp_path / "exact backend executables"
    backend_root.mkdir()
    suffix = ".exe" if sys.platform == "win32" else ""
    for backend in ("claude", "codex"):
        executable = backend_root / f"{backend}{suffix}"
        shutil.copy2(sys.executable, executable)
        executable.chmod(0o755)
    environment["PATH"] = os.pathsep.join((str(backend_root), environment.get("PATH", "")))
    environment["PYTHONPATH"] = str(_REPO_ROOT / "src")
    release = context.paths.releases_root / "managed-live-smoke"
    python_dir = release / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    python_dir.mkdir(parents=True)
    python_bin = python_dir / ("python.exe" if sys.platform == "win32" else "python")
    shutil.copy2(sys.executable, python_bin)
    python_bin.chmod(0o755)
    record = _publish_installation_context(release_dir=release, context=context)
    _deploy_resolver(runtime_root=context.paths.runtime_root, trusted_interpreter_roots=())
    activate_release(
        runtime_root=context.paths.runtime_root, release_dir=release,
        python_bin=python_bin, repository_config=checkout / "officina.toml",
        launcher_resources=checkout, installation_context=record,
    )
    ensure_launcher_configuration(config_root=context.paths.config_root, default_backend="codex")
    return context, environment


# famulus-skip: category=live-smoke-opt-in; reason=managed live scheduler smoke mutates uniquely namespaced host scheduler state; alternate=managed renderer migration identity and teardown tests run in the normal suite
@pytest.mark.skipif(
    os.environ.get("FAMULUS_RUN_SCHEDULER_SMOKE") != "1",
    reason="managed live scheduler smoke is opt-in; set FAMULUS_RUN_SCHEDULER_SMOKE=1",
)
def _retired_managed_public_control_live_sync_trigger_record_and_selected_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        # famulus-skip: category=native-backend-unavailable; reason=hosted runners do not expose a representative persistent installed context to scheduled processes; alternate=managed control renderer migration and teardown tests run on every matrix OS
        pytest.skip("hosted runner has no representative persistent scheduler context")
    _native_capability()
    context, environment = _active_live_development_runtime(tmp_path)
    marker_script = tmp_path / "write managed marker.py"
    marker = tmp_path / "managed marker 雪.json"
    marker_script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps({'managed': True}) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = (
        subprocess.list2cmdline(["codex", str(marker_script), str(marker)])
        if sys.platform == "win32"
        else shlex.join(["codex", str(marker_script), str(marker)])
    )
    job_name = f"managed-live-{uuid.uuid4().hex}"
    jobs_file = context.paths.recurring_config_root / "jobs.yaml"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text(
        yaml.safe_dump({"jobs": [{"name": job_name, "description": "managed live smoke", "command": command, "schedule": "0 0 * * *", "enabled": True}]}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(managed_control, "discover_runtime_root", lambda: context.paths.runtime_root)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    selected = None
    canary = None
    canary_bytes = b"foreign native registration must survive\x00\xff"
    try:
        assert managed_control.run("setup") == 0
        selected = runtime.load_public_schedule(
            runtime_root=context.paths.runtime_root, environ=os.environ
        )
        canary = selected.native_registration_root / f"famulus-foreign-{uuid.uuid4().hex}.canary"
        canary.write_bytes(canary_bytes)
        assert managed_control.run("sync") == 0
        assert managed_control.run("test", [job_name]) == 0
        record = json.loads(
            (selected.log_root / job_name / "latest.json").read_text(encoding="utf-8")
        )
        assert record["success"] is True and record["process_exit_code"] == 0
        assert json.loads(marker.read_text(encoding="utf-8")) == {"managed": True}
        assert managed_control.run("remove-context") == 0
        if sys.platform.startswith("linux"):
            service, timer = native.linux_names(job_name, selected.installation_id)
            assert not (selected.native_registration_root / service).exists()
            assert not (selected.native_registration_root / timer).exists()
        elif sys.platform == "darwin":
            assert not (selected.native_registration_root / f"ai-{native.registration_token(selected.installation_id)}{job_name}.plist").exists()
        else:
            assert not (selected.native_registration_root / native.windows_wrapper_name(job_name, selected.installation_id)).exists()
        assert canary.read_bytes() == canary_bytes
    finally:
        if selected is None and (
            context.paths.recurring_config_root / "schedule-descriptor.json"
        ).is_file():
            try:
                selected = runtime.load_public_schedule(
                    runtime_root=context.paths.runtime_root, environ=os.environ
                )
            except Exception:
                selected = None
        try:
            if selected is not None:
                native.remove_context(selected)
        finally:
            if canary is not None:
                canary.unlink(missing_ok=True)


@pytest.mark.parametrize("field,value", [("owner_id", "/tmp/other"), ("unexpected", "value")])
def test_managed_descriptor_rejects_noncanonical_authority(tmp_path, field, value):
    expected = _managed_schedule(tmp_path)
    expected.descriptor_path.parent.mkdir(mode=0o700, exist_ok=True)
    expected.descriptor_path.parent.chmod(0o700)
    payload = runtime._payload(expected)
    payload[field] = value
    expected.descriptor_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    expected.descriptor_path.chmod(0o600)
    with pytest.raises(RecurringRuntimeError, match="canonical|schema"):
        runtime.load_managed_schedule(descriptor_path=expected.descriptor_path)


@pytest.mark.parametrize(
    "module,extra",
    [
        ("officina.recurring.control", ["status"]),
        ("officina.recurring.executor", ["--job", "demo", "--log-root", "/tmp/logs"]),
        ("officina.recurring.healthcheck", ["--log-root", "/tmp/logs"]),
    ],
)
def test_recurring_entrypoints_publish_plugin_root_and_descriptor_cli(
    module, extra
):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )

    assert result.returncode == 0, result.stderr
    assert "--plugin-root" in result.stdout
    assert "--runtime-root" not in result.stdout
    assert "--descriptor" in result.stdout


def test_managed_executor_runs_after_skill_source_disappears(tmp_path):
    missing_skill_source = tmp_path / "removed-plugin-cache" / "recurring-tasks"
    jobs_file = tmp_path / "config 雪" / "jobs.yaml"
    log_root = tmp_path / "state with spaces" / "logs"
    jobs_file.parent.mkdir(parents=True)
    jobs_file.write_text(
        yaml.safe_dump(
            {
                "jobs": [
                    {
                        "name": "demo",
                        "description": "Demo",
                        "command": 'codex -c "print(123)"',
                        "schedule": "0 * * * *",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    backend = Path(sys.executable).resolve()
    schedule = ManagedSchedule(**{**_managed_schedule(tmp_path).__dict__, "jobs_file": jobs_file, "log_root": log_root, "config_root": jobs_file.parent, "state_root": log_root.parent, "descriptor_path": jobs_file.parent / "schedule-descriptor.json", "owner_id": str(jobs_file.parent), "backend_executables": {"claude": backend, "codex": backend}})

    assert not missing_skill_source.exists()
    assert executor.run_job(schedule=schedule, job_name="demo") == 0
    assert yaml.safe_load((log_root / "demo" / "latest.json").read_text())["success"] is True


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_executor_launches_exact_descriptor_backend_as_process_origin(tmp_path, monkeypatch, backend):
    schedule = _managed_schedule(tmp_path)
    resources = tmp_path / "resources"
    (resources / "agents").mkdir(parents=True)
    (resources / "agents" / "background_run.md").write_text("---\ndescription: scheduled\n---\nRun the skill.", encoding="utf-8")
    exact = tmp_path / "trusted" / backend
    exact.parent.mkdir()
    exact.write_text("trusted", encoding="utf-8")
    backends = dict(schedule.backend_executables)
    backends[backend] = exact
    schedule = ManagedSchedule(**{**schedule.__dict__, "backend_executables": backends, "plugin_root": resources})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "command": "invoke-skill demo", "backend": backend, "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    observed = []
    monkeypatch.setattr(executor.subprocess, "run", lambda argv, **kwargs: observed.append(argv) or subprocess.CompletedProcess(argv, 0))

    assert executor.run_job(schedule=schedule, job_name="demo") == 0
    assert observed[0][0] == str(exact)


def test_control_and_healthcheck_implementations_do_not_depend_on_skill_source(
    tmp_path, monkeypatch, capsys
):
    schedule = _managed_schedule(tmp_path)
    monkeypatch.setattr(control, "status", lambda selected: "managed status")
    monkeypatch.setattr(healthcheck, "_manager_failure", lambda: None)
    monkeypatch.setattr(healthcheck, "load_jobs", lambda selected: [])

    assert control.run_operation(schedule, operation="status", name=None, lines=50) == 0
    assert healthcheck.check(schedule) == []
    assert "managed status" in capsys.readouterr().out
    for module in (control, executor, healthcheck):
        assert "skills/recurring-tasks" not in Path(module.__file__).as_posix()


def test_managed_healthcheck_writes_aggregated_failure_summary_and_manual_log(tmp_path, monkeypatch, capsys):
    schedule = _managed_schedule(tmp_path)
    monkeypatch.setattr(healthcheck, "check", lambda selected: ["manager unavailable", "demo: last run failed"])

    assert healthcheck.run(schedule, cron=False) == 1

    health_root = schedule.log_root / "healthcheck"
    summary = (health_root / "last-failure.txt").read_text(encoding="utf-8")
    report = (health_root / "run.log").read_text(encoding="utf-8")
    assert "manager unavailable" in summary and "demo: last run failed" in summary
    assert "FAIL: 2 problem(s) found" in report
    assert "FAIL: manager unavailable" in capsys.readouterr().out


def test_managed_healthcheck_clears_stale_summary_on_success(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    summary = schedule.log_root / "healthcheck" / "last-failure.txt"
    summary.parent.mkdir(parents=True)
    summary.write_text("stale failure", encoding="utf-8")
    monkeypatch.setattr(healthcheck, "check", lambda selected: [])

    assert healthcheck.run(schedule, cron=False) == 0
    assert not summary.exists()
    assert "OK: recurring tasks healthy" in (summary.parent / "run.log").read_text(encoding="utf-8")


def test_managed_healthcheck_records_jobs_load_failure_and_cron_does_not_double_log(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    monkeypatch.setattr(healthcheck, "check", lambda selected: (_ for _ in ()).throw(ValueError("malformed jobs")))

    assert healthcheck.run(schedule, cron=True) == 1
    health_root = schedule.log_root / "healthcheck"
    assert "malformed jobs" in (health_root / "last-failure.txt").read_text(encoding="utf-8")
    assert not (health_root / "run.log").exists()


def test_managed_healthcheck_never_owns_desktop_notification(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    monkeypatch.setattr(healthcheck, "check", lambda selected: ["failure"])
    notify = mock.Mock(side_effect=OSError("notification unavailable"))
    monkeypatch.setattr(healthcheck.subprocess, "run", notify)

    assert healthcheck.run(schedule, cron=False) == 1
    notify.assert_not_called()


def test_managed_enable_disable_edit_only_canonical_jobs_and_sync(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "schedule": "0 * * * *", "command": "codex", "enabled": False}]}), encoding="utf-8")
    synced = []
    monkeypatch.setattr(control, "load_managed_schedule", lambda **_kwargs: schedule)
    monkeypatch.setattr(control, "sync", lambda selected: synced.append(selected))

    assert control.run_operation(schedule, operation="enable", name="demo", lines=50) == 0
    assert yaml.safe_load(schedule.jobs_file.read_text())["jobs"][0]["enabled"] is True
    assert control.run_operation(schedule, operation="disable", name="demo", lines=50) == 0
    assert yaml.safe_load(schedule.jobs_file.read_text())["jobs"][0]["enabled"] is False
    assert synced == [schedule, schedule]


def test_linux_sync_disables_and_removes_stale_context_units(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "old", "command": "invoke-skill old", "schedule": "0 * * * *", "enabled": False}]}), encoding="utf-8")
    schedule.native_registration_root.mkdir()
    service, timer = native.linux_names("old")
    (schedule.native_registration_root / service).write_text("stale", encoding="utf-8")
    (schedule.native_registration_root / timer).write_text("stale", encoding="utf-8")
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0))

    native.sync(schedule)

    assert ["systemctl", "--user", "disable", "--now", timer] in calls
    assert not (schedule.native_registration_root / service).exists()
    assert not (schedule.native_registration_root / timer).exists()


def test_linux_sync_from_another_installation_replaces_the_shared_set(tmp_path, monkeypatch):
    shared = tmp_path / "native"
    first = ManagedSchedule(**{**_managed_schedule(tmp_path / "first").__dict__, "native_registration_root": shared})
    second = ManagedSchedule(**{**_managed_schedule(tmp_path / "second").__dict__, "native_registration_root": shared})
    for schedule, name in ((first, "old"), (second, "new")):
        schedule.descriptor_path.write_text(json.dumps(runtime._payload(schedule)), encoding="utf-8")
        schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": name, "command": f"invoke-skill {name}", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")
    monkeypatch.setattr(native, "_write_crontab", lambda _value: None)
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0))

    native.sync(first)
    native.sync(second)

    assert sorted(path.name for path in shared.glob("ai-*.*")) == [
        "ai-new.service", "ai-new.timer", "ai-recurring-healthcheck.sh"
    ]
    assert str(second.descriptor_path) in (shared / "ai-new.service").read_text()
    assert str(second.descriptor_path) in (shared / "ai-recurring-healthcheck.sh").read_text()
    assert json.loads((shared / "install-owner.json").read_text())["owner_id"] == second.owner_id

    native.remove_context(first)
    assert (shared / "ai-new.service").exists()
    assert json.loads((shared / "install-owner.json").read_text())["owner_id"] == second.owner_id

    monkeypatch.setattr(native, "_systemd_unit_inventory", lambda *_args: native._NativeInventory(True, ()))
    monkeypatch.setattr(native, "_systemd_unit_state", lambda *_args: (False, ""))
    native.remove_context(second)
    assert not (shared / "ai-new.service").exists()
    assert not (shared / "install-owner.json").exists()


def test_failed_reconciliation_restores_previous_owner_and_complete_set(tmp_path, monkeypatch):
    shared = tmp_path / "native"
    first = ManagedSchedule(**{**_managed_schedule(tmp_path / "first").__dict__, "native_registration_root": shared})
    second = ManagedSchedule(**{**_managed_schedule(tmp_path / "second").__dict__, "native_registration_root": shared})
    for schedule, name in ((first, "old"), (second, "new")):
        schedule.descriptor_path.write_text(json.dumps(runtime._payload(schedule)), encoding="utf-8")
        schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": name, "command": f"invoke-skill {name}", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")
    monkeypatch.setattr(native, "_write_crontab", lambda _value: None)
    calls = 0

    def run(argv, **kwargs):
        nonlocal calls
        pending = second.state_root / "registrations.pending.json"
        if pending.exists() and second.owner_id in pending.read_text(encoding="utf-8") and argv[-1] == "ai-new.timer" and calls == 0:
            calls += 1
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native.subprocess, "run", run)
    native.sync(first)

    with pytest.raises(subprocess.CalledProcessError):
        native.sync(second)

    assert sorted(path.name for path in shared.glob("ai-*.*")) == [
        "ai-old.service", "ai-old.timer", "ai-recurring-healthcheck.sh"
    ]
    assert json.loads((shared / "install-owner.json").read_text())["owner_id"] == first.owner_id


def test_failed_same_owner_setup_keeps_previous_descriptor_plugin_and_native_set(tmp_path, monkeypatch):
    first = _managed_schedule(tmp_path)
    old_plugin = tmp_path / "plugin one"
    new_plugin = tmp_path / "plugin two"
    old_plugin.mkdir()
    new_plugin.mkdir()
    first = ManagedSchedule(**{**first.__dict__, "plugin_root": old_plugin})
    second = ManagedSchedule(**{**first.__dict__, "plugin_root": new_plugin})
    first.descriptor_path.write_text(json.dumps(runtime._payload(first)), encoding="utf-8")
    first.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "same", "command": "invoke-skill same", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_read_crontab", lambda: "")
    monkeypatch.setattr(native, "_write_crontab", lambda _value: None)
    failed = False

    def run(argv, **kwargs):
        nonlocal failed
        if not failed and argv[-1] == "daemon-reload" and new_plugin.as_posix() in (first.native_registration_root / "ai-same.service").read_text():
            failed = True
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native.subprocess, "run", run)
    native.sync(first)
    monkeypatch.setattr(managed_control, "build_managed_schedule", lambda **_kwargs: second)
    writer = mock.Mock()
    monkeypatch.setattr(managed_control, "write_managed_schedule", writer)

    with pytest.raises(subprocess.CalledProcessError):
        managed_control.run("setup", python=second.python, plugin_root=new_plugin)

    assert json.loads(first.descriptor_path.read_text())["plugin_root"] == str(old_plugin)
    assert old_plugin.as_posix() in (first.native_registration_root / "ai-same.service").read_text()
    assert old_plugin.as_posix() in (first.native_registration_root / "ai-recurring-healthcheck.sh").read_text()
    assert json.loads((first.native_registration_root / "install-owner.json").read_text())["owner_id"] == first.owner_id
    writer.assert_not_called()


def test_scheduler_names_are_one_shared_namespace():
    assert native.linux_names("demo") == ("ai-demo.service", "ai-demo.timer")
    assert native.launchd_label("demo") == "com.famulus.ai.demo"
    assert native.windows_task_name("demo") == "Famulus-AI-ai-demo"


def test_windows_native_root_is_shared_across_installations(tmp_path, monkeypatch):
    assert runtime._roots({"USERPROFILE": str(tmp_path), "LOCALAPPDATA": str(tmp_path)}, "win32")[2] == tmp_path / "Famulus" / "recurring-tasks" / "native"


def test_macos_step_schedule_expands_and_sync_reloads_exact_label(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "quarter", "command": "invoke-skill quarter", "schedule": "*/15 9 * * *", "enabled": True}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="loaded"))

    payload = __import__("plistlib").loads(native.render_macos_plist(schedule, {"name": "quarter", "command": "invoke-skill quarter", "schedule": "*/15 9 * * *", "enabled": True}))
    native.sync(schedule)

    assert [entry["Minute"] for entry in payload["StartCalendarInterval"]] == [0, 15, 30, 45]
    target = f"gui/{os.getuid()}/{native.launchd_label('quarter')}"
    assert ["launchctl", "print", target] in calls
    assert ["launchctl", "bootout", target] in calls
    assert any(call[:2] == ["launchctl", "bootstrap"] for call in calls)


def test_windows_sync_preserves_cron_semantics(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "quarter", "command": "invoke-skill quarter", "schedule": "*/15 * * * *", "enabled": True}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout=""))

    native.sync(schedule)

    create = next(call for call in calls if call[:2] == ["schtasks", "/Create"])
    assert create[-4:] == ["/SC", "MINUTE", "/MO", "15"]


def test_native_renderers_preserve_only_exact_bounded_environment(tmp_path):
    schedule = _managed_schedule(tmp_path)
    bounded = {
        "HOME": str(tmp_path / "home with spaces"),
        "PATH": str(tmp_path / "bin 雪"),
        "CODEX_HOME": str(tmp_path / 'codex "quoted" % !'),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude % ! 雪"),
        "FAMULUS_ACTIVE_RELEASE": "release % ! 雪",
    }
    schedule = ManagedSchedule(**{**schedule.__dict__, "environment": bounded})
    job = {"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True}

    linux = native.render_linux_service(schedule, job)
    plist = __import__("plistlib").loads(native.render_macos_plist(schedule, job))
    wrapper = native.render_windows_wrapper(schedule, job)

    assert set(plist["EnvironmentVariables"]) == set(bounded)
    assert plist["EnvironmentVariables"] == bounded
    assert all(f"{name}=" in linux for name in bounded)
    assert "SECRET_CANARY" not in linux + wrapper + repr(plist)
    assert 'set "CODEX_HOME=' in wrapper
    assert "%%" in wrapper
    assert "雪" in linux + wrapper


@pytest.mark.parametrize("renderer", [native.render_linux_service, native.render_macos_plist, native.render_windows_wrapper])
def test_native_renderers_reject_crlf_environment(tmp_path, renderer):
    schedule = _managed_schedule(tmp_path)
    schedule = ManagedSchedule(**{**schedule.__dict__, "environment": {"HOME": str(tmp_path), "PATH": "bad\nvalue"}})
    with pytest.raises(ValueError, match="CR or LF"):
        renderer(schedule, {"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True})


def test_managed_identities_have_no_context_parameter():
    assert native.launchd_label("same-job") == "com.famulus.ai.same-job"
    assert native.windows_task_name("same-job") == "Famulus-AI-ai-same-job"
    assert native.windows_wrapper_name("same-job") == "Famulus-AI-ai-same-job.cmd"


def _retired_windows_sync_migrates_only_this_contexts_old_managed_identity(tmp_path, monkeypatch):
    installation = "dev-0123456789abcdef0123456789abcdef"
    schedule = _managed_schedule(tmp_path)
    schedule = ManagedSchedule(**{**schedule.__dict__, "installation_id": installation, "bootstrap_python": Path(sys.executable)})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    schedule.native_registration_root.mkdir()
    old_wrapper = schedule.native_registration_root / f"ai-{installation}-demo.cmd"
    old_wrapper.write_text("old", encoding="utf-8")
    old_task = f"Famulus-AI-ai-{installation}-demo"
    unrelated = "Famulus-AI-dev-ffffffffffffffffffffffffffffffff-ai-demo"
    calls = []
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_windows_existing_tasks", lambda: [old_task, unrelated])
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout=""))

    native.sync(schedule)

    assert ["schtasks", "/Delete", "/TN", old_task, "/F"] in calls
    assert not any(unrelated in call for call in calls)
    assert not old_wrapper.exists()
    assert any(native.windows_task_name("demo", installation) in call for call in calls if call[:2] == ["schtasks", "/Create"])


def _retired_macos_sync_boots_out_old_managed_label_before_task5c_label(tmp_path, monkeypatch):
    installation = "dev-0123456789abcdef0123456789abcdef"
    schedule = _managed_schedule(tmp_path)
    schedule = ManagedSchedule(**{**schedule.__dict__, "installation_id": installation})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="loaded"))

    native.sync(schedule)

    target = f"gui/{os.getuid()}"
    old = f"{target}/com.famulus.ai.{installation}-demo"
    current = f"{target}/com.famulus.ai.{installation}.demo"
    assert ["launchctl", "bootout", old] in calls
    assert ["launchctl", "bootout", current] in calls
    assert not any("ffffffff" in " ".join(call) for call in calls)


def test_linux_systemctl_calls_receive_only_derived_session_baseline(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    schedule = ManagedSchedule(**{**schedule.__dict__, "environment": {**schedule.environment, "SECRET_CANARY": "must-not-pass"}})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text(yaml.safe_dump({"jobs": [{"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(native.sys, "platform", "linux")

    def observed(argv, **kwargs):
        calls.append((argv, kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 0, stdout="running", stderr="")

    monkeypatch.setattr(native.subprocess, "run", observed)
    native.sync(schedule)
    native.status(schedule)
    native.trigger(schedule, "demo")

    expected = {
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
    }
    systemctl = [(argv, env) for argv, env in calls if argv[0] == "systemctl"]
    assert systemctl
    assert all(env == expected for _argv, env in systemctl)
    assert all("SECRET_CANARY" not in env for _argv, env in systemctl)


def test_managed_healthcheck_systemctl_uses_derived_session_baseline(tmp_path, monkeypatch):
    schedule = _managed_schedule(tmp_path)
    calls = []
    monkeypatch.setattr(healthcheck.sys, "platform", "linux")
    monkeypatch.setattr(healthcheck, "load_jobs", lambda selected: [])
    monkeypatch.setattr(healthcheck.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs.get("env"))) or subprocess.CompletedProcess(argv, 0, stdout="running"))

    assert healthcheck.check(schedule) == []
    assert calls == [
        (["systemctl", "--user", "is-system-running"], {
            "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
        })
    ]


def _retired_windows_sync_sweeps_removed_legacy_jobs_without_cross_context_delete(tmp_path, monkeypatch):
    installation = "dev-0123456789abcdef0123456789abcdef"
    schedule = _managed_schedule(tmp_path)
    schedule = ManagedSchedule(**{**schedule.__dict__, "installation_id": installation, "bootstrap_python": Path(sys.executable)})
    schedule.jobs_file.parent.mkdir(parents=True, exist_ok=True)
    schedule.jobs_file.write_text("jobs: []\n", encoding="utf-8")
    schedule.native_registration_root.mkdir()
    orphan_wrapper = schedule.native_registration_root / f"ai-{installation}-removed.cmd"
    orphan_wrapper.write_text("old", encoding="utf-8")
    orphan_task = f"Famulus-AI-ai-{installation}-removed"
    other_task = "Famulus-AI-ai-dev-ffffffffffffffffffffffffffffffff-removed"
    calls = []
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_windows_existing_tasks", lambda: [orphan_task, other_task])
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout=""))

    native.sync(schedule)

    assert ["schtasks", "/Delete", "/TN", orphan_task, "/F"] in calls
    assert not any(other_task in call for call in calls)
    assert not orphan_wrapper.exists()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_status_queries_only_the_selected_context_namespace(tmp_path, monkeypatch, platform):
    schedule = _managed_schedule(tmp_path)
    schedule.native_registration_root.mkdir()
    if platform == "darwin":
        (schedule.native_registration_root / "ai-demo.plist").write_bytes(native.render_macos_plist(schedule, {"name": "demo", "command": "invoke-skill demo", "schedule": "0 * * * *", "enabled": True}))
    calls = []
    monkeypatch.setattr(native.sys, "platform", platform)
    monkeypatch.setattr(native.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="ok", stderr=""))

    native.status(schedule)

    if platform == "darwin":
        assert calls == [["launchctl", "print", f"gui/{os.getuid()}/{native.launchd_label('demo')}"]]
    else:
        assert calls == [["schtasks", "/Query", "/FO", "CSV", "/NH"]]
