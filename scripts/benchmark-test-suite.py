#!/usr/bin/env python3
"""Benchmark canonical repository-check suites without duplicating runner policy.

The selected checkout's ``repo_checks.py`` remains authoritative for discovery,
repository views, phase selection, and pytest arguments. This harness controls
cache conditions, captures immutable repository fingerprints around each run,
and adds OS-accounting and optional diagnostic process-tree evidence.
"""

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
    """Return the benchmark harness's default worker request.

    Intent
    ------
    Match the repository runner's host-capacity policy without importing it.

    Rationale
    ---------
    The harness must remain usable against another checkout while reporting the
    worker request it selected on the current host.

    Pseudocode
    ----------
    - set logical_cpus = detected logical CPUs or one
    - return two thirds of logical_cpus with minimum one

    Wraps
    -----
    - none
    """
    return max(1, (2 * (os.cpu_count() or 1)) // 3)


def _load_script(path: Path, module_name: str) -> ModuleType:
    """Load one selected-checkout benchmark helper under a private module name.

    Intent
    ------
    Reuse the measured checkout's command-accounting implementation.

    Rationale
    ---------
    Loading by exact path avoids importing a helper from the caller's checkout
    when a different repository root is under comparison.

    Pseudocode
    ----------
    - set module_spec = import specification for path
    - if module_spec is unavailable:
      - raise RuntimeError
    - set module = module constructed from module_spec
    - set executed_module = module_spec execution result
    - return module

    Wraps
    -----
    - none
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _cache_environment(cache_root: Path) -> dict[str, str]:
    """Return an isolated cache environment without inherited probe outputs.

    Intent
    ------
    Keep Python, XDG, UV, and worker-metric artifacts inside benchmark-owned
    locations.

    Rationale
    ---------
    Cache policy must be explicit and a parent measurement path must not leak
    into a nested benchmark observation.

    Pseudocode
    ----------
    - set environment = copy of current environment
    - set environment = environment without inherited worker metrics path
    - set environment = environment plus benchmark-owned cache paths
    - return environment

    Wraps
    -----
    - none
    """

    cache_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.pop("OFFICINA_PYTEST_WORKER_METRICS", None)
    environment.update(
        {
            "PYTHONPYCACHEPREFIX": str(cache_root / "pycache"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "UV_CACHE_DIR": str(cache_root / "uv"),
        }
    )
    return environment


def _git_output(repo_root: Path, *args: str) -> bytes:
    """Return exact stdout from a read-only Git command in ``repo_root``.

    Intent
    ------
    Provide one byte-preserving Git boundary for repository fingerprints and
    commit identity.

    Rationale
    ---------
    Fingerprints must include binary diffs without text decoding or newline
    normalization.

    Pseudocode
    ----------
    - return captured stdout from Git arguments in repo_root

    Wraps
    -----
    - none
    """

    return subprocess.check_output(["git", *args], cwd=repo_root)


def _staged_fingerprint(repo_root: Path) -> str:
    """Hash the complete staged binary diff used by staged-view suites.

    Intent
    ------
    Detect index mutations across a benchmark observation.

    Rationale
    ---------
    A staged-view suite may change behavior if its own input index changes, so
    such an observation cannot be accepted.

    Pseudocode
    ----------
    - set staged_diff = exact cached binary diff
    - return SHA-256 digest of staged_diff

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._git_output:
      why:
        reads: "Captures the exact staged binary diff before hashing."
    """

    return hashlib.sha256(
        _git_output(repo_root, "diff", "--cached", "--binary")
    ).hexdigest()


def _tracked_worktree_fingerprint(repo_root: Path) -> str:
    """Hash tracked unstaged changes while intentionally ignoring untracked files.

    Intent
    ------
    Detect mutation of tracked working bytes during an observation.

    Rationale
    ---------
    Untracked benchmark artifacts are permitted, while changed tracked inputs
    invalidate before-and-after timing.

    Pseudocode
    ----------
    - set tracked_diff = exact unstaged binary diff
    - return SHA-256 digest of tracked_diff

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._git_output:
      why:
        reads: "Captures tracked unstaged bytes without including untracked files."
    """

    return hashlib.sha256(_git_output(repo_root, "diff", "--binary")).hexdigest()


def resolve_benchmark_command(
    repo_root: Path,
    suite: str,
    jobs: int,
    task_id: str | None,
    task_cache_dir: Path | None = None,
) -> tuple[list[str], int | None]:
    """Build a selected-checkout runner command and report its worker lease.

    Intent
    ------
    Address the public runner in the measured repository without importing its
    policy module.

    Rationale
    ---------
    Complete suites own internal allocation. Direct shared phases lease the
    requested xdist workers; validator and performance phases report one slot.

    Pseudocode
    ----------
    - set command = selected checkout runner with suite and jobs
    - if task_id is absent:
      - return command and unknown task slots
    - set command = command plus task selection and optional cache path
    - return command and resolved task slots

    Wraps
    -----
    - none
    """
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
    """Run cache-controlled observations and write schema-version-5 evidence.

    Intent
    ------
    Produce comparable suite or task observations with enough evidence to
    reject failed, mutated, or instrumented runs.

    Rationale
    ---------
    The harness must control caches and preserve the selected checkout's runner
    policy. Sampling can perturb short tests, so sampled runs remain diagnostic.

    Pseudocode
    ----------
    - set validated_counts = positive run count and worker count
    - set benchmark_roots = selected-checkout helper and cache roots
    - if warm priming is enabled:
      - set prime_result = unmeasured command with verified repository fingerprints
    - for observation in requested_observations:
      - set observation_inputs = command, cache environment, log, and worker metrics path
      - set metrics = benchmark command result
      - set classification = acceptance only for successful unsampled result
      - set fingerprints = repository fingerprints before and after
    - set artifact = schema-version-5 JSON payload
    - return artifact payload

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._git_output:
      why:
        computes: "Captures the measured checkout's commit identity."
    ._staged_fingerprint:
      why:
        computes: "Detects staged mutations around prime and measured runs."
    ._tracked_worktree_fingerprint:
      why:
        computes: "Detects tracked working-tree mutations around each run."

    InstantiationsFromRepo
    ----------------------
    ._load_script:
      why:
        constructs: "Loads benchmark-command.py from the measured checkout."
    ._cache_environment:
      why:
        constructs: "Builds isolated cache environments for prime and measured runs."
    .default_jobs:
      why:
        constructs: "Records the host's default worker selection beside the request."
    .resolve_benchmark_command:
      why:
        constructs: "Builds complete-suite or direct-task runner commands."
    ._staged_fingerprint:
      why:
        constructs: "Carries before-and-after staged-state evidence."
    ._tracked_worktree_fingerprint:
      why:
        constructs: "Carries before-and-after tracked-state evidence."
    """
    if runs < 1:
        raise ValueError("runs must be positive")
    if jobs < 1:
        raise ValueError("jobs must be positive")
    root = Path(repo_root).resolve()
    benchmark = _load_script(
        root / "scripts" / "benchmark-command.py",
        "_benchmark_command",
    )
    cache_root = root / "_build" / "test-performance-cache"
    artifact_root = output.parent / f"{output.stem}-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    task_cache_root = artifact_root / "task-cache"
    task_cache_paths: list[dict[str, str]] = []
    task_slots: int | None = None

    def command_for(phase: str) -> list[str]:
        """Build one phase command and record its benchmark-owned cache path.

        Intent
        ------
        Give cold task observations fresh pytest caches and warm observations a
        shared cache without changing complete-suite runner behavior.

        Rationale
        ---------
        The task cache is a public runner input only for direct task selection.

        Pseudocode
        ----------
        - if task_id is absent:
          - return complete-suite command
        - set cache_dir = shared warm path or fresh cold path
        - set task_cache_paths = task_cache_paths plus cache_dir and task slots
        - return direct-task command

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .resolve_benchmark_command:
          why:
            constructs: "Builds the runner command returned for this phase."
        """
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
        warmup = subprocess.run(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if warmup.returncode:
            raise RuntimeError(
                f"warm-cache prime failed with exit code {warmup.returncode}"
            )
        if (
            _staged_fingerprint(root),
            _tracked_worktree_fingerprint(root),
        ) != (staged_before, tracked_before):
            raise RuntimeError("warm-cache prime changed repository state")

    measurements: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        if cache == "cold":
            shutil.rmtree(cache_root, ignore_errors=True)
        staged_before = _staged_fingerprint(root)
        tracked_before = _tracked_worktree_fingerprint(root)
        command = command_for(f"run-{index}")
        worker_metrics_path = artifact_root / f"run-{index}-workers.json"
        worker_metrics_path.unlink(missing_ok=True)
        command_environment = _cache_environment(cache_root)
        if measure_resources:
            command_environment["OFFICINA_PYTEST_WORKER_METRICS"] = str(
                worker_metrics_path
            )
        if task_id:
            command_environment["OFFICINA_FIXTURE_PROBE_TASK_ID"] = task_id
        metrics = dict(
            benchmark.benchmark_command(
                command,
                log_path=artifact_root / f"run-{index}.log",
                cwd=root,
                env=command_environment,
                record_samples=measure_resources,
                sample_process_tree=measure_resources,
            )
        )
        metrics["aggregate_descendant_cpu_concurrency"] = metrics.get(
            "average_effective_cores"
        )
        metrics["cpu_work_seconds"] = metrics.get("cpu_seconds")
        metrics["pytest_workers"] = (
            json.loads(worker_metrics_path.read_text(encoding="utf-8"))
            if measure_resources and worker_metrics_path.is_file()
            else None
        )
        staged_after = _staged_fingerprint(root)
        tracked_after = _tracked_worktree_fingerprint(root)
        measurements.append(
            {
                "run": index,
                "classification": (
                    "acceptance"
                    if int(metrics["returncode"]) == 0 and not measure_resources
                    else "diagnostic"
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
        "schema_version": 5,
        "repo": str(root),
        "commit": _git_output(root, "rev-parse", "HEAD").decode().strip(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "cache_condition": cache,
        "jobs": jobs,
        "requested_jobs": jobs,
        "resolved_default_jobs": resolved_jobs,
        "suite": suite,
        "task_id": task_id,
        "task_slots": task_slots,
        "task_cache_paths": task_cache_paths,
        "prime": prime,
        "runs_requested": runs,
        "command": command,
        "runs": measurements,
        "scheduler": "phased",
        "sequential_alias": sequential,
        "measurement_mode": (
            "sampled-diagnostic" if measure_resources else "os-accounting"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse public cache, repetition, selection, and diagnostic controls.

    Intent
    ------
    Keep the command-line contract in one place for direct and test invocation.

    Rationale
    ---------
    Explicit required cache and run-count arguments prevent accidental timing
    under an unspecified condition.

    Pseudocode
    ----------
    - set parser = benchmark argument parser
    - set parser = parser plus repository, suite, task, cache, run, worker, and diagnostic options
    - return parsed arguments

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .default_jobs:
      why:
        computes: "Supplies the default worker request shown by the CLI."
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument(
        "--suite",
        default="full",
        choices=("precommit", "pre-push", "full"),
    )
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
    """Run the requested benchmark and reject diagnostic-only observations.

    Intent
    ------
    Expose one process exit status suitable for scripts and maintainers.

    Rationale
    ---------
    Failed test commands and intentionally intrusive measurements must not be
    mistaken for acceptance timing merely because an artifact was written.

    Pseudocode
    ----------
    - set arguments = parsed command-line arguments
    - set result = cache-controlled benchmark observations
    - if any observation is diagnostic:
      - return failure
    - return success

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .parse_args:
      why:
        constructs: "Builds the validated benchmark request."
    .run_benchmarks:
      why:
        constructs: "Writes and returns the benchmark evidence artifact."
    """

    args = parse_args(argv)
    result = run_benchmarks(
        args.repo,
        args.suite,
        args.output,
        args.runs,
        args.cache,
        args.jobs,
        args.task_id,
        prime=not args.no_prime,
        sequential=args.sequential,
        measure_resources=args.measure_resources,
    )
    return (
        1
        if any(run["classification"] == "diagnostic" for run in result["runs"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
