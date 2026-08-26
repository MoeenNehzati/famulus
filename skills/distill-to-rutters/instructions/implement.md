# Implement the approved Rutters

Require accepted validation of `05_implementation_design.md`. Re-read the live
target and confirm its relevant files and public APIs still match the approved
design. Write exactly the approved implementation, declarations,
registrations, and focused tests, then write `06_implementation_report.md`.

Begin the report with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: implement
outcome: <implemented|implementation-gap|implementation-blocked|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/05_implementation_design.md
    sha256: <approved-design-digest>
    stage: design-implementation
    schema_version: distill-to-rutters/v1
body_schema: implementation-report/v1
```

The only allowed outcomes are `implemented`, `implementation-gap`,
`implementation-blocked`, `partial`, and `failed`. Include exactly one fenced
`distill-contract` YAML block with `implementation_trace_map`, `changed_files`,
and `limitations`. Every trace row maps an obligation and design item to an
implemented symbol, repository path, evidence, and status.

Use `implemented` only when the approved predecessor is `design-ready` and
every implementation trace row has status `implemented`. A `gap`, `blocked`,
`partial`, or `failed` trace row requires the corresponding non-success report
outcome and cannot authorize finalization.

Keep the concrete graph transparent in the primary implementation unit and
complex mechanics in support. Support may not choose an unauthorized route.
Do not run finalization or verification in this stage.

Do not compute or embed this artifact's own digest. Return the report path and
typed outcome to the gateway. Report the gateway-computed raw-byte SHA-256 and
ask the user to validate the exact `(path, digest, outcome)` tuple.
