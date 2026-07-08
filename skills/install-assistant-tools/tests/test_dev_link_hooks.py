"""Unit tests for registry-driven Claude/Codex hook installation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dev_link  # noqa: E402


class SetupToolsHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT_DIR

    def test_install_claude_hooks_installs_registered_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)

            settings = json.loads((claude_home / "settings.local.json").read_text(encoding="utf-8"))
            session_start = settings["hooks"]["SessionStart"]
            commands = [hook["command"] for entry in session_start for hook in entry["hooks"]]

            self.assertTrue(commands)
            self.assertTrue(any("--claude" in command for command in commands))
            self.assertTrue(any("inject_dispatcher_context.py" in command and "llmhooks" in command for command in commands))

    def test_install_claude_hooks_replaces_legacy_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            claude_home.mkdir(parents=True)
            legacy_command = f'python3 "{self.repo_root / "hooks" / "inject_dispatcher_context.py"}"'
            settings_file = claude_home / "settings.local.json"
            settings_file.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"matcher": "startup|clear|compact", "hooks": [{"type": "command", "command": legacy_command}]}
                            ]
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)

            settings = json.loads(settings_file.read_text(encoding="utf-8"))
            commands = [hook["command"] for entry in settings["hooks"]["SessionStart"] for hook in entry["hooks"]]
            self.assertNotIn(legacy_command, commands)
            self.assertTrue(any("--claude" in command for command in commands))

    def test_install_codex_hooks_writes_managed_block_for_registered_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            dev_link.install_codex_hooks(codex_home, self.repo_root, dry_run=False)

            config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn(dev_link.HOOKS_BLOCK_BEGIN, config_text)
            self.assertIn("[[hooks.SessionStart]]", config_text)
            self.assertIn("--codex", config_text)
            self.assertIn("inject_dispatcher_context.py", config_text)
            self.assertIn("llmhooks", config_text)

    def test_install_codex_hooks_replaces_existing_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir(parents=True)
            config_file = codex_home / "config.toml"
            config_file.write_text(
                "user = 'keep'\n"
                f"{dev_link.HOOKS_BLOCK_BEGIN}\n"
                "[[hooks.SessionStart]]\n"
                'matcher = "startup|clear|compact"\n'
                "[[hooks.SessionStart.hooks]]\n"
                'type = "command"\n'
                f'command = "{self.repo_root / "hooks" / "inject_dispatcher_context.py"}"\n'
                f"{dev_link.HOOKS_BLOCK_END}\n",
                encoding="utf-8",
            )

            dev_link.install_codex_hooks(codex_home, self.repo_root, dry_run=False)

            config_text = config_file.read_text(encoding="utf-8")
            self.assertIn("user = 'keep'", config_text)
            self.assertIn("--codex", config_text)
            self.assertNotIn('/hooks/inject_dispatcher_context.py"', config_text)


if __name__ == "__main__":
    unittest.main()
