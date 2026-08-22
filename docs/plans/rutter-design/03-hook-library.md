# Rutter hooks and standard hook library

This document owns transition-hook semantics, the CaseMaker API, reusable
diagnostic children, and CaseMaker constructors. Hook details do not belong in
the core design.

## Hook model

A transition hook performs attached work before an already-selected transition
continues.

```text
state completes and selects edge
-> consult CaseMakers
-> if one selects a Charter, run its child Rutter
-> child returns
-> resume the frozen edge
```

The child cannot redirect, replace, or cancel the frozen transition. If a child
result should control parent routing, use a visible `Call` state instead.

## CaseMaker

`CaseMaker` is the sole transition-hook API:

```python
CaseMaker(
    id: str,
    *,
    on: EdgeMatch,
    child: type[Rutter],
    charter: Callable[[EdgeContext], JsonObject | None],
)
```

Returning `None` declines. Returning finite JSON selects one child Charter.

```python
EdgeMatch(
    source: str | None = None,
    outcome: str | None = None,
    target: str | None = None,
)
```

`None` is a wildcard. Convenience matchers are constructors, not runtime
concepts:

```python
after("report")
before("publish")
on_edge(source="review", outcome="approved", target="publish")
```

This covers after-state, before-target, outcome-specific, exact-edge, post-Call,
and post-Done attachments. Initialization has no edge; preflight uses an
initial Call. A terminal hook matches the visible Done source.

All structurally matching makers are evaluated in definition order against the
same immutable anchored EdgeContext and their selected children are pooled in
that order. Its history is the strict prefix before the accepted source record;
the record is supplied separately, and same-edge attachments never enter the
callback-visible context. Zero selections continue normally. If multiple are
selected while the parent's `allow_multiple_cases_at_once` is false, a
cardinality fault names every selected maker before any child starts. Otherwise
the children run sequentially. The pool is derived from the accepted record and
versioned pure callbacks; it is not a persisted queue or lifecycle phase.

`allow_multiple_cases_at_once` controls selection cardinality on one edge; it
never authorizes concurrent child execution.

The exact attached lifecycle is:

```text
source node record accepted while the parent remains at that node
-> `then` chooses the destination and CaseMakers derive the ordered child pool
-> atomically attach the first unfinished child Rutter
-> child completes
-> atomically archive and detach it and append its attached CallRecord
-> recompute the same pure/versioned edge and pool from the accepted record
-> skip stable maker/edge identities already represented by CallRecords
-> run the next unfinished child, or enter the chosen target when none remain
```

Recovery may rerun pure `then` and CaseMaker callbacks, but the definition
version and anchored history prefix make their result stable. A sealed active
child keeps its original Charter. The engine—not callback-visible history—uses
the full durable history to skip completed maker/edge identities. CaseMaker
callback failure preserves the accepted source record and commits a fault. A
cardinality fault enters neither a child nor the selected target.

When order or data dependency is intrinsic to one attachment, represent it as
one ordinary orchestrating child:

```python
class CombinedChecks(Rutter):
    start_state = "first"

    def define_states(self):
        return {
            "first": Call(CheckA, charter=self.case_a, then="second"),
            "second": Call(CheckB, charter=self.case_b, then="complete"),
            "complete": Done(result=self.result),
        }
```

Changing a CaseMaker ID, order, matcher, child, or Charter builder requires a
parent definition-version change.

## Library boundary

The reusable library contains:

- two small ordinary Rutters suitable as hook children; and
- three constructors for repeated CaseMaker patterns.

They compile to ordinary Rutter, CaseMaker, Charter, CallRecord, and RunResult
values. They add no engine phase, diagnostic reducer, scheduler record, or
persistence type. The core runtime does not import this library.

## Diagnostic values

All values have exact finite JSON projections. `to_json()` returns an
independently frozen plain mapping; `from_json()` rejects missing, extra, or
wrongly typed fields.

```text
QuestionCase
  case_id: str
  enquiry: str
  expected_answer: str
  format_hint: JsonValue | null
  metadata: JsonObject

DiagnosisCase
  question: QuestionCase
  actual_answer: str
  precomputed_verdict: bool | null

DiagnosisDetail
  mistake: str
  reason: str
  minimal_fix: str
```

`QuestionCase` and `DiagnosisCase` are distinct because asking a fresh question
and diagnosing an existing answer are different entry contracts.

The standard result uses the core envelope:

```text
RunResult
  outcome: "equal" | "different"
  value:
    case_id: str
    actual_answer: str
    expected_answer: str
    decided_by: "evaluator" | "llm"
    detail: DiagnosisDetail | null
```

`detail` is null exactly for `equal` and required for `different`. RunResult is
the only completion-result authority; there is no public DiagnosisResult type.
Structured reports use canonical JSON text, which an evaluator may parse.

## Standard child: `DiagnoseAnswer`

Purpose: diagnose a supplied answer, such as an accepted parent response.

Charter: one `DiagnosisCase`.

```text
route: pure Action reads precomputed_verdict
  true  -> complete-equal-evaluator
  false -> explain
  null  -> compare

compare: Prompt
  instructions: decide whether actual and expected are semantically the same;
                reply with explicit yes or no
  data: enquiry, actual_answer, expected_answer, format_hint, metadata
  yes -> complete-equal-llm
  no  -> explain

explain: Prompt
  instructions: explain the difference as separate fields:
                mistake, reason, minimal_fix to the governing instructions
  data: enquiry, actual_answer, expected_answer, format_hint, metadata
  -> complete-different

complete-*: Done(RunResult)
```

A true mechanical verdict completes without another LLM turn. A false verdict
reveals gold and requests only the three-part diagnosis. Without an evaluator,
semantic equality and diagnostic explanation are separate accepted turns.

The fixed three-field response deliberately supports instruction debugging. A
different review rubric is a custom child Rutter.

## Standard child: `AskAndDiagnose`

Purpose: ask a fresh question and reuse `DiagnoseAnswer`.

Charter: one `QuestionCase`.

```text
ask: Prompt
  instructions: answer the enquiry using the optional format hint
  data: enquiry, format_hint, metadata
  AnswerSpec accepts only outcome "answered" with evidence {"answer": str}
  -> diagnose

diagnose: Call(
  child=DiagnoseAnswer,
  charter=verify latest Turn is ask/answered; use evidence["answer"] as
          actual_answer; apply evaluator; validate DiagnosisCase JSON,
  then="complete",
)

complete: Done(forward exact child RunResult)
```

There is no prepare state. The evaluator contract is:

```python
evaluator: Callable[[str, str, StateContext], bool] | None = None
```

The library default has `evaluator=None`. An application needing mechanical
evaluation defines a concrete no-argument subclass with its own identity and
version:

```python
class InventoryAskAndDiagnose(AskAndDiagnose):
    rutter_id = "inventory-ask-and-diagnose"
    definition_version = 1

    @staticmethod
    def evaluator(actual_answer, expected_answer, context):
        return semantically_same_inventory(actual_answer, expected_answer)
```

The evaluator result must have exact type `bool`; merely truthy results fault.
The callback is definition policy and never enters a Charter. There is no
generated class factory or evaluator registry.

## Why there are two diagnostic children

- `DiagnoseAnswer` handles an answer that already exists.
- `AskAndDiagnose` adds one visible Prompt and one visible Call.
- Each graph has one entry contract and is independently understandable.
- One mode-bearing child would hide the difference and add conditional states.

## CaseMaker constructor 1: `diagnose_answer_on`

Diagnose an answer already carried by a matched edge:

```python
diagnose_answer_on(
    *,
    id: str,
    on: EdgeMatch,
    question: QuestionCase | Callable[[EdgeContext], QuestionCase],
    actual_answer: Callable[[EdgeContext], str],
    evaluator: Callable[[str, str, EdgeContext], bool] | None = None,
) -> CaseMaker
```

The pure provider resolves the question and actual answer, applies the optional
evaluator, requires an exact Boolean, and seals one `DiagnosisCase` Charter. It
fixes `child=DiagnoseAnswer`.

Extraction or evaluation failure preserves the accepted edge record and faults
before the selected target is entered.

## CaseMaker constructor 2: `ask_and_diagnose_on`

Ask and diagnose one fresh question on a matched edge:

```python
ask_and_diagnose_on(
    *,
    id: str,
    on: EdgeMatch,
    question: QuestionCase | Callable[[EdgeContext], QuestionCase],
    child: type[AskAndDiagnose] = AskAndDiagnose,
) -> CaseMaker
```

The child may be an explicit application-owned evaluator subclass. Use this
constructor when the hook should initiate the question; use
`diagnose_answer_on` when the parent edge already contains the answer.

## CaseMaker constructor 3: `case_sequence_after`

Attach a finite sequence of arbitrary children after selected states:

```python
case_sequence_after(
    *,
    id: str,
    after_states: Collection[str],
    items: Sequence[JsonObject],
    child: type[Rutter],
    charter: Callable[[JsonObject, EdgeContext], JsonObject] | None = None,
) -> CaseMaker
```

The constructor creates one CaseMaker and therefore contributes at most one
child to any accepted edge; the sequence advances across completed matching
attachments. Other CaseMakers may contribute additional sequential children
when the parent permits multiple selections.

At definition binding it snapshots `after_states` as a frozenset, snapshots
`items` as a tuple, and deeply freezes each item. Empty state or item
collections are definition errors. With `charter=None`, the selected item is
the exact child Charter.

Static items are JSON. A context-dependent sequence uses the optional pure
Charter builder to combine the selected item with the current edge. Callback
behavior is owned by the parent definition version; only resolved finite JSON
enters persistence.

The pure provider is conceptually:

```text
if edge.source not in after_states:
    decline

index = len(anchored_history.attached_calls(case_maker_id=id))
if index > len(items):
    fault with stable history-inconsistency category
if index == len(items):
    decline

selected = items[index]
return selected if charter is absent else charter(selected, edge_context)
```

No schedule index is persisted or injected into the child Charter. One
attachment is identified by CaseMaker ID plus accepted edge ID. Sequence
position is derived from ordered completed attached calls.

A crash during the child resumes the same sealed Charter. Only atomic child
return appends the attached CallRecord, so a later matching edge advances by
exactly one item. An explicit Call with the same site name cannot advance the
sequence because `attached_calls` excludes it.

## History support for hooks

The sole attachment-specific query is:

```python
history.attached_calls(
    case_maker_id: str | None = None,
    edge_id: str | None = None,
) -> tuple[CallRecordView, ...]
```

Sequence helpers count by CaseMaker ID in the anchored callback history, so
attachments on the current edge do not advance the sequence during recovery.
A later ledger Action sees full history, filters by maker and edge, and requires
exactly one record. Duplicate maker/edge results are persisted-state
inconsistencies, never “first match wins.”

## External ledgers

The CompletedRun already contains the submitted answer, equality turn when
needed, diagnostic turn when needed, and terminal RunResult. External ledger
publication remains a visible application Action after the frozen edge resumes.

The library provides no generic recorder Action: it cannot safely choose a
sink, path, effect-recovery policy, successor, or historical attachment for
every application. An application Action filters `attached_calls` by exact
maker and edge and uses its own idempotency policy.

## Constructor validation and versioning

- Binding validates stable IDs, registered child definitions, nonempty finite
  configuration, exact JSON projections, and callable signatures.
- Resolved Charter output is revalidated as finite JSON before sealing.
- Concrete standard children own library IDs and versions.
- An evaluator-bearing subclass owns an application ID and version and has a
  no-argument constructor.
- Changing questions, gold, evaluators, selected states, item order, Charter
  construction, or metadata semantics requires an owner-version change.
- Provider or evaluator failure preserves accepted parent work.
- Child faults retain the complete recursive parent/child path and fault the
  voyage.

## Library non-goals

- a workflow/combinator DSL;
- generated Rutter classes or serialized callables;
- mutable counters, skipping, cycling, or resetting schedules;
- concurrent attached-child execution;
- hidden routing from a hook child;
- initialization or rejected-answer hooks;
- hooks during rendering, validation, Action execution, effect recovery, or
  fault handling;
- a generic ledger sink or service locator; and
- standard review rubrics beyond the demonstrated instruction diagnosis.
