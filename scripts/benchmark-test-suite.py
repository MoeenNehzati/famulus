#!/usr/bin/env python3
"""Benchmark a named repository check suite or one canonical check task."""

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
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


def default_jobs() -> int:
    """Return two thirds of logical CPUs, with a minimum of one."""
    return max(1, (2 * (os.cpu_count() or 1)) // 3)


def _load_script(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _cache_environment(cache_root: Path) -> dict[str, str]:
    cache_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPYCACHEPREFIX": str(cache_root / "pycache"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "UV_CACHE_DIR": str(cache_root / "uv"),
        }
    )
    return environment


def _git_output(repo_root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo_root)


def _staged_fingerprint(repo_root: Path) -> str:
    return hashlib.sha256(_git_output(repo_root, "diff", "--cached", "--binary")).hexdigest()


def _tracked_worktree_fingerprint(repo_root: Path) -> str:
    return hashlib.sha256(_git_output(repo_root, "diff", "--binary")).hexdigest()


def resolve_benchmark_command(
    repo_root: Path,
    suite: str,
    jobs: int,
    task_id: str | None,
    task_cache_dir: Path | None = None,
) -> tuple[list[str], int | None]:
    """Build a runner CLI command rooted in the selected repository."""
    root = Path(repo_root).resolve()
    command = [
        sys.executable,
        str(root / "repo_checks.py"),
        "--suite",
        suite,
        "--jobs",
        str(jobs),
    ]
    if task_id is None:
        return command, None
    command.extend(["--task-id", task_id])
    if task_cache_dir is not None:
        command.extend(["--task-cache-dir", str(task_cache_dir.resolve())])
    return command, jobs if task_id == "tests:shared" else 1


def run_benchmarks(
    repo_root: Path,
    suite: str,
    output: Path,
    runs: int,
    cache: str,
    jobs: int,
    task_id: str | None,
    prime: bool = True,
    sequential: bool = False,
    measure_resources: bool = False,
) -> dict[str, object]:
    """Run cache-controlled suite or task observations and write a JSON artifact."""
    if runs < 1:
        raise ValueError("runs must be positive")
    if jobs < 1:
        raise ValueError("jobs must be positive")
    root = Path(repo_root).resolve()
    benchmark = _load_script(root / "scripts" / "benchmark-command.py", "_benchmark_command")
    cache_root = root / "_build" / "test-performance-cache"
    artifact_root = output.parent / f"{output.stem}-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    task_cache_root = artifact_root / "task-cache"
    task_cache_paths: list[dict[str, str]] = []
    task_slots: int | None = None

    def command_for(phase: str) -> list[str]:
        nonlocal task_slots
        if task_id is None:
            command, task_slots = resolve_benchmark_command(root, suite, jobs, None)
            if sequential:
                command.append("--sequential")
            return command
        task_cache_root.mkdir(parents=True, exist_ok=True)
        if cache == "warm":
            cache_dir = task_cache_root / "warm"
            cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            cache_dir = Path(
                tempfile.mkdtemp(prefix=f"{phase}-", dir=task_cache_root)
            )
        command, resolved_slots = resolve_benchmark_command(
            root, suite, jobs, task_id, cache_dir
        )
        task_slots = resolved_slots
        task_cache_paths.append({"phase": phase, "path": str(cache_dir.resolve())})
        return command
    resolved_jobs = default_jobs()
    environment = _cache_environment(cache_root)
    if task_id is not None:
        environment["OFFICINA_FIXTURE_PROBE_TASK_ID"] = task_id
    if cache == "warm" and prime:
        command = command_for("prime")
        staged_before = _staged_fingerprint(root)
        tracked_before = _tracked_worktree_fingerprint(root)
        warmup = subprocess.run(command, cwd=root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=False)
        if warmup.returncode:
            raise RuntimeError(f"warm-cache prime failed with exit code {warmup.returncode}")
        if (_staged_fingerprint(root), _tracked_worktree_fingerprint(root)) != (staged_before, tracked_before):
            raise RuntimeError("warm-cache prime changed repository state")
    measurements: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        if cache == "cold":
            shutil.rmtree(cache_root, ignore_errors=True)
        staged_before = _staged_fingerprint(root)
        tracked_before = _tracked_worktree_fingerprint(root)
        command = command_for(f"run-{index}")
        metrics = benchmark.benchmark_command(command, log_path=artifact_root / f"run-{index}.log", cwd=root, env=_cache_environment(cache_root) | ({"OFFICINA_FIXTURE_PROBE_TASK_ID": task_id} if task_id else {}), record_samples=measure_resources, sample_process_tree=measure_resources)
        staged_after = _staged_fingerprint(root)
        tracked_after = _tracked_worktree_fingerprint(root)
        measurements.append({"run": index, "classification": "acceptance" if int(metrics["returncode"]) == 0 else "diagnostic", "staged_fingerprint_before": staged_before, "staged_fingerprint_after": staged_after, "staged_state_changed": staged_before != staged_after, "tracked_worktree_fingerprint_before": tracked_before, "tracked_worktree_fingerprint_after": tracked_after, "tracked_worktree_changed": tracked_before != tracked_after, "metrics": metrics})
    result: dict[str, object] = {
        "schema_version": 3, "repo": str(root), "commit": _git_output(root, "rev-parse", "HEAD").decode().strip(),
        "host": {"platform": platform.platform(), "python": platform.python_version(), "logical_cpus": os.cpu_count()},
        "cache_condition": cache, "jobs": jobs, "requested_jobs": jobs, "resolved_default_jobs": resolved_jobs,
        "suite": suite, "task_id": task_id, "task_slots": task_slots, "task_cache_paths": task_cache_paths,
        "prime": prime, "runs_requested": runs, "command": command, "runs": measurements,
        "scheduler": "phased",
        "sequential_alias": sequential,
        "measurement_mode": "resources" if measure_resources else "timing",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--suite", default="full", choices=("precommit", "pre-push", "full"))
    parser.add_argument("--task-id")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--cache", required=True, choices=("cold", "warm"))
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--no-prime", action="store_true")
    parser.add_argument("--sequential", action="store_true")
    parser.add_argument("--measure-resources", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_benchmarks(args.repo, args.suite, args.output, args.runs, args.cache, args.jobs, args.task_id, prime=not args.no_prime, sequential=args.sequential, measure_resources=args.measure_resources)
    return 1 if any(run["classification"] == "diagnostic" for run in result["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
