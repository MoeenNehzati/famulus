from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _ensure_agent_env as ensure_agent_env
else:
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


def _systemctl_calls(run_mock):
    return [call.args[0] for call in run_mock.call_args_list]


def test_skips_set_environment_when_the_user_manager_is_unreachable(tmp_path, monkeypatch):
    """Running setup from cron must not silently leave the variable unset.

    Before, an unreachable manager skipped set-environment with no message,
    so setup "succeeded" while the health check later reported
    AI_AGENT_COMMAND_TEMPLATE: not set.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ensure_agent_env.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(ensure_agent_env.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    messages: list[str] = []
    monkeypatch.setattr(ensure_agent_env, "log", messages.append)

    unreachable = mock.Mock(returncode=1, stdout=b"", stderr=b"Failed to connect to bus")
    with mock.patch.object(ensure_agent_env.subprocess, "run", return_value=unreachable) as run:
        ensure_agent_env.install_ai_agent_env(home, dry_run=False)

    calls = _systemctl_calls(run)
    assert not any("set-environment" in " ".join(c) for c in calls), (
        f"set-environment must not run against an unreachable manager: {calls}"
    )
    assert any("not reachable" in m for m in messages), (
        f"the skip must be reported, not silent: {messages}"
    )


def test_uses_a_derived_session_environment_for_systemctl(tmp_path, monkeypatch):
    """cron has no XDG_RUNTIME_DIR; the writer half must derive it too."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ensure_agent_env.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(ensure_agent_env.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()

    ok = mock.Mock(returncode=0, stdout=b"", stderr=b"")
    with mock.patch.object(ensure_agent_env.subprocess, "run", return_value=ok) as run:
        ensure_agent_env.install_ai_agent_env(home, dry_run=False)

    for call in run.call_args_list:
        env = call.kwargs.get("env")
        assert env is not None, "systemctl must run with an explicit session env"
        assert env.get("XDG_RUNTIME_DIR"), "XDG_RUNTIME_DIR must be derived when absent"
