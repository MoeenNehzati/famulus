# Typed Blueprint Graph And Signed Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace monolithic interface declarations with typed, file-backed blueprint nodes while preserving legacy skills during migration, then certify the recursive graph with authenticated per-node health records and non-authoritative pooled reviews.

**Architecture:** `src/officina/common/blueprint_graph.py` is the shared compatibility boundary: it expands legacy roots into virtual nodes and loads version-2 roots plus hidden sidecars into the same typed graph. JSON Schemas remain the normative authoring and validation source. `src/officina/common/artifact_health.py` computes bottom-up Merkle-style artifact and certified-health hashes, while `audit_records.py` provides dependency-free HMAC-SHA-256 authentication.

**Tech Stack:** Python 3.10+, Draft 7 JSON Schema, PyYAML, `hashlib`, `hmac`, `secrets`, `json`, pytest.

## Global Constraints

- Keep one canonical `skills/<skill>/blueprint.yaml` as the skill graph root.
- Every subordinate blueprint binds exactly one regular file; directory bindings are invalid.
- One interface has exactly one blueprint; multiple interface blueprints may bind the same file.
- Hidden sidecars use `.<artifact>[.<local-node-name>].blueprint.yaml` and matching `.health.json` names.
- `SKILL.md` is the explicit binding of the default LLM interface.
- Python entrypoints live under `_rtx/`; future command files live under `_cx/` and are executed directly, never through inline shell strings.
- `direct_io` remains descriptive and does not prove subsystem confinement.
- Behavior-source nodes may have version-pinned edges to other behavior-source nodes.
- Generated pooled blueprints are review artifacts, never canonical graph inputs.
- Use SHA-256 for hashes and HMAC-SHA-256 with a 32-byte skill-local key; add no runtime dependency.
- Preserve the committed `schema.annotated-draft.json` as migration/reference input until typed schemas carry equivalent annotations.

---

### Task 1: Shared Typed Graph Model And Legacy Expansion

**Files:**
- Create: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/__init__.py`
- Create: `tests/test_officina_blueprint_graph.py`

**Interfaces:**
- Produces: `BlueprintNode`, `BlueprintEdge`, `SkillBlueprintGraph`, `load_skill_blueprint_graph(skill_root: Path)`, and `load_repository_blueprint_graphs(repo_root: Path)`.
- Consumes: Parsed YAML mappings only; schema validation remains Task 3.

- [x] **Step 1: Write failing tests for legacy expansion and typed loading**

```python
def test_legacy_root_expands_interfaces_without_writing_sidecars(tmp_path: Path) -> None:
    graph = load_skill_blueprint_graph(write_legacy_skill(tmp_path))
    assert graph.root.blueprint_type == "skill"
    assert graph.nodes["demo-skill.llm.default"].virtual is True


def test_typed_root_loads_hidden_file_backed_node(tmp_path: Path) -> None:
    skill = write_typed_skill(tmp_path, binding="SKILL.md")
    graph = load_skill_blueprint_graph(skill)
    node = graph.nodes["demo-skill.llm.default"]
    assert node.blueprint_path == skill / ".SKILL.md.blueprint.yaml"
    assert node.binding_path == skill / "SKILL.md"
```

- [x] **Step 2: Run the tests and verify missing-module failure**

Run: `python3 -m pytest tests/test_officina_blueprint_graph.py -q`

Expected: FAIL because `officina.common.blueprint_graph` does not exist.

- [x] **Step 3: Implement immutable graph records and deterministic loading**

```python
@dataclass(frozen=True)
class BlueprintNode:
    node_id: str
    blueprint_type: str
    version: int
    skill_root: Path
    blueprint_path: Path
    binding_path: Path | None
    declaration: dict[str, Any]
    virtual: bool = False


@dataclass(frozen=True)
class BlueprintEdge:
    relation: str
    source_id: str
    target_id: str
    required_version: int
    target_blueprint_path: Path | None = None
```

Typed roots resolve interface sidecars from explicit root edges. Legacy roots become in-memory virtual interface nodes and never create files.

- [x] **Step 4: Add failing tests for duplicate IDs, missing sidecars, and cycles**

Expected errors: `duplicate node id`, `missing subordinate blueprint`, and `blueprint graph cycle`.

- [x] **Step 5: Implement deterministic index and DFS cycle detection**

Sort every path, node ID, and edge tuple before indexing or traversing.

- [x] **Step 6: Run focused tests**

Run: `python3 -m pytest tests/test_officina_blueprint_graph.py -q`

Expected: PASS.

### Task 2: Typed Schema Family And Complete Authoring Metadata

**Files:**
- Create: `references/blueprint/legacy-skill.schema.json`
- Replace: `references/blueprint/schema.json`
- Create: `references/blueprint/common.schema.json`
- Create: `references/blueprint/skill.schema.json`
- Create: `references/blueprint/llm-interface.schema.json`
- Create: `references/blueprint/machine-interface.schema.json`
- Create: `references/blueprint/behavior-source.schema.json`
- Create: `references/blueprint/health.schema.json`
- Create: `references/blueprint/pooled-review.schema.json`
- Create: `references/blueprint/schema-meta.json`
- Create: `tests/test_typed_blueprint_schemas.py`

**Interfaces:**
- `schema.json` dispatches legacy and typed canonical blueprints.
- Each typed schema is independently resolvable from its `$id` and local references.

- [x] **Step 1: Snapshot the current live schema as the exact legacy schema**

The test must assert semantic JSON equality between the pre-change `schema.json` fixture and `legacy-skill.schema.json`.

- [x] **Step 2: Write failing valid/invalid fixtures for every blueprint type**

```python
@pytest.mark.parametrize("fixture", [
    "typed-skill.yaml",
    "typed-llm-interface.yaml",
    "typed-python-interface.yaml",
    "typed-command-interface.yaml",
    "typed-behavior-source.yaml",
])
def test_valid_typed_blueprints(fixture: str) -> None:
    validate_fixture(fixture)
```

Invalid fixtures must cover directory bindings, unknown fields, inline command strings, `_cx`/`_rtx` mismatch, absent default LLM edge, and behavior-source-to-interface edges.

- [x] **Step 3: Implement `common.schema.json` definitions**

Include canonical IDs, versions, locators, access control, patterns, runtime dependencies, `direct_io`, filesystem ownership, interface edges, and behavior-source edges. Behavior-source edges carry `source`, `version`, `blueprint`, and relationship-local `reason`.

- [x] **Step 4: Implement the four canonical typed schemas**

Required discriminants:

```json
{"schema_version": {"const": 2}, "blueprint_type": {"const": "machine-interface"}}
```

`machine-interface.binding` is a `oneOf` between `python-entrypoint` and `command-file`; no command string property exists.

- [x] **Step 5: Add complete `x-famulus` metadata**

Every public property must define authoring guidance, validation-rule references, template behavior, and explicit `x-famulus.audit_hash` value. Set `direct_io` to `exclude`; exact-file tracking remains available separately.

- [x] **Step 6: Implement and test `schema-meta.json`**

Reject unresolved validation-rule IDs, missing field status, missing audit-hash policy, or nonexistent validator/test paths.

- [x] **Step 7: Run schema tests and existing template tests**

Run: `python3 -m pytest tests/test_typed_blueprint_schemas.py tests/test_officina_blueprint_template.py -q`

Expected: PASS.

### Task 3: Schema-Driven Layout And Relationship Validation

**Files:**
- Modify: `skills/skill-maker/validators/blueprints.py`
- Modify: `skills/skill-maker/validators/blueprint_relationships.py`
- Modify: `skills/skill-maker/validators/interface_ids.py`
- Modify: `skills/skill-maker/validators/dependencies.py`
- Modify: `tests/validate_blueprints.py`
- Modify: `tests/validate_blueprint_relationships.py`
- Modify: `tests/validate_interface_ids.py`
- Modify: `tests/validate_dependencies.py`

**Interfaces:**
- Consumes: `load_repository_blueprint_graphs()` from Task 1.
- Produces: compatibility validation for legacy and typed graphs.

- [x] **Step 1: Add failing tests for hidden-sidecar discovery and file-only bindings**

Tests must prove that directories, missing files, pooled reviews, health files, and blueprint files are rejected as bindings.

- [x] **Step 2: Add failing tests for qualified sidecar naming**

One bound node permits `.foo.py.blueprint.yaml`; two nodes bound to `foo.py` require `.foo.py.first.blueprint.yaml` and `.foo.py.second.blueprint.yaml`.

- [x] **Step 3: Replace nested-map traversal with graph traversal**

Keep existing legacy behavior by consuming virtual nodes from the shared loader. Do not duplicate a second legacy expander in validators.

- [x] **Step 4: Validate graph relationships**

Check target existence, type, pinned version, access control, namespace restrictions, edge-set uniqueness, reachability from one skill root, and cycle rejection.

- [x] **Step 5: Update exact-skill mention checks**

Resolve the default LLM node through the root and read its `uses_interfaces`; legacy roots continue to work through virtual nodes.

- [x] **Step 6: Run validator tests**

Run: `python3 -m pytest tests/validate_blueprints.py tests/validate_blueprint_relationships.py tests/validate_interface_ids.py tests/validate_dependencies.py -q`

Expected: PASS.

### Task 4: Dependency-Free Health Authentication

**Files:**
- Modify: `src/officina/common/audit_records.py`
- Modify: `tests/test_officina_audit_records.py`

**Interfaces:**
- Produces: `load_or_create_hmac_key(path: Path)`, `attach_record_authentication(record, key)`, and `record_authentication_matches(record, key)`.

- [x] **Step 1: Write failing tests for deterministic canonicalization and HMAC verification**

```python
def test_manual_edit_with_recomputed_record_hash_still_fails_mac() -> None:
    authenticated = attach_record_authentication(RECORD, KEY)
    tampered = {**authenticated, "certification": {"result": "passed", "note": "edited"}}
    tampered = attach_record_digest(tampered)
    assert not record_authentication_matches(tampered, KEY)
```

- [x] **Step 2: Implement HMAC-SHA-256 using only stdlib**

```python
DOMAIN = b"famulus-health-record-v1\0"
mac = hmac.digest(key, DOMAIN + bytes.fromhex(record_hash.removeprefix("sha256:")), "sha256")
```

Use `hmac.compare_digest`; store Base64 MAC text and a random, non-secret key ID.

- [x] **Step 3: Implement atomic 32-byte key creation**

Create `skills/skill-audit/.health-authentication-key` with `secrets.token_bytes(32)`, exclusive creation, and POSIX mode `0600`. Never log key bytes.

- [x] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_officina_audit_records.py -q`

Expected: PASS without adding runtime dependencies.

### Task 5: Recursive Artifact And Certified-Health Hashing

**Files:**
- Create: `src/officina/common/artifact_health.py`
- Create: `tests/test_officina_artifact_health.py`

**Interfaces:**
- Produces: `certify_graph(graph, policy_hash, schema_hash, checks) -> dict[str, HealthRecord]` and `check_graph_health(graph, records, policy_hash, schema_hash, key) -> GraphHealthReport`.

- [x] **Step 1: Write failing leaf and transitive-change tests**

Changing a leaf file must alter the leaf, parent interface, and root expected certified-health hashes. Re-certifying unchanged content at a different timestamp must not alter `certified_health_hash`.

- [x] **Step 2: Implement canonical hash projections**

```text
local_hash = H(node identity, version, blueprint_contract_hash, bound_file_hash)
downstream_artifact_hash = H(sorted edge + child artifact_graph_hash)
artifact_graph_hash = H(local_hash, downstream_artifact_hash)
downstream_health_hash = H(sorted edge + child certified_health_hash)
certified_health_hash = H(local_hash, downstream_health_hash, schema_hash, policy_hash, normalized checks)
```

Also record `blueprint_file_hash` so formatting or excluded metadata changes remain reportable without necessarily becoming audit-stale.

- [x] **Step 3: Implement bottom-up certification and recursive checking**

The checker must verify each child against live files before using its recorded `certified_health_hash`. Missing, corrupt, unauthenticated, stale, and cyclic inputs receive distinct concerns.

- [x] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_officina_artifact_health.py -q`

Expected: PASS.

### Task 6: Pooled Review Generation And Downstream-Only Health

**Files:**
- Create: `src/officina/common/pooled_blueprint.py`
- Create: `tests/test_officina_pooled_blueprint.py`

**Interfaces:**
- Produces: `render_pooled_review(graph, records) -> str` and `check_pooled_review(path, health_path, root_report, key) -> PooledReviewHealth`.

- [x] **Step 1: Write failing tests proving pooled files are non-authoritative**

Deleting or corrupting a pool must not change canonical root health. A stale root must make the pool stale; a healthy root plus mismatched pooled content must make only the pool stale.

- [x] **Step 2: Implement deterministic expanded review rendering**

Use `document_type: pooled-blueprint-review`, include root graph/certified-health hashes, and expand current node declarations plus bounded health summaries.

- [x] **Step 3: Authenticate pooled health records**

Use the same HMAC key and record envelope as canonical health records, but never include pooled health in canonical child edges.

- [x] **Step 4: Run focused tests**

Run: `python3 -m pytest tests/test_officina_pooled_blueprint.py -q`

Expected: PASS.

### Task 7: Reference-Skill Migration And Runtime Integration

**Files:**
- Modify: `skills/skill-audit/blueprint.yaml`
- Create: `skills/skill-audit/.SKILL.md.blueprint.yaml`
- Create qualified machine-interface sidecars under `skills/skill-audit/_rtx/`
- Modify: `skills/skill-drift/blueprint.yaml`
- Create: `skills/skill-drift/.SKILL.md.blueprint.yaml`
- Create: `skills/skill-drift/_rtx/._check_drift_state.py.drift-status.blueprint.yaml`
- Create: `skills/skill-drift/_rtx/._check_drift_state.py.compute-hashes.blueprint.yaml`
- Modify runtime integration only after explicit approval to inspect the private `_rtx` files.
- Modify: `skills/skill-audit/tests/test_audit_certifier.py`
- Modify: `skills/skill-drift/tests/test_drift_check.py`

**Interfaces:**
- `skill-audit` writes authenticated bottom-up node records, pooled review plus health, then `.last_audit.json`.
- `skill-drift` recursively verifies live graph state and authentication without rewriting records.

- [x] **Step 1: Add failing integration tests for the typed reference skills**

Cover default `SKILL.md` bindings, shared Python-file sidecars, behavior-source recursion, HMAC failure, downstream propagation, and pooled-review independence.

- [x] **Step 2: Migrate declarations without changing public interface IDs**

Keep `skill-audit.machine.certify`, `skill-drift.machine.drift-status`, and `skill-drift.machine.compute-hashes` at version 1 unless their caller-visible contracts change.

- [x] **Step 3: Integrate shared graph and health APIs**

The private runtimes should orchestrate checks and IO only; hashing, canonicalization, authentication, and traversal remain in `officina.common`.

- [x] **Step 4: Run both skill suites through the repository runner**

Run: `python3 scripts/run-python-tests.py`

Expected: all tests pass.

### Task 8: Sync, Documentation, `_cx`, And Final Verification

**Files:**
- Modify blueprint sync implementation through the `skill-maker` boundary after approval to inspect its private runtime.
- Modify: `references/blueprint/guide.md`
- Modify: `references/blueprint/README.md`
- Modify: `references/skill-guidelines.md`
- Modify: `docs/audit_and_drift.md`
- Modify or regenerate: `references/blueprint/template.yaml`
- Add validator tests for `_cx` command-file bindings.

**Interfaces:**
- Sync resolves root edges plus typed sidecars and never consumes pooled files or health records as contract input.

- [x] **Step 1: Add failing sync tests for typed roots**

The generated `SKILL.md` contract must be identical in public meaning to the legacy declaration and must not expose `_rtx` or `_cx` paths outside generated internal metadata.

- [x] **Step 2: Add `_cx` schema and validator fixtures**

Accept a tracked regular executable file under `_cx`; reject inline command strings, `bash -c`, directory targets, and `_cx` paths in hand-authored skill prose.

- [x] **Step 3: Update normative documentation**

State file-only bindings, multiple interface blueprints per file, behavior-source recursion, ownership-versus-behavior distinction, non-enforcing `direct_io`/patterns semantics, health formulas, and HMAC threat-model limits.

- [x] **Step 4: Run complete verification**

Run:

```text
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
python3 scripts/run-python-tests.py
git diff --check
```

Expected: sync current, all tests pass, no whitespace errors, and no untracked private key.

## Self-Review

- Spec coverage: typed nodes, file-only bindings, shared implementation files, `_cx`, recursive behavior sources, filesystem ownership, pooled health, recursive hashes, HMAC, and migration compatibility each have an implementation task.
- Placeholder scan: no deferred implementation placeholders remain; external key storage, revocation, and anti-replay are intentionally outside the approved phase-one scope.
- Type consistency: graph APIs are produced in Task 1 and consumed by validators and health; stable `certified_health_hash` is distinct from whole-record `record_hash`; command files remain `machine-interface` nodes.
