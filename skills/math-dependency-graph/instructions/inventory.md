# Inventory One Iterator Assignment

## Goal

Make one concise, recall-first inventory of plausible mathematical nodes and direct dependency leads while consuming your assigned source units exactly once in iterator order. Inventory is discovery, not final graph construction: retain uncertain but source-grounded mathematics, because extraction later resolves identities, merges duplicates, and rejects false positives.

Setup is controller-owned. You receive an existing iterator state directory, one worker index, your inventory and progress paths, and `inventory.schema.json`. Write only your assigned inventory and progress files. Obtain source content only through `math-dependency-graph._rtx.interface.scripts-next-inventory-unit`; never read prepared input, a controller-owned artifact, iterator storage, an assignment manifest, or a source file directly.

## Output contract

Read `inventory.schema.json` completely before requesting source content. Maintain the initialized cumulative version-3 inventory at your supplied inventory path. Preserve its exact `chunk_id` and `files` table. Keep cumulative `nodes`, `edges`, and `gaps` arrays schema-valid, and append concise progress evidence only to your supplied progress path.

For every unit, preserve source-faithful locations and author-visible labels, environment names, and titles. Give each node one short summary of its mathematical content and material qualifications. Give each edge one short clause explaining the direct use. Use prerequisite or support as `from` and the dependent result as `to`, except that proof ownership runs from the proof to the result it proves.

## Required iterator loop

1. Invoke `math-dependency-graph._rtx.interface.scripts-next-inventory-unit` using exactly `<state-dir> <worker-index> [--ack <unit-id>] [--wrap]`: supply the state directory and worker index without an acknowledgement to obtain the first unit. The iterator state already binds your inventory and progress paths and the inventory schema. Those paths are your file responsibilities, never arguments to `next`. Retain the response's nonnegative `process_timings_ms.process_dispatch` and `process_timings_ms.total` measurements verbatim; they cover the public wrapper's controlled child spawn through bootstrap and complete child stdout/exit, not outer gateway latency.
2. If the response state is `unit`, inspect only that returned unit, linearly, while retaining enough context from consecutive earlier units to identify the same owning mathematical object or result.
3. Add every plausible graph node and every explicit or mathematically inferred direct dependency destination supported by the unit to your own cumulative inventory. Keep the concise explanation and source evidence required by the schema.
4. Save and validate the complete cumulative inventory against `inventory.schema.json`.
5. Acknowledge that exact unit by invoking the same `next` interface as `<state-dir> <worker-index> --ack <unit-id>`, adding only `--wrap` when this unit closes the consecutive attention sequence for its owning object or result. Do not pass the inventory path, schema path, or progress path. The acknowledgement validates the bound current inventory's schema and every recorded location against the durable assignment before it advances and returns either the next unit or `complete`.
6. Repeat from step 2 for every returned unit, retaining every returned next-call timing object in the same way. Finish only when a successful acknowledgement returns state `complete`.

Every leased unit is acknowledged through the next call, including a unit that yields no new records. Never advance after a failed acknowledgement: repair your own inventory, validate it again, and retry the same acknowledgement with the same wrap choice. Do not create a setup call, cursor, span marker, checkpoint interval, node-state DSL, or alternative traversal protocol.

## Recall-first mathematical scan

For each returned unit, identify its owning mathematical object or result before recording anything. Retain context across consecutive units only as needed to complete that owner; use `--wrap` on the unit that closes it. A proof paragraph or display has its own temporary proof identity when it meets the proof-candidate policy below, and that proof must still identify the result it proves.

Check all of these source-grounded possibilities before acknowledging:

- author-visible assumptions and scoped hypotheses;
- definitions, notation, maps, sets, events, constructions, and other reusable setup;
- lemmas, propositions, theorems, corollaries, conjectures, and reusable prose claims;
- named or cited external results whose mathematical content is used;
- formal proof environments, unwrapped informal arguments, and proof sketches that perform substantive inferential work for a result whose target is resolved or retained through a source-grounded unresolved target handle;
- proof uses of earlier results, setup, assumptions, or tools, attached to the proof that actually uses them;
- examples, substantive remarks, and applications with mathematical force.

Theorem-like environments and small titled mathematical Markdown or LaTeX blocks are strong candidate signals. Prefer a plausible candidate over premature pruning; extraction may merge or reject it later. A technical wrapper that merely duplicates an inner author-visible statement is not a second node.

### Proof candidates and ownership

Record a proof candidate with `type_hint: "proof"` only when the separable passage performs substantive inferential work toward one mathematical claim and retaining it preserves ownership or dependency evidence. Classify its argument in the summary as `formal`, `informal`, or `sketch`: an explicit proof environment or equivalently delimited argument is formal, unwrapped argumentative prose is informal, and an explicitly incomplete or high-level argument is a sketch. The complete proof fragment is the proof candidate's location; the proved result keeps its own smallest complete statement location.

For every retained proof candidate, record the ownership lead in this direction:

```text
proof entity --proves--> proved result
```

Emit exactly one `proves` lead when the target is resolved. Its evidence location is the smallest source-visible ownership link: a proof heading, label, surrounding theorem structure, or exact prose tying the argument to its target. Do not infer the target from proximity alone. When ownership is source-grounded but not uniquely resolvable, preserve unresolved ownership through an unresolved endpoint or a gap; do not guess a result merely to close the record.

Attach each graph-relevant direct proof use in this direction:

```text
prerequisite --supports--> proof entity
```

Use the smallest exact proof-use span where the proof actually invokes the prerequisite. A source-visible assumption, earlier result, named external result, or reusable construction can qualify. A mere mention, shared notation, proximity, thematic relation, or a dependency inferred only from the proof's conclusion does not.

Do not create a proof candidate for motivation, navigation, restatement, local algebra, intuition without an argument, or a temporary proof-local claim that does not independently satisfy the ordinary graph-entity policy. Separate an informal explanation and a formal proof as distinct proof candidates when each contains substantive inferential work; inventory does not decide whether they are complementary presentations or alternative proofs.

Record each direct prerequisite-to-dependent lead where the use is stated or occurs. Explicit references, explicit prose, proof use, and justified mathematical inference all count when the schema fields honestly distinguish them. Use a local node endpoint when available and a concise unresolved endpoint for forward, cross-file, implicit, or external identities. Do not infer dependencies from proximity or add transitive edges.

Use a gap only when uncertainty could materially change identity, scope, coverage, evidence, or a relationship. A gap is not a substitute for a source-grounded low-confidence candidate.

## Finish

Before acknowledging the unit that closes a proof, perform proof closeout: account for the complete proof span, its proved target or unresolved ownership, exactly one resolved `proves` lead when possible, every graph-relevant direct prerequisite, and any uncertainty that could change those decisions. After every unit, the saved inventory must validate before its acknowledgement. When the acknowledgement returns `complete`, validate once more, append a final concise completion entry to your progress path, and return your assigned inventory path with the bounded numeric `process_dispatch` and `total` measurements for every next call. Do not include unit text, source prose, or inventory prose in timing data. Do not reopen source, compact records in a later pass, modify another worker's files, or claim completion from local reasoning before the iterator reports `complete`.
