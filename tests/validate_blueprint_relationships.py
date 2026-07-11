"""Smoke tests for skills/skill-maker/validators/blueprint_relationships.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "blueprint_relationships.py"
)
_spec = importlib.util.spec_from_file_location("blueprint_relationships", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_no_blueprints_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_self_dependency_flagged(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {"my-skill": None}},
    )
    errors = _mod.validate(tmp_path)
    assert any("itself" in e for e in errors)


def test_valid_blueprint_passes(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {}},
    )
    assert _mod.validate(tmp_path) == []


def test_machine_export_is_resolved_by_canonical_name(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interface_version": 1,
            "interfaces": {
                "machine": {
                    "read-data": {
                        "runtime": {
                            "kind": "python_machine_interface",
                            "entrypoint": "_rtx/_tool_entry.py:Interface",
                        },
                        "dependencies": [],
                        "patterns": [{"min_positionals": 1}],
                        "allowed_callers": ["consumer-skill"],
                    }
                },
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "depends_on": {
                "producer-skill": {
                    "major_version": 1,
                    "exports": ["producer-skill.machine.read-data"],
                }
            }
        },
    )
    assert _mod.validate(tmp_path) == []


def test_machine_export_is_resolved_by_local_name(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interface_version": 1,
            "interfaces": {
                "machine": {
                    "read-data": {
                        "runtime": {
                            "kind": "python_machine_interface",
                            "entrypoint": "_rtx/_tool_entry.py:Interface",
                        },
                        "dependencies": [],
                        "allowed_callers": ["consumer-skill"],
                    }
                }
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "depends_on": {
                "producer-skill": {
                    "major_version": 1,
                    "exports": ["read-data"],
                }
            }
        },
    )
    assert _mod.validate(tmp_path) == []
