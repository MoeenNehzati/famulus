#!/usr/bin/env python3
"""Regression tests for blueprint sync and exported script dispatch."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT_TEMPLATE = REPO_ROOT / "references" / "blueprint" / "template.yaml"
DISPATCHER_SRC = REPO_ROOT / "script_dispatcher" / "src"


def default_llm_interface() -> dict:
    return {
        "default": {
            "version": 1,
            "description": "Primary LLM-facing skill instructions.",
            "binding": {"kind": "skill_file", "path": "SKILL.md"},
            "behavior_sources": [],
        }
    }


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

    def run_dispatcher_cmd(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(DISPATCHER_SRC) if not current else f"{DISPATCHER_SRC}:{current}"
        return subprocess.run(
            [sys.executable, "-m", "script_dispatcher.cli", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_blueprints_are_in_sync(self) -> None:
        result = self.run_cmd("skills/skill-maker/_rtx/_blueprint_syncer.py", "--check")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_blueprint_template_exists_and_is_comment_rich(self) -> None:
        self.assertTrue(BLUEPRINT_TEMPLATE.exists(), "reference blueprint template is missing")
        text = BLUEPRINT_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("version: 1", text)
        self.assertIn("interfaces:", text)
        self.assertIn("machine:", text)
        self.assertIn("llm:", text)
        self.assertIn("patterns:", text)
        self.assertIn("allow_all_skills:", text)
        self.assertGreaterEqual(text.count("#"), 25, "template should remain heavily commented")

    def test_blueprint_hook_check_passes(self) -> None:
        result = self.run_cmd("skills/skill-maker/validators/blueprints.py")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_sync_validator_requires_machine_interface_dependencies(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_dependency_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_handoff_scan.py:Interface",
                                    "behavior_sources": [],
                                },
                            }
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("interfaces.machine.scan.dependencies" in error for error in errors))

    def test_sync_validator_accepts_empty_machine_interface_dependencies(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_empty_dependency_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_handoff_scan.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [],
                            }
                        },
                        "llm": default_llm_interface(),
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertEqual(errors, [])

    def test_sync_validator_rejects_python_module_runtime(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_python_module_rejected_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {"kind": "python_module", "module": "_rtx._handoff_scan"},
                                "dependencies": [],
                            }
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("invocation kind must be `python_machine_interface`" in error for error in errors))

    def test_sync_validator_accepts_python_machine_interface_runtime(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_python_machine_runtime_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/scan.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [],
                            }
                        },
                        "llm": default_llm_interface(),
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertEqual(errors, [])

    def test_sync_validator_requires_invocation_behavior_sources(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_python_machine_runtime_direct_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/scan.py:Interface",
                                },
                                "dependencies": [],
                            }
                        },
                        "llm": default_llm_interface(),
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(
            any("interfaces.machine.scan.invocation.behavior_sources" in error for error in errors)
        )

    def test_sync_validator_accepts_python_machine_interface_args_prefix(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_python_machine_args_prefix_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/scan.py:Interface",
                                    "behavior_sources": [],
                                    "args_prefix": ["--mode", "fast"],
                                },
                                "dependencies": [],
                            }
                        },
                        "llm": default_llm_interface(),
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertEqual(errors, [])

    def test_sync_validator_rejects_malformed_python_machine_interface_runtime(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_python_machine_bad_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "../scan.py:Interface",
                                    "behavior_sources": [],
                                    "args_prefix": ["--mode", ""],
                                },
                                "dependencies": [],
                            }
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("entrypoint must look like `_rtx/file.py:Interface`" in error for error in errors))
        self.assertTrue(any("args_prefix" in error for error in errors))

    def test_sync_validator_rejects_script_interfaces(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_removed_script_interfaces_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {"machine": {}},
                    "script_interfaces": {
                        "scan": {
                            "id": "scan",
                            "command": ["python3", "_rtx/_handoff_scan.py"],
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("script_interfaces" in error and "removed" in error for error in errors))

    def test_sync_validator_rejects_dependency_shell_commands(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_bad_dependency_name_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_scan_tool.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [
                                    {
                                        "kind": "binary",
                                        "name": "rg --files",
                                        "reason": "Searches local files.",
                                    }
                                ],
                            }
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("not a path or shell command" in error for error in errors))

    def test_runtime_dependency_manifest_is_generated_from_machine_interfaces(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_manifest_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = {
            "demo-skill": sync_module.SkillBlueprint(
                "demo-skill",
                Path("skills/demo-skill/blueprint.yaml"),
                {
                    "category": "workflow-general-assistant",
                    "interfaces": {
                        "machine": {
                            "scan": {
                                "version": 1,
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_handoff_scan.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "PyYAML",
                                        "reason": "Parses YAML inputs.",
                                    },
                                    {
                                        "kind": "binary",
                                        "name": "rg",
                                        "reason": "Searches local text quickly.",
                                    },
                                ],
                            }
                        }
                    },
                },
            )
        }

        manifest = sync_module.generated_runtime_dependencies_manifest(blueprints)

        self.assertEqual(manifest["all"], {"python-package": ["PyYAML"], "binary": ["rg"]})
        self.assertEqual(
            manifest["skills"]["demo-skill"]["interfaces"]["scan"]["dependencies"],
            [
                {"kind": "python-package", "name": "PyYAML", "reason": "Parses YAML inputs."},
                {"kind": "binary", "name": "rg", "reason": "Searches local text quickly."},
            ],
        )

    def test_boundary_hook_check_passes(self) -> None:
        result = self.run_cmd("skills/skill-maker/validators/boundaries.py")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_dispatcher_allows_declared_export(self) -> None:
        result = self.run_dispatcher_cmd(
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
                sys.executable,
                "-m",
                "officina.runtime.python_machine_interface_runner",
                "_rtx/_yaml_store.py:Interface",
                "update",
                "/tmp/todo.yaml",
                "--file",
                "/tmp/todo-updates.yaml",
            ],
        )

    def test_dispatcher_requires_caller_skill(self) -> None:
        result = self.run_dispatcher_cmd(
            "--dry-run",
            "list-manager",
            "read-list",
            "/tmp/todo.yaml",
            "state=incomplete",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--caller-skill", result.stderr)

    def test_dispatcher_rejects_private_interface_for_dependency(self) -> None:
        """Test that internal-only interfaces cannot be used by dependent skills."""
        result = self.run_dispatcher_cmd(
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
        result = self.run_dispatcher_cmd(
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
        result = self.run_dispatcher_cmd(
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
        result = self.run_dispatcher_cmd(
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
        result = self.run_dispatcher_cmd(
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
                "-m",
                "script_dispatcher.cli",
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
            env={
                **os.environ,
                "PYTHONPATH": str(DISPATCHER_SRC)
                if not os.environ.get("PYTHONPATH")
                else f"{DISPATCHER_SRC}:{os.environ['PYTHONPATH']}",
            },
            input="x",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match any declared pattern", result.stderr)

    def test_contract_block_is_injected_after_frontmatter(self) -> None:
        sync_module = load_module("sync_skill_blueprints", REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py")
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
        sync_module = load_module("sync_skill_blueprints", REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py")
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

    def test_interface_block_is_injected_after_contract_block(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        interface_block = (
            "<!-- BEGIN BLUEPRINT INTERFACES -->\nInjected\n<!-- END BLUEPRINT INTERFACES -->\n"
        )
        text = (
            "---\nname: example-skill\ndescription: Use when testing.\n---\n\n"
            "<!-- BEGIN BLUEPRINT CONTRACT -->\nContract\n<!-- END BLUEPRINT CONTRACT -->\n"
            "Body.\n"
        )

        updated = sync_module.sync_interface_block(text, interface_block)

        expected = (
            "---\nname: example-skill\ndescription: Use when testing.\n---\n\n"
            "<!-- BEGIN BLUEPRINT CONTRACT -->\nContract\n<!-- END BLUEPRINT CONTRACT -->\n"
            "<!-- BEGIN BLUEPRINT INTERFACES -->\nInjected\n<!-- END BLUEPRINT INTERFACES -->\n"
            "Body.\n"
        )
        self.assertEqual(updated, expected)

    def test_generated_interface_block_uses_machine_interface_names(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        block = sync_module.generated_interface_block(
            "demo-skill",
            {
                "interfaces": {
                    "machine": {
                        "read-data": {
                            "description": "Read an input file.",
                            "usage": "<path>",
                            "invocation": {
                                "kind": "python_machine_interface",
                                "entrypoint": "_rtx/_tool_entry.py:Interface",
                                "behavior_sources": [],
                            },
                            "dependencies": [],
                        }
                    }
                }
            }
        )

        self.assertIn("`read-data` — Read an input file.", block)
        self.assertIn("dispatcher --caller-skill demo-skill demo-skill.machine.read-data <path>", block)


if __name__ == "__main__":
    unittest.main()
