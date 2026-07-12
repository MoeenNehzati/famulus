"""Tests for the shared Python machine-interface runner."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.runtime.python_machine_interface import (  # noqa: E402
    DispatchCall,
    DispatchDependencyResolver,
    PythonMachineInterface,
)
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


def test_declared_dispatch_method_uses_dispatch_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("officina.dispatcher.dispatch", fake_dispatch)

    class Interface(PythonMachineInterface):
        dispatches = {
            "read-cloud": DispatchCall(
                caller_skill="demo-skill",
                target_skill="cloud-files",
                interface="read",
            )
        }

        def run(self, args):
            return self.dispatch("read-cloud", args=["x"], stdin="payload", text=True)

    assert Interface().run(None) == "ok"
    assert captured["caller_skill"] == "demo-skill"
    assert captured["target_skill"] == "cloud-files"
    assert captured["script_interface"] == "read"
    assert captured["args"] == ["x"]
    assert captured["stdin"] == "payload"
    assert captured["text"] is True


def test_dispatch_dependency_resolver_follows_transitive_dispatches(tmp_path: Path) -> None:
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write(
        tmp_path / "skills" / "source-skill" / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: middle-skill.machine.middle\n"
        "          version: 1\n",
    )
    write(tmp_path / "skills" / "middle-skill" / "_rtx" / "__init__.py", "")
    write(
        tmp_path / "skills" / "middle-skill" / "_rtx" / "_middle.py",
        "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    dispatches = {\n"
        "        'leaf': DispatchCall(\n"
        "            caller_skill='middle-skill',\n"
        "            target_skill='leaf-skill',\n"
        "            interface='leaf',\n"
        "        )\n"
        "    }\n"
        "    def run(self, args):\n"
        "        return 0\n",
    )
    write(
        tmp_path / "skills" / "middle-skill" / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    middle:\n"
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      uses_interfaces:\n"
        "        - interface: leaf-skill.machine.leaf\n"
        "          version: 1\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_middle.py:Interface\n",
    )
    write(tmp_path / "skills" / "leaf-skill" / "_rtx" / "__init__.py", "")
    write(
        tmp_path / "skills" / "leaf-skill" / "_rtx" / "_leaf.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n",
    )
    write(
        tmp_path / "skills" / "leaf-skill" / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interfaces:\n"
        "  machine:\n"
        "    leaf:\n"
        "      version: 1\n"
        "      allowed_callers: [middle-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_leaf.py:Interface\n",
    )

    class SourceInterface(PythonMachineInterface):
        dispatches = {
            "middle": DispatchCall(
                caller_skill="source-skill",
                target_skill="middle-skill",
                interface="middle",
            )
        }

    dependencies = DispatchDependencyResolver(repo_root=tmp_path).collect(SourceInterface())

    assert [(item.key, item.resolved.target) for item in dependencies] == [
        ("middle", "middle-skill.machine.middle"),
        ("leaf", "leaf-skill.machine.leaf"),
    ]
    assert [item.depth for item in dependencies] == [0, 1]


def test_main_reports_bad_interface_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["not-a-spec"]) == 2
    assert "interface spec must be" in capsys.readouterr().err
