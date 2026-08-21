# Audit a Module

Audit one module after its affected or required child nodes have been reviewed.
The module judgment covers its declaration, directly owned content, exports,
namespace authority, and composition of already-audited child nodes.

## Required input

Read the current module blueprint, module-owned content, child registrations,
exports, namespace routes, authority declarations, and child audit results.
Read child implementation content only when a child result requests expansion;
otherwise consume the bounded result supplied by that child audit.

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

Return `needs-context` for a missing child result or the smallest unresolved
module evidence. Reject invalid exports, authority, topology, or composition.

## Result

Return exactly these sections:

- `Subject`: canonical module id and version.
- `Verdict`: `pass`, `reject`, or `needs-context`.
- `Child results`: child ids, versions, and verdicts consumed.
- `Evidence`: module declarations, content, routes, authority, changes, and
  child results actually examined.
- `Findings`: module-level mismatches or `none`.
- `Requested context`: the smallest required expansion or `none`.

Do not sign or write certificate history. A `pass` result authorizes the
gateway to request deterministic certification; it is not itself a signed
certificate.
