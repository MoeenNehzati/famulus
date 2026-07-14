"""Smoke tests for skills/skill-maker/validators/interface_ids.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "interface_ids.py"
)
_spec = importlib.util.spec_from_file_location("interface_ids", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_machine_and_llm_interface_names_pass(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "read-data": {
                        "invocation": {
                            "kind": "python_machine_interface",
                            "entrypoint": "_rtx/_tool_entry.py:Interface",
                        },
                        "dependencies": [],
                    }
                },
                "llm": {
                    "skill-doc": {
                        "description": "Prompt surface.",
                        "binding": {"kind": "markdown_file", "path": "SKILL.md"},
                    }
                },
            }
        },
    )
    assert _mod.validate(tmp_path) == []


def test_dotted_interface_name_fails(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "read.data": {
                        "invocation": {
                            "kind": "python_machine_interface",
                            "entrypoint": "_rtx/_tool_entry.py:Interface",
                        },
                        "dependencies": [],
                    }
                }
            }
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("must not contain `.`" in error for error in errors)


def test_typed_interface_id_namespace_must_match_node_type(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "my-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Body.\n")
    (runtime / "_runner.py").write_text("class Interface: pass\n")
    _write_blueprint(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "my-skill",
            "interfaces": [
                {
                    "interface": "my-skill.llm.run",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._runner.py.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_blueprint(
        runtime / "._runner.py.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": "my-skill.llm.run",
            "version": 1,
            "binding": {
                "kind": "python-entrypoint",
                "path": "_rtx/_runner.py",
                "symbol": "Interface",
            },
        },
    )

    errors = _mod.validate(tmp_path)
    assert any("machine-interface id must use `.machine.`" in error for error in errors)
