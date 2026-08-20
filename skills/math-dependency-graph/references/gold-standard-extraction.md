# Extract a Gold-Standard Mathematical Dependency Graph

Use this playbook to create or revise an evidence-backed gold graph for a fixed mathematical source scope. Gold construction must be independent of the production inventory and extract instructions so their omissions and biases do not become the benchmark answer.

The deliverables are schema-valid canonical graph JSON for direct comparison and rendering, plus an annotation sidecar that preserves source evidence, scope decisions, exclusions, identity mappings, and adjudication history.

## Freeze source and policy

Record the exact entrypoint, included files, selected sections or appendices, source hashes, canonical schema version, annotation-sidecar schema id and version, validator contract, and renderer version. For canonical graph JSON, the validation contract is `validate_math_payload` followed by `BaseRenderer.validate`, the same pair used by deterministic compilation; record the exact checkout command used to invoke it. Define and freeze the sidecar schema before annotation rather than validating against an implicit shape. Define the inclusion policy before annotation. It must say how to treat:

- assumptions, definitions, reusable setups, formal results, examples, applications, and substantive remarks;
- body or boundary results whose proof context is supplied by the selected scope;
- named external results that are indispensable to a proof;
- restatements, proof-only material, technical wrappers, navigation, and proof-local algebra;
- direct dependencies versus thematic, adjacent, or transitive relationships.

Use author-visible mathematical roles and environment names. Do not invent model-centric categories merely to make annotation easier.

## Keep gold annotators independent

Start each primary annotator in a new agent session with a distinct draft path. Give it only the frozen source, annotation policy, canonical output requirements, and evidence-sidecar requirements. Do not give it production worker prompts, production IR, prior experimental outputs, controller notes, another draft, or a list of known production misses. Prohibit annotator-to-annotator messages until their drafts are closed.

Annotators may work on disjoint source spans, but every output must use stable source identities so cross-file references can be reconciled. Preserve separate drafts until adjudication; do not let annotators concurrently edit one JSON file.

## Build the entity inventory

Read the scope linearly and create a candidate for every source-grounded graph entity permitted by policy. A candidate may span several paragraphs or environments. Preserve its author-visible label, title, environment, mathematical role, source location, and a concise source-faithful description.

Then audit every entity one by one against its cited source span:

1. Is it explicitly present or an allowed boundary/external entity?
2. Is its identity distinct from neighboring candidates?
3. Does its location cover the complete statement without absorbing another formal block?
4. Does its name and description match what the source establishes?
5. Should it be merged, split, excluded, or retained under the policy?

Record exclusions with reasons. A technical wrapper around an inner theorem is source structure, not a duplicate entity. A proof-local expression, algebraic step, or temporary claim is evidence rather than an entity unless the source gives it a stable mathematical identity and reuses it outside the immediate proof step. When that boundary remains ambiguous, record an unresolved decision rather than silently adding or dropping a node.

## Build only direct relationships

For every result, proof, construction, and application, identify each prerequisite actually stated or used. Add relationships in the invariant direction prerequisite -> dependent. Preserve the smallest source evidence span and a short explanation of the use.

Audit every direct edge one by one:

1. Do both endpoints resolve to the intended mathematical concepts?
2. Does the cited source show an actual dependency rather than proximity or thematic relevance?
3. Is the edge direct, or is an intermediate represented entity being skipped?
4. Is the direction correct?
5. Is a named external result or explicit local assumption missing as an endpoint?

Also inspect every explicit reference, citation, named theorem, proof invocation, and substantive dependency statement. Each must map to an entity, direct edge, exclusion, or documented unresolved decision.

## Reconcile drafts and cross-file identities

Merge candidates by mathematical identity, not spelling alone. Resolve labels, restatements, body/appendix boundaries, named external tools, and repeated prose descriptions. Preserve a sidecar mapping from draft identities and source locators to final canonical ids. Mathematical identity controls only when the cited spans support the same object; if stable source locators conflict with the proposed identity, keep the records distinct and contested until the source or an adjudicator resolves the conflict.

After merging, perform a reverse audit from source to graph for omissions and from graph to source for unsupported content. Do not treat absence from another draft as proof that an item is false.

## Adjudicate disputed gold

Every proposed addition, deletion, merge, split, or retargeting is contested until independently reviewed. Start at least two fresh independent reviewers in new sessions with separate output paths. Give each only the frozen policy, exact source evidence, and proposed decision; withhold other reviewers' verdicts. If they disagree, start a third isolated session as tie-breaker focused on the disputed policy question. Record all verdicts and reasons in the sidecar. The controller is the only role that edits the candidate gold bundle after adjudication.

Do not call a production output item a false positive solely because it is absent from gold. First verify it against source and policy. If it is valid, correct gold and update all affected denominators and mappings.

## Validate and inspect

Before freezing a gold version:

1. validate the annotation sidecar against the recorded sidecar schema id and version;
2. validate the final canonical graph JSON with the recorded `validate_math_payload` plus `BaseRenderer.validate` contract;
3. verify unique entity and relationship ids and resolvable endpoints;
4. verify every entity and relationship has source evidence or an explicit allowed boundary/external justification;
5. render HTML from the canonical JSON;
6. inspect the HTML for missing nodes, disconnected results, implausible hubs, duplicate identities, reversed arrows, unreadable descriptions, and incorrectly shaped external results;
7. sample source-to-graph and graph-to-source mappings again after rendering.

Record entity and direct-edge counts, unresolved decisions, reviewer disagreements, validation commands, artifact hashes, and elapsed time. Freeze the source snapshot, policy, schemas, validator and renderer versions, sidecar, canonical JSON, and HTML in a new versioned directory with a manifest of relative paths and SHA-256 hashes. Make the directory read-only by procedure: never revise or overwrite its members in place.

Gold is reviewed evidence, not immutable truth. Future experimental disagreements may reopen it, but only through the same source-backed independent-review procedure. Reopening creates a new versioned directory, preserves the earlier bundle unchanged, and records its parent version, proposed changes, adjudication records, changed denominators, and replacement hashes.
