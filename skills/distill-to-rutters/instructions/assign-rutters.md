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

The first release supports exactly one concrete Rutter and one Voyage. Group
inseparable obligations into that one breakdown part. Use `mode: single`, set
`coordinator_rutter_id` to null, leave every coordinator rule group and
`independent_workflows` empty, and name the sole Rutter as `retry_owner`. If the
approved breakdown genuinely requires independent Voyages or coordinated
policy, write `assignment-gap` and identify multi-Voyage orchestration as an
unsupported first-release capability; do not emit a partial coordinator
contract or claim `assignment-ready`.

The dispenser may mechanically execute an authorized action, but may not choose
ordering, branching, or retry policy outside the sole Rutter. Final-result
validation does not substitute for transition authorization. Account for the
single approved part exactly once.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
