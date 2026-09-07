"""Tests for the shared Python machine-interface runner."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.certification.view import CertificationDecision  # noqa: E402
from officina.blueprints.graph import (  # noqa: E402
    descriptor_safe_open_supported,
    load_repository_blueprint_graph,
)
from officina.dispatcher.core import ResolvedInvocationMetadata  # noqa: E402
from officina.dispatcher.errors import DispatcherError  # noqa: E402
import officina.dispatcher.core as dispatcher_core  # noqa: E402
import officina.dispatcher.direct_runtime as direct_runtime  # noqa: E402
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

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint-schema"
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


def test_dispatch_invocation_error_classifier_accepts_real_invocation_error() -> None:
    """Catches a classifier that misses the dispatcher's compatibility base."""
    error = dispatcher_core.InvocationError("expected dispatch failure")

    assert python_interface.is_dispatch_invocation_error(error)


def test_dispatch_invocation_error_classifier_rejects_arbitrary_runtime_error() -> None:
    """Catches broad classification that would hide programmer defects."""
    error = RuntimeError("programmer defect")

    assert not python_interface.is_dispatch_invocation_error(error)


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


def legacy_dispatch_call_analyzer_resolves_aliases_in_nested_imports() -> None:
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


def test_load_interface_accepts_a_configured_interface_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requiring a class would force every configured process adapter to subclass."""

    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "_demo.py").write_text(
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class DemoInterface(PythonMachineInterface):\n"
        "    def run(self, args):\n"
        "        return 0\n"
        "Interface = DemoInterface()\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    interface = load_interface("_rtx/_demo.py", "Interface")

    assert interface.__class__.__name__ == "DemoInterface"


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


def test_route_smoke_batch_preserves_repository_and_process_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_package = Path(__file__).resolve().parents[1] / "src" / "officina"
    candidate_package = tmp_path / "src" / "officina"
    candidate_package.mkdir(parents=True)
    candidate_init = candidate_package / "__init__.py"
    candidate_init.write_text(
        f"__path__.append({str(live_package)!r})\n",
        encoding="utf-8",
    )
    live_runtime = live_package / "runtime"
    candidate_runtime = candidate_package / "runtime"
    candidate_runtime.mkdir()
    (candidate_runtime / "__init__.py").write_text(
        f"__path__.append({str(live_runtime)!r})\n",
        encoding="utf-8",
    )
    candidate_python_interface = candidate_runtime / "python_machine_interface.py"
    shutil.copyfile(
        live_runtime / "python_machine_interface.py",
        candidate_python_interface,
    )
    candidate_marker = candidate_package / "_batch_trace_marker.py"
    candidate_marker.write_text("MARKER = True\n", encoding="utf-8")

    first = tmp_path / "skills" / "alpha-loaded-skill"
    second = tmp_path / "skills" / "beta-loaded-skill"
    write_traced_interface(first, "first")
    write_traced_interface(second, "second")
    unused = first / "_rtx" / "_unused.py"
    unused.write_text("UNUSED = True\n", encoding="utf-8")

    importer = tmp_path / "skills" / "gamma-1-importer-skill"
    nonimporter = tmp_path / "skills" / "gamma-2-nonimporter-skill"
    write_route_smoke_worker(
        importer,
        "        import officina._batch_trace_marker\n",
    )
    write_route_smoke_worker(nonimporter, "        pass\n")

    shared_first = tmp_path / "skills" / "epsilon-shared-import-skill"
    shared_second = tmp_path / "skills" / "zeta-shared-import-skill"
    for skill in (shared_first, shared_second):
        write_route_smoke_worker(
            skill,
            "        from officina import _batch_trace_marker\n",
        )

    cwd_mutator = tmp_path / "skills" / "eta-cwd-mutator-skill"
    cwd_observer = tmp_path / "skills" / "theta-cwd-observer-skill"
    leaked_path = (tmp_path / "leaked-path").as_posix()
    write_route_smoke_worker(
        cwd_mutator,
        "        import os, sys\n"
        f"        os.chdir({str(tmp_path)!r})\n"
        f"        sys.path.insert(0, {leaked_path!r})\n",
    )
    write_route_smoke_worker(
        cwd_observer,
        "        import sys\n"
        "        from pathlib import Path\n"
        "        assert Path.cwd() == Path(__file__).resolve().parents[1]\n"
        f"        assert {leaked_path!r} not in sys.path\n",
    )

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
            (shared_second, target),
            (first, target),
            (importer, target),
            (nonimporter, target),
            (shared_first, target),
            (cwd_mutator, target),
            (cwd_observer, target),
            (first.resolve(), target),
        ],
    )

    first_key = (first.resolve(), target)
    second_key = (second.resolve(), target)
    assert child_calls and len(child_calls) == 1
    expected_keys = [
        (skill.resolve(), target)
        for skill in (
            first,
            second,
            shared_first,
            cwd_mutator,
            importer,
            nonimporter,
            cwd_observer,
            shared_second,
        )
    ]
    assert list(traces) == expected_keys
    assert (first / "_rtx" / "_dependency.py").resolve() in traces[first_key]
    assert (second / "_rtx" / "_dependency.py").resolve() not in traces[first_key]
    assert (second / "_rtx" / "_dependency.py").resolve() in traces[second_key]
    assert (first / "_rtx" / "_dependency.py").resolve() not in traces[second_key]
    assert (first / "_rtx" / "_worker.py").resolve() in traces[first_key]
    assert unused.resolve() not in traces[first_key]
    assert candidate_marker.resolve() in traces[(importer.resolve(), target)]
    assert candidate_marker.resolve() not in traces[(nonimporter.resolve(), target)]
    assert candidate_marker.resolve() in traces[(shared_first.resolve(), target)]
    assert candidate_marker.resolve() in traces[(shared_second.resolve(), target)]
    assert candidate_init.resolve() in traces[first_key]
    assert (live_package / "__init__.py").resolve() not in traces[first_key]
    assert candidate_python_interface.resolve() in traces[first_key]
    assert (
        live_runtime / "python_machine_interface.py"
    ).resolve() not in traces[first_key]


def legacy_route_smoke_batch_rejects_invalid_blueprint_outside_skills(
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
                **{"expected_" + "schema_version": 4},
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


def test_route_smoke_uses_current_schema(
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
    assert len(commands) == 1
    assert Path(commands[0][-1]).name == "blueprint-schema"


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
    monkeypatch.setitem(sys.modules, target.logical_package, hostile)
    monkeypatch.delitem(sys.modules, "runtime", raising=False)
    monkeypatch.delitem(sys.modules, "helper", raising=False)
    monkeypatch.delitem(sys.modules, "late_helper", raising=False)
    monkeypatch.chdir(module_root)

    interface = load_interface(
        target.gateway_path,
        target.process_entry,
        logical_package=target.logical_package,
        logical_entrypoint=target.logical_entrypoint,
    )

    assert interface.value == "trusted"
    assert interface.physical_file == (module_root / "runtime.py").resolve()
    assert Path(interface.run.__func__.__code__.co_filename) == (
        module_root / "runtime.py"
    ).resolve()
    assert interface.resource == "resource-trusted"
    assert "runtime" not in sys.modules
    assert "helper" not in sys.modules
    assert target.logical_entrypoint not in sys.modules
    assert sys.modules[target.logical_package] is hostile
    assert run_python_machine_interface(interface, []) == 0
    assert interface.late_value == "late-trusted"
    assert "late_helper" not in sys.modules
    assert f"{target.logical_package}.late_helper" not in sys.modules
    assert target.logical_entrypoint not in sys.modules
    assert sys.modules[target.logical_package] is hostile


def test_main_shares_dispatch_context_with_a_helper_modules_own_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A helper module's own interface object must still resolve the config.

    Skills factor shared dispatch into a helper module that builds its own
    interface at import time (list-manager's `_cloud_transport._DISPATCHER`,
    daily-plan's `_day_model._dispatch_interface`). The runner only ever
    configures the ONE interface it loaded, so those helper objects saw an
    empty context and every nested dispatch through them died with
    "dispatcher requires the exact repository configuration path" -- which is
    what kept email-triage failing after its own run completed.

    The context describes the process, not one object: the runner executes
    exactly one interface per process. Anything dispatching in that process
    is entitled to it.
    """
    seen = {}

    class Helper(PythonMachineInterface):
        """Stands in for a helper module's import-time instance."""

    helper = Helper()

    class Interface(PythonMachineInterface):
        def run(self, args):
            context = python_interface.runtime_dispatch_context(helper)
            seen["repository_config"] = context.repository_config
            seen["repo_root"] = context.repo_root
            return 0

    monkeypatch.setattr(
        python_runner, "load_interface", lambda *a, **k: Interface()
    )

    config_path = tmp_path / "officina.toml"
    result = main(
        [
            "--runtime-caller-module-id",
            "demo-rtx",
            "--runtime-repo-root",
            str(tmp_path),
            "--runtime-repository-config",
            str(config_path),
            "_rtx/_demo.py",
            "Interface",
        ]
    )

    assert result == 0
    assert seen["repository_config"] == config_path
    assert seen["repo_root"] == tmp_path


def test_runtime_dispatch_context_prefers_an_objects_own_context(
    tmp_path: Path,
) -> None:
    """The process fallback must not overwrite an explicitly configured one."""
    own = PythonMachineInterface()
    python_interface.set_process_dispatch_context(
        python_interface.RuntimeDispatchContext(
            caller_module_id="process", repository_config=tmp_path / "process.toml"
        )
    )
    try:
        python_interface.set_runtime_dispatch_context(
            own, caller_module_id="own", repository_config=tmp_path / "own.toml"
        )
        assert (
            python_interface.runtime_dispatch_context(own).repository_config
            == tmp_path / "own.toml"
        )
    finally:
        python_interface.set_process_dispatch_context(None)


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


def test_logical_bound_transports_preserve_identity_and_reject_bare_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from officina.blueprints.graph import (
        encode_runtime_python_package_snapshot,
    )

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
    monkeypatch.chdir(module_root)
    paths = (module_root / "__init__.py", helper, runtime)
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
    descriptors = [
        os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        for path in paths
    ]
    descriptor_sources = None
    if not descriptor_safe_open_supported():
        # famulus-skip: category=platform-contract; reason=Windows denies renaming an open CRT descriptor; alternate=descriptor identity is checked before the swap and snapshot sources exercise live-swap isolation
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
        descriptors = []
        assert descriptor_sources == snapshot_sources

    helper.replace(module_root / "captured-helper.py")
    helper.write_text(
        "raise AssertionError('hostile live helper executed')\n",
        encoding="utf-8",
    )
    sources_by_transport = [("snapshot", snapshot_sources)]
    if descriptors:
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
        assert descriptor_sources == snapshot_sources
        sources_by_transport.append(("descriptor", descriptor_sources))

    assert target.logical_entrypoint in snapshot_sources
    assert snapshot_sources[target.logical_entrypoint][1] == str(runtime.resolve())
    original_sys_path = list(sys.path)
    monkeypatch.delitem(sys.modules, "helper", raising=False)
    sys.path.insert(0, str(module_root))
    sys.path.insert(0, "")
    try:
        for transport, sources in sources_by_transport:
            with pytest.raises(ModuleNotFoundError, match="helper") as error:
                load_interface(
                    target.gateway_path,
                    target.process_entry,
                    logical_package=target.logical_package,
                    logical_entrypoint=target.logical_entrypoint,
                    _package_sources=sources,
                )
            assert error.value.name == "helper", transport
            assert "helper" not in sys.modules
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
        "from . import state\n"
        "ROOT = Path(os.getcwd()).resolve()\n"
        "def assert_confined():\n"
        "    assert all(Path(entry or os.getcwd()).resolve() != ROOT for entry in sys.path)\n"
        "assert_confined()\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class Interface(PythonMachineInterface):\n"
        "    def __init__(self):\n"
        "        state.VALUE = 1\n"
        "        self.constructed_state = state.VALUE\n"
        "    def route_smoke(self): assert_confined()\n"
        "    def run(self, args):\n"
        "        assert_confined()\n"
        "        from . import state\n"
        "        assert state.VALUE == 1\n"
        "        self.run_state = state.VALUE\n"
        "        return 0\n"
    ).encode("utf-8")
    state_source = b"VALUE = 0\n"
    (module_root / "__init__.py").write_bytes(b"")
    (module_root / "state.py").write_bytes(state_source)
    (module_root / "runtime.py").write_bytes(source)
    monkeypatch.chdir(module_root)
    sources = python_runner._index_bound_package_sources(
        (
            (b"", "__init__.py"),
            (state_source, "state.py"),
            (source, "runtime.py"),
        ),
        logical_package=target.logical_package,
    )
    assert sources[target.logical_entrypoint][1] == str(physical_runtime)
    original_sys_path = list(sys.path)
    sys.path.insert(0, str(module_root))
    sys.path.insert(0, "")
    confined_sys_path = list(sys.path)
    try:
        interface = load_interface(
            target.gateway_path,
            target.process_entry,
            logical_package=target.logical_package,
            logical_entrypoint=target.logical_entrypoint,
            _package_sources=sources,
        )
        assert interface.constructed_state == 1
        assert sys.path == confined_sys_path

        assert run_python_machine_interface(interface, ["--route-smoke"]) == 0
        assert sys.path == confined_sys_path
        assert run_python_machine_interface(interface, []) == 0
        assert interface.run_state == 1
        assert sys.path == confined_sys_path
    finally:
        sys.path[:] = original_sys_path


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


def test_route_smoke_then_normal_mode_preserves_parser_and_run_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    write_interface(runtime / "_demo.py")
    monkeypatch.chdir(tmp_path)
    interface = load_interface("_rtx/_demo.py", "Interface")

    route_result = run_python_machine_interface(interface, ["--route-smoke"])

    assert route_result == 0
    assert not interface.ran
    assert capsys.readouterr().out == "route-smoke ok\n"
    normal_result = run_python_machine_interface(interface, ["--name", "Ada"])

    assert normal_result == 0
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


def legacy_declared_dispatch_rejects_local_interface_alias() -> None:
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


def legacy_declared_dispatch_uses_generic_export_id_without_legacy_rewrite(
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


@pytest.mark.parametrize("configured", [False, True], ids=["no-config", "configured"])
def test_trace_rejects_supplied_non_v6_graph_before_route_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: bool,
) -> None:
    if configured:
        (tmp_path / "officina.toml").write_text("", encoding="utf-8")
        monkeypatch.setattr(
            dispatcher_core,
            "_resolve_dispatch",
            lambda **_kwargs: pytest.fail("configured dispatch reached"),
        )

    with pytest.raises(
        dispatcher_core.InvocationError,
        match=(
            "Dispatcher metadata trace requires blueprint graph schema 6; "
            "received schema 5"
        ),
    ):
        dispatcher_core._resolve_dispatch_metadata_for_trace(
            caller_module_id="demo", target="provider.interface.run",
            repo_root=tmp_path, graph=type("Graph", (), {"schema_version": 5})(),
        )


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
        caller_module_id="demo-skill",
        target_module_id="cloud-files",
        interface="read",
    )

    result = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=certification_view,
    ).resolve_call(call)

    assert result is sentinel
    assert captured["target"] == "cloud-files.interface.read"
    assert captured["certification_view"] is certification_view


def historical_dependency_resolver_reuses_preloaded_graph_without_fd_growth(
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

    class SourceInterface(PythonMachineInterface):
        dispatches = {
            "middle": DispatchCall(
                caller_skill="source-skill",
                target_skill="middle-skill",
                interface="middle-skill.interface.run",
            )
        }

    resolver = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=_PassingCertificationView(),
    )
    proc_fds = Path("/proc/self/fd")
    before = len(list(proc_fds.iterdir())) if proc_fds.is_dir() else None
    graphless_results = [resolver.collect(SourceInterface()) for _ in range(2)]
    after = len(list(proc_fds.iterdir())) if proc_fds.is_dir() else None
    expected = [
        ("middle", "middle-skill.interface.run"),
        ("next", "leaf-skill.interface.run"),
    ]
    for dependencies in graphless_results:
        assert [(item.key, item.resolved.target) for item in dependencies] == expected
        assert [item.depth for item in dependencies] == [0, 1]
    if before is not None:
        assert after == before

    graph = load_repository_blueprint_graph(
        tmp_path,
        **{"expected_" + "schema_version": 4},
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

    preloaded_dependencies = DispatchDependencyResolver(
        repo_root=tmp_path,
        certification_view=_PassingCertificationView(),
        graph=graph,
    ).collect(SourceInterface())

    assert [
        (item.key, item.resolved.target) for item in preloaded_dependencies
    ] == expected
    assert [item.depth for item in preloaded_dependencies] == [0, 1]
    assert inventory_calls == 0
    assert graph_calls == 0


def test_main_reports_incomplete_python_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["not-a-spec"]) == 2
    assert "missing Python gateway path or process entry" in capsys.readouterr().err


# famulus-skip: category=platform-contract; reason=this case passes a POSIX descriptor directly; alternate=the native-handle roundtrip below covers registered private diagnosis transport
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_main_reports_registered_failure_through_private_writer() -> None:
    reader, writer = os.pipe()
    try:
        assert main(["--diagnostic-writer", str(writer)]) == 70
        payload = json.loads(os.read(reader, 16 * 1024))
    finally:
        os.close(reader)

    assert payload == {
        "schema_version": 1,
        "code": "dispatcher.runner_request_invalid",
        "message": "Python interface runner requires a gateway path and process entry.",
    }


# famulus-skip: category=platform-contract; reason=this case inspects POSIX descriptor closure directly; alternate=the native-handle conversion and launch-failure cases cover early ownership cleanup
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_private_writer_transports_template_context_and_closes_on_early_rejection() -> None:
    reader, writer = os.pipe()
    try:
        result = main(
            [
                "--diagnostic-writer",
                str(writer),
                "--source-fd",
            ]
        )
        payload = json.loads(os.read(reader, 16 * 1024))
        with pytest.raises(OSError):
            os.fstat(writer)
    finally:
        os.close(reader)

    assert result == 70
    assert payload == {
        "schema_version": 1,
        "code": "dispatcher.runner_request_invalid",
        "message": "Python interface runner option `--source-fd` is missing required arguments.",
        "option": "--source-fd",
    }


# famulus-skip: category=platform-contract; reason=this case inspects POSIX descriptor closure after payload construction fails; alternate=the native-handle conversion-failure case covers the corresponding ownership cleanup
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_private_diagnosis_closes_writer_when_payload_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, writer = os.pipe()
    monkeypatch.setattr(
        DispatcherError,
        "from_spec",
        classmethod(lambda _cls, *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad spec"))),
    )
    try:
        with pytest.raises(ValueError, match="bad spec"):
            python_runner._emit_private_diagnosis(writer, "R01")
        with pytest.raises(OSError):
            os.fstat(writer)
    finally:
        os.close(reader)


# famulus-skip: category=platform-contract; reason=this case asserts POSIX descriptor ownership during option parsing; alternate=the native-handle conversion-failure and grandchild-noninheritance cases cover native ownership
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor ownership")
def test_private_writer_closes_when_later_option_parsing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, writer = os.pipe()
    original_resolve = Path.resolve

    def fail_selected_resolve(path: Path, *args, **kwargs):
        if str(path) == "unresolvable-root":
            raise OSError("private parse failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_selected_resolve)
    try:
        with pytest.raises(OSError, match="private parse failure"):
            main(
                [
                    "--diagnostic-writer",
                    str(writer),
                    "--runtime-repo-root",
                    "unresolvable-root",
                    "gateway.py",
                    "Entry",
                ]
            )
        with pytest.raises(OSError):
            os.fstat(writer)
    finally:
        os.close(reader)


def test_windows_writer_conversion_failure_closes_raw_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(python_runner.os, "name", "nt")
    monkeypatch.setattr(
        python_runner.os,
        "set_handle_inheritable",
        lambda handle, value: events.append(("inherit", (handle, value))),
        raising=False,
    )
    monkeypatch.setattr(python_runner.os, "O_BINARY", 0, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        types.SimpleNamespace(
            open_osfhandle=lambda *_args: (_ for _ in ()).throw(
                OSError("conversion failed")
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "_winapi",
        types.SimpleNamespace(
            CloseHandle=lambda handle: events.append(("close", handle))
        ),
    )

    with pytest.raises(OSError, match="conversion failed"):
        main(["--diagnostic-writer", "123", "gateway.py", "Entry"])

    assert events == [("inherit", (123, False)), ("close", 123)]


def _main_private_payload(
    monkeypatch: pytest.MonkeyPatch,
    interface: PythonMachineInterface,
    interface_argv: list[str],
) -> dict[str, object]:
    reader, writer = os.pipe()
    monkeypatch.setattr(python_runner, "load_interface", lambda *_args, **_kwargs: interface)
    try:
        result = main(
            [
                "--diagnostic-writer",
                str(writer),
                "_rtx/_demo.py",
                "Interface",
                *interface_argv,
            ]
        )
        payload = json.loads(os.read(reader, 16 * 1024))
    finally:
        os.close(reader)
    assert result == 70
    return payload


# famulus-skip: category=platform-contract; reason=this lifecycle case writes through a POSIX descriptor; alternate=direct lifecycle classification tests and the native-handle roundtrip cover the same boundary
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
@pytest.mark.parametrize(
    ("failure", "interface_argv", "entry_code"),
    [
        ("parser", [], "dispatcher.runner_interface_initialization_failed"),
        ("route-smoke", ["--route-smoke"], "dispatcher.runner_route_smoke_failed"),
        ("parse", [], "dispatcher.runner_request_validation_failed"),
        ("run", [], "dispatcher.runner_execution_failed"),
        ("result", [], "dispatcher.runner_interface_invalid"),
    ],
)
def test_private_writer_contains_machine_interface_lifecycle_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    interface_argv: list[str],
    entry_code: str,
) -> None:
    class FailingInterface(PythonMachineInterface):
        def build_parser(self) -> argparse.ArgumentParser:
            if failure == "parser":
                raise RuntimeError("private parser failure")
            return super().build_parser()

        def route_smoke(self) -> None:
            if failure == "route-smoke":
                raise RuntimeError("private route failure")

        def parse_args(self, parser: argparse.ArgumentParser, argv: list[str]):
            if failure == "parse":
                raise RuntimeError("private validation failure")
            return super().parse_args(parser, argv)

        def run(self, args):
            if failure == "run":
                raise RuntimeError("private execution failure")
            if failure == "result":
                return object()
            return 0

    payload = _main_private_payload(
        monkeypatch,
        FailingInterface(),
        interface_argv,
    )

    assert payload["code"] == entry_code
    assert "private" not in json.dumps(payload)


# famulus-skip: category=platform-contract; reason=this argparse case writes through a POSIX descriptor; alternate=argument-rejection classification and the native-handle roundtrip cover the same result
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_private_writer_contains_normal_argparse_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RequiredArgumentInterface(PythonMachineInterface):
        def build_parser(self) -> argparse.ArgumentParser:
            parser = super().build_parser()
            parser.add_argument("--required", required=True)
            return parser

        def run(self, args):
            return 0

    payload = _main_private_payload(monkeypatch, RequiredArgumentInterface(), [])

    assert payload["code"] == "dispatcher.invalid_request"
    assert payload["message"] == (
        "The Python interface request does not match the declared interface signature."
    )


@pytest.mark.parametrize(
    ("failure", "entry_id"),
    [
        (lambda root: load_interface("bad.py", "Entry"), "R10"),
        (lambda root: python_runner._bound_module_name("../bad.py"), "R11"),
        (
            lambda root: python_runner._load_package_snapshot_sources(
                root / "snapshot.json",
                "bad-digest",
            ),
            "R12",
        ),
        (
            lambda root: python_runner._read_bound_source(
                root / "missing.py",
                None,
                allowed_root=root,
            ),
            "R13",
        ),
        (
            lambda root: python_runner._load_confined_package_sources(
                root.parent / "outside.py"
            ),
            "R21",
        ),
        (
            lambda root: load_interface(
                "_rtx/_demo.py",
                "Entry",
                _lazy_confined=True,
            ),
            "R22",
        ),
    ],
)
def test_runner_load_failures_select_their_catalogue_predicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure,
    entry_id: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(InterfaceLoadError) as caught:
        failure(tmp_path)

    assert caught.value.entry_id == entry_id


# famulus-skip: category=platform-contract; reason=this gateway-stage case writes through a POSIX descriptor; alternate=direct runner-stage predicates and the native-handle roundtrip cover classification and transport
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
@pytest.mark.parametrize(
    ("source", "entry_id"),
    [
        ("not valid Python :", "R15"),
        ("VALUE = 1\n", "R16"),
        (
            "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
            "class Interface(PythonMachineInterface):\n"
            "    def __init__(self):\n"
            "        raise RuntimeError('private constructor detail')\n",
            "R23",
        ),
    ],
)
def test_private_writer_contains_gateway_load_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    entry_id: str,
) -> None:
    runtime = tmp_path / "_rtx"
    runtime.mkdir()
    (runtime / "_demo.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reader, writer = os.pipe()
    try:
        result = main(
            [
                "--diagnostic-writer",
                str(writer),
                "_rtx/_demo.py",
                "Interface",
            ]
        )
        payload = json.loads(os.read(reader, 16 * 1024))
    finally:
        os.close(reader)

    assert result == 70
    assert payload == DispatcherError.from_spec(
        entry_id,
        **({"reason": "the entry is absent"} if entry_id == "R16" else {}),
    ).as_payload()


# famulus-skip: category=platform-contract; reason=this confined-import case writes through a POSIX descriptor; alternate=confined-import predicate tests and the native-handle roundtrip cover rejection and transport
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_private_writer_contains_confined_import_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "runtime.py").write_text("import pkg.missing\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reader, writer = os.pipe()
    try:
        result = main(
            [
                "--diagnostic-writer",
                str(writer),
                "--logical-package",
                "pkg",
                "--logical-entrypoint",
                "pkg.runtime",
                "--confined-module-root",
                str(tmp_path),
                "runtime.py",
                "Interface",
            ]
        )
        payload = json.loads(os.read(reader, 16 * 1024))
    finally:
        os.close(reader)

    assert result == 70
    assert payload == DispatcherError.from_spec(
        "R14",
        reason="the module is outside the validated package",
    ).as_payload()


# famulus-skip: category=platform-contract; reason=this integration case launches with POSIX descriptor inheritance; alternate=the native-handle roundtrip exercises the corresponding registered diagnosis path
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor half of transport")
def test_dispatcher_accepts_registered_private_runner_diagnosis() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = ResolvedInvocationMetadata(
        caller_module_id="caller",
        target_module_id="target",
        script_interface="target.source.runtime.interface.run",
        target="target.interface.run",
        pattern="default",
        cwd=root,
        command=[],
        stdin=False,
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    resolved = dispatcher_core.ResolvedInvocation(
        metadata,
        [
            sys.executable,
            "-P",
            "-m",
            "officina.runtime.python_machine_interface_runner",
        ],
        environment,
    )

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(resolved, text=True)

    assert caught.value.code == "dispatcher.runner_request_invalid"
    assert str(caught.value) == (
        "Python interface runner requires a gateway path and process entry."
    )


def _transport_resolved(tmp_path: Path) -> dispatcher_core.ResolvedInvocation:
    metadata = ResolvedInvocationMetadata(
        caller_module_id="caller",
        target_module_id="target",
        script_interface="target.source.runtime.interface.run",
        target="target.interface.run",
        pattern="default",
        cwd=tmp_path,
        command=[],
        stdin=False,
    )
    return dispatcher_core.ResolvedInvocation(
        metadata,
        [sys.executable, "-P", "-m", "runner", "gateway.py", "Entry"],
        {},
    )


def _write_private_diagnosis(
    command: list[str], kwargs: dict[str, object], payload: bytes
) -> int:
    writer = int(command[5])
    if os.name == "nt":
        import _winapi

        startupinfo = kwargs["startupinfo"]
        assert startupinfo.lpAttributeList["handle_list"] == [writer]
        _winapi.WriteFile(writer, payload)
    else:
        assert kwargs["pass_fds"] == (writer,)
        os.write(writer, payload)
    return writer


def _assert_private_diagnosis_writer_closed(writer: int) -> None:
    if os.name == "nt":
        import _winapi

        with pytest.raises(OSError):
            _winapi.WriteFile(writer, b"")
    else:
        with pytest.raises(OSError):
            os.fstat(writer)


class _SuccessfulTransportProcess:
    returncode = 0

    def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
        return b"", b""


def _fake_windows_platform(
    monkeypatch: pytest.MonkeyPatch,
    popen,
) -> tuple[list[tuple[int, bool]], type]:
    inheritance: list[tuple[int, bool]] = []

    class StartupInfo:
        lpAttributeList: dict[str, list[int]]

    monkeypatch.setattr(direct_runtime.os, "name", "nt")
    monkeypatch.setattr(
        direct_runtime.os,
        "set_handle_inheritable",
        lambda handle, value: inheritance.append((handle, value)),
        raising=False,
    )
    monkeypatch.setattr(direct_runtime.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(direct_runtime.subprocess, "Popen", popen)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        types.SimpleNamespace(get_osfhandle=lambda descriptor: descriptor),
    )
    return inheritance, StartupInfo


def test_windows_launch_uses_only_the_private_diagnostic_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    def popen(_command: list[str], **kwargs: object) -> _SuccessfulTransportProcess:
        observed.append(kwargs)
        return _SuccessfulTransportProcess()

    resolved = _transport_resolved(tmp_path)
    with monkeypatch.context() as platform:
        _fake_windows_platform(platform, popen)
        dispatcher_core._run_resolved_invocation(resolved)

    assert observed[0]["close_fds"] is True
    assert len(observed[0]["startupinfo"].lpAttributeList["handle_list"]) == 1
    assert "pass_fds" not in observed[0]


def test_windows_launch_restores_and_closes_duplicated_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited: list[int] = []

    def popen(_command: list[str], **kwargs: object) -> _SuccessfulTransportProcess:
        inherited.extend(kwargs["startupinfo"].lpAttributeList["handle_list"])
        return _SuccessfulTransportProcess()

    resolved = _transport_resolved(tmp_path)
    with monkeypatch.context() as platform:
        inheritance, _ = _fake_windows_platform(platform, popen)
        dispatcher_core._run_resolved_invocation(resolved)

    assert inheritance == [(inherited[0], True), (inherited[0], False)]
    with pytest.raises(OSError):
        os.fstat(inherited[0])


def test_windows_popen_failure_restores_and_closes_duplicated_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited: list[int] = []

    def popen(_command: list[str], **kwargs: object):
        inherited.extend(kwargs["startupinfo"].lpAttributeList["handle_list"])
        raise OSError("launch failed")

    resolved = _transport_resolved(tmp_path)
    with monkeypatch.context() as platform:
        inheritance, _ = _fake_windows_platform(platform, popen)
        with pytest.raises(DispatcherError) as caught:
            dispatcher_core._run_resolved_invocation(resolved)

    assert caught.value.code == "dispatcher.launch_failed"
    assert inheritance == [(inherited[0], True), (inherited[0], False)]
    with pytest.raises(OSError):
        os.fstat(inherited[0])


def test_windows_restore_failure_after_launch_closes_writer_and_reaps_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited: list[int] = []
    communicated: list[bool] = []

    class Process(_SuccessfulTransportProcess):
        def communicate(self, **kwargs: object) -> tuple[bytes, bytes]:
            communicated.append(True)
            return super().communicate(**kwargs)

    def popen(_command: list[str], **kwargs: object) -> Process:
        inherited.extend(kwargs["startupinfo"].lpAttributeList["handle_list"])
        return Process()

    def set_inheritable(_handle: int, value: bool) -> None:
        if not value:
            raise OSError("restore failed")

    resolved = _transport_resolved(tmp_path)
    with monkeypatch.context() as platform:
        _fake_windows_platform(platform, popen)
        platform.setattr(direct_runtime.os, "set_handle_inheritable", set_inheritable)
        result = dispatcher_core._run_resolved_invocation(resolved)

    assert result.returncode == 0
    assert communicated == [True]
    with pytest.raises(OSError):
        os.fstat(inherited[0])


def test_windows_restore_failure_preserves_popen_failure_and_closes_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited: list[int] = []
    launch_failure = OSError("launch failed")

    def popen(_command: list[str], **kwargs: object):
        inherited.extend(kwargs["startupinfo"].lpAttributeList["handle_list"])
        raise launch_failure

    def set_inheritable(_handle: int, value: bool) -> None:
        if not value:
            raise OSError("restore failed")

    resolved = _transport_resolved(tmp_path)
    with monkeypatch.context() as platform:
        _fake_windows_platform(platform, popen)
        platform.setattr(direct_runtime.os, "set_handle_inheritable", set_inheritable)
        with pytest.raises(DispatcherError) as caught:
            dispatcher_core._run_resolved_invocation(resolved)

    assert caught.value.code == "dispatcher.launch_failed"
    assert caught.value.__cause__ is launch_failure
    with pytest.raises(OSError):
        os.fstat(inherited[0])


def test_windows_runner_clears_writer_before_target_can_spawn_grandchild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, writer = os.pipe()
    events: list[tuple[str, object]] = []
    with monkeypatch.context() as platform:
        platform.setattr(python_runner.os, "name", "nt")
        platform.setattr(python_runner.os, "O_BINARY", 0, raising=False)
        platform.setattr(
            python_runner.os,
            "set_handle_inheritable",
            lambda handle, value: events.append(("inherit", (handle, value))),
            raising=False,
        )
        platform.setitem(
            sys.modules,
            "msvcrt",
            types.SimpleNamespace(
                open_osfhandle=lambda handle, _flags: (
                    events.append(("open", handle)) or writer
                )
            ),
        )
        platform.setitem(
            sys.modules,
            "_winapi",
            types.SimpleNamespace(CloseHandle=lambda _handle: None),
        )
        try:
            assert main(["--diagnostic-writer", "123"]) == 70
            json.loads(os.read(reader, 16 * 1024))
        finally:
            os.close(reader)

    assert events == [("inherit", (123, False)), ("open", 123)]


def test_windows_launch_lock_isolates_concurrent_diagnostic_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active: set[int] = set()
    maximum_active = 0
    state_lock = threading.Lock()

    def set_inheritable(handle: int, value: bool) -> None:
        nonlocal maximum_active
        with state_lock:
            (active.add if value else active.discard)(handle)
            maximum_active = max(maximum_active, len(active))

    def popen(_command: list[str], **_kwargs: object) -> _SuccessfulTransportProcess:
        threading.Event().wait(0.01)
        return _SuccessfulTransportProcess()

    resolved = [_transport_resolved(tmp_path) for _ in range(4)]
    with monkeypatch.context() as platform:
        _fake_windows_platform(platform, popen)
        platform.setattr(
            direct_runtime.os,
            "set_handle_inheritable",
            set_inheritable,
            raising=False,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(dispatcher_core._run_resolved_invocation, resolved))

    assert [result.returncode for result in results] == [0, 0, 0, 0]
    assert maximum_active == 1
    assert active == set()


# famulus-skip: category=platform-contract; reason=this case requires native process handles; alternate=simulated native-handle tests cover allowlisting, restoration, cleanup, noninheritance, and concurrency on every host
@pytest.mark.skipif(os.name != "nt", reason="native Windows transport")
def test_native_windows_private_diagnosis_round_trip() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = ResolvedInvocationMetadata(
        caller_module_id="caller",
        target_module_id="target",
        script_interface="target.source.runtime.interface.run",
        target="target.interface.run",
        pattern="default",
        cwd=root,
        command=[],
        stdin=False,
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    resolved = dispatcher_core.ResolvedInvocation(
        metadata,
        [sys.executable, "-P", "-m", "officina.runtime.python_machine_interface_runner"],
        environment,
    )

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(resolved, text=True)

    assert caught.value.code == "dispatcher.runner_request_invalid"


def test_private_diagnosis_wins_before_output_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 70

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"\xff", b"\xff"

    def popen(command: list[str], **kwargs: object) -> Process:
        payload = DispatcherError.from_spec("R01").as_payload()
        _write_private_diagnosis(
            command, kwargs, json.dumps(payload).encode("utf-8")
        )
        return Process()

    monkeypatch.setattr(direct_runtime.subprocess, "Popen", popen)

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(
            _transport_resolved(tmp_path), text=True
        )

    assert caught.value.code == "dispatcher.runner_request_invalid"


def test_invalid_private_payload_does_not_reclassify_exit_70(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 70

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"ordinary", b"failure"

    def popen(command: list[str], **kwargs: object) -> Process:
        _write_private_diagnosis(
            command, kwargs, b'{"code":"unknown"} trailing'
        )
        return Process()

    monkeypatch.setattr(direct_runtime.subprocess, "Popen", popen)
    result = dispatcher_core._run_resolved_invocation(
        _transport_resolved(tmp_path), text=True
    )

    assert result.returncode == 70
    assert result.stdout == "ordinary"
    assert result.stderr == "failure"


@pytest.mark.parametrize("padding", [b" ", b"\n", b"\t"])
def test_whitespace_around_private_payload_does_not_reclassify_exit_70(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    padding: bytes,
) -> None:
    class Process:
        returncode = 70

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"ordinary", b"failure"

    def popen(command: list[str], **kwargs: object) -> Process:
        payload = json.dumps(DispatcherError.from_spec("R01").as_payload()).encode()
        _write_private_diagnosis(command, kwargs, padding + payload)
        return Process()

    monkeypatch.setattr(direct_runtime.subprocess, "Popen", popen)
    result = dispatcher_core._run_resolved_invocation(
        _transport_resolved(tmp_path), text=True
    )

    assert result.returncode == 70
    assert result.stdout == "ordinary"
    assert result.stderr == "failure"


@pytest.mark.parametrize(
    "diagnosis",
    [
        json.dumps(DispatcherError.from_spec("R01").as_payload()).encode() * 2,
        json.dumps(DispatcherError.from_spec("R01").as_payload()).encode()
        + b" " * (16 * 1024),
    ],
    ids=["multiple", "oversized"],
)
def test_invalid_private_diagnosis_records_remain_ordinary_exit_70(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnosis: bytes,
) -> None:
    inherited_writer: list[int] = []

    class Process:
        returncode = 70

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"ordinary", b"failure"

    def popen(command: list[str], **kwargs: object) -> Process:
        writer = _write_private_diagnosis(command, kwargs, diagnosis)
        inherited_writer.append(writer)
        return Process()

    monkeypatch.setattr(direct_runtime.subprocess, "Popen", popen)
    result = dispatcher_core._run_resolved_invocation(
        _transport_resolved(tmp_path), text=True
    )

    assert result.returncode == 70
    assert result.stderr == "failure"
    _assert_private_diagnosis_writer_closed(inherited_writer[0])


def test_output_decode_failure_precedes_checked_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 4

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"\xff", b""

    monkeypatch.setattr(
        direct_runtime.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Process(),
    )

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(
            _transport_resolved(tmp_path), text=True, check=True
        )

    assert caught.value.code == "dispatcher.output_decode_failed"


def test_checked_nonzero_uses_registered_dispatcher_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 4

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            return b"", b""

    monkeypatch.setattr(
        direct_runtime.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Process(),
    )

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(
            _transport_resolved(tmp_path), text=True, check=True
        )

    assert caught.value.code == "dispatcher.checked_process_failed"
    assert caught.value.as_payload()["returncode"] == 4


def test_timeout_terminates_then_kills_and_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Process:
        returncode = None
        calls = 0

        def communicate(self, **_kwargs: object) -> tuple[bytes, bytes]:
            self.calls += 1
            events.append(f"communicate:{_kwargs.get('timeout')}")
            if self.calls < 3:
                raise subprocess.TimeoutExpired(["runner"], 0.01)
            self.returncode = -9
            return b"", b""

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

    monkeypatch.setattr(
        direct_runtime.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Process(),
    )

    with pytest.raises(DispatcherError) as caught:
        dispatcher_core._run_resolved_invocation(
            _transport_resolved(tmp_path), timeout=0.01, text=True, check=True
        )

    assert caught.value.code == "dispatcher.execution_timeout"
    assert events == ["communicate:0.01", "terminate", "communicate:1", "kill", "communicate:None"]
