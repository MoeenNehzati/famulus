# Assign Rutters and Voyages

Read the input and approved `01_breakdown.md`. Write
`02_rutter_assignment.md` in the same workspace.

Begin the artifact with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: assign-rutters
outcome: <assignment-ready|assignment-gap|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/01_breakdown.md
    sha256: <approved-breakdown-digest>
    stage: breakdown
    schema_version: distill-to-rutters/v1
body_schema: assignment/v1
```

The only allowed outcomes are `assignment-ready`, `assignment-gap`, `partial`,
and `failed`. Include exactly one fenced `distill-contract` YAML block with
`assignments` and `orchestration`. Every assignment records the part, Voyage,
concrete Rutter definition, Charter fields, inputs, and outputs. Reuse a
definition only when state and transition semantics are identical.

For `assignment-ready`, the assignment `part_id` values must equal the approved
breakdown part IDs exactly, with no omission, duplicate, or extra row. The
approved predecessor must itself be `breakdown-ready`; a syntactically valid
gap, partial, or failed breakdown cannot authorize assignment.

Keep inseparable parts in one Rutter. Independent workflows may use multiple
Voyages only with an explicit join. For multiple Voyages, use coordinated
orchestration and name a coordinator Rutter. The coordinator owns starts,
dependencies, joins, aggregate results, retries, cancellation, failure
propagation, authorization, and release. Its machine rows must cover starts,
dependencies, joins, aggregate results, partial failure, retries, cancellation, failure
propagation, authorization, and release; each row names its obligation, owning
transition, and checked evidence. Map every cross-part obligation to a
coordinator transition and its evidence checked before advancement. Record
partial failure and the owning Rutter for every retry. Preserve the exact
Rutter definition IDs, Voyage IDs, workflow Voyage references, orchestration
obligation IDs, and coordinator ID so the graph stage can close them as
production foreign keys rather than by prose comparison.

The dispenser may mechanically execute an authorized action, but may not choose
ordering, branching, retry, cancellation, join, or release policy. Final-result
validation does not substitute for transition authorization. Account for every
approved part exactly once.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
