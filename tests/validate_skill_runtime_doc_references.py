"""Tests for validators/skill_runtime_doc_references.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "skill_runtime_doc_references.py"
_spec = importlib.util.spec_from_file_location("skill_runtime_doc_references", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "_rtx" / "_Calendar_Gateway.py").write_text("# runtime\n", encoding="utf-8")
    return skill


def _write_module_blueprint(skill: Path, export_id: str) -> None:
    (skill / "blueprint.yaml").write_text(
        "schema_version: 5\n"
        "node_type: module\n"
        "id: demo-skill\n"
        "exports:\n"
        f"  {export_id}: {{}}\n",
        encoding="utf-8",
    )


def test_public_interface_name_passes(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Use the `read-calendar` interface.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []


def test_declared_public_interface_id_may_match_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(skill, "demo-skill.interface.calendar-gateway")
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []


def test_noncanonical_export_cannot_mask_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(skill, "calendar-gateway")
    (skill / "SKILL.md").write_text("Use `calendar-gateway`.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_malformed_module_blueprint_cannot_mask_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "blueprint.yaml").write_text(
        "schema_version: 4\n"
        "node_type: module\n"
        "id: demo-skill\n"
        "exports:\n"
        "  demo-skill.interface.calendar-gateway: {}\n",
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_public_id_does_not_mask_adjacent_private_runtime_stem(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _write_module_blueprint(skill, "demo-skill.interface.calendar-gateway")
    (skill / "SKILL.md").write_text(
        "Use `demo-skill.interface.calendar-gateway`, not `calendar-gateway`.\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_private_runtime_directory_name_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run _rtx directly.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention `_rtx`" in error for error in errors)


def test_suffix_qualified_runtime_file_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run _Calendar_Gateway.py.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention runtime file" in error for error in errors)


def test_private_stem_with_underscore_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run Calendar_Gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_private_stem_as_words_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run the calendar gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_private_stem_with_hyphen_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Run calendar-gateway.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_Calendar_Gateway`" in error for error in errors)


def test_nested_runtime_package_name_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    package = skill / "_rtx" / "_install_launcher"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_windows_launcher.py").write_text("# runtime\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("Use install launcher internals.\n", encoding="utf-8")

    errors = _mod.validate(tmp_path)

    assert any("must not mention private runtime name `_install_launcher`" in error for error in errors)


def test_assets_markdown_is_exempt(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "assets" / "README.md").parent.mkdir()
    (skill / "assets" / "README.md").write_text("Run install.sh.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []


def test_registered_child_artifacts_are_not_runtime_names_but_executable_is(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    child = skill / "_rtx"
    (child / "assets").mkdir(parents=True)
    (child / "assets" / "_artifact_name.py").write_text("", encoding="utf-8")
    (child / "tests").mkdir()
    (child / "tests" / "_test_helper.py").write_text("", encoding="utf-8")
    (child / "_Child_Gateway.py").write_text("", encoding="utf-8")
    (child / "blueprint.yaml").write_text("", encoding="utf-8")
    (child / "README.md").write_text(
        "Child Gateway is private.\n", encoding="utf-8"
    )
    (skill / "SKILL.md").write_text(
        "Artifact Name and Test Helper are ordinary prose; "
        "Child Gateway is private.\n",
        encoding="utf-8",
    )
    graph = SimpleNamespace(
        module_parents={"demo-rtx": "demo-skill"},
        nodes={
            "demo-rtx": SimpleNamespace(module_root=child),
        },
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert not any("_artifact_name" in error for error in errors)
    assert not any("_test_helper" in error for error in errors)
    assert any("_Child_Gateway" in error for error in errors)
    assert not any("README.md" in error for error in errors)
