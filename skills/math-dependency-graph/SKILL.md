---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: mathematical-reasoning, visualization, scholarly-documents; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 69

Uses Interfaces:
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-advance-extraction-phases@23`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-record-run-diagnostics@5`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.extract@26`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory@29`

Public Interfaces:
- `math-dependency-graph.interface.default`
- `math-dependency-graph.interface.extract`
- `math-dependency-graph.interface.inventory`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `math-dependency-graph.interface.default` — Orchestrate a measured inventory, extract, compile, and render run.
- `math-dependency-graph.interface.extract` — Reconcile pooled inventory and the retained entrypoint into final notation-faithful entities and direct relationships, or author one returned narrow repair.
- `math-dependency-graph.interface.inventory` — Author one concise recall-first graph inventory fragment through one bounded-unit, signal-complete, validated forward loop.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

The gateway orchestrates one canonical semantic pipeline: inventory -> extract -> compile -> render. It invokes interfaces and schedules workers; it does not restate or perform the mathematical judgments owned by `inventory` and `extract`.

Use a fresh empty run directory and never reuse an earlier inventory, semantic IR, graph JSON, or HTML artifact. Use only absolute paths returned by process-interface reports. A worker writes its assigned JSON directly; the gateway never generates semantic records with code or bulk transformations.

## Gateway algorithm

1. **Prepare.** Resolve the TeX entrypoint and invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases prepare <entrypoint> --run-dir <run-dir>`. This operation prepares the complete reachable source, creates the minimum safe ordered inventory jobs, and performs the required diagnostics `initialize` event. Require its report to name an absolute diagnostics path and retain that exact path for the whole run. Do not issue a second `initialize` event.

2. **Inventory.** Schedule every reported `next_jobs` item through a fresh worker. Pass its returned instruction, schema, packet, progress, and output paths exactly. Require one recall-first monotone source loop. At each bounded source unit—one prose paragraph, one display, one complete delimited theorem-like environment, or one paragraph/display inside a proof—the worker must, in order: identify the owning object; make the bounded-formal-block decision; check all six formal-claim, reusable-setup, assumption, named-tool, proof-use, and exposition/application slots; emit every direct prerequisite-to-dependent lead; account for the observed signals; validate and checkpoint when due; then advance. Section and subsection commands and Markdown headings supply context only; they are not graph-reading units. Theorem-like and definition-like TeX or Markdown blocks default to candidates, while technical wrappers never duplicate their inner statements. Require one-sentence node summaries and one-clause edge explanations when first recorded, a schema-valid cumulative fragment at every bounded cursor, and complete explanations before appending matching cursor/counter progress; never dispatch a later compaction or wording-rewrite job. Retries reuse both paths only to repair validation or coverage failures. Immediately before a job waits for or requests a worker, invoke `math-dependency-graph._rtx.interface.scripts-record-run-diagnostics worker-queued <run-dir> <job-id> --phase inventory --model <exact-model-id>`. Immediately after successful worker creation, invoke `math-dependency-graph._rtx.interface.scripts-record-run-diagnostics worker-started <run-dir> <job-id>`. After the worker terminates, invoke `math-dependency-graph._rtx.interface.scripts-record-run-diagnostics worker-finished <run-dir> <job-id> --status success --output <output>` or the same operation with `--status failure --error-code <code>`. If worker creation itself fails, omit `worker-started` and close the queued attempt with the failure event. The gateway is the sole writer of the shared diagnostics report: serialize these diagnostics calls even while the inventory workers themselves run concurrently and write separate fragment and progress paths.

   Fill available worker slots and launch the next queued job as capacity opens. A retry uses the same job id, records a new `worker-queued` event, and must include exactly one `--retry-code`: `worker-failed`, `validation-failed`, `timeout`, `capacity`, or `transient`. Failure events use the matching allowlisted error code. Record the worker's actual model identifier, never a model tier or alias. After all inventory jobs succeed, write their fragment paths in reported order to the manifest.

3. **Pool.** Invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases advance-inventory <inventory-manifest> --run-dir <run-dir>`. Require its report to name the same diagnostics path. Pooling authenticates source ownership and records exact local and aggregate inventory-size ratios as controller-facing quality diagnostics. Size never rejects a fragment and never causes a worker compaction job. During skill evaluation, compare the ratios with the 50% local and 35% aggregate reference thresholds to decide whether the general inventory instruction needs improvement. Retry only the rejected inventory job when schema, ownership, anchor coverage, or record accounting identifies one; do not replace valid fragments or broaden their source assignments.

4. **Extract.** Launch exactly one fresh worker from the returned `next_job`, passing its instruction, schema, base, packet, sidecar, immutable source snapshot, retained entrypoint, progress, and output paths exactly. Require every attempt to append bounded actual-clock milestones to the stable `progress_path`; full retries reuse it. Record `worker-queued`, `worker-started`, and `worker-finished` as above with `--phase extract`, the exact model identifier, and an allowlisted retry code on every later attempt. Write the successful output path to the extract manifest.

5. **Finalize.** Invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases finalize-extract <extract-manifest> --run-dir <run-dir> [--html <path>]` and require the report to name the same diagnostics path. A successful final report validates, compiles, renders, and performs the sole diagnostics `finish` event; do not issue another `finish` event. If the report instead has `status: "correction-required"`, the run remains open: schedule only its returned `next_job`, pass the diagnostic, persisted repair base, pooled inventory, immutable extract inputs, entrypoint, repair schema, progress, and output paths exactly, and record the same queued/started/finished lifecycle with `--phase extract`. The correction appends to the same stable progress sidecar. Submit that output in a new manifest to `finalize-extract`. If the diagnostic is not record-local, rerun the single extract job from its immutable inputs instead of submitting a repair.

6. **Verify and report.** Require nonempty semantic IR, graph JSON, and HTML artifacts and a schema-valid diagnostics report with final status `success`. Report the diagnostics path and its latest-stage summary; inventory and extract job counts; exact models; retries and retry codes; queue, worker, stage, initialization, and total durations; artifact paths, sizes, hashes, and counts; every per-fragment ratio, the aggregate canonical-fragment ratio, and the remaining physical/pipeline ratios; final entity, relationship, exclusion, unresolved, and gap counts; represented source scope; and genuine unresolved gaps. Never conflate the aggregate canonical-to-owned ratio with physical pooled-inventory-to-active-source bytes. Use the durable report rather than reconstructed console timing.

If a diagnostics update fails, stop: an unmeasured run is not a successful run. Invalid semantic output never reaches compile or render, and deterministic finalization never supplies missing mathematical content.

## Maintainer evaluation

When explicitly constructing or revising benchmark truth, follow `references/gold-standard-extraction.md`. When explicitly improving this skill through measured subagent trials, follow `references/experimental-improvement.md`. These are controller-only references: never include either file, gold artifacts, benchmark scores, or prior-pass findings in an inventory or extract worker's context.
