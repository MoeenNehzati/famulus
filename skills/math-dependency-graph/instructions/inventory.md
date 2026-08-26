# Inventory One Source Chunk

## Goal

Make one concise, recall-first inventory of possible nodes and direct dependency edges while reading the assigned source exactly once from beginning to end.

Inventory is discovery, not final graph construction. Retain uncertain but source-grounded mathematics concisely; the extract pass resolves identities, merges duplicates, and prunes indirect or irrelevant material. Never invent a node or edge merely to improve coverage.

## How you receive source

You never open source files. The assigned source reaches you as **packets**. Each
report presents exactly one text string whose rows are numbered
`NNNNNN | <source text>`. The numbers are opaque chunk-local coordinates, not
source-file line numbers. Use them in every location as `[start_row, end_row]`.
You neither know nor construct source-file identities; transport maps the row
coordinates after submission.

`cumulative_packets_file` holds every packet you have been shown so far, in order.
It never contains source ahead of your cursor, and it is your only record of
packets you have already passed. `inventory_file` is your durable cumulative
inventory and your final output. You, not transport, create and update it.

Your **cursor** is the reading unit you are currently on. It advances one unit
at a time and never moves backward.

The current packet and `cumulative_packets_file` are your only authorized views
of the source. Never inspect repository files, schemas, gold answers, baselines,
transport state, or any other artifact.

## Output contract

Keep one cumulative JSON object in `inventory_file`:

```json
{"nodes":[],"edges":[],"gaps":[]}
```

Create it before the first packet report. After each packet, update that file
first. Retain prior IDs and records, except for uniquely justified forward
endpoint reconciliation, and append newly discovered records. Transport maps
opaque row coordinates to canonical source locations only after your final
update.

The report is separate diagnostic evidence. Return only the records newly
appended while processing the current packet:

The response has this top-level shape:

```json
{"outcome":"reported","nodes":[],"edges":[],"gaps":[]}
```

Every location, including locations nested in assumption scopes and references,
is exactly `[start_row, end_row]`, with positive row numbers, `start_row <=
end_row`, and both endpoints have already been displayed. Use cumulative local
IDs `n1`, `n2`, ... for nodes, `d1`, `d2`, ... for edges, and `g1`, `g2`, ...
for gaps. A resolved endpoint is `{"local_node":"nN"}`. The records returned
in a report must equal the new suffix appended to the corresponding arrays in
`inventory_file`. Additional properties are forbidden.

## The single forward loop

Start at the first assigned source line and move monotonically to the final assigned line. A reading unit is exactly one bounded source unit:

- one prose paragraph;
- one displayed statement;
- one complete explicitly delimited theorem-like environment, such as an assumption, definition, lemma, proposition, theorem, corollary, remark, example, or document-defined equivalent; or
- one standalone document-defined or otherwise opaque TeX command that may denote an author-visible mathematical object; or
- one prose paragraph or displayed statement inside a proof.

A LaTeX section or subsection command and a Markdown heading supply context for the units that follow; neither is itself a graph-reading unit, and neither makes the whole section one unit. Split a proof into its successive paragraphs and displays while retaining the result being proved as their owner. Boundary context may explain an owned unit but never owns a record. Do not read the whole chunk before starting, do not prepare a separate census before the loop, and do not make a second semantic pass over the chunk. Bounded look-back into `cumulative_packets_file`, described under Looking back, is not a second pass.

Keep at most one unfinished node spanning adjacent units. At every reading unit,
perform these steps in order before advancing:

1. **Recognize the unit.** Identify its owning mathematical object or result. A proof or restatement belongs to the result it establishes, including a result declared outside this chunk.
2. **Handle a bounded formal block first.** If the unit is an explicitly delimited TeX environment, Markdown mathematical admonition, or document-defined mathematical block, make an explicit candidate-or-omission decision. Theorem-like and definition-like blocks default to candidates. Omit only a purely navigational block or a technical wrapper duplicating an inner author-visible statement.
3. **Check all six discovery slots.** Formal claim; reusable prose setup; assumption or hypothesis; named mathematical tool; proof use; exposition or application. One unit may fill several slots. Finding one signal does not end the check.
4. **Record direct prerequisites.** Emit one lead for every distinct premise stated or used. Its dependent is the owning proof entity when one is retained, otherwise the directly using result. Use the invariant direction prerequisite `from` -> dependent `to`. Do not replace several premises with one vague lead.
5. **Account and advance.** Every observed signal must now appear as a node, edge, or precise gap. Use `none` only if all six slots are absent.

### Slot 1: formal claim or bounded formal block

Candidate blocks include author-visible assumptions, definitions, lemmas, propositions, theorems, corollaries, conjectures, examples, substantive remarks, and document-defined equivalents.

- A technical wrapper such as a restatement container is not a second node. Preserve the inner author-visible environment, labels, and title.
- Never let a prose-spanning node swallow or hide an inner formal block.
- A restatement or proof-only block identifies its owning result and the prerequisites used in that proof; it is not automatically a separate proof node.

TeX environment delimiters, Markdown mathematical block annotations, labels, theorem names, and proof headers are strong structural signals.

A standalone document-defined or otherwise opaque TeX command is a bounded discovery signal even when its expansion is unavailable and it is not a paragraph, display, or delimited environment. Look only in the same bounded context for corroborating source-visible cues: an adjacent proof header, exact label or reference, author-visible title, or explanatory prose identifying it as a mathematical result.

- With corroboration, make an explicit candidate-or-gap decision; never silently dismiss the invocation as a technical wrapper.
- If corroboration establishes exactly one result identity but hides the substantive statement, emit a minimal identity-only result at the invocation line and a precise unavailable-expansion gap whose subject is that local node. Do not infer conditions or conclusions from the macro name. Retain an adjacent substantive proof separately and connect its `proves` edge to the identity-only result.
- If corroboration establishes mathematical relevance but not one unique identity, emit an identity or coverage gap with an unresolved subject. Create no result candidate and no guessed `proves` target.
- Omit the invocation only when there is no corroborating mathematical cue or when a separately source-visible inner statement would be duplicated.

### Slot 2: reusable prose setup

Retain a named or defined map, set, event, partition, correspondence, objective, statistic, bound, construction, or similar mathematical object when later reasoning refers to it or uses its properties. Signals include “define,” “denote,” “set,” “let,” “write,” “call,” “consider,” displayed definitions, and prose declarations followed by use. A label or environment is not required.

### Slot 3: assumption or hypothesis

Retain standing assumptions, scoped conditions, theorem hypotheses, and explicitly imposed properties. Ambient assumptions must not disappear: add an assumption lead to every result or proof that directly uses one, whether or not the dependent restates it and whether or not the assumption was declared in the current unit. Preserve standing/local kind and local scope when the schema requires them.

### Slot 4: named mathematical tool

Retain a cited or named theorem, lemma, inequality, formula, identity, convergence principle, or other external result when its mathematical content is used. Signals include citations and phrases such as “by,” “using,” “applying,” “from,” and “it follows from.” When the source gives the tool a unique identity, append one `external-result` node and use its local handle in the dependency edge. An unresolved endpoint is not a substitute for that node. Attach the tool to the result or proof that uses it, not to the nearest sentence.

### Slot 5: proof use

For each proof paragraph, first identify the result being proved and its retained proof entity. Add one prerequisite-to-proof lead for every directly used earlier result, definition, setup, assumption, named tool, or indispensable condition; the proof entity then proves the result. If no proof entity is retained, attach a direct use to the owning result only when the source states that dependence directly. Do not add adjacency or transitive edges.

### Slot 6: exposition or application

Retain examples, substantive remarks, application conclusions, and constructions that explain, instantiate, establish, or refute graph-relevant mathematics. Use `illustrated-by` from a mathematical object to its example. Use `supports` from a premise or construction to the result it directly enables.

The phrases above are clues, not a closed vocabulary. Apply the same semantic tests to equivalent research prose and custom environments.

### Edge triggers

Check these independently at every reading unit, even when the unit creates no
node:

- **E1 — explicit reference:** every substantive reference or citation in a use
  position contributes an `explicit-reference` edge from the referenced object
  to the owning result or proof. Keep an unresolved endpoint when the target has
  not been seen; do not drop the edge.
- **E2 — proof use:** every directly used prerequisite in a proof contributes a
  `proof-use` edge to the retained proof entity. Emit it where the use occurs.
- **E3 — prose use:** every direct use stated in words such as “by,” “using,”
  “applying,” “from,” or “it follows from” contributes an `explicit-prose` edge
  to the owning result or proof.

Each trigger records a direct use only. Do not add adjacency or transitive
edges, and do not suppress a triggered edge merely because the current unit
creates no node.

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

## Looking back

Consult `cumulative_packets_file` by searching it, never by reading it whole:

```
grep -n 'label{prop:measurable}' <cumulative_packets_file>
sed -n '118,137p' <cumulative_packets_file>
```

Read back at most 20 lines in one `sed` range, and look back at most three times
per reading unit. If three reads have not settled the question, keep the edge
with its unresolved endpoint and a confidence that reflects the doubt; do not
convert it into a gap.

A look-back may only improve the edge you are emitting at your cursor: the
`title`, `statement`, `type_hint`, and `locators` of its unresolved endpoint. It
never creates a node for an earlier unit, revises a closed record, resolves an
endpoint to a hidden prior-packet node, or moves your cursor.

## Encode a node

For each reading unit choose exactly one node action:

- `none`: no graph-relevant node material;
- `start`: begin a node that continues beyond this unit;
- `extend`: continue the one open node;
- `close`: finish the open node and append its record.

A node may span several paragraphs or nested source constructs. Use the smallest
complete inclusive location covering its statement, qualifications, and scope.
As soon as it closes, append a complete record with local ids `n1`, `n2`, ...
in closing order.

Write one short source-faithful summary sentence: what the object, premise, claim, or result establishes, plus only qualifications needed to distinguish it. Preserve author-visible environment, labels, and title. Fill the provisional type, provenance, assumption kind/scope, and external identity required by the schema.

Potential nodes include reusable notation and constructions, assumptions, definitions, examples with mathematical force, formal results, substantive remarks, application conclusions, reusable prose claims, and indispensable named external results. Equations, algebraic steps, temporary variables, and proof-local conditions are normally edge evidence, not nodes; promote one only when named and reused as a unit or stated as an author-visible result.

Once an endpoint has a node, use its `local_node` handle in later edges. Use the
same shortest source-faithful identity whenever an unresolved endpoint recurs.

### Reconcile forward local identities

After every node closure and again before submitting a packet, reconcile every
unresolved endpoint already saved in the cumulative inventory against its local
node table. Use only saved records and the newly closed node; do not inspect
transport state, another worker's artifact, gold, or diagnostic output.

Test identity in this precedence order:

1. an exact source-visible label or other exact locator shared with the closed node;
2. the same source-visible identity with compatible mathematical type and scope;
3. a clearly equivalent mathematical statement with compatible type and scope.

Replace an unresolved endpoint with `{"local_node":"<local-id>"}` only when
exactly one cumulative node represents the same entity. A related, stronger,
weaker, or merely thematic result is not a match. If there is no unique match,
preserve the unresolved endpoint unchanged.

The substitution is identity-only: it may not change direction, type, evidence
location, reference, description, assertion, basis, confidence, semantic
content, or ownership, and it may not create a relationship that was not
already recorded. Retain records with distinct source evidence. Remove a
duplicate only when prerequisite, dependent, relationship type, location,
reference, description, assertion, basis, and confidence are all identical.
The schema has no multi-evidence field, so never merge distinct evidence into
one record.

## Encode a direct dependency

Add an edge during the unit where the direct use is stated or occurs.

- `from`: prerequisite or supporting source;
- `to`: owning proof entity when retained, otherwise the directly using result;
- location: smallest span supporting that direct use;
- description: one short clause saying how the prerequisite is used.

Use a local node handle when available. Otherwise use an inline unresolved
endpoint and keep scanning. Forward references remain unresolved until a later
node closes and forward reconciliation uniquely identifies them. Prefer a
source-grounded low-confidence lead to silent omission; extract later checks
identity and directness.

When several nearby dependents are possible, use the proof header, statement identity, explicit label, and declared scope—not proximity—to determine ownership. Apply the exact-duplicate rule from forward reconciliation: preserve evidence-distinct records for the same prerequisite-dependent pair, and remove only records whose endpoint pair, relationship type, location, reference, description, assertion, basis, and confidence are all identical, keeping the earlier local id.

## Use gaps sparingly

Record a gap only when uncertainty at the current cursor could materially change node identity, scope, coverage, or a dependency lead. A gap is not a substitute for recording a source-grounded uncertain candidate.

## Check before each packet report

The packet boundary is the checkpoint. Before submitting, update
`inventory_file`, then verify that every
location uses displayed rows, is increasing, inclusive, and row-contiguous;
local IDs are unique and sequential; every `local_node` endpoint exists in the
cumulative inventory; every edge is prerequisite-to-dependent; and every
graph-relevant annotated block, reference, citation, or named result seen so far
is accounted for. In particular, every E1, E2, and E3 sighting has an edge, and
every uniquely identified named tool whose mathematical content is used has an
`external-result` node rather than only an unresolved endpoint. Perform forward
local-identity reconciliation first.
Transport validates the cumulative file and the packet-local report separately.

## Keep records concise

Spend words on recall, exact locations, identity, directness, and the explanation required by the schema. Use one sentence for a node summary, one short clause for an edge description, and the shortest stable identity for an unresolved endpoint. Do not copy source paragraphs, repeat endpoint prose, or add general justification.

Write each completed record concisely when first encountered. Do not perform a
later rewriting or compaction pass.

## Finish

At the final row of each packet:

1. close every source-visible candidate that is complete within this packet;
2. retain an unfinished node only when its source clearly continues into the next
   packet; do not invent completion;
3. perform forward local-identity reconciliation; and
4. atomically update `inventory_file`; and
5. return only the newly appended nodes, edges, and gaps for this packet.
