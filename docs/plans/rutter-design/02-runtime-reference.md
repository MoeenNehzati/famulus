# Rutter runtime reference

This document specifies durable representation, atomic transitions, recovery,
and invariants. It does not define hook authoring conveniences; those are in
`03-hook-library.md`.

## JSON boundary

All persisted values are finite JSON. Public value objects expose immutable
views. `to_json()` returns an independently deeply frozen plain JSON value;
`from_json()` rejects missing, extra, or wrongly typed fields.

Executable callbacks and definitions are never serialized. Reopen resolves
active definitions by stable Rutter ID and definition version.
`PythonInstruction` is an in-process view over such a callback and durable
action identity; it has no JSON projection and is never exposed to Compass
during automatic continuation.

## Local history

Each run has one chronological history:

```text
HistoryEntry = Turn | ActionRecord | CallRecord | DoneRecord

Turn
  record_id
  node_entry_id
  state_id
  revision
  message
  response: Response | null

ActionRecord
  record_id
  action_id
  node_entry_id
  state_id
  mode
  result: ActionResult

CallRecord
  call_id
  node_entry_id
  site_kind: explicit_call | attached_case
  site_id: state ID | CaseMaker ID
  attached_to_edge_id: str | null
  completed_run_id

DoneRecord
  record_id
  node_entry_id
  state_id
  result: RunResult

CompletedRun
  run_id
  rutter_id
  definition_version
  charter
  history
```

Entering a Prompt atomically renders and stores its Message as an open Turn.
Until a response is accepted, its `response` is null and repeated
`get_instruction()` calls return that exact stored Message without mutation.
Acceptance atomically fills the response on the same Turn. Thus request
delivery is a durable history fact without introducing a waiting lifecycle
state. Author-facing `turns()` exposes accepted Turns by default;
`open_turn()` exposes the sole unanswered Turn, when present.

DoneRecord is the sole stored completion-result authority. `CompletedRun.result`
and root result properties project it rather than storing another copy.

Author-facing `CallRecordView` resolves the completed run and result:

```text
CallRecordView
  call_id
  site
  attached_to_edge_id
  completed: CompletedRunView
  result: RunResult

CompletedRunView
  run_id
  rutter_id
  definition_version
  history: HistoryView
  result: RunResult
```

## HistoryView

```python
history.entries()
history.turns(state_id=None)
history.open_turn()
history.actions(state_id=None)
history.calls(site=None)
history.done()
history.latest_turn(state_id=None)
history.latest_action(state_id=None)
history.latest_call(site=None)
history.require_latest_turn(state_id=None)
history.require_latest_action(state_id=None)
history.require_latest_call(site=None)
```

Returned sequences and views are immutable. `latest_*` returns `None` when
absent; `require_latest_*` faults with a stable definition-error category.
The runtime may expose a prefix view: edge-routing and CaseMaker contexts end
strictly before their accepted source record, while a later entered node sees
the full history preceding that entrance.

The attachment-specific history query is defined with the CaseMaker API in
`03-hook-library.md`.

## Edges

Every successful state completion stages one real edge and one record:

```text
Edge
  edge_id
  source_entry_id
  source: state ID
  outcome: str
  target: state ID | null
```

- Prompt edge ID is its Turn record ID.
- Action edge ID is its ActionRecord ID.
- Explicit Call edge ID is its CallRecord ID.
- Done edge ID is its DoneRecord ID.
- `target=null` denotes completion or child return.

The accepted source record is durable authority. `then` reconstructs the Edge
deterministically from that record, the strict history prefix before it, and
the definition version; the Edge is not a separate control coordinate. Later
same-edge CallRecords are excluded from callback-visible history. The engine
consults them separately only to skip completed maker/edge identities.

Initialization is not an edge. Terminal attachments match the visible Done
source rather than a synthetic RETURN coordinate.

## Reckoning

One atomically replaced Reckoning file contains a recursive active run:

```text
Reckoning
  storage_version
  global_revision
  root: ActiveRun
  completed_runs: {run_id: CompletedRun}
  active_effect: EffectRecovery | null
  fault: Fault | null

ActiveRun
  run_id
  rutter_id
  definition_version
  charter
  entered_node: EnteredNode
  history
  active_child: ActiveChild | null

EnteredNode
  entry_id
  state_id

ActiveChild
  call_id
  kind: explicit_call | attached_case
  site
  attached_to_edge_id: str | null
  run: ActiveRun
```

The deepest active child is executable. Every run, including a child, persists
only one entrance occurrence `(entry_id, state_id)` as its control coordinate.
Entering a target always allocates a new entry ID, including a self-loop to the
same state ID. Every completion record names its source entry ID, so recovery
settles a record only while that entrance remains current. The parent remains
at its entered source while its child runs. `ActiveChild` is recursive ownership
and provenance, not a parent lifecycle phase. Its call ID is durable from child
allocation through the eventual CallRecord.

Messages, accepted responses, Action results, edges, and completed children are
history facts. Effects and faults are conditions anchored at the entered node,
not alternative control coordinates. Completed runs have no active child or
effect. Active and completed run IDs are disjoint.

## Call invariants

- Every non-root CompletedRun is referenced by exactly one CallRecord.
- Every CallRecord references one existing CompletedRun.
- Completed-run references are acyclic.
- Every CompletedRun contains exactly one DoneRecord.
- Entries after a DoneRecord may only be attached CallRecords bound to that
  Done edge.
- A CallRecord from an explicit Call has `attached_to_edge_id=null`.
- An attached CallRecord names the exact frozen edge ID.

Storage may normalize records by ID; author views resolve those joins.

## Push and return

### Explicit Call push

```text
enter Call state
-> construct and validate finite child Charter
-> allocate call and child run IDs
-> atomically attach the child run while the parent remains at Call
```

### Explicit Call return

After the child's DoneRecord is authoritative, one atomic return settlement:

```text
archive and detach child
+ append explicit CallRecord
```

The parent remains at its Call entrance after that commit. Routing, CaseMaker
pooling, later child attachment, and eventual target entrance are subsequent
operations reconstructed from the CallRecord. They cannot be part of the
return commit because an attached child may require LLM interaction. A crash at
any boundary therefore resumes from the same entered parent node and durable
record without repeating the returned child.

Attached-child push, return, and frozen-edge continuation are specified in
`03-hook-library.md`. Their persisted representation uses the same recursive
ActiveRun, ActiveChild, CompletedRun, and CallRecord authorities defined here.

## Definition discovery and reopen

Binding recursively discovers all children named by Call states and hooks,
including grandchildren. The executable definition graph must be acyclic and
identity/version consistent.

Reopen resolves definitions only for the recursively active root and children.
Completed runs remain structural audit records and do not require historical
executable code.

Reopen validates structural and referential consistency, not adversarial
integrity against an actor able to rewrite the entire unsigned file.

## Effects and recovery

Only the active leaf may execute an Action. Reckoning owns one effect slot:

```text
EffectRecovery
  action_id
  owner_run_id
  node_entry_id
  state_id
  mode
  disposition: planned | completed | uncertain
  result: ActionResult | null
```

Recovery invariants:

- `planned` means the effect has not been issued and has no result;
- `completed` holds the exact frozen ActionResult;
- `uncertain` means a non-repeat-safe effect may have executed and has no result;
- completed recovery is atomically consumed into one ActionRecord and cleared;
- `owner_run_id` is the deepest active run with no active child;
- `node_entry_id` and `state_id` identify its current effectful Action and its
  mode matches the bound definition;
- an action ID is absent from prior consumed ActionRecords; and
- action ID, not Prompt revision, identifies an operation.

Entering a repeat-safe or non-repeat-safe Action atomically creates its planned
EffectRecovery with the entrance. `get_instruction()` can therefore return the
stable `PythonInstruction` without mutation. Pure Actions need no recovery slot.

For an effectful Action, `PythonInstruction.run()` executes through the recovery
protocol and persists `completed` before returning its ActionResult. A supplied
ActionResult to `next(result)` is accepted only when it exactly matches that
completed recovery authority; `next()` then atomically moves the result into one
ActionRecord and clears recovery. Supplying a bare result while recovery is only
`planned` is rejected. With an omitted argument, automatic continuation invokes
the same wrapper and then consumes its durable result. A pure Action may accept
a directly supplied, validated ActionResult because replay has no effect risk.
For non-repeat-safe work specifically, the wrapper atomically changes
`planned -> uncertain` before invoking the author callback, then persists
`uncertain -> completed` on success. Reopen at `planned` may still issue the
effect; reopen at `uncertain` blocks conservatively; reopen at `completed`
returns the stored result without invoking it. Repeat-safe work may retry from
`planned` with the same action ID.

### Pure Action

The runtime evaluates it and records its ActionResult, selects the edge, runs
selected transition-hook children, and then enters the successor. Throughout
those operations the Action node remains the persisted control coordinate.

### Repeat-safe Action

The application receives a stable action ID as its idempotency key. Reopen may
retry the same action ID until the result is committed.

### Non-repeat-safe Action

```text
persist planned recovery
-> atomically mark uncertain
-> perform effect once
-> persist completed recovery with exact result
-> atomically move result into ActionRecord and clear recovery
```

If the process dies after the uncertain marker, reopen never silently retries,
whether the crash preceded or followed the actual effect. A completed recovery
is consumed into exactly one ActionRecord without rerunning the effect.

Effects are globally serialized because there is only one active leaf and one
effect slot.

## Accepted-work invariant

Once an LLM Response, ActionResult, or child return is accepted, its durable
record survives every later callback failure and is never replayed.

- A valid Prompt response and Turn remain durable if routing, hook selection,
  or target materialization fails.
- A completed Action result remains durable if later routing or hook selection
  fails.
- A returned child and parent CallRecord remain durable if result routing or
  attached-hook selection fails.
- CaseMaker failure commits the staged source record plus fault.
- Target Prompt materialization failure retains the source entrance and
  accepted record; fault metadata names the attempted target and callback. The
  target was not entered because Prompt entrance and open-Turn creation are
  atomic.
- Done result projection failure creates no DoneRecord and faults at Done.

These failures commit a fault after the accepted record. The user is not asked
to repeat accepted work.

## Faults

Callback exceptions, invalid callback return types, unavailable target states,
definition-version mismatches, malformed history, and violated invariants
become durable faults with the recursive active path retained.

Fault records contain stable categories and state/run coordinates, not raw
exception text that may reveal private data. A faulted or uncertain voyage
accepts no further LLM response and remains inspection-only. A child fault
faults the entire voyage; there is no parent catch initially.

Hook selection and cardinality behavior are specified in
`03-hook-library.md`.

## Storage and concurrency

- The whole Reckoning is locked and atomically replaced.
- One global revision prevents stale responses across nested frames.
- At most one active path and one effect exist.
- An unanswered Turn stores its Message exactly as delivered.
- Validation, evaluation, edge selection, CaseMaker pooling, and child return
  are operations, never persisted phase values.
- Automatic continuation has a bounded operation budget. Exhaustion raises a
  continuation-limit condition anchored at the currently entered node; it does
  not create a new state. A later `next()` resumes from that node.

## Compatibility boundary

The external Rutter interface is `get_instruction`, `validate`, `next`, and
`get_current_node`. `next(..., continue_=True)` returns only the final entered
node at which automatic work stops; history contains the intervening path.
`next(..., continue_=False)` returns the first node actually entered, which may
be an attached or explicit child start rather than the selected parent target.
`dry_run=True` instead previews only the immediate parent-edge target: it may
run pure validation and routing callbacks but never Actions, CaseMakers, or
children, and raises `PreviewUnavailable` when required results are absent.
At an entered Done it may run the pure result projection and return a terminal
preview of that same node without creating DoneRecord.
The new Message shape intentionally separates instructions and data. The
Response envelope remains unchanged. Existing flat Message content requires
migration to the two-part Message. Existing scalar Done values require wrapping
in `RunResult`.

The implementation must choose a new storage version or an explicit in-place
migration. It must not keep parallel reducers indefinitely or silently
reinterpret an in-flight legacy diagnostic voyage.
