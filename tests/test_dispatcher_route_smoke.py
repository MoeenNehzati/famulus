"""Route-smoke coverage for dispatcher-resolved machine interfaces."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import pytest
import yaml

from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.blueprint_inventory import BlueprintInventoryError
from officina.common.process_binding_compiler import gateway_language_name
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
    env["AI"] = str(REPO_ROOT)
    return env


def _runner_interfaces(repo_root: Path = REPO_ROOT) -> list[RouteSmokeCase]:
    graph = load_repository_blueprint_graph(repo_root)
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


def _route_smoke_cases(repo_root: Path = REPO_ROOT) -> list[RouteSmokeCase]:
    return _runner_interfaces(repo_root)


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
    assert "Invoke a skill machine interface declared in blueprint.yaml." in result.stdout
    assert "--caller-skill" in result.stdout


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
    assert _route_smoke_cases(tmp_path) == expected


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
        _route_smoke_cases(tmp_path)


def test_live_blueprints_have_runner_interfaces_to_smoke_or_skip() -> None:
    if not _runner_interfaces():
        # famulus-skip: category=empty-contract; reason=no live runner-backed machine interfaces exist; alternate=route-smoke extraction unit tests cover discovery logic
        pytest.skip(f"no machine interfaces currently invoke {RUNNER_MODULE}")

    assert _runner_interfaces()


def test_python_machine_runner_interfaces_accept_route_smoke() -> None:
    cases = _route_smoke_cases()
    if not cases:
        # famulus-skip: category=empty-contract; reason=no python_machine_interface interfaces exist; alternate=route-smoke extraction unit tests cover case selection
        pytest.skip("no python_machine_interface machine interfaces currently exist")

    graph = load_repository_blueprint_graph(REPO_ROOT)
    specifications: list[
        tuple[Path, python_interface.PythonProcessTarget]
    ] = []
    for case in cases:
        export = graph.exports[case.target]
        source = graph.nodes[export.source_node_id]
        gateway = source.declaration["gateway"]
        binding = export.declaration["process_binding"]
        target = python_interface.PythonProcessTarget(
            Path(gateway["path"]),
            binding["entry"],
        )
        specifications.append((source.skill_root, target))

    batch_tracer = getattr(
        python_interface,
        "trace_python_route_smoke_dependencies_batch",
        None,
    )
    assert callable(batch_tracer)
    try:
        batch_tracer(REPO_ROOT, specifications)
    except python_interface.PythonRouteSmokeTraceError as exc:
        pytest.fail(f"certification route-smoke batch failed:\n{exc}")
