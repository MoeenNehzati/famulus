"""Reference-host latency gates for the route-local v6 dispatcher."""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path

import yaml

from officina.common.repository_configuration import RepositoryConfiguration
from officina.common.repository_configuration import load_repository_configuration
from officina.dispatcher.direct_authorization import resolve_direct_invocation


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "officina.toml"
TARGET = "daily-plan._rtx.interface.plan-storage"


def _top_level_fixture(tmp_path: Path) -> RepositoryConfiguration:
    modules = tmp_path / "skills"
    module = modules / "top"
    (module / "blueprints").mkdir(parents=True)
    (tmp_path / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n'
    )
    (module / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "top",
                "version": 1,
                "gateway": {"path": "__init__.py", "language": "Python"},
                "content": [r"__init__\.py"],
                "discovery": {"mechanism": "skill"},
                "authority": {"owns_filesystem": []},
                "sources": {
                    "top.source.runtime": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/runtime.yaml",
                        }
                    }
                },
                "children": {},
                "namespace_exports": {},
                "exports": {
                    "top.interface.execute": {
                        "source_interface": "top.source.runtime.interface.execute",
                        "access": {"allow_all_modules": True, "allowed_callers": []},
                    }
                },
            },
            sort_keys=False,
        )
    )
    (module / "blueprints" / "runtime.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "top.source.runtime",
                "version": 1,
                "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
                "content": [r"runtime\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    "top.source.runtime.interface.execute": {
                        "version": 1,
                        "contract": {"arguments": {}},
                        "process_binding": {
                            "kind": "process",
                            "entry": "Interface",
                            "args_prefix": ["run"],
                            "arguments": {},
                        },
                    }
                },
            },
            sort_keys=False,
        )
    )
    (module / "runtime.py").write_text("class Interface: pass\n")
    return RepositoryConfiguration(
        1,
        tmp_path / "officina.toml",
        tmp_path,
        (modules,),
    )


def _milliseconds(samples_ns: list[int]) -> list[float]:
    return [sample / 1_000_000 for sample in samples_ns]


def _p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, (95 * len(samples) + 99) // 100 - 1)]


def test_warm_direct_resolution_median_is_below_50_ms() -> None:
    configuration = load_repository_configuration(CONFIG)
    samples = []
    for _ in range(15):
        started = time.perf_counter_ns()
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="daily-plan",
            interface_id=TARGET,
            interface_version=None,
            argv=["--route-smoke"],
            stdin_requested=False,
            host_caller=True,
        )
        samples.append(time.perf_counter_ns() - started)

    assert statistics.median(_milliseconds(samples)) < 50


def test_top_level_resolution_and_read_count_ignore_unrelated_modules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configuration = _top_level_fixture(tmp_path)
    original_open = Path.open
    original_lstat = Path.lstat
    reads: list[Path] = []
    probes: list[Path] = []

    def recording_open(path: Path, *args, **kwargs):
        reads.append(path)
        return original_open(path, *args, **kwargs)

    def recording_lstat(path: Path, *args, **kwargs):
        probes.append(path)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr(Path, "lstat", recording_lstat)
    resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="top",
        interface_id="top.interface.execute",
        interface_version=None,
        argv=[],
        stdin_requested=False,
        host_caller=True,
    )
    baseline = (len(reads), len(probes))
    assert baseline[0] > 0
    assert baseline[1] > 0
    for index in range(500):
        (configuration.module_roots[0] / f"unrelated-{index}").mkdir()
    reads.clear()
    probes.clear()
    started = time.perf_counter_ns()
    resolve_direct_invocation(
        configuration=configuration,
        caller_module_id="top",
        interface_id="top.interface.execute",
        interface_version=None,
        argv=[],
        stdin_requested=False,
        host_caller=True,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000

    assert (len(reads), len(probes)) == baseline
    assert elapsed_ms < 50


def test_fresh_process_dry_run_median_and_p95_meet_reference_budget() -> None:
    command = [
        sys.executable,
        "-m",
        "officina.dispatcher.cli",
        "--repository-config",
        str(CONFIG),
        "--caller-skill",
        "daily-plan",
        "--dry-run",
        TARGET,
        "--route-smoke",
    ]
    samples = []
    for _ in range(10):
        started = time.perf_counter_ns()
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT / "src",
            capture_output=True,
            text=True,
            check=False,
        )
        samples.append(time.perf_counter_ns() - started)
        assert completed.returncode == 0, completed.stderr

    elapsed_ms = _milliseconds(samples)
    assert statistics.median(elapsed_ms) < 100
    assert _p95(elapsed_ms) < 150
