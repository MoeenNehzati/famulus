#!/usr/bin/env python3
"""Install test for the Claude plugin packaging sourced from real GitHub.

This runs the exact commands from README's "Recommended: plugin install"
section (`/plugin marketplace add MoeenNehzati/famulus`, `/plugin install
famulus@nullkit`) against the actual public GitHub repo, not a local path —
catching packaging problems that only show up when installing what's really
published (untracked files, symlinks that don't survive a clone, etc.).

This is a standing health check on the currently-published default branch,
not a per-PR diff gate: Claude's `owner/repo` marketplace source has no
ref-pinning option, so it always resolves whatever is on GitHub's default
branch. If GitHub is unreachable, this test fails like any other command
failure — there is no network-availability skip.

It does not call a model.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from install_test_utils import (  # noqa: E402
    REPO_ROOT,
    claude_env,
    expected_skills,
    github_owner_repo,
    read_json,
    run_command,
)


class ClaudeGithubInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("claude") is None:
            raise unittest.SkipTest("claude CLI is not installed")

    def test_claude_plugin_marketplace_install_from_github(self) -> None:
        expected = expected_skills()
        plugin_name = read_json(REPO_ROOT / ".claude-plugin" / "plugin.json")["name"]
        marketplace_name = read_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")["name"]
        owner_repo = github_owner_repo()

        with tempfile.TemporaryDirectory(prefix=f"{plugin_name}-claude-github-install-") as tmp:
            tmp_root = Path(tmp)
            home = tmp_root / "home"
            claude_home = home / ".claude"
            home.mkdir()
            plugin_env = claude_env(home, claude_home, tmp_root)

            before_install = run_command(["claude", "plugins", "list"], env=plugin_env)
            self.assertNotIn(f"{plugin_name}@{marketplace_name}", before_install.stdout)

            run_command(
                ["claude", "plugins", "marketplace", "add", owner_repo],
                env=plugin_env,
            )

            marketplace_list = run_command(["claude", "plugins", "marketplace", "list"], env=plugin_env)
            self.assertIn(marketplace_name, marketplace_list.stdout)

            known_marketplaces = json.loads(
                (claude_home / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8")
            )
            self.assertEqual(known_marketplaces[marketplace_name]["source"]["source"], "github")
            self.assertEqual(known_marketplaces[marketplace_name]["source"]["repo"], owner_repo)

            run_command(["claude", "plugins", "install", f"{plugin_name}@{marketplace_name}"], env=plugin_env)
            plugins_list = run_command(["claude", "plugins", "list"], env=plugin_env)
            self.assertIn(f"{plugin_name}@{marketplace_name}", plugins_list.stdout)

            installed_plugins = json.loads(
                (claude_home / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
            )
            installs = installed_plugins["plugins"][f"{plugin_name}@{marketplace_name}"]
            self.assertEqual(len(installs), 1)
            installed_path = Path(installs[0]["installPath"])
            # resolve both sides: on macOS /var/folders tempdirs are symlinks
            # into /private/var, and the CLI may report the resolved path
            self.assertTrue(
                str(installed_path.resolve()).startswith(
                    str((claude_home / "plugins" / "cache").resolve())
                )
            )
            self.assertNotEqual(installed_path.resolve(), REPO_ROOT.resolve())

            missing_skills = [
                skill_name
                for skill_name in expected
                if not (installed_path / "skills" / skill_name / "SKILL.md").is_file()
            ]
            self.assertEqual(missing_skills, [], f"Missing installed Claude skills: {missing_skills}")

            required_paths = [
                installed_path / ".claude-plugin" / "plugin.json",
                installed_path / ".claude-plugin" / "marketplace.json",
                installed_path / "CLAUDE.md",
                installed_path / "references",
                installed_path / "hooks" / "hooks.json",
                installed_path / "hooks" / "inject_dispatcher_context.py",
                installed_path / "llmhooks" / "inject_dispatcher_context.py",
                installed_path / "agents" / "assistant.md",
                installed_path / "agents" / "collab.md",
                installed_path / "agents" / "coauthor.md",
                installed_path / "skills" / "install-assistant-tools" / "_rtx" / "_phase_entry.py",
            ]
            missing_paths = [
                str(path.relative_to(installed_path)) for path in required_paths if not path.exists()
            ]
            self.assertEqual(missing_paths, [], f"Missing installed Claude plugin assets: {missing_paths}")

            details = run_command(["claude", "plugins", "details", f"{plugin_name}@{marketplace_name}"], env=plugin_env)
            details_text = details.stdout
            self.assertIn(f"Skills ({len(expected)})", details_text)
            self.assertIn("Agents (3)", details_text)
            for skill_name in expected:
                self.assertIn(skill_name, details_text)
            for agent_name in ("assistant", "collab", "coauthor"):
                self.assertIn(agent_name, details_text)

            # Claude plugin mode should fire SessionStart and emit our hook context
            session = run_command(
                [
                    "claude",
                    "-p",
                    "hello",
                    "--output-format",
                    "stream-json",
                    "--include-hook-events",
                    "--verbose",
                    "--allowedTools",
                    "",
                ],
                env=plugin_env,
                check=False,
            )
            self.assertNotEqual(session.returncode, 0)  # temp HOME is unauthenticated
            lines = [json.loads(line) for line in session.stdout.splitlines() if line.strip()]
            hook_started = [
                item for item in lines
                if item.get("type") == "system" and item.get("subtype") == "hook_started"
            ]
            hook_responses = [
                item for item in lines
                if item.get("type") == "system" and item.get("subtype") == "hook_response"
            ]
            self.assertTrue(hook_started, "Expected SessionStart hook_started event in Claude plugin mode")
            self.assertTrue(hook_responses, "Expected SessionStart hook_response event in Claude plugin mode")
            self.assertTrue(any(item.get("hook_event") == "SessionStart" for item in hook_started))
            self.assertTrue(
                any("Skill System" in json.dumps(item) for item in hook_responses),
                "Expected dispatcher-context payload in Claude plugin hook response",
            )

            # ── Uninstall phase: plugin removal must clean up completely ──
            run_command(
                ["claude", "plugins", "uninstall", f"{plugin_name}@{marketplace_name}"],
                env=plugin_env,
            )
            after_uninstall = run_command(["claude", "plugins", "list"], env=plugin_env)
            self.assertNotIn(f"{plugin_name}@{marketplace_name}", after_uninstall.stdout)

            installed_plugins = json.loads(
                (claude_home / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                f"{plugin_name}@{marketplace_name}", installed_plugins.get("plugins", {})
            )

            run_command(
                ["claude", "plugins", "marketplace", "remove", marketplace_name],
                env=plugin_env,
            )
            after_marketplace = run_command(
                ["claude", "plugins", "marketplace", "list"], env=plugin_env
            )
            self.assertNotIn(marketplace_name, after_marketplace.stdout)
            known_marketplaces = json.loads(
                (claude_home / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(marketplace_name, known_marketplaces)


if __name__ == "__main__":
    unittest.main()
