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


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_MODULE = "officina.runtime.python_machine_interface_runner"


@dataclass(frozen=True)
class RouteSmokeCase:
    skill: str
    interface: str

    @property
    def target(self) -> str:
        return f"{self.skill}.machine.{self.interface}"


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
