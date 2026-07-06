from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import setup_tools  # noqa: E402


@unittest.skipIf(sys.platform == "win32", "env.sh generation is POSIX-only by design")
class RecurringEnvScriptTests(unittest.TestCase):
    def test_writes_recurring_env_script_with_required_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_root = root / "repo"
            home = root / "home"
            bin_dir = home / "Documents" / "scripts" / "bin"
            env_script = repo_root / "skills" / "recurring-tasks" / "scripts" / "env.sh"

            setup_tools.install_recurring_tasks_env_script(
                repo_root=repo_root,
                home=home,
                bin_dir=bin_dir,
                dry_run=False,
            )

            self.assertTrue(env_script.is_file())
            content = env_script.read_text(encoding="utf-8")

        self.assertIn(str(bin_dir), content)
        self.assertIn(str(home / ".npm-global" / "bin"), content)
        self.assertIn(str(home / ".local" / "bin"), content)
        self.assertIn('export PATH="', content)

    def test_dry_run_does_not_write_recurring_env_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_root = root / "repo"
            home = root / "home"
            bin_dir = home / "Documents" / "scripts" / "bin"
            env_script = repo_root / "skills" / "recurring-tasks" / "scripts" / "env.sh"

            setup_tools.install_recurring_tasks_env_script(
                repo_root=repo_root,
                home=home,
                bin_dir=bin_dir,
                dry_run=True,
            )

            self.assertFalse(env_script.exists())


@unittest.skipIf(sys.platform == "win32", "systemd environment is Linux-only by design")
class AiAgentEnvLiveSessionGuardTests(unittest.TestCase):
    """install_ai_agent_env writes its environment.d file scoped to whatever
    `home` it's given, but must only mutate the *live* systemd user session
    (via `systemctl --user set-environment`) when `home` is the real $HOME.

    Regression coverage for a real incident: a sandboxed install run with an
    overridden --home pointing at a temporary directory was clobbering the
    real session's AI_AGENT_COMMAND_TEMPLATE with a path that vanished when
    the temp directory was cleaned up, silently breaking every scheduled job
    until the next manual fix.
    """

    def test_real_home_updates_live_systemd_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_real_home = Path(tmp) / "realhome"
            fake_real_home.mkdir()

            with patch.object(setup_tools.Path, "home", return_value=fake_real_home), \
                 patch.object(setup_tools.shutil, "which", return_value="/usr/bin/systemctl"), \
                 patch.object(setup_tools.subprocess, "run") as mock_run:
                mock_run.return_value.returncode = 0
                setup_tools.install_ai_agent_env(fake_real_home, dry_run=False)

            set_env_calls = [
                call for call in mock_run.call_args_list
                if call.args and "set-environment" in call.args[0]
            ]
            self.assertEqual(
                len(set_env_calls), 1,
                "expected exactly one systemctl --user set-environment call when home is the real $HOME",
            )

    def test_overridden_home_does_not_touch_live_systemd_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_home = Path(tmp) / "realhome"
            real_home.mkdir()
            sandbox_home = Path(tmp) / "sandbox-install-home"
            sandbox_home.mkdir()

            with patch.object(setup_tools.Path, "home", return_value=real_home), \
                 patch.object(setup_tools.shutil, "which", return_value="/usr/bin/systemctl"), \
                 patch.object(setup_tools.subprocess, "run") as mock_run:
                mock_run.return_value.returncode = 0
                setup_tools.install_ai_agent_env(sandbox_home, dry_run=False)

            set_env_calls = [
                call for call in mock_run.call_args_list
                if call.args and "set-environment" in call.args[0]
            ]
            self.assertEqual(
                len(set_env_calls), 0,
                "must not touch the live systemd session when home != real $HOME",
            )

            # The environment.d file itself should still be written, scoped
            # to the sandbox home — only the live-session mutation is guarded.
            env_file = sandbox_home / ".config" / "environment.d" / "20-ai-agent.conf"
            self.assertTrue(env_file.is_file())
            self.assertIn(str(sandbox_home), env_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
