# Audit a Behavioral Source

Audit only the assigned task from its supplied versioned packet. Do not recursively
audit, schedule, or delegate dependencies. If required dependency evidence is
missing, inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do
not modify or certify repository state.

Audit one behavioral source after its required interface audits have completed.
The source judgment covers the gateway, source-wide declarations, remainder
content, direct source dependencies, and integration of its interfaces.

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

Read the current source blueprint, gateway, source-owned remainder content,
direct dependencies, and interface audit results. Changed files are evidence
for the owning interface or source remainder; they are not separate audit
subjects. Consume supplied interface audit results and machine-authenticated
unchanged facet evidence from the latest valid payload-v3 certificate.
The whole-node semantic-review pass covers included unchanged facets; this is
not a separately signed per-facet semantic attestation. Evaluate the contracts
and their composition without rechecking signatures, hashes or currentness.

## Audit

Establish that:

- the gateway and all directly owned files are represented by source content;
- source-wide dependencies, platform support, and runtime dependencies are
  accurate for behavior not confined to one interface;
- the audited and reused interface contracts remain mutually consistent;
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
consumed, using exactly the task IDs in `prerequisite_reports` (reusable
certificates have no report task ID); use an empty `findings` array only for `pass`.

Do not sign, write certificate history, or claim that the parent module is
certified.
