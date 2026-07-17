# Standard v6 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install revised standard v6, add typed `remedied-by` links, replace four hand-authored Markdown standards with three canonical YAML standards, preserve readable generated views, and document conversion quality.

**Architecture:** `references/standards/` owns one JSON Schema, semantic validator, and deterministic Markdown renderer. Three canonical YAML documents own policy data; generated Markdown views remain the LLM-facing consumption surface. Repository validators check canonical data and renderer freshness, while conversion fidelity tests prove that the deleted sources were preserved.

**Tech Stack:** Python 3, JSON Schema draft 2020-12, PyYAML, pytest/unittest-compatible repository tests, existing validator and Git-hook infrastructure.

## Global Constraints

- Work directly on `master`, as explicitly approved by the user.
- Canonical files are `skill-guidelines.standard.yaml`, `skill-refactoring.standard.yaml`, and `document-profile.standard.yaml`.
- `skill-refactoring.standard.yaml` has distinct diagnostic-signals and refactoring-moves families.
- `remedied-by` links run from a diagnostic smell to a refactoring family or procedure; render the inverse as `Addresses`.
- Keep one schema, semantic validator, and renderer; do not build a general documentation framework.
- Do not migrate `llm-interface-design.md` or Blueprint supporting artifacts.
- Standards contain relevant enforcement references, not run histories or pass/fail state.
- Do not commit, stage, or push changes without separate user authorization.

---

### Task 1: Install the v6 contract and test `remedied-by`

**Files:**
- Create: `references/standards/standard-v6.schema.json`
- Create: `references/standards/validate_standard_v6.py`
- Create: `references/standards/render_standard_v6.py`
- Create: `tests/test_standard_v6.py`

**Interfaces:**
- Produces: `validate_document(document: dict, root: Path) -> list[str]`
- Produces: `render_document(document: dict) -> str`

- [ ] **Step 1: Write failing tests** for schema acceptance of `remedied-by`, rejection of invalid source/target ancestry, and forward/reverse renderer labels.
- [ ] **Step 2: Run `pytest -q tests/test_standard_v6.py`** and confirm failures arise from missing repository tooling.
- [ ] **Step 3: Copy the audited revised-v6 schema/validator/renderer into the canonical directory**, change the schema identity to its repository path, add `remedied-by` to the relation enum, and implement ancestry validation plus `Remedies`/`Addresses` rendering.
- [ ] **Step 4: Run `pytest -q tests/test_standard_v6.py`** and require all Task 1 tests to pass.

### Task 2: Convert and merge skill smells with refactoring remedies

**Files:**
- Create: `references/skill-standards/skill-refactoring.standard.yaml`
- Create: `references/skill-standards/skill-refactoring.md`
- Create: `tests/test_skill_refactoring_standard.py`
- Delete after fidelity gate: `references/skill-standards/skill-smells.md`
- Delete after fidelity gate: `references/skill-standards/skill-refactoring-catalog.md`

**Interfaces:**
- Consumes: Task 1 validator and renderer.
- Produces: stable smell IDs, remedy IDs, and complete `remedied-by` mappings.

- [ ] **Step 1: Write failing fidelity tests** that enumerate every smell heading, signal, analog, move mapping, remedy heading/body, preservation condition, verification instruction, risk, risk ordering, and ordering rule from immutable test fixtures or explicit expected values.
- [ ] **Step 2: Run the focused test** and confirm the canonical merged standard is missing.
- [ ] **Step 3: Convert both sources** into the two internal families, replace generic recommendation mappings with `remedied-by`, resolve the two previously unmatched remedy labels without losing their source meaning, and render the Markdown view.
- [ ] **Step 4: Validate, render twice to prove determinism, run fidelity tests, then remove the two original Markdown sources.**

### Task 3: Convert skill guidelines and document profiles

**Files:**
- Create: `references/skill-standards/skill-guidelines.standard.yaml`
- Create: `references/skill-standards/skill-guidelines.md`
- Create: `references/document-standards/document-profile.standard.yaml`
- Create: `references/document-standards/document-profile.md`
- Create: `tests/test_migrated_standards_fidelity.py`
- Delete after fidelity gate: `references/document-profile-schema.md`

**Interfaces:**
- Consumes: Task 1 validator and renderer.
- Produces: canonical rule IDs and readable views preserving current consumer behavior.

- [ ] **Step 1: Write failing fidelity tests** covering every guideline heading/normative statement/enforcement reference and every document-profile field, field note, TeX template line, optionality rule, inference rule, and normalization mapping.
- [ ] **Step 2: Run the focused test** and confirm the canonical standards are missing.
- [ ] **Step 3: Convert the sources**, retaining atomic rules where practical, typed check/test references, explicit semantic-review remainder, and no execution history.
- [ ] **Step 4: Validate both documents, render deterministic Markdown views, pass fidelity tests, and remove `references/document-profile-schema.md`.** The skill-guidelines path remains but its contents become generated Markdown.

### Task 4: Migrate active consumers and enforce canonical freshness

**Files:**
- Modify: active skill files and blueprints found by exact-path search
- Modify: `skills/skill-drift/references/policy-hash-roots.json`
- Modify: `validators/platform_neutral.py`
- Create: `validators/standard_documents.py`
- Create: `tests/validate_standard_documents.py`
- Modify: current documentation with live links

**Interfaces:**
- Consumes: three canonical standards and generated views.
- Produces: repository validation errors for invalid standards or stale rendered views.

- [ ] **Step 1: Write failing validator tests** for invalid YAML/schema semantics, stale Markdown rendering, and valid repository standards.
- [ ] **Step 2: Run the focused validator tests** and confirm failure because the validator is absent.
- [ ] **Step 3: Implement the validator** using Task 1 tooling and update active consumers, blueprint behavior sources, policy roots, tests, comments, and current documentation to canonical or generated paths as appropriate.
- [ ] **Step 4: Run zero-reference searches** for deleted source names, allowing only explicit historical migration records, and resolve every live reference.
- [ ] **Step 5: Run focused tests, blueprint synchronization check, skill hooks, and repository validator runner.**

### Task 5: Audit conversion quality and verify the repository

**Files:**
- Create: `docs/standard-v6-conversion-assessment.md`

**Interfaces:**
- Consumes: all migration artifacts and test evidence.
- Produces: evidence-backed fidelity, placement, exposition, and residual-risk assessment.

- [ ] **Step 1: Compare source content recovered from Git HEAD with canonical items and rendered views**, recording any semantic combination, split, renamed term, or intentionally omitted migration-only material.
- [ ] **Step 2: Assess** information preservation, item placement, smell-remedy completeness, human readability, enforcement linkage, schema complexity, and remaining limitations.
- [ ] **Step 3: Run fresh focused tests, `python3 validators/runner.py`, `bash .githooks/skill/check-blueprints`, and `bash .githooks/pre-commit`.** Record exact outcomes without storing them in standards.
- [ ] **Step 4: Run `git diff --check` and inspect the final diff for unintended files or unrelated changes.**
