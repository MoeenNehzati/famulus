from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-precommit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("benchmark_precommit", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_jobs_uses_two_thirds_of_logical_cpus(monkeypatch) -> None:
    benchmark = load_module()
    monkeypatch.setattr(benchmark.os, "cpu_count", lambda: 12)

    assert benchmark.default_jobs() == 8


@pytest.mark.parametrize(("runs", "jobs"), [(0, 1), (1, 0)])
def test_run_benchmarks_rejects_nonpositive_dimensions(
    tmp_path: Path, runs: int, jobs: int
) -> None:
    benchmark = load_module()

    with pytest.raises(ValueError):
        benchmark.run_benchmarks(
            repo_root=tmp_path,
            output=tmp_path / "result.json",
            runs=runs,
            cache="warm",
            jobs=jobs,
        )


def test_run_benchmarks_invokes_centralized_runner(monkeypatch, tmp_path: Path) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    calls = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(command, **kwargs):
            calls.append((command, kwargs))
            return {"returncode": 0, "wall_seconds": 1.25}

    monkeypatch.setattr(benchmark, "_load_script", lambda *args: FakeBenchmark)
    monkeypatch.setattr(
        benchmark,
        "_git_output",
        lambda repo_root, *args: b"commit\n" if args[0] == "rev-parse" else b"",
    )
    output = tmp_path / "results" / "precommit.json"

    result = benchmark.run_benchmarks(
        repo_root=tmp_path,
        output=output,
        runs=1,
        cache="warm",
        jobs=4,
    )

    assert calls[0][0][1:] == [
        str(tmp_path / "repo_checks.py"),
        "--suite",
        "precommit",
        "--jobs",
        "4",
    ]
    assert result["runs"][0]["classification"] == "acceptance"
    assert json.loads(output.read_text(encoding="utf-8"))["jobs"] == 4


def test_run_benchmarks_selects_sequential_timing_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    benchmark = load_module()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "repo_checks.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "benchmark-command.py").write_text("", encoding="utf-8")
    calls = []

    class FakeBenchmark:
        @staticmethod
        def benchmark_command(command, **kwargs):
            calls.append((command, kwargs))
            return {"returncode": 0, "wall_seconds": 1.0}

    monkeypatch.setattr(benchmark, "_load_script", lambda *args: FakeBenchmark)
    monkeypatch.setattr(
        benchmark,
        "_git_output",
        lambda repo_root, *args: b"commit\n" if args[0] == "rev-parse" else b"",
    )

    result = benchmark.run_benchmarks(
        repo_root=tmp_path,
        output=tmp_path / "result.json",
        runs=1,
        cache="warm",
        jobs=4,
        sequential=True,
        measure_resources=False,
    )

    assert calls[0][0][-1] == "--sequential"
    assert calls[0][1]["sample_process_tree"] is False
    assert calls[0][1]["record_samples"] is False
    assert result["scheduler"] == "sequential"
