#!/usr/bin/env python3
"""Install test for the Codex plugin packaging sourced from real GitHub.

Runs the GitHub-shorthand form of README's Codex quick-install
(`codex plugin marketplace add MoeenNehzati/famulus`, `codex plugin add
famulus@nullkit`) against the actual public GitHub repo, not a local
checkout — same rationale as test_claude_github_install.py.

Scope is deliberately packaging-only: it does not re-run the
install-assistant-tools bootstrap/launcher-symlink phase that
test_codex_install.py covers from a local checkout without needing network.

It does not call a model. It uses `codex debug prompt-input` for local
prompt construction checks.
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
    codex_env,
    expected_skills,
    github_owner_repo,
    read_json,
    run_command,
)


class CodexGithubInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("codex") is None:
            # famulus-skip: category=capability-unavailable; reason=GitHub marketplace install test requires the codex CLI; alternate=local Codex install tests cover packaged install behavior
            raise unittest.SkipTest("codex CLI is not installed")

    def test_codex_plugin_install_from_github(self) -> None:
        plugin_name = read_json(REPO_ROOT / ".codex-plugin" / "plugin.json")["name"]
        marketplace_name = read_json(REPO_ROOT / ".agents" / "plugins" / "marketplace.json")["name"]
        owner_repo = github_owner_repo()
        expected = expected_skills()

        with tempfile.TemporaryDirectory(prefix=f"{plugin_name}-codex-github-install-") as tmp:
            tmp_root = Path(tmp)
            codex_home = tmp_root / "codex-home"
            tmp_home = tmp_root / "home"
            workdir = tmp_root / "work"
            codex_home.mkdir()
            tmp_home.mkdir()
            workdir.mkdir()

            plugin_env = codex_env(tmp_home, codex_home, tmp_root)
            baseline = run_command(
                ["codex", "debug", "prompt-input", "List available skills."],
                env=plugin_env,
                cwd=workdir,
            )
            baseline_text = json.dumps(json.loads(baseline.stdout))
            leaked = [
                name
                for name in expected
                if f"{plugin_name}:{name}" in baseline_text or f"- {name}:" in baseline_text
            ]
            self.assertEqual(leaked, [], f"Repo skills visible before install: {leaked}")

            run_command(
                ["codex", "plugin", "marketplace", "add", owner_repo, "--json"],
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
            self.assertNotEqual(installed_path.resolve(), REPO_ROOT.resolve())

            self.assertTrue((installed_path / "skills").is_dir())
            self.assertTrue((installed_path / "references").is_dir())

            missing_skills = [
                name
                for name in expected
                if not (installed_path / "skills" / name / "SKILL.md").is_file()
            ]
            self.assertEqual(missing_skills, [], f"Missing installed skills: {missing_skills}")

            required_paths = [
                # Codex's installed plugin cache exposes skills correctly but
                # may omit root-level symlink aliases from the marketplace
                # checkout (for example AGENTS.md -> CLAUDE.md). Assert the
                # real content/plugin assets instead of that cache-shape detail.
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
                prompt_text = json.dumps(json.loads(prompt_result.stdout))
                self.assertIn(
                    f"{plugin_name}:{skill_name}",
                    prompt_text,
                    f"Skill not visible when explicitly invoked: {skill_name}",
                )

            # ── Uninstall phase: plugin removal must clean up completely ──
            run_command(
                ["codex", "plugin", "remove", f"{plugin_name}@{marketplace_name}", "--json"],
                env=plugin_env,
            )
            self.assertFalse(
                installed_path.exists(),
                f"plugin cache dir left behind after removal: {installed_path}",
            )
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
