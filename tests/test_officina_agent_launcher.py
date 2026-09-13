from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from officina.common.famulus_paths import resolve_famulus_paths
from officina.launchers import agent as agent_module
from officina.launchers.agent import (
    LauncherConfigurationError,
    ensure_launcher_configuration,
    load_launcher_configuration,
    select_backend,
)


class RecordingManifest:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, object]]] = []

    def record(self, kind: str, **fields: object) -> None:
        self.entries.append((kind, fields))


def test_launcher_module_imports_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """Catch a stale import of the deleted install package."""
    repo_root = Path(__file__).resolve().parents[1]
    environ = os.environ.copy()
    environ["PYTHONPATH"] = str(repo_root / "src")

    result = subprocess.run(
        [sys.executable, "-c", "import officina.launchers.agent"],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_launcher_package_preserves_live_lazy_export() -> None:
    from officina import launchers

    assert launchers.build_agent_command.__module__ == "officina.launchers.agent"


def test_launcher_configuration_is_created_atomically_with_manifest_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacements: list[Path] = []
    real_replace = agent_module.atomic_replace_bytes

    def recording_replace(path: Path, *args: object, **kwargs: object) -> None:
        replacements.append(path)
        real_replace(path, *args, **kwargs)

    monkeypatch.setattr(agent_module, "atomic_replace_bytes", recording_replace)
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


def test_agent_specific_override_precedes_assistant_override(tmp_path: Path) -> None:
    configured = ensure_launcher_configuration(
        config_root=tmp_path, default_backend="claude"
    )

    assert select_backend(
        agent="collab",
        configuration=configured,
        environ={"COLLAB_DEFAULT": "claude", "ASSISTANT_DEFAULT": "codex"},
    ) == "claude"


def test_selected_plugin_launch_preserves_spaced_argument_and_drops_state_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch pointer fallback or argv splitting in the Task 8 launcher route."""
    plugin_root = Path(agent_module.__file__).resolve().parents[3]
    home = tmp_path / "home with spaces"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ASSISTANT_LOGS", str(tmp_path / "foreign logs"))
    paths = resolve_famulus_paths(
        platform=agent_module.sys.platform,
        home=home,
        environ=os.environ,
    )
    ensure_launcher_configuration(
        config_root=paths.config_root,
        default_backend="codex",
    )
    launched: list[list[str]] = []
    monkeypatch.setattr(
        agent_module,
        "_launch_command",
        lambda command: launched.append(command) or 0,
    )

    assert agent_module.main(
        [str(plugin_root), "assistant", "--local", "--codex", "argument with spaces"]
    ) == 0

    assert launched[0][0] == "codex"
    assert launched[0][-1] == "argument with spaces"
    assert os.environ["FAMULUS_LAUNCHER_RESOURCES"] == str(plugin_root)
    assert "ASSISTANT_LOGS" not in agent_module._launch_environment(os.environ)


def test_runtime_root_route_is_rejected() -> None:
    """Catch accidental restoration of the deleted pointer/context launch branch."""
    with pytest.raises(LauncherConfigurationError, match="selected plugin root"):
        agent_module.main(["--runtime-root", "/tmp/obsolete", "--agent", "assistant"])


def test_background_hook_uses_common_python_and_process_local_plugin_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "selected plugin with spaces"
    monkeypatch.setenv("FAMULUS_LAUNCHER_RESOURCES", str(resources))
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "profiles" / "background_run_claude_setting.json")
        .read_text(encoding="utf-8")
    )
    hook = payload["hooks"]["SessionStart"][0]["hooks"][0]

    assert hook["command"] == "python"
    assert [os.path.expandvars(argument) for argument in hook["args"]] == [
        f"{resources}/llmhooks/inject_dispatcher_context.py",
        "--claude",
    ]
