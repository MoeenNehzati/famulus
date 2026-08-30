"""Route-smoke coverage for dispatcher-resolved machine interfaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from officina.blueprints.graph import (
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.process_binding import gateway_language_name
from officina.dispatcher import cli as dispatcher_cli
import officina.runtime.python_machine_interface as python_interface


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RouteSmokeCase:
    target: str


def _dispatcher_env() -> dict[str, str]:
    env = os.environ.copy()
    src_root = str(REPO_ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_root if not existing_pythonpath else os.pathsep.join([src_root, existing_pythonpath])
    )
    return env


def _runner_interfaces_from_graph(
    graph: RepositoryBlueprintGraph,
) -> list[RouteSmokeCase]:
    cases: list[RouteSmokeCase] = []
    for export_id, export in sorted(graph.exports.items()):
        if export.source_node_id is None:
            continue
        source = graph.nodes[export.source_node_id]
        gateway = source.declaration.get("gateway")
        language = gateway.get("language") if isinstance(gateway, dict) else None
        if (
            isinstance(export.declaration.get("process_binding"), dict)
            and isinstance(language, str)
            and gateway_language_name(language) == "Python"
        ):
            cases.append(RouteSmokeCase(export_id))
    return cases


def _route_smoke_specifications(
    graph: RepositoryBlueprintGraph,
    cases: tuple[RouteSmokeCase, ...],
) -> tuple[tuple[Path, python_interface.PythonProcessTarget], ...]:
    specifications: list[
        tuple[Path, python_interface.PythonProcessTarget]
    ] = []
    for case in cases:
        export = graph.exports[case.target]
        source = graph.nodes[export.source_node_id]
        gateway = source.declaration["gateway"]
        binding = export.declaration["process_binding"]
        module_id = graph.source_modules[source.node_id]
        logical_package = python_interface.logical_python_package_name(module_id)
        module_path = Path(gateway["path"])
        physical_parts = (
            module_path.parent.parts
            if module_path.name == "__init__.py"
            else (*module_path.parent.parts, module_path.stem)
        )
        suffix = ".".join(
            part for part in physical_parts if part not in {"", "."}
        )
        target = python_interface.PythonProcessTarget(
            module_path,
            binding["entry"],
            logical_package=logical_package,
            logical_entrypoint=(
                logical_package
                if not suffix
                else f"{logical_package}.{suffix}"
            ),
        )
        specifications.append((source.module_root, target))
    return tuple(specifications)


@pytest.fixture(scope="module")
def live_repository_graph() -> RepositoryBlueprintGraph:
    """Load the immutable live repository graph once for this test module."""

    return load_repository_blueprint_graph(REPO_ROOT)


@pytest.fixture(scope="module")
def live_runner_interfaces(
    live_repository_graph: RepositoryBlueprintGraph,
) -> tuple[RouteSmokeCase, ...]:
    """Reuse one graph when discovering all live Python runner exports."""

    return tuple(_runner_interfaces_from_graph(live_repository_graph))


@pytest.fixture(scope="module")
def live_route_smoke_specifications(
    live_repository_graph: RepositoryBlueprintGraph,
    live_runner_interfaces: tuple[RouteSmokeCase, ...],
) -> tuple[tuple[Path, python_interface.PythonProcessTarget], ...]:
    """Build the complete live route-smoke manifest once from the shared graph."""

    return _route_smoke_specifications(
        live_repository_graph,
        live_runner_interfaces,
    )


def _run_dispatcher(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "officina.dispatcher.cli", *args],
        cwd=cwd,
        env=_dispatcher_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=30,
    )


def test_dispatcher_module_cli_help_is_available(tmp_path: Path) -> None:
    result = _run_dispatcher(["--help"], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.startswith("usage: dispatcher ")
    assert "Invoke a skill machine interface declared in blueprint.yaml." in result.stdout
    assert "--caller-skill" in result.stdout
    assert "--error-format" in result.stdout


def test_dispatcher_cli_formats_configuration_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = "nonexistent-module.interface.does-not-exist"

    monkeypatch.setattr(
        sys,
        "argv",
        ["dispatcher", "--caller-skill", "demo-caller", target],
    )
    assert dispatcher_cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error: dispatcher requires the exact repository configuration path\n"
    )

    config = tmp_path / "officina.toml"
    config.write_text("not valid = [")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dispatcher",
            "--repository-config",
            str(config),
            "--caller-skill",
            "demo-caller",
            target,
        ],
    )
    assert dispatcher_cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dispatcher",
            "--caller-skill",
            "demo-caller",
            "--error-format",
            "json",
            target,
        ],
    )
    assert dispatcher_cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.runtime_misconfigured"
    assert payload["caller_module_id"] == "demo-caller"
    assert payload["target_module_id"] == "nonexistent-module"
    assert "token" not in captured.err.lower()
    assert "secret" not in captured.err.lower()


def test_route_smoke_discovers_and_builds_python_process_targets() -> None:
    source_specs = {
        "demo.source.java": ("Java>=17", Path("java.py"), Path("/repo/java")),
        "demo.source.zeta": ("Python", Path("zeta.py"), Path("/repo/zeta")),
        "demo.source.alpha": (
            "Python>=3.11",
            Path("nested/__init__.py"),
            Path("/repo/alpha"),
        ),
    }
    nodes = {
        source_id: BlueprintNode(
            node_id=source_id,
            node_type="behavioral_source",
            version=1,
            module_root=root,
            blueprint_path=root / "blueprints" / "worker.yaml",
            gateway_path=root / gateway_path,
            declaration={
                "gateway": {"language": language, "path": gateway_path.as_posix()}
            },
        )
        for source_id, (language, gateway_path, root) in source_specs.items()
    }
    export_specs = {
        "zeta.interface.run": ("demo.source.zeta", {"entry": "Zeta"}),
        "missing.interface.run": (None, {"entry": "Missing"}),
        "java.interface.run": ("demo.source.java", {"entry": "Java"}),
        "alpha.interface.run": ("demo.source.alpha", {"entry": "Alpha"}),
        "unbound.interface.run": ("demo.source.zeta", None),
    }
    exports = {
        export_id: InterfaceExport(
            interface_id=export_id,
            version=1,
            local_name="run",
            module_node_id="demo",
            declaration=(
                {"process_binding": {"kind": "process", **binding}}
                if binding is not None
                else {}
            ),
            source_node_id=source_id,
        )
        for export_id, (source_id, binding) in export_specs.items()
    }
    graph = RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=(),
        exports=exports,
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        source_modules={
            "demo.source.alpha": "alpha-module",
            "demo.source.zeta": "zeta-module",
        },
    )

    cases = tuple(_runner_interfaces_from_graph(graph))
    assert cases == (
        RouteSmokeCase("alpha.interface.run"),
        RouteSmokeCase("zeta.interface.run"),
    )

    alpha_package = python_interface.logical_python_package_name("alpha-module")
    zeta_package = python_interface.logical_python_package_name("zeta-module")
    assert _route_smoke_specifications(graph, cases) == (
        (
            Path("/repo/alpha"),
            python_interface.PythonProcessTarget(
                Path("nested/__init__.py"),
                "Alpha",
                logical_package=alpha_package,
                logical_entrypoint=f"{alpha_package}.nested",
            ),
        ),
        (
            Path("/repo/zeta"),
            python_interface.PythonProcessTarget(
                Path("zeta.py"),
                "Zeta",
                logical_package=zeta_package,
                logical_entrypoint=f"{zeta_package}.zeta",
            ),
        ),
    )


def test_python_machine_runner_interfaces_accept_route_smoke(
    live_runner_interfaces: tuple[RouteSmokeCase, ...],
    live_route_smoke_specifications: tuple[
        tuple[Path, python_interface.PythonProcessTarget], ...
    ],
) -> None:
    if not live_runner_interfaces:
        # famulus-skip: category=empty-contract; reason=no python_machine_interface interfaces exist; alternate=route-smoke extraction unit tests cover case selection
        pytest.skip("no python_machine_interface machine interfaces currently exist")

    batch_tracer = getattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        None,
    )
    assert callable(batch_tracer)
    try:
        batch_tracer(REPO_ROOT, live_route_smoke_specifications)
    except python_interface.PythonRouteSmokeTraceError as exc:
        pytest.fail(f"certification route-smoke batch failed:\n{exc}")
