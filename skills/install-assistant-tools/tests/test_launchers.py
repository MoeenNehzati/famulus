from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import launchers


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    skill_dir = repo_root / "skills" / "install-assistant-tools"
    source_bin = skill_dir / "bin"
    source_bin.mkdir(parents=True)
    for name in ["assistant", "collab", "coauthor", "tmux-workspace", "_agent_launch.py",
                 "assistant.bat", "collab.bat", "coauthor.bat"]:
        (source_bin / name).write_text("#!/bin/sh\necho stub\n")
        (source_bin / name).chmod(0o755)
    profiles_dir = repo_root / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "assistant.config.toml").write_text(
        'model_instructions_file = "agents/assistant.md"\nmodel = "gpt-5.4-mini"\n'
    )
    (profiles_dir / "assistant_claude_setting.json").write_text("{}")
    (repo_root / "agents").mkdir()
    (repo_root / "agents" / "assistant.md").write_text(
        "---\nname: assistant\ndescription: test\n---\n\nYou are a test agent.\n"
    )
    return repo_root


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

    assert (bin_dir / "assistant").is_symlink()
    assert (repo_root / "workers" / "assistant").is_dir()
    assert not (repo_root / "workers" / "collab").exists()
    assert (codex_home / "assistant.config.toml").is_file()
    assert not (codex_home / "assistant.config.toml").is_symlink()


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
    expected_line = f'model_instructions_file = "{repo_root / "agents" / "assistant.md"}"'
    assert expected_line in codex_config
    assert expected_line in claude_config
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


def test_run_sets_assistant_default_in_rc(tmp_path):
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

    content = rc_file.read_text()
    assert "export ASSISTANT_DEFAULT=codex" in content
    assert 'export PATH="' not in content  # launchers does not own PATH


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
    assert f"OK:   {bin_dir / 'assistant'} --help" in out
    # only the selected agent is verified, not the whole fixed list
    assert "collab" not in out


def test_verify_install_reports_fail_for_missing_launcher(tmp_path, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ok = launchers.verify_install(bin_dir, ["assistant"])

    assert ok is False
    assert f"FAIL: {bin_dir / 'assistant'} not found" in capsys.readouterr().out


def test_tw_agent_links_both_tmux_workspace_and_tw_alias(tmp_path):
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

    assert (bin_dir / "tmux-workspace").is_symlink()
    assert (bin_dir / "tw").is_symlink()
    assert (bin_dir / "tmux-workspace").resolve() == (bin_dir / "tw").resolve()
