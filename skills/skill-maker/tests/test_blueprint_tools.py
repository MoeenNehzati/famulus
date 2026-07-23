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
from types import SimpleNamespace
from unittest import mock

import yaml

from officina.common.certification_view import CertificationDecision
from officina.common.blueprint_graph import (
    authored_node_input_paths,
    load_repository_blueprint_graph,
)
from officina.dispatcher.core import InvocationError, resolve_dispatch_metadata


REPO_ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT_TEMPLATE = REPO_ROOT / "references" / "blueprint" / "template.yaml"
DISPATCHER_SRC = REPO_ROOT / "script_dispatcher" / "src"


class PassingCertificationView:
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
    ) -> CertificationDecision:
        return CertificationDecision(True, "test-certified", "accepted by test fixture")

    def certificate_for(self, node_id: str) -> None:
        return None


def typed_sidecars_untracked_until_commit() -> tuple[str, ...]:
    head_paths = set(
        subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8").rstrip("\0").split("\0")
    )
    graph = load_repository_blueprint_graph(REPO_ROOT)
    candidates = {
        path.relative_to(REPO_ROOT).as_posix()
        for node in graph.nodes.values()
        for path in authored_node_input_paths(node, REPO_ROOT)
    }
    return tuple(sorted(candidates - head_paths))


def default_llm_interface() -> dict:
    return {
        "default": {
            "version": 1,
            "description": "Primary LLM-facing skill instructions.",
            "binding": {"kind": "skill_file", "path": "SKILL.md"},
            "behavior_sources": [],
        }
    }


def platform_support() -> dict:
    return {"linux": True, "macos": True, "windows": True}


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def make_typed_sync_fixture(root: Path) -> Path:
    skill = root / "skills" / "demo-skill"
    (skill / "_rtx").mkdir(parents=True)
    (root / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
    (skill / "_rtx" / "_shared_runner.py").write_text("class Interface: pass\n", encoding="utf-8")
    (root / "references" / "policy.md").write_text("Policy.\n", encoding="utf-8")
    interfaces = [
        {
            "interface": "demo-skill.llm.default",
            "version": 1,
            "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
        },
        *[
            {
                "interface": f"demo-skill.machine.{name}",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": f"_rtx/._shared_runner.py.{name}.blueprint.yaml",
                },
            }
            for name in ("first", "second")
        ],
    ]
    write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "interfaces": interfaces,
        },
    )
    write_yaml(
        skill / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary instructions.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "behavior_sources": [
                {
                    "source": "references.source.policy",
                    "version": 1,
                    "blueprint": {
                        "base": "repository-root",
                        "path": "references/.policy.md.blueprint.yaml",
                    },
                    "reason": "Shared policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    for name in ("first", "second"):
        write_yaml(
            skill / "_rtx" / f"._shared_runner.py.{name}.blueprint.yaml",
            {
                "schema_version": 2,
                "blueprint_type": "machine-interface",
                "id": f"demo-skill.machine.{name}",
                "version": 1,
                "description": f"Run {name} operation.",
                "binding": {
                    "kind": "python-entrypoint",
                    "path": "_rtx/_shared_runner.py",
                    "symbol": "Interface",
                },
                "platform_support": platform_support(),
                "dependencies": [],
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
        )
    write_yaml(
        root / "references" / ".policy.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "references.source.policy",
            "version": 1,
            "description": "Shared policy.",
            "binding": {"kind": "file", "path": "references/policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )
    return skill


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
        env["AI"] = str(REPO_ROOT)
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

    def test_blueprint_template_is_schema_family_artifact_manifest(self) -> None:
        self.assertTrue(BLUEPRINT_TEMPLATE.exists(), "reference blueprint template is missing")
        manifest = yaml.safe_load(BLUEPRINT_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(manifest["examples"]["module"], "blueprint.yaml")
        self.assertEqual(
            manifest["examples"]["behavioral_sources"],
            [
                "blueprints/gateway.yaml",
                "blueprints/runner.yaml",
            ],
        )
        self.assertIn("SKILL.md blueprint contract block", manifest["generated_outputs"])

    def test_blueprint_hook_check_passes(self) -> None:
        real_index_before = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "intended-source.index"
            object_directory = Path(temp) / "objects"
            object_directory.mkdir()
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(index)
            env["GIT_OBJECT_DIRECTORY"] = str(object_directory)
            common_git_dir = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            common_git_path = Path(common_git_dir)
            if not common_git_path.is_absolute():
                common_git_path = REPO_ROOT / common_git_path
            env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(
                common_git_path.resolve() / "objects"
            )
            subprocess.run(
                ["git", "read-tree", "HEAD"],
                cwd=REPO_ROOT,
                env=env,
                check=True,
            )
            untracked_sources = typed_sidecars_untracked_until_commit()
            if untracked_sources:
                subprocess.run(
                    ["git", "add", "--", *untracked_sources],
                    cwd=REPO_ROOT,
                    env=env,
                    check=True,
                )
            result = subprocess.run(
                [sys.executable, "skills/skill-maker/validators/blueprints.py"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        real_index_after = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(real_index_after, real_index_before)

    def test_typed_sync_loads_repository_source_and_shared_file_interfaces(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_typed_graph_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = make_typed_sync_fixture(root)
            with mock.patch.object(sync_module, "SKILLS_ROOT", root / "skills"):
                blueprint = sync_module.load_blueprints()["demo-skill"]

            machine = blueprint.data["interfaces"]["machine"]
            self.assertEqual(
                machine["first"]["invocation"]["entrypoint"],
                "_rtx/_shared_runner.py:Interface",
            )
            self.assertEqual(
                machine["second"]["invocation"]["entrypoint"],
                "_rtx/_shared_runner.py:Interface",
            )
            self.assertEqual(
                blueprint.data["interfaces"]["llm"]["default"]["behavior_sources"][0]["path"],
                "references/policy.md",
            )
            repository_sidecar = root / "references" / ".policy.md.blueprint.yaml"
            repository_source = yaml.safe_load(repository_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(repository_source["id"], "references.source.policy")
            self.assertEqual(
                repository_source["binding"]["path"],
                "references/policy.md",
            )
            self.assertEqual(blueprint.path, skill / "blueprint.yaml")

    def test_typed_sync_loads_inline_default_without_sidecar(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_inline_default_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = make_typed_sync_fixture(root)
            root_path = skill / "blueprint.yaml"
            sidecar_path = skill / ".SKILL.md.blueprint.yaml"
            root_declaration = yaml.safe_load(root_path.read_text(encoding="utf-8"))
            sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
            root_declaration["default_interface"] = {
                key: value
                for key, value in sidecar.items()
                if key not in {"schema_version", "blueprint_type", "id", "binding"}
            }
            root_declaration["interfaces"] = [
                edge
                for edge in root_declaration["interfaces"]
                if edge["interface"] != "demo-skill.llm.default"
            ]
            write_yaml(root_path, root_declaration)
            sidecar_path.unlink()

            with mock.patch.object(sync_module, "SKILLS_ROOT", root / "skills"):
                blueprint = sync_module.load_blueprints()["demo-skill"]

            default = blueprint.data["interfaces"]["llm"]["default"]
            self.assertEqual(default["binding"], {"kind": "skill_file", "path": "SKILL.md"})

    def test_typed_sync_ignores_health_and_pool_artifacts(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_generated_artifacts_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = make_typed_sync_fixture(root)
            (skill / ".SKILL.md.health.json").write_text("not json", encoding="utf-8")
            (skill / ".last_audit.json").write_text("not json", encoding="utf-8")
            (skill / ".pooled-blueprint-review.yaml").write_text("not: [yaml", encoding="utf-8")
            (skill / ".pooled-blueprint-review.health.json").write_text("not json", encoding="utf-8")
            with mock.patch.object(sync_module, "SKILLS_ROOT", root / "skills"):
                loaded = sync_module.load_blueprints()

            self.assertIn("demo-skill", loaded)

    def test_typed_sync_preserves_authored_blueprint_comments(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_comment_preservation_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = make_typed_sync_fixture(root)
            root_blueprint = skill / "blueprint.yaml"
            sidecar = skill / ".SKILL.md.blueprint.yaml"
            root_blueprint.write_text(
                "# keep root comment\n" + root_blueprint.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            sidecar.write_text(
                "# keep sidecar comment\n" + sidecar.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            root_before = root_blueprint.read_bytes()
            sidecar_before = sidecar.read_bytes()
            (root / "references" / "blueprint").mkdir(parents=True)
            with (
                mock.patch.object(sync_module, "SKILLS_ROOT", root / "skills"),
                mock.patch.object(
                    sync_module,
                    "RUNTIME_DEPENDENCIES_PATH",
                    root / "references" / "blueprint" / "runtime_dependencies.json",
                ),
            ):
                self.assertEqual(sync_module.run_sync(check_only=False), 0)

            self.assertEqual(root_blueprint.read_bytes(), root_before)
            self.assertEqual(sidecar.read_bytes(), sidecar_before)

    def test_typed_sync_rejects_schema_invalid_sidecar(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_schema_invalid_test",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = make_typed_sync_fixture(root)
            sidecar = skill / ".SKILL.md.blueprint.yaml"
            declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
            declaration.pop("description")
            write_yaml(sidecar, declaration)
            with mock.patch.object(sync_module, "SKILLS_ROOT", root / "skills"):
                with self.assertRaises(sync_module.BlueprintError) as raised:
                    sync_module.load_blueprints()

            self.assertIn("$.description", str(raised.exception))

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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
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
                                "platform_support": platform_support(),
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_scan_tool.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [
                                    {
                                        "kind": "binary",
                                        "name": "rg --files",
                                        "version": "any",
                                        "platforms": platform_support(),
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

    def test_sync_validator_rejects_unknown_system_service_name(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_bad_system_service_test",
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
                                "platform_support": platform_support(),
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_scan_tool.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [
                                    {
                                        "kind": "system-service",
                                        "name": "systemd",
                                        "version": "any",
                                        "platforms": platform_support(),
                                        "reason": "Schedules jobs.",
                                    }
                                ],
                            }
                        }
                    },
                },
            )
        }

        errors = sync_module.validate_blueprints(blueprints)

        self.assertTrue(any("system-service must be one of" in error for error in errors))

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
                                "platform_support": platform_support(),
                                "invocation": {
                                    "kind": "python_machine_interface",
                                    "entrypoint": "_rtx/_handoff_scan.py:Interface",
                                    "behavior_sources": [],
                                },
                                "dependencies": [
                                    {
                                        "kind": "python-package",
                                        "name": "PyYAML",
                                        "version": ">=6",
                                        "platforms": platform_support(),
                                        "reason": "Parses YAML inputs.",
                                    },
                                    {
                                        "kind": "binary",
                                        "name": "rg",
                                        "version": "any",
                                        "platforms": platform_support(),
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

        self.assertEqual(
            manifest["all"],
            {
                "python-package": ["PyYAML"],
                "binary": ["rg"],
                "system-service": [],
                "system-library": [],
                "external-application": [],
                "runtime": [],
                "model-data": [],
            },
        )
        self.assertEqual(
            manifest["skills"]["demo-skill"]["interfaces"]["scan"]["dependencies"],
            [
                {
                    "kind": "python-package",
                    "name": "PyYAML",
                    "version": ">=6",
                    "platforms": platform_support(),
                    "reason": "Parses YAML inputs.",
                },
                {
                    "kind": "binary",
                    "name": "rg",
                    "version": "any",
                    "platforms": platform_support(),
                    "reason": "Searches local text quickly.",
                },
            ],
        )

    def test_boundary_hook_check_passes(self) -> None:
        result = self.run_cmd("skills/skill-maker/validators/boundaries.py")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_dispatcher_allows_certified_declared_export(self) -> None:
        payload = resolve_dispatch_metadata(
            caller_skill="daily-plan",
            target="list-manager.interface.update-list",
            args=[
                "/tmp/todo.yaml",
                "--file",
                "/tmp/todo-updates.yaml",
            ],
            repo_root=REPO_ROOT,
            certification_view=PassingCertificationView(),
        ).as_payload()
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

    def test_dispatcher_cli_keeps_v4_export_closed_before_certification(self) -> None:
        result = self.run_dispatcher_cmd(
            "--dry-run",
            "--caller-skill",
            "daily-plan",
            "list-manager.interface.update-list",
            "/tmp/todo.yaml",
            "--file",
            "/tmp/todo-updates.yaml",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("certification rejected", result.stderr)

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
        with self.assertRaisesRegex(InvocationError, "does not declare use"):
            resolve_dispatch_metadata(
                caller_skill="daily-plan",
                target="list-manager.interface.migrate-markdown",
                args=["/tmp/input.md", "/tmp/output.yaml", "todo"],
                repo_root=REPO_ROOT,
                certification_view=PassingCertificationView(),
            )

    def test_dispatcher_allows_certified_private_interface_for_owner(self) -> None:
        payload = resolve_dispatch_metadata(
            caller_skill="list-manager",
            target="list-manager.interface.update-list",
            args=["/tmp/todo.yaml", "--file", "/tmp/updates.yaml"],
            repo_root=REPO_ROOT,
            certification_view=PassingCertificationView(),
        ).as_payload()
        self.assertIn("pattern", payload)

    def test_dispatcher_enforces_required_flags_for_certified_export(self) -> None:
        resolve_dispatch_metadata(
            caller_skill="daily-plan",
            target="list-manager.interface.update-list",
            args=["/tmp/todo.yaml", "--file", "/tmp/updates.yaml"],
            repo_root=REPO_ROOT,
            certification_view=PassingCertificationView(),
        )
        with self.assertRaisesRegex(
            InvocationError, "does not match any declared pattern"
        ):
            resolve_dispatch_metadata(
                caller_skill="daily-plan",
                target="list-manager.interface.update-list",
                args=[
                    "/tmp/todo.yaml",
                    "--file",
                    "/tmp/updates.yaml",
                    "--stdin",
                ],
                repo_root=REPO_ROOT,
                certification_view=PassingCertificationView(),
            )

    def test_dispatcher_rejects_unexpected_flag(self) -> None:
        with self.assertRaisesRegex(
            InvocationError, "does not match any declared pattern"
        ):
            resolve_dispatch_metadata(
                caller_skill="daily-plan",
                target="list-manager.interface.update-list",
                args=[
                    "/tmp/todo.yaml",
                    "--file",
                    "/tmp/todo-updates.yaml",
                    "--bogus",
                    "/tmp/value",
                ],
                repo_root=REPO_ROOT,
                certification_view=PassingCertificationView(),
            )

    def test_dispatcher_rejects_stdin_when_pattern_disallows_it(self) -> None:
        with self.assertRaisesRegex(
            InvocationError, "does not match any declared pattern"
        ):
            resolve_dispatch_metadata(
                caller_skill="daily-plan",
                target="list-manager.interface.update-list",
                args=[
                    "/tmp/todo.yaml",
                    "--file",
                    "/tmp/todo-updates.yaml",
                ],
                stdin_requested=True,
                repo_root=REPO_ROOT,
                certification_view=PassingCertificationView(),
            )

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

    def test_v4_generation_uses_repository_graph_exports_and_source_requirements(
        self,
    ) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_v4_generation",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        blueprints = sync_module.load_blueprints()
        blueprint = blueprints["list-manager"]

        self.assertIsNotNone(blueprint.repository_graph)
        contract = sync_module.generated_contract_block(
            blueprint.name,
            blueprint.data,
            blueprint.repository_graph,
        )
        self.assertIn("Skill Version: 1", contract)
        self.assertIn("`list-manager.interface.update-list`", contract)
        owner_block = sync_module.generated_interface_block(
            blueprint.name,
            blueprint.data,
            blueprint.repository_graph,
        )
        self.assertIn(
            "dispatcher --caller-skill list-manager "
            "list-manager.interface.update-list",
            owner_block,
        )

        manifest = sync_module.generated_runtime_dependencies_manifest(blueprints)
        update = manifest["skills"]["list-manager"]["interfaces"]["update-list"]
        self.assertEqual(update["id"], "list-manager.interface.update-list")
        self.assertIn(
            "PyYAML",
            {dependency["name"] for dependency in update["dependencies"]},
        )
        certify = manifest["skills"]["skill-certifier"]["interfaces"]["certify"]
        self.assertIn(
            "cryptography",
            {dependency["name"] for dependency in certify["dependencies"]},
        )
        self.assertNotIn(
            "cryptography",
            {dependency["name"] for dependency in update["dependencies"]},
        )

    def test_consumer_local_blocks_use_root_and_named_gateway_placement(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_consumer_blocks",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            skill = Path(temp) / "demo-skill"
            named_dir = skill / "llm_interfaces"
            named_dir.mkdir(parents=True)
            root_gateway = skill / "SKILL.md"
            named_gateway = named_dir / "coach.md"
            root_gateway.write_text(
                "---\nname: demo-skill\n---\n"
                "<!-- BEGIN BLUEPRINT CONTRACT -->\nContract\n<!-- END BLUEPRINT CONTRACT -->\n"
                "<!-- BEGIN BLUEPRINT INTERFACES -->\nOwner\n<!-- END BLUEPRINT INTERFACES -->\n"
                "Root body.\n",
                encoding="utf-8",
            )
            named_gateway.write_text("Named body.\n", encoding="utf-8")
            graph = SimpleNamespace(
                nodes={
                    "demo-skill.llm.default": SimpleNamespace(
                        node_type="llm-interface",
                        gateway_path=root_gateway,
                        skill_root=skill,
                    ),
                    "demo-skill.llm.coach": SimpleNamespace(
                        node_type="llm-interface",
                        gateway_path=named_gateway,
                        skill_root=skill,
                    ),
                }
            )
            selected = {
                "schema_version": 1,
                "consumer": "demo-skill.llm.default",
                "interfaces": {"provider.machine.run": {"id": "provider.machine.run"}},
                "helper_interfaces": {},
                "llm_interfaces": {},
                "definitions": {},
            }
            projections = {
                "demo-skill.llm.default": SimpleNamespace(document=selected),
                "demo-skill.llm.coach": SimpleNamespace(
                    document={**selected, "consumer": "demo-skill.llm.coach"}
                ),
            }

            first = sync_module.plan_consumer_interface_updates(graph, projections)
            sync_module.apply_consumer_interface_updates(first)
            root_text = root_gateway.read_text(encoding="utf-8")
            named_text = named_gateway.read_text(encoding="utf-8")
            second = sync_module.plan_consumer_interface_updates(graph, projections)

            self.assertLess(
                root_text.index(sync_module.CONTRACT_END),
                root_text.index(sync_module.USED_INTERFACES_START),
            )
            self.assertLess(
                root_text.index(sync_module.USED_INTERFACES_START),
                root_text.index(sync_module.INTERFACES_START),
            )
            self.assertTrue(named_text.startswith(sync_module.USED_INTERFACES_START))
            self.assertEqual(first[root_gateway], second[root_gateway])
            self.assertEqual(first[named_gateway], second[named_gateway])

    def test_v4_consumer_projection_uses_behavioral_source_gateways(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_v4_consumer_blocks",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            skill = Path(temp) / "demo-skill"
            named_dir = skill / "llm_interfaces"
            named_dir.mkdir(parents=True)
            root_gateway = skill / "SKILL.md"
            named_gateway = named_dir / "coach.md"
            root_gateway.write_text(
                "---\nname: demo-skill\n---\n"
                f"{sync_module.CONTRACT_START}\nContract\n"
                f"{sync_module.CONTRACT_END}\n"
                f"{sync_module.INTERFACES_START}\nOwner\n"
                f"{sync_module.INTERFACES_END}\nBody.\n",
                encoding="utf-8",
            )
            named_gateway.write_text("Named body.\n", encoding="utf-8")
            graph = SimpleNamespace(
                nodes={
                    "demo-skill.source.gateway": SimpleNamespace(
                        node_type="behavioral_source",
                        gateway_path=root_gateway,
                        skill_root=skill,
                        declaration={"gateway": {"language": "Markdown"}},
                    ),
                    "demo-skill.source.coach": SimpleNamespace(
                        node_type="behavioral_source",
                        gateway_path=named_gateway,
                        skill_root=skill,
                        declaration={"gateway": {"language": "Markdown"}},
                    ),
                }
            )
            projected = SimpleNamespace(
                document={
                    "schema_version": 2,
                    "consumer": "demo-skill.source.gateway",
                    "interfaces": {
                        "provider.interface.run": {
                            "id": "provider.interface.run"
                        }
                    },
                    "helper_interfaces": {},
                    "definitions": {},
                }
            )
            with mock.patch.object(
                sync_module,
                "project_consumer_interfaces",
                return_value=projected,
            ) as project:
                planned = sync_module.plan_projected_consumer_interface_updates(
                    graph,
                    SimpleNamespace(),
                )

            self.assertEqual(project.call_count, 2)
            self.assertLess(
                planned[root_gateway].index(sync_module.CONTRACT_END),
                planned[root_gateway].index(sync_module.USED_INTERFACES_START),
            )
            self.assertTrue(
                planned[named_gateway].startswith(sync_module.USED_INTERFACES_START)
            )
            shared = SimpleNamespace(
                nodes={
                    "demo-skill.source.one": SimpleNamespace(
                        node_type="behavioral_source",
                        gateway_path=named_gateway,
                        skill_root=skill,
                    ),
                    "demo-skill.source.two": SimpleNamespace(
                        node_type="behavioral_source",
                        gateway_path=named_gateway,
                        skill_root=skill,
                    ),
                }
            )
            with self.assertRaises(sync_module.BlueprintError):
                sync_module.plan_consumer_interface_updates(
                    shared,
                    {
                        "demo-skill.source.one": projected,
                        "demo-skill.source.two": projected,
                    },
                )

    def test_consumer_local_block_removes_stale_and_rejects_conflicting_markers(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_consumer_marker_rules",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        stale = (
            f"{sync_module.USED_INTERFACES_START}\nOld\n"
            f"{sync_module.USED_INTERFACES_END}\nBody.\n"
        )
        self.assertEqual(
            sync_module.sync_used_interfaces_block(stale, "", root_consumer=False),
            "Body.\n",
        )
        with self.assertRaises(sync_module.BlueprintError):
            sync_module.sync_used_interfaces_block(
                f"{sync_module.USED_INTERFACES_START}\nUnclosed.\n",
                "",
                root_consumer=False,
            )

    def test_consumer_update_plan_rejects_shared_named_gateway(self) -> None:
        sync_module = load_module(
            "sync_skill_blueprints_consumer_shared_gateway",
            REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            skill = Path(temp) / "demo-skill"
            skill.mkdir()
            gateway = skill / "shared.md"
            gateway.write_text("Body.\n", encoding="utf-8")
            node = lambda: SimpleNamespace(  # noqa: E731
                node_type="llm-interface", gateway_path=gateway, skill_root=skill
            )
            graph = SimpleNamespace(
                nodes={"demo-skill.llm.one": node(), "demo-skill.llm.two": node()}
            )
            empty = {
                "schema_version": 1,
                "consumer": "demo-skill.llm.one",
                "interfaces": {},
                "helper_interfaces": {},
                "llm_interfaces": {},
                "definitions": {},
            }
            with self.assertRaises(sync_module.BlueprintError):
                sync_module.plan_consumer_interface_updates(
                    graph,
                    {
                        "demo-skill.llm.one": SimpleNamespace(document=empty),
                        "demo-skill.llm.two": SimpleNamespace(
                            document={**empty, "consumer": "demo-skill.llm.two"}
                        ),
                    },
                )


if __name__ == "__main__":
    unittest.main()
