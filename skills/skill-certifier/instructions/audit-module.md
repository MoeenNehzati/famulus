# Audit a Module

Audit only the assigned task from its supplied versioned packet. Do not recursively
audit, schedule, or delegate dependencies. If required dependency evidence is
missing, inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do
not modify or certify repository state.

Audit one module after its affected or required child nodes have been reviewed.
The module judgment covers its declaration, directly owned content, exports,
namespace authority, and composition of already-audited child nodes.

## Required input

The packet binds the reviewed repository and commit, audited input identity,
selected declaration and input manifest, and prerequisite reports or reusable
certificate evidence. Use `prerequisite_declarations` for dependency contracts
and composition; consume these inline declarations without opening prerequisite
blueprints or certificate logs. They contain no child implementation. Read only the selected content needed for your judgment.
Treat authentication, schema checks, canonical hashes, version pins, graph
membership, and prerequisite pass/currentness as machine-checked facts. Do not
repeat those checks or request additional tasks. Judge what the supplied
evidence means for this subject; return `abort` if semantic evidence is inadequate.

Read the current module blueprint, module-owned content, child registrations,
exports, namespace routes, authority declarations, and child audit results.
Do not inspect child implementation content. Consume the bounded passing result
or reusable certificate evidence supplied for each direct child. Return
`abort` when that evidence cannot establish the required composition facts.

## Audit

Establish that:

- the registered children express the intended module decomposition;
- every export binds the intended intrinsic interface without copying its
  contract or widening its access;
- namespace routes stay within registered-child and authorization ceilings;
- directly owned module content and discovery metadata describe the module
  accurately;
- authority and filesystem ownership are complete and do not duplicate child
  declarations; and
- the combined module surface is coherent given the supplied child evidence.

Return `abort` for a missing child result or unresolved module evidence. Reject
invalid exports, authority, topology, or composition.

## Result

Return exactly one `skill-certifier.semantic-audit-result/v1` JSON object and no
surrounding prose. Use the assigned task ID; set `verdict` to `pass`, `reject`,
or `abort`; list evidence strings and direct passing dependency results actually
consumed, using exactly the task IDs in `prerequisite_reports` (reusable
certificates have no report task ID); use an empty `findings` array only for `pass`.

Use this complete object shape, replacing the example values with your judgment:

```json
{
  "schema_version": "skill-certifier.semantic-audit-result/v1",
  "task_id": "ASSIGNED_TASK_ID",
  "verdict": "pass",
  "summary": "Nonempty assessment summary.",
  "evidence": ["Evidence supporting the assessment."],
  "consumed_dependencies": [],
  "findings": []
}
```

Include all seven keys and no others. Each consumed dependency is exactly
`{"task_id": "PREREQUISITE_REPORT_TASK_ID", "verdict": "pass"}`; use `[]` when
there are no prerequisite reports. `evidence` and `findings` contain strings;
`reject` and `abort` require at least one finding.

Do not sign or write certificate history. The machine consumes a `pass` result before
exact-node certification; it is not itself a signed
certificate.
