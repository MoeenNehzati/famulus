# Node-entry lifecycle integration audit

Status: passed after correction.

Scope: the five normative documents in `rutter-design/`, checked against
`node-entry-lifecycle-simplification.md`.

## Corrections found by the first audit

- identify each entrance occurrence, not only its state ID, so self-loops are
  recoverable;
- reconstruct routing and CaseMaker pools from the immutable history prefix
  before the accepted source record;
- keep same-edge completion history private to engine skip logic;
- persist child call identity and separate atomic child return from later
  routing and target entrance;
- distinguish the first actually entered child/target from a dry-run preview of
  the parent-edge target;
- define the four-method argument and stopping protocol by node kind;
- make effectful `PythonInstruction.run()` recovery-owned; and
- mark non-repeat-safe work uncertain before invoking it.

## Final audit result

- Hook/replay audit: pass. Inventory sequence replay selects the same case on
  recovery, skips its completed maker/edge identity, and advances only on the
  next accepted report edge.
- Author-interface audit: pass. `get_instruction`, `validate`, `next`, and
  `get_current_node` have explicit Prompt, Action, Call, Done, terminal, fault,
  uncertainty, continuation, and dry-run behavior.
- Runtime/recovery audit: pass. Unique entrance identities, recursive children,
  child return, self-loops, Prompt materialization failure, and effect crash
  windows have unambiguous durable authority.

No validation, evaluation, transition, hook, queue, or return phase was added
to persisted control state. The only control coordinate remains the current
node entrance of each recursively active Rutter.
