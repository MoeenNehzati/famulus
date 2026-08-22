"""Route-smoke coverage for dispatcher-resolved machine interfaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import pytest
import yaml

from officina.blueprints.graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from officina.blueprints.inventory import BlueprintInventoryError
from officina.blueprints.process_binding import gateway_language_name
import officina.runtime.python_machine_interface as python_interface


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_MODULE = "officina.runtime.python_machine_interface_runner"


@dataclass(frozen=True)
class RouteSmokeCase:
    target: str


def _v4_contract() -> dict[str, object]:
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
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
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
            "consistency": {"snapshot": "One snapshot."},
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


def _dispatcher_env() -> dict[str, str]:
    env = os.environ.copy()
    src_root = str(REPO_ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_root if not existing_pythonpath else os.pathsep.join([src_root, existing_pythonpath])
    )
    return env


def _runner_interfaces(
    repo_root: Path = REPO_ROOT,
    *,
    expected_schema_version: int = 6,
    schema_root: Path | None = None,
) -> list[RouteSmokeCase]:
    graph = load_repository_blueprint_graph(
        repo_root,
        expected_schema_version=expected_schema_version,
        schema_root=schema_root,
    )
    return _runner_interfaces_from_graph(graph)


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


def _route_smoke_cases(
    repo_root: Path = REPO_ROOT,
    *,
    expected_schema_version: int = 6,
    schema_root: Path | None = None,
) -> list[RouteSmokeCase]:
    return _runner_interfaces(
        repo_root,
        expected_schema_version=expected_schema_version,
        schema_root=schema_root,
    )


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


def test_dispatcher_cli_text_requires_exact_repository_config(tmp_path: Path) -> None:
    result = _run_dispatcher(
        [
            "--caller-skill",
            "demo-caller",
            "nonexistent-module.interface.does-not-exist",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "error: dispatcher requires the exact repository configuration path\n"


def test_dispatcher_cli_reports_invalid_repository_config_without_traceback(
    tmp_path: Path,
) -> None:
    config = tmp_path / "officina.toml"
    config.write_text("not valid = [")
    result = _run_dispatcher(
        [
            "--repository-config",
            str(config),
            "--caller-skill",
            "demo-caller",
            "nonexistent-module.interface.does-not-exist",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert "Traceback" not in result.stderr


def test_dispatcher_cli_error_format_json_emits_structured_payload(
    tmp_path: Path,
) -> None:
    result = _run_dispatcher(
        [
            "--caller-skill",
            "demo-caller",
            "--error-format",
            "json",
            "nonexistent-module.interface.does-not-exist",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["schema_version"] == 1
    assert payload["code"] == "dispatcher.runtime_misconfigured"
    assert payload["caller_module_id"] == "demo-caller"
    assert payload["target_module_id"] == "nonexistent-module"
    assert "token" not in result.stderr.lower()
    assert "secret" not in result.stderr.lower()


@pytest.mark.parametrize(
    ("language", "included"),
    [
        ("Python", True),
        ("Python==3.11", True),
        ("Python>=3.11", True),
        ("Python>3.11", True),
        ("Python<=3.13", True),
        ("Python<4", True),
        ("Java>=17", False),
    ],
)
def test_route_smoke_discovers_v4_python_process_exports(
    tmp_path: Path,
    language: str,
    included: bool,
) -> None:
    module = tmp_path / "skills" / "demo-skill"
    runtime = module / "_rtx"
    runtime.mkdir(parents=True)
    (runtime / "worker.py").write_text("class Interface: pass\n", encoding="utf-8")
    (module / "blueprints").mkdir()
    source_id = "demo-skill.source.worker"
    source_interface = f"{source_id}.interface.run"
    (module / "blueprints" / "worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "description": "Worker.",
                "gateway": {"path": "_rtx/worker.py", "language": language},
                "content": [r"_rtx/worker\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                        source_interface: {
                            "version": 1,
                            "description": "Run.",
                            "contract": _v4_contract(),
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
                "id": "demo-skill",
                "version": 1,
                "description": "Demo.",
                "gateway": {"path": "_rtx/worker.py", "language": language},
                "content": [r"_rtx/worker\.py"],
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
                    "demo-skill.interface.run": {
                        "source_interface": source_interface,
                        "access": {"allow_all_modules": True, "allowed_callers": []},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    expected = [
        RouteSmokeCase("demo-skill.interface.run")
    ] if included else []
    assert _route_smoke_cases(
        tmp_path,
        expected_schema_version=4,
        schema_root=REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v4",
    ) == expected


def test_route_smoke_does_not_fall_back_when_v4_inventory_is_malformed(
    tmp_path: Path,
) -> None:
    module = tmp_path / "skills" / "demo-skill"
    (module / "blueprints").mkdir(parents=True)
    (module / "blueprint.yaml").write_text(
        "schema_version: 4\n? [not, a, string]: invalid\n",
        encoding="utf-8",
    )
    (module / "blueprints" / "broken.yaml").write_text(
        "schema_version: 4\n? [not, a, string]: invalid\n",
        encoding="utf-8",
    )

    with pytest.raises(BlueprintInventoryError):
        _route_smoke_cases(
            tmp_path,
            expected_schema_version=4,
            schema_root=REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v4",
        )


def test_live_blueprints_have_runner_interfaces_to_smoke_or_skip(
    live_runner_interfaces: tuple[RouteSmokeCase, ...],
) -> None:
    if not live_runner_interfaces:
        # famulus-skip: category=empty-contract; reason=no live runner-backed machine interfaces exist; alternate=route-smoke extraction unit tests cover discovery logic
        pytest.skip(f"no machine interfaces currently invoke {RUNNER_MODULE}")

    assert live_runner_interfaces


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
