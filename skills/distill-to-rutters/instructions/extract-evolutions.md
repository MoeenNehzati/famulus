# Extract evolutions and transitions

Read the input and approved breakdown and assignment artifacts. Write or revise
`03_evolutions_and_transitions.md` in the distillation workspace.

Begin the artifact with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: extract-evolutions
outcome: <graph-ready|graph-gap|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/02_rutter_assignment.md
    sha256: <approved-assignment-digest>
    stage: assign-rutters
    schema_version: distill-to-rutters/v1
body_schema: graph/v1
```

The only allowed outcomes are `graph-ready`, `graph-gap`, `partial`, and
`failed`. Include exactly one fenced `distill-contract` YAML block. Its
`rutters` rows name identity, all assigned Voyage IDs, version, initial evolution, Charter fields,
evolutions, transitions, and terminal results. Every evolution names its type,
obligation IDs, original decision owner, validator, and finite outcomes. Every
declared `(from evolution, outcome)` pair has exactly one successor: reject a
missing successor or duplicate pair, and require every target to name an
evolution in the same Rutter or one of that Rutter's terminal results.

For `graph-ready`, the graph Rutter IDs and Voyage IDs must close exactly
against the approved assignments. Every assignment workflow Voyage reference
must name a graph Voyage. Every assignment and orchestration obligation must be
owned by a graph evolution; in coordinated mode the exact coordinator Rutter
must exist and own every orchestration rule assigned to it. The approved
predecessor must itself be `assignment-ready`.

Represent hooks, child Rutters, loops, retries, faults, stop conditions, and
coordinator decisions in the graph. Human, LLM, or external judgment remains
owned by that actor. For each such decision, name a string-instruction
evolution that requests that actor's answer, a validator that accepts only
evidence of that answer, and outcomes whose transitions are authorized only by
that evolution. Deterministic work may not supply or infer the actor's answer.
Use `<rutter_id>/<evolution_id>` as the stable owning-evolution identity for the
next stage.

Read the approved assignment's complete orchestration body. In coordinated
mode, every rule in starts, dependencies, joins, aggregation, partial failure,
retries, cancellation, failure propagation, authorization, and release names
an obligation and owning transition. Represent each rule as an obligation of
the named coordinator evolution and give that evolution at least one declared
transition. A coordinator decision present only in the assignment is a graph
gap.

Read the current public Rutter interface declarations and their versions while
extracting the graph. An operation label, prompt, wrapper statement, or schema
field is not a runtime mechanism. Record a wrapper requirement as a constraint;
do not turn it into an enforcement evolution unless the public API can request
and observe it. If the graph cannot name a decision owner, evidence validator,
or exclusive transition authority for an obligation, use `graph-gap`. A
correction updates every affected row rather than appending contradictory
prose.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
