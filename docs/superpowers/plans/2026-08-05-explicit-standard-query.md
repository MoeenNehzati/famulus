# Explicit Standard Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace target-discovering standards queries with a generic common query that accepts one explicit standard path and returns its complete validated import closure, while teaching `refactor-node` and `skill-maker` to select the correct roots.

**Architecture:** Move the current view/projection logic into a common process adapter over `standard_extractor`, delete blueprint ownership resolution from the query path, and migrate both instruction consumers to an explicit four-root matrix. Benchmark the resulting cold and six-view paths; defer closure parse deduplication to a separately documented legacy-module migration.

**Tech Stack:** Python 3.11+, PyYAML, jsonschema, pytest, Officina blueprint interfaces, dispatcher.

## Global Constraints

- The query accepts one canonical repository-relative standard path and never infers a standard from a node, path, filename, or blueprint.
- Every successful result retains metadata for the root and every unique transitive import, even when a view returns no matching records.
- Selected closure validation remains fail closed for schemas, semantics, pins, digests, paths, cycles, identities, facts, and refs.
- `refactor-node` and `skill-maker` must explicitly select among the Python/instruction module/behavioral-source roots.
- Do not update archival plans that record the former command.
- Do not alter the exhaustive no-fail-fast gate or omit tests.
- Preserve unrelated worktree changes. Do not stage or commit without separate authorization.

---

### Task 1: Common explicit-standard query

**Files:**
- Create: `src/officina/common/standard_query.py`
- Create: `tests/test_standard_query.py`
- Modify: `src/officina/common/blueprint.yaml`
- Create: `src/officina/common/blueprints/standard-query.yaml`

**Interfaces:**
- Consumes: `extract_standard(repo_root: Path, leaf_path: Path, *, facts: dict | None, query: Mapping | None) -> dict`
- Produces: `materialize_standard(repo_root: Path, standard_path: Path, *, facts=None, view="requirements", refs=None, record_query=None) -> dict`, `query(...) -> dict`, and process interface `common.interface.query-standard@1`.

- [ ] **Step 1: Add failing tests for explicit root input and closure-preserving output**

Add tests that call `query(REPO_ROOT, Path("references/node-standards/python-module.standard.yaml"), facts={"task.kind": "refactor"})` and assert:

```python
assert result["standard"] == "references/node-standards/python-module.standard.yaml"
assert result["root_document"] == "node-standards.python-module"
assert {item["id"] for item in result["documents"]} >= {
    "node-standards.python-module",
    "node-standards.module",
    "node-standards.python-node",
    "node-standards.node",
}
assert result["requirements"]["true"]
```

Add a monkeypatch test that makes `officina.common.blueprint_inventory.collect_blueprints` raise and proves the query still succeeds. Add direct/transitive/shared-import fixture assertions showing each document occurs once and every projected record retains `document`.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python3 -m pytest tests/test_standard_query.py -q`

Expected: FAIL because `officina.common.standard_query` and the public interface do not exist.

- [ ] **Step 3: Implement the common process adapter**

Move only standard materialization, ref normalization, view projection, and CLI parsing from `skills/refactor-node/_rtx/_closure_engine.py`. Remove `Partition`, `OwnershipIndex`, `_ownership_index`, `_partition`, `resolve_partitions`, `BlueprintNode`, `resolved_node_content_paths`, and `collect_blueprints`.

Use the caller path directly:

```python
def query(repo_root: Path, standard_path: Path, *, facts=None, view="requirements", refs=None, record_query=None):
    repo_root = Path(repo_root).resolve()
    materialized = materialize_standard(
        repo_root,
        standard_path,
        facts=facts,
        view=view,
        refs=refs,
        record_query=record_query,
    )
    root = next(
        document for document in materialized["documents"]
        if document["path"] == materialized["standard"]
    )
    return {
        "repository_root": str(repo_root),
        "root_document": root["id"],
        **materialized,
    }
```

`materialize_standard` passes `standard_path` directly to `extract_standard`, renames extractor `leaf` to `standard`, and preserves `documents` in every view. The interface positional is named `standard_path` and rejects non-object facts, non-list refs, incompatible arguments, and roots outside the repository through existing validation.

- [ ] **Step 4: Declare and export the common interface**

Register a `standard-query` behavioral source with a process binding to `Interface`, export `common.interface.query-standard`, allow `refactor-node`, `refactor-node-rtx`, `skill-maker`, and `skill-maker-rtx`, and declare `common.interface.standard-extractor` as its sole used interface.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python3 -m pytest tests/test_standard_query.py tests/test_standard_extractor.py -q`

Expected: PASS.

- [ ] **Step 6: Inspect the exact Task 1 diff**

Run: `git diff --check -- src/officina/common/standard_query.py src/officina/common/blueprint.yaml src/officina/common/blueprints/standard-query.yaml tests/test_standard_query.py`

Expected: no output. Record a checkpoint; do not commit.

---

### Task 2: Migrate refactor-node and skill-maker

**Files:**
- Modify: `skills/refactor-node/SKILL.md`
- Modify: `skills/refactor-node/blueprint.yaml`
- Modify: `skills/refactor-node/blueprints/gateway.yaml`
- Modify: `skills/refactor-node/_rtx/blueprint.yaml`
- Delete: `skills/refactor-node/_rtx/_closure_engine.py`
- Delete: `skills/refactor-node/_rtx/blueprints/query-standards.yaml`
- Modify/Delete: `skills/refactor-node/tests/test_query_standards.py`
- Modify: `skills/refactor-node/tests/test_refactor_node_routing.py`
- Modify: `skills/skill-maker/SKILL.md`
- Modify: `skills/skill-maker/blueprints/gateway.yaml`
- Modify: `tests/test_standard_consumers.py`
- Modify: `references/node-standards/authority-disposition.yaml`

**Interfaces:**
- Consumes: `common.interface.query-standard@1` from Task 1.
- Produces: explicit selection instructions for both consumers and no active dependency on `refactor-node.interface.query-standards`.

- [ ] **Step 1: Write failing consumer-contract tests**

Assert both gateway blueprints use `common.interface.query-standard@1`. Assert authored instructions contain all four canonical paths and the component-selection rules. Assert `skill-maker` starts new skills with the instruction-module root and queries additional roots only for components it authors. Assert no active blueprint, SKILL.md, authority ledger, or consumer test refers to the retired interface.

- [ ] **Step 2: Run consumer tests and verify RED**

Run: `python3 -m pytest skills/refactor-node/tests/test_refactor_node_routing.py tests/test_standard_consumers.py -q`

Expected: FAIL on the old facade and target-based instructions.

- [ ] **Step 3: Rewrite refactor-node instructions**

Replace target/partition language with the fixed matrix from the spec. Require one requirements query per applicable root, the same root/facts for follow-ups, exact returned document/ref pairs, and caller-owned classification of mixed scopes. Preserve the existing fact-resolution and refactoring-route behavior.

- [ ] **Step 4: Rewrite skill-maker instructions**

Require the instruction-module root for the initial schema-minimum skill and add instruction/Python module/source roots only for corresponding components. Preserve task facts, repository-validator facts, progressive views, Git safety, and authoring workflow.

- [ ] **Step 5: Remove the old refactor-node query facade/runtime**

Delete the public export, private source/interface, and ownership tests. Keep any view-projection tests that remain valuable by moving them to `tests/test_standard_query.py`. Update the active authority ledger to `common.interface.query-standard`; leave historical plans unchanged.

- [ ] **Step 6: Regenerate blueprint-derived artifacts**

Run: `dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints`

Expected: generated SKILL.md contract/interface blocks and `references/blueprint/runtime_dependencies.json` reflect the new common interface and contain no active old query export.

- [ ] **Step 7: Verify migrated consumers**

Run: `python3 -m pytest skills/refactor-node/tests/test_refactor_node_routing.py tests/test_standard_consumers.py tests/test_standard_query.py -q`

Expected: PASS.

- [ ] **Step 8: Inspect the exact Task 2 diff**

Run: `git diff --check -- skills/refactor-node skills/skill-maker references/node-standards/authority-disposition.yaml references/blueprint/runtime_dependencies.json tests/test_standard_consumers.py tests/test_standard_query.py`

Expected: no output. Record a checkpoint; do not commit.

---

### Task 3: Deferred follow-up — validate each selected standard document once

This optimization is intentionally not part of the explicit-root migration. The prototype improved the query further but touched two legacy Python modules whose whole callable surfaces do not yet satisfy the canonical documentation validator. Preserve the simpler validated extractor boundary in this commit and perform this task only as a separate documentation-plus-optimization change.

**Files:**
- Modify: `references/standards/validate_standard_v6.py`
- Modify: `src/officina/common/standard_extractor.py`
- Modify: `tests/test_standard_v6.py`
- Modify: `tests/test_standard_extractor.py`

**Interfaces:**
- Produces: `load_validated_closure(path: Path, root: Path | None = None) -> tuple[list[str], list[tuple[Path, dict]]]`; `validate_file` remains backward compatible and returns only errors.
- Consumed by: `extract_standard` instead of reparsing the validated closure.

- [ ] **Step 1: Add failing validation-count tests**

Use a fixture where two imports share a transitive standard. Instrument YAML document loading and compiled-validator calls. Assert one standard-schema validation and one YAML parse per unique standard document, while all pin, semantic, artifact, evidence, and external-reference checks still execute.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest tests/test_standard_v6.py tests/test_standard_extractor.py -q`

Expected: FAIL because imported children are currently schema-validated before recursive validation and the extractor reparses the closure.

- [ ] **Step 3: Compile the standard schema validator once per loaded module**

Create the schema and validator at module initialization:

```python
STANDARD_SCHEMA = _schema()
STANDARD_VALIDATOR_CLASS = jsonschema.validators.validator_for(STANDARD_SCHEMA)
STANDARD_VALIDATOR_CLASS.check_schema(STANDARD_SCHEMA)
STANDARD_VALIDATOR = STANDARD_VALIDATOR_CLASS(STANDARD_SCHEMA)
```

Replace repeated `jsonschema.validate(document, _schema())` calls with `STANDARD_VALIDATOR.validate(document)` and preserve error text.

- [ ] **Step 4: Return the validated parsed closure**

Refactor recursive validation so each resolved path is parsed, schema-validated, and semantically validated once into a shared path cache. Parent import checks read the cached child for ID/version/revision/facts and external references. `load_validated_closure` returns errors plus deterministic imports-before-importers `(path, document)` pairs. `validate_file` delegates to it and returns the error list.

- [ ] **Step 5: Reuse parsed documents in the extractor**

Have `extract_standard` call `validator.load_validated_closure`, raise on its errors, and build facts/records/documents from the returned parsed closure instead of calling `validate_file` and then recursively reading YAML again.

- [ ] **Step 6: Verify validator and extractor behavior**

Run: `python3 -m pytest tests/test_standard_v6.py tests/test_standard_extractor.py tests/test_standard_query.py -q`

Expected: PASS with the structural call-count assertions.

- [ ] **Step 7: Inspect the exact Task 3 diff**

Run: `git diff --check -- references/standards/validate_standard_v6.py src/officina/common/standard_extractor.py tests/test_standard_v6.py tests/test_standard_extractor.py tests/test_standard_query.py`

Expected: no output. Record a checkpoint; do not commit.

---

### Task 4: Synchronize, benchmark, and certify the change

**Files:**
- Modify: benchmark invocation/expectations in `scripts/benchmark-precommit.py` or the existing query microbenchmark only if needed.
- Create: `docs/reports/2026-08-05-explicit-standard-query-benchmark.json`
- Modify: `docs/superpowers/plans/2026-08-04-test-performance-remediation.md` only to record measured results and the next benchmark gate.

**Interfaces:**
- Consumes: completed common query and exhaustive gate.
- Produces: reproducible cold/warm CPU, wall, RSS, and call-count evidence.

- [ ] **Step 1: Run blueprint and repository validation**

Run: `dispatcher --caller-skill skill-maker skill-maker.interface.sync-blueprints --check`

Run: `python3 validators/runner.py`

Expected: PASS.

- [ ] **Step 2: Run focused and impacted test suites**

Run: `python3 -m pytest tests/test_standard_query.py tests/test_standard_extractor.py tests/test_standard_v6.py tests/test_standard_consumers.py skills/refactor-node/tests/test_refactor_node_routing.py -q`

Expected: PASS.

- [ ] **Step 3: Benchmark the cold query and six-view sequence**

Invoke `common.interface.query-standard` with `references/node-standards/python-module.standard.yaml` and `task.kind=refactor`. Record one cold run and three warm runs for requirements and for requirements/context/evidence/remedies/full/generic-query. Record CPU, wall, RSS, YAML loads, schema validations, and blueprint inventory calls.

Expected structural result for this change: zero blueprint inventory calls. YAML parse and schema-validation deduplication remain the deferred Task 3 follow-up.

- [ ] **Step 4: Run the exhaustive precommit gate**

Run: `python3 scripts/run-precommit-gate.py --report docs/reports/2026-08-05-explicit-standard-query-precommit.json`

Expected: every configured group runs exactly once and the report distinguishes ordinary failures from infrastructure limitations. Do not claim repository acceptance unless every required group passes.

- [ ] **Step 5: Inspect all owned changes**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; unrelated pre-existing gate/benchmark changes remain identifiable and untouched. Report results and blockers; do not commit.
