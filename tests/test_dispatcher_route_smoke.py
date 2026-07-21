"""Route-smoke coverage for dispatcher-resolved machine interfaces."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pytest
import yaml

from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.blueprint_inventory import BlueprintInventoryError


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_MODULE = "officina.runtime.python_machine_interface_runner"


@dataclass(frozen=True)
class RouteSmokeCase:
    skill: str
    interface: str
    canonical_target: str | None = None

    @property
    def target(self) -> str:
        return self.canonical_target or f"{self.skill}.machine.{self.interface}"


def _dispatcher_env() -> dict[str, str]:
    env = os.environ.copy()
    src_root = str(REPO_ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_root if not existing_pythonpath else os.pathsep.join([src_root, existing_pythonpath])
    )
    env["AI"] = str(REPO_ROOT)
    return env


def _runtime_invokes_python_runner(invocation: dict[str, Any]) -> bool:
    return invocation.get("kind") == "python_machine_interface"


def _iter_blueprints(repo_root: Path = REPO_ROOT) -> Iterable[tuple[str, dict[str, Any]]]:
    for blueprint_path in sorted((repo_root / "skills").glob("*/blueprint.yaml")):
        raw = yaml.safe_load(blueprint_path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            yield blueprint_path.parent.name, raw


def _runner_interfaces(repo_root: Path = REPO_ROOT) -> list[RouteSmokeCase]:
    v4_source_blueprints = tuple(
        (repo_root / "skills").glob("*/blueprints/*.yaml")
    )
    if v4_source_blueprints:
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
                and language.split(">", 1)[0].split("=", 1)[0] == "Python"
            ):
                cases.append(
                    RouteSmokeCase(
                        skill=export.module_node_id,
                        interface=export.local_name,
                        canonical_target=export_id,
                    )
                )
        return cases

    cases: list[RouteSmokeCase] = []
    for skill_name, blueprint in _iter_blueprints(repo_root):
        interfaces = blueprint.get("interfaces")
        if not isinstance(interfaces, dict):
            continue
        machine = interfaces.get("machine")
        if not isinstance(machine, dict):
            continue
        for interface_name, interface_spec in machine.items():
            if not isinstance(interface_name, str) or not isinstance(interface_spec, dict):
                continue
            runtime = interface_spec.get("invocation")
            if isinstance(runtime, dict) and _runtime_invokes_python_runner(runtime):
                cases.append(RouteSmokeCase(skill=skill_name, interface=interface_name))
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


def test_discovers_python_machine_runner_interfaces(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "demo-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "blueprint.yaml").write_text(
        """
category: coding-development-assistant
interfaces:
  machine:
    route-check:
      version: 1
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/demo.py:Interface
      patterns:
        - min_positionals: 1
""".lstrip(),
        encoding="utf-8",
    )

    assert _runner_interfaces(tmp_path) == [RouteSmokeCase("demo-skill", "route-check")]


def test_route_smoke_cases_include_all_python_machine_interfaces(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "demo-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "blueprint.yaml").write_text(
        """
category: coding-development-assistant
interfaces:
  machine:
    route-check:
      version: 1
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/demo.py:Interface
      patterns:
        - min_positionals: 1
    requires-project:
      version: 1
      invocation:
        kind: python_machine_interface
        entrypoint: _rtx/demo.py:Interface
      patterns:
        - min_positionals: 1
          max_positionals: 1
""".lstrip(),
        encoding="utf-8",
    )

    assert _route_smoke_cases(tmp_path) == [
        RouteSmokeCase("demo-skill", "route-check"),
        RouteSmokeCase("demo-skill", "requires-project"),
    ]


def test_route_smoke_discovers_v4_python_process_exports(tmp_path: Path) -> None:
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
                "gateway": {"path": "_rtx/worker.py", "language": "Python>=3.11"},
                "content": [r"_rtx/worker\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    source_interface: {
                        "version": 1,
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
                "gateway": {"path": "_rtx/worker.py", "language": "Python>=3.11"},
                "content": [r"_rtx/worker\.py"],
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

    assert _route_smoke_cases(tmp_path) == [
        RouteSmokeCase(
            "demo-skill",
            "run",
            "demo-skill.interface.run",
        )
    ]


def test_route_smoke_does_not_fall_back_when_v4_inventory_is_malformed(
    tmp_path: Path,
) -> None:
    module = tmp_path / "skills" / "demo-skill"
    (module / "blueprints").mkdir(parents=True)
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


def test_python_machine_runner_interfaces_accept_route_smoke(tmp_path: Path) -> None:
    cases = _route_smoke_cases()
    if not cases:
        # famulus-skip: category=empty-contract; reason=no python_machine_interface interfaces exist; alternate=route-smoke extraction unit tests cover case selection
        pytest.skip("no python_machine_interface machine interfaces currently exist")

    failures: list[str] = []
    for case in cases:
        result = _run_dispatcher(
            ["--caller-skill", case.skill, case.target, "--route-smoke"],
            cwd=tmp_path,
        )
        if result.returncode != 0:
            failures.append(
                f"{case.target} exited {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

    assert not failures, "\n\n".join(failures)
