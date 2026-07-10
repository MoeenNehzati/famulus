"""Tests for validators/skill_runtime_files.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "skill_runtime_files.py"
_spec = importlib.util.spec_from_file_location("skill_runtime_files", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# runtime\n", encoding="utf-8")


def test_private_python_runtime_name_passes(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_Calendar_Gateway.py")

    assert _mod.validate(tmp_path) == []


def test_private_shell_runtime_name_passes(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_mail_transport.sh")

    assert _mod.validate(tmp_path) == []


def test_init_file_is_exempt(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "__init__.py")

    assert _mod.validate(tmp_path) == []


def test_runtime_file_under_scripts_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "scripts" / "_calendar_gateway.py")

    errors = _mod.validate(tmp_path)

    assert any("must live under `skills/<skill>/_rtx/`" in error for error in errors)


def test_missing_leading_underscore_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "calendar_gateway.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error for error in errors)


def test_one_word_runtime_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_gcal.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error for error in errors)


def test_hyphenated_runtime_name_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_get-weather.py")

    errors = _mod.validate(tmp_path)

    assert any("runtime filename stem must match" in error for error in errors)


def test_unsupported_runtime_suffix_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_calendar_gateway.txt")

    errors = _mod.validate(tmp_path)

    assert any("unsupported runtime suffix `.txt`" in error for error in errors)


def test_case_insensitive_runtime_name_collision_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_Calendar_Gateway.py")
    _write(tmp_path / "skills" / "demo-skill" / "_rtx" / "_calendar_gateway.py")

    errors = _mod.validate(tmp_path)

    assert any("case-insensitive runtime filename collision" in error for error in errors)


def test_system_skill_cache_is_exempt(tmp_path: Path) -> None:
    _write(tmp_path / "skills" / ".system" / "tool" / "_rtx" / "run-task.py")

    assert _mod.validate(tmp_path) == []
