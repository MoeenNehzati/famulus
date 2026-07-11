"""Smoke tests for skills/skill-maker/validators/dependencies.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from textwrap import dedent

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "dependencies.py"
)
_spec = importlib.util.spec_from_file_location("dependencies", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _skill(
    tmp_path: Path,
    name: str,
    deps_block: str = "Dependencies: none",
    body: str = "",
    blueprint_deps: list[str] | None = None,
) -> Path:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = f"---\nname: {name}\n---\n{deps_block}\n\n{body}\n"
    (skill_dir / "SKILL.md").write_text(skill_md)
    deps = blueprint_deps or []
    dep_lines = "\n".join(f"  {dep}: {{}}" for dep in deps)
    depends_block = f"depends_on:\n{dep_lines}\n" if deps else "depends_on: {}\n"
    (skill_dir / "blueprint.yaml").write_text(dedent(depends_block))
    return skill_dir


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_no_deps_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill")
    assert _mod.validate(tmp_path) == []


def test_missing_deps_block_flagged(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill_dir / "blueprint.yaml").write_text("depends_on: {}\n")
    errors = _mod.validate(tmp_path)
    assert any("missing Dependencies block" in e for e in errors)


def test_mismatched_deps_flagged(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill", deps_block="Dependencies:\n- other-skill")
    errors = _mod.validate(tmp_path)
    assert any("Dependencies block does not match" in e for e in errors)


def test_body_mentions_not_in_sidecar_flagged(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "my-skill",
        deps_block="Dependencies: none",
        body="Use other-skill for this.",
    )
    # Create other-skill so it appears in skill_names
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)
    errors = _mod.validate(tmp_path)
    assert any("exact skill-name mentions" in e for e in errors)
