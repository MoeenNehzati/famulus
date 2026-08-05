# Principle Agreements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring certificate retention, gateway ownership, and relationship-policy enforcement into agreement with Officina's architectural principles.

**Architecture:** A certificate records the commit at which an exact clean state was audited, but later currentness is determined only by the node, its certification dependencies, and the certification basis. A Python relationship-policy registry becomes the executable authority and deterministically renders the schema metadata view. A module may own its gateway directly or bind it to one contained behavioral source; source-owned gateway bytes remain in the source's local hash and reach the module through a certification dependency.

**Tech Stack:** Python 3, JSON Schema draft 7 or the canonical post-v5 successor, PyYAML, pytest, the Officina blueprint graph and certification packages, and the existing skill-maker synchronization interface.

## Global Constraints

- This plan implements only agreements 2, 4, and 7 below.
- Do not add semantic-review evidence to certificates.
- Keep `source_commit` in the signed certificate payload and certifier identity as issuance provenance.
- Issuance must still require `HEAD == reviewed_commit`, no tracked worktree changes, and stable audited bytes.
- Currentness must not depend on the current repository `HEAD` when the node, its dependencies, and the certification basis are unchanged.
- The Python relationship registry is canonical; `schema-meta.json` is a generated readable view.
- A source-bound module gateway must target exactly one contained behavioral source at an exact version.
- Behavioral-source gateways remain direct whole-file declarations.
- Direct module gateways remain valid for modules that own their gateway bytes.
- Do not add nested-module topology, machine-compatibility admission, standalone packaging, or dispatcher changes.
- The concurrent nested-modules-v5 work owns schema-version cutover. Execute this plan only after that work stops changing the shared schema, graph, hashing, and migration files; apply the changes to the resulting canonical unversioned paths and do not modify archived v4 schema bundles.
- Do not stage, commit, push, append certificates, or alter signing material without separate user authorization.

---

## Agreements

### 2. A certification commit identifies the audited snapshot; it is not a currentness condition

**Problem**

1. `evaluate_certificate_currentness()` currently compares `payload.source_commit` with the current checkout commit.
2. It also compares the complete certifier identity, whose `source_commit` likewise changes at every later commit.
3. An unrelated clean commit therefore makes unchanged certificates suspect even when every relevant byte and dependency hash is unchanged.

**Agreement**

1. Certification issuance is tied to one exact clean committed snapshot.
2. `source_commit` records that snapshot and remains signed.
3. Later currentness compares the signed node state, dependency state, certifier functional identity, certification basis, checks, signature, and history.
4. Later currentness does not require either recorded commit to equal current `HEAD`.

**Why**

The repository is only a container for many nodes. A commit outside a node's relevant closure is not drift in that node. Retaining certification across such commits is the purpose of the drift mechanism.

### 4. One gateway fact has one owner

**Problem**

1. A module and one of its behavioral sources can separately declare the same gateway path, language, and machines.
2. The two declarations can disagree.
3. The gateway file has one direct owner, but mandatory gateway closure currently places the same bytes in both local node manifests.
4. This confuses containment with ownership and causes a module's local hash to absorb source-owned bytes.

**Agreement**

1. A behavioral source continues to declare a direct gateway.
2. A module either declares a direct gateway that it owns or binds its gateway to one contained behavioral source.
3. A source-bound module derives the gateway path, language, and machines from that source.
4. The module records the binding in its local blueprint state.
5. The source owns and locally hashes the gateway bytes.
6. The module depends on the source's certified node state through `binds-gateway-source`.

**Why**

A fact should be authored once. Local hashes should cover what a node owns; certificate dependencies should cover what it relies on.

### 7. Executable relationship policy has one authority

**Problem**

1. `references/blueprint/schema-meta.json` describes `x-famulus.relationship_matrix` as the complete relationship policy.
2. `src/officina/common/blueprint_graph.py` constructs and checks relationships independently of that matrix.
3. Tests repeat the metadata literal rather than proving that enforcement and documentation share one source.
4. The documented matrix and the executable graph can therefore drift while both local test groups pass.

**Agreement**

1. A small Python registry owns the allowed source-kind, relation, and target-kind triples.
2. Graph and hashing code consult that registry when materializing authored relationships.
3. `schema-meta.json` is rendered from the registry by the existing skill-maker synchronization route.
4. Tests compare the generated view with the registry instead of maintaining a second literal.

**Why**

A normative rule that can be checked mechanically should be represented once in executable form. Other representations should be derived views.

---

## File Map

**Create**

- `src/officina/common/relationship_policy.py` — canonical relationship registry, validation, and deterministic JSON projection.
- `src/officina/common/blueprints/relationship-policy.yaml` — behavioral-source description of the registry.
- `tests/test_officina_relationship_policy.py` — isolated policy and rendering tests.

**Modify for agreement 2**

- `src/officina/common/certification_view.py`
- `tests/test_officina_certification_view.py`
- `skills/skill-certifier/tests/test_certifier.py`
- `references/blueprint/certificate.schema.json`
- `docs/certification_and_drift.md`
- `docs/architecture.md`

**Modify for agreement 7**

- `src/officina/common/blueprint_graph.py`
- `src/officina/common/certification_hashing.py`
- `src/officina/common/blueprint.yaml`
- `src/officina/common/blueprints/blueprint-graph.yaml`
- `src/officina/common/blueprints/certification-hashing.yaml`
- `skills/skill-maker/_rtx/_blueprint_syncer.py`
- `skills/skill-maker/blueprints/rtx-blueprint-syncer.yaml`
- `skills/skill-drift/references/certification-basis-roots.json`
- `references/blueprint/schema-meta.json`
- `tests/test_blueprint_schema_metadata.py`
- `tests/test_node_certification_hashing.py`
- `skills/skill-maker/tests/test_blueprint_tools.py`

**Modify for agreement 4**

- `references/blueprint/common.schema.json`
- `references/blueprint/module.schema.json`
- `references/blueprint/schema.annotated-draft.json`
- `references/blueprint/template.yaml`
- `src/officina/common/blueprint_graph.py`
- `src/officina/common/certification_hashing.py`
- `src/officina/common/relationship_policy.py`
- `references/blueprint/schema-meta.json`
- `tests/test_typed_blueprint_schemas.py`
- `tests/test_officina_blueprint_graph.py`
- `tests/test_node_certification_hashing.py`
- `docs/skill-blueprints.md`
- `docs/architecture.md`
- `docs/certification_and_drift.md`
- Repository module blueprints satisfying the exact migration predicate in Task 3.

---

### Task 1: Separate audit provenance from certificate currentness — Agreement 2

**Files:**

- Modify: `src/officina/common/certification_view.py`
- Modify: `tests/test_officina_certification_view.py`
- Modify: `skills/skill-certifier/tests/test_certifier.py`
- Modify: `references/blueprint/certificate.schema.json`
- Modify: `docs/certification_and_drift.md`
- Modify: `docs/architecture.md`

**Interfaces:**

- Produces:

```python
def certifier_currentness_identity(
    identity: Mapping[str, object],
) -> dict[str, object]:
    """Return interface, version, and node_hash; omit issuance provenance."""
```

- Changes:

```python
def evaluate_certificate_currentness(
    graph: RepositoryBlueprintGraph,
    states: Mapping[str, NodeHashState],
    *,
    repo_root: Path,
    public_key_root: Path,
    certifier_identity: Mapping[str, object],
    checks_by_node: Mapping[str, Sequence[Mapping[str, object]]],
    schema_root: Path,
    allow_non_atomic: bool = False,
) -> CertificateCurrentnessReport:
    ...
```

- Preserves: `derive_certifier_identity(..., source_commit)` and the signed `payload.source_commit`.
- Preserves: exact reviewed-commit and clean-tree gates in `_certify_v4_repository()` or its post-v5 replacement.

- [ ] **Step 1: Write failing unit tests for commit-insensitive currentness.**

  Add cases proving that changing only `payload.source_commit` does not make a structurally valid signed certificate stale, and changing only `payload.certifier.source_commit` does not make it stale. Keep mismatch tests for the certifier interface, version, and node hash.

```python
def test_currentness_treats_source_commits_as_provenance(tmp_path: Path) -> None:
    graph, states, commit, public_key_root, _backend, key = _fixture(tmp_path)
    node_id = "demo-skill"
    payload = _payload(tmp_path, graph, states, node_id, commit, key.key_id)
    payload["source_commit"] = "d" * 40
    payload["certifier"] = {**payload["certifier"], "source_commit": "e" * 40}
    _write_log(graph, node_id, [sign_certificate_payload(payload, key)])

    status = _evaluate(tmp_path, graph, states, commit, public_key_root).nodes[node_id]

    assert status.current
```

- [ ] **Step 2: Write the repository regression test.**

  Start from `create_certified_fixture()`, commit a tracked file outside every node and certification-basis pattern, derive repository certification state at the new `HEAD`, and assert that every unchanged certificate remains current. Add companion assertions that a changed node input, changed dependency hash, or changed basis input remains suspect.

```python
def test_unrelated_clean_commit_preserves_current_certificates(tmp_path: Path) -> None:
    graph, _states, _commit, public_keys, _backend, _key = create_certified_fixture(
        tmp_path
    )
    repository = GitTestRepository(tmp_path)
    (tmp_path / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    repository.git("add", "unrelated.txt")
    repository.git("commit", "-qm", "unrelated change")

    state = derive_repository_certification_state(
        tmp_path,
        public_key_root=public_keys,
    )

    assert set(state.currentness.nodes) == set(graph.nodes)
    assert all(item.current for item in state.currentness.nodes.values())
```

- [ ] **Step 3: Run the focused currentness tests and confirm the intended failures.**

  Run:

```bash
pytest -q -o pythonpath=src \
  tests/test_officina_certification_view.py \
  skills/skill-certifier/tests/test_certifier.py
```

  Expected before implementation: failures name `source-commit-mismatch` or `certifier-mismatch`; exact-issuance tests continue to pass.

- [ ] **Step 4: Remove current-HEAD commit comparisons from currentness.**

  Remove the `source_commit` parameter from `evaluate_certificate_currentness()` and all call sites. Delete the direct `payload.source_commit == current HEAD` condition. Compare certifier identities through `certifier_currentness_identity()` so only `interface`, `version`, and `node_hash` affect currentness. Do not remove or rewrite either signed commit field.

```python
CERTIFIER_CURRENTNESS_FIELDS = ("interface", "version", "node_hash")


def certifier_currentness_identity(
    identity: Mapping[str, object],
) -> dict[str, object]:
    return {
        field: identity.get(field)
        for field in CERTIFIER_CURRENTNESS_FIELDS
    }
```

- [ ] **Step 5: Preserve issuance guarantees with explicit regression tests.**

  Add focused writer tests proving that `_certify_v4_repository()` or its post-v5 replacement still rejects:

  1. `reviewed_commit` different from current `HEAD`;
  2. tracked changes present at issuance;
  3. audited tracked bytes changing between derivation and append.

  These tests must exercise issuance, not `evaluate_certificate_currentness()`.

- [ ] **Step 6: Correct the normative descriptions.**

  In `certificate.schema.json`, describe both commit fields as signed audit provenance. In the two architecture documents, state:

  1. tracked audited inputs must agree with `source_commit` during issuance;
  2. later currentness reconstructs relevant state and does not compare current `HEAD`;
  3. the certifier's functional identity is interface, version, and node hash;
  4. the certifier's commit remains provenance.

- [ ] **Step 7: Run the focused tests again.**

```bash
pytest -q -o pythonpath=src \
  tests/test_officina_certification_view.py \
  skills/skill-certifier/tests/test_certifier.py \
  tests/test_node_certification_hashing.py
```

  Require: unrelated clean commits preserve currentness; relevant input, dependency, basis, certifier code, checks, signature, and history changes still make certificates suspect.

### Task 2: Make relationship policy executable and canonical — Agreement 7

**Files:**

- Create: `src/officina/common/relationship_policy.py`
- Create: `src/officina/common/blueprints/relationship-policy.yaml`
- Create: `tests/test_officina_relationship_policy.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/certification_hashing.py`
- Modify: `src/officina/common/blueprint.yaml`
- Modify: `src/officina/common/blueprints/blueprint-graph.yaml`
- Modify: `src/officina/common/blueprints/certification-hashing.yaml`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `skills/skill-maker/blueprints/rtx-blueprint-syncer.yaml`
- Modify: `skills/skill-drift/references/certification-basis-roots.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `tests/test_blueprint_schema_metadata.py`
- Modify: `tests/test_node_certification_hashing.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`

**Interfaces:**

- Produces:

```python
class RelationshipPolicyError(ValueError):
    pass


RELATIONSHIP_TARGET_KINDS: Mapping[
    str,
    Mapping[str, frozenset[str]],
]


def validate_relationship(
    source_kind: str,
    relation: str,
    target_kind: str,
) -> None:
    ...


def relationship_matrix_payload() -> dict[str, dict[str, list[str]]]:
    ...
```

- Produces in the existing syncer:

```python
def sync_relationship_matrix(*, check_only: bool) -> list[str]:
    ...
```

- [ ] **Step 1: Write the isolated policy tests.**

  Define tests for every current allowed triple, rejection of an unknown source kind, relation, and target kind, deterministic sorted projection, and the new Task 3 triple:

```python
def test_relationship_policy_accepts_only_registered_triples() -> None:
    validate_relationship("module", "contains-source", "behavioral_source")
    validate_relationship("module", "binds-gateway-source", "behavioral_source")
    validate_relationship("behavioral_source", "uses-export", "module")

    with pytest.raises(RelationshipPolicyError):
        validate_relationship("module", "uses-export", "module")


def test_relationship_matrix_payload_is_deterministic() -> None:
    assert relationship_matrix_payload() == relationship_matrix_payload()
    assert relationship_matrix_payload()["module"]["binds-gateway-source"] == [
        "behavioral_source"
    ]
```

- [ ] **Step 2: Run the isolated test and confirm it fails because the policy module is absent.**

```bash
pytest -q -o pythonpath=src tests/test_officina_relationship_policy.py
```

- [ ] **Step 3: Implement the closed registry and projection.**

  Store immutable sets internally. Return newly allocated, lexically sorted dictionaries and lists from `relationship_matrix_payload()` so callers cannot mutate the registry.

```python
RELATIONSHIP_TARGET_KINDS = {
    "module": {
        "binds-gateway-source": frozenset({"behavioral_source"}),
        "contains-source": frozenset({"behavioral_source"}),
        "exports-interface": frozenset({"behavioral_source"}),
        "references-cross-owner-contract": frozenset(
            {"module", "behavioral_source"}
        ),
    },
    "behavioral_source": {
        "references-cross-owner-contract": frozenset(
            {"module", "behavioral_source"}
        ),
        "uses-export": frozenset({"module"}),
        "uses-private-interface": frozenset({"behavioral_source"}),
        "uses-source": frozenset({"behavioral_source"}),
    },
}
```

- [ ] **Step 4: Route authored relation construction through the registry.**

  In `blueprint_graph.py`, call `validate_relationship()` after resolving both logical endpoint kinds and before recording:

  1. `contains-source`;
  2. `exports-interface`;
  3. `uses-source`;
  4. `uses-private-interface`;
  5. `uses-export`;
  6. `binds-gateway-source` from Task 3.

  For `uses-export`, validate against the exporting module kind even though `BlueprintEdge.target_id` stores the export interface ID. In `certification_hashing.py`, validate `references-cross-owner-contract` against the direct owner node's kind before recording the dependency. Do not apply the authored-node matrix to derived projection edges whose endpoint types have different meanings.

- [ ] **Step 5: Register the new common source and its consumers.**

  Add `relationship_policy.py` to `common` content, contain `common.source.relationship-policy`, and export `common.interface.relationship-policy` for `skill-maker`. Declare source dependencies from `blueprint-graph` and `certification-hashing`, and add `common.interface.relationship-policy@1` to the blueprint-syncer source. Add `relationship_policy.py` to the certification-basis roots because changing relationship admissibility changes certification semantics.

- [ ] **Step 6: Make `schema-meta.json` a generated view.**

  Add `SCHEMA_META_PATH`. `sync_relationship_matrix()` must load the existing JSON object, replace only `x-famulus.relationship_matrix`, serialize with `indent=2` plus one final newline, and use the existing confined atomic-write helper. In check mode it returns:

```text
references/blueprint/schema-meta.json: relationship matrix is out of sync
```

  Call it from `run_sync()`. Update the syncer's blueprint contract, direct I/O declaration, effect list, and user warning so writing `schema-meta.json` is declared behavior.

- [ ] **Step 7: Replace duplicate-literal tests with derivation tests.**

  Change `test_schema_meta_declares_only_the_v4_relationship_matrix()` to compare the stored matrix with `relationship_matrix_payload()`. Add syncer tests that corrupt one target-kind list, assert `--check`-equivalent behavior reports the stale view, run synchronization, and assert exact repair without changing other metadata.

- [ ] **Step 8: Update the metadata rule description and regenerate the view.**

  Change the `relationship-matrix` catalog entry so its creation rule names the Python registry as authority and `x-famulus.relationship_matrix` as its generated view. Refresh through:

```bash
dispatcher --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints
dispatcher --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints --check
```

- [ ] **Step 9: Run the policy, graph, hashing, metadata, and syncer tests.**

```bash
pytest -q -o pythonpath=src \
  tests/test_officina_relationship_policy.py \
  tests/test_officina_blueprint_graph.py \
  tests/test_node_certification_hashing.py \
  tests/test_blueprint_schema_metadata.py \
  skills/skill-maker/tests/test_blueprint_tools.py \
  tests/validate_blueprint_relationships.py
```

  Require: the generated matrix equals the executable registry, stale metadata fails check mode, and graph/hashing relation creation cannot bypass the registry.

### Task 3: Add source-bound module gateways — Agreement 4

**Files:**

- Modify: `references/blueprint/common.schema.json`
- Modify: `references/blueprint/module.schema.json`
- Modify: `references/blueprint/schema.annotated-draft.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/certification_hashing.py`
- Modify: `src/officina/common/relationship_policy.py`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `tests/test_typed_blueprint_schemas.py`
- Modify: `tests/test_officina_blueprint_graph.py`
- Modify: `tests/test_node_certification_hashing.py`
- Modify: repository module blueprints selected by Step 7
- Modify: `docs/skill-blueprints.md`
- Modify: `docs/architecture.md`
- Modify: `docs/certification_and_drift.md`

**Interfaces:**

- Adds the module-only gateway reference:

```yaml
gateway:
  source: example-skill.source.gateway
  version: 1
```

- Extends the graph node without breaking existing constructors:

```python
@dataclass(frozen=True)
class BlueprintNode:
    # existing fields remain unchanged
    gateway_source_id: str | None = None
    resolved_gateway: Mapping[str, JsonValue] | None = None
```

- Preserves: `BlueprintNode.gateway_path` as the resolved operational path for both direct and source-bound gateways.
- Produces: `BlueprintNode.resolved_gateway` as the direct gateway declaration or the bound source's gateway declaration.
- Produces: authored and certification relation `binds-gateway-source`.

- [ ] **Step 1: Write failing schema tests for the closed union.**

  Test that a module accepts either:

```yaml
gateway:
  path: __init__.py
  language: Python
```

  or:

```yaml
gateway:
  source: demo-skill.source.gateway
  version: 1
```

  Reject mixed forms, missing versions, zero or negative versions, behavioral-source gateway references, and extra fields.

- [ ] **Step 2: Write failing graph tests for resolution.**

  Cover:

  1. a source-bound module resolves `gateway_path`, `gateway_source_id`, language, and machines from its contained source;
  2. the binding emits one `binds-gateway-source` authored edge and one certification edge;
  3. unknown, cross-module, non-contained, or stale-version sources fail closed;
  4. a direct module gateway behaves exactly as before;
  5. source gateway confinement and regular-file checks remain authoritative.

```python
assert graph.nodes["demo-skill"].gateway_source_id == (
    "demo-skill.source.gateway"
)
assert graph.nodes["demo-skill"].gateway_path == graph.nodes[
    "demo-skill.source.gateway"
].gateway_path
assert graph.nodes["demo-skill"].resolved_gateway == graph.nodes[
    "demo-skill.source.gateway"
].resolved_gateway
assert (
    "demo-skill",
    "binds-gateway-source",
    "demo-skill.source.gateway",
    1,
) in {
    (
        edge.source_node_id,
        edge.relation,
        edge.target_node_id,
        edge.target_version,
    )
    for edge in graph.certification_edges
}
```

- [ ] **Step 3: Write failing hashing tests for ownership and dependency.**

  For a source-bound gateway, assert:

  1. the module manifest contains its blueprint but not the source-owned gateway bytes;
  2. the source manifest contains the gateway bytes;
  3. changing gateway bytes changes the source local hash;
  4. the module local node hash remains unchanged;
  5. the module's `binds-gateway-source` dependency claim changes;
  6. a direct module gateway remains a mandatory module-local input.

- [ ] **Step 4: Run the focused tests and confirm failures identify the absent union, resolver, and dependency.**

```bash
pytest -q -o pythonpath=src \
  tests/test_typed_blueprint_schemas.py \
  tests/test_officina_blueprint_graph.py \
  tests/test_node_certification_hashing.py
```

- [ ] **Step 5: Implement the schema union.**

  Add a closed `sourceGatewayBinding` definition to `common.schema.json`. Change only the module's `gateway` property to a `oneOf` between direct `gateway` and `sourceGatewayBinding`. Keep the behavioral-source schema bound directly to `common.schema.json#/definitions/gateway`. Carry the same rule into the annotated draft and template.

- [ ] **Step 6: Resolve bindings after containment is known.**

  Keep direct gateway extraction in the initial document-to-node pass. After `module_sources` and `source_modules` are resolved:

  1. detect module gateway declarations with `source`;
  2. require the source to occur in `module_sources[module_id]`;
  3. require the exact source version;
  4. require the source to have a direct resolved gateway;
  5. replace the module graph node with the source's resolved `gateway_path`, `resolved_gateway`, and `gateway_source_id`;
  6. validate and record `binds-gateway-source`.

  Initialize `resolved_gateway` from the direct object for ordinary modules and sources. Downstream operational consumers use `node.gateway_path` for the file and `node.resolved_gateway` for language or machines; they must not re-read those fields from a module's reference object.

- [ ] **Step 7: Align hashing with ownership.**

  In mandatory gateway closure, include `node.gateway_path` only when `node.gateway_source_id is None`. Add the resolved `binds-gateway-source` certification edge so dependency hashes carry the source's exact version and node hash. Keep the module blueprint itself in the module manifest; changing the binding therefore remains a local module change.

- [ ] **Step 8: Migrate only exact duplicate declarations.**

  For each canonical module blueprint:

  1. find contained sources whose direct gateway object exactly equals the module's direct gateway object after canonical normalization;
  2. if exactly one source matches, replace the module gateway with `{source: <id>, version: <source-version>}`;
  3. if none match, retain the direct module gateway;
  4. if more than one matches, stop and report the ambiguity rather than choosing;
  5. do not alter behavioral-source gateways or archived v4 migration inputs.

  Review the resulting file list. Expected skill modules bind to their instruction gateway sources; utility modules with their own operational file, such as a package `__init__.py` not owned by a contained source, remain direct.

- [ ] **Step 9: Regenerate schema metadata and test the migrated repository graph.**

```bash
dispatcher --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints
dispatcher --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints --check
pytest -q -o pythonpath=src \
  tests/test_typed_blueprint_schemas.py \
  tests/test_officina_blueprint_graph.py \
  tests/test_node_certification_hashing.py \
  tests/test_blueprint_schema_metadata.py
```

- [ ] **Step 10: Document the direct/reference distinction.**

  State in the three architecture documents:

  1. direct gateway means the node owns the gateway fact and bytes;
  2. source-bound gateway means the module derives the operational gateway from a contained source;
  3. the module hashes the binding while the source hashes the file;
  4. `binds-gateway-source` carries certification drift from source to module;
  5. containment alone does not make every contained source a module certification dependency.

### Task 4: Run the agreement-level integration gate

**Files:**

- Verify only; repair failures in the owning task's files.

**Interfaces:**

- Consumes: Task 1 currentness semantics, Task 2 relationship registry, and Task 3 gateway binding.
- Produces: one verified canonical repository state with generated views synchronized.

- [ ] **Step 1: Run exact regression searches.**

```bash
rg -n \
  'source-commit-mismatch|payload\.get\("source_commit"\) !=|relationship_matrix.*== \{' \
  src tests skills references docs
```

  Require: no current-HEAD currentness comparison and no independently maintained relationship-matrix literal. Schema requirements and issuance provenance references to `source_commit` remain valid.

- [ ] **Step 2: Run all focused suites together.**

```bash
pytest -q -o pythonpath=src \
  tests/test_officina_certification_view.py \
  skills/skill-certifier/tests/test_certifier.py \
  tests/test_officina_relationship_policy.py \
  tests/test_officina_blueprint_graph.py \
  tests/test_node_certification_hashing.py \
  tests/test_typed_blueprint_schemas.py \
  tests/test_blueprint_schema_metadata.py \
  skills/skill-maker/tests/test_blueprint_tools.py \
  tests/validate_blueprint_relationships.py
```

- [ ] **Step 3: Check generated artifacts and repository validators.**

```bash
dispatcher --caller-skill skill-maker \
  skill-maker._rtx.interface.sync-blueprints --check
python3 validators/runner.py
git diff --check
```

- [ ] **Step 4: Run the full Python suite.**

```bash
pytest -q -o pythonpath=src
```

- [ ] **Step 5: Inspect the final diff by agreement.**

  Confirm:

  1. Agreement 2 changed currentness but did not weaken issuance.
  2. Agreement 7 removed the metadata/enforcement dual authority.
  3. Agreement 4 removed duplicate gateway authorship without making all containment edges certification dependencies.
  4. No evidence fields, dispatcher admission, nested topology, packaging, signing, certificate append, staging, commit, or push entered the change.
