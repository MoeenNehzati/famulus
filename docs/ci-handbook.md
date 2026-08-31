# Continuous Integration Handbook

This handbook explains how continuous integration (CI) works in this
repository, why its platform coverage is arranged as it is, and how to debug it
without repeating expensive or misleading investigations. It complements the
[repository testing reference](./testing.md), which remains the command and
suite reference, and the `ci-debug` skill, which remains the executable
coordination workflow for exact-SHA diagnosis and repair.

The live implementation is authoritative when this handbook and code disagree:

- `.github/workflows/python-tests.yml` owns GitHub Actions triggers, matrix
  topology, job timeouts, setup, and sentinels;
- `repo_checks.py` is the only repository-check entry point;
- `src/officina/repository/checks/runner.py` owns suites, tasks, selection,
  execution order, worker policy, and timing reports;
- `src/officina/repository/checks/remote_macos_windows.py` owns the matrix
  identity expected by the remote runner;
- `test_support/browser.py` owns Chrome discovery and invocation;
- `docs/testing.md` documents local suites, repository views, and collection.

Update this handbook when any of those contracts changes. Do not use a dated
benchmark below as a substitute for inspecting the current workflow.

## The pipeline at a glance

The normal path is:

1. A developer runs an appropriate local repository suite.
2. The Git hook runs its staged or working-tree gate.
3. A pushed commit or pull request starts `Python Tests` on GitHub Actions.
4. Eight matrix elements run in parallel on Ubuntu, macOS, and Windows.
5. Each element uploads machine-readable repository-check timing evidence.
6. A CI repair run uses the exact pushed SHA, isolates a red matrix element,
   and probes the smallest selector that still reproduces its failure.
7. Targeted evidence is followed by a whole-element probe, then a complete
   exact-SHA matrix. When changes land on the default branch, the ordinary
   push-triggered workflow is the final integration proof.

Local, probe, matrix, and default-branch results answer different questions.
None can silently stand in for another.

## Local gates and repository views

The common local gates are:

```bash
python3 repo_checks.py --suite precommit
python3 repo_checks.py --suite pre-push
python3 repo_checks.py --suite full --verbose
```

`precommit` reads a temporary mirror of the Git index. Unstaged and untracked
files are absent. The other suites use the working tree unless a repository
view is explicitly selected. A new implementation or test therefore must be
staged before the canonical pre-commit gate can exercise it.

The local pre-push suite is broad but is not matrix proof. It cannot reproduce
all native runner behavior, it may use a different Python version, and Chrome
may skip when the local environment does not promise browser coverage. A local
green result means the local gate passed, not that Linux, macOS, and Windows
passed the exact pushed commit.

## CI triggers and candidate identity

`Python Tests` runs for:

- pushes to `master` or `main`;
- pull requests targeting `master` or `main`;
- manual `workflow_dispatch` requests made by the repository remote runner.

A dispatched matrix or probe carries a request ID, Git ref, and expected
40-character commit SHA. After checkout, the workflow compares `HEAD` with the
expected SHA before running tests. This prevents a moving branch from silently
testing a different candidate.

Always record four identifiers together during diagnosis:

- repository and workflow;
- remote ref;
- exact pushed SHA;
- GitHub Actions run ID.

If a branch is rebased or force-pushed, every older run remains evidence only
for its old SHA. Record the new SHA and start a new exact-SHA verification. Use
`--force-with-lease`, rather than an unconditional force, when rewriting an
authorized non-default branch so unexpected remote work is not overwritten.

## Repository tasks

The matrix is expressed in stable repository-check tasks rather than raw test
directories.

| Task | Responsibility | Execution policy |
| --- | --- | --- |
| `validators` | Repository and Officina structural validators | Parallel when the matrix supplies multiple workers |
| `tests:shared` | Browser-free, non-performance functional tests | Parallel with pytest-xdist work stealing |
| `tests:performance` | Load-sensitive dispatcher performance invariants | Serial and executed before pooled work in a full suite |
| `tests:browser` | HTML, SVG, graph projection, inspector, containment, and readability behavior in a real Chrome DOM | Serial, `--maxfail=1`, and a 30-second bound per Chrome invocation |
| `tests:portability` | Seven cross-platform boundary sentinels | Serial, outside the main shard |
| `native:keyring` | Native credential-store behavior | Serial on macOS and Windows |
| `native:scheduler` | Native recurring-scheduler behavior | Serial on macOS and Windows |

`tests:docstrings` remains selectable for focused local or probe use. The
matrix topology is intentionally smaller than the selectable-task inventory.

In a full suite, the runner orders performance first, pools validators with
shared tests, and runs browser tests afterward. The pooled invocation gives
validators and ordinary pytest items one xdist queue and one worker budget.
Chrome is kept out of that pool because concurrent browser processes made
virtual-time completion unreliable under repository load.

## Current matrix

The current matrix has eight elements:

| Operating system | Matrix task | Main evidence | Additional sentinels |
| --- | --- | --- | --- |
| Ubuntu | `combined` | Performance, pooled validators/shared tests, and browser tests | Portability |
| macOS | `validators` | Validators | None |
| macOS | `tests:shared` | Shared tests | Portability |
| macOS | `tests:performance` | Performance invariants | Native keyring and scheduler |
| Windows | `validators` | Validators | None |
| Windows | `tests:shared` | Shared tests | Portability |
| Windows | `tests:performance` | Performance invariants | Native keyring and scheduler |
| Windows | `tests:browser` | Complete browser behavior suite | None |

The Ubuntu `combined` timing report labels its pooled validators/shared phase
as `tests:shared`. That label is a reporting normalization, not proof that the
phase contains functional tests only.

The portability and native smoke steps use `always()` where configured. They
can produce evidence after the main shard is already red. Their later failure
is not automatically the cause of the earlier failure; treat each result as a
separate ledger entry.

### Why the operating systems differ

The matrix seeks meaningful platform evidence, not identical job shapes.

- Ubuntu runs the complete suite and provides one stable Chrome gate.
- Windows keeps validators, shared tests, performance tests, and browser tests
  separate. The browser shard has one worker, while shared tests retain four.
- macOS runs validators, shared tests, performance invariants, portability,
  keyring, and scheduler behavior. It does not gate Chrome rendering.

Hosted macOS Chrome was observed to produce the expected DOM and then fail to
terminate reliably. Accepting a timeout merely because stdout looked complete
would turn a process-lifecycle failure into a false pass. Repeated launch flags,
a Chrome-for-Testing preference, and output salvage were investigated during
the 2026-08-21 repair, but they accumulated special cases without establishing
a reliable lifecycle contract. The maintainable resolution was to preserve
complete browser behavior coverage on stable Ubuntu and Windows hosts and keep
macOS coverage focused on behavior it can gate reliably.

## Browser coverage

Browser tests exist because renderer unit tests cannot prove behavior that
depends on a real Document Object Model (DOM), browser layout, SVG geometry,
event dispatch, or JavaScript mutation. They cover graph projection and
arrangement behavior, inspector and Bezier interactions, containment edges,
node readability, and the rendered visualization contract.

Every `*_browser.py` module must:

- call `require_chrome()`, so a promised browser gate cannot pass by skipping;
- call the shared `run_html()` helper, so discovery, flags, temporary profiles,
  UTF-8 decoding, and timeout behavior remain consistent;
- use portable temporary paths rather than host-specific browser URLs.

The browser task itself supplies serialization through `jobs=1`. Do not add a
second filesystem lock, xdist group, or fixture-level scheduler. Multiple
serialization mechanisms obscure ownership and can deadlock or waste time.

`FAMULUS_REQUIRE_BROWSER=1` is set only for the Ubuntu combined job, the
Windows browser job, and browser-bearing probes. Elsewhere a missing Chrome may
skip with documented alternate renderer coverage. A browser gate with that
environment variable must fail if Chrome is absent.

Each Chrome process has a 30-second wall-clock timeout. Exceeding it is red even
if partial or apparently complete DOM text was captured. A successful browser
assertion includes clean process completion.

## Timeouts and failure boundaries

The current limits are:

| Boundary | Limit | Meaning |
| --- | ---: | --- |
| Complete matrix job | 20 minutes | Stops an unhealthy OS/task element while leaving room above its healthy duration |
| Targeted probe job | 10 minutes | Bounds an isolated diagnostic run |
| One Chrome invocation | 30 seconds | Bounds browser startup, rendering, DOM dump, and termination |

A timeout is a failure class, not an instruction to enlarge the outer timeout.
First identify the last completed selector and the process that remains alive.
Then reproduce the smallest containing selector with a bound appropriate to
that subprocess. Verify cleanup of the entire process tree and inherited
resources before accepting a repair.

The previous 60-minute job limit allowed a normally sub-10-minute suite to sit
unproductively for most of an hour. The 20-minute limit leaves substantial
headroom while turning a hang into useful evidence sooner. Do not reduce it
from one successful observation alone; use the rolling benchmark procedure in
[Potential improvements](#potential-improvements).

## Artifacts and timing evidence

Every matrix element uploads `.repo-checks/*.json`, even after failure. The
schema-version-1 timing report contains:

- task wall time;
- pytest setup, call, and teardown totals by file;
- item counts and failure information used by the remote report.

Per-file timing totals are cumulative test durations. With xdist they overlap
across workers and can exceed the shard's wall time. Use them to identify
expensive test families, not to reconstruct elapsed time by addition.

GitHub job duration includes checkout, language setup, dependency and CLI
installation, repository checks, artifact upload, and post-job cleanup. To
separate useful test time from setup variance, compare GitHub step timestamps
with task wall times in the uploaded artifacts.

## Debugging workflow

Use the `ci-debug` skill when CI is red. This handbook supplies project context;
it does not replace the skill's failure ledger, isolated repair elements, or
exact-SHA orchestration.

### 1. Establish authority

Before changing code:

1. Verify the current branch and worktree status.
2. Verify the remote ref and exact pushed SHA.
3. Open the run for that SHA, not merely the newest run with a familiar branch
   name.
4. Download its repository-check evidence.
5. Record every red matrix element and sentinel independently.

Do not diagnose from a stale checkout, a different worktree, an installed copy
of the repository, or a run for a superseded SHA.

### 2. Classify before repairing

Useful failure classes are:

| Class | Typical evidence | First response |
| --- | --- | --- |
| Product regression | Deterministic assertion or validator failure on multiple hosts | Reproduce the smallest node or file locally, then probe its matrix element |
| Platform portability | Failure exists only on one native OS | Inspect path, encoding, socket, process, timezone, file-mode, or native API boundaries |
| Performance budget | Correct output exceeds a calibrated threshold | Measure the affected platform; change only its budget if the healthy distribution supports it |
| Browser lifecycle | Chrome starts, hangs, times out, or leaves descendants | Bound one browser selector; inspect process completion and cleanup, not only DOM output |
| Infrastructure/setup | Checkout, network install, authentication, or hosted-runner service fails | Retry the unchanged operation or narrowly escalate host access before editing product code |
| Coverage-policy failure | A task skips, disappears, or runs on the wrong host | Repair category routing and its policy test, not one test filename |

Sandbox errors such as repository unavailability, loopback `PermissionError`,
or SSH owner/permission rejection are not evidence of a source regression.
Retry the unchanged read, socket, or GitHub operation with narrowly scoped host
access before modifying code.

### 3. Shrink the reproduction

For each red element:

1. Prefer the exact failing pytest node.
2. If the report lacks a node, use the smallest containing test file.
3. Use the same OS and repository task as the failure.
4. Use one worker for suspected concurrency or lifecycle failures.
5. Keep unprobed failures in the ledger.

A local focused command has this shape:

```bash
python3 repo_checks.py --suite full --task tests:browser \
  --selector tests/example_browser.py::test_example --jobs 1
```

Selectors require an explicit task. Do not rerun selectors already known to
pass, and do not launch a complete matrix merely to learn whether one edited
test still fails.

### 4. Repair the category

Fix the narrowest root cause, then add the simplest durable guard that prevents
the same category:

- fixture behavior for a fixture race;
- shared browser-helper behavior for all browser callers;
- task routing for a missing category;
- one policy test for the matrix contract;
- a platform-specific budget only when platform measurements justify it.

Avoid instance lists when the category can be discovered mechanically. Avoid
layers of retries, timeout salvage, locks, flags, and host exceptions that
cannot state one coherent contract.

### 5. Escalate evidence in order

The proof ladder is:

1. smallest failing node or file;
2. every known selector in that failure class;
3. the whole affected matrix element;
4. the complete matrix for the exact pushed SHA;
5. after integration, the ordinary default-branch push workflow.

Targeted green is not whole-element green. Whole-element green is not matrix
green. A matrix on an unpushed or superseded SHA is not integration proof.

If the same failure set repeats without a relevant code or environmental
change, stop rerunning and report a concrete blocker. Repetition is not new
evidence.

## Common pitfalls and lessons

### Local success is not native matrix success

The 2026-08-21 repair began from a local pre-push result with thousands of
passing tests and a passing local browser phase. Native CI still exposed a
Windows loopback reset and a macOS cold-start budget mismatch. Preserve local
gates, but require native exact-SHA evidence for native claims.

### Do not turn a hang into a pass

A Chrome process that emits expected DOM and then fails to exit is still a
lifecycle failure. Salvaging stdout from `TimeoutExpired` hid the distinction
between correct rendering and clean completion. The final design rejects that
salvage path.

### Do not accumulate speculative Chrome workarounds

Suppressing first-run behavior, adding flags, preferring another Chrome build,
or extending timeouts can each be a valid isolated hypothesis. Applying them
serially without a confirmed cause produces a fragile browser abstraction.
When one host cannot reliably satisfy the lifecycle contract, preserve the
same behavior coverage on stable hosts and keep the unreliable host's other
native coverage explicit.

### One scheduler should own serialization

The browser suite briefly had runner serialization, xdist grouping, and a
filesystem lock. Only the runner's one-worker task was necessary. Redundant
locks complicate cleanup and can create their own stalls.

### Platform budgets need platform measurements

Linux timing is not a universal performance budget. A prior repair measured a
normal macOS cold-start median of 132.81 ms, kept Linux at 125 ms and Windows at
175 ms, and changed only macOS from 125 ms to 150 ms. Change budgets from
healthy platform distributions, not by copying the slowest host's limit to
every platform.

### Expected connection resets can be fixture behavior

An intentionally oversized OAuth callback caused Windows to reset the sender
connection before the fixture sent its later valid callback. The application
was waiting correctly; the test driver had terminated early. The repair made
the fixture tolerate the expected reset so it could continue its scenario.
Distinguish subject failure from fixture-control-flow failure.

### `always()` creates secondary evidence

A portability or native smoke may execute after the main shard fails. Do not
attribute the job's first failure to the last red step. Read timestamps and
artifacts in execution order.

### Keep unrelated maintenance outside the repair

Do not regenerate blueprints, issue certificates, rewrite standards, or fix
unrelated documentation merely because a CI investigation encounters them.
Add an independently failing check to the ledger or defer it to its owning
workflow. Browser debugging does not authorize certification work.

### Optimize the critical path, not the loudest anomaly

A three-minute setup job can be wasteful without delaying the workflow when a
ten-minute job runs in parallel. Report both end-to-end wall time and aggregate
runner occupancy. Speed work should identify which job determines completion.

## Maintaining CI safely

When changing matrix policy:

1. Update the workflow and `EXPECTED_MATRIX` together.
2. Update policy assertions in `tests/test_repository_test_checks.py` and
   transport assertions in `tests/test_repo_checks_remote.py`.
3. Keep category discovery in the runner; do not maintain parallel filename
   inventories in workflow YAML.
4. Preserve `FAMULUS_REQUIRE_BROWSER=1` on every browser-promising route.
5. Preserve one-worker browser execution and the shared helper policy tests.
6. Update `docs/testing.md` and this handbook.
7. Run focused policy tests, the local pre-push gate, an exact-SHA complete
   matrix, and the normal default-branch workflow when the change lands.

When adding or moving a test, first decide which behavioral category owns it.
The prevention mechanism should recognize the category rather than enumerate
the particular failure that prompted the change.

## Potential improvements

This section records optimization hypotheses, not approved changes. Each
proposal should begin with measurement and preserve the behavior boundary the
test exists to exercise.

### Benchmark timeline

The relevant observations from the 2026-08-21 repair were:

1. Healthy per-OS work normally completed in less than ten minutes, while the
   outer job timeout was 60 minutes. A stuck browser or descendant process
   could therefore occupy a runner for most of an hour without new evidence.
2. Chrome invocation was bounded at 30 seconds and stalls were made explicit
   failures. The outer matrix and probe limits became 20 and 10 minutes.
3. Several macOS Chrome mitigations were tried, but the repeated symptom was
   correct DOM output followed by unreliable process termination. An
   architecture audit replaced those layered mitigations with stable-host
   browser coverage.
4. Exact-SHA run
   [32521019328](https://github.com/MoeenNehzati/famulus/actions/runs/32521019328)
   at commit `8e62d49c472f58821e5d8cbc543908b0a3bb8e6c` passed all eight elements in
   9 minutes 56 seconds. Aggregate job occupancy was 25 minutes 58 seconds.

That successful run is a snapshot, not a statistical baseline:

| Matrix element | Job wall time | Recorded checks | Setup and other overhead |
| --- | ---: | ---: | ---: |
| Ubuntu combined | 4m56s | 4m35s | 21s |
| macOS validators | 49s | 21s | 28s |
| macOS shared | 2m43s | 2m22s | 21s |
| macOS performance/native | 40s | 9s | 31s |
| Windows validators | 1m17s | 29s | 48s |
| Windows shared | 9m49s | 8m41s | 1m08s |
| Windows performance/native | 3m17s | 14s | 3m03s |
| Windows browser | 2m27s | 1m40s | 47s |

The Windows shared element was the critical path. Browser execution took 1m19s
inside Ubuntu combined and 1m40s on Windows; under healthy conditions it was
not the dominant speed bottleneck.

### Rolling benchmark procedure

Before changing timeouts or optimizing a shard:

1. Use `scripts/benchmark-test-suite.py` for controlled local cold- or
   warm-cache measurements; it delegates discovery and policy to the checkout's
   `repo_checks.py` instead of duplicating the suite.
2. Collect at least ten successful CI runs with the same matrix shape and runner
   images.
3. Record workflow wall time, every job's wall time, setup-step time, task wall
   time, and the slowest per-file cumulative totals.
4. Calculate median and p95 separately for each OS/task pair. Do not pool
   operating systems.
5. Mark runs with GitHub service or package-registry incidents instead of
   silently treating them as product performance.
6. Reset the comparison window after a matrix, dependency, runner-image, or
   major test-inventory change.
7. Set outer timeouts above the healthy p95 while keeping subprocess timeouts
   close to the operation they bound.

For example, a controlled local task benchmark can be started with:

```bash
scripts/benchmark-test-suite.py --repo . --suite full \
  --task-id tests:shared --output /tmp/famulus-shared-benchmark \
  --runs 10 --cache cold --jobs 4 --measure-resources
```

### Bottlenecks, hypotheses, and remedies

| Area | Measured evidence | Hypothesis | Confidence | Simple next step | Plausible effect |
| --- | --- | --- | --- | --- | --- |
| Windows shared certification tests | Shared work took 8m03s; `test_certifier.py` contributed 12m21s of cumulative per-test time across workers | Repeated Git, filesystem, hashing, or subprocess setup is amplified on Windows | High that this is the main target; medium on cause | Count expensive operations per test and profile fixture setup before refactoring; reuse only immutable/resettable preparation | 30s-2.5m is plausible; larger gains need evidence |
| Windows portability sentinel | 38s versus 2-4s on Ubuntu/macOS | One or more sentinels exercise Windows process or filesystem boundaries repeatedly | Medium | Inspect its per-test report and optimize only the dominant boundary | At most about 38s on the critical path |
| Per-job CLI installation | One Windows performance job spent 2m27s installing CLIs while its checks took 14s; other installs took 5-19s | Transient registry/network variance, plus installation on shards that may not all consume both CLIs | High for wasted runner time; low for workflow wall time | Audit which tasks import or execute each CLI; skip installation only where absence cannot weaken the test | Up to roughly 2m20s runner occupancy in an anomalous job; little end-to-end gain |
| Windows shared setup | The critical job had about 1m08s outside recorded checks | Checkout and repeated dependency/CLI setup are slower on Windows | Medium | Break out rolling setup medians before adding caches or combining jobs | Likely 10-40s if a stable repeated cost exists |
| Browser projection arrangements | About 43s of 79s on Ubuntu and 54s of 100s on Windows browser execution | Many real-browser cases pay independent Chrome startup cost | High on location; medium on safe remedy | Determine whether cases can share one HTML scenario without losing isolation or failure attribution | Browser job could improve, but current workflow wall time would barely change |
| Performance/native shards | macOS performed 9s of checks in a 40s job; Windows performed 14s in a 3m17s anomalous job | Isolation is diagnostically valuable but has a high setup-to-test ratio | High | Keep isolation unless rolling runner cost justifies a simple merge with an existing non-critical shard | Runner-cost reduction, normally no critical-path reduction |

The first optimization target should be Windows shared certification behavior.
Every minute removed from that job currently removes approximately one minute
from workflow wall time, until it approaches the Ubuntu combined duration of
about five minutes. Chrome optimization should remain secondary unless rolling
data shows a regression or lifecycle failures return.

Do not introduce a cache, retry system, persistent browser pool, or new
scheduler merely because it might be faster. Prefer removing duplicated work,
making existing category ownership explicit, and measuring again.
