"""Smoke tests for skills/skill-maker/validators/blueprints.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "blueprints.py"
)
_spec = importlib.util.spec_from_file_location("blueprints", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_template(tmp_path: Path) -> None:
    t = tmp_path / "references" / "blueprint"
    t.mkdir(parents=True)
    (t / "template.yaml").write_text("# blueprint template\n")


def _default_llm() -> dict:
    return {
        "default": {
            "description": "Primary LLM-facing skill instructions.",
            "binding": {"kind": "skill_file", "path": "SKILL.md"},
            "directly_reads": ["SKILL.md"],
            "directly_executes": [],
            "directly_writes": [],
        }
    }


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    _make_template(tmp_path)
    assert _mod.validate(tmp_path) == []


def test_skill_without_blueprint_flagged(tmp_path: Path) -> None:
    _make_template(tmp_path)
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing blueprint.yaml" in e for e in errors)


def test_skill_with_blueprint_but_no_contract_flagged(tmp_path: Path) -> None:
    _make_template(tmp_path)
    skill = tmp_path / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    errors = _mod.validate(tmp_path)
    assert any("contract block" in e for e in errors)


def test_machine_interface_without_dependencies_flagged_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("dependencies" in error and "required" in error for error in errors)


def test_script_interfaces_are_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "script_interfaces": {
            "scan": {
                "id": "scan",
                "command": ["python3", "_rtx/_handoff_scan.py"],
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("script_interfaces" in error and "Additional properties" in error for error in errors)


def test_machine_interface_dependency_objects_pass_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [
                        {
                            "kind": "python",
                            "name": "PyYAML",
                            "reason": "Reads YAML files.",
                        },
                        {
                            "kind": "binary",
                            "name": "curl",
                            "reason": "Fetches remote JSON.",
                        },
                    ],
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert errors == []


def test_llm_default_is_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {"machine": {}, "llm": {}},
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("interfaces.llm" in error and "'default' is a required property" in error for error in errors)


def test_direct_effect_roots_are_required_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("directly_reads" in error and "required" in error for error in errors)
    assert any("directly_executes" in error and "required" in error for error in errors)
    assert any("directly_writes" in error and "required" in error for error in errors)


def test_direct_effect_roots_reject_parent_traversal_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_handoff_scan.py:Interface",
                    },
                    "dependencies": [],
                    "directly_reads": ["../secret.txt"],
                    "directly_executes": ["_rtx/_handoff_scan.py"],
                    "directly_writes": [],
                }
            },
            "llm": _default_llm(),
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("directly_reads.0" in error and "does not match" in error for error in errors)


def test_python_module_runtime_is_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {"kind": "python_module", "module": "_rtx._handoff_scan"},
                    "dependencies": [],
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("is not valid under any of the given schemas" in error for error in errors)


def test_route_smoke_supported_flag_is_rejected_by_schema() -> None:
    schema = _mod._load_schema()
    assert schema is not None
    blueprint = {
        "category": "workflow-general-assistant",
        "interface_version": 1,
        "interfaces": {
            "machine": {
                "scan": {
                    "runtime": {
                        "kind": "command",
                        "argv": [
                            "python3",
                            "-m",
                            "officina.runtime.python_machine_interface_runner",
                            "_rtx/scan.py:Scan",
                        ],
                    },
                    "route_smoke": {"argv": [], "supported": True},
                    "dependencies": [],
                }
            }
        },
    }

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), blueprint, schema)

    assert any("route_smoke" in error and "Additional properties" in error for error in errors)


def test_missing_jsonschema_is_reported_as_validator_error(monkeypatch) -> None:
    schema = _mod._load_schema()
    assert schema is not None
    monkeypatch.setattr(_mod, "jsonschema", None)

    errors = _mod._validate_blueprint_schema(Path("blueprint.yaml"), {}, schema)

    assert errors == [
        "blueprint.yaml: cannot validate blueprint schema because required "
        "Python package `jsonschema` is not installed"
    ]
