# Inventory One Source Chunk

## Goal

Make one concise, recall-first inventory of possible nodes and direct dependency edges while reading the assigned source exactly once from beginning to end.

Inventory is discovery, not final graph construction. Retain uncertain but source-grounded mathematics concisely; the extract pass resolves identities, merges duplicates, and prunes indirect or irrelevant material. Never invent a node or edge merely to improve coverage.

## Output contract

Read `inventory.schema.json` completely before reading source. Maintain one cumulative schema-valid JSON object with:

- `ir_version: 3`;
- the packet's exact `chunk_id`;
- `files`: every distinct owned source path, in first-appearance order;
- cumulative `nodes`, `edges`, and `gaps` arrays.

Write only to the supplied output and progress paths. The final JSON must validate against `inventory.schema.json`.

Before the semantic pass, scan only the packet's assigned-span markers to build `files`. Write and validate the empty cumulative fragment. Then append an `initialized` progress line with a timestamp read from an available clock at append time. This marker scan is not a source-reading pass.

## The single forward loop

Start at the first assigned source line and move monotonically to the final assigned line. A reading unit is exactly one bounded source unit:

- one prose paragraph;
- one displayed statement;
- one complete explicitly delimited theorem-like environment, such as an assumption, definition, lemma, proposition, theorem, corollary, remark, example, or document-defined equivalent; or
- one prose paragraph or displayed statement inside a proof.

A LaTeX section or subsection command and a Markdown heading supply context for the units that follow; neither is itself a graph-reading unit, and neither makes the whole section one unit. Split a proof into its successive paragraphs and displays while retaining the result being proved as their owner. Boundary context may explain an owned unit but never owns a record. Do not read the whole chunk first, prepare a separate census, or return for a second semantic pass.

Keep at most one unfinished node spanning adjacent units. At every reading unit, perform these steps in order before advancing:

1. **Recognize the unit.** Identify its owning mathematical object or result. A proof or restatement belongs to the result it establishes, including a result declared outside this chunk.
2. **Handle a bounded formal block first.** If the unit is an explicitly delimited TeX environment, Markdown mathematical admonition, or document-defined mathematical block, make an explicit candidate-or-omission decision. Theorem-like and definition-like blocks default to candidates. Omit only a purely navigational block or a technical wrapper duplicating an inner author-visible statement.
3. **Check all six discovery slots.** Formal claim; reusable prose setup; assumption or hypothesis; named mathematical tool; proof use; exposition or application. One unit may fill several slots. Finding one signal does not end the check.
4. **Record direct prerequisites.** Emit one lead for every distinct premise stated or used for the owning result. Use the invariant direction prerequisite `from` -> dependent `to`. Do not replace several premises with one vague lead.
5. **Account and advance.** Every observed signal must now appear as a node, edge, or precise gap. Use `none` only if all six slots are absent.

### Slot 1: formal claim or bounded formal block

Candidate blocks include author-visible assumptions, definitions, lemmas, propositions, theorems, corollaries, conjectures, examples, substantive remarks, and document-defined equivalents.

- A technical wrapper such as a restatement container is not a second node. Preserve the inner author-visible environment, labels, and title.
- Never let a prose-spanning node swallow or hide an inner formal block.
- A restatement or proof-only block identifies its owning result and the prerequisites used in that proof; it is not automatically a separate proof node.

TeX environment delimiters, Markdown mathematical block annotations, labels, theorem names, and proof headers are strong structural signals.

### Slot 2: reusable prose setup

Retain a named or defined map, set, event, partition, correspondence, objective, statistic, bound, construction, or similar mathematical object when later reasoning refers to it or uses its properties. Signals include “define,” “denote,” “set,” “let,” “write,” “call,” “consider,” displayed definitions, and prose declarations followed by use. A label or environment is not required.

### Slot 3: assumption or hypothesis

Retain standing assumptions, scoped conditions, theorem hypotheses, and explicitly imposed properties. If the current result directly invokes one, add an assumption-to-result lead. Preserve standing/local kind and local scope when the schema requires them.

### Slot 4: named mathematical tool

Retain a cited or named theorem, lemma, inequality, formula, identity, convergence principle, or other external result when its mathematical content is used. Signals include citations and phrases such as “by,” “using,” “applying,” “from,” and “it follows from.” Attach the tool to the result whose proof or construction uses it, not to the nearest sentence.

### Slot 5: proof use

For each proof paragraph, first identify the result being proved. Then add one prerequisite-to-result lead for every directly used earlier result, definition, setup, assumption, named tool, or indispensable condition. Direct prose use counts even without a reference command. Do not add adjacency or transitive edges.

### Slot 6: exposition or application

Retain examples, substantive remarks, application conclusions, and constructions that explain, instantiate, establish, or refute graph-relevant mathematics. Use `illustrated-by` from a mathematical object to its example. Use `supports` from a premise or construction to the result it directly enables.

The phrases above are clues, not a closed vocabulary. Apply the same semantic tests to equivalent research prose and custom environments.

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

## Encode a node

For each reading unit choose exactly one node action:

- `none`: no graph-relevant node material;
- `start`: begin a node that continues beyond this unit;
- `extend`: continue the one open node;
- `close`: finish the open node and append its record.

A node may span several paragraphs or nested source constructs. Use the smallest complete inclusive location covering its statement, qualifications, and scope. As soon as it closes, append a complete record with local ids `n1`, `n2`, ... in closing order.

Write one short source-faithful summary sentence: what the object, premise, claim, or result establishes, plus only qualifications needed to distinguish it. Preserve author-visible environment, labels, and title. Fill the provisional type, provenance, assumption kind/scope, and external identity required by the schema.

Potential nodes include reusable notation and constructions, assumptions, definitions, examples with mathematical force, formal results, substantive remarks, application conclusions, reusable prose claims, and indispensable named external results. Equations, algebraic steps, temporary variables, and proof-local conditions are normally edge evidence, not nodes; promote one only when named and reused as a unit or stated as an author-visible result.

Once a local endpoint has a node, use its local handle in later edges. Use the same shortest source-faithful identity whenever an unresolved endpoint recurs.

## Encode a direct dependency

Add an edge during the unit where the direct use is stated or occurs.

- `from`: prerequisite or supporting source;
- `to`: owning dependent result;
- location: smallest span supporting that direct use;
- description: one short clause saying how the prerequisite is used.

Use a local node handle when available. Otherwise use an inline unresolved endpoint and keep scanning. Forward references and cross-file labels remain unresolved. Prefer a source-grounded low-confidence lead to silent omission; extract later checks identity and directness.

When several nearby dependents are possible, use the proof header, statement identity, explicit label, and declared scope—not proximity—to determine ownership. Coalesce repeated evidence for the same prerequisite-dependent pair.

## Use gaps sparingly

Record a gap only when uncertainty at the current cursor could materially change node identity, scope, coverage, or a dependency lead. A gap is not a substitute for recording a source-grounded uncertain candidate.

## Checkpoint while scanning

After every four closed nodes, or after 120 assigned source lines since the previous checkpoint, whichever comes first:

1. write every completed node, encountered edge, and gap to the cumulative JSON;
2. verify that `files` is correct; locations are owned, in range, increasing, and inclusive; local ids are unique; local endpoints exist; every edge is prerequisite-to-dependent; and every graph-relevant annotated block, reference, citation, or named result seen so far is accounted for;
3. validate the entire cumulative object against `inventory.schema.json`;
4. only after validation, append a `scan-checkpoint` progress line with a fresh clock timestamp, cursor file/line, and node/edge/gap counts.

The saved artifact is the progress evidence. Do not keep finished records only in reasoning or write all checkpoints at the end. A checkpoint excludes the unfinished open node.

At the end of each assigned span, close any complete open node, save and validate, and append `span-complete` with that span's exact final owned line and current counts. Do this even if the counts did not change. Process spans in packet order; boundary context gets no completion line.

## Keep records concise

Spend words on recall, exact locations, identity, directness, and the explanation required by the schema. Use one sentence for a node summary, one short clause for an edge description, and the shortest stable identity for an unresolved endpoint. Do not copy source paragraphs, repeat endpoint prose, or add general justification.

Write the completed record concisely when first encountered. Do not perform a later rewriting or compaction pass.

## Finish

At the final owned line:

1. close any complete open node or record a precise gap for an incomplete one;
2. verify that `files` exactly matches the assigned source paths and that the cursor is the final line of the final assigned span;
3. save all records and validate the full final JSON against `inventory.schema.json`;
4. append `output-written` with a newly read timestamp, the final cursor, and final counts.

Do not write `output-written` before every span has a `span-complete` line. If any invariant or schema validation fails, correct the cumulative artifact before returning. Return only the completed output path.
