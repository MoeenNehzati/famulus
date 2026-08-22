# Rutter verification and implementation notes

This document collects evidence requirements, compatibility work, and the
implementation sequence. It is intentionally separate from the design.

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
- completed recovery moves into exactly one ActionRecord; and
- later callback failure preserves completed Action work;
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
- child faults retain the complete recursive parent/child path; and
- returned child records survive later routing or hook-selection failure.
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
- ordinary non-diagnostic Compass usage remains unchanged; and
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

## Core implementation sequence

1. Freeze public JSON schemas and definition-binding errors with failing tests.
2. Implement exact Message/Response separation and open-Turn Messages.
3. Implement RunResult normalization and DoneRecord authority.
4. Implement recursive active runs, unique entrance IDs, completed-run archive,
   and anchored/full history projections with entrance as the only control
   coordinate.
5. Implement explicit child attachment and atomic return settlement.
6. Implement effect recovery for all Action modes.
7. Implement edge staging, accepted-work preservation, and cardinality faults.
8. Implement transition-hook child push and frozen-edge resumption.
9. Integrate the four-method Rutter interface and thin Compass operating guide.

## Hook-library implementation sequence

1. Add `attached_calls(case_maker_id=None, edge_id=None)` and collision tests.
2. Freeze QuestionCase, DiagnosisCase, and DiagnosisDetail projections.
3. Implement `DiagnoseAnswer` as its documented ordinary state graph.
4. Implement `AskAndDiagnose` with its visible Call.
5. Implement `diagnose_answer_on` and `ask_and_diagnose_on`.
6. Implement `case_sequence_after` with restart and history-overrun tests.
7. Re-express inventory diagnosis using `case_sequence_after`.
8. Run interactive frozen-appendix trials before adding another helper.

## Planning gate

Implementation should begin only after:

1. the five-document design is accepted;
2. `[complete]` a subagent semantic-preservation audit confirms this refactor
   did not change the prior design;
3. remaining public names and JSON schemas are frozen in tests; and
4. an implementation plan maps changes onto the live Rutter code and existing
   dirty-worktree ownership.

## Audit archive

The design history remains outside this reading sequence:

- `notes/node-entry-lifecycle-integration-audit.md`.

Earlier drafting audits remain in the prototype workspace. They are not part of
the tracked normative design and are not required reading for implementers or
ordinary authors.
