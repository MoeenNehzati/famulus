"""Smoke tests for skills/skill-maker/validators/names.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "names.py"
)
_spec = importlib.util.spec_from_file_location("names", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _skill(tmp_path: Path, name: str, frontmatter_name: str | None = None) -> Path:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    fn = frontmatter_name if frontmatter_name is not None else name
    (skill_dir / "SKILL.md").write_text(f"---\nname: {fn}\n---\nBody.\n")
    return skill_dir


def test_valid_skill_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill")
    assert _mod.validate(tmp_path) == []


def test_single_word_name_flagged(tmp_path: Path) -> None:
    _skill(tmp_path, "myskill")
    errors = _mod.validate(tmp_path)
    assert any("lower-case dash-separated" in e for e in errors)


def test_uppercase_name_flagged(tmp_path: Path) -> None:
    _skill(tmp_path, "My-Skill")
    errors = _mod.validate(tmp_path)
    assert any("lower-case dash-separated" in e or "SKILL.md" in e for e in errors)


def test_missing_skill_md_flagged(tmp_path: Path) -> None:
    (tmp_path / "skills" / "my-skill").mkdir(parents=True)
    errors = _mod.validate(tmp_path)
    assert any("missing SKILL.md" in e for e in errors)


def test_mismatched_frontmatter_name_flagged(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill", frontmatter_name="other-skill")
    errors = _mod.validate(tmp_path)
    assert any("frontmatter name" in e for e in errors)


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []
