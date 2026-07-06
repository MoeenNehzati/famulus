#!/usr/bin/env python3
"""Cross-platform isolated install test for the Codex plugin packaging.

This is intentionally stronger than a packaging smoke test:
- it confirms repo skills are invisible before install in a fresh CODEX_HOME
- installs the plugin through a temporary local marketplace
- checks every packaged skill and key shared assets
- runs the packaged install-assistant-tools installer into a fresh temp home
- verifies launcher/profile/symlink behavior from that installed copy

It does not call a model. It uses `codex debug prompt-input` for local prompt
construction checks.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from install_test_utils import (  # noqa: E402
    REPO_ROOT,
    can_create_symlink,
    codex_env,
    copy_repo_tree,
    expected_skills,
    launcher_path,
    python_test_env,
    read_json,
    run_command,
)


class CodexInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("codex") is None:
            raise unittest.SkipTest("codex CLI is not installed")

    def test_codex_plugin_install_and_installed_tool_bootstrap(self) -> None:
        plugin_name = read_json(REPO_ROOT / ".codex-plugin" / "plugin.json")["name"]
        marketplace_name = f"{plugin_name}-local-test"
        expected = expected_skills()

        with tempfile.TemporaryDirectory(prefix=f"{plugin_name}-codex-install-") as tmp:
            tmp_root = Path(tmp)
            env = python_test_env(tmp_root)
            run_command([sys.executable, str(REPO_ROOT / "skills" / "my-writing-skills" / "validators" / "skill_metadata.py")], env=env)
            run_command([sys.executable, str(REPO_ROOT / "validators" / "platform_neutral.py")], env=env)

            marketplace_root = tmp_root / "marketplace"
            repo_copy_root = marketplace_root / "plugins" / plugin_name
            codex_home = tmp_root / "codex-home"
            tmp_home = tmp_root / "home"
            workdir = tmp_root / "work"
            (marketplace_root / ".agents" / "plugins").mkdir(parents=True)
            codex_home.mkdir()
            tmp_home.mkdir()
            workdir.mkdir()
            copy_repo_tree(repo_copy_root)

            marketplace_manifest = marketplace_root / ".agents" / "plugins" / "marketplace.json"
            marketplace_manifest.write_text(
                json.dumps(
                    {
                        "name": marketplace_name,
                        "interface": {"displayName": marketplace_name},
                        "plugins": [
                            {
                                "name": plugin_name,
                                "source": {
                                    "source": "local",
                                    "path": f"./plugins/{plugin_name}",
                                },
                                "policy": {
                                    "installation": "AVAILABLE",
                                    "authentication": "ON_INSTALL",
                                },
                                "category": "Productivity",
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            plugin_env = codex_env(tmp_home, codex_home, tmp_root)
            baseline = run_command(
                ["codex", "debug", "prompt-input", "List available skills."],
                env=plugin_env,
                cwd=workdir,
            )
            baseline_prompt = json.loads(baseline.stdout)
            baseline_text = json.dumps(baseline_prompt)
            leaked = [
                name
                for name in expected
                if f"{plugin_name}:{name}" in baseline_text or f"- {name}:" in baseline_text
            ]
            self.assertEqual(leaked, [], f"Repo skills visible before install: {leaked}")

            run_command(
                ["codex", "plugin", "marketplace", "add", str(marketplace_root), "--json"],
                env=plugin_env,
            )
            install_result = run_command(
                ["codex", "plugin", "add", f"{plugin_name}@{marketplace_name}", "--json"],
                env=plugin_env,
            )
            installed_path = Path(json.loads(install_result.stdout)["installedPath"])
            self.assertTrue(str(installed_path).startswith(str(codex_home / "plugins" / "cache")))
            self.assertNotEqual(installed_path.resolve(), repo_copy_root.resolve())

            self.assertTrue((installed_path / "skills").is_dir())
            self.assertTrue((installed_path / "references").is_dir())

            missing_skills = [
                name
                for name in expected
                if not (installed_path / "skills" / name / "SKILL.md").is_file()
            ]
            self.assertEqual(missing_skills, [], f"Missing installed skills: {missing_skills}")

            required_paths = [
                installed_path / "AGENTS.md",
                installed_path / "CLAUDE.md",
                installed_path / "agents" / "assistant.md",
                installed_path / "agents" / "collab.md",
                installed_path / "agents" / "coauthor.md",
                installed_path / "profiles" / "assistant.config.toml",
                installed_path / "profiles" / "collab.config.toml",
                installed_path / "profiles" / "coauthor.config.toml",
                installed_path / "profiles" / "assistant_claude_setting.json",
                installed_path / "profiles" / "collab_claude_setting.json",
                installed_path / "profiles" / "coauthor_claude_setting.json",
                installed_path / "skills" / "install-assistant-tools" / "scripts" / "install.py",
                installed_path / "workers" / "assistant",
                installed_path / "workers" / "collab",
                installed_path / "workers" / "coauthor",
            ]
            missing_paths = [
                str(path.relative_to(installed_path)) for path in required_paths if not path.exists()
            ]
            self.assertEqual(missing_paths, [], f"Missing install assets: {missing_paths}")

            for skill_name in expected:
                prompt_result = run_command(
                    ["codex", "debug", "prompt-input", f"Use ${plugin_name}:{skill_name}."],
                    env=plugin_env,
                    cwd=workdir,
                )
                prompt = json.loads(prompt_result.stdout)
                visible_text = json.dumps(prompt)
                self.assertIn(
                    f"{plugin_name}:{skill_name}",
                    visible_text,
                    f"Skill not visible when explicitly invoked: {skill_name}",
                )

            if not can_create_symlink():
                self.skipTest("symlink creation is unavailable on this machine")

            install_home = tmp_root / "install-home"
            install_codex_home = tmp_root / "install-codex-home"
            install_claude_home = tmp_root / "install-claude-home"
            install_bin = tmp_root / "install-bin"
            install_home.mkdir()
            install_codex_home.mkdir()
            install_claude_home.mkdir()
            install_shell_rc = tmp_root / "install.bashrc"

            install_cmd = [
                sys.executable,
                str(installed_path / "skills" / "install-assistant-tools" / "scripts" / "install.py"),
                "--home",
                str(install_home),
                "--codex-home",
                str(install_codex_home),
                "--claude-home",
                str(install_claude_home),
                "--bin-dir",
                str(install_bin),
                "--default-llm",
                "codex",
            ]
            if sys.platform != "win32":
                install_cmd.extend(["--shell-rc", str(install_shell_rc), "--no-system-shell-rc"])

            install_env = python_test_env(
                tmp_root,
                {
                    "HOME": str(install_home),
                    "CODEX_HOME": str(install_codex_home),
                    "CLAUDE_HOME": str(install_claude_home),
                },
            )
            run_command(install_cmd, env=install_env)

            def expect_symlink(path: Path, target: Path) -> None:
                self.assertTrue(path.is_symlink(), f"Expected symlink: {path}")
                self.assertEqual(path.resolve(), target.resolve(), f"Wrong target for {path}")

            def expect_copy(path: Path, source: Path) -> None:
                # Profiles are copied, not symlinked: the tool writes
                # machine-local state back into its config file.
                self.assertTrue(path.is_file(), f"Expected file: {path}")
                self.assertFalse(path.is_symlink(), f"Expected copy, got symlink: {path}")
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    source.read_text(encoding="utf-8"),
                    f"Copy content mismatch for {path}",
                )

            codex_copies = {
                install_codex_home / "assistant.config.toml": installed_path / "profiles" / "assistant.config.toml",
                install_codex_home / "collab.config.toml": installed_path / "profiles" / "collab.config.toml",
                install_codex_home / "coauthor.config.toml": installed_path / "profiles" / "coauthor.config.toml",
            }
            claude_copies = {
                install_claude_home / "assistant.config.toml": installed_path / "profiles" / "assistant.config.toml",
                install_claude_home / "collab.config.toml": installed_path / "profiles" / "collab.config.toml",
                install_claude_home / "coauthor.config.toml": installed_path / "profiles" / "coauthor.config.toml",
            }

            codex_links = {
                install_codex_home / "skills": installed_path / "skills",
                install_codex_home / "references": installed_path / "references",
                install_codex_home / "agents": installed_path / "agents",
                install_codex_home / "AGENTS.md": (installed_path / "AGENTS.md") if (installed_path / "AGENTS.md").exists() else (installed_path / "CLAUDE.md"),
            }
            claude_links = {
                install_claude_home / "skills": installed_path / "skills",
                install_claude_home / "references": installed_path / "references",
                install_claude_home / "agents": installed_path / "agents",
                install_claude_home / "CLAUDE.md": installed_path / "CLAUDE.md",
                install_claude_home / "assistant_claude_setting.json": installed_path / "profiles" / "assistant_claude_setting.json",
                install_claude_home / "collab_claude_setting.json": installed_path / "profiles" / "collab_claude_setting.json",
                install_claude_home / "coauthor_claude_setting.json": installed_path / "profiles" / "coauthor_claude_setting.json",
            }
            bin_links = {
                install_bin / "_agent_launch.py": installed_path / "skills" / "install-assistant-tools" / "bin" / "_agent_launch.py",
                install_bin / "assistant": installed_path / "skills" / "install-assistant-tools" / "bin" / "assistant",
                install_bin / "collab": installed_path / "skills" / "install-assistant-tools" / "bin" / "collab",
                install_bin / "coauthor": installed_path / "skills" / "install-assistant-tools" / "bin" / "coauthor",
                install_bin / "tmux-workspace": installed_path / "skills" / "install-assistant-tools" / "bin" / "tmux-workspace",
                install_bin / "tw": installed_path / "skills" / "install-assistant-tools" / "bin" / "tmux-workspace",
                install_bin / "assistant.bat": installed_path / "skills" / "install-assistant-tools" / "bin" / "assistant.bat",
                install_bin / "collab.bat": installed_path / "skills" / "install-assistant-tools" / "bin" / "collab.bat",
                install_bin / "coauthor.bat": installed_path / "skills" / "install-assistant-tools" / "bin" / "coauthor.bat",
            }

            for mapping in (codex_links, claude_links, bin_links):
                for path, target in mapping.items():
                    expect_symlink(path, target)

            for mapping in (codex_copies, claude_copies):
                for path, source in mapping.items():
                    expect_copy(path, source)

            # dispatcher launcher: generated file (not symlink), runs
            # script_dispatcher from the repo with an install-time fallback path
            launcher = install_bin / "dispatcher"
            self.assertTrue(launcher.is_file(), "dispatcher launcher missing")
            self.assertFalse(launcher.is_symlink(), "dispatcher must be a generated file")
            self.assertTrue(os.access(launcher, os.X_OK), "dispatcher launcher not executable")
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertIn(f'AI="${{AI:-{installed_path}}}"', launcher_text)
            self.assertIn("script_dispatcher.cli", launcher_text)

            if sys.platform != "win32":
                shell_text = install_shell_rc.read_text(encoding="utf-8")
                self.assertIn(f'export PATH="{install_bin}:$PATH"', shell_text)
                self.assertIn("export ASSISTANT_DEFAULT=codex", shell_text)
                self.assertIn(f'export AI="{installed_path}"', shell_text)

            launcher_env = python_test_env(
                tmp_root,
                {
                    "HOME": str(install_home),
                    "CODEX_HOME": str(install_codex_home),
                    "CLAUDE_HOME": str(install_claude_home),
                    "ASSISTANT_DEFAULT": "codex",
                    "AI": str(installed_path),
                    "PATH": str(install_bin) + os.pathsep + os.environ.get("PATH", ""),
                },
            )
            for agent in ("assistant", "collab", "coauthor"):
                command = [
                    str(launcher_path(install_bin, agent)),
                    "debug",
                    "prompt-input",
                    f"Use ${plugin_name}:daily-plan.",
                ]
                prompt_result = run_command(command, env=launcher_env)
                prompt = json.loads(prompt_result.stdout)
                visible_text = json.dumps(prompt)
                self.assertIn(f"{plugin_name}:daily-plan", visible_text)
                self.assertIn(str(installed_path / "workers" / agent), visible_text)


if __name__ == "__main__":
    unittest.main()
