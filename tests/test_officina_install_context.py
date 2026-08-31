import json
import os
from pathlib import Path
import shutil
import sys

import pytest
import plistlib

import officina.common.atomic_files as atomic_files
import officina.install.context as install_context_module
from officina.common.famulus_paths import resolve_famulus_paths

from officina.install.context import (
    DevelopmentBoundaryError,
    InstallationContext,
    InvalidInstallationContextError,
    load_active_context,
    load_or_create_development_installation_id,
    resolve_installation_context,
    validate_development_boundaries,
)
from officina.install.runtime_pointer import RuntimePointerError, activate_release
from officina.install.managed_runtime import (
    ManagedRuntimeError,
    _publish_installation_context,
)
from officina.recurring.native import render_macos_plist
from officina.recurring.runtime import load_managed_schedule, write_managed_schedule


def _active_candidate(
    tmp_path: Path,
    *,
    mode: str,
    release_id: str = "release-a",
    platform: str | None = None,
) -> tuple[Path, dict[str, str], Path]:
    selected_platform = platform or install_context_module.sys.platform
    if mode == "standard":
        home = tmp_path / "home"
        source = tmp_path / "source"
        development_root = None
        installation_id = "standard"
        selected_home = home
        codex_home = home / ".codex"
        claude_home = home / ".claude"
        environ = {"HOME": str(home), "AI": str(tmp_path / "wrong-checkout")}
        if selected_platform == "win32":
            environ.update(
                {
                    "USERPROFILE": str(home),
                    "LOCALAPPDATA": str(home / "AppData" / "Local"),
                    "APPDATA": str(home / "AppData" / "Roaming"),
                }
            )
        runtime_root = resolve_famulus_paths(
            platform=selected_platform, home=home, environ=environ
        ).runtime_root
    else:
        source = tmp_path / "checkout"
        local_root = source / ".famulus"
        isolated_home = local_root / "home"
        development_root = source
        installation_id = "dev-0123456789abcdef0123456789abcdef"
        selected_home = isolated_home
        codex_home = local_root / "homes" / "codex"
        claude_home = local_root / "homes" / "claude"
        environ = {
            "HOME": str(isolated_home),
            "AI": str(tmp_path / "wrong-checkout"),
            "CODEX_HOME": str(codex_home),
            "CLAUDE_CONFIG_DIR": str(claude_home),
            "XDG_DATA_HOME": str(isolated_home / ".local" / "share"),
            "XDG_CONFIG_HOME": str(isolated_home / ".config"),
            "XDG_STATE_HOME": str(isolated_home / ".local" / "state"),
        }
        if selected_platform == "win32":
            environ = {
                "HOME": str(isolated_home),
                "USERPROFILE": str(isolated_home),
                "AI": str(tmp_path / "wrong-checkout"),
                "CODEX_HOME": str(codex_home),
                "CLAUDE_CONFIG_DIR": str(claude_home),
                "LOCALAPPDATA": str(isolated_home / "AppData" / "Local"),
                "APPDATA": str(isolated_home / "AppData" / "Roaming"),
            }
        runtime_root = resolve_famulus_paths(
            platform=selected_platform, home=isolated_home, environ=environ
        ).runtime_root
        local_root.mkdir(parents=True)
        (local_root / "install-id").write_text(installation_id + "\n", encoding="utf-8")
    source.mkdir(parents=True, exist_ok=True)
    release = runtime_root / "releases" / release_id
    python_bin = release / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    repository = tmp_path / f"repository-{release_id}"
    (repository / "skills").mkdir(parents=True)
    repository_config = repository / "officina.toml"
    repository_config.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n', encoding="utf-8"
    )
    launcher_resources = release / "launcher-resources" if mode == "standard" else source
    launcher_resources.mkdir(parents=True, exist_ok=True)
    context_record = release / "installation-context.json"
    context_record.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_id": release_id,
                "mode": mode,
                "installation_id": installation_id,
                "source_root": str(source),
                "development_root": str(development_root) if development_root else None,
                "selected_home": str(selected_home),
                "codex_home": str(codex_home),
                "claude_home": str(claude_home),
            }
        ),
        encoding="utf-8",
    )
    activate_release(
        runtime_root=runtime_root,
        release_dir=release,
        python_bin=python_bin,
        repository_config=repository_config,
        launcher_resources=launcher_resources,
        installation_context=context_record,
    )
    return runtime_root, environ, source


def _new_standard_candidate(
    tmp_path: Path, *, runtime_root: Path, environ: dict[str, str]
) -> tuple[dict[str, Path], InstallationContext]:
    source = tmp_path / "source-new"
    source.mkdir()
    context = resolve_installation_context(
        mode="standard",
        source_root=source,
        development_root=None,
        platform="linux",
        home=Path(environ["HOME"]),
        environ=environ,
    )
    release = runtime_root / "releases" / "release-new"
    python_bin = release / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    resources = release / "launcher-resources"
    resources.mkdir()
    repository = tmp_path / "repository-new"
    (repository / "skills").mkdir(parents=True)
    repository_config = repository / "officina.toml"
    repository_config.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n', encoding="utf-8"
    )
    return {
        "runtime_root": runtime_root,
        "release_dir": release,
        "python_bin": python_bin,
        "repository_config": repository_config,
        "launcher_resources": resources,
    }, context


def test_recurring_owned_descriptor_sanitizes_persistent_environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = tmp_path / "selected plugin"
    plugin_root.mkdir()
    backend_root = tmp_path / "backend with spaces 雪"
    backend_root.mkdir()
    for name in ("claude", "codex"):
        executable = backend_root / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
        if sys.platform == "win32":
            shutil.copy2(sys.executable, executable.with_suffix(".exe"))
    if sys.platform == "win32":
        shutil.copy2(sys.executable, backend_root / "python.exe")
    authority_environment = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "APPDATA": str(tmp_path / "appdata"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "PATH": str(backend_root),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "SECRET_CANARY": "must-not-persist",
    }
    schedule = write_managed_schedule(
        python=Path(sys.executable).resolve(),
        plugin_root=plugin_root,
        environ=authority_environment,
    )
    rendered = plistlib.loads(
        render_macos_plist(
            schedule,
            {
                "name": "demo",
                "command": "invoke-skill demo",
                "schedule": "0 * * * *",
                "enabled": True,
            },
        )
    )["EnvironmentVariables"]

    assert "SECRET_CANARY" not in rendered
    loaded = load_managed_schedule(descriptor_path=schedule.descriptor_path)
    assert loaded == schedule


def _inject_posix_atomic_boundary(
    monkeypatch: pytest.MonkeyPatch, *, boundary: str, timing: str = "before"
) -> None:
    if boundary == "write":
        real = atomic_files._write_and_sync

        def injected_write(descriptor: int, data: bytes) -> None:
            if timing == "before":
                raise OSError("injected temp write interruption")
            real(descriptor, data)
            raise OSError("injected temp write interruption")

        monkeypatch.setattr(atomic_files, "_write_and_sync", injected_write)
        return
    if boundary == "replace":
        real = atomic_files._secure_replace

        def injected_replace(parent_fd: int, source: str, destination: str) -> None:
            if timing == "before":
                raise OSError("injected replace interruption")
            real(parent_fd, source, destination)
            raise OSError("injected replace interruption")

        monkeypatch.setattr(atomic_files, "_secure_replace", injected_replace)
        return
    real_fsync = atomic_files.os.fsync
    calls = 0
    target_call = 1 if boundary == "fsync" else 2

    def injected_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == target_call and timing == "before":
            raise OSError(f"injected {boundary} interruption")
        real_fsync(descriptor)
        if calls == target_call and timing == "after":
            raise OSError(f"injected {boundary} interruption")

    monkeypatch.setattr(atomic_files.os, "fsync", injected_fsync)


def _development_id(checkout: Path, *, home: Path) -> str:
    return load_or_create_development_installation_id(
        checkout,
        platform="linux",
        home=home,
        environ={},
    )


def test_standard_context_uses_reserved_identity_and_normal_homes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    context = resolve_installation_context(
        mode="standard",
        source_root=source,
        development_root=None,
        platform="linux",
        home=tmp_path,
        environ={},
    )
    assert context.mode == "standard"
    assert context.installation_id == "standard"
    assert context.development_root is None
    assert context.codex_home == tmp_path / ".codex"
    assert context.claude_home == tmp_path / ".claude"


@pytest.mark.parametrize("mode", ["standard", "development"])
def test_load_active_context_reconstructs_pointer_selected_context(
    tmp_path: Path, mode: str
) -> None:
    runtime_root, environ, source = _active_candidate(tmp_path, mode=mode)

    context = load_active_context(runtime_root=runtime_root, environ=environ)

    assert context.mode == mode
    assert context.source_root == source.resolve()
    assert context.paths.runtime_root == runtime_root
    if mode == "standard":
        assert context.installation_id == "standard"
        assert context.development_root is None
        assert context.codex_home == Path(environ["HOME"]) / ".codex"
    else:
        assert context.installation_id == "dev-0123456789abcdef0123456789abcdef"
        assert context.development_root == source.resolve()
        assert context.codex_home == source / ".famulus" / "homes" / "codex"


@pytest.mark.parametrize("platform", ["darwin", "win32"])
@pytest.mark.parametrize("mode", ["standard", "development"])
def test_active_context_fixture_round_trips_native_platform_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    mode: str,
) -> None:
    runtime_root, environ, source = _active_candidate(
        tmp_path, mode=mode, platform=platform
    )
    monkeypatch.setattr(install_context_module.sys, "platform", platform)

    context = load_active_context(runtime_root=runtime_root, environ=environ)

    assert context.paths.runtime_root == runtime_root
    assert context.source_root == source.resolve()


def test_load_active_context_ignores_cwd_and_legacy_source_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root, environ, source = _active_candidate(tmp_path, mode="development")
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    environ["AI"] = str(tmp_path / "attacker-selected-checkout")

    context = load_active_context(runtime_root=runtime_root, environ=environ)

    assert context.source_root == source.resolve()
    assert context.development_root == source.resolve()


def test_load_active_development_context_uses_only_the_supplied_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root, environ, source = _active_candidate(tmp_path, mode="development")
    hostile_home = tmp_path / "ambient-hostile-home"
    monkeypatch.setenv("HOME", str(hostile_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(hostile_home / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(hostile_home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(hostile_home / "state"))

    context = load_active_context(runtime_root=runtime_root, environ=environ)

    assert context.development_root == source.resolve()
    assert context.paths.runtime_root == runtime_root


@pytest.mark.parametrize(
    ("selector", "relative"),
    [
        ("HOME", Path("other-home")),
        ("XDG_DATA_HOME", Path("other-data")),
        ("XDG_CONFIG_HOME", Path("other-config")),
        ("XDG_STATE_HOME", Path("other-state")),
    ],
)
def test_load_active_development_context_rejects_supplied_selector_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
    relative: Path,
) -> None:
    runtime_root, environ, _ = _active_candidate(
        tmp_path, mode="development", platform="linux"
    )
    monkeypatch.setattr(install_context_module.sys, "platform", "linux")
    environ[selector] = str(tmp_path / relative)

    with pytest.raises(InvalidInstallationContextError, match="development context"):
        load_active_context(runtime_root=runtime_root, environ=environ)


@pytest.mark.parametrize("selector", ["HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"])
def test_load_active_development_context_rejects_windows_selector_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    runtime_root, environ, _ = _active_candidate(
        tmp_path, mode="development", platform="win32"
    )
    environ[selector] = str(tmp_path / f"wrong-{selector.lower()}")
    monkeypatch.setattr(install_context_module.sys, "platform", "win32")

    with pytest.raises(InvalidInstallationContextError, match="development context"):
        load_active_context(runtime_root=runtime_root, environ=environ)


def test_load_active_context_rejects_relative_runtime_root() -> None:
    with pytest.raises(InvalidInstallationContextError, match="runtime_root"):
        load_active_context(runtime_root=Path("relative"), environ={"HOME": "/home/test"})


@pytest.mark.parametrize("pointer_payload", [None, "{", "[]"])
def test_load_active_context_rejects_absent_or_malformed_pointer(
    tmp_path: Path, pointer_payload: str | None
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    if pointer_payload is not None:
        (runtime_root / "current.json").write_text(pointer_payload, encoding="utf-8")

    with pytest.raises(RuntimePointerError):
        load_active_context(
            runtime_root=runtime_root,
            environ={"HOME": str(tmp_path / "home")},
        )


def test_load_active_context_rejects_computed_runtime_root_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root, environ, _ = _active_candidate(
        tmp_path, mode="standard", platform="linux"
    )
    monkeypatch.setattr(install_context_module.sys, "platform", "linux")
    environ["HOME"] = str(tmp_path / "different-home")

    with pytest.raises(InvalidInstallationContextError, match="runtime_root"):
        load_active_context(runtime_root=runtime_root, environ=environ)


def test_load_active_context_rejects_development_identity_substitution(tmp_path: Path) -> None:
    runtime_root, environ, source = _active_candidate(tmp_path, mode="development")
    (source / ".famulus" / "install-id").write_text(
        "dev-ffffffffffffffffffffffffffffffff\n", encoding="utf-8"
    )

    with pytest.raises(InvalidInstallationContextError, match="installation ID"):
        load_active_context(runtime_root=runtime_root, environ=environ)


def test_load_active_context_rejects_missing_launcher_resources(tmp_path: Path) -> None:
    runtime_root, environ, _ = _active_candidate(tmp_path, mode="standard")
    pointer = json.loads((runtime_root / "current.json").read_text(encoding="utf-8"))
    launcher_resources = Path(pointer["launcher_resources"])
    launcher_resources.rmdir()

    with pytest.raises(RuntimePointerError, match="launcher_resources"):
        load_active_context(runtime_root=runtime_root, environ=environ)


def test_load_active_context_uses_the_fixed_resolver_trusted_interpreter_generation(
    tmp_path: Path,
) -> None:
    runtime_root, environ, _ = _active_candidate(tmp_path, mode="standard")
    pointer = json.loads((runtime_root / "current.json").read_text(encoding="utf-8"))
    python_bin = Path(pointer["python_bin"])
    trusted_root = tmp_path / "uv-python-store"
    trusted_python = trusted_root / "cpython" / "bin" / "python"
    trusted_python.parent.mkdir(parents=True)
    trusted_python.write_text("#!/bin/sh\n", encoding="utf-8")
    python_bin.unlink()
    python_bin.symlink_to(trusted_python)
    generation = "a" * 64
    generation_root = runtime_root / "bootstrap" / "resolvers" / "generations" / generation
    generation_root.mkdir(parents=True)
    (generation_root / "launch.py").write_text("# fixed resolver\n", encoding="utf-8")
    (generation_root / "trusted-roots.json").write_text(
        json.dumps([str(trusted_root)]), encoding="utf-8"
    )
    fixed_root = runtime_root / "bootstrap" / "resolvers" / "v1"
    fixed_root.mkdir(parents=True)
    (fixed_root / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}), encoding="utf-8"
    )

    context = load_active_context(runtime_root=runtime_root, environ=environ)

    assert context.paths.runtime_root == runtime_root


def test_load_active_context_rejects_cwd_relative_resolver_trust_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root, environ, _ = _active_candidate(tmp_path, mode="standard")
    pointer = json.loads((runtime_root / "current.json").read_text(encoding="utf-8"))
    python_bin = Path(pointer["python_bin"])
    attacker_cwd = tmp_path / "attacker-cwd"
    attacker_python = attacker_cwd / "python"
    attacker_cwd.mkdir()
    attacker_python.write_text("#!/bin/sh\n", encoding="utf-8")
    python_bin.unlink()
    python_bin.symlink_to(attacker_python)
    generation = "b" * 64
    generation_root = runtime_root / "bootstrap" / "resolvers" / "generations" / generation
    generation_root.mkdir(parents=True)
    (generation_root / "launch.py").write_text("# fixed resolver\n", encoding="utf-8")
    (generation_root / "trusted-roots.json").write_text('["."]', encoding="utf-8")
    fixed_root = runtime_root / "bootstrap" / "resolvers" / "v1"
    fixed_root.mkdir(parents=True)
    (fixed_root / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}), encoding="utf-8"
    )
    monkeypatch.chdir(attacker_cwd)

    with pytest.raises(RuntimePointerError, match="absolute"):
        load_active_context(runtime_root=runtime_root, environ=environ)


@pytest.mark.parametrize("payload", ["{", "{}", '["/absolute", 7]'])
def test_load_active_context_rejects_malformed_selected_trust_metadata(
    tmp_path: Path, payload: str
) -> None:
    runtime_root, environ, _ = _active_candidate(tmp_path, mode="standard")
    generation = "c" * 64
    generation_root = runtime_root / "bootstrap" / "resolvers" / "generations" / generation
    generation_root.mkdir(parents=True)
    (generation_root / "launch.py").write_text("# fixed resolver\n", encoding="utf-8")
    (generation_root / "trusted-roots.json").write_text(payload, encoding="utf-8")
    fixed_root = runtime_root / "bootstrap" / "resolvers" / "v1"
    fixed_root.mkdir(parents=True)
    (fixed_root / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}), encoding="utf-8"
    )

    with pytest.raises(RuntimePointerError, match="trusted roots"):
        load_active_context(runtime_root=runtime_root, environ=environ)


# famulus-skip: category=platform-contract; reason=POSIX descriptor write/fsync/rename boundaries are injected directly; alternate=native Windows atomic replacement remains covered by the Task 5 acceptance matrix
@pytest.mark.skipif(os.name == "nt", reason="POSIX atomic publication boundaries")
@pytest.mark.parametrize(
    ("boundary", "timing"),
    [
        ("write", "before"),
        ("write", "after"),
        ("fsync", "before"),
        ("fsync", "after"),
        ("replace", "before"),
        ("replace", "after"),
    ],
)
def test_context_record_publication_interruption_keeps_the_complete_old_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    timing: str,
) -> None:
    runtime_root, environ, old_source = _active_candidate(
        tmp_path, mode="standard", release_id="release-old"
    )
    candidate, new_context = _new_standard_candidate(
        tmp_path, runtime_root=runtime_root, environ=environ
    )
    _inject_posix_atomic_boundary(monkeypatch, boundary=boundary, timing=timing)

    with pytest.raises(ManagedRuntimeError, match="installation context"):
        _publish_installation_context(
            release_dir=candidate["release_dir"], context=new_context
        )

    visible = load_active_context(runtime_root=runtime_root, environ=environ)
    assert visible.source_root == old_source.resolve()


# famulus-skip: category=platform-contract; reason=POSIX descriptor write/fsync/replace/directory-durability boundaries are injected directly; alternate=native Windows atomic replacement remains covered by the Task 5 acceptance matrix
@pytest.mark.skipif(os.name == "nt", reason="POSIX atomic publication boundaries")
@pytest.mark.parametrize(
    ("boundary", "timing", "visible_pair"),
    [
        ("write", "before", "old"),
        ("write", "after", "old"),
        ("fsync", "before", "old"),
        ("fsync", "after", "old"),
        ("replace", "before", "old"),
        ("replace", "after", "new"),
        ("directory-fsync", "before", "new"),
        ("directory-fsync", "after", "new"),
    ],
)
def test_pointer_publication_interruption_exposes_one_complete_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    timing: str,
    visible_pair: str,
) -> None:
    runtime_root, environ, old_source = _active_candidate(
        tmp_path, mode="standard", release_id="release-old"
    )
    candidate, new_context = _new_standard_candidate(
        tmp_path, runtime_root=runtime_root, environ=environ
    )
    candidate["installation_context"] = _publish_installation_context(
        release_dir=candidate["release_dir"], context=new_context
    )
    _inject_posix_atomic_boundary(monkeypatch, boundary=boundary, timing=timing)

    with pytest.raises(OSError, match="injected"):
        activate_release(**candidate)

    visible = load_active_context(runtime_root=runtime_root, environ=environ)
    expected_source = (
        old_source.resolve() if visible_pair == "old" else new_context.source_root
    )
    assert visible.source_root == expected_source


def test_development_context_is_clone_local_and_stores_no_environment_or_path_policy(tmp_path):
    checkout = tmp_path / "checkout with spaces" / "fåmulus"
    checkout.mkdir(parents=True)
    installation_id = _development_id(checkout, home=tmp_path)
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform="linux",
        home=tmp_path,
        environ={"AI": "/wrong", "FAMULUS_REPO_ROOT": "/also-wrong"},
        installation_id=installation_id,
    )
    local_root = checkout / ".famulus"
    isolated_home = local_root / "home"
    assert context.source_root == checkout.resolve()
    assert context.development_root == checkout.resolve()
    assert context.paths.data_root == isolated_home / ".local" / "share" / "famulus"
    assert context.paths.config_root == isolated_home / ".config" / "famulus"
    assert context.paths.state_root == isolated_home / ".local" / "state" / "famulus"
    assert context.paths.user_bin == isolated_home / ".local" / "bin"
    assert context.codex_home == local_root / "homes" / "codex"
    assert context.claude_home == local_root / "homes" / "claude"
    assert not hasattr(context, "environ")
    assert not hasattr(context, "persist_user_bin")


def test_development_installation_id_is_random_scheduler_safe_and_reused(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    first = _development_id(checkout, home=tmp_path)
    second = _development_id(checkout, home=tmp_path)
    assert first == second
    assert first.startswith("dev-")
    assert first[4:].isalnum()
    assert (checkout / ".famulus" / "install-id").read_text(encoding="utf-8") == first + "\n"


@pytest.mark.parametrize("mode", ["standard", "development"])
def test_context_rejects_relative_source_root(mode):
    with pytest.raises(InvalidInstallationContextError):
        resolve_installation_context(
            mode=mode,
            source_root=Path("relative"),
            development_root=Path("relative") if mode == "development" else None,
            platform="linux",
            home=Path("/home/test"),
            environ={},
            installation_id="dev-0123456789abcdef0123456789abcdef" if mode == "development" else None,
        )


def test_development_boundary_rejects_symlink_escape(tmp_path):
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside"
    checkout.mkdir()
    outside.mkdir()
    (checkout / ".famulus").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DevelopmentBoundaryError):
        _development_id(checkout, home=tmp_path)


def test_development_installation_id_rejects_leaf_symlink_escape(tmp_path):
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside-id"
    checkout.mkdir()
    (checkout / ".famulus").mkdir()
    outside.write_text("dev-0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    (checkout / ".famulus" / "install-id").symlink_to(outside)
    with pytest.raises(DevelopmentBoundaryError):
        _development_id(checkout, home=tmp_path)


def test_development_context_requires_its_checkout_as_source(tmp_path):
    checkout = tmp_path / "checkout"
    other_source = tmp_path / "other-source"
    checkout.mkdir()
    other_source.mkdir()
    installation_id = _development_id(checkout, home=tmp_path)
    with pytest.raises(InvalidInstallationContextError):
        resolve_installation_context(
            mode="development",
            source_root=other_source,
            development_root=checkout,
            platform="linux",
            home=tmp_path,
            environ={},
            installation_id=installation_id,
        )


def test_development_context_rejects_identity_substitution(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _development_id(checkout, home=tmp_path)
    with pytest.raises(InvalidInstallationContextError):
        resolve_installation_context(
            mode="development",
            source_root=checkout,
            development_root=checkout,
            platform="linux",
            home=tmp_path,
            environ={},
            installation_id="dev-0123456789abcdef0123456789abcdef",
        )


def test_installation_id_creation_rejects_checkout_inside_normal_assistant_home(tmp_path):
    normal_home = tmp_path / "normal-home"
    checkout = normal_home / ".codex" / "checkout"
    checkout.mkdir(parents=True)
    with pytest.raises(DevelopmentBoundaryError, match="stable root"):
        _development_id(checkout, home=normal_home)
    assert not (checkout / ".famulus").exists()


def test_resolver_rejects_checkout_inside_standard_famulus_root(tmp_path):
    normal_home = tmp_path / "normal-home"
    checkout = normal_home / ".local" / "share" / "famulus" / "checkout"
    identity_root = checkout / ".famulus"
    identity_root.mkdir(parents=True)
    installation_id = "dev-0123456789abcdef0123456789abcdef"
    (identity_root / "install-id").write_text(installation_id + "\n", encoding="utf-8")
    with pytest.raises(DevelopmentBoundaryError, match="stable root"):
        resolve_installation_context(
            mode="development",
            source_root=checkout,
            development_root=checkout,
            platform="linux",
            home=normal_home,
            environ={},
            installation_id=installation_id,
        )


@pytest.mark.parametrize("operation", ["install", "repair", "uninstall"])
@pytest.mark.parametrize(
    "protected_relative_root",
    [
        Path(".codex"),
        Path(".claude"),
        Path(".local/share/famulus"),
        Path(".config/famulus"),
        Path(".local/state/famulus"),
        Path(".local/bin"),
    ],
)
def test_actual_standard_roots_and_homes_are_canaries_before_every_mutation(
    tmp_path, operation, protected_relative_root
):
    normal_home = tmp_path / "normal-home"
    checkout = normal_home / protected_relative_root / "checkout"
    checkout.mkdir(parents=True)
    bootstrap_home = tmp_path / "different-stable-home"
    installation_id = _development_id(checkout, home=bootstrap_home)
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform="linux",
        home=bootstrap_home,
        environ={},
        installation_id=installation_id,
    )
    with pytest.raises(DevelopmentBoundaryError, match=operation):
        validate_development_boundaries(
            context,
            operation=operation,
            platform="linux",
            home=normal_home,
            environ={},
        )
