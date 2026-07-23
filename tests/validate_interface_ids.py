"""Smoke tests for skills/skill-maker/validators/interface_ids.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = (
    _REPO_ROOT
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


def test_v4_export_id_must_use_generic_interface_namespace(tmp_path: Path) -> None:
    shutil.copytree(
        _REPO_ROOT / "references" / "blueprint",
        tmp_path / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    skill = tmp_path / "skills" / "loose-mode"
    shutil.copytree(_REPO_ROOT / "skills" / "loose-mode", skill)
    module = yaml.safe_load((skill / "blueprint.yaml").read_text(encoding="utf-8"))
    module["exports"]["loose-mode.bad.default"] = module["exports"].pop(
        "loose-mode.interface.default"
    )
    _write_blueprint(skill / "blueprint.yaml", module)

    errors = _mod.validate(tmp_path)

    assert any("loose-mode.bad.default" in error for error in errors)


def test_malformed_v4_inventory_is_returned_as_a_finding(tmp_path: Path) -> None:
    blueprint = tmp_path / "skills" / "bad-skill" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text("schema_version: 4\nnode_type: [\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("blueprint inventory failed" in error for error in errors)
