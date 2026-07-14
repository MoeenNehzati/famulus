# Node-Local Commit-Backed Health Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace whole-graph, working-tree health snapshots with schema-defined, node-local, commit-backed certifications that are safe to reuse, write atomically, and evaluate against an exact target closure.

**Architecture:** The schema family defines every authoring and validation rule before runtime changes begin. `blueprint_graph.py` provides canonical node ownership, visibility, edge-set, and reachable-closure semantics. New shared Git-provenance and atomic-file modules support node-local stamping; `artifact_health.py` builds or verifies one node record at a time; `skill-audit` orchestrates bottom-up reuse or refresh; `skill-drift` remains read-only and target-relative.

**Tech Stack:** Python 3.10+, Draft 7 JSON Schema, PyYAML, `hashlib`, `hmac`, `json`, `subprocess`, `os`, `secrets`, pytest. No new runtime dependency.

## Global Constraints

- Only `skill-audit` writes health records.
- A selected target means one root plus its reachable closure; unrelated skills are ignored.
- Behavior-source visibility is location-based: declaring skill-local files or repository-root `references/`.
- Cross-skill interface dependencies use canonical interface IDs and pinned versions.
- Existing healthy child records are authenticated and live-hash checked, but their Git status is not revisited.
- Every new node stamp requires node-local inputs matching a captured Git commit.
- Dirty and non-Git nodes receive complete semantic results but no new stamp.
- Health, pooled-review, and HMAC-key files remain Git-ignored.
- `_cx` commands and every relevant path component are non-symlinks.
- Source Git tracking is a schema-referenced repository validator rule, not a dispatcher runtime requirement.
- Raw stdout, stderr, elapsed times, and temporary paths never enter certified-health hashes.
- Pooled artifacts and `pooled-review.schema.json` never affect canonical root health.
- The running trusted certifier reads but never executes copied target code.
- Every non-JSON-Schema rule has a machine-readable rule ID, validator path, test paths, creation guidance, and template behavior.
- Do not touch unrelated dirty files or concurrent `email-triage` work.
- Do not create commits unless the user explicitly approves a stable checkpoint.

---

## File Responsibility Map

- `references/blueprint/*.schema.json`: normative data contracts.
- `references/blueprint/schema-meta.json`: normative non-JSON-Schema rules and implementation/test links.
- `references/blueprint/template.yaml`: schema-family creation entry point and complete sidecar examples.
- `src/officina/common/blueprint_graph.py`: canonical graph identity, visibility, closure, and edge-set semantics.
- `src/officina/common/git_provenance.py`: Git snapshot capture and node-local commit readiness.
- `src/officina/common/atomic_files.py`: fail-closed, symlink-safe atomic replacement and secret creation.
- `src/officina/common/artifact_health.py`: stable hash projections, record admission, one-node record construction, and graph health.
- `src/officina/common/audit_records.py`: canonical record authentication and HMAC-key loading through atomic files.
- `src/officina/common/pooled_blueprint.py`: canonical rendering and independent pool validation.
- `skills/skill-audit/_rtx/_audit_certifier.py`: exact-target audit orchestration and user-facing audit outcomes.
- `skills/skill-drift/_rtx/_check_drift_state.py`: read-only exact-target status and graph-native hash output.
- `skills/skill-drift/_rtx/_drift_hashes.py`: legacy compatibility hashing only; no target code execution.
- `skills/skill-maker/validators/*.py`: schema-referenced repository enforcement.
- `src/officina/dispatcher/core.py`: runtime typed-schema and executable-surface enforcement without Git.

---

### Task 1: Make The Schema Family Complete And Normative

**Files:**
- Modify: `references/blueprint/common.schema.json`
- Modify: `references/blueprint/skill.schema.json`
- Modify: `references/blueprint/llm-interface.schema.json`
- Modify: `references/blueprint/machine-interface.schema.json`
- Modify: `references/blueprint/behavior-source.schema.json`
- Modify: `references/blueprint/health.schema.json`
- Modify: `references/blueprint/pooled-review.schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `tests/test_blueprint_schema_metadata.py`
- Modify: `tests/test_typed_blueprint_schemas.py`
- Modify: `tests/test_officina_blueprint_template.py`

**Interfaces:**
- Produces: rule IDs `generated-contract-block`, `sidecar-naming`, `binding-tracked`, `binding-non-symlink`, `behavior-source-visibility`, `relationship-matrix`, `commit-backed-stamp`, and `canonical-pooled-review`.
- Produces: health `source` object with `vcs`, `commit`, and `input_paths`.
- Consumes: existing `x-famulus` field metadata protocol.

- [x] **Step 1: Write failing schema-authority tests**

Add tests that enumerate validator rule constants and require matching
`schema-meta.json#/x-famulus/validation_rule_catalog` entries:

```python
REQUIRED_RULES = {
    "generated-contract-block",
    "sidecar-naming",
    "binding-tracked",
    "binding-non-symlink",
    "behavior-source-visibility",
    "relationship-matrix",
    "commit-backed-stamp",
    "canonical-pooled-review",
}

def test_schema_meta_catalogs_every_repository_rule(schema_meta):
    catalog = schema_meta["x-famulus"]["validation_rule_catalog"]
    assert REQUIRED_RULES <= set(catalog)
    for rule_id in REQUIRED_RULES:
        rule = catalog[rule_id]
        assert rule["creation"]
        assert rule["validator"]
        assert rule["tests"]
        assert "template" in rule
```

Add valid and invalid health fixtures:

```python
def test_node_health_requires_commit_backed_source(health_validator, node_health):
    node_health["source"] = {
        "vcs": "git",
        "commit": "a" * 40,
        "input_paths": ["skills/demo/SKILL.md", "skills/demo/.SKILL.md.blueprint.yaml"],
    }
    health_validator.validate(node_health)

def test_node_health_rejects_missing_source(health_validator, node_health):
    node_health.pop("source", None)
    with pytest.raises(jsonschema.ValidationError):
        health_validator.validate(node_health)
```

- [x] **Step 2: Run schema tests and verify they fail**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_blueprint_schema_metadata.py tests/test_typed_blueprint_schemas.py tests/test_officina_blueprint_template.py -q
```

Expected: FAIL for missing rule catalog entries, missing health `source`, and incomplete template lifecycle metadata.

- [x] **Step 3: Extend the health schema**

Add this definition and require it only for `node-health` and
`skill-health` branches:

```json
"gitSource": {
  "type": "object",
  "required": ["vcs", "commit", "input_paths"],
  "additionalProperties": false,
  "properties": {
    "vcs": {"const": "git"},
    "commit": {
      "type": "string",
      "pattern": "^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
    },
    "input_paths": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {
        "type": "string",
        "minLength": 1,
        "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$"
      }
    }
  }
}
```

Node branches add `"required": ["source"]`; pooled health does not define
`source`. Tighten checks to stable fields:

```json
"check": {
  "type": "object",
  "required": ["id", "version", "passed", "findings"],
  "additionalProperties": false,
  "properties": {
    "id": {"type": "string", "minLength": 1},
    "version": {"type": "integer", "minimum": 1},
    "passed": {"const": true},
    "findings": {"type": "array", "items": {"type": "string"}}
  }
}
```

- [x] **Step 4: Encode layout and relationship policy**

Add machine-readable metadata:

```json
"relationship_matrix": {
  "skill": {"declares-interface": ["llm-interface", "machine-interface"]},
  "llm-interface": {
    "uses-interface": ["llm-interface", "machine-interface"],
    "uses-behavior-source": ["behavior-source"]
  },
  "machine-interface": {
    "uses-interface": ["machine-interface"],
    "uses-behavior-source": ["behavior-source"]
  },
  "behavior-source": {
    "uses-interface": ["llm-interface", "machine-interface"],
    "uses-behavior-source": ["behavior-source"]
  }
},
"behavior_source_visibility": {
  "skill_local": "declaring-skill-only",
  "repository_references": "all-skills"
}
```

Catalog each rule with concrete validator and test paths. Define the sidecar
derivation algorithm, qualified naming for multiple nodes bound to one file,
generated `SKILL.md` blocks, tracked-source requirements, and non-symlink
requirements in `creation` and `template` fields.

Add `local_hash_inputs` to each file-backed typed node:

```json
"local_hash_inputs": {
  "type": "array",
  "uniqueItems": true,
  "items": {
    "type": "string",
    "minLength": 1,
    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$"
  },
  "x-famulus": {
    "field_status": "optional",
    "audit_hash": "include",
    "template": {"include": false},
    "doc": {
      "authoring": [
        "List exact additional files owned by this node whose bytes affect its local behavior. Child-node bindings do not belong here."
      ]
    },
    "related_validation_rules": ["file-binding", "commit-backed-stamp"]
  }
}
```

Paths resolve against the node owner root, must name tracked regular
non-symlink files, and participate in both local hashing and commit readiness.

- [x] **Step 5: Make schema-only creation executable**

Extend `template.yaml` with complete examples for:

```yaml
examples:
  skill_root: blueprint.yaml
  default_llm: .SKILL.md.blueprint.yaml
  shared_python_interfaces:
    - _rtx/._runner.py.first.blueprint.yaml
    - _rtx/._runner.py.second.blueprint.yaml
  command_interface: _cx/._command.blueprint.yaml
  repository_behavior_source: references/.policy.md.blueprint.yaml
generated_outputs:
  - SKILL.md blueprint contract block
  - SKILL.md blueprint interface block
```

Update the template tests to generate a complete temporary graph from only
schema-family inputs and assert that every schema-referenced validator accepts
it.

- [x] **Step 6: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 7: Record a stable checkpoint**

Report the exact schema and test files changed. Do not stage or commit without
explicit user approval.

---

### Task 2: Canonicalize Shared Graph Identity, Visibility, And Edge Sets

**Files:**
- Modify: `src/officina/common/blueprint_graph.py:18-545`
- Modify: `skills/skill-maker/validators/blueprint_relationships.py:21-193`
- Modify: `skills/skill-maker/validators/interface_ids.py`
- Modify: `tests/test_officina_blueprint_graph.py`
- Modify: `tests/validate_blueprint_relationships.py`
- Modify: `tests/validate_interface_ids.py`

**Interfaces:**
- Produces: `node_owner_namespace(node: BlueprintNode, repo_root: Path) -> str`.
- Produces: `edge_key(edge: BlueprintEdge) -> tuple[str, str, str, int, str | None]`.
- Produces: `postorder_node_ids(graph: SkillBlueprintGraph) -> tuple[str, ...]`.
- Produces: identical edge sets from targeted and repository-wide resolution.
- Consumes: Task 1 relationship matrix and visibility metadata.

- [x] **Step 1: Add failing shared-graph tests**

```python
def test_repository_resolution_deduplicates_shared_source_edges(shared_repo):
    targeted = load_reachable_repository_skill_graph(shared_repo, "first-skill")
    resolved = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(shared_repo), {"first-skill", "second-skill"}
    )
    assert edge_projection(targeted, "references.source.shared") == edge_projection(
        resolved, "references.source.shared"
    )

def test_skill_cannot_directly_reference_other_skill_behavior_source(shared_repo):
    errors = validate_graphs(load_repository_blueprint_graphs(shared_repo))
    assert any("behavior source outside declaring skill or repository references" in e for e in errors)

def test_repository_reference_namespace_is_required(shared_repo):
    sidecar = shared_repo / "references/.shared.md.blueprint.yaml"
    replace_id(sidecar, "alien-skill.source.shared")
    with pytest.raises(BlueprintGraphError, match="references.source"):
        load_reachable_repository_skill_graph(shared_repo, "first-skill")
```

- [x] **Step 2: Run focused tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_officina_blueprint_graph.py tests/validate_blueprint_relationships.py tests/validate_interface_ids.py -q
```

Expected: FAIL for duplicate cross-root edges, missing visibility checks, and owner/ID mismatch.

- [x] **Step 3: Add explicit owner and edge helpers**

```python
def node_owner_namespace(node: BlueprintNode, repo_root: Path) -> str:
    blueprint = node.blueprint_path.resolve()
    references = (repo_root / "references").resolve()
    if blueprint.is_relative_to(references):
        return "references"
    skills = (repo_root / "skills").resolve()
    relative = blueprint.relative_to(skills)
    return relative.parts[0]


def edge_key(edge: BlueprintEdge) -> tuple[str, str, str, int, str | None]:
    return (
        edge.relation,
        edge.source_id,
        edge.target_id,
        edge.required_version,
        edge.target_blueprint_path.as_posix() if edge.target_blueprint_path else None,
    )


def postorder_node_ids(graph: SkillBlueprintGraph) -> tuple[str, ...]:
    children: dict[str, list[str]] = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges:
        children[edge.source_id].append(edge.target_id)
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for child_id in sorted(children[node_id]):
            visit(child_id)
        ordered.append(node_id)

    visit(graph.root.node_id)
    return tuple(ordered)
```

Use an ordered dictionary keyed by `edge_key` when combining graphs. Continue
to reject duplicate authored edges inside one graph before repository
composition.

- [x] **Step 4: Enforce behavior-source visibility**

In relationship validation:

```python
def behavior_source_visible(
    source: BlueprintNode,
    target: BlueprintNode,
    repo_root: Path,
) -> bool:
    target_owner = node_owner_namespace(target, repo_root)
    if target_owner == "references":
        return True
    return target_owner == node_owner_namespace(source, repo_root)
```

Apply this only to direct `uses-behavior-source` edges. Cross-skill interface
edges remain governed by the schema relationship matrix and interface access
control.

- [x] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS, including unchanged legacy graph tests.

- [x] **Step 6: Record a stable checkpoint**

Report graph behavior and test evidence. Do not commit automatically.

---

### Task 3: Add Git Snapshot And Node-Local Commit Readiness

**Files:**
- Create: `src/officina/common/git_provenance.py`
- Modify: `src/officina/common/__init__.py`
- Create: `tests/test_officina_git_provenance.py`

**Interfaces:**
- Produces: `GitSnapshot(repo_root: Path, commit: str)`.
- Produces: `CommitReadiness(stamp_worthy: bool, source: dict[str, object] | None, reasons: tuple[str, ...])`.
- Produces: `capture_git_snapshot(path: Path) -> GitSnapshot | None`.
- Produces: `check_commit_readiness(snapshot, input_paths, expected_hashes) -> CommitReadiness`.
- Produces: `snapshot_head_matches(snapshot) -> bool`.

- [x] **Step 1: Write failing Git-provenance tests**

```python
def test_unrelated_dirty_file_does_not_block_node(repo):
    snapshot = capture_git_snapshot(repo)
    (repo / "unrelated.txt").write_text("dirty", encoding="utf-8")
    result = check_commit_readiness(
        snapshot,
        [repo / "skills/demo/SKILL.md"],
        {"skills/demo/SKILL.md": sha256_file(repo / "skills/demo/SKILL.md")},
    )
    assert result.stamp_worthy

@pytest.mark.parametrize("state", ["staged", "unstaged", "untracked", "symlink"])
def test_local_input_change_blocks_stamp(repo, state):
    path = mutate_local_input(repo, state)
    result = check_commit_readiness(capture_git_snapshot(repo), [path], {})
    assert not result.stamp_worthy

def test_index_skip_worktree_does_not_hide_changed_bytes(repo):
    path = repo / "skills/demo/SKILL.md"
    mark_skip_worktree(repo, path)
    path.write_text("changed", encoding="utf-8")
    assert not check_commit_readiness(capture_git_snapshot(repo), [path], {}).stamp_worthy
```

- [x] **Step 2: Run tests and verify missing-module failure**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_officina_git_provenance.py -q
```

Expected: FAIL because `officina.common.git_provenance` does not exist.

- [x] **Step 3: Implement snapshot capture**

```python
@dataclass(frozen=True)
class GitSnapshot:
    repo_root: Path
    commit: str


@dataclass(frozen=True)
class CommitReadiness:
    stamp_worthy: bool
    source: dict[str, object] | None
    reasons: tuple[str, ...]


def capture_git_snapshot(path: Path) -> GitSnapshot | None:
    root = _git(path, "rev-parse", "--show-toplevel", check=False)
    if root.returncode != 0:
        return None
    repo_root = Path(root.stdout.strip()).resolve()
    commit = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    return GitSnapshot(repo_root=repo_root, commit=commit)
```

`_git` invokes `git -C <root>` with an argument list and never invokes a
shell.

- [x] **Step 4: Compare commit, index, and working bytes**

For each sorted unique repository-relative input:

1. use `git ls-tree` to obtain the commit mode and blob object ID;
2. use `git ls-files --stage` to require stage zero and the same blob ID;
3. reject modes other than `100644` and `100755`;
4. reject any symlink path component using `lstat`;
5. read the commit blob through `git cat-file blob <oid>`;
6. compare it with working-tree bytes;
7. compare the working hash with `expected_hashes` when provided.

Return:

```python
source = {
    "vcs": "git",
    "commit": snapshot.commit,
    "input_paths": relative_paths,
}
return CommitReadiness(not reasons, source if not reasons else None, tuple(reasons))
```

- [x] **Step 5: Test a moving HEAD**

```python
def test_snapshot_head_matches_detects_new_commit(repo):
    snapshot = capture_git_snapshot(repo)
    commit_unrelated_change(repo)
    assert not snapshot_head_matches(snapshot)
```

- [x] **Step 6: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 7: Record a stable checkpoint**

Report the new API and test count. Do not commit automatically.

---

### Task 4: Add Fail-Closed Atomic And Symlink-Safe Writes

**Files:**
- Create: `src/officina/common/atomic_files.py`
- Modify: `src/officina/common/audit_records.py:149-180`
- Modify: `src/officina/common/__init__.py`
- Create: `tests/test_officina_atomic_files.py`
- Modify: `tests/test_officina_audit_records.py`

**Interfaces:**
- Produces: `AtomicWriteError`.
- Produces: `atomic_replace_bytes(path, data, *, allowed_root, mode) -> None`.
- Produces: `atomic_create_bytes(path, data, *, allowed_root, mode) -> bool`.
- Consumes: no health or graph types.

- [x] **Step 1: Write failing atomic-write tests**

```python
def test_existing_final_symlink_is_rejected(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("safe", encoding="utf-8")
    target = tmp_path / "health.json"
    target.symlink_to(victim)
    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)
    assert victim.read_text(encoding="utf-8") == "safe"

def test_parent_swap_cannot_redirect_write(tmp_path, monkeypatch):
    fixture = ParentSwapFixture(tmp_path)
    fixture.swap_after_parent_open(monkeypatch)
    atomic_replace_bytes(
        fixture.target, b"new", allowed_root=fixture.allowed_root, mode=0o600
    )
    assert not fixture.outside_target.exists()

def test_interrupted_replace_preserves_previous_complete_bytes(tmp_path, monkeypatch):
    target = tmp_path / "health.json"
    target.write_bytes(b"old")
    inject_failure_before_replace(monkeypatch)
    with pytest.raises(OSError):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)
    assert target.read_bytes() == b"old"
```

- [x] **Step 2: Run tests and verify missing-module failure**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_officina_atomic_files.py tests/test_officina_audit_records.py -q
```

Expected: FAIL because the atomic module does not exist and key creation remains in-place.

- [x] **Step 3: Implement directory-FD traversal**

```python
class AtomicWriteError(OSError):
    pass


def _open_parent(path: Path, allowed_root: Path) -> tuple[int, str]:
    root = allowed_root.absolute()
    relative = path.absolute().relative_to(root)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}")
    parts = relative.parts
    directory_fd = os.open(
        allowed_root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    for part in parts[:-1]:
        next_fd = os.open(
            part,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        os.close(directory_fd)
        directory_fd = next_fd
    return directory_fd, parts[-1]
```

Fail closed with `AtomicWriteError("secure directory-relative replacement is unavailable")`
when the host lacks the required directory-FD operations. Do not fall back to
an unsafe check-then-open sequence.

- [x] **Step 4: Implement temporary write and replacement**

```python
def atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> None:
    parent_fd, name = _open_parent(path, allowed_root)
    temp_name = f".{name}.tmp-{secrets.token_hex(8)}"
    try:
        _reject_final_symlink(parent_fd, name)
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_fd,
        )
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        _unlink_if_present(parent_fd, temp_name)
        os.close(parent_fd)
```

`atomic_create_bytes` uses the same primitive but returns `False` without
replacement when the final file already exists.

- [x] **Step 5: Move HMAC-key creation to atomic creation**

```python
def load_or_create_hmac_key(path: Path, *, allowed_root: Path) -> bytes:
    try:
        return load_hmac_key(path)
    except FileNotFoundError:
        candidate = secrets.token_bytes(32)
        atomic_create_bytes(path, candidate, allowed_root=allowed_root, mode=0o600)
        return load_hmac_key(path)
```

An interrupted creation leaves no short final key. Existing malformed keys
still fail explicitly.

- [x] **Step 6: Run focused tests**

Run the command from Step 2.

Expected: PASS, including final symlink, parent swap, interruption, and key retry cases.

- [x] **Step 7: Record a stable checkpoint**

Report host capability behavior. Do not commit automatically.

---

### Task 5: Build Stable One-Node Health Records And Refresh Decisions

**Files:**
- Modify: `src/officina/common/artifact_health.py:24-505`
- Modify: `src/officina/common/audit_records.py`
- Modify: `tests/test_officina_artifact_health.py`
- Modify: `tests/test_officina_audit_records.py`

**Interfaces:**
- Produces: `normalize_node_checks(checks) -> tuple[dict[str, object], ...]`.
- Produces: `local_input_paths_for_node(node) -> tuple[Path, ...]`.
- Produces: public `NodeHashState`, replacing private `_NodeHashes`.
- Produces: `build_node_health_record(graph, node_id, states, source, checks, key, certified_at) -> dict[str, object]`.
- Produces: `node_requires_refresh(status: NodeHealthStatus) -> bool`.
- Deprecates: all-nodes `certify_graph` as an orchestration API; retain only a test compatibility wrapper until Task 10.

- [x] **Step 1: Add failing evidence and no-op tests**

```python
def test_raw_command_output_does_not_change_certified_health_hash(graph, key):
    first = normalize_node_checks([
        {"id": "tests", "version": 1, "passed": True, "findings": [],
         "stdout": "69 passed in 20.05s"}
    ])
    second = normalize_node_checks([
        {"id": "tests", "version": 1, "passed": True, "findings": [],
         "stdout": "69 passed in 20.06s"}
    ])
    assert first == second

def test_source_commit_changes_record_hash_not_certified_health_hash(graph, key):
    first = build_record_for_source(graph, key, "a" * 40)
    second = build_record_for_source(graph, key, "b" * 40)
    assert first["hashes"]["certified_health_hash"] == second["hashes"]["certified_health_hash"]
    assert first["record_hash"] != second["record_hash"]
```

Add admission tests requiring exact source paths, stable checks, subject,
certifier, and dependencies.

- [x] **Step 2: Run focused tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_officina_artifact_health.py tests/test_officina_audit_records.py -q
```

Expected: FAIL because raw evidence is accepted, source metadata is absent, and records are constructed graph-wide.

- [x] **Step 3: Normalize checks**

```python
_STABLE_CHECK_FIELDS = ("id", "version", "passed", "findings")


def normalize_node_checks(
    checks: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    normalized = []
    for check in checks:
        item = {field: deepcopy(check[field]) for field in _STABLE_CHECK_FIELDS}
        if item["passed"] is not True:
            raise ArtifactHealthError("cannot certify failed node check")
        normalized.append(item)
    return tuple(sorted(normalized, key=lambda item: (str(item["id"]), int(item["version"]))))
```

Raw evidence stays in the audit-run result and never reaches this function.

- [x] **Step 4: Define one source of local input paths**

```python
def local_input_paths_for_node(node: BlueprintNode) -> tuple[Path, ...]:
    paths = {node.blueprint_path}
    if node.binding_path is not None:
        paths.add(node.binding_path)
    for declared in node.declaration.get("local_hash_inputs", []):
        paths.add((node.skill_root / declared).resolve())
    return tuple(sorted(paths))
```

The hasher and Git-readiness code both consume this function so their scopes
cannot drift.

- [x] **Step 5: Construct one record**

```python
@dataclass(frozen=True)
class NodeHashState:
    blueprint_file_hash: str
    blueprint_contract_hash: str
    bound_file_hash: str | None
    local_hash: str
    downstream_artifact_hash: str
    artifact_graph_hash: str
    downstream_health_hash: str
    certified_health_hash: str
    dependencies: tuple[dict[str, Any], ...]


def build_node_health_record(
    graph: SkillBlueprintGraph,
    node_id: str,
    states: Mapping[str, NodeHashState],
    *,
    source: Mapping[str, object],
    checks: Sequence[Mapping[str, object]],
    key: bytes,
    certified_at: str,
) -> dict[str, object]:
    state = states[node_id]
    record = _node_record_payload(
        graph.nodes[node_id],
        state,
        source=source,
        checks=normalize_node_checks(checks),
        certified_at=certified_at,
    )
    return attach_record_authentication(record, key)
```

Source metadata is included in `record_hash` but excluded from
`certified_health_hash`. Checks are per node; do not assign one consumer-run
check list to every graph node.

- [x] **Step 6: Define refresh-required semantics**

```python
_REFRESH_CONCERNS = {
    "missing-health-record",
    "authentication-failed",
    "invalid-health-record",
    "artifact-stale",
    "dependency-stale",
    "schema-stale",
    "policy-stale",
    "checks-stale",
    "blueprint-file-changed",
}


def node_requires_refresh(status: NodeHealthStatus) -> bool:
    return any(concern in _REFRESH_CONCERNS for concern in status.concerns)
```

A parent is refresh-required only if its own admitted record is invalid or its
expected dependency projection differs.

- [x] **Step 7: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 8: Record a stable checkpoint**

Report compatibility behavior of `certify_graph`. Do not commit automatically.

---

### Task 6: Implement Incremental Commit-Backed Audit Orchestration

**Files:**
- Modify: `skills/skill-audit/_rtx/_audit_certifier.py:86-856`
- Modify: `skills/skill-audit/tests/test_audit_certifier.py`
- Modify: `skills/skill-audit/SKILL.md`
- Modify: `skills/skill-audit/.SKILL.md.blueprint.yaml`
- Modify: `skills/skill-audit/_rtx/._audit_certifier.py.blueprint.yaml`

**Interfaces:**
- Produces: `NodeAuditOutcome` and expanded `AuditOutcome`.
- Produces: `AuditContext`.
- Produces: `audit_typed_graph(context: AuditContext) -> AuditOutcome`.
- Produces: `check_graph_health_from_disk(context: AuditContext) -> GraphHealthReport`.
- Produces: `audit_and_maybe_stamp_node(context, node_id, outcomes) -> NodeAuditOutcome`.
- Produces: `finish_root_and_pool(context, outcomes) -> AuditOutcome`.
- Produces: exact statuses `reused`, `written`, `not-written`, and `failed`.
- Consumes: Tasks 2-5 graph, provenance, atomic-write, and record APIs.

- [x] **Step 1: Add failing orchestration tests**

```python
def test_dirty_leaf_finishes_semantics_without_stamp(typed_target, dispatcher):
    dirty(typed_target.path / "references/policy.md")
    result = certify(dispatcher, targets=[str(typed_target.path)], skip_mechanical=True)
    leaf = result.outcomes[0].nodes["demo.source.policy"]
    assert leaf.semantic_status == "passed"
    assert not leaf.stamp_worthy
    assert leaf.stamp_status == "not-written"
    assert not health_path_for_node(leaf.node).exists()

def test_healthy_child_is_reused_without_git_check(typed_target, dispatcher, monkeypatch):
    certify_clean_target(typed_target, dispatcher)
    spy = provenance_spy(monkeypatch)
    certify(dispatcher, targets=[str(typed_target.path)], skip_mechanical=True)
    assert typed_target.child_id not in spy.checked_node_ids

def test_second_target_failure_keeps_first_and_reports_partial_success(two_targets):
    payload = run_with_second_target_failure(two_targets)
    assert payload["certified"][0]["status"] == "audit-current"
    assert payload["failed"][0]["skill"] == two_targets.second.name
```

- [x] **Step 2: Run audit tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src skills/skill-audit/tests/test_audit_certifier.py -q
```

Expected: FAIL because current audit rewrites all records, rejects semantic findings before structured output, and rolls back graph-wide.

- [x] **Step 3: Introduce explicit node outcomes**

```python
@dataclass(frozen=True)
class NodeAuditOutcome:
    node_id: str
    semantic_status: str
    health_status: str
    stamp_worthy: bool
    stamp_status: str
    reasons: tuple[str, ...]
    record_path: Path | None


@dataclass(frozen=True)
class AuditOutcome:
    skill: str
    source: str
    skill_root: Path
    semantic_status: str
    stamp_worthy: bool
    stamp_status: str
    nodes: tuple[NodeAuditOutcome, ...]
    pool_status: str


@dataclass(frozen=True)
class AuditContext:
    graph: SkillBlueprintGraph
    repo_root: Path
    schema_root: Path
    policy_hash: str
    schema_hash: str
    key: bytes
    snapshot: GitSnapshot | None
    node_checks: Mapping[str, tuple[dict[str, object], ...]]
    raw_evidence: tuple[CommandResult, ...]
```

`as_payload()` emits these exact fields. Raw command output belongs in the
top-level audit-run evidence, not node checks.

- [x] **Step 4: Replace graph-wide writing with deterministic postorder**

```python
def audit_typed_graph(context: AuditContext) -> AuditOutcome:
    report = check_graph_health_from_disk(context)
    outcomes: dict[str, NodeAuditOutcome] = {}
    for node_id in postorder_node_ids(context.graph):
        status = report.nodes[node_id]
        if status.healthy and not node_requires_refresh(status):
            outcomes[node_id] = NodeAuditOutcome(
                node_id=node_id,
                semantic_status="passed",
                health_status="healthy",
                stamp_worthy=True,
                stamp_status="reused",
                reasons=(),
                record_path=health_path_for_node(context.graph.nodes[node_id]),
            )
            continue
        outcome = audit_and_maybe_stamp_node(context, node_id, outcomes)
        outcomes[node_id] = outcome
    return finish_root_and_pool(context, outcomes)
```

`audit_and_maybe_stamp_node`:

1. completes semantic checks;
2. requires current child stamps;
3. checks `snapshot_head_matches`;
4. calls `check_commit_readiness` only for the current node;
5. returns `not-written` with reasons if unstampable;
6. builds one record;
7. calls `atomic_replace_bytes`.

Do not roll back valid child records when a parent fails.

- [x] **Step 5: Make policy readiness a single invocation gate**

Capture target `HEAD` before traversal. Check target schema, policy manifest,
target certifier, and shared policy implementation paths once. If absent or
dirty, retain semantic results and mark every refresh-required node
`not-written` with `policy-not-commit-backed`.

- [x] **Step 6: Make legacy records commit-backed and atomic**

Legacy `.last_audit.json` uses the same provenance and atomic replacement.
Reject legacy symlinks. A dirty legacy skill returns semantic results and no
record.

- [x] **Step 7: Verify exact post-write identity**

```python
def verify_post_write(dispatcher: Dispatcher, target: TargetHash) -> None:
    payload = dispatch_exact_status(dispatcher, target.skill_root)
    reports = payload.get("skills", [])
    if len(reports) != 1 or reports[0].get("skill") != target.skill:
        raise AuditError("drift-status did not return the exact requested skill")
    if reports[0].get("derived_status") != "audit-current":
        raise AuditError(f"post-write drift verification failed for {target.skill}")
```

Also verify every newly written node ID from the audit outcome.

- [x] **Step 8: Update the public interface documentation**

Document:

- exact target closure;
- semantic audit without a stamp;
- commit-only stamping;
- independent multi-target results;
- ignored local health state.

Regenerate contract blocks through the approved sync interface; do not hand-edit generated blocks.

- [x] **Step 9: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 10: Record a stable checkpoint**

Report outcomes and modified files. Do not commit automatically.

---

### Task 7: Make Drift And Hashing Exact-Target And Target-Relative

**Files:**
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py:178-1030`
- Modify: `skills/skill-drift/_rtx/_drift_hashes.py:19-630`
- Modify: `skills/skill-drift/tests/test_drift_check.py`
- Modify: `skills/skill-drift/references/policy-hash-roots.json`
- Modify: `skills/skill-drift/SKILL.md`
- Modify: `skills/skill-drift/.SKILL.md.blueprint.yaml`
- Modify: `skills/skill-drift/_rtx/._check_drift_state.py.compute-hashes.blueprint.yaml`
- Modify: `skills/skill-drift/_rtx/._check_drift_state.py.drift-status.blueprint.yaml`

**Interfaces:**
- Produces: `RequestedScope(source: SkillSource, skill_names: tuple[str, ...])`.
- Produces: `requested_scopes(args: argparse.Namespace) -> tuple[RequestedScope, ...]`.
- Produces: graph-native typed hash payloads.
- Consumes: target-relative schema/policy roots and Task 5 health admission.

- [x] **Step 1: Add failing target-isolation tests**

```python
def test_skill_root_selects_exactly_one_skill(copied_installation):
    payload = run_status("--skill-root", copied_installation / "skills/demo", "--json")
    assert [item["skill"] for item in payload["skills"]] == ["demo"]

def test_unrelated_malformed_skill_does_not_block_exact_target(copied_installation):
    add_malformed_skill(copied_installation, "broken")
    result = run_status("--skill-root", copied_installation / "skills/demo", "--json")
    assert result.returncode == 0

def test_target_policy_hash_does_not_use_running_repo(copied_installation):
    change_target_policy(copied_installation)
    target = run_target_hash(copied_installation, runtime="current")
    assert target.policy_hash == expected_target_policy_hash(copied_installation)
```

- [x] **Step 2: Run drift tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src skills/skill-drift/tests/test_drift_check.py -q
```

Expected: FAIL for skill-root fanout, source policy leakage, and empty typed interface hashes.

- [x] **Step 3: Represent exact requested scopes**

```python
@dataclass(frozen=True)
class RequestedScope:
    source: SkillSource
    skill_names: tuple[str, ...]


def requested_scopes(args: argparse.Namespace) -> tuple[RequestedScope, ...]:
    if args.skill_root is not None:
        root = args.skill_root.resolve()
        source = source_for_skill_root(root, source="override")
        return (RequestedScope(source, (root.name,)),)
    sources = requested_skill_sources(args)
    scopes = []
    for source in sources:
        names = (
            tuple(args.skills)
            if args.skills
            else tuple(blueprint_skill_names(source.skills_root))
        )
        scopes.append(RequestedScope(source, names))
    return tuple(scopes)
```

`run_status` and `run_compute_hashes` consume `requested_scopes`; they never
translate an exact root into an empty skill list.

- [x] **Step 4: Parameterize all policy and schema paths**

Remove module-level source-repository policy constants from target hashing.
Every function receives `source.package_root` or explicit `schema_root`.
Policy manifest entries resolve under the selected target package root.

- [x] **Step 5: Remove target module execution**

Delete the subprocess/import dependency explorer for typed graphs. Typed hash
output comes from:

```python
graph = load_reachable_repository_skill_graph(source.package_root, skill_name)
report = check_graph_health(graph, records, policy_hash, schema_hash, key, schema_root)
return SkillHashReport.from_graph_report(graph, report)
```

Retain legacy dependency exploration only for legacy blueprints in the running
installation. Exact copied-target certification must not import target files.

- [x] **Step 6: Emit graph-native typed hashes**

Typed `compute-hashes` returns each canonical interface and behavior source,
its local hash, artifact graph hash, and expected certified-health hash.
Do not read the legacy nested `interfaces` mapping for schema-version-2 roots.

- [x] **Step 7: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 8: Record a stable checkpoint**

Report exact-target fixture paths and hashes. Do not commit automatically.

---

### Task 8: Align Source Validators And Dispatcher Runtime Enforcement

**Files:**
- Modify: `skills/skill-maker/validators/blueprints.py:65-770`
- Modify: `skills/skill-maker/validators/blueprint_relationships.py`
- Modify: `skills/skill-maker/validators/dependencies.py`
- Modify: `skills/skill-maker/validators/skill_body_execution.py`
- Modify: `src/officina/dispatcher/core.py:67-420`
- Modify: `tests/validate_blueprints.py`
- Modify: `tests/validate_blueprint_relationships.py`
- Modify: `tests/validate_dependencies.py`
- Modify: `tests/validate_skill_body_execution.py`
- Modify: `tests/test_officina_dispatcher.py`

**Interfaces:**
- Produces: `load_validated_skill_blueprint_graph(skill_root, schema_root) -> SkillBlueprintGraph`.
- Consumes: Task 1 schema rules and Task 2 visibility/identity helpers.

- [x] **Step 1: Add failing differential tests**

```python
def test_dispatcher_rejects_schema_invalid_typed_sidecar(typed_skill):
    remove_required_description(typed_skill.machine_sidecar)
    with pytest.raises(InvocationError, match="description"):
        resolve_dispatch(
            caller_skill="caller",
            target="demo.machine.run",
            repo_root=typed_skill.repo_root,
        )

def test_source_validator_rejects_untracked_command_but_runtime_without_git_accepts_copy(
    typed_command_skill,
):
    assert has_error(validate(typed_command_skill.repo_root), "not tracked")
    installed = copy_without_git(typed_command_skill)
    assert resolve_dispatch(
        caller_skill="caller", target="demo.machine.command", repo_root=installed
    )

def test_command_symlink_inside_cx_is_rejected(typed_command_skill):
    replace_command_with_in_tree_symlink(typed_command_skill)
    assert validator_and_dispatcher_both_reject(typed_command_skill)
```

- [x] **Step 2: Run focused tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/validate_blueprints.py tests/validate_blueprint_relationships.py tests/validate_dependencies.py tests/validate_skill_body_execution.py tests/test_officina_dispatcher.py -q
```

Expected: FAIL because dispatcher does not schema-validate typed sidecars and tracked symlinks can hide untracked targets.

- [x] **Step 3: Add validated graph loading**

```python
def load_validated_skill_blueprint_graph(
    skill_root: Path,
    schema_root: Path,
) -> SkillBlueprintGraph:
    graph = load_skill_blueprint_graph(skill_root)
    validator = schema_validator(load_schema(schema_root / "schema.json"))
    for node in graph.nodes.values():
        validator.validate(node.declaration)
    validate_graph_contract(graph, schema_root)
    return graph
```

Dispatcher uses this API for typed skills. Convert validation exceptions to
`InvocationError` with the sidecar path and JSON path.

- [x] **Step 4: Separate source and runtime checks**

Source validator:

- requires every authored blueprint and binding to be Git-tracked;
- compares the tracked path itself, not its resolved symlink target;
- rejects any symlink binding;
- requires `_cx` executability.

Dispatcher:

- does not call Git;
- rejects symlinks and path-component symlinks;
- validates schema, graph, access, containment, and executability.

- [x] **Step 5: Align the relationship matrix and instruction bodies**

Allow LLM and behavior-source bodies to name declared canonical machine or LLM
interface IDs, including cross-skill IDs. Continue to reject bare skill
invocation and direct `_rtx`/`_cx` paths. Generate validator decisions from
Task 1's relationship matrix rather than duplicating a conflicting hard-coded
matrix.

- [x] **Step 6: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 7: Record a stable checkpoint**

Report source/runtime enforcement differences. Do not commit automatically.

---

### Task 9: Make Pooled Review Exact And Non-Authoritative

**Files:**
- Modify: `src/officina/common/pooled_blueprint.py:24-210`
- Modify: `src/officina/common/artifact_health.py:60-105`
- Modify: `tests/test_officina_pooled_blueprint.py`
- Modify: `tests/test_officina_artifact_health.py`

**Interfaces:**
- Changes: `check_pooled_review(path: Path, health_path: Path, root_report: GraphHealthReport, key: bytes, *, graph: SkillBlueprintGraph, records: Mapping[str, dict[str, object]], schema_root: Path) -> PooledReviewHealth`.
- Produces: canonical-render comparison and pooled-schema validation.
- Consumes: Task 4 atomic writes through the audit orchestrator.

- [x] **Step 1: Add failing pool-boundary tests**

```python
def test_arbitrary_authenticated_yaml_is_not_a_healthy_pool(pool_fixture):
    pool_fixture.path.write_text("not: a pooled review\n", encoding="utf-8")
    pool_fixture.reauthenticate_current_bytes()
    result = pool_fixture.check()
    assert not result.healthy
    assert "invalid-pooled-review" in result.concerns

def test_noncanonical_schema_valid_pool_is_unhealthy(pool_fixture):
    mutate_bounded_summary_without_breaking_schema(pool_fixture.path)
    pool_fixture.reauthenticate_current_bytes()
    assert "noncanonical-pooled-review" in pool_fixture.check().concerns

def test_pooled_schema_change_does_not_change_root_schema_hash(schema_root):
    before = blueprint_schema_hash(schema_root)
    mutate(schema_root / "pooled-review.schema.json")
    assert blueprint_schema_hash(schema_root) == before
```

- [x] **Step 2: Run focused tests and verify failures**

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_officina_pooled_blueprint.py tests/test_officina_artifact_health.py -q
```

Expected: FAIL because current pool checking authenticates bytes only and pooled schema participates in canonical schema hash.

- [x] **Step 3: Validate content and canonical rendering**

```python
def check_pooled_review(
    path: Path,
    health_path: Path,
    root_report: GraphHealthReport,
    key: bytes,
    *,
    graph: SkillBlueprintGraph,
    records: Mapping[str, dict[str, object]],
    schema_root: Path,
) -> PooledReviewHealth:
    admitted = _admit_pooled_health(health_path, root_report, key, schema_root)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema_validator(load_schema(schema_root / "pooled-review.schema.json")).validate(document)
    expected = render_pooled_review(graph, records)
    if path.read_text(encoding="utf-8") != expected:
        return PooledReviewHealth(False, ("noncanonical-pooled-review",))
    return _check_pooled_hashes(admitted, path, root_report)
```

Catch malformed authentication, canonicalization, schema, and IO exceptions
and return concerns rather than leaking exceptions from the common API.

- [x] **Step 4: Remove pool schema from root schema hash**

Split schema hash inputs:

```python
CANONICAL_GRAPH_SCHEMA_INPUTS = (
    "schema.json",
    "schema-meta.json",
    "common.schema.json",
    "skill.schema.json",
    "llm-interface.schema.json",
    "machine-interface.schema.json",
    "behavior-source.schema.json",
    "health.schema.json",
    "schema.annotated-draft.json",
    "template.yaml",
)

POOLED_REVIEW_SCHEMA_INPUTS = ("pooled-review.schema.json",)
```

Only the first tuple contributes to canonical node schema hashes.

- [x] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [x] **Step 6: Record a stable checkpoint**

Report pool-only failure behavior. Do not commit automatically.

---

### Task 10: Integrate, Document, And Convert Audit Reproducers Into Permanent Gates

**Files:**
- Modify: `.gitignore`
- Modify: `docs/audit_and_drift.md`
- Modify: `references/blueprint/README.md`
- Modify: `references/blueprint/guide.md`
- Modify: `references/skill-guidelines.md`
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `skills/skill-maker/tests/test_blueprint_tools.py`
- Modify: `skills/skill-audit/_rtx/_audit_certifier.py`
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py`
- Modify: `scripts/run-python-tests.py`
- Modify: `tests/test_run_python_tests.py`
- Modify: `validators/skill_runtime_files.py`
- Modify: `tests/validate_skill_runtime_files.py`
- Modify: `tests/test_blueprint_schema_metadata.py`
- Modify: `tests/test_officina_artifact_health.py`
- Modify: `tests/test_officina_blueprint_graph.py`
- Modify: `tests/test_officina_pooled_blueprint.py`
- Modify: `skills/skill-audit/tests/test_audit_certifier.py`
- Modify: `skills/skill-drift/tests/test_drift_check.py`
- Modify: `docs/superpowers/plans/2026-07-13-node-local-commit-backed-health-remediation.md`

**Interfaces:**
- Consumes: all earlier task APIs.
- Produces: one synchronized schema-first reference implementation and permanent regression suite.
- Migrates: audit and drift pooled-review callers to pass the exact graph and
  admitted records required by `check_pooled_review`.

- [x] **Step 1: Port the independent reproducers into named tests**

Convert these temporary findings into repository fixtures without importing
temporary scripts:

- legacy health symlink overwrite;
- parent-directory swap;
- interrupted record and key writes;
- shared-source last-consumer-wins;
- duplicate shared edges;
- canonical-ID owner mismatch;
- unchanged-node rewrite;
- volatile check-output churn;
- exact-target fanout and source-policy leakage;
- arbitrary healthy pool;
- dispatcher acceptance of schema-invalid typed sidecars.

Each test name states the invariant, for example:

```python
def test_consumer_audit_does_not_replace_reusable_shared_node_evidence(
    shared_source_fixture,
):
    first = shared_source_fixture.certify("first-skill", stdout="first run")
    second = shared_source_fixture.certify("second-skill", stdout="second run")
    assert first.shared_certified_health_hash == second.shared_certified_health_hash
    assert shared_source_fixture.status("first-skill").healthy
    assert shared_source_fixture.status("second-skill").healthy

def test_exact_skill_root_ignores_unrelated_malformed_skill(copied_installation):
    copied_installation.add_malformed_skill("broken")
    payload = copied_installation.status_exact("demo")
    assert [item["skill"] for item in payload["skills"]] == ["demo"]

def test_legacy_record_symlink_cannot_modify_target(legacy_target):
    victim = legacy_target.skill_root / "victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    legacy_target.audit_record.symlink_to(victim)
    result = legacy_target.certify()
    assert result.stamp_status == "failed"
    assert victim.read_text(encoding="utf-8") == "unchanged"
```

- [x] **Step 2: Update ignored local artifacts**

Ensure `.gitignore` covers:

```gitignore
skills/**/.last_audit.json
skills/**/.*.health.json
references/**/.*.health.json
skills/**/.pooled-blueprint-review.yaml
skills/**/.pooled-blueprint-review.health.json
skills/skill-audit/.health-authentication-key
```

Use patterns that do not ignore authored blueprint sidecars.

- [x] **Step 3: Update synchronization**

The syncer consumes schema-family sidecar and generated-block rules. It:

- validates root and sidecar schemas;
- derives deterministic sidecar names;
- generates contract/interface blocks;
- ignores health and pooled artifacts as inputs;
- preserves authored comments;
- rejects schema/validator disagreement.

Add sync tests for repository-root behavior sources and multiple interfaces
bound to one file.

- [x] **Step 4: Update normative documentation**

Replace whole-graph snapshot language with:

- node-local commit-backed stamps;
- semantic audit without stamping;
- exact-target closure;
- behavior-source location visibility;
- child-stamp reuse;
- ignored local health state;
- stable checks;
- atomic writes;
- exact pooled rendering;
- deferred portable ledger.

Remove resolved gap statements that would contradict the implementation.

- [x] **Step 5: Run focused integration suites**

Before running the suites, make the repository runner supply its own source
path instead of requiring a shell environment prefix:

```python
def _pytest_args(*, verbose: bool) -> list[str]:
    return ["-o", "pythonpath=src", "-v" if verbose else "-q"]


pytest_args = _pytest_args(verbose=args.verbose)
```

Add this runner test:

```python
def test_runner_supplies_repo_src_pythonpath():
    assert runner._pytest_args(verbose=False) == [
        "-o",
        "pythonpath=src",
        "-q",
    ]
```

Run:

```bash
python3 -m pytest -o pythonpath=src tests/test_blueprint_schema_metadata.py tests/test_typed_blueprint_schemas.py tests/test_officina_blueprint_template.py tests/test_officina_blueprint_graph.py tests/test_officina_git_provenance.py tests/test_officina_atomic_files.py tests/test_officina_audit_records.py tests/test_officina_artifact_health.py tests/test_officina_pooled_blueprint.py tests/test_officina_dispatcher.py tests/validate_blueprints.py tests/validate_blueprint_relationships.py tests/validate_dependencies.py tests/validate_skill_body_execution.py tests/validate_skill_runtime_files.py skills/skill-audit/tests/test_audit_certifier.py skills/skill-drift/tests/test_drift_check.py -q
```

Expected: PASS.

- [x] **Step 6: Run blueprint synchronization**

Run:

```bash
dispatcher --caller-skill skill-maker skill-maker.machine.sync-blueprints --check
```

Expected: exit 0 with no output.

- [x] **Step 7: Run repository validators**

The acceptance gate uses an isolated intended-source repository so source
validators see the authored Task 1-10 files without mutating the real index:

1. Create a disposable copy under `/tmp` that includes independent Git
   metadata and the current working-tree files.
2. In that copy only, stage the intended Task 1-10 source set.
3. Run `python3 validators/runner.py` with the disposable repository as the
   working directory.
4. Confirm the real repository index bytes and cached diff are unchanged.

Expected acceptance result: the isolated validator command exits 0 with no
output and the real index is unchanged.

Also run the literal live unstaged `python3 validators/runner.py` as a
diagnostic. At this checkpoint it is expected to fail only because the five
authored blueprint sidecars are untracked and therefore absent from the
validator's index-only source mirror. This diagnostic is not claimed as a pass,
and tracked-source admission remains mandatory.

- [x] **Step 8: Run the complete local precommit suite**

Run:

```bash
python3 scripts/run-python-tests.py --suite precommit
```

Expected: all collected tests pass; record the exact passed/skipped count.

- [x] **Step 9: Run final repository checks**

Run:

```bash
git diff --check
git diff --cached --stat
git status --short
```

Expected:

- no whitespace errors;
- no temporary validation staging remains;
- unrelated concurrent work is still present and untouched;
- no generated health, pool, or key file is tracked or unignored.

- [x] **Step 10: Self-review the implementation against the specification**

Check each acceptance criterion in
`docs/superpowers/specs/2026-07-13-node-local-commit-backed-health-design.md`
against a named test and implementation path. Record any external live-suite
blocker separately from local repository failures.

- [x] **Step 11: Record the final stable checkpoint**

Summarize changed files, test evidence, remaining deferred ledger/signature
work, and unrelated worktree state. Do not stage or commit unless the user
explicitly requests it.

## Plan Self-Review

- **Spec coverage:** Every specification section maps to at least one task:
  schema authority (Task 1), graph visibility and identity (Task 2), commit
  provenance (Task 3), safe writes (Task 4), stable node health (Task 5),
  recursive audit semantics (Task 6), target isolation (Task 7),
  validator/dispatcher agreement (Task 8), pooled review (Task 9), and
  migration/full verification (Task 10).
- **Placeholder scan:** The plan contains no TBD, TODO, generic "handle edge
  cases," or unnamed testing steps.
- **Type consistency:** Task 3 provenance types feed Task 6; Task 4 write APIs
  feed audit records and Task 6; Task 5 record APIs feed Tasks 6, 7, and 9;
  Task 2 graph helpers feed Tasks 5, 7, and 8.
- **Scope:** Portable ledgers, external signatures, and execution of copied
  target code remain explicitly outside phase one.

## Completion Record

As of 2026-07-13, Tasks 1-10 are implemented in the working tree and the R5
whole-change review reported zero Critical, Important, or Minor findings.
Fresh controller verification recorded:

- 608 focused tests passed;
- blueprint synchronization exited 0 with no output;
- validators exited 0 with no output in a disposable intended-source checkout;
- `git diff --check` exited 0;
- the real repository index remained empty.

The literal unstaged validator run reports five missing-subordinate-blueprint
findings because the new `skill-audit` and `skill-drift` sidecars are authored
but untracked. This is the expected index-only source-admission behavior. Stage
the complete intended sidecar set before treating the live validator result as
an acceptance gate; do not weaken tracked-source validation.

The complete precommit suite recorded 4 failures, 1171 passes, and 3 skips.
The four failures are an independently reproduced test-isolation defect:
`skills/email-triage/tests/test_fetch_filtered_envelopes.py` leaves a foreign
`_rtx` package in `sys.modules`, after which
`skills/list-manager/tests/test_category_cache.py` cannot import
`_rtx._category_cache`. The list-manager file passes in isolation, and neither
skill was modified by this remediation.

Windows dispatcher behavior is covered through simulated platform and
descriptor-capability tests; no fresh real-Windows execution was performed.
The feature remains unstaged and uncommitted on `master` at `d551198`. Health
records, pooled reviews, and the local authentication key remain ignored, and
the implementation adds no runtime dependency.
