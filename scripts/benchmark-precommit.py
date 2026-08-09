#!/usr/bin/env python3
"""Benchmark the centralized repository precommit suite."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def default_jobs() -> int:
    """Return two-thirds of available logical CPUs, with a minimum of one."""
    return max(1, (2 * (os.cpu_count() or 1)) // 3)


def _load_script(path: Path, module_name: str) -> ModuleType:
    """Load a repository script so its measurement API can be reused."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _git_output(repo_root: Path, *args: str) -> bytes:
    """Return output from a read-only Git query."""
    return subprocess.check_output(["git", *args], cwd=repo_root)


def _staged_fingerprint(repo_root: Path) -> str:
    """Hash staged content so measurements expose hook-input drift."""
    staged = _git_output(repo_root, "diff", "--cached", "--binary")
    return hashlib.sha256(staged).hexdigest()


def _tracked_worktree_fingerprint(repo_root: Path) -> str:
    """Hash unstaged tracked content used by one benchmark observation.

    Intent
    ------
    Detect working-tree input drift around cache priming and measured executions.

    Rationale
    ---------
    A stable commit and staged index do not prove that ordinary tests saw identical
    tracked working-tree content.

    Pseudocode
    ----------
    - set tracked_diff = binary unstaged tracked diff
    - set fingerprint = SHA-256 hash of tracked_diff
    - return fingerprint

    Wraps
    -----
    ._git_output -> preprocess: request the binary tracked diff; postprocess: hash returned bytes; fixed_arguments: diff and --binary
    """
    tracked = _git_output(repo_root, "diff", "--binary")
    return hashlib.sha256(tracked).hexdigest()


def _cache_environment(cache_root: Path) -> dict[str, str]:
    """Build one explicit cache environment for prime and measured runs.

    Intent
    ------
    Point Python, XDG, and uv caches at one benchmark-owned root.

    Rationale
    ---------
    Warm-cache comparisons require the untimed prime and every observation to use
    exactly the same cache locations.

    Pseudocode
    ----------
    - set cache_root = existing benchmark cache directory
    - set environment = current process environment
    - set environment = environment with benchmark-owned Python XDG and uv cache paths
    - return environment

    Wraps
    -----
    - none
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPYCACHEPREFIX"] = str(cache_root / "pycache")
    environment["XDG_CACHE_HOME"] = str(cache_root / "xdg")
    environment["UV_CACHE_DIR"] = str(cache_root / "uv")
    return environment


def _host_metadata() -> dict[str, object]:
    """Return stable host facts relevant to performance comparisons."""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
    }


def run_benchmarks(
    *,
    repo_root: Path,
    output: Path,
    runs: int,
    cache: str,
    jobs: int,
    sequential: bool = False,
    measure_resources: bool = False,
) -> dict[str, object]:
    """Measure repeated centralized precommit-suite executions.

    Intent
    ------
    Produce reproducible scheduler observations without allowing repository drift to
    masquerade as a performance change.

    Rationale
    ---------
    Paired comparisons require identical commands, cache conditions, repository
    inputs, and measurement settings for every recorded run.

    Pseudocode
    ----------
    - set command = centralized precommit runner with scheduler and worker options
    - if cache condition is warm:
      - set prime_result = untimed command execution with benchmark cache paths
      - if prime_result failed or changed repository state:
        - raise invalid benchmark error
    - for run in requested runs:
      - set repository_before = staged and tracked fingerprints
      - set metrics = measured command execution
      - set repository_after = staged and tracked fingerprints
      - set measurements = measurements plus metrics and drift indicators
    - set benchmark_record = benchmark metadata and measurements
    - set output_artifact = benchmark_record serialized at output path
    - return benchmark_record

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._cache_environment:
      why:
        computes: "Supplies identical cache paths to prime and measured commands."
    ._git_output:
      why:
        computes: "Supplies the exact commit recorded in benchmark metadata."

    InstantiationsFromRepo
    ----------------------
    ._load_script:
      why:
        constructs: "Builds the reusable command-measurement module."
    ._cache_environment:
      why:
        constructs: "Builds each benchmark subprocess environment."
    ._staged_fingerprint:
      why:
        constructs: "Builds staged-state identities around each execution."
    ._tracked_worktree_fingerprint:
      why:
        constructs: "Builds tracked-working-tree identities around each execution."
    ._host_metadata:
      why:
        constructs: "Builds host facts needed to interpret measurements."
    """
    if runs < 1:
        raise ValueError("runs must be positive")
    if jobs < 1:
        raise ValueError("jobs must be positive")

    repo_root = repo_root.resolve()
    entrypoint = repo_root / "repo_checks.py"
    benchmark_path = repo_root / "scripts" / "benchmark-command.py"
    if not entrypoint.is_file():
        raise RuntimeError(f"centralized check runner is missing: {entrypoint}")
    if not benchmark_path.is_file():
        raise RuntimeError(f"benchmark helper is missing: {benchmark_path}")

    benchmark = _load_script(benchmark_path, "_benchmark_command")
    cache_root = repo_root / "_build" / "test-performance-cache"
    artifact_root = output.parent / f"{output.stem}-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(entrypoint),
        "--suite",
        "precommit",
        "--jobs",
        str(jobs),
    ]
    if sequential:
        command.append("--sequential")

    if cache == "warm":
        staged_before_prime = _staged_fingerprint(repo_root)
        tracked_before_prime = _tracked_worktree_fingerprint(repo_root)
        warmup = subprocess.run(
            command,
            cwd=repo_root,
            env=_cache_environment(cache_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if warmup.returncode:
            raise RuntimeError(
                f"warm-cache prime failed with exit code {warmup.returncode}"
            )
        staged_after_prime = _staged_fingerprint(repo_root)
        tracked_after_prime = _tracked_worktree_fingerprint(repo_root)
        if (
            staged_before_prime != staged_after_prime
            or tracked_before_prime != tracked_after_prime
        ):
            raise RuntimeError("warm-cache prime changed repository state")

    measurements: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        if cache == "cold":
            shutil.rmtree(cache_root, ignore_errors=True)
        environment = _cache_environment(cache_root)
        staged_before = _staged_fingerprint(repo_root)
        tracked_before = _tracked_worktree_fingerprint(repo_root)
        metrics = benchmark.benchmark_command(
            command,
            log_path=artifact_root / f"run-{index}.log",
            cwd=repo_root,
            env=environment,
            record_samples=measure_resources,
            sample_process_tree=measure_resources,
        )
        staged_after = _staged_fingerprint(repo_root)
        tracked_after = _tracked_worktree_fingerprint(repo_root)
        measurements.append(
            {
                "run": index,
                "classification": (
                    "acceptance" if int(metrics["returncode"]) == 0 else "diagnostic"
                ),
                "staged_fingerprint_before": staged_before,
                "staged_fingerprint_after": staged_after,
                "staged_state_changed": staged_before != staged_after,
                "tracked_worktree_fingerprint_before": tracked_before,
                "tracked_worktree_fingerprint_after": tracked_after,
                "tracked_worktree_changed": tracked_before != tracked_after,
                "metrics": metrics,
            }
        )

    result: dict[str, object] = {
        "schema_version": 2,
        "repo": str(repo_root),
        "commit": _git_output(repo_root, "rev-parse", "HEAD").decode().strip(),
        "host": _host_metadata(),
        "cache_condition": cache,
        "jobs": jobs,
        "scheduler": "sequential" if sequential else "pooled",
        "measurement_mode": "resources" if measure_resources else "timing",
        "runs_requested": runs,
        "command": command,
        "runs": measurements,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the benchmark command-line contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--cache", required=True, choices=("cold", "warm"))
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--sequential", action="store_true")
    parser.add_argument("--measure-resources", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run benchmarks and fail when any measured check execution fails."""
    args = parse_args(argv)
    result = run_benchmarks(
        repo_root=args.repo,
        output=args.output,
        runs=args.runs,
        cache=args.cache,
        jobs=args.jobs,
        sequential=args.sequential,
        measure_resources=args.measure_resources,
    )
    return 1 if any(
        run["classification"] == "diagnostic" for run in result["runs"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
