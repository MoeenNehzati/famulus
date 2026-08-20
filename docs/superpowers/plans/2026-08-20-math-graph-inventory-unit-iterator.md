# Math Graph Inventory Unit Iterator Implementation Plan

Implement the design in [the inventory iterator specification](../specs/2026-08-20-math-graph-inventory-unit-iterator-design.md). Work test-first, preserve unrelated dirty files, and do not stage or commit unless explicitly requested.

## 1. Establish the test surface

Create:

- `skills/math-dependency-graph/_rtx/tests/test_inventory_unit_iterator.py`

The runtime API under test is:

```python
def setup_inventory_iterator(
    source_packet_path: Path,
    state_dir: Path,
    *,
    requested_workers: int,
    window_chars: int,
    clock_ns=...,
    utc_now=...,
) -> dict: ...

def next_inventory_unit(
    state_dir: Path,
    worker_index: int,
    *,
    ack: str | None = None,
    wrap: bool = False,
    clock_ns=...,
    utc_now=...,
) -> dict: ...

def load_iterator_summary(state_dir: Path) -> dict: ...
```

First tests must cover:

- linear include order and stable global unit IDs;
- whole small LaTeX/Markdown environments;
- environment-aware splitting of oversized blocks;
- paragraph grouping outside environments;
- an indivisible oversize unit;
- exact coordinate coverage with no gaps or overlap;
- deterministic contiguous character-balanced assignments;
- effective worker count when requested workers exceed units;
- atomic setup publication;
- exact setup reuse and mismatch rejection.

Run only this test file and record the expected import/behavior failures before implementation.

## 2. Implement setup

Create:

- `skills/math-dependency-graph/_rtx/_inventory_unit_iterator.py`

Implement a single-pass scanner that produces unit metadata and stores unit text so `next` can fetch one unit without reparsing the project. Treat headings as context; preserve environment, owner, source coordinates, part number, and `oversize` metadata.

Implement deterministic contiguous partitioning by character count. Create the SQLite schema, assignments manifest, per-worker output paths, and private controller packets inside a temporary sibling directory. Validate all invariants before atomic rename.

Make repeated setup idempotent only when the source hash, requested worker count, window, scanner version, and schema version agree exactly.

Get the setup tests green. Add an appendix smoke measurement confirming one scan and reporting setup substage times; do not turn the performance target into a flaky unit-test deadline.

## 3. Implement lease, acknowledgement, and wrapping

Add failing tests for:

- first lease and replay without acknowledgement;
- independent worker cursors;
- schema validation before advancement;
- rollback on missing/invalid/wrong-worker inventory;
- stale, future, and cross-worker acknowledgements;
- exact idempotent retry of the most recent acknowledgement;
- rejection when retry wrap intent differs;
- explicit sequence closure with `--wrap`;
- automatic final sequence closure;
- real concurrent calls against separate worker indices.

Implement `next_inventory_unit` using one SQLite transaction for validation snapshot, acknowledgement, sequence mutation, cursor advance, and next lease. Validate the full worker-owned inventory fragment on every ack and record its content hash plus bounded semantic counts.

Return machine-readable states: `unit`, `complete`, or a structured failure. Never advance on failure.

## 4. Add two atomic process interfaces

Create an authored blueprint, expected location:

- `skills/math-dependency-graph/_rtx/blueprints/rtx-inventory-unit-iterator.yaml`

Expose separate setup and next interfaces. Add thin CLI parsing to the runtime module if repository conventions place CLI ownership there. The interfaces must map exactly to:

```text
scripts-setup-inventory-iterator <source-packet> <state-dir> --workers N --window-chars W
scripts-next-inventory-unit <state-dir> <worker-index> [--ack UNIT] [--wrap]
```

Test compiled argument order, invalid combinations, positive integer checks, and structured output. Keep setup and next separate so each call is atomic and independently observable.

## 5. Integrate phase control and retire inventory planning

Modify, with tests first:

- `skills/math-dependency-graph/_rtx/_extraction_chunk_planner.py`
- `skills/math-dependency-graph/_rtx/schemas/chunk-plan.schema.json`
- `skills/math-dependency-graph/_rtx/_extraction_phase_driver.py`
- their focused tests

Required behavior:

- the chunk planner accepts only extraction work;
- `prepare` produces one iterator setup assignment, not inventory jobs;
- setup produces effective worker assignments;
- inventory advancement fails closed until every worker is complete;
- pooling consumes controller packets and worker inventories, never source packets exposed to workers;
- retry/resume uses the durable iterator state rather than recomputing units;
- extraction behavior remains unchanged.

Test crash/resume, partial worker completion, mismatched state, and successful pooling.

## 6. Add bounded diagnostics

Modify, with tests first:

- `skills/math-dependency-graph/_rtx/_run_diagnostics.py`
- its diagnostics schema and tests
- phase-driver diagnostics tests

Record setup scan, unitization, partition, database, validation, publication, and total milliseconds. Record `next` validation, transaction, lookup, serialization, and total milliseconds. Summaries must include unit/worker counts, assigned characters, acknowledgements, wraps, open-sequence size/time, retries, and failures.

Verify diagnostics exclude unit text, source prose, inventory prose, and raw validation instance values. Measure dispatcher/process startup separately from internal runtime time.

## 7. Rewrite the inventory workflow instructions

Modify the authored instruction sources, including:

- `skills/math-dependency-graph/instructions/inventory.md`
- the math-dependency-graph gateway instruction source
- their authored behavioral blueprints and graph-profile tests

The worker instruction should say, in this order:

1. call `next` for its assigned worker index;
2. inspect the returned unit linearly, retaining cross-unit ownership context;
3. add plausible math-graph nodes and explicit or inferred dependency destinations to its own inventory fragment;
4. keep concise explanations and source evidence required by the inventory schema;
5. acknowledge the unit in the next call;
6. use `--wrap` when closing the consecutive units considered together;
7. validate the inventory schema at every acknowledgement and finish only on `complete`.

Stress vigilance over premature pruning: theorem-like environments and small titled Markdown/LaTeX blocks are strong candidate signals, while the extraction pass may later merge or reject candidates. Do not teach appendix-specific entities.

Add an agent-behavior test using Luna 5.6 medium. Verify that the worker follows iterator order, writes only its own inventory, and does not read a source/controller packet directly.

## 8. Register contracts and propagate versions

Update affected authored blueprints and dependencies, then synchronize generated artifacts through the skill-maker interface. Verify live versions before editing; expected changes from the current working view are approximately:

- parent math graph: `68 -> 69`;
- gateway source/interface: `65 -> 66` / `59 -> 60`;
- inventory source/interface: `29 -> 30`;
- runtime module: `42 -> 43`;
- planner source/interface: `17 -> 18` / `14 -> 15`;
- phase source/interface: `25 -> 26` / `22 -> 23`;
- diagnostics source/interface: `8 -> 9` / `5 -> 6`;
- new iterator source and interfaces: version `1`.

These are expected starting points, not authority: re-read the live authored graph and bump each changed node/interface once, then propagate dependency and facade versions bottom-up.

Run the repository's skill-maker blueprint synchronization interface, followed by its check mode. Do not hand-edit generated `SKILL.md` blocks or runtime-dependency manifests.

## 9. Verify and run the controlled appendix experiment

Run, in order:

1. iterator, planner, phase-driver, pooler, diagnostics, and schema tests;
2. the full math-dependency-graph runtime suite;
3. blueprint/schema consistency tests and focused repository validators;
4. JSON/YAML parsing, Python compilation, and scoped whitespace checks;
5. dispatcher dry-runs for both new public interfaces;
6. one fresh appendix run with Luna 5.6 medium and `window_chars=8000`.

For the experiment, retain raw setup and per-call timing summaries and compare the canonical JSON to the adjudicated gold graph. Check source coverage mechanically. Then adjudicate every node/edge discrepancy: decide whether the worker or gold is correct, categorize the underlying miss/false-positive mechanism, and propose a general correction only after that adjudication.

Report separately:

- setup and internal/dispatcher `next` timing;
- inventory and canonical artifact sizes;
- source coverage gaps/overlaps;
- node and edge precision/recall after gold corrections;
- time spent reading, writing inventory, schema repair, retries, pooling, and extraction;
- whether the change meets the control-flow and semantic acceptance criteria.

Do not claim success from schema validity, discrepancy counts, or rendered HTML alone. Canonical JSON is authoritative.
