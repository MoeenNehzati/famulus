"""Tests for the shared Python machine-interface runner."""
from __future__ import annotations

import ast
import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.certification_view import CertificationDecision  # noqa: E402
import officina.runtime.python_machine_interface as python_interface  # noqa: E402
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


class _PassingCertificationView:
    def check_export(
        self,
        module_id: str,
        interface_id: str,
        interface_version: int,
        source_node_id: str | None,
    ) -> CertificationDecision:
        return CertificationDecision(True, "current", "Current test certificate.")


def _v4_runtime_contract() -> dict[str, object]:
    return {
        "arguments": {},
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "result",
                "audience": "machine",
                "description": "Result.",
                "direct_io_ref": "stdout",
                "type": {"kind": "string"},
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "Never empty.",
            }
        ],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One invocation."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "formats": ["text"],
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _write_v4_runtime_module(
    repo: Path,
    name: str,
    *,
    target: str | None = None,
    allowed_callers: tuple[str, ...] = (),
) -> None:
    module = repo / "skills" / name
    runtime = module / "_rtx"
    blueprints = module / "blueprints"
    runtime.mkdir(parents=True)
    blueprints.mkdir()
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    dispatch_source = ""
    uses_interfaces: list[dict[str, object]] = []
    if target is not None:
        target_module = target.split(".interface.", 1)[0]
        dispatch_source = (
            "    dispatches = {\n"
            "        'next': DispatchCall(\n"
            f"            caller_skill='{name}',\n"
            f"            target_skill='{target_module}',\n"
            f"            interface='{target}',\n"
            "        )\n"
            "    }\n"
        )
        uses_interfaces.append({"interface": target, "version": 1})
    (runtime / "_worker.py").write_text(
        "from officina.runtime.python_machine_interface import (\n"
        "    DispatchCall,\n"
        "    PythonMachineInterface,\n"
        ")\n"
        "class Interface(PythonMachineInterface):\n"
        f"{dispatch_source}"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    source_id = f"{name}.source.worker"
    source_interface = f"{source_id}.interface.run"
    (blueprints / "worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "description": "Worker.",
                "gateway": {"path": "_rtx/_worker.py", "language": "Python"},
                "content": [r"_rtx/.*\.py"],
                "platform_support": {
                    "linux": True,
                    "macos": True,
                    "windows": True,
                },
                "runtime_dependencies": [],
                "dependencies": [],
                "uses_interfaces": uses_interfaces,
                "interfaces": {
                    source_interface: {
                        "version": 1,
                        "description": "Run.",
                        "contract": _v4_runtime_contract(),
                        "process_binding": {
                            "kind": "process",
                            "entry": "Interface",
                            "arguments": {},
                            "fixed": [],
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (module / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "module",
                "id": name,
                "version": 1,
                "description": "Runtime module.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md", r"_rtx/(?:__init__|_worker)\.py"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    source_id: {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/worker.yaml",
                        }
                    }
                },
                "exports": {
                    f"{name}.interface.run": {
                        "source_interface": source_interface,
                        "access": {
                            "allow_all_modules": False,
                            "allowed_callers": list(allowed_callers),
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_dispatch_call_analyzer_resolves_aliases_in_nested_imports() -> None:
    tree = ast.parse(
        "try:\n"
        "    from officina.runtime.python_machine_interface import DispatchCall as Call\n"
        "except ImportError:\n"
        "    from officina.runtime.python_machine_interface import DispatchCall as Call\n"
        "import officina.runtime.python_machine_interface as pmi\n"
        "import officina.runtime.python_machine_interface\n"
        "TARGET = 'cloud-files'\n"
        "call = Call(caller_skill='daily-plan', target_skill=TARGET, interface='read')\n"
        "aliased = pmi.DispatchCall(caller_skill='daily-plan', target_skill=TARGET, interface='write')\n"
        "qualified = officina.runtime.python_machine_interface.DispatchCall(\n"
        "    caller_skill='daily-plan', target_skill=TARGET, interface='delete'\n"
        ")\n"
    )

    declarations = python_interface.analyze_dispatch_call_declarations(tree)

    assert len(declarations) == 3
    assert declarations[0].caller_skill == "daily-plan"
    assert declarations[0].target_skill == "cloud-files"
    assert declarations[0].interface == "read"
    assert [item.interface for item in declarations] == ["read", "write", "delete"]


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


def test_route_smoke_trace_supports_temporary_repository(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    write_interface(runtime / "_demo.py")

    paths = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        "_rtx/_demo.py:Interface",
    )

    assert (runtime / "_demo.py").resolve() in paths
    assert any(path.name == "python_machine_interface.py" for path in paths)


def test_route_smoke_trace_prefers_candidate_local_officina_source(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    write_interface(runtime / "_demo.py")
    live_source = Path(python_interface.__file__).resolve().parents[2]
    candidate_source = tmp_path / "src"
    shutil.copytree(live_source / "officina", candidate_source / "officina")

    paths = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        "_rtx/_demo.py:Interface",
    )

    assert (candidate_source / "officina" / "__init__.py").resolve() in paths
    assert (live_source / "officina" / "__init__.py").resolve() not in paths


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


def test_load_interface_uses_bound_source_snapshot_after_final_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    entrypoint = runtime / "_demo.py"
    write_interface(entrypoint)
    source_fd = os.open(entrypoint, os.O_RDONLY)
    entrypoint.replace(runtime / "trusted.py")
    entrypoint.write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    marker = 'untrusted'\n"
        "    def run(self, args): return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    try:
        interface = load_interface("_rtx/_demo.py:Interface", source_fd=source_fd)
    finally:
        os.close(source_fd)

    assert interface.__class__.__name__ == "Interface"
    assert not hasattr(interface, "marker")


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


def test_declared_dispatch_rejects_local_interface_alias() -> None:
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

    with pytest.raises(
        ValueError,
        match=r"fully qualified `<module>\.interface\.<name>`",
    ):
        Interface().run(None)


def test_declared_dispatch_uses_generic_export_id_without_legacy_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("officina.dispatcher.dispatch", fake_dispatch)

    class Interface(PythonMachineInterface):
        dispatches = {
            "read": DispatchCall(
                caller_skill="demo-skill",
                target_skill="cloud-files",
                interface="cloud-files.interface.read",
            )
        }

    assert Interface().dispatch("read") == "ok"
    assert captured["target"] == "cloud-files.interface.read"
    assert "script_interface" not in captured
    assert "target_skill" not in captured


def test_dependency_resolver_uses_private_trace_certification_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification_view = object()
    sentinel = object()
    captured = {}

    def resolve_for_trace(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "officina.dispatcher.core._resolve_dispatch_metadata_for_trace",
        resolve_for_trace,
    )
    call = DispatchCall(
        caller_skill="demo-skill",
        target_skill="cloud-files",
        interface="cloud-files.interface.read",
    )

    result = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=certification_view,
    ).resolve_call(call)

    assert result is sentinel
    assert captured["target"] == "cloud-files.interface.read"
    assert captured["certification_view"] is certification_view


def test_dependency_resolver_collects_transitive_v4_dispatches(tmp_path: Path) -> None:
    _write_v4_runtime_module(
        tmp_path,
        "source-skill",
        target="middle-skill.interface.run",
    )
    _write_v4_runtime_module(
        tmp_path,
        "middle-skill",
        target="leaf-skill.interface.run",
        allowed_callers=("source-skill",),
    )
    _write_v4_runtime_module(
        tmp_path,
        "leaf-skill",
        allowed_callers=("middle-skill",),
    )

    class SourceInterface(PythonMachineInterface):
        dispatches = {
            "middle": DispatchCall(
                caller_skill="source-skill",
                target_skill="middle-skill",
                interface="middle-skill.interface.run",
            )
        }

    dependencies = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=_PassingCertificationView(),
    ).collect(SourceInterface())

    assert [(item.key, item.resolved.target) for item in dependencies] == [
        ("middle", "middle-skill.interface.run"),
        ("next", "leaf-skill.interface.run"),
    ]
    assert [item.depth for item in dependencies] == [0, 1]


def test_repeated_v4_dependency_collection_does_not_retain_file_descriptors(
    tmp_path: Path,
) -> None:
    proc_fds = Path("/proc/self/fd")
    if not proc_fds.is_dir():
        # famulus-skip: category=platform-contract; reason=FD enumeration requires procfs; alternate=metadata resolver tests cover deterministic closure
        pytest.skip("descriptor-count assertion requires /proc/self/fd")
    _write_v4_runtime_module(
        tmp_path,
        "source-skill",
        target="target-skill.interface.run",
    )
    _write_v4_runtime_module(
        tmp_path,
        "target-skill",
        allowed_callers=("source-skill",),
    )

    class SourceInterface(PythonMachineInterface):
        dispatches = {
            "target": DispatchCall(
                caller_skill="source-skill",
                target_skill="target-skill",
                interface="target-skill.interface.run",
            )
        }

    resolver = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=_PassingCertificationView(),
    )
    before = len(list(proc_fds.iterdir()))
    retained_results = [resolver.collect(SourceInterface()) for _ in range(20)]
    after = len(list(proc_fds.iterdir()))

    assert retained_results
    assert after == before


def test_main_reports_bad_interface_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["not-a-spec"]) == 2
    assert "interface spec must be" in capsys.readouterr().err
