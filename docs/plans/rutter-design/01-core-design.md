# Rutter core design

Status: accepted design basis for implementation planning; implementation has
not started.

## Reading map

1. This document explains what a Rutter is and how an author uses it.
2. `02-runtime-reference.md` specifies durable state and recovery.
3. `03-hook-library.md` specifies transition hooks, CaseMakers, and reusable
   diagnostic children.
4. `04-examples.md` shows complete authoring patterns.
5. `05-verification-and-implementation.md` records compatibility, tests, and
   implementation order.
6. `06-core-reimplementation-plan.md` maps the accepted design onto the live
   code: what is preserved, rewritten, replaced, and verified.

## Goal

A Rutter converts prose instructions into a machine-managed LLM interaction
whose exact progress can survive process and LLM restarts. It must support:

- state-specific instructions;
- data derived from the initial Charter and accepted history;
- contextual validation and routing;
- ordinary nested Rutters;
- attached work on accepted transitions; and
- one small public interface for Compass.

The design favors explicit, inspectable control flow over a workflow DSL.

## Mental model

A Rutter definition is a stateless, versioned program. A run supplies a finite
JSON Charter and produces a `RunResult`.

```text
Rutter definition
  identity + version
  start state
  state graph
  transition hooks

Rutter run
  Charter
  entered node
  accepted local history
  optional active child Rutter
```

The state selects stable instructions. The Charter and accepted history supply
changing data. Re-entering the same state may therefore produce a new Message
without changing its instructions: for example, a loop can supply the next
text chunk on each visit.

Each successful state completion selects one transition. Hooks may perform
attached work on that transition, but the selected continuation is frozen.
Result-directed child work is instead a visible `Call` state.

Node entrance is the only persisted control coordinate. Rendering or executing
an instruction, validation, evaluation, transition selection, CaseMaker
selection, and child return are operations from an entered node, not additional
lifecycle states. Requests, accepted responses, Action results, and child
completions are durable history facts. An active child applies this
same model recursively while its parent remains at its entered source node.

## Definition lifecycle

The runtime registry owns a registered no-argument Rutter class, definition
instance, or factory; a separate bound voyage owns the Reckoning.
Authors may use `__init__` only to freeze definition constants shared by every
run of that definition version. Callbacks may be bound methods on that stateless
instance or module functions; class-level state dictionaries containing unbound
descriptors are unsupported. Binding validates, before a run starts:

- nonempty stable `rutter_id` and positive `definition_version`;
- exact-Boolean `allow_multiple_cases_at_once`;
- one valid start state;
- unique state and CaseMaker IDs;
- all referenced local successor IDs;
- Prompt, Action, and Call outcomes and routes;
- CaseMaker identities and child definitions;
- callable signatures; and
- transitively referenced child identities and versions.

Definition instances are stateless and run-neutral. Per-run data belongs only
to the Charter, contexts, and durable Reckoning. Changing executable behavior,
instructions, routing, callbacks, hooks, or static case data requires a
definition-version change.

Ordinary state loops are allowed. Only cycles in the graph of Rutter
definitions calling other Rutter definitions are rejected initially.

## Author-facing primitives

```python
class Rutter:
    rutter_id: str
    definition_version: int
    start_state: str
    allow_multiple_cases_at_once: bool = False

    def define_states(self) -> Mapping[str, State]: ...
    def define_case_makers(self) -> tuple[CaseMaker, ...]: ...
```

`State` has four variants.

### Prompt

```python
Prompt(
    text: str,
    *,
    answer: AnswerSpec,
    data: Callable[[StateContext], JsonObject] = empty_data,
    validate: Callable[[AnswerContext], ValidationReport] = accept,
    then: str | Mapping[str, str] | Callable[[AnswerContext], str],
)
```

- `text` is intrinsic instruction prose.
- `data` derives the current payload from Charter and accepted history.
- `answer` declares allowed outcomes and optional format guidance.
- `validate` enforces contextual evidence rules without mutation.
- `then` selects a target from the accepted outcome.

`AnswerSpec` maps allowed outcomes to finite JSON format hints. `None` means no
guidance; `{}` literally recommends an empty evidence object; other values are
descriptive example shapes. It is not a second schema language. There are no
defaults or coercions. The engine validates response envelope, revision, finite
JSON, and declared outcome before calling the contextual validator.

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

### Action

```python
Action(
    run: Callable[[ActionContext], ActionResult],
    *,
    mode: "pure" | "repeat-safe" | "non-repeat-safe",
    then: str | Mapping[str, str] |
          Callable[[ActionContext, ActionResult], str],
)
```

- `pure` is deterministic and side-effect free.
- `repeat-safe` may be retried using application idempotency.
- `non-repeat-safe` uses the runtime recovery protocol and may become uncertain.

```text
ActionResult
  outcome: str
  value: JsonValue
```

### Call

```python
Call(
    child: type[Rutter],
    *,
    charter: Callable[[StateContext], JsonObject],
    then: str | Mapping[str, str] |
          Callable[[StateContext, RunResult], str],
)
```

Advancing from an entered Call creates one child; conditional calls route to or
around the Call state. A mapping routes from `RunResult.outcome`; a callable
may read the whole result but cannot remove the outcome from the parent edge.
Children may themselves contain Prompts, Actions, Calls, and hooks.

### Done

```python
Done(
    result: RunResult | Callable[[StateContext], RunResult],
)
```

Every root and child completes with the same envelope:

```text
RunResult
  outcome: str
  value: JsonValue
```

## Messages and responses

Every public Message has exactly two top-level parts:

```json
{
  "instructions": {
    "text": "State-specific invariant instructions",
    "answer": {}
  },
  "data": {
    "state": {"id": "report", "entry_id": "entry-...", "revision": 7},
    "payload": {"chunk": "..."}
  }
}
```

The engine owns `data.state`; a Prompt callback supplies only `data.payload`.
The response is:

```json
{
  "revision": 7,
  "outcome": "reported",
  "evidence": {}
}
```

One global revision spans the active root-to-leaf path. The exact delivered
Message is stored as an open Turn atomically with Prompt entrance; the accepted
Response fills that Turn. `get_instruction()` only reads the stored Message, so
it never rerenders or mutates the run.

## Contexts and purity

Callbacks receive immutable views:

```text
StateContext
  charter: JsonObject
  state_id: str
  node_entry_id: str
  history: HistoryView

AnswerContext
  state: StateContext
  message: Message
  response: Response

ActionContext
  state: StateContext
  action_id: str

EdgeContext
  state: StateContext
  edge: Edge
  record: Turn | ActionRecord | CallRecord | DoneRecord
```

`EdgeContext.state.history` is the immutable prefix strictly before its source
`record`, which appears exactly once through `record`. Later CallRecords
attached to that edge are excluded from callback-visible history and used only
by the engine to skip stable completed maker/edge identities. This keeps `then`
and every CaseMaker on one edge anchored to the same context across recovery.
A child sees only its own Charter and local history; required parent data must
be copied deliberately into the child Charter.

Prompt data, contextual validation, successor selection, Call Charter
construction, Done projection, edge matching, CaseMaker Charter selection,
diagnostic evaluation, and pure Actions depend only on immutable arguments and
definition constants. They do not read clocks, randomness, environment
variables, networks, mutable files, or databases.

External inputs enter through the initial Charter or an explicit effectful
Action whose frozen ActionResult becomes history. Only Actions may perform
external work.

The complete persisted record schemas are in `02-runtime-reference.md`; the
context schemas are above.

## Transitions, calls, and hooks

Every successful state completion stages one real edge:

```text
source state + accepted outcome -> selected target or completion
```

Before entering that already-selected target, the runtime consults transition
hooks. Selected hook children are pooled in definition order and run
sequentially before the frozen edge resumes. If more than one is selected while
`allow_multiple_cases_at_once` is false, the Rutter faults before starting any
child. Hook work cannot redirect, replace, or cancel the transition.

This is the central distinction:

```text
Call state       child result may select the next parent state
transition hook  child finishes, then the frozen parent edge resumes
```

Initialization is not an edge. Preflight work is a visible initial Call.
Terminal hooks match the visible Done state. The hook and CaseMaker API is
specified only in `03-hook-library.md`.

## Public operating interface

The Rutter class exposes four operations:

```python
rutter.get_instruction() -> Instruction | None
rutter.validate(response) -> ValidationReport
rutter.next(response=MISSING, *, continue_=True, dry_run=False) -> NodeView
rutter.get_current_node() -> NodeView
```

- `get_instruction()` is read-only and returns the active leaf node's exact LLM
  Message or in-process `PythonInstruction`. During automatic continuation,
  Rutter executes Python work internally; Compass receives only LLM Messages
  and stopping conditions. `get_instruction()` returns `None` for internal
  Call/Done work and at a terminal or blocked node.
- `validate()` is read-only and checks a proposed result against that
  instruction's format and contextual validator.
- `get_current_node()` returns an immutable view of the active leaf node. An
  active hook or explicit Call child is therefore visible as the current node;
  its parent remains recursively persisted at its source node.
- `next()` is the sole advancing operation. It revalidates under lock, records
  accepted work, selects the edge, runs selected hook children, and enters the
  destination. With `continue_=True`, it continues through automatic Python
  instructions and child Rutters and returns only the final entered node that
  cannot proceed automatically. The traversed path is available from durable
  history. With `continue_=False`, it advances from the active node until the
  first new node is entered or a stopping condition occurs. It returns a
  selected child start, or the parent-edge target when no child intervenes.
- `next(..., dry_run=True)` is a read-only preview of the immediate parent-edge
  target. It validates the supplied result and may run pure validation and
  routing callbacks, but it does not record, enter a node, evaluate CaseMakers,
  run Actions, or start children. It raises `PreviewUnavailable` when the
  required ActionResult or child RunResult does not already exist or is not
  supplied. At Done it may run the pure result projection and preview terminal
  completion at the current Done node. Automatic continuation does not apply.

An invalid response leaves the current node unchanged. A fault or uncertain
effect is a condition anchored at an entered node, not another lifecycle
coordinate. Compass requests the current instruction and calls
`next(..., continue_=True)`; it never manipulates nesting or diagnostic branches.

`NodeView` is an immutable identifier, not the mutable definition node. It
contains Rutter ID, definition version, node ID, unique entrance ID, nesting
depth, and a condition such as `ready`, `terminal`, `fault`, `uncertain`, or
`preview`. It does not repeat the path or history.
The entrance ID is `None` only on a dry-run preview because that node has not
actually been entered.

The instruction values are:

```text
Instruction = Message | PythonInstruction

PythonInstruction
  action_id: str
  mode: pure | repeat-safe | non-repeat-safe
  run: Callable[[], ActionResult]
  answer_format: fixed ActionResult format
```

For an effectful Action, `run()` is a runtime-owned recovery wrapper rather than
the raw author callback: it uses the entrance's stable action ID and durably
records the completed result before returning. Rutter invokes the same wrapper
during automatic continuation.

The argument protocol is deliberately small:

| Node/condition | `get_instruction()` | `validate(x)` | `next(x)` |
|---|---|---|---|
| Prompt | stored `Message` | validates `Response` | requires accepted `Response` |
| Action | `PythonInstruction` | validates `ActionResult` | omitted runs it; supplied result must match durable completed recovery when effectful |
| Call | `None` | raises `NotApplicable` | no argument; attach or settle child |
| Done | `None` | raises `NotApplicable` | no argument; project/settle terminal result |
| terminal | `None` | raises `NotApplicable` | idempotently returns current NodeView |
| fault or uncertain | `None` | raises `RunBlocked` | raises `RunBlocked` |

`MISSING` distinguishes an omitted argument from a valid JSON `null`. Prompt
responses and Action results use their exact declared envelopes. `validate()`
raises stable `NotApplicable` rather than overloading `ValidationReport` where
no answer is accepted. On entering an effectful Action, its durable recovery
plan is created with the same node entrance; therefore retrieving its
`PythonInstruction` remains read-only.

## Limits

The initial design deliberately supports one active path and sequential hook
children. It does not provide:

- concurrent child execution;
- fault catching or parent recovery from child failure;
- dynamic or unregistered child definitions;
- hidden routing from hooks;
- wall-clock or external-state scheduling;
- a workflow/combinator DSL; or
- public synthetic START or RETURN lifecycle coordinates.

Root counts as call depth one. Maximum depth is checked before allocating a
child run or invoking work, and serialized active depth is bounded before model
construction. All Charters, Messages, Responses, results, and records are
finite JSON. Run, call, record, Action, state, and CaseMaker identities are
unique in their declared scopes.

Sequential or dependent attached work is one visible orchestrating child
Rutter.

## Prose-to-Rutter procedure

1. Identify visible control locations.
2. Make each LLM interaction a Prompt.
3. Separate invariant instructions from contextual payload data.
4. Declare outcomes and format guidance.
5. Put exact acceptance rules in Prompt validation.
6. Put deterministic computation in pure Actions and effects in explicit
   effectful Actions.
7. Use Call where a child result controls routing.
8. Use transition hooks only for work that must resume an already-selected
   edge.
9. Use Done for one explicit `RunResult`.
10. Version every behavioral change.

## Non-goals

- reconstructing progress from conversation history;
- parsing prose into a state graph automatically;
- treating a state as a fixed outbound message;
- embedding run state in definition instances;
- serializing callbacks or source code;
- silently retrying non-repeat-safe effects;
- duplicating completion-result authority; and
- adding diagnostic behavior to the core reducer.
