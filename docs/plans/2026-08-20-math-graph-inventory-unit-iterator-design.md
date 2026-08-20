# Math Graph Inventory Unit Iterator

## Purpose

Inventory workers should inspect mathematical source once, linearly, without receiving a large source packet or choosing their own reading strategy. A deterministic iterator prepares source units once, assigns contiguous ranges to workers, and records exactly what each worker read and grouped together. Semantic identification remains the worker's responsibility.

This design is general to LaTeX and Markdown research projects. It does not encode appendix-specific mathematical knowledge.

## Public interfaces

### Setup

```text
scripts-setup-inventory-iterator <source-packet> <state-dir> \
  --workers <positive-int> --window-chars <positive-int>
```

Setup performs the only full source scan. It unitizes the source, assigns contiguous ranges, creates shared durable state, and publishes the completed state directory atomically.

Requested workers are a maximum. The effective count is:

```text
min(requested_workers, unit_count)
```

Worker indices are one-based. Repeating setup is allowed only when the source identity and configuration match exactly; otherwise it fails closed.

### Next

```text
scripts-next-inventory-unit <state-dir> <worker-index> \
  [--ack <unit-id>] [--wrap]
```

`next` is atomic:

- With no outstanding unit, it leases and returns the worker's next unit.
- With an outstanding unit and no `--ack`, it returns that same unit.
- `--ack` must name that worker's outstanding unit. Before advancing, the command validates the worker's inventory fragment, records its hash and entity/reference counts, commits the acknowledgement, and returns the next unit.
- `--wrap` is valid only with `--ack`. It closes the consecutive attention sequence ending at that unit.
- The final acknowledgement returns `complete` and automatically closes an open final sequence.
- An exact retry of the most recent acknowledgement with the same wrap intent is idempotent.
- Stale, cross-worker, out-of-order, and conflicting acknowledgements fail without advancing state.

## Durable state and ownership

All workers share one SQLite database for cursor and audit state. Semantic writes are not shared. Each worker owns a separate inventory JSON fragment and progress Markdown file, preventing write races.

The setup directory contains at least:

```text
iterator.sqlite3
inventory-assignments.json
workers/worker-<index>/inventory.json
workers/worker-<index>/progress.md
controller/worker-<index>-packet.json
```

Controller packets retain the source/provenance data needed by the pooler but are not given to inventory workers. Setup constructs a temporary sibling directory, validates it, then renames it atomically to `<state-dir>`.

An inventory worker receives only:

- the inventory instruction and schema;
- the shared state-directory path;
- its worker index and chunk identifier;
- its inventory-fragment and progress paths; and
- iterator responses obtained through `next`.

This is a workflow boundary, not a filesystem security boundary.

## Source order and unitization

Setup scans the expanded project in deterministic source/include order and records source coordinates. Headings provide context but are not semantic candidates by themselves.

At each cursor:

1. If the next complete author-visible block fits `window_chars`, return it whole. Strong blocks include theorem-like environments, proofs, examples, remarks, definitions, custom formal environments, Markdown titled math blocks, and display-math blocks.
2. If such a block is too large, split it only at complete paragraph, display, or nested-environment boundaries. Every part retains the parent environment, semantic owner, and part number.
3. Outside an environment, return the largest consecutive paragraph prefix that fits the window.
4. If the first indivisible item alone exceeds the window, return it whole and set `oversize: true`.

No content is truncated. Every owned source coordinate is covered exactly once by a returned unit or by explicitly recorded structural context. Units have global monotonic IDs `u000001`, `u000002`, and so on.

The default experimental setting is `window_chars=8000`; this is a starting point, not an asserted optimum.

## Assignment

Setup partitions the ordered unit list into deterministic, contiguous worker ranges, balancing by character count. A worker never receives interleaved ranges. Empty workers are not created.

Assignment records include worker index, first and last unit IDs, unit count, character count, source identities, inventory path, progress path, and controller-packet path.

## Attention sequences

`--wrap` records which consecutive units the worker considered together. A closed sequence records:

- worker index;
- first and last unit IDs;
- unit and character counts;
- opened and closed timestamps;
- inventory counts and hashes before and after the sequence; and
- closure reason: `worker-wrap` or `end-of-source`.

While open, diagnostics record its unit count, character count, and elapsed time. Version 1 reports these facts but imposes no warning or hard limit; limits require experimental evidence first.

## Validation and transactions

Every acknowledgement validates the worker fragment against the inventory schema and verifies worker/chunk ownership. Invalid JSON, invalid schema, wrong ownership, or a missing output leaves the lease and cursor unchanged.

The state transition, fragment hash/count snapshot, sequence update, and next-unit lease occur in one SQLite transaction. Concurrent workers may advance independently. Setup and `next` must never reparse the entire source on each call.

## Workflow integration

The inventory phase becomes:

1. `prepare` creates source artifacts and diagnostics, then returns one setup assignment rather than inventory jobs.
2. The gateway calls setup once and launches the effective workers from `inventory-assignments.json`.
3. Each worker repeatedly calls `next`, writes only its own inventory, and advances with `--ack`, optionally adding `--wrap`.
4. Inventory advancement requires every effective worker to be complete. The controller uses the private packets to pool inventories and records iterator diagnostics.
5. Extraction proceeds through the existing extract route.

The extraction chunk planner becomes extract-only; inventory traversal belongs to the iterator.

## Diagnostics and performance

Setup records elapsed milliseconds for scan, unitization, partitioning, database creation, validation, publication, and total work. `next` records validation, transaction, lookup, serialization, and total milliseconds. Durable detail lives in SQLite; bounded aggregates flow into run diagnostics. Run diagnostics contain no source text or mathematical prose.

Complexity targets:

- setup: `O(source characters + units)`, with exactly one source scan;
- no-ack `next`: `O(returned characters)` plus process startup;
- acking `next`: `O(returned characters + that worker's cumulative inventory size)`;
- `next` never loads every unit's text or reparses the project.

For the appendix experiment, setup should take under one second and internal no-ack `next` under 10 ms; dispatcher/process startup is measured separately.

## Evaluation

Use Luna 5.6 at medium reasoning for the controlled worker experiment. Compare canonical output against the adjudicated gold graph, with the previous baseline of 41/57 nodes and 26/63 edges retained as historical context.

A control-flow improvement is accepted only if source coverage has no gaps or duplication, timing targets are met, and graph recall is not materially worse. A semantic improvement is claimed only when discrepancy-by-discrepancy adjudication shows higher recall or precision; disagreement counts alone are insufficient because the gold graph may be wrong.

## Non-goals

- Encoding semantic graph decisions in Python.
- Sharing one semantic output file among workers.
- Building an extraction iterator.
- Adding wrap warnings or limits in version 1.
- Providing a hard filesystem sandbox.
