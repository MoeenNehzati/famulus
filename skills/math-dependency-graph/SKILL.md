---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: mathematical-reasoning, visualization, scholarly-documents; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 76

Uses Interfaces:
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-advance-extraction-phases@24`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-next-inventory-unit@3`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-record-run-diagnostics@7`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-setup-inventory-iterator@5`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.extract@26`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory@33`
- `math-dependency-graph.source.instructions-inventory -> math-dependency-graph._rtx.interface.scripts-next-inventory-unit@3`

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
- `math-dependency-graph.interface.inventory` — Author one concise recall-first graph inventory fragment through a validated iterator acknowledgement loop.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

The gateway orchestrates one canonical semantic pipeline: inventory -> extract -> compile -> render. It invokes interfaces and schedules workers; it does not restate or perform the mathematical judgments owned by `inventory` and `extract`.

Use a fresh empty run directory and never reuse an earlier inventory, semantic IR, graph JSON, or HTML artifact. Use only absolute paths returned by process-interface reports. A worker writes its assigned JSON directly; the gateway never generates semantic records with code or bulk transformations.

## Gateway algorithm

1. **Prepare.** Resolve the TeX entrypoint and invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases prepare <entrypoint> --run-dir <run-dir>`. This prepares the complete reachable source, returns one controller-owned iterator setup assignment, and performs the required diagnostics `initialize` event. Require an absolute diagnostics path and retain it for the whole run. Do not issue a second `initialize` event.

2. **Set up and inventory.** Invoke `math-dependency-graph._rtx.interface.scripts-setup-inventory-iterator` once with the returned prepared iterator input and state directory, the selected positive worker limit, and the selected positive source-unit window. The setup response is authoritative: retain its durable identity, internal timings, assignment boundaries, and ordered coordinate coverage; launch exactly its effective assignments and pass each worker only its state directory, worker index, inventory schema, and returned inventory/progress paths through `math-dependency-graph.interface.inventory`. Never pass prepared input, a controller-owned artifact, a source path, an assignment manifest, or iterator storage to a worker.

   Each worker obtains source content only by invoking `math-dependency-graph._rtx.interface.scripts-next-inventory-unit` for its assigned index. It validates its own complete inventory before acknowledging every returned unit through the next call, uses `--wrap` when closing the consecutive units considered together, and finishes only when an acknowledgement returns `complete`. Mathematical candidate identification and dependency judgment remain worker-owned; the iterator owns only traversal, durable acknowledgement, validation, and measurement. Theorem-like environments and small titled mathematical Markdown or LaTeX blocks are strong candidate signals, while extraction may later merge or reject them. Do not dispatch a later compaction or wording-rewrite job.

   Retain the public wrapper's measured timing object from every actual setup and next response. `process_dispatch` measures immediately before the wrapper spawns its controlled child through child bootstrap entry; `total` measures through complete child stdout and exit. A fresh setup also reports `publication_observed: true` and measures `publication` immediately around atomic state replacement. Idempotent setup reuse reports `publication_observed: false` and omits publication; never substitute zero. These timings deliberately exclude outer gateway-to-dispatcher latency. Record the returned nonnegative values verbatim—never subtract, estimate, or fabricate a field. A worker returns its bounded next-call timing objects with its completed inventory path; no source or inventory prose belongs in timing data. After iterator completion and pooling have installed the durable internal iterator summary, the gateway invokes `math-dependency-graph._rtx.interface.scripts-record-run-diagnostics iterator-controller-timing <run-dir> setup --process-dispatch-ms <n> --total-ms <n> [--publication-ms <n>]` once for setup and the same operation with `next` and no publication once for every retained next-call measurement. Serialize these diagnostics calls. If a measured wrapper or diagnostics update fails, stop: the run is not successfully measured.

   Immediately before a worker waits for or starts, invoke `math-dependency-graph._rtx.interface.scripts-record-run-diagnostics worker-queued <run-dir> <job-id> --phase inventory --model <exact-model-id>`. Immediately after successful worker creation, record `worker-started`. After termination, record `worker-finished` with success and the returned inventory path, or failure and an allowlisted error code. The gateway remains the sole writer of shared diagnostics. Fill available worker slots; retries reuse the same assignment paths and job id, add exactly one matching allowlisted retry code, and resume the durable outstanding lease rather than rebuilding units. After all workers report `complete`, continue with the iterator state directory; do not create an inventory manifest from worker-selected packets.

3. **Pool.** Invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases advance-inventory <iterator-state-dir> --run-dir <run-dir>`. Require the same diagnostics path. Pooling authenticates the completed worker inventories, installs their iterator summary, and records exact local and aggregate inventory-size ratios. Record all retained setup/next controller measurements through the public diagnostics operation now, before starting extraction. Size never rejects a fragment and never causes compaction. During skill evaluation, compare the ratios with the 50% local and 35% aggregate reference thresholds. Retry only the rejected worker when schema, ownership, anchor coverage, or record accounting identifies one; do not replace valid fragments or broaden assignments.

4. **Extract.** Launch exactly one fresh worker from the returned `next_job`, passing its instruction, schema, base, packet, sidecar, immutable source snapshot, retained entrypoint, progress, and output paths exactly. Require every attempt to append bounded actual-clock milestones to the stable `progress_path`; full retries reuse it. Record `worker-queued`, `worker-started`, and `worker-finished` as above with `--phase extract`, the exact model identifier, and an allowlisted retry code on every later attempt. Write the successful output path to the extract manifest.

5. **Finalize.** Invoke `math-dependency-graph._rtx.interface.scripts-advance-extraction-phases finalize-extract <extract-manifest> --run-dir <run-dir> [--html <path>]` and require the report to name the same diagnostics path. A successful final report validates, compiles, renders, and performs the sole diagnostics `finish` event; do not issue another `finish` event. If the report instead has `status: "correction-required"`, the run remains open: schedule only its returned `next_job`, pass the diagnostic, persisted repair base, pooled inventory, immutable extract inputs, entrypoint, repair schema, progress, and output paths exactly, and record the same queued/started/finished lifecycle with `--phase extract`. The correction appends to the same stable progress sidecar. Submit that output in a new manifest to `finalize-extract`. If the diagnostic is not record-local, rerun the single extract job from its immutable inputs instead of submitting a repair.

6. **Verify and report.** Require nonempty semantic IR, graph JSON, and HTML artifacts and a schema-valid diagnostics report with final status `success`. Report the diagnostics path and its latest-stage summary; inventory and extract job counts; exact models; retries and retry codes; queue, worker, stage, initialization, and total durations; artifact paths, sizes, hashes, and counts; every per-fragment ratio, the aggregate canonical-fragment ratio, and the remaining physical/pipeline ratios; final entity, relationship, exclusion, unresolved, and gap counts; represented source scope; and genuine unresolved gaps. Never conflate the aggregate canonical-to-owned ratio with physical pooled-inventory-to-active-source bytes. Use the durable report rather than reconstructed console timing.

If a diagnostics update fails, stop: an unmeasured run is not a successful run. Invalid semantic output never reaches compile or render, and deterministic finalization never supplies missing mathematical content.

## Maintainer evaluation

When explicitly constructing or revising benchmark truth, follow `references/gold-standard-extraction.md`. When explicitly improving this skill through measured subagent trials, follow `references/experimental-improvement.md`. These are controller-only references: never include either file, gold artifacts, benchmark scores, or prior-pass findings in an inventory or extract worker's context.
