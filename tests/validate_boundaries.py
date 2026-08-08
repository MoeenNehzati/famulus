"""Smoke tests for validators/skill/boundaries.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "validators" / "skill" / "boundaries.py"
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


def test_direct_cross_skill_cx_path_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    (caller / "_cx").mkdir(parents=True)
    target.mkdir(parents=True)
    (caller / "blueprint.yaml").write_text("name: caller-skill\n")
    (target / "blueprint.yaml").write_text("name: target-skill\n")
    (caller / "_cx" / "run-task").write_text(
        "exec ../target-skill/_cx/private-command\n"
    )

    errors = _mod.validate(tmp_path)

    assert any("target-skill" in error for error in errors)


def test_multiple_direct_paths_report_alphabetically_first_skill(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    alpha = skills / "alpha-skill"
    zeta = skills / "zeta-skill"
    for skill in (caller, alpha, zeta):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "use ../zeta-skill/_rtx/run.py and ../alpha-skill/_rtx/run.py\n"
    )

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/run.py:1: direct cross-skill runtime path "
        "to alpha-skill is forbidden"
    ]


def test_sys_path_violation_keeps_per_skill_order_before_later_direct_path(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    alpha = skills / "alpha-skill"
    zeta = skills / "zeta-skill"
    for skill in (caller, alpha, zeta):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "sys.path.insert(0, 'skills/alpha-skill'); "
        "use('../zeta-skill/_rtx/run.py')\n"
    )

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/run.py:1: cross-skill sys.path insertion "
        "to alpha-skill is forbidden"
    ]


def test_boundary_matchers_are_compiled_once_per_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    for skill in (caller, target):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text("\n".join(f"value_{index} = {index}" for index in range(100)))
    compiled_patterns: list[str] = []
    real_compile = re.compile

    def counted_compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
        compiled_patterns.append(pattern)
        return real_compile(pattern, flags)

    monkeypatch.setattr(_mod.re, "compile", counted_compile)

    assert _mod.validate(tmp_path) == []
    assert len(compiled_patterns) == 3
