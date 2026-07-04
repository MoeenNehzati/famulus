"""Smoke tests for skills/my-writing-skills/validators/skill_md_dispatch.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "skill_md_dispatch.py"
)
_spec = importlib.util.spec_from_file_location("skill_md_dispatch", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_missing_interface_block_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "script_interfaces:\n  run:\n    id: run\n    command: ['python3', 'scripts/run.py']\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
    errors = _mod.validate(tmp_path)
    assert any("missing generated blueprint interface block" in error for error in errors)


def test_raw_script_in_generated_block_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "script_interfaces:\n  run:\n    id: run\n    command: ['python3', 'scripts/run.py']\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "scripts/run.py\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("must not expose raw scripts" in error for error in errors)


def test_dispatcher_command_required(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "script_interfaces:\n  run:\n    id: run\n    command: ['python3', 'scripts/run.py']\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "dispatcher --caller-skill other other run\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("missing dispatcher command" in error for error in errors)


def test_valid_generated_block_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "script_interfaces:\n  run:\n    id: run\n    command: ['python3', 'scripts/run.py']\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "<!-- BEGIN BLUEPRINT INTERFACES -->\n"
        "dispatcher --caller-skill demo-skill demo-skill run\n"
        "<!-- END BLUEPRINT INTERFACES -->\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []
