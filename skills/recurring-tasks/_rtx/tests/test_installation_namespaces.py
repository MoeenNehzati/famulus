from __future__ import annotations

import json
from pathlib import Path

import pytest

from .. import _install_owner, _setup_runner
from .._schedule_backend._base_backend import ScheduleContext, registration_token
from .._schedule_backend._linux_backend import (
    LinuxScheduleBackend,
    service_name,
    timer_name,
)
from .._schedule_backend._osx_backend import launchd_label, plist_name
from .._schedule_backend._windows_backend import (
    WindowsScheduleBackend,
    task_name,
    wrapper_name,
)


DEV_A = "dev-0123456789abcdef0123456789abcdef"
DEV_B = "dev-fedcba9876543210fedcba9876543210"


def test_native_registration_names_preserve_standard_and_namespace_development():
    assert registration_token("standard") == ""
    assert service_name("same-job", "standard") == "ai-same-job.service"
    assert timer_name("same-job", "standard") == "ai-same-job.timer"
    assert launchd_label("same-job", "standard") == "com.famulus.ai.same-job"
    assert plist_name("same-job", "standard") == "ai-same-job.plist"
    assert task_name("same-job", "standard") == "Famulus-AI-ai-same-job"

    assert service_name("same-job", DEV_A) == f"ai-{DEV_A}-same-job.service"
    assert timer_name("same-job", DEV_A) == f"ai-{DEV_A}-same-job.timer"
    assert launchd_label("same-job", DEV_A) == f"com.famulus.ai.{DEV_A}.same-job"
    assert plist_name("same-job", DEV_A) == f"ai-{DEV_A}-same-job.plist"
    assert task_name("same-job", DEV_A) == f"Famulus-AI-{DEV_A}-ai-same-job"
    assert wrapper_name("same-job", DEV_A) == f"Famulus-AI-{DEV_A}-ai-same-job.cmd"


def test_owner_records_are_namespaced_and_legacy_standard_is_migrated(tmp_path):
    legacy = tmp_path / "install-owner.json"
    legacy.write_text(
        json.dumps({"schema_version": 1, "owner": "/old/source"}),
        encoding="utf-8",
    )

    standard = _install_owner.read_owner_record(tmp_path, "standard")
    assert standard is not None
    assert standard.installation_id == "standard"
    assert standard.source_path == Path("/old/source")
    assert legacy.exists()
    assert json.loads(legacy.read_text(encoding="utf-8"))["schema_version"] == 2

    _install_owner.write_owner(
        unit_dir=tmp_path,
        installation_id=DEV_A,
        owner=Path("/source/a"),
    )
    _install_owner.write_owner(
        unit_dir=tmp_path,
        installation_id=DEV_B,
        owner=Path("/source/b"),
    )
    assert _install_owner.read_owner(tmp_path, DEV_A) == Path("/source/a")
    assert _install_owner.read_owner(tmp_path, DEV_B) == Path("/source/b")
    assert _install_owner.record_path(tmp_path, DEV_A).name == f"install-owner-{DEV_A}.json"


def test_owner_record_rejects_cross_installation_payload(tmp_path):
    path = _install_owner.record_path(tmp_path, DEV_A)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "installation_id": DEV_B,
                "source_path": "/source/b",
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    assert _install_owner.read_owner_record(tmp_path, DEV_A) is None


def test_interrupted_legacy_owner_migration_preserves_source_and_retries(
    monkeypatch, tmp_path
):
    legacy = _install_owner.record_path(tmp_path, "standard")
    original = json.dumps({"schema_version": 1, "owner": "/old/source"})
    legacy.write_text(original, encoding="utf-8")
    real_replace = _install_owner.os.replace
    monkeypatch.setattr(
        _install_owner.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("interrupted")),
    )

    with pytest.raises(OSError, match="interrupted"):
        _install_owner.read_owner_record(tmp_path, "standard")
    assert legacy.read_text(encoding="utf-8") == original

    monkeypatch.setattr(_install_owner.os, "replace", real_replace)
    migrated = _install_owner.read_owner_record(tmp_path, "standard")
    assert migrated is not None and migrated.source_path == Path("/old/source")
    assert json.loads(legacy.read_text(encoding="utf-8"))["schema_version"] == 2


def test_linux_sync_scans_and_removes_only_its_installation(monkeypatch, tmp_path):
    from .._schedule_backend import _linux_backend

    monkeypatch.setattr(_linux_backend, "_systemctl", lambda *args, **kwargs: None)
    other_timer = tmp_path / timer_name("same-job", DEV_B)
    other_service = tmp_path / service_name("same-job", DEV_B)
    other_timer.write_text("other timer", encoding="utf-8")
    other_service.write_text("other service", encoding="utf-8")
    standard_timer = tmp_path / timer_name("same-job", "standard")
    standard_service = tmp_path / service_name("same-job", "standard")
    standard_timer.write_text("standard timer", encoding="utf-8")
    standard_service.write_text("standard service", encoding="utf-8")
    own_timer = tmp_path / timer_name("removed", DEV_A)
    own_service = tmp_path / service_name("removed", DEV_A)
    own_timer.write_text("own timer", encoding="utf-8")
    own_service.write_text("own service", encoding="utf-8")

    LinuxScheduleBackend().sync([], _context(tmp_path, DEV_A))

    assert other_timer.read_text(encoding="utf-8") == "other timer"
    assert other_service.read_text(encoding="utf-8") == "other service"
    assert standard_timer.read_text(encoding="utf-8") == "standard timer"
    assert standard_service.read_text(encoding="utf-8") == "standard service"
    assert not own_timer.exists()
    assert not own_service.exists()


def test_standard_sync_does_not_claim_development_registration_names(monkeypatch, tmp_path):
    from .._schedule_backend import _linux_backend

    monkeypatch.setattr(_linux_backend, "_systemctl", lambda *args, **kwargs: None)
    development_timer = tmp_path / timer_name("same-job", DEV_A)
    development_service = tmp_path / service_name("same-job", DEV_A)
    development_timer.write_text("development timer", encoding="utf-8")
    development_service.write_text("development service", encoding="utf-8")
    standard_timer = tmp_path / timer_name("removed", "standard")
    standard_service = tmp_path / service_name("removed", "standard")
    standard_timer.write_text("standard timer", encoding="utf-8")
    standard_service.write_text("standard service", encoding="utf-8")

    LinuxScheduleBackend().sync([], _context(tmp_path, "standard"))

    assert development_timer.read_text(encoding="utf-8") == "development timer"
    assert development_service.read_text(encoding="utf-8") == "development service"
    assert not standard_timer.exists()
    assert not standard_service.exists()


def test_linux_status_does_not_report_another_installation(monkeypatch, tmp_path):
    from .._schedule_backend import _linux_backend

    output = (
        "ai-standard-job.timer loaded active\n"
        f"ai-{DEV_A}-same-job.timer loaded active\n"
        f"ai-{DEV_B}-same-job.timer loaded active\n"
    )
    monkeypatch.setattr(
        _linux_backend,
        "_systemctl",
        lambda *args, **kwargs: type("Result", (), {"stdout": output})(),
    )

    standard = LinuxScheduleBackend().status(_context(tmp_path, "standard"))
    development = LinuxScheduleBackend().status(_context(tmp_path, DEV_A))

    assert "ai-standard-job.timer" in standard
    assert DEV_A not in standard and DEV_B not in standard
    assert f"ai-{DEV_A}-same-job.timer" in development
    assert "ai-standard-job.timer" not in development and DEV_B not in development


@pytest.mark.parametrize(
    ("installation_id", "marker"),
    [
        ("standard", "# ai-recurring-healthcheck"),
        (DEV_A, f"# ai-recurring-healthcheck:{DEV_A}"),
    ],
)
def test_linux_healthcheck_marker_is_namespaced(installation_id, marker):
    rendered = _setup_runner.render_healthcheck_cron(
        runtime_resolver=Path("/runtime/launch.py"),
        healthcheck=Path("/source/_healthcheck_probe.py"),
        log_file=Path("/state/logs/healthcheck/run.log"),
        uid=1000,
        installation_id=installation_id,
    )
    assert rendered.endswith(marker)


def test_cron_replacement_changes_only_the_selected_installation():
    standard = "0 1 * * * old-standard # ai-recurring-healthcheck"
    dev_a = f"0 2 * * * old-a # ai-recurring-healthcheck:{DEV_A}"
    dev_b = f"0 3 * * * old-b # ai-recurring-healthcheck:{DEV_B}"
    desired = f"0 */4 * * * new-a # ai-recurring-healthcheck:{DEV_A}"

    result = _setup_runner._replace_managed_cron_line(
        "\n".join((standard, dev_a, dev_b)) + "\n",
        desired,
        installation_id=DEV_A,
    )

    assert standard in result
    assert dev_b in result
    assert dev_a not in result
    assert result.count(desired) == 1


@pytest.mark.parametrize(
    ("platform", "independent", "detail"),
    [
        ("linux", True, "cron"),
        ("darwin", False, "on-demand"),
        ("win32", False, "on-demand"),
    ],
)
def test_healthcheck_capability_is_explicit(platform, independent, detail):
    capability = _setup_runner.healthcheck_capability(platform)
    assert capability.independent_scheduler is independent
    assert detail in capability.detail


def test_windows_inventory_partitions_standard_and_two_development_contexts(
    monkeypatch, tmp_path
):
    from .._schedule_backend import _windows_backend

    rows = (
        '"\\Famulus-AI-ai-standard-job","N/A","Ready"\n'
        f'"\\Famulus-AI-{DEV_A}-ai-old-a","N/A","Ready"\n'
        f'"\\Famulus-AI-{DEV_B}-ai-keep-b","N/A","Ready"\n'
        '"\\Foreign-Task","N/A","Ready"\n'
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:4] == ["schtasks", "/Query", "/FO", "CSV"]:
            return type("Result", (), {"returncode": 0, "stdout": rows, "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(_windows_backend.subprocess, "run", fake_run)
    backend = WindowsScheduleBackend()
    context_a = _context(tmp_path, DEV_A)
    context_b = _context(tmp_path, DEV_B)
    standard = _context(tmp_path, "standard")

    assert DEV_A in backend.status(context_a) and DEV_B not in backend.status(context_a)
    assert DEV_B in backend.status(context_b) and DEV_A not in backend.status(context_b)
    assert "standard-job" in backend.status(standard) and DEV_A not in backend.status(standard)
    assert backend.registrations_present(context_a)

    live_a = context_a.__class__(**{**context_a.__dict__, "live": True})
    backend.sync([], live_a)

    assert ["schtasks", "/Delete", "/TN", f"\\Famulus-AI-{DEV_A}-ai-old-a", "/F"] in calls
    assert not any(DEV_B in " ".join(call) and "/Delete" in call for call in calls)
    assert not any("standard-job" in " ".join(call) and "/Delete" in call for call in calls)


def _context(unit_dir: Path, installation_id: str) -> ScheduleContext:
    root = unit_dir.parent
    return ScheduleContext(
        skill_dir=root / "source" / "_rtx",
        jobs_file=root / installation_id / "jobs.yaml",
        log_dir=root / installation_id / "logs",
        unit_dir=unit_dir,
        live=False,
        runtime_resolver=root / installation_id / "launch.py",
        config_root=root / installation_id / "config",
        state_root=root / installation_id / "state",
        installation_id=installation_id,
    )
