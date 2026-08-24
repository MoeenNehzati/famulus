# Transparent Rutter Instance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Rutter a stateless, directly constructed workflow-definition
object whose complete control flow, LLM instructions, handler references,
routing, and hooks can be read in one declaration without understanding Python
subclass mechanics.

**Architecture:** `Rutter(...)` constructs one stateless definition instance;
`Voyage` owns one execution and exposes its Rutter through a read-only property.
The existing binder remains the validation and graph-freezing boundary.
`SubRutter` and transition hooks each construct their contextual child through
one explicit `rutter_constructor`, using the same validated runtime resolution
boundary. No generated class, authoring DSL, parser, or second execution path
is introduced.

**Tech Stack:** Python standard library, `jsonschema` 4.x, existing Officina
Rutter primitives, finite immutable JSON values, `pytest`, repository blueprint
validators.

**Spec:** This plan narrowly amends the subclass-based definition lifecycle in
`docs/plans/rutter-design/01-core-design.md`. The older examples in
`docs/plans/rutter-design/04-examples.md` use pre-cutover terminology and are
not an implementation source for this change. All current Voyage, Reckoning,
transition, hook, recovery, and Compass semantics remain normative.

## Global Constraints

- A `Rutter` instance is a stateless workflow definition whose authored
  collections are snapshotted at construction; a `Voyage` owns all execution
  state, storage, and durable progress.
- The entire authored graph remains visible in one `Rutter(...)` declaration:
  identity, version, start evolution, evolutions, routing, and hooks.
- Python handlers may be defined beside the declaration or imported normally;
  the declaration references the callable objects directly.
- A transition hook declares when it runs with `after(...)`, `before(...)`, or
  `on_transition(...)`, and declares what it runs with
  `rutter_constructor(context) -> Rutter`. Its `charter_constructor` callback
  remains the selection and child-Charter boundary.
- A `SubRutter` evolution declares its child with
  `rutter_constructor(context) -> Rutter`; its `charter_constructor` callback
  constructs the child Voyage's Charter.
- Static routing uses `next_on_outcome` with the existing string-or-outcome-map
  semantics; callback routing uses `choose_next`. Remove `then` without adding
  aliases, deprecation machinery, or new routing behavior.
- Each `LLMStep` uses `response_schema` for context-free JSON Schema validation
  of the complete flat LLM response, and `assess_response` for contextual
  acceptance after schema validation. Remove `AnswerSpec`, the public
  `Response` wrapper, and the nested `evidence` field.
- `Voyage.advance(..., responding_to=evolution_entry_id)` replaces
  `Voyage.next(...)`. The response contains no revision; the exact evolution
  entrance identifies the prompt being answered. Keep `Reckoning.global_revision`
  and the v3 wire revision internal only where required for storage compatibility.
- Author contexts receive the existing typed `Transition`; terminal values and
  terminal constructors use distinct keyword names; `Message` exposes its
  meaningful envelope fields directly; `VoyageStatus.terminal_result` names
  its terminal-only value; and `FaultSummary` validates its IDs.
- Do not introduce `DeclaredRutter`, dynamic/generated classes, decorators,
  import strings, `importlib`, YAML, a parser, or another workflow language.
- Preserve existing Rutter subclasses as a compatibility authoring path during
  this change; do not migrate unrelated test-only or diagnostic subclasses.
- Preserve the existing binder as the single definition-validation boundary.
- Preserve storage version 3, Reckoning JSON, recovery, Compass, and engine
  transition semantics. Preserve canonical bytes for losslessly representable
  v3 history values; reject legacy response evidence containing reserved
  `outcome` or `revision` keys, and do not claim semantic reopening across
  changed Rutter definition versions.
- Do not modify or stage unrelated dirty files.
- Do not commit, amend, stage, or push unless the user explicitly authorizes
  that Git operation.

---

## Success example

The supplied inventory Rutter must end in this author-facing shape, with its
existing handlers retained as ordinary functions:

```python
_DIAGNOSIS_RUTTER = DiagnoseAnswer()


def _diagnosis_rutter(context: TransitionContext) -> Rutter:
    del context
    return _DIAGNOSIS_RUTTER


INQUISITIVE_INVENTORY = Rutter(
    id=_RUTTER_ID,
    version=5,
    start=_REPORT_EVOLUTION,
    evolutions={
        _REPORT_EVOLUTION: LLMStep(
            "Read the displayed source text, update the prior inventory using "
            "the normal inventory rules, and return the complete cumulative "
            "inventory snapshot for this interaction.",
            response_schema={
                "type": "object",
                "properties": {
                    "outcome": {"const": "reported"},
                    "sequence_id": {"type": "integer", "minimum": 1},
                    "inventory": {"type": "object"},
                },
                "required": ["outcome", "sequence_id", "inventory"],
                "additionalProperties": False,
            },
            data=_report_data,
            assess_response=_assess_report,
            next_on_outcome=_RECORD_EVOLUTION,
        ),
        _RECORD_EVOLUTION: MachineStep(
            _record_iteration,
            mode="repeat-safe",
            next_on_outcome={"more": _REPORT_EVOLUTION, "done": "complete"},
        ),
        "complete": Terminal(result_constructor=_complete_result),
    },
    hooks=(
        hook_sequence_after(
            id=_TRANSITION_HOOK_ID,
            after_evolutions={_REPORT_EVOLUTION},
            items=_INTERACTION_SLOTS,
            rutter_constructor=_diagnosis_rutter,
            charter_constructor=_diagnosis_charter,
        ),
    ),
)
```

The corresponding LLM-response operation is flat and identifies the exact
Message entrance outside the response itself:

```python
message = voyage.get_status().instruction
assert isinstance(message, Message)
response = {
    "outcome": "reported",
    "sequence_id": 1,
    "inventory": {},
}
report = voyage.validate(
    response,
    responding_to=message.evolution_entry_id,
)
if report.valid:
    voyage.advance(
        response,
        responding_to=message.evolution_entry_id,
    )
```

This change does not attempt to shorten the handlers themselves. It makes the
workflow construction independently visible and permits those handlers to move
to a focused sibling module later without changing the Rutter API.

## File responsibility map

| File | Minimal responsibility in this change |
|---|---|
| `pyproject.toml` | Declare the existing repository-standard `jsonschema` 4.x package as a runtime dependency. |
| `src/officina/rutter/values.py` | Remove `AnswerSpec` and `Response`, expose meaningful `Message` fields directly, rename the terminal status result, and complete `FaultSummary` invariants. |
| `src/officina/rutter/authoring.py` | Rename authored routing and Charter-constructor fields, expose `response_schema` and `assess_response`, flatten response contexts, make transition and terminal contracts explicit, make `Rutter` directly constructible, and replace fixed SubRutter and hook children with `rutter_constructor`. |
| `src/officina/rutter/history.py` | Keep revision internal and project public Messages and flat responses through the unchanged storage-v3 wire envelope. |
| `src/officina/rutter/runtime.py` | Rename binder routing access and provide one binder validation service for contextual SubRutter and hook results. |
| `src/officina/rutter/evaluation.py` | Validate response bodies against JSON Schema before calling contextual assessment. |
| `src/officina/rutter/engine.py` | Materialize response schemas, correlate flat responses by evolution entrance, expose `advance`, enforce validation order, construct and bind contextual children, and expose `Voyage.rutter`. |
| `src/officina/rutter/diagnostic.py` | Pass `rutter_constructor` through the diagnostic hook helpers without adding another hook abstraction. |
| `src/officina/visualization/from_rutter/__init__.py` and `payload_builder.py` | Accept a Rutter instance directly; retain class compatibility. |
| `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py` | Replace only the `InquisitiveInventoryRutter` subclass declaration and its registry reference with the concrete instance. |
| `skills/math-dependency-graph/_rtx/_inquisitive_inventory_cli.py` | Rename its Rutter operation to `advance` and project the revised public values. |
| `skills/math-dependency-graph/_rtx/_transparent_rutter_prototype.py` | Delete after the real constructor path passes the same acceptance behavior. |
| `src/officina/rutter/tests/test_rutter_model.py`, `src/officina/rutter/tests/test_rutter_runtime.py`, `src/officina/rutter/tests/test_rutter_visualization.py` | Specify construction, composition, Voyage ownership, compatibility, and visualization. |
| `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py` | Prove the converted declaration retains the full inventory behavior. |
| `docs/plans/rutter-design/01-core-design.md` and the affected Rutter source blueprints | Align only the changed authoring and public-value contracts after behavior is green. |

---

## Independent audit record

Two read-only subagents audited this plan independently: one against the
transparency goal and one against minimal safe change. Their first passes found
instance-child visualization gaps, ambiguous constructor modes, insufficient
active-hook restart coverage, wrong-checkout blueprint commands, unnecessary
`runtime.py` edits, overbroad documentation/blueprint scope, duplicated
inventory identifiers, and a keyword-compatibility break. Those findings are
addressed in the tasks below.

The transparency audit also identified a pre-existing limitation:
`hook_sequence_after` stores its multi-source filter inside its Charter callback,
so visualization cannot infer that filter from `TransitionMatch`. The
minimality audit correctly classified a new source-set matcher API as a
separate public-interface change. This plan therefore keeps the hook filter
explicit in the authored `hook_sequence_after(after_evolutions=...)`
declaration but does not redesign matcher or visualization semantics.

After revision, both subagents performed closure audits and reported no
remaining blockers: the transparency goal is met by the single visible
declaration, and the adjustment set is the minimal safe transition under the
stated compatibility constraints.

The later user-approved routing vocabulary change is isolated as Task 1. It is
a mechanical rename and split of the existing `then` union, not a routing
redesign.

The later user-approved LLM response contract is isolated as Task 2:
`response_schema` performs context-free JSON Schema validation and
`assess_response` performs contextual acceptance. The obsolete `AnswerSpec`
wrapper is removed rather than retained as a parallel authoring path.

The subsequent public-value audit found no second object as superficial as
`AnswerSpec`, but found several weaker boundaries. Tasks 2 and 3 keep the
meaningful objects and repair only those boundaries: flatten the response,
correlate it by evolution entrance, rename advancement, pass the existing typed
`Transition` through `TransitionContext`, distinguish terminal value and
constructor modes, name Charter constructors as constructors, expose direct
read-only `Message` properties, name the terminal-only status result, and
enforce the declared `FaultSummary` invariants.

Two new independent audits then reviewed those later routing, response, and
public-value additions. They confirmed that the direction simplifies the
author declaration, but found an inaccurate visualization label, an
underspecified two-mode routing constructor, unmapped-outcome regression,
ambiguous machine validation signature, incomplete fault-summary invariants,
stale canonical-document boundaries, and unconditional v3 compatibility
claims. A focused codec adjudication resolved the hard conflict in favor of
the user's flat response: preserve storage v3 for losslessly representable
values, reject reserved legacy evidence collisions deterministically, add a
Turn-aware Message adapter, and bump definitions whose LLM authority changes.
Tasks 1-3 and 8 below incorporate those findings.

Two follow-up subagent audits reviewed the contextual-hook task. The
transparency audit required the dynamic boundary to remain explicit in the
declaration and visualization; the minimality/runtime audit required atomic
identity merging, active-cycle rejection, replay-safe reopen behavior,
transactional binder state, and omitted diagnostic callers. After those
revisions, both closure audits reported no remaining blockers.

---

### Task 1: Rename authored routing fields

**Files:**
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/runtime.py`
- Modify: `src/officina/rutter/evaluation.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/diagnostic.py`
- Modify: `src/officina/visualization/from_rutter/payload_builder.py`
- Modify: `src/officina/rutter/tests/fixtures.py`
- Modify: `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py`
- Modify: `src/officina/rutter/tests/test_rutter_model.py`
- Modify: `src/officina/rutter/tests/test_rutter_runtime.py`
- Modify: `src/officina/rutter/tests/test_rutter_evaluation.py`
- Modify: `src/officina/rutter/tests/test_rutter_engine.py`
- Modify: `src/officina/rutter/tests/test_rutter_hooks.py`
- Modify: `src/officina/rutter/tests/test_rutter_lifecycle.py`
- Modify: `src/officina/rutter/tests/test_rutter_diagnostic.py`
- Modify: `src/officina/rutter/tests/test_rutter_visualization.py`

- [ ] Give `LLMStep`, `MachineStep`, and `SubRutter` separate keyword-only
  routing modes:

  ```python
  next_on_outcome: str | Mapping[str, str] | None = None
  choose_next: Callable[..., str] | None = None
  ```

  Require exactly one mode, snapshot only the static string-or-map mode, and
  validate the callback with the evolution kind's existing routing arity.
- [ ] Rename static string-or-map routing from `then` to `next_on_outcome` and
  callable routing from `then` to `choose_next`. Internally normalizing the two
  modes to one private routing union is permitted when it keeps runtime changes
  mechanical.
- [ ] Add failing constructor tests for static and callback routing and for the
  invalid both/neither cases on all three evolution types, then update direct
  attribute accesses and callers without aliases or routing behavior changes.
- [ ] Run the existing Rutter and inventory-Rutter tests.
- [ ] Search the owned paths for authored `then=`, `.then`, or `"then"`
  routing fields. Every remaining match must be unrelated prose rather than a
  Rutter routing contract.
- [ ] Review and commit the exact owned rename checkpoint:

  ```bash
  git add src/officina/rutter/authoring.py src/officina/rutter/runtime.py \
    src/officina/rutter/evaluation.py src/officina/rutter/engine.py \
    src/officina/rutter/diagnostic.py \
    src/officina/visualization/from_rutter/payload_builder.py \
    src/officina/rutter/tests/fixtures.py \
    skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py \
    src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_runtime.py \
    src/officina/rutter/tests/test_rutter_evaluation.py src/officina/rutter/tests/test_rutter_engine.py \
    src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_lifecycle.py \
    src/officina/rutter/tests/test_rutter_diagnostic.py src/officina/rutter/tests/test_rutter_visualization.py
  git commit -m "refactor(rutter): clarify routing fields"
  ```

### Task 2: Make response submission flat, schema-backed, and explicitly advancing

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/officina/rutter/__init__.py`
- Modify: `src/officina/rutter/model.py`
- Modify: `src/officina/rutter/values.py`
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/runtime.py`
- Modify: `src/officina/rutter/history.py`
- Modify: `src/officina/rutter/evaluation.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/diagnostic.py`
- Modify: `src/officina/visualization/from_rutter/payload_builder.py`
- Modify: `src/officina/rutter/tests/fixtures.py`
- Modify: `src/officina/rutter/tests/test_rutter_model.py`
- Modify: `src/officina/rutter/tests/test_rutter_runtime.py`
- Modify: `src/officina/rutter/tests/test_rutter_evaluation.py`
- Modify: `src/officina/rutter/tests/test_rutter_engine.py`
- Modify: `src/officina/rutter/tests/test_rutter_hooks.py`
- Modify: `src/officina/rutter/tests/test_rutter_lifecycle.py`
- Modify: `src/officina/rutter/tests/test_rutter_diagnostic.py`
- Modify: `src/officina/rutter/tests/test_rutter_storage.py`
- Modify: `src/officina/rutter/tests/test_rutter_visualization.py`
- Modify: `skills/math-dependency-graph/_rtx/_inquisitive_inventory_cli.py`
- Modify: `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py`

**Interfaces:**
- Consumes: Task 1 routing names, finite immutable `JsonObject` values, the
  existing `LLMResponseContext` and `ValidationReport`, and each open Turn's
  unique `evolution_entry_id`.
- Produces: `LLMStep(..., response_schema: JsonObject | None = None,
  assess_response: Callable[[LLMResponseContext], ValidationReport] =
  _accept_response, ...)` using self-contained JSON Schema Draft 2020-12
  documents; `LLMResponseContext.response: JsonObject`;
  `Voyage.validate(value, *, responding_to: str | None = None)`; and
  `Voyage.advance(value=MISSING, *, responding_to: str | None = None,
  continue_=True, dry_run=False)`.

- [ ] **Step 1: Write failing schema and assessment tests**

Add exact cases proving:

- `response_schema=None` omits the schema from the prompt and skips schema
  validation;
- `response_schema={}` is preserved in the prompt and accepts every
  finite-JSON object with the required stable `outcome` field;
- a schema with `additionalProperties: false` validates the complete flat
  LLM-authored response such as `{outcome, sequence_id, inventory}`;
- neither the Message nor response exposes `revision`, and a response
  containing a `revision` key is rejected as reserved engine metadata;
- `responding_to` must equal the current open Turn's `evolution_entry_id` for
  both validation and advancement, so a response to an earlier entrance is
  rejected even when the same LLMStep is entered again;
- a schema-invalid response returns deterministic `ValidationIssue` paths and
  never calls `assess_response`;
- a schema-valid response calls `assess_response` exactly once;
- an assessment rejection retains its authored issues and does not route;
- a malformed schema and any non-fragment `$ref` fail during definition
  binding;
- `next_on_outcome` mapping keys, rather than schema introspection, define the
  statically handled outcomes;
- `Voyage.next` is absent, `Voyage.advance` is Compass-facing, and the
  inventory CLI exposes `advance` rather than `next`;
- mapping-based `next_on_outcome` rejects an unmapped response outcome as
  ordinary invalid input before assessment and without persisting a fault;
- storage v3 canonically round-trips legacy response envelopes whose evidence
  has no reserved-key collision, and rejects legacy evidence containing either
  `outcome` or `revision` with one stable `RutterStateError` before callbacks or
  mutation;
- the Message-v3 adapter injects and strips the enclosing Turn revision,
  rejects duplicated-coordinate mismatches, and round-trips `response_schema`
  through wire `instructions.answer` as `None`/`{}`/nonempty schema; and
- machine-result validation accepts omitted `responding_to` and rejects a
  supplied token.

- [ ] **Step 2: Run the focused tests and verify the new fields fail**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_engine.py
```

Expected: `response_schema`, `assess_response`, `responding_to`, and `advance`
are unsupported because the current model requires `AnswerSpec`, `validate`,
the nested response envelope, and `Voyage.next`.

- [ ] **Step 3: Replace the authoring fields and remove AnswerSpec**

In `LLMStep`, replace `answer` with a snapshotted
`response_schema: JsonObject | None` and replace `validate` with
`assess_response`. Remove `AnswerSpec` and `Response` from `values.py`, public
exports, model re-exports, direct callers, and tests without compatibility
aliases. Change `LLMResponseContext.response` and `Turn.response` to frozen
flat `JsonObject` values. `Turn.response` receives a dedicated v3 projection in
`history.py`; do not introduce a private replacement response record.

Keep the existing default acceptance behavior in a private
`_accept_response`; remove public `accept`. `None` means no schema is included
or applied; `{}` remains a real empty schema and must not be normalized to
`None`.

- [ ] **Step 4: Validate schemas at the binder boundary**

Add `jsonschema>=4,<5` to `pyproject.toml`. At binding, use
`jsonschema.Draft202012Validator.check_schema` and prepare the validator for
each non-`None` schema. Require self-contained schemas: fragment-only `$ref`
values are allowed, while filesystem, network, and other external references
are definition errors. Validate `assess_response` as an inspectable
one-argument callback.

Do not infer possible outcomes from arbitrary JSON Schema. For mapping-based
`next_on_outcome`, retain nonempty route and declared-target validation, treat
the mapping keys as the accepted outcomes, and reject an absent outcome before
`assess_response` as ordinary response invalidity. String routing and
`choose_next` accept any formatting-valid, schema-valid, contextually accepted
stable outcome.

Convert schema failures to stable repository-owned issue codes and messages;
do not expose version-sensitive `jsonschema` exception prose.

- [ ] **Step 5: Flatten responses and correlate them by evolution entrance**

Render each non-`None` schema into the prompt as
`instructions["response_schema"]`. Expose `evolution_entry_id` on the Message,
but do not expose the Reckoning revision. Rename `Voyage.next` to
`Voyage.advance` without an alias, and rename the inventory CLI operation at
the same public boundary. On LLM response submission:

1. require `responding_to` to be a stable ID equal to the current open Turn's
   `evolution_entry_id` before interpreting the response;
2. freeze the response itself as one finite JSON object, reject the reserved
   key `revision`, and require a stable string `outcome` for routing;
3. validate that complete flat response against `response_schema` when
   present;
4. construct `LLMResponseContext` with the frozen response mapping and call
   `assess_response` only after schema success; and
5. route only after contextual acceptance.

`Voyage.validate` requires `responding_to` for an LLM response and performs the
same identity and formatting checks without mutation. `Voyage.advance`
requires it when accepting an LLM response, rejects it for unrelated machine
or continuation operations, and retains the existing `continue_` and
`dry_run` behavior. `Voyage.validate` likewise accepts omitted `responding_to`
for a machine result and rejects a non-`None` token there.

Sort schema errors deterministically by absolute response path and schema path,
then convert them to `ValidationIssue` values rooted directly at the flat
response field. A stale `responding_to` is ordinary response invalidity, not a
persisted Rutter fault. Schema-engine exceptions remain definition or state
errors and must not reach authored assessment.

Keep `Reckoning.global_revision` and `Turn.revision` as internal v3 storage
authority for this change, but remove revision from the public Message and
response contracts. Implement both projections in `Turn.to_json` /
`Turn.from_json` and their `history.py` helpers:

- encode a flat accepted response `{outcome, ...fields}` as the existing
  `{revision: turn.revision, outcome, evidence: {...fields}}` wire object;
- decode legacy evidence only when it contains neither reserved key `outcome`
  nor `revision`, producing `{outcome: wire.outcome, **wire.evidence}`;
- reject either collision with one stable `RutterStateError` before callbacks
  or mutation, leaving the persisted file untouched;
- pass the enclosing Turn revision to Message encoding, insert it only into
  wire `data.state`, and strip it from the public Message on decode;
- verify the duplicated wire state ID, entry ID, and revision against the
  enclosing Turn coordinates during decode; and
- keep wire `instructions.answer`, encoding absent `response_schema` as JSON
  `null` and otherwise encoding the schema object, then invert that projection
  on decode. `{}` remains distinct from absence.

Preserve canonical bytes for historical values without reserved evidence-key
collisions and do not bump `storage_version`. Do not claim that a live Turn
created under an old `AnswerSpec` definition can reopen under a changed schema
definition with the same identity version.

- [ ] **Step 6: Update visualization and the supplied inventory declaration**

Visualization statically emits the frozen `response_schema` and never compiles
or executes it. For a string `next_on_outcome`, render one edge labeled
`any accepted outcome`; for a mapping, render its explicit accepted outcome
keys. Cover no-schema responses, schema success followed by assessment
rejection, and schema-plus-assessment success as fixtures whose assessment
callbacks static visualization never invokes; the graph only labels routing
after runtime acceptance.

In the inventory Rutter, replace `AnswerSpec(...)` with the concrete flat
schema in the success example and rename `_validate_report` to
`_assess_report`. Move context-free checks for the exact response fields and
their basic JSON types into the schema. Update callbacks from
`response.evidence[...]` to direct mapping access such as
`response["inventory"]`. Keep sequence identity, history exhaustion,
inventory-domain shape, and semantic checks in `_assess_report`; remove its
now-redundant `outcome == "reported"` and basic field-presence checks.

Bump every durable Rutter definition whose LLM contract changes rather than
silently rebinding old active prompts: inventory version 4 to 5,
`DiagnoseAnswer` version 3 to 4, and `AskAndDiagnose` version 2 to 3. This is a
definition-version change, not a storage-version bump. Existing voyages require
their matching old definitions; this task does not add multi-version registry
support or a semantic prompt migrator.

- [ ] **Step 7: Run response, visualization, diagnostic, and inventory tests**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_evaluation.py src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_lifecycle.py \
  src/officina/rutter/tests/test_rutter_diagnostic.py src/officina/rutter/tests/test_rutter_storage.py \
  src/officina/rutter/tests/test_rutter_visualization.py \
  skills/math-dependency-graph/_rtx/tests/test_inventory_voyage_dispenser.py
```

Expected: stale entrance IDs and schema-invalid flat responses stop before
assessment; schema-valid responses retain existing contextual acceptance,
routing, persistence, and restart behavior through `advance`.

- [ ] **Step 8: Record a review checkpoint**

Reject any replacement response wrapper, parallel response-spec class,
schema-to-Python generator, schema outcome inference, remote reference loader,
public revision, storage-version change, claim that old `AnswerSpec` prompts
reopen under new schemas, or alias for `next` or `accept`. Permit only the
narrow Turn response/Message v3 adapters and stable reserved-key collision
rejection described above.
Commit only if explicitly authorized:

```bash
git add pyproject.toml src/officina/rutter/__init__.py \
  src/officina/rutter/model.py src/officina/rutter/values.py \
  src/officina/rutter/authoring.py src/officina/rutter/history.py \
  src/officina/rutter/runtime.py \
  src/officina/rutter/evaluation.py src/officina/rutter/engine.py \
  src/officina/rutter/diagnostic.py \
  src/officina/visualization/from_rutter/payload_builder.py \
  src/officina/rutter/tests/fixtures.py src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_runtime.py src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_engine.py src/officina/rutter/tests/test_rutter_hooks.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py src/officina/rutter/tests/test_rutter_diagnostic.py \
  src/officina/rutter/tests/test_rutter_storage.py \
  src/officina/rutter/tests/test_rutter_visualization.py \
  skills/math-dependency-graph/_rtx/_inquisitive_inventory_cli.py \
  skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py
git commit -m "feat(rutter): validate LLM responses with JSON Schema"
```

### Task 3: Make remaining public value boundaries explicit

**Files:**
- Modify: `src/officina/rutter/values.py`
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/evaluation.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/diagnostic.py`
- Modify: `src/officina/rutter/tests/fixtures.py`
- Modify: direct callers and focused tests in `src/officina/rutter/tests/test_rutter_model.py`,
  `src/officina/rutter/tests/test_rutter_runtime.py`, `src/officina/rutter/tests/test_rutter_engine.py`,
  `src/officina/rutter/tests/test_rutter_hooks.py`, `src/officina/rutter/tests/test_rutter_diagnostic.py`, and
  `src/officina/rutter/tests/test_rutter_lifecycle.py`
- Modify: direct `charter=` callers under
  `skills/math-dependency-graph/_rtx/`

**Interfaces:**
- Consumes: Task 2's schema-backed `Message` envelope and the existing typed
  `Transition`, `VoyageResult`, and `FaultSummary` values.
- Produces: `TransitionContext.transition: Transition`;
  `Terminal(*, result=...)` or `Terminal(*, result_constructor=...)` with
  exactly one mode; `SubRutter.charter_constructor` and
  `TransitionHook.charter_constructor`; read-only `Message.text`,
  `Message.response_schema`, `Message.payload`, `Message.evolution_id`, and
  `Message.evolution_entry_id` properties; `VoyageStatus.terminal_result`; and
  fully validated `FaultSummary` values.

- [ ] **Step 1: Write failing focused contract tests**

Add exact cases proving:

- transition-hook callbacks receive the existing typed `Transition` and read
  `source`, `outcome`, `target`, and `transition_id` as attributes;
- `Terminal(result=VoyageResult(...))` accepts a fixed result;
- `Terminal(result_constructor=callback)` accepts a one-context callback;
- providing both terminal modes or neither is a definition error;
- child Charter callbacks are passed only as `charter_constructor=` and the
  final Charter seen by the child is unchanged;
- `Message.text`, `Message.response_schema`, `Message.payload`,
  `Message.evolution_id`, and `Message.evolution_entry_id` return the frozen
  values already held in the public message projection;
- `VoyageStatus.terminal_result` contains exactly the value previously exposed
  as `active_result`, and `active_result` is absent; and
- `FaultSummary` rejects an invalid category, invalid optional evolution or
  target IDs, invalid hook IDs, a bare string/bytes hook-ID collection, and a
  non-tuple iterable containing invalid values;
- evolution ID and entry ID must be present together; every non-opaque summary
  requires both, while an opaque summary requires all IDs and hook IDs empty.

- [ ] **Step 2: Run the focused tests and verify the explicit contracts fail**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_hooks.py
```

Expected: typed transition attribute access, explicit terminal modes,
`charter_constructor`, and `terminal_result` are unsupported; `Message` lacks
the direct properties; and `FaultSummary` accepts malformed identifiers.

- [ ] **Step 3: Preserve the typed Transition in its context**

Change `TransitionContext.transition` from `JsonObject` to the existing
`Transition` value, require that exact type, and pass the object directly from
the engine instead of serializing it with `to_json()`. Update author callbacks
mechanically from mapping access such as `context.transition["source"]` to
attribute access such as `context.transition.source`. Do not create another
transition DTO or change the persisted transition/history representation.

- [ ] **Step 4: Split the two Terminal construction modes by name**

Give `Terminal` keyword-only `result: VoyageResult | None = None` and
`result_constructor: Callable[[EvolutionContext], VoyageResult] | None = None`.
Require exactly one. Validate the constructor as an inspectable one-argument
callback at the definition boundary. Update dynamic declarations, including
the success example, to use
`Terminal(result_constructor=_complete_result)`; fixed declarations use
`Terminal(result=VoyageResult(...))`. Remove the ambiguous positional and
callable-in-`result` modes without an alias.

Keep terminal evaluation, fault categorization, persisted `TerminalRecord`,
and `VoyageResult` semantics unchanged.

- [ ] **Step 5: Add direct Message properties without another wrapper**

Add read-only properties that project the already frozen envelope:

```python
@property
def text(self) -> str: ...

@property
def response_schema(self) -> JsonObject | None: ...

@property
def payload(self) -> JsonObject: ...

@property
def evolution_id(self) -> str: ...

@property
def evolution_entry_id(self) -> str: ...
```

Use these properties in author-facing context consumers and definition
authority checks where they replace nested dictionary access cleanly. Preserve
Task 2's revision-free public projection and qualified storage-version-3
compatibility boundary; do not add `MessageInstructions`, `MessageData`, or
another protocol object.

- [ ] **Step 6: Name child Charter constructors explicitly**

Rename the callable fields and keywords on both `SubRutter` and
`TransitionHook` from `charter` to `charter_constructor`. Apply the same rename
to `hook_sequence_after` and other diagnostic constructors, binder validation,
controlled evaluation, visualization labels, fixtures, and declarations.
Keep `Charter`, `EvolutionContext.charter`, persisted `ActiveRun.charter`, and
root `charter_data` unchanged. Do not add an alias.

For transition hooks, preserve the existing contract in this change: `None`
means that the contextual hook is not selected, while a JSON object constructs
the selected child Charter. Record that combined selection/construction
behavior explicitly; do not silently split it into another callback.

- [ ] **Step 7: Name the terminal-only VoyageStatus value**

Rename `VoyageStatus.active_result` to `terminal_result` and update the engine,
Compass-facing descriptions, inventory CLI projection, tests, and blueprint
descriptions mechanically. Preserve the invariant that it is non-null only
when the active evolution's calculated condition is `terminal` and a matching
persisted `TerminalRecord` exists. Do not add an alias.

- [ ] **Step 8: Enforce FaultSummary's declared invariants**

Require `category` to be a nonempty stable token; validate every non-`None`
evolution, entry, and target ID; freeze `transition_hook_ids` to a tuple and
validate every member as a stable hook ID, rejecting bare string and bytes
collections. Require `evolution_id` and `evolution_entry_id` together. Every
non-opaque summary requires both coordinates; category `opaque` requires both
coordinates, `target_evolution_id`, and hook IDs to be empty. Keep
`target_evolution_id` optional for non-opaque summaries. Do not introduce a
closed category enum or expose private fault authority. Preserve the opaque
projection `FaultSummary("opaque", None, None, None, ())`.

- [ ] **Step 9: Run focused behavior and persistence tests**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_diagnostic.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py src/officina/rutter/tests/test_rutter_storage.py
```

Expected: authors use typed transition attributes, explicit terminal and
Charter construction, direct Message properties, and `terminal_result` while
message serialization, fault projection, terminal records, hook selection,
and restart behavior remain unchanged.

- [ ] **Step 10: Record a review checkpoint**

Reject new wrapper values, persistence changes, a fault-category enum,
constructor aliases, or behavioral changes beyond the explicit boundaries.
Commit only if explicitly authorized:

```bash
git add src/officina/rutter/values.py src/officina/rutter/authoring.py \
  src/officina/rutter/runtime.py src/officina/rutter/evaluation.py \
  src/officina/rutter/engine.py src/officina/rutter/diagnostic.py \
  src/officina/rutter/tests/fixtures.py src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_runtime.py src/officina/rutter/tests/test_rutter_engine.py src/officina/rutter/tests/test_rutter_hooks.py \
  src/officina/rutter/tests/test_rutter_diagnostic.py src/officina/rutter/tests/test_rutter_lifecycle.py \
  src/officina/rutter/tests/test_rutter_storage.py \
  skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py
git commit -m "refactor(rutter): clarify public value boundaries"
```

### Task 4: Construct stateless Rutter definitions directly

**Files:**
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/tests/test_rutter_model.py`
- Modify: `src/officina/rutter/tests/test_rutter_runtime.py`

**Interfaces:**
- Consumes: Task 3's explicit terminal contract and schema-backed `Evolution`
  values plus existing
  `TransitionHook`, identifier validation, and binder validation.
- Produces: `Rutter(*, id, version, start, evolutions, hooks=(),
  allow_multiple_hooks_per_transition=False)`.

- [ ] **Step 1: Write failing direct-construction and snapshot tests**

Add tests equivalent to:

```python
def test_rutter_constructor_exposes_one_frozen_definition() -> None:
    evolutions = {"done": Terminal(result=VoyageResult("complete", {}))}
    definition = Rutter(
        id="direct",
        version=1,
        start="done",
        evolutions=evolutions,
    )
    evolutions.clear()

    assert definition.rutter_id == "direct"
    assert definition.definition_version == 1
    assert definition.initial_evolution_id == "done"
    assert set(definition.define_evolutions()) == {"done"}
    assert isinstance(definition.define_evolutions(), MappingProxyType)
    assert definition.define_transition_hooks() == ()


def test_legacy_no_argument_subclass_definition_remains_supported() -> None:
    definition = ExampleRutter()
    assert set(definition.define_evolutions()) == {"report", "complete"}


def test_rutter_constructor_snapshots_hook_sequence() -> None:
    hooks = []
    definition = Rutter(
        id="direct",
        version=1,
        start="done",
        evolutions={"done": Terminal(result=VoyageResult("complete", {}))},
        hooks=hooks,
    )
    hooks.append(object())
    assert definition.define_transition_hooks() == ()


def test_constructor_modes_are_disjoint() -> None:
    with pytest.raises(RutterDefinitionError):
        Rutter()
    with pytest.raises(RutterDefinitionError):
        ExampleRutter(
            id="hybrid",
            version=1,
            start="done",
            evolutions={"done": Terminal(result=VoyageResult("complete", {}))},
        )
```

Also add exact rejection cases for partially supplied constructor arguments,
non-mapping `evolutions`, non-sequence `hooks`, and non-Boolean
`allow_multiple_hooks_per_transition`. Do not duplicate graph validation that
the binder already owns.

```python
@pytest.mark.parametrize(
    "arguments",
    (
        {"id": "partial"},
        {"id": "bad", "version": 1, "start": "done", "evolutions": []},
        {
            "id": "bad",
            "version": 1,
            "start": "done",
            "evolutions": {
                "done": Terminal(result=VoyageResult("complete", {}))
            },
            "hooks": "audit",
        },
        {
            "id": "bad",
            "version": 1,
            "start": "done",
            "evolutions": {
                "done": Terminal(result=VoyageResult("complete", {}))
            },
            "allow_multiple_hooks_per_transition": 1,
        },
    ),
)
def test_rutter_constructor_rejects_invalid_definition_shape(arguments) -> None:
    with pytest.raises(RutterDefinitionError):
        Rutter(**arguments)
```

- [ ] **Step 2: Run the constructor tests and verify the current abstract class fails**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py::test_rutter_constructor_exposes_one_frozen_definition \
  src/officina/rutter/tests/test_rutter_model.py::test_legacy_no_argument_subclass_definition_remains_supported
```

Expected: direct construction fails because `Rutter` has no constructor-backed
definition; the legacy assertion remains green.

- [ ] **Step 3: Add the compatibility constructor to `Rutter`**

Use one omitted-value sentinel so inherited no-argument subclass construction
continues to call no setup path. When any direct-construction argument is
provided, require all of `id`, `version`, `start`, and `evolutions`; snapshot
the mapping with `MappingProxyType(dict(evolutions))`, snapshot hooks as a
tuple, and store the existing public metadata attributes. The existing
`define_evolutions()` and `define_transition_hooks()` methods return the stored
values for direct instances and retain the override path for subclasses.

The two modes are disjoint: exact `Rutter(...)` requires the complete direct
constructor, while legacy subclasses remain callable with no arguments whether
they inherit the base no-op path or define their own no-argument `__init__`.
The base direct-definition keywords are unavailable to subclasses. Reject bare
`Rutter()` and reject those keywords when a subclass inherits the base
constructor.

Do not validate successor graphs or callbacks in this constructor. The binder
must remain the sole graph-validation boundary.

- [ ] **Step 4: Run the focused authoring and registry tests**

Run:

```bash
python3 -m pytest -q src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_runtime.py
```

Expected: all tests pass, including the pre-existing class, instance, and
no-argument root-factory registration tests.

- [ ] **Step 5: Record a review checkpoint**

Review the diff for `authoring.py` and the two test files. Confirm that no
engine, storage, Voyage, diagnostic, visualization, or application behavior
changed in this task. Commit only if explicitly authorized:

```bash
git add src/officina/rutter/authoring.py \
  src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_runtime.py
git commit -m "feat(rutter): construct definition instances directly"
```

### Task 5: Construct transition-hook Rutters from context

**Files:**
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/runtime.py`
- Modify: `src/officina/rutter/evaluation.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/diagnostic.py`
- Modify: `src/officina/rutter/tests/fixtures.py`
- Modify: `src/officina/rutter/tests/test_rutter_hooks.py`
- Modify: `src/officina/rutter/tests/test_rutter_evaluation.py`
- Modify: `src/officina/rutter/tests/test_rutter_engine.py`
- Modify: `src/officina/rutter/tests/test_rutter_lifecycle.py`
- Modify: `src/officina/rutter/tests/test_rutter_diagnostic.py`

**Interfaces:**
- Consumes: Task 4 directly constructed `Rutter` definitions and the existing
  `TransitionContext`, `TransitionMatch`, and Charter-selection behavior.
- Produces: `TransitionHook(id, *, on, rutter_constructor,
  charter_constructor)`, where
  `rutter_constructor: Callable[[TransitionContext], Rutter]`; and
  `hook_sequence_after(..., rutter_constructor=...,
  charter_constructor=...)`; plus the transactional contextual-definition
  binding service consumed by Task 6.

- [ ] **Step 1: Write failing contextual-hook tests**

Add an authoring test proving that `TransitionHook` stores an inspectable
one-argument `rutter_constructor` instead of a fixed `child`. Add an engine test
with one hook whose constructor chooses between two preconstructed Rutter
instances from `context.transition`; assert that the selected child identity is
the one persisted in the active run.

Add a lifecycle test that stops while that hook child is active, opens the
Reckoning through a fresh equivalent registry, reconstructs the child from the
same transition context, verifies the persisted Rutter ID and version, and
continues exactly once. Add focused failure cases for a constructor that raises
and one that returns a non-Rutter value; both failures identify the hook and do
not create a child run. Add atomic rejection tests for a constructed child that
reuses the parent identity and one that collides with an already bound child
identity through a different Rutter object. Cover a valid returned Rutter that
fails binder validation, fault retry after correction, reopen failure without
disk mutation, and completed hook attachments being skipped before constructor
execution. In the same registry, reject one candidate and then successfully
retry a corrected different object under the same intended identity, proving
that failed contextual binding leaves no binder cache behind.

- [ ] **Step 2: Run the focused tests and verify fixed-child authoring fails**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_hooks.py \
  src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py
```

Expected: the new constructor keyword and contextual child lifecycle fail
because `TransitionHook` still requires `child` and the binder assumes every
hook child is statically reachable.

- [ ] **Step 3: Replace the fixed hook child with one constructor callback**

In `TransitionHook`, replace `child` with
`rutter_constructor: Callable[[TransitionContext], Rutter]`. Keep `on` as the
complete declaration of when the hook applies and keep the existing optional
Charter-construction semantics under the Task 3 name
`charter_constructor(context) -> JsonObject | None`. It remains the existing
selection and child-Charter boundary. Require both callbacks to accept exactly
one context at binding. Do not add
`pre_evolution`, `post_evolution`, a fixed-child alias, a second hook type, or a
wrapper result object.

Update `diagnose_answer_on`, `ask_and_diagnose_on`, and
`hook_sequence_after` to pass a `rutter_constructor` through to
`TransitionHook`. Their existing matching, sequence, and Charter behavior does
not change. Update the shared Rutter fixture and diagnostic tests mechanically
from fixed `child` assertions to constructor-result assertions.

- [ ] **Step 4: Bind the constructed child at the transition boundary**

Evaluate `rutter_constructor(context)` only after `on` matches and the
`charter_constructor` selects the hook. Require a `Rutter` instance result;
convert callback
exceptions or invalid results into one hook-construction fault carrying the
hook ID. `RutterRegistry` retains its `_DefinitionBinder` and injects one
contextual-binding callable into `Voyage`; `engine.py` must not import the
binder back from `runtime.py`. That callable validates each newly encountered
constructor result through a fork or snapshot of the current binder state.

Before mutating the Voyage's in-memory definition map, compare the entire
candidate reachable closure against every definition already present. An
absent identity may be added; an existing identity may be reused only when it
names the same in-memory Rutter source object. Reject parent-identity and
existing-child collisions atomically before pushing the child. Also reject a
result whose identity is already on the active ancestor path, even when it is
the same source object, preserving runtime recursive-call-cycle safety.
Constructors therefore choose among stable definition instances within one
Voyage rather than manufacturing a different object under an existing
identity.

Commit the forked binder caches and the Voyage definition-map additions only
after binding, reachable-closure validation, identity collision checks, and
active-ancestor checks all succeed. Any failure discards the fork completely,
so a corrected constructor result can be retried in the same registry without
stale `_by_source`, `_source_by_id`, or `_visiting` state.

Do not persist the Python object. Reckoning continues to store only the active
child's Rutter ID, definition version, hook ID, transition attachment, Charter,
and history.

- [ ] **Step 5: Reconstruct contextual children during open**

Use one contextual-child resolver for initial hook push and active-hook
authority validation. Cache its successful result in memory by parent run,
hook ID, and attached transition ID so ordinary store transactions validate
against the already bound result rather than rerunning authored code.

Before validating an active hook child on reopen, recover its parent hook and
attached transition from persisted history, rebuild the same
`TransitionContext`, call `rutter_constructor` once, and bind the result.
Require its `(rutter_id, definition_version)` to equal the persisted active
child identity before normal validation continues. A mismatch is
`RutterStateError`; do not silently substitute another definition or change
storage version 3.

During normal advancement, constructor exceptions, invalid return values, and
binder-validation failures become the same persisted hook-construction fault
carrying the hook ID. During `open`, the same failures become a hook-identified
`RutterStateError`; opening is read-only and must not publish a fault or mutate
the Reckoning file. Check the completed-attachment set before invoking the
constructor so a completed hook is never reconstructed merely to skip it.

Document and test the callback contract as replayable: for the same immutable
Charter, transition, record, and history prefix, it must return an equivalent
Rutter identity. Within one Voyage, repeated selection of an identity returns
the same definition instance; a freshly opened registry may reconstruct a
fresh equivalent instance with the persisted ID and version. The callback may
choose different definitions for different contexts, but must not rely on
mutable voyage state or external side effects.

- [ ] **Step 6: Run hook, recovery, and storage-compatibility tests**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_hooks.py \
  src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py \
  src/officina/rutter/tests/test_rutter_storage.py \
  src/officina/rutter/tests/test_rutter_diagnostic.py
```

Expected: contextual choice and reopen pass without further Reckoning or
storage-codec changes, while existing hook ordering, cardinality, skip, and
fault behavior remain green.

- [ ] **Step 7: Record a review checkpoint**

Reject any new hook hierarchy, pre/post wrapper, persisted definition object,
or callback execution during static binding or visualization. Commit only if
explicitly authorized:

```bash
git add src/officina/rutter/authoring.py src/officina/rutter/runtime.py \
  src/officina/rutter/evaluation.py src/officina/rutter/engine.py \
  src/officina/rutter/diagnostic.py src/officina/rutter/tests/fixtures.py \
  src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_diagnostic.py \
  src/officina/rutter/tests/test_rutter_evaluation.py src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py
git commit -m "feat(rutter): construct hook children from context"
```

### Task 6: Construct and execute contextual SubRutters

**Files:**
- Modify: `src/officina/rutter/authoring.py`
- Modify: `src/officina/rutter/runtime.py`
- Modify: `src/officina/rutter/engine.py`
- Modify: `src/officina/rutter/diagnostic.py`
- Modify: `src/officina/rutter/tests/fixtures.py`
- Modify: `src/officina/rutter/tests/test_rutter_model.py`
- Modify: `src/officina/rutter/tests/test_rutter_runtime.py`
- Modify: `src/officina/rutter/tests/test_rutter_evaluation.py`
- Modify: `src/officina/rutter/tests/test_rutter_hooks.py`
- Modify: `src/officina/rutter/tests/test_rutter_engine.py`
- Modify: `src/officina/rutter/tests/test_rutter_lifecycle.py`

**Interfaces:**
- Consumes: Task 4 directly constructed definitions and Task 5's transactional
  contextual-definition binding service.
- Produces: `SubRutter(rutter_constructor, *, charter_constructor,
  next_on_outcome | choose_next)`, where
  `rutter_constructor: Callable[[EvolutionContext], Rutter]`; and read-only
  `Voyage.rutter: Rutter`.

- [ ] **Step 1: Write failing contextual SubRutter tests**

Construct a stable terminal child with `Rutter(...)`, then cover:

```python
def test_subrutter_constructs_child_from_evolution_context(
    reckoning_root: Path,
) -> None:
    child = Rutter(
        id="instance-child",
        version=1,
        start="done",
        evolutions={"done": Terminal(result=VoyageResult("complete", {}))},
    )

    def make_child(context: EvolutionContext) -> Rutter:
        assert context.evolution_id == "call"
        return child

    parent = Rutter(
        id="instance-parent",
        version=1,
        start="call",
        evolutions={
            "call": SubRutter(
                make_child,
                charter_constructor=lambda context: {},
                next_on_outcome="done",
            ),
            "done": Terminal(result=VoyageResult("complete", {})),
        },
    )

    voyage = RutterRegistry({"parent": parent}, reckoning_root).create(
        "parent", Path("instance.reckoning.json"), {}
    )
    assert voyage.rutter is parent
```

Add a second test whose constructor chooses between two preconstructed child
Rutters from `EvolutionContext`. Assert that the selected identity is persisted
in the active child and that child push, return, and parent routing occur once.

Construct fresh equivalent parent and child definition instances with the same
identities, open the Reckoning through a second registry, complete the child,
and reach the frozen parent target exactly once. Assert
`opened.rutter is replacement_parent` and that assigning `opened.rutter`
raises `AttributeError`.

Add focused constructor-raises, non-Rutter-result, active-ancestor-cycle, and
identity-collision cases. Assert that every rejection leaves binder and Voyage
definition maps unchanged, and that a corrected different object under the
same intended identity succeeds on retry in the same registry. Do not create a
second binder or collision policy for explicit children.

- [ ] **Step 2: Run the focused tests and verify the constructor is unsupported**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py
```

Expected: the contextual constructor tests fail because `SubRutter` still
requires a fixed Rutter class in `child`.

- [ ] **Step 3: Replace the fixed child with one constructor callback**

Replace `SubRutter.child` with
`rutter_constructor: Callable[[EvolutionContext], Rutter]`. Require exactly
one positional context argument at binding. Remove the fixed `child` keyword
without an alias or parallel constructor mode.

Update existing SubRutter declarations mechanically to provide a named
one-argument constructor returning their stable child definition. Do not
execute those constructors during static binding or change their
`charter_constructor` and routing callbacks. The temporary transparent-Rutter
prototype remains owned by
Task 7, which deletes it.

- [ ] **Step 4: Resolve explicit children through the contextual binding service**

When entering a `SubRutter` evolution, call
`rutter_constructor(EvolutionContext)` and require a Rutter instance. Resolve
it through Task 5's injected transactional binding service before calling
`charter_constructor` and pushing the child. Cache the successful resolution by
parent run and evolution-entry ID so validation does not rerun authored code.

On reopen with an active explicit child, reconstruct the parent
`EvolutionContext`, invoke the constructor once, resolve it transactionally,
and require its identity to match the persisted active child before ordinary
authority validation. Constructor or binding failure during advancement uses
one persisted child-construction fault; the same failure during open is a
read-only `RutterStateError`. Do not change Reckoning or storage version 3.

Add:

```python
@property
def rutter(self) -> Rutter:
    return self._definition.definition
```

Do not persist the Rutter object or add it to Reckoning JSON.

- [ ] **Step 5: Run composition, engine, and lifecycle tests**

Run:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_hooks.py \
  src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py
```

Expected: all contextual SubRutter, hook, instance, restart, and compatibility
paths pass without storage-schema or history changes.

- [ ] **Step 6: Record a review checkpoint**

Confirm that SubRutter and TransitionHook share one binding service and differ
only in their author context, provenance, and Charter selection semantics.
Commit only if explicitly authorized:

```bash
git add src/officina/rutter/authoring.py src/officina/rutter/runtime.py \
  src/officina/rutter/engine.py src/officina/rutter/diagnostic.py \
  src/officina/rutter/tests/fixtures.py src/officina/rutter/tests/test_rutter_model.py \
  src/officina/rutter/tests/test_rutter_runtime.py src/officina/rutter/tests/test_rutter_evaluation.py \
  src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_lifecycle.py
git commit -m "feat(rutter): construct explicit children from context"
```

### Task 7: Make instances visible to authors and convert the supplied Rutter

**Files:**
- Modify: `src/officina/visualization/from_rutter/__init__.py`
- Modify: `src/officina/visualization/from_rutter/payload_builder.py`
- Modify: `src/officina/rutter/tests/test_rutter_visualization.py`
- Modify: `skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py`
- Delete: `skills/math-dependency-graph/_rtx/_transparent_rutter_prototype.py`

**Interfaces:**
- Consumes: schema-backed directly constructed Rutters and contextual hook and
  SubRutter constructors from Tasks 2-6.
- Produces: instance-compatible `build_rutter_payload(rutter)` and
  `RutterVisualizer.build(rutter, ...)`; canonical
  `INQUISITIVE_INVENTORY: Rutter`.

- [ ] **Step 1: Write the failing visualization instance test**

Instantiate the existing visualization example with `Rutter(...)` and assert
that `build_rutter_payload(definition)` preserves its identity, evolution
order, instructions, routing, and hooks. Its graph must include contextual
SubRutter and hook children. Represent both child identities as
`Determined at runtime` without executing either `rutter_constructor`.

Keep the existing class-input and `rutter_class=` keyword tests to prove
compatibility. This plan does not change visualization's pre-existing treatment
of source filtering performed inside `hook_sequence_after` callbacks.

- [ ] **Step 2: Run the visualization test and verify the class-only boundary fails**

Run the new exact test with:

```bash
python3 -m pytest -q src/officina/rutter/tests/test_rutter_visualization.py
```

Expected: the instance case fails with `rutter_class must be a Rutter class`.

- [ ] **Step 3: Accept either an instance or legacy class in visualization**

Keep the public parameter name `rutter_class` so keyword callers remain
compatible, but broaden its accepted type. If it is already a `Rutter`, use it
directly; if it is a Rutter subclass, instantiate it through the existing
compatibility path; otherwise raise `TypeError`. Add one `_rutter_label`
helper that returns an instance's `rutter_id` or a legacy class's declared ID;
use it for definition descriptions and detail fields. A SubRutter or hook's
contextual child is always labeled `Determined at runtime`; static extraction
must never call `rutter_constructor`. Make only these source adaptations in
this task; do not redesign hook matching or duplicate binder validation.

- [ ] **Step 4: Convert only the inventory Rutter declaration**

Replace `class InquisitiveInventoryRutter(Rutter)` with the
`INQUISITIVE_INVENTORY = Rutter(...)` declaration in this plan's success
example, using `_REPORT_EVOLUTION`, `_RECORD_EVOLUTION`,
`_TRANSITION_HOOK_ID`, and `_RUTTER_ID` rather than duplicating their literal
values. Preserve the Task 2 definition-version bumps: inventory version 5 and
`DiagnoseAnswer` version 4. Define one stable
`_DIAGNOSIS_RUTTER = DiagnoseAnswer()` instance and
the adjacent ordinary `_diagnosis_rutter(context) -> Rutter` hook constructor
shown in the success example; it returns that instance and performs no IO.
Change `_registry()` to register the inventory object. Update assertions that read
`InquisitiveInventoryRutter.rutter_id` to read the canonical instance.

Do not move, simplify, or otherwise edit the semantic comparison,
Charter constructors, response assessors, machine handler, setup functions,
SQLite logic, or ledger implementation in this task.

- [ ] **Step 5: Run the complete supplied-Rutter behavior suite**

Run:

```bash
python3 -m pytest -q \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py \
  src/officina/rutter/tests/test_rutter_visualization.py
```

Expected: the equal-report, different-report, reopen-at-each-boundary,
two-sequence, setup, CLI, and visualization cases all pass with the instance
declaration.

- [ ] **Step 6: Remove the generated-class prototype**

Delete `_transparent_rutter_prototype.py`. Search the owned scope:

```bash
rg -n "DeclaredRutter|def rutter\(|importlib|InquisitiveInventoryRutter" \
  src/officina/rutter src/officina/visualization/from_rutter \
  skills/math-dependency-graph/_rtx src/officina/rutter/tests/test_rutter_*.py
```

Expected: no prototype adapter remains; any `importlib` match must be unrelated
existing infrastructure and must not be edited under this plan.

- [ ] **Step 7: Record a review checkpoint**

Confirm that the inventory diff changes only its definition declaration, local
hook-constructor function and stable returned instance, registry reference,
and corresponding identity assertions. Commit only if explicitly authorized:

```bash
git add src/officina/visualization/from_rutter/__init__.py \
  src/officina/visualization/from_rutter/payload_builder.py \
  src/officina/rutter/tests/test_rutter_visualization.py \
  skills/math-dependency-graph/_rtx/_inventory_pipeline/_voyage_dispenser.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py
git commit -m "refactor(rutter): make inventory definition transparent"
```

### Task 8: Align the authoring contract and verify the repository

**Files:**
- Modify: `docs/plans/rutter-design/01-core-design.md`
- Modify: `src/officina/rutter/blueprints/authoring.yaml`
- Modify: `src/officina/rutter/blueprints/values.yaml`
- Modify: `src/officina/rutter/blueprints/evaluation.yaml`
- Modify: `src/officina/rutter/blueprints/runtime.yaml`
- Modify: `src/officina/rutter/blueprints/engine.yaml`
- Modify: `src/officina/rutter/blueprints/model.yaml`
- Regenerate: `src/officina/rutter/blueprint.yaml`

**Interfaces:**
- Consumes: the passing authoring behavior from Tasks 1-7.
- Produces: instance-first documentation using `next_on_outcome` and
  `choose_next`, LLM response validation using `response_schema` and
  `assess_response`, flat revision-free responses correlated by
  `responding_to`, operational advancement through `Voyage.advance`,
  contextual SubRutter and hook construction using `rutter_constructor` and
  `charter_constructor`, typed transition contexts, explicit terminal
  construction modes, direct Message properties,
  `VoyageStatus.terminal_result`, validated fault summaries, and ownership
  metadata with no claim that a specific Rutter requires a subclass.

- [ ] **Step 1: Update the exact canonical public contracts changed here**

In `01-core-design.md`, update the definition-lifecycle and author-facing
primitive sections: state that a Rutter is a stateless definition object whose
authored mappings are snapshotted, a Voyage owns its execution, and direct
construction is preferred. Retain legacy subclass compatibility in one concise
note. Replace the routing
parameter `then` with `next_on_outcome` for static routing and `choose_next` for
callback routing; replace `AnswerSpec` and `validate` with `response_schema`
and `assess_response`; replace `Voyage.next` with `Voyage.advance`; specify the
flat response and `responding_to=evolution_entry_id` contract; remove the
public `Response`, revision, evidence wrapper, and `accept`; replace fixed
transition-hook children with the explicit `rutter_constructor(context) ->
Rutter` contract shared by SubRutter and transition hooks; and name child
Charter callbacks `charter_constructor`.

Apply narrow mechanical corrections wherever the canonical document currently
states those public APIs, including its existing “Messages and responses,”
“Contexts and purity,” “Public operating interface,” operation table, and
Compass call spelling. Show both normal advance forms:
`advance(response, responding_to=message.evolution_entry_id, ...)` for LLM
submission and `advance(...)` for machine or continuation work. State that
omitting `assess_response` accepts every response that passes engine formatting,
mapping-key acceptance, and the optional schema. For transition hooks, state
that `charter_constructor(context) -> JsonObject | None` returns `None` to
suppress the hook and a JSON object to both select it and construct its child
Charter.

Do not edit the stale pre-cutover examples in `04-examples.md`, and do not
rewrite lifecycle, storage, recovery, or Compass semantics. Document the Task 3
boundary changes only where those public values are already specified. Search
the canonical document and owned blueprints for stale `AnswerSpec`, public
`Response`, `active_result`, public `Voyage.next`, and the old public response
envelope; every remaining match must be explicitly historical or out of scope.

- [ ] **Step 2: Update and regenerate owned blueprint descriptions**

Change only source descriptions that describe `AnswerSpec`, response
validation order, public response revisions or evidence envelopes,
`Voyage.next`, public `accept`, transition contexts as JSON mappings,
ambiguous terminal results, `active_result`, unvalidated fault summaries,
unnamed Charter constructors, or child references as classes or factories.
Declare the `jsonschema` runtime dependency on the evaluation source.
Regenerate derived blueprint output with:

```bash
env PYTHONPATH=src \
  python3 -m officina.dispatcher.cli \
  --repository-config officina.toml \
  --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints
```

Do not hand-edit `src/officina/rutter/blueprint.yaml`.

- [ ] **Step 3: Run focused and repository verification**

Run the Rutter suites:

```bash
python3 -m pytest -q \
  src/officina/rutter/tests/test_rutter_model.py src/officina/rutter/tests/test_rutter_runtime.py \
  src/officina/rutter/tests/test_rutter_evaluation.py src/officina/rutter/tests/test_rutter_engine.py \
  src/officina/rutter/tests/test_rutter_hooks.py src/officina/rutter/tests/test_rutter_lifecycle.py \
  src/officina/rutter/tests/test_rutter_diagnostic.py src/officina/rutter/tests/test_rutter_visualization.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_rutter.py \
  skills/math-dependency-graph/_rtx/tests/test_inquisitive_inventory_cli.py
```

Then run:

```bash
env PYTHONPATH=src \
  python3 -m officina.dispatcher.cli \
  --repository-config officina.toml \
  --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints --check
python3 repo_checks.py --suite precommit --jobs 8
```

Record exit codes, pass counts, skips, and any unrelated dirty-worktree
blocker; do not claim whole-repository success from only the focused command.

- [ ] **Step 4: Audit the final diff against minimality**

The final owned diff may contain only:

1. the `then` to `next_on_outcome`/`choose_next` routing rename and mechanical
   caller updates;
2. `Voyage.next` to `Voyage.advance`, the `responding_to` entrance token, and
   mechanical Compass and inventory-CLI caller updates;
3. `AnswerSpec` and `Response` removal, flat revision-free public responses,
   `response_schema` validation, `assess_response`, private default acceptance,
   and the `jsonschema` runtime dependency;
4. the narrow Turn-aware v3 Message and response projections, stable rejection
   of reserved legacy evidence-key collisions, and unchanged persisted
   `{revision, outcome, evidence}` response envelope;
5. typed `TransitionContext.transition`, explicit `Terminal` result modes,
   `charter_constructor`, direct `Message` properties,
   `VoyageStatus.terminal_result`, and `FaultSummary` invariant checks;
6. the concrete `Rutter` constructor and compatibility accessors;
7. contextual `TransitionHook.rutter_constructor` evaluation, binding, and
   restart reconstruction;
8. contextual `SubRutter.rutter_constructor` evaluation and restart
   reconstruction through the shared binding service;
9. `Voyage.rutter`;
10. instance-compatible visualization with dynamic children left unexecuted;
11. the inventory declaration conversion;
12. focused tests; and
13. routing, response submission, schema/assessment, explicit value
    boundaries, child-constructor,
    and class-versus-instance documentation and blueprint wording.

Reject storage-version or persisted-wire changes beyond the narrow Message and
response projections; any claim that old `AnswerSpec` prompts semantically
reopen under new schema definitions; changes to Reckoning revision semantics;
and unrelated Compass-loop, handler, setup, semantic-comparison, ledger, or
refactoring changes.

- [ ] **Step 5: Record the final checkpoint**

If all required verification is green and the user explicitly authorizes a
commit, stage only the exact owned documentation, blueprint, and test files and
commit with:

```bash
git add docs/plans/rutter-design/01-core-design.md \
  src/officina/rutter/blueprints/authoring.yaml \
  src/officina/rutter/blueprints/values.yaml \
  src/officina/rutter/blueprints/evaluation.yaml \
  src/officina/rutter/blueprints/runtime.yaml \
  src/officina/rutter/blueprints/engine.yaml \
  src/officina/rutter/blueprints/model.yaml \
  src/officina/rutter/blueprint.yaml
git commit -m "docs(rutter): document instance definitions"
```

### Task 9: Expose collections of Voyages to Compass without serialization

This user-approved follow-up expands the Task 8 minimality boundary. A Python
Rutter or Voyage must not cross the prompt boundary. Instead, one configured
`VoyageDispenser` enumerates opaque Voyage IDs, resolves each authorized ID to
one live Voyage, and mirrors `get_status`, `validate`, and `advance` with a
leading `voyage_id`. One shared CLI maps `list`, `status`, `validate`, and
`advance` to finite JSON. `using-compass` consumes that versioned process
binding directly; runtime `help()` discovery is removed because every dispenser
has the same contract.

**Files:**
- Create: `src/officina/rutter/dispenser.py`
- Create: `src/officina/rutter/blueprints/dispenser.yaml`
- Create: `src/officina/rutter/tests/test_rutter_dispenser.py`
- Modify: `src/officina/rutter/__init__.py`
- Modify: `src/officina/rutter/blueprint.yaml`
- Modify: `src/officina/runtime/python_machine_interface_runner.py`
- Modify: `tests/test_officina_python_machine_interface.py`
- Modify: `skills/using-compass/SKILL.md`
- Modify: `skills/using-compass/blueprint.yaml`
- Modify: `skills/using-compass/blueprints/gateway.yaml`
- Modify: `skills/using-compass/tests/test_using_compass_instructions.py`
- Modify: the inquisitive-inventory CLI source, blueprint, module versions, and
  focused tests.

- [x] Add failing tests proving that two real Voyages are enumerated and that
  operations affect only the selected `voyage_id`.
- [x] Implement `VoyageDispenser(get_voyage_ids=..., open_voyage=...)` and the
  reusable `voyage_dispenser_cli(dispenser, argv)` mapper.
- [x] Permit a registered Python process entry to be a configured
  `PythonMachineInterface` instance as well as an existing constructor.
- [x] Replace Compass's untransportable Python-Voyage/help bootstrap with the
  authorized dispenser process loop.
- [x] Demonstrate two-worker inventory enumeration and selection from sibling
  experiment directories without exposing their paths as Voyage IDs.
- [x] Run focused Rutter, dispatcher, Compass, inventory, blueprint, and diff
  checks; confirm the final implementation agrees with this task. The scoped
  tests pass. The broader blueprint test reaches and passes the new dispenser
  ownership assertions, then stops on the checkout's unrelated pre-existing
  inventory dependency mismatch; the global sync check likewise retains that
  unrelated dirty manifest mismatch.

### Task 10: Make the inventory Rutter module the dispenser entrypoint

- [x] Move the configured `VoyageDispenser`, setup compatibility operations,
  process `Interface`, and `main` into `_inquisitive_inventory_rutter.py`.
- [x] Remove the redundant `_inquisitive_inventory_cli.py` implementation and
  its separate behavioral-source blueprint.
- [x] Make the Rutter source own the public experiment process binding and
  preserve its two-file implementation boundary with
  `_inquisitive_inventory_support.py`.
- [x] Update focused behavior and blueprint-ownership tests and regenerate the
  math-dependency-graph skill contract.
