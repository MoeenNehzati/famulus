from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_drift_hashes.py"
SPEC = importlib.util.spec_from_file_location("skill_drift_hashes", MODULE_PATH)
health_state = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health_state
SPEC.loader.exec_module(health_state)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_file_hash_changes_when_file_content_changes(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "one\n")

    first = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])
    write(skill / "SKILL.md", "two\n")
    second = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])

    assert first.startswith("sha256:")
    assert first != second


def test_markdown_reference_changes_hash_transitively(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "See [extra](references/extra.md).\n")
    write(skill / "references" / "extra.md", "one\n")

    first = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])
    write(skill / "references" / "extra.md", "two\n")
    second = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])

    assert first != second

def test_directory_hash_is_recursive_and_ignores_health_record(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "references" / "a.txt", "a\n")
    write(skill / "references" / "nested" / "b.txt", "b\n")
    write(skill / ".last_audit.json", "{}\n")

    first = health_state.hash_declared_roots(skill, repo, ["references/"])
    write(skill / "references" / "nested" / "b.txt", "changed\n")
    second = health_state.hash_declared_roots(skill, repo, ["references/"])

    assert first != second


def test_hash_root_rejects_absolute_and_parent_paths(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    skill.mkdir(parents=True)

    with pytest.raises(health_state.HashRootError):
        health_state.hash_declared_roots(skill, repo, ["/tmp/x"])
    with pytest.raises(health_state.HashRootError):
        health_state.hash_declared_roots(skill, repo, ["../other-skill/SKILL.md"])


def test_repo_relative_root_hashes_repo_file(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(repo / "references" / "policy.md", "policy\n")

    first = health_state.hash_declared_roots(skill, repo, ["$repo/references/policy.md"])
    write(repo / "references" / "policy.md", "policy changed\n")
    second = health_state.hash_declared_roots(skill, repo, ["$repo/references/policy.md"])

    assert first != second


def test_interface_hash_includes_binding_and_direct_roots(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(skill / "_rtx" / "_drift_hashes.py", "print('x')\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "directly_reads": ["SKILL.md"],
        "directly_executes": ["_rtx/_drift_hashes.py"],
        "directly_writes": [],
    }

    first = health_state.hash_interface(skill, repo, spec)
    write(skill / "_rtx" / "_drift_hashes.py", "print('y')\n")
    second = health_state.hash_interface(skill, repo, spec)

    assert first != second


def test_interface_hash_includes_used_machine_interface_hash(tmp_path: Path) -> None:
    repo = tmp_path
    consumer = repo / "skills" / "consumer-skill"
    provider = repo / "skills" / "provider-skill"
    write(consumer / "SKILL.md", "Use the provider interface.\n")
    write(provider / "_rtx" / "_worker.py", "VALUE = 'one'\n")
    write(
        provider / "blueprint.yaml",
        "\n".join(
            [
                "category: development-assistant",
                "interface_version: 1",
                "interfaces:",
                "  llm:",
                "    default:",
                "      description: Primary.",
                "      binding:",
                "        kind: skill_file",
                "        path: SKILL.md",
                "      directly_reads:",
                "        - SKILL.md",
                "      directly_executes: []",
                "      directly_writes: []",
                "  machine:",
                "    worker:",
                "      directly_reads: []",
                "      directly_executes:",
                "        - _rtx/_worker.py",
                "      directly_writes: []",
                "",
            ]
        ),
    )
    write(provider / "SKILL.md", "provider\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "uses_interfaces": ["provider-skill.machine.worker"],
        "directly_reads": ["SKILL.md"],
        "directly_executes": [],
        "directly_writes": [],
    }

    first = health_state.hash_interface(consumer, repo, spec)
    write(provider / "_rtx" / "_worker.py", "VALUE = 'two'\n")
    second = health_state.hash_interface(consumer, repo, spec)

    assert first != second


def test_interface_hash_includes_used_machine_interface_hash_recursively(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "skills" / "root-skill"
    middle = repo / "skills" / "middle-skill"
    leaf = repo / "skills" / "leaf-skill"
    write(root / "SKILL.md", "Use the middle interface.\n")
    write(middle / "_rtx" / "_worker.py", "middle\n")
    write(leaf / "_rtx" / "_leaf.py", "VALUE = 'one'\n")
    for skill, interface_name, execute_path, uses in [
        (middle, "worker", "_rtx/_worker.py", "      uses_interfaces:\n        - leaf-skill.machine.leaf\n"),
        (leaf, "leaf", "_rtx/_leaf.py", ""),
    ]:
        write(skill / "SKILL.md", f"{skill.name}\n")
        write(
            skill / "blueprint.yaml",
            "\n".join(
                [
                    "category: development-assistant",
                    "interface_version: 1",
                    "interfaces:",
                    "  llm:",
                    "    default:",
                    "      description: Primary.",
                    "      binding:",
                    "        kind: skill_file",
                    "        path: SKILL.md",
                    "      directly_reads:",
                    "        - SKILL.md",
                    "      directly_executes: []",
                    "      directly_writes: []",
                    "  machine:",
                    f"    {interface_name}:",
                    uses.rstrip("\n"),
                    "      directly_reads: []",
                    "      directly_executes:",
                    f"        - {execute_path}",
                    "      directly_writes: []",
                    "",
                ]
            ),
        )
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "uses_interfaces": ["middle-skill.machine.worker"],
        "directly_reads": ["SKILL.md"],
        "directly_executes": [],
        "directly_writes": [],
    }

    first = health_state.hash_interface(root, repo, spec)
    write(leaf / "_rtx" / "_leaf.py", "VALUE = 'two'\n")
    second = health_state.hash_interface(root, repo, spec)

    assert first != second


def test_skill_hash_collects_all_interface_roots(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(skill / "_rtx" / "_worker.py", "worker\n")
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                }
            },
            "machine": {
                "worker": {
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_worker.py"],
                    "directly_writes": [],
                }
            },
        }
    }

    first = health_state.hash_skill(skill, repo, blueprint)
    write(skill / "_rtx" / "_worker.py", "worker changed\n")
    second = health_state.hash_skill(skill, repo, blueprint)

    assert first != second
