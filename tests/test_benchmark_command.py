from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark-command.py"


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_command", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_benchmark_preserves_failure_and_records_cost_metrics(tmp_path: Path) -> None:
    benchmark = _load_benchmark_module()
    log_path = tmp_path / "command.log"

    metrics = benchmark.benchmark_command(
        [
            sys.executable,
            "-c",
            "import sys; print('captured'); raise SystemExit(7)",
        ],
        log_path=log_path,
        sample_interval_seconds=0.005,
    )

    assert metrics["returncode"] == 7
    assert metrics["wall_seconds"] > 0
    assert metrics["cpu_seconds"] is None or metrics["cpu_seconds"] >= 0
    assert metrics["average_effective_cores"] is None or (
        metrics["average_effective_cores"] >= 0
    )
    assert metrics["capabilities"]["wall_time"] is True
    assert log_path.read_text(encoding="utf-8") == "captured\n"


def test_benchmark_passes_explicit_working_directory_and_environment(
    tmp_path: Path,
) -> None:
    """Dropping cwd or env forwarding must change the child-observed payload."""
    benchmark = _load_benchmark_module()
    log_path = tmp_path / "command.log"
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    env = os.environ.copy()
    env["BENCHMARK_SENTINEL"] = "present"

    metrics = benchmark.benchmark_command(
        [
            sys.executable,
            "-c",
            (
                "import json, os; from pathlib import Path; "
                "print(json.dumps([str(Path.cwd()), os.environ['BENCHMARK_SENTINEL']]))"
            ),
        ],
        log_path=log_path,
        cwd=child_dir,
        env=env,
        sample_interval_seconds=0.005,
    )

    assert metrics["returncode"] == 0
    assert json.loads(log_path.read_text(encoding="utf-8")) == [
        str(child_dir),
        "present",
    ]


def test_benchmark_can_return_process_tree_samples_for_attribution(
    monkeypatch, tmp_path: Path,
) -> None:
    """Removing sample retention must make interval attribution impossible."""
    benchmark = _load_benchmark_module()
    release_path = tmp_path / "release-child"
    command = [sys.executable, "-c", "pass"]

    if benchmark._linux_proc_constants() is not None:
        real_process_rows = benchmark._process_rows
        snapshot_count = 0

        def release_child_after_second_snapshot(page_kb: float):
            nonlocal snapshot_count
            rows = real_process_rows(page_kb)
            snapshot_count += 1
            if snapshot_count == 2:
                release_path.touch()
            return rows

        monkeypatch.setattr(benchmark, "_process_rows", release_child_after_second_snapshot)
        command = [
            sys.executable,
            "-c",
            (
                "import sys, time; from pathlib import Path; "
                "release = Path(sys.argv[1]); deadline = time.monotonic() + 5; "
                "exec(\"while not release.exists():\\n"
                " if time.monotonic() >= deadline: raise SystemExit(9)\\n"
                " time.sleep(0.001)\")"
            ),
            str(release_path),
        ]

    metrics = benchmark.benchmark_command(
        command,
        log_path=tmp_path / "command.log",
        sample_interval_seconds=0.005,
        record_samples=True,
    )

    if metrics["capabilities"]["linux_process_tree_sampling"]:
        assert metrics["returncode"] == 0
        samples = metrics["process_tree_samples"]
        assert len(samples) >= 2
        assert all(row["elapsed_seconds"] >= 0 for row in samples)
        assert all(row["interval_seconds"] > 0 for row in samples)
        assert all(row["cpu_seconds"] >= 0 for row in samples)
        assert all(row["rss_kb"] >= 0 for row in samples)
    else:
        assert metrics["process_tree_samples"] is None


def test_timing_mode_disables_process_tree_sampling(monkeypatch, tmp_path: Path) -> None:
    benchmark = _load_benchmark_module()
    monkeypatch.setattr(
        benchmark,
        "_process_rows",
        lambda _page_kb: (_ for _ in ()).throw(AssertionError("sampled")),
    )

    metrics = benchmark.benchmark_command(
        [sys.executable, "-c", "pass"],
        log_path=tmp_path / "command.log",
        sample_process_tree=False,
    )

    assert metrics["capabilities"]["linux_process_tree_sampling"] is False
    assert metrics["peak_sampled_tree_rss_kb"] is None
