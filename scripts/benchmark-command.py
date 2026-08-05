#!/usr/bin/env python3
"""Measure the elapsed time and computational cost of one child command.

The portable baseline records wall time, output, and exit status. On platforms
with ``resource.RUSAGE_CHILDREN`` it also records aggregate CPU and OS resource
usage. Linux additionally samples the complete descendant process tree to
estimate peak concurrent cores, processes, threads, and resident memory.

This is diagnostic tooling, not a benchmark framework: callers remain
responsible for controlling the commit, environment, cache state, repetitions,
and interpretation of failed commands.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None  # type: ignore[assignment]


def _linux_proc_constants() -> tuple[float, float] | None:
    """Return Linux clock and page constants when process sampling is usable.

    Intent
    ------
    Resolve the units needed to convert ``/proc`` CPU ticks and resident pages.

    Rationale
    ---------
    Sampling is optional and must disable itself cleanly on unsupported hosts.

    Pseudocode
    ----------
    - if the Linux process-stat file is absent:
      - return none
    - set constants = clock ticks per second and page size in kilobytes
    - return constants or none when the host query fails

    Wraps
    -----
    - none
    """
    if not Path("/proc/self/stat").is_file():
        return None
    try:
        return float(os.sysconf("SC_CLK_TCK")), os.sysconf("SC_PAGE_SIZE") / 1024
    except (OSError, ValueError):
        return None


def _process_rows(page_kb: float) -> dict[int, dict[str, object]]:
    """Read one best-effort Linux process-tree resource snapshot.

    Intent
    ------
    Map visible process IDs to ancestry, command, CPU, thread, and RSS facts.

    Rationale
    ---------
    Processes may disappear during sampling, so individual read failures are
    omitted while failure to inspect ``/proc`` yields an empty snapshot.

    Pseudocode
    ----------
    - set rows = empty process mapping
    - for entry in the Linux process directory:
      - if entry is a readable numeric process:
        - set rows = rows plus parsed process ancestry and resource facts
    - return rows

    Wraps
    -----
    - none
    """
    rows: dict[int, dict[str, object]] = {}
    try:
        entries = Path("/proc").iterdir()
    except OSError:
        return rows
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            close = raw.rfind(")")
            fields = raw[close + 2 :].split()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
            rows[int(entry.name)] = {
                "ppid": int(fields[1]),
                "command": (
                    cmdline.decode("utf-8", errors="replace").strip()
                    or raw[raw.find("(") + 1 : close]
                ),
                "cpu_ticks": int(fields[11]) + int(fields[12]),
                "threads": int(fields[17]),
                "rss_kb": int(fields[21]) * page_kb,
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return rows


def _descendants(rows: dict[int, dict[str, object]], root_pid: int) -> set[int]:
    """Resolve the root process and every visible transitive descendant.

    Intent
    ------
    Select the sampled process subtree rooted at ``root_pid``.

    Rationale
    ---------
    Resource cost belongs to the command's whole descendant tree, not only the
    launcher process observed directly by the benchmark harness.

    Pseudocode
    ----------
    - set selected = root process id
    - while another row has a parent in selected:
      - set selected = selected plus that child process id
    - return selected

    Wraps
    -----
    - none
    """
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, row in rows.items():
            if pid not in selected and row["ppid"] in selected:
                selected.add(pid)
                changed = True
    return selected


def _usage_snapshot() -> Any | None:
    """Read cumulative waited-child resource usage when the host supports it.

    Intent
    ------
    Capture one portable resource baseline for later before-and-after subtraction.

    Rationale
    ---------
    Platforms without ``RUSAGE_CHILDREN`` still provide wall and sampled metrics.

    Pseudocode
    ----------
    - if child resource usage is unavailable:
      - return none
    - set usage = cumulative waited-child resource snapshot
    - return usage

    Wraps
    -----
    - none
    """
    if resource is None or not hasattr(resource, "RUSAGE_CHILDREN"):
        return None
    return resource.getrusage(resource.RUSAGE_CHILDREN)


def _usage_delta(before: Any | None, after: Any | None) -> dict[str, float | int | None]:
    """Convert cumulative child-usage snapshots into command-local metrics.

    Intent
    ------
    Compute CPU, paging, I/O, and context-switch values attributable to one run.

    Rationale
    ---------
    The OS exposes cumulative waited-child counters, so subtraction is required;
    unavailable snapshots remain explicit ``None`` values rather than zeroes.

    Pseudocode
    ----------
    - if either usage snapshot is absent:
      - return unavailable metric values
    - set delta_metrics = after counters minus before counters
    - return delta_metrics

    Wraps
    -----
    - none
    """
    if before is None or after is None:
        return {
            "user_cpu_seconds": None,
            "system_cpu_seconds": None,
            "cpu_seconds": None,
            "max_rss_kb_waited_children": None,
            "minor_faults": None,
            "major_faults": None,
            "voluntary_context_switches": None,
            "involuntary_context_switches": None,
            "input_blocks": None,
            "output_blocks": None,
        }
    user = after.ru_utime - before.ru_utime
    system = after.ru_stime - before.ru_stime
    return {
        "user_cpu_seconds": user,
        "system_cpu_seconds": system,
        "cpu_seconds": user + system,
        # ru_maxrss is a maximum, not an additive counter; report its observed
        # post-run value and use sampled tree RSS for within-run comparisons.
        "max_rss_kb_waited_children": after.ru_maxrss,
        "minor_faults": after.ru_minflt - before.ru_minflt,
        "major_faults": after.ru_majflt - before.ru_majflt,
        "voluntary_context_switches": after.ru_nvcsw - before.ru_nvcsw,
        "involuntary_context_switches": after.ru_nivcsw - before.ru_nivcsw,
        "input_blocks": after.ru_inblock - before.ru_inblock,
        "output_blocks": after.ru_oublock - before.ru_oublock,
    }


def benchmark_command(
    command: list[str],
    *,
    log_path: Path,
    sample_interval_seconds: float = 0.02,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    record_samples: bool = False,
) -> dict[str, object]:
    """Run one command and return wall, CPU, concurrency, and OS metrics.

    Intent
    ------
    Measure one child command and its visible descendants while retaining output
    in ``log_path`` and preserving the exact child return code.

    Rationale
    ---------
    Wall time alone hides computation cost and parallelism. Portable resource
    deltas and Linux tree sampling provide complementary evidence without turning
    command failure into benchmark infrastructure failure.

    Pseudocode
    ----------
    - if the command or sampling interval is invalid:
      - raise benchmark argument error
    - set process_constants = optional Linux sampling units
    - set before_usage = cumulative child resource snapshot
    - set process = child command with combined output written to log_path
    - while process is running:
      - set sampled_rows = visible process facts
      - set selected_processes = process and visible descendants
      - set sample_metrics = CPU concurrency thread count and resident memory
    - set usage_delta = after snapshot minus before snapshot
    - set metrics = wall resource concurrency process and capability results
    - return metrics

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._usage_snapshot:
      why:
        reads: "Captures the post-run cumulative resource snapshot used by delta calculation."

    InstantiationsFromRepo
    ----------------------
    ._descendants:
      why:
        constructs: "Builds each visible descendant-process selection."
    ._linux_proc_constants:
      why:
        constructs: "Builds the optional Linux sampling conversion constants."
    ._process_rows:
      why:
        constructs: "Builds each process-tree resource snapshot."
    ._usage_delta:
      why:
        constructs: "Builds command-local resource metrics from cumulative snapshots."
    ._usage_snapshot:
      why:
        constructs: "Builds the pre-run resource baseline."
    """
    if not command:
        raise ValueError("command must not be empty")
    if sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")

    proc_constants = _linux_proc_constants()
    before = _usage_snapshot()
    start = time.monotonic()
    seen: dict[int, str] = {}
    last_ticks: dict[int, int] = {}
    interval_cores: list[float] = []
    sampled_cpu_seconds = 0.0
    peak_processes = 0
    peak_threads = 0
    peak_rss_kb = 0.0
    process_tree_samples: list[dict[str, float | int]] = []

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        previous_sample = time.monotonic()
        while process.poll() is None:
            if proc_constants is not None:
                ticks_per_second, page_kb = proc_constants
                rows = _process_rows(page_kb)
                selected = _descendants(rows, process.pid)
                now = time.monotonic()
                delta_ticks = 0
                process_count = 0
                thread_count = 0
                rss_kb = 0.0
                for pid in selected:
                    row = rows.get(pid)
                    if row is None:
                        continue
                    process_count += 1
                    thread_count += int(row["threads"])
                    rss_kb += float(row["rss_kb"])
                    seen[pid] = str(row["command"])
                    ticks = int(row["cpu_ticks"])
                    delta_ticks += max(0, ticks - last_ticks.get(pid, ticks))
                    last_ticks[pid] = ticks
                sample_cpu = delta_ticks / ticks_per_second
                elapsed = max(now - previous_sample, 1e-9)
                sampled_cpu_seconds += sample_cpu
                interval_cores.append(sample_cpu / elapsed)
                peak_processes = max(peak_processes, process_count)
                peak_threads = max(peak_threads, thread_count)
                peak_rss_kb = max(peak_rss_kb, rss_kb)
                if record_samples:
                    process_tree_samples.append(
                        {
                            "elapsed_seconds": now - start,
                            "interval_seconds": elapsed,
                            "cpu_seconds": sample_cpu,
                            "effective_cores": sample_cpu / elapsed,
                            "processes": process_count,
                            "threads": thread_count,
                            "rss_kb": rss_kb,
                        }
                    )
                previous_sample = now
            time.sleep(sample_interval_seconds)
        returncode = process.wait()

    wall = time.monotonic() - start
    usage = _usage_delta(before, _usage_snapshot())
    cpu_seconds = usage["cpu_seconds"]
    command_counts: dict[str, int] = {}
    for cmdline in seen.values():
        parts = cmdline.split()
        executable = Path(parts[0]).name if parts else "unknown"
        command_counts[executable] = command_counts.get(executable, 0) + 1

    metrics: dict[str, object] = {
        "command": command,
        "returncode": returncode,
        "wall_seconds": wall,
        **usage,
        "average_effective_cores": (
            float(cpu_seconds) / wall if cpu_seconds is not None and wall else None
        ),
        "sampled_cpu_seconds": sampled_cpu_seconds if proc_constants else None,
        "peak_effective_cores": max(interval_cores, default=0.0) if proc_constants else None,
        "interval_fraction_over_one_core": (
            sum(value > 1.0 for value in interval_cores) / len(interval_cores)
            if interval_cores
            else (0.0 if proc_constants else None)
        ),
        "peak_processes": peak_processes if proc_constants else None,
        "peak_threads": peak_threads if proc_constants else None,
        "peak_sampled_tree_rss_kb": peak_rss_kb if proc_constants else None,
        "sampled_unique_processes": len(seen) if proc_constants else None,
        "sampled_command_counts": (
            dict(sorted(command_counts.items())) if proc_constants else None
        ),
        "process_tree_samples": (
            process_tree_samples if proc_constants and record_samples else None
        ),
        "capabilities": {
            "wall_time": True,
            "child_resource_usage": before is not None,
            "linux_process_tree_sampling": proc_constants is not None,
        },
    }
    return metrics


def main() -> int:
    """Parse the benchmark CLI, persist metrics, and preserve child status.

    Intent
    ------
    Convert command-line inputs into one measured command and JSON output artifact.

    Rationale
    ---------
    Returning the child's status keeps the wrapper transparent to automation while
    still emitting diagnostic measurements for failed commands.

    Pseudocode
    ----------
    - set args = parsed output log interval and child command arguments
    - if the child command is empty:
      - raise command-line usage error
    - set metrics = measured child command result
    - set benchmark_output = persisted formatted metrics
    - return the measured child return code

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .benchmark_command:
      why:
        constructs: "Builds the measurement record whose status becomes the process exit code."
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required after --")

    metrics = benchmark_command(
        command,
        log_path=args.log,
        sample_interval_seconds=args.interval,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return int(metrics["returncode"])


if __name__ == "__main__":
    raise SystemExit(main())
