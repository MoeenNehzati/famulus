# Interface Facet Hashing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit v6 interface content/use subsets, derive canonical interface and remainder facets, and report exact facet drift while preserving v4/v5 behavior.

**Architecture:** Behavioral sources remain the sole ownership envelope. The canonical graph resolves each explicit interface to a subset of source-owned files and source-declared interface uses; hashing partitions policy-selected source inputs into explicit interface facets plus a remainder, then aggregates their local hashes into the source node hash. Certificate payload v3 records those facet claims so currentness can identify the exact changed interface or remainder.

**Tech Stack:** Python 3.11+, JSON Schema draft 7, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-interface-facet-hashing-design.md`

## Global Constraints

- Preserve schema-v4 and schema-v5 graph, hash, and certificate behavior.
- Do not create or require `.interface.default`.
- Keep source-level `content` and `uses_interfaces` as authoritative envelopes.
- Keep source `dependencies` source-scoped.
- Keep module hashing and export authority unchanged.
- Use TDD for each production behavior.
- Preserve unrelated work in the main checkout.

---

### Task 1: V6 interface facet declarations and graph resolution

**Files:**
- Modify: `references/blueprint/behavioral-source.schema.json`
- Modify: `src/officina/blueprints/graph.py`
- Modify: `src/officina/blueprints/blueprints/graph.yaml`
- Test: `tests/test_typed_blueprint_schemas.py`
- Test: `tests/test_officina_blueprint_graph.py`

**Interfaces:**
- Produces: `RepositoryBlueprintGraph.interface_content_paths: Mapping[str, tuple[Path, ...]]`
- Produces: `RepositoryBlueprintGraph.interface_uses: Mapping[str, tuple[tuple[str, int], ...]]`

- [x] Add schema tests showing an explicit v6 interface requires `content` and `uses_interfaces`, while an empty `interfaces` mapping remains valid.
- [x] Run the exact schema tests and confirm failure because the fields are not required or accepted.
- [x] Add graph tests showing interface content must be a subset of source content, must include the source gateway, may overlap another interface, and interface uses must be a subset of source uses.
- [x] Run the exact graph tests and confirm failure because graph facet resolution is absent.
- [x] Add `content` and `uses_interfaces` to the interface schema using the existing shared definitions.
- [x] Implement one graph-owned resolver that materializes interface file/use subsets after source ownership and source interface-use resolution.
- [x] Add the two immutable graph mappings and update the graph blueprint documentation.
- [x] Run schema and graph tests until green.

### Task 2: Canonical interface and remainder hash states

**Files:**
- Modify: `src/officina/certification/hashing.py`
- Modify: `src/officina/certification/blueprints/hashing.yaml`
- Test: `tests/test_officina_certification_hashing.py`
- Test: `tests/test_node_certification_hashing.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class CertificationFacetHashState:
      facet_id: str
      facet_type: str
      local_hash: str
      input_manifest: tuple[dict[str, str], ...]
      dependency_hashes: tuple[dict[str, Any], ...]
  ```
- Extends: `NodeHashState.facets: tuple[CertificationFacetHashState, ...]`
- Extends: `compute_interface_hash(extracted_interface, *, input_manifest=()) -> str`

- [x] Add tests that name the breaks: changing one claimed file changes only its interface facet and aggregate source hash; changing an unclaimed file changes only the remainder and aggregate; changing one used interface changes the exact interface dependency record but not its local hash; zero-interface sources use only the remainder.
- [x] Run those tests and confirm the facet assertions fail against current `NodeHashState`.
- [x] Add `CertificationFacetHashState` and a default-empty `facets` field so v4/v5 call sites remain valid.
- [x] Split v6 source manifests into interface manifests and a remainder manifest from the graph-owned path sets.
- [x] Compute interface local hashes from canonical declarations plus their policy-selected manifests; compute dependency records separately.
- [x] Compute the v6 behavioral-source node hash from the remainder hash and sorted interface local hashes; leave module and older-schema hashes unchanged.
- [x] Preserve the node `input_manifest` and `dependency_hashes` as canonical unions used by existing route-smoke and certificate paths.
- [x] Update hashing blueprint documentation and run focused hashing tests until green.

### Task 3: Facet-aware certificates and drift diagnostics

**Files:**
- Modify: `references/blueprint/certificate.schema.json`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: `src/officina/certification/view.py`
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py` only if rendering requires it
- Test: `tests/test_typed_blueprint_schemas.py`
- Test: `skills/skill-certifier/_rtx/tests/test_certifier.py`
- Test: `tests/test_officina_certification_view.py`
- Test: `skills/skill-drift/_rtx/tests/test_drift_check.py`

**Interfaces:**
- Produces certificate payload version 3 for schema v6.
- Produces `facets`, a canonical array of `{id, type, local_hash, input_manifest, dependencies}` claims.
- Produces exact facet mismatch concern strings from the design spec.

- [x] Add schema and payload tests requiring canonical v3 facet claims for v6 while retaining v1/v2 validation for historical logs.
- [x] Run those tests and confirm failure because v6 still emits payload v2 and no facets.
- [x] Add currentness tests for interface hash, interface manifest, interface dependency, remainder hash, remainder manifest, and remainder dependency mismatches.
- [x] Run them and confirm currentness reports only existing node-wide concerns.
- [x] Extend the certificate schema with a strict facet claim and require `facets` for payload v3.
- [x] Emit v3 plus canonical facet claims from the sole certifier writer.
- [x] Compare facet claims in the shared certification view and emit exact concerns without mutating logs.
- [x] Update drift rendering only if it currently filters or rewrites concern strings.
- [x] Run certificate, view, and drift tests until green.

### Task 4: Mechanical blueprint migration

**Files:**
- Create: `scripts/migrate_interface_facets.py`
- Test: `tests/test_migrate_interface_facets.py`
- Modify: every current schema-v6 behavioral-source blueprint containing at least one interface

**Interfaces:**
- Produces: `scripts/migrate_interface_facets.py --repo-root PATH [--write]`
- Check mode exits nonzero and prints paths requiring migration.
- Write mode copies source `content` and `uses_interfaces` into each interface missing either field, preserving already-authored interface fields.

- [x] Add a real-filesystem test proving check mode reports missing fields, write mode adds only missing fields, authored facet subsets remain unchanged, and a second write is idempotent.
- [x] Run it and confirm failure because the script is absent.
- [x] Implement the confined YAML migration with deterministic output and no certificate writes.
- [x] Run the migration test until green.
- [x] Run write mode on the isolated repository.
- [x] Run check mode and require exit zero.
- [x] Load the complete v6 graph and report counts of sources, interfaces, and remainder-only sources.

### Task 5: Repository verification and handoff

**Files:**
- Modify if required: `docs/officina/architectural-principles.md`
- Modify if required: `references/blueprint/README.md`

**Interfaces:**
- Consumes all prior task outputs.

- [x] Update only documentation that would otherwise state false field ownership or certificate semantics.
- [x] Run focused schema, graph, hashing, certificate, currentness, drift, and migration tests.
- [x] Run the repository schema/blueprint validators against the working view.
- [x] Run `python3 repo_checks.py --task tests:shared --jobs 4 --repository-view working`.
- [x] Run `git diff --check` and inspect the exact changed-path inventory.
- [x] Confirm no certificate log was created or modified.
- [x] Report the isolated branch/worktree and integration implications for the dirty main checkout; do not merge or push without explicit approval.

Implementation verification used the isolated
`feat/interface-facet-hashing` worktree. The dirty main checkout contains newer
blueprints and overlapping edits, so integration must rerun the migration,
complete graph load, and verification against that then-current state.
