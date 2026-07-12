"""Smoke tests for skills/skill-maker/validators/dependencies.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

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
    body: str = "",
    blueprint_uses: list[str] | None = None,
) -> Path:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    skill_md = f"---\nname: {name}\n---\n{body}\n"
    (skill_dir / "SKILL.md").write_text(skill_md)
    uses = blueprint_uses or []
    default = {
        "version": 1,
        "description": "Primary LLM-facing skill instructions.",
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "directly_reads": ["SKILL.md"],
        "directly_executes": [],
        "directly_writes": [],
    }
    if uses:
        default["uses_interfaces"] = [
            {"interface": f"{dep}.llm.default", "version": 1} for dep in uses
        ]
    (skill_dir / "blueprint.yaml").write_text(yaml.dump({"interfaces": {"llm": {"default": default}}}))
    return skill_dir


def test_no_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_valid_no_deps_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "my-skill")
    assert _mod.validate(tmp_path) == []


def test_missing_dependency_block_is_allowed(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\nBody.\n")
    (skill_dir / "blueprint.yaml").write_text("interfaces: {}\n")
    assert _mod.validate(tmp_path) == []


def test_body_mentions_not_in_sidecar_flagged(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "my-skill",
        body="Use other-skill for this.",
    )
    # Create other-skill so it appears in skill_names
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)
    errors = _mod.validate(tmp_path)
    assert any("exact skill-name mentions" in e for e in errors)


def test_body_mentions_declared_interface_use_pass(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "my-skill",
        body="Use other-skill for this.",
        blueprint_uses=["other-skill"],
    )
    (tmp_path / "skills" / "other-skill").mkdir(parents=True)
    assert _mod.validate(tmp_path) == []
