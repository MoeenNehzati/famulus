#!/usr/bin/env python3
"""Benchmark every configured pre-commit phase and pytest execution group.

The command runs the repository-owned exhaustive gate, activates the live
pytest execution-report plugin in every child group, samples the descendant
process tree, and rejects structurally incomplete evidence.  Ordinary test
failures remain usable for diagnostic timing only; acceptance evidence also
requires a passing gate and every required capability probe.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
from typing import Any, Iterable, Mapping
import urllib.request


EXPECTED_PHASE_IDS = (
    "settings-generation",
    "documentation-generation",
    "preview-generation",
    "gitleaks",
    "validators",
    "python-tests",
)


def _exactly_once(actual: Iterable[str], expected: Iterable[str]) -> bool:
    """Return whether expected identifiers occur exactly once.

    Intent
    ------
    Compare actual and expected identifier multisets while rejecting duplicates.

    Rationale
    ---------
    Set equality alone cannot distinguish one execution from repeated execution.

    Pseudocode
    ----------
    - set counts_match = actual and expected counters are equal
    - return counts_match and every actual count equals one

    Wraps
    -----
    - none
    """
    return Counter(actual) == Counter(expected) and all(
        count == 1 for count in Counter(actual).values()
    )


def assess_run(
    *,
    gate_report: Mapping[str, Any],
    group_report: Mapping[str, Any],
    execution_reports: list[Mapping[str, Any]],
    expected_phase_ids: Iterable[str],
    expected_group_ids: Iterable[str],
    capabilities: Mapping[str, Mapping[str, Any]],
    gate_returncode: int,
) -> dict[str, object]:
    """Classify a measured gate run as rejected, diagnostic, or acceptance evidence.

    Intent
    ------
    Validate structural completeness before separating ordinary failures and
    unavailable required capabilities from acceptance-quality evidence.

    Rationale
    ---------
    Complete failed runs still support diagnostic timing, but absent, duplicate,
    cancelled, or collection-less groups make attribution ambiguous and unusable.

    Pseudocode
    ----------
    - set reasons = structural report phase group and execution defects
    - if structural reasons exist:
      - return rejected incomplete assessment
    - set reasons = ordinary failures and unavailable required capabilities
    - set classification = acceptance when reasons is empty else diagnostic
    - return complete assessment with classification and reasons

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._exactly_once:
      why:
        validates: "Checks phase, group, and execution identifiers for exact-once coverage."
    """
    reasons: list[str] = []
    phases = list(gate_report.get("phases", []))
    groups = list(group_report.get("groups", []))
    expected_phases = list(expected_phase_ids)
    expected_groups = list(expected_group_ids)

    if gate_report.get("complete") is not True:
        reasons.append("gate report is incomplete")
    if group_report.get("complete") is not True:
        reasons.append("pytest group report is incomplete")
    if not _exactly_once(
        (str(row.get("phase_id")) for row in phases), expected_phases
    ):
        reasons.append("configured phase set mismatch (absent or launched twice)")
    if not _exactly_once(
        (str(row.get("group_id")) for row in groups), expected_groups
    ):
        reasons.append("configured group set mismatch (absent or launched twice)")
    if any(row.get("status") == "cancelled" for row in phases):
        reasons.append("phase was cancelled")
    if any(row.get("cancelled") is True for row in groups):
        reasons.append("pytest group was cancelled")

    execution_ids = [str(row.get("group_id")) for row in execution_reports]
    if not _exactly_once(execution_ids, expected_groups):
        reasons.append("pytest execution report set mismatch")
    for report in execution_reports:
        group_id = str(report.get("group_id"))
        if report.get("complete") is not True:
            reasons.append(f"{group_id} execution report is incomplete")
        if report.get("cancelled") is True:
            reasons.append(f"{group_id} was cancelled")
        collection = report.get("collection")
        if not isinstance(collection, Mapping) or collection.get("finished") is not True:
            reasons.append(f"{group_id} lacks collection events")
        elif not isinstance(collection.get("selected_nodeids"), list) or not isinstance(
            collection.get("deselected_nodeids"), list
        ):
            reasons.append(f"{group_id} lacks collection events")

    structural_reasons = list(reasons)
    if structural_reasons:
        return {
            "complete": False,
            "acceptance_usable": False,
            "classification": "rejected",
            "reasons": structural_reasons,
        }

    ordinary_failure = gate_returncode != 0 or any(
        row.get("returncode") not in (0, None) for row in phases
    ) or any(row.get("returncode") != 0 for row in groups)
    if ordinary_failure:
        reasons.append("complete run contains ordinary failures")
    for capability, probe in capabilities.items():
        if probe.get("required") is True and probe.get("available") is not True:
            reasons.append(f"required capability unavailable: {capability}")

    acceptance = not reasons
    return {
        "complete": True,
        "acceptance_usable": acceptance,
        "classification": "acceptance" if acceptance else "diagnostic",
        "reasons": reasons,
    }


def attribute_window(
    samples: list[Mapping[str, float]], *, start: float, wall_seconds: float
) -> dict[str, float | int | None]:
    """Attribute sampled CPU, concurrency, and RSS to one serial time window.

    Intent
    ------
    Aggregate process-tree samples falling within a component's wall-time interval.

    Rationale
    ---------
    The gate runs components serially, permitting sampled total resources to be
    assigned by their elapsed-time windows without launching extra measurements.

    Pseudocode
    ----------
    - set selected = samples whose elapsed time lies in the component window
    - set cpu = sum of selected sampled CPU seconds
    - return CPU average-core peak-core peak-RSS and sample-count metrics

    Wraps
    -----
    - none
    """
    end = start + wall_seconds
    selected = [
        sample
        for sample in samples
        if start <= float(sample["elapsed_seconds"]) <= end
    ]
    cpu = sum(float(sample["cpu_seconds"]) for sample in selected)
    return {
        "sampled_cpu_seconds": cpu,
        "average_effective_cores": cpu / wall_seconds if wall_seconds else 0.0,
        "peak_effective_cores": max(
            (float(sample["effective_cores"]) for sample in selected), default=0.0
        ),
        "peak_sampled_tree_rss_kb": max(
            (float(sample["rss_kb"]) for sample in selected), default=0.0
        ),
        "sample_count": len(selected),
    }


def select_material_rows(
    rows: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Select the eighty-percent critical path plus CPU and RSS outliers.

    Intent
    ------
    Retain components that dominate serial wall time or exceed resource thresholds.

    Rationale
    ---------
    A bounded material set focuses remediation without hiding expensive parallel or
    memory-heavy components whose wall-time share alone is modest.

    Pseudocode
    ----------
    - set ordered = rows sorted by descending wall time
    - set selected_ids = rows covering eighty percent of serial wall time
    - for row in ordered:
      - if row exceeds the CPU or RSS threshold:
        - set selected_ids = selected_ids plus row id
    - return ordered rows whose ids are selected

    Wraps
    -----
    - none
    """
    ordered = sorted(rows, key=lambda row: float(row["wall_seconds"]), reverse=True)
    total = sum(float(row["wall_seconds"]) for row in ordered)
    threshold = total * 0.8
    cumulative = 0.0
    selected_ids: set[str] = set()
    for row in ordered:
        if cumulative < threshold:
            selected_ids.add(str(row["id"]))
            cumulative += float(row["wall_seconds"])
    for row in ordered:
        if (
            float(row.get("average_effective_cores") or 0) > 1.0
            or float(row.get("peak_sampled_tree_rss_kb") or 0) > 500 * 1024
        ):
            selected_ids.add(str(row["id"]))
    return [row for row in ordered if str(row["id"]) in selected_ids]


def classify_optimization(
    *,
    component_wall_seconds: float,
    complete_warm_wall_seconds: float,
    component_cpu_seconds: float,
    measured: bool,
) -> str:
    """Classify optimization priority using wall-share and CPU-cost thresholds.

    Intent
    ------
    Apply the audit's five-percent-wall or five-CPU-second decision rule.

    Rationale
    ---------
    The checkpoint prevents optimization effort on components without measured cost.

    Pseudocode
    ----------
    - if component cost is unmeasured:
      - return unmeasured
    - set wall_share = component wall time divided by complete warm wall time
    - return proceed when either threshold is met else defer

    Wraps
    -----
    - none
    """
    if not measured:
        return "unmeasured"
    wall_share = (
        component_wall_seconds / complete_warm_wall_seconds
        if complete_warm_wall_seconds
        else 0.0
    )
    return "proceed" if wall_share >= 0.05 or component_cpu_seconds >= 5.0 else "defer"


def _load_script(path: Path, module_name: str):
    """Load a repository script whose filename is not import-safe.

    Intent
    ------
    Materialize a named Python module from an explicit script path.

    Rationale
    ---------
    Hyphenated repository scripts cannot be imported through ordinary module syntax.

    Pseudocode
    ----------
    - set spec = import specification for path and module_name
    - if spec or its loader is absent:
      - raise script-loading error
    - set module = module materialized from spec
    - set module_state = module executed through its loader
    - return module

    Wraps
    -----
    - none
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _expected_group_ids(repo_root: Path) -> list[str]:
    """Derive expected group IDs from canonical test-runner discovery.

    Intent
    ------
    Compute the exact precommit group count without duplicating suite policy.

    Rationale
    ---------
    Benchmark completeness must track the runner's current grouping implementation.

    Pseudocode
    ----------
    - set runner = loaded repository Python-test runner
    - set groups = runner partitions for the precommit suite
    - return sequential group identifiers for groups

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._load_script:
      why:
        constructs: "Builds the runner module used to discover canonical groups."
    """
    runner = _load_script(repo_root / "scripts/run-python-tests.py", "_benchmark_runner")
    groups = runner._execution_groups(runner._resolve_suite("precommit"))
    return [f"group-{index}" for index in range(1, len(groups) + 1)]


def _run_text(command: list[str], *, cwd: Path, timeout: float = 30.0) -> tuple[bool, str]:
    """Run a bounded probe and return availability with diagnostic text.

    Intent
    ------
    Execute one metadata or capability command with combined captured output.

    Rationale
    ---------
    Probe failure is benchmark evidence rather than an exception that aborts collection.

    Pseudocode
    ----------
    - set completed = bounded command result with combined text output
    - if execution fails or times out:
      - return false and exception text
    - return command success and output or numeric-exit explanation

    Wraps
    -----
    - none
    """
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = completed.stdout.strip()
    return completed.returncode == 0, output or f"exit {completed.returncode}"


def _capability_probes(repo_root: Path) -> dict[str, dict[str, object]]:
    """Probe capabilities whose absence invalidates acceptance timing.

    Intent
    ------
    Record browser, network, package-tool, and installer availability with reasons.

    Rationale
    ---------
    Missing dependencies can shorten a failed run and must not be averaged as passes.

    Pseudocode
    ----------
    - set chrome_probe = bounded headless-browser result or missing reason
    - set uv_probe = bounded package-tool version result
    - set network_probe = bounded package-index request result
    - return required availability records plus excluded-installer status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_text:
      why:
        constructs: "Builds bounded Chrome and package-tool capability results."
    """
    chrome = next(
        (
            candidate
            for name in ("google-chrome", "chromium", "chromium-browser")
            if (candidate := shutil.which(name)) is not None
        ),
        None,
    )
    if chrome is None:
        chrome_ok, chrome_reason = False, "Chrome executable not found"
    else:
        chrome_ok, chrome_reason = _run_text(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--dump-dom",
                "data:text/html,<p>capability-probe</p>",
            ],
            cwd=repo_root,
        )
    uv_ok, uv_reason = _run_text(["uv", "--version"], cwd=repo_root)
    try:
        with urllib.request.urlopen("https://pypi.org/simple/", timeout=5) as response:
            network_ok = response.status < 400
            network_reason = f"HTTP {response.status}"
    except Exception as exc:  # capability result, not benchmark infrastructure
        network_ok, network_reason = False, str(exc)
    return {
        "chrome": {"required": True, "available": chrome_ok, "reason": chrome_reason},
        "network": {"required": True, "available": network_ok, "reason": network_reason},
        "uv": {"required": True, "available": uv_ok, "reason": uv_reason},
        "installer": {
            "required": False,
            "available": False,
            "reason": "installer groups are excluded from precommit",
        },
    }


def _tool_versions(repo_root: Path) -> dict[str, str | None]:
    """Record versions of tools that can alter timing or capability.

    Intent
    ------
    Capture available version text for Python, pytest, Git, gitleaks, and uv.

    Rationale
    ---------
    Benchmark comparisons require enough environment identity to detect tool drift.

    Pseudocode
    ----------
    - set commands = benchmark-relevant version probes
    - for probe in commands:
      - set versions = versions plus successful probe output or none
    - return versions

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._run_text:
      why:
        constructs: "Builds each bounded tool-version probe result."
    """
    commands = {
        "python": [sys.executable, "--version"],
        "pytest": [sys.executable, "-m", "pytest", "--version"],
        "git": ["git", "--version"],
        "gitleaks": ["gitleaks", "version"],
        "uv": ["uv", "--version"],
    }
    versions: dict[str, str | None] = {}
    for name, command in commands.items():
        ok, output = _run_text(command, cwd=repo_root)
        versions[name] = output if ok else None
    return versions


def _git_output(repo_root: Path, *args: str) -> bytes:
    """Read exact Git metadata or fail when a run cannot be pinned.

    Intent
    ------
    Execute a Git query in the measured repository and return its exact bytes.

    Rationale
    ---------
    Benchmark provenance and staged fingerprints must not normalize binary Git data.

    Pseudocode
    ----------
    - set output = exact bytes from Git with args in repo_root
    - return output

    Wraps
    -----
    - none
    """
    return subprocess.check_output(["git", *args], cwd=repo_root)


def _staged_fingerprint(repo_root: Path) -> str:
    """Hash the exact staged binary diff consumed by the gate.

    Intent
    ------
    Produce a stable SHA-256 identity for the measured staged state.

    Rationale
    ---------
    Before-and-after equality proves generators did not silently change benchmark scope.

    Pseudocode
    ----------
    - set staged = exact staged binary diff bytes
    - return SHA-256 hexadecimal digest of staged

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._git_output:
      why:
        constructs: "Builds the exact staged binary bytes supplied to the hash."
    """
    staged = _git_output(repo_root, "diff", "--cached", "--binary", "--no-ext-diff")
    return hashlib.sha256(staged).hexdigest()


def _host_metadata() -> dict[str, object]:
    """Describe the benchmark host without optional dependencies.

    Intent
    ------
    Record hostname, platform, processor model, and logical CPU count.

    Rationale
    ---------
    Host capacity materially affects elapsed time and parallel computation cost.

    Pseudocode
    ----------
    - set cpu_model = platform processor description
    - if Linux CPU information is readable:
      - set cpu_model = first reported model name
    - return host identity and capacity metadata

    Wraps
    -----
    - none
    """
    cpu_model = platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "cpu": cpu_model,
        "logical_cpus": os.cpu_count(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    """Load a required child report or return an incomplete sentinel.

    Intent
    ------
    Decode one JSON report while preserving read and parse failure as data.

    Rationale
    ---------
    Missing or malformed child evidence must reject a run without hiding other results.

    Pseudocode
    ----------
    - set report = JSON decoded from path
    - if reading or decoding fails:
      - return incomplete report with load error
    - return report

    Wraps
    -----
    - none
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"complete": False, "load_error": str(exc)}


def _execution_reports(paths: list[Path], group_ids: list[str]) -> list[dict[str, Any]]:
    """Associate sequential pytest execution reports with group IDs.

    Intent
    ------
    Load reports in start order and label them against expected execution groups.

    Rationale
    ---------
    PID filenames do not encode group identity, but groups launch serially by design.

    Pseudocode
    ----------
    - set loaded = JSON report for each path sorted by start timestamp
    - for report in loaded:
      - set report_group_id = expected id or unexpected sentinel
    - return loaded

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._load_json:
      why:
        constructs: "Builds each decoded execution report before group attribution."
    """
    loaded = [_load_json(path) for path in paths]
    loaded.sort(key=lambda row: int(row.get("started_at_ns", 0)))
    for index, report in enumerate(loaded):
        report["group_id"] = (
            group_ids[index] if index < len(group_ids) else f"unexpected-{index + 1}"
        )
    return loaded


def _rows_with_resources(
    gate_report: Mapping[str, Any],
    group_report: Mapping[str, Any],
    samples: list[Mapping[str, float]],
    executions: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one sampled-resource row per gate phase and pytest group.

    Intent
    ------
    Map serial phase and group durations onto process-tree sample windows.

    Rationale
    ---------
    Unified rows make wall time, CPU, concurrency, memory, and pytest evidence comparable.

    Pseudocode
    ----------
    - set phase_rows = phases enriched from consecutive sample windows
    - set group_start = Python phase start adjusted for group wall totals
    - set group_rows = groups enriched from consecutive sample windows and execution reports
    - return phase_rows and group_rows

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .attribute_window:
      why:
        constructs: "Builds sampled resource metrics for each phase and group row."
    """
    phase_rows: list[dict[str, Any]] = []
    phase_start = 0.0
    python_start = 0.0
    python_wall = 0.0
    for phase in gate_report.get("phases", []):
        wall = float(phase.get("wall_seconds", 0.0))
        row = dict(phase)
        row["id"] = f"phase:{phase.get('phase_id')}"
        row["start_seconds"] = phase_start
        row.update(attribute_window(samples, start=phase_start, wall_seconds=wall))
        phase_rows.append(row)
        if phase.get("phase_id") == "python-tests":
            python_start, python_wall = phase_start, wall
        phase_start += wall

    groups = list(group_report.get("groups", []))
    group_walls = sum(float(row.get("wall_seconds", 0.0)) for row in groups)
    group_start = python_start + max(0.0, python_wall - group_walls)
    execution_by_id = {str(row.get("group_id")): row for row in executions}
    group_rows: list[dict[str, Any]] = []
    for group in groups:
        wall = float(group.get("wall_seconds", 0.0))
        group_id = str(group.get("group_id"))
        row = dict(group)
        row["id"] = f"group:{group_id}"
        row["start_seconds"] = group_start
        row.update(attribute_window(samples, start=group_start, wall_seconds=wall))
        row["pytest_execution"] = execution_by_id.get(group_id)
        group_rows.append(row)
        group_start += wall
    return phase_rows, group_rows


def _clear_run_artifacts(repo_root: Path, execution_dir: Path) -> None:
    """Remove only benchmark-owned artifacts before one measured run.

    Intent
    ------
    Recreate the execution-report directory and delete known gate report files.

    Rationale
    ---------
    Stale reports could make an interrupted run appear structurally complete.

    Pseudocode
    ----------
    - set execution_dir = newly recreated empty directory
    - for path in benchmark-owned gate reports:
      - set path_state = absent
    - return

    Wraps
    -----
    - none
    """
    shutil.rmtree(execution_dir, ignore_errors=True)
    execution_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        repo_root / "_build/precommit-gate-report.json",
        repo_root / "_build/precommit-gate-report-python-groups.json",
    ):
        path.unlink(missing_ok=True)


def run_benchmarks(
    *, repo_root: Path, output: Path, runs: int, cache: str
) -> dict[str, object]:
    """Run and classify complete gates under one cache condition.

    Intent
    ------
    Produce provenance-rich phase and group measurements for repeated exhaustive gates.

    Rationale
    ---------
    Controlled cache state, capability evidence, staged fingerprints, and structural
    assessment prevent incomplete or incomparable runs from becoming acceptance data.

    Pseudocode
    ----------
    - if benchmark prerequisites are invalid:
      - raise benchmark configuration error
    - set benchmark_context = groups capabilities caches and artifacts
    - for run_index in requested repetitions:
      - set run_context = clean artifacts caches and live reporting
      - set metrics = measured exhaustive gate command
      - set reports = gate group and pytest execution evidence
      - set assessment = structural and acceptance classification
      - set measured_runs = measured_runs plus enriched phase and group rows
    - set result = provenance capabilities measurements and material rows
    - set benchmark_output = persisted formatted benchmark result
    - return benchmark_result

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._clear_run_artifacts:
      why:
        writes: "Clears only benchmark-owned evidence before each repetition."
    .select_material_rows:
      why:
        computes: "Selects the bounded hotspot set from complete measured rows."
    ._git_output:
      why:
        reads: "Reads the exact commit identity stored with the result."

    InstantiationsFromRepo
    ----------------------
    ._capability_probes:
      why:
        constructs: "Builds acceptance-critical capability evidence."
    ._execution_reports:
      why:
        constructs: "Builds group-attributed live pytest execution evidence."
    ._expected_group_ids:
      why:
        constructs: "Builds the canonical group identifiers required for completeness."
    ._host_metadata:
      why:
        constructs: "Builds the host identity and capacity record."
    ._load_json:
      why:
        constructs: "Builds decoded gate and group reports with incomplete sentinels."
    ._load_script:
      why:
        constructs: "Builds the command benchmark module used for measurement."
    ._rows_with_resources:
      why:
        constructs: "Builds resource-enriched phase and group result rows."
    ._staged_fingerprint:
      why:
        constructs: "Builds before-and-after staged-state identities."
    ._tool_versions:
      why:
        constructs: "Builds the benchmark tool-version provenance record."
    .assess_run:
      why:
        constructs: "Builds each run's structural and acceptance classification."
    """
    if runs < 1:
        raise ValueError("runs must be positive")
    repo_root = repo_root.resolve()
    if not (repo_root / "scripts/run-precommit-gate.py").is_file():
        raise RuntimeError(f"complete gate is missing from repository: {repo_root}")
    expected_groups = _expected_group_ids(repo_root)
    capabilities = _capability_probes(repo_root)
    benchmark = _load_script(
        repo_root / "scripts/benchmark-command.py", "_benchmark_command"
    )
    cache_root = repo_root / "_build/test-performance-cache"
    artifact_root = output.parent / f"{output.stem}-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    if cache == "cold":
        shutil.rmtree(cache_root, ignore_errors=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    measured_runs: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        if cache == "cold":
            shutil.rmtree(cache_root, ignore_errors=True)
            cache_root.mkdir(parents=True, exist_ok=True)
        execution_dir = artifact_root / f"run-{index}-pytest"
        _clear_run_artifacts(repo_root, execution_dir)
        before_fingerprint = _staged_fingerprint(repo_root)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get(
            "PYTHONPATH", ""
        )
        env["PYTEST_PLUGINS"] = "officina.common.pytest_execution_report"
        env["OFFICINA_PYTEST_EXECUTION_REPORT"] = str(
            execution_dir / "pytest-{pid}.json"
        )
        env["PYTHONPYCACHEPREFIX"] = str(cache_root / "pycache")
        env["XDG_CACHE_HOME"] = str(cache_root / "xdg")
        env["UV_CACHE_DIR"] = str(cache_root / "uv")
        metrics = benchmark.benchmark_command(
            [sys.executable, "scripts/run-precommit-gate.py"],
            log_path=artifact_root / f"run-{index}.log",
            cwd=repo_root,
            env=env,
            record_samples=True,
        )
        gate_report = _load_json(repo_root / "_build/precommit-gate-report.json")
        group_report = _load_json(
            repo_root / "_build/precommit-gate-report-python-groups.json"
        )
        executions = _execution_reports(
            list(execution_dir.glob("pytest-*.json")),
            [str(row.get("group_id")) for row in group_report.get("groups", [])],
        )
        assessment = assess_run(
            gate_report=gate_report,
            group_report=group_report,
            execution_reports=executions,
            expected_phase_ids=EXPECTED_PHASE_IDS,
            expected_group_ids=expected_groups,
            capabilities=capabilities,
            gate_returncode=int(metrics["returncode"]),
        )
        samples = list(metrics.pop("process_tree_samples") or [])
        phase_rows, group_rows = _rows_with_resources(
            gate_report, group_report, samples, executions
        )
        measured_runs.append(
            {
                "run": index,
                "cache": cache,
                "staged_fingerprint_before": before_fingerprint,
                "staged_fingerprint_after": _staged_fingerprint(repo_root),
                "assessment": assessment,
                "total": {**metrics, "sample_count": len(samples)},
                "phases": phase_rows,
                "groups": group_rows,
            }
        )

    all_rows = [
        row
        for run in measured_runs
        if run["assessment"]["complete"]
        for row in [*run["phases"], *run["groups"]]
    ]
    result: dict[str, object] = {
        "schema_version": 1,
        "repo": str(repo_root),
        "commit": _git_output(repo_root, "rev-parse", "HEAD").decode().strip(),
        "host": _host_metadata(),
        "tool_versions": _tool_versions(repo_root),
        "capabilities": capabilities,
        "cache_condition": cache,
        "runs_requested": runs,
        "expected_phase_ids": list(EXPECTED_PHASE_IDS),
        "expected_group_ids": expected_groups,
        "runs": measured_runs,
        "material_rows": select_material_rows(all_rows) if all_rows else [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the stable benchmark command-line contract.

    Intent
    ------
    Require repository, output, repetition count, and cache-condition arguments.

    Rationale
    ---------
    Explicit inputs keep benchmark scope reproducible in reports and audit commands.

    Pseudocode
    ----------
    - set parser = benchmark command-line argument parser
    - return parsed arguments from argv

    Wraps
    -----
    - none
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--cache", required=True, choices=("cold", "warm"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write all measurements and reject structurally incomplete runs.

    Intent
    ------
    Execute the configured benchmark and map rejected assessments to process failure.

    Rationale
    ---------
    Diagnostic ordinary failures remain reportable, while incomplete evidence must
    be unmistakably unsuccessful to automation.

    Pseudocode
    ----------
    - set args = parsed benchmark arguments
    - set result = repeated benchmark measurements
    - return failure when any run is rejected else success

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .parse_args:
      why:
        constructs: "Builds the validated command-line configuration."
    .run_benchmarks:
      why:
        constructs: "Builds the benchmark result used to derive process status."
    """
    args = parse_args(argv)
    result = run_benchmarks(
        repo_root=args.repo, output=args.output, runs=args.runs, cache=args.cache
    )
    return 1 if any(
        run["assessment"]["classification"] == "rejected"
        for run in result["runs"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
