# Managed Setup Teardown-All: Dormant Core and Publication Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and prove a dormant, platform-agnostic manager operation that can remove the complete current state represented by a valid selected-context setup ledger, then publish its exact zero-argument interface only after separately approved production activation evidence exists.

**Architecture:** The setup-interface manager owns receipt validation, deterministic dependents-before-prerequisites planning, coordination, settlement, and recovery. Each admitted setup owner remains solely responsible for its effects, exact teardown action, verifier, teardown equivalence, and repeat safety. Stage A exercises the internal manager directly with injected synthetic registered owners; structural absence of an interface class, registration, export, gateway route, and runtime dependency makes the dormant core unreachable through Dispatcher/MCP. Stage B alone publishes `setup-interface-manager._rtx.interface.teardown-all@1`.

**Tech Stack:** Python 3.11+, Officina V6 setup graphs, JSON ledger state, and pytest; Famulus Dispatcher/MCP applies only to Stage B publication.

**Spec:** `docs/setup.md` sections 1.4-1.5 and the required behavior below.

## Stage ordering and activation boundary

- `teardown-all` means managed setup-state teardown, not plugin/runtime uninstall or general purge.
- **Stage A is executable now.** It adds only the dormant generic graph, state, planning, settlement, manager, and synthetic-fixture tests listed in the Stage A allowlist. It does not admit, select, or modify a production owner.
- Stage A tests may use injected synthetic registered owners only. Those fixtures prove manager planning, ledger transitions, ordering, settlement, cancellation, and recovery; they do not prove any production owner's teardown equivalence, repeat safety, or need for a bulk route.
- Stage A has one activation authority: structural unreachability. It must add no `TeardownAllInterface`, machine-interface declaration, source or namespace export, gateway use, runtime dependency, generated interface block, route documentation, or other capability flag. Coverage must positively assert those surfaces remain absent.
- **Stage B is publication.** It activates only when an approved record identifies either (a) at least two independent production managed roots plus a release decision requiring one selected-context reset operation, or (b) a named bulk consumer whose requirements repeated ordinary root teardown cannot satisfy.
- The activation record must inventory every current production managed owner, prove exact equality among production managed metadata, `PRODUCTION_BINDINGS`, and `PRODUCTION_ACTION_CALLS`, and supply fully expanded green commands for each owner's teardown-equivalence, repeat-safety, lifecycle-epoch, and recovery suite.
- Until that record exists, Gate 0B cannot pass and Tasks 3 and 4B must not execute. Dormant-core implementation and synthetic tests do not relax this publication gate.
- The ledger is the only manager-owned manifest: each receipt pins the setup interface/version and root claims, while live blueprint metadata remains authoritative for the exact teardown interface/version/verifier/kind. Do not add another manifest, command log, traversal, or effect registry.
- Only already-admitted fixed whole-node managed setup is in scope. Do not persist arguments, stdin, environment, commands, credentials, paths, or arbitrary verifier payloads.

### Managed-setup admission contract

- Setup and teardown actions must both be Markdown or both be executable Python. Both actions and both verifiers take zero arguments in this phase; complete preflight rejects a nonempty finite `binding.arguments`.
- The setup interface version is the lifecycle epoch. Any change to either action, either verifier, execution kind, or externally visible state semantics requires a setup-version bump.
- Owners must independently prove setup-then-teardown equivalence, repeat-safe teardown after partial execution, and safe cancel-then-retry behavior. Manager verifier success does not establish those owner properties.
- Blueprint lifecycle metadata declares semantics. Finite production bindings/action calls declare executable permission, and Stage B preflight requires exact agreement.

## Bounded result

| State class | Result |
|---|---|
| Receipts for admitted fixed managed setups | Each exact declared teardown and verifier runs through generic manager coordination |
| One managed setup closure | Existing `begin(teardown, ROOT_SETUP_INTERFACE, ...)` remains unchanged |
| Canonical setup ledger and flow state | Successful completion leaves an empty retained ledger and no active flow |
| Owner-specific unmanaged effects or data | Outside the manager; retained unless an owner contract says otherwise |
| Host, plugin/runtime, credential, remote-authority, historical, or irreversible effects | Outside this operation and explicitly not claimed as removed |

No acceptance statement may shorten “all valid managed setup receipts in the selected context” to “all Famulus side effects.” A broader uninstall/purge orchestrator needs a separately reviewed owner/effect inventory and retention policy.

## Global behavioral constraints

- Scope is every receipt key in the selected `setup_status`, including empty or foreign `required_by`; claims affect ordinary root teardown, not global membership.
- Give each receipt one plan entry and at most one successful settlement/removal. Normal execution invokes its teardown and verifier once; recovery re-verifies and reruns only after exact false.
- Accept only verifier result `{"torn_down": true}` as successful settlement. Effect reversal remains the owner's contract.
- Preflight the complete receipt set before flow creation or dispatch. Unknown interfaces, setup-version mismatches, missing metadata, missing finite bindings, or nonempty binding arguments fail without byte changes; only a valid empty ledger yields an empty plan.
- Order receipt-bearing nodes by unioning `managed_setup_order()` for sorted receipt keys, deduplicating, filtering absent receipts, and reversing. Add no traversal.
- Recompute the deterministic remainder before dispatch and settlement; its first step must equal the persisted current step. Invalid current state becomes recovery-required, never an alternate action.
- Retry verifies before rerunning. Cancel verifies first: true atomically removes the current receipt and clears the flow; exact false clears the flow without removal; uncertainty retains a recovery-required flow. Later receipts remain unchanged.
- Persisted success is `interfaces == {}` and `active_flow is None`; keep the canonical empty ledger. A published response separately has `original: null` and `resume_original: false`.

## Scope harness against overscoping

Changed LOC means additions plus deletions from `git diff --numstat BASE_SHA`; replacement costs two. Generated files count and binary diffs are forbidden. The combined hard ceiling is **745 changed LOC**.

- **Stage A ceiling: 580 changed LOC.** Ten exact paths: the nine generic graph/state/evaluation/manager/integration paths below, plus at most 10 LOC in coverage solely asserting structural route absence.
- **Stage B reserve: 165 changed LOC.** Publication may use the remaining allowed mutations, including the manager interface class and the remaining coverage allowance.
- Exact arithmetic: **580 + 165 = 745**. Stage ceilings are cumulative and do not replace the unchanged per-file cumulative caps. A file touched in both stages must remain within its single original cap across both stages.
- No file may be created, deleted, renamed, or moved. If another path, larger cap, second manifest/traversal, or new runner is needed, stop and revise/review the plan before editing.

| Exact path | Exact allowed cumulative mutation | Cap | Stage A permission |
|---|---|---:|---|
| `src/officina/blueprints/graph.py` | Validate kind equality and zero action/verifier arguments only | 30 | Yes |
| `tests/test_officina_setup_requirements.py` | Focused lifecycle-shape accept/reject cases | 40 | Yes |
| `skills/setup-interface-manager/_rtx/_setup_state.py` | Schema-v2 tagged flow; v1 reads; global nullability/validation | 60 | Yes |
| `skills/setup-interface-manager/_rtx/tests/test_setup_state.py` | v1/v2, migration, nullability, malformed-state cases | 45 | Yes |
| `skills/setup-interface-manager/_rtx/_setup_evaluation.py` | Global plan and shared teardown-settlement core | 80 | Yes |
| `skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py` | Planning and ordinary/global shared-core cases | 65 | Yes |
| `skills/setup-interface-manager/_rtx/_setup_manager.py` | Internal manager operation, recovery branches, and Stage B interface class | 130 | Yes, except interface class |
| `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py` | Direct injected-manager tests; Stage B signature cases | 110 | Yes, except route/signature cases |
| `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml` | Stage B zero-argument machine interface | 15 | No |
| `skills/setup-interface-manager/_rtx/blueprint.yaml` | Stage B source export | 10 | No |
| `skills/setup-interface-manager/blueprints/gateway.yaml` | Stage B used-interface reference | 10 | No |
| `skills/setup-interface-manager/blueprint.yaml` | Stage B private namespace/export | 10 | No |
| `skills/setup-interface-manager/SKILL.md` | Stage B route prose and generated block only | 25 | No |
| `references/blueprint-schema/runtime_dependencies.json` | Stage B mechanical dependency regeneration | 10 | No |
| `tests/test_setup_interface_manager_coverage.py` | Stage A absence assertions (maximum 10 LOC); Stage B registration/activation assertions | 25 | Absence only |
| `tests/test_setup_interface_manager_integration.py` | Direct synthetic internal integration; Stage B route/production activation case | 55 | Synthetic internal only |
| `docs/setup.md` | Stage B published-route semantics and bounded exclusions | 25 | No |
| **Combined** | **17 exact paths; no other product changes** | **745** | **10 paths now** |

Stage A explicitly prohibits owner/platform files, `_setup_dispatches.py`, all blueprint files, generated metadata, `SKILL.md`, route documentation, and publication assertions. The Stage A budget command names only its ten paths; Stage B uses the complete 17-path table.

---

### Gate 0A: Lock the dormant-core scope before Task 1

**Checkpoint:** This is a precondition, not an implementation task, and creates no commit. Its evidence is recorded in the execution record before Task 1 begins.

- [ ] Record exact `BASE_SHA` from this clean isolated worktree and the ten-path Stage A allowlist.
- [ ] Require empty output from `git status --porcelain=v1 --untracked-files=all` and `git diff --name-status BASE_SHA`.
- [ ] Run focused graph, state, evaluation, manager, integration, and coverage baselines. A pre-existing publication route or failing negative absence assertion blocks Stage A.
- [ ] After every Stage A task, permit only tracked `M` entries on these ten paths; reject untracked/add/delete/rename/copy/type changes and owner/platform/publication paths.
- [ ] After every Stage A task, run `git diff --numstat BASE_SHA` over the ten Stage A paths. Enforce each cumulative per-file cap, the coverage sub-cap of 10, and the Stage A total of 580.

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_setup_requirements.py --selector tests/test_setup_interface_manager_coverage.py --selector tests/test_setup_interface_manager_integration.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 1
git diff --numstat BASE_SHA -- src/officina/blueprints/graph.py tests/test_officina_setup_requirements.py skills/setup-interface-manager/_rtx/_setup_state.py skills/setup-interface-manager/_rtx/tests/test_setup_state.py skills/setup-interface-manager/_rtx/_setup_evaluation.py skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py skills/setup-interface-manager/_rtx/_setup_manager.py skills/setup-interface-manager/_rtx/tests/test_setup_manager.py tests/test_setup_interface_manager_coverage.py tests/test_setup_interface_manager_integration.py
```

### Task 1: Model and plan all-receipts teardown

**Files:** the six graph/state/evaluation implementation and test paths in the table.

- [ ] Write failing graph tests for kind equality and zero arguments on both actions and verifiers.
- [ ] Write failing state tests for read-only canonical v1, v2 ordinary/global round trips, operation-dependent fields, first-mutation migration, and malformed state.
- [ ] Write failing planning tests for empty, chain, diamond, stale, foreign-claim, deterministic, deduplicated, and missing-prerequisite cases.
- [ ] Implement `teardown_all_plan(graph, ledger) -> tuple[TeardownStep, ...]` by unioning existing `managed_setup_order()` results; only a valid empty ledger returns `()`.
- [ ] Add schema-v2 `ActiveFlow.operation` for `"teardown-all"`; require null root/continuation and empty global `verified_steps`; preserve ordinary flow fields and read-only v1 parsing.
- [ ] Extract one private operation-aware settlement core. Preserve ordinary `record_teardown_success()` as a thin wrapper and add a thin global wrapper; `advance=False` removes only the verified current receipt and clears the flow.
- [ ] Prove stale/unknown receipts fail without mutation and ordinary setup/teardown behavior remains unchanged.
- [ ] Run the six focused files and require PASS; independently review Task 1, then commit it before Task 2.

### Task 2: Execute and recover the dormant manager operation

**Files:** `_setup_manager.py`, its test, synthetic integration test, and coverage absence test.

- [ ] Test `SetupManager.teardown_all()` directly with injected synthetic graph/bindings: valid empty no-op, full preflight, dependents-first dispatch, no setup dispatch, terminal empty ledger, and unchanged v1/v2 bytes on preflight failure.
- [ ] Test CAS plan equality against racing ledger state, action/verifier failures, busy behavior, stale graph/binding recovery, retry verify-first, and cancellation true/false/uncertain outcomes.
- [ ] Translate `BlueprintGraphError` to the manager's structured domain failure before mutation or dispatch.
- [ ] Implement only `SetupManager.teardown_all() -> tuple[int, dict[str, object]]` and the minimum generic tagged-flow branches. Reuse existing runners and dispatch boundary; extract verifier parsing once so cancel distinguishes exact false from uncertainty without changing ordinary semantics.
- [ ] Do **not** add `TeardownAllInterface`, registration, exports, gateway references, dependencies, documentation, or a capability flag.
- [ ] Add synthetic internal integration cases for ordering, receipt removal, interruption/restart/retry, stale/malformed fail-closed behavior, and terminal empty state. Do not invoke a Dispatcher route.
- [ ] Add at most 10 LOC of coverage assertions proving the interface class, declarations, exports, gateway use, runtime dependency, and exact route remain absent. Existing empty-production assertions remain.
- [ ] Run focused manager/integration/coverage tests and the Stage A scope/budget gates. Independently review Task 2, then commit it.

### Gate 0B: Authorize publication before Task 3

**Checkpoint:** This is a publication precondition, not an implementation task, and creates no commit. Its evidence is recorded in the execution record before Task 3 begins.

- [ ] Require the approved activation record described above. Refuse publication if it is absent, incomplete, or ordinary teardown already satisfies the named need.
- [ ] Require exact equality among production managed metadata, bindings, and action calls, plus every fully expanded owner suite green.
- [ ] Recheck Stage A commits, all cumulative per-file caps, and remaining Stage B reserve. Stage B may not rewrite the dormant core to fit publication.

### Task 3: Publish the exact zero-argument interface

**Files:** `_setup_manager.py`, its test, four blueprint paths, `SKILL.md`, runtime dependencies, and coverage test.

- [ ] Add failing signature/coverage tests for exact `setup-interface-manager._rtx.interface.teardown-all@1`, no arguments/stdin, `original: null`, and `resume_original: false`.
- [ ] Add `TeardownAllInterface` as a thin adapter to the proven manager method; register its machine contract, source export, namespace surface, and gateway uses.
- [ ] Regenerate only managed interface/dependency output through the declared sync interface; hand-edit no generated line.
- [ ] Run manager/coverage tests, blueprint validation, sync check, cumulative scope gate, and budget gate. Independently review Task 3, then commit it.

### Task 4B: Prove production publication and document its boundary

**Files:** integration test, coverage test, and `docs/setup.md`.

- [ ] Add public-route empty-ledger and qualifying activation scenarios without changing owner files. Prove exact production metadata/binding/action-call equality and rerun every owner admission suite.
- [ ] Document exact invocation, selected-context scope, ordering, recovery, retained empty ledger, existing individual teardown route, and bounded exclusions.
- [ ] State explicitly that owners prove effect reversal and repeat safety; manager verifier success alone does not.
- [ ] Run all six focused test files, validators, the declared blueprint-sync check interface, every fully expanded owner suite from the activation record, and staged-view precommit with eight workers.
- [ ] Stage exact allowed paths only; reject every untracked/add/delete/rename/copy/type change. Enforce cumulative file caps, Stage A <=580, Stage B incremental <=165, and combined <=745. Commit only after independent review.

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_setup_requirements.py --selector tests/test_setup_interface_manager_coverage.py --selector tests/test_setup_interface_manager_integration.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 1
./repo_checks.py --suite validators --jobs 1
./repo_checks.py --suite precommit --jobs 8 --repository-view staged
git diff --cached --numstat BASE_SHA -- src/officina/blueprints/graph.py tests/test_officina_setup_requirements.py skills/setup-interface-manager/_rtx/_setup_state.py skills/setup-interface-manager/_rtx/tests/test_setup_state.py skills/setup-interface-manager/_rtx/_setup_evaluation.py skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py skills/setup-interface-manager/_rtx/_setup_manager.py skills/setup-interface-manager/_rtx/tests/test_setup_manager.py skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml skills/setup-interface-manager/_rtx/blueprint.yaml skills/setup-interface-manager/blueprints/gateway.yaml skills/setup-interface-manager/blueprint.yaml skills/setup-interface-manager/SKILL.md references/blueprint-schema/runtime_dependencies.json tests/test_setup_interface_manager_coverage.py tests/test_setup_interface_manager_integration.py docs/setup.md
```

### Task 5: Independent final subagent audit

**Files:** None unless a defect is returned to the owning task and fixed in a new reviewed commit. A successful no-change audit is still a task checkpoint and requires an explicit empty commit.

- [ ] Assign fresh subagents who did not implement the relevant task to audit plan conformance, generic-manager boundaries, ordinary-flow regressions, structural publication state, tests, staged/tree scope, and LOC arithmetic.
- [ ] For a Stage A-only completion, require explicit evidence that the dormant core is structurally unreachable and Gate 0B plus Tasks 3/4B remain gated and untouched.
- [ ] For published completion, require exact activation evidence and all Stage B gates. Do not infer publication authorization from synthetic fixtures.
- [ ] Return every finding to the owning implementation task, add a focused regression test where applicable, commit the fix separately, and repeat the independent audit until clear.
- [ ] Once the audit is clear, record the task checkpoint without inventing a file: use `git commit --allow-empty -m "audit: verify teardown-all dormant core"` for Stage A-only completion or `git commit --allow-empty -m "audit: verify teardown-all publication"` after Stage B. Verify that the empty commit's tree equals its parent and record the audit evidence with the commit SHA.

## Acceptance criteria

### Stage A dormant core

- Internal manager execution exhausts every valid synthetic managed receipt through exact injected teardown/verifier bindings with deterministic dependents-first ordering and at most one successful settlement per receipt.
- Invalid inventory blocks all dispatch; persisted current-step and verify-first recovery do not lose or invent completion; ordinary managed setup/teardown behavior remains intact.
- The ledger remains the sole manager manifest, and no owner/platform code, new runner, traversal, registry, or effect inventory is introduced.
- No interface class, declaration, export, gateway use, runtime dependency, generated block, documentation, or Dispatcher/MCP route exposes the operation.
- The final Stage A diff contains only tracked modifications to the ten-path allowlist, respects cumulative file caps and the coverage 10-LOC sub-cap, and is at most 580 changed LOC.

### Stage B publication

- The exact zero-argument route is published only after the approved activation condition and every current owner's complete admission evidence pass.
- Public execution preserves the proven dormant-core semantics and returns no continuation/original resumption.
- The combined diff remains within all 17 paths, every unchanged cumulative per-file cap, the 165-LOC Stage B reserve, and the 745-LOC total.
- No claim extends beyond all valid managed setup receipts in the selected context; broader uninstall/purge remains a separate design.
