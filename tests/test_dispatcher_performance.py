"""Structural performance checks and opt-in dispatcher latency benchmarks."""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from officina.configuration.repository import (
    RepositoryConfiguration,
    load_repository_configuration,
)
from officina.dispatcher.direct_authorization import resolve_direct_invocation


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "officina.toml"
LIVE_TARGET = "daily-plan._rtx.interface.render-plan"
CALLER = "pilot"
TARGET = "pilot._rtx.interface.run"


# famulus-skip: category=live-smoke-opt-in; reason=absolute wall-clock budgets measure host contention as well as dispatcher work; alternate=structural resolution invariants run in CI, while this benchmark is available explicitly on a controlled host
requires_reference_host = pytest.mark.skipif(
    os.environ.get("FAMULUS_RUN_PERFORMANCE_GATES") != "1",
    reason=(
        "latency benchmarks require an explicitly controlled host; "
        "set FAMULUS_RUN_PERFORMANCE_GATES=1"
    ),
)

# Cold-process samples cost one subprocess each, so the count trades runtime for
# resistance to transient spikes. An odd count keeps the median a real sample,
# and 21 requires 11 slow samples to shift the gate where 10 required 5.
FRESH_CLI_SAMPLES = 21


def _fresh_cli_budget_ms(platform: str | None = None) -> int:
    """Return the cold-process median latency budget for this OS family."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        # Hosted Windows process creation is materially slower than the
        # Linux/macOS reference hosts. Keep a bounded gate around the observed
        # 131--133 ms medians without treating OS startup cost as a dispatcher
        # regression.
        return 175
    if platform.startswith("linux"):
        # The full parallel suite can leave short-lived CPU contention behind
        # before this serial gate begins. Preserve a strict cold-process bound
        # with enough headroom for that measured hosted-runner variance.
        return 125
    # GitHub's hosted Apple Silicon runners have measured 112--133 ms medians
    # across image revisions. Keep headroom for normal host variance while
    # retaining separation from the observed load-contaminated 194 ms median.
    return 150


def _write_yaml(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _pilot(tmp_path: Path) -> RepositoryConfiguration:
    modules = tmp_path / "skills"
    config = tmp_path / "officina.toml"
    config.write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n',
        encoding="utf-8",
    )
    access = {"allow_all_modules": False, "allowed_callers": [CALLER]}
    _write_yaml(
        modules / "pilot" / "blueprint.yaml",
        {
            "schema_version": 6,
            "node_type": "module",
            "id": "pilot",
            "version": 1,
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "discovery": {"mechanism": "skill"},
            "authority": {"owns_filesystem": []},
            "sources": {},
            "children": {"_rtx": {}},
            "namespace_exports": {
                "_rtx": {
                    "version": 1,
                    "access": access,
                    "surface": {"only": {TARGET: 1}},
                }
            },
            "exports": {},
        },
    )
    source_id = "pilot._rtx.source.runtime"
    source_interface = f"{source_id}.interface.run"
    _write_yaml(
        modules / "pilot" / "_rtx" / "blueprint.yaml",
        {
            "schema_version": 6,
            "node_type": "module",
            "id": "pilot._rtx",
            "version": 1,
            "gateway": {"path": "__init__.py", "language": "Python>=3.11"},
            "content": [r"__init__\.py"],
            "authority": {"owns_filesystem": []},
            "sources": {
                source_id: {
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/runtime.yaml",
                    }
                }
            },
            "children": {},
            "namespace_exports": {},
            "exports": {
                TARGET: {
                    "source_interface": source_interface,
                    "access": access,
                }
            },
        },
    )
    _write_yaml(
        modules / "pilot" / "_rtx" / "blueprints" / "runtime.yaml",
        {
            "schema_version": 6,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
            "content": [r"runtime\.py"],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                source_interface: {
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
    )
    (modules / "pilot" / "_rtx" / "runtime.py").write_text(
        "class Interface: pass\n",
        encoding="utf-8",
    )
    return RepositoryConfiguration(1, config, tmp_path, (modules,))


def _resolve(configuration: RepositoryConfiguration) -> None:
    resolve_direct_invocation(
        configuration=configuration,
        caller_module_id=CALLER,
        interface_id=TARGET,
        interface_version=None,
        argv=[],
        stdin_requested=False,
        host_caller=True,
    )


def _milliseconds(samples_ns: list[int]) -> list[float]:
    return [sample / 1_000_000 for sample in samples_ns]


def test_fresh_cli_budgets_are_platform_specific() -> None:
    assert _fresh_cli_budget_ms("linux") == 125
    assert _fresh_cli_budget_ms("darwin") == 150
    assert _fresh_cli_budget_ms("win32") == 175


@requires_reference_host
def test_warm_direct_resolution_median_is_below_50_ms(tmp_path: Path) -> None:
    configuration = _pilot(tmp_path)
    samples = []
    for _ in range(15):
        started = time.perf_counter_ns()
        _resolve(configuration)
        samples.append(time.perf_counter_ns() - started)

    assert statistics.median(_milliseconds(samples)) < 50


@requires_reference_host
def test_live_inventory_warm_resolution_median_is_below_50_ms() -> None:
    configuration = load_repository_configuration(LIVE_CONFIG)
    samples = []
    for _ in range(15):
        started = time.perf_counter_ns()
        resolve_direct_invocation(
            configuration=configuration,
            caller_module_id="daily-plan",
            interface_id=LIVE_TARGET,
            interface_version=None,
            argv=["--route-smoke"],
            stdin_requested=False,
            host_caller=True,
        )
        samples.append(time.perf_counter_ns() - started)

    assert statistics.median(_milliseconds(samples)) < 50


def test_resolution_work_ignores_unrelated_modules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configuration = _pilot(tmp_path)
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
    _resolve(configuration)
    baseline = (len(reads), len(probes))
    assert baseline[0] > 0 and baseline[1] > 0
    for index in range(500):
        (configuration.module_roots[0] / f"unrelated-{index}").mkdir()
    reads.clear()
    probes.clear()
    _resolve(configuration)

    assert (len(reads), len(probes)) == baseline


def test_direct_lookup_uses_no_enumeration_subprocess_or_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configuration = _pilot(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden operation reached")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(os, "walk", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    _resolve(configuration)


@requires_reference_host
def test_fresh_checkout_cli_meets_reference_budget(tmp_path: Path) -> None:
    configuration = _pilot(tmp_path)
    command = [
        sys.executable,
        "-m",
        "officina.dispatcher.cli",
        "--repository-config",
        str(configuration.config_path),
        "--caller-skill",
        CALLER,
        "--dry-run",
        TARGET,
    ]
    samples = []
    for _ in range(FRESH_CLI_SAMPLES):
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

    assert statistics.median(_milliseconds(samples)) < _fresh_cli_budget_ms()


@requires_reference_host
def test_live_inventory_fresh_cli_meets_reference_budget() -> None:
    command = [
        sys.executable,
        "-m",
        "officina.dispatcher.cli",
        "--repository-config",
        str(LIVE_CONFIG),
        "--caller-skill",
        "daily-plan",
        "--dry-run",
        LIVE_TARGET,
        "--route-smoke",
    ]
    samples = []
    for _ in range(FRESH_CLI_SAMPLES):
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

    assert statistics.median(_milliseconds(samples)) < _fresh_cli_budget_ms()
