from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from test_support.runtime_module import load_runtime_module


def _package(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / "_rtx"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_helper.py").write_text("VALUE = 'bound'\n", encoding="utf-8")
    gateway = package / "_gateway.py"
    gateway.write_text(
        "from ._helper import VALUE\n\n"
        "def main(argv=None):\n"
        "    print(VALUE, *(argv or []))\n"
        "    return 0\n",
        encoding="utf-8",
    )
    return package, gateway


def test_load_runtime_module_preserves_relative_package_imports(
    tmp_path: Path,
) -> None:
    _package_root, gateway = _package(tmp_path)

    module = load_runtime_module(gateway)

    assert module.VALUE == "bound"


def test_runtime_module_helper_executes_main_in_package_context(
    tmp_path: Path,
) -> None:
    package_root, gateway = _package(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "test_support.runtime_module",
            str(gateway),
            "--",
            "probe",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "bound probe"
