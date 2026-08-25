---
name: math-dependency-graph
description: >-
  Use when the user asks for a direct assumptions-to-results dependency graph of a TeX or Markdown mathematical document. Do not use for proof, notation, prose, or literature review.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: research; topics: mathematical-reasoning, visualization, scholarly-documents; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 108

Uses Interfaces:
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-apply-proof-digest@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-pool-inventory-chunks@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph._rtx.interface.scripts-semantic-to-canonical-json@1`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.extract@29`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory-voyages@7`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.inventory@37`
- `math-dependency-graph.source.gateway -> math-dependency-graph.interface.proof-reconciliation@3`
- `math-dependency-graph.source.instructions-inventory-voyages -> math-dependency-graph._rtx.interface.inventory-voyage-dispenser@7`
- `math-dependency-graph.source.instructions-inventory-voyages -> using-compass.interface.default@11`

Public Interfaces:
- `math-dependency-graph.interface.default`
- `math-dependency-graph.interface.extract`
- `math-dependency-graph.interface.inventory`
- `math-dependency-graph.interface.inventory-voyages`
- `math-dependency-graph.interface.proof-reconciliation`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `math-dependency-graph.interface.default` — Orchestrate a Voyage inventory, extract, normalize, and compile run.
- `math-dependency-graph.interface.extract` — Reconcile pooled inventory and the retained entrypoint into transitional notation-faithful entities, proof ownership, and direct relationships, or author one returned narrow repair.
- `math-dependency-graph.interface.inventory` — Author one concise recall-first graph inventory fragment through one bounded-unit, signal-complete, forward-reconciling validated loop that preserves opaque results as an identity-only candidate plus attached gap when unique, or a gap only when identity is nonunique.
- `math-dependency-graph.interface.inventory-voyages` — Discover and initialize one run-prefixed inventory Voyage collection, apply Compass to only its returned IDs, collect their completed inventory paths, and release terminal Voyages while retaining debug pre-reference decision bases and attributed diagnostic reckonings under the run artifacts.
- `math-dependency-graph.interface.proof-reconciliation` — Group complementary proof fragments, preserve alternative proofs, resolve source-grounded targets, and exhaustively decide every registered proof.
<!-- END BLUEPRINT INTERFACES -->

# Mathematical Dependency Graph

The gateway orchestrates one canonical dependency-graph workflow: inventory -> extract -> optional proof reconciliation -> deterministic normalization -> compile. It invokes interfaces and schedules workers; it does not restate or perform the mathematical judgments owned by `inventory`, `extract`, and `proof-reconciliation`.

Use a fresh empty run directory and never reuse an earlier inventory, semantic IR, or graph JSON artifact. Use only absolute paths returned by process-interface reports. A worker writes its assigned JSON directly; the gateway never generates semantic records with code or bulk transformations.

## Gateway algorithm

1. Initialize the document inventory and apply Compass through `math-dependency-graph.interface.inventory-voyages`. Supply the TeX or Markdown entrypoint and a positive requested chunk count. Use its default mode unless debug was explicitly requested with inventory gold-standard and source-alias JSON paths; use an empty alias object when source paths already align. The dispenser owns durable run storage.
2. Retain each schema-valid completed chunk inventory and pool them through `math-dependency-graph._rtx.interface.scripts-pool-inventory-chunks`. Retry only the Voyage whose validation failed; never replace a valid inventory or broaden its immutable chunk assignment.
3. Pass the ordered inventory fragments and retained entrypoint to `math-dependency-graph.interface.extract`. If extraction identifies proof reconciliation work, invoke `math-dependency-graph.interface.proof-reconciliation` only on its bounded proof packet.
4. Deterministically apply the proof digest through `math-dependency-graph._rtx.interface.scripts-apply-proof-digest`, convert the semantic IR through `math-dependency-graph._rtx.interface.scripts-semantic-to-canonical-json`, and verify that the final semantic IR and canonical JSON are nonempty and schema-valid. Report artifact paths, represented source scope, counts, and genuine unresolved gaps. Visualization and serving belong to the shared visualization layer.

Invalid semantic output never reaches compilation, and deterministic finalization never supplies missing mathematical content.

## Maintainer evaluation

When explicitly constructing or revising benchmark truth, follow `references/gold-standard-extraction.md`. When explicitly improving this skill through measured subagent trials, follow `references/experimental-improvement.md`. These are controller-only references: never include either file, gold artifacts, benchmark scores, or prior-pass findings in an inventory or extract worker's context.
