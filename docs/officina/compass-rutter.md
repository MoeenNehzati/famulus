# Compass and Rutter

Compass and Rutter divide durable algorithm execution into two parts:

- A **Rutter** is the explicit, durable algorithm. It owns the state graph,
  instructions, validation, transitions, and recovery policy.
- **Compass** is the generic LLM-facing operating protocol. It follows the
  current public instruction of one already-bound Rutter and returns finite
  evidence through that Rutter's public operations.

The Rutter is authoritative. Compass does not reconstruct progress from a
conversation, inspect implementation code to choose a route, or own a second
workflow state.

## Vocabulary

**Charter**
: Immutable initial data for one undertaking. It identifies the Rutter and its
  definition version and contains the domain inputs, paths, scope, and options.

**Fix**
: The current machine coordinate: state ID, revision, lifecycle, and the
  minimum framework-owned effect-recovery or fault diagnostics.

**Reckoning**
: The durable authority for one undertaking: `Charter + Fix`. A Reckoning, not
  process or conversation memory, determines where the algorithm resumes.

**Voyage**
: The lifecycle from creation of the initial Fix until a terminal state. A
  fault or uncertain effect may halt it pending intervention. Voyage is
  vocabulary, not another persisted record or public API layer.

**State**
: One nonterminal graph entry containing an instruction, input validator,
  direct successor function, optional description, and optional callable-only
  effect policy.

**TerminalState**
: A terminal graph entry. It carries readable description only and cannot be
  partially configured as a nonterminal state.

**Rutter**
: One direct `BaseRutter` subclass. The class defines the immutable algorithm;
  each bound instance represents one voyage backed by one Reckoning file.

**Compass**
: The generic protocol in [`using-compass`](../../skills/using-compass/). It
  operates a bound Rutter through public values and methods; it is not a
  registry, storage layer, state record, or domain-specific algorithm.

## Ownership boundary

A named Rutter owns only:

- `rutter_id`, `definition_version`, and `start_state`;
- `define_states()` and its explicit state mapping;
- its string and callable instructions;
- its validators and direct successor functions; and
- domain helpers used by those functions.

`BaseRutter` owns construction, binding, validation of the definition,
persistence, locking, effect recovery, and all public engine operations. A
named Rutter must directly and exclusively subclass `BaseRutter`. During class
creation, `BaseRutter.__init_subclass__` examines the method-resolution order
and raises `RutterDefinitionError` if the subclass overrides engine-owned
members such as `advance`, `validate`, `get_instruction`, construction, or
binding. `define_states()` is the intended extension point.

Rutter code does not create or manage subagents. Compass may do so only when
the current string instruction explicitly authorizes it.

## Definition and binding

The complete graph is a direct mapping returned by `define_states()`:

```python
class ExampleRutter(BaseRutter):
    rutter_id = "example"
    definition_version = 1
    start_state = "review"

    def define_states(self):
        return {
            "review": State(
                instruction=self.REVIEW_INSTRUCTION,
                input_validator=InputValidatorContract(
                    self.validate_review,
                    ("accepted", "stopped"),
                ),
                next_state=self.next_after_review,
            ),
            "complete": TerminalState("Review complete."),
            "stopped": TerminalState("Review stopped safely."),
        }
```

The engine calls `define_states()` once while binding and freezes the returned
mapping. State IDs and the start state are checked immediately; successor IDs
are checked against the same frozen mapping when a transition is selected.
Instructions and transition functions are not executed during definition
validation.

There are two construction routes:

```python
rutter = ExampleRutter.create(Path("paper.reckoning.json"), charter)
rutter = ExampleRutter.open(Path("paper.reckoning.json"))
```

`create` validates the Charter and initial Fix, atomically creates the complete
Reckoning, and fails if that authority file already exists. `open` strictly
loads the complete persisted Reckoning without advancing it or reconstructing
missing data. Rutter identity and definition version must match the bound
class.

`RutterRegistry` is the optional name-to-definition boundary. It accepts an
explicit mapping of names to direct Rutter subclasses, confines relative
Reckoning paths beneath one root, and returns an already-bound instance.
Compass receives that instance; it does not resolve the registry or path.

## Public operations

After binding, the public operational surface is:

```python
rutter.get_instruction()
rutter.validate(result)
rutter.advance(result=None, continue_=True, dry_run=False)
```

`get_instruction()` returns a source-free string instruction or a structured
status for callable, effectful, pending, uncertain, terminal, or faulted
authority. A string instruction includes the immutable Charter data, current
state and revision, allowed outcomes, and exact result format.

String and callable instructions use the same finite-JSON envelope:

```json
{"revision": 7, "outcome": "accepted", "evidence": {}}
```

An LLM supplies the displayed revision because its work crosses an
asynchronous boundary. For a callable instruction, the callable returns only
`outcome` and `evidence`; the engine attaches the authoritative revision.

`validate(result)` is observational. It checks the exact envelope, finite JSON,
revision, declared outcome contract, and current state's validator. It neither
runs the successor function nor writes authority.

`advance(result, continue_=False)` consumes one supplied result and crosses at
most one edge. `advance(result, continue_=True)` consumes that result and then
settles consecutive callable states until it reaches a string instruction,
terminal state, fault, uncertainty, or the bounded settling limit.

`advance(result, continue_=False, dry_run=True)` validates and previews exactly
one immediate successor. It does not write, invoke an instruction, or grant
permission to perform the previewed work. Dry run is incompatible with
`continue_=True`.

The reduction is intentionally small:

```text
current instruction -> finite JSON -> validate -> select successor -> persist Fix
```

## Compass operating loop

Compass operates only an invoker-provided bound Rutter:

1. Call `advance(continue_=True)` to settle callable work and effect recovery.
   An `input_required` validation issue means the current string state is ready
   for step 3.
2. Classify the returned successor and public `fix`. Stop at complete, faulted,
   or uncertain authority.
3. At an active string state, call `get_instruction()` and perform exactly that
   instruction.
4. Return its displayed revision, one declared outcome (or `unexpected`), and a
   finite JSON evidence object.
5. Call `validate(result)`. Repair only from its public validation issues.
6. Call `advance(result, continue_=True)` and repeat.

If no declared outcome fits, Compass returns `unexpected` with nonempty
`observed`, `conflict`, `why_no_outcome_fits`, and `uncertainty` evidence. It
does not inspect Rutter source to invent a transition.

The executable operating rules, including stopped-authority classification,
are maintained in [`using-compass`](../../skills/using-compass/).

## Persistence and concurrency

Every authority filename ends with `.reckoning.json`. The cooperative lock is
the complete filename with `.lock` appended:

```text
paper.reckoning.json
paper.reckoning.json.lock
```

The lock file cannot itself be accepted as a Reckoning. Creation atomically
claims a new Reckoning path. Reads and reductions of existing authority are
serialized by the per-Reckoning sidecar lock; replacements compare the exact
predecessor before publishing canonical JSON atomically.

Persistence rejects missing or unknown fields, duplicate JSON keys,
non-finite values, invalid identity or versions, non-regular authority files,
and paths outside the configured root. The Charter is immutable. Domain
artifacts live at explicit Charter-owned paths rather than in generic mutable
`memory`, `context`, or `state_data` fields.

## Effects and recovery

A callable that may change a file, process, database, service, or other
external system declares `EffectPolicy`. Writing a Rutter-owned artifact is
also an external effect.

Before invocation, the engine persists `planned` effect authority. After a
validated return and transition, it persists `completed` authority with the
successor Fix. If execution is interrupted, repeat-safe work may be retried;
non-repeat-safe work becomes `uncertain` and requires manual reconciliation.

Atomic Reckoning replacement cannot roll back external effects, direct edits,
or LLM work already performed in response to a string instruction. A caller
that receives a persistence error reopens the Reckoning before deciding
whether any retry is safe.

## Module map

- `officina.rutter.model` — immutable Charter, Fix, Reckoning, State, terminal,
  validation, and effect-policy values.
- `officina.rutter.engine` — direct graph binding and the three public
  operations.
- `officina.rutter.storage` — strict canonical JSON, confinement, locks, and
  atomic authority replacement.
- `officina.rutter.runtime` — explicit registry creation and opening.
- [`using-compass`](../../skills/using-compass/) — generic LLM-facing operating
  protocol.
