# 2026-08 Code-Test Performance Cleanup

This retrospective preserves durable evidence from the August 2026 test-suite
cleanup. It is history, not policy. Current policy lives in the
[Code Test Design and Performance Standard](../../references/node-standards/code-testing.standard.yaml),
and the current workflow lives in the
[optimization playbook](../contributors/optimizing-code-tests.md).

## Scope and result

The campaign targeted the eight-worker precommit suite on a 12-logical-core
host. The initial accepted baseline was 154.64 seconds of pytest wall time and
1,016 seconds of aggregate file work. The last comparable working-view result
accepted by the campaign was phase 14b:

| Measure | Initial accepted baseline | Phase 14b accepted result |
| --- | ---: | ---: |
| Pytest wall | 154.64s | 76.08s |
| Runner wall | not recorded in the initial baseline | 83.43s |
| Aggregate file work | 1,016.00s | 400.62s |
| Outcomes | not retained in the campaign notes | 3,373 passed, 18 skipped |

The comparable pytest wall fell by 78.56 seconds (50.8%), and aggregate work
fell by 615.38 seconds (60.6%). The original sub-minute working-view target was
not reached: phase 14b remained 16.08 seconds above it.

Later committed reuse work appears in:

- `c96a492e` — reuse validator preparation;
- `146c11d0` — reduce blueprint setup duplication;
- `b491f542` — consolidate skill integration setup.

The staged precommit hook for `81fb1160` passed 3,149 tests with 18 skips in
34.47 seconds of pytest time and 37.68 seconds of runner time. That is useful
current feedback-loop evidence, but it is not a direct comparison with the
phase-14b working-view corpus and should not be used to claim additional
like-for-like savings.

## Accepted progression

| Accepted checkpoint | Pytest wall | Aggregate work | Main change class |
| --- | ---: | ---: | --- |
| Initial baseline | 154.64s | 1,016.00s | Before broad cleanup |
| Phase 2 | 118.72s | 713.98s | Consolidated duplicated repository/process histories |
| Phase 3 | 106.52s | 648.14s | Narrower node-certification setup and duplicate removal |
| Phase 5 | 93.13s | 535.39s | Broader node-level consolidation |
| Phase 8 | 89.91s | 496.96s | Diminishing-return checkpoint |
| Phase 9 | 81.46s | 454.25s | Conservative exact-node gate placement |
| Phase 13 | 77.59s | 410.91s | Canonical-validator preparation cleanup |
| Phase 14b | 76.08s | 400.62s | Final comparable accepted campaign result |

## Changes that worked

- Consolidate compatible transitions and findings that consume identical
  expensive state, while restoring every mutation between labeled scenarios.
- Keep one canonical live validator owner and remove ordinary live-health
  duplicates; retain synthetic positive and negative behavior tests.
- Replace whole repositories, signing, or processes with minimal production
  objects for pure ordering, filtering, projection, and diagnostic logic.
- Prepare immutable Git repositories, graphs, schemas, or parsed documents once
  within a valid isolation domain, then copy before mutation.
- Split eager setup so materialization, graph loading, hashing, and signing are
  requested only by tests that observe them.
- Collapse parameter axes only after mapping each value to a production branch,
  observable, masking behavior, or isolation boundary.
- Use one process for compatible ordered protocol scenarios while retaining
  separate processes for transport, import isolation, signals, descriptors,
  environment, and lifecycle behavior.
- Move only exact broad integrations from the fast gate, preserve targeted
  canaries, and mechanically verify the slower enforced gate.
- Preclassify immutable per-file metadata when that removes repeated inner-loop
  work without changing discovery, ordering, decoding, or failure ownership.

## Review corrections that prevented regressions

- Restore bytes, modes, index entries, environment, projections, and hashes
  between merged scenarios; a fresh private copy is safer when restoration
  cannot be proven.
- Fixtures must match production metadata. One accepted correction restored a
  security-relevant `0600` mode rather than using a convenient `0644` file.
- Empty findings do not prove execution. Tests need positive discovery, call,
  selected-ID, or emitted-record evidence.
- Fast token gates must preserve Unicode normalization, decoding, syntax, and
  fail-closed behavior; ambiguous input returns to the authoritative path.
- Process readers, threads, sockets, locks, and environment changes need
  failure-safe cleanup even when an assertion interrupts the scenario.
- Collection-affecting changes require before/after test identities and a
  parameter/contract map, not merely a green reduced selection.

## Rejected or reverted approaches

- Increasing workers: the bottleneck was repeated physical work, and excess
  workers amplified contention.
- Authored inventory replacements for canonical traversal: rejected after
  ordering, nested-repository, symlink, decode, and embedded-tree gaps.
- Changed-only narrowing of repository-wide validators: rejected because the
  canonical contract owns the complete selected projection.
- Cross-worker source/AST snapshots: rejected when serialization, publication,
  and isolation costs had no guaranteed worksteal saving.
- Immutable repository templates for cheap authorization tests: reverted after
  target-worker time regressed from 2.20s to 3.72s despite a small serial gain.
- A registered-child ownership index: reverted because host-mismatched timing
  could not establish material benefit.
- Token shortcuts around parsers: rejected when syntax or UTF-8 failure was
  part of the security contract.
- Aggressive gate deferral: rejected when it removed primary computation,
  signing, race, Git/index, or public-adapter evidence.
- Cheap-suite churn: correct merges below the declared materiality threshold
  were rejected because they added maintenance risk without useful savings.
- Contaminated timings: concurrent runs, systemic unchanged-file slowdown,
  high host load, changed revision, and missing socket capability invalidated
  multiple otherwise-green comparisons.

## Reusable infrastructure inventory

These are candidates, not blanket mandates. Reuse follows deletion,
consolidation, and narrowing, groups consumers with identical fidelity and
mutation needs, pilots a small set, and must demonstrate its claimed benefit.

| ID | Facility | Likely consumers and boundary |
| --- | --- | --- |
| R1 | Immutable Git repository templates | Certification, provenance, and release tests with identical histories; preserve independent `.git`, modes, identity, branch, symlinks, and ambient `GIT_*` isolation. |
| R2 | Minimal blueprint graph builders | Pure identity, projection, report, worklist, and renderer tests; require explicit schema/root/owner fields and retain one live repository owner. |
| R3 | Provenance-bound prepared live graph | Read-only consumers of one exact repository projection; exclude topology mutation and graph-failure tests. |
| R4 | Prepared authoritative schemas | Repeated validation against unchanged schema bytes; keep findings and mutable validation context invocation-local. |
| R5 | Subprocess adapter harness | Argv/stdin/stdout/stderr/exit forwarding; retain physical children for transport, package, descriptor, signal, and isolation contracts. |
| R6 | Byte/index mutation snapshots | Release, certification, and provenance transitions; restore existence, bytes, modes, index entries, and failure cleanup. |
| R7 | Operation-count instrumentation | Regressions in graph loads, Git children, scans, copies, or processes; do not assert incidental helper counts. |
| R8 | Immutable parsed-YAML templates | Checked-in scenario/oracle variants; key exact bytes/parser version and deep-copy before mutation. |
| R9 | Verified certificate-record view factory | Reader/projection states only; signed admission and writer lifecycle retain real signing and Git. |
| R10 | Persistent state-root helper | Default/override/context path resolution; preserve platform semantics and fresh mutable roots. |
| R11 | Schema-faithful catalog fixtures | Pure catalog projections or exact public-loader repositories; prohibit fallback to checkout schemas. |
| R12 | Git-readiness metadata harness | Pure native-reader/capability logic; never replace physical index, pathspec, symlink, FIFO, or ambient-Git owners. |
| R13 | Registered child-artifact ownership index | Share only a pure deepest-ancestor index if repeated measurements prove material work removal; the campaign experiment was reverted. |
| R14 | Source/AST preparation | Overlapping validators only if exact provenance, failure replay, symlink identity, parser version, isolation, and net target-worker savings are proven. |
| R15 | Pure subprocess-call AST classifier | Share call classification only; keep policy, exclusions, findings, roots, and diagnostics owned by each validator. |

## Durable stopping rule

Stop when remaining candidates do not clear the predeclared materiality
threshold or require weakening unique evidence. Source deduplication alone is
not a performance result. Preserve uncertain coverage, record the gap, and
prefer a nimble suite maintained by explicit ownership over a smaller suite
maintained by test-count pressure.
