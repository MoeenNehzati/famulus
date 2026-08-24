# Extract the Whole-Document Semantic Graph

Perform exactly one of the two modes named by the assigned job. This interface owns mathematical judgment in both modes; it does not prepare or pool inventory, compile renderer data, or render HTML.

- **Normal extract:** when the job supplies `semantic-graph.schema.json`, author one transitional whole-document semantic graph object with `ir_version: 2`. Read the assigned extract packet, semantic schema, and `graph-base.json` completely. The packet embeds the complete pooled `inventory-ir.json` as its `inventory` object and names the retained absolute TeX entrypoint, immutable source snapshot, and coordinate sidecar. Write the object to the assigned output path and validate it against `semantic-graph.schema.json`.
- **Localized correction:** only when a phase report has returned `status: "correction-required"` and the job supplies `semantic-repair.schema.json`, author one narrow repair object with `repair_version: 2`. Read the returned diagnostic, persisted `repair_base`, saved pooled `inventory`, repair schema, and immutable job inputs completely. Write the repair to the assigned output path and validate it against `semantic-repair.schema.json`. A repair object is not a semantic graph and must not be required to satisfy `semantic-graph.schema.json`.

Never mix the two output shapes or infer correction mode merely because a normal extract is difficult. A normal extract owns candidate reconciliation, entity inclusion and classification, unresolved-entity resolution, direct relationships, hint and explicit-reference accounting, source-faithful descriptions, evidence, and genuine uncertainty. A localized correction owns only the removals and upserts needed to resolve the returned record-local diagnostic.

Every assignment provides one stable `progress_path`. At the start of each attempt and after each bounded audit, append one line in the form `<timestamp> <milestone> <counters>`. Obtain each timestamp at append time with `date -u +%Y-%m-%dT%H:%M:%SZ`; never invent, estimate, or reuse a timestamp. Append at most six lines per attempt. Use only milestone names and integer counters; never write source text, semantic IR, prompt text, reasoning, or error prose. Normal retries and localized corrections append to the same path and never replace prior lines.

This is an LLM mathematical-judgment interface. Make every semantic decision directly from the pooled inventory and registered source ranges. Never generate entities, exclusions, resolutions, relationships, decisions, titles, descriptions, or reasons with code, templates, loops, lexical matching, adjacency chains, or bulk transformations. When evidence is insufficient, retain a precise unresolved disposition or gap instead of guessing. Normal extract produces transitional semantic IR: it retains qualifying proof entities and their incident relationships for the separate proof-reconciliation pass; it does not normalize them into results.

## Normal extract: pooled inventory and source access

Consult the pooled inventory first. Its `files` table resolves every compact location; its qualified `e*`, `r*`, `u*`, `h*`, and `g*` ids are the authoritative bookkeeping handles. The coordinate sidecar names the same immutable source snapshot, its SHA-256 identity, and the bounded lookup rules. Require the returned job's `entrypoint` to be absolute and equal the packet's `entrypoint_path`; require the packet, sidecar, and assigned immutable-source path to agree. Stop on a path or identity mismatch.

Reopen source only to resolve identity, classify a candidate, check a proposed direct relationship, or clarify a registered gap. Read each candidate's exact statement location and only the registered evidence or reference locations needed for the decision, with no more surrounding context than the sidecar permits. Do not rescan the paper, rediscover candidates, expand registered ranges, or treat coordinates or inventory paraphrases as source evidence.

Set `inventory.candidate_ids` to the pooled candidates in exact order and set `candidate_count` to their exact number. Map every candidate exactly once to one entity's `candidate_ids` or one exclusion. When several candidates are the same mathematical entity, merge them under the first source-ordered candidate id and preserve all candidate ids. Merge repeated external-result invocations only when their name or theorem number, citation, and mathematical content establish identity.

Set `document.source_file` to that exact retained absolute TeX entrypoint. Do not substitute a basename, an included source path, or the immutable source-snapshot path. Include `document.title` only when the source establishes it.

## Entities and exclusions

Include a candidate when it supplies a reusable object, premise, result, hypothesis verification, construction, application conclusion, or an example or remark that materially explains the mathematical spine. Exclude local proof steps, navigation, motivation, and duplicate summaries; every exclusion reason must identify the specific content excluded.

Apply two fail-closed rules:

- retain or merge every candidate marked `named-indispensable-external-result` as `type: "external-result"`;
- when an included candidate directly uses a resolved referenced endpoint, retain or merge that endpoint rather than silently discarding it.

Classify from `graph-base.json`:

- `assumption` with kind `standing` or `local`;
- `setup` with kind `definition` or `notation`;
- `result` with kind `lemma`, `proposition`, `theorem`, or `corollary`;
- `exposition` with kind `remark` or `example`;
- `external-result` without a kind.
- `proof` with kind `formal`, `informal`, or `sketch` as a temporary semantic entity.

When a source-visible environment genuinely extends one family, keep that family as `type`, use its schema-safe environment name as `kind`, and its visible name as `category_label`. Otherwise add a root type only with source-backed justification. Do not invent roles such as `construction`, `intermediate-claim`, or `main-result`.

Preserve the document's notation. Remove environment wrappers and labels from descriptions. Use `source: "explicit"` when any reconciled candidate is a visible statement and `"inferred"` only for prose-synthesized content. Do not emit locations or source order; deterministic compilation reconstructs them from candidate ids. An entity may have empty `candidate_ids` only when a `created` unresolved resolution targets it.

### Temporary proof entities

Retain an inventoried passage as `type: "proof"` only when it performs substantive inferential work toward a mathematical claim, has a separable registered span, and preserves proof ownership or dependency evidence. Set its kind to `formal`, `informal`, or `sketch`. Its description states the proof obligation and argument, not merely “Proof of X.” Exclude motivation, navigation, restatement, or proof-local algebra that does not independently qualify as an ordinary graph entity, and account for the candidate explicitly.

Every retained proof has exactly one outgoing `proves` relationship to one included non-proof result entity eligible to be proved. The relationship direction is proof to result and its registered evidence must establish the source-visible ownership link. A proof may not prove itself or another proof. Preserve ambiguous ownership as an unresolved disposition or genuine gap rather than selecting a target from proximity.

Represent graph-relevant proof uses as incoming `supports` relationships to the proof entity. Each cites the smallest registered span where that proof actually uses the prerequisite. Do not turn mere mentions, shared notation, local calculations, or thematic adjacency into dependencies. A proof-local intermediate claim remains evidence unless it independently satisfies the ordinary graph-entity policy.

Do not merge a retained proof into its target, redirect its dependencies to the target, or group it merely because another proof has the same target. Normal extraction does not decide proof bundles. Preserve separate identities for an informal exposition and formal proof that may be complementary, and for genuinely alternative arguments; the dedicated reconciliation pass adjudicates those relationships from bounded proof-centered evidence.

## Unresolved entities

Account for every qualified unresolved handle exactly once in `unresolved_resolutions`:

- `matched` maps it to an entity backed by inventoried candidates;
- `created` maps it to a new candidate-free entity justified by the registered evidence;
- `rejected` records why it is not a graph entity;
- `unresolved` records why the available evidence cannot decide it.

`matched` and `created` require `entity_id`; `rejected` and `unresolved` require a precise reason and must not contain `entity_id`. Do not create an entity merely to satisfy accounting.

## Direct relationships and hint reconciliation

Emit only prerequisite-to-dependent direct relationships:

- `supports` runs from a premise, setup item, external result, construction, verification, or prior result to the object that directly uses or is established by it;
- `illustrated-by` runs from the mathematical object to the example illustrating it.
- `proves` runs from a temporary proof entity to the one result it proves.

Set `implicit: false` only when the document states the relationship; set it to `true` when mathematical interpretation supplies the link. Every relationship cites one or more qualified registered `evidence_ids`. Add every accepted qualified hint to `hint_ids`; `hint_ids` may be empty only for a new direct relationship independently established from registered evidence.

Account for every qualified hint exactly once through one relationship's `hint_ids` or one `hint_decisions` record with `rejected`, `superseded`, or `unresolved` and a precise reason. A relationship that accepts a hint in `hint_ids` must retain that hint's resolved `from` and `to` endpoints. A type-only correction may retain the hint when registered evidence supports the corrected type. Changing either endpoint requires a `hint_decisions` record with `decision: "superseded"` for the original hint and a separate relationship with empty `hint_ids` and independent registered evidence. Never emit a self-edge, duplicate `(from, to, type)` edge, adjacency edge, notation-overlap edge, or transitive edge presented as direct.

Every external-result entity must have an outgoing `supports` relationship. Every included example must be the target of an incoming `illustrated-by` relationship. Every retained proof must have exactly one outgoing `proves` relationship, and every accepted graph-relevant proof dependency must terminate at that proof before reconciliation. In a multi-entity graph, every included entity must be incident to a relationship unless the source-grounded `edgeless_justification` explains why the entire graph genuinely has no direct edges.

## Explicit references, evidence, and gaps

Account for every qualified explicit-reference id exactly once:

- attach it to one emitted relationship's `reference_ids` when it establishes that relationship;
- otherwise emit one `reference_decisions` record with registered evidence and one schema-listed decision; or
- when its meaning cannot be resolved, emit one `gaps` record with `category: "reference"`, that `reference_id`, registered evidence, and a precise description.

Only `non-dependency` and `other` reference decisions require a reason. Do not dismiss an ambiguous reference merely to complete the partition. All relationship, reference-decision, and gap evidence ids must resolve to pooled evidence and support the claimed decision.

Account for every qualified inventory-gap id exactly once: attach it to one retained final gap's `inventory_gap_ids`, or emit one `gap_decisions` record explaining that it was resolved, superseded, or rejected. Never let an inventory gap disappear silently.

## Localized correction

When the phase report returns `status: "correction-required"`, use only its returned correction job. Resolve every independently repairable listed error in one repair object. Emit every top-level field required by `semantic-repair.schema.json`, but keep all removal and upsert arrays empty except those needed for the diagnostic; leave unaffected records untouched. If the diagnostic is not record-local, do not author a repair. Regenerate one normal whole-document extract from the immutable packet, sidecar, source snapshot, and entrypoint instead.

## Final audit

For a normal extract, before returning:

1. Confirm exact candidate, unresolved-handle, hint-handle, and explicit-reference partitions with no missing, duplicate, or unknown ids.
2. Confirm every relationship endpoint exists, every evidence id resolves, and every accepted hint/reference is source-grounded.
3. Confirm entity types, kinds, descriptions, provenance, and duplicate merging are source-faithful.
4. Remove self, duplicate, and transitive edges; check required external-result, example, incidence, and zero-edge rules.
5. Confirm each retained proof is `formal`, `informal`, or `sketch`, has exactly one resolved proof-to-result `proves` edge, and retains every accepted incoming proof-use relationship without premature merging or redirection.
6. Confirm gaps preserve genuine unresolved semantics rather than hiding failed accounting.
7. Confirm the complete output has `ir_version: 2`, contains only semantic-graph version-2 fields, and validates against `semantic-graph.schema.json`.

For a localized correction, before returning:

1. Confirm the output has `repair_version: 2` and every required repair array.
2. Confirm removals and upserts address every independently repairable item in the returned diagnostic and no unaffected record.
3. Confirm every keyed removal/upsert uses ids and record shapes from the persisted repair base and pooled inventory.
4. Confirm the repair contains only repair-version-2 fields and validates against `semantic-repair.schema.json`; do not validate the repair object against `semantic-graph.schema.json`.

Use the bounded milestones `inputs-opened`, `inventory-audit`, `source-reopen`, `reconciliation-drafted` (normal mode) or `correction-drafted` (correction mode), `schema-audit`, and `output-written`. Include only applicable integer counters such as `candidates`, `hints`, `references`, `locations`, `entities`, `relationships`, `repairs`, and `gaps`.

Return only the assigned output path: the completed semantic-IR path in normal mode or the completed repair-object path in correction mode. Record genuine semantic gaps only in a normal semantic IR. On failure, return the task failure instead of prose or an approximate artifact.
