# Blueprint Maturity and Installation Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit blueprint maturity, installation-tier, and personal-preference metadata and use it to derive optional runtime dependency installation.

**Architecture:** Blueprint schemas own the metadata contract. The blueprint syncer emits module metadata alongside executable dependency attribution in the generated runtime manifest. The installer selects core plus user-approved optional module closures, then builds one pooled lock/install set from that selection.

**Tech Stack:** JSON Schema, YAML blueprints, Python standard library, existing `uv` lock generation, pytest.

**Spec:** `docs/superpowers/specs/2026-08-19-blueprint-maturity-installation-metadata-design.md`

## Global Constraints

- Maturity values are exactly `stable` and `experimental`.
- `installation_tier` is `core` or `optional` and applies only to discoverable module blueprints.
- `personal_preference.applies: true` requires a nonempty description.
- Optionality is selected at module granularity; contained behavioral sources are not independently selected.
- Cost is estimated dynamically from resolved package metadata; no package-name exception remains.
- Package size estimates come from package-index wheel/sdist metadata through the existing cache boundary, with an explicit unavailable result when metadata is missing.
- Core-only installation uses the checked-in universal lock; optional selections generate a separate pinned-uv, hash-checked lock and record selected modules plus input/lock hashes in the candidate artifact.

### Task 1: Extend the live schemas and authoring templates

**Files:**
- Modify: `references/blueprint/module.schema.json`
- Modify: `references/blueprint/behavioral-source.schema.json`
- Modify: `references/blueprint/schema.annotated-draft.json`
- Modify: `references/blueprint/schema-meta.json`
- Modify: `references/blueprint/template.yaml`
- Modify: `src/officina/blueprints/template.py`
- Test: `tests/test_typed_blueprint_schemas.py`
- Test: `tests/test_blueprint_schema_metadata.py`

**Interfaces:**
- Produces schema validation for `maturity`, module-only `installation_tier`, and module-only nested `personal_preference`.

- [ ] Add failing schema tests for valid stable/experimental values, invalid values, module-only installation metadata, and the conditional preference description.
- [ ] Run the focused schema tests and confirm the new cases fail before implementation.
- [ ] Add the properties and conditional requirements to both concrete schemas and their annotated metadata.
- [ ] Update the authoring template/example generator so new module and source blueprints contain the required fields.
- [ ] Run the focused schema and metadata tests; confirm all new and existing cases pass.

### Task 2: Migrate repository blueprints and documentation

**Files:**
- Create: `scripts/migrate-blueprint-installation-metadata.py`
- Modify: all live `skills/*/blueprint.yaml` and `skills/*/_rtx/blueprint.yaml` files as required by the schema
- Modify: `src/officina/*/blueprint.yaml` files as required by the schema
- Modify: `docs/officina/skill-blueprints.md`
- Modify: `docs/officina/installation.md`
- Modify: `docs/dependency-and-bootstrap-audit.md`
- Test: `tests/test_docs_catalog.py`
- Test: `tests/validate_documentation_validators.py`

**Interfaces:**
- Produces a repository-wide schema-valid catalog with explicit metadata for every discoverable module and source node.

- [ ] Add a failing script test for deterministic defaults and the `pdf-to-markdown`, `using-compass`, and `rutter` overrides.
- [ ] Run blueprint discovery/schema validation to enumerate every affected live blueprint.
- [ ] Implement and run the idempotent standard-library migration script; default every node to stable/core/false and apply only the named overrides.
- [ ] Mark `pdf-to-markdown` optional through the script; do not infer optionality from dependency names.
- [ ] Verify the target worktree contains the named `using-compass` and `rutter` nodes before applying their experimental overrides; if absent, retain the explicit override map for a later branch migration and report that absence.
- [ ] Document the maturity, installation, and preference semantics and the dynamic cost estimate.
- [ ] Run the focused documentation/catalog and blueprint validation tests.

### Task 3: Generate metadata-aware runtime dependency manifests

**Files:**
- Modify: `skills/skill-maker/_rtx/_blueprint_syncer.py`
- Modify: `references/blueprint/runtime_dependencies.json`
- Test: `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- Test: `tests/test_officina_runtime_lock.py`

**Interfaces:**
- `generated_runtime_dependencies_manifest()` emits module installation metadata and dependency attribution sufficient for module selection.
- Existing `render_runtime_requirements()` consumes the selected manifest view without changing hash-lock guarantees.

- [ ] Add failing syncer tests proving module metadata is emitted and dependencies retain their owning module/interface attribution.
- [ ] Run those focused syncer tests and confirm failure.
- [ ] Extend manifest generation with validated module metadata and selected-module dependency records while preserving deterministic ordering.
- [ ] Regenerate the checked-in runtime manifest and update lock input only through the existing sync/generation workflow.
- [ ] Run syncer, manifest, and runtime-lock tests, including stale-manifest rejection.

### Task 4: Replace installer hardcoding with optional-module selection

**Files:**
- Modify: `src/officina/install/managed_runtime.py`
- Modify: `src/officina/install/runtime_lock.py`
- Modify: `skills/install-assistant-tools/_rtx/_phase_entry.py`
- Modify: `skills/install-assistant-tools/SKILL.md`
- Test: `tests/test_officina_managed_runtime.py`
- Test: `tests/test_officina_runtime_lock.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_install.py`
- Test: `skills/install-assistant-tools/_rtx/tests/test_e2e_lifecycle.py`

**Interfaces:**
- Replace package-name filtering with a manifest-driven selected-module API.
- The phase entry prompt receives optional module metadata, displays dependency and estimate information, and passes the approved selection to candidate construction.
- Core-only noninteractive installation remains deterministic and does not prompt.

- [ ] Add failing tests for core-only selection, accepting selected optional modules, rejecting unknown module IDs, shared-package deduplication, and no hardcoded `marker-pdf` policy.
- [ ] Add tests for prompt output naming optional skills, packages, and unavailable size estimates without requiring network access.
- [ ] Run the focused installer tests and confirm failure.
- [ ] Implement module closure selection and platform-aware dependency pooling from the generated manifest.
- [ ] Implement best-effort package-size estimation through the existing resolver/package metadata boundary; represent unavailable estimates explicitly.
- [ ] Read wheel/sdist sizes from package-index metadata through the existing cache boundary; never synthesize a value when metadata is unavailable.
- [ ] Generate a separate optional-selection lock with pinned `uv --generate-hashes`, enforce its hashes during install, and record selected module IDs plus input/lock hashes in the candidate artifact.
- [ ] Remove `_OPTIONAL_HEAVY_PACKAGE_NAMES`, the unconditional optional-dependency rejection, and the obsolete first-release exception text.
- [ ] Run focused installer and lock tests on core-only and selected-optional paths.

### Task 5: Regenerate, validate, and review the complete change

**Files:**
- Modify: generated `SKILL.md` contract blocks and generated runtime artifacts only where the canonical sync commands require them
- Test: repository blueprint sync and configured test runner

- [ ] Run the canonical blueprint sync in check mode and repair only generated drift.
- [ ] Run the focused schema, syncer, manifest, installer, and documentation tests.
- [ ] Run `scripts/generate-runtime-lock.py --check` with the configured lock inputs.
- [ ] Inspect the final diff for exact scope, especially optional-module metadata and removal of package-name policy.
- [ ] Run the repository’s configured validation command and record any unrelated failures separately.
