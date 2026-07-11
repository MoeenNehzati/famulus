"""Tests for validators/skill_runtime_doc_references.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_public_interface_name_passes(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    (skill / "SKILL.md").write_text("Use the `read-calendar` interface.\n", encoding="utf-8")

    assert _mod.validate(tmp_path) == []


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
