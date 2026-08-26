# Inventory Changes From Run `edgetrig1`

Proposed changes to `skills/math-dependency-graph/instructions/inventory.md`,
derived from the first measured run of the edge-trigger instruction.

> Decision update: the packet-local-delta and transport-owned ID-allocation
> proposal below was rejected after implementation and experiment. The accepted
> boundary is simpler: append each packet to the cumulative file, deliver that
> exact source-text string to the LLM, and let the worker maintain one cumulative
> inventory response. Transport maps hidden row coordinates only.

Anchors are quoted phrases rather than line numbers, because the file is under
concurrent edit.

## The run these come from

Run `edgetrig1`, voyage 1, 2026-08-25. Scope: `appendix-dynamics.tex` 1-422 and
`appendix-prevalence.tex` 1-198 — chunk 1 of a 2-chunk split of the
`inference-from-random-restarts` appendix. Gold in scope: 42 nodes, 53 edges
from `results/inventory-gold.json`. Baseline is the 2026-08-24 run of the
pre-edge-trigger instruction, rescored on the identical span.

| | nodes recall | nodes prec | edges recall | edges prec |
|---|---:|---:|---:|---:|
| baseline | 83.3% | 94.6% | 54.7% | 76.3% |
| `edgetrig1`, literal | 92.9% | 95.1% | 84.9% | 66.2% |
| `edgetrig1`, proof-collapsed and deduplicated | — | — | 84.8% | 77.8% |

The edge-trigger run is associated with substantially higher edge recall on this
span. Because packet granularity changed at the same time, this does not yet
identify the instruction change as the cause. The changes below address the
observed residuals and make subsequent measurements interpretable.

Two caveats carried forward. `packet_chars` was 12000 for the baseline and 3000
for this run, so instruction and packet granularity both moved; a control run of
the old instruction at 3000 is still outstanding. And only 3 of the 13 residual
disagreements have been adjudicated against source, so the worker-wrong versus
gold-wrong split is unknown.

## 1. Route proof-use edges to the proof entity, never to the result

**Motivation.** This is the largest single source of error in the run. Proof
nodes close *after* the result they prove: result `n9` closes, the worker then
reads the proof body and must emit use-edges at the unit where each use occurs,
but the proof node `n10` does not close until the body ends. The only closed
handle available is the result, so the edge lands there.

The evidence is consistent across both error classes, and they are the same
edges counted twice:

- 6 of the 8 missed gold edges terminate at a `proof`;
- 23 emitted edges target a result that has its own proof entity, bypassing it;
- in 10 of those the proof node's local id is later than the prerequisite's,
  which is the timing signature;
- collapsing the proof/result distinction recovers **6.4 precision points at
  zero recall cost** (71.4% to 77.8%).

The durable Voyage record does not preserve worker commentary or an action trace,
so the endpoint-timing mechanism above is an author diagnosis of the emitted
artifact, not a worker-reported fact. Before adopting the wording, reproduce the
23 bypassed-edge and 10 timing-signature counts in a checked-in diagnostic and
run a targeted comparison.

**What it achieves.** Removes a mis-routing that currently corrupts both recall
and precision, without asking the worker to find anything new. The dependency is
already being discovered; only its endpoint is wrong.

**Change.** In `**E2 — proof use.**`, after the existing first sentence:

> The `to` endpoint is that proof entity. If the proof entity is not yet closed,
> emit an unresolved `implicit-entity` endpoint identifying the proof, and let
> forward reconciliation attach it on closure. Never substitute the proved
> result for an unclosed proof entity.

Forward reconciliation already handles this; it resolved 6 endpoints this run.

## 2. Let a proof node stay open while inner nodes close

**Motivation.** Direct enabler of change 1. The rule "Keep at most one unfinished
node spanning adjacent units" reads as forbidding an open proof node while an
external-result node closes inside it — which happens routinely, for instance
Sard's theorem cited inside the parametric-submersion proof. The final artifact
is consistent with permissive nesting, but the run does not retain enough action
history to establish how the worker interpreted the rule. A stricter reading
would forbid retaining cited tools inside multi-paragraph proofs at all.

**What it achieves.** If a proof node has a handle from the moment the proof
opens, change 1's fallback path is rarely needed. It also removes an ambiguity
that two readers could resolve in opposite directions.

**Change.** Amend "Keep at most one unfinished node spanning adjacent units":

> Keep at most one unfinished node per nesting level. A proof node may remain
> open across units while nodes recognized inside it open and close.

## 3. Prefer the standing assumption over a local restatement of it

**Motivation.** A verified worker error. Gold records `Root compactness
(ass:kkq) -> break-into-two lemma`. The run instead credited a local setup node,
"With C^2 boundary the outward normal n is well-defined". The source at
`appendix-prevalence.tex:57` is explicit:

> "Under Assumption~\ref{ass:kkq}, $X$ has $C^2$ boundary … As the boundary is
> $C^2$, $\vn$ is $C^1$, making $\QTCP$ continuous"

The dependency is on the assumption; the local sentence only restates its
consequence.

This is the class the current Slot 3 sentence already targets — "Ambient
assumptions must not disappear" — which was added before this run and was not
sufficient. Stating that assumptions must be attached does not tell the worker
what to do when a nearer node also expresses the property.

**What it achieves.** Converts a recall miss into a hit, and removes a
plausible-looking wrong answer rather than merely encouraging the right one.

**Change.** Append to Slot 3, after the existing ambient-assumption sentence:

> When the source derives a used property from a standing assumption, the edge
> goes to the assumption, not to the local sentence restating its consequence. A
> nearer node expressing the same property does not displace the assumption it
> came from.

## 4. Require a source-visible warrant for an inferred edge

**Motivation.** Weakest-evidence item here, offered as a candidate rather than a
conclusion. Half the residual extras — 4 of 8 — carry `assertion: "inferred"`
with `basis: "mathematical-inference"`. The triggers fire on sightings and give
no stopping condition for edges the worker reasons to rather than reads.

Against this: at least one scored false positive is a **gold omission**, not a
worker error. The run emitted `R(Q^TC) notation -> containment lemma`, and the
source defines that notation immediately above a lemma stated in it. Tightening
inference could suppress correct edges of that kind.

**What it achieves.** Bounds the one trigger with no stopping rule — but it
trades recall for precision, and the residual extras have not been adjudicated,
so the trade is not yet quantified. Hold this until the remaining ten residual
disagreements are adjudicated against source.

**Change, if taken.** In the edge-trigger preamble:

> An edge asserted as `inferred` requires a source-visible warrant that the
> dependency is actually used, not only that it would be needed for the result
> to hold.

## 5. Do not change the duplicate-evidence convention; fix the scorer instead

**Motivation.** `## Encode a direct dependency` says to "preserve
evidence-distinct records for the same prerequisite-dependent pair". The run
produced 54 collapsed edge records covering 36 distinct pairs — 18 duplicates.
Under one-to-one matching every duplicate after the first scores as a false
positive, which is most of the apparent precision loss: 18 of 23 residual extras
were duplicates, not errors.

**What it achieves.** Nothing in the instruction; this is a measurement fix. The
convention plausibly helps extract, which consumes multiple evidence records.
What it costs is interpretability, and that cost belongs to the scorer.

**Change.** Leave `inventory.md` alone. Score with proof-collapse and pair
deduplication as the default convention, and report literal figures only as a
secondary line. Without this, every future run is misread the way the 66.2%
figure was.

---

# Inventory Transport Cleanup

Scope principle: the worker receives the contents of
`instructions/inventory.md` and the path to `cumulative_packets_file` once, then
each report receives exactly one text string. That string contains line-numbered
mathematical source and nothing else: no object wrapper, packet id or count,
prior inventory, output schema, transport-added source-file path, or `@@ source:`
marker. A path that is literally part of the mathematical source remains source
text. The worker cannot inspect repository files, schemas, gold, baselines,
transport state, or any other artifact. `inventory.md` must therefore contain
the complete worker-side response contract.

## Why

The path and progress machinery predates the Voyage transport. The frozen
instruction requires the worker to write output and progress files, but the
observed run supplied neither path. The current implementation instead sends a
structured report payload containing packet metadata, prior inventory, and an
output schema, then accepts and persists a full cumulative inventory. The target
boundary removes all of those report-payload fields; transport must therefore
own accumulation and canonicalization rather than shifting them into hidden LLM
state.

`_chunk_extractor.py:444-449` still computes `fragment_path` and `progress_path`
into every chunk record, but neither path is surfaced to the worker. The current
dispenser still requires `fragment_path` to be a string even though it constructs
the actual Voyage output path independently; `progress_path` has no corresponding
consumer.

Observed mismatch: the frozen instruction mandates writes and progress markers,
while the actual worker-visible inputs contain no output or progress path. The
run had nine packet reports and ten successful validation calls in total: one
introduction acknowledgement plus nine reports. The durable run record contains
no worker-commentary field, so this plan does not attribute a friction ranking
or quoted complaint to the worker.

## Remove

| line | text | reason |
|---|---|---|
| 36 | "Write only to the supplied output and progress paths." | neither path is supplied |
| 38 | whole marker-scan paragraph: "scan only the packet's assigned-span markers to build `files` … Write and validate the empty cumulative fragment … append an `initialized` progress line" | the target packet contains no source-file marker, and no fragment or progress path is supplied; file identity and canonical output framing are transport-owned |
| 252 | checkpoint step 4, "append a `scan-checkpoint` progress line …" | no progress path |
| 254 | "The saved artifact is the progress evidence. Do not keep finished records only in reasoning or write all checkpoints at the end." | no saved artifact; the transport forces a validated cumulative submission at every packet |
| 256 | the `span-complete` paragraph | no progress path |
| 272 | finish step 4, "append `output-written` …" | no progress path |
| 274 | "Do not write `output-written` before every span has a `span-complete` line … Return only the completed output path." | contradicts the response schema, which requires the inventory inline |

## Required transport contract

Implement this contract before changing `inventory.md`.

### Rutter string-payload prerequisite

The current Rutter model requires `LLMStep.data` and `Message.payload` to be JSON
objects. Do not encode the packet as `{"packet": "..."}` merely to satisfy that
restriction. First widen the payload value, while preserving the surrounding
Message/evolution protocol envelope:

- `LLMStep.data` becomes `Callable[[EvolutionContext], JsonValue]`;
- `build_llm_data` freezes and returns any finite `JsonValue`, including a
  string, rather than calling `_freeze_object`;
- Message construction, validation, history encoding/decoding, and
  `Message.payload` accept and preserve `JsonValue`;
- existing object-payload Rutters and stored reckoning files remain byte- and
  behavior-compatible; object-specific consumers explicitly narrow to a
  mapping; and
- the inventory report step's payload value is the packet string itself. The
  protocol envelope remains transport structure and is not part of the packet.

Modify `src/officina/rutter/authoring.py`, `evaluation.py`, `values.py`,
`history.py`, and any engine/visualization typing that assumes an object payload.
Add focused authoring, evaluation, model, lifecycle, storage-roundtrip, runtime,
and visualization tests for both a string payload and an unchanged object
payload. This core prerequisite is complete only when a Voyage status exposes
the inventory report Message with `message.payload == packet_text` and no
inventory-specific wrapper.

### Marker-free packet text and hidden coordinates

- `_render_packet` emits only `NNNNNN | <source text>` rows. `NNNNNN` is a
  monotonically increasing chunk-local row number, not a source-file line.
- A packet never crosses a source-file boundary and never splits an explicitly
  delimited theorem-like block, proof environment, display, or prose paragraph.
  An indivisible unit larger than `packet_chars` becomes one oversized packet.
- The chunk manifest retains a hidden coordinate for every rendered row:
  `(chunk_row, source_file, source_line)`. The worker never receives it.
- Sealing verifies one coordinate per rendered row, strictly increasing unique
  chunk rows, packet order, and source-line order within each file.
- A worker span is `[start_row, end_row]`. Canonicalization rejects a span unless
  every row exists, the range is contiguous, and all rows map to one source
  file. It then converts the span to `[file_index, start_line, end_line]` using
  the transport-owned chunk `files` table.

### Per-packet delta response

Add `schemas/inventory-delta.schema.json` as the machine source of truth and
embed its complete JSON content verbatim under `inventory.md`'s output contract.
A test extracts the fenced schema from the instruction and requires exact JSON
equality with the schema file, so the worker-visible contract cannot drift from
transport validation.

The delta schema defines this top-level response shape:

```json
{
  "outcome": "reported",
  "delta": {
    "nodes": [],
    "edges": [],
    "gaps": []
  }
}
```

The report evolution supplies only the packet text as its data payload. It does
not require a worker-returned packet id; the active evolution entry identifies
the packet. Every location and assumption-scope location inside the delta uses
the worker `[start_row, end_row]` form.

The complete adapted record grammar is:

- `row_location` is exactly two positive integers `[start_row, end_row]`, with
  `start_row <= end_row`;
- a node has every semantic field and conditional rule from the canonical node
  schema, replaces every canonical location with `row_location`, and uses a
  required delta-local `delta_id: "n1"`, `"n2"`, … in closing order instead of
  `local_id`;
- a same-delta endpoint is exactly `{"delta_node": "nN"}`; the unresolved
  endpoint variant retains all canonical unresolved fields, but any nested
  scope locations use `row_location`;
- an edge has `from`, `to`, `type`, `basis`, `assertion`, `location`,
  `description`, `confidence`, and the same conditional `reference` / `reason`
  rules as the canonical schema, but has no worker id; reference locations use
  `row_location`;
- a gap has `category`, `location`, `description`, and the same conditional
  `subject` / `reference` rules as the canonical schema, but has no worker id;
  and
- `nodes`, `edges`, and `gaps` contain only records first discovered in the
  current packet. Additional properties are rejected at every level.

An endpoint introduced in an earlier packet is never referenced by a hidden
canonical id: emit the unresolved semantic endpoint with its source-visible
label, name, citation, or shortest stable identity. Transport assigns canonical
node, edge, and gap ids only after delta validation.

No unfinished node crosses a packet boundary. At the end of a packet, close a
source-visible candidate that is complete within the packet. If prose clearly
continues beyond the boundary, emit a precise continuation gap rather than
inventing completion or retaining hidden state; a later packet emits its own
candidate, and extract reconciles them. Proof environments are never split, so
proof-use edges and their proof entity remain available within one delta.

### Deterministic accumulation and replay

For each accepted delta, the record machine step:

1. validates the worker delta against the worker contract embedded verbatim in
   `inventory.md`;
2. maps every row span through the sealed hidden coordinates;
3. assigns chunk-global node ids after the current maximum, in delta order, and
   rewrites same-delta endpoint references;
4. assigns edge and gap ids after their current maxima;
5. appends the canonical records to a transport-owned fragment containing
   `ir_version`, `chunk_id`, and `files`;
6. validates the entire fragment against `inventory.schema.json`; and
7. atomically persists the canonical fragment and the accepted evolution-entry
   id plus response hash before advancing.

Replaying the same accepted evolution entry with the same response hash returns
the recorded result without allocating ids or appending records again. A
different response for an accepted entry is rejected. On resume, the transport
loads the persisted canonical fragment and advances to the first unrecorded
packet; the new worker still receives only `inventory.md`, the cumulative-file
path, and then the next packet text.

Forward local-identity reconciliation becomes same-delta only. Cross-packet
semantic identities remain unresolved for extract to reconcile; transport must
not guess semantic equivalence. The final persisted fragment retains the current
inventory schema and therefore remains direct input to pooling, scoring, and
extract.

### Required acceptance tests

- the Rutter report Message payload value is a string exactly equal to packet
  text, with no inventory-specific wrapper or metadata;
- existing object-payload Rutters and reckoning round trips remain unchanged;
- no rendered packet or cumulative transcript contains `@@ source:` or any
  transport-added source-path metadata;
- packets do not cross source files or split proof/theorem environments;
- hidden coordinates map valid row spans and reject missing, discontinuous, or
  cross-file spans;
- two deltas allocate stable sequential ids and translate same-delta endpoints;
- a cross-packet endpoint remains unresolved and survives canonical validation;
- the worker-visible fenced delta schema equals
  `schemas/inventory-delta.schema.json` and rejects each omitted required field,
  forbidden extra field, invalid endpoint variant, and invalid row span;
- identical replay is a no-op and conflicting replay is rejected;
- resume after one accepted delta produces the same final canonical bytes as an
  uninterrupted run;
- the final fragment passes `inventory.schema.json`, pooling, and the restricted
  scorer without adapters.

## Rewrite `inventory.md`

**`## Output contract` (27-38).** Describe the per-packet delta above, not a
written or cumulative canonical fragment. Remove worker responsibility for
`ir_version`, `chunk_id`, `files`, global ids, persistence, and cross-packet
accumulation. The worker owns only the semantic records discovered in the
current packet.

Remove "Read `inventory.schema.json` completely before reading source": the
worker cannot open that repository file and, under the target boundary, receives
no separate `output_schema`. Put every requirement the worker must follow
directly in `inventory.md`; transport-side validation may reject a submitted
response but is not worker-visible source material.

**`## Checkpoint while scanning` (244-257).** The cadence "after every four
closed nodes, or after 120 assigned source lines" is obsolete. The packet is the
checkpoint and transport validation is compulsory at each submission. Keep step
2's semantic invariant list, reframed as "before each packet report, verify …".
Remove checks for `files`, global ids, the final chunk cursor, and the repository
schema; those are transport responsibilities. Check only the self-contained
delta contract before submission.

**`## Finish` (264-274).** Replace document-final behavior with packet-final
behavior: close complete packet-local nodes, emit a continuation gap for any
incomplete prose candidate, reconcile same-delta local identities, and return
the delta. Drop final chunk cursor, `files`, save, `output-written`, global
forward-reconciliation, and return-a-path steps.

## Fixes in the edge-trigger change

- `## Looking back` says a look-back "never … resolves an endpoint to a
  `local_node`". That reads as forbidding what `### Reconcile forward local
  identities` does on node closure. Under the delta contract, scope this to
  look-back and same-delta identities; a prior-packet identity stays unresolved.
- State under `## How you receive source` that displayed row numbers are opaque
  chunk-local coordinates. The worker neither knows nor constructs source-file
  identities; the transport maps those coordinates after submission.

## Semantics retained

The six discovery slots, three edge triggers, proof ownership, node/edge/gap
semantics, record concision, and source-grounded uncertainty policy stay. Only
transport ownership, row-location encoding, packet-local ids, and the boundary
of forward reconciliation change.

## Follow-on outside this file

`_chunk_extractor.py:444-449` computes `fragment_path` and `progress_path`.
`progress_path` is unused. `fragment_path` is syntactically required by the
current dispenser's manifest-completeness check, but its value is not used to
choose the Voyage artifact path. Remove the field only together with that check
and its tests; do not treat it as an extractor-only cleanup.
