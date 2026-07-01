"""Smoke tests for skills/my-writing-skills/validators/blueprints.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "blueprints.py"
)
_spec = importlib.util.spec_from_file_location("blueprints", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_template(tmp_path: Path) -> None:
    t = tmp_path / "references" / "blueprint"
    t.mkdir(parents=True)
    (t / "template.yaml").write_text("# blueprint template\n")


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
