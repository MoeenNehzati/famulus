# Finalize the Compass entrypoint

Require accepted validation of `06_implementation_report.md`. Regenerate and
check every approved derived projection before creating the entrypoint. Resolve
the exact public Compass binding from its live contract; do not guess an
interface/version pair.

Write the final `<source-stem>_distilled.md` candidate beside the original,
then write `07_entrypoint.md` in the distillation workspace. Do not mutate the
candidate after recording its digest. Begin `07_entrypoint.md` with:

```yaml
schema_version: distill-to-rutters/v1
stage: finalize
outcome: <entrypoint-ready|entrypoint-gap|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/06_implementation_report.md
    sha256: <approved-implementation-digest>
    stage: implement
    schema_version: distill-to-rutters/v1
  - kind: deliverable
    path: <source-stem>_distilled.md
    sha256: <candidate-digest>
body_schema: entrypoint/v1
```

The only allowed outcomes are `entrypoint-ready`, `entrypoint-gap`, `partial`,
and `failed`. Include exactly one fenced `distill-contract` YAML block with
`entrypoint_binding`: candidate path and digest, exact public binding, source
outcome, and gateway interpretation. Use `entrypoint-gap` when the live handoff
cannot represent a required authorization or interaction boundary.

For `entrypoint-ready`, the predecessor must be `implemented`, the candidate
must be the exact repository-contained `<source-stem>_distilled.md` sibling of
the common root source, and the body candidate path and digest must equal the
envelope's single contained `deliverable` prerequisite path and digest exactly.
No alternate leaf, alias, or cross-run candidate can satisfy this identity.

Do not compute or embed this artifact's own digest. Return its path and typed
outcome to the gateway. Report the gateway-computed raw-byte SHA-256 and ask
the user to validate the exact `(path, digest, outcome)` tuple.
