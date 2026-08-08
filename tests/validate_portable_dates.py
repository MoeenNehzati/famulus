"""Tests for validators/portable_dates.py."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.portable_dates import validate  # noqa: E402
from validators import portable_dates as module_under_test  # noqa: E402
from officina.common.python_source_cache import PythonSourceCache  # noqa: E402


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


def test_injected_cache_preserves_findings_and_ast(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "demo" / "runtime.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = now.strftime('%-d')\n", encoding="utf-8")
    expected = validate(tmp_path)
    source_cache = PythonSourceCache(tmp_path)
    _source, tree = source_cache.read_parse(path)
    before = ast.dump(tree, include_attributes=True)

    assert module_under_test._validate(tmp_path, source_cache) == expected
    assert ast.dump(tree, include_attributes=True) == before


def test_injected_cache_preserves_syntax_error_finding(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "demo" / "broken.py"
    path.parent.mkdir(parents=True)
    path.write_text("if:\n", encoding="utf-8")

    assert module_under_test._validate(
        tmp_path,
        PythonSourceCache(tmp_path),
    ) == validate(tmp_path)


def test_injected_cache_preserves_unicode_error(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "demo" / "broken.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError) as direct:
        validate(tmp_path)
    with pytest.raises(UnicodeDecodeError) as injected:
        module_under_test._validate(tmp_path, PythonSourceCache(tmp_path))

    assert direct.value.args == injected.value.args


def test_injected_cache_preserves_os_error(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "skills" / "demo" / "missing.py"
    monkeypatch.setattr(module_under_test, "_iter_files", lambda _root: iter([missing]))

    with pytest.raises(FileNotFoundError) as direct:
        validate(tmp_path)
    with pytest.raises(FileNotFoundError) as injected:
        module_under_test._validate(tmp_path, PythonSourceCache(tmp_path))

    assert direct.value.args == injected.value.args


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
