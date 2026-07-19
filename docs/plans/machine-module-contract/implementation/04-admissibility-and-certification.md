# Admissibility and Certification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**Goal:** Run versioned machine, conformance, and semantic checks; bind passing
evidence to module certificates; and enforce indexing/dispatch/injection
gates without adding a parallel status system.

**Architecture:** The schema-meta catalog plus a pinned profile produce an
immutable rule set. Pure rule
functions emit complete diagnostic results. Conformance adapters execute
controlled probes. The certifier signs a module-level payload containing
per-export evidence digests; legacy consumers may derive a transitional
pass-only view without writing health artifacts.

**Tech Stack:** Python, JSON Schema, pytest, existing graph/artifact-health and
skill-audit/skill-drift components.

**Primary requirements:** `ARG-008`, `OUT-002`, `OUT-005`, `IO-003`, `IO-005`,
`EXE-003` through `EXE-005`, and `ADM-001` through `ADM-010`.

## Preconditions and required reading

- Phases 1 through 3 are accepted. Their schema, graph, conformance-locator,
  projection, and `CertificationView` APIs are stable.
  These are prerequisites; they do not authorize Phase 4.
- Read Tasks 1-5 of `docs/plans/migrate_audit_to_certification.md` as source
  contracts for certificate storage, signing, Git provenance, and atomic writes.
  Reuse implementations that exist; otherwise implement the required target
  primitives in Task 5 here.
- Read `../IMPLEMENT.md`, the requirement entries above in
  `../01-decision-ledger.md`, and all of
  `../04-interface-admissibility.md`.
- Read only the matching rows in `../05-verification-matrix.md`.

## Phase stop conditions

Stop if the certification source contracts conflict with this target,
if a machine check would claim a semantic fact without evidence, if effect/I/O
conformance lacks an injected adapter plus demonstrated sandbox boundary, or if
self-certification would create a public bypass. Do not convert operational
health into certificate truth.

## Certification migration dependency and execution order

This plan and `docs/plans/migrate_audit_to_certification.md` are one migration,
not parallel implementations:

| Migration slice | Disposition here |
|---|---|
| External Tasks 1-5: inventory, certificate schemas, keys, Git/atomic mechanics, read-only status | source contracts; Task 5 reuses existing APIs or implements the required target subset |
| External Task 6: create `skill-certifier` and self-certification | absorbed by Tasks 4-5 here; this plan adds interface admissibility, conformance, and semantic evidence to that core |
| External Tasks 7-12: legacy nodes, callers, recertification, cutover, docs removal | execute through this package's Plan 5 after module certification works |

Within this plan execute Tasks 1-3, then Task 5's certificate adapter/payload
work, then Task 4's semantic certifier, then Task 6. Task 4 is documented first
because it defines the evidence Task 5 binds, but its public interface is not
enabled before Task 5 passes.

## Task 1: Catalog loader and diagnostic runner

**Files:**

- Create: `src/officina/common/interface_admissibility.py`
- Test: `tests/test_interface_admissibility.py`
- Use: `references/blueprint/schema-meta.json`
- Use: `references/blueprint/interface-admissibility.profile.yaml`
- Use: `references/blueprint/interface-admissibility-result.schema.json`

**Produces:** `AdmissibilityProfile`, `RuleResult`, `AdmissibilityReport`,
`load_admissibility_profile()`, and `check_interface_admissibility()`.

- [ ] Add tests for profile validation/hash, deterministic rule order, exact
  applicability, `passed|failed|not-applicable|checker-error`, stable JSON
  pointers/graph subjects, and aggregate admissibility.
- [ ] Implement rule dispatch from catalog metadata without dynamic arbitrary
  imports: validators are registered in a closed in-process registry keyed by
  catalog validator name.
- [ ] Require every profile rule to produce one result per applicable subject.
  Treat missing results and exceptions as checker errors, never passes.
- [ ] Validate the emitted report against the result schema before returning.
- [ ] Run `pytest tests/test_interface_admissibility.py -q`.

## Task 2: Pure schema/static/graph rule implementations

**Files:**

- Create: `src/officina/common/interface_admissibility_rules.py`
- Create fixtures: `tests/fixtures/interface_admissibility/`
- Test: `tests/test_interface_admissibility_rules.py`
- Refactor deterministic checks from:
  `skills/skill-audit/_rtx/_audit_certifier.py`
- Preserve focused behavior tests in:
  `skills/skill-audit/tests/test_audit_certifier.py`

- [ ] For each machine check ID in `04-interface-admissibility.md`, add one
  positive fixture and one minimal negative fixture whose only intended failure
  is that rule.
- [ ] Implement pure contract checks over normalized mappings and pure graph
  checks over graph/export records. Reuse schema, binding, ownership, graph, and
  projection functions; do not duplicate their semantics.
- [ ] Apply the projection module's standalone-export counter at 12,288 UTF-8
  bytes as a certification rule. Do not apply the 16,384-byte combined-consumer
  limit here; that composition-specific limit belongs only to Plan 3 injection.
- [ ] Compare each export with its previous certified public-contract
  projection. Mechanically flag removed arguments/outcomes, newly required
  arguments, narrowed accepted values, changed effects/authorization, and other
  cataloged breaking differences when the public version is unchanged; route
  ambiguous compatibility changes to semantic review.
- [ ] Reconstruct the predecessor projection from its certificate
  `source_commit`, recorded module path, export ID/version, and versioned
  canonical projection algorithm. First certification is not-applicable; a
  claimed predecessor that cannot be reproduced is a checker error.
- [ ] Split deterministic file/root/runtime-entrypoint/exposed-logic checks out
  of the currently named semantic check and give each the correct machine rule
  ID. Reserve semantic checks for judgment.
- [ ] Add a matrix test that loads the catalog and proves every nonsemantic rule
  has a registered validator plus positive and negative fixtures.
- [ ] Run rule, schema, graph, binding, and projection tests together.

## Task 3: Standard conformance protocol

**Files:**

- Use/extend: `references/blueprint/interface-conformance.schema.json`
- Create: `src/officina/common/interface_conformance.py`
- Create: `src/officina/common/conformance_adapters.py`
- Test: `tests/test_interface_conformance.py`

**Produces:** A confined fixture/probe manifest and runner for every public
export.

- [ ] Load each module's required `conformance_manifest` locator, require one
  case inventory for every export, bind manifest bytes into the evidence digest,
  and reject missing, escaping, symlinked, or untracked manifests.
- [ ] Define cases with caller values/stdin, controlled fixture state, expected
  outcome, stream assertions, termination/readiness/stop assertions, expected
  effects, and declared observation scope.
- [ ] Generate contract-derived minimum/finite-maximum/missing/invalid/unknown,
  stdin mismatch, and fixed override/duplication cases. Require every semantic
  outcome to have a manifest case or explained
  `not-deterministically-inducible`; emit the latter as not-applicable and pass
  it to semantic review.
- [ ] Execute through the same compiled binding and private gateway runner used
  by dispatcher. Use only closed-registry fixture adapters for filesystem,
  clock, network, helper, subprocess, calendar, and email boundaries. Forbid
  live external mutation and real credentials. Never execute a shell string or
  arbitrary catalog command.
- [ ] Require `python-adapter-v1` as the manifest `execution_boundary`. Inject a
  typed adapter bundle through the declared gateway seam and deny direct
  boundary access in an OS sandbox. Record the sandbox mechanism and covered
  boundaries in evidence.
- [ ] Implement Python adapter calls over the `BoundaryAdapter.invoke()`
  registry. Validate operation schemas and stable error codes; reject every
  unknown operation or unvalidated payload/result.
- [ ] Mark a gateway without the injection seam or demonstrated sandbox coverage
  ineligible for effect-conformance and no-undeclared-I/O claims. Add negative
  Python cases proving a manifest declaration alone cannot claim interception.
- [ ] Implement checks for gateway acceptance, fixed-value enforcement,
  output/outcome conformance, lifecycle, and positive effect observation.
- [ ] Require absence-of-undeclared-effect claims to name a tracer/sandbox
  coverage mechanism; otherwise record that claim as not established and leave
  semantic I/O completeness for certification.
- [ ] Require process, readiness, and stop deadlines plus cleanup and
  post-cleanup assertions for every long-running case. Treat cleanup failure as
  a checker error. Keep live probes in operational health, outside required
  certification evidence.
- [ ] Add read, mutation, finite, long-running, refusal, partial, and parser
  boundary fixtures. Run conformance tests.

## Task 4: Genuine semantic certification checks

**Files:**

- Create/update through `skill-maker`: `skills/skill-certifier/`
- Create: `skills/skill-certifier/tests/test_certifier.py`
- Create: `skills/skill-certifier/references/semantic-certification.md`
- Create:
  `skills/skill-certifier/references/semantic-certification-result.schema.json`
- Modify: `skills/skill-audit/_rtx/_audit_certifier.py` only as the temporary
  forwarding compatibility surface required by the certification migration
- Modify: `skills/skill-audit/tests/test_audit_certifier.py` for forwarding tests

- [ ] Require machine admissibility and conformance before semantic review.
- [ ] Review every exported interface against implementation, focused tests,
  behavior sources, previous certified public contract, and direct dependencies
  for the complete semantic profile.
- [ ] Classify ambiguous public-contract differences as breaking or nonbreaking
  and require a version bump before signing when breaking.
- [ ] Store exact interface-targeted findings and evidence summaries. Treat text
  heuristics as advisory prompts only; do not map them directly to pass/fail.
- [ ] Reject module certification when any public export fails. Preserve module
  and interface IDs in all diagnostics.
- [ ] Add fixtures distinguishing structurally valid multimodal prose, hidden
  argument interaction, incomplete outcomes, inaccurate execution claims,
  missing I/O, and a genuinely admissible/certifiable interface.
- [ ] Run `pytest skills/skill-certifier/tests/test_certifier.py
  skills/skill-audit/tests/test_audit_certifier.py -q`.

## Task 5: Certificate/profile/evidence binding

**Files:**

- Create/modify: `references/blueprint/certificate.schema.json`
- Create: `src/officina/common/certificates.py`
- Create: `src/officina/common/certificate_signing.py`
- Create: `src/officina/common/certificate_certification_view.py`
- Modify: `src/officina/common/git_provenance.py`
- Modify: `src/officina/common/atomic_files.py`
- Modify: `src/officina/common/blueprint_graph.py`
- Modify: `src/officina/common/artifact_health.py` only to read legacy state or
  derive a read-only compatibility view
- Preserve read-only: `references/blueprint/health.schema.json`
- Create: `tests/test_officina_certificates.py`
- Create: `tests/test_officina_certificate_signing.py`
- Create: `tests/test_officina_certificate_certification_view.py`
- Test: `tests/test_officina_git_provenance.py`
- Test: `tests/test_officina_atomic_files.py`
- Test: `tests/test_officina_artifact_health.py`

This task subsumes the payload/profile portion of
`docs/plans/migrate_audit_to_certification.md`; do not execute the two plans in
parallel or create competing certificate schemas.

- [ ] Extend the target signed payload with profile ID/hash, required
  rule/version set, complete evidence-report digest, and per-export result
  digests. Bind the canonical conformance-manifest and transitive resolved
  contract-definition locator/digest map. Include the catalog/profile in
  certifier behavior roots.
- [ ] Make rule/profile/evidence drift produce suspect status even when module
  content is unchanged. Preserve dependency hash/signature checks.
- [ ] Do not dual-write legacy health. If compatibility reporting needs passing
  rule IDs, derive a read-only view from the certificate and diagnostic report.
- [ ] Add tests for changed rule version, changed profile membership, changed
  evidence, one failed sibling export, missing report, invalid signature, and
  recovery after exact state restoration.
- [ ] Implement `CertificateCertificationView.check_export(module_id,
  interface_id, interface_version) -> CertificationDecision` by resolving the
  module subject, reading
  its authoritative current certificate, verifying signature/source/node and
  dependency hashes, matching the active profile ID/hash, and requiring the
  exact export version's passing result digest. Any missing/mismatch/error is
  false with a stable diagnostic.
- [ ] Re-resolve every bound manifest/definition locator no-follow before
  returning certified. Changed, moved, missing, extra, or symlinked bytes make
  the certificate suspect even when blueprint/runtime content is unchanged.
- [ ] Bootstrap `skill-certifier` only through its private self-certification
  entrypoint from the certification migration: verify committed source, signing
  authority, exact dependencies, profile/evidence, unchanged HEAD, and
  post-write status without dispatcher. This exception is exact-node-only and
  does not bypass module admissibility or permit certifying another target.
- [ ] Run certificate, skill-audit, skill-drift, and artifact-health tests.

## Task 6: Enforce runtime gates

**Files:**

- Modify: dispatcher repository-index path from Plan 2
- Modify: projection/certification view from Plan 3
- Test: dispatcher and projection tests

- [ ] Recheck inexpensive blocking machine rules at indexing/dispatch and
  reject all malformed/unsafe public exports.
- [ ] Require current certificate/profile/per-export pass for public dispatcher
  execution and injection.
- [ ] Apply the new certificate gate to target `machine-module` fixtures and
  APIs. Do not migrate or reinterpret existing declarations in this phase.
  Keep structural safety gates active; never reinterpret a legacy health record
  as a certificate.
- [ ] Keep operational-health failures reportable without rewriting the
  certificate or blueprint.
- [ ] Prove no flag, environment variable, caller ID, or owner identity bypasses
  structural dispatcher rejection.

## Task 7: Phase gate

- [ ] Run `pytest tests/test_interface_admissibility.py
  tests/test_interface_admissibility_rules.py
  tests/test_interface_conformance.py tests/test_interface_projection.py
  tests/test_officina_dispatcher.py tests/test_officina_certificates.py
  tests/test_officina_certificate_signing.py
  tests/test_officina_certificate_certification_view.py
  tests/test_officina_git_provenance.py tests/test_officina_atomic_files.py
  tests/test_officina_artifact_health.py
  skills/skill-certifier/tests/test_certifier.py
  skills/skill-audit/tests/test_audit_certifier.py
  skills/skill-drift/tests/test_drift_check.py -q`.
- [ ] Validate every diagnostic report against its schema and every certificate
  against the target certificate schema.
- [ ] Run `git diff --check` before repository migration.

## Phase completion evidence

Report rule/profile versions and hashes, conformance boundaries exercised,
semantic evidence disposition, certificate payload/view behavior, requirement
IDs, exact test commands and counts, remaining operational-health findings, and
exact worktree scope. Stop for review before Plan 5.
