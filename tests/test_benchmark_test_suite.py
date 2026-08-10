from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_task_resolution_uses_selected_root_and_task_cache(
    monkeypatch, tmp_path: Path
) -> None:
    benchmark = load_module()
    task_cache = tmp_path / "task-cache"

    class FakeChecks:
        CheckTask = SimpleNamespace

        @staticmethod
        def _build_check_tasks(*_args, **_kwargs):
            return [
                SimpleNamespace(
                    id="tests:shared",
                    argv=(sys.executable, "-m", "pytest", "-q", "tests"),
                    slots=6,
                ),
                SimpleNamespace(id="tests:isolated", argv=("check",), slots=1),
            ]

    monkeypatch.setattr(benchmark, "_load_repository_checks", lambda _root: FakeChecks)

    command, slots = benchmark.resolve_benchmark_command(
        tmp_path, "full", 8, "tests:shared", task_cache
    )

    assert command == [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        f"cache_dir={task_cache.resolve()}",
        "-q",
        "tests",
    ]
    assert slots == 6
    assert benchmark.resolve_benchmark_command(
        tmp_path, "full", 8, "tests:isolated", task_cache
    )[1] == 1
    with pytest.raises(ValueError, match="tests:shared.*tests:isolated"):
        benchmark.resolve_benchmark_command(tmp_path, "full", 8, "unknown")


def test_task_resolution_loads_live_selected_root_without_officina_leakage(
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "officina" or name.startswith("officina.")
    }

    command, slots = benchmark.resolve_benchmark_command(
        Path(__file__).resolve().parents[1],
        "full",
        8,
        "tests:shared",
        tmp_path / "task-cache",
    )

    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert slots == 6
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "officina" or name.startswith("officina.")
    } == before


def test_selected_root_load_restores_all_module_and_path_mappings(tmp_path: Path) -> None:
    benchmark = load_module()
    source = tmp_path / "src" / "officina"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "selected_root_sentinel.py").write_text(
        "VALUE = 'selected-root'\n", encoding="utf-8"
    )
    (source / "repository_checks.py").write_text(
        "from dataclasses import dataclass\n"
        "import selected_root_sentinel\n"
        "@dataclass(frozen=True)\n"
        "class CheckTask:\n"
        "    id: str\n"
        "    argv: tuple[str, ...]\n"
        "    slots: int\n"
        "def _build_check_tasks(*_args, **_kwargs):\n"
        "    return [CheckTask('selected', ('check',), 1)]\n",
        encoding="utf-8",
    )
    modules_before = dict(sys.modules)
    path_before = list(sys.path)

    command, slots = benchmark.resolve_benchmark_command(
        tmp_path, "full", 1, "selected"
    )

    assert command == ["check"]
    assert slots == 1
    assert sys.modules == modules_before
    assert sys.path == path_before


def test_direct_task_observations_receive_fresh_cache_per_invocation(
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
    assert len({path.resolve() for path in resolved_caches}) == 4
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
