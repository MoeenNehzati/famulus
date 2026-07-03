"""Smoke tests for skills/my-writing-skills/validators/interface_ids.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "interface_ids.py"
)
_spec = importlib.util.spec_from_file_location("interface_ids", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_unique_ids_pass(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {
            "script_interfaces": {
                "read-data": {
                    "id": "read-data",
                    "command": ["python3", "scripts/tool.py"],
                    "subinterfaces": {
                        "daily-plan-view": {
                            "id": "read-data-daily-plan",
                        }
                    },
                }
            }
        },
    )
    assert _mod.validate(tmp_path) == []


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {
            "script_interfaces": {
                "read-data": {
                    "id": "shared-id",
                    "command": ["python3", "scripts/tool.py"],
                },
                "write-data": {
                    "id": "write-data",
                    "command": ["python3", "scripts/tool.py"],
                    "subinterfaces": {
                        "narrow": {
                            "id": "shared-id",
                        }
                    },
                },
            }
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("shared-id" in error and "unique within a skill" in error for error in errors)
