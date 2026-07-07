#!/usr/bin/env python3
"""Reference calibration tool for each parser's default_threshold in scan.py.

Not part of the skill's exported interface -- this is a manual dev tool for
re-deriving reasonable per-host thresholds when the current defaults start
to feel wrong (too many/too few sessions flagged in practice). Fully
generic like scan.py itself -- loops over whatever __init__.py's `parsers`
list provides, never naming a host (see references/skill-guidelines.md,
guideline 13).

Measures whole-file net chars (raw length minus each host's own opaque
field, via scan.opaque_len) across all session files touched in the last
--days days, and prints per-host summary statistics plus a comparison
against the current threshold. This is an upper bound on the actual
gap_net_chars metric scan.py flags on (which resets at each completed
handoff) -- for sessions that never had a handoff, the two are identical.

The original 2026-07 calibration (see scan.py's module docstring) was done
this way, ad hoc, in a /tmp scratch script that was not preserved. This
script reproduces that methodology as a durable, reusable tool.

Usage:
    calibrate.py [--days N]

Output: human-readable stats to stdout. Does not modify scan.py or the
parser files -- if the numbers suggest new defaults, update each parser's
default_threshold by hand and re-run the test suite.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import statistics

_SCAN_PATH = os.path.join(os.path.dirname(__file__), "scan.py")
_spec = importlib.util.spec_from_file_location("scan", _SCAN_PATH)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def _net_chars_for_file(path: str, opaque_field: str) -> tuple[int, int]:
    """Return (line_count, net_chars) for a whole transcript file."""
    net_total = 0
    n = 0
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            net_total += len(line) - scan.opaque_len(obj, opaque_field)
    return n, net_total


def collect(days: int, parsers=None):
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    results = {}

    for host_parser in (parsers if parsers is not None else scan.PARSERS):
        records = []
        for path in host_parser.list_session_files():
            try:
                mtime = os.path.getmtime(path)
                if mtime < cutoff:
                    continue
                n, net = _net_chars_for_file(path, host_parser.opaque_field)
            except OSError:
                continue
            records.append((n, net, mtime))
        results[host_parser.id] = records

    return results


def _print_stats(host_id: str, records: list, default_threshold: int):
    if not records:
        print(f"=== {host_id}: no sessions in window ===\n")
        return
    nets = sorted(net for _, net, _ in records)
    lines = [n for n, _, _ in records]
    print(f"=== {host_id}: n={len(nets)} ===")
    print(f"net_chars  min={nets[0]:,}  median={int(statistics.median(nets)):,}  "
          f"p75={nets[int(len(nets)*0.75)]:,}  p90={nets[int(len(nets)*0.90)]:,}  max={nets[-1]:,}")
    print(f"line_count min={min(lines)}  median={int(statistics.median(lines))}  max={max(lines)}")
    print(f"current default_threshold = {default_threshold:,}")
    exceeding = sum(1 for v in nets if v >= default_threshold)
    print(f"  -> {exceeding}/{len(nets)} sessions would exceed the current threshold")
    print()


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--days", type=int, default=5, help="Lookback window in days (default: 5).")
    args = arg_parser.parse_args()

    results = collect(args.days)
    for host_parser in scan.PARSERS:
        _print_stats(host_parser.id, results[host_parser.id], host_parser.default_threshold)


if __name__ == "__main__":
    main()
