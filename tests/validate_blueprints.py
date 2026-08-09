"""Focused tests for the canonical blueprint source validator."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from v5_blueprint_fixtures import copy_v5_fixture_tree


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "validators" / "skill" / "blueprints.py"
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


def _copy_canonical_skill(repo_root: Path) -> Path:
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


@pytest.mark.parametrize("version", [4, 5])
def test_repository_schema_version_reads_canonical_bootstrap_marker(
    tmp_path: Path,
    version: int,
) -> None:
    marker = tmp_path / "references" / "blueprint" / "blueprint.yaml"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        f"schema_version: {version}\nnode_type: module\n",
        encoding="utf-8",
    )

    assert MOD.repository_schema_version(tmp_path) == version


def test_repository_schema_version_rejects_missing_marker(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="canonical schema marker is missing"):
        MOD.repository_schema_version(tmp_path)


def test_canonical_skill_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_canonical_skill(tmp_path)
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    assert MOD.validate(tmp_path) == []


def test_preflight_explicitly_selects_v5_for_an_all_v5_staged_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_schema_root(tmp_path)
    fixture = REPO_ROOT / "tests" / "fixtures" / "blueprint_v5" / "authorization"
    copy_v5_fixture_tree(fixture / "modules", tmp_path / "modules")
    copy_v5_fixture_tree(fixture / "skills", tmp_path / "skills")
    monkeypatch.setattr(MOD, "_validate_generated_markers", lambda _path: [])

    errors, graph = MOD.preflight(tmp_path, expected_schema_version=5)

    assert errors == []
    assert graph is not None
    assert graph.schema_version == 5


def test_preflight_defaults_to_v6_for_an_all_v6_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_schema_root(tmp_path)
    target = tmp_path / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    monkeypatch.setattr(MOD, "_validate_generated_markers", lambda _path: [])

    errors, graph = MOD.preflight(tmp_path)

    assert errors == []
    assert graph is not None
    assert graph.schema_version == 6


def test_full_v5_validate_checks_generated_views_with_v5_syncer_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_schema_root(tmp_path)
    fixture = REPO_ROOT / "tests" / "fixtures" / "blueprint_v5" / "authorization"
    copy_v5_fixture_tree(fixture / "modules", tmp_path / "modules")
    copy_v5_fixture_tree(fixture / "skills", tmp_path / "skills")
    sync_script = (
        tmp_path
        / "skills"
        / "skill-maker"
        / "_rtx"
        / "_blueprint_syncer.py"
    )
    sync_script.parent.mkdir(parents=True)
    sync_script.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(MOD, "_validate_generated_markers", lambda _path: [])
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))
    monkeypatch.setattr(
        MOD,
        "_validate_authored_input_files",
        lambda _graph, _root, _tracked_files: [],
    )

    def _run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MOD.subprocess, "run", _run)

    assert MOD.validate(tmp_path, expected_schema_version=5) == []
    assert commands == [
        [
            MOD.sys.executable,
            str(sync_script),
            "--check",
            "--schema-version",
            "5",
        ]
    ]


def test_default_validate_uses_v6_syncer_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_canonical_skill(tmp_path)
    sync_script = (
        tmp_path
        / "skills"
        / "skill-maker"
        / "_rtx"
        / "_blueprint_syncer.py"
    )
    sync_script.parent.mkdir(parents=True)
    sync_script.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    def _run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MOD.subprocess, "run", _run)

    assert MOD.validate(tmp_path) == []
    assert commands == [
        [
            MOD.sys.executable,
            str(sync_script),
            "--check",
            "--schema-version",
            "6",
        ]
    ]


def test_canonical_skill_passes_through_equivalent_repository_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_parent = tmp_path / "physical"
    repository = physical_parent / "repository"
    _copy_canonical_skill(repository)
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
    skill = _copy_canonical_skill(tmp_path)
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
    skill = _copy_canonical_skill(tmp_path)
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
    _copy_canonical_skill(tmp_path)
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: {})

    errors = MOD.validate(tmp_path)

    assert any("not tracked by git" in error for error in errors)


def test_cx_command_requires_stage_zero_executable_mode() -> None:
    tracked = {
        "skills/demo-skill/_cx/run-task": (("100644", "0"),),
        "skills/demo-skill/_cx/run-other": (("100755", "0"),),
    }

    errors = MOD._validate_command_file_modes(tracked)

    assert errors == [
        "skills/demo-skill/_cx/run-task: _cx command file must have "
        "one stage-0 executable Git index entry"
    ]


def test_malformed_v4_source_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _copy_canonical_skill(tmp_path)
    (skill / "blueprints" / "gateway.yaml").write_text(
        "schema_version: 4\nnode_type: [\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(MOD, "_git_tracked_files", lambda _root: _tracked(tmp_path))

    errors = MOD.validate(tmp_path)

    assert any("gateway.yaml" in error for error in errors)
