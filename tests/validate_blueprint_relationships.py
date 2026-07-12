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


def _machine_interface(version: int = 1, **extra: object) -> dict:
    interface = {
        "version": version,
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_tool_entry.py:Interface",
            "behavior_sources": [],
        },
        "dependencies": [],
    }
    interface.update(extra)
    return interface


def _llm_interface(version: int = 1, **extra: object) -> dict:
    interface = {
        "version": version,
        "description": "Primary LLM-facing skill instructions.",
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [],
    }
    interface.update(extra)
    return interface


def test_no_blueprints_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_legacy_depends_on_is_rejected(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {"other-skill": {}}},
    )
    errors = _mod.validate(tmp_path)
    assert any("top-level `depends_on`" in e for e in errors)


def test_valid_machine_use_passes(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "read-data": _machine_interface(allowed_callers=["consumer-skill"]),
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    assert _mod.validate(tmp_path) == []


def test_unknown_and_stale_interface_uses_are_rejected(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(version=2, allow_all_skills=True)},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1},
                            {"interface": "missing-skill.machine.read-data", "version": 1},
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("target version is 2" in e for e in errors)
    assert any("unknown interface" in e for e in errors)


def test_cross_skill_llm_to_machine_use_is_rejected(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(allow_all_skills=True)},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {},
                "llm": {
                    "default": _llm_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("LLM interfaces may only use same-skill machine interfaces" in e for e in errors)


def test_access_control_is_enforced(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(allowed_callers=["other-skill"])},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("not allowed by target access control" in e for e in errors)
