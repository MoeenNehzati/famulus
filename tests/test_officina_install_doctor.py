from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import officina.install.doctor as doctor_module

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import InstallationContext
from officina.install.doctor import (
    DiagnosticReport,
    diagnose_installation,
    render_diagnostic_json,
    render_diagnostic_text,
)
from officina.recurring.runtime import write_managed_schedule


def _standard_context(tmp_path: Path) -> InstallationContext:
    source = Path(__file__).resolve().parents[1]
    return InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=resolve_famulus_paths(platform=sys.platform, home=tmp_path, environ={}),
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )


def _write_healthy_installation(context: InstallationContext) -> None:
    release = context.paths.releases_root / "release-a"
    python_bin = (
        release / "venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else release / "venv" / "bin" / "python"
    )
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("python\n", encoding="utf-8")
    resources = release / "launcher-resources"
    (resources / "agents").mkdir(parents=True)
    (resources / "profiles").mkdir()
    record = release / "installation-context.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_id": release.name,
                "mode": "standard",
                "installation_id": "standard",
                "source_root": str(context.source_root),
                "development_root": None,
                "codex_home": str(context.codex_home),
                "claude_home": str(context.claude_home),
            }
        ),
        encoding="utf-8",
    )
    context.paths.current_pointer.parent.mkdir(parents=True, exist_ok=True)
    context.paths.current_pointer.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "release_id": release.name,
                "runtime_source": str(release),
                "python_bin": str(python_bin),
                "repository_config": str(context.source_root / "officina.toml"),
                "launcher_resources": str(resources),
                "installation_context": str(record),
            }
        ),
        encoding="utf-8",
    )
    context.paths.config_root.mkdir(parents=True, exist_ok=True)
    (context.paths.config_root / "launchers.json").write_text(
        '{"schema_version": 1, "default_backend": "claude"}\n', encoding="utf-8"
    )
    context.paths.user_bin.mkdir(parents=True, exist_ok=True)
    for command in ("dispatcher", "invoke-skill", "llm-wakeup", "lw", "background_run"):
        path = context.paths.user_bin / (
            f"{command}.bat" if sys.platform == "win32" else command
        )
        path.write_text(
            "@echo off\r\n" if sys.platform == "win32" else "#!/bin/sh\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    context.paths.install_state_root.mkdir(parents=True, exist_ok=True)
    (context.paths.install_state_root / "install-manifest.json").write_text(
        json.dumps({
            "version": 2,
            "entries": [],
            "installation": {"mode": "standard", "installation_id": "standard"},
        }) + "\n",
        encoding="utf-8",
    )


def _context_environment(context: InstallationContext) -> dict[str, str]:
    environment = {
        "HOME": str(context.codex_home.parent),
        "PATH": str(context.paths.user_bin),
    }
    if sys.platform == "win32":
        environment.update(
            {
                "USERPROFILE": environment["HOME"],
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            }
        )
    return environment


def _diagnose(context: InstallationContext) -> DiagnosticReport:
    return diagnose_installation(
        context=context,
        environ=_context_environment(context),
        platform=sys.platform,
    )


def _write_recurring_descriptor(context: InstallationContext) -> None:
    resolver = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    resolver.parent.mkdir(parents=True, exist_ok=True)
    resolver.write_text("# managed resolver\n", encoding="utf-8")
    for backend in ("claude", "codex"):
        path = context.paths.user_bin / backend
        if sys.platform == "win32":
            shutil.copy2(sys.executable, path.with_suffix(".exe"))
        else:
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
    write_managed_schedule(
        runtime_root=context.paths.runtime_root,
        environ=_context_environment(context),
    )


def test_doctor_reports_healthy_installation_in_human_and_schema_json(tmp_path: Path) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)

    report = _diagnose(context)

    assert report.status == "healthy"
    assert "healthy" in render_diagnostic_text(report).lower()
    payload = json.loads(render_diagnostic_json(report))
    assert payload["schema_version"] == 1


def test_doctor_rejects_schema2_manifest_bound_to_another_context(tmp_path: Path) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    manifest = context.paths.install_state_root / "install-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["installation"] = {
        "mode": "development",
        "installation_id": "dev-" + "a" * 32,
        "development_root": str(tmp_path / "other"),
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    check = next(item for item in _diagnose(context).checks if item.id == "manifest")

    assert check.status == "error"
    assert "selected installation context" in check.summary


def test_valid_recurring_descriptor_and_registrations_are_an_informational_healthy_summary(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    context.paths.recurring_state_root.mkdir(parents=True)
    (context.paths.recurring_state_root / "registrations.json").write_text(
        '{"schema_version": 1, "installation_id": "standard", '
        '"registrations": ["daily", "triage"]}\n',
        encoding="utf-8",
    )

    report = _diagnose(context)

    recurring = next(check for check in report.checks if check.id == "recurring")
    assert report.status == "healthy"
    assert recurring.status == "ok"
    assert recurring.summary == "Recurring descriptor: schema 1, backend claude; registrations: 2"
    assert recurring.recovery == ""


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("runtime_resolver", "/tmp/foreign-resolver.py"),
        ("bootstrap_python", "/tmp/foreign-python"),
        ("backend_executables", {"claude": "/tmp/foreign-claude", "codex": "/tmp/foreign-codex"}),
        ("native_registration_root", "/tmp/foreign-native-root"),
        ("environment", {"HOME": "/tmp/redirected", "INJECTED": "1"}),
    ],
)
def test_doctor_rejects_every_mutable_recurring_authority_field_with_teardown_recovery(
    tmp_path: Path, field: str, replacement: object
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    descriptor = context.paths.recurring_config_root / "schedule-descriptor.json"
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload[field] = replacement
    descriptor.write_text(json.dumps(payload), encoding="utf-8")

    recurring = next(check for check in _diagnose(context).checks if check.id == "recurring")

    assert recurring.status == "error"
    assert recurring.recovery == (
        "dispatcher --caller-skill recurring-tasks "
        "recurring-tasks._rtx.interface.scripts-remove-context"
    )


def test_doctor_names_missing_recurring_backend_and_uses_install_recovery(tmp_path: Path) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    (context.paths.user_bin / "claude").unlink()

    recurring = next(check for check in _diagnose(context).checks if check.id == "recurring")

    assert recurring.status == "error"
    assert "claude" in recurring.summary
    assert recurring.recovery == (
        "dispatcher --caller-skill install-assistant-tools "
        "install-assistant-tools._rtx.interface.scripts-install "
        "--no-dev-mode --non-interactive --yes"
    )


def test_doctor_returns_apply_recovery_when_launcher_authority_is_malformed(tmp_path: Path) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    (context.paths.config_root / "launchers.json").write_text("{", encoding="utf-8")

    report = _diagnose(context)
    recurring = next(check for check in report.checks if check.id == "recurring")

    assert report.status == "unhealthy"
    assert "launcher" in recurring.summary.lower()
    assert recurring.recovery == (
        "dispatcher --caller-skill install-assistant-tools "
        "install-assistant-tools._rtx.interface.scripts-install "
        "--no-dev-mode --non-interactive --yes"
    )


def test_doctor_validates_reconstruction_before_missing_descriptor_teardown_recovery(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    (context.paths.recurring_config_root / "schedule-descriptor.json").unlink()
    (context.paths.user_bin / "claude").unlink()
    context.paths.recurring_state_root.mkdir(parents=True)
    (context.paths.recurring_state_root / "registrations.json").write_text(
        '{"schema_version": 1, "installation_id": "standard", "registrations": ["daily"]}\n',
        encoding="utf-8",
    )

    recurring = next(check for check in _diagnose(context).checks if check.id == "recurring")

    assert recurring.status == "error"
    assert "claude" in recurring.summary
    assert recurring.recovery == (
        "dispatcher --caller-skill install-assistant-tools "
        "install-assistant-tools._rtx.interface.scripts-install "
        "--no-dev-mode --non-interactive --yes"
    )


@pytest.mark.parametrize("artifact", ["descriptor", "registrations"])
def test_doctor_rejects_recurring_artifacts_for_another_context_with_exact_teardown_recovery(
    tmp_path: Path, artifact: str
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    _write_recurring_descriptor(context)
    descriptor = context.paths.recurring_config_root / "schedule-descriptor.json"
    descriptor_payload = json.loads(descriptor.read_text(encoding="utf-8"))
    descriptor_payload["installation_id"] = "dev-" + "f" * 32
    descriptor.write_text(json.dumps(descriptor_payload), encoding="utf-8")
    context.paths.recurring_state_root.mkdir(parents=True)
    summary = context.paths.recurring_state_root / "registrations.json"
    summary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "installation_id": "dev-" + "f" * 32,
                "registrations": ["daily"],
            }
        ),
        encoding="utf-8",
    )
    if artifact == "descriptor":
        summary.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "installation_id": context.installation_id,
                    "registrations": ["daily"],
                }
            ),
            encoding="utf-8",
        )
    else:
        descriptor_payload["installation_id"] = context.installation_id
        descriptor.write_text(json.dumps(descriptor_payload), encoding="utf-8")

    report = _diagnose(context)
    recurring = next(check for check in report.checks if check.id == "recurring")

    assert report.status == "unhealthy"
    assert recurring.status == "error"
    assert recurring.recovery == (
        "dispatcher --caller-skill recurring-tasks "
        "recurring-tasks._rtx.interface.scripts-remove-context"
    )


def test_doctor_accepts_external_interpreter_symlink_from_active_resolver_trust(
    tmp_path: Path,
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    pointer = json.loads(context.paths.current_pointer.read_text(encoding="utf-8"))
    python_bin = Path(pointer["python_bin"])
    external_root = tmp_path / "uv-python-store"
    external_python = external_root / "cpython-3.13" / "bin" / "python"
    external_python.parent.mkdir(parents=True)
    external_python.write_text("python\n", encoding="utf-8")
    python_bin.unlink()
    try:
        python_bin.symlink_to(external_python)
    except OSError:
        # famulus-skip: category=capability-unavailable; reason=trusted interpreter coverage requires a real filesystem symlink; alternate=runtime-pointer unit tests cover trusted-root containment directly
        pytest.skip("filesystem symlinks are unavailable")
    generation = "a" * 64
    fixed = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1"
    selected = context.paths.runtime_root / "bootstrap" / "resolvers" / "generations" / generation
    fixed.mkdir(parents=True)
    selected.mkdir(parents=True)
    (selected / "launch.py").write_text("# complete resolver\n", encoding="utf-8")
    (fixed / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}), encoding="utf-8"
    )
    (selected / "trusted-roots.json").write_text(
        json.dumps([str(external_root)]), encoding="utf-8"
    )

    report = _diagnose(context)

    assert report.status == "healthy"
    assert next(check for check in report.checks if check.id == "pointer").status == "ok"


def test_recovery_commands_use_registered_routes_and_real_installer_flags(
    tmp_path: Path,
) -> None:
    standard = _standard_context(tmp_path / "standard")
    development_root = tmp_path / "development"
    development_root.mkdir()
    development = InstallationContext(
        mode="development",
        source_root=development_root,
        development_root=development_root,
        paths=resolve_famulus_paths(
            platform="linux", home=development_root / ".famulus" / "home", environ={}
        ),
        codex_home=development_root / ".famulus" / "homes" / "codex",
        claude_home=development_root / ".famulus" / "homes" / "claude",
        installation_id="dev-" + "a" * 32,
    )

    standard_pointer = next(
        check for check in _diagnose(standard).checks if check.id == "pointer"
    )
    development_pointer = next(
        check for check in _diagnose(development).checks if check.id == "pointer"
    )

    assert standard_pointer.recovery == (
        "dispatcher --caller-skill install-assistant-tools "
        "install-assistant-tools._rtx.interface.scripts-install "
        "--no-dev-mode --non-interactive --yes"
    )
    assert development_pointer.recovery == (
        "dispatcher --caller-skill install-assistant-tools "
        "install-assistant-tools._rtx.interface.scripts-install "
        f"--dev-mode --repo-path {development_root} --non-interactive --yes"
    )


def test_doctor_accepts_windows_batch_command_origins(tmp_path: Path, monkeypatch) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    for command in ("dispatcher", "invoke-skill", "llm-wakeup", "lw", "background_run"):
        (context.paths.user_bin / command).rename(context.paths.user_bin / f"{command}.bat")
    monkeypatch.setattr(
        doctor_module.shutil,
        "which",
        lambda command, path: str(context.paths.user_bin / f"{command}.bat"),
    )

    report = diagnose_installation(
        context=context,
        environ={"PATH": str(context.paths.user_bin)},
        platform="win32",
    )

    assert next(check for check in report.checks if check.id == "commands").status == "ok"


@pytest.mark.parametrize(
    ("mutation", "expected_check", "recovery_fragment"),
    [
        ("absent", "pointer", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("malformed", "pointer", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("mixed-release", "pointer", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("context-mismatch", "pointer", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("resolver-selector", "pointer", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("missing-source", "source", "restore source"),
        ("stale-command", "commands", "scripts-install --no-dev-mode --non-interactive --yes"),
        ("manifest", "manifest", "scripts-install --no-dev-mode --non-interactive --yes"),
        (
            "recurring-malformed",
            "recurring",
            "dispatcher --caller-skill recurring-tasks "
            "recurring-tasks._rtx.interface.scripts-remove-context",
        ),
    ],
)
def test_doctor_classifies_unhealthy_states_with_safe_recovery(
    tmp_path: Path,
    mutation: str,
    expected_check: str,
    recovery_fragment: str,
) -> None:
    context = _standard_context(tmp_path)
    _write_healthy_installation(context)
    if mutation == "absent":
        context.paths.current_pointer.unlink()
    elif mutation == "malformed":
        context.paths.current_pointer.write_text("{", encoding="utf-8")
    elif mutation == "mixed-release":
        payload = json.loads(context.paths.current_pointer.read_text(encoding="utf-8"))
        payload["release_id"] = "release-b"
        context.paths.current_pointer.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "context-mismatch":
        context = InstallationContext(
            **{**context.__dict__, "codex_home": tmp_path / "different-codex-home"}
        )
    elif mutation == "resolver-selector":
        fixed = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1"
        fixed.mkdir(parents=True)
        (fixed / "active.json").write_text(
            '{"schema_version": 2, "generation": "' + "a" * 64 + '"}',
            encoding="utf-8",
        )
    elif mutation == "missing-source":
        context = InstallationContext(
            **{**context.__dict__, "source_root": tmp_path / "missing-source"}
        )
    elif mutation == "stale-command":
        (context.paths.user_bin / "dispatcher").unlink()
    elif mutation == "manifest":
        (context.paths.install_state_root / "install-manifest.json").write_text("[]", encoding="utf-8")
    elif mutation == "recurring-malformed":
        _write_recurring_descriptor(context)
        context.paths.recurring_state_root.mkdir(parents=True)
        (context.paths.recurring_state_root / "registrations.json").write_text(
            "{", encoding="utf-8"
        )

    report = _diagnose(context)

    assert report.status == "unhealthy"
    check = next(check for check in report.checks if check.id == expected_check)
    assert check.status == "error"
    assert recovery_fragment in check.recovery
