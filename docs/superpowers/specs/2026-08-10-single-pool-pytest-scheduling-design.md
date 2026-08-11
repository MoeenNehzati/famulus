# Single-Pool Pytest Scheduling Design

> **Historical design:** The selected `loadgroup` browser policy remains in
> use, but the outer scheduler and separate validator/runtime boundaries in this
> document were later removed. The current architecture is
> [Native Pytest Repository Runner Design](2026-08-10-native-pytest-runner-design.md).

## Goal

Reduce canonical full-suite wall time and raise useful CPU occupancy without
splitting ordinary and browser tests into separate pytest processes. Preserve
the current test inventory, assertions, browser serialization policy, isolated
skill-runtime processes, validator lifecycle, and serial execution behavior.

Wall time is the acceptance metric. Average effective cores is diagnostic: the
main pytest phase should approach the eight-slot runner budget where runnable
work exists, but I/O, subprocess waits, collection, startup, and the suite tail
need not consume eight CPU cores continuously.

## Diagnosis

The full suite currently uses xdist `worksteal` for ordinary tests. Its
`tests/conftest.py` autouse fixture serializes every `*_browser.py` test through
one invocation-local lock. Work stealing may assign browser items to several
workers even though only one can pass the lock. Those workers wait instead of
running eligible ordinary tests.

The uncommitted fixed-lane experiment reduces that interference, but it creates
two pytest pools, repeats collection, and permanently reserves workers for each
lane. One of the two browser-lane workers can still wait on the one-browser
lock. The experiment is evidence that browser scheduling matters, not the
desired final architecture.

## Approaches considered

### 1. One xdist loadgroup pool — selected

Mark every Chrome-backed item with the same `xdist_group("browser")` marker and
run browser-containing suites with `--dist=loadgroup`. Xdist treats the browser
items as one work unit assigned to one worker. The remaining workers execute
ordinary items, and the browser worker can accept ordinary work after completing
its group.

This directly represents the existing one-browser-at-a-time constraint, removes
blocked browser workers, uses an established xdist scheduling mode, and requires
no custom scheduling implementation.

### 2. Custom resource-aware xdist scheduler — deferred

A custom scheduler could track resource tokens and allow a configurable number
of concurrent browser items. It would be appropriate if the repository later
permits more than one Chrome test at a time or develops several overlapping
resource classes. It is unnecessary while the canonical fixture deliberately
serializes Chrome, and it would duplicate nontrivial xdist queue, crash, restart,
and shutdown behavior.

### 3. Separate browser pytest process — rejected

The fixed general/browser lane is simple and measured better than unconstrained
work stealing, but it duplicates collection and prevents idle capacity from
crossing lane boundaries. It remains a rollback experiment, not the target.

## Architecture

The repository coordinator retains one bounded task queue. With `--jobs 8`, the
full suite initially admits:

- one validator process with one slot;
- one `tests:shared` pytest process with six xdist workers;
- one isolated skill-runtime pytest process with one slot.

There is no `tests:browser` task. The shared pytest process owns both ordinary
and browser items.

Every browser module declares the module-level marker
`pytest.mark.xdist_group("browser")`. `pytest.ini` registers the marker so
single-process pytest remains warning-free even when xdist is unavailable.

Suite scheduling is selected by inventory:

- `full`, `tests`, and `pre-push` include browser tests and use
  `--dist=loadgroup` when their shared group has more than one worker;
- `precommit` excludes browser tests and retains `--dist=worksteal`;
- `portability` and one-worker execution retain their existing behavior;
- isolated `skills/*/_rtx/tests` processes remain one-worker tasks.

The browser lock fixture remains as a defensive execution constraint. It is
uncontended under canonical `loadgroup`, but it continues protecting direct or
noncanonical invocations that select browser files with another distribution
mode.

## Data flow

1. Suite policy resolves the same test targets and deselections as before.
2. The runner builds one shared pytest command with its existing six-worker
   lease at an eight-slot budget.
3. Browser-containing parallel suites select `loadgroup`; other parallel suites
   select `worksteal`.
4. Each xdist worker collects the same items and observes the same browser group
   markers.
5. Xdist assigns the complete browser group to one worker and dynamically feeds
   ordinary work units to all available workers.
6. The outer coordinator continues admitting validators and isolated runtime
   tasks under the same global slot budget.

## Failure and compatibility behavior

- `--jobs 1` adds no xdist distribution arguments and browser tests remain
  ordinary serial pytest items.
- Missing xdist continues to fail before task construction only when parallel
  execution was requested.
- Browser skips, failures, output, temporary profiles, and assertions are
  unchanged.
- The lock timeout remains a visible pytest failure for a noncanonical run that
  violates browser serialization.
- Fail-fast task admission, active-child completion, staged validator mirrors,
  performance-test isolation, and platform-specific suite policy are unchanged.
- The benchmark harness continues resolving `tests:shared`; the experimental
  `tests:browser` task disappears.

## Implementation scope

The implementation will:

1. Revert only the uncommitted fixed browser-lane runner, test, benchmark, and
   documentation edits.
2. Add the browser xdist group marker to the five existing browser modules and
   register it in `pytest.ini`.
3. Let pytest argument construction select `loadgroup` only for parallel suites
   that include browser tests.
4. Add task-construction tests for full, pre-push, precommit, and serial modes.
5. Assert that every discovered `tests/**/*_browser.py` path is present in the
   exact browser-marker inventory; runner command tests cover distribution mode,
   worker leases, shared-task shape, and serial behavior without a nested pool.
6. Update `TESTING.md` to document the single shared pool and marker contract.

No test files are split, no production behavior is changed, and no historical
timing data is required by the scheduler.

## Verification and measurement

Verification proceeds in increasing cost:

1. Red/green focused tests for pytest arguments, task inventory, marker
   registration, and benchmark task resolution.
2. Exact discovery-to-inventory equality plus the five per-module browser-marker
   cases, with no nested pytest/xdist process in the focused scheduler suite.
3. A matched small-workload comparison against the current unified
   `worksteal` command and the fixed-lane experiment, using identical cache and
   environment conditions.
4. One canonical unrestricted diagnostic full run to record wall time, average
   effective cores, peak cores, task durations, failures, and fail-fast effects.
5. A clean-worktree repeated full benchmark before making a suite-level speed
   claim.

The candidate is accepted when it preserves collection and outcomes, removes
the separate browser task, improves or matches the measured small-workload wall
time, and raises main-phase useful occupancy without increasing browser
flakiness. A dirty or failing full run is diagnostic only and cannot certify
whole-suite wall time.
