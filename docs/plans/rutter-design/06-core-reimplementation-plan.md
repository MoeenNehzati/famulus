# Rutter Node-Entry Core Reimplementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current state-ID/diagnostic-phase reducer with the
accepted node-entry Rutter lifecycle while preserving the hardened storage and
registry infrastructure that remains valid.

**Architecture:** Build a fresh core implementation in the existing
`officina.rutter` package from clean commit `d170ccfa`, using storage version 3
and one reducer. A Reckoning recursively owns one entered node per active
Rutter; Turns, Actions, Calls, and Done results are durable records rather than
control phases. Transition CaseMakers attach ordinary child Rutters before a
frozen edge resumes, and the diagnostic library composes only public core
primitives.

**Tech Stack:** Python standard library, frozen dataclasses, finite JSON,
repository atomic-file helpers, `pytest`, Officina blueprint validators.

**Spec:** `01-core-design.md`, `02-runtime-reference.md`,
`03-hook-library.md`, and `05-verification-and-implementation.md` in this
directory.

## Global Constraints

- Node entrance `(entry_id, state_id)` is the only persisted control
  coordinate for each recursively active Rutter.
- Prompt entrance and creation of its exact open Turn are one atomic write.
- The public operating interface is only `get_instruction()`, `validate()`,
  `next()`, and `get_current_node()`; `advance()` is removed.
- Definition objects are stateless and run-neutral. All run data lives in the
  Charter, immutable callback contexts, or Reckoning.
- One Reckoning file owns the recursive active path, completed-run archive,
  global revision, active effect, and fault.
- There is one reducer and one storage schema. Storage version 3 explicitly
  rejects versions 1 and 2; it does not run a compatibility reducer.
- Pure callbacks are replayable from a strict history prefix plus the accepted
  source record. External work occurs only in an Action.
- Hook children run sequentially and cannot alter the already-selected edge.
- `allow_multiple_cases_at_once` must be an exact Boolean; false faults before
  any child starts if several makers select.
- `dry_run=True` performs no Action, CaseMaker, child work, mutation, or node
  entrance.
- Do not edit or clean the existing dirty prototype worktree. It is evidence,
  not the implementation base.
- Do not add a workflow DSL, generated Rutter classes, generic ledger sink,
  parallel child execution, fault catching, or legacy migration unless a
  separately approved requirement establishes a need.
- Do not commit, amend, stage, or push unless the user explicitly authorizes
  that Git operation.

---

## Replacement decision

Use a fresh implementation worktree, but keep the package and public concept
names that remain sound. Updating the current 1,977-line prototype engine in
place would require proving that every diagnostic phase, `Fix.lifecycle`
branch, and state-ID-only recovery path was removed. Starting from clean
`d170ccfa` gives a smaller review surface while retaining the already-tested
repository foundation.

The normative design is tracked on master at `docs/plans/rutter-design/`. The
old worktree remains available for two purposes only:

1. port storage, locking, crash-window, and integration tests whose behavioral
   assertions still apply; and
2. compare the frozen appendix diagnostic observations after the new runtime
   passes its own tests.

Do not copy prototype `engine.py`, `model.py`, `storage.py`, or
`diagnostic.py` wholesale into the new worktree.

## File responsibility map

### Preserve substantially

| Existing responsibility | Source | Preservation rule |
|---|---|---|
| Atomic create/replace, exclusive locking, confined regular-file reads, no-follow directory walking | `src/officina/rutter/storage.py` lower `_ReckoningStore` section and `src/officina/common/atomic_files.py` | Move the store behind the version-3 codec with behavior unchanged. Rename only types required by the new model. Port its adversarial tests before changing it. |
| Strict JSON parsing | `storage.py` duplicate-key and nonfinite-number rejection | Retain byte-level behavior and error categories. Replace only the object decoder reached after parsing. |
| Finite JSON freezing and identifier checks | `model.py` `_freeze_json`, `_freeze_json_mapping`, `_require_identifier` | Reuse after moving them to the new value model. Extend exact-field decoding consistently; do not introduce a schema dependency. |
| Validation vocabulary | `ValidationIssue`, `ValidationReport`, `RutterValidationError`, definition/state errors | Preserve concepts and stable invalid-versus-exception distinction. Adjust paths to `tuple[str | int, ...]` and add `NotApplicable`, `RunBlocked`, and `PreviewUnavailable`. |
| Charter identity | `Charter` | Preserve `rutter_id`, `definition_version`, and finite immutable data. It becomes a field of every `ActiveRun`. |
| Registry boundary and path confinement | `runtime.py` `RutterRegistry` | Preserve name/identity lookup, frozen registrations, definition-version checks, and confined paths. Rewrite discovery so it binds transitive Call and CaseMaker children. |
| Safety and recovery test intent | storage, engine, and runtime tests | Port atomicity, lock ownership, malformed JSON, path confinement, stale response, and crash-window assertions. Rewrite fixtures and expected schemas rather than preserving old representations. |

Port and rename concrete prototype evidence rather than relying on the general
categories alone:

- `test_replace_requires_the_exact_canonical_predecessor_bytes`,
  `test_replace_requires_transaction_ownership`, and
  `test_failed_replacement_preserves_exact_predecessor_bytes` move to the
  version-3 storage suite;
- `test_store_rejects_symlinked_parent_and_lock_without_writing_outside` and
  `test_missing_root_cannot_become_an_unlocked_successful_read` remain path and
  lock boundary tests;
- `test_restart_messages_and_stale_writers_are_exact_at_every_response_boundary`
  becomes nested global-revision and stale-response coverage; and
- `test_repeat_safe_planned_effect_reopens_and_retries`,
  `test_non_repeat_safe_planned_effect_becomes_uncertain_without_retry`, and
  `test_post_effect_failure_persists_completed_disposition_and_fault` become the
  version-3 Action recovery cases.

### Rewrite in place

| File | Why it cannot be patched safely | Replacement shape |
|---|---|---|
| `src/officina/rutter/model.py` | `Fix(current_state_id, lifecycle, diagnostic)` and two State variants encode the rejected lifecycle. | Define the public JSON values, four node variants, immutable contexts/history views, recursive active-run records, effects, faults, and exact codecs' input types. No reducer logic. |
| `src/officina/rutter/storage.py` upper codec section | It serializes `Fix`, diagnostic frames, pending messages, and versions 1/2. | Add one strict version-3 encoder/decoder for `Reckoning`, `ActiveRun`, records, completed runs, effects, and faults; retain the lower store mechanics. |
| `src/officina/rutter/engine.py` | `advance()` interleaves root transitions, diagnostic phases, and effect handling around state ID. | Write one small reducer around recursive active-leaf resolution and atomic operations: enter, accept/record, select edge, attach/settle child, resume edge, and enter target. Expose only the four methods. |
| `src/officina/rutter/runtime.py` | It accepts only direct subclasses and resolves only the root Charter identity. | Bind a stateless definition graph transitively, reject definition-call cycles, and resolve only active definitions on reopen. Keep completed runs structural. |
| `src/officina/rutter/__init__.py` | It exports obsolete `State`, `TerminalState`, and diagnostic-sidecar values. | Export the accepted author-facing primitives and stable exceptions; keep storage-private types private. |
| `test_support/rutter_fixtures.py` | Fixtures author the obsolete `State`/`TerminalState` API and inspect `Fix.lifecycle`. | Replace with readable Prompt/Action/Call/Done Rutters, nested fixtures, hook fixtures, and version-3 Reckoning builders. |
| `docs/officina/compass-rutter.md` and `skills/using-compass/` | They describe `advance()` and prototype message/status behavior. | Document and exercise Compass's use of the four public Rutter methods, two-part Messages, recursive current leaf, Rutter-owned automatic Python continuation, and blocking conditions. |
| Rutter blueprints | Current interfaces and facets name the old model and reducer. | Regenerate ownership/interfaces only after source behavior and tests stabilize; describe the new public methods and storage authority exactly. |

### Write from scratch

| New responsibility | Destination | Construction rule |
|---|---|---|
| Edge matching and CaseMaker constructors | `src/officina/rutter/hooks.py` | Depend only on public model/context types. Produce ordinary `CaseMaker` values; add no persisted hook type beyond active/call provenance already in the core model. |
| Standard diagnostic child Rutters and values | `src/officina/rutter/diagnostic.py` | Replace the prototype file entirely with `QuestionCase`, `DiagnosisCase`, `DiagnosisDetail`, `DiagnoseAnswer`, `AskAndDiagnose`, and the three documented constructors. Build them from Prompt/Action/Call/Done. |
| Recursive lifecycle tests | `tests/test_rutter_lifecycle.py` | Specify entrance identity, open Turn atomicity, nested return settlement, history anchoring, continuation, dry-run, and restart boundaries independently of implementation helpers. |
| Hook tests | `tests/test_rutter_hooks.py` | Specify matching, cardinality, stable order, same-edge replay skipping, and frozen-edge resumption. |
| Inventory diagnostic adapter | `skills/math-dependency-graph/_rtx/_inquisitive_inventory_rutter.py` | Re-express the frozen example through `case_sequence_after` plus an application-owned repeat-safe ledger Action. Do not put inventory semantics in the core. |

### Remove at cutover

- `_DiagnosticFrame`, `_DiagnosticState`, `DiagnosticResult`, `CaseContext`, and
  every diagnostic lifecycle/pending-message branch in core model, storage, and
  engine;
- `Fix.current_state_id`, `Fix.lifecycle`, state-level `is_diagnostic`, and any
  persisted `entered`, `waiting`, `transitioning`, case queue, or schedule
  index;
- `State`, `TerminalState`, `InputValidatorContract`, and `advance()` after all
  repository callers have migrated;
- tests that assert obsolete JSON shapes or implementation-private phases;
  preserve their safety intent in new tests before deletion; and
- any parallel v1/v2 runtime path.

## Normative requirement traceability

This table maps the normative acceptance catalogue in
`05-verification-and-implementation.md` to implementation ownership. It does
not restate or replace that catalogue.

| Normative requirement | Implementing task | Test ownership |
|---|---|---|
| Exact values, finite JSON, immutable complete/prefix `HistoryView`, and all `latest_*`/`require_latest_*` queries | Tasks 2-5 integrated core boundary | `tests/test_rutter_model.py`, `tests/test_rutter_lifecycle.py` |
| Version-3 structure, effect-owner corruption matrix, completed-run/CallRecord bijection and acyclicity, post-Done restrictions | Tasks 3, 6, and 7 | `tests/test_rutter_storage.py`, `tests/test_rutter_lifecycle.py` |
| Prompt entrance/open Turn, four-method behavior, global revision, rejection without mutation | Task 5 | `tests/test_rutter_lifecycle.py`, `tests/test_rutter_engine.py` |
| Recursive Calls, Prompt/Call self-loops, nested stale responses, atomic return, result routing | Task 6 | `tests/test_rutter_lifecycle.py`, `tests/test_rutter_runtime.py` |
| Pure/repeat-safe/non-repeat-safe Actions, Action self-loops, child Actions, and all recovery dispositions | Task 7 | `tests/test_rutter_engine.py`, `tests/test_rutter_lifecycle.py`, `tests/test_rutter_storage.py` |
| Edge matching, cardinality, frozen-edge replay, post-Done settlement, and attachment provenance | Task 8 | `tests/test_rutter_hooks.py`, `tests/test_rutter_lifecycle.py` |
| Standard diagnostic children and CaseMaker constructors | Task 9 | `tests/test_rutter_diagnostic.py`, `tests/test_rutter_hooks.py` |
| Ordinary Compass behavior, evaluator-backed fresh questions, non-diagnostic scheduled children, and frozen inventory trials | Tasks 10 and 11 | Compass and inventory integration suites named in those tasks |

---

### Task 1: Establish the clean implementation boundary

**Files:**
- Restore from tracked master: `docs/plans/rutter-design/`
- Create with execution tooling: a fresh linked worktree and named feature
  branch based on `d170ccfaa2535abb28b7326c877f40d577a52981`
- Inspect only: the dirty `feat/rutter-diagnostic-cases` worktree

**Interfaces:**
- Consumes: the tracked design documents on master and frozen prototype
  evidence.
- Produces: a clean implementation checkout with no copied prototype source
  changes and a locally available tracked normative design directory.

- [ ] **Step 1: Verify the source authority and dirty-reference boundary**

  Run in the existing prototype worktree:

  ```bash
  git symbolic-ref HEAD
  git log -1 --format='%H %s' HEAD
  git status --short
  git -C /home/moeen/Documents/AI ls-files docs/plans/rutter-design
  ```

  Expected: branch `feat/rutter-diagnostic-cases`, HEAD `d170ccfa...`, a dirty
  prototype, and all eight design files listed as tracked on master.

- [ ] **Step 2: Create the implementation worktree using the required skill**

  Invoke `superpowers:using-git-worktrees`. Base the new named branch on the
  verified clean commit, not on the dirty working tree. A suitable branch name
  is `feat/rutter-node-entry-core`.

- [ ] **Step 3: Carry the tracked specifications explicitly**

  In the new worktree, restore the complete tracked design directory from
  master, then verify byte equality against the master checkout:

  ```bash
  git restore --source=master -- docs/plans/rutter-design
  diff -ru /home/moeen/Documents/AI/docs/plans/rutter-design /home/moeen/Documents/AI/.worktrees/rutter-node-entry-core/docs/plans/rutter-design
  ```

  Expected: no output. Do not copy changed Python, skill, blueprint, test, or
  documentation files.

- [ ] **Step 4: Record baseline verification**

  Run the repository's configured Python checks in the clean implementation
  worktree and record any pre-existing failures separately:

  ```bash
  python3 repo_checks.py --suite precommit --jobs 8
  ```

  Expected: the clean baseline result is recorded before source edits.

### Integrated core review boundary

Tasks 2-5 are one review boundary with four internal construction stages. The
model, codec, registry, and reducer are mutually importing replacement parts;
Tasks 2-4 therefore need not leave the package collectable and must not receive
separate commits. Their commands are red probes that localize missing contracts
or obsolete imports. Task 5 completes the cutover, runs the combined focused
suite, and is the first green review/commit checkpoint. Do not preserve the old
public types merely to make an intermediate stage importable.

### Task 2: Replace the public value model and freeze its contracts

**Files:**
- Rewrite: `src/officina/rutter/model.py`
- Rewrite: `tests/test_rutter_model.py`
- Rewrite: `test_support/rutter_fixtures.py`
- Modify: `src/officina/rutter/__init__.py`

**Interfaces:**
- Consumes: finite-JSON freezing, Charter identity, and validation vocabulary.
- Produces: `Prompt`, `Action`, `Call`, `Done`, `AnswerSpec`, `Message`,
  `Response`, `ActionResult`, `RunResult`, immutable contexts and history
  records, `Reckoning`, `NodeView`, and stable errors.

- [ ] **Step 1: Write failing exact-value tests**

  Cover at least these public contracts:

  ```python
  def test_message_has_exact_instruction_and_data_parts():
      message = Message(
          instructions={"text": "Report.", "answer": {"reported": {}}},
          data={"state": {"id": "report", "entry_id": "e1", "revision": 1},
                "payload": {"chunk": "A"}},
      )
      assert set(message.to_json()) == {"instructions", "data"}

  def test_active_run_has_one_entered_node_and_recursive_child():
      assert fields(ActiveRun)[4].name == "entered_node"

  def test_validation_issue_path_accepts_string_and_integer_segments():
      issue = ValidationIssue(("evidence", "nodes", 0), "missing", "required")
      assert issue.path[-1] == 0
  ```

  Add rejection cases for extra/missing fields, wrong exact Booleans,
  nonfinite JSON, bad IDs, duplicate record IDs, malformed Done authority,
  invalid child provenance, and active/completed run ID overlap.

  Freeze the complete immutable `HistoryView` contract: `entries`, `turns`,
  `open_turn`, `actions`, `calls`, `done`, every `latest_*`, every
  `require_latest_*`, complete versus strict-prefix visibility, and stable
  absence/error behavior.

- [ ] **Step 2: Run the model tests and confirm old types fail the contract**

  ```bash
  python3 -m pytest tests/test_rutter_model.py -q
  ```

  Expected: failures identify missing new types or obsolete shapes. If package
  initialization reaches an obsolete engine/storage import, record it as an
  expected integrated-boundary failure for Task 5; do not add a compatibility
  facade.

- [ ] **Step 3: Implement the model as frozen values**

  Use these signatures as the author boundary:

  ```python
  class Rutter:
      rutter_id: str
      definition_version: int
      start_state: str
      allow_multiple_cases_at_once: bool = False
      def define_states(self) -> Mapping[str, Prompt | Action | Call | Done]: ...
      def define_case_makers(self) -> tuple[CaseMaker, ...]: return ()

  @dataclass(frozen=True)
  class EnteredNode:
      entry_id: str
      state_id: str

  @dataclass(frozen=True)
  class ActiveRun:
      run_id: str
      rutter_id: str
      definition_version: int
      charter: Charter
      entered_node: EnteredNode
      history: tuple[HistoryEntry, ...]
      active_child: ActiveChild | None
  ```

  Keep callable-bearing definitions separate from serializable values. Keep
  `PythonInstruction` in-process only. Implement exact `to_json`/`from_json`
  for every persisted/public value without serializing callbacks.

- [ ] **Step 4: Run the model probe**

  ```bash
  python3 -m pytest tests/test_rutter_model.py -q
  ```

  Expected: value-level tests that can import the new model pass. Any remaining
  package-import failure must name an obsolete dependency scheduled in Tasks
  3-5.

- [ ] **Step 5: Review the partial model contract without committing**

  Review the intended public export list against `01-core-design.md`, record the
  red/green probe results, and continue to Task 3. This is not a review or commit
  boundary.

### Task 3: Introduce storage version 3 while preserving store hardening

**Files:**
- Rewrite upper section: `src/officina/rutter/storage.py`
- Preserve/adapt lower section: `src/officina/rutter/storage.py`
- Rewrite: `tests/test_rutter_storage.py`

**Interfaces:**
- Consumes: Task 2 `Reckoning` and record values.
- Produces: `_canonical_reckoning_bytes(Reckoning) -> bytes`,
  `_decode_reckoning(bytes, semantic_validator=...) -> Reckoning`, and the
  existing `_ReckoningStore` transaction API over storage version 3.

- [ ] **Step 1: Port the safety tests before replacing codecs**

  Retain cases equivalent to:

  ```python
  def test_replace_requires_live_locked_predecessor(...): ...
  def test_store_rejects_symlink_parent(...): ...
  def test_decode_rejects_duplicate_keys(...): ...
  def test_decode_rejects_nonfinite_numbers(...): ...
  def test_atomic_replace_reopens_as_one_complete_reckoning(...): ...
  ```

  Change only fixture construction to version 3. Add explicit rejection tests
  for `storage_version` 1 and 2 with one stable unsupported-version error.

- [ ] **Step 2: Run storage tests to establish the codec failure**

  ```bash
  python3 -m pytest tests/test_rutter_storage.py -q
  ```

  Expected: version-3 model/codec tests fail while confined path, lock, and
  malformed-byte tests expose any accidental infrastructure regressions.

- [ ] **Step 3: Replace only the schema codecs**

  Encode exactly:

  ```text
  Reckoning(storage_version=3, global_revision, root,
             completed_runs, active_effect, fault)
  ActiveRun(..., entered_node, history, active_child)
  HistoryEntry = Turn | ActionRecord | CallRecord | DoneRecord
  ```

  Decode in two passes: strict structural construction, then referential and
  semantic validation. Reject v1/v2 before interpreting their fields. Retain
  `_ReckoningStore.create`, `replace`, `transaction`, path checks, no-follow
  reads, byte-exact predecessor checks, modes, and atomic helpers.

- [ ] **Step 4: Add recursive corruption tests**

  Test excessive depth, wrong active-effect owner, dangling completed-run
  references, duplicate run/call/record/entrance IDs, a CallRecord referencing
  no completed run, more than one DoneRecord, and an active child under a
  completed run. Also reject a non-root CompletedRun referenced by zero or more
  than one CallRecord, cyclic completed-run references, non-attached records
  after DoneRecord, explicit Calls with attached-edge provenance, attached Calls
  without exact edge provenance, and duplicate `(maker_id, edge_id)` attachment
  authority.

- [ ] **Step 5: Run storage and model suites**

  ```bash
  python3 -m pytest tests/test_rutter_model.py tests/test_rutter_storage.py -q
  ```

  Expected: codec and model assertions pass when reached; obsolete package
  imports may remain red until Task 5. No version-1/2 success path may be added,
  and this is not a review or commit boundary.

### Task 4: Bind stateless definitions and transitive children

**Files:**
- Rewrite: `src/officina/rutter/runtime.py`
- Rewrite: `tests/test_rutter_runtime.py`
- Modify: `test_support/rutter_fixtures.py`

**Interfaces:**
- Consumes: Task 2 author nodes/CaseMakers and Task 3 store.
- Produces: an immutable bound-definition graph resolved by
  `(rutter_id, definition_version)` and `RutterRegistry.create/open` returning a
  voyage bound to one Reckoning.

  Freeze the boundary before implementation: registry entries are no-argument
  Rutter definitions, instances, or factories; the bound voyage alone owns the
  store and Reckoning and implements the four public operations;
  `RutterRegistry.create(name, reckoning_path, charter_data)` and
  `RutterRegistry.open(reckoning_path)` return that same operating protocol.
  The concrete bound-voyage class may remain private and need not be exported.

- [ ] **Step 1: Write binding failures first**

  Cover invalid IDs/versions/start state, non-Boolean multiple-case policy,
  duplicate state and CaseMaker IDs, undeclared targets/outcomes, bad callback
  signatures, run-state stored on definition instances, child identity
  conflicts, and recursive definition-call cycles.

- [ ] **Step 2: Add transitive discovery tests**

  Define a root Call child, an attached child, and a grandchild. Assert that
  reopen requires definitions only for the recursively active path and does
  not require executable code for archived completed runs.

  Also freeze the exact registry constructor and `create/open` signatures,
  assert both return objects implementing the four-method protocol, and prove
  definition instances own no store, Reckoning, path, revision, or run data.

- [ ] **Step 3: Run runtime tests to verify the old direct-only registry fails**

  ```bash
  python3 -m pytest tests/test_rutter_runtime.py -q
  ```

- [ ] **Step 4: Implement binding as a separate pure pass**

  Construct each no-argument definition once, freeze its state and CaseMaker
  mappings, inspect callable signatures without invoking them, recursively
  discover Call/hook children, and reject cycles before creating a Reckoning.
  Keep path confinement and frozen registry metadata from the current runtime.

- [ ] **Step 5: Run focused model/storage/runtime tests**

  ```bash
  python3 -m pytest tests/test_rutter_model.py tests/test_rutter_storage.py tests/test_rutter_runtime.py -q
  ```

  Expected: binding-specific assertions pass when reached; any remaining
  obsolete engine/facade import is an expected integrated-boundary failure for
  Task 5. This is not a review or commit boundary.

### Task 5: Build the Prompt/Done lifecycle and four-method interface

**Files:**
- Rewrite: `src/officina/rutter/engine.py`
- Create: `tests/test_rutter_lifecycle.py`
- Rewrite focused portions: `tests/test_rutter_engine.py`
- Complete cutover: `src/officina/rutter/__init__.py`

**Interfaces:**
- Consumes: bound definitions and transactional version-3 storage.
- Produces:

  ```python
  get_instruction() -> Instruction | None
  validate(response: object) -> ValidationReport
  next(response=MISSING, *, continue_: bool = True,
       dry_run: bool = False) -> NodeView
  get_current_node() -> NodeView
  ```

- [ ] **Step 1: Specify Prompt entrance and read-only operations**

  Tests must prove creation atomically allocates an entrance and exact open
  Turn, repeated `get_instruction()` returns byte-equivalent Message data,
  re-entry to the same state allocates a new entrance and may render different
  payload data, and both read-only methods leave file bytes unchanged.

- [ ] **Step 2: Specify acceptance and rejection**

  Add stale-revision, unknown-outcome, malformed-envelope, nonfinite-evidence,
  and contextual-validation cases. An invalid `next(response)` must preserve
  current entrance, open Turn, revision, and file bytes. A valid response fills
  the same Turn and survives later routing failure.

- [ ] **Step 3: Implement a small operation reducer**

  Organize the reducer around explicit functions rather than persisted phases:

  ```python
  _active_leaf(reckoning) -> ActiveLeaf
  _accept_prompt(reckoning, response) -> Reckoning
  _source_record(run, entered_node) -> HistoryEntry | None
  _select_edge(bound_run, strict_prefix, record) -> Edge
  _enter_node(reckoning, run_id, target) -> Reckoning
  _settle_done(reckoning, run_id) -> Reckoning
  ```

  Prompt target entrance renders and stores its open Turn in the same
  replacement. Done creates one DoneRecord; terminal root `next()` is
  idempotent.

- [ ] **Step 4: Implement continuation and dry-run boundaries**

  `continue_=False` returns the first actually entered node. `continue_=True`
  loops through automatic operations using a bounded in-memory operation
  budget and returns the final LLM/terminal/fault/uncertain node. `dry_run`
  validates and routes from supplied/durable authority, returns a preview
  NodeView with `entry_id=None`, and never enters or persists.

  Add the complete node/condition method matrix: Prompt accepts Response;
  Action accepts ActionResult; Call and Done reject validation with
  `NotApplicable`; terminal `next()` is idempotent; fault and uncertain reject
  validation/advancement with `RunBlocked`; `get_instruction()` returns `None`
  for Call, Done, terminal, fault, and uncertain conditions.

- [ ] **Step 5: Run lifecycle tests with restart after every write boundary**

  ```bash
  python3 -m pytest tests/test_rutter_model.py tests/test_rutter_storage.py tests/test_rutter_runtime.py tests/test_rutter_lifecycle.py tests/test_rutter_engine.py -q
  ```

  Expected: Prompt/Done, self-loop, reopen, invalid response, routing fault,
  continuation-limit, and dry-run tests pass before Actions or children are
  enabled. All package imports and collection must now pass; this is the first
  green review boundary for Tasks 2-5. If commit authority exists, stage only
  the reviewed integrated-core files; otherwise record the command and leave
  Git state untouched.

### Task 6: Add recursive explicit Calls and atomic return settlement

**Files:**
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/model.py`
- Modify: `tests/test_rutter_lifecycle.py`

**Interfaces:**
- Consumes: `Call`, `ActiveChild`, `CompletedRun`, `CallRecord`, `RunResult`.
- Produces: recursive active-leaf resolution, explicit child push, and child
  return that archives/detaches/appends CallRecord while the parent stays at
  its Call entrance.

- [ ] **Step 1: Specify child push and leaf visibility**

  Test a parent Call whose child starts at Prompt. After push,
  `get_current_node()` and `get_instruction()` resolve the child leaf while the
  stored parent remains at the Call entrance. `continue_=False` returns that
  child start.

- [ ] **Step 2: Specify nested return boundaries**

  Test child and grandchild completion with a reopen after push, accepted
  response, DoneRecord, return settlement, parent routing, and target entrance.
  Assert one global revision and unique entrance IDs at every depth. Cover
  Prompt and Call self-loops and reject stale responses created at another
  active depth.

- [ ] **Step 3: Implement push and return as separate atomic operations**

  Push seals the child Charter and IDs in one replacement. Return settlement
  archives the completed child, detaches it, and appends the CallRecord in one
  replacement. It does not route or enter the parent successor in that write.
  The next reducer operation reconstructs routing from the durable CallRecord.

- [ ] **Step 4: Add result-directed routing and fault tests**

  Cover mapping and callable `then`, child faults retaining the full recursive
  path, maximum-depth rejection before allocation, and later routing failure
  preserving the returned child record. Call preview without an already
  available RunResult raises `PreviewUnavailable` and never pushes a child.

- [ ] **Step 5: Run the recursive lifecycle suite**

  ```bash
  python3 -m pytest tests/test_rutter_lifecycle.py tests/test_rutter_engine.py tests/test_rutter_runtime.py -q
  ```

### Task 7: Add Actions and recovery-owned Python instructions

**Files:**
- Modify: `src/officina/rutter/engine.py`
- Modify: `tests/test_rutter_engine.py`
- Modify: `tests/test_rutter_lifecycle.py`
- Modify: `tests/test_rutter_storage.py`

**Interfaces:**
- Consumes: Task 6 recursive active-leaf ownership plus `Action`,
  `ActionContext`, `ActionResult`, and `EffectRecovery`.
- Produces: `PythonInstruction(action_id, mode, run, answer_format)` and one
  effect slot owned by the deepest active run.

- [ ] **Step 1: Write pure, repeat-safe, and non-repeat-safe tests**

  Assert stable action ID per entrance, exact ActionResult envelope, pure
  supplied-result validation, repeat-safe retries with the same idempotency
  key, exact completed-result matching before consumption, Action self-loops,
  and Actions owned by nested children across reopen.

- [ ] **Step 2: Write the four non-repeat-safe crash-window tests**

  Inject failure before invocation, after `planned -> uncertain`, after the
  external effect, and after completed-result persistence. Verify `planned`
  proves unissued, `uncertain` blocks, and `completed` returns stored authority
  without rerunning the callback.

  Add the complete reopen corruption matrix for `planned`, `completed`, and
  `uncertain`: reject a wrong or non-leaf owner, stale entrance ID, mismatched
  state or mode, an owner with an active child, and an action ID already present
  in consumed ActionRecords.

- [ ] **Step 3: Implement `PythonInstruction.run()` as an engine wrapper**

  On effectful Action entrance atomically allocate planned recovery. For
  non-repeat-safe work persist `uncertain` before invoking author code. Persist
  `completed` before returning. `next(result)` may consume only the exact
  completed result into one ActionRecord and clear recovery atomically.

- [ ] **Step 4: Verify accepted Action work survives downstream faults**

  Add routing, hook-selection, and target-rendering failures after ActionRecord
  creation. Reopen must not execute or request the Action again.

  Add Action preview cases: pure routing may use a supplied ActionResult, while
  missing effectful authority raises `PreviewUnavailable` without allocating
  recovery or invoking the callback.

- [ ] **Step 5: Run engine, lifecycle, and storage tests**

  ```bash
  python3 -m pytest tests/test_rutter_engine.py tests/test_rutter_lifecycle.py tests/test_rutter_storage.py -q
  ```

### Task 8: Add transition hooks without adding reducer phases

**Files:**
- Create: `src/officina/rutter/hooks.py`
- Create: `src/officina/rutter/blueprints/hooks.yaml`
- Create: `tests/test_rutter_hooks.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/model.py`
- Modify: `src/officina/rutter/__init__.py`
- Modify: `src/officina/rutter/blueprint.yaml`
- Modify: `tests/test_blueprint_inventory.py`
- Modify: `tests/test_officina_blueprint_graph.py`

**Interfaces:**
- Consumes: `Edge`, `EdgeContext`, `CaseMaker`, and the Task 6 child machinery.
- Produces: `EdgeMatch`, `after`, `before`, `on_edge`, attached Call provenance,
  and `HistoryView.attached_calls(case_maker_id=None, edge_id=None)`.

- [ ] **Step 1: Test matchers and pure CaseMaker selection**

  Cover after-state, before-target, exact edge, post-Call, and post-Done
  matching. Assert all makers see the identical strict history prefix and
  accepted source record, never same-edge child results.

- [ ] **Step 2: Test zero/one/multiple selection behavior**

  Zero selections enter the frozen target. One selection attaches its child.
  Multiple selections with the policy false fault with all maker IDs before
  any child starts; with true they run sequentially in definition order.

- [ ] **Step 3: Implement same-edge replay from records, not a queue**

  Recompute `then` and the ordered maker pool from the anchored prefix after
  every return. Use later attached CallRecords only to skip completed
  `(maker_id, edge_id)` identities. Persist no transition, hook, case, or queue
  phase and no sibling list.

  Extend the Task 5/6 Done settlement seam rather than replacing its no-hook
  behavior: create the DoneRecord while remaining at its Done entrance,
  evaluate any post-Done CaseMakers, and only after all attached children finish
  mark the root terminal or return the child to its parent. DoneRecord remains
  the sole result authority throughout.

- [ ] **Step 4: Test frozen-edge resumption across crashes**

  Reopen after source acceptance, child attachment, child Done, and atomic
  return. The maker must neither repeat nor skip, and no child result may alter
  the target. Explicit Calls with colliding site names must not appear in
  `attached_calls`. A matcher or Charter-builder exception must preserve the
  accepted source record and commit a stable fault before target entrance.

- [ ] **Step 5: Run hook and lifecycle suites**

  ```bash
  python3 -m pytest tests/test_rutter_hooks.py tests/test_rutter_lifecycle.py tests/test_rutter_engine.py -q
  ```

### Task 9: Build the standard diagnostic library from ordinary Rutters

**Files:**
- Rewrite from scratch: `src/officina/rutter/diagnostic.py`
- Rewrite: `tests/test_rutter_diagnostic.py`
- Modify: `src/officina/rutter/__init__.py`

**Interfaces:**
- Consumes: public Prompt/Action/Call/Done and CaseMaker APIs only.
- Produces: `QuestionCase`, `DiagnosisCase`, `DiagnosisDetail`,
  `DiagnoseAnswer`, `AskAndDiagnose`, `diagnose_answer_on`,
  `ask_and_diagnose_on`, and `case_sequence_after`.

- [ ] **Step 1: Freeze diagnostic JSON contracts**

  Test exact one-string `actual_answer` and `expected_answer`, optional finite
  `format_hint`, metadata, nullable exact-Boolean precomputed verdict, and the
  three distinct nonempty diagnostic fields `mistake`, `reason`, and
  `minimal_fix`.

- [ ] **Step 2: Implement and test `DiagnoseAnswer` as a normal graph**

  Mechanical true goes directly to equal Done. Mechanical false reveals gold
  and requests the three fields. No evaluator asks explicit yes/no first; yes
  finishes equal and no opens the separate explanation Prompt. Invalid replies
  preserve earlier Turns.

- [ ] **Step 3: Implement and test `AskAndDiagnose`**

  The ask Prompt accepts exactly `{"outcome": "answered", "evidence":
  {"answer": str}}`, then a visible Call constructs `DiagnosisCase` from that
  Turn. A concrete subclass owns evaluator, identity, and version. Require the
  evaluator result to have exact type `bool`.

- [ ] **Step 4: Implement the three CaseMaker constructors**

  `diagnose_answer_on` extracts an accepted answer and optionally precomputes a
  verdict. `ask_and_diagnose_on` supplies a `QuestionCase` Charter.
  `case_sequence_after` snapshots finite items and selects index
  `len(history.attached_calls(case_maker_id=id))`; exhaustion declines and
  overrun faults. Empty configurations, mutable source collections, evaluator
  non-Booleans, and malformed projected Charters are rejected. No counter or
  index is persisted.

  Add two composition tests required by the normative integration catalogue:
  an evaluator-backed fresh-question sequence using an `AskAndDiagnose`
  subclass, and a non-diagnostic child scheduled by a CaseMaker. Reopen each
  composition after child attachment, child Done, and atomic return.

- [ ] **Step 5: Prove the library adds no core persistence concepts**

  Assert serialized Reckonings contain only ordinary ActiveRun, Turn,
  ActionRecord, CallRecord, DoneRecord, completed-run, effect, and fault values.
  Search source to ensure core engine/storage does not import the diagnostic
  module.

  ```bash
  rg -n 'from officina\.rutter\.diagnostic|import officina\.rutter\.diagnostic' src/officina/rutter/engine.py src/officina/rutter/storage.py
  python3 -m pytest tests/test_rutter_diagnostic.py tests/test_rutter_hooks.py -q
  ```

  Expected: search has no matches and tests pass.

### Task 10: Cut Compass over to the four-method interface

**Files:**
- Rewrite: `skills/using-compass/SKILL.md`
- Rewrite: `skills/using-compass/tests/test_using_compass_instructions.py`
- Modify: `docs/officina/compass-rutter.md`
- Modify: `src/officina/rutter/blueprints/engine.yaml`
- Modify after behavior stabilizes: `skills/using-compass/blueprint.yaml`
- Modify after behavior stabilizes: `skills/using-compass/blueprints/gateway.yaml`
- Modify: `tests/test_officina_blueprint_graph.py`

**Interfaces:**
- Consumes: the four Rutter operations and two-part Message.
- Produces: LLM-facing Compass instructions that operate the public Rutter
  methods while Rutter executes automatic Python work internally. Compass sees
  only LLM Messages, validation results, the current NodeView, and
  terminal/blocking conditions.

- [ ] **Step 1: Write Compass contract tests**

  Assert Compass requests `get_instruction` only after
  `next(..., continue_=True)` has settled automatic work, submits LLM results
  through `next`, never calls `advance`, never manipulates an active-child
  stack, never executes `PythonInstruction` itself, and receives Message with
  exactly `instructions` and `data`.

- [ ] **Step 2: Update the skill instructions and documentation**

  Describe where `next` resumes: at the deepest active entered node. Explain
  `continue_=True`, validation failure, terminal/fault/uncertain conditions,
  and that history—not a verbose return value—contains intermediate traversal.

- [ ] **Step 3: Update the gateway contract and regenerate owned blueprints**

  Use `famulus:regenerate-blueprints` rather than editing generated contract
  blocks by hand. The gateway remains thin: it operates the invoker-provided
  Rutter through the four methods; it does not implement automatic Python,
  hook, or diagnostic logic.

  Bump `rutter.interface.bound-operations` from version 2 to version 3
  because replacing `advance` with `next` is breaking. Update
  `using-compass.source.gateway` here. Record the inquisitive-inventory CLI as
  the other required consumer: Task 11 creates its accepted source blueprint
  against version 3, and Task 12 proves no repository consumer still
  pins version 2.

- [ ] **Step 4: Run Compass and blueprint checks**

  ```bash
  python3 -m pytest skills/using-compass/tests/test_using_compass_instructions.py -q
  python3 -m pytest tests/test_blueprint_inventory.py tests/test_officina_blueprint_graph.py -q
  ```

### Task 11: Rebuild and exercise the inventory diagnostic example

**Files:**
- Rewrite: `skills/math-dependency-graph/_rtx/_inquisitive_inventory_rutter.py`
- Rewrite: `skills/math-dependency-graph/_rtx/_inquisitive_inventory_cli.py`
- Rewrite: `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py`
- Rewrite: `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py`
- Create from the accepted prototype ownership contract: `skills/math-dependency-graph/_rtx/blueprints/rtx-inquisitive-inventory-rutter.yaml`
- Create from the accepted prototype ownership contract: `skills/math-dependency-graph/_rtx/blueprints/rtx-inquisitive-inventory-cli.yaml`
- Modify: `skills/math-dependency-graph/_rtx/blueprint.yaml`
- Preserve as frozen input: appendix chunks and adjudicated gold standard used
  by the prototype trials

**Interfaces:**
- Consumes: `case_sequence_after`, an inventory evaluator, completed attached
  calls, and a repeat-safe application Action.
- Produces: per-iteration report -> gold comparison -> optional diagnosis ->
  ledger publication -> next iteration, with semantic equality independent of
  exact node labels. The CLI source blueprint consumes
  `rutter.interface.bound-operations@3`.

- [ ] **Step 1: Write frozen integration cases**

  Include semantically equal inventories with renamed nodes, unequal node and
  edge sets, unresolved endpoints, omitted entities, recovered edges, and
  partial chains. Gold must not enter the ordinary report Message before its
  response is accepted.

- [ ] **Step 2: Define the parent and diagnostic child composition**

  The report Prompt asks for new nodes and edges for the current appendix
  chunk. A sequence CaseMaker diagnoses the accepted canonical response against
  the matching gold string. The attached child returns equal/different. The
  frozen edge resumes to a repeat-safe Action that locates exactly one
  `(maker_id, edge_id)` CallRecord, writes any differing result to the ledger
  using `action_id` as idempotency key, and enters the next report or Done.

- [ ] **Step 3: Test recovery and ledger idempotency**

  Reopen after report acceptance, child push, equality answer, diagnosis
  answer, child return, ledger effect, and next Prompt entrance. Verify no case
  repeats or skips and no ledger row duplicates. Assert the created CLI source
  blueprint pins `rutter.interface.bound-operations` version 3.

  Run the complete focused integration boundary:

  ```bash
  python3 -m pytest skills/math-dependency-graph/_rtx/tests/test_inventory_unit_iterator.py skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py -q
  ```

- [ ] **Step 4: Run several interactive frozen-appendix iterations**

  Persist exact delivered Messages, accepted Responses, evaluator/LLM verdict,
  child RunResult, and ledger row. Classify failures as core lifecycle,
  CaseMaker construction, evaluator semantics, diagnostic prompt usefulness,
  or application adapter. Fix only runtime/library defects revealed by the
  trace; do not tune `inventory.md` during this task.

- [ ] **Step 5: Repeat after each correction**

  Run at least one equal and one different case through a new on-disk voyage
  after the final correction. The success condition is a readable and accurate
  diagnostic trace, not agreement with exact labels.

### Task 12: Remove obsolete runtime paths and pass the final gates

**Files:**
- Remove obsolete exports/branches from: `src/officina/rutter/`
- Update: `src/officina/rutter/blueprint.yaml`
- Update: `src/officina/rutter/blueprints/model.yaml`
- Update: `src/officina/rutter/blueprints/storage.yaml`
- Update: `src/officina/rutter/blueprints/engine.yaml`
- Update: `src/officina/rutter/blueprints/runtime.yaml`
- Update/create: `src/officina/rutter/blueprints/diagnostic.yaml`
- Update/create: `src/officina/rutter/blueprints/hooks.yaml`
- Update: `test_support/rutter_fixtures.py`
- Update: `docs/officina/compass-rutter.md`
- Update: `skills/using-compass/SKILL.md`
- Update: `skills/using-compass/tests/test_using_compass_instructions.py`
- Update: `skills/math-dependency-graph/_rtx/_inquisitive_inventory_rutter.py`
- Update: `skills/math-dependency-graph/_rtx/_inquisitive_inventory_cli.py`
- Update: `skills/math-dependency-graph/_rtx/blueprints/rtx-inquisitive-inventory-rutter.yaml`
- Update: `skills/math-dependency-graph/_rtx/blueprints/rtx-inquisitive-inventory-cli.yaml`
- Update: `skills/math-dependency-graph/_rtx/blueprint.yaml`
- Update only if regeneration changes them: owning parent blueprints and
  `references/blueprint/runtime_dependencies.json`

**Interfaces:**
- Consumes: the tested version-3 runtime and migrated integrations.
- Produces: one maintainable core with no compatibility reducer or
  diagnostic-specific lifecycle.

- [ ] **Step 1: Search for obsolete concepts**

  ```bash
  rg -n '\.advance\(|def advance\b|current_state_id|Fix\.lifecycle|_DiagnosticFrame|_DiagnosticState|pending_message|case_queue|schedule_index' src/officina/rutter tests/test_rutter_* test_support/rutter_fixtures.py skills/using-compass skills/math-dependency-graph/_rtx docs/officina/compass-rutter.md
  rg -n -U 'interface: rutter\.interface\.bound-operations\n[[:space:]]+version: 2' src skills
  ```

  Expected: matches exist only in explicit historical/rejection text. Remove or
  migrate every executable caller and normative document match.

- [ ] **Step 2: Regenerate and validate blueprint ownership**

  Update source blueprints to match the final source boundaries, regenerate
  their generated artifacts using the repository workflow, and validate both
  inventory and graph consistency.

- [ ] **Step 3: Run all focused Rutter and integration tests**

  ```bash
  python3 -m pytest tests/test_rutter_model.py tests/test_rutter_storage.py tests/test_rutter_runtime.py tests/test_rutter_lifecycle.py tests/test_rutter_engine.py tests/test_rutter_hooks.py tests/test_rutter_diagnostic.py skills/using-compass/tests/test_using_compass_instructions.py skills/math-dependency-graph/_rtx/tests/test_inventory_unit_iterator.py skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py -q
  ```

- [ ] **Step 4: Run the configured repository gate**

  ```bash
  python3 repo_checks.py --suite precommit --jobs 8
  ```

  Expected: exit 0. Record pass/fail counts and elapsed time from the fresh run;
  do not reuse earlier prototype evidence.

- [ ] **Step 5: Perform the simplification audit**

  Review against these questions:

  1. Can an author understand one Rutter by reading `define_states()` and
     `define_case_makers()`?
  2. Is node entrance the only persisted control coordinate at every depth?
  3. Is every accepted piece of work represented once and recoverable after
     the next callback fails?
  4. Does the core import no diagnostic or inventory code?
  5. Is there one reducer, one version-3 codec, no persisted child queue, and no
     hidden transition-routing hook?
  6. Do standard helpers remove application boilerplate without adding a DSL?

  Any negative answer blocks completion and must be resolved with a focused
  failing test before another full gate.

- [ ] **Step 6: Review the final owned diff**

  Verify the dirty prototype remains untouched, the implementation worktree
  contains only the planned ownership set, and its tracked design documents
  remain byte-identical to the master source. If commit authority exists, stage
  only reviewed explicit paths; otherwise hand off the verified uncommitted diff
  and suggested commit boundaries.

---

## Suggested review/commit boundaries

These are review gates, not authorization to commit:

1. integrated model, version-3 storage, definition binding, and Prompt/Done
   lifecycle (Tasks 2-5; no earlier green boundary);
2. recursive Calls;
3. Actions and effect recovery;
4. transition hooks;
5. diagnostic library;
6. Compass and inventory integrations; and
7. blueprint regeneration and obsolete-code removal.

Each boundary must have its focused tests passing before work proceeds. A
failure after a boundary should be repaired in that boundary rather than
papered over in a later integration task.

## Migration policy

Version-1 and version-2 Reckonings are rejected with a stable error explaining
that their control semantics are unsupported. Do not infer a version-3
entrance, open Turn, accepted-record authority, recursive child, or effect
disposition from legacy fields. If a separately identified live voyage later
must be retained, write a one-off, offline migration tool against a frozen
fixture and audit its output; do not add migration branches to the reducer.
Registry roots are caller-provided, so this plan makes no claim that a global
legacy-voyage scan is possible or complete.

The source API cutover is intentionally decisive. Repository callers migrate
from `advance()` and old Message shapes in the same implementation branch.
There is no `BaseRutterV2`, feature flag, compatibility facade, or dual-write
period.
