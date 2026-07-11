"""Tests for validators/subprocess_text_encoding.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "subprocess_text_encoding.py"
_spec = importlib.util.spec_from_file_location("subprocess_text_encoding", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_runtime(tmp_path: Path, source: str) -> None:
    path = tmp_path / "skills" / "demo-skill" / "_rtx" / "_run_tool.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")


def test_binary_capture_output_passes(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['tool'], capture_output=True, check=False)\n",
    )

    assert _mod.validate(tmp_path) == []


def test_text_true_without_encoding_is_rejected(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['tool'], capture_output=True, text=True, check=False)\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("subprocess text mode must set both encoding and errors" in error for error in errors)


def test_universal_newlines_without_encoding_is_rejected(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['tool'], universal_newlines=True)\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("subprocess text mode must set both encoding and errors" in error for error in errors)


def test_encoding_without_errors_is_rejected(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['tool'], capture_output=True, text=True, encoding='utf-8')\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("subprocess text mode must set both encoding and errors" in error for error in errors)


def test_explicit_encoding_and_errors_pass(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(\n"
        "    ['tool'],\n"
        "    capture_output=True,\n"
        "    text=True,\n"
        "    encoding='utf-8',\n"
        "    errors='strict',\n"
        "    check=False,\n"
        ")\n",
    )

    assert _mod.validate(tmp_path) == []


def test_tests_directory_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "demo-skill" / "tests" / "test_tool.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import subprocess\n"
        "subprocess.run(['tool'], capture_output=True, text=True)\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []


def test_ignored_system_skill_directory_is_excluded(tmp_path: Path) -> None:
    path = tmp_path / "skills" / ".system" / "skill-installer" / "scripts" / "installer.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import subprocess\n"
        "subprocess.run(['git'], stdout=subprocess.PIPE, text=True)\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []
