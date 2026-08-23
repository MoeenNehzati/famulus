# DiagnoseAnswer Correction and Inventory Recognition Hook Design

Status: Approved in conversation; pending written-spec review

## Goal

Extend the standard `DiagnoseAnswer` Rutter with an opt-in correction loop, then
use it from the math-dependency inventory Rutter to verify and, when necessary,
correct recognition of new nodes and direct dependency edges before the next
text section is shown.

The design keeps diagnosis generic and keeps inventory mutation in an
inventory-owned repeat-safe action.

## Public diagnostic contract

`DiagnosisCase` gains one exact-Boolean field:

```python
DiagnosisCase(
    question=question,
    actual_answer=answer,
    precomputed_verdict=verdict,
    ask_for_fix=False,
)
```

`diagnose_answer_on(...)` gains the matching keyword-only
`ask_for_fix: bool = False` argument. The default preserves the current
diagnosis-only behavior.

`ask_for_fix` is part of the exact JSON contract. Integers and other truthy
values are rejected. The new contract does not silently reinterpret persisted
voyages created under the old `DiagnoseAnswer` definition version.

No `FixAndDiagnose` Rutter is added to the core. `AskAndDiagnose` continues to
construct diagnosis-only cases unless its caller explicitly supplies the new
behavior through a later, separately designed interface change.

## DiagnoseAnswer state flow

The initial candidate is `DiagnosisCase.actual_answer`.

1. `route` honors an initial precomputed verdict. `True` completes as equal,
   `False` proceeds to explanation, and `None` proceeds to LLM comparison.
2. `compare` decides whether the current candidate is semantically equal to the
   expected answer.
3. `explain` returns the existing structured `DiagnosisDetail`.
4. If `ask_for_fix` is false, `explain` completes with the existing `different`
   result, unchanged.
5. If `ask_for_fix` is true, `explain` proceeds to a `fix` Prompt. That Prompt
   returns one replacement answer constrained by the question, expected answer,
   format hint, metadata, and latest diagnosis.
6. A replacement always returns to `compare`. A failed comparison produces a
   new explanation and another fix opportunity; a successful comparison
   completes as `corrected`.

Only the initial candidate may use `precomputed_verdict`. Every replacement is
checked by `compare`; an old verdict must not be reused for new text. The loop
does not spin automatically: every compare, explanation, and fix is an explicit
persisted Prompt turn that can be inspected or resumed.

The existing `equal` and `different` result payloads remain unchanged when
`ask_for_fix` is false. A successful correction returns:

```json
{
  "case_id": "...",
  "original_answer": "...",
  "final_answer": "...",
  "expected_answer": "...",
  "correction_count": 1,
  "initial_detail": {
    "mistake": "...",
    "reason": "...",
    "minimal_fix": "..."
  }
}
```

The generic Rutter returns text; it does not apply that text to a file or parent
Rutter. Application remains owned by the caller.

## Inventory integration

The inventory parent keeps `report` as the Prompt that displays a text section
and accepts recognized node and edge IDs. The current after-`report` diagnosis
maker is replaced by one inventory-owned child Rutter attached to both incoming
boundaries:

- `before("report")` checks the report for the section just completed before the
  next section is exposed;
- `before("complete")` performs the same check for the final section.

The initial `report` has no incoming transition and therefore correctly has no
prior section to check.

The inventory child has three small responsibilities:

1. derive the just-completed report and its gold recognition case from the
   frozen parent-edge context;
2. call `DiagnoseAnswer` with `ask_for_fix=True`;
3. on `corrected`, run an inventory-owned repeat-safe action that validates and
   records the corrected recognition; on `equal`, record the accepted original
   without rewriting it.

This child is an application adapter, not a second generic diagnosis API. Its
repeat-safe action owns the inventory ledger row and uses the engine-provided
action ID for idempotency. Reopening a completed hook must neither duplicate a
row nor apply a correction twice.

The parent action between `report` and the next state becomes routing-only: it
decides whether another section remains. Ledger writing moves into the hook
child because correction now finishes on the incoming edge after that parent
action. The next `report` data is built from finalized hook results, so it sees
the corrected cumulative recognition before exposing the next section.

There is exactly one recognition hook per accepted report. The old after-state
maker is removed rather than retained alongside the incoming-edge hooks.

## Versioning and ownership

The implementation must bump every changed public definition, behavioral
source, interface, and exporting module version required by the repository's
Officina standards. At minimum, `DiagnoseAnswer.definition_version` advances
from 1 to 2 because its state graph and charter contract change. The inventory
Rutter definition also advances because its state and hook graphs change.

Before implementation, the exact blueprint/version closure will be obtained
from the repository standards and dependency graph. Existing persisted voyages
remain bound to their recorded versions; there is no migration or compatibility
shim that reads an old charter as the new exact schema.

Core diagnostic files own the generic flag, prompts, result construction, and
case-maker argument. Math-dependency files own extraction of the preceding
report, corrected inventory application, ledger behavior, and incoming-edge
hook registration. No engine, reducer, storage, or generic hook-DSL changes are
in scope.

## Verification

Core diagnostic tests must establish:

- the default false path preserves current equal and different results;
- exact-Boolean validation and exact JSON fields;
- initial equal completion with correction enabled;
- precomputed-different, explain, fix, compare-equal, corrected completion;
- repeated compare-different, explain, and fix cycles;
- invalid correction evidence does not mutate state;
- reopen behavior at every new state;
- old/new definition-version separation and JSON round trips.

Inventory tests must establish:

- no hook runs before the initial report;
- every later report waits for recognition diagnosis and any correction;
- the final report is checked before completion;
- equal answers are recorded without rewriting;
- corrected answers are validated, recorded, and visible to the next report;
- repeat-safe application is exactly once across reopen/recovery;
- one and only one hook result and ledger row exists per accepted report;
- malformed or stale submissions remain non-mutating.

The scoped tests run first, followed by the plan-owned repository verification
commands. Stress coverage exercises long correction chains, repeated recovery,
and concurrent duplicate submissions without changing product behavior solely
to satisfy the harness.

## Non-goals

- No new generic `FixAndDiagnose` class.
- No filesystem or application callback in `DiagnoseAnswer`.
- No implicit correction when `ask_for_fix` is absent or false.
- No automatic migration of persisted version-1 voyages.
- No parallel hook execution or hook-DSL extension.
- No broader math-dependency extraction redesign.
