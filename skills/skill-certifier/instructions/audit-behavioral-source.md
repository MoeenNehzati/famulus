# Audit a Behavioral Source

Audit only the assigned task from its scheduler input file. Do not recursively
audit, schedule, or delegate dependencies. If required dependency evidence is
missing, inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do
not modify or certify repository state.

Audit one behavioral source after its required interface audits have completed.
The source judgment covers the gateway, source-wide declarations, remainder
content, direct source dependencies, and integration of its interfaces.

## Required input

Read the current source blueprint, gateway, source-owned remainder content,
direct dependencies, and interface audit results. Changed files are evidence
for the owning interface or source remainder; they are not separate audit
subjects. Require current audit results for affected interfaces. For each
unchanged interface, require authenticated unchanged facet evidence from the
latest valid payload-v3 certificate. Its signature authenticates the facet
manifest and dependencies plus the certificate's whole-node semantic-review
pass; selective reuse treats that pass as covering every included unchanged
facet, but it is not a separately signed per-facet semantic attestation. When
valid reusable evidence is unavailable, require current audit results for
every declared interface.

## Audit

Establish that:

- the gateway and all directly owned files are represented by source content;
- source-wide dependencies, platform support, and runtime dependencies are
  accurate for behavior not confined to one interface;
- every affected interface audit passed, reused interfaces have valid unchanged
  evidence, and their contracts remain mutually consistent;
- remainder content does not implement an undeclared interface or conceal an
  undeclared dependency, effect, helper, or authority requirement; and
- the source description and behavior agree with the combined interface and
  remainder evidence.

Return `abort` when an interface result or necessary source evidence is absent.
Reject when any interface rejected or the source composition is materially
inaccurate.

## Result

Return exactly one `skill-certifier.semantic-audit-result/v1` JSON object and no
surrounding prose. Use the assigned task ID; set `verdict` to `pass`, `reject`,
or `abort`; list evidence strings and direct passing dependency results actually
consumed; use an empty `findings` array only for `pass`.

Do not sign, write certificate history, or claim that the parent module is
certified.
