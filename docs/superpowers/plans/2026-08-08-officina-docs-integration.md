# Officina Documentation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the Officina documentation relocation while removing the canonical refactoring standard's dependency on a non-normative documentation path.

**Architecture:** Keep the two existing documentation commits intact. Remove only the informative documentation clause from the category-remedy instruction, then propagate revision and exact-byte SHA-256 digest changes through the complete node-standard import closure.

**Tech Stack:** YAML standard documents, Python 3.11, pytest, SHA-256 pinned imports, Git worktrees.

## Global Constraints

- `docs/officina/` remains the sole location of the moved framework documentation.
- Do not recreate `docs/skill-blueprints.md` or rewrite historical plans.
- Do not change `standard_version`, source digests, source-unit digests, unrelated policy, or generated views.
- Every import digest is SHA-256 over the imported YAML file's exact bytes, prefixed with `sha256:`.
- Preserve documentation commits `31c8295` and `812c718` unchanged.

---

### Task 1: Remove the non-normative documentation dependency

**Files:**
- Modify: `tests/test_skill_refactoring_standard.py`
- Modify: `references/node-standards/refactoring.standard.yaml`
- Modify: `tests/fixtures/standards/skill-refactoring-source-map.yaml`

**Interfaces:**
- Consumes: `node-standards.refactoring` revision 3.
- Produces: revision 4 with the category remedy governed only by `references/blueprint/schema.json`.

- [ ] **Step 1: Add the focused authority-boundary test**

Add after `test_standard_validates_and_has_explicit_canonical_path`:

```python
def test_category_remedy_uses_schema_without_documentation_dependency():
    document = load_standard()
    remedy = semantic_nodes(document)[
        "skill-refactoring.remedies.declare-fix-category"
    ]

    assert remedy["steps"][0]["instruction"] == (
        "Use a typed enum value from `references/blueprint/schema.json`."
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m pytest -q tests/test_skill_refactoring_standard.py::test_category_remedy_uses_schema_without_documentation_dependency
```

Expected: FAIL because the instruction still names `docs/skill-blueprints.md`.

- [ ] **Step 3: Apply the minimal semantic change**

In `refactoring.standard.yaml`, change `revision: 3` to `revision: 4` and replace the `set-category` instruction with:

```yaml
instruction: "Use a typed enum value from `references/blueprint/schema.json`."
```

In the source map, change the three `expected` values for source lines 20, 21, and 22 to that same sentence. Preserve the immutable source text and digest.

- [ ] **Step 4: Run the focused file and inspect scope**

```bash
python3 -m pytest -q tests/test_skill_refactoring_standard.py
git diff --check
git diff -- references/node-standards/refactoring.standard.yaml tests/fixtures/standards/skill-refactoring-source-map.yaml tests/test_skill_refactoring_standard.py
```

Expected: focused tests pass; only the revision, sentence, mapped expectations, and test changed.

---

### Task 2: Propagate the pinned standard closure

**Files:**
- Modify: `references/node-standards/node.standard.yaml`
- Modify: `references/node-standards/behavioral-source.standard.yaml`
- Modify: `references/node-standards/instruction-node.standard.yaml`
- Modify: `references/node-standards/module.standard.yaml`
- Modify: `references/node-standards/python-node.standard.yaml`
- Modify: `references/node-standards/instruction-module.standard.yaml`
- Modify: `references/node-standards/instruction-behavioral-source.standard.yaml`
- Modify: `references/node-standards/python-behavioral-source.standard.yaml`
- Modify: `references/node-standards/python-module.standard.yaml`

**Interfaces:**
- Consumes: exact bytes and revision 4 of `node-standards.refactoring`.
- Produces: a fresh acyclic import closure with exact revision and digest pins.

- [ ] **Step 1: Update direct refactoring dependents**

Run `sha256sum references/node-standards/refactoring.standard.yaml`. Prefix the digest with `sha256:` and update both `refactoring` imports:

```text
node.standard.yaml: revision 11 -> 12; refactoring import 3 -> 4
instruction-module.standard.yaml: revision 15 -> 16; refactoring import 3 -> 4
```

- [ ] **Step 2: Update direct node dependents**

Run `sha256sum references/node-standards/node.standard.yaml`. Prefix the digest and update all four `node` imports:

```text
behavioral-source.standard.yaml: revision 11 -> 12; node import 11 -> 12
instruction-node.standard.yaml: revision 12 -> 13; node import 11 -> 12
module.standard.yaml: revision 11 -> 12; node import 11 -> 12
python-node.standard.yaml: revision 15 -> 16; node import 11 -> 12
```

- [ ] **Step 3: Finish leaf dependents**

Compute SHA-256 for `behavioral-source.standard.yaml`, `instruction-node.standard.yaml`, `module.standard.yaml`, and `python-node.standard.yaml`; update every matching import digest and revision:

```text
instruction-behavioral-source.standard.yaml: revision 12 -> 13
  behavioral-source import 11 -> 12; instruction-node import 12 -> 13
instruction-module.standard.yaml: retain revision 16
  module import 11 -> 12; instruction-node import 12 -> 13
python-behavioral-source.standard.yaml: revision 15 -> 16
  behavioral-source import 11 -> 12; python-node import 15 -> 16
python-module.standard.yaml: revision 16 -> 17
  module import 11 -> 12; python-node import 15 -> 16
```

- [ ] **Step 4: Validate all ten changed standards**

```bash
for standard in \
  references/node-standards/refactoring.standard.yaml \
  references/node-standards/node.standard.yaml \
  references/node-standards/behavioral-source.standard.yaml \
  references/node-standards/instruction-node.standard.yaml \
  references/node-standards/module.standard.yaml \
  references/node-standards/python-node.standard.yaml \
  references/node-standards/instruction-module.standard.yaml \
  references/node-standards/instruction-behavioral-source.standard.yaml \
  references/node-standards/python-behavioral-source.standard.yaml \
  references/node-standards/python-module.standard.yaml; do
  python3 references/standards/validate_standard_v6.py "$standard" --root . || exit 1
done
```

Expected: ten `validation passed` lines.

- [ ] **Step 5: Inspect the closure diff**

```bash
git diff --check
git diff -- references/node-standards tests/fixtures/standards/skill-refactoring-source-map.yaml tests/test_skill_refactoring_standard.py
```

Expected: one semantic sentence/test change plus mechanical revision and digest propagation.

---

### Task 3: Validate and commit the integrated content

**Files:**
- Modify: `docs/superpowers/plans/2026-08-08-officina-docs-integration.md` to mark completed steps.

**Interfaces:**
- Consumes: the documentation move and fresh standards closure.
- Produces: one clean tested content commit.

- [ ] **Step 1: Run focused tests**

```bash
python3 -m pytest -q \
  tests/test_skill_refactoring_standard.py \
  tests/test_standard_extractor.py \
  tests/test_standard_v6.py \
  tests/validate_standard_documents.py \
  tests/test_docs_catalog.py \
  tests/validate_documentation_validators.py \
  tests/test_docstrings_validator.py
```

Expected: PASS with no failures.

- [ ] **Step 2: Run generated-preview and repository validation**

```bash
python3 scripts/generate-previews.py --target readme
python3 validators/runner.py
git diff --check
git status --short
```

Expected: validators pass; only planned files are tracked changes; `_build/README-preview.html` remains ignored.

- [ ] **Step 3: Mark plan steps complete and stage exact files**

Change completed checkboxes in this plan to `[x]`, then stage only:

```bash
git add \
  docs/superpowers/plans/2026-08-08-officina-docs-integration.md \
  references/node-standards/refactoring.standard.yaml \
  references/node-standards/node.standard.yaml \
  references/node-standards/behavioral-source.standard.yaml \
  references/node-standards/instruction-node.standard.yaml \
  references/node-standards/module.standard.yaml \
  references/node-standards/python-node.standard.yaml \
  references/node-standards/instruction-module.standard.yaml \
  references/node-standards/instruction-behavioral-source.standard.yaml \
  references/node-standards/python-behavioral-source.standard.yaml \
  references/node-standards/python-module.standard.yaml \
  tests/fixtures/standards/skill-refactoring-source-map.yaml \
  tests/test_skill_refactoring_standard.py
```

- [ ] **Step 4: Commit and verify**

```bash
git commit -m "docs(standards): close Officina documentation move"
git status --short --branch
git log -4 --oneline
```

Expected: commit hook passes; the branch is clean with the two original documentation commits, design commit, and standards-closure commit.

---

### Task 4: Fast-forward master and clean up

**Files:**
- No file edits.

**Interfaces:**
- Consumes: clean validated `integrate/officina-docs`.
- Produces: local `master` containing the integration.

- [ ] **Step 1: Fast-forward from the primary repository worktree**

```bash
git merge --ff-only integrate/officina-docs
```

Expected: no merge commit or conflict.

- [ ] **Step 2: Verify the merged result**

```bash
git status --short --branch
python3 -m pytest -q tests/test_skill_refactoring_standard.py tests/validate_standard_documents.py tests/test_docs_catalog.py
```

Expected: clean `master`; all focused tests pass.

- [ ] **Step 3: Remove temporary integration state**

```bash
git worktree remove /tmp/ai-officina-docs-integration
git branch -d integrate/officina-docs
git branch -d worktree-human-audit-doc
git worktree prune
```

Expected: only the main worktree remains and both integrated branch refs are absent.
