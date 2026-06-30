from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import setup_tools  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
