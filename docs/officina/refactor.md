# Refactoring Officina Nodes

> **Status:** Informative.
>
> This document explains the available refactoring tools and the problems they
> solve. Their live skill interfaces own operational instructions.

Officina makes repository architecture explicit. A registered node is not just
a collection of files: its blueprint describes its identity, content,
ownership, gateway, dependencies, and interfaces. This makes architectural
changes inspectable and mechanically checkable.

The same structure makes ordinary refactoring tools insufficient. Moving a
directory may change node identities, ownership, imports, callers, prose
references, and generated views. Editing a node in place may leave its behavior
intact while violating an applicable standard or an ownership boundary. What
looks like a local change in the filesystem can therefore become a graph-wide
change in the repository.

Officina separates these problems between two tools:

- [`refactor-node`](../../skills/refactor-node/) changes how a registered node
  or one of its owned sources is organized while preserving what it does.
- [`relocate-nodes`](../../skills/relocate-nodes/) changes where registered
  nodes or owned files live while preserving behavior and updating their
  architectural addresses.

The distinction is the invariant under change: `refactor-node` preserves
behavior while implementation structure changes; `relocate-nodes` preserves
behavior while location changes.

## 1. Choosing the tool

| Problem | Tool |
| --- | --- |
| Audit or improve instructions or Python inside an existing registered scope | `refactor-node` |
| Move a registered node or owned file to a new path | `relocate-nodes` |
| Add behavior, fix a behavioral defect, or redesign a public interface | Neither; use the workflow that owns that change |

A task that needs both semantic refactoring and relocation contains two
different changes. Keep them distinct so that each preservation claim can be
reviewed and verified. Do not hide a behavioral change inside an address
rewrite.

## 2. Refactoring a node in place

`refactor-node` solves the problem of improving a registered node without
changing its observable behavior. It applies to a whole node or to a narrower
owned source, such as an instruction source or Python implementation.

The workflow begins with architectural scope rather than a filename. It
identifies the affected node role and gateway, retrieves the applicable
repository standards, and establishes what must remain unchanged. That
preservation map includes relevant behavior, dependencies, authorization,
callers, outcomes, and approval boundaries.

Only then does the workflow propose a refactoring. Changes are approved and
applied one move at a time, checked against the preservation map, and verified
before the next move. This prevents a broad cleanup from silently becoming
feature work, an API redesign, or a cross-node edit.

Use `refactor-node` when the registered address is staying in place and the
question is whether the node can be made clearer, simpler, or better aligned
with its standards without changing what consumers observe.

## 3. Relocating registered content

`relocate-nodes` solves the mechanical and semantic consequences of changing a
registered address. A filesystem move alone is not enough because blueprints
turn paths into architectural facts and other artifacts may refer to those
facts.

A relocation can affect:

1. physical paths and node or source identities;
2. blueprint ownership, content, gateways, and declared relationships;
3. imports, callers, and generated artifacts; and
4. remaining old-address occurrences in instructions, documentation,
   configuration, or other text.

The first three categories can often be derived mechanically. The fourth
cannot: an old address may be an active reference that must change, or a
historical statement that must remain. The tool therefore combines a
mechanical transaction with a bounded semantic review.

The workflow has four phases:

1. **Preflight.** Describe complete physical moves in one manifest and produce
   a read-only report of every planned change and old-address occurrence.
2. **Review.** Let the user accept the default rewrites, preserve irrelevant or
   historical occurrences, and refine prose that a mechanical replacement
   would make awkward.
3. **Apply.** Publish the reviewed move and its dependent changes once, through
   a recovery-backed failure-atomic transaction.
4. **Postflight.** Run the same manifest without applying it and require an
   empty target-side plan with no unaccounted occurrences or errors.

The relocation tool does not infer new behavior, authority, compatibility
policy, or public interfaces from a path change. It also does not perform
certification or installation. Those remain separate concerns after the move.

## 4. Why the separation matters

Blueprints make Officina maintainable by replacing implicit architecture with
declared facts. The cost is that moving registered content becomes more
involved: every affected fact must move coherently, and every ambiguous textual
reference must be adjudicated.

`relocate-nodes` absorbs that mechanical complexity without pretending that
all prose can be understood mechanically. `refactor-node` handles the separate
problem of changing implementation structure under explicit standards and a
behavior-preservation contract. Keeping the tools separate makes it clear what
changed, what was preserved, and what evidence is required.

For exact invocation, inputs, and approval requirements, follow the live
[`refactor-node`](../../skills/refactor-node/) and
[`relocate-nodes`](../../skills/relocate-nodes/) skills.
