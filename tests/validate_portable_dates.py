"""Tests for validators/portable_dates.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.portable_dates import validate  # noqa: E402


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_explicit_python_date_formatting_passes_for_shared_helper(tmp_path: Path) -> None:
    script = tmp_path / "src" / "officina" / "common" / "dates.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "def format_date_key(date_value):\n"
        "    return f'{date_value.month}-{date_value.day}-{date_value.year % 100:02d}'\n",
        encoding="utf-8",
    )
    assert validate(tmp_path) == []


def test_local_explicit_python_date_formatting_passes(tmp_path: Path) -> None:
    script = tmp_path / "skills" / "daily-plan" / "_rtx" / "_day_model.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "def format_plan_date_key(date_value):\n"
        "    return f'{date_value.month}-{date_value.day}-{date_value.year % 100:02d}'\n",
        encoding="utf-8",
    )
    assert validate(tmp_path) == []


def test_gnu_strftime_padding_modifier_is_rejected_in_skill_runtime(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "daily-plan"
    script = skill / "_rtx" / "_day_model.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from datetime import datetime\n"
        "def get_today_date():\n"
        "    return datetime.now().strftime('%-m-%-d-%y')\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("non-portable strftime directive `%-m`" in error for error in errors)
    assert any("non-portable strftime directive `%-d`" in error for error in errors)


def test_windows_strftime_padding_modifier_is_rejected(tmp_path: Path) -> None:
    script = tmp_path / "skills" / "my-skill" / "_rtx" / "run.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "from datetime import datetime\n"
        "label = datetime.now().strftime('%#m-%#d-%y')\n",
        encoding="utf-8",
    )

    errors = validate(tmp_path)

    assert any("non-portable strftime directive `%#m`" in error for error in errors)
    assert any("non-portable strftime directive `%#d`" in error for error in errors)
