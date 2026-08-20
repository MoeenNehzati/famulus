# Interface Dependency Hashing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `uses-export` and `uses-private-interface` dependencies hash only the canonical used-interface blueprint projection; retain node hashes for every other dependency relation.

**Architecture:** Add one extractor and one hash wrapper in shared certification hashing. Preserve provider node targets for closure, retain the used interface ID in each claim, and update only the strict route-smoke validator needed to consume the new claim.

**Tech Stack:** Python 3.11+, pytest, JSON Schema.

## Global Constraints

- Do not change interface content ownership, transitive interface dependencies, currentness propagation, or certification scheduling.
- Preserve unrelated dirty work.
- Check elapsed time after red tests, green implementation, and focused regression tests; stop and reassess if scope expands.

---

### Task 1: Canonical interface dependency hashes

**Files:**
- Modify: `src/officina/certification/hashing.py`
- Modify: `src/officina/certification/blueprints/hashing.yaml`
- Modify if required by the claim shape: `references/blueprint/certificate.schema.json`
- Test: `tests/test_node_certification_hashing.py`
- Test: `tests/test_typed_blueprint_schemas.py`

**Interfaces:**
- Produces: `extract_interface_from_blueprint(graph, interface_id, version) -> dict[str, Any]`
- Produces: `compute_interface_hash(extracted_interface) -> str`

- [x] Add tests proving unrelated provider blueprint changes leave the consumer interface dependency hash unchanged.
- [x] Add tests proving the used interface contract changes its dependency hash.
- [x] Run the focused tests and confirm they fail for the missing behavior.
- [x] Implement the two helpers and relation-specific dependency serialization.
- [x] Update route-smoke dependency validation without changing reachability.
- [x] Run focused hashing, certificate-schema, currentness, certifier, and drift tests; preserve the three unrelated pre-existing drift-test failures.
- [x] Inspect the exact diff and report any remaining limitation without expanding scope.

## Time Checks

- 19:18 EDT: started and fixed scope.
- 19:24 EDT: confirmed the three intended red tests.
- 19:28 EDT: new behavior green.
- 19:40 EDT: focused regressions and live-graph validation complete.

## Certificate Migration Audit

- Verified all 73 existing logs and their signatures; every latest entry is a v2
  certificate issued at `02675d045bc81964446e483a48578ed0a1acc582`.
- Replayed currentness at the issuance commit: 16 of 232 nodes were current.
- Replayed currentness at pre-cutover `HEAD`
  (`608bf2a35504f466627c9f50bf63fc72d040a12d`): 0 of 232 nodes were current.
- Migration action: reissue no certificates. All 73 were already stale before the
  interface-hash change, so append-only history remains untouched.
