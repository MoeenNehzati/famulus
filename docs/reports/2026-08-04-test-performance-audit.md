# Test Performance and Quality Audit

Date: 2026-08-04

Immutable baseline: `5d4fdfc40afab80c3102b333015d217554913a4c`

Machine: Linux, Intel i7-1365U, 10 physical cores / 12 logical CPUs

## Verdict

Within the measured shared group and selected expensive suites, repeated
repository reconstruction and validation are major costs and execution is
nearly single-core. Because the nested-runtime groups and complete hook were not
measured end to end, this audit does not establish the dominant cause for the
whole suite.

The clean precommit observation used 187.96 wall-seconds and 186.27 CPU-seconds:
0.991 effective cores on average. The full observation used 189.36 wall-seconds
and 188.72 CPU-seconds. Both are incomplete lower bounds: the shared pytest
group failed, and the runner returned before executing any later nested-runtime
group. The apparent two-second difference is therefore neither a whole-suite
comparison nor a valid estimate of installer cost.

Adding workers is an available latency tradeoff, but it is not the first fix.
For the live refactor suite, four pytest workers reduced 97.17 seconds to 45.14
seconds while increasing CPU from 97.06 to 162.62 seconds and peak sampled tree
RSS from about 95 MB to 376 MB. That is 54% less waiting for 68% more CPU and
roughly four times the memory.

## Measurement method

`scripts/benchmark-command.py` runs a command without changing its exit status,
captures combined output, measures wall time and aggregate child resource use,
and, on Linux, samples the transitive process tree every 20 ms. It reports:

- user and system CPU time, and CPU/wall as effective cores;
- peak sampled effective cores and fraction of intervals above one core;
- peak descendant processes, threads, and total resident memory;
- faults, context switches, block I/O, unique sampled processes, and executable
  counts.

Example:

```bash
scripts/benchmark-command.py \
  --output /tmp/precommit.json \
  --log /tmp/precommit.log \
  -- python3 scripts/run-python-tests.py --suite precommit
```

The checked-in machine-readable results are in
`docs/reports/data/2026-08-04-test-performance.json`. Raw logs and cProfile
files are intentionally excluded because they are large, host-specific, and
contain temporary paths.

## Incomplete runner observations

| Workload | Wall s | CPU s | Avg cores | Peak proc/thread | Peak tree RSS | Result |
|---|---:|---:|---:|---:|---:|---|
| Precommit shared group only | 187.96 | 186.27 | 0.991 | 8 / 9 | 364 MB | 35 failed, 1405 passed, 15 skipped, 1 deselected |
| Full shared group only | 189.36 | 188.72 | 0.997 | 7 / 20 | 427 MB | 36 failed, 1405 passed, 15 skipped |
| Core, browser excluded | 122.27 | 123.56 | 1.011 | 6 / 19 | 302 MB | environment-limited failures |
| Refactor-node | 68.23 | 68.22 | 1.000 | 2 / 4 | 103 MB | passed |
| Validators | 43.16 | 43.14 | 0.999 | 3 / 3 | 188 MB | passed |
| Portability sentinel | 1.06 | 1.05 | 0.988 | 3 / 3 | 86 MB | 7 passed |

The precommit process sample observed 711 descendant PIDs: 505 `git`, 83
Python, and 31 Chrome processes were the largest named groups. These are counts
of unique observed processes, not simultaneous processes; peak simultaneity was
only eight.

Because the runner is fail-fast between execution groups, neither observation
includes the 15 nested-runtime precommit groups; the full observation also does
not reach the installer groups. The clean full run's shared group has one
additional repository-contract failure: its reviewed
nested-module inventory at that commit no longer matches the repository. The
other 35 failures are local capability failures: four real-`uv` tests cannot
write/download in this sandbox, and all 31 Chrome tests terminate with SIGTRAP.
Therefore this audit does not claim a passing clean suite.

## Execution topology and CI cost

`scripts/run-python-tests.py` creates one shared pytest process, then runs every
discovered `skills/*/_rtx/tests` directory in a separate pytest process. There
are 16 nested runtime test directories at this snapshot; precommit excludes the
installer and runs 15 nested groups after the shared group. The groups are
strictly serial. Neither the runner, `pytest.ini`, nor the workflow configures
pytest-xdist.

The benchmarked shared group failed in both named suites. Lines 104--108 of the
runner return immediately after that failure, so the named-suite measurements
did not exercise the serial topology described above. A complete baseline needs
a diagnostic keep-going mode, one result per atomic group, and a
capability-correct passing environment.

GitHub Actions does run three operating systems concurrently. Within each OS
job it serially runs validators, the seven portability tests, and the full
suite. The portability sentinel is deliberately repeated inside full, costing
about one second per OS job. More importantly, total CI compute cost is roughly
the sum across all three jobs even though user-visible latency is their maximum.

## Where the time goes

### Repository-wide standards queries

The clean refactor-node suite is 36% of the clean precommit wall time. In the
changing live tree it reached 97.17 seconds. Its slowest live test took 18.72
seconds unprofiled.

A cProfile run of that one test recorded 226,660,083 calls in 68.27 profiled
seconds. Six public queries caused 12 materializations and 12 extractions. The
profile attributed 41.07 cumulative seconds to 168 JSON-schema validations,
18.74 seconds to 1,572 YAML loads, and 9.66 seconds to six ownership-index
resolutions. The test asks distinct and useful contract questions; the repeated
closure preparation is the inflation.

### Core route smoke

The clean core suite's slowest test took 11.09 seconds. Its profile loaded the
repository blueprint graph twice (7.88 profiled seconds total) and spent 9.33
seconds in the already-batched child trace. The helper that enumerates smoke
cases recomputes a graph already needed by the test.

### Validators

Two validators account for 22.42 of 43.16 clean validator seconds:

| Validator | Wall s |
|---|---:|
| `standard_documents` | 14.16 |
| `user_docs_cover_blueprints` | 8.26 |
| `platform_neutral` | 4.52 |
| `contributor_docs_contract` | 3.29 |
| `cross_platform` | 3.15 |

The validator runner's isolated mirror setup was about 0.15 profiled seconds;
most cost is inside validator work. Optimizing isolation away would target the
wrong layer.

### Certifier and Git process fan-out

The live certifier suite took 25.01 seconds and observed 450 Git processes. A
profile of one representative test showed two certifications, four repository
graph loads, nine schema-bundle loads, 20 readiness checks, 402 `run_git` calls,
and 424 subprocess calls. The race, ordering, append-only, and stale-state cases
are distinct safety contracts; the better target is immutable graph/schema
reuse and batched Git queries.

### List-manager contracts

The live suite took 15.35 seconds. One description-contract test spent 3.98
profiled seconds loading and validating the full repository graph, including
218 blueprint declarations. The actual concurrent-writer tests cost about 1.15
seconds each and exercise real locking behavior. Preserve those; narrow the
contract fixture instead.

### Browser tests

The five browser files contain 31 cases. Twenty-four projection-arrangement
cases each start a fresh Chrome profile with a 3.5-second virtual-time budget.
The remaining cases use 3--7 second budgets, and one test starts Chrome twice.
Chrome could not execute in this sandbox, so real wall/CPU cost is unknown. The
31 sampled Chrome startups and source structure are sufficient to flag startup
amplification, but not to quantify its successful-run cost.

### Installer tests

The live installer suite took 29.08 wall-seconds and 32.71 CPU-seconds despite
eight environment failures. Two real CLI installation tests accounted for
21.44 seconds (74% of wall time). One phase-entry test mocks release building
but still reaches managed-`uv` bootstrap and attempts a network download. That
is an isolation gap, separate from the intentionally end-to-end install cases.

## Static quality inventory

The audited working tree, including the one benchmark-tool test added by this
audit, contains 59,510 test-code lines and 2,052 statically defined test
functions. Static analysis found 4,634 `assert` statements, 347 `raises`
contexts, 64 parametrization decorators, 77 skip sites, no xfails, 75
subprocess call sites, 17 network-like sites, nine parallel primitives, three
real sleeps, and 18 test functions over 100 lines.

Seven clusters have exactly identical normalized AST bodies. Three clusters are
the same empty-repository acceptance shape across validators; three are shared
OAuth transaction contracts across cloud-files and g-calendar; one is an
install lifecycle pair. Exact bodies are review leads, not proof of redundant
coverage: separate implementations may intentionally share a contract.

The current testing standard requires contract focus, change evidence,
boundary coverage, and coverage breadth. It does not yet define performance
budgets, tier ownership, overlap justification, benchmark metadata, or a rule
that expensive tests must avoid repeated immutable setup.

## Diagnosis

Verified:

1. The suite is overwhelmingly serial locally: CPU time and wall time are
   nearly identical for the end-to-end and expensive-suite measurements.
2. Repository-wide graph loading, YAML parsing, and schema validation are
   repeatedly performed inside individual tests and across tests.
3. Git subprocess fan-out is material: 505 observed Git processes in clean
   precommit and 402 calls in one profiled certifier test.
4. Browser process startup is repeated per case; successful cost needs a host
   where Chrome can run.
5. External integration tests do not fail closed as capability skips in this
   sandbox, so failed local runs are both noisy and artificially short.
6. Blind xdist parallelism exchanges compute and memory for latency. It does
   not remove the underlying work.

Not established:

- No coverage or mutation score was available, so this audit does not label any
  behavior assertion useless solely from duplication or test count.
- Successful browser and online installer cost is not measured here.
- Whole-suite and complete pre-commit-hook wall/CPU cost is not measured: the
  runner observations stopped after the shared group, and generators, gitleaks,
  validators, and nested-runtime groups were not one instrumented transaction.
- The changing live-worktree timings are not comparable across commits; only
  the immutable baseline and within-workload parallel experiments support
  numerical comparisons.

## Optimization order

1. Correct the baseline with a keep-going, per-group result and benchmark the
   complete hook, including generators, gitleaks, validators, and tests.
2. Establish collection identity, coverage, mutation, flake, overlap, and
   change-impact evidence before changing suite membership or deleting tests.
3. Prepare immutable standards/blueprint/schema state once, then project or
   query it many times. Preserve a separate freshness boundary for production.
4. Reuse repository graph state within route smoke, certifier batches, and
   contract tests; batch Git object/index queries where consistency permits.
5. Optimize validators against the same prepared repository snapshot.
6. Batch browser scenarios in one Chrome session while returning per-scenario
   failures; retain a small independent cold-start smoke.
7. Split hermetic installer policy tests from explicitly capability-gated
   online/real-CLI tests.
8. Schedule concurrency-certified shared buckets, atomic fresh-process runtime
   groups, validators, and browser work with core/RSS and exclusivity limits.
9. Require a warm-cache complete-hook p95 at or below 10 seconds, aggregate CPU
   at or below 80 seconds, exact outcomes, and full required CI fallback.

The detailed implementation sequence and acceptance thresholds are in
`docs/superpowers/plans/2026-08-04-test-performance-remediation.md`.
