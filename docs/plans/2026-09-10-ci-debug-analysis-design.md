# CI-debug historical analysis design

Date: 2026-09-10
Status: implemented candidate

## Objective

Add a read-only GitHub Actions history-analysis route to `ci-debug` without
changing qualification or repair authority. The route collects one bounded,
immutable snapshot and derives two offline reports:

- pytest failures grouped into complete green-to-green episodes; and
- descriptive runtime hotspots from run, job, and step timing evidence.

The route never dispatches a workflow and never mutates Git or GitHub state.
It does not claim billing cost, DAG critical path, removable time, or causal
speedup from observational timing data.

## Public routes

`ci-debug.interface.default@3` selects exactly one route:

- historical failure or runtime analysis -> `analyze-ci@1`;
- local branch qualification or optional push -> `qualify-ci@2`;
- one explicitly assigned matrix element -> `repair-element@1`;
- mixed or ambiguous mutation intent -> clarification.

`ci-debug.interface.analyze-ci@1` accepts:

- `request`;
- local `repository`;
- exact `workflow` and `branch` selectors;
- a new private `output-directory` outside all repository worktrees;
- optional exact `event`, inclusive `since`, and positive `run-limit`.

Structured arguments override conflicting prose. `request` may change emphasis,
but it does not change which reports run or authorize mutation.

The route creates the private output root and derives three new child paths:

1. `snapshot` for collection;
2. `failure-episodes` for the failure report;
3. `runtime-hotspots` for the timing report.

It invokes collection first, then both reporters against the same published
snapshot. Each child publishes independently. The route verifies that both
report receipts name the snapshot identifier and digest returned by collection,
then reports all three paths. There is no sibling lock, root manifest, root
publication marker, or finalize operation.

## Runtime interfaces

### `fetch-github-actions-history@1`

Inputs are `repo-root`, `workflow`, `branch`, new `snapshot-dir`, and optional
`event`, `since`, `run-limit`, `timeout`, and `workers`.

Collection uses authenticated `gh api` reads and local read-only Git commands.
It resolves the workflow unambiguously, applies exact event and branch filters,
enumerates every retained run attempt, and fetches jobs, steps, and available
logs under bounded retries, concurrency, and an overall deadline. HTTP 403/429
responses honor rate-limit or retry metadata within the remaining budget.

The collector freezes provenance for observed commits and test paths. Missing,
expired, denied, malformed, truncated, or unclassified evidence is retained as
an explicit gap; it is never silently treated as success or absence.

### `report-test-failures-between-green-runs@1`

Inputs are the published `snapshot-dir` and a new sibling `report-dir` named
`failure-episodes`. The reporter is offline. It validates the snapshot before
analysis and publishes JSON, CSV, and HTML bound to its source snapshot digest.

A logical run is one workflow run ID. Attempts are observations of that run,
not independent runs. The terminal attempt determines the logical conclusion.
A complete failure episode is the open interval between consecutive qualifying
green logical runs. Leading and trailing red spans are censored rather than
silently included in the complete-episode denominator.

Within each complete episode, each canonical pytest node ID contributes at most
one incidence. Exact recurrence is reported when evidence is complete; otherwise
lower and upper bounds preserve uncertainty. Parse failures, unavailable logs,
and unresolved test identities remain explicit gaps.

First-observation provenance is frozen from Git objects available during
collection. The report distinguishes observed introduction, edited-in-range,
present-before-range, and unknown cases without inventing commit history.

### `report-ci-runtime-hotspots@1`

Inputs are the same published `snapshot-dir` and a new sibling `report-dir`
named `runtime-hotspots`. This reporter is also offline and publishes JSON, CSV,
and HTML bound to the same source snapshot digest.

It reports separate descriptive measures:

- API update span;
- API start delay when a start timestamp exists;
- observed job execution envelope;
- observed job-minutes;
- repeated-step aggregates keyed by stable step ordinal and normalized name.

Missing timestamps and contradictory orderings are counted explicitly. Runner
OS and hosting classifications include their evidence basis and remain
unclassified when labels do not justify a classification.

## Snapshot and publication

The snapshot contains a canonical manifest, a create-once publication marker,
run and provenance members, and captured logs. The snapshot digest is derived
from canonical manifest bytes. Consumers reject an absent marker, schema
mismatch, manifest mismatch, member digest mismatch, unexpected member, or path
escape.

Each destination must not exist. Snapshot and report writers create private
directories, write members and a canonical manifest, then create that child's
publication marker last. No child writer replaces a destination or marker.

The two reports are named siblings of `snapshot`. This keeps one analysis
invocation inspectable without introducing a transaction across the three
independent publication units.

## Outcome and error behavior

Successful collection and reporting may still include evidence gaps. Receipts
distinguish complete and gap-bearing outcomes and include path, snapshot
identity, counts, and gap counts.

Preflight, authentication, selection, rate-limit, timeout, schema, digest, and
publication failures are classified. Any invocation-created incomplete child
is retained and reported for inspection; the route does not perform broad
cleanup.

Analysis reports:

- all three artifact paths and the common snapshot identity;
- selected logical-run and attempt counts;
- complete episodes, censored spans, and evidence coverage;
- recurrence bounds;
- the separate descriptive timing measures;
- every material evidence gap.

## Blueprint ownership

The parent module exports `analyze-ci@1` and routes only the three runtime
interfaces needed by analysis. The `_rtx` child owns one shared history source,
the collector, the two reporters, runner classification, and the existing
qualification adapters.

There is no analysis-workspace runtime source, reserve export, finalize export,
or runtime dependency for those operations. Existing qualification and repair
interfaces keep their versions and authority.

## Verification

Focused tests cover:

- workflow resolution and exact filtering;
- attempt enumeration and retry/deadline behavior;
- immutable snapshot validation and gap preservation;
- green-to-green episode boundaries and recurrence bounds;
- provenance classification;
- timing measures, stable step identity, and runner classification;
- independent child publication and common source digest;
- public route and blueprint interface edges.

Blueprint synchronization must pass after canonical YAML changes. Focused
CI-debug tests must pass without changing shared atomic-file infrastructure.

## Acceptance criteria

1. Historical analysis never dispatches CI or mutates Git/GitHub state.
2. One bounded collection feeds both offline reporters.
3. Both reports verify and expose the same source snapshot digest.
4. Attempts are not double-counted as logical runs.
5. Only complete green-to-green episodes enter the complete denominator.
6. Incomplete evidence produces explicit bounds and gaps.
7. Runtime measures remain descriptive and separately labeled.
8. The output consists of three independently published sibling directories.
9. No workspace coordinator, lock, root manifest, root marker, or finalize
   operation remains.
10. Focused tests and blueprint synchronization pass.

## Non-goals

- workflow dispatch, rerun, cancellation, or repository repair;
- replacing `qualify-ci` or `repair-element`;
- pricing or billing calculation;
- causal performance claims;
- inferring a complete DAG critical path from observed timestamps;
- repository-wide infrastructure changes.
