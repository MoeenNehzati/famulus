from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install import context as install_context
from officina.install import runtime_pointer
from officina.install.context import InstallationContext
from officina.install.runtime_pointer import RuntimePointer
from officina.launchers.agent import LauncherConfiguration
from officina.recurring.runtime import ManagedSchedule

if __package__ and __package__.count(".") >= 1:
    from .. import _schedule_context as schedule_context
else:
    import _schedule_context as schedule_context


def _authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, platform: str = "linux"
):
    home = tmp_path / "home with 'quotes' % ! 雪"
    home.mkdir()
    selector_environ = {}
    if platform == "win32":
        selector_environ = {
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "APPDATA": str(home / "AppData" / "Roaming"),
        }
    paths = resolve_famulus_paths(
        platform=platform, home=home, environ=selector_environ
    )
    runtime_source = paths.runtime_root / "releases" / "release-a"
    python_bin = runtime_source / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    resolver = paths.runtime_root / "bootstrap" / "resolvers" / "v1" / "launch.py"
    resolver.parent.mkdir(parents=True)
    resolver.write_text("# resolver\n", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    context = InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=paths,
        codex_home=home / ".codex",
        claude_home=home / ".claude",
        installation_id="standard",
    )
    pointer = RuntimePointer(
        release_id="release-a",
        runtime_source=runtime_source,
        python_bin=python_bin,
        launcher_resources=runtime_source / "launcher-resources",
        installation_context=runtime_source / "installation-context.json",
    )
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    (paths.runtime_root / "current.json").write_text(
        json.dumps({"release": "release-a"}), encoding="utf-8"
    )
    backend_bin = tmp_path / "backend bin"
    backend_bin.mkdir()
    for name in ("claude", "codex"):
        executable = backend_bin / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
        if platform == "win32":
            shutil.copy2(sys.executable, backend_bin / f"{name}.exe")
    if platform == "win32":
        python = backend_bin / "python"
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
        shutil.copy2(sys.executable, backend_bin / "python.exe")
    (paths.config_root).mkdir(parents=True)
    monkeypatch.setattr(schedule_context, "decode_current_pointer", lambda *_args, **_kwargs: pointer)
    monkeypatch.setattr(
        schedule_context,
        "load_context_from_pointer",
        lambda *, pointer, runtime_root, environ: context,
    )
    monkeypatch.setattr(
        schedule_context,
        "load_launcher_configuration",
        lambda **_: LauncherConfiguration(default_backend="codex", identity="a" * 64),
    )

    def as_managed(descriptor, canonical_path):
        return ManagedSchedule(
            descriptor_path=canonical_path,
            runtime_root=descriptor.runtime_root,
            runtime_resolver=descriptor.runtime_resolver,
            bootstrap_python=descriptor.bootstrap_python,
            installation_id=descriptor.installation_id,
            jobs_file=descriptor.jobs_file,
            log_root=descriptor.log_root,
            config_root=descriptor.config_root,
            state_root=descriptor.state_root,
            native_registration_root=descriptor.native_registration_root,
            default_backend=descriptor.default_backend,
            backend_executables=descriptor.backend_executables,
            environment=descriptor.environment,
            launcher_bin=descriptor.launcher_bin,
            launcher_resources=pointer.launcher_resources,
        )

    def test_writer(*, runtime_root, environ):
        descriptor = schedule_context._write_schedule_descriptor_for_test(
            runtime_root=runtime_root, environ=environ, platform=platform
        )
        return as_managed(descriptor, schedule_context.schedule_descriptor_path(context))

    def test_loader(*, runtime_root, descriptor_path, environ, **_kwargs):
        descriptor = schedule_context._load_schedule_descriptor_for_test(
            path=descriptor_path, environ=environ, platform=platform
        )
        return as_managed(descriptor, descriptor_path)

    monkeypatch.setattr(schedule_context, "write_managed_schedule", test_writer)
    monkeypatch.setattr(schedule_context, "load_managed_schedule", test_loader)
    environ = {"HOME": str(home), "PATH": str(backend_bin), **selector_environ}
    if platform == "win32":
        environ["USERPROFILE"] = str(home)
        environ["PATHEXT"] = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return context, environ


def _blueprint(relative: str) -> dict:
    repository_root = Path(__file__).resolve().parents[4]
    return yaml.safe_load((repository_root / relative).read_text(encoding="utf-8"))


def test_public_cli_blueprints_match_strict_setup_and_sync_surfaces() -> None:
    setup = _blueprint(
        "skills/recurring-tasks/_rtx/blueprints/rtx-setup-runner.yaml"
    )["interfaces"][
        "recurring-tasks._rtx.source.rtx-setup-runner.interface.scripts-setup"
    ]
    sync = _blueprint(
        "skills/recurring-tasks/_rtx/blueprints/rtx-unit-writer.yaml"
    )["interfaces"][
        "recurring-tasks._rtx.source.rtx-unit-writer.interface.scripts-sync"
    ]

    assert set(setup["contract"]["arguments"]) == {"migrate-cron"}
    assert setup["process_binding"]["patterns"][0]["allowed_flags"] == [
        "--migrate-cron"
    ]
    assert sync["contract"]["arguments"] == {}
    assert sync["process_binding"]["patterns"][0]["allowed_flags"] == []
    assert sync["uses_interfaces"] == [
        {"interface": "recurring.interface.control", "version": 1}
    ]
    assert "non-live" not in str(sync).lower()

    job_control = _blueprint(
        "skills/recurring-tasks/_rtx/blueprints/rtx-job-control.yaml"
    )
    public_text = str(job_control).lower()
    assert "no-sync" not in public_text
    assert "jobs-file" not in public_text
    assert "selected jobs file" not in public_text

    source_usage = (Path(__file__).resolve().parents[1] / "_job_control.py").read_text(encoding="utf-8").split('"""', 2)[1]
    assert "--no-sync" not in source_usage
    assert "--jobs-file" not in source_usage


def test_schedule_authority_imports_are_exported_and_contracted() -> None:
    assert "decode_current_pointer" in runtime_pointer.__all__
    assert "load_context_from_pointer" in install_context.__all__

    pointer_contract = _blueprint(
        "src/officina/install/blueprints/runtime-pointer.yaml"
    )["interfaces"]["install.source.runtime-pointer.interface.python-api"]["contract"]
    context_contract = _blueprint(
        "src/officina/install/blueprints/context.yaml"
    )["interfaces"]["install.source.context.interface.python-api"]["contract"]
    pointer_operations = {
        item["value"] for item in pointer_contract["arguments"]["operation"]["type"]["values"]
    }
    context_operations = {
        item["value"] for item in context_contract["arguments"]["operation"]["type"]["values"]
    }

    assert "decode-current" in pointer_operations
    assert "payload" in pointer_contract["arguments"]
    assert "load-context-from-pointer" in context_operations
    assert "pointer" in context_contract["arguments"]


def test_context_contract_states_operation_specific_authority_inputs() -> None:
    contract = _blueprint(
        "src/officina/install/blueprints/context.yaml"
    )["interfaces"]["install.source.context.interface.python-api"]["contract"]
    arguments = contract["arguments"]

    assert arguments["platform"]["required"] is False
    assert "not accepted by load-active or load-context-from-pointer" in arguments[
        "platform"
    ]["description"]
    assert arguments["home"]["required"] is False
    assert "not accepted by load-active or load-context-from-pointer" in arguments[
        "home"
    ]["description"]
    assert arguments["runtime-root"]["required"] is False
    assert "required by load-active and load-context-from-pointer" in arguments[
        "runtime-root"
    ]["description"]
    assert arguments["pointer"]["required"] is False
    assert "required only by load-context-from-pointer" in arguments["pointer"][
        "description"
    ]
    assert arguments["environ"]["required"] is True
    assert "recompute paths" in arguments["environ"]["description"]
    assert "referenced context" in arguments["environ"]["description"]


def test_descriptor_round_trip_preserves_hostile_but_valid_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)

    written = schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root,
        environ=environ,
    )
    loaded = schedule_context.load_schedule_descriptor(
        path=schedule_context.schedule_descriptor_path(context),
        environ=environ,
    )

    assert loaded == written
    assert loaded.default_backend == "codex"
    assert "雪" in str(loaded.jobs_file)
    assert loaded.environment == {
        "HOME": str(Path(environ["HOME"])),
        "PATH": os.pathsep.join(
            [
                str(context.paths.user_bin),
                str(Path(environ["PATH"])),
            ]
        ),
        "CODEX_HOME": str(context.codex_home),
        "CLAUDE_CONFIG_DIR": str(context.claude_home),
        "FAMULUS_ACTIVE_RELEASE": "release-a",
    }
    built = schedule_context.build_schedule_context(descriptor=loaded)
    assert built.jobs_file == context.paths.recurring_config_root / "jobs.yaml"
    assert built.assistant_default == "codex"


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("schema_version", 2, "schema"),
        ("installation_id", "dev-ffffffffffffffffffffffffffffffff", "installation"),
        ("runtime_root", "relative/runtime", "absolute"),
        ("jobs_file", "/tmp/jobs\n.yaml", "CR or LF"),
    ],
)
def test_descriptor_rejects_invalid_schema_identity_paths_and_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: object,
    match: str,
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    path = schedule_context.schedule_descriptor_path(context)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(schedule_context.ScheduleContextError, match=match):
        schedule_context.load_schedule_descriptor(
            path=path, environ=environ
        )


def test_descriptor_rejects_unknown_secret_field_and_ambient_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    environ.update({"SECRET_TOKEN_CANARY": "do-not-copy", "HTTPS_PROXY": "secret"})
    descriptor = schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    path = schedule_context.schedule_descriptor_path(context)
    assert "SECRET_TOKEN_CANARY" not in descriptor.environment
    assert "HTTPS_PROXY" not in descriptor.environment
    assert "do-not-copy" not in path.read_text(encoding="utf-8")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["secret"] = "injected"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(schedule_context.ScheduleContextError, match="exact fields"):
        schedule_context.load_schedule_descriptor(
            path=path, environ=environ
        )


def test_descriptor_rejects_noncanonical_symlink_and_open_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    canonical = schedule_context.schedule_descriptor_path(context)
    canonical.chmod(0o644)
    with pytest.raises(schedule_context.ScheduleContextError, match="user-only"):
        schedule_context.load_schedule_descriptor(
            path=canonical, environ=environ
        )

    canonical.chmod(0o600)
    alias = tmp_path / "descriptor-link.json"
    alias.symlink_to(canonical)
    with pytest.raises(schedule_context.ScheduleContextError, match="canonical"):
        schedule_context.load_schedule_descriptor(
            path=alias, environ=environ
        )

    backup = tmp_path / "descriptor-backup.json"
    canonical.replace(backup)
    canonical.symlink_to(backup)
    with pytest.raises(schedule_context.ScheduleContextError, match="symlink"):
        schedule_context.load_schedule_descriptor(path=canonical, environ=environ)


def test_descriptor_revalidates_pointer_launcher_backend_and_registration_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    path = schedule_context.schedule_descriptor_path(context)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key, value in (
        ("runtime_resolver", str(tmp_path / "stale-resolver")),
        ("log_root", str(tmp_path / "foreign-logs")),
        ("native_registration_root", str(tmp_path / "foreign-units")),
        ("default_backend", "claude"),
        ("backend_executables", {"claude": str(tmp_path / "x"), "codex": str(tmp_path / "y")}),
    ):
        changed = dict(payload)
        changed[key] = value
        path.write_text(json.dumps(changed), encoding="utf-8")
        path.chmod(0o600)
        with pytest.raises(schedule_context.ScheduleContextError):
            schedule_context.load_schedule_descriptor(
                path=path, environ=environ
            )


@pytest.mark.parametrize("bad_schema", [True, False, 1.0, "1"])
def test_descriptor_rejects_noninteger_schema_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_schema: object
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    path = schedule_context.schedule_descriptor_path(context)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = bad_schema
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(schedule_context.ScheduleContextError, match="schema"):
        schedule_context.load_schedule_descriptor(path=path, environ=environ)


def test_descriptor_rejects_nonstring_backend_path_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    path = schedule_context.schedule_descriptor_path(context)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["backend_executables"]["codex"] = 7
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(schedule_context.ScheduleContextError, match="codex executable"):
        schedule_context.load_schedule_descriptor(path=path, environ=environ)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_descriptor_uses_live_platform_adapter_for_supported_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    if platform in {"linux", "darwin"}:
        monkeypatch.setattr(
            schedule_context, "_posix_account_home", lambda: tmp_path / "host-home"
        )
    context, environ = _authority(tmp_path, monkeypatch, platform=platform)
    writer = (
        schedule_context.write_schedule_descriptor
        if platform == schedule_context.sys.platform
        else schedule_context._write_schedule_descriptor_for_test
    )
    loader = (
        schedule_context.load_schedule_descriptor
        if platform == schedule_context.sys.platform
        else schedule_context._load_schedule_descriptor_for_test
    )
    kwargs = {} if platform == schedule_context.sys.platform else {"platform": platform}
    written = writer(runtime_root=context.paths.runtime_root, environ=environ, **kwargs)
    loaded = loader(
        path=schedule_context.schedule_descriptor_path(context), environ=environ, **kwargs
    )
    assert loaded == written
    if platform == "linux":
        assert loaded.native_registration_root == schedule_context._posix_account_home() / ".config/systemd/user"
        assert loaded.bootstrap_python is None
    elif platform == "darwin":
        assert loaded.native_registration_root == schedule_context._posix_account_home() / "Library/LaunchAgents"
        assert loaded.bootstrap_python is None
    else:
        assert loaded.native_registration_root == context.paths.recurring_state_root / "task-wrappers"
        assert loaded.bootstrap_python == Path(environ["PATH"]) / "python.exe"


@pytest.mark.parametrize(
    ("platform", "relative"),
    [("linux", Path(".config/systemd/user")), ("darwin", Path("Library/LaunchAgents"))],
)
def test_live_posix_registration_root_uses_account_home_not_context_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    relative: Path,
) -> None:
    context, environ = _authority(tmp_path, monkeypatch, platform=platform)
    isolated_home = tmp_path / "checkout" / ".famulus" / "home"
    environ["HOME"] = str(isolated_home)
    account_home = tmp_path / "host-account-home"
    monkeypatch.setattr(schedule_context, "_posix_account_home", lambda: account_home)

    descriptor = schedule_context._write_schedule_descriptor_for_test(
        runtime_root=context.paths.runtime_root,
        environ=environ,
        platform=platform,
    )

    assert descriptor.native_registration_root == account_home / relative
    assert not descriptor.native_registration_root.is_relative_to(isolated_home)


def test_authority_snapshot_retries_after_atomic_pointer_aba(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    pointer_path = context.paths.runtime_root / "current.json"
    release_b = context.paths.runtime_root / "releases" / "release-b"
    release_b.mkdir(parents=True)
    calls = 0

    def swapping_context(*, pointer, runtime_root, environ):
        nonlocal calls
        calls += 1
        if calls == 1:
            replacement_b = pointer_path.with_name("current-b.json")
            replacement_b.write_text(json.dumps({"release": "release-b"}), encoding="utf-8")
            replacement_b.replace(pointer_path)
            replacement_a = pointer_path.with_name("current-a.json")
            replacement_a.write_text(json.dumps({"release": "release-a"}), encoding="utf-8")
            replacement_a.replace(pointer_path)
        return context

    monkeypatch.setattr(schedule_context, "load_context_from_pointer", swapping_context)
    descriptor = schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    assert calls == 2
    assert descriptor.environment["FAMULUS_ACTIVE_RELEASE"] == "release-a"


def test_descriptor_becomes_stale_after_same_installation_pointer_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    release_b = context.paths.runtime_root / "releases" / "release-b"
    release_b.mkdir(parents=True)
    pointer_b = RuntimePointer(
        release_id="release-b",
        runtime_source=release_b,
        python_bin=release_b / "python",
        launcher_resources=release_b / "launcher-resources",
        installation_context=release_b / "installation-context.json",
    )
    (context.paths.runtime_root / "current.json").write_text(
        json.dumps({"release": "release-b"}), encoding="utf-8"
    )
    monkeypatch.setattr(schedule_context, "decode_current_pointer", lambda *_args, **_kwargs: pointer_b)
    with pytest.raises(schedule_context.ScheduleContextError, match="does not match"):
        schedule_context.load_schedule_descriptor(
            path=schedule_context.schedule_descriptor_path(context), environ=environ
        )


def test_managed_production_descriptor_excludes_ai_and_python_injection_and_detects_backend_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    environ.update({"AI": "/attacker", "PYTHONPATH": "/attacker/python"})
    descriptor = schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    assert "AI" not in descriptor.environment
    assert "PYTHONPATH" not in descriptor.environment
    path = schedule_context.schedule_descriptor_path(context)
    replacement = tmp_path / "replacement-bin"
    replacement.mkdir()
    for name in ("claude", "codex"):
        executable = replacement / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
    changed_environ = {**environ, "PATH": str(replacement)}
    with pytest.raises(schedule_context.ScheduleContextError, match="does not match"):
        schedule_context.load_schedule_descriptor(path=path, environ=changed_environ)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["environment"]["AI"] = "/attacker"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(schedule_context.ScheduleContextError, match="does not match"):
        schedule_context.load_schedule_descriptor(path=path, environ=environ)


def test_descriptor_rejects_swapped_launcher_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, environ = _authority(tmp_path, monkeypatch)
    schedule_context.write_schedule_descriptor(
        runtime_root=context.paths.runtime_root, environ=environ
    )
    monkeypatch.setattr(
        schedule_context,
        "load_launcher_configuration",
        lambda **_: LauncherConfiguration(default_backend="claude", identity="b" * 64),
    )
    with pytest.raises(schedule_context.ScheduleContextError, match="does not match"):
        schedule_context.load_schedule_descriptor(
            path=schedule_context.schedule_descriptor_path(context), environ=environ
        )


def test_production_context_requires_canonical_descriptor_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FAMULUS_SCHEDULE_DESCRIPTOR", raising=False)
    with pytest.raises(schedule_context.ScheduleContextError, match="FAMULUS_SCHEDULE_DESCRIPTOR"):
        schedule_context.production_schedule_context(environ={})


def test_factory_refuses_unvalidated_descriptor_and_live_registration_override(
    tmp_path: Path,
) -> None:
    raw = schedule_context.ScheduleDescriptor(
        schema_version=1,
        installation_id="standard",
        runtime_root=tmp_path,
        runtime_resolver=tmp_path / "launch.py",
        bootstrap_python=None,
        launcher_bin=tmp_path / "bin",
        backend_executables={"claude": tmp_path / "claude", "codex": tmp_path / "codex"},
        jobs_file=tmp_path / "jobs.yaml",
        log_root=tmp_path / "logs",
        config_root=tmp_path / "config",
        state_root=tmp_path / "state",
        native_registration_root=tmp_path / "units",
        default_backend="claude",
        environment={},
    )
    with pytest.raises(schedule_context.ScheduleContextError, match="validated"):
        schedule_context.build_schedule_context(descriptor=raw)
    forged = schedule_context._ValidatedScheduleDescriptor(
        **raw.__dict__, canonical_path=tmp_path / "schedule-descriptor.json"
    )
    with pytest.raises(schedule_context.ScheduleContextError, match="validated"):
        schedule_context.build_schedule_context(descriptor=forged)


def test_four_production_callers_do_not_construct_schedule_context_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("_setup_runner.py", "_unit_writer.py", "_job_control.py", "_healthcheck_probe.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "ScheduleContext(" not in text
        assert "legacy_schedule_context" not in text
        assert "production_schedule_context" in text
