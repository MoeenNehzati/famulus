# Test-Suite Performance

This document records the benchmark contract and the current conclusions about
repository-test performance. It intentionally omits per-file audit ledgers and
superseded implementation plans.

## Measurement rules

An acceptance timing must:

- run the canonical `repo_checks.py` interface;
- use the same suite, worker count, repository view, and cache condition on
  both sides;
- preserve test selection and successful exit status;
- record at least three observations when deciding whether to retain an
  optimization;
- compare medians and show every observation;
- verify that neither the staged diff nor tracked working diff changed during
  a run.

Resource sampling and process tracing are diagnostics, not acceptance timing.
They perturb short tests and can make timing-sensitive assertions fail. The
benchmark harness therefore labels sampled observations `diagnostic` even when
the test command exits successfully.

## Canonical benchmark

Run repeated warm observations:

```bash
scripts/benchmark-test-suite.py \
  --repo . \
  --suite precommit \
  --output /tmp/precommit.json \
  --runs 3 \
  --cache warm \
  --jobs 8
```

Use `--cache cold` for fresh benchmark-owned Python, XDG, UV, and pytest
caches. Warm mode primes by default; `--no-prime` disables that preliminary
run. `--task-id tests:shared` measures one stable runner phase rather than the
complete suite.

Add `--measure-resources` only for diagnosis. Each run records its console log,
optional process-tree samples, and worker-assignment report beside the JSON
artifact.

## Metric meanings

- `wall_seconds` is elapsed command time and is the primary performance result.
- `cpu_work_seconds` is aggregate user and system CPU consumed by waited-for
  descendants.
- `aggregate_descendant_cpu_concurrency` is CPU work divided by wall time. It
  is not literal average hardware-core occupancy: pytest workers may spawn
  descendants, and their CPU intervals can overlap.
- `pytest_workers.assigned_seconds` sums pytest setup, call, and teardown report
  durations for one worker. It measures assignment occupancy, not CPU use.
- Per-file JUnit totals omit collection, controller startup, and unattributed
  scheduler overhead.

Do not infer eight-core saturation solely because aggregate descendant CPU
concurrency is near eight. Do not infer scheduler starvation solely from low
hardware utilization without checking worker assignment and the end-of-suite
tail.

## Current architecture

The functional and validator items share one pytest-xdist pool. Browser-free
suites use work stealing; browser-containing suites use load grouping. Only the
full-suite performance thresholds run separately and serially. The runner does
not fail fast and does not maintain a second process pool.

Fresh dispatcher CLI gates use 100 ms median / 150 ms p95 on the macOS
reference host, 125 ms / 200 ms on Linux, and 175 ms / 250 ms on Windows.
The Linux allowance covers short-lived contention after the parallel shared
suite, while Windows process creation has a higher fixed cost. The warm
in-process resolution gates remain identical across platforms.

This is the simplest supported architecture. Performance work should target
test or validator computation, not add another scheduler layer.

## Verified scaling evidence

Measurements on 2026-08-11 showed substantial host variance. One matched,
uninstrumented observation measured approximately 146.83 seconds with one
worker and 39.98 seconds with eight workers, a 3.67x speedup. Other successful
warm eight-worker observations ranged from about 38.5 to 51.3 seconds.
Consequently, a single run is not sufficient evidence for a small change.

Worker-assignment reports did not show one long test running alone after every
other worker became idle. The scaling gap is therefore not explained by a
simple test-granularity tail.

## Rejected hypotheses

### Prebuilt repository graph

A trial that prepared and shared a repository graph changed matched medians
from 42.33 to 45.27 seconds. It was reverted because it was slower and added
state-sharing complexity.

### Per-blob Git process contention

A staged experiment replaced individual Git blob reads with batch reads. It
worked mechanically:

| Measure | Baseline | Batched | Change |
| --- | ---: | ---: | ---: |
| Successful Git processes | 23,503 | 15,319 | -35% |
| Individual `cat-file blob` processes | 9,489 | 841 | -91% |
| Batch reads | 31 | 486 | increased as intended |

Three canonical warm observations were:

| Version | Observations (s) | Median (s) |
| --- | --- | ---: |
| Baseline | 46.95, 50.97, 51.32 | 50.97 |
| Batched | 46.47, 50.46, 49.45 | 49.45 |

The 1.52-second, 3.0% median improvement was too small to justify the added
batch protocol and error fallback. The experiment was reverted. Git process
count is therefore not the major cause of the gap between current scaling and
the sevenfold target.

## Optimization policy

Prefer pytest fixtures, parametrization, and ordinary import caching when they
remove repeated preparation without sharing mutable state. Preserve real
processes when process startup, environment construction, installed entry
points, locking, or cross-process persistence is the behavior under test.

For runner-level performance changes intended to close the scaling gap, retain
the change only when three matched canonical observations show at least a 10%
or four-second median wall improvement. Smaller local refactors may still be
appropriate when they simplify code, but they must not be presented as a
suite-level speedup.

The unresolved problem is the increase in aggregate CPU work under parallel
execution. Its cause has not been pinned down. Future experiments should be
narrow, reversible, and aimed at computations that plausibly explain tens of
seconds rather than sub-second preparation costs.
