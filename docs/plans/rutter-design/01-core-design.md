# Rutter core design

Status: accepted design basis; authoring and public-value vocabulary are aligned
with the implemented surface.

## Reading map

1. This document explains what a Rutter is and how an author uses it.
2. `02-runtime-reference.md` specifies durable state and recovery.
3. `03-hook-library.md` specifies transition hooks and reusable diagnostic
   children.
4. `04-examples.md` shows complete authoring patterns.
5. `05-verification-and-implementation.md` records compatibility, tests, and
   implementation order.
6. `06-core-reimplementation-plan.md` maps the accepted design onto the live
   code: what is preserved, rewritten, replaced, and verified.

## Goal

A Rutter converts prose instructions into a machine-managed LLM interaction
whose exact progress can survive process and LLM restarts. It must support:

- evolution-specific instructions;
- data derived from the initial Charter and accepted history;
- contextual validation and routing;
- ordinary nested Rutters;
- attached work on accepted transitions; and
- one small public interface for Compass.

The design favors explicit, inspectable control flow over a workflow DSL.

## Mental model

A Rutter is a stateless, versioned definition object. A Voyage owns one
execution from a finite JSON Charter and produces a `VoyageResult`.

```text
Rutter definition object
  identity + version
  initial evolution
  evolution graph
  transition hooks

Voyage
  Charter
  entered evolution
  accepted local history
  optional active child Rutter
```

The evolution selects stable instructions. The Charter and accepted history
supply changing data. Re-entering the same evolution may therefore produce a
new Message without changing its instructions: for example, a loop can supply
the next text chunk on each visit.

Each successful evolution completion selects one transition. Hooks may perform
attached work on that transition, but the selected continuation is frozen.
Result-directed child work is instead a visible `SubRutter` evolution.

Evolution entrance is the only persisted control coordinate. Rendering or
executing an instruction, validation, evaluation, transition selection,
transition-hook selection, and child return are operations from an entered
evolution, not additional lifecycle states. Requests, accepted responses,
MachineStep results, and child completions are durable history facts. An active
child applies this same model recursively while its parent remains at its
entered source evolution.

## Definition lifecycle

Direct `Rutter(...)` construction is preferred. It snapshots the authored
evolution mapping and transition-hook sequence into a stateless, run-neutral
definition object; a bound Voyage owns the Charter, Reckoning, storage, and all
execution state. Callbacks may be module functions or bound methods on a
stateless definition object. Binding validates, before a Voyage starts:

- nonempty stable `rutter_id` and positive `definition_version`;
- exact-Boolean `allow_multiple_hooks_per_transition`;
- one valid initial evolution;
- unique evolution and transition-hook IDs;
- every referenced local successor ID;
- LLMStep, MachineStep, and SubRutter routes;
- transition-hook identities and child constructors;
- callable signatures; and
- transitively referenced child identities and versions.

Definition instances are stateless and run-neutral. Per-run data belongs only
to the Charter, contexts, and durable Reckoning. Changing executable behavior,
instructions, routing, callbacks, hooks, or static hook data requires a
definition-version change.

Legacy subclasses remain compatible: they may declare metadata and implement
`define_evolutions()` and `define_transition_hooks()`, and the registry still
accepts no-argument class or factory sources.

Ordinary evolution loops are allowed. Only cycles in the graph of Rutter
definitions calling other Rutter definitions are rejected initially.

## Author-facing primitives

```python
Rutter(
    *,
    id: str,
    version: int,
    start: str,
    evolutions: Mapping[str, Evolution],
    hooks: Sequence[TransitionHook] = (),
    allow_multiple_hooks_per_transition: bool = False,
)
```

`Evolution` has four variants.

### LLMStep

```python
LLMStep(
    text: str,
    *,
    response_schema: JsonObject | None = None,
    data: Callable[[EvolutionContext], JsonObject] = empty_data,
    assess_response: Callable[[LLMResponseContext], ValidationReport] = ...,
    next_on_outcome: str | Mapping[str, str] | None = None,
    choose_next: Callable[[LLMResponseContext], str] | None = None,
)
```

- `text` is intrinsic instruction prose.
- `data` derives the current payload from Charter and accepted history.
- `response_schema` optionally constrains the complete flat response with a
  self-contained Draft 2020-12 JSON Schema.
- `assess_response` applies contextual acceptance without mutation after
  engine formatting, schema, and static outcome-map checks.
- `next_on_outcome` statically routes every accepted outcome, either to one
  target or through an outcome-to-target mapping.
- `choose_next` is the alternative callback-routing mode. Exactly one of
  `next_on_outcome` and `choose_next` is required.

Omitting `assess_response` accepts every response that passes engine
formatting, static mapping-key acceptance, and the optional schema. There are
no defaults or coercions in schema validation. The engine checks the entrance
token, finite JSON object shape, reserved metadata, outcome token, optional
schema, and static outcome mapping before calling contextual assessment.

```text
ValidationReport
  valid: bool
  issues: tuple[ValidationIssue, ...]

ValidationIssue
  path: tuple[str | int, ...]
  code: str
  message: str
```

A valid report has no issues; an invalid report has at least one. Validation
never mutates the Reckoning.

### MachineStep

```python
MachineStep(
    run: Callable[[MachineContext], MachineResult],
    *,
    mode: "pure" | "repeat-safe" | "non-repeat-safe",
    next_on_outcome: str | Mapping[str, str] | None = None,
    choose_next: Callable[[MachineContext, MachineResult], str] | None = None,
)
```

- `pure` is deterministic and side-effect free.
- `repeat-safe` may be retried using application idempotency.
- `non-repeat-safe` uses the runtime recovery protocol and may become uncertain.

```text
MachineResult
  outcome: str
  value: JsonValue
```

### SubRutter

```python
SubRutter(
    rutter_constructor: Callable[[EvolutionContext], Rutter],
    *,
    charter_constructor: Callable[[EvolutionContext], JsonObject],
    next_on_outcome: str | Mapping[str, str] | None = None,
    choose_next: Callable[[EvolutionContext, VoyageResult], str] | None = None,
)
```

Advancing from an entered SubRutter evaluates
`rutter_constructor(context) -> Rutter` and constructs the child Charter with
`charter_constructor(context)`. Repeated resolution within one Voyage must
return the same definition instance; a reopened registry may return a fresh
equivalent object with the persisted identity. A static mapping routes from
`VoyageResult.outcome`; `choose_next` may read the whole result but cannot
remove the outcome from the parent transition. Children may themselves contain
LLMSteps, MachineSteps, SubRutters, and hooks.

### Terminal

```python
Terminal(*, result: VoyageResult)
Terminal(*, result_constructor: Callable[[EvolutionContext], VoyageResult])
```

Exactly one explicit result mode is required. Every root and child completes
with the same value:

```text
VoyageResult
  outcome: str
  value: JsonValue
```

## Messages and responses

Every public Message has exactly two top-level parts:

```json
{
  "instructions": {
    "text": "Evolution-specific invariant instructions",
    "response_schema": {
      "type": "object",
      "required": ["outcome"]
    }
  },
  "data": {
    "evolution": {"id": "report", "entry_id": "entry-..."},
    "payload": {"chunk": "..."}
  }
}
```

The engine owns `data.evolution`; an LLMStep callback supplies only
`data.payload`. `Message` exposes these meaningful fields directly through
`text`, `response_schema`, `payload`, `evolution_id`, and
`evolution_entry_id` read-only properties.
The response is:

```json
{
  "outcome": "reported",
  "inventory": {}
}
```

The public response is one flat finite JSON object. Its nonempty `outcome` is
available for routing, and every other field is author-defined. The response
contains no revision or evidence wrapper. Its authority is supplied separately
as `responding_to=message.evolution_entry_id` to both `validate()` and
`advance()`.

One internal global revision still spans the active root-to-leaf path. The
exact delivered Message is stored as an open Turn atomically with LLMStep
entrance, and the accepted response fills that Turn. The persisted version-3
projection retains its historical `{revision, outcome, evidence}` envelope for
storage compatibility; that envelope is not a public response type.
`get_status()` only reads the stored Message, so it never rerenders or mutates
the Voyage.

## Contexts and purity

Callbacks receive immutable views:

```text
EvolutionContext
  charter: Charter
  evolution_id: str
  evolution_entry_id: str
  history: HistoryView

LLMResponseContext
  evolution: EvolutionContext
  message: Message
  response: JsonObject

MachineContext
  evolution: EvolutionContext
  machine_id: str

TransitionContext
  evolution: EvolutionContext
  transition: Transition
  record: Turn | MachineRecord | SubRutterRecord | TerminalRecord
```

`TransitionContext.evolution.history` is the immutable prefix strictly before
its source `record`, which appears exactly once through `record`. Later
SubRutterRecords attached to that transition are excluded from callback-visible
history and used only by the engine to skip stable completed hook/transition
identities. This keeps successor selection and every hook on one transition
anchored to the same typed context across recovery.
A child sees only its own Charter and local history; required parent data must
be copied deliberately into the child Charter.

LLMStep data, contextual response assessment, successor selection, SubRutter
Charter and Rutter construction, Terminal projection, transition matching,
transition-hook Charter and Rutter construction, diagnostic evaluation, and
pure MachineSteps depend only on immutable arguments and definition constants.
They do not read clocks, randomness, environment variables, networks, mutable
files, or databases.

External inputs enter through the initial Charter or an explicit effectful
MachineStep whose frozen MachineResult becomes history. Only MachineSteps may
perform external work.

The complete persisted record schemas are in `02-runtime-reference.md`; the
context schemas are above.

## Transitions, SubRutters, and hooks

Every successful evolution completion stages one real transition:

```text
source evolution + accepted outcome -> selected target or completion
```

Before entering that already-selected target, the runtime consults transition
hooks. Selected hook children are pooled in definition order and run
sequentially before the frozen transition resumes. If more than one is selected
while `allow_multiple_hooks_per_transition` is false, the Rutter faults before
starting any child. Hook work cannot redirect, replace, or cancel the
transition.

This is the central distinction:

```text
SubRutter evolution  child result may select the next parent evolution
transition hook      child finishes, then the frozen parent transition resumes
```

Every transition hook declares its child explicitly:

```python
TransitionHook(
    id: str,
    *,
    on: TransitionMatch,
    rutter_constructor: Callable[[TransitionContext], Rutter],
    charter_constructor: Callable[[TransitionContext], JsonObject | None],
)
```

`rutter_constructor(context) -> Rutter` shares the same contextual binding and
restart contract as SubRutter. `charter_constructor(context) -> JsonObject |
None` returns `None` to suppress the hook; a JSON object both selects the hook
and constructs its child Charter.

Initialization is not a transition. Preflight work is a visible initial
SubRutter. Terminal hooks match the visible Terminal evolution. The reusable
hook API is specified only in `03-hook-library.md`.

## Public operating interface

Each Voyage exposes its stateless definition through the read-only `rutter`
property. `help()` describes the three Compass-facing operations:

```python
voyage.get_status() -> VoyageStatus
voyage.validate(response, *, responding_to=None) -> ValidationReport
voyage.advance(
    response=MISSING,
    *,
    responding_to=None,
    continue_=True,
    dry_run=False,
) -> EvolutionView
```

- `get_status()` reads one atomic `VoyageStatus`. Its `current_evolution` is the
  active leaf; an active hook or explicit SubRutter child is therefore visible
  while its parent remains recursively persisted at its source evolution. Its
  `instruction` is the exact stored Message, an in-process MachineInstruction,
  or `None`. Its terminal-only value is `terminal_result`; its optional
  `FaultSummary` validates stable IDs and exposes evolution coordinates
  together, except that the `opaque` category exposes none.
- `validate()` is read-only. For an LLMStep, it checks the exact
  `responding_to` entrance and the complete flat response before contextual
  assessment. For a MachineStep, it validates a MachineResult and rejects a
  response-correlation token.
- `advance()` is the sole advancing operation. It revalidates under lock,
  records accepted work, selects the transition, runs selected hook children,
  and enters the destination. With `continue_=True`, it continues through
  automatic Python instructions and child Rutters and returns only the final
  entered evolution that cannot proceed automatically. The traversed path is
  available from durable history. With `continue_=False`, it advances from the
  active evolution until the first new evolution is entered or a stopping
  condition occurs. It returns a selected child start, or the parent-transition
  target when no child intervenes.
- `advance(..., dry_run=True)` is a read-only preview of the immediate
  parent-transition target. It validates the supplied result and may run pure validation and
  routing callbacks, but it does not record, enter an evolution, evaluate
  hooks, run MachineSteps, or start children. It raises `PreviewUnavailable`
  when the required MachineResult or child VoyageResult does not already exist
  or is not supplied. At Terminal it may run the pure result projection and
  preview terminal completion at the current Terminal evolution. Automatic
  continuation does not apply.

The two normal advancement forms are deliberately distinct:

```python
voyage.advance(
    response,
    responding_to=message.evolution_entry_id,
    continue_=True,
)
voyage.advance(continue_=True)
```

The first submits an LLM response. The second settles machine, child, terminal,
or continuation work without inventing a response token.

An invalid response leaves the current evolution unchanged. A fault or
uncertain effect is a condition anchored at an entered evolution, not another
lifecycle coordinate. Compass reads `get_status()`, performs only a Message,
and calls
`advance(response, responding_to=message.evolution_entry_id, continue_=True)`
for that LLM work or `advance(continue_=True)` for machine or continuation
work; it never manipulates nesting or diagnostic branches.

`EvolutionView` is an immutable identifier, not the definition evolution. It
contains Rutter ID, definition version, evolution ID, unique entrance ID,
nesting depth, and a condition such as `ready`, `terminal`, `fault`,
`uncertain`, or `preview`. It does not repeat the path or history. The entrance
ID is `None` only on a dry-run preview because that evolution has not actually
been entered.

The instruction values are:

```text
Instruction = Message | MachineInstruction

MachineInstruction
  machine_id: str
  mode: pure | repeat-safe | non-repeat-safe
  run: Callable[[], MachineResult]
  answer_format: fixed MachineResult format
```

For an effectful MachineStep, `run()` is a runtime-owned recovery wrapper rather
than the raw author callback: it uses the entrance's stable machine ID and
durably records the completed result before returning. Voyage invokes the same
wrapper during automatic continuation.

The argument protocol is deliberately small:

| Evolution/condition | `get_status().instruction` | `validate(x)` | `advance(x)` |
|---|---|---|---|
| LLMStep | stored `Message` | validates flat response with `responding_to` | requires accepted response with the same `responding_to` |
| MachineStep | `MachineInstruction` | validates `MachineResult` | omitted runs it; supplied result must match durable completed recovery when effectful |
| SubRutter | `None` | raises `NotApplicable` | no argument; attach or settle child |
| Terminal | `None` | raises `NotApplicable` | no argument; project/settle terminal result |
| terminal | `None` | raises `NotApplicable` | idempotently returns current EvolutionView |
| fault or uncertain | `None` | raises `RunBlocked` | raises `RunBlocked` |

`MISSING` distinguishes an omitted argument from a valid JSON `null`. LLMStep
responses are flat mappings, while MachineResults retain their exact declared
value. `validate()` raises stable `NotApplicable` rather than overloading
`ValidationReport` where no response is accepted. On entering an effectful
MachineStep, its durable recovery plan is created with the same evolution
entrance; therefore retrieving its `MachineInstruction` remains read-only.

## Limits

The initial design deliberately supports one active path and sequential hook
children. It does not provide:

- concurrent child execution;
- fault catching or parent recovery from child failure;
- child definitions outside the validated binding boundary;
- hidden routing from hooks;
- wall-clock or external-state scheduling;
- a workflow/combinator DSL; or
- public synthetic START or RETURN lifecycle coordinates.

Root counts as call depth one. Maximum depth is checked before allocating a
child run or invoking work, and serialized active depth is bounded before model
construction. All Charters, Messages, responses, results, and records are
finite JSON. Run, child, record, MachineStep, evolution, and transition-hook
identities are unique in their declared scopes.

Sequential or dependent attached work is one visible orchestrating child
Rutter.

## Prose-to-Rutter procedure

1. Identify visible control locations.
2. Make each LLM interaction an LLMStep.
3. Separate invariant instructions from contextual payload data.
4. Declare the complete flat response with `response_schema` where useful.
5. Put contextual acceptance rules in `assess_response`.
6. Put deterministic computation in pure MachineSteps and effects in explicit
   effectful MachineSteps.
7. Use SubRutter where a child result controls routing.
8. Use transition hooks only for work that must resume an already-selected
   transition.
9. Use Terminal with exactly one explicit `VoyageResult` mode.
10. Version every behavioral change.

## Non-goals

- reconstructing progress from conversation history;
- parsing prose into an evolution graph automatically;
- treating an evolution as a fixed outbound message;
- embedding run state in definition instances;
- serializing callbacks or source code;
- silently retrying non-repeat-safe effects;
- duplicating completion-result authority; and
- adding diagnostic behavior to the core reducer.
