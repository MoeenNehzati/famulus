# Thin Pytest Repository Runner Plan

**Goal:** Make repository checking simple, reliable, and fast by keeping `repo_checks.py` a thin wrapper around pytest. Pytest continues to collect and execute tests and validator items. The wrapper selects a profile, creates a small number of isolated pytest tasks, schedules them under one worker budget, and reports task outcomes.

**Performance hypothesis:** Most avoidable wall time comes from running existing isolated pytest groups serially. The first change will run those same groups concurrently without redesigning collection, validators, fixtures, or reports. Further abstraction is allowed only if this minimal change passes an early performance gate.

## Design Principles

1. Pytest owns test collection, node IDs, fixtures, parametrization, selection, deselection, plugins, and item-level reporting.
2. The wrapper does not maintain a second item inventory or result framework.
3. The profile resolver constructs one complete `list[CheckTask]` containing the validator session and every selected test group before execution. One coordinator receives that list once; validators are never run as a preliminary or separate phase.
4. Existing isolation boundaries remain intact:
   - Shared compatible tests remain one pytest task.
   - Each incompatible `skills/*/_rtx/tests` root remains its own pytest task.
   - Validators remain one pytest-backed session so staged-state handling, caches, graph preflight, and adapter behavior remain shared.
5. Existing discovery remains authoritative, including `tests/`, `hooks/tests/`, `skills/*/tests`, and `skills/*/_rtx/tests`.
6. Profiles are subtractive:
   - `full` runs every pytest test returned by the existing repository test discoverer and every pytest-backed validator returned by the existing validator registry.
   - `pre-push` removes docstring checks.
   - `precommit` removes docstring, browser, installation, and clean-tree-only checks.
7. Partial-file policy continues to use pytest node selectors or `--deselect`; it does not require pre-collecting every item.
8. Normal runs record only task status, duration, exit code, and pytest output. Detailed resource telemetry and exact node manifests are benchmark/certification features, not runtime requirements.
9. Concurrency intentionally weakens strict sequential fail-fast: after an observed failure, no new task starts, but already-running tasks finish and all their failures are reported. This is not exhaustive execution and requires no item-level cancellation protocol.
10. Prefer changes inside the existing repository-check module. Extract a scheduler module only if the implementation becomes materially clearer and easier to test.

## Minimal Runtime Model

```python
@dataclass(frozen=True)
class CheckTask:
    id: str
    argv: tuple[str, ...]
    slots: int
```

`CheckTask` is both the scheduling unit and the isolation boundary. There is no separate `CheckSpec`, inventory object, execution cohort, or universal item report.

Initial task types:

| Task | Boundary | Worker leases |
|---|---|---:|
| Shared tests | Existing compatible pytest roots | Number of xdist workers assigned |
| Skill runtime | One existing isolated `_rtx/tests` root | 1 |
| Validators | Existing complete validator pytest session | 1 |

Worker leases are a top-level pytest-worker budget, not a hard process or CPU limit. A single-process pytest task consumes one lease; an xdist task consumes the number of workers assigned to it. Total active leases must not exceed `--jobs`. The xdist controller is treated as coordination overhead unless benchmarks show it needs a lease. Certification separately rejects uncontrolled descendant-process oversubscription.

Browser tests remain in their current pytest group during the initial experiment. If a later `full` benchmark reproduces the observed Chrome memory spike, add the smallest measured browser concurrency limit then. Platform-native CI smoke commands remain outside this runner unless they are separately converted into pytest tests.

The validator task uses one private, non-recursive worker mode of the existing repository-check module. The coordinator launches it with `sys.executable` and observes only its output and exit status. Inside that worker, existing `_validator_snapshot.run_all()` continues to own index capture, mirror creation, isolated Git metadata, tracked-child execution, findings parsing/rendering, and cleanup exactly as today. No snapshot paths or payloads enter `CheckTask`. The worker maps outcomes to `0` for no findings, `1` for validator findings, and `2` for infrastructure or protocol failure, then exits without profile resolution or scheduler construction. This private mode is not a second public runner.

## Profile Policy

Keep profile policy as declarative data close to the current suite resolver. Apply it while constructing pytest arguments and tasks, not through a new collection pass.

Keep two policy mechanisms explicit: excluded discovery roots and pytest `--deselect` node selectors. Portability and partial-file exclusions retain their exact node selectors.

| Profile | Exemptions |
|---|---|
| `full` | None |
| `pre-push` | Docstrings |
| `precommit` | Docstrings, browser, installation, clean-tree-only |

Explicit validator inclusion continues to override a profile exemption. Explicit exclusion still wins. Preserve current public suite names and CLI spellings.

Public suite contracts remain:

- `tests`: all discovered ordinary pytest tests and no validators.
- `validators`: selected pytest-backed validators and no ordinary tests.
- `portability`: existing exact ordinary-test node selectors and no validators.
- `full`: `tests` plus `validators` in one queue.

`--jobs N` intentionally becomes the total top-level pytest-worker budget rather than the xdist count assigned to every task. Update CLI help and compatibility tests accordingly. Jobs greater than one require the declared `pytest-xdist` testing dependency and fail early with a clear message when it is unavailable.

## Phase 1: Freeze the Baseline

**Purpose:** Establish coverage and timing evidence without changing behavior.

1. Record the authoritative discovery roots returned by the existing discoverer.
2. Capture controlled node-ID manifests for `full`, `pre-push`, and `precommit` using pytest collection only for certification.
3. Record selected validator IDs and explicit include/exclude behavior.
4. Measure current duration for the validator session and every existing pytest execution group.
5. Record warm-cache precommit wall time and peak RSS at `--jobs 1`, `4`, and `8`.
6. Keep the existing 8-worker precommit result of approximately 110.77 seconds as the reference until replaced by a fresh paired baseline.
7. Determine whether repository checks depend on pytest's cache provider. Either disable it for both schedulers or assign deterministic task-specific cache directories under one invocation root; baseline and candidate must use the identical policy.
8. Record the commit, staged-state fingerprint, and tracked-working-tree fingerprint used for paired certification, or require a clean tracked working tree.
9. Confirm `pytest-xdist` is an explicit local and CI testing dependency before pooled runs.

**Deliverable:** One migration baseline containing exact coverage and per-group timing. It is not loaded during normal hook runs.

## Phase 2: Minimal Shared-Pool Experiment

**Purpose:** Test the performance hypothesis before building broader infrastructure.

1. Reuse the current suite resolver, discovery, validator adapter, and exact execution-group boundaries: one shared group containing every discovered non-`_rtx` target, one group per discovered `_rtx/tests` root, and one validator task. Phase 2 may change only process-group scheduling and the division of `--jobs` between shared xdist and isolated tasks.
2. Convert each existing process invocation into a `CheckTask`.
3. Add a small rolling `subprocess.Popen` coordinator that:
   - Starts the first task in the deterministic pending list that fits available worker leases, so a large blocked task does not leave usable leases idle.
   - Tracks active tasks and releases leases on completion.
   - At jobs `1`, runs validator-first without xdist and preserves exact serial ordering. At higher jobs, starts the validator task first, then admits ordinary test tasks.
   - Stops admitting new tasks after any task fails.
   - Lets already-running tasks finish, reports all of their failures, and chooses the process exit code deterministically by original task order with validator priority.
   - Redirects each task's combined output to a temporary file rather than an unread pipe. It prints task start/completion lines, replays pytest output once under a task header in deterministic order, then deletes the file. No asynchronous readers or persistent log protocol are added.
   - Records only task ID, duration, and exit code during normal runs.
   - Applies the cache policy selected in Phase 1 identically to sequential and pooled execution.
   - Starts every task in an isolated process group. On interruption, it stops admission, terminates active process groups, waits for cleanup, removes parent-owned temporary resources, and returns the conventional interrupted status. Windows process-tree behavior must be implemented before enabling pooling in Windows CI.
4. Build the complete validator-and-test task list before starting the coordinator. Use a deterministic static admission order chosen from baseline timings; do not add fairness or dynamic work stealing.
5. Keep the existing sequential scheduler available behind a temporary internal switch only for paired benchmarking and immediate rollback.
6. Do not change hooks, CI, standards, profile membership, browser grouping, native checks, output schemas, or validator internals in this phase.

**Required tests:** Lease accounting, deterministic first-fitting admission, no duplicate task execution, concurrent fail-fast behavior, validator-priority exit selection, private validator-worker non-recursion and outcome mapping, validator single-session execution, staged-index/dirty-tree/linked-worktree/unmerged-index safety, no caller-index mutation, interruption cleanup, and preserved `_rtx` isolation.

## Phase 3: Early Performance Gate

**Purpose:** Decide whether the thin pool solves the measured problem.

1. Use a tuning stage followed by fresh certification runs. During tuning, run three warm-cache interleaved or randomized paired repetitions at jobs `1`, `4`, and `8`.
2. At 4 jobs, compare shared/isolated splits of `3/1` and `2/2`. At 8 jobs, compare:
   - Shared pytest uses 6 leases; two isolated tasks may overlap.
   - Shared pytest uses 5 leases; three isolated tasks may overlap.
   - Shared pytest uses 4 leases; four isolated tasks may overlap.
3. Report every tested allocation, not only the winner, then select one simple deterministic allocation function for all job counts; do not add adaptive allocation.
4. Run fresh paired certification using only the selected allocation. Separate timing passes with process sampling disabled from resource passes measuring whole-tree RSS, CPU, processes, and threads. Apply the 25% gate only to timing passes and require the gain to exceed run-to-run spread.
5. Verify identical ordinary pytest node-ID multisets, validator pytest item-ID multisets including entry names, multiplicities, and profile-specific deselection manifests under controlled conditions. Keep validator manifest generation in certification helpers/tests, never normal `repo_checks.py` execution.

**Stop/go gate:**

- Minimum acceptable improvement: at least 25% faster than the paired 8-worker baseline.
- Current-reference threshold: approximately 83 seconds or less from 110.77 seconds.
- Target: 75 seconds or less.
- `--jobs 1` may not regress by more than 5%.
- Peak precommit RSS may not increase by more than 25% without explicit justification.
- Coverage must remain identical.

If the candidate fails this gate, stop the refactor. Profile the remaining critical path and revise task allocation or granularity; do not proceed by adding inventory or reporting abstractions.

## Phase 4: Simplify Profiles

**Prerequisite:** Phase 3 passes.

1. Express `full`, `pre-push`, and `precommit` as the subtractive exemption table above.
2. Keep node-level selectors for partial-file exclusions and portability.
3. Remove the precommit hook's redundant docstring exclusion after profile tests prove the same behavior.
4. Test exact profile relations and explicit validator include/exclude precedence.
5. Do not generate or reconcile a complete node inventory during normal runs.

## Phase 5: Targeted Performance Refinement

**Prerequisite:** Only perform work justified by Phase 3 timings.

1. Adjust static worker allocation between shared xdist and isolated tasks.
2. Preserve all current isolation boundaries. Split only the shared compatible-test task when measured startup or tail latency justifies it. Never coalesce `_rtx` roots without an explicit import-compatibility proof.
3. Add a browser concurrency cap only if a measured `full` run requires it.
4. Add no generic resource taxonomy, dynamic work stealing, fairness framework, or continuous process telemetry unless a measured problem requires it.
5. Reapply the Phase 3 coverage and performance gates after each refinement.

## Phase 6: Rollout and Authority Cleanup

**Prerequisite:** Scheduler and profile behavior are stable.

1. The early gate authorizes pooled `precommit` only. Before changing `full`, run a separate pooled `full` resource certification; if memory is unsafe, keep `full` sequential or add one measured browser limit.
2. Certify pooled `precommit` and `full` on Linux, then pooled `full` on macOS and Windows while retaining portability and native smoke steps separately. Only then make pooling the default for the certified suites.
3. Update precommit and pre-push hooks to invoke only their named profiles.
4. Preserve existing platform-specific portability and native CI checks; they need not move into `full`.
5. Update the active repository-check standard and active consumers to identify the unified runner as authoritative.
6. Repair active references to deleted runner paths; leave historical fixtures unchanged.
7. Retain the sequential fallback for one bounded rollout interval. Remove it in the next cleanup change only after default pooled `full` passes Linux, macOS, and Windows CI. Remove obsolete paths separately.

## Final Certification

1. Compare exact baseline and candidate ordinary node-ID multisets, validator pytest item-ID multisets, multiplicities, and deselection manifests for every public profile.
2. Run sequential and pooled modes with jobs `1`, `4`, and `8`.
3. Verify staged-index validator behavior, dirty-tree behavior, `_rtx` isolation, browser limits, explicit validator selection, and interruption behavior.
4. Perform five fresh paired warm-cache timing runs at jobs `1` and the selected default, plus separate whole-tree resource runs.
5. Require the performance, RSS, process/thread, effective-core, and coverage gates from Phase 3; reject gains caused by omitted work or uncontrolled oversubscription.
6. Classify pre-existing or environment-specific failures separately; do not hide them as scheduler results.

## Explicit Non-Goals

- Reimplementing pytest collection or reporting.
- Representing every pytest item as a repository-runner object.
- Running incompatible `_rtx` packages in one pytest interpreter.
- Splitting the validator session into independent validator processes.
- Introducing a universal task-result protocol.
- Changing fixture scope, test objectives, or coverage.
- Making exhaustive failure execution part of this performance refactor.
- Building a general resource scheduler.
- Scheduling platform-native smoke commands that are not pytest tests.
- Folding every test-looking historical, generated, scaffold, or opt-in artifact into `full`.

## Completion Criteria

- The runner remains a thin pytest wrapper with one small `CheckTask` abstraction.
- Tests and validators use one bounded worker-lease coordinator.
- Pytest remains authoritative for collection and item reporting.
- Existing `_rtx` and validator isolation semantics are preserved.
- Profiles are subtractive and covered by focused policy tests.
- Controlled manifests prove no test or validator coverage was lost.
- The default 8-worker precommit run is at least 25% faster, targets 75 seconds or less, and stays within the RSS gate.
- Additional machinery exists only where benchmark or correctness evidence required it.
