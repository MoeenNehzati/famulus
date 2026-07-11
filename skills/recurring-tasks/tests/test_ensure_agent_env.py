from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rtx"))

import _ensure_agent_env as ensure_agent_env


def test_does_not_write_legacy_agent_env_shell_script(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    (repo_root / "skills" / "recurring-tasks" / "_rtx").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"

    ensure_agent_env.run(repo_root=repo_root, home=home, bin_dir=bin_dir, dry_run=False)

    env_script = repo_root / "skills" / "recurring-tasks" / "_rtx" / "_agent_env.sh"
    assert not env_script.exists()


def test_writes_systemd_environment_file_scoped_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ensure_agent_env.shutil, "which", lambda name: None)  # no systemctl in test env
    repo_root = tmp_path / "repo"
    (repo_root / "skills" / "recurring-tasks" / "_rtx").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    ensure_agent_env.run(repo_root=repo_root, home=home, bin_dir=tmp_path / "bin", dry_run=False)

    env_file = home / ".config" / "environment.d" / "20-ai-agent.conf"
    assert env_file.is_file()
    assert "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}" in env_file.read_text()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    repo_root = tmp_path / "repo"
    (repo_root / "skills" / "recurring-tasks" / "_rtx").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    ensure_agent_env.run(repo_root=repo_root, home=home, bin_dir=tmp_path / "bin", dry_run=True)

    assert not (home / ".config" / "environment.d" / "20-ai-agent.conf").exists()
