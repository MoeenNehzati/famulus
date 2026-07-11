"""Tests for the shared Python machine-interface runner."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.runtime.python_machine_interface_runner import (  # noqa: E402
    load_interface,
    main,
    run_python_machine_interface,
)


def write_interface(path: Path) -> None:
    path.write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def __init__(self):\n"
        "        self.ran = False\n"
        "\n"
        "    def build_parser(self):\n"
        "        parser = super().build_parser()\n"
        "        parser.add_argument('--name', required=True)\n"
        "        return parser\n"
        "\n"
        "    def route_smoke(self):\n"
        "        import json\n"
        "\n"
        "    def run(self, args):\n"
        "        self.ran = True\n"
        "        print(f'hello {args.name}')\n",
        encoding="utf-8",
    )


def test_load_interface_from_relative_file_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/_demo.py:Interface")

    assert interface.__class__.__name__ == "Interface"


def test_load_interface_preserves_package_relative_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (runtime / "_demo.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from . import VALUE\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self):\n"
        "        assert VALUE == 'ok'\n"
        "\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/_demo.py:Interface")

    assert interface.__class__.__name__ == "Interface"


def test_load_interface_ignores_conflicting_cached_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_root = tmp_path / "foreign"
    foreign_runtime = foreign_root / "_rtx"
    foreign_runtime.mkdir(parents=True)
    (foreign_runtime / "__init__.py").write_text("VALUE = 'wrong'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(foreign_root))
    sys.modules.pop("_rtx", None)
    importlib.import_module("_rtx")

    skill_root = tmp_path / "skill"
    runtime = skill_root / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (runtime / "_demo.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from . import VALUE\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def __init__(self):\n"
        "        self.value = VALUE\n"
        "\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(skill_root)

    interface = load_interface("_rtx/_demo.py:Interface")

    assert interface.value == "ok"
    sys.modules.pop("_rtx", None)
    sys.modules.pop("_rtx._demo", None)


def test_route_smoke_builds_parser_but_does_not_require_normal_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)
    interface = load_interface("_rtx/_demo.py:Interface")

    result = run_python_machine_interface(interface, ["--route-smoke"])

    assert result == 0
    assert not interface.ran
    assert capsys.readouterr().out == "route-smoke ok\n"


def test_normal_mode_parses_args_and_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)
    interface = load_interface("_rtx/_demo.py:Interface")

    result = run_python_machine_interface(interface, ["--name", "Ada"])

    assert result == 0
    assert interface.ran
    assert capsys.readouterr().out == "hello Ada\n"


def test_argv_adapter_passes_normal_args_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "_legacy.py").write_text(
        "from officina.runtime.python_machine_interface import PythonArgvMachineInterface\n"
        "\n"
        "class Interface(PythonArgvMachineInterface):\n"
        "    def run(self, argv):\n"
        "        print('|'.join(argv))\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    interface = load_interface("_rtx/_legacy.py:Interface")

    result = run_python_machine_interface(interface, ["--legacy-flag", "value"])

    assert result == 0
    assert capsys.readouterr().out == "--legacy-flag|value\n"


def test_main_reports_bad_interface_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["not-a-spec"]) == 2
    assert "interface spec must be" in capsys.readouterr().err
