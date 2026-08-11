from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-test-suite.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_test_suite", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_full_suite_resolves_to_root_runner(tmp_path: Path) -> None:
    benchmark = load_module()

    command, slots = benchmark.resolve_benchmark_command(tmp_path, "full", 1, None)

    assert command == [
        sys.executable,
        str(tmp_path / "repo_checks.py"),
        "--suite",
        "full",
        "--jobs",
        "1",
    ]
    assert slots is None


def test_task_resolution_uses_selected_root_runner_and_task_cache(
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    task_cache = tmp_path / "task-cache"

    command, slots = benchmark.resolve_benchmark_command(
        tmp_path, "full", 8, "tests:shared", task_cache
    )

    assert command == [
        sys.executable,
        str(tmp_path / "repo_checks.py"),
        "--suite",
        "full",
        "--jobs",
        "8",
        "--task-id",
        "tests:shared",
        "--task-cache-dir",
        str(task_cache.resolve()),
    ]
    assert slots == 8
    assert benchmark.resolve_benchmark_command(
        tmp_path, "full", 8, "tests:performance", task_cache
    )[1] == 1


def test_task_resolution_does_not_import_the_selected_checkout(
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "officina" or name.startswith("officina.")
    }

    modules_before = dict(sys.modules)
    path_before = list(sys.path)

    command, slots = benchmark.resolve_benchmark_command(
        tmp_path,
        "full",
        8,
        "tests:shared",
        tmp_path / "task-cache",
    )

    assert command[:2] == [sys.executable, str(tmp_path / "repo_checks.py")]
    assert slots == 8
    assert sys.modules == modules_before
    assert sys.path == path_before
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "officina" or name.startswith("officina.")
    } == before


def test_warm_task_observations_share_cache_only_within_one_invocation(
    monkeypatch, tmp_path: Path
) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    resolved_caches = []
    commands = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(command, **_kwargs):
            commands.append(command)
            return {"returncode": 0, "wall_seconds": 0.1}

    def resolve(_root, _suite, _jobs, _task_id, cache_dir=None):
        resolved_caches.append(cache_dir)
        return [sys.executable, "-m", "pytest", "-o", f"cache_dir={cache_dir}", "-q"], 6

    monkeypatch.setattr(benchmark, "_load_script", lambda *_args: FakeBenchmark)
    monkeypatch.setattr(benchmark, "resolve_benchmark_command", resolve)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(benchmark.subprocess, "run", lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(benchmark, "_git_output", lambda _root, *args: b"commit\n" if args[0] == "rev-parse" else b"")

    first = benchmark.run_benchmarks(tmp_path, "full", tmp_path / "first.json", 2, "warm", 8, "tests:shared")
    second = benchmark.run_benchmarks(tmp_path, "full", tmp_path / "second.json", 1, "warm", 8, "tests:shared", prime=False)

    assert len(resolved_caches) == 4
    assert len({path.resolve() for path in resolved_caches}) == 2
    assert len({path.resolve() for path in resolved_caches[:3]}) == 1
    assert resolved_caches[3].resolve() != resolved_caches[0].resolve()
    assert all("cache_dir=" in command[4] for command in commands)
    assert len(first["task_cache_paths"]) == 3
    assert len(second["task_cache_paths"]) == 1


def test_no_prime_makes_only_requested_measured_calls(monkeypatch, tmp_path: Path) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    measured = []
    primes = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(command, **_kwargs):
            measured.append(command)
            return {"returncode": 0, "wall_seconds": 0.1}

    monkeypatch.setattr(benchmark, "_load_script", lambda *_args: FakeBenchmark)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *args, **_kwargs: primes.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(benchmark, "_git_output", lambda _root, *args: b"commit\n" if args[0] == "rev-parse" else b"")

    benchmark.run_benchmarks(tmp_path, "full", tmp_path / "out.json", 2, "warm", 1, None, prime=False)

    assert len(measured) == 2
    assert primes == []


def test_benchmark_embeds_distinct_pytest_worker_metrics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    metric_paths: list[Path] = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(_command, **kwargs):
            path = Path(kwargs["env"]["OFFICINA_PYTEST_WORKER_METRICS"])
            metric_paths.append(path)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "session_seconds": 10.0,
                        "workers": [
                            {
                                "worker_id": "gw0",
                                "item_count": 2,
                                "assigned_seconds": 8.0,
                                "unassigned_seconds": 2.0,
                                "assigned_fraction": 0.8,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return {
                "returncode": 0,
                "wall_seconds": 10.0,
                "cpu_seconds": 35.0,
                "average_effective_cores": 3.5,
            }

    monkeypatch.setattr(benchmark, "_load_script", lambda *_args: FakeBenchmark)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(
        benchmark,
        "_git_output",
        lambda _root, *args: b"commit\n" if args[0] == "rev-parse" else b"",
    )

    result = benchmark.run_benchmarks(
        tmp_path,
        "precommit",
        tmp_path / "out.json",
        2,
        "warm",
        8,
        None,
        prime=False,
        measure_resources=True,
    )

    assert result["schema_version"] == 5
    assert result["measurement_mode"] == "sampled-diagnostic"
    assert len(set(metric_paths)) == 2
    for run in result["runs"]:
        assert run["classification"] == "diagnostic"
        assert run["metrics"]["cpu_work_seconds"] == 35.0
        assert run["metrics"]["aggregate_descendant_cpu_concurrency"] == 3.5
        assert run["metrics"]["pytest_workers"]["workers"][0][
            "assigned_fraction"
        ] == 0.8


def test_acceptance_benchmark_does_not_enable_worker_metrics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    stale_metrics = tmp_path / "out-artifacts" / "run-1-workers.json"
    stale_metrics.parent.mkdir()
    stale_metrics.write_text("stale\n", encoding="utf-8")

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(_command, **kwargs):
            assert "OFFICINA_PYTEST_WORKER_METRICS" not in kwargs["env"]
            return {
                "returncode": 0,
                "wall_seconds": 10.0,
                "cpu_seconds": 35.0,
                "average_effective_cores": 3.5,
            }

    monkeypatch.setattr(benchmark, "_load_script", lambda *_args: FakeBenchmark)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(
        benchmark,
        "_git_output",
        lambda _root, *args: b"commit\n" if args[0] == "rev-parse" else b"",
    )

    result = benchmark.run_benchmarks(
        tmp_path,
        "precommit",
        tmp_path / "out.json",
        1,
        "warm",
        8,
        None,
        prime=False,
        measure_resources=False,
    )

    assert result["runs"][0]["classification"] == "acceptance"
    assert result["runs"][0]["metrics"]["pytest_workers"] is None
    assert not stale_metrics.exists()


def test_prime_adds_one_unmeasured_call(monkeypatch, tmp_path: Path) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    measured = []
    primes = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(command, **_kwargs):
            measured.append(command)
            return {"returncode": 0, "wall_seconds": 0.1}

    monkeypatch.setattr(benchmark, "_load_script", lambda *_args: FakeBenchmark)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *args, **_kwargs: primes.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(benchmark, "_git_output", lambda _root, *args: b"commit\n" if args[0] == "rev-parse" else b"")

    benchmark.run_benchmarks(tmp_path, "full", tmp_path / "out.json", 2, "warm", 1, None, prime=True)

    assert len(measured) == 2
    assert len(primes) == 1
