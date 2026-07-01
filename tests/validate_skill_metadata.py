"""Tests for skills/my-writing-skills/validators/skill_metadata.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "my-writing-skills" / "validators" / "skill_metadata.py"
)
_spec = importlib.util.spec_from_file_location("skill_metadata", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

MAX_LEN = 1024


def _make_skill(skills_dir: Path, name: str, content: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content)


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_skill_passes(tmp_path: Path) -> None:
    _make_skill(
        tmp_path / "skills", "my-skill",
        "---\nname: my-skill\ndescription: A short description.\n---\nBody.\n",
    )
    assert _mod.validate(tmp_path) == []


def test_missing_frontmatter_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", "No frontmatter here.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing YAML frontmatter" in e for e in errors)


def test_missing_description_flagged(tmp_path: Path) -> None:
    _make_skill(tmp_path / "skills", "my-skill", "---\nname: my-skill\n---\nBody.\n")
    errors = _mod.validate(tmp_path)
    assert any("missing description" in e for e in errors)


def test_long_description_flagged(tmp_path: Path) -> None:
    long_desc = "x" * (MAX_LEN + 1)
    _make_skill(
        tmp_path / "skills", "my-skill",
        f"---\nname: my-skill\ndescription: {long_desc}\n---\nBody.\n",
    )
    errors = _mod.validate(tmp_path)
    assert any(f"{MAX_LEN + 1} characters" in e for e in errors)


def test_description_at_limit_passes(tmp_path: Path) -> None:
    exact_desc = "x" * MAX_LEN
    _make_skill(
        tmp_path / "skills", "my-skill",
        f"---\nname: my-skill\ndescription: {exact_desc}\n---\nBody.\n",
    )
    assert _mod.validate(tmp_path) == []


def test_multiple_skills_all_checked(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _make_skill(skills, "good-skill", "---\nname: good-skill\ndescription: Fine.\n---\n")
    _make_skill(skills, "bad-skill", "No frontmatter.\n")
    errors = _mod.validate(tmp_path)
    assert len(errors) == 1
    assert "bad-skill" in errors[0]
