# Rutter examples

These examples illustrate the design without defining it. Core and runtime
semantics are in `01-core-design.md` and `02-runtime-reference.md`; hook and
CaseMaker APIs are in `03-hook-library.md`. Acceptance requirements are in
`05-verification-and-implementation.md`.

## Repeated inventory diagnosis

The parent owns iteration and ledger publication. A transition hook diagnoses
each accepted report before the frozen `report -> record` edge resumes.

```python
class Inventory(Rutter):
    rutter_id = "inventory"
    definition_version = 5
    start_state = "report"

    def define_states(self):
        return {
            "report": Prompt(
                "Report the new nodes and direct dependency edges in this chunk.",
                answer=AnswerSpec({"reported": INVENTORY_FORMAT_HINT}),
                data=self.next_iteration,
                validate=self.validate_report,
                then="record",
            ),
            "record": Action(
                self.write_ledger,
                mode="repeat-safe",
                then={"more": "report", "done": "complete"},
            ),
            "complete": Done(result=self.summary),
        }

    def define_case_makers(self):
        return (
            case_sequence_after(
                id="inventory-diagnosis",
                after_states={"report"},
                items=FROZEN_APPENDIX_GOLD,
                child=DiagnoseAnswer,
                charter=self.inventory_diagnosis_charter,
            ),
        )

    def inventory_diagnosis_charter(self, item, edge):
        actual_answer = canonical_json_text(inventory_answer_from_edge(edge))
        expected_answer = item["expected_answer"]
        verdict = semantically_same_inventory(
            actual_answer, expected_answer, edge
        )
        return DiagnosisCase(
            question=QuestionCase(
                case_id=item["case_id"],
                enquiry=(
                    "Which new nodes and direct dependency edges appear in "
                    "this chunk?"
                ),
                expected_answer=expected_answer,
                format_hint=INVENTORY_FORMAT_HINT,
                metadata={"appendix_iteration": item["case_id"]},
            ),
            actual_answer=actual_answer,
            precomputed_verdict=verdict,
        ).to_json()
```

The evaluator compares semantic entities and edges rather than exact labels. A
correct report returns immediately. A mismatch reveals gold and collects
`mistake`, `reason`, and `minimal_fix`.

The ledger Action selects the diagnosis using:

```python
records = history.attached_calls(
    case_maker_id="inventory-diagnosis",
    edge_id=report_turn.record_id,
)
```

It requires exactly one record and uses the enclosing repeat-safe Action's
`action_id` as its application idempotency key.

### Interaction trace

```text
Inventory.report publishes iteration N
-> report entrance has a unique entry ID
-> LLM report is accepted; Turn binds that entrance and becomes edge ID
-> report/reported/record edge is frozen
-> inventory CaseMaker creates DiagnoseAnswer Charter
-> child compares actual and gold
-> different path collects mistake, reason, minimal_fix
-> completed attached CallRecord binds to report edge ID
-> frozen edge enters Inventory.record
-> repeat-safe Action writes ledger
-> Action routes to next report or complete
-> on this successful path, `next(..., continue_=True)` returns that final
   Prompt or Done node only
```

Gold is not exposed before the ordinary report is accepted. The intermediate
child and Action path is available from history rather than duplicated in the
return value of `next`.

## Fresh diagnostic questions from a list

```python
class TerminologyAskAndDiagnose(AskAndDiagnose):
    rutter_id = "terminology-ask-and-diagnose"
    definition_version = 1

    @staticmethod
    def evaluator(actual_answer, expected_answer, context):
        return terminology_matches(actual_answer, expected_answer)


case_sequence_after(
    id="terminology-checks",
    after_states={"draft", "revise"},
    items=tuple(question.to_json() for question in QUESTIONS),
    child=TerminologyAskAndDiagnose,
)
```

Identity Charter behavior means no callback is needed. Each completed matching
attachment consumes exactly one QuestionCase.

## A non-diagnostic sequence

```python
case_sequence_after(
    id="progressive-previews",
    after_states={"section-complete"},
    items=PREVIEW_CHARTERS,
    child=RenderPreview,
)
```

This uses the same scheduling helper without importing diagnostic data or
behavior.

## One attachment before a shared target

```python
CaseMaker(
    id="pre-publication-audit",
    on=before("publish"),
    child=PublicationAudit,
    charter=self.publication_case,
)
```

The audit runs whether automated or manual review selected `publish`, then the
already-selected publication edge resumes.

## Result-directed approval

```python
"approval": Call(
    child=Approval,
    charter=self.approval_input,
    then={"approved": "publish", "revise": "draft"},
)
```

This is a Call rather than a hook because the result controls routing.

## A child calling a grandchild

```python
class Approval(Rutter):
    start_state = "review"

    def define_states(self):
        return {
            "review": Prompt(..., then="evidence"),
            "evidence": Call(
                child=EvidenceCheck,
                charter=self.evidence_input,
                then={"sound": "complete", "unsound": "reconsider"},
            ),
            "reconsider": Prompt(..., then="complete"),
            "complete": Done(result=self.result),
        }
```

All depths use the same Rutter semantics and one recursive active path.

## A terminal audit

```python
CaseMaker(
    id="final-quality-audit",
    on=after("complete"),
    child=QualityAudit,
    charter=self.final_audit_case,
)
```

Its EdgeContext contains the staged DoneRecord, so it sees the exact immutable
result before root completion or child return.

## Sequence recovery trace

For scheduled item `i` on accepted edge `e`:

1. The parent accepted record is bound to a unique source entrance and remains
   durable while that entrance is current. Pure `then` and the CaseMaker use the
   strict history prefix before the record and derive item `i`.
2. Attaching the initialized child and its sealed Charter commits atomically. A
   crash during the child reopens that recursive child directly.
3. Child Done commits its sole result authority.
4. Atomic return archives and detaches the child and appends the attached
   CallRecord bound to `e`.
5. Recovery uses that same anchored prefix to recompute the same edge and case
   pool. The engine separately sees full history, skips the completed stable
   maker/edge identity, and enters or finalizes the target when none remain.
6. Only after the CallRecord commits does a later matching edge select item
   `i + 1`.

7. Target entrance allocates a new entry ID even when it is a self-loop, proving
   that settlement of the source entrance has finished.

The sequence therefore cannot repeat or skip an item under restart.
