# Deferred Fixes Audited Integration Guide

## Goal

Integrate every still-valid outcome from `codex/deferred-fixes` into current
`master` without restoring obsolete architecture, losing current behavior, or
merely producing a conflict-free tree.

This is a preservation ledger and execution guide, not a historical plan.

## Verified topology

- Common ancestor: `b9ab63f5f5815973f43bcc53727813a99737ed26`.
- Current `master`: 38 commits after the common ancestor.
- `codex/deferred-fixes`: 20 commits after the common ancestor.
- 28 paths changed on both sides.
- A direct final-tree merge has 18 textually conflicting paths.
- Ten additional changed-on-both paths auto-merge but still need semantic review.

The original guide incorrectly counted 39 master commits and treated all 29
`merge-tree` changed-on-both sections as textual conflicts.

## Two-audit conclusion

Neither a blind direct merge nor one-at-a-time replay is sufficient.

- A direct merge preserves ancestry and gives one rollback boundary, but it
  presents only final-tree conflicts. It can silently accept semantically wrong
  auto-merges and gives no natural subsystem commit checkpoints.
- One-at-a-time `cherry-pick -x` isolates historical commits, but repeatedly
  replays obsolete intermediate states, revisits the same difficult files, and
  does not make the source branch an ancestor of `master`.
- `rerere` remembers textual conflict resolutions. It is not evidence that a
  resolution is semantically correct, and automatic staging must not be trusted.

## Integration method

Use semantic final-state transplants with explicit source accounting, followed
by ancestry and rollback merges.

1. Commit this guide separately before integration.
2. Create durable recovery refs or a Git bundle containing exact current
   `master` and `codex/deferred-fixes` tips.
3. Create `integration/deferred-fixes` from exact current `master`.
4. Implement the semantic slices below as separate commits. Use the source
   commits as evidence, not as patches that must be replayed literally.
5. Add `Source-Commit:` trailers naming every branch commit represented by
   each integration commit.
6. At every slice, map each source regression test to a resulting test, execute
   it directly, and prove that the unified repository runner collects it.
7. Regenerate standards, blueprints, contract blocks, catalogs, and inventories
   from the integrated tree when a slice changes their sources.
8. After all ledger entries and checks are complete, create an explicit
   `-s ours --no-ff` merge of `codex/deferred-fixes` on the integration
   branch. This records ancestry only; the preceding semantic commits contain
   the integrated behavior. Do not create this merge while any ledger entry is
   pending.
9. Merge `integration/deferred-fixes` into `master` with `--no-ff`. This
   outer merge is the single rollback boundary for the integration.
10. Delete `codex/deferred-fixes` normally only after Git reports it merged
    and the final audit passes.

The ancestry-only merge is safe only because the completed ledger proves that
every source outcome was integrated, adapted, already present, or deliberately
rejected. Without that proof it would falsely hide omitted changes.

## Time and deviation control

Time spent is not evidence of progress. Progress is measured by closed ledger
items, mapped tests, passing focused checks, and decreasing uncertainty.

Control overhead must remain below five percent of integration time and should
never exceed five minutes in an hour. Measurements should come from normal Git
diffs, test output, and timestamps rather than separate audit work.

### Reporting cadence

Issue a progress report:

- At the start of every semantic slice.
- Every 30 minutes only while a slice remains active.
- Immediately when an off-rail trigger fires.
- At the end of every slice.

Use one concise line:

`elapsed | slice | goals closed/total | tests passed/expected | unplanned paths | continue/adjust/stop`

Add at most three short bullets only when there is a blocker, scope change, or
decision. Do not interrupt active implementation merely to produce a longer
report.

### Time budgets

| Slice class | Examples | Review point | Hard stop |
|---|---|---:|---:|
| Narrow | list, graph server, drift, cloud, cache exclusion, small guidance | 30 minutes | 60 minutes |
| Medium | recurring metadata, wakeup, TDD guidance | 45 minutes | 90 minutes |
| Complex | Google services, installer guidance, refactoring standards/workflow | 60 minutes | 120 minutes |

The review point requires a progress report and an explicit decision to
continue unchanged or adjust. The hard stop forbids continuing the same
approach without reporting the evidence and obtaining a new decision.

Total-integration controls:

- At two hours, report completed slices, remaining slices, measured pace, and a
  revised completion range.
- At four hours, stop for a method and scope audit even if individual slices
  remain within their limits.
- Six hours is an absolute stop. Do not continue toward another eight-hour
  attempt without explicit user authorization based on the progress ledger.

### Off-rail triggers

Stop the current approach and report immediately if any condition occurs:

- No preservation requirement or test objective closes for 30 minutes.
- The same conflict, failed check, or generated-artifact mismatch survives
  three materially different attempts.
- More than two unplanned paths or one unplanned module becomes necessary.
- The slice reaches its hard time limit.
- Repository state differs unexpectedly from the last progress report.

### Adjustment ladder

When a trigger fires, spend no more than five minutes choosing one response:

1. Narrow or split the slice.
2. Restore the current-master implementation and transplant only the missing
   branch behavior.
3. Stop for a user decision if architecture, scope, or a branch goal would
   change.

Do not respond to delay by broadening the diff, deleting tests, accepting an
entire branch side, or postponing semantic review until the full-suite run.

### Progress ledger

Update one row only at slice completion or an off-rail stop. Routine 30-minute
reports remain conversational and do not create additional documentation work.

| Timestamp | Total elapsed | Slice | Slice elapsed | Requirements closed/total | Tests mapped/passing/collected | Planned/unplanned paths | Variance | Decision |
|---|---:|---|---:|---:|---:|---:|---|---|
| 2026-08-09 12:38 EDT | about 6m | list contract | about 3m | 4/4 | 3/3/collected | 5/0 | on time | commit slice |
| 2026-08-09 12:42 EDT | about 10m | graph-server contract | about 3m | 4/4 | 3/3/pending commit hook | 4/0 | on time | commit slice |
| 2026-08-09 12:46 EDT | about 14m | recurring metadata/catalog/inventory | about 4m | 6/6 | blueprint and inventory checks passed/pending hook | 11/0 | on time | commit slice |
| 2026-08-09 12:52 EDT | about 20m | wakeup behavior | about 5m | 5/5 | 6/6/pending hook | 6/0 | on time | commit slice |
| 2026-08-09 12:54 EDT | about 22m | skill-drift diagnostics | about 2m | 2/2 | 1/1/pending hook | 3/0 | on time | commit slice |
| 2026-08-09 12:58 EDT | about 26m | cloud route matrix | about 4m | 3/3 | 2/2/pending hook | 3/0 | on time | commit slice |
| 2026-08-09 13:10 EDT | about 38m | Google service contracts | about 12m | 8/8 | 29/29/pending hook | 11/0 | on time | commit slice |
| 2026-08-09 13:12 EDT | about 40m | TDD guidance | about 2m | 5/5 | 3/3/pending hook | 2/0 | on time | commit slice |
| 2026-08-09 13:17 EDT | about 45m | installer guidance | about 5m | 7/7 | blueprint check passed/pending hook | 2/0 | on time | commit slice |

## Current-master preservation ledger

Every post-base master commit is indexed here. An integration audit must retain
the stated outcome even where Git reports no textual conflict.

| Commit | Outcome that must survive | Main area | Branch interaction |
|---|---|---|---|
| `4eb542e` | Staged-aware docstring validation and runner coverage. | docstring validator | Retain current unified-check tier policy. |
| `5d4fdfc` | Generic presentation-node grouping. | visualization | None. |
| `d85083e` | Collapsed skill-grouping UI controls. | visualization UI | None. |
| `b002c4c` | Controller workflow and wire-protocol contracts. | controller | None. |
| `ab7de8f` | Independent recurring-task failure reporting and Linux registration checks. | recurring tasks | Reconcile branch blueprint metadata without reverting reporting. |
| `e92a93b` | Exhaustive pre-commit benchmarks and execution reports. | testing | Retain benchmark interfaces. |
| `0ac064f` | Caller-selected explicit standard-query roots; deletion of ownership-inference closure engine. | standards/refactor-node | Branch refactor outcomes must be redesigned for this architecture. |
| `5059dc4` | Centralized repository-check runner. | testing | All imported tests and validators must use this runner. |
| `b12254f` | Historical synchronization merge. | ancestry | No product behavior. |
| `6f8ef36` | Prepared docs catalogs and blueprint graphs reused across suites. | validators/graph | Preserve prepared-object reuse. |
| `64937a9` | Boundary matchers prepared once. | validator performance | None. |
| `acc1b4e` | Standard-schema preparation reused. | standards validation | New revisions must use current preparation. |
| `02a4476` | Recorded remaining validator work. | historical plan | No product behavior. |
| `af5ccc4` | Shared graph reused for duplicate-subcommand validation. | validator performance | None. |
| `2a74629` | Platform relative paths prepared once. | validator performance | None. |
| `a597ec0` | Runtime-document matchers and graph state prepared once. | runtime-doc validator | Branch rules must layer onto this implementation. |
| `d13d47f` | Avoided unnecessary personal-information scrubbing. | validator performance | None. |
| `bee9625` | Session-wide Python-source cache. | repository checks | Imported validators must remain cache-compatible. |
| `abdb7c7` | Shared cross-platform Python parses. | validator performance | None. |
| `2047db5` | Shared TOML-boundary Python parses. | validator performance | None. |
| `6934bfd` | Shared dispatcher-caller Python parses. | validator performance | None. |
| `fe3e0ef` | Shared subprocess-encoding Python parses. | validator performance | None. |
| `23d9690` | Shared portable-date Python parses. | validator performance | None. |
| `a88c6c3` | Shared skip-hygiene Python parses. | validator performance | None. |
| `fb0d495` | Recorded validator-performance findings. | design record | No product behavior. |
| `7b1320d` | Officina installed in managed runtimes; venv launch paths retained; heavy dependencies optional. | installation runtime | Guidance must not contradict this behavior. |
| `31c8295` | Officina docs moved under `docs/officina/`; mechanism docs removed from certificate authority. | documentation | Never restore old docs paths or authority. |
| `812c718` | Officina overview and all moved-document links/validators updated. | documentation | Runtime-doc rules must use current paths. |
| `a8db114` | Recorded Officina documentation design. | design record | No product behavior. |
| `91ce1df` | Standards and fixtures updated for the Officina documentation move. | node standards | Combine with preservation maps through a new revision cascade. |
| `8b1f5fc` | Known recurring-task failures can be tolerated by healthchecks. | recurring tasks | Branch metadata must retain this policy. |
| `65e3159` | Docstring gate removed from pre-commit while remaining explicitly runnable. | testing tiers | Preserve current tier policy. |
| `071dbf4` | `tw` pane-management controls and installer guidance. | assistant-tools installer | Installer simplification must retain every current control. |
| `1dabdbd` | Historical repository-check merge. | ancestry | No product behavior. |
| `282c46d` | Pooled/tiered checks and refactored dispatcher smoke tests. | testing | New tests must use the common pool and tiers. |
| `bf307ed` | Historical exhaustive-precommit merge. | ancestry | No product behavior. |
| `0979a65` | One pooled pytest collection for tests and validators. | testing | Never restore a separate validator runner. |
| `e9f0a08` | Process-tree cleanup, safe CI fallback, benchmark priming/state checks, scheduler docs. | testing/CI | Preserve all rollout safeguards. |

## Branch commit ledger

Every branch commit must receive one final status: `integrated`, `adapted`,
`already present`, or `rejected with reason`.

| Commit | Actual outcome to account for | Main area | Intended treatment |
|---|---|---|---|
| `873d63a` | Seven deferred-defect families: list sorting; graph-server CLI validation; wakeup route projection; recurring metadata/catalog/inventory; schema-neutral drift diagnostics; graph/list contract fixes; nested-module inventory correction. It also removed an obsolete recurring-task diagram. | multiple skills and repository inventories | Split across narrow subsystem slices. Never import this atomic commit or its historical plan/spec files. |
| `12e007e` | Read, list, delete, and source-expansion cloud routes remain non-creating; upload/destination routes may create the configured root. | cloud-files | Narrow cloud route-matrix slice. |
| `13f99ce` | Simplified email routing while preserving opaque shared credential delegation and legacy service fallback. | Google service contracts | Integrate with `6daf9a0` and `2901238` as one final-state slice. |
| `6daf9a0` | Clarified calendar routing, process metadata, mutation confirmation/verification/rollback, limits, account selection, and runtime-doc interface references. | Google service contracts | Integrate only in the final state closed by `2901238`. |
| `ff8a3ab` | Shortened TDD guidance while preserving staged design, tests, implementation, and documentation approvals. | initialize-tdd | Narrow guidance slice with a preservation checklist. |
| `3ce3cd3` | Shortened installer guidance and added per-entry skill-directory conflict behavior. | assistant-tools installer | Manual preservation-map rewrite against current installer and `tw` behavior. |
| `2901238` | Closed service-contract gaps: public-interface masking, malformed/empty/noncanonical export rejection, delegation coverage, cloud/list guidance, and runtime-doc enforcement. | Google service/runtime docs | Final commit evidence for the combined service slice. |
| `ab3f91c` | Required preservation maps and changed refactoring ordering across the standard family. | standards/refactor-node | Current-architecture standards slice with new revisions/digests. |
| `738f66f` | Removed two repeated negative lesson filters while retaining positive lesson-selection guidance in prepare-handoff. | prepare-handoff | Narrow prose slice; exclude its historical plan. |
| `1934f2d` | Named canonical dependency interfaces in wrap-up. | wrap-up | Narrow prose slice. |
| `f038629` | Required implementation-child inspection and added routing coverage. | refactor-node | Combine with the refactoring workflow slice. |
| `bbaace9` | Removed duplicated branch-check rationale, not the branch-safety requirement. | skill-maker | Narrow prose slice adapted to explicit-query guidance. |
| `9c6e2d6` | Made wakeup policy-status reads side-effect free. | wakeup | Combine with wakeup projection from `873d63a`. |
| `29166b2` | Excluded paths containing `__pycache__` and files ending in `.pyc` from ownership, while preserving authored binary/fixture files. | blueprint graph | Narrow graph-ownership slice. |
| `0bea7c6` | Closed a historical five-skill plan. | historical plan | Reject document import after extracting any unique acceptance evidence. |
| `66e2b4c` | Required query provenance and evidence preflight, but expressed them through a now-deleted ownership-inference interface. | refactor-node | Preserve the goal through current explicit-query facts, not literal old commands. |
| `799e062` | Made LaTeX Workshop activation trigger-only across skill/catalog/user docs. | LaTeX Workshop | Narrow documentation/skill slice. |
| `cc1dfde` | Restored semantic-review and exact-diff gates accidentally lost by `66e2b4c`. | refactor-node | Mandatory final state of the refactoring workflow slice. |
| `cac5ba9` | Recorded calibration findings, including incomplete full-suite/certification evidence and the gate regression repaired by `cc1dfde`. | historical plan/evidence | Do not import the plan; retain its assurance limitations in this ledger. |
| `64fac71` | Marked calibration complete. | historical plan | Reject document import; no product behavior. |

## Semantic slices

### Slice 1: list contract

Source: part of `873d63a`.

Preserve:

- Unfiltered reads honor `--sort`.
- Missing sort values are placed last.
- The implementation and blueprint expose the same route behavior.
- The branch regression tests have direct equivalents and unified-runner
  collection evidence.

### Slice 2: graph-server contract

Source: part of `873d63a`.

Preserve:

- Exactly the three declared flags are accepted.
- Positional arguments are rejected.
- Ports are restricted to `1..65535`.
- Blueprint, implementation, and tests agree.

### Slice 3: recurring metadata, catalog, and inventory

Source: part of `873d63a`.

Preserve:

- Missing owned-module/dependency metadata is added from the final tree.
- Independent failure reporting from `ab7de8f` remains.
- Known-failure tolerance from `8b1f5fc` remains.
- The obsolete `skill.mmd` is removed only if no current generated-document
  contract requires it.
- Catalog, runtime dependencies, generated contract blocks, and exact inventory
  assertions are regenerated rather than copied from either branch.

### Slice 4: wakeup behavior

Source: part of `873d63a` and `9c6e2d6`.

Preserve:

- Route projection reports policy state versus scheduled time correctly.
- Policy-status reads never create, update, or delete scheduling state.
- CLI blueprint and behavioral tests describe the same contract.

### Slice 5: skill-drift diagnostics

Source: part of `873d63a`.

Preserve schema-neutral diagnostics. A diagnostic must not assume one obsolete
certificate schema when reporting malformed or unavailable drift state.

### Slice 6: cloud route matrix

Source: `12e007e`.

Preserve:

- Read, list, delete, and source-expansion routes do not create the configured
  Drive root.
- Upload and destination-resolution routes may create it where required.
- Tests cover both sides of this matrix.

### Slice 7: Google service contracts

Source: `13f99ce`, `6daf9a0`, and `2901238`.

Integrate only their final combined state:

- Opaque shared credential delegation and legacy per-service fallback.
- Complete Calendar process metadata.
- Mutation confirmation, verification, rollback, limits, and account selection.
- Schema-validated public-interface masking.
- Rejection of malformed, empty, and noncanonical exports.
- Runtime-document rules layered onto master's prepared graph/matcher APIs.
- Direct source-test equivalents and unified-runner collection proof.

### Slice 8: TDD guidance

Source: `ff8a3ab`.

The shortened skill must retain separate design, tests, implementation, and
documentation stages and their approval gates. Word-count reduction is not an
acceptance criterion.

### Slice 9: installer guidance

Source: `3ce3cd3`.

Build an explicit preservation map for:

- Mode selection and capability reporting.
- Installation phases and dry-run behavior.
- Whole-directory and per-entry conflict handling.
- Completion checks and failure approval.
- Every current `tw` control from `071dbf4`.
- No contradiction of managed-runtime behavior from `7b1320d`.

Do not require the skill to document internal managed-runtime mechanics merely
because installer code changed on master.

### Slice 10: refactoring standards and workflow

Source: `ab3f91c`, `f038629`, `66e2b4c`, and `cc1dfde`.

Required final workflow:

1. Discover the component and implementation children before querying.
2. Select an explicit standard query root through
   `common.interface.query-standard`.
3. Verify caller, repository root, selected standard path, facts, view, refs,
   and rendered common-query entry point.
4. Evaluate evidence sufficiency.
5. Construct a preservation map.
6. Make the change.
7. Inspect the exact diff.
8. Perform semantic review.

Do not restore `refactor-node.interface.query-standards`, inferred targets,
gateway-path ownership, or the deleted closure engine.

Both branches reused revision increments with different content. Integrating the
semantics requires a fresh standards cascade from current master:

| Standard | Current revision | Expected integrated revision |
|---|---:|---:|
| refactoring | 4 | 5 |
| node, module, behavioral-source | 12 | 13 |
| instruction-node | 13 | 14 |
| python-node | 16 | 17 |
| instruction-module | 16 | 17 |
| instruction-behavioral-source | 13 | 14 |
| python-behavioral-source | 16 | 17 |
| python-module | 18 | 19 |

The canonical standards-update procedure must regenerate authority digests,
source-map fixtures, extracted views, and consumer expectations. Do not import
branch digest edits.

### Slice 11: small guidance corrections

Source: `738f66f`, `1934f2d`, `bbaace9`, and `799e062`.

These may be separate commits or one reviewed prose slice. Preserve:

- Positive lesson-selection guidance after removing repeated negatives.
- Canonical wrap-up dependency interfaces.
- Skill-maker branch safety and current explicit-query guidance.
- Trigger-only LaTeX Workshop activation in skill, catalog, and user docs.

### Slice 12: ownership cache exclusion

Source: `29166b2`.

Exclude only `__pycache__` path components and `.pyc` files. Preserve authored
binary and fixture files. Add the rule to master's shared prepared graph rather
than replacing graph preparation.

## Generated-artifact obligations

Each affected slice must identify and regenerate, where applicable:

- `references/blueprint/runtime_dependencies.json`.
- Generated `SKILL.md` contract blocks.
- `docs/skills.md`, `docs/user/system.md`, and `docs/user/research.md`.
- Blueprint owned-file and dependency inventories.
- Exact graph-node inventory assertions.
- Node-standard revisions and authority digests.
- Extracted standard views and source-map fixtures.

Historical plan/spec files introduced inside behavior commits are excluded after
their acceptance evidence is captured here. This applies not only to plan-only
commits, but also to `873d63a`, `ab3f91c`, `738f66f`, and `66e2b4c`.

## Test-preservation record

For every slice, complete one row per source regression-test file.

| Slice | Source test and objective | Resulting test or exact equivalent | Direct focused result | Unified-runner collection evidence |
|---|---|---|---|---|
| list contract | `test_lists.py`: unfiltered reads sort requested fields, put missing values last, and compare short/long strings consistently; shared projection test verifies exported usage | Same three named regressions plus joint list/graph projection test retained | 3 behavioral tests passed in 0.33s; projection passed in 1.06s | unified commit hook collected 135 list-manager tests; all passed |
| graph-server contract | `test_graph_server.py`: reject ports 0 and 65536; accept 65535; shared projection test verifies flags and positionals | Same three parameterized assertions plus joint list/graph projection test retained | 3 behavioral tests passed in 0.02s; projection passed in 1.06s | unified hook collected nine math-graph tests; all passed |
| recurring metadata/catalog/inventory | Source increments the exact live graph inventory by one; blueprint ownership and dependencies are executable contracts | Adapted current-master count from 217 to 218, canonical blueprint check, and existing recurring-task suite | blueprint check and focused inventory test passed | pending commit hook |
| wakeup behavior | `test_features.py`: policy status preserves absent, existing, non-directory, directory-symlink, and dangling-symlink state; scheduled-session reads do not create storage | Same six focused assertions retained | 6 passed in 0.07s | pending commit hook |
| skill-drift diagnostics | `test_drift_check.py`: an empty active-plugin graph reports schema-neutral language | Same named regression retained | 1 passed in 0.12s | pending commit hook |
| cloud route matrix | `test_cloud_files.py`: missing-root reads do not create folders; existing write test retains creating route | New read regression plus existing write-path test | 2 passed in 0.05s | pending commit hook |
| Google service contracts | Service delegation, Calendar process metadata/guidance, and schema-validated public-interface masking including malformed and adjacent-token negatives | Final-state branch tests retained; validator adapted to master's prepared graph/matcher implementation | 18 validator, 9 delegation, and 2 Calendar tests passed | pending commit hook |
| TDD guidance | Existing initialize-tdd suite plus preservation review of design, tests, implementation, documentation, overwrite, bootstrap, and no-commit gates | Branch final guidance retained | 3 passed in 0.09s | pending commit hook |
| installer guidance | Source has no regression-test change; preservation map covers mode, phases, dry-run, conflicts, completion/failure handling, and current `tw` controls | Branch final guidance plus master `tw` contract | canonical blueprint check passed; installation runtime tests deferred to pre-push tier | pending commit hook |

A green `full` run does not prove that a nested skill-owned test was collected.
Deleting a source test requires naming the exact replacement assertion.

## Source-resolution record

| Source commit | Status | Resulting slice/commit | Evidence |
|---|---|---|---|
| `873d63a` | pending | | |
| `12e007e` | integrated | cloud route matrix / `f8108e0` | focused and unified cloud tests passed |
| `13f99ce` | adapted | Google service contracts / `5cab8a0` | final-state delegation and unified checks passed |
| `6daf9a0` | adapted | Google service contracts / `5cab8a0` | Calendar, validator, and unified checks passed |
| `ff8a3ab` | pending | | |
| `3ce3cd3` | adapted | installer guidance / pending slice commit | branch simplification combined with current `tw` contract |
| `2901238` | adapted | Google service contracts / `5cab8a0` | closing delegation, masking, and unified checks passed |
| `ab3f91c` | pending | | |
| `738f66f` | pending | | |
| `1934f2d` | pending | | |
| `f038629` | pending | | |
| `bbaace9` | pending | | |
| `9c6e2d6` | integrated | wakeup behavior / `469e638` | six focused and unified wakeup checks passed |
| `29166b2` | pending | | |
| `0bea7c6` | pending | | |
| `66e2b4c` | pending | | |
| `799e062` | pending | | |
| `cc1dfde` | pending | | |
| `cac5ba9` | pending | | |
| `64fac71` | pending | | |

## Final audit gates

1. All 20 source-resolution rows are complete.
2. Every source regression test has a resulting objective map, direct result,
   and collection result.
3. Every one of the 28 changed-on-both paths, including the ten clean
   auto-merges a direct merge would accept, has a semantic disposition.
4. No old `docs/skill-blueprints.md` or other pre-move Officina path returns.
5. No deleted closure engine, ownership-inference query, or separate validator
   runner returns.
6. Standards have fresh revisions, digests, fixtures, and generated views.
7. Blueprints, contract blocks, catalogs, runtime dependencies, and inventories
   are generated from the final tree.
8. Focused checks pass for every slice.
9. The unified runner demonstrably collects every nested imported test.
10. The authoritative full repository tier passes.
11. The ancestry-only merge is created only after gates 1 through 10.
12. The final outer merge into `master` provides the rollback boundary.
