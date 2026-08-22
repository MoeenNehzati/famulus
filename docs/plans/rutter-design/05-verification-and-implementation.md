# Rutter verification and acceptance catalogue

This document is the normative acceptance catalogue and compatibility record.
Core and hook semantics are defined by `01-core-design.md`,
`02-runtime-reference.md`, and `03-hook-library.md`. `04-examples.md` is
illustrative. The executable task order and review boundaries are owned solely
by `06-core-reimplementation-plan.md`.

## Required evidence before implementation approval

### Definition and model contracts

- binding rejects invalid IDs, versions, targets, outcomes, routes, callback
  signatures, non-Boolean multiple-case policy, duplicate CaseMaker IDs, and
  inconsistent transitive children;
- every persisted value has an exact finite JSON projection;
- Message has exactly `instructions` and `data`, with engine-owned state data;
- every Done normalizes to `RunResult(outcome, value)`; and
- definition instances remain stateless and no-argument constructible.

### Prompt contracts

- exact Message persists as an open Turn and acceptance fills that Turn's
  Response without creating a waiting control state;
- stale revisions, unknown outcomes, nonfinite evidence, invalid envelopes, and
  contextual validation failures are rejected without mutation;
- re-entering one state may render new data from accepted history while keeping
  instructions fixed; and
- reopen returns the stored open-Turn Message rather than rerendering it.

### Public operating interface

- `get_instruction()` and `get_current_node()` are read-only and resolve the
  active leaf recursively;
- `validate(response)` never mutates or advances the entered node;
- rejected `next(response)` leaves the entered node and history unchanged;
- `next(..., continue_=False)` returns the first node actually entered by the
  completed operation: child start when one intervenes, otherwise parent target;
- `next(..., continue_=True)` runs automatic Python and nested work until an LLM
  instruction, terminal node, fault, or uncertainty, and returns only that final
  active node;
- every intermediate node and child traversal remains recoverable from durable
  history without appearing in the return value; and
- `next(..., dry_run=True)` predicts only the immediate parent-edge destination,
  may run pure validation/routing callbacks, never runs Actions, CaseMakers, or
  children, returns a NodeView with no entrance ID, and raises
  `PreviewUnavailable` when required results are absent.

### Actions and effects

- pure, repeat-safe, and non-repeat-safe modes have distinct tests;
- `PythonInstruction.run()` persists effectful completion before returning, and
  `next(result)` accepts only the exact durable completed result;
- repeat-safe retries preserve one stable action ID;
- non-repeat-safe planned, completed, and uncertain crash windows are covered;
- non-repeat-safe crash tests distinguish before invocation, after the durable
  uncertain marker, after effect execution, and after completed persistence;
- completed recovery moves into exactly one ActionRecord;
- later callback failure preserves completed Action work; and
- reopen rejects a wrong or non-leaf owner run, stale entrance ID, mismatched
  state or mode, an owner with an active child, and an already-consumed action
  ID for planned, completed, and uncertain recovery records.

### Calls and nesting

- explicit Call attachment, child Prompt, child Action, child Done, and return are
  tested across restart boundaries;
- recursively nested children and grandchildren preserve one global revision,
  one active leaf, and one entered-node coordinate per active Rutter;
- every entrance has a unique ID, every completion record binds to it, and
  Prompt, Action, and Call self-loops recover unambiguously;
- stale answers are rejected across frame depth by that global revision;
- RunResult mapping and callable routing are covered;
- child faults retain the complete recursive parent/child path;
- returned child records survive later routing or hook-selection failure; and
- atomic reopen is covered after every push, accepted answer, effect result,
  child return, and parent resumption boundary.

### Hooks and CaseMakers

- after-state, before-target, exact-edge, post-Call, and post-Done matching;
- zero, one, and multiple selected maker behavior;
- multiple selections fault before child start when
  `allow_multiple_cases_at_once` is false and run sequentially when true;
- attached children always resume the frozen edge and cannot route it;
- recovery recomputes pure/versioned routing and maker selection, skips stable
  completed maker/edge identities, and never persists a case queue or phase;
- recomputation uses the strict history prefix before the accepted source
  record; same-edge CallRecords are visible only to engine skip logic;
- atomic child attachment and atomic return settlement;
- active children preserve call ID, and return settlement archives/detaches and
  appends the CallRecord before later routing or child attachment operations;
- exact maker/edge CallRecord provenance;
- explicit Calls with colliding site names do not enter `attached_calls`; and
- selected children run in definition order, while intrinsically dependent
  attached work may be represented by one orchestrating child.

### Standard diagnostic children

- `DiagnoseAnswer`: evaluator true, false, and absent branches;
- without evaluator, explicit yes finishes and explicit no opens a separate
  three-field diagnostic Prompt;
- invalid equality replies and incomplete diagnostics retain earlier Turns;
- `AskAndDiagnose`: exact ask contract, accepted answer extraction, static
  evaluator subclass, visible child Call, and exact RunResult forwarding;
- exact Boolean evaluator enforcement; and
- reopen after each Prompt, child push, Done, and return.

### CaseMaker constructors

- fixed and context-derived QuestionCases;
- canonical text projections of structured actual and expected answers;
- evaluator result sealed into DiagnosisCase Charter;
- identity Charter and custom Charter-builder behavior;
- immutable source and item snapshots;
- state filtering, exhaustion, and count-overrun fault;
- restart neither repeats nor skips items;
- duplicate maker/edge identity faults; and
- extractor/evaluator failure preserves the accepted source record.

### Integration

- flat completed-run archive references are structurally valid and preserve one
  DoneRecord result authority;
- node entrance is the sole persisted control coordinate; validation,
  evaluation, transition selection, CaseMaker pooling, and return settlement
  introduce no persisted phases;
- continuation-limit exhaustion remains anchored at the entered node and can be
  resumed without a persisted yield state;
- target Prompt rendering failure retains the source entrance and records the
  attempted target only as fault metadata;
- reopen resolves active definitions without requiring executable definitions
  for historical completed runs;
- ordinary non-diagnostic Compass workflow behavior is preserved after
  migration to the four-method Rutter interface;
- several frozen appendix inventory iterations, including semantically equal
  and unequal reports, with `mistake`, `reason`, and `minimal_fix` requested only
  after inequality and semantic label differences treated as equal;
- a fresh-question sequence using an evaluator subclass;
- a non-diagnostic scheduled child;
- an application-owned repeat-safe ledger Action using exact maker/edge lookup;
- Compass integration without stack manipulation; and
- persisted reopen through real storage, not only in-memory fixtures.

## Compatibility work

The external Rutter methods are:

```python
get_instruction() -> Instruction | None
validate(response) -> ValidationReport
next(response=MISSING, *, continue_=True, dry_run=False) -> NodeView
get_current_node() -> NodeView
```

With continuation, `next` returns only the final entered node that cannot
proceed automatically; history proves the traversed path. Without continuation
it returns the first node actually entered, including a child start. Dry-run is
different: it previews only the parent-edge target using already available
results and pure callbacks, or raises `PreviewUnavailable`.

Required migrations:

- flat outbound Messages become `{instructions, data}`;
- dynamic instruction text moves into data callbacks;
- scalar Done values become `RunResult`;
- any run-specific definition-instance fields move into Charter or history;
- callers of `advance` migrate to `next`; Compass remains a thin LLM-facing
  operator over the public Rutter methods;
- old diagnostic-specific reducer state becomes ordinary child Rutters and
  attached CallRecords; and
- consumers that inferred progress from conversation history use Reckoning.

Storage changes require an explicit storage-version migration or rejection;
they must not be silently reinterpreted.

## Implementation ownership

`06-core-reimplementation-plan.md` is the only implementation sequence. Its
task ownership must cover this catalogue as follows:

- Tasks 2-5 establish the value model, storage, definition binding, and the
  Prompt/Done lifecycle through the base four-method interface;
- Task 6 adds recursive Calls and atomic return settlement;
- Task 7 adds Actions and effect recovery;
- Task 8 adds transition hooks, CaseMaker selection, and attached-call history;
- Task 9 builds the diagnostic children and CaseMaker constructors;
- Tasks 10-11 migrate Compass and the inventory integration; and
- Task 12 removes obsolete paths and runs the final acceptance gates.

This mapping assigns evidence to tasks; it does not introduce a second task
order or review boundary.

## Planning gate

Implementation should begin only after:

1. the normative design in documents 01-03 is accepted;
2. the illustrative examples in document 04 have been reviewed for consistency
   with that design;
3. this acceptance catalogue is accepted;
4. a current cross-document consistency review covers the normative design,
   examples, acceptance catalogue, and implementation plan; and
5. `06-core-reimplementation-plan.md` is approved as the sole implementation
   sequence and maps changes onto the live Rutter code and existing
   dirty-worktree ownership.

## Audit archive

The design history remains outside this reading sequence:

- `notes/node-entry-lifecycle-integration-audit.md`.

Earlier drafting audits remain in the prototype workspace. They are not part of
the tracked normative design and are not required reading for implementers or
ordinary authors.
