# Inventory Edge-Recall Design

Proposed changes to `skills/math-dependency-graph/instructions/inventory.md`.

## 1. Why

### What was measured

Paper: `arXiv-2602.13450v2`, appendix scope — `appendix-dynamics.tex`,
`appendix-prevalence.tex`, `appendix-bayes.tex`, `appendix-application.tex`
(1303 lines). All four files hash-identical to
`assets/inference-from-random-restarts` and to the benchmark manifest.

Two golds were used. `results/inventory-gold.json` is inventory-stage: 83 nodes,
98 edges, including 29 proof nodes. `results/semantic-gold.json` is final-stage:
53 entities, 63 edges, no proof entities.

Stage-matched, against `inventory-gold.json`, span-overlap matching on both
endpoints:

| inventory stage | node recall | node precision | edge recall | edge precision |
|---|---:|---:|---:|---:|
| voyages (2026-08-24 run) | 80.7% (67/83) | 83.8% (67/80) | **60.2%** (59/98) | 69.4% (59/85) |
| v69 `pass-10` | 47.0% (39/83) | 90.7% (39/43) | **7.1%** (7/98) | 28.0% (7/25) |

Against `semantic-gold.json`, for reference, a single-worker whole-document
prompt (the `instructions/extract.md` in force at `316f407a`, immediately before
the v69 pipeline rewrite) delivered **92.5% node recall and 85.7% edge recall**.
Projecting the voyage inventory onto the same gold gives a **ceiling** of 84.9%
nodes and 49.2% edges — the best its extract stage could achieve, since extract
merges and prunes but never adds.

### Diagnosis

The voyage architecture is a large improvement on v69 — 1.7x node recall and
8.5x edge recall at the inventory stage. Its remaining weakness is
disproportionately in **edges**, not nodes: 80.7% vs 60.2%.

`inventory.md` shows the same asymmetry. It spends roughly 337 words on six
**node**-discovery slots and roughly 154 words on edges (Slot 5 plus "Encode a
direct dependency"). Edges are a sub-clause of a node-finding document, and
performance follows the word budget.

Characterising the 39 missed gold edges:

| signal | finding |
|---|---|
| touching a proof node | 34/39 (87%) |
| by basis | 13 `explicit-reference`, 13 `proof-use`, 10 `explicit-prose`, 3 `mathematical-inference` |
| missing endpoint node type | 14 `proof`, 8 `result` |
| both endpoints already existed as nodes | **19/39** |
| separation >= 100 lines | 30% of misses vs 12% of matches |

Supporting observations:

- Unresolved endpoints were used **twice in 104 edges**. The mechanism exists
  ("Otherwise use an inline unresolved endpoint") but is treated as a last
  resort. Nothing in the six slots is triggered by a *reference*; every slot
  fires on *encountering* mathematics.
- Edge representation is internally inconsistent: the run routed 57% of edges
  through proof nodes against gold's 90%, emitting 9 `setup->proof` alongside 9
  `setup->result`. Slot 5 mandates `prerequisite-to-result`; the
  `### Proof candidates and ownership` section mandates
  `prerequisite --supports--> proof entity`. Same document, two shapes.
- The worker cannot look back. `_voyage_support.report_data` returns one packet
  plus `prior_inventory`; there is no operation to re-request an earlier packet.
  Packets average ~260 lines (`_PACKET_CHARS = 3000`, anchor-aligned; 5 packets
  over 1303 lines), which is the scale at which the long-range miss enrichment
  appears.
- `cursor` is used four times (lines 149, 159, 177, 179) and never defined.

### Two hypotheses that were tested and rejected

**Representation mismatch is not the cause.** Collapsing both sides onto
(prerequisite -> owning result) moved edge recall to 54.7%, *worse* than the
literal 60.2%. The misses are genuine disagreements about what depends on what,
not encoding differences. The Slot 5 contradiction is a correctness defect worth
fixing for measurement hygiene, not a recall lever.

**Chunking is not the cause.** Only 3 of 63 gold edges cross the chunk boundary
(4.8%), and only 2 endpoints in the whole run required an unresolved scope hint.
The costly constraint is the monotone single-pass reading rule, not the
partitioning. No change here proposes abandoning chunked voyages.

### What these changes do not address

**19 of 39 misses had both endpoints already present as nodes.** The worker held
everything it needed and did not emit the edge. That residue belongs to the
per-unit accounting in the forward loop and is untouched by everything below.
Expect a ceiling well short of 100% even if every change lands perfectly.

The 8 missing `result` endpoint nodes are `\restatable` macros
(`\lemLipschitz*`) whose declarations live in `sections/model.tex`, outside the
appendix scope. That is an artifact of benchmarking a fragment and is not
treated as a defect.

## 2. Assumed

`cumulative_packets_file` is supplied to the worker and holds every packet shown
so far, in order, line-numbered. It never contains source ahead of the cursor.
Implementation is owned elsewhere (a `MachineStep`, `mode="repeat-safe"`, that
renders packets `0..index` before each report step).

Packets no longer carry `@@ source:` headers.

**Open dependency:** `statement_location` requires a `file_index`. With
`@@ source:` removed the worker has no in-text source of file identity, so that
field must come from `coordinates` or be derived engine-side. This needs a
deliberate decision from whoever owns the dispenser change.

## 3. Changes

Anchors refer to the worktree copy of `inventory.md` (157 lines).

### 1. New section after `## Goal`, before `## Output contract`

Define three terms the document already relies on:

- **packet** — you never read source files; `status` hands you one packet at a
  time; its `text` is line-numbered `NNNN | `; those are true source line
  numbers, to be used in every location field.
- **`cumulative_packets_file`** — holds every packet shown so far, in order.
  Never contains source you have not been given.
- **cursor** — the reading unit you are currently on. Advances one unit at a
  time and never moves backward.

### 2. Add to that section: source-access prohibition

> The current packet and `cumulative_packets_file` are your only authorized
> views of the source. Source file names appear only so you can record true
> locations — never open them.

No such rule exists in the file today.

### 3. Line 32 — narrow the blanket prohibition

Replace *"Do not read the whole chunk first, prepare a separate census, or
return for a second semantic pass."* with: no reading ahead of the cursor, no
census pass **before** the loop, no second semantic pass over the chunk. Add:
"Bounded look-back into `cumulative_packets_file`, as described below, is not a
second pass."

### 4. New `## Looking back` section, after the forward loop

> Consult `cumulative_packets_file` by searching it, never by reading it whole:
>
> ```
> grep -n 'label{prop:measurable}' "$cumulative_packets_file"
> sed -n '118,137p' "$cumulative_packets_file"
> ```
>
> To re-check a node you already recorded, use its `statement_location`
> directly — no search needed.
>
> Never read back more than 20 lines or 1000 characters in one read, and look
> back at most three times per reading unit. If three reads have not resolved
> the question, record a gap.
>
> A look-back informs only the edges of the unit at your cursor. It never
> creates a node for an earlier unit, revises a closed record, or moves your
> cursor.

Cap justification: non-proof gold statement spans are median 6, p90 17, p95 26,
max 28 lines. 20 covers p90 in one read; longer statements take a second
targeted read. The count limit prevents the per-read cap being defeated by
repetition; the gap gives a defined exit instead of improvisation.

### 5. New `## Edge triggers` section, parallel to the six slots

> The six slots ask whether this unit *introduces* mathematics. An edge trigger
> asks whether it *uses* mathematics introduced elsewhere. Check all three at
> every unit. They fire independently of the node decision: a unit that creates
> no node can still emit several edges.
>
> **E1 — Explicit reference.** Every `\ref`, `\cref`, `\eqref`, `\autoref`, or
> citation command in this unit is an edge sighting. Emit a lead from the
> referenced object to the owning result. **You do not need to have seen the
> target defined.** If it has no local handle yet, emit an unresolved endpoint
> carrying the label text and keep scanning. A reference you cannot resolve is
> still an edge; silently dropping it is the failure this trigger exists to
> prevent.
>
> **E2 — Proof use.** (moved from Slot 5)
>
> **E3 — Prose use.** Direct use stated in words — "by", "using", "applying",
> "it follows from" — with no reference command.

### 6. Line 40, loop step 3 — update the count

*"Check all six discovery slots"* becomes: check the six node slots **and the
three edge triggers**. Step 3 currently names only node slots, leaving edges
entirely to step 4.

### 7. Line 73, Slot 5 — resolve the self-contradiction

Delete Slot 5's `prerequisite-to-result` edge rule and point it at E2 and
`### Proof candidates and ownership`, which mandates
`prerequisite --supports--> proof entity`. Keep one shape.

### 8. Line ~140 — unresolved endpoints become the default

*"Use a local node handle when available. **Otherwise** use an inline unresolved
endpoint"* becomes: an unresolved endpoint is the expected output whenever the
target has no handle yet, not a last resort.

### 9. Line 122 — scope the reread prohibition

`### Reconcile forward local identities` says *"do not reread source"*, which
now contradicts change 4. Scope it: reconciliation uses only saved records;
look-back for edge discovery at the cursor is a separate, permitted activity.

### 10. Line 65, Slot 3 — add the anti-omission sentence

> Ambient assumptions must not disappear. Attach them directly to every result
> whose statement or proof uses them.

Current text is cursor-scoped ("if the current result directly invokes one").
Four `assumption->proof` misses sit in this class.

## 4. Sequencing

These are three independent hypotheses. `references/experimental-improvement.md`
requires one change per pass for interpretable results.

- **Commit A** — changes 5, 6, 7, 10. Edge triggers. Targets the 13
  `explicit-reference` and 10 `explicit-prose` misses. Depends on nothing.
- **Commit B** — changes 1, 2, 3, 4, 9. Look-back. Targets the long-range
  residue. Blocked on `cumulative_packets_file`.
- **Commit C** — change 8. One line. May ride with A.

Recommended order: A alone first, measured against `inventory-gold.json` with
the metrics in section 1, then B.
