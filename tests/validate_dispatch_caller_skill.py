"""Smoke tests for skills/skill-maker/validators/dispatch_caller_skill.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "dispatch_caller_skill.py"
)
_spec = importlib.util.spec_from_file_location("dispatch_caller_skill", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_literal_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='good-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_module_constant_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "OWNER = 'good-skill'\n"
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill=OWNER, target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_missing_caller_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("must include caller_skill" in error for error in errors)


def test_wrong_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='other-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("expected `bad-skill`" in error for error in errors)


def test_dynamic_caller_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "def wrapper(caller_skill: str):\n"
        "    dispatch(caller_skill=caller_skill, target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("module-level string constant" in error for error in errors)


def test_famulus_dispatcher_import_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from famulus.dispatcher import dispatch\n"
        "dispatch(caller_skill='bad-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("removed famulus.dispatcher" in error for error in errors)
