# Audit a Behavioral Source

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

Return `needs-context` when an interface result or the smallest necessary
source evidence is absent. Reject when any interface rejected or the source
composition is materially inaccurate.

## Result

Return exactly these sections:

- `Subject`: canonical behavioral-source id and version.
- `Verdict`: `pass`, `reject`, or `needs-context`.
- `Interface results`: interface ids, versions, and verdicts consumed.
- `Evidence`: source declarations, remainder content, behavior, changes, and
  dependencies actually examined.
- `Findings`: source-level mismatches or `none`.
- `Requested context`: the smallest required expansion or `none`.

Do not sign, write certificate history, or claim that the parent module is
certified.
