# Validate source logic coverage

Read the input and all three approved artifacts. Write
`04_logic_validation.md` in the distillation workspace.

Begin the artifact with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: validate-logic
outcome: <logic-captured|logic-gap|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/03_evolutions_and_transitions.md
    sha256: <approved-graph-digest>
    stage: extract-evolutions
    schema_version: distill-to-rutters/v1
body_schema: logic-validation/v1
```

The only allowed outcomes are `logic-captured`, `logic-gap`, `partial`, and
`failed`. Include exactly one fenced `distill-contract` YAML block whose
`enforcement_matrix` maps every normative obligation to: original decision
owner, automation permission, public runtime capability and version,
public binding-contract version, capability verification, any explicit
capability gap and repair, owning evolution, exact mechanism, precondition,
postcondition, failure result, observable evidence, positive trace, and
negative trace.

`logic-captured` is permitted only when every obligation has a mechanism
verified against the live public API in the repository containing the
artifacts. Resolve the public Rutter root blueprint and require its real path to
remain inside the artifact repository before reading it. Use the exact public
export ID and integer interface version, resolve its repository-relative source
blueprint without accepting absolute paths, parent traversal, or symlink
escape, and verify every claimed operation against the interface's declared
operation vocabulary. The versioned public interface
contract must also structurally expose the request-owner, evidence, validator,
owning-evolution, transition-authority, and successor bindings claimed by the
row. Matching strings in the artifact are not proof. If the current public
contract exposes only operation vocabulary, the truthful outcome is
`logic-gap`. A self-asserted `capability_verified: true` does not replace these
checks.

Use one matrix row for every normative obligation in the approved context
closure, with no duplicates or extras, and require the graph to cover that same
set exactly. Parse the complete approved assignment orchestration body and
require every coordinator rule's obligation and owning transition to exist in
the graph under its named coordinator evolution. Identify each owner as
`<rutter_id>/<evolution_id>`. Encode
`exact_mechanism` as:

```yaml
enforcement_class: rutter-state-transition
request:  # null only for a Rutter-owned deterministic decision
  operation: get-status
  owner: <human|llm|external>
  owner_ref: <public owner-result binding>
  evidence_ref: <public request-evidence binding>
validation:
  operation: validate
  input_ref: <public validation-input binding>
  evidence_ref: <public validation-evidence binding>
  validator_ref: <validator named by the owning evolution>
  validator_binding_ref: <public validator binding>
  output_ref: <public validation-output binding>
transition:
  operation: advance
  input_ref: <public transition-input binding>
  evidence_ref: <public transition-evidence binding>
  authority: owning-rutter-evolution
  owning_evolution_ref: <public owning-evolution binding>
  authority_ref: <public transition-authority binding>
  successor_ref: <public successor binding>
```

Encode evidence and traces as capability-level objects, not prose. Observable
evidence names the validation operation, evidence reference, and output
reference. A positive trace names the transition operation, input and evidence
references, one graph outcome, and an expected successor object whose
`result_ref` exactly matches the public transition `successor_ref`. A negative
trace names the validation operation, input and evidence references, the
rejected case, and an expected `rejection_code` declared by the public
validation binding's result contract. Reject arbitrary codes. These are
structural consistency claims; do not describe them as proof of live execution
when no runtime was executed.

For human, LLM, or external judgment, require
`automation_permission: request-owner-decision`; the request must name the same
actor, the validator must check that actor's evidence, and only the owning
evolution may authorize the successor. Deterministic automation cannot
substitute the answer. Prompt wording, an operation name without this binding,
wrapper prose, and schema shape alone are not enforcement. A preserved wrapper
requirement is `wrapper-constraint`, not an enforcement class; without a public
mechanism that requests and observes compliance, use `logic-gap`.

Missing or mismatched owner, evidence, validator, owning-evolution, authority,
or successor bindings; automated owner judgment; unowned coordinator
decisions; incomplete obligation coverage; and absent interface versions,
binding-contract versions, or operations are `logic-gap`. Name the exact graph
or public-contract repair; do not revise the graph in this stage. A gap artifact
must use `capability_verified: false`, record the absent binding contract and
exact repair in `capability_gap`, and classify operation vocabulary alone as
`operation-name-only`; it must not simultaneously claim verified
`rutter-state-transition` enforcement. Any row containing `capability_gap` is
therefore invalid under `logic-captured`.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
