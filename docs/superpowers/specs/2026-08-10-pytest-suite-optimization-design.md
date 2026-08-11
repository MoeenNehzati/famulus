# Pytest Suite Optimization Design

## Goal

Reduce the wall time of the repository's canonical Python test suites by using
pytest's lifecycle and collection features wherever they remove real repeated
work, without changing what is tested, weakening subprocess boundaries, or
altering diagnostics. The pre-commit suite is the primary optimization target;
the full suite is a required no-regression and correctness gate.

This is a whole-suite effort. The earlier validator-performance plan is a
historical record of narrower work, not the implementation plan for this pass.

## Correctness before measurement

Two known regressions must be corrected before performance results are accepted:

- `validators/standard_documents.py` must not share traversal-dependent cached
  findings across top-level roots. Cycle findings depend on the traversal stack,
  so a cache keyed only by document path can change prefixes and cycle paths.
  The cache must return to traversal scope unless a separate implementation can
  prove diagnostic equivalence for every calling context.
- `skills/list-manager/_rtx/tests/test_lists.py` must remain directly
  collectable through the repository's supported focused-test workflow. A speed
  refactor is invalid if the canonical runner passes only because it supplies an
  import path that a focused invocation lacks.

Any performance ledger entry based on the invalid cross-root cache must be
removed or replaced with a matched measurement after the correction.

## Optimization policy

The suite will be optimized in this order:

1. Measure repeated setup through the canonical runners.
2. Move genuinely shared preparation to the narrowest correct pytest fixture
   scope.
3. Use pytest parametrization, factories, temporary-path fixtures, and automatic
   restoration facilities to reduce harness work while retaining independent
   cases and useful failure identities.
4. Keep test modules small enough for natural parallel distribution.
5. Change xdist scheduling only when canonical profiling proves that worker-local
   fixture duplication is a material remaining bottleneck.

Fixtures are not a target by themselves. A fixture is appropriate only when it
expresses a real preparation lifetime, dependency, factory, or cleanup boundary.
Repeated work within one test item should be hoisted inside that item or its
production scan rather than hidden behind an ornamental fixture.

## Fixture and pytest feature rules

### Scope selection

- Function scope remains the default for mutable inputs and isolated state.
- Module scope is used for expensive, read-only preparation shared by tests in
  one module.
- Session scope is used only when preparation is valid for the entire pytest
  process. Under xdist, this means once per worker, not once for the whole run.
- A broad fixture must not return shared mutable state directly. Tests receive an
  immutable value, a factory, or an explicit copy whose cost is lower than the
  preparation it replaces.
- `tmp_path_factory` is preferred when a module or worker can safely reuse an
  expensive prepared directory. Ordinary `tmp_path` remains the default when a
  test mutates its filesystem fixture.

### Collection and cleanup

- Parametrization replaces structurally duplicated test functions when cases
  have the same setup, action, and assertion shape. Case IDs must preserve clear
  failure reporting.
- `monkeypatch` or fixture finalizers restore process state, environment
  variables, registries, and module attributes. Tests must not rely on ordering
  to repair state.
- Factory fixtures create fresh mutable objects from cached immutable source
  data when tests need independent mutations.
- Subprocess tests remain at representative public-boundary cases where process
  startup, imports, encoding, signals, or installed entry points are part of the
  contract. Pure internal branches may call a Python entry point directly when
  an existing subprocess test still covers the real boundary.

## Parallel execution

The canonical shared suite initially retains xdist `worksteal`. Module- and
session-scoped fixtures are worker-local, so their effective construction count
must be measured under the canonical worker allocation rather than inferred
from a serial focused run.

Test files should remain cohesive and modest in size so xdist can distribute
them naturally. `loadfile`, `loadscope`, or xdist groups may be introduced only
for a measured case where all of the following hold:

- duplicated worker-local setup is material in canonical wall time;
- grouping reduces that duplication;
- reduced parallelism does not offset the gain;
- isolation, ordering, and failure behavior remain unchanged.

Scheduler changes are therefore an evidence-triggered exception, not the main
optimization mechanism.

## Audit and refactor workflow

Each test area is processed one at a time:

1. Record its canonical task, file, item count, setup dependencies, subprocess
   boundaries, and current fixture scopes.
2. Count expensive preparation calls in both focused execution and the canonical
   parallel run.
3. Characterize outputs and side effects before changing the harness.
4. Apply the smallest pytest-native refactor that removes repeated work.
5. Run focused behavioral tests and the owning canonical task.
6. Keep the change only if it preserves behavior and improves the relevant
   canonical metric, or if it provides a clear maintainability benefit without
   measurable regression.

The audit list is a living execution ledger, not evidence of improvement by
itself. It records unchanged files and the reason they were left alone as well
as successful refactors.

## Measurement contract

Baseline and candidate measurements must use clean, reproducible repository
snapshots with identical code except for the candidate batch. Dirty-tree guard
failures and incomplete fail-fast runs are not whole-suite timings.

The benchmark matrix covers both canonical suites at one and default job counts:

| Suite | Jobs | Purpose |
| --- | ---: | --- |
| pre-commit | 1 | expose serial setup and subprocess costs |
| pre-commit | default (currently 8 on this host) | primary wall-time target |
| full | 1 | detect serial regressions and hidden setup costs |
| full | default (currently 8 on this host) | correctness and no-regression gate |

Each accepted batch uses the same command, environment, worker allocation,
cache condition, repetition count, and summary statistic before and after. At
least one warm-up and five recorded green runs are required for suite-level
claims. Raw samples, exit status, item counts, skips, deselections, and worker
counts are retained in the audit ledger.

Direct per-file timings and fixture call counts are diagnostic evidence only.
They do not establish a suite improvement unless the canonical runner also
improves. The combined refactor batch must reduce default-job pre-commit wall
time. The full suite must remain green and show no material regression at either
job count.

## Behavioral equivalence and failure handling

For every changed test area:

- the same production interfaces, branches, and externally observable effects
  remain covered;
- assertions are not removed, broadened, or replaced with weaker smoke checks;
- expected diagnostics, ordering, and exception boundaries are preserved where
  they are part of the contract;
- skipped and platform-specific cases continue to follow the repository's
  existing marker and canonical-runner policy;
- a fixture setup error remains visible as an error for every dependent case,
  rather than being swallowed or converted into a pass;
- shared preparation does not leak mutation between tests or workers;
- focused test invocation remains supported wherever it was supported before.

If equivalence is uncertain, the existing implementation stays in place until a
characterization test resolves the uncertainty.

## Deliverables

The implementation pass will produce:

- a corrected, green starting point for the known cache and collection issues;
- a repository-wide test-file audit ledger with measured decisions;
- fixture-first refactors applied one test area at a time;
- focused and canonical verification evidence for each accepted batch;
- matched one-job and default-job before/after results for pre-commit and full
  suites;
- an updated concise performance report that distinguishes proven canonical
  gains from direct or serial observations.

The work stops when no remaining repeated setup is material under the canonical
runner, or when removing it would require changing the behavior under test.
