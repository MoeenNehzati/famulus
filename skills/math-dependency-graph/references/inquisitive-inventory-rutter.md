# Inquisitive inventory Rutter

The private `_rtx` directory implements the Python runtime for the `math-dependency-graph` skill. Its Python files implement behavior; `_rtx/blueprint.yaml` and `_rtx/blueprints/*.yaml` describe ownership, dependencies, process bindings, arguments, effects, and interfaces. A blueprint is metadata and does not execute the Rutter.

## Inventory and Rutter flow

1. `_rtx/_inventory_unit_iterator.py` divides source text into durable units, leases units to workers, and records authenticated before/after inventory state.
2. A worker writes its cumulative inventory fragment. The iterator SQLite database records source coverage, sequence boundaries, counts, and snapshot hashes.
3. `_rtx/_inquisitive_inventory_cli.py` selects a setup mode and exposes finite JSON operations.
4. `_rtx/_inquisitive_inventory_rutter.py` authenticates iterator evidence, reconstructs each sequence's source text, maps corrected gold entities and edges to sequences by source span, and seals the interaction sequence into a durable Rutter experiment.
5. For each interaction, the Rutter shows the LLM only the source text and prior inventory. The LLM returns a cumulative inventory snapshot.
6. A `case_sequence_after` CaseMaker selects the gold answer at the same interaction index, derives the LLM's inventory delta, compares it semantically, and opens `DiagnoseAnswer` only on a mismatch.
7. A repeat-safe action advances the interaction sequence and records an action-ID-keyed ledger row.

The local frozen benchmark is in the gitignored `assets/inference-from-random-restarts/` directory. Frozen-gold setup uses its annotation and correction overlay by default when that local bundle is present. Explicit gold paths remain available for another authenticated bundle.

## Setup modes

The CLI's `setup` operation chooses among three inputs:

- `--source-cases-file` with `--gold-cases-file`: seal caller-prepared finite text interactions and indexed answers.
- `--iterator-state-dir`, `--worker-index`, and `--gold-cases-file`: load completed sequence artifacts from the current iterator format.
- `--iterator-state-dir`, `--worker-index`, and one or more `--inventory-fragment-file` values: reconstruct legacy iterator history and use the bundled frozen gold. Add both `--gold-annotation-file` and `--gold-overlay-file` to override that bundle.

Every setup also requires a new `--experiment-dir`. Setup is non-idempotent and rejects an existing directory. Concurrent setup calls targeting the same absent directory are unsafe.

After setup:

- `show` returns the current interaction's text, prior inventory, and public instruction; it never returns the historical after-snapshot or gold.
- `next` advances without a response or validates a supplied response before advancing.
- `ledger` reads and validates the sorted durable ledger.

## Important files

- `_rtx/_inventory_unit_iterator.py`: text iteration, worker leasing, SQLite authority, and authenticated sequence artifacts.
- `_rtx/_inquisitive_inventory_rutter.py`: semantic inventory comparison, legacy reconstruction, gold projection, Rutter definition, and ledger behavior.
- `_rtx/_inquisitive_inventory_cli.py`: bounded command-line and process interface.
- `_rtx/blueprints/rtx-inventory-unit-iterator.yaml`: iterator interface blueprint.
- `_rtx/blueprints/rtx-inquisitive-inventory-rutter.yaml`: Rutter lifecycle blueprint.
- `_rtx/blueprints/rtx-inquisitive-inventory-cli.yaml`: callable CLI blueprint and process patterns.
- `_rtx/tests/test_inventory_unit_iterator.py`: iterator behavior tests.
- `_rtx/tests/test_inquisitive_inventory_rutter.py`: reconstruction, semantic comparison, lifecycle, and ledger tests.
- `_rtx/tests/test_inquisitive_inventory_cli.py`: public JSON operation tests.

## Frozen example

The bundled gold is only the expected mathematical answer. Replaying the legacy example also requires its iterator database and authenticated inventory fragments. Given those inputs, run setup through the registered `math-dependency-graph._rtx.interface.inquisitive-inventory-experiment` interface with:

- operation `setup`
- iterator state directory
- completed worker index
- each authenticated inventory fragment
- a new experiment directory

Omit gold path options to use the bundled *Inference From Random Restarts* gold. The dispatcher enforces the blueprint process contract before the CLI opens caller-supplied paths.

## Invariants

- The correction overlay must authenticate the exact base gold SHA-256 and benchmark version.
- Fragment-derived snapshots must match hashes stored by the iterator.
- Gold records are assigned to the first sequence whose covered source coordinates overlap their evidence span.
- One completed CaseMaker attachment consumes exactly one indexed interaction.
- Gold remains sealed until the LLM's inventory response has been accepted.
- Experiment state is durable. The helper does not delete or roll back an experiment directory after partial creation.
