# Audit a Source Interface

Audit one source interface against its current declaration and observed
behavior. Changed files are evidence for this judgment, not independent
certification subjects.

For a non-process Markdown interface, observed behavior means instruction and
prompt behavior evidenced by its selected content; do not invent process
execution.

## Required input

Read the interface declaration, contract, gateway, binding, selected content,
and exact direct interface dependencies. Read supplied file changes and
dependency changes only as evidence about that interface. Treat any supplied
source or module boundary constraints as limits to check against, not facts for
this audit to establish. If those inputs do not establish the interface-owned
facts, return `needs-context` and name the smallest additional evidence or
context scope required. That scope may include source or module context without
transferring the higher-level audit's responsibility.

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

Return exactly these sections:

- `Subject`: canonical interface id and version.
- `Verdict`: `pass`, `reject`, or `needs-context`.
- `Evidence`: declarations, content, behavior, changes, and dependency results
  actually examined.
- `Findings`: contract mismatches or `none`.
- `Requested context`: the smallest required expansion or `none`.

Do not sign, write certificate history, or claim that the containing source or
module is certified.
