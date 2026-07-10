"""Tests for validators/python_script_names.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.python_script_names import validate  # noqa: E402


def test_underscore_python_script_name_passes(tmp_path: Path) -> None:
    script = tmp_path / "skills" / "demo-skill" / "scripts" / "run_task.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")

    assert validate(tmp_path) == []


def test_hyphenated_python_script_name_is_rejected(tmp_path: Path) -> None:
    script = tmp_path / "skills" / "demo-skill" / "scripts" / "run-task.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('bad')\n", encoding="utf-8")

    errors = validate(tmp_path)

    assert any("rename to `run_task.py`" in error for error in errors)


def test_system_skill_cache_is_exempt(tmp_path: Path) -> None:
    script = tmp_path / "skills" / ".system" / "tool" / "scripts" / "run-task.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('system')\n", encoding="utf-8")

    assert validate(tmp_path) == []
