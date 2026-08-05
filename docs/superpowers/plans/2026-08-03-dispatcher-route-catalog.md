# Dispatcher Route Catalog Implementation Plan

> **Superseded by the version-6 direct dispatcher. Historical plan only.**
> The live dispatcher has no route catalog, snapshot, cache, graph build, or
> routing writes. See `docs/dispatcher.md` and
> `docs/superpowers/specs/2026-08-04-fast-dispatcher-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse validated route resolution state across dispatcher processes.

**Architecture:** Add a safe JSON route catalog between canonical repository
loading and `_resolve_export_dispatch()`. Cache hits reconstruct the exact graph
and certification view; misses retain the existing resolver behavior and write
an atomic entry.

**Tech stack:** Python standard library, existing Officina graph and
certification dataclasses, pytest.

## Global Constraints

- Cache state is never authoritative when stale, malformed, or unavailable.
- Cache failure cannot admit a request or reject an otherwise valid request.
- Dispatcher warnings remain structured `InvocationDiagnostic` values.
- Hop-local authorization and caller identity do not change.

---

### Task 1: Catalog graph round trip

**Files:**
- Create: `src/officina/dispatcher/catalog.py`
- Create: `tests/test_dispatcher_catalog.py`

1. Add a failing round-trip test using a real v5 fixture graph.
2. Implement an allow-listed JSON encoder and decoder.
3. Verify the focused test.

### Task 2: Fresh route storage

**Files:**
- Modify: `src/officina/dispatcher/catalog.py`
- Modify: `tests/test_dispatcher_catalog.py`

1. Add failing tests for atomic storage, route separation, malformed entries,
   and stale dependency fingerprints.
2. Implement repository-keyed cache paths and fingerprint validation.
3. Verify the focused tests.

### Task 3: Dispatcher integration

**Files:**
- Modify: `src/officina/dispatcher/core.py`
- Modify: `tests/test_officina_dispatcher.py`

1. Add a failing regression test proving the second identical resolution does
   not call graph or certification loaders.
2. Load fresh catalog entries before canonical resolution and store successful
   misses afterward.
3. Preserve diagnostics and hop-local authorization behavior.
4. Verify dispatcher and catalog tests.

### Task 4: Documentation and performance verification

**Files:**
- Modify: `docs/architecture.md`

1. Document ownership, invalidation, and conservative failure behavior.
2. Run focused tests, the dispatcher/runtime suite, and two identical live dry
   runs.
3. Inspect the final diff and report unrelated pre-existing dirty files
   separately.

### Task 5: Report catalog rebuilds and write failures

**Files:**
- Modify: `src/officina/dispatcher/catalog.py`
- Modify: `src/officina/dispatcher/core.py`
- Modify: `tests/test_dispatcher_catalog.py`
- Modify: `tests/test_officina_dispatcher.py`
- Modify: `docs/architecture.md`

**Interfaces:**
- Produces: `CatalogLookup(status, graph)` and
  `lookup_route_graph(repo_root, route, cache_root=None)`.
- Preserves: `load_route_graph(...)` as the graph-or-`None` compatibility API.

- [ ] **Step 1: Write failing catalog-status tests**

  Assert literal `missing`, `stale`, `malformed`, and `unavailable` statuses,
  with a graph present only for `hit`.

- [ ] **Step 2: Run the focused status tests**

  Run:
  `/usr/bin/env PYTHONPATH=src pytest -q tests/test_dispatcher_catalog.py`

  Expected: failures because `CatalogLookup` and `lookup_route_graph` do not
  exist.

- [ ] **Step 3: Implement typed lookup without changing cache admission**

  Classify file absence as `missing`, fingerprint mismatch or format mismatch
  as `stale`, invalid JSON/shape/typed payload as `malformed`, and other read
  failures as `unavailable`. Make `load_route_graph` return `lookup.graph`.

- [ ] **Step 4: Write failing dispatcher-warning tests**

  Assert a successful cold rebuild emits `dispatcher-catalog-rebuilt`, a hit
  emits no catalog warning, and failed persistence emits
  `dispatcher-catalog-write-failed` without changing the resolved command.

- [ ] **Step 5: Implement structured dispatcher warnings**

  Consume `lookup_route_graph` in `_resolve_export_dispatch`. Add the rebuild
  warning only after successful version 5 canonical resolution. Convert graph
  and certification persistence failures into one deduplicated write warning.

- [ ] **Step 6: Verify behavior and performance**

  Run the catalog, dispatcher, Python machine-interface, and route-smoke tests,
  then compare two live dry runs. Expected: all focused tests pass; the second
  run remains near the dispatcher import floor.
