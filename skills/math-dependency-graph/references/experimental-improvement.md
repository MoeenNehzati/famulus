# Improve the Graph Skill Through Controlled Experiments

Use this playbook to improve `math-dependency-graph` by running fresh subagents on representative mathematical source and comparing their canonical output with a potentially flawed gold standard. This is a maintainer evaluation workflow. Never give this file, the gold graph, prior outputs, score reports, or benchmark-specific hints to inventory or extract workers.

## Fix the experimental conditions

Before a pass, record:

- the immutable source snapshot, entrypoint, and exact source scope;
- the current skill and instruction versions;
- the model id and reasoning level for every worker;
- the gold version and its annotation policy;
- the canonical schema and validator used for final output;
- the acceptance metrics and the single question tested by this pass.

Use a fresh run directory. Preserve every input, worker artifact, progress sidecar, diagnostic report, canonical JSON file, discrepancy ledger, score, and adjudication. Never overwrite an earlier pass.

## Keep roles separated

1. **Graph workers are context-free.** Start each worker in a new agent session whose initial message contains only the production job and its returned paths. Inventory and extract workers receive only the material returned by the production skill. They must not see gold, prior passes, benchmark reports, proposed fixes, controller notes, or another worker's output. Give parallel inventory workers distinct fragment and progress paths and prohibit cross-worker messages.
2. **The evaluator sees both artifacts and the source.** It compares the completed canonical graph with the current gold by mathematical identity, source location, labels, descriptions, and directness—not by generated ids alone. It adjudicates every discrepancy from source evidence and the frozen annotation policy before counting errors.
3. **Gold adjudicators see the dispute.** They receive the annotation policy, exact source evidence, current gold decision, and contested proposed correction. They do not edit artifacts.
4. **The controller owns diagnostics.** It serializes lifecycle events, preserves artifacts, calculates metrics, and decides the next experimental hypothesis. Semantic workers never manage benchmark thresholds.

## Run one pass

1. Prepare the source through the live public skill route.
2. Queue fresh inventory workers with the chosen model and reasoning level. Record queue time, worker time, progress checkpoints, retries, failures, and output bytes separately.
3. Pool once all initial fragments validate. Retry only schema, ownership, anchor-coverage, or accounting failures. Do not dispatch compaction or wording-rewrite work. Size is an evaluation signal, not a worker task.
4. Run one fresh extract worker, finalize through the live route, and validate the semantic and canonical schemas. The resulting canonical JSON is the authoritative experimental output.
5. Build a discrepancy ledger covering every gold-only node or edge, every output-only node or edge, and every non-equivalent match. For each discrepancy, inspect its exact source evidence and the frozen annotation policy, then decide whether the worker is wrong, the gold is wrong, both are wrong, the difference is an allowed granularity choice, or the evidence remains unresolved. Do not count a discrepancy as an error before this decision.
6. Independently adjudicate every proposed gold correction, update gold only when accepted, and recompute mappings and denominators after any correction.
7. Categorize the confirmed worker mistakes by observable failure type, pipeline stage, and underlying cause. Develop proposed solutions for the categories, connecting each solution to its supporting discrepancies, expected effect, risks, and a measurable paper-independent test.
8. Write the pass report, including the discrepancy ledger, adjudications, mistake taxonomy, causes, and proposed solutions, before changing instructions, schemas, or runtime.

## Measure time and space

Report both wall-clock and allocation detail:

- source preparation, planning, pooling, validation, and compilation duration;
- per-attempt queue time and worker time for inventory and extract;
- retry count, retry reason, and time spent in failed or discarded attempts;
- raw and canonical bytes for each inventory fragment, pooled IR, semantic IR, and canonical graph;
- local fragment-to-owned-source ratios and the aggregate canonical-fragment ratio;
- entity, relationship, exclusion, unresolved-resolution, and gap counts.

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
