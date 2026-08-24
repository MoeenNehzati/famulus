# Rutter requested-change ledger

Status: implemented and verified on `feat/rutter-node-entry-core`.

This ledger records the requested Rutter simplification. The implementation
must make the product easier to understand and maintain without removing
workflow, recovery, diagnostic, or persistence capabilities.

## Product vocabulary

Use one coherent public vocabulary. Prefer descriptive terms over mechanical
or metaphorical terms when the two conflict.

### Evolution kinds

An **evolution** is one reusable node in a Rutter definition. An **entered
evolution** is one runtime occurrence of that definition node. Rename the four
author-facing evolution kinds without changing their behavior:

| Current | Approved | Meaning |
| --- | --- | --- |
| `Prompt` | `LLMStep` | requires an LLM response |
| `Action` | `MachineStep` | executes machine work |
| `Call` | `SubRutter` | enters a child Rutter |
| `Done` | `Terminal` | completes the Rutter |

Apply the evolution vocabulary consistently to the author-facing model:

| Current | Approved |
| --- | --- |
| `State` | `Evolution` |
| `state_id` | `evolution_id` |
| `start_state` | `initial_evolution_id` |
| `define_states()` | `define_evolutions()` |
| `StateContext` | `EvolutionContext` |
| `context.state` | `context.evolution` |
| `EnteredNode` | `EnteredEvolution` |
| `NodeView` | `EvolutionView` |
| `node_entry_id` | `evolution_entry_id` |
| `ActionContext` | `MachineContext` |
| `ActionResult` | `MachineResult` |
| `ActionRecord` | `MachineRecord` |
| `action_id` | `machine_id` |
| `CallRecord` | `SubRutterRecord` |
| `CallRecordView` | `SubRutterRecordView` |
| `CompletedRunView` | `CompletedVoyageView` |
| `call_id` | `invocation_id` |
| `completed_run_id` | `completed_voyage_instance_id` |
| `DoneRecord` | `TerminalRecord` |
| `RunResult` | `VoyageResult` |
| `AnswerContext` | `LLMResponseContext` |
| `PythonInstruction` | `MachineInstruction` |

Keep `Rutter`, `Voyage`, `Charter`, `Reckoning`, and `HistoryView`. They are
clear within the maritime vocabulary and name distinct responsibilities.

Do not retain permanent aliases for the replaced public names. Update all
in-repository consumers atomically. The Rutter module is experimental, so a
clean versioned cutover is preferable to carrying two vocabularies.

Retain `AnswerSpec`, `Message`, `Response`, and `Turn`: an answer specification
describes the expected answer, a Message is the public LLM instruction
projection, a Response is its submitted revision envelope, and a Turn joins the
two. These terms remain distinct from machine results and terminal voyage
results.

The public `Message` projects its persisted v3 identity envelope as:

```text
data
  evolution
    id
    entry_id
    revision
  payload
```

The stored v3 message retains `data.state` unchanged. The codec, not callers or
author callbacks, owns the projection between wire and public vocabulary.

### Transitions and hooks

Use **transition**, not edge, in the public model. A **TransitionHook** is an
attachment that may run a child Rutter after a transition has been selected
and before its destination evolution is entered.

| Current | Approved |
| --- | --- |
| `CaseMaker` | `TransitionHook` |
| `EdgeMatch` | `TransitionMatch` |
| `EdgeContext` | `TransitionContext` |
| `define_case_makers()` | `define_transition_hooks()` |
| `on_edge()` | `on_transition()` |
| `case_sequence_after()` | `hook_sequence_after()` |
| `edge_id` | `transition_id` |
| `EdgeContext.edge` | `TransitionContext.transition` |
| `attached_to_edge_id` | `attached_to_transition_id` |
| `case_maker_id` | `transition_hook_id` |
| `allow_multiple_cases_at_once` | `allow_multiple_hooks_per_transition` |

Retain `after()` and `before()` as concise transition-match constructors.
Keep domain terms such as `QuestionCase` and `DiagnosisCase`; those are cases,
not hooks.

`transition_id` is the accepted source-record ID that authorizes the
transition; it is not a separately persisted transition object.

`allow_multiple_hooks_per_transition` permits multiple selected hooks for one
transition. Selected hooks still execute sequentially.

`SubRutter` and `TransitionHook` remain separate concepts. `SubRutter` names
the explicit evolution that invokes its `child`; it does not name the child
Rutter definition itself. Its child result routes the parent. A `TransitionHook`
attaches work to an already selected transition and resumes that frozen
transition after the child completes.

Rename the public history queries consistently:

| Current | Approved |
| --- | --- |
| `turns(state_id=...)` | `turns(evolution_id=...)` |
| `latest_turn(state_id=...)` / `require_latest_turn(state_id=...)` | `latest_turn(evolution_id=...)` / `require_latest_turn(evolution_id=...)` |
| `actions(state_id=...)` | `machines(evolution_id=...)` |
| `calls(site=...)` | `subrutters(origin_evolution_id=..., transition_hook_id=...)` |
| `attached_calls(case_maker_id=..., edge_id=...)` | `hook_runs(transition_hook_id=..., transition_id=...)` |
| `done()` | `terminal()` |
| `latest_action(state_id=...)` / `require_latest_action(state_id=...)` | `latest_machine(evolution_id=...)` / `require_latest_machine(evolution_id=...)` |
| `latest_call(site=...)` / `require_latest_call(site=...)` | `latest_subrutter(origin_evolution_id=..., transition_hook_id=...)` / `require_latest_subrutter(origin_evolution_id=..., transition_hook_id=...)` |

Public sub-Rutter history projections do not expose the overloaded legacy
`site` pair:

```text
SubRutterRecord
  invocation_id: str
  origin_evolution_id: str | null
  transition_hook_id: str | null
  attached_to_transition_id: str | null
  completed_voyage_instance_id: str

SubRutterRecordView
  invocation_id: str
  origin_evolution_id: str | null
  transition_hook_id: str | null
  attached_to_transition_id: str | null
  completed: CompletedVoyageView
  result: VoyageResult

CompletedVoyageView
  voyage_instance_id: str
  rutter_id: str
  definition_version: int
  history: HistoryView
  result: VoyageResult
```

Exactly one of `origin_evolution_id` and `transition_hook_id` is non-null.
`attached_to_transition_id` is non-null exactly for a transition-hook
invocation. The two origin filters are optional and mutually exclusive on
`subrutters()`, `latest_subrutter()`, and `require_latest_subrutter()`. The v3
codec maps these projections to and from `call_id`, `site_kind`, `site_id`,
`attached_to_edge_id`, and `completed_run_id` without changing the wire.

## Public operating surface

### Add one atomic read operation

Replace the ordinary two-read sequence of `get_current_node()` followed by
`get_instruction()` with:

```python
status = voyage.get_status()
```

`get_status()` is read-only and returns one immutable `VoyageStatus` containing
a coherent snapshot:

```text
VoyageStatus
  current_evolution: EvolutionView
  instruction: Message | MachineInstruction | null
  active_result: VoyageResult | null
  fault: FaultSummary | null
```

`get_status()` executes under one `Voyage` transaction. It loads and
bound-validates one Reckoning, selects its deepest active evolution, and
calculates the condition once. It does not invoke author callbacks or mutate
the Reckoning.

The instruction is the public projection of the stored `Message` for a ready,
unaccepted `LLMStep`, the engine-owned `MachineInstruction` for a ready
`MachineStep`, and otherwise null. `active_result` is present only when the
active evolution has an already persisted matching `TerminalRecord` **and** the
calculated condition is `terminal`. It is null for `fault`, `uncertain`, and
`ready`, even if a matching TerminalRecord remains in history. It therefore
reports a terminal child when diagnostic continuation has stopped inside that
child, not an unfinished root result.

Condition priority remains fault, then uncertain effect, then terminal or
ready. `FaultSummary` contains only:

```text
FaultSummary
  category: str
  evolution_id: str | null
  evolution_entry_id: str | null
  target_evolution_id: str | null
  transition_hook_ids: tuple[str, ...]
```

It omits run IDs and raw persistence data. An opaque legacy fault reopens as a
non-null summary with category `opaque` and unavailable fields set to null or
empty; it is neither rejected nor exposed verbatim.

Internally, a `KnownFault` retains strict coordinate matching during bound
validation. An `OpaqueFault` has no coordinate invariant and bypasses only that
matching check during reopen; it remains permanently fault-conditioned and
exposes only `FaultSummary(category="opaque", ...)`. A malformed known fault
does not become opaque and remains rejected.

The single read prevents the current evolution and instruction from being
observed from different revisions. Remove `get_current_node()` and
`get_instruction()` at the versioned cutover rather than preserving redundant
long-lived methods.

### Keep one advancing API

Retain one `next()` API with optional diagnostic controls:

```python
voyage.next(response=MISSING, *, continue_=True, dry_run=False)
```

`next()` returns `EvolutionView` for ready, terminal, and preview conditions. A
call that durably creates a new fault or uncertain effect may return that new
condition's view. Calling `next()` when the Voyage is already faulted or
uncertain raises `RunBlocked`, preserving the current blocked-entry behavior.

Do not split diagnostic stepping or preview into separate public operations.
`continue_=False` remains the first-entered-evolution diagnostic stop boundary,
and `dry_run=True` remains the read-only immediate-transition preview.

Calling ordinary `next()` at an `LLMStep` without a response remains an error.
Use `get_status()` for inspection. `validate(response)` remains a separate
read-only operation because its structured issues support response repair
before mutation.

### Make machine execution engine-owned

Rutter owns `MachineStep` callback execution and its recovery protocol. A
public `MachineInstruction.run()` remains available for current in-process
executors, but it is an engine-owned recovery wrapper rather than the raw
author callback. Ordinary continuation may execute that same wrapper through
`next()`.

Preserve all current machine-result routes:

- `next(MachineResult)` at a pure `MachineStep` accepts the supplied result
  without invoking the callback;
- an omitted result executes the machine callback through Rutter;
- effectful supplied results are accepted only through the existing durable
  completed-recovery authority; and
- `next(MachineResult, dry_run=True)` may use a non-authoritative preview result
  without executing, recording, or accepting it.

Preserve non-repeat-safe publication order: planned marker, uncertain marker,
callback, completed marker, then accepted `MachineRecord`.

## Object-oriented ownership

Use an object-oriented shell around a functional, immutable reduction core.
Do not move transition behavior onto every evolution or record merely to add
methods.

### Public `Voyage` owns operational lifecycle

Replace the private, externally returned `_BoundVoyage` with a named public
`Voyage`. It owns:

- bound definitions;
- the current `Reckoning` cache;
- the `ReckoningStore` collaborator;
- transaction and publication ordering; and
- `get_status()`, `validate()`, and `next()`.

Reducer functions receive explicit immutable inputs and return replacement
values. They must not reach through a protocol into `Voyage` private fields.
This creates one owner for the operational lifecycle and removes the
`runtime.py` and `engine.py` import cycle.

### Keep cohesive collaborators

- Definition binding and graph validation remain owned by the registry/runtime
  boundary.
- Pure run-tree, history, structural transition, hook attachment, and
  child-return transformations remain stateless functions in focused internal
  modules.
- Authored evaluation is a separate controlled boundary for route callbacks,
  hook Charters, LLM payloads, Terminal projections, and machine execution. It
  converts callback failures into the existing durable fault categories and
  preserves accepted-record-before-fault publication ordering.
- Machine-effect execution and recovery use a focused internal boundary.
- Machine-effect recovery is a specialization inside controlled authored
  evaluation, not a competing owner of machine execution.
- `ReckoningStore` continues to own confinement, locking, canonical bytes,
  compare-and-replace, and atomic filesystem persistence.
- Internal Protocols describe only collaborator operations actually used. The
  store Protocol includes `replace()`; no Protocol mirrors another object's
  private attributes.

Do not introduce stateless service classes, a generic repository, a separate
unit-of-work abstraction, or a generic reducer framework.

### Put invariants with their rightful owner

- Definition-independent run-tree, provenance, archive-reference, uniqueness,
  and acyclicity invariants belong to the private `Reckoning` aggregate
  validation boundary.
- Invariants requiring bound definitions belong to `Voyage`/runtime validation.
- Wire syntax, size/depth limits, canonicalization, and filesystem safety remain
  in storage.
- Active machine recovery uses a typed private `EffectRecovery` value with the
  exact existing seven-field wire representation. Fault storage remains an
  opaque finite-JSON private wrapper so legacy faults remain readable; only
  `FaultSummary` is typed publicly.

### Separate model responsibilities and narrow exports

Separate author definitions, durable records, history views, execution
contexts, and operating projections into focused internal modules. Moving a
type must preserve its supported runtime semantics.

The public facade exports only the authoring model, operating values, approved
hook constructors, errors, `RutterRegistry`, and `Voyage`. Persistence tree,
codec, recovery, and mutable operational internals remain private.

## Recovery and protocol correctness

Centralize validation of active machine recovery, including deterministic
machine identity, owning evolution, execution mode, and disposition. Reject a
persisted recovery marker that does not match the active bound `MachineStep`.
Run this bound validation on create, open, and every transactional reload
before any operation can execute.

Characterize and reject recovery authority attached to an `LLMStep`,
`SubRutter`, or `Terminal`, as well as a wrong machine mode or a machine ID that
does not match the deterministic entered-evolution identity.

Make every internal Protocol complete and honest. In particular, declare the
store's `replace()` operation and replace `tuple[Any, ...]` hook contracts with
the exact `TransitionHook` type. Do not invent hook substitutability that the
public authoring model does not provide.

## Preserved behavior

The refactor preserves:

- the four evolution kinds and their acceptance/routing behavior;
- explicit `SubRutter` versus attached `TransitionHook` lifecycle semantics;
- the flag that restricts one selected hook per transition unless multiple
  hooks are explicitly allowed;
- hook selection order, multiplicity faults, frozen-transition resumption, and
  sequential child execution;
- pure, repeat-safe, and non-repeat-safe machine modes;
- strict response validation and structured validation issues;
- `next()` continuation, diagnostic stepping, and dry-run preview semantics;
- fault, uncertain-effect, terminal, and recovery behavior;
- history observability, child provenance, revision authority, and restart
  behavior;
- storage confinement, locks, atomic replacement, and canonical serialization;
  and
- the ability to open existing persisted Reckonings.

Author-facing Python names and module boundaries change intentionally in one
versioned cutover. Concrete history record types and query keywords therefore
change at the Python level. Product capabilities, execution ordering,
persistence authority, wire history, and failure behavior do not.

## Storage boundary

Do not pursue a storage-v4 deduplication as part of this work. Do not remove
persisted coordinates, revision, action identity, or completed-run identity
merely because another value could derive them.

Public terminology does not rewrite the v3 wire format. A canonical write of
the same semantic Reckoning remains byte-for-byte identical and retains the
existing keys and discriminator strings, including `state_id`, `entry_id`,
`action_id`, `mode`, `site_kind`, `active_effect`, `explicit_call`, and
`attached_case`. Typed private values wrap those exact fields. Any changed v3
shape is out of scope and would require a separately approved storage-version
migration.

## Refactoring discipline

Apply one behavior-preserving structural move at a time and verify between
moves. Use this order:

1. Add characterization fixtures for legacy Reckoning open, supplied pure
   machine results, preview results, completed/uncertain effects, nested
   `SubRutter`/hook return, callback-fault ordering, and corrupted bound recovery.
   Cover `get_status()` at ready LLM, ready machine, nested terminal child,
   uncertain effect, known fault, opaque legacy fault, and fault with a matching
   TerminalRecord. Include archived completed children whose definitions are no
   longer available and inactive reachable child metadata that need not resolve
   on reopen.
2. Introduce typed `EffectRecovery` and opaque fault wrappers without changing
   v3 bytes or public names.
3. Separate pure structural reduction from controlled authored evaluation while
   preserving publication order.
4. Introduce `Voyage` lifecycle ownership and remove the runtime/engine cycle.
5. Make one atomic public cutover for vocabulary, `get_status()`, and every
   source, test, blueprint, generated interface contract, operator instruction,
   and authorized downstream consumer.
6. Split the remaining internal model responsibilities after the public seam is
   stable.

Verify between each step. Do not combine the lifecycle-owner move, reducer
boundary, model split, and recovery repair into one unreviewable change.

Prefer narrow extraction over rewriting `_next()` or flattening routing,
effects, hooks, recovery, and child execution into one abstraction.
