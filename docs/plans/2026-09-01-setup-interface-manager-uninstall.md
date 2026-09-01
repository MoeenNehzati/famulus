# Deferred Phase-Two Managed Setup Teardown-All Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After production has a demonstrated bulk-reset need, add one recoverable manager operation that removes the complete current state owned by every admitted fixed managed setup in the selected Famulus context.

**Architecture:** `setup-interface-manager._rtx.interface.teardown-all@1` treats the existing getter-selected setup ledger as the manifest of manager-owned state. It validates the complete receipt set, derives one deterministic dependents-before-prerequisites plan from live managed-setup metadata, and reuses the existing runners, verifiers, atomic state transitions, and recovery protocol. Individual exact setup-interface teardowns continue to use `begin(teardown, ROOT_SETUP_INTERFACE, ...)`.

**Tech Stack:** Python 3.11+, Officina V6 setup graphs, Famulus Dispatcher/MCP, JSON ledger state, and pytest.

**Spec:** `docs/setup.md` sections 1.4-1.5 and the required behavior below.

## Phase ordering and activation boundary

- `teardown-all` is the correct name. The operation tears down managed setup state; it does not uninstall the Famulus plugin or runtime.
- **Phase one is owner-only.** Admit each first production managed setup in its own exact plan, then use the existing `begin(teardown, ROOT_SETUP_INTERFACE, ...)`, runners, verifier settlement, and `recover@1`. Phase one adds no schema-v2 flow, global planner, bulk interface, or global cancellation semantics.
- The current production graph has no `setup_management` declarations and `PRODUCTION_BINDINGS` is empty. This document does not choose, admit, or modify the first production owner and must not be executed merely to make `teardown-all` nonempty.
- **Phase two activates only when one of these is documented and approved:** (a) at least two independent production managed roots exist and the release decision explicitly requires one selected-context reset operation, or (b) a named bulk consumer has requirements that repeated ordinary root teardown cannot satisfy. Root count alone does not silently authorize implementation.
- There is no central Famulus `setup.sh` lifecycle. Setup exports may be Markdown or Python, and persistent features have separate owners. The manager records a receipt only after the exact declared verifier succeeds; it must not attempt to discover shell invocations or infer effects from process execution.
- The existing ledger is the minimal managed-state manifest: each receipt pins the setup interface/version and root claims. The exact teardown interface/version/verifier/kind remain authoritative live blueprint metadata. Do not duplicate those bindings in a second manifest.
- Phase two supports only already-admitted fixed whole-node managed setup. Do not persist setup arguments, stdin, environment, commands, credentials, paths, or arbitrary verifier payloads. A parameterized setup must first define an owner-issued, nonsensitive teardown receipt and its validation contract in a separate owner plan; it must not be smuggled into this plan.
- This operation is a prerequisite for, not a substitute for, a future Famulus uninstall/purge orchestrator. That separate design must coordinate manager teardown, owner-specific feature teardown, host/plugin installation artifacts, and explicit credential/data retention choices.

**Execution precondition:** Do not begin until an approved activation record identifies the qualifying roots or named bulk consumer, explains why ordinary teardown is insufficient, inventories every current production managed owner, and supplies fully expanded test commands for every owner's teardown-equivalence, repeat-safety, lifecycle-epoch, and recovery suite. It must also prove exact equality among production managed metadata, `PRODUCTION_BINDINGS`, and the required `PRODUCTION_ACTION_CALLS`. Without that evidence, this document is a deferred phase-two plan, not an implementation-ready first release.

### Managed-setup admission contract

- Setup and teardown actions must both be Markdown or both be executable Python. Both actions and both verifiers must take zero arguments for this phase; complete preflight also rejects a nonempty finite `binding.arguments`.
- The setup interface version is the complete lifecycle epoch. Any change to setup action, teardown action, either verifier, execution kind, or their externally visible state semantics requires a setup-version bump.
- The owner must prove in focused tests that setup followed by teardown returns all owned state to the declared torn-down baseline, that teardown is repeat-safe after partial execution, and that cancel followed by a new teardown cannot duplicate an unsafe external effect.
- Blueprint lifecycle metadata declares the semantics. `PRODUCTION_BINDINGS` and `PRODUCTION_ACTION_CALLS` are the finite executable allowlist, and manager preflight requires exact agreement between them.

## End-goal accounting

| Side-effect class | Current route/status | `teardown-all@1` result |
|---|---|---|
| Admitted fixed managed setup state | Exact declared teardown and verifier | Removed |
| One managed setup closure | Existing `begin(teardown, ROOT_SETUP_INTERFACE, ...)` | Unchanged exact route |
| Canonical setup ledger, parent, and lock residue | Manager operational state; no purge route | Empty ledger retained |
| MCP plugin-data and milestone directories/logs | No bulk removal route | Retained |
| Recurring native registrations and health checks | `recurring-tasks._rtx.interface.scripts-remove-context@1` | Outside manager; definitions, history, and logs retained |
| Wakeup delivery registration | `wakeup.interface.setup@1 teardown` | Outside manager; queued jobs and session policies retained |
| Optional launchers, worker/profile configuration, and command files | Setup route exists; complete teardown route missing | Retained |
| Selected-Python package mutations | Setup/repair exists; no safe generic rollback | Retained |
| Email-owned account credentials | `email-client._rtx.interface.accounts-remove@1 --purge-credentials` | Outside manager; only email-owned credentials removable |
| Shared Google clients, tokens, descriptors, and server-side grants | Reference-aware/manual cleanup; local deletion does not revoke authority | Retained |
| Plugin registration/cache, hooks, access roots, development activation/homes, and historical installation residue | Host-native, external, or missing cleanup routes | Retained |
| Local lists, plans, attachments, queues, logs, caches, and other operational/user data | Owner-specific retention decisions | Retained |
| Remote data and irreversible external actions such as sent mail or feedback | No general reversal is possible | Retained and explicitly reported |

This is a bounded current inventory, not proof that every historical effect is discoverable. No acceptance statement may shorten “all managed setup receipts in the selected context” to “all Famulus side effects.” Define the broader end goal as removing current persistent Famulus-owned integration/configuration residue while explicitly reporting retained data, credentials, external authority, historical residue, and irreversible actions. A future uninstall/purge orchestrator needs its own owner/effect inventory and retention manifest; do not fold that state into setup receipts.

## Global constraints

- Scope is every receipt key in the current getter-selected `setup_status`, including receipts with empty or foreign `required_by`. Claims affect ordinary root teardown, not `teardown-all` membership.
- Give each receipt one plan entry and at most one successful settlement/removal. Normal uninterrupted execution invokes its declared teardown and verifier once; recovery may re-verify and may rerun only after the verifier reports false.
- The manager accepts only the declared verifier result `{"torn_down": true}`. Whether a teardown fully reverses its setup remains the setup owner's contract and test responsibility.
- Preflight the complete receipt set before creating a flow or dispatching teardown. Unknown interfaces, setup-version mismatches, missing managed metadata, or missing finite bindings raise a typed failure and leave receipt and flow state byte-for-byte unchanged; only an empty valid ledger yields an empty plan.
- Order only receipt-bearing nodes: form a deterministic dependency-first union from `managed_setup_order()` over sorted receipt keys, deduplicate it, filter out absent receipts, then reverse it. Do not add a second graph traversal.
- `teardown-all` ignores shared claims and tears down each receipted node once. A verified step removes its receipt; failure leaves the current and later receipts unchanged.
- Persist one exact current teardown step. Recompute the remaining deterministic plan before each dispatch and settlement, require its first step to match the persisted step, and validate its finite binding. An invalid current step becomes recovery-required without selecting an alternate action.
- An active flow makes `teardown-all` busy. Retry verifies before rerunning the exact current action. Cancel first runs the current teardown verifier. If true, one atomic transition removes the verified current receipt and clears the flow without selecting a successor; if false, it clears the flow without removing the current receipt; if verification is uncertain, it retains the flow as recovery-required. Both determinate outcomes leave `active_flow is None`, preserve every later receipt, and permit an explicit new flow.
- Persisted success means `interfaces == {}` and `active_flow is None`; keep the canonical empty ledger file. The response separately requires `original is null` and `resume_original` is false.

## Scope harness against overscoping

**This section is the implementation harness against overscoping.** The path allowlist, exact mutation descriptions, and LOC caps are normative release constraints. Passing tests does not excuse exceeding them.

Define changed LOC as additions plus deletions reported by `git diff --numstat BASE_SHA`; a one-line replacement costs two changed LOC. Generated files count. Binary diffs are forbidden. This phase-two plan has a hard three-digit ceiling of **745 changed LOC** across the exact paths below.

Per-file caps cannot be borrowed. No file may be created, deleted, renamed, or moved. No path outside this table may change. In particular, this plan does not change production-owner actions, verifiers, blueprints, tests, or `skills/setup-interface-manager/_rtx/_setup_dispatches.py`; those must already exist from phase one. If implementation needs another path, a larger cap, a second ledger/manifest/traversal, or a new runner, stop before editing it and obtain a revised reviewed plan.

| Exact core path | Exact allowed mutation | Changed-LOC cap |
|---|---|---:|
| `src/officina/blueprints/graph.py` | Change `_managed_setup_for_export()` validation only: reject setup/teardown execution-kind mismatch and nonzero action/verifier arguments. Do not change graph dataclasses, ordering, loading, or traversal. | 30 |
| `tests/test_officina_setup_requirements.py` | Add focused accept/reject cases for the two lifecycle-shape invariants above; change no unrelated fixtures. | 40 |
| `skills/setup-interface-manager/_rtx/_setup_state.py` | Add schema-v2 tagged-flow encode/decode and validation; retain read-only v1 parsing; allow null root/continuation only for `teardown-all`; require empty global `verified_steps`. Remove no ledger security checks or atomic-store behavior. | 60 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_state.py` | Add v1 compatibility, v2 round-trip/migration, nullability, empty-`verified_steps`, and malformed-state tests. | 45 |
| `skills/setup-interface-manager/_rtx/_setup_evaluation.py` | Add `teardown_all_plan()`; widen `FlowResult.operation`; extract one shared teardown-settlement core with ordinary/global wrappers and `advance=False` cancel settlement. Remove no ordinary setup/teardown behavior. | 80 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py` | Add empty/chain/diamond/stale/foreign-claim/order tests and shared-core ordinary/global regression cases. | 65 |
| `skills/setup-interface-manager/_rtx/_setup_manager.py` | Add `SetupManager.teardown_all()` and `TeardownAllInterface`; branch tagged flow selection, settlement, responses, retry, and cancel; extract shared verifier decoding with cancellation-only tri-state; translate `BlueprintGraphError`. Reuse existing runners and dispatch boundary. | 130 |
| `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py` | Add signature, preflight, race, dispatch order, recovery, tri-state cancel, stale-inventory, and terminal-response tests; retain existing ordinary-flow tests unchanged except shared-helper adaptations. | 110 |
| `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml` | Add the zero-argument `teardown-all@1` machine-interface declaration and its existing-helper dependencies; remove no interface. | 15 |
| `skills/setup-interface-manager/_rtx/blueprint.yaml` | Add the `teardown-all@1` source export only. | 10 |
| `skills/setup-interface-manager/blueprints/gateway.yaml` | Add the exact `teardown-all@1` used-interface reference only. | 10 |
| `skills/setup-interface-manager/blueprint.yaml` | Add the exact private namespace surface/export for `teardown-all@1` only. | 10 |
| `skills/setup-interface-manager/SKILL.md` | Change authored activation/route prose and regenerate only the managed interface block for `teardown-all@1`; hand-edit no generated line. | 25 |
| `references/blueprint-schema/runtime_dependencies.json` | Regenerate dependency entries mechanically; no authored edits or unrelated reorder. | 10 |
| `tests/test_setup_interface_manager_coverage.py` | Add exact registration/signature assertions and require the approved activation condition; remove the current empty-production assertion only after activation evidence exists. | 25 |
| `tests/test_setup_interface_manager_integration.py` | Add empty-ledger, fixture teardown-all, stale-state, interruption/retry, and qualifying production-root or named-bulk-consumer scenarios; add no duplicate focused unit cases. | 55 |
| `docs/setup.md` | Add the exact route, selected-context semantics, recovery outcomes, retained empty-ledger statement, and bounded exclusions; change no unrelated setup guidance. | 25 |
| **Core total** | **17 exact paths; no other core changes** | **745** |

The table above is the complete phase-two implementation allowlist. Phase-one owner work is a prerequisite, not part of this diff or LOC budget.

---

### Task 0: Lock the scope harness

**Files:** None.

- [ ] Record the exact `BASE_SHA` from a clean isolated worktree, the approved activation record, every current production managed owner and its fully expanded admission-test command, and this exact path table in the execution record. Refuse to start if activation evidence is absent, metadata/binding/action-call equality fails, any owner suite is missing or red, or the worktree is not clean.
- [ ] Before mutation, run `git status --porcelain=v1 --untracked-files=all` and require empty output. Run `git diff --name-status BASE_SHA` and require empty output.
- [ ] Before mutation, call `skill-maker._rtx.interface.sync-blueprints@1` with `--check` and require success, then run every focused test and validator command listed in Tasks 1-4 plus every owner command from the activation record. Stop if any baseline is red; do not let the later repository-wide generator absorb pre-existing drift.
- [ ] After every task, run `git status --porcelain=v1 --untracked-files=all` and `git diff --name-status BASE_SHA`. Permit only tracked `M` entries on the 17 exact paths; reject every untracked, `A`, `D`, `R`, `C`, type-change, or out-of-allowlist entry.
- [ ] After every task, run the fully expanded command below. Reject binary output, any per-file cap breach, or total churn above 745. Record the output and arithmetic in the task review.

```bash
git diff --numstat BASE_SHA -- src/officina/blueprints/graph.py tests/test_officina_setup_requirements.py skills/setup-interface-manager/_rtx/_setup_state.py skills/setup-interface-manager/_rtx/tests/test_setup_state.py skills/setup-interface-manager/_rtx/_setup_evaluation.py skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py skills/setup-interface-manager/_rtx/_setup_manager.py skills/setup-interface-manager/_rtx/tests/test_setup_manager.py skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml skills/setup-interface-manager/_rtx/blueprint.yaml skills/setup-interface-manager/blueprints/gateway.yaml skills/setup-interface-manager/blueprint.yaml skills/setup-interface-manager/SKILL.md references/blueprint-schema/runtime_dependencies.json tests/test_setup_interface_manager_coverage.py tests/test_setup_interface_manager_integration.py docs/setup.md
```

- [ ] Treat a required out-of-allowlist edit or budget breach as a plan failure: stop, explain the need, and revise/review the plan before continuing. Do not “temporarily” make the edit.

### Task 1: Model and plan all-receipts teardown

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_state.py`
- Modify: `skills/setup-interface-manager/_rtx/_setup_evaluation.py`
- Modify: `src/officina/blueprints/graph.py`
- Test: `tests/test_officina_setup_requirements.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_state.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py`

**Interfaces:**
- Produces: `teardown_all_plan(graph, ledger) -> tuple[TeardownStep, ...]`; only a valid empty ledger returns `()`.
- Produces: shared `_record_teardown_step_success(store, graph, flow_id, step, operation, *, advance=True) -> FlowResult`, with the existing `record_teardown_success()` and new `record_teardown_all_success()` as thin ordinary/global policy wrappers. `advance=False` removes only the verified current global receipt and clears the flow for cancellation.
- Produces: schema-v2 `ActiveFlow` with `operation: Literal["setup", "teardown", "teardown-all"]`; `teardown-all` requires `root is None`, `continuation is None`, and `verified_steps == ()`, while ordinary flows retain their existing fields.
- Widens: `FlowResult.operation` to the same three-operation literal.

- [ ] Add failing graph tests requiring setup/teardown kind equality and zero arguments on both actions and both verifiers.
- [ ] Add failing state tests for read-only canonical v1 parsing, v2 ordinary/`teardown-all` round-trips, operation-dependent root/continuation validation, first-mutation migration, and malformed-state rejection.
- [ ] Add failing evaluation tests for empty state, chains, diamonds, unclaimed and foreign-claimed receipts, deterministic ordering, shared-node deduplication, and exclusion of missing prerequisites.
- [ ] Add tests proving unknown or stale receipts raise the typed preflight failure without mutation; distinguish them from a valid empty plan.
- [ ] Run the graph, state, and evaluation focused test files and require the new cases to fail for the expected missing behavior.
- [ ] Parse canonical v1 and v2 without rewriting on reads/status. Make the first validated mutating CAS encode v2; a failed `teardown-all` must leave canonical v1 bytes unchanged.
- [ ] Implement the global plan by unioning `managed_setup_order()` results; do not add another dependency traversal.
- [ ] Extract the existing ordinary teardown settlement into one private operation-aware core. Preserve `record_teardown_success()` as a wrapper; add only a thin global wrapper whose policy removes the exact receipt and uses `teardown_all_plan()`. Its `advance=False` cancellation path reuses exact-current validation and removal but clears the flow instead of selecting the next step. Do not duplicate validation, removal, recomputation, advancement, or completion logic.
- [ ] Rerun the graph, state, and evaluation focused tests and require PASS.

### Task 2: Execute and recover `teardown-all`

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`

**Interfaces:**
- Consumes: `teardown_all_plan()` and the shared teardown-settlement core from Task 1.
- Produces: `SetupManager.teardown_all() -> tuple[int, dict[str, object]]`.
- Reuses the existing action runners and verifier JSON contract. Extracts one shared verifier decoder and adds a cancellation-only tri-state outcome: exact successful `{"torn_down": true}`, exact successful `{"torn_down": false}`, or uncertain. The existing Boolean verifier wrapper keeps its current behavior outside global cancellation. Widens `_flow_step()`, `_settle_verified()`, `_result_response(original: ContinuationIdentity | None, ...)`, retry responses, and cancellation only where the tagged global operation requires it.

- [ ] Add failing tests for no-op empty teardown, full preflight before mutation, exact dependents-first dispatch, no setup dispatch, and terminal empty-ledger response.
- [ ] Add a preflight test proving a missing finite binding causes zero dispatch and leaves canonical v1/v2 bytes unchanged.
- [ ] Add a racing-ledger test: inside the successful `store.update(start)` transition, recompute the plan from the current predecessor and require equality with the fully preflighted candidate before writing the flow.
- [ ] Add failure tests proving an action/verifier failure retains the current receipt, a current-step graph/binding mismatch becomes recovery-required, and a second lifecycle request is busy.
- [ ] Add recovery tests proving retry verifies first and cancellation preserves already removed receipts; current-receipt behavior follows the outcome-specific rules below.
- [ ] Add cancellation tests for verifier true, exact false, nonzero exit, dispatch failure, malformed/unsupported output, and cancel-then-new-flow behavior. For true, require current receipt removal, later receipt preservation, and no active flow; only exact successful `{"torn_down": false}` may preserve all remaining receipts and clear the flow. Every nonzero, failed, malformed, or unsupported result is uncertain and must retain the exact active flow. Require no unsafe action rerun after a true or uncertain result.
- [ ] Add a stale-inventory test proving `BlueprintGraphError` is translated into the manager's structured domain failure before mutation or managed dispatch.
- [ ] Run the manager test and require the new cases to fail for the expected missing behavior.
- [ ] Implement `teardown_all()` as a dedicated zero-argument operation. Validate every planned `_binding()`, including empty arguments, before the CAS that creates/migrates the flow and repeat candidate-plan equality inside that CAS. Translate `BlueprintGraphError` at the public manager boundary. Refactor verifier parsing once so cancellation can distinguish exact false from uncertainty without changing ordinary setup/teardown semantics. Branch flow selection, response identity, settlement, retry, and cancel only where `operation == "teardown-all"` requires it.
- [ ] Rerun the manager test and require PASS.

### Task 3: Register the exact `teardown-all` interface

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Modify: `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml`
- Modify: `skills/setup-interface-manager/_rtx/blueprint.yaml`
- Modify: `skills/setup-interface-manager/blueprints/gateway.yaml`
- Modify: `skills/setup-interface-manager/blueprint.yaml`
- Regenerate: `skills/setup-interface-manager/SKILL.md`
- Regenerate: `references/blueprint-schema/runtime_dependencies.json`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
- Test: `tests/test_setup_interface_manager_coverage.py`

**Interfaces:**
- Produces: `setup-interface-manager._rtx.interface.teardown-all@1` with no arguments or stdin.

- [ ] Add failing signature and coverage tests requiring the exact zero-argument route, `original: null`, and `resume_original: false`; reject extra arguments and stdin.
- [ ] Register `TeardownAllInterface`, its machine contract, `_rtx` export, parent namespace surface, and both authored gateway `uses_interfaces` lists. Keep blueprint metadata authoritative for semantics and the finite binding map authoritative for executable permission.
- [ ] Update authored activation/route prose for the exact `teardown-all` invocation. Regenerate the managed interface block with `skill-maker._rtx.interface.sync-blueprints@1`, then run its `--check` route; do not hand-edit the generated block.
- [ ] Run the manager and coverage tests plus the blueprint validator and require PASS.

### Task 4: Prove semantics and document the honest boundary

**Files:**
- Modify: `tests/test_setup_interface_manager_integration.py`
- Modify: `tests/test_setup_interface_manager_coverage.py`
- Modify: `docs/setup.md`

- [ ] Add an empty-ledger integration case on a qualifying nonempty managed graph proving the exact `teardown-all@1` route is a successful idempotent no-op with no managed action or verifier dispatch; the setup-status path getter still runs.
- [ ] Add registered-fixture integration cases for dependents-first exact teardown/verifier dispatch, receipt removal, interruption/restart/retry, and terminal empty state.
- [ ] Add stale/malformed-receipt fail-closed integration cases. Keep cancellation, busy, binding failure, and detailed graph ordering in focused manager/state/evaluation tests.
- [ ] Prove the approved activation condition without changing owner files: exercise at least two already-admitted independent production roots when using the root-count trigger, or the exact named bulk-consumer scenario when using that trigger. Assert exact equality among all current production managed metadata, finite bindings, and required action calls. Rerun every current production owner's fully expanded admission suite before exposing `teardown-all@1`.
- [ ] Document the exact invocation, selected-context scope, ordering, recovery, retained empty ledger, individual exact teardown route, and the bounded side-effect inventory from this plan.
- [ ] State that setup owners must prove teardown equivalence and repeat safety for every managed effect; verifier success alone does not establish either property.
- [ ] Run:

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_setup_requirements.py --selector tests/test_setup_interface_manager_coverage.py --selector tests/test_setup_interface_manager_integration.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 1
./repo_checks.py --suite validators --jobs 1
```

- [ ] Stage only the approved exact paths. Run `git status --porcelain=v1 --untracked-files=all` plus `git diff --cached --name-status BASE_SHA`; permit only staged `M` entries on the 17-path table and reject all untracked/add/delete/rename/copy/type-change entries. Rerun the Task 0 budget command with `--cached`; recheck every per-file cap and the 745-LOC ceiling. Rerun every current owner suite, `skill-maker._rtx.interface.sync-blueprints@1 --check`, and `./repo_checks.py --suite precommit --jobs 1 --repository-view staged`. Require all gates to pass before committing.

## Acceptance criteria

- `setup-interface-manager._rtx.interface.teardown-all@1` exhausts every valid managed setup receipt in the selected context through its exact declared teardown and verifier, with one plan entry and at most one successful settlement per receipt.
- Ordering is deterministic, dependents-first, deduplicated, and limited to receipt-bearing nodes.
- Invalid inventory blocks all teardown work; the persisted exact current step and verify-first recovery never lose or invent completion.
- Completion is an empty canonical ledger with no continuation or original-request resumption.
- Individual managed teardown continues through `begin(teardown, ROOT_SETUP_INTERFACE, ...)` and shares the same runners, verifiers, and settlement machinery.
- The ledger remains the only managed-state manifest; phase two adds no command log, setup-script observer, secret-bearing receipt, or parallel teardown registry.
- The interface is published only after the approved activation condition is demonstrated and every current production managed owner—not only the trigger roots—passes metadata/binding/action-call equality plus its complete admission suite; this plan adds or changes no owner.
- The final staged diff contains only tracked modifications to the 17-path allowlist, contains no untracked/add/delete/rename/copy/type-change entry, respects every per-file cap, and never exceeds 745 changed LOC. This is the harness against overscoping, not an advisory estimate.
- Phase two makes no claim to reverse unmanaged Famulus side effects. The broader uninstall/purge end goal remains incomplete until a separately reviewed orchestrator covers or explicitly retains every excluded side-effect class.
