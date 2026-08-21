# Improve the Graph Skill Through Controlled Experiments

Use this playbook to improve `math-dependency-graph` by running fresh subagents on representative mathematical source and comparing their canonical output with a potentially flawed gold standard. This is a maintainer evaluation workflow. Never give this file, the gold graph, prior outputs, score reports, or benchmark-specific hints to inventory or extract workers.

## Fix the experimental conditions

Before a pass, record:

- the immutable source snapshot, entrypoint, and exact source scope;
- the prepared-input SHA-256 and the iterator version;
- the requested and effective worker counts and the window character limit;
- the scanner version, inventory schema version, and setup and next interface versions;
- the current skill and instruction versions;
- the model id and reasoning level for every worker;
- the proof-reconciliation worker model id and reasoning level when the extract contains proof entities;
- the gold version and its annotation policy;
- the canonical schema and validator used for final output;
- the acceptance metrics and the single question tested by this pass.

Obtain these identities from public contracts and returned reports. The setup response supplies the durable iterator identity, persisted internal timings, assignment boundaries, and complete prose-free coordinate universe. If another timing boundary required by the hypothesis is not publicly observable, record a contract gap and stop the pass; do not recover it by inspecting private iterator storage or inventing a field.

Use a fresh run directory. Preserve every input, worker artifact, progress sidecar, diagnostic report, canonical JSON file, discrepancy ledger, score, and adjudication. Never overwrite an earlier pass.

## Keep roles separated

1. **Graph workers are context-free.** Start each worker in a new agent session whose initial message contains only the production job and its returned paths. Inventory and extract workers receive only the material returned by the production skill. They must not see gold, prior passes, benchmark reports, proposed fixes, controller notes, or another worker's output. Give parallel inventory workers distinct fragment and progress paths and prohibit cross-worker messages. An inventory worker receives source text only in responses from the public next interface: it must not read the prepared input, controller-only packets, iterator state, or another worker's artifacts.
2. **The evaluator sees both artifacts and the source.** It compares the completed canonical graph with the current gold by mathematical identity, source location, labels, descriptions, and directness—not by generated ids alone. It adjudicates every discrepancy from source evidence and the frozen annotation policy before counting errors.
3. **Gold adjudicators see the dispute.** They receive the annotation policy, exact source evidence, current gold decision, and contested proposed correction. They do not edit artifacts.
4. **The controller owns diagnostics.** It invokes public setup exactly once, serializes lifecycle events, preserves artifacts, calculates metrics, and decides the next experimental hypothesis. Semantic workers never manage benchmark thresholds. Use a controller-owned invocation recorder that starts the outer monotonic clock immediately before each public setup or next invocation, atomically preserves the complete public response and elapsed outer gateway latency before releasing it to a worker, and then returns that response unchanged. It retains the controlled-child timing returned by every setup and next response; neither timing layer may be estimated from or subtracted from the other.

## Run one pass

1. Prepare the source through the live public skill route. Invoke the public setup interface exactly once with the frozen worker and window configuration. Preserve the exact requested worker and window arguments, its response, effective assignments, setup summary, and timing before launching workers; a failed setup attempt remains part of the pass record.
2. Queue one fresh inventory worker for each effective assignment with the chosen model and reasoning level. Each worker repeatedly invokes only the public next interface for its assigned state and worker index, consumes the returned unit in order, and updates only its assigned inventory and progress artifacts. Require it to validate the complete worker-owned inventory before every acknowledgement and acknowledge every returned unit with that exact unit id. It must close each attention sequence with `--wrap` when the consecutive mathematical context ends and continue until an acknowledgement returns `complete`; a lease response alone is not completion.
3. Preserve every public next response and record queue time, worker time, progress checkpoints, calls, acknowledgements, wraps, retries, and failures separately. The controller recorder, not a semantic worker, owns exact response and timing retention: a worker must not transcribe, summarize, or truncate either one. If atomic capture fails, stop the pass instead of reconstructing a response from worker context, logs, or private state. Build the controller-owned attention-sequence record from returned unit ids, character counts, wrap choices, completion states, and observed call times; record worker index, first and last unit ids, unit and character counts, closure reason, elapsed time, and whether it remained open at collection. Do not ask a worker to read iterator storage for this accounting.
4. Before pooling, require the report returned by the single setup call or the pooling report to expose exact coordinate coverage. Compare its complete assignable coordinate universe with the multiset union of unit coordinates and report exact covered, missing, and duplicate counts plus all coordinate gaps and overlaps. Report structural-context-only coordinates separately. Require each effective assignment to be nonempty, each unit to have one owner, every leased unit to have one accepted acknowledgement, and every worker to reach `complete`. If the public reports do not expose this accounting, classify the experiment as unobservable rather than inspecting private state.
5. Pool once all initial fragments validate and iterator completion authenticates them. Retry only schema, ownership, anchor-coverage, or accounting failures. Do not dispatch compaction or wording-rewrite work. Size is an evaluation signal, not a worker task. Preserve the authenticated iterator summary alongside the pooled version-2 inventory passed upstream to extraction.
6. Run one fresh extract worker and finalize through the live route. When proof entities are present, preserve the transitional semantic artifact and bounded proof packet, then run exactly one fresh context-free proof-reconciliation worker from only that packet. Record its model id, queue time, worker time, retries, decisions artifact, and stable progress artifact separately from extraction. Submit its decisions through the production normalization stage; preserve the normalized semantic IR, compiler-facing projected inventory, and proof provenance artifact before compile. Record proof target counts, accepted and excluded proof counts, proof bundle counts, alternative-bundle counts, redirected dependency counts, and provenance coverage linking every redirected edge to its exact bundle, proof, and registered evidence. Define the alternative-bundle count as the number of distinct bundles belonging to a target that has more than one distinct accepted bundle. Record the total number of redirected canonical relationships together with every redirected relationship's complete bundle, proof, and evidence routes so coverage is mechanically computable. If no proofs are present, record that the optional stage was not entered rather than reporting zero worker time. Validate the normalized semantic and canonical schemas and require the canonical graph to be proof-free. The resulting canonical JSON is the authoritative experimental output. Iterator traversal does not create a second extraction iterator or alter the extract worker's pooled-inventory reconciliation obligations.
7. Build a discrepancy ledger covering every gold-only node or edge, every output-only node or edge, and every non-equivalent match. For each discrepancy, inspect its exact source evidence and the frozen annotation policy, then decide whether the worker is wrong, the gold is wrong, both are wrong, the difference is an allowed granularity choice, or the evidence remains unresolved. Do not count a discrepancy as an error before this decision.
8. Independently adjudicate every proposed gold correction, update gold only when accepted, and recompute mappings and denominators after any correction.
9. Categorize the confirmed worker mistakes by observable failure type, pipeline stage, and underlying cause. Develop proposed solutions for the categories, connecting each solution to its supporting discrepancies, expected effect, risks, and a measurable paper-independent test.
10. Write the pass report, including the discrepancy ledger, adjudications, mistake taxonomy, causes, and proposed solutions, before changing instructions, schemas, or runtime.

## Measure time and space

Report both wall-clock and allocation detail:

- source preparation, planning, pooling, validation, and compilation duration;
- setup internal scan, unitization, partition, database, validation, and total duration;
- next internal validation, transaction, lookup, serialization, and total aggregates;
- public controlled-child process dispatch, publication, and total durations, with publication present only when fresh setup actually publishes state;
- outer gateway latency for every setup and next invocation, kept distinct from the public controlled-child and iterator-internal timing layers;
- per-attempt queue time and worker time for inventory, extract, and the proof-reconciliation worker;
- retry count, retry reason, and time spent in failed or discarded attempts;
- iterator calls, acknowledgements, wraps, retries, failures, closed-sequence data, and any open-sequence aggregate;
- raw and canonical bytes for each inventory fragment, pooled IR, semantic IR, and canonical graph, with each artifact path, size, and hash preserved;
- local fragment-to-owned-source ratios and the aggregate canonical-fragment ratio;
- entity, relationship, exclusion, unresolved-resolution, and gap counts.
- proof target, proof bundle, alternative-bundle, excluded-proof, redirected-edge, and proof provenance counts, plus normalization stage duration and artifact hashes.

Never combine the three timing layers. Iterator-internal timings describe work inside setup or next; public process timings describe the controlled child used by that interface; outer gateway latency includes the surrounding dispatch and response boundary. Record returned measurements verbatim, record unobserved publication as absent rather than zero, and reject a pass whose required timing layer cannot be observed.

Use the diagnostics record rather than an informal percentage. The local ratio is kind `inventory-fragment-to-owned-packet`: canonical bytes for one fragment's records divided by the bytes of source lines owned by that job. The aggregate ratio is kind `pooled-canonical-fragments-to-owned-packets`: deduplicated canonical bytes for all retained fragments divided by the union of their owned source-line bytes. A value above 0.50 locally or 0.35 in aggregate crosses the controller-facing reference threshold. Crossing one does not invalidate the graph and must not cause an LLM compaction pass. It indicates that the general instructions or schema may be spending space poorly.

## Adjudicate discrepancies and score mathematical quality

Start with semantic matching rather than exact generated ids. Record every proposed match and every discrepancy in a ledger with output identity, gold identity when present, exact source evidence, policy basis, adjudication status, and final outcome.

Adjudicate every discrepancy before computing final error counts. The allowed outcomes are:

1. **Worker error:** the gold is supported and the worker is wrong.
2. **Gold error:** the worker is supported and the gold is wrong; correction remains contested until independently reviewed.
3. **Both wrong:** neither representation matches the source and policy.
4. **Allowed difference:** both are defensible under the policy, such as an explicitly permitted granularity choice.
5. **Unresolved:** source evidence or policy cannot yet decide the issue.

Apply this procedure symmetrically to gold-only and output-only items. A missing gold item is not automatically a worker omission, and an output-only item is not automatically a false positive. Reopen the exact source spans needed to decide each case; representative sampling is insufficient for the discrepancy ledger.

After adjudication, report at least:

- node recall: gold entities represented by an equivalent output entity;
- direct-edge recall: gold direct dependencies whose prerequisite, dependent, and direct use all match;
- unmatched output entities and relationships;
- schema or identity errors, wrong endpoint choices, reversed edges, indirect edges, merges, splits, and description defects;
- source-grounded output items absent from gold;
- gold items rejected or corrected through adjudication;
- unresolved and allowed-difference items excluded from worker-error counts.

Similarly, do not credit a merely related relationship. Direct-edge recall requires the correct two endpoint concepts and direct dependency semantics.

## Categorize causes and design solutions

Group only confirmed worker mistakes. Useful general categories include node omission, unsupported node, mistaken merge or split, wrong mathematical role, missing direct edge, wrong endpoint or direction, thematic or transitive edge, description or evidence defect, and schema or accounting failure. Add a new category when the evidence does not fit; do not force discrepancies into a preset bucket.

For each category, identify the pipeline stage where the mistake entered and the underlying cause supported by the artifacts. Distinguish worker semantic errors from planner, packet, pooling, schema, correction, or evaluation defects. Counts describe prevalence; they do not establish cause.

For each supported cause, record one or more proposed solutions. A solution must state which adjudicated discrepancies it addresses, why it should prevent that category, what regressions it could introduce, and how the next controlled pass will test it. Prefer a general instruction or contract change over benchmark-specific language.

## Audit the gold before correcting it

Treat every proposed gold change as contested. Start at least two new reviewer sessions with separate output paths. Give each only the frozen policy, exact source evidence, current gold decision, and proposed correction; do not reveal another reviewer's verdict. If they disagree, start a third isolated session as tie-breaker on the policy question. Record each verdict and its evidence. The controller alone applies an accepted change to a new gold version; otherwise it preserves the disagreement in the report.

After any gold correction, revalidate ids and endpoints, regenerate canonical gold JSON, and rescore earlier results when the denominator or mapping changed.

## Improve one thing at a time

Choose one hypothesis supported by the adjudicated error taxonomy and proposed-solution analysis. Make the smallest paper-independent instruction, schema, or runtime change that tests it. Examples include making formal blocks structurally salient, clarifying proof ownership, or preserving same-start-line nodes. Do not encode appendix-specific labels, theorem names, or conclusions.

Set any timing target and its decision rule before dispatch. One worker duration is a measurement, not evidence that the target or design is good. Run a no-change control when model variance could explain the result. Then rerun fresh context-free workers under the changed skill. Because each iteration tests one hypothesis, changes in speed, size, recall, and error categories remain interpretable.

## Stop conditions

Stop an iteration when its report contains reproducible inputs, complete timing and size allocation, schema-valid canonical JSON, a fully adjudicated discrepancy ledger, corrected-gold decisions, final semantic scores, mistake categories, supported underlying causes, and proposed solutions. Stop the experiment series when repeated fresh runs meet the agreed quality and time targets without benchmark-specific wording, hidden retries, or manual semantic repair by the controller.

Do not claim convergence from one favorable sample. Preserve regressions and wasted work in the reports; they are evidence about the design.
