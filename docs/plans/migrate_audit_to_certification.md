# Audit-to-Certification Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Track execution with checkbox steps and stop at every review gate.

**Goal:** Replace the implemented audit/health system with the certificate model in `docs/certification_and_drift.md`, migrate supported callers and installed artifacts without an unreadable intermediate state, and delete `docs/audit_and_drift.md` after every live dependency has moved.

**Architecture:** Build a new certification core behind new certificate interfaces, but adapt the existing mechanical infrastructure for graph discovery, hashing, Git provenance, safe writes, validation, and reporting. Do not implement the design as either a ground-up rewrite or a gradual mutation of the old health semantics. Retain temporary read-only compatibility for legacy health records and old caller aliases; new code writes certificates only and never dual-writes or converts a health record into a certificate. Cutover proceeds through inventory, dual-read compatibility, caller migration, recertification, removal of legacy surfaces, and final documentation deletion.

**Primary surfaces:** Python, JSON Schema, YAML blueprints, dispatcher interfaces, Git provenance, public-key signatures, repository validators, pytest, and precommit validation.

## Global constraints

- `docs/audit_and_drift.md` describes the current system until cutover; `docs/certification_and_drift.md` is the target design.
- Preserve exact-target isolation: unrelated malformed or suspect nodes cannot block a targeted operation unless they are in its required closure.
- Preserve node-local commit backing, unchanged-HEAD checks, atomic no-follow writes, symlink protections, and post-write verification.
- Every blueprint field contributes to `node_hash(x)`, including `direct_io`, descriptions, permissions, and ownership declarations.
- `node_hash(skill-certifier)` is the complete certification basis in this version; certifier dependencies are deliberately excluded.
- Routine tests and validators remain separate health signals and ordinary validation workflows; they are not certificate-status inputs.
- Existing health records are never accepted as certificates. Migration requires new certification.
- No task may delete or disable a compatibility surface before its inventory and migration tests are complete.
- Do not delete `docs/audit_and_drift.md` until the final documentation-removal task.

## Implementation boundary: reuse mechanics, replace semantics

The implementation must first inventory and test the existing mechanical components, then reuse or adapt them when their contracts remain valid under the certificate model. This includes:

- blueprint and node discovery;
- dependency-graph construction and traversal;
- canonical hashing primitives;
- Git cleanliness, commit capture, and unchanged-HEAD checks;
- atomic no-follow writes and symlink protections;
- pooled review, validation, and status-rendering infrastructure where it is independent of health semantics.

The implementation must create a separate certification core for behavior whose meaning changes. Do not preserve old internal abstractions merely to minimize the diff. The new core owns:

- certificate schemas and canonical signed payloads;
- `certified` and `suspect` status evaluation;
- direct dependency `node_hash(d)` recording and comparison;
- the two-pass, dependency-first certification algorithm;
- certifier-basis validation and signing-key authority;
- certificate-specific interfaces and exit behavior.

Legacy health parsing exists only in the compatibility reader. It must not be imported into the certification core, used as certificate evidence, or extended to implement certificate semantics. Conversely, shared mechanical code must not be copied into new certifier modules unless Task 1 demonstrates that adapting it would violate the new contract. Tests at the boundary must prove that the certification core can replace the compatibility reader without replacing the reused mechanical infrastructure.

## Transition contract

The migration uses these phases:

1. **Inventory:** old readers and writers remain authoritative; no new records are written.
2. **Dual-read, single-write:** `skill-drift` can report both legacy health and new certificate state. Only `skill-certifier` writes new certificates; `skill-audit` remains a forwarding compatibility alias and never writes legacy records.
3. **Caller migration:** supported callers move to the new versioned interfaces. Old aliases remain covered by compatibility tests.
4. **Recertification:** supported skills receive new certificates. A legacy-current record may be reported as legacy evidence but never as `certified` under the new model.
5. **Cutover:** legacy writes are already disabled; old aliases and legacy reads are removed only after inventory proves that no supported caller or installed copy needs them.
6. **Documentation removal:** all policy and behavior-source references move first; `docs/audit_and_drift.md` is deleted only after a successful zero-reference and validation pass.

Rollback is supported through phase 4 by reverting the migration commits and retaining legacy record files untouched. After phase 5 removes legacy readers and aliases, rollback requires reverting to the recorded pre-cutover commit. New certificate files must be ignored by old readers, and legacy health files must be ignored by new certificate validation.

## Artifact and behavior disposition

| Current surface | Target disposition | Migration proof |
| --- | --- | --- |
| `skill-audit` and `skill-audit.machine.certify@1` | Replace with `skill-certifier.machine.certify@1`; retain a temporary forwarding alias with no legacy writes | Old/new caller contract tests |
| `skill-drift.machine.drift-status@1` | Add version 2 certificate fields and certified/suspect concerns; retain version 1 during compatibility | Golden JSON, Markdown, target, and exit-code tests |
| `skill-drift.machine.compute-hashes@1` | Add version 2 local node-hash output; retain version 1 until all certifier callers migrate | Golden hash payload tests |
| Health records and `.last_audit.json` | Read-only legacy evidence during compatibility; never treated as certificates; retire after recertification | Mixed-installation and precedence tests |
| `health.schema.json` | Replace for new writes with a certificate schema; retain legacy validation only during dual-read | Schema fixtures for both formats |
| Pooled review | Retain as a non-authoritative human review assembled from blueprints and certificates | Canonical rendering tests |
| Pooled-review health | Remove after certificate-backed pooled review is verified | Absence and migration tests |
| `--with-test-validate` | Preserve as a separate health signal that never changes certified/suspect status | Combined-status tests |
| `direct_io` exclusion | Remove: all blueprint fields affect `node_hash` | Hash-change regression tests |
| Local HMAC key | Replace with public/private signing keys | Forgery-denial and key-permission tests |
| Atomic/no-follow writes and Git provenance | Preserve | Race, symlink, dirty-file, and unchanged-HEAD tests |
| `docs/audit_and_drift.md` | Delete after all references and policy inputs migrate | Two zero-reference scans and full validation |

## Task 1: Freeze inventories and public contracts

**Files:**
- Create: `docs/plans/audit-to-certification-inventory.md`
- Modify: `docs/plans/migrate_audit_to_certification.md`
- Inspect: `skills/skill-audit/`, `skills/skill-drift/`, `references/blueprint/`, `references/skill-standards/`, `validators/`, `tests/`, `docs/`

**Deliverable:** A checked-in inventory of every old interface ID, caller, behavior-source or policy reference, record/schema artifact, generated manifest, installed-source class, and test family. Classify each implementation component as reusable mechanics, adaptable mechanics, legacy compatibility only, or replaced semantics, with evidence from its existing contract and tests. Record the pre-cutover commit used for rollback.

**Verification:** Repository-wide searches for `skill-audit`, `skill-drift.machine`, `audit_and_drift.md`, `health.schema.json`, `.last_audit.json`, `pooled-review`, and `with-test-validate` must be captured in the inventory with an owner and target disposition.

## Task 2: Specify versioned interface and transition schemas

**Files:**
- Create: `references/blueprint/certificate.schema.json`
- Create: versioned golden payload fixtures under `tests/fixtures/certification/`
- Modify: `references/blueprint/schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/README.md`
- Modify: `references/blueprint/template.yaml`
- Test: `tests/test_typed_blueprint_schemas.py`
- Test: new `tests/test_certificate_schema.py`

**Deliverable:** Exact schemas for certificates, mixed legacy/new status output, status concerns, versioned interface arguments, exit behavior, JSON fields, Markdown rendering, no-target behavior, exact-target behavior, and report side effects. The certificate payload includes node identity, `node_hash`, the exact direct-dependency ID-to-hash mapping, `node_hash(skill-certifier)`, `source_commit`, `certified_at`, signer/key identity, and signature.

**Verification:** Invalid signatures, extra or missing dependency keys, wrong node/certifier identity, unsupported schema versions, missing commits, and malformed mixed-status payloads fail closed in schema fixtures.

## Task 3: Implement signing-key lifecycle and authority boundaries

**Files:**
- Create focused signing/key modules under the shared `src/officina/` package.
- Modify installer and permission declarations identified by Task 1.
- Test: new focused key-lifecycle and authority-boundary tests under `tests/`.

**Deliverable:** Key generation, installation, public-key discovery, private-key permissions, rotation, loss/recovery, key-ID replacement, and certifier-upgrade behavior. Only the `skill-certifier` signing path can read the private key or write certificates/blueprints; `skill-drift` receives the public key and read-only paths.

**Verification:** Tests prove that drift cannot read the private key, sign payloads, or write certificates; rotation makes old certificates suspect; a suspect `skill-certifier` blocks certification of other nodes while remaining directly certifiable; no unsigned certificate is accepted.

## Task 4: Implement local hashes, Git provenance, and safe certificate writes

**Files:**
- Modify shared blueprint graph, Git provenance, artifact-record, and atomic-file modules under `src/officina/` identified by Task 1.
- Test: `tests/test_officina_blueprint_graph.py`
- Test: `tests/test_officina_git_provenance.py`
- Test: `tests/test_officina_atomic_files.py`
- Test: new certificate-record tests under `tests/`.

**Deliverable:** Adapt the inventoried shared mechanical modules to provide `node_hash(x)`, `is_committed(x)`, unchanged-HEAD verification, source-commit capture, and atomic no-follow replacement. Add certificate-specific canonical signing and signature verification behind the new certification core rather than embedding certificate semantics in the shared Git or file primitives. All blueprint fields change the local node hash; certificates and dependency contents do not.

**Verification:** Cover dirty content, dirty blueprint, staged-but-uncommitted input, unrelated dirty files, content races, HEAD changes, symlinks, missing files, restored hashes, and retrieval of certified node files from `source_commit`.

## Task 5: Implement read-only certificate status with dual-read compatibility

**Files:**
- Modify: `skills/skill-drift/SKILL.md`
- Modify: `skills/skill-drift/blueprint.yaml` and its interface sidecars.
- Modify the drift runtime entrypoints identified by Task 1 through the skill's declared machine interfaces.
- Test: `skills/skill-drift/tests/test_drift_check.py`
- Test: `skills/skill-drift/tests/test_drift_hash.py`
- Test: `skills/skill-drift/tests/test_dependency_explorer.py`

**Deliverable:** Recursive `is_certified(x)`, `certification_statuses(G)`, exact dependency-key agreement, certifier-hash binding, source-commit reporting, certified/suspect concerns, version-2 node-hash output, and temporary legacy health reporting. Certificate status and health/test status remain separate.

**Verification:** Cover retained-certificate recovery, missing/corrupt/incorrectly signed certificates, suspect dependencies, dependency hash mismatch, extra dependency entries, certifier changes, exact-target isolation, no-target discovery, blueprintless external/plugin skills, mixed legacy/new installations, Markdown/JSON output, and version-1 compatibility.

## Task 6: Create `skill-certifier` and disable legacy writes

**Files:**
- Create: `skills/skill-certifier/` using `skill-maker` and the repository's typed blueprint conventions.
- Modify: `skills/skill-audit/SKILL.md` and blueprint/interface metadata into a temporary forwarding compatibility surface.
- Modify: `references/blueprint/runtime_dependencies.json` through its generating workflow.
- Test: migrate and extend `skills/skill-audit/tests/test_audit_certifier.py` under `skills/skill-certifier/tests/`.

**Deliverable:** A new certification core implementing two-pass blueprint production, dependency-first `certify(x)`, automatic or recursively human-approved blueprint repair, commit-required failure, certifier-status assertion, certificate writing, post-write status verification, `repair_dependents`, `certification_statuses(G)`, and `certify_all(G)` with per-node failure collection. It consumes the reused mechanical interfaces from Tasks 1 and 4 but does not import legacy health evaluation. The old alias forwards to the new interface and cannot write health records.

**Verification:** Cover two-pass dependency disagreement, automatic repair, recursive approval, already-certified early return, explicit certifier self-certification, suspect-certifier denial, commit-required outcomes, dependent repair propagation/stopping, graph mutation refresh, graph-wide independent failures, and post-write rollback on verification failure.

## Task 7: Define and implement legacy node migration

**Files:**
- Create migration fixtures under `tests/fixtures/certification/legacy/`.
- Modify compatibility loaders and installed-source adapters identified in Task 1.
- Test: new `tests/test_certificate_legacy_migration.py` plus focused drift/certifier tests.

**Deliverable:** Inventory-backed handling for legacy monolithic blueprints, virtual interface nodes, typed roots, shared behavior sources, and blueprintless external/plugin skills. Conversion is deterministic and idempotent. Old health records remain legacy evidence only; every migrated node requires a new certificate.

**Verification:** Compare pre/post graph identity and direct edges on representative fixtures, run conversion twice with no second change, and test mixed old/new installed copies across supported hosts.

## Task 8: Migrate callers, docs, policies, and generated artifacts

**Files:**
- Modify every path in the Task 1 inventory.
- Modify: `references/skill-standards/skill-guidelines.md`
- Modify: `docs/skill-blueprints.md`
- Modify: `docs/skills.md`
- Modify: `docs/contributors/README.md`
- Modify schemas, validators, tests, blueprints, behavior-source sidecars, and generated manifests that name old interfaces or health artifacts.

**Deliverable:** All supported callers use new versioned interfaces. Pooled review reads certificates, pooled-review health is removed, health remains an explicitly separate optional signal, and all policy inputs point to `docs/certification_and_drift.md`.

**Verification:** Golden caller tests pass for new callers and temporary aliases. Generated artifacts reproduce cleanly. The inventory has no unassigned old reference.

## Task 9: Recertify supported skills and exercise rollback

**Files:**
- Update only generated local certificate state and migration fixtures; do not commit private keys.
- Update the Task 1 inventory with recertification results.

**Deliverable:** Every supported skill is newly certified or explicitly reported suspect with a concrete reason. Exercise rollback to the recorded pre-cutover commit before removing legacy readers.

**Verification:** Mixed-state reporting is correct before recertification, new certificates are reproducible from `source_commit`, rollback restores old readers, and returning forward restores certificate status without accepting legacy records as certificates.

## Task 10: Cut over and remove compatibility surfaces

**Files:**
- Remove the temporary `skill-audit` forwarding surface after inventory proof.
- Remove legacy health readers, version-1 aliases, and obsolete schemas only when no supported installed copy uses them.
- Update the inventory and runtime dependency manifests.

**Deliverable:** New interfaces and certificates are the only supported write/read model. The last rollback-supported commit is recorded before removals.

**Verification:** Zero supported callers use old interfaces; no runtime writes or reads health records; no private key is reachable from drift; all new status, exact-target, installed-copy, and security tests pass.

## Task 11: Migrate references before deleting the old design document

**Files:**
- Modify every documentation, blueprint, behavior-source, policy-hash, schema, validator, test, generated manifest, runtime, and installed-artifact reference to `docs/audit_and_drift.md`.

**Deliverable:** Every live reference points to `docs/certification_and_drift.md` or is removed. Only this migration plan may mention the old filename as migration history.

**Verification:** Run a repository-wide zero-reference scan, blueprint sync, schema tests, validators, focused skill tests, full pytest/precommit, and installed-artifact migration checks before deletion.

## Task 12: Delete `docs/audit_and_drift.md` and verify again

**Files:**
- Delete: `docs/audit_and_drift.md`
- Modify: `docs/certification_and_drift.md` to remove its proposal warning and describe the implemented system.

**Deliverable:** The old document is deleted, not archived. The target document is authoritative.

**Verification:** Repeat the same zero-reference scan and complete validation suite after deletion. No live documentation, blueprint, behavior source, policy manifest, validator, test, generated artifact, runtime path, or installed copy may depend on the removed file.

## Completion criteria

The migration is complete only when all twelve tasks have passed their review gates and:

- the checked-in inventory has no unresolved caller, artifact, installed-copy, or reference entry;
- the new certificate model and authority boundary pass their named focused tests;
- every supported skill is certified or explicitly suspect under the new model;
- exact-target, no-target, mixed-installation, rollback, key-rotation, commit-race, symlink, atomic-write, and graph-wide partial-failure tests pass;
- repository blueprint sync, schema validation, validators, focused skill tests, full pytest/precommit, and generated-artifact checks pass with zero unexplained failures;
- temporary aliases and legacy readers/writers are removed;
- `docs/audit_and_drift.md` is deleted;
- `docs/certification_and_drift.md` describes the implemented system;
- the final zero-reference scan finds no dependency on the deleted document, except the historical statement in this completed migration plan.
