#!/usr/bin/env python3
"""Regression tests for blueprint sync and exported script dispatch."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT_TEMPLATE = REPO_ROOT / "references" / "blueprint" / "template.yaml"
README = REPO_ROOT / "README.md"


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class SkillBlueprintToolTests(unittest.TestCase):
    def run_cmd(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_blueprints_are_in_sync(self) -> None:
        result = self.run_cmd("skills/my-writing-skills/scripts/sync_skill_blueprints.py", "--check")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_blueprint_template_exists_and_is_comment_rich(self) -> None:
        self.assertTrue(BLUEPRINT_TEMPLATE.exists(), "reference blueprint template is missing")
        text = BLUEPRINT_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("interface_version:", text)
        self.assertIn("script_interfaces:", text)
        self.assertIn("patterns:", text)
        self.assertIn("allow_all_skills:", text)
        self.assertGreaterEqual(text.count("#"), 25, "template should remain heavily commented")

    def test_readme_covers_blueprint_handoff_basics(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("## Blueprint Migration", text)
        self.assertIn("references/blueprint", text)
        self.assertIn("tools/invoke_skill_export.py", text)
        self.assertIn("python3 skills/my-writing-skills/scripts/sync_skill_blueprints.py", text)
        self.assertIn(".githooks/skill/check-blueprints", text)
        self.assertIn("list-manager", text)
        self.assertIn("daily-plan", text)

    def test_blueprint_hook_check_passes(self) -> None:
        result = self.run_cmd("skills/my-writing-skills/validators/blueprints.py")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_boundary_hook_check_passes(self) -> None:
        result = self.run_cmd("skills/my-writing-skills/validators/boundaries.py")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_dispatcher_allows_declared_export(self) -> None:
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager",
            "update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/todo-updates.yaml",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["cwd"], str(REPO_ROOT / "skills" / "list-manager"))
        self.assertEqual(
            payload["command"],
            [
                "python3",
                "scripts/lists.py",
                "update",
                "/tmp/todo.yaml",
                "--file",
                "/tmp/todo-updates.yaml",
            ],
        )

    def test_dispatcher_allows_export_without_caller_context(self) -> None:
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "list-manager",
            "read-list",
            "/tmp/todo.yaml",
            "state=incomplete",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["script_interface"], "read-list")

    def test_dispatcher_rejects_private_interface_for_dependency(self) -> None:
        """Test that internal-only interfaces cannot be used by dependent skills."""
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager",
            "migrate-markdown",  # This is internal-only
            "/tmp/input.md",
            "/tmp/output.yaml",
            "todo",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("internal-only", result.stderr)

    def test_dispatcher_allows_private_interface_for_owner(self) -> None:
        """Test that the owning skill can use non-exported interfaces."""
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "list-manager",
            "list-manager",
            "update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/updates.yaml",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("pattern", payload)

    def test_dispatcher_enforces_required_flags(self) -> None:
        """Test that if a pattern requires flags, they must be present."""
        # update-list file-mode requires --file flag.
        # Calling with --file flag should work:
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager",
            "update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/updates.yaml",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # Calling with both --file and a forbidden flag should fail:
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager",
            "update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/updates.yaml",
            "--stdin",  # stdin-batch forbids --file
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match any declared pattern", result.stderr)

    def test_dispatcher_rejects_unexpected_flag(self) -> None:
        result = self.run_cmd(
            "tools/invoke_skill_export.py",
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager",
            "update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/todo-updates.yaml",
            "--bogus",
            "/tmp/value",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match any declared pattern", result.stderr)

    def test_dispatcher_rejects_stdin_when_pattern_disallows_it(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/invoke_skill_export.py",
                "--dry-run",
                "--stdin",
                "--caller-skill",
                "daily-plan",
                "list-manager",
                "update-list",
                "/tmp/todo.yaml",
                "--file",
                "/tmp/todo-updates.yaml",
            ],
            cwd=REPO_ROOT,
            input="x",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match any declared pattern", result.stderr)

    def test_contract_block_is_injected_after_frontmatter(self) -> None:
        sync_module = load_module("sync_skill_blueprints", REPO_ROOT / "skills" / "my-writing-skills" / "scripts" / "sync_skill_blueprints.py")
        contract_block = "<!-- BEGIN BLUEPRINT CONTRACT -->\nInjected\n<!-- END BLUEPRINT CONTRACT -->\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "SKILL.md"
            skill_file.write_text(
                "---\nname: example-skill\ndescription: Use when testing.\n---\n\nWhen this skill is used, begin with:\n",
                encoding="utf-8",
            )
            updated = sync_module.sync_contract_block(skill_file, contract_block)

        expected = (
            "---\nname: example-skill\ndescription: Use when testing.\n---\n\n"
            "<!-- BEGIN BLUEPRINT CONTRACT -->\nInjected\n<!-- END BLUEPRINT CONTRACT -->\n"
            "When this skill is used, begin with:\n"
        )
        self.assertEqual(updated, expected)

    def test_contract_block_is_replaced_in_place(self) -> None:
        sync_module = load_module("sync_skill_blueprints", REPO_ROOT / "skills" / "my-writing-skills" / "scripts" / "sync_skill_blueprints.py")
        contract_block = "<!-- BEGIN BLUEPRINT CONTRACT -->\nNew\n<!-- END BLUEPRINT CONTRACT -->\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "SKILL.md"
            skill_file.write_text(
                "---\nname: example-skill\ndescription: Use when testing.\n---\n\n"
                "<!-- BEGIN BLUEPRINT CONTRACT -->\nOld\n<!-- END BLUEPRINT CONTRACT -->\n"
                "When this skill is used, begin with:\n",
                encoding="utf-8",
            )
            updated = sync_module.sync_contract_block(skill_file, contract_block)

        self.assertIn("New", updated)
        self.assertNotIn("Old", updated)
        self.assertEqual(updated.count("BEGIN BLUEPRINT CONTRACT"), 1)


if __name__ == "__main__":
    unittest.main()
