from __future__ import annotations

import copy
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


def test_markdown_reference_does_not_change_hash_unless_declared(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "See [extra](references/extra.md).\n")
    write(skill / "references" / "extra.md", "one\n")

    first = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])
    write(skill / "references" / "extra.md", "two\n")
    second = health_state.hash_declared_roots(skill, repo, ["SKILL.md"])

    assert first == second


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


def test_interface_hash_includes_binding_and_behavior_sources(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(skill / "references" / "policy.md", "one\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [
            {
                "path": "references/policy.md",
                "content": "config",
                "format": "markdown",
                "reason": "Defines behavior for the interface.",
            }
        ],
    }

    first = health_state.hash_interface(skill, repo, spec)
    write(skill / "references" / "policy.md", "two\n")
    second = health_state.hash_interface(skill, repo, spec)

    assert first != second


def test_interface_hash_includes_missing_behavior_source_declarations(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [
            {
                "path": "references/missing.md",
                "content": "config",
                "format": "markdown",
                "reason": "Defines behavior for the interface.",
            }
        ],
    }

    first = health_state.hash_interface(skill, repo, spec)
    write(skill / "references" / "missing.md", "now present\n")
    second = health_state.hash_interface(skill, repo, spec)

    assert first != second


def test_interface_hash_does_not_hash_direct_io_subject_data(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(repo / "inbox" / "message.txt", "one\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [],
        "direct_io": {
            "reads": [
                {
                    "medium": "local-filesystem",
                    "path": "$repo/inbox/message.txt",
                    "content": "email",
                    "format": "text",
                    "access": "read",
                }
            ],
            "writes": [],
            "network": [],
        },
    }

    first = health_state.hash_interface(skill, repo, spec)
    write(repo / "inbox" / "message.txt", "two\n")
    second = health_state.hash_interface(skill, repo, spec)

    assert first == second


def test_interface_hash_includes_structured_blueprint_metadata(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(skill / "references" / "policy.md", "policy\n")
    write(skill / "_rtx" / "__init__.py", "")
    write(
        skill / "_rtx" / "_noop.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )
    spec = {
        "version": 1,
        "description": "Run the worker.",
        "patterns": [
            {
                "name": "run",
                "min_positionals": 0,
                "max_positionals": 0,
                "allow_extra_positionals": False,
                "allow_stdin": False,
            }
        ],
        "allow_all_skills": False,
        "allowed_callers": ["skill-audit"],
        "platform_support": {"linux": True, "macos": True, "windows": False},
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_noop.py:Interface",
            "args_prefix": ["status"],
            "behavior_sources": [
                {
                    "path": "references/policy.md",
                    "content": "config",
                    "format": "markdown",
                    "reason": "Defines behavior.",
                }
            ],
        },
        "dependencies": [
            {
                "kind": "python-package",
                "name": "pyyaml",
                "version": ">=6",
                "platforms": {"linux": True, "macos": True, "windows": True},
                "reason": "Parse blueprint files.",
            }
        ],
        "uses_interfaces": [],
        "direct_io": {"reads": [], "writes": [], "network": []},
        "owns_filesystem": [],
    }

    first = health_state.hash_interface(skill, repo, spec)
    changed = copy.deepcopy(spec)
    changed["invocation"]["args_prefix"] = ["compute"]
    second = health_state.hash_interface(skill, repo, changed)

    assert first != second


def test_interface_hash_does_not_hash_direct_io_declaration(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [],
        "direct_io": {
            "reads": [
                {
                    "medium": "local-filesystem",
                    "path": "$repo/inbox/message.txt",
                    "content": "email",
                    "access": "read",
                    "sensitivity": "user-private",
                }
            ],
            "writes": [],
            "network": [],
        },
    }

    first = health_state.hash_interface(skill, repo, spec)
    changed = copy.deepcopy(spec)
    changed["direct_io"]["reads"][0]["path"] = "$repo/inbox/other.txt"
    second = health_state.hash_interface(skill, repo, changed)

    assert first == second


def test_interface_hash_includes_used_machine_interface_hash(tmp_path: Path) -> None:
    repo = tmp_path
    consumer = repo / "skills" / "consumer-skill"
    provider = repo / "skills" / "provider-skill"
    write(consumer / "SKILL.md", "Use the provider interface.\n")
    write(provider / "references" / "policy.md", "one\n")
    write(provider / "_rtx" / "__init__.py", "")
    write(
        provider / "_rtx" / "_noop.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )
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
                "      behavior_sources: []",
                "  machine:",
                "    worker:",
                "      invocation:",
                "        kind: python_machine_interface",
                "        entrypoint: _rtx/_noop.py:Interface",
                "        behavior_sources:",
                "          - path: references/policy.md",
                "            content: config",
                "            format: markdown",
                "            reason: Defines worker behavior.",
                "",
            ]
        ),
    )
    write(provider / "SKILL.md", "provider\n")
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "uses_interfaces": ["provider-skill.machine.worker"],
        "behavior_sources": [],
    }

    first = health_state.hash_interface(consumer, repo, spec)
    write(provider / "references" / "policy.md", "two\n")
    second = health_state.hash_interface(consumer, repo, spec)

    assert first != second


def test_interface_hash_includes_used_machine_interface_hash_recursively(tmp_path: Path) -> None:
    repo = tmp_path
    root = repo / "skills" / "root-skill"
    middle = repo / "skills" / "middle-skill"
    leaf = repo / "skills" / "leaf-skill"
    write(root / "SKILL.md", "Use the middle interface.\n")
    write(middle / "references" / "policy.md", "middle\n")
    write(leaf / "references" / "policy.md", "one\n")
    for skill in (middle, leaf):
        write(skill / "_rtx" / "__init__.py", "")
        write(
            skill / "_rtx" / "_noop.py",
            "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
            "class Interface(PythonMachineInterface):\n"
            "    pass\n",
        )
    for skill, interface_name, execute_path, uses in [
        (middle, "worker", "references/policy.md", "      uses_interfaces:\n        - leaf-skill.machine.leaf\n"),
        (leaf, "leaf", "references/policy.md", ""),
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
                    "      behavior_sources: []",
                    "  machine:",
                    f"    {interface_name}:",
                    uses.rstrip("\n"),
                    "      invocation:",
                    "        kind: python_machine_interface",
                    "        entrypoint: _rtx/_noop.py:Interface",
                    "        behavior_sources:",
                    f"          - path: {execute_path}",
                    "            content: config",
                    "            format: markdown",
                    "            reason: Defines behavior.",
                    "",
                ]
            ),
        )
    spec = {
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "uses_interfaces": ["middle-skill.machine.worker"],
        "behavior_sources": [],
    }

    first = health_state.hash_interface(root, repo, spec)
    write(leaf / "references" / "policy.md", "two\n")
    second = health_state.hash_interface(root, repo, spec)

    assert first != second


def test_skill_hash_collects_all_interface_roots(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    write(skill / "references" / "worker-policy.md", "worker\n")
    write(skill / "_rtx" / "__init__.py", "")
    write(
        skill / "_rtx" / "_noop.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "behavior_sources": [],
                }
            },
            "machine": {
                "worker": {
                    "invocation": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_noop.py:Interface",
                        "behavior_sources": [
                            {
                                "path": "references/worker-policy.md",
                                "content": "config",
                                "format": "markdown",
                                "reason": "Defines worker behavior.",
                            }
                        ],
                    },
                }
            },
        }
    }

    first = health_state.hash_skill(skill, repo, blueprint)
    write(skill / "references" / "worker-policy.md", "worker changed\n")
    second = health_state.hash_skill(skill, repo, blueprint)

    assert first != second


def test_skill_hash_changes_when_hashable_blueprint_metadata_changes(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    blueprint = {
        "category": "development-assistant",
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "behavior_sources": [],
                }
            }
        }
    }

    first = health_state.hash_skill(skill, repo, blueprint)
    changed = copy.deepcopy(blueprint)
    changed["category"] = "skill-making-development-assistant"
    second = health_state.hash_skill(skill, repo, changed)

    assert first != second


def test_skill_hash_does_not_hash_direct_io_declaration(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "skill\n")
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "behavior_sources": [],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "local-filesystem",
                                "path": "$repo/inbox/message.txt",
                                "content": "email",
                                "access": "read",
                                "sensitivity": "user-private",
                            }
                        ],
                        "writes": [],
                        "network": [],
                    },
                }
            }
        }
    }

    first = health_state.hash_skill(skill, repo, blueprint)
    changed = copy.deepcopy(blueprint)
    changed["interfaces"]["llm"]["default"]["direct_io"]["reads"][0]["path"] = "$repo/inbox/other.txt"
    second = health_state.hash_skill(skill, repo, changed)

    assert first == second
