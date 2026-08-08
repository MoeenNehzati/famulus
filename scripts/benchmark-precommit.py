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
    """Measure repeated centralized precommit-suite executions."""
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

    measurements: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        if cache == "cold":
            shutil.rmtree(cache_root, ignore_errors=True)
        cache_root.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = str(cache_root / "pycache")
        environment["XDG_CACHE_HOME"] = str(cache_root / "xdg")
        environment["UV_CACHE_DIR"] = str(cache_root / "uv")
        before = _staged_fingerprint(repo_root)
        metrics = benchmark.benchmark_command(
            command,
            log_path=artifact_root / f"run-{index}.log",
            cwd=repo_root,
            env=environment,
            record_samples=measure_resources,
            sample_process_tree=measure_resources,
        )
        after = _staged_fingerprint(repo_root)
        measurements.append(
            {
                "run": index,
                "classification": (
                    "acceptance" if int(metrics["returncode"]) == 0 else "diagnostic"
                ),
                "staged_fingerprint_before": before,
                "staged_fingerprint_after": after,
                "staged_state_changed": before != after,
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
