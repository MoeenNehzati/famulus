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
import subprocess
import sys
import tempfile
import tomllib
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
    python_test_env,
    read_json,
    run_command,
)


def platform_shell_command(command: str, args: list[str]) -> list[str]:
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline([command, *args])]
    return ["/bin/sh", "-c", 'exec "$@"', "launcher-smoke", command, *args]


class CodexInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("codex") is None:
            # famulus-skip: category=capability-unavailable; reason=packaged Codex install test requires the codex CLI; alternate=unit installer tests cover local installer behavior
            raise unittest.SkipTest("codex CLI is not installed")

    def test_codex_plugin_install_and_installed_tool_bootstrap(self) -> None:
        plugin_name = read_json(REPO_ROOT / ".codex-plugin" / "plugin.json")["name"]
        marketplace_name = f"{plugin_name}-local-test"
        expected = expected_skills()

        with tempfile.TemporaryDirectory(prefix=f"{plugin_name}-codex install-") as tmp:
            tmp_root = Path(tmp)
            env = python_test_env(tmp_root)
            run_command([sys.executable, str(REPO_ROOT / "skills" / "skill-maker" / "validators" / "skill_metadata.py")], env=env)
            run_command([sys.executable, str(REPO_ROOT / "validators" / "platform_neutral.py")], env=env)

            marketplace_root = tmp_root / "marketplace"
            repo_copy_root = marketplace_root / "plugins" / plugin_name
            codex_home = tmp_root / "codex home"
            tmp_home = tmp_root / "home dir"
            workdir = tmp_root / "work dir"
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
            # resolve both sides: on macOS /var/folders tempdirs are symlinks
            # into /private/var, and codex reports the resolved path
            self.assertTrue(
                str(installed_path.resolve()).startswith(
                    str((codex_home / "plugins" / "cache").resolve())
                )
            )
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
                installed_path / "skills" / "install-assistant-tools" / "_rtx" / "_phase_entry.py",
                # workers/ are deliberately NOT here: they are runtime dirs
                # created by the installer bootstrap, never plugin content
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
                # famulus-skip: category=capability-unavailable; reason=plugin install bootstrap verifies symlink behavior where supported; alternate=Windows copy launcher assertions run when symlinks are available
                self.skipTest("symlink creation is unavailable on this machine")

            install_home = tmp_root / "install home"
            install_codex_home = tmp_root / "install codex home"
            install_claude_home = tmp_root / "install claude home"
            install_bin = tmp_root / "install bin"
            install_home.mkdir()
            install_codex_home.mkdir()
            install_claude_home.mkdir()
            install_shell_rc = tmp_root / "install.bashrc"

            install_cmd = [
                sys.executable,
                str(installed_path / "skills" / "install-assistant-tools" / "_rtx" / "_phase_entry.py"),
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
                # This exercises plugin-mode install.py (no --dev-mode): only
                # scaffold + launchers run, not dev_link. --non-interactive is
                # required since this subprocess has no attached stdin.
                "--non-interactive",
                "--no-dev-mode",
                "--agents",
                "assistant,collab,coauthor,tw",
            ]
            if sys.platform != "win32":
                install_cmd.extend(["--shell-rc", str(install_shell_rc)])

            install_env = python_test_env(
                tmp_root,
                {
                    "HOME": str(install_home),
                    "CODEX_HOME": str(install_codex_home),
                    "CLAUDE_HOME": str(install_claude_home),
                },
            )
            run_command(install_cmd, env=install_env)

            # workers are created at install time by the bootstrap (runtime
            # dirs, not plugin content)
            for agent in ("assistant", "collab", "coauthor"):
                self.assertTrue(
                    (installed_path / "workers" / agent).is_dir(),
                    f"worker dir not created by installer bootstrap: {agent}",
                )

            def expect_symlink(path: Path, target: Path) -> None:
                self.assertTrue(path.is_symlink(), f"Expected symlink: {path}")
                self.assertEqual(path.resolve(), target.resolve(), f"Wrong target for {path}")

            def expect_file(path: Path) -> None:
                self.assertTrue(path.is_file(), f"Expected file: {path}")
                self.assertFalse(path.is_symlink(), f"Expected copy, got symlink: {path}")

            def expect_copy(path: Path, source: Path, agent: str) -> None:
                # Profiles are copied (not symlinked: the tool writes
                # machine-local state back into its config file), but
                # model_instructions_file is rewritten to an absolute path
                # pointing at the plugin's own bundled agents/<agent>.md —
                # so the copy is not byte-identical to the source on that
                # one line. This means Codex agent launches work in plugin
                # mode without needing $CODEX_HOME/agents wired at all.
                self.assertTrue(path.is_file(), f"Expected file: {path}")
                self.assertFalse(path.is_symlink(), f"Expected copy, got symlink: {path}")
                expected_agent_md = installed_path / "agents" / f"{agent}.md"
                installed = tomllib.loads(path.read_text(encoding="utf-8"))
                source_payload = tomllib.loads(source.read_text(encoding="utf-8"))
                self.assertEqual(installed["model_instructions_file"], str(expected_agent_md))
                self.assertEqual(
                    {k: v for k, v in installed.items() if k != "model_instructions_file"},
                    {k: v for k, v in source_payload.items() if k != "model_instructions_file"},
                )

            codex_copies = {
                install_codex_home / "assistant.config.toml": (installed_path / "profiles" / "assistant.config.toml", "assistant"),
                install_codex_home / "collab.config.toml": (installed_path / "profiles" / "collab.config.toml", "collab"),
                install_codex_home / "coauthor.config.toml": (installed_path / "profiles" / "coauthor.config.toml", "coauthor"),
            }
            claude_copies = {
                install_claude_home / "assistant.config.toml": (installed_path / "profiles" / "assistant.config.toml", "assistant"),
                install_claude_home / "collab.config.toml": (installed_path / "profiles" / "collab.config.toml", "collab"),
                install_claude_home / "coauthor.config.toml": (installed_path / "profiles" / "coauthor.config.toml", "coauthor"),
            }

            # NOTE: skills/references/agents/CLAUDE.md/AGENTS.md symlinks are
            # dev_link.py's job, not run here — this install_cmd uses
            # --no-dev-mode (plugin mode), and plugin-mode skill/reference
            # visibility already comes from the plugin loader itself (already
            # confirmed above via `codex debug prompt-input`, before install.py
            # ever ran). Only scaffold + launchers run in this test.
            claude_links = {
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

            for path, target in claude_links.items():
                expect_symlink(path, target)

            if sys.platform == "win32":
                windows_bin_files = [
                    install_bin / "_agent_launch.py",
                    install_bin / "assistant",
                    install_bin / "collab",
                    install_bin / "coauthor",
                    install_bin / "assistant.bat",
                    install_bin / "collab.bat",
                    install_bin / "coauthor.bat",
                ]
                for path in windows_bin_files:
                    expect_file(path)
                self.assertFalse((install_bin / "tmux-workspace").exists())
                self.assertFalse((install_bin / "tw").exists())
            else:
                for path, target in bin_links.items():
                    expect_symlink(path, target)

            for mapping in (codex_copies, claude_copies):
                for path, (source, agent) in mapping.items():
                    expect_copy(path, source, agent)

            # dispatcher launcher: generated file (not symlink), runs
            # officina.dispatcher from the repo with an install-time fallback
            # path. Windows has a separate .bat launcher.
            if sys.platform == "win32":
                self.assertFalse((install_bin / "dispatcher").exists())
                launcher = install_bin / "dispatcher.bat"
                self.assertTrue(launcher.is_file(), "dispatcher launcher missing")
                launcher_text = launcher.read_text(encoding="utf-8")
                self.assertIn("officina.dispatcher.cli", launcher_text)
                self.assertIn(str(installed_path), launcher_text)
            else:
                launcher = install_bin / "dispatcher"
                self.assertTrue(launcher.is_file(), "dispatcher launcher missing")
                self.assertFalse(launcher.is_symlink(), "dispatcher must be a generated file")
                self.assertTrue(os.access(launcher, os.X_OK), "dispatcher launcher not executable")
                launcher_text = launcher.read_text(encoding="utf-8")
                self.assertIn(f"os.environ.get('AI', '{installed_path}')", launcher_text)
                self.assertIn("officina.dispatcher.cli", launcher_text)

            if sys.platform != "win32":
                shell_text = install_shell_rc.read_text(encoding="utf-8")
                self.assertIn(f'export PATH="{install_bin}:$PATH"', shell_text)
                self.assertIn("export ASSISTANT_DEFAULT=codex", shell_text)
                # $AI is dev_link.py's export, not run here (--no-dev-mode) —
                # plugin-mode dispatcher already has its own baked-in
                # repo_root fallback (asserted above), so it doesn't need it.
                self.assertNotIn("export AI=", shell_text)

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
            run_command(
                platform_shell_command("dispatcher", ["--help"]),
                env=launcher_env,
                cwd=workdir,
            )
            for agent in ("assistant", "collab", "coauthor"):
                command = platform_shell_command(
                    agent,
                    [
                    "debug",
                    "prompt-input",
                    f"Use ${plugin_name}:daily-plan.",
                    ],
                )
                prompt_result = run_command(command, env=launcher_env)
                prompt = json.loads(prompt_result.stdout)
                visible_text = json.dumps(prompt)
                self.assertIn(f"{plugin_name}:daily-plan", visible_text)
                # visible_text is json.dumps output: backslashes in Windows
                # paths are escaped, so compare in JSON-escaped space
                worker_dir_json = json.dumps(str(installed_path / "workers" / agent))[1:-1]
                self.assertIn(worker_dir_json, visible_text)

            # ── Uninstall phase: plugin removal must clean up completely ──
            run_command(
                ["codex", "plugin", "remove", f"{plugin_name}@{marketplace_name}", "--json"],
                env=plugin_env,
            )
            self.assertFalse(
                installed_path.exists(),
                f"plugin cache dir left behind after removal: {installed_path}",
            )
            # skills must no longer be visible to codex
            post_remove = run_command(
                ["codex", "debug", "prompt-input", "List available skills."],
                env=plugin_env,
                cwd=workdir,
            )
            post_text = json.dumps(json.loads(post_remove.stdout))
            still_visible = [
                name
                for name in expected
                if f"{plugin_name}:{name}" in post_text
            ]
            self.assertEqual(still_visible, [], f"skills visible after removal: {still_visible}")

            run_command(
                ["codex", "plugin", "marketplace", "remove", marketplace_name, "--json"],
                env=plugin_env,
            )
            marketplace_list = run_command(
                ["codex", "plugin", "marketplace", "list", "--json"], env=plugin_env
            )
            self.assertNotIn(marketplace_name, marketplace_list.stdout)


if __name__ == "__main__":
    unittest.main()
