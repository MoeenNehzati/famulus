# Audit a Source Interface

Audit only the assigned task from its supplied versioned packet. Do not recursively
audit, schedule, or delegate dependencies. If required dependency evidence is
missing, inconsistent, or cannot be evaluated, return `verdict: "abort"`. Do
not modify or certify repository state.

Audit one source interface against its current declaration and observed
behavior. Changed files are evidence for this judgment, not independent
certification subjects.

For a non-process Markdown interface, observed behavior means instruction and
prompt behavior evidenced by its selected content; do not invent process
execution.

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

Read the interface declaration, contract, gateway, binding, selected content,
and exact direct interface dependencies. Read supplied file changes and
dependency changes only as evidence about that interface. Treat any supplied
source or module boundary constraints as limits to check against, not facts for
this audit to establish. If those inputs do not establish the interface-owned
facts, return `abort`.

## Audit

Establish that:

- every operation and argument is independently usable as declared and any
  process binding accepts exactly the documented invocation;
- outputs, outcomes, errors, lifecycle, interaction mode, and verification
  match observed behavior;
- reads, writes, network access, effects, and helpers declared by the interface
  are complete, mutually consistent, and stay within supplied boundaries;
- sensitivity, preconditions, caller warnings, and version compatibility are
  accurate; and
- every implementation or instruction dependency needed by this interface is
  represented by its selected content, binding, or direct interface uses.

Do not audit source-wide platform support or runtime dependencies, and do not
establish module authority or protected-file ownership here. The behavioral-
source and module audits own those judgments.

Reject the interface when the contract is inaccurate or material behavior is
not represented. Do not infer correctness from schema validity or unchanged
neighboring facets.

## Result

Return exactly one `skill-certifier.semantic-audit-result/v1` JSON object and no
surrounding prose. Use the assigned task ID; set `verdict` to `pass`, `reject`,
or `abort`; list evidence strings and direct passing dependency results actually
consumed, using exactly the task IDs in `prerequisite_reports` (reusable
certificates have no report task ID); use an empty `findings` array only for `pass`.

Do not sign, write certificate history, or claim that the containing source or
module is certified.
