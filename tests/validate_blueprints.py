"""Focused tests for the version-4 blueprint source validator."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "skills" / "skill-maker" / "validators" / "blueprints.py"
SPEC = importlib.util.spec_from_file_location("blueprints_validator", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def _copy_schema_root(repo_root: Path) -> None:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )


def _copy_v4_skill(repo_root: Path) -> Path:
    _copy_schema_root(repo_root)
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def _tracked(repo_root: Path) -> dict[str, tuple[tuple[str, str], ...]]:
    return {
        path.relative_to(repo_root).as_posix(): (("100644", "0"),)
        for path in repo_root.rglob("*")
        if path.is_file()
    }


def test_v4_skill_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_v4_skill(tmp_path)
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    assert MOD.validate(tmp_path) == []


def test_v4_skill_passes_through_equivalent_repository_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_parent = tmp_path / "physical"
    repository = physical_parent / "repository"
    _copy_v4_skill(repository)
    alias_parent = tmp_path / "alias"
    try:
        alias_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError:
        # famulus-skip: category=platform-contract; reason=some Windows runners deny directory-symlink creation; alternate=Linux and macOS exercise the validator-alias regression
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setattr(
        MOD,
        "_git_tracked_files",
        lambda _root: _tracked(repository),
    )

    assert MOD.validate(alias_parent / "repository") == []


def test_skill_file_requires_module_blueprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_schema_root(tmp_path)
    skill = tmp_path / "skills" / "missing-blueprint"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: missing-blueprint\n---\n")
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    errors = MOD.validate(tmp_path)

    assert any("missing blueprint.yaml" in error for error in errors)


def test_canonical_module_marker_requires_module_node_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _copy_v4_skill(tmp_path)
    declaration = yaml.safe_load(
        (skill / "blueprint.yaml").read_text(encoding="utf-8")
    )
    declaration["node_type"] = "behavioral_source"
    (skill / "blueprint.yaml").write_text(
        yaml.safe_dump(declaration),
        encoding="utf-8",
    )
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    errors = MOD.validate(tmp_path)

    assert any("node_type module" in error for error in errors)


def test_unbalanced_generated_markers_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _copy_v4_skill(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            MOD.CONTRACT_END,
            "",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    errors = MOD.validate(tmp_path)

    assert any("contract markers are unbalanced" in error for error in errors)


def test_authored_inputs_must_be_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_v4_skill(tmp_path)
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: {})

    errors = MOD.validate(tmp_path)

    assert any("not tracked by git" in error for error in errors)


def test_malformed_v4_source_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _copy_v4_skill(tmp_path)
    (skill / "blueprints" / "gateway.yaml").write_text(
        "schema_version: 4\nnode_type: [\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    errors = MOD.validate(tmp_path)

    assert any("gateway.yaml" in error for error in errors)
