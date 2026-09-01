# Managed Setup Uninstall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task by task.

**Goal:** Add one recoverable manager operation that discharges every setup receipt in the selected Famulus context by invoking and settling its declared teardown.

**Architecture:** `setup-interface-manager._rtx.interface.uninstall@1` reads the manager's existing getter-selected ledger, validates the complete receipt set, and executes one deterministic dependents-before-prerequisites teardown plan. It reuses the existing Markdown/Python runners, verifiers, atomic ledger, and recovery protocol; successful completion leaves an empty receipt map and never resumes another request.

**Tech stack:** Python 3.11+, Officina V6 setup graphs, Famulus Dispatcher/MCP, JSON ledger state, and pytest.

**Spec:** `docs/setup.md` sections 1.4-1.5 and the required behavior below.

## Release-one boundary

- Production inventory is exactly `milestone-logging.interface.setup@1`, with teardown `milestone-logging.interface.teardown@1` and verifier `milestone-logging._rtx.interface.teardown-status@1`.
- That setup creates the getter-selected logging directory; its declared teardown intentionally retains the directory and records. Release acceptance is teardown dispatch, verifier settlement, and receipt removal—not filesystem removal.
- Chain and diamond fixtures prove the generic graph order. No parameterized production setup, plugin uninstall, or broader host cleanup enters this release.

## Global constraints

- Scope is every receipt key in the current getter-selected `setup_status`, including receipts with empty or foreign `required_by`. Claims affect root teardown, not uninstall membership.
- Give each receipt one plan entry and at most one successful settlement/removal. Normal uninterrupted execution invokes its declared teardown and verifier once; recovery may re-verify and may rerun only after the verifier reports false.
- The manager accepts only the declared verifier result `{"torn_down": true}`. Whether a teardown fully reverses its setup remains the setup owner's responsibility.
- Preflight the entire receipt set before creating the flow or dispatching teardown. Unknown interfaces, setup-version mismatches, missing managed metadata, or missing finite bindings raise a typed failure and leave receipt and flow state unchanged; only an empty valid ledger yields an empty plan.
- Order only receipt-bearing nodes: form a deterministic dependency-first union from `managed_setup_order()` over sorted receipt keys, deduplicate it, filter out absent receipts, then reverse it. This tears dependents before prerequisites without touching missing or newly declared prerequisites.
- Global uninstall ignores shared claims and tears down each receipted node once. A verified step removes its receipt; failure leaves the current and later receipts unchanged.
- Persist one exact current uninstall step. Recompute the remaining deterministic plan before each dispatch and settlement, require its first step to match the persisted step, and validate its finite binding; an invalid current step becomes recovery-required without selecting an alternate action.
- An active flow makes uninstall busy. Retry verifies before rerunning the exact current action. Cancel clears only the uninstall flow; it does not recreate removed receipts or alter current/unvisited receipts.
- Success means `interfaces == {}`, `active_flow is None`, `original is null`, and `resume_original` is false. Keep the canonical empty ledger file.
- This does not uninstall the Famulus plugin/runtime, development activation, launchers, schedulers, wakeup delivery, Google credentials, cloud/list/calendar data, or any other state not represented by a managed setup receipt.

## File responsibility map

| Responsibility | Files |
|---|---|
| Global plan and settlement | `skills/setup-interface-manager/_rtx/_setup_evaluation.py` |
| Persisted uninstall flow and schema migration | `skills/setup-interface-manager/_rtx/_setup_state.py` |
| Public operation, runners, and recovery | `skills/setup-interface-manager/_rtx/_setup_manager.py` |
| Registered interface contract | `skills/setup-interface-manager/{blueprint.yaml,blueprints/gateway.yaml,SKILL.md}`, `_rtx/{blueprint.yaml,blueprints/rtx-manager.yaml}` |
| Acceptance and user-facing scope | `tests/test_setup_interface_manager_integration.py`, `docs/setup.md` |

---

### Task 1: Model and plan an all-receipts teardown

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_state.py`
- Modify: `skills/setup-interface-manager/_rtx/_setup_evaluation.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_state.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py`

**Interfaces:**
- Produces: `uninstall_plan(graph, ledger) -> tuple[TeardownStep, ...]`; only a valid empty ledger returns `()`.
- Produces: `record_uninstall_success(store, graph, flow_id, step) -> FlowResult`.
- Produces: schema-v2 `ActiveFlow` with `operation: Literal["setup", "teardown", "uninstall"]`; uninstall requires `root is None` and `continuation is None`, while ordinary flows retain both.

- [ ] Add failing state tests for read-only canonical v1 parsing, v2 ordinary/uninstall round-trips, operation-dependent root/continuation validation, first-mutation migration, and rejection of malformed state.
- [ ] Add failing evaluation tests for empty state, chains, diamonds, unclaimed and foreign-claimed receipts, deterministic ordering, shared-node deduplication, and exclusion of missing prerequisites.
- [ ] Add evaluation tests proving unknown or stale receipts raise the typed preflight failure without mutation; distinguish them from a valid empty plan.
- [ ] Run the two focused test files; require the new cases to fail for the expected missing behavior.
- [ ] Parse canonical v1 and v2 without rewriting on reads/status. Make the first validated mutating CAS encode v2; a failed uninstall must leave canonical v1 bytes unchanged.
- [ ] Implement the global plan using `managed_setup_order()`; do not add a second graph traversal.
- [ ] Implement settlement so only the exact current verified step is removed; recompute the plan and persist its first remaining step before advancing.
- [ ] Rerun both focused test files; require PASS.

### Task 2: Execute and recover uninstall

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`

**Interfaces:**
- Consumes: `uninstall_plan()` and `record_uninstall_success()` from Task 1.
- Produces: `SetupManager.uninstall() -> tuple[int, dict[str, object]]`.
- Reuses: `run_markdown(flow_id, interface)`, `run_python(flow_id, interface, stdin)`, `settle(flow_id, interface)`, and `recover(flow_id, action)`.

- [ ] Add failing tests for no-op empty uninstall, full preflight before mutation, exact dependents-first dispatch, the release-one Markdown path, no setup dispatch, and terminal empty-ledger response.
- [ ] Add a preflight test proving a missing finite binding causes zero dispatch and leaves canonical v1/v2 bytes unchanged.
- [ ] Add a racing-ledger test: inside the successful `store.update(start)` transition, recompute the plan from the current predecessor and require equality with the fully preflighted candidate before writing the flow.
- [ ] Add failure tests proving an action/verifier failure retains the current receipt, a current-step graph/binding mismatch becomes recovery-required, and a second lifecycle request is busy.
- [ ] Add recovery tests proving retry verifies first and cancel preserves already removed receipts plus every current/unvisited receipt.
- [ ] Run the manager test; require the new cases to fail for the expected missing behavior.
- [ ] Implement `uninstall()` as a dedicated zero-argument operation. Validate every planned `_binding()` before the CAS that creates/migrates the flow and repeat candidate-plan equality inside that CAS. Reuse the existing step runners and verifier contract; branch settlement and cancel by `operation == "uninstall"`.
- [ ] Rerun the manager test; require PASS.

### Task 3: Register the exact uninstall interface

**Files:**
- Modify: `skills/setup-interface-manager/_rtx/_setup_manager.py`
- Modify: `skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml`
- Modify: `skills/setup-interface-manager/_rtx/blueprint.yaml`
- Modify: `skills/setup-interface-manager/blueprints/gateway.yaml`
- Modify: `skills/setup-interface-manager/blueprint.yaml`
- Regenerate: `skills/setup-interface-manager/SKILL.md`
- Test: `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`
- Test: `tests/test_setup_interface_manager_coverage.py`

**Interfaces:**
- Produces: `setup-interface-manager._rtx.interface.uninstall@1` with no arguments or stdin.

- [ ] Add failing signature and coverage tests requiring the exact zero-argument route, `original: null`, and `resume_original: false`; reject extra arguments and stdin.
- [ ] Register `UninstallInterface`, its machine contract, `_rtx` export, parent namespace surface, and both authored gateway `uses_interfaces` lists. Keep the finite teardown binding map authoritative.
- [ ] Update the authored `SKILL.md` activation/route prose for an exact uninstall invocation. Regenerate the managed interface block with `skill-maker._rtx.interface.sync-blueprints@1`, then run its `--check` route; do not hand-edit the generated block.
- [ ] Run the manager and coverage tests plus the blueprint validator; require PASS.

### Task 4: Prove release-one semantics and document the boundary

**Files:**
- Modify: `tests/test_setup_interface_manager_integration.py`
- Modify: `docs/setup.md`

- [ ] Add one uninterrupted production-canary scenario proving the exact milestone teardown/verifier route, receipt removal, no setup dispatch, and terminal empty state. Do not assert directory deletion or setup/teardown equivalence because its declared teardown intentionally retains logs.
- [ ] Add stale/malformed-receipt fail-closed and interruption/restart/retry integration cases. Keep cancellation, busy, empty/idempotent, binding-failure, and graph-order detail in focused manager/state/evaluation tests.
- [ ] Document the exact invocation, selected-context scope, ordering, recovery, empty-ledger result, and exclusions. State that setup owners—not the manager—own teardown completeness.
- [ ] Run:

```bash
./repo_checks.py --task tests:shared --selector tests/test_setup_interface_manager_coverage.py --selector tests/test_setup_interface_manager_integration.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --jobs 1
./repo_checks.py --suite validators --jobs 1
```

- [ ] Stage only the task-owned files, inspect `git diff --cached --name-only`, and run `./repo_checks.py --suite precommit --jobs 1 --repository-view staged`. Require PASS before committing.

## Acceptance criteria

- One explicit no-argument interface exhausts every valid receipt in the selected `setup_status` through its declared teardown and verifier, with one plan entry and at most one successful settlement per receipt.
- Ordering is deterministic, dependents-first, deduplicated, and limited to receipt-bearing nodes.
- Invalid inventory blocks all teardown work; the persisted exact current step and verify-first recovery never lose or invent completion.
- Completion is an empty canonical ledger with no continuation or original-request resumption.
- No success claim depends on measuring whether teardown reverses setup, and no state outside managed setup receipts is touched.
