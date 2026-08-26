# Functional breakdown

Read the complete input Markdown and compute a recursive, cycle-safe closure of
every referenced instruction, schema, standard, template, asset, and interface
that can change behavior. Write `01_breakdown.md` inside the sibling
distillation workspace.

Build the context closure to a fixed point. Start with the input and resolve
every behavior-defining reference it exposes; add each newly resolved item to
the worklist and continue until no new reference remains. Identify items by a
canonical repository-relative identity and record visited identities before
following their references, so a cycle terminates while every discovered item
is still recorded. A missing or unreadable normative reference is unresolved;
do not omit it or proceed as though the closure were complete.

For every closure item, record its exact path, `availability`, authority,
provenance, why it is behavior-defining, and resolution. A present item uses
`availability: present` and its 64-hex raw-byte digest, including a present
item whose resolution is `conflict`. A missing item uses
`availability: missing`; an unreadable item uses `availability: unreadable`.
Both use `digest: null` and `resolution: unresolved`, because neither provides
readable raw bytes to hash. Authority is `normative` or `informative`, and
provenance is `source` or `generated projection`. Give each normative item a
stable obligation ID derived from its behavioral identity rather than from
traversal order. A generated projection may expose normative behavior, but it
must name its governing source, and that governing source must itself appear as
a present, resolved, normative closure row. Every present closure path is an
implicit governed dependency: it must remain repository-contained and its
recorded digest must equal the SHA-256 of its exact raw bytes whenever this
artifact or any descendant is accepted. Do not follow absolute paths,
parent-directory locators, or symlinks that escape the repository.

Resolve disagreements by source authority, not by generated status: a governing
source decides the authority of its generated projection. If independent source
authorities contradict each other and no declared precedence resolves them,
record the conflict for user resolution. `breakdown-ready` is impossible while
any normative reference is unresolved or any authority conflict remains; write
`breakdown-gap` instead.

Begin the artifact with this envelope, replacing placeholders with exact
repository-relative paths and raw-byte SHA-256 values:

```yaml
schema_version: distill-to-rutters/v1
stage: breakdown
outcome: <breakdown-ready|breakdown-gap|partial|failed>
prerequisites:
  - kind: source
    path: <source.md>
    sha256: <source-digest>
body_schema: breakdown/v1
```

The only allowed outcomes are `breakdown-ready`, `breakdown-gap`, `partial`,
and `failed`. Include exactly one fenced `distill-contract` YAML block. Its
`context_closure` rows record `obligation_id`, `path`, `availability`,
`digest`, `authority`, `provenance`, `why_behavior_defining`, `resolution`,
and, for a generated projection, its `governing_source`. Also record
`conflicts` and `parts`; each part names its obligation IDs, independence
verdict, and reason.

`breakdown-ready` is permitted only when every closure row is present and
resolved, every recorded raw-byte digest matches, every generated normative
row closes against its governing-source row, and no authority conflict remains.
Missing, unreadable, unresolved, or conflicting rows may be recorded only with
`breakdown-gap`; they can never accompany `breakdown-ready`. A part is
independent only when an immutable
Charter can describe its work and it can complete without hidden mutable exchange
with another part. Parts that are not behaviorally independent must
remain in one Rutter. Shared logic may share a Rutter only when its state and
transition semantics are identical. Do not design evolutions yet.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. The gateway
computes the SHA-256 of the complete stored bytes. Report that gateway-computed
digest and ask the user to validate the exact `(path, digest, outcome)` tuple.
