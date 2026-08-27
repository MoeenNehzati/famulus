# Audit a Module

Audit only the assigned task from its scheduler input file. Do not recursively
audit, schedule, or delegate dependencies. If required dependency evidence is
missing, inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do
not modify or certify repository state.

Audit one module after its affected or required child nodes have been reviewed.
The module judgment covers its declaration, directly owned content, exports,
namespace authority, and composition of already-audited child nodes.

## Required input

Read the current module blueprint, module-owned content, child registrations,
exports, namespace routes, authority declarations, and child audit results.
Do not inspect child implementation content. Consume the bounded passing result
or reusable certificate evidence supplied for each direct child. Return
`abort` when that evidence cannot establish the required composition facts.

## Audit

Establish that:

- every direct child is registered at the correct version and blueprint;
- every export binds the intended intrinsic interface without copying its
  contract or widening its access;
- namespace routes stay within registered-child and authorization ceilings;
- directly owned module content and discovery metadata describe the module
  accurately;
- authority and filesystem ownership are complete and do not duplicate child
  declarations; and
- every required child audit passed and the combined module surface is
  coherent.

Return `abort` for a missing child result or unresolved module evidence. Reject
invalid exports, authority, topology, or composition.

## Result

Return exactly one `skill-certifier.semantic-audit-result/v1` JSON object and no
surrounding prose. Use the assigned task ID; set `verdict` to `pass`, `reject`,
or `abort`; list evidence strings and direct passing dependency results actually
consumed; use an empty `findings` array only for `pass`.

Do not sign or write certificate history. A `pass` result authorizes the
gateway to request deterministic certification; it is not itself a signed
certificate.
