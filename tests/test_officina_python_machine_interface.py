"""Tests for the shared Python machine-interface runner."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common.certification_view import CertificationDecision  # noqa: E402
from officina.common.blueprint_graph import (  # noqa: E402
    descriptor_safe_open_supported,
    load_repository_blueprint_graph,
)
from officina.dispatcher.core import ResolvedInvocationMetadata  # noqa: E402
import officina.dispatcher.core as dispatcher_core  # noqa: E402
import officina.runtime.python_machine_interface as python_interface  # noqa: E402
import officina.runtime.python_machine_interface_runner as python_runner  # noqa: E402
from officina.runtime.python_machine_interface import (  # noqa: E402
    DispatchCall,
    DispatchDependencyResolver,
    PythonMachineInterface,
    PythonProcessTarget,
    PythonProcessTargetError,
)
from officina.runtime.python_machine_interface_runner import (  # noqa: E402
    InterfaceLoadError,
    load_interface,
    main,
    run_python_machine_interface,
)

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"
V4_SCHEMA_ROOT = SCHEMA_ROOT / "migrations" / "v4"


@pytest.fixture(autouse=True)
def _select_frozen_v4_for_historical_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_load = dispatcher_core.load_repository_blueprint_graph

    def load_fixture_graph(
        root: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if "expected_schema_version" not in kwargs:
            for marker in Path(root).rglob("blueprint.yaml"):
                document = yaml.safe_load(marker.read_text(encoding="utf-8"))
                if isinstance(document, dict) and document.get("schema_version") == 4:
                    kwargs["expected_schema_version"] = 4
                    kwargs["schema_root"] = V4_SCHEMA_ROOT
                    break
        return real_load(root, *args, **kwargs)

    monkeypatch.setattr(
        dispatcher_core,
        "load_repository_blueprint_graph",
        load_fixture_graph,
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


def _target(
    gateway_path: str = "_rtx/_demo.py",
    process_entry: str = "Interface",
) -> PythonProcessTarget:
    return PythonProcessTarget(Path(gateway_path), process_entry)


def _logical_target(module_id: str) -> PythonProcessTarget:
    package = python_interface.logical_python_package_name(module_id)
    return PythonProcessTarget(
        Path("runtime.py"),
        "Interface",
        logical_package=package,
        logical_entrypoint=f"{package}.runtime",
    )


def _write_logical_runtime(
    module_root: Path,
    *,
    value: str,
) -> None:
    module_root.mkdir(parents=True)
    (module_root / "__init__.py").write_text("", encoding="utf-8")
    (module_root / "helper.py").write_text(
        f"VALUE = {value!r}\n",
        encoding="utf-8",
    )
    (module_root / "late_helper.py").write_text(
        f"LATE_VALUE = 'late-{value}'\n",
        encoding="utf-8",
    )
    (module_root / "resource.txt").write_text(
        f"resource-{value}",
        encoding="utf-8",
    )
    (module_root / "runtime.py").write_text(
        "from pathlib import Path\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from .helper import VALUE\n"
        "class Interface(PythonMachineInterface):\n"
        "    value = VALUE\n"
        "    physical_file = Path(__file__).resolve()\n"
        "    compiled_file = Path(__code__.co_filename).resolve() if False else None\n"
        "    resource = Path(__file__).with_name('resource.txt').read_text(encoding='utf-8')\n"
        "    def route_smoke(self):\n"
        "        assert self.value\n"
        "    def run(self, args):\n"
        "        from .late_helper import LATE_VALUE\n"
        "        self.late_value = LATE_VALUE\n"
        "        return 0\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("gateway_path", "process_entry"),
    [
        (Path("worker.py"), "Interface"),
        (Path("../_rtx/worker.py"), "Interface"),
        (Path("_rtx/worker.txt"), "Interface"),
        (Path("/_rtx/worker.py"), "Interface"),
        (Path("_rtx/worker.py"), "module.Interface"),
        (Path("_rtx/worker.py"), ""),
    ],
)
def test_python_process_target_rejects_noncanonical_fields(
    gateway_path: Path,
    process_entry: str,
) -> None:
    with pytest.raises(PythonProcessTargetError):
        PythonProcessTarget(gateway_path, process_entry)


def test_logical_process_target_accepts_module_root_relative_gateway() -> None:
    package = python_interface.logical_python_package_name("demo-rtx")

    target = PythonProcessTarget(
        Path("runtime.py"),
        "Interface",
        logical_package=package,
        logical_entrypoint=f"{package}.runtime",
    )

    assert target.gateway_path == Path("runtime.py")
    assert target.logical_package == package
    assert target.logical_entrypoint == f"{package}.runtime"


def test_logical_package_identity_is_injective_and_import_safe() -> None:
    first = python_interface.logical_python_package_name("alpha-rtx")
    second = python_interface.logical_python_package_name("alpha2-rtx")

    assert first != second
    assert first.isidentifier()
    assert second.isidentifier()
    assert bytes.fromhex(first.removeprefix("_officina_module_")).decode(
        "utf-8"
    ) == "alpha-rtx"


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


def test_dispatch_call_analyzer_reads_canonical_v5_module_ids() -> None:
    tree = ast.parse(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "call = DispatchCall(\n"
        "    caller_module_id='demo-rtx',\n"
        "    target_module_id='cloud-files-rtx',\n"
        "    interface='read',\n"
        ")\n"
    )

    declaration = python_interface.analyze_dispatch_call_declarations(tree)[0]

    assert declaration.caller_module_id == "demo-rtx"
    assert declaration.target_module_id == "cloud-files-rtx"
    assert declaration.interface == "read"
    assert declaration.legacy_v4 is False


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


def write_traced_interface(skill_root: Path, marker: str) -> None:
    runtime = skill_root / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_dependency.py").write_text(
        f"MARKER = {marker!r}\n",
        encoding="utf-8",
    )
    (runtime / "_worker.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from ._dependency import MARKER\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self):\n"
        f"        assert MARKER == {marker!r}\n"
        "\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )


def write_route_smoke_worker(skill_root: Path, route_smoke_body: str) -> None:
    runtime = skill_root / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "_worker.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self):\n"
        f"{route_smoke_body}"
        "\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )


def test_load_interface_from_relative_file_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/_demo.py", "Interface")

    assert interface.__class__.__name__ == "Interface"


def test_route_smoke_trace_supports_temporary_repository(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    write_interface(runtime / "_demo.py")

    paths = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        _target(),
    )

    assert (runtime / "_demo.py").resolve() in paths
    assert any(path.name == "python_machine_interface.py" for path in paths)


def test_dependency_loader_uses_structured_target_not_command(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    resolved = ResolvedInvocationMetadata(
        caller_module_id="caller-skill",
        target_module_id="demo-skill",
        script_interface="run",
        target="demo-skill.interface.run",
        pattern="run",
        cwd=tmp_path,
        command=["opaque", "command", "without", "target"],
        stdin=False,
        python_target=_target(),
    )

    interface = DispatchDependencyResolver(
        repo_root=tmp_path,
    ).load_resolved_python_interface(resolved)

    assert interface is not None
    assert interface.__class__.__name__ == "Interface"


def test_route_smoke_batch_uses_one_child_and_isolates_loaded_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "skills" / "first-skill"
    second = tmp_path / "skills" / "second-skill"
    write_traced_interface(first, "first")
    write_traced_interface(second, "second")
    target = _target("_rtx/_worker.py")
    child_calls: list[list[str]] = []
    real_run = python_interface.subprocess.run

    def counting_run(*args: object, **kwargs: object):
        child_calls.append(list(args[0]))  # type: ignore[arg-type]
        return real_run(*args, **kwargs)

    monkeypatch.setattr(python_interface.subprocess, "run", counting_run)
    batch_tracer = getattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        None,
    )
    assert callable(batch_tracer)

    traces = batch_tracer(
        tmp_path,
        [
            (second, target),
            (first, target),
            (first.resolve(), target),
        ],
    )

    first_key = (first.resolve(), target)
    second_key = (second.resolve(), target)
    assert child_calls and len(child_calls) == 1
    assert list(traces) == [first_key, second_key]
    assert (first / "_rtx" / "_dependency.py").resolve() in traces[first_key]
    assert (second / "_rtx" / "_dependency.py").resolve() not in traces[first_key]
    assert (second / "_rtx" / "_dependency.py").resolve() in traces[second_key]
    assert (first / "_rtx" / "_dependency.py").resolve() not in traces[second_key]


def test_v4_route_smoke_trace_reports_loaded_not_merely_bound_sources(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    write_traced_interface(skill, "loaded")
    unused = skill / "_rtx" / "_unused.py"
    unused.write_text("UNUSED = True\n", encoding="utf-8")
    target = _target("_rtx/_worker.py")

    paths = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        target,
    )

    assert (skill / "_rtx" / "_worker.py").resolve() in paths
    assert (skill / "_rtx" / "_dependency.py").resolve() in paths
    assert unused.resolve() not in paths


def test_route_smoke_batch_isolates_lazy_officina_imports_between_specs(
    tmp_path: Path,
) -> None:
    source_package = tmp_path / "src" / "officina"
    source_package.mkdir(parents=True)
    current_package = Path(__file__).resolve().parents[1] / "src" / "officina"
    (source_package / "__init__.py").write_text(
        f"__path__.append({str(current_package)!r})\n",
        encoding="utf-8",
    )
    marker = source_package / "_route_smoke_test_marker.py"
    marker.write_text("MARKER = True\n", encoding="utf-8")
    first = tmp_path / "skills" / "a-skill"
    second = tmp_path / "skills" / "b-skill"
    write_route_smoke_worker(
        first,
        "        import officina._route_smoke_test_marker\n",
    )
    write_route_smoke_worker(second, "        pass\n")
    target = _target("_rtx/_worker.py")

    traces = python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((first, target), (second, target)),
    )

    assert marker.resolve() in traces[(first.resolve(), target)]
    assert marker.resolve() not in traces[(second.resolve(), target)]


def test_route_smoke_batch_retraces_shared_lazy_officina_import_per_spec(
    tmp_path: Path,
) -> None:
    source_package = tmp_path / "src" / "officina"
    source_package.mkdir(parents=True)
    current_package = Path(__file__).resolve().parents[1] / "src" / "officina"
    (source_package / "__init__.py").write_text(
        f"__path__.append({str(current_package)!r})\n",
        encoding="utf-8",
    )
    marker = source_package / "_route_smoke_test_marker.py"
    marker.write_text("MARKER = True\n", encoding="utf-8")
    first = tmp_path / "skills" / "a-skill"
    second = tmp_path / "skills" / "b-skill"
    for skill in (first, second):
        write_route_smoke_worker(
            skill,
            "        from officina import _route_smoke_test_marker\n",
        )
    target = _target("_rtx/_worker.py")

    traces = python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((first, target), (second, target)),
    )

    assert marker.resolve() in traces[(first.resolve(), target)]
    assert marker.resolve() in traces[(second.resolve(), target)]


def test_route_smoke_batch_restores_cwd_and_sys_path_between_specs(
    tmp_path: Path,
) -> None:
    first = tmp_path / "skills" / "a-skill"
    second = tmp_path / "skills" / "b-skill"
    leaked_path = (tmp_path / "leaked-path").as_posix()
    write_route_smoke_worker(
        first,
        "        import os, sys\n"
        f"        os.chdir({str(tmp_path)!r})\n"
        f"        sys.path.insert(0, {leaked_path!r})\n",
    )
    write_route_smoke_worker(
        second,
        "        import sys\n"
        "        from pathlib import Path\n"
        "        assert Path.cwd() == Path(__file__).resolve().parents[1]\n"
        f"        assert {leaked_path!r} not in sys.path\n",
    )
    target = _target("_rtx/_worker.py")

    traces = python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((first, target), (second, target)),
    )

    assert set(traces) == {
        (first.resolve(), target),
        (second.resolve(), target),
    }


def test_route_smoke_batch_rejects_invalid_blueprint_outside_skills(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    write_route_smoke_worker(skill, "        pass\n")
    invalid_module = tmp_path / "components" / "invalid-module"
    invalid_module.mkdir(parents=True)
    (invalid_module / "GATEWAY.md").write_text("Gateway.\n", encoding="utf-8")
    (invalid_module / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "module",
                "id": "invalid-module",
                "version": 1,
                "gateway": {
                    "path": "GATEWAY.md",
                    "language": "Markdown",
                },
                "content": [r"GATEWAY\.md"],
                "authority": {"owns_filesystem": []},
                "sources": {},
                "exports": {
                    "invalid-module.interface.run": {
                        "source_interface": "missing.source.interface.run",
                        "access": {
                            "allow_all_modules": True,
                            "allowed_callers": [],
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        python_interface.PythonRouteSmokeTraceError,
        match="schema error",
    ):
        python_interface.trace_python_route_smoke_dependencies_batch(
            tmp_path,
            ((skill, _target("_rtx/_worker.py")),),
            expected_schema_version=4,
            schema_root=V4_SCHEMA_ROOT,
        )


def test_route_smoke_batch_rejects_noncanonical_child_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    write_route_smoke_worker(skill, "        pass\n")
    target = _target("_rtx/_worker.py")
    payload = [
        {
            "skill_root": "skills/demo-skill",
            "python_target": {
                "gateway_path": target.gateway_path.as_posix(),
                "process_entry": target.process_entry,
            },
            "paths": [(skill / "_rtx" / "_worker.py").resolve().as_posix()],
        }
    ]

    monkeypatch.setattr(
        python_interface.subprocess,
        "run",
        lambda *_args, **_kwargs: python_interface.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    with pytest.raises(
        python_interface.PythonRouteSmokeTraceError,
        match="invalid batch paths",
    ):
        python_interface.trace_python_route_smoke_dependencies_batch(
            tmp_path,
            ((skill, target),),
        )


def test_route_smoke_batch_reports_nonzero_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    write_route_smoke_worker(skill, "        pass\n")
    target = _target("_rtx/_worker.py")
    monkeypatch.setattr(
        python_interface.subprocess,
        "run",
        lambda *_args, **_kwargs: python_interface.subprocess.CompletedProcess(
            args=[],
            returncode=7,
            stdout="",
            stderr="child failed",
        ),
    )

    with pytest.raises(
        python_interface.PythonRouteSmokeTraceError,
        match="child failed",
    ):
        python_interface.trace_python_route_smoke_dependencies_batch(
            tmp_path,
            ((skill, target),),
        )


def test_route_smoke_batch_empty_input_launches_no_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("empty route-smoke batch launched a child process")

    monkeypatch.setattr(python_interface.subprocess, "run", reject_run)
    batch_tracer = getattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        None,
    )
    assert callable(batch_tracer)

    assert batch_tracer(tmp_path, []) == {}


def test_route_smoke_schema_version_is_explicit_and_defaults_to_v6(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "skills" / "demo" / "_rtx"
    _write_logical_runtime(skill, value="demo")
    target = _logical_target("demo-rtx")
    commands: list[list[str]] = []
    payload = [
        {
            "skill_root": skill.resolve().as_posix(),
            "python_target": python_interface._python_process_target_payload(
                target
            ),
            "paths": [(skill / "runtime.py").resolve().as_posix()],
        }
    ]

    def capture_run(command, **_kwargs):
        commands.append(list(command))
        return python_interface.subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(python_interface.subprocess, "run", capture_run)

    python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((skill, target),),
    )
    python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((skill, target),),
        expected_schema_version=5,
    )

    assert [command[-1] for command in commands] == ["6", "5"]
    assert Path(commands[0][5]).name == "blueprint"
    assert Path(commands[1][5]).name == "v5"


def test_scalar_route_smoke_trace_delegates_to_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    write_interface(runtime / "_demo.py")
    target = _target()
    expected = (runtime / "_demo.py",)
    calls: list[
        tuple[Path, tuple[tuple[Path, PythonProcessTarget], ...]]
    ] = []

    def trace_batch(
        repo_root: Path,
        specifications: tuple[tuple[Path, PythonProcessTarget], ...],
        **_options: object,
    ) -> dict[tuple[Path, PythonProcessTarget], tuple[Path, ...]]:
        normalized = tuple(specifications)
        calls.append((repo_root, normalized))
        return {(skill.resolve(), target): expected}

    monkeypatch.setattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        trace_batch,
        raising=False,
    )

    result = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        target,
    )

    assert result == expected
    assert calls == [(tmp_path, ((skill, target),))]


def test_route_smoke_trace_prefers_candidate_local_officina_source(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    write_interface(runtime / "_demo.py")
    live_source = Path(python_interface.__file__).resolve().parents[2]
    candidate_source = tmp_path / "src"
    shutil.copytree(
        live_source / "officina",
        candidate_source / "officina",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )

    paths = python_interface.trace_python_route_smoke_dependencies(
        skill,
        tmp_path,
        _target(),
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

    interface = load_interface("_rtx/_demo.py", "Interface")

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

    interface = load_interface("_rtx/_demo.py", "Interface")

    assert interface.value == "ok"
    sys.modules.pop("_rtx", None)
    sys.modules.pop("_rtx._demo", None)


def test_logical_loader_preserves_relative_imports_physical_file_and_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = tmp_path / "skills" / "demo" / "_rtx"
    _write_logical_runtime(module_root, value="demo")
    target = _logical_target("demo-rtx")
    monkeypatch.chdir(module_root)
    sys.modules.pop("runtime", None)
    sys.modules.pop("helper", None)

    interface = load_interface(
        target.gateway_path,
        target.process_entry,
        logical_package=target.logical_package,
        logical_entrypoint=target.logical_entrypoint,
    )

    assert interface.value == "demo"
    assert interface.physical_file == (module_root / "runtime.py").resolve()
    assert Path(interface.run.__func__.__code__.co_filename) == (
        module_root / "runtime.py"
    ).resolve()
    assert interface.resource == "resource-demo"
    assert "runtime" not in sys.modules
    assert "helper" not in sys.modules


def test_logical_loader_replaces_hostile_cached_package_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = tmp_path / "skills" / "demo" / "_rtx"
    _write_logical_runtime(module_root, value="trusted")
    target = _logical_target("demo-rtx")
    hostile = type(sys)(target.logical_package)
    hostile.VALUE = "hostile"
    hostile.__file__ = str(tmp_path / "hostile.py")
    sys.modules[target.logical_package] = hostile
    monkeypatch.chdir(module_root)

    interface = load_interface(
        target.gateway_path,
        target.process_entry,
        logical_package=target.logical_package,
        logical_entrypoint=target.logical_entrypoint,
    )

    assert interface.value == "trusted"
    assert sys.modules[target.logical_package] is hostile
    assert run_python_machine_interface(interface, []) == 0
    assert interface.late_value == "late-trusted"
    assert sys.modules[target.logical_package] is hostile


def test_main_attaches_runtime_dispatch_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class Interface(PythonMachineInterface):
        def run(self, args):
            context = python_interface.runtime_dispatch_context(self)
            captured["caller_module_id"] = context.caller_module_id
            captured["caller_source_id"] = context.caller_source_id
            captured["repo_root"] = context.repo_root
            return 0

    def fake_load_interface(*_args, **_kwargs):
        return Interface()

    monkeypatch.setattr(python_runner, "load_interface", fake_load_interface)

    result = main(
        [
            "--runtime-caller-module-id",
            "demo-rtx",
            "--runtime-caller-source-id",
            "demo-rtx.source.runtime",
            "--runtime-repo-root",
            str(tmp_path),
            "_rtx/_demo.py",
            "Interface",
        ]
    )

    assert result == 0
    assert captured == {
        "caller_module_id": "demo-rtx",
        "caller_source_id": "demo-rtx.source.runtime",
        "repo_root": tmp_path,
    }


def test_logical_descriptor_and_snapshot_sources_have_identical_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from officina.common.blueprint_graph import (
        encode_runtime_python_package_snapshot,
    )

    module_root = tmp_path / "skills" / "demo" / "_rtx"
    _write_logical_runtime(module_root, value="same")
    target = _logical_target("demo-rtx")
    monkeypatch.chdir(module_root)
    paths = (module_root / "__init__.py", module_root / "helper.py", module_root / "runtime.py")
    descriptors = [
        os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        for path in paths
    ]
    try:
        descriptor_sources = python_runner._load_bound_package_sources(
                tuple(
                    (descriptor, path.relative_to(module_root.parent).as_posix())
                    for descriptor, path in zip(descriptors, paths, strict=True)
                ),
                logical_package=target.logical_package,
                physical_package_prefix=module_root.name,
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)

    snapshots = tuple((path, path.read_bytes()) for path in paths)
    payload = encode_runtime_python_package_snapshot(
        snapshots,
        module_root.parent,
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(payload)
    snapshot_sources = python_runner._load_package_snapshot_sources(
        snapshot,
        hashlib.sha256(payload).hexdigest(),
        logical_package=target.logical_package,
        physical_package_prefix=module_root.name,
    )

    assert descriptor_sources == snapshot_sources
    assert target.logical_entrypoint in descriptor_sources
    assert descriptor_sources[target.logical_entrypoint][1] == str(
        (module_root / "runtime.py").resolve()
    )


@pytest.mark.parametrize("transport", ["descriptor", "snapshot"])
def test_logical_bound_transport_rejects_bare_sibling_import_after_live_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
) -> None:
    from officina.common.blueprint_graph import (
        encode_runtime_python_package_snapshot,
    )

    if transport == "descriptor" and os.name == "nt":
        # famulus-skip: category=platform-contract; reason=Windows denies renaming an open CRT descriptor; alternate=the snapshot parameter exercises the same bound-source isolation contract
        pytest.skip("Windows cannot rename an open descriptor")

    module_root = tmp_path / "skills" / "demo" / "_rtx"
    module_root.mkdir(parents=True)
    (module_root / "__init__.py").write_text("", encoding="utf-8")
    helper = module_root / "helper.py"
    helper.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    runtime = module_root / "runtime.py"
    runtime.write_text(
        "import helper\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    value = helper.VALUE\n"
        "    def run(self, args): return 0\n",
        encoding="utf-8",
    )
    target = _logical_target("demo-rtx")
    paths = (module_root / "__init__.py", helper, runtime)
    monkeypatch.chdir(module_root)
    sys.modules.pop("helper", None)
    original_sys_path = list(sys.path)
    sys.path.insert(0, str(module_root))
    sys.path.insert(0, "")

    if transport == "descriptor":
        descriptors = [
            os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            for path in paths
        ]
        try:
            helper.replace(module_root / "captured-helper.py")
            helper.write_text(
                "raise AssertionError('hostile live helper executed')\n",
                encoding="utf-8",
            )
            sources = python_runner._load_bound_package_sources(
                tuple(
                    (descriptor, path.relative_to(module_root.parent).as_posix())
                    for descriptor, path in zip(descriptors, paths, strict=True)
                ),
                logical_package=target.logical_package,
                physical_package_prefix=module_root.name,
            )
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
    else:
        payload = encode_runtime_python_package_snapshot(
            tuple((path, path.read_bytes()) for path in paths),
            module_root.parent,
        )
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_bytes(payload)
        helper.replace(module_root / "captured-helper.py")
        helper.write_text(
            "raise AssertionError('hostile live helper executed')\n",
            encoding="utf-8",
        )
        sources = python_runner._load_package_snapshot_sources(
            snapshot,
            hashlib.sha256(payload).hexdigest(),
            logical_package=target.logical_package,
            physical_package_prefix=module_root.name,
        )

    try:
        with pytest.raises(ModuleNotFoundError, match="helper"):
            load_interface(
                target.gateway_path,
                target.process_entry,
                logical_package=target.logical_package,
                logical_entrypoint=target.logical_entrypoint,
                _package_sources=sources,
            )
    finally:
        sys.path[:] = original_sys_path


def test_logical_in_process_loader_and_route_smoke_confine_and_restore_sys_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = tmp_path / "skills" / "demo" / "_rtx"
    module_root.mkdir(parents=True)
    target = _logical_target("demo-rtx")
    physical_runtime = (module_root / "runtime.py").resolve()
    source = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "ROOT = Path(os.getcwd()).resolve()\n"
        "def assert_confined():\n"
        "    assert all(Path(entry or os.getcwd()).resolve() != ROOT for entry in sys.path)\n"
        "assert_confined()\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def route_smoke(self): assert_confined()\n"
        "    def run(self, args): return 0\n"
    ).encode("utf-8")
    (module_root / "__init__.py").write_bytes(b"")
    (module_root / "runtime.py").write_bytes(source)
    monkeypatch.chdir(module_root)
    sources = python_runner._index_bound_package_sources(
        (
            (b"", "__init__.py"),
            (source, "runtime.py"),
        ),
        logical_package=target.logical_package,
    )
    assert sources[target.logical_entrypoint][1] == str(physical_runtime)
    sys.path.insert(0, str(module_root))
    sys.path.insert(0, "")
    before = list(sys.path)

    interface = load_interface(
        target.gateway_path,
        target.process_entry,
        logical_package=target.logical_package,
        logical_entrypoint=target.logical_entrypoint,
        _package_sources=sources,
    )
    assert sys.path == before

    assert run_python_machine_interface(interface, ["--route-smoke"]) == 0
    assert sys.path == before


def test_bound_interface_reuses_the_same_trusted_module_state_across_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "state.py").write_text("VALUE = 0\n", encoding="utf-8")
    (runtime / "worker.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "from . import state\n"
        "class Interface(PythonMachineInterface):\n"
        "    def __init__(self): state.VALUE = 1\n"
        "    def run(self, args):\n"
        "        from . import state\n"
        "        assert state.VALUE == 1\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/worker.py", "Interface")

    assert run_python_machine_interface(interface, []) == 0


def test_route_smoke_trace_isolates_two_nested_rtx_logical_packages(
    tmp_path: Path,
) -> None:
    first = tmp_path / "skills" / "first" / "_rtx"
    second = tmp_path / "skills" / "second" / "_rtx"
    _write_logical_runtime(first, value="first")
    _write_logical_runtime(second, value="second")
    first_target = _logical_target("first-rtx")
    second_target = _logical_target("second-rtx")

    traces = python_interface.trace_python_route_smoke_dependencies_batch(
        tmp_path,
        ((first, first_target), (second, second_target)),
        expected_schema_version=5,
    )

    assert (first / "helper.py").resolve() in traces[(first.resolve(), first_target)]
    assert (second / "helper.py").resolve() not in traces[
        (first.resolve(), first_target)
    ]
    assert (second / "helper.py").resolve() in traces[
        (second.resolve(), second_target)
    ]
    assert (first / "helper.py").resolve() not in traces[
        (second.resolve(), second_target)
    ]


def test_load_interface_uses_shared_reader_without_posix_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PlatformWithoutPosixDescriptors:
        name = "nt"
        supports_dir_fd: set[object] = set()

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        python_runner,
        "os",
        PlatformWithoutPosixDescriptors(),
    )

    interface = load_interface("_rtx/_demo.py", "Interface")

    assert interface.__class__.__name__ == "Interface"


def test_load_interface_rejects_source_outside_working_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    outside = tmp_path / "_outside.py"
    write_interface(outside)
    monkeypatch.chdir(skill_root)

    with pytest.raises(InterfaceLoadError, match="must be a relative `_rtx/"):
        load_interface(outside, "Interface")


def test_load_interface_binds_lazy_package_imports_to_initial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    helper = runtime / "_helper.py"
    helper.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    (runtime / "_demo.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        from ._helper import VALUE\n"
        "        print(VALUE)\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/_demo.py", "Interface")
    helper.write_text("VALUE = 'untrusted'\n", encoding="utf-8")

    assert run_python_machine_interface(interface, []) == 0
    assert capsys.readouterr().out == "trusted\n"


def test_load_interface_rejects_symlinked_package_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("", encoding="utf-8")
    outside = tmp_path / "_outside.py"
    outside.write_text("VALUE = 'outside'\n", encoding="utf-8")
    try:
        (runtime / "_helper.py").symlink_to(outside)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=some Windows runners deny symlink creation; alternate=Linux and macOS exercise the same confined-loader rejection
        pytest.skip(f"symlink creation unavailable: {exc}")
    (runtime / "_demo.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InterfaceLoadError, match="symbolic link|reparse point"):
        load_interface("_rtx/_demo.py", "Interface")


def test_main_rejects_matching_digest_for_malformed_package_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"{}"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(payload)

    result = main(
        [
            "--package-snapshot",
            str(snapshot),
            "--package-snapshot-sha256",
            hashlib.sha256(payload).hexdigest(),
            "_rtx/_demo.py",
            "Interface",
        ]
    )

    assert result == 2
    assert "invalid package snapshot" in capsys.readouterr().err


# famulus-skip: category=platform-contract; reason=retained POSIX descriptors permit rename-after-open while Windows denies that rename; alternate=path-snapshot and lazy-package-snapshot tests cover the native Windows reader
@pytest.mark.skipif(
    not descriptor_safe_open_supported(),
    reason="retained descriptor swap requires POSIX dir-fd support",
)
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
        interface = load_interface(
            "_rtx/_demo.py",
            "Interface",
            source_fd=source_fd,
        )
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
    interface = load_interface("_rtx/_demo.py", "Interface")

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
    interface = load_interface("_rtx/_demo.py", "Interface")

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
    interface = load_interface("_rtx/_legacy.py", "Interface")

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
    sentinel = object()

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return sentinel

    def fake_run(resolved, **kwargs):
        assert resolved is sentinel
        return "ok"

    monkeypatch.setattr("officina.dispatcher.core._resolve_dispatch", fake_resolve)
    monkeypatch.setattr(
        "officina.dispatcher.core._run_resolved_invocation",
        fake_run,
    )

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


def test_declared_v5_dispatch_builds_target_from_module_and_local_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    sentinel = object()

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return sentinel

    def fake_run(resolved, **kwargs):
        assert resolved is sentinel
        return "ok"

    monkeypatch.setattr("officina.dispatcher.core._resolve_dispatch", fake_resolve)
    monkeypatch.setattr(
        "officina.dispatcher.core._run_resolved_invocation",
        fake_run,
    )

    class Interface(PythonMachineInterface):
        dispatches = {
            "read": DispatchCall(
                caller_module_id="demo-rtx",
                target_module_id="cloud-files-rtx",
                interface="read",
            )
        }

    assert Interface().dispatch("read") == "ok"
    assert captured["caller_skill"] == "demo-rtx"
    assert captured["target"] == "cloud-files-rtx.interface.read"


def test_declared_v5_dispatch_ignores_runtime_source_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_resolve = {}
    captured_run = {}
    sentinel = object()

    def fake_resolve(**kwargs):
        captured_resolve.update(kwargs)
        return sentinel

    def fake_run(resolved, **kwargs):
        captured_run["resolved"] = resolved
        captured_run.update(kwargs)
        return "ok"

    monkeypatch.setattr(
        "officina.dispatcher.core._resolve_dispatch",
        fake_resolve,
    )
    monkeypatch.setattr(
        "officina.dispatcher.core._run_resolved_invocation",
        fake_run,
    )

    class Interface(PythonMachineInterface):
        dispatches = {
            "read": DispatchCall(
                caller_module_id="demo-rtx",
                target_module_id="cloud-files-rtx",
                interface="read",
            )
        }

    interface = Interface()
    python_interface.set_runtime_dispatch_context(
        interface,
        caller_module_id="demo-rtx",
        caller_source_id="demo-rtx.source.rtx-plan-orchestrate",
        repo_root=tmp_path,
    )

    assert (
        interface.dispatch("read", args=["todo"], stdin="payload", text=True)
        == "ok"
    )
    assert captured_resolve["caller_skill"] == "demo-rtx"
    assert captured_resolve["caller_source_id"] is None
    assert captured_resolve["target"] == "cloud-files-rtx.interface.read"
    assert captured_resolve["repo_root"] == tmp_path
    assert captured_resolve["host_caller"] is False
    assert captured_run["resolved"] is sentinel
    assert captured_run["stdin"] == "payload"
    assert captured_run["text"] is True


def test_declared_v5_dispatch_rejects_mismatched_runtime_caller_context(
    tmp_path: Path,
) -> None:
    class Interface(PythonMachineInterface):
        dispatches = {
            "read": DispatchCall(
                caller_module_id="demo-rtx",
                target_module_id="cloud-files-rtx",
                interface="read",
            )
        }

    interface = Interface()
    python_interface.set_runtime_dispatch_context(
        interface,
        caller_module_id="daily-plan-rtx",
        caller_source_id="daily-plan-rtx.source.rtx-plan-orchestrate",
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="does not match declared dispatch caller"):
        interface.dispatch("read")


def test_dependency_resolver_builds_v5_target_from_local_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    sentinel = object()

    def resolve_for_trace(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "officina.dispatcher.core._resolve_dispatch_metadata_for_trace",
        resolve_for_trace,
    )
    call = DispatchCall(
        caller_module_id="demo-rtx",
        target_module_id="cloud-files-rtx",
        interface="read",
    )

    result = DispatchDependencyResolver(repo_root=tmp_path).resolve_call(call)

    assert result is sentinel
    assert captured["caller_module_id"] == "demo-rtx"
    assert captured["target"] == "cloud-files-rtx.interface.read"


def test_dependency_resolver_rejects_mismatched_runtime_caller_context(
    tmp_path: Path,
) -> None:
    call = DispatchCall(
        caller_module_id="demo-rtx",
        target_module_id="cloud-files-rtx",
        interface="read",
    )

    with pytest.raises(ValueError, match="does not match declared dispatch caller"):
        DispatchDependencyResolver(repo_root=tmp_path).resolve_call(
            call,
            caller_module_id="daily-plan-rtx",
            caller_source_id="daily-plan-rtx.source.rtx-plan-orchestrate",
        )


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


def test_preloaded_graph_resolves_transitive_dispatches_without_reloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import officina.dispatcher.core as dispatcher_core

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
    graph = load_repository_blueprint_graph(
        tmp_path,
        expected_schema_version=4,
        schema_root=V4_SCHEMA_ROOT,
    )
    inventory_calls = 0
    graph_calls = 0

    def reject_inventory(*_args: object, **_kwargs: object) -> None:
        nonlocal inventory_calls
        inventory_calls += 1
        pytest.fail("preloaded trace resolution reloaded repository inventory")

    def reject_graph(*_args: object, **_kwargs: object) -> None:
        nonlocal graph_calls
        graph_calls += 1
        pytest.fail("preloaded trace resolution rebuilt the repository graph")

    monkeypatch.setattr(dispatcher_core, "collect_blueprints", reject_inventory)
    monkeypatch.setattr(
        dispatcher_core,
        "load_repository_blueprint_graph",
        reject_graph,
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
        graph=graph,
    ).collect(SourceInterface())

    assert [item.resolved.target for item in dependencies] == [
        "middle-skill.interface.run",
        "leaf-skill.interface.run",
    ]
    assert inventory_calls == 0
    assert graph_calls == 0


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


def test_main_reports_incomplete_python_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["not-a-spec"]) == 2
    assert "missing Python gateway path or process entry" in capsys.readouterr().err
