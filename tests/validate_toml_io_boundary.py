from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validators.toml_io_boundary import validate  # noqa: E402


def _write_runtime_file(tmp_path: Path, content: str, rel: str = "skills/demo/scripts/run.py") -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_toml_io_open_literal_filename_passes(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from officina.common import toml_io\n"
        'with toml_io.open(base, "config.toml", "r") as f:\n'
        "    f.read()\n",
    )
    assert validate(tmp_path) == []


def test_toml_io_open_f_string_filename_passes(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from officina.common import toml_io\n"
        'with toml_io.open(base, f"{agent}.config.toml", "r") as f:\n'
        "    f.read()\n",
    )
    assert validate(tmp_path) == []


def test_direct_toml_path_literal_is_rejected(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from pathlib import Path\n"
        'path = Path("config.toml")\n',
    )
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "direct filename argument to toml_io.open" in errors[0]


def test_toml_filename_variable_is_rejected_at_assignment(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from officina.common import toml_io\n"
        'name = "config.toml"\n'
        'with toml_io.open(base, name, "r") as f:\n'
        "    f.read()\n",
    )
    errors = validate(tmp_path)
    assert len(errors) == 2
    assert any("may only appear" in error for error in errors)
    assert any("filename must be" in error for error in errors)


def test_concatenated_toml_filename_is_rejected_even_inside_open(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from officina.common import toml_io\n"
        'with toml_io.open(base, "config" + ".toml", "r") as f:\n'
        "    f.read()\n",
    )
    errors = validate(tmp_path)
    assert len(errors) == 2


def test_open_with_nonliteral_variable_filename_is_rejected(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        "from officina.common import toml_io\n"
        "name = get_name()\n"
        'with toml_io.open(base, name, "r") as f:\n'
        "    f.read()\n",
    )
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "filename must be" in errors[0]


def test_docstring_toml_mentions_are_ignored(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        '"""Mentions config.toml for documentation."""\n'
        "def run():\n"
        "    return None\n",
    )
    assert validate(tmp_path) == []


def test_toml_io_module_is_exempt(tmp_path: Path) -> None:
    _write_runtime_file(
        tmp_path,
        'NAME = "config.toml"\n',
        rel="src/officina/common/toml_io.py",
    )
    assert validate(tmp_path) == []
