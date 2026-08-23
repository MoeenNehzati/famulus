from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import InstallationContext, resolve_installation_context
from officina.install.development_activation import build_interactive_environment

from officina.launchers.agent import (
    LauncherConfigurationError,
    ensure_launcher_configuration,
    load_launcher_configuration,
    select_backend,
)
from officina.launchers import agent as agent_module


def test_launcher_module_starts_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """Catch package-level import cycles before a generated launcher delegates."""
    repo_root = Path(__file__).resolve().parents[1]
    environ = os.environ.copy()
    environ["PYTHONPATH"] = str(repo_root / "src")

    result = subprocess.run(
        [sys.executable, "-m", "officina.launchers.agent", "--help"],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_launcher_packages_preserve_lazy_compatibility_exports() -> None:
    from officina import install, launchers

    assert install.diagnose_installation.__module__ == "officina.install.doctor"
    assert launchers.build_agent_command.__module__ == "officina.launchers.agent"


class RecordingManifest:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, object]]] = []

    def record(self, kind: str, **fields: object) -> None:
        self.entries.append((kind, fields))


def _isolated_standard_environment(home: Path) -> dict[str, str]:
    environ = {"HOME": str(home)}
    if agent_module.sys.platform == "win32":
        environ.update(
            {
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "APPDATA": str(home / "AppData" / "Roaming"),
            }
        )
    return environ


def _write_active_standard_launcher(tmp_path: Path) -> tuple[InstallationContext, Path]:
    source = Path(__file__).resolve().parents[1]
    context = InstallationContext(
        mode="standard",
        source_root=source,
        development_root=None,
        paths=resolve_famulus_paths(
            platform=agent_module.sys.platform,
            home=tmp_path,
            environ=_isolated_standard_environment(tmp_path),
        ),
        selected_home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_home=tmp_path / ".claude",
        installation_id="standard",
    )
    release = context.paths.releases_root / "release-a"
    python_bin = release / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("python\n", encoding="utf-8")
    resources = release / "launcher-resources"
    (resources / "agents").mkdir(parents=True)
    (resources / "profiles").mkdir()
    for name in ("assistant", "collab", "coauthor", "background_run"):
        (resources / "agents" / f"{name}.md").write_text(
            f"---\ndescription: {name}\n---\n{name} prompt\n", encoding="utf-8"
        )
    (resources / "profiles" / "background_run.config.toml").write_text(
        "approval_policy = \"never\"\n", encoding="utf-8"
    )
    (resources / "profiles" / "background_run_claude_setting.json").write_text(
        "{}\n", encoding="utf-8"
    )
    record = release / "installation-context.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_id": release.name,
                "mode": "standard",
                "installation_id": "standard",
                "source_root": str(source),
                "development_root": None,
                "selected_home": str(context.selected_home),
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
                "repository_config": str(source / "officina.toml"),
                "launcher_resources": str(resources),
                "installation_context": str(record),
            }
        ),
        encoding="utf-8",
    )
    context.paths.config_root.mkdir(parents=True, exist_ok=True)
    (context.paths.config_root / "launchers.json").write_text(
        '{"schema_version": 1, "default_backend": "codex"}\n', encoding="utf-8"
    )
    return context, resources


def test_managed_launch_environment_removes_legacy_state_overrides() -> None:
    cleaned = agent_module._managed_launch_environment(
        {
            "ASSISTANT_LOGS": "/hostile/logs",
            "EMAIL_TRIAGE_STATE_DIR": "/hostile/triage",
            "LIST_MANAGER_CLOUD_LOCK_DIR": "/hostile/locks",
            "LLM_WAKEUP_HOME": "/hostile/wakeup",
            "HOME": "/selected/home",
        }
    )

    assert cleaned == {"HOME": "/selected/home"}


def test_fresh_launcher_accepts_active_generation_interpreter_trust(
    tmp_path: Path,
) -> None:
    """Catch launchers that read the retired fixed resolver trust sidecar."""
    context, _resources = _write_active_standard_launcher(tmp_path)
    pointer = json.loads(context.paths.current_pointer.read_text(encoding="utf-8"))
    python_bin = Path(pointer["python_bin"])
    python_bin.unlink()
    python_root = tmp_path / "uv-python"
    external_python = python_root / "bin" / "python3"
    external_python.parent.mkdir(parents=True)
    external_python.write_text("python\n", encoding="utf-8")
    python_bin.symlink_to(external_python)

    generation = "a" * 64
    generation_root = (
        context.paths.runtime_root
        / "bootstrap"
        / "resolvers"
        / "generations"
        / generation
    )
    generation_root.mkdir(parents=True)
    (generation_root / "launch.py").write_text("# resolver\n", encoding="utf-8")
    (generation_root / "trusted-roots.json").write_text(
        json.dumps([str(python_root)]), encoding="utf-8"
    )
    fixed_root = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1"
    fixed_root.mkdir(parents=True)
    (fixed_root / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / ("codex.exe" if sys.platform == "win32" else "codex")
    shutil.copy2(sys.executable, fake_codex)
    repo_root = Path(__file__).resolve().parents[1]
    environ = os.environ.copy()
    for name in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    ):
        environ.pop(name, None)
    environ.update(_isolated_standard_environment(tmp_path))
    environ.update(
        {
            "PATH": os.pathsep.join((str(fake_bin), environ.get("PATH", ""))),
            "PYTHONPATH": str(repo_root / "src"),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "officina.launchers.agent",
            "--runtime-root",
            str(context.paths.runtime_root),
            "--agent",
            "assistant",
            "--codex",
            "--version",
        ],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert "RuntimePointerError" not in result.stderr
    assert "model_instructions_file" in result.stderr


def test_fresh_launcher_rejects_relative_interpreter_trust(tmp_path: Path) -> None:
    context, _resources = _write_active_standard_launcher(tmp_path)
    generation = "b" * 64
    generation_root = (
        context.paths.runtime_root
        / "bootstrap"
        / "resolvers"
        / "generations"
        / generation
    )
    generation_root.mkdir(parents=True)
    (generation_root / "launch.py").write_text("# resolver\n", encoding="utf-8")
    (generation_root / "trusted-roots.json").write_text(
        json.dumps(["relative-root"]), encoding="utf-8"
    )
    fixed_root = context.paths.runtime_root / "bootstrap" / "resolvers" / "v1"
    fixed_root.mkdir(parents=True)
    (fixed_root / "active.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]
    environ = os.environ.copy()
    for name in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
    ):
        environ.pop(name, None)
    environ.update(_isolated_standard_environment(tmp_path))
    environ["PYTHONPATH"] = str(repo_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "officina.launchers.agent",
            "--runtime-root",
            str(context.paths.runtime_root),
            "--agent",
            "assistant",
            "--codex",
            "--version",
        ],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode != 0
    assert "resolver trusted roots must be absolute path strings" in result.stderr


def _write_active_development_launcher(
    tmp_path: Path,
) -> tuple[InstallationContext, Path, Path]:
    checkout = tmp_path / "live checkout ü"
    checkout.mkdir()
    (checkout / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n', encoding="utf-8"
    )
    (checkout / "skills").mkdir()
    install_id = "dev-" + "a" * 32
    (checkout / ".famulus").mkdir()
    (checkout / ".famulus" / "install-id").write_text(install_id + "\n", encoding="utf-8")
    host_home = tmp_path / "host home"
    host_home.mkdir()
    context = resolve_installation_context(
        mode="development",
        source_root=checkout,
        development_root=checkout,
        platform=agent_module.sys.platform,
        home=host_home,
        environ={"HOME": str(host_home)},
        installation_id=install_id,
    )
    release = context.paths.releases_root / "release-dev"
    python_bin = release / "venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("python\n", encoding="utf-8")
    (checkout / "agents").mkdir()
    (checkout / "profiles").mkdir()
    for name in ("assistant", "collab", "coauthor", "background_run"):
        (checkout / "agents" / f"{name}.md").write_text(
            f"---\ndescription: {name}\n---\n{name} prompt\n", encoding="utf-8"
        )
        (checkout / "profiles" / f"{name}_claude_setting.json").write_text(
            "{}\n", encoding="utf-8"
        )
    (checkout / "profiles" / "background_run.config.toml").write_text(
        "approval_policy = \"never\"\n", encoding="utf-8"
    )
    record = release / "installation-context.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_id": release.name,
                "mode": "development",
                "installation_id": install_id,
                "source_root": str(checkout),
                "development_root": str(checkout),
                "selected_home": str(context.selected_home),
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
                "repository_config": str(checkout / "officina.toml"),
                "launcher_resources": str(checkout),
                "installation_context": str(record),
            }
        ),
        encoding="utf-8",
    )
    context.paths.config_root.mkdir(parents=True, exist_ok=True)
    (context.paths.config_root / "launchers.json").write_text(
        '{"schema_version": 1, "default_backend": "claude"}\n', encoding="utf-8"
    )
    return context, checkout, host_home


def test_launcher_configuration_is_created_atomically_with_manifest_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacements: list[Path] = []
    from officina.launchers import agent

    real_replace = agent.atomic_replace_bytes

    def recording_replace(path: Path, *args: object, **kwargs: object) -> None:
        replacements.append(path)
        real_replace(path, *args, **kwargs)

    monkeypatch.setattr(agent, "atomic_replace_bytes", recording_replace)
    manifest = RecordingManifest()

    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend="codex", manifest=manifest
    )

    path = tmp_path / "launchers.json"
    assert configured.default_backend == "codex"
    assert replacements == [path]
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "default_backend": "codex",
    }
    assert manifest.entries == [
        (
            "file",
            {
                "path": str(path),
                "sha256": configured.identity,
                "preserve_if_modified": True,
            },
        )
    ]


def test_launcher_configuration_preserves_existing_choice_without_explicit_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "launchers.json"
    path.write_text('{"schema_version": 1, "default_backend": "codex"}\n')
    before = path.read_bytes()

    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend=None, manifest=RecordingManifest()
    )

    assert configured.default_backend == "codex"
    assert path.read_bytes() == before


def test_launcher_configuration_changes_only_when_explicit(tmp_path: Path) -> None:
    path = tmp_path / "launchers.json"
    path.write_text('{"schema_version": 1, "default_backend": "codex"}\n')

    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend="claude", manifest=RecordingManifest()
    )

    assert configured.default_backend == "claude"
    assert load_launcher_configuration(config_root=tmp_path).default_backend == "claude"


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "default_backend": "claude"},
        {"schema_version": 1, "default_backend": "other"},
        {"schema_version": 1, "default_backend": "claude", "extra": True},
    ],
)
def test_launcher_configuration_rejects_invalid_exact_schema(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    (tmp_path / "launchers.json").write_text(json.dumps(payload))

    with pytest.raises(LauncherConfigurationError):
        load_launcher_configuration(config_root=tmp_path)


def test_process_local_backend_override_wins_without_mutating_configuration(
    tmp_path: Path,
) -> None:
    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend="claude"
    )

    assert select_backend(
        agent="assistant",
        configuration=configured,
        environ={"ASSISTANT_DEFAULT": "codex"},
    ) == "codex"
    assert load_launcher_configuration(config_root=tmp_path).default_backend == "claude"


def test_agent_specific_process_override_precedes_assistant_override(tmp_path: Path) -> None:
    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend="claude"
    )

    assert select_backend(
        agent="collab",
        configuration=configured,
        environ={"COLLAB_DEFAULT": "claude", "ASSISTANT_DEFAULT": "codex"},
    ) == "claude"


def test_production_agent_launches_use_active_context_and_durable_backend_without_legacy_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASSISTANT_DEFAULT and agent-specific defaults are process overrides only;
    absent overrides select launchers.json, never cwd, AI, or FAMULUS_REPO_ROOT.
    """
    context, resources = _write_active_standard_launcher(tmp_path)
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    for name in (
        "AI",
        "FAMULUS_REPO_ROOT",
        "ASSISTANT_DEFAULT",
        "COLLAB_DEFAULT",
        "COAUTHOR_DEFAULT",
        "BACKGROUND_RUN_DEFAULT",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in _isolated_standard_environment(tmp_path).items():
        monkeypatch.setenv(name, value)
    launched: list[list[str]] = []
    monkeypatch.setattr(agent_module, "_launch_command", lambda command: launched.append(command) or 0)

    for name in ("assistant", "collab", "coauthor", "background_run"):
        assert agent_module.main(
            ["--runtime-root", str(context.paths.runtime_root), "--agent", name, "--local"]
        ) == 0

    assert [command[0] for command in launched] == ["codex"] * 4
    assert all(str(resources / "agents") in " ".join(command) for command in launched)
    assert os.environ["FAMULUS_LAUNCHER_RESOURCES"] == str(resources)


def test_background_run_hook_command_consumes_only_process_local_pointer_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAMULUS_LAUNCHER_RESOURCES is a process-local pointer product; the
    background hook does not persist or consult AI/FAMULUS_REPO_ROOT.
    """
    resources = tmp_path / "immutable release" / "launcher-resources"
    monkeypatch.setenv("FAMULUS_LAUNCHER_RESOURCES", str(resources))
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "profiles" / "background_run_claude_setting.json")
        .read_text(encoding="utf-8")
    )
    command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    assert os.path.expandvars(command) == (
        f'python3 "{resources}/llmhooks/inject_dispatcher_context.py" --claude'
    )


def test_development_agent_launches_use_exact_live_resources_without_legacy_selectors_or_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, checkout, host_home = _write_active_development_launcher(tmp_path)
    unrelated = tmp_path / "foreign cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    for name in (
        "AI",
        "FAMULUS_REPO_ROOT",
        "ASSISTANT_DEFAULT",
        "COLLAB_DEFAULT",
        "COAUTHOR_DEFAULT",
        "BACKGROUND_RUN_DEFAULT",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    activated = build_interactive_environment(
        context,
        environ={"HOME": str(host_home), "PATH": os.environ.get("PATH", "")},
        platform=agent_module.sys.platform,
    )
    for name, value in activated.items():
        monkeypatch.setenv(name, value)
    launched: list[list[str]] = []
    monkeypatch.setattr(agent_module, "_launch_command", lambda command: launched.append(command) or 0)

    for name in ("assistant", "collab", "coauthor", "background_run"):
        assert agent_module.main(
            ["--runtime-root", str(context.paths.runtime_root), "--agent", name, "--local"]
        ) == 0

    assert [command[0] for command in launched] == ["claude"] * 4
    assert all(str(checkout / "profiles") in " ".join(command) for command in launched)
    assert os.environ["FAMULUS_LAUNCHER_RESOURCES"] == str(checkout)
