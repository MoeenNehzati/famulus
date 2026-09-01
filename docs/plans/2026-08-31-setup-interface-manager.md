# Setup Interface Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task by task.

**Goal:** Reliably detect when an opted-in interface needs setup, switch to its recursive setup stack, record verified completion, and return to the original request exactly once.

**Architecture:** Reuse the V6 `setup_requires_setup_of` graph and `setup_order()`. A hidden `setup-interface-manager` owns one atomic JSON ledger at the `setup_status` path returned by `common.interface.famulus-paths-get`. Generated Markdown gates and Famulus MCP preflight enforce setup. Setup and teardown execute only declared interfaces; setup is dependency-first and teardown reverses that order while preserving shared dependencies.

**Tech stack:** Python 3.11+, Officina V6 blueprints, `RepositoryBlueprintGraph`, `PythonMachineInterface`, Famulus MCP, pytest, and `repo_checks.py`.

## Release boundary and prerequisites

- Treat `common.interface.famulus-paths-get@1 setup-status` as the sole path source. The getter implementation and path choice are a prerequisite supplied by the Famulus-path work; this plan does not change them.
- The execution handoff must supply the exact reviewed commit containing this plan and the completed getter change. Before Task 1, compare that commit to `git rev-parse HEAD` and verify that the getter returns an absolute path in plugin context. Execute from a clean isolated worktree; stop on a mismatch. The hash cannot be pinned while the prerequisite change is still uncommitted.
- `setup-python-environment` remains session-start-hook bootstrap. It never opts in and never receives a generated gate.
- A `.interface.setup` export alone does not activate management. Opt-in is explicit metadata.
- Release one manages one safe production canary, Markdown `milestone-logging`. The Python runner is exercised against a registered test fixture; migration of parameterized production setup such as `wakeup` is deferred until receipts can identify configuration instances.
- Managed setup is Boolean whole-node state. Parameterized or partial state is ineligible.
- Generated Markdown and Famulus MCP are the enforcement surfaces. Direct shell dispatcher calls and unauthorized ledger edits are outside release one.
- Setup conversation alone never activates the manager. Activation requires a generated gate, an MCP `setup_required`/`setup_managed` result, an explicit manager status call, or an exact managed setup/teardown invocation.
- One lifecycle flow may mutate a ledger at a time. Atomic writes prevent corruption; `setup_busy` prevents duplicate managed actions on these enforcement surfaces.

## File responsibility map

| Responsibility | Owner |
|---|---|
| Managed metadata and graph projection | `src/officina/blueprints/graph.py` plus V6 schemas |
| Path selection | existing `common.interface.famulus-paths-get` contract |
| Ledger parsing, confinement, and atomic mutation | `skills/setup-interface-manager/_rtx/_setup_state.py` |
| Setup/teardown evaluation | `skills/setup-interface-manager/_rtx/_setup_evaluation.py` |
| Finite orchestration and runners | `skills/setup-interface-manager/_rtx/_setup_manager.py`, `_setup_dispatches.py` |
| Markdown enforcement | existing blueprint syncer generated block |
| Executable enforcement | `mcp_server.py` |
| Production evidence | canary-owned verifier interfaces |

## Runtime contracts

### Opt-in metadata

```yaml
exports:
  example.interface.setup:
    source_interface: example.source.setup.interface.default
    setup_requires_setup_of: []
    setup_management:
      setup_verifier: {interface: example.interface.setup-status, version: 1}
      teardown:
        interface: example.interface.teardown
        version: 1
        verifier: {interface: example.interface.teardown-status, version: 1}
```

Validation requires that metadata occurs only on a public `.interface.setup` export; teardown and both verifier exports exist in the same top-level module at the pinned versions; every managed closure member is managed; setup and teardown use dedicated sources; and `setup-python-environment` cannot opt in. Verifiers are executable and read-only. The setup verifier returns exactly `{"set_up": boolean}`; the teardown verifier returns exactly `{"torn_down": boolean}`. Release-one verifiers take no arguments.

The free function `managed_setup_order(graph, root)` delegates to the existing free function `setup_order(graph, root)` and then validates metadata. It must not implement another traversal.

### Ledger and shared-dependency claims

The getter-selected file contains only manager state:

```json
{
  "schema_version": 1,
  "interfaces": {
    "leaf.interface.setup": {
      "version": 1,
      "required_by": ["root.interface.setup"]
    }
  },
  "active_flow": null
}
```

An exact interface/version receipt means its verifier passed. `required_by` records the managed roots whose authorized use still claims that setup. This provenance is required: a version-only receipt cannot distinguish a shared dependency from one installed solely for the root being torn down.

When an ordinary managed target is authorized, a ready closure atomically adds its root to `required_by` on every closure receipt. During setup, each verified step records its receipt and root claim. Repeated authorization is idempotent.

A malformed/unsupported ledger fails closed. Structurally valid orphan records are preserved but ignored. The manager creates an absent parent directory with mode `0700` after rejecting symlink/non-directory components, then atomically creates/replaces the ledger with mode `0600`. It never accepts a caller-supplied ledger path.

`active_flow` contains `flow_id`, operation, root, current step, verified steps, and non-sensitive continuation identity. Arguments, stdin, environment, and verifier output never enter the ledger.

### Status, invalidation, and stacks

For dependency-first order `[leaf.setup, parent.setup, root.setup]`, the first missing or stale receipt invalidates that step and its dependent suffix. A stale parent therefore requires `[parent.setup, root.setup]`, even if the root receipt is exact. The returned stack is reversed so `pop()` yields the next dependency-first step.

Explicit invalidation removes the selected receipt and every managed dependent receipt whose current `setup_order()` contains it. Claim loss caused by invalidation is safe: each affected root fails closed and rebuilds its current closure on next use.

```python
@dataclass(frozen=True)
class SetupReceipt:
    version: int
    required_by: frozenset[str]

@dataclass(frozen=True)
class SetupStep:
    setup_interface: str
    setup_version: int
    teardown_interface: str
    teardown_version: int
    setup_verifier_interface: str
    setup_verifier_version: int
    kind: Literal["markdown", "python"]

@dataclass(frozen=True)
class TeardownStep:
    setup_interface: str
    setup_version: int
    teardown_interface: str
    teardown_version: int
    teardown_verifier_interface: str
    teardown_verifier_version: int
    kind: Literal["markdown", "python"]
    action: Literal["run-teardown", "release-claim"]
```

### Finite manager protocol

Public operations are:

```text
setup-interface-manager.interface.status TARGET_INTERFACE
setup-interface-manager.interface.authorize TARGET_INTERFACE ORIGINAL_CALLER ORIGINAL_INTERFACE ORIGINAL_VERSION
setup-interface-manager.interface.begin OPERATION ROOT_SETUP ORIGINAL_CALLER ORIGINAL_INTERFACE ORIGINAL_VERSION
setup-interface-manager.interface.run-markdown FLOW_ID INTERFACE
setup-interface-manager.interface.run-python FLOW_ID INTERFACE
setup-interface-manager.interface.settle FLOW_ID INTERFACE
setup-interface-manager.interface.invalidate SETUP_INTERFACE
setup-interface-manager.interface.recover FLOW_ID ACTION
```

`OPERATION` is `setup` or `teardown`; `ACTION` is `retry` or `cancel`. There is no `accept-verified`: only the declared verifier establishes completion. `run-python` accepts one JSON request on stdin containing only arguments declared by the exact canary interface; it never accepts a path or interface substitution. `settle` accepts no caller evidence and invokes the verifier itself.

Every operation returns one JSON object with `schema_version`, `flow_id`, `operation`, `state`, `current_step`, redacted `original`, and `resume_original`. `state` is `ready`, `run-step`, `awaiting-settlement`, `busy`, `failed`, or `recovery-required`; `current_step` is null unless actionable. Domain failures use exit 2, malformed calls use exit 64, and success uses exit 0.

`status` is read-only and instead returns `code` (`unmanaged`, `ready`, `setup_required`, or `setup_busy`), `root_setup_interface`, and the complete `pending_stack`; unavailable fields are null/empty. `setup_required` orders the stack for `pop()` as specified above. All status outcomes are successful queries with exit 0; only malformed input/ledger uses exit 2 or 64.

`authorize` succeeds only for `unmanaged` or `ready`. For ready managed state it atomically adds the root claim to every closure receipt and returns `resume_original: true`; for pending or busy state it fails closed. This is the only ready-path claim transition used by both enforcement surfaces.

Normative setup sequence:

```text
status(original)
→ obtain permission if setup_required
→ begin(setup, root, continuation)
→ run current exact setup interface
→ invoke its verifier
→ record receipt/root claim
→ return next step and repeat
→ status(original) == ready
→ authorize(original) records closure claims
→ retry original request exactly once
```

Python `run-python` performs run, verification, recording, and next-step return in one call. Markdown `run-markdown` returns the exact instructions and `awaiting-settlement`; after the agent follows them, `settle` independently runs the verifier, records only `set_up: true`, and returns the next step.

### Teardown

An exact managed teardown invocation is intercepted by the generated Markdown gate or MCP and routed to `begin("teardown", ...)`; it never launches directly. The plan is `reversed(setup_order(root))`.

For each candidate, remove the root claim without external teardown when another root remains in `required_by`. Otherwise run the declared teardown and require its verifier to return `{"torn_down": true}` before removing the receipt. Failure leaves that receipt/claim unchanged and stops before later dependencies. A terminal step clears the active flow and returns `ready` without resuming an ordinary operation.

`cancel` atomically removes the cancelled root from `required_by` on every step listed in that flow's `verified_steps`, preserves the receipts themselves, and clears `active_flow`. This prevents an incomplete root from becoming a ghost shared-dependency claimant while retaining verified installed state. It never guesses whether an interrupted current action completed. `retry` runs the verifier first and then either settles or reruns the exact current step.

## Implementation tasks

### Task 1: Add managed metadata and graph projection

**Files:** `references/blueprint-schema/module.schema.json`, `tests/fixtures/blueprint_schemas/v6/module.schema.json`, `src/officina/blueprints/graph.py`, `tests/test_officina_setup_requirements.py`.

- [ ] Add failing tests for every validation rule above, including unmanaged closure members and bootstrap exclusion.
- [ ] Run `./repo_checks.py --task tests:shared --selector tests/test_officina_setup_requirements.py --jobs 1`; expect FAIL because metadata is not projected.
- [ ] Add immutable projection fields for setup, teardown, both verifiers, versions, and source kind. Implement `managed_setup_order()` around `setup_order()`.
- [ ] Rerun; expect PASS. Commit the listed files as `feat: declare managed setup lifecycle metadata`.

### Task 2: Implement confined ledger I/O and claims

**Files:** create `skills/setup-interface-manager/_rtx/_setup_state.py` and `skills/setup-interface-manager/_rtx/tests/test_setup_state.py`; modify `src/officina/common/atomic_files.py`, `src/officina/common/blueprints/atomic-files.yaml`, and `tests/test_officina_atomic_files.py`.

```python
class LedgerStore:
    def __init__(self, resolved_getter_path: Path) -> None: ...
    def read(self) -> SetupLedger: ...
    def compare_and_update(self, previous: SetupLedger, next_: SetupLedger) -> None: ...
```

Only the manager constructs `LedgerStore`, using the path returned by its declared getter dispatch. No public interface accepts this constructor argument.

- [ ] Add failing tests for absent parent/file, strict parsing, deterministic encoding, modes, symlink/non-regular rejection, compare-and-swap retry, orphan preservation, idempotent claims, and one active flow.
- [ ] Run the state test; expect missing-module failure.
- [ ] Extend the restricted atomic-files Python API with a confined `ensure-private-directory` operation that creates missing descendants with mode `0700`, rejects symlink/reparse/non-directory components, and retains descriptor/handle-relative confinement on POSIX and Windows. Implement the ledger using an injected adapter for that operation plus a stable exclusive lock, confined reads, exact-predecessor compare-and-replace, and post-write verification/recovery; do not claim portable byte CAS. Task 4 wires the registered restricted interface after the manager module exists.
- [ ] Rerun; expect PASS. Commit the reviewed common atomic-files extension as `feat: add confined private directory operation`. Keep the manager-owned state files uncommitted until Task 4 registers the hidden node; the repository hook rejects partial unregistered runtime trees. Task 4 commits the reviewed Task 2–4 manager-owned files together.

### Task 3: Implement evaluation and teardown planning

**Files:** create `skills/setup-interface-manager/_rtx/_setup_evaluation.py` and `_rtx/tests/test_setup_evaluation.py`.

```python
def evaluate_target(graph, target_interface: str, ledger: SetupLedger) -> SetupEvaluation: ...
def authorize_ready_root(store: LedgerStore, graph, target_interface: str) -> SetupEvaluation: ...
def record_setup_success(store: LedgerStore, graph, flow_id: str, step: SetupStep) -> FlowResult: ...
def record_teardown_success(store: LedgerStore, graph, flow_id: str, step: TeardownStep) -> FlowResult: ...
def invalidate(store: LedgerStore, graph, setup_interface: str) -> tuple[str, ...]: ...
def teardown_plan(graph, root_setup_interface: str, ledger: SetupLedger) -> tuple[TeardownStep, ...]: ...
```

- [ ] Test unmanaged/root/child targets, chains, diamonds, stale suffixes, invalidation, out-of-order settlement, root authorization, reverse teardown, and both shared-dependency histories.
- [ ] Run the new test; expect failure. Implement owner resolution through `graph.module_parents` and all mutations through `LedgerStore`.
- [ ] Rerun; expect PASS. Keep the reviewed evaluation files uncommitted for the registered-node commit in Task 4.

### Task 4: Register the hidden manager and runners

**Files:** create `skills/setup-interface-manager/{SKILL.md,blueprint.yaml,blueprints/gateway.yaml}` and `_rtx/{blueprint.yaml,blueprints/rtx-manager.yaml,_setup_manager.py,_setup_dispatches.py,tests/test_setup_manager.py}`; include the reviewed Task 2–3 manager-owned files; modify `src/officina/common/blueprint.yaml`, `src/officina/runtime/python_machine_interface.py`, `tests/test_officina_python_machine_interface.py`, and generated runtime-dependency metadata.

- [ ] Test every public signature, state/exit code, exact current-step enforcement, stdin redaction, ready-only authorization, Python run-and-verify, Markdown settlement, teardown settlement, busy flow, retry/cancel, and non-activation from generic setup prose.
- [ ] Run the manager test; expect missing-node failure.
- [ ] Register a hidden `skill-workflow` manager with no production managed targets yet; tests install a finite Python fixture map. Grant its `_rtx` module atomic-files access and declare its canonical getter/atomic-files uses. Declare the getter dependency as `DispatchCall(caller_module_id="setup-interface-manager._rtx", target_module_id="common", interface="famulus-paths-get", smoke_args=("setup-status",))`; runtime calls use `self.dispatch(GETTER_KEY, args=("setup-status",))`. Construct `LedgerStore` only from the captured absolute stdout. No public path or arbitrary interface ID is accepted. Task 7 adds the first production target only after its interfaces exist.
- [ ] Expose a runtime-owned `is_dispatch_invocation_error(exc)` classifier from `python_machine_interface.py`. The skill may use it to translate only real dispatcher invocation failures to redacted domain responses without importing raw dispatcher modules or changing `PythonMachineInterface.dispatch()` exception compatibility; arbitrary programmer exceptions still propagate.
- [ ] Run the state, evaluation, and manager tests plus `./repo_checks.py --task validators --validator skill-maker/blueprints --jobs 1`; expect PASS. Commit the complete registered `skills/setup-interface-manager` tree and common caller grant as `feat: add setup interface manager`.

### Task 5: Add the generated Markdown gate

**Files:** modify `skills/skill-maker/_rtx/_blueprint_syncer.py` and `_rtx/tests/test_blueprint_tools.py`.

- [ ] Test that only opted-in Markdown skills receive the gate; bootstrap/plain setup exports do not; output is deterministic/idempotent; opt-out removes it; and bytes outside the existing markers remain unchanged.
- [ ] Require the exact setup sequence and teardown route above, including `authorize` after the ready recheck, caller-held arguments, structured-step bypass only, public IDs, and no paths.
- [ ] Run the syncer test; expect failure. Add `generated_setup_gate()` inside the existing `BEGIN/END BLUEPRINT INTERFACES` block.
- [ ] Rerun; expect PASS. Commit as `feat: inject managed setup checks into skills`.

### Task 6: Add MCP preflight and remove readiness-file ownership

**Files:** modify `mcp_server.py`, `tests/test_famulus_mcp.py`, `src/officina/dispatcher/{__init__.py,direct_authorization.py,direct_runtime.py}`, and `tests/test_dispatcher_direct_authorization.py`; create `tests/test_mcp_setup_preflight.py`.

- [ ] Test unmanaged/ready/pending/child targets, suffixes, busy flow, redaction, dry-run/manager exemptions, and exact setup and teardown redirection.
- [ ] Define `setup_required` as an ordinary-call refusal with root, ordered path, next setup, manager `begin`, and redacted continuation; `setup_managed` as direct setup/teardown redirection with operation/root/manager; and `setup_busy` with flow ID/recovery route but no arguments.
- [ ] Run the MCP tests; expect failure.
- [ ] Split preflight at the existing authorization seams. After canonical export lookup and access authorization—but before process-binding resolution—intercept exact managed setup/teardown exports and return `setup_managed`; this permits informative routing for Markdown instruction interfaces that are intentionally not process-bindable. For ordinary executable exports, resolve the process route, then run status/`authorize` immediately before launch. Remove MCP startup writing of the old `{"host", "schema_version", "status"}` readiness payload; live `famulus.invoke` availability is authoritative.
- [ ] Reuse a public direct-authorization helper for pre-binding host authorization so lifecycle interception enforces the same discoverable-top-level-skill caller rule as ordinary host dispatch without compiling a process binding. Nonzero manager exits are redacted runtime-misconfiguration refusals; only successful manager JSON may produce setup states, and `setup_busy` requires a nonempty flow ID.
- [ ] Rerun; expect PASS. Commit as `feat: enforce managed setup in Famulus MCP`.

### Task 7: Migrate the Markdown canary

**Files:** modify `skills/milestone-logging/{blueprint.yaml,setup.md,blueprints/gateway.yaml,blueprints/setup.yaml,_rtx/blueprint.yaml,_rtx/tests/test_milestone_run_journal.py}`, `skills/setup-interface-manager/{blueprint.yaml,_rtx/blueprint.yaml,_rtx/_setup_dispatches.py}`; create `skills/milestone-logging/{teardown.md,blueprints/teardown.yaml,_rtx/_setup_status_interface.py,_rtx/_teardown_status_interface.py,_rtx/blueprints/rtx-setup-status.yaml,_rtx/blueprints/rtx-teardown-status.yaml}`.

- [ ] Test dedicated setup/teardown plus executable read-only setup and teardown verifiers. Bind the setup-status and teardown-status sources to `_setup_status_interface.py` and `_teardown_status_interface.py`; export both through `_rtx/blueprint.yaml` and the parent namespace surface; declare the getter use needed by setup-status. Setup verifies only the getter-projected logging path and returns exactly `{"set_up": boolean}` without reading `setup-status` as MCP readiness. Teardown makes no external mutation and returns exactly `{"torn_down": boolean}`; after verifier success the manager removes the claim/receipt.
- [ ] Run the owner test; expect failure. Add opt-in metadata, dedicated instructions, both verifiers, and the manager's finite production dispatch/use entries for this now-existing pair.
- [ ] Rerun the owner test and blueprint validator; expect PASS. Commit as `feat: manage milestone logging setup`.

### Task 8: Prove the Python runner without parameterized production state

**Files:** modify `skills/setup-interface-manager/_rtx/tests/test_setup_manager.py`; create `tests/fixtures/setup_interface_manager/repository/officina.toml`, `tests/fixtures/setup_interface_manager/repository/python-canary/blueprint.yaml`, `tests/fixtures/setup_interface_manager/repository/python-canary/blueprints/lifecycle.yaml`, `tests/fixtures/setup_interface_manager/repository/python-canary/python_canary.py`, and `tests/test_setup_interface_manager_coverage.py`.

- [ ] Add a registered test-only Python setup/teardown/verifier fixture with no external effects. Its setup and teardown interfaces have fixed process bindings; the dispatch map cannot select the opposite action. The setup verifier returns `{"set_up": true}` only after the fixture setup succeeds, and teardown verifier returns `{"torn_down": true}` only after teardown succeeds.
- [ ] Test zero exit plus verifier success, nonzero exit, malformed/false verifier output, wrong current interface, and receipt mutation only after verification.
- [ ] Add coverage requiring exactly `milestone-logging.interface.setup` in production, its pair/verifiers in the manager map, bootstrap unmanaged, and no parameterized production setup opted in.
- [ ] Run manager and coverage tests; expect failure before the fixture is wired. Add only test injection seams needed to supply the finite fixture map.
- [ ] Rerun tests and blueprint validator; expect PASS. Commit as `test: prove Python setup runner lifecycle`.

### Task 9: Add acceptance and documentation

**Files:** create `tests/test_setup_interface_manager_integration.py`; modify `docs/setup.md`.

- [ ] Test unmanaged execution, dependency-first switching, both runners, ready authorization, refusal/failure, stale suffix repair, invalidation, reverse teardown, both shared-dependency histories, interruption/recovery, malformed ledger, restart, and one exact resume.
- [ ] Run the integration test; expect PASS because Tasks 1-8 implement its unit-tested boundaries. Repair any defect in its owning task before proceeding.
- [ ] Run:

```bash
./repo_checks.py --task tests:shared --selector tests/test_officina_setup_requirements.py --selector tests/test_setup_interface_manager_coverage.py --selector tests/test_setup_interface_manager_integration.py --selector tests/test_mcp_setup_preflight.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_evaluation.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_state.py --selector skills/setup-interface-manager/_rtx/tests/test_setup_manager.py --selector skills/skill-maker/_rtx/tests/test_blueprint_tools.py --selector skills/milestone-logging/_rtx/tests/test_milestone_run_journal.py --jobs 1
./repo_checks.py --suite validators --jobs 1
```

- [ ] Document session-hook bootstrap, opt-in setup, ledger versus live MCP readiness, invalidation, teardown, recovery, argument handling, and canaries.
- [ ] Stage every Task 1-9 file explicitly, confirm `git diff --cached --name-only` has no unrelated paths, and run `./repo_checks.py --suite precommit --jobs 1 --repository-view staged`. Require PASS, including a host-capable rerun for any sandbox-only failure.
- [ ] Commit the integration test and documentation as `test: verify managed setup task switching`.

### Task 10: Run isolated interactive setup experiments

**Source prompt:** `~/Desktop/interactive-prompt.md`.

**Files:** create `docs/testing/setup-interface-manager-interactive.md`; modify implementation/tests only when an experiment proves a defect, with every repair attributed to its owning Task 1–9 area and independently reviewed before commit.

- [ ] Use the current supported Codex/plugin installation procedure to create a task-owned isolated directory and isolated Codex home. Do not change the user's normal Codex profile, plugin installation, or persisted setup ledger. Record the exact tested build/commit, host capabilities, installation commands, and isolation boundaries.
- [ ] Keep production evidence separate from a second isolated synthetic-fixture installation. Build the fixture from the exact tested commit plus a reviewable Task-10-only overlay containing fixed, effect-confined managed nodes `A -> B -> C` and `D -> C`, dedicated setup/teardown/verifier routes, child probes, finite manager bindings, and focused validation. Record the overlay file list and digest, require the production manager/MCP state-machine files to remain byte-identical, never include the fixture in release inventory, and remove only the task-owned fixture lane during cleanup. Label every fixture result as synthetic interactive evidence rather than shipped production behavior.
- [ ] Build a scenario inventory before execution. It must include the user's first-use managed-skill trigger, following the suggested setup, persistence across fresh sessions, and a deep `A → B → C` managed setup chain. Add negative/edge scenarios for an unmanaged skill, duplicate/busy invocation, interruption plus retry/cancel, stale receipt/invalidation suffix repair, shared-dependency claims and teardown, malformed ledger, exact setup/teardown redirection, argument redaction, and exactly-once original-call resumption.
- [ ] Convert each scenario into a detailed pipeline with preconditions, fresh-agent/session boundary, action, expected trigger/stack/state, persisted evidence, cleanup/recovery, and pass/fail criteria. Execute scenarios one by one using fresh subagents for the user-facing interactions; subagents must not inherit the controller's implementation context.
- [ ] Preserve redacted commands, outputs, ledger snapshots, and session outcomes in the report. Distinguish product defects from sandbox/host capability limits and experimental setup errors. Never treat a scripted/unit result as interactive evidence.
- [ ] When a scenario fails, reproduce it minimally, repair only the owning implementation area through TDD, obtain scoped independent green review, rerun the failed scenario in a fresh isolated session, and record both failure and corrected result. Do not weaken an expected behavior to make the experiment pass.
- [ ] Maintain a reusable lessons ledger in the report: scenario design improvements, reliable isolation/install strategy, failure injection patterns, evidence collection, cleanup, and a template for repeating this class of plugin-behavior experiment.
- [ ] Run a fresh post-experiment acceptance/precommit verification for every implementation or documentation change. Obtain independent review that the experiment covered the source prompt and that results support each claim. Commit the interactive report and any reviewed repairs in coherent owning-area chunks; use `test: exercise setup manager interactively` for the report-only checkpoint.

## Acceptance criteria

- Bootstrap works without MCP and is never manager-controlled.
- Only explicit opt-ins are gated; setup discussion cannot activate the manager.
- Child targets resolve through graph ancestry to the managed root.
- Missing/stale dependencies rerun their dependent suffix dependency-first.
- Both runners invoke only declared interfaces and record only verifier success.
- Root claims make reverse teardown correct for independent and shared use.
- The getter-selected ledger is the only persisted setup state; MCP does not overwrite it.
- Generated skills and MCP retain the request, switch, recheck, reauthorize, and resume once.
- Exact setup/teardown calls receive informative manager routes.
- One active flow prevents duplicate managed actions; recovery is explicit.
- Release coverage contains exactly the milestone-logging production canary; the Python runner passes its registered fixture contract without claiming parameterized production state.
- Isolated interactive evidence demonstrates first-use setup guidance, persistence across fresh sessions, deep dependency setup, recovery/teardown edge cases, and exactly-once resumption without mutating the user's normal Codex state; limitations and failures remain visible in the reusable experiment ledger.
