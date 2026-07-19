# Schema, Standards, and Fixture Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
> Steps use checkbox syntax for tracking.

**Goal:** Make the target machine-module and simple-interface contract the only
v3 schema authority and establish a versioned admissibility rule/result format.

**Architecture:** Create closed machine-module and caller-contract schemas,
centralize direct I/O, register validation rules once, and build minimal
positive/negative fixtures before graph or dispatcher changes.

**Tech Stack:** JSON Schema draft-07, YAML, Python, pytest.

**Primary requirements:** `MOD-001` through `MOD-006`, `IFC-001` through
`IFC-006`, `BND-001` through `BND-007`, `ARG-001` through `ARG-011`,
`PRE-001`, `OUT-001` through `OUT-005`, `DEP-001` through `DEP-004`, `IO-001`
through `IO-005`, `EXE-001` through `EXE-005`, and `ADM-001`/`ADM-002`.

## Preconditions and required reading

- No earlier package phase is required. Record the existing typed-schema,
  schema-metadata, and standard-generation baseline before editing.
- Read `../IMPLEMENT.md` and the requirement entries above in
  `../01-decision-ledger.md`.
- Read all sections of `../02-machine-module-contract.md`; this phase encodes
  that complete local shape.
- Read `Definitions`, `Canonical rule authority`, `Diagnostic result`, and
  `Schema and static rules` in `../04-interface-admissibility.md`.
- Read only the matching rows in `../05-verification-matrix.md`.

## Phase stop conditions

Stop before changing code if the live schema registry cannot host the planned
module selector, if a required field has no single
normative definition, or if a baseline failure prevents observing the intended
red-green result. Stop during execution if a fixture requires `calls`, a mode
selector, or another structure rejected by `IFC-002`.

## Task 1: Freeze behavioral evidence and write failing target-shape tests

**Files:**

- Modify: `tests/test_typed_blueprint_schemas.py`
- Create: `tests/fixtures/machine_modules/records.valid.yaml`
- Create: `tests/fixtures/machine_modules/calls.invalid.yaml`
- Use as executable fixtures: `../examples/interface-conformance.yaml` and
  `../examples/advanced-interface-conformance.yaml`
- Inspect only: `skills/skill-drift/_rtx/_check_drift_state.py`,
  `skills/skill-drift/tests/test_drift_check.py`,
  `skills/skill-drift/tests/test_drift_hash.py`,
  `skills/email-triage/_rtx/_watermark_writer.py`, and
  `skills/email-triage/tests/test_watermark.py`; existing blueprint declarations
  are non-authoritative migration hints and are not modified in this phase

**Produces:** Executable examples for MOD, IFC, BND, ARG, and EXE requirements.

- [ ] Add a fixture loader and a valid document matching
  `examples/machine-module.yaml`, including one complete export.
- [ ] Load the simple and advanced conformance examples directly as canonical
  target fixtures; avoid a copied fixture set that can drift. Keep them
  parseable in Task 1 and require full schema validity in Task 2.
- [ ] Add one minimal negative fixture for each removed structure: `calls`,
  selector, accepts, constraints, conditional default, profile,
  draft/unresolved state, dispatcher consequences, and removed tag alternatives.
- [ ] Assert the target v3 selector accepts only the new module shape. Existing
  v2 and prototype declarations are not acceptance evidence for this phase and
  do not constrain the target schema.
- [ ] Run `pytest tests/test_typed_blueprint_schemas.py -q`; verify the target
  valid fixture fails because `machine-module.schema.json` is absent.

## Task 2: Define the module and export schemas

**Files:**

- Create: `references/blueprint/machine-module.schema.json`
- Create: `references/blueprint/interface-conformance.schema.json`
- Create: `references/blueprint/conformance-boundary-operations.yaml`
- Create: `references/blueprint/conformance-operations/filesystem.schema.json`
- Create: `references/blueprint/conformance-operations/clock.schema.json`
- Create: `references/blueprint/conformance-operations/network.schema.json`
- Create: `references/blueprint/conformance-operations/helpers.schema.json`
- Create: `references/blueprint/conformance-operations/subprocess.schema.json`
- Create: `references/blueprint/conformance-operations/calendar.schema.json`
- Create: `references/blueprint/conformance-operations/email.schema.json`
- Create: `references/blueprint/caller-contract.schema.json`
- Create: `references/blueprint/direct-io.schema.json`
- Modify: `references/blueprint/common.schema.json`
- Modify: `references/blueprint/schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Inspect only as a migration hint:
  `references/blueprint/machine-interface.schema.json`

**Produces:** Closed schemas implementing `MOD-001` through `MOD-006`,
`IFC-001` through `IFC-006`, `BND-001` through `BND-007`, `ARG-001`
through `ARG-011`, `PRE-001`, `OUT-001` through `OUT-005`, `IO-001`
through `IO-005`, `DEP-001` through `DEP-003`, `EXE-001` through
`EXE-005`, and the conformance-manifest locator/case shape. Graph/runtime and
semantic enforcement for these fields remains in later plans.

- [ ] Add `machineModuleId`, nested `machineInterfaceExport`, module/export
  authorization, shared/private ownership, module tool edges, and interface tool
  and helper definitions to the correct schema files.
- [ ] Require one confined regular-file `conformance_manifest` locator on every
  module using exact `{base: skill-root, path: <relative-literal>}` form; it is
  certification evidence and not runtime `content`.
- [ ] Define the closed conformance-manifest shape, including complete export
  coverage; parser cases; fixture state; stream/effect assertions; observation
  scope; helper fixtures; execution boundary; and the long-running
  readiness/stop/cleanup/post-cleanup branch. Permit an uncovered semantic
  outcome only through explained `not-deterministically-inducible` disposition;
  binding boundary cases are runner-generated. Validate both examples.
- [ ] Define and schema-check the shared versioned boundary/operation registry,
  request/success/error envelopes, initial stable error codes, and one positive
  and negative fixture for every initial operation.
- [ ] Define `interfaceInvocationBinding.fixed` as discriminated `positional`,
  `option`, and `switch` entries. Define argument binding separately and reject
  raw strings, dispatcher-global names, collisions representable locally, and
  secret fixed values.
- [ ] Rewrite the caller contract without `title` or `calls`. Require arguments,
  preconditions, interaction, outputs, outcomes, execution, and helpers at
  interface scope with explicit empty-list forms where appropriate.
- [ ] Require every output to reference a compatible immediate `direct_io`
  write. Encode typed fixed-value types, structured effect value/evidence
  references, the closed action/access compatibility, and exact literal
  directory-subtree ownership semantics.
- [ ] Require bidirectional filesystem argument/direct-I/O links using
  `direct_io_ref` plus typed `path_source`, and exact inverse consistency
  between outcome effect lists and effect occurrence sets.
- [ ] Encode every recursive type branch with `additionalProperties: false` and
  kind-specific `oneOf` conditions. Add closed sensitivity, framing, signal,
  relative base, secret-file protection, verification method, and execution tag
  vocabularies.
- [ ] Add ordered inclusive numeric bounds and closed units. Require
  glob/regex `match_count`, while fixing presented-path matching, selected-link
  following, and no directory-link traversal as standard semantics rather than
  author-configurable switches.
- [ ] Make `direct-io.schema.json` the single definition and replace copied
  common-schema definitions with references. Put the tmp/log exception in the
  schema `description` and `x-famulus.doc.authoring` metadata.
- [ ] Update root schema selection and schema-meta node relationships for
  `machine-module`. The target v3 authoring path must not select the singular
  prototype schema. Any legacy parser retained for Phase 5 is outside the
  target selector and cannot authorize new declarations.
- [ ] Run the typed schema file. Verify the valid fixture passes and every
  negative fixture fails at the intended JSON pointer.

## Task 3: Add a canonical admissibility catalog and result schema

**Files:**

- Create: `references/blueprint/interface-admissibility.profile.yaml`
- Create: `references/blueprint/interface-admissibility-result.schema.json`
- Modify: `references/blueprint/schema-meta.json`
- Test: `tests/test_blueprint_schema_metadata.py`
- Test: `tests/test_interface_admissibility_catalog.py`

**Produces:** One schema-meta rule catalog with two discriminated entry kinds,
a pinned admissibility profile, profile hash inputs, and complete diagnostic
results.

- [ ] Make `schema-meta.json#/definitions/validationRule` a discriminated
  `oneOf`. Add `rule_kind: repository-validation` to every existing entry while
  retaining its current fields. Add new `rule_kind: interface-admissibility`
  entries with closed `version`, `phase`, `scope`, `statement`, `blocks`,
  validator, evidence, applicability, and positive/negative fixture fields.
  Use a closed validator registry key and schema-validated
  `always|field-present|field-equals` applicability rather than import paths.
  Encode every rule from `04-interface-admissibility.md` in the existing
  catalog ID namespace.
- [ ] Add a single `machine-export-admissibility@1` profile pinning the
  ordered rule/version set. Reject non-admissibility profile members, duplicate
  IDs, and unresolved schema `related_validation_rules` references.
- [ ] Add diagnostic result variants `passed`, `failed`, `not-applicable`, and
  `checker-error`, stable findings, evidence, subject IDs, source hash, profile
  ID, and profile hash.
- [ ] Add tests proving unrelated catalog map order does not affect canonical
  profile hashing, while profile-list reordering or any pinned rule
  ID/version/meaning change does.
- [ ] Run `pytest tests/test_blueprint_schema_metadata.py tests/test_interface_admissibility_catalog.py -q`.

## Task 4: Update the canonical skill standard

**Files:**

- Modify: `references/skill-standards/skill-guidelines.standard.yaml`
- Regenerate: `references/skill-standards/skill-guidelines.md`
- Modify: `.githooks/skill/check-blueprints`
- Inspect only: `.githooks/skill/check-runtime-files`,
  `.githooks/skill/check-dependencies`, `.githooks/skill/check-names`
- Test: `tests/validate_standard_documents.py`
- Test: `tests/validate_platform_neutral.py`
- Test: `tests/test_migrated_standards_fidelity.py`

**Produces:** Author-facing machine-module rules with one enforcement owner per
machine-enforceable assertion.

- [ ] Add one authoritative v3 machine-module family that explicitly supersedes
  the imported singular sidecar/`binding`/`usage`/`patterns` guidance for new
  declarations. Preserve the source-fidelity history while defining the
  module/export model, invocation binding, direct-I/O scope, tool union, simple
  interface invariant, and certification/admissibility distinction.
- [ ] Split compound assertions into atomic assertions and attach the applicable
  rule IDs. Remove stale one-interface-per-sidecar and hidden-subinterface
  prohibitions only where machine modules replace them.
- [ ] Document the tmp/log exception and future behavior-inheritance deferral in
  the canonical YAML; do not add either to the SessionStart hook.
- [ ] Regenerate Markdown with the repository's standard renderer; run the
  three named standard tests and `.githooks/skill/check-blueprints`.
- [ ] Run `git diff --check` and inspect that no unrelated plan was modified.

## Task 5: Foundation gate

- [ ] Run `pytest tests/test_typed_blueprint_schemas.py tests/test_blueprint_schema_metadata.py tests/test_interface_admissibility_catalog.py -q`.
- [ ] Run `pytest tests/test_typed_blueprint_schemas.py
  tests/test_officina_blueprint_template.py
  tests/test_blueprint_schema_metadata.py
  tests/test_interface_admissibility_catalog.py -q` to prove every required
  field has `x-famulus` authoring metadata and every catalog rule resolves.
- [ ] Run `tests/validate_standard_documents.py`,
  `tests/validate_platform_neutral.py`,
  `tests/test_migrated_standards_fidelity.py`, and
  `.githooks/skill/check-blueprints`.
- [ ] Record the exact target-schema files and fixture count in the plan
  execution log before starting Plan 2.

## Phase completion evidence

Report the exact schemas, standards, fixtures, and tests changed; requirement
IDs covered; focused command results and counts; generated standard artifacts;
baseline failures that remain demonstrably unrelated; and exact worktree scope.
Stop for review before Plan 2.
