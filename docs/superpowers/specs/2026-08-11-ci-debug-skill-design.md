# CI Debug Skill Design

## Goal

Create a repository-owned `ci-debug` skill that makes remote CI repair a
repeatable, evidence-driven workflow. The skill must isolate the smallest
failing boundary, repair only that boundary, prove the repair under the same
remote conditions, and then expand verification through the complete GitHub
Actions matrix before integration.

## Problem

The current workflow is useful as a certification gate but inefficient as a
debugger:

- every `master` push starts the full operating-system matrix;
- a debugging branch has no manual, parameterized entry point;
- the fixed repository tasks cannot select one test path or node;
- hosted runners disappear without preserving structured failure state;
- JUnit data produced internally by the repository runner is temporary;
- raw job logs are large, sometimes unavailable through `gh run view
  --log-failed`, and do not record important state such as the path that made a
  supposedly clean repository dirty; and
- a repair cannot be certified on its branch with the exact canonical matrix
  before it reaches `master`.

The August 11 Windows failure is representative: four jobs passed, while the
shared Windows task failed after seven minutes because a migration test found
its repository dirty. The job did not preserve the dirty paths, worker history,
or a serial rerun, so the runner vanished before the failure could be
classified as deterministic, order-dependent, parallel, or platform-specific.

## Chosen Architecture

The solution has four layers with distinct ownership.

### 1. `ci-debug` instruction skill

`skills/ci-debug/SKILL.md` owns the decision procedure and safety policy. It
directs an assistant to:

1. capture a failed run as an incident;
2. select the smallest remote reproducer;
3. reproduce before editing when the evidence permits;
4. change only files implicated by the evidence;
5. rerun the exact reproducer under the original conditions;
6. expand verification in ordered steps; and
7. integrate only after a complete branch matrix passes.

The skill is not a general GitHub helper. It is specifically a CI failure
isolation, repair, and recertification workflow. Before its implementation is
authored, `skill-maker` must retrieve the applicable canonical instruction and
Python standards and their pinned closures.

### 2. Deterministic `ci-debug` runtime

The skill-owned Python runtime owns operations that should not be improvised by
an assistant:

- start or resume an incident from a workflow run;
- resolve run, job, attempt, branch, and commit metadata;
- compute a stable failure fingerprint from failed nodes, exception type, top
  repository frame, operating system, and task;
- validate branch, operating-system, suite, selector, worker, repeat, and
  diagnostic-profile inputs;
- dispatch targeted or full workflow runs through `gh` without shell
  interpolation;
- watch a run and retrieve only the relevant jobs;
- download, validate, and summarize diagnostic artifacts;
- update the incident record atomically; and
- generate a redacted runner-side diagnostic bundle.

The runtime exposes small machine interfaces for incident start, dispatch,
inspection, and artifact collection. Exact registered interface declarations
must follow the standards returned by `skill-maker`; the behavior above is the
required contract.

### 3. Canonical repository runner

`repo_checks.py` remains the sole source of truth for repository suites,
phases, xdist policy, validator selection, and repository views. `ci-debug`
must call it rather than maintaining a second test inventory.

The runner gains bounded diagnostic selection:

- zero or more tracked test paths or pytest node IDs;
- an optional pytest keyword expression passed as one argv value;
- an explicit worker count;
- an outer repeat count for reproducing intermittent failures; and
- a durable diagnostic directory for JUnit, timing, and phase records.

Selectors are data, not shell fragments. Their file portion must resolve to a
tracked test file inside the repository and belong to the selected suite.
Arbitrary pytest options and arbitrary environment assignments are rejected.

### 4. GitHub Actions adapter

The existing Python workflow gains a `workflow_dispatch` entry point. Push and
pull-request events always select the complete canonical matrix. Manual events
select either one validated debugging target or the same complete matrix.

This keeps certification and debugging on the same checkout, setup, dependency,
and test execution path. A pushed `codex/ci-debug/<incident>` branch can run one
Windows test repeatedly without triggering the automatic `master` push matrix,
then invoke the full matrix manually before integration.

## Incident Record

Each repair has a local record under `_build/ci-debug/<incident-id>/` and a
matching remote artifact. `_build` remains untracked. The record contains:

- incident identifier and status;
- base and current branch, ref, and commit SHA;
- source run, attempt, job, operating system, and runner image;
- selected suite, test targets, condition profile, workers, and repeat count;
- failure fingerprint and failed node IDs;
- diagnostic artifact names and run URLs;
- repair commits attempted;
- results at each verification level; and
- final full-matrix result.

Statuses are `captured`, `reproduced`, `repairing`, `target-green`,
`scope-green`, `matrix-green`, `integrated`, and `confirmed`. `integrated`
means the repair reached `master`; `confirmed` additionally requires the
automatic `master` workflows to pass. A changed fingerprint creates a linked
sub-incident rather than silently redefining the active failure.

## Isolation and Branch Policy

The skill creates or reuses a dedicated worktree on a named
`codex/ci-debug/<incident>` branch based on the failing commit. It never edits a
dirty primary checkout or stages unrelated files. The branch must be pushed
before a hosted runner can test it.

No pull request is required during the isolation loop. Once the branch passes
the full matrix, the skill uses the normal repository Git workflow to integrate
the reviewed repair. The final `master` run is monitored as confirmation, but
the repair must already have passed the full branch matrix.

## Selection Model

Manual debugging accepts the following bounded dimensions:

- operating system: Ubuntu, macOS, or Windows;
- repository task: validators, shared tests, browser tests, performance tests,
  or portability tests;
- native target: keyring or scheduler smoke;
- exact tracked test path or node ID;
- optional keyword expression;
- worker count from a small declared set;
- repeat count from a small declared set;
- supported Python version; and
- named condition profile: standard, serial, parallel, browser,
  native-keyring, or native-scheduler.

Named profiles set only reviewed environment flags. There is no free-form shell
command or environment input.

## Failure Flight Recorder

Every manual run uploads a short-retention diagnostic artifact, even when the
selected test passes. The bundle contains:

- `incident.json` with resolved inputs and runner metadata;
- `summary.md` with the failure fingerprint and verification history;
- JUnit XML, repository timing JSON, and complete phase logs;
- the exact argv used for every process;
- allowlisted Python, Node, Git, dependency, and runner-image versions;
- Git status, changed paths, untracked paths, and file modes before and after;
- failed node IDs and xdist worker association when available;
- a process snapshot on failure; and
- target-specific evidence such as browser screenshots, Task Scheduler query
  output, scheduler logs, or native-keyring diagnostics.

The bundle never dumps the complete environment, secret store, credentials,
tokens, home-directory contents, or unrelated user data. Console output ends
with a concise job summary and artifact name rather than requiring inspection
of the complete raw log.

## Automatic Classification Rerun

When an ordinary pytest phase fails, the same runner immediately reruns each
failed node serially with verbose traceback and local variables, after first
recording Git and process state. If the node passes, the runner next executes
the smallest enclosing selection serially and then repeats the original
parallel selection when the remaining job budget permits. The artifact
classifies the observation as:

- `deterministic` when the serial node still fails;
- `parallel-sensitive` when the node and enclosing serial selection pass but
  the original parallel condition fails;
- `order-sensitive` when the node passes alone but the enclosing serial
  selection reproduces the failure; or
- `platform-specific` when the same selected condition differs across requested
  operating systems.

These labels are evidence summaries, not automatic root-cause claims. The
assistant must still inspect the recorded state before editing.

## Repair Scope Rules

The skill may modify only code, tests, fixtures, workflow adapters, or dependency
contracts directly supported by the active incident evidence. It must not use
the following as default repairs:

- skipping, deselecting, or weakening the failing test;
- increasing a timeout without identifying the consumed time;
- permanently disabling parallelism because a serial rerun passes;
- broad dependency upgrades;
- reducing operating-system or native integration coverage; or
- combining a newly discovered failure into an unrelated patch.

If evidence is insufficient, the next action is a narrower diagnostic run or an
explicitly enabled interactive session, not a speculative code change.

## Verification Ladder

After a repair, verification expands monotonically:

1. the exact failing node under the original operating system and conditions;
2. repeated execution when the incident is intermittent or parallel-sensitive;
3. the enclosing test file or subsystem;
4. the affected repository task on that operating system;
5. any directly coupled native or portability checks;
6. the complete canonical matrix on the debugging branch; and
7. the automatic `master` workflows after integration.

A failure at any level stops expansion and returns the incident to diagnosis.
Only `matrix-green` authorizes integration.

## Interactive Escalation

An actor-restricted, time-limited `tmate` session may be requested manually when
the artifact cannot capture the necessary ephemeral state. It is disabled by
default, available only on manually dispatched debugging runs, receives no
repository secrets, uses read-only GitHub permissions, and limits access to the
triggering actor's registered key. The session command is treated as sensitive
diagnostic output.

Self-hosted runners are not part of the initial implementation. They remain a
future option if repeated native debugging demonstrates that targeted hosted
runs plus bounded interactive access are insufficient.

## Performance Improvements

The same design shortens both targeted and complete feedback:

- enable `setup-python` pip caching;
- cache Node downloads only when they are keyed by a reviewed CLI dependency
  manifest;
- skip Claude/Codex CLI installation for tasks that do not require them;
- use persisted timing data to divide the Windows shared task into balanced,
  deterministic shards;
- preserve browser tests as a separate serial phase; and
- cancel obsolete runs in the same workflow-and-branch concurrency group.

Increasing xdist workers is not the primary strategy. Worker count remains an
explicit diagnostic condition because excess concurrency can hide or create
state-isolation failures.

## Error Handling

The deterministic runtime fails closed when a ref is absent, a selector escapes
the repository, the branch is not pushed, GitHub authentication is missing, an
artifact does not match the requested run, or workflow metadata is ambiguous.
It prints one actionable error with the exact unresolved boundary.

Network and GitHub API failures do not alter incident conclusions. Interrupted
monitoring can resume from the persisted run ID. Artifact upload runs under
`always()` but does not overwrite the test conclusion; a simultaneous test and
artifact failure reports both.

## Testing

Implementation follows test-driven slices:

1. parser and selector validation, including shell-injection and path-escape
   rejection;
2. failure fingerprint and incident-state transitions;
3. mocked `gh` dispatch, watch, rerun, and artifact-download behavior;
4. diagnostic redaction and artifact schema tests;
5. repository-runner selection, repeat, and durable-output tests;
6. workflow policy tests proving push/PR always select the full matrix;
7. workflow-dispatch tests for each supported operating-system/task profile;
8. automatic serial classification-rerun tests; and
9. one live branch-scoped targeted run followed by one live full branch matrix.

The existing precommit and pre-push gates remain required. Fresh node
certification is a separate final step if the implemented skill standards
require it.

## Acceptance Criteria

- One command or skill invocation can capture an existing failed run and print
  the smallest supported remote reproducer.
- A pushed debugging branch can run one selected test under one selected
  operating system without running unrelated suites.
- A typical selected unit test completes without a full matrix and preserves a
  self-contained artifact.
- A failed parallel test receives an automatic serial classification rerun on
  the same hosted runner.
- Every failure artifact identifies Git changes made during the test.
- The skill resumes an interrupted incident without losing prior attempts.
- The complete canonical matrix can run against the debugging branch using the
  same workflow path as `master`.
- Integration is refused until the branch reaches `matrix-green`.
- The primary checkout and unrelated dirty state remain untouched.

## Non-goals

- Replacing GitHub Actions or `repo_checks.py`.
- Automatically repairing arbitrary failures without evidence review.
- Providing a general-purpose remote shell service.
- Persisting hosted runners after workflow completion.
- Introducing self-hosted runner infrastructure in the first release.
- Making targeted debug runs a substitute for final full-matrix certification.
