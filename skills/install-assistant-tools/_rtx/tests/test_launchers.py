from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _agent_launchers as launchers
else:
    import _agent_launchers as launchers
from .install_test_utils import assert_default_bin_dir_matches_famulus_paths


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / "skills" / "install-assistant-tools"
    source_bin = skill_dir / "_rtx/assets/bin"
    source_bin.mkdir(parents=True)
    for name in ["assistant", "collab", "coauthor", "background_run", "tmux-workspace",
                 "tw-break", "tw-join", "tw-monitor", "tw-help",
                 "_agent_launch.py", "assistant.bat", "collab.bat", "coauthor.bat",
                 "background_run.bat"]:
        content = "@echo off\r\nexit /b 0\r\n" if name.endswith(".bat") else "#!/bin/sh\necho stub\n"
        (source_bin / name).write_text(content)
        (source_bin / name).chmod(0o755)
    profiles_dir = repo_root / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "assistant.config.toml").write_text(
        'model_instructions_file = "agents/assistant.md"\nmodel = "gpt-5.4-mini"\n'
    )
    (profiles_dir / "assistant_claude_setting.json").write_text("{}")
    (profiles_dir / "background_run.config.toml").write_text(
        'model_instructions_file = "agents/background_run.md"\nmodel = "gpt-5.6-sol"\n'
    )
    (profiles_dir / "background_run_claude_setting.json").write_text("{}")
    (repo_root / "agents").mkdir()
    (repo_root / "agents" / "assistant.md").write_text(
        "---\nname: assistant\ndescription: test\n---\n\nYou are a test agent.\n"
    )
    (repo_root / "agents" / "background_run.md").write_text(
        "---\nname: background_run\ndescription: test\n---\n\nYou run unattended.\n"
    )
    return repo_root


def test_default_bin_dir_is_not_under_documents(tmp_path):
    assert_default_bin_dir_matches_famulus_paths(launchers.default_bin_dir, tmp_path)


def test_worker_root_in_plugin_mode_is_not_under_repo_workers(tmp_path):
    from officina.common.famulus_paths import resolve_famulus_paths

    repo_root = tmp_path / "repo"
    expected_root = resolve_famulus_paths(
        platform=sys.platform, home=tmp_path, environ=os.environ
    ).worker_root

    result = launchers.install_worker_dir(
        repo_root, "assistant", dry_run=True, mode="plugin", home=tmp_path
    )

    assert result == expected_root / "assistant"
    assert result != repo_root / "workers" / "assistant"
    assert "Documents" not in str(result)


def test_worker_root_in_development_mode_stays_under_repo_workers(tmp_path):
    repo_root = tmp_path / "repo"

    result = launchers.install_worker_dir(
        repo_root, "assistant", dry_run=True, mode="development", home=tmp_path
    )

    assert result == repo_root / "workers" / "assistant"


def test_run_installs_only_selected_agents(tmp_path):
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=codex_home,
        claude_home=claude_home,
        shell_rc=rc_file,
        default_llm="claude",
        dry_run=False,
    )

    if sys.platform == "win32":
        assert (bin_dir / "assistant.bat").is_file()
        assert not (bin_dir / "assistant").exists()
    else:
        assert (bin_dir / "assistant").is_file()
        assert not (bin_dir / "assistant").is_symlink()
    assert (repo_root / "workers" / "assistant").is_dir()
    assert not (repo_root / "workers" / "collab").exists()
    assert (codex_home / "assistant.config.toml").is_file()
    assert not (codex_home / "assistant.config.toml").is_symlink()


def test_run_copies_windows_agent_launcher_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    )
    monkeypatch.setattr(launchers, "verify_install", lambda *_args, **_kwargs: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=True,
    )

    assert not (bin_dir / "assistant").exists()

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    assert (bin_dir / "assistant.bat").is_file()
    assert not (bin_dir / "assistant").exists()
    assert not (bin_dir / "_agent_launch.py").exists()


def test_config_toml_gets_absolute_agent_path_not_codex_home_relative(tmp_path):
    """Codex resolves model_instructions_file relative to $CODEX_HOME by
    default — rewriting it to an absolute path means plugin-mode installs
    don't need $CODEX_HOME/agents wired at all for the launcher to work."""
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=codex_home,
        claude_home=claude_home,
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    codex_config = (codex_home / "assistant.config.toml").read_text()
    claude_config = (claude_home / "assistant.config.toml").read_text()
    expected_agent_path = str(repo_root / "agents" / "assistant.md")
    assert tomllib.loads(codex_config)["model_instructions_file"] == expected_agent_path
    assert tomllib.loads(claude_config)["model_instructions_file"] == expected_agent_path
    assert 'model_instructions_file = "agents/assistant.md"' not in codex_config
    assert 'model = "gpt-5.4-mini"' in codex_config  # other lines preserved


def test_config_toml_rewrite_treats_windows_backslashes_as_literal_path(tmp_path):
    src = tmp_path / "assistant.config.toml"
    dst = tmp_path / "codex" / "assistant.config.toml"
    windows_agent_path = Path(r"C:\Users\tester\Officina\agents\assistant.md")
    src.write_text(
        'model_instructions_file = "agents/assistant.md"\nmodel = "gpt-5.4-mini"\n',
        encoding="utf-8",
    )

    launchers.write_profile_config_with_absolute_agent_path(
        src.parent,
        dst.parent,
        "assistant",
        windows_agent_path,
        dry_run=False,
    )

    installed = dst.read_text(encoding="utf-8")
    parsed = tomllib.loads(installed)
    assert parsed["model_instructions_file"] == str(windows_agent_path)
    assert (
        r'model_instructions_file = "C:\\Users\\tester\\Officina\\agents\\assistant.md"'
        in installed
    )
    assert parsed["model"] == "gpt-5.4-mini"


def test_config_toml_preserves_existing_machine_local_copy(tmp_path):
    repo_root = _make_repo(tmp_path)
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True)
    (codex_home / "assistant.config.toml").write_text("model = \"user-edited\"\n")

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=tmp_path / "bin",
        codex_home=codex_home,
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    assert (codex_home / "assistant.config.toml").read_text() == 'model = "user-edited"\n'


def test_run_writes_durable_default_without_touching_shell_rc(tmp_path):
    if sys.platform == "win32":
        # famulus-skip: category=platform-contract; reason=this assertion names the POSIX Famulus config path; alternate=test_run_writes_windows_durable_default_without_registry_env
        pytest.skip("POSIX durable-config path assertion")
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=codex_home,
        claude_home=claude_home,
        shell_rc=rc_file,
        default_llm="codex",
        dry_run=False,
    )

    assert rc_file.read_text() == ""
    config = tmp_path / "home" / ".config" / "famulus" / "launchers.json"
    assert '"default_backend": "codex"' in config.read_text()


def test_run_writes_windows_durable_default_without_registry_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: r"C:\Python312\python.exe" if name == "python" else None,
    )
    monkeypatch.setattr(launchers, "verify_install", lambda *_args, **_kwargs: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=rc_file,
        default_llm="codex",
        dry_run=False,
    )

    assert rc_file.read_text() == ""
    config = tmp_path / "roaming" / "Famulus" / "launchers.json"
    assert '"default_backend": "codex"' in config.read_text()


def test_run_with_no_agents_installs_nothing(tmp_path):
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=[],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    assert not (bin_dir / "assistant").exists()


def test_run_verifies_installed_launchers(tmp_path, capsys):
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=["assistant"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    out = capsys.readouterr().out
    launcher_name = "assistant.bat" if sys.platform == "win32" else "assistant"
    assert f"OK:   {bin_dir / launcher_name} --help" in out
    # only the selected agent is verified, not the whole fixed list
    assert "collab" not in out


def test_verify_install_reports_fail_for_missing_launcher(tmp_path, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ok = launchers.verify_install(bin_dir, ["assistant"])

    assert ok is False
    launcher_name = "assistant.bat" if sys.platform == "win32" else "assistant"
    assert f"FAIL: {bin_dir / launcher_name} not found" in capsys.readouterr().out


def test_verify_install_uses_windows_wrapper_for_every_agent(tmp_path, monkeypatch):
    """Windows verifies every agent through its runnable batch wrapper."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "future_agent").write_text("#!/bin/sh\n")
    (bin_dir / "future_agent.bat").write_text("@echo off\r\nexit /b 0\r\n")
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(launchers.subprocess, "run", run)

    assert launchers.verify_install(bin_dir, ["future_agent"])
    assert calls == [[str(bin_dir / "future_agent.bat"), "--help"]]


def test_tw_selection_installs_the_complete_workspace_bundle(tmp_path):
    if sys.platform == "win32" or not hasattr(os, "symlink"):
        # famulus-skip: category=platform-contract; reason=tw symlink alias is Unix-only; alternate=Windows launcher copy tests cover supported Windows agents
        pytest.skip("tw symlink alias is Unix-only")
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=["tw"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    for command in ("tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help"):
        assert (bin_dir / command).is_symlink()
    assert (bin_dir / "tmux-workspace").resolve() == (bin_dir / "tw").resolve()


def test_tw_verification_fails_when_any_bundle_command_is_missing(tmp_path):
    if sys.platform == "win32" or not hasattr(os, "symlink"):
        # famulus-skip: category=platform-contract; reason=tw bundle is Unix-only; alternate=Windows launcher tests cover supported Windows commands
        pytest.skip("tw bundle is Unix-only")
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    launchers.run(
        repo_root=repo_root,
        agents=["tw"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        default_llm="claude",
        dry_run=False,
    )
    (bin_dir / "tw-help").unlink()

    assert not launchers.verify_install(bin_dir, ["tw"])


def test_launcher_closure_always_includes_background_run():
    """invoke-skill execs `background_run` by name, so installing the
    scheduler's launcher without that agent leaves every scheduled job dying
    with "Command not found"."""
    assert launchers.launcher_closure((), install_invoke_skill=True) == ("background_run",)


def test_launcher_closure_puts_background_run_first_no_duplicate():
    assert launchers.launcher_closure(
        ("collab", "background_run"), install_invoke_skill=True
    ) == ("background_run", "collab")


def test_launcher_closure_no_op_when_install_invoke_skill_false():
    assert launchers.launcher_closure(("collab",), install_invoke_skill=False) == ("collab",)


def test_install_with_no_agents_still_creates_background_run_launcher(tmp_path):
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=[],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
        install_invoke_skill=True,
    )

    # invoke-skill execs background_run, so that agent -- not assistant -- is
    # the one whose absence would break every scheduled job.
    if sys.platform == "win32":
        assert (bin_dir / "background_run.bat").exists()
    else:
        assert (bin_dir / "background_run").exists()


def test_tw_agent_is_skipped_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    repo_root = _make_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers.run(
        repo_root=repo_root,
        agents=["tw"],
        home=tmp_path / "home",
        bin_dir=bin_dir,
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        shell_rc=tmp_path / ".bashrc",
        default_llm="claude",
        dry_run=False,
    )

    assert not (bin_dir / "tmux-workspace").exists()
    assert not (bin_dir / "tw").exists()
