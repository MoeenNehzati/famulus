# Reconcile Proof Entities for Deterministic Normalization

## Goal

Read one bounded proof-centered packet, then write exhaustive proof-normalization decisions. Group complementary proof fragments that present the same argument, preserve genuinely alternative proof bundles, resolve only source-grounded ownership, and exclude registered proof-like prose that does not qualify. This interface makes semantic judgments only; deterministic normalization applies the decisions later.

## Bounded inputs and output

Read the assigned packet and `proof-normalization.schema.json` completely. Require the packet to carry its controller-recorded semantic-IR identity and immutable source identity. The packet contains only registered proof candidates, proposed targets, incident relationships, exact registered source ranges, and necessary neighboring entity identities. It is the complete evidence boundary: do not open the controller's whole transitional semantic IR, rescan the paper, open unregistered ranges, or use adjacency outside the packet.

Write one object with `document_kind: "proof-normalization-decisions"`, `ir_version: 1`, and a `decisions` array that validates against `proof-normalization.schema.json`. Return only the assigned decisions-output path. The runtime, not this interface, produces the normalized semantic IR and provenance report.

Append bounded actual-clock progress lines to the assigned stable progress path using only timestamps, milestone names, and integer counters. A retry reuses the path and appends without needing to read prior lines. Do not put source text, semantic decisions, or reasoning in progress output.

## Registered evidence boundary

Judge only registered proof candidates and incident relationships from the packet. You may not create mathematical entities or dependencies, change a dependency endpoint, invent a proof target, or expand an evidence range. Necessary neighboring ordinary entities establish identity and eligibility; they are not new candidate material.

Every proof entity must be decided exactly once. Its decision is either accepted or excluded. An accepted decision names exactly one existing eligible non-proof result target and exactly one bundle. An excluded decision names neither and gives a source-grounded reason. Do not omit a proof because its disposition is difficult.

If ownership remains ambiguous after inspecting all registered ownership evidence, fail closed rather than guessing. Do not use source proximity or same-section placement as target evidence. The transitional IR may not advance to normalization while a retained proof has zero, multiple, or unresolved targets.

## Inclusion and exclusion

Accept a proof fragment only when it performs substantive inferential work toward its target, has a separable registered span, and preserves ownership or dependency evidence. Explicit proof environments, unwrapped informal arguments, and sketches may all qualify.

Exclude motivation, restatement, navigation, and local algebra that does not independently constitute the registered proof argument. Also exclude intuition without an argument, duplicate statement summaries, merely illustrative prose, and proof-local calculations with no independent reusable identity. Do not reject a substantive informal explanation merely because a formal proof follows it.

## Target and bundle judgments

For each accepted proof, verify its single registered `proves` relationship identifies the same target named by the decision. The target must be an included non-proof result eligible to be proved. A proof may not target itself, another proof, an excluded entity, or an entity chosen only by proximity.

Create one bundle for fragments that collectively present the same proof. Complementary informal and formal fragments, or a sketch and its formal expansion, may share a bundle only when all of these hold:

- they prove the same target and address the same proof obligation;
- their argument structures or dependency paths are compatible; and
- registered source evidence establishes continuation, expansion, restatement, or formalization.

The same target is necessary but not sufficient for bundling. Materially different argument structures or dependency paths remain separate alternative proof bundles even when they prove the same result. Give bundle ids stable descriptive identities derived from the registered proof identities, not from source adjacency.

## Dependency and accounting audit

Inspect every incident relationship for each proof. Incoming `supports` relationships must represent actual registered proof uses. The decision pass does not add, redirect, or delete dependency relationships; deterministic normalization later redirects accepted dependencies and accounts for excluded incident records. Treat a mere mention, shared notation, thematic relation, or local calculation as insufficient evidence for accepting a proof dependency.

Before writing output:

1. Partition every proof entity exactly once into accepted or excluded.
2. Confirm every accepted proof has one bundle and one target matching exactly one registered outgoing `proves` relationship.
3. Confirm complementary informal and formal presentations share a bundle only with registered continuation or argument-compatibility evidence.
4. Confirm alternative proof bundles remain separate when their arguments materially differ.
5. Confirm excluded irrelevant prose is reasoned from registered evidence and no accepted proof or dependency was invented.
6. Validate the decisions object against `proof-normalization.schema.json`.

Return only the decisions-output path. On ambiguous ownership, inconsistent registered evidence, incomplete accounting, or schema failure, return task failure rather than an approximate decisions object.
