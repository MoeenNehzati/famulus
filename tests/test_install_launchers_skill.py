from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills" / "install-launchers" / "_rtx" / "_agent_launchers.py"
SKILL = ROOT / "skills" / "install-launchers" / "SKILL.md"


def _runtime():
    spec = importlib.util.spec_from_file_location("task8_agent_launchers", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_install = lambda *_args, **_kwargs: True
    return module


def _plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "Selected Plugin Ω"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "profiles").mkdir()
    (plugin / "skills" / "install-launchers" / "_rtx" / "assets" / "bin").mkdir(
        parents=True
    )
    (plugin / "src" / "officina" / "launchers").mkdir(parents=True)
    (plugin / "src" / "officina" / "launchers" / "agent.py").write_text("# entry\n")
    for agent in ("assistant", "collab", "coauthor"):
        (plugin / "agents" / f"{agent}.md").write_text(f"# {agent}\n")
        (plugin / "profiles" / f"{agent}.config.toml").write_text(
            f'model_instructions_file = "agents/{agent}.md"\n'
        )
    for command in ("tmux-workspace", "tw-break", "tw-join", "tw-monitor", "tw-help"):
        path = plugin / "skills" / "install-launchers" / "_rtx" / "assets" / "bin" / command
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    return plugin


def _run(module, tmp_path: Path, plugin: Path, selected: list[str]):
    home = tmp_path / "home"
    return module.run(
        canonical_python=tmp_path / "Selected Python Ω" / "python",
        repo_root=plugin,
        agents=selected,
        home=home,
        bin_dir=home / "bin",
        codex_home=home / ".codex",
        claude_home=home / ".claude",
    )


@pytest.mark.parametrize("selected", ["assistant", "collab", "coauthor", "tw"])
def test_each_launcher_selection_is_independent(
    tmp_path: Path, selected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _runtime()
    plugin = _plugin(tmp_path)
    if selected == "tw":
        monkeypatch.setattr(
            module.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(["tmux", "-V"], 0),
        )

    assert _run(module, tmp_path, plugin, [selected])

    bin_dir = tmp_path / "home" / "bin"
    if selected == "tw" and sys.platform == "win32":
        assert not any(bin_dir.iterdir())
        return
    if selected == "tw":
        expected = {"tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help"}
    else:
        expected = {selected + ".bat" if sys.platform == "win32" else selected}
    assert {path.name for path in bin_dir.iterdir()} == expected


def test_blank_selection_is_effect_free(tmp_path: Path) -> None:
    module = _runtime()
    plugin = _plugin(tmp_path)
    home = tmp_path / "home"

    assert module.run(
        canonical_python=Path("/opt/python"),
        repo_root=plugin,
        agents=[],
        home=home,
    ) is None
    assert not home.exists()


def _task2_templates() -> dict[str, list[str]]:
    text = (ROOT / "skills" / "setup-python-environment" / "SKILL.md").read_text()
    return {
        name: json.loads(payload)
        for name, payload in re.findall(
            r"<!-- command:([a-z-]+) -->\n```json\n([^`]+)```", text
        )
    }


def test_empty_packages_execute_mandatory_task2_commands_without_pip_install(
    tmp_path: Path,
) -> None:
    templates = _task2_templates()
    selected_environment = tmp_path / "Selected Python Environment"
    subprocess.run(
        [sys.executable, "-m", "venv", str(selected_environment)], check=True
    )
    bin_dir = selected_environment / ("Scripts" if os.name == "nt" else "bin")
    environment = {**os.environ, "PATH": str(bin_dir)}
    calls: list[list[str]] = []

    def execute(name: str, canonical: str | None = None) -> str:
        argv = [
            canonical
            if token in ("${canonical_executable}", "${candidate}")
            else token
            for token in templates[name]
        ]
        assert "${selected_packages}" not in argv
        calls.append(argv)
        return subprocess.run(
            argv,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    venv_python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    first = json.loads(execute("candidate-fingerprint", str(venv_python)))
    canonical = first["executable"]
    assert Path(canonical).is_absolute()
    assert first["prefix"] != first["base_prefix"]
    assert execute("pip-check", canonical).startswith("pip ")
    assert execute("target-check", canonical) == "normal install target is writable"
    final = json.loads(execute("candidate-fingerprint", canonical))
    assert final == first

    plugin = Path("/tmp/Selected Plugin Ω")
    forwarded = {
        "canonical_python": Path(first["executable"]),
        "plugin_root": plugin,
        "agents": ["assistant"],
    }

    assert calls[0][0] == str(venv_python)
    assert [call[0] for call in calls[1:]] == [canonical, canonical, canonical]
    assert all(Path(call[0]).is_absolute() for call in calls)
    assert not any(call[0] in {"python", "python3", "py"} for call in calls)
    assert not any(
        call[1:3] == ["-m", "pip"] and "install" in call for call in calls
    )
    assert forwarded == {
        "canonical_python": Path(canonical),
        "plugin_root": plugin,
        "agents": ["assistant"],
    }
    text = SKILL.read_text()
    assert "successful\nno-ops" in text
    assert "do not invoke either `pip install` command" in text
    assert "empty" in text and "canonical" in text and "selected plugin root" in text


# famulus-skip: category=platform-contract; reason=POSIX launchers cannot execute on Windows; alternate=controlled Windows adapter test covers argv quoting
@pytest.mark.skipif(sys.platform == "win32", reason="executes the generated POSIX launcher")
def test_native_posix_installed_launcher_preserves_ordered_spaced_argv(tmp_path: Path) -> None:
    module = _runtime()
    plugin = _plugin(tmp_path)
    capture = tmp_path / "captured.json"
    canonical = tmp_path / "Selected Python Ω" / "python"
    canonical.parent.mkdir()
    canonical.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    canonical.chmod(0o755)
    home = tmp_path / "home"
    module.run(
        canonical_python=canonical,
        repo_root=plugin,
        agents=["assistant"],
        home=home,
        bin_dir=home / "bin",
    )

    result = subprocess.run(
        [home / "bin" / "assistant", "argument with spaces", "Ω"],
        env={**os.environ, "CAPTURE_FILE": str(capture)},
        check=False,
    )

    assert result.returncode == 0
    assert capture.read_text().splitlines() == [
        str(plugin / "src" / "officina" / "launchers" / "agent.py"),
        str(plugin),
        "assistant",
        "argument with spaces",
        "Ω",
    ]


def test_controlled_windows_cmd_adapter_preserves_ordered_spaced_argv(tmp_path: Path) -> None:
    module = _runtime()
    canonical = Path(r"C:\Program Files\Python Ω\python.exe")
    plugin = Path(r"C:\Users\Name\Plugin Root Ω")
    content = module.command_content(canonical, plugin, "assistant", windows=True)

    quoted = re.findall(r'"([^"]*)"', content.splitlines()[-1])
    assert quoted == [
        str(canonical),
        str(plugin / "src" / "officina" / "launchers" / "agent.py"),
        str(plugin),
        "assistant",
    ]
    assert content.rstrip().endswith('"assistant" %*')


def test_selected_repair_and_plugin_refresh_leave_unrelated_sentinels(tmp_path: Path) -> None:
    module = _runtime()
    first = _plugin(tmp_path / "first")
    second = _plugin(tmp_path / "second")
    home = tmp_path / "home"
    bin_dir = home / "bin"
    unrelated = bin_dir / "collab"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated sentinel")
    nonlauncher = home / "notes.txt"
    nonlauncher.write_bytes(b"non-launcher sentinel")

    class Records:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def record(self, _kind: str, *, path: str, **_fields) -> None:
            self.paths.append(path)

    records = Records()
    canonical_python = tmp_path / "Selected Python Ω" / "python"

    module.run(canonical_python=canonical_python, repo_root=first, agents=["assistant"], home=home, bin_dir=bin_dir, manifest=records)
    selected = bin_dir / ("assistant.bat" if sys.platform == "win32" else "assistant")
    selected.write_text("damaged", encoding="utf-8")
    module.run(canonical_python=canonical_python, repo_root=second, agents=["assistant"], home=home, bin_dir=bin_dir, manifest=records)

    refreshed = selected.read_text(encoding="utf-8")
    assert str(second) in refreshed
    assert str(first) not in refreshed
    assert unrelated.read_bytes() == b"unrelated sentinel"
    assert nonlauncher.read_bytes() == b"non-launcher sentinel"
    assert str(selected) in records.paths


def test_runtime_reuses_task7_and_has_no_selector_or_resolver() -> None:
    text = RUNTIME.read_text()
    assert "officina.common.command_files" in text
    executable_text = "\n".join(text.splitlines()[1:])
    for forbidden in ("current.json", "runtime_resolver", "launch.py", "shutil.which", "python3", '"py"'):
        assert forbidden not in executable_text


def test_skill_blueprint_has_single_instruction_owner_and_exact_edges() -> None:
    module = yaml.safe_load((ROOT / "skills" / "install-launchers" / "blueprint.yaml").read_text())
    gateway = yaml.safe_load((ROOT / "skills" / "install-launchers" / "blueprints" / "gateway.yaml").read_text())

    assert list(module["sources"]) == ["install-launchers.source.gateway"]
    assert len(gateway["interfaces"]) == 1
    edges = {dependency["source"] for dependency in gateway["dependencies"]}
    assert edges == {
        "setup-python-environment.source.gateway",
        "install-launchers._rtx.source.agent-launchers",
    }
    assert not (ROOT / "skills" / "install-launchers" / "instructions").exists()
