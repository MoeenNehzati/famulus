"""Smoke tests for skills/skill-maker/validators/boundaries.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "boundaries.py"
)
_spec = importlib.util.spec_from_file_location("boundaries", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_direct_cross_skill_path_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    caller.mkdir(parents=True)
    target.mkdir(parents=True)
    (target / "blueprint.yaml").write_text("name: target-skill\n")
    (caller / "blueprint.yaml").write_text("name: caller-skill\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "import subprocess\n"
        "subprocess.run(['python3', '../target-skill/_rtx/_helper_tool.py'])\n"
    )
    errors = _mod.validate(tmp_path)
    assert any("target-skill" in e for e in errors)


def test_same_skill_path_allowed(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "my-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    script = skill / "_rtx" / "run.py"
    script.write_text("import subprocess\nsubprocess.run(['python3', './helper.py'])\n")
    assert _mod.validate(tmp_path) == []
