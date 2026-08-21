# Proof entities and dependency normalization

Date: 2026-08-21

## Purpose

Represent proofs explicitly during mathematical extraction so every proof has
an owned target and every graph-relevant prerequisite used by that proof has a
direct, source-grounded route to the result being proved. A later semantic pass
groups complementary proof fragments, merges each proof into its target, and
unions its direct dependencies into the target while retaining proof provenance.

The author-facing canonical graph remains a direct assumptions-to-results graph.
Proof entities and `proves` relationships are transitional semantic IR, not
canonical visualization nodes.

## Motivation

The controlled appendix experiment traversed every source coordinate exactly
once, yet missed 40 gold dependencies. Thirty-one were impossible after
inventory omitted an endpoint. Nine more were missed even though both endpoints
survived. Thirty-seven of 38 entity anchors and all 35 relationship evidence
spans were defective. The dominant structural gap is that a proof paragraph is
described as belonging to a result in the instruction, but that ownership is
not represented in either inventory or semantic IR. Consequently, proof uses
must later be reconstructed from conceptual relatedness, which produced both
omissions and thematic, reversed, or transitive false edges.

## Goals

- Extract explicit proof environments and unwrapped proof prose as temporary
  proof entities when they perform substantive inferential work.
- Require every retained proof to identify exactly one result that it proves.
- Represent proof ownership with a new `proves` relationship type.
- Represent graph-relevant proof uses as incoming `supports` relationships to
  the proof entity, with the smallest exact use span.
- Reconcile informal exposition, proof sketches, and formal proof environments
  that are complementary presentations of the same proof.
- Merge proof bundles into their proved result and redirect the union of their
  direct dependencies to that result.
- Preserve enough provenance to audit which proof fragment and exact use span
  caused every redirected canonical edge.
- Reject irrelevant prose, local algebra, navigation, motivation, and thematic
  adjacency rather than turning them into proof entities or dependencies.

## Non-goals

- Do not display proof entities in the canonical graph.
- Do not model every proof step, formula, bound, or temporary claim as a node.
- Do not treat every symbol or entity mentioned inside a proof as a dependency.
- Do not infer proof identity or target from proximity alone.
- Do not collapse genuinely alternative proofs into one proof bundle.
- Do not change iterator traversal, ownership, acknowledgement, or pooling
  semantics beyond carrying the new schema-valid records.
- Do not add benchmark-specific vocabulary or repair rules.

## Pipeline

The pipeline gains an explicit proof-reconciliation boundary:

1. **Inventory discovery.** Inventory workers emit ordinary mathematical node
   candidates, proof candidates, ordinary dependency leads, and `proves` leads.
   Proof candidates are recall-first but must have a source-grounded target or an
   unresolved target handle.
2. **Inventory pooling.** Pooling qualifies proof candidates and `proves` leads
   exactly as it qualifies existing nodes and edges. It performs structural,
   ownership, and schema validation but no semantic proof merging.
3. **Semantic extraction.** The normal extraction pass resolves candidates into
   ordinary entities and temporary proof entities. It emits incoming `supports`
   edges for actual proof uses and exactly one outgoing `proves` edge for every
   retained proof. It may exclude irrelevant proof-like prose.
4. **Proof reconciliation.** A fresh bounded semantic pass groups complementary
   proof fragments into proof bundles, distinguishes alternative proofs, resolves
   ambiguous proof ownership, and returns explicit normalization decisions. This
   is an LLM judgment boundary; grouping informal and formal presentations is not
   a deterministic string or adjacency operation.
5. **Deterministic normalization.** Runtime code validates and applies the
   reconciliation decisions, redirects dependencies, preserves provenance, and
   emits normalized semantic IR containing no proof entities or `proves` edges.
6. **Compilation.** The existing deterministic compiler accepts only normalized
   semantic IR and produces the unchanged canonical graph shape.

The proof-reconciliation worker receives a bounded packet containing only the
proof candidates, proposed targets, incident relationships, exact registered
source ranges, and necessary neighboring entity identities. It does not rescan
the paper or invent new candidates.

## Transitional semantic model

### Proof entity

A temporary proof entity uses `type: "proof"` and one of these initial kinds:

- `formal`: an explicit proof environment or equivalently delimited proof;
- `informal`: unwrapped prose that performs an argument for a named claim;
- `sketch`: an explicitly incomplete or high-level proof argument.

It otherwise follows the existing semantic entity identity, candidate,
description, and provenance rules. Its description identifies the proof
obligation and argument, not merely “Proof of X.” Its candidate provenance
anchors the complete proof fragment, while the proved result retains its own
smallest complete statement span.

### `proves` relationship

`proves` is added alongside `supports` and `illustrated-by` in inventory and
transitional semantic IR:

```text
proof entity --proves--> result entity
```

The direction is proof to proved result. A retained proof must have exactly one
outgoing `proves` relationship. The target must be an included non-proof entity
that is eligible to be proved. A proof may not prove itself or another proof.
The evidence identifies the source-visible ownership link: an explicit proof
heading, label, surrounding theorem structure, or exact prose connecting the
argument to its target.

Ambiguous ownership is represented through the existing unresolved/gap
mechanism during inventory and extraction. Transitional semantic IR may not
advance to normalization until every retained proof has one resolved target.

### Proof dependencies

Graph-relevant prerequisites point to the proof:

```text
assumption --supports--> proof
earlier result --supports--> proof
named external result --supports--> proof
reusable construction --supports--> proof
```

Each relationship cites the smallest exact span where the proof actually uses
the prerequisite. A mention, shared notation, proximity, or thematic relation
does not qualify. Proof-local algebra or a temporary claim remains evidence
unless it independently meets the ordinary graph-entity policy.

## Proof inclusion and exclusion

A source passage qualifies as a proof candidate only when all are true:

1. it contains substantive inferential work toward a mathematical claim;
2. its intended target is explicit or can be source-faithfully left unresolved;
3. its span can be separated from surrounding motivation, navigation, or
   commentary; and
4. representing it enables proof ownership or dependency evidence that would
   otherwise be lost.

Positive signals include explicit proof environments, “proof of” headings,
proof sketches, “to see this” or “indeed” passages with a unique mathematical
target, appendix arguments supplied for a body result, and an informal
explanation immediately developed into a formal argument.

Negative signals include motivation, intuition without an argument, duplicate
statement restatement, navigation, examples that merely illustrate, local
calculation with no reusable identity, and exposition whose target cannot be
distinguished from nearby claims. Uncertain material produces a gap or exclusion
decision rather than a guessed proof node.

## Proof bundles

A proof bundle groups fragments that collectively present the same proof. The
reconciliation pass may group an informal explanation, a sketch, and a formal
proof when they have:

- the same proved target;
- the same proof obligation;
- compatible argument structure or dependency path; and
- source evidence of continuation, expansion, restatement, or formalization.

Having the same target is necessary but not sufficient. Proofs using materially
different arguments remain separate alternative bundles. The reconciliation
output records, for every proof entity, its bundle, target, disposition, and
reason. Rejected fragments remain accounted for as exclusions or normalization
decisions.

Alternative proof bundles may both collapse into the same result. Their
dependencies are unioned into the canonical result because each records a
source-visible direct proof use, but the provenance sidecar retains bundle
membership. Consumers can therefore distinguish “used by at least one proof”
from “required by every known proof.” The canonical graph does not claim logical
necessity across all possible proofs.

## Deterministic normalization

For each accepted proof bundle targeting result `R`:

1. Validate that every member has exactly one `proves` edge to `R`.
2. Collect every incoming accepted direct dependency `D -> proof`.
3. Emit or merge `D -> R` with the same relationship type.
4. Preserve the union of exact proof-use evidence, candidate/hint/reference
   provenance, proof entity ids, and bundle ids on the normalization sidecar.
5. Merge with an existing `D -> R` relationship by endpoint and type without
   discarding either evidence route.
6. Remove proof entities, their incoming transitional edges, and their `proves`
   edges from normalized semantic IR.

Normalization rejects:

- a proof with zero or multiple `proves` targets;
- a target that is absent, excluded, or itself a proof;
- a redirected self-edge;
- unknown or duplicate normalization decisions;
- endpoint changes without registered evidence;
- an output retaining a proof entity or `proves` edge; or
- accounting that fails to dispose of every transitional proof and incident
  relationship exactly once.

Normalization does not perform transitive reduction. Directness remains a
semantic judgment established by the proof-use evidence and reconciliation
pass. Existing direct edges through represented intermediate objects remain
valid when the source also directly uses the prerequisite in the target proof.

## Artifacts and schemas

The design introduces three distinct contracts:

1. **Inventory schema revision.** Add `proof` to node type hints and `proves` to
   edge types. Preserve unresolved endpoints and exact location requirements.
2. **Transitional semantic IR revision.** Permit proof entities and `proves`
   relationships and require their proof-specific invariants.
3. **Proof-normalization decision/report schema.** Record proof bundles,
   dispositions, targets, redirected relationships, evidence provenance,
   alternative-proof membership, exclusions, and complete accounting.

Normalized semantic IR remains separately validated. It must contain neither
proof entities nor `proves` relationships before compilation. This may be a
separate normalized schema or a strict normalized profile enforced by the
normalizer and compiler; implementation planning must choose one explicit
contract rather than rely on an undocumented mode flag.

The proof-normalization sidecar is retained with diagnostics and experimental
artifacts but is not embedded in canonical renderer JSON.

## Instruction responsibilities

### Inventory

Inventory instructions must require workers to identify proof ownership before
acknowledgement, emit proof candidates for both explicit and unwrapped arguments,
record `proves` leads, and attach each graph-relevant proof use to the proof.
They must explicitly reject motivation, navigation, local algebra, and mere
mentions. Proof closeout accounts for the target, proof span, every direct
prerequisite, and any unresolved ownership question.

### Extraction

Normal extraction reconciles proof candidates without prematurely merging them
into results. It distinguishes proof entities from ordinary reusable results,
resolves `proves` targets, and accounts for every proof dependency hint. It does
not decide proof bundles unless the contract assigns that decision to the
separate reconciliation pass.

### Proof reconciliation

The new reconciliation instruction sees only registered proof-centered evidence.
It groups complementary informal/formal fragments, preserves alternative proofs,
rejects irrelevant prose, and returns exhaustive normalization decisions. It
may not create new mathematical entities or dependencies beyond the registered
proof candidates and incident evidence.

## Runtime and interface boundaries

The proof normalizer is a new same-skill private Python behavioral source under
`_rtx/` with a public facade interface routed through the existing module. It
uses only standard-library deterministic validation and transformation. The LLM
reconciliation step is an instruction interface owned by the parent skill; the
runtime applies its output but makes no semantic grouping decisions.

Authored source interfaces own their input/output and direct-I/O contracts.
Parent and runtime module blueprints export or route those interfaces without
copying contracts. Generated `SKILL.md` blocks and runtime dependency manifests
are refreshed only through the public blueprint synchronization interface.

The live repository schema version at implementation time is authoritative;
the current worktree uses schema version 6 even though one queried standard
projection still contains stale version-5 wording. All affected source,
interface, runtime, namespace, and parent versions advance bottom-up so stale
consumers cannot silently accept the new IR.

## Failure behavior

- Inventory validation fails before acknowledgement for malformed proof or
  `proves` records, preserving the current lease for correction.
- Pooling fails atomically on invalid qualified proof ownership or evidence.
- Extraction validation reports record-local proof target, dependency, and
  accounting errors through the existing correction mechanism.
- Reconciliation fails closed when proof grouping, target ownership, or evidence
  is ambiguous; it never falls back to same-target or adjacency merging.
- Deterministic normalization writes nothing until all proof records and
  decisions validate and account exactly once.
- Compilation rejects transitional IR so a proof entity cannot leak into the
  canonical graph accidentally.

## Testing strategy

### Schema and inventory tests

- Accept explicit formal, informal prose, and proof-sketch candidates.
- Accept `proves` only from proof to non-proof result.
- Reject proof-to-proof, result-to-result `proves`, self-target, absent target,
  malformed evidence, and out-of-owned-span proof records.
- Verify invalid proof acknowledgement leaves the same iterator lease retryable.

### Reconciliation behavior tests

- A fresh worker groups an informal explanation with the formal proof it expands.
- Same-target alternative proofs remain separate bundles.
- Motivation, restatement, navigation, and local algebra are rejected.
- Ambiguous unwrapped prose yields an unresolved disposition, not a guessed
  bundle or target.
- Every proof candidate and incident relationship receives exactly one decision.

### Normalizer tests

- Redirect one proof dependency to its proved result.
- Union dependencies across complementary proof fragments.
- Preserve separate alternative-bundle provenance.
- Deduplicate an edge already present on the result while retaining all evidence.
- Reject missing/multiple targets, self-edges, unknown decisions, incomplete
  accounting, and residual proof/proves records.
- Replace output atomically and preserve the previous artifact on failure.

### End-to-end tests

- Run explicit and unwrapped proof examples through inventory, pooling,
  extraction, reconciliation, normalization, and compilation.
- Verify the canonical graph shape remains proof-free.
- Verify every redirected canonical edge maps back to a proof bundle and exact
  proof-use span.
- Re-run the frozen appendix experiment with model, worker count, window, and
  non-proof extraction behavior held fixed. Compare missing nodes, missing
  direct edges, wrong edges, proof-target resolution, and anchor accuracy. Do
  not claim improvement without a no-change control or predeclared comparison.

## Acceptance criteria

- Every retained proof has one and only one resolved `proves` target before
  normalization.
- Every proof candidate, `proves` lead, and proof dependency is exhaustively
  accounted for.
- Informal and formal fragments of the same proof can be bundled from exact
  source evidence; same-target alternative proofs remain distinguishable.
- Canonical output contains no proof entity or `proves` relationship.
- Every redirected canonical dependency retains exact proof-use evidence and
  proof-bundle provenance.
- Invalid or ambiguous proof records fail before mutation or compilation.
- Existing non-proof graphs continue to normalize and compile without semantic
  change.
- Full math runtime tests, repository validators, public blueprint sync/check,
  and commit hooks pass.

## Principal risk and mitigation

The largest risk is extracting arbitrary proof prose and turning local logic
into graph dependencies. The mitigation is a three-part boundary: strict proof
inclusion criteria during inventory, semantic proof-bundle adjudication from
bounded registered evidence, and deterministic exhaustive accounting before
canonical compilation. Proof nodes are useful only as temporary ownership
containers; they do not relax the existing graph-entity or direct-edge policy.
