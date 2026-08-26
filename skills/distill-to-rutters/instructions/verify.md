# Verify the exact distilled delivery

Require accepted validation of `07_entrypoint.md`. Recheck its candidate path,
candidate digest, recursive prerequisite freshness, exact implementation diff,
and live public interfaces. Invoke that exact candidate through the public
Compass route and write `08_verification.md` in the distillation workspace.

Begin the artifact with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: verify
outcome: <verified|verification-failed|verification-blocked|partial>
prerequisites:
  - kind: artifact
    path: <workspace>/07_entrypoint.md
    sha256: <approved-entrypoint-artifact-digest>
    stage: finalize
    schema_version: distill-to-rutters/v1
  - kind: deliverable
    path: <source-stem>_distilled.md
    sha256: <approved-candidate-digest>
body_schema: verification/v1
```

The only allowed outcomes are `verified`, `verification-failed`,
`verification-blocked`, and `partial`. Include exactly one fenced
`distill-contract` YAML block with candidate path and digest,
`verification_evidence`, and `semantic_traces`. Each evidence row records the
exact command, passed/failed/blocked result, product/environment classification,
and observable evidence. Each semantic trace records positive and negative
results for one obligation.

For `verified`, the predecessor must be `entrypoint-ready`, the verification
envelope's single contained deliverable leaf must equal that approved
entrypoint's candidate path and digest, and the verification body must repeat
the same exact path and digest. Every verification evidence row must be
`passed`; a `failed` or `blocked` row requires a non-success outcome.

Run focused contracts, every distinct transition and validator, terminal
results, orchestration, public dispenser and Compass entrypoints, blueprint
checks, and relevant repository validators. Terminal completion proves only
orchestration; verify semantic outputs separately. After writing the report,
rerun all check-only gates and invoke the exact candidate as the final
operation. Any later target-closure mutation invalidates verification.

Keep three acceptance layers distinct in the evidence:

1. structural instruction contracts check routing, required fields, stage
   ordering, and the public/private boundary;
2. the production artifact parser, digest chain, outcome registry, and route
   decision check deterministic artifact behavior directly; and
3. fixture-specific obligation and trace oracles compare checked-in known-good
   and independently mutated contract bodies with hand-authored literals.

The fixture-specific oracles do not execute a Rutter and do not prove arbitrary
semantic equivalence. Keep any live agent/user comparison separate from pytest;
it is user-adjudicated acceptance evidence, not a deterministic semantic
oracle. Do not describe parsed positive or negative traces as runtime
execution.

Do not compute or embed this artifact's own digest. Return its path and typed
outcome to the gateway. Report the gateway-computed raw-byte SHA-256 and ask
the user to validate the exact `(path, digest, outcome)` tuple.
