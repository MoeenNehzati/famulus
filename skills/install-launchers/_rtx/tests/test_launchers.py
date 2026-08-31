from __future__ import annotations

import importlib.util
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1] / "_agent_launchers.py"


def _runtime():
    spec = importlib.util.spec_from_file_location("install_launchers_runtime", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_install = lambda *_args, **_kwargs: True
    return module


def _plugin(tmp_path: Path) -> Path:
    root = tmp_path / "Plugin Root"
    (root / "agents").mkdir(parents=True)
    (root / "profiles").mkdir()
    assets = root / "skills" / "install-launchers" / "_rtx" / "assets" / "bin"
    assets.mkdir(parents=True)
    for name in ("tmux-workspace", "tw-break", "tw-join", "tw-monitor", "tw-help"):
        path = assets / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    for agent in ("assistant", "collab", "coauthor"):
        (root / "agents" / f"{agent}.md").write_text(f"# {agent}\n")
        (root / "profiles" / f"{agent}.config.toml").write_text(
            f'model_instructions_file = "agents/{agent}.md"\n'
        )
    return root


def test_selected_profile_and_worker_use_selected_plugin_root(tmp_path: Path) -> None:
    launchers = _runtime()
    plugin = _plugin(tmp_path)
    home = tmp_path / "home"
    launchers.run(
        canonical_python=Path("/opt/python"),
        repo_root=plugin,
        agents=["assistant"],
        home=home,
        bin_dir=home / "bin",
        codex_home=home / ".codex",
        claude_home=home / ".claude",
        mode="plugin",
    )

    assert str(plugin / "agents" / "assistant.md") in (
        home / ".codex" / "assistant.config.toml"
    ).read_text()
    assert launchers.worker_root_for_mode(
        "plugin", plugin, home, environ={}
    ) != plugin / "workers"


def test_tw_checks_tmux_only_when_selected(tmp_path: Path, monkeypatch) -> None:
    launchers = _runtime()
    plugin = _plugin(tmp_path)
    calls: list[list[str]] = []

    def record(argv, **_kwargs):
        calls.append([str(item) for item in argv])
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(launchers.subprocess, "run", record)
    home = tmp_path / "home"
    launchers.run(
        canonical_python=Path("/opt/python"), repo_root=plugin,
        agents=["assistant"], home=home, bin_dir=home / "bin",
    )
    assert calls == []

    launchers.run(
        canonical_python=Path("/opt/python"), repo_root=plugin,
        agents=["tw"], home=home, bin_dir=home / "tw-bin",
    )
    assert calls == [["tmux", "-V"]]
    assert {path.name for path in (home / "tw-bin").iterdir()} == {
        "tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help"
    }


def test_deduplicated_selection_does_not_add_another_launcher(tmp_path: Path) -> None:
    launchers = _runtime()
    plugin = _plugin(tmp_path)
    home = tmp_path / "home"
    launchers.run(
        canonical_python=Path("/opt/python"), repo_root=plugin,
        agents=["assistant", "assistant"], home=home, bin_dir=home / "bin",
    )
    assert {path.name for path in (home / "bin").iterdir()} == {"assistant"}
