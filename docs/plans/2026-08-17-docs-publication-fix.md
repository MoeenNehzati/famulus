# Documentation Publication Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the production Pages build, use the repository README as the website landing page, and publish and verify the reviewed documentation surface.

**Architecture:** The site assembler will treat the root `README.md` as the MkDocs `index.md`, retain `docs/README.md` as `documentation/index.md`, and continue publishing every regular file below `docs/` except the `docs/plans/` subtree. The default graph builder will be imported from its concrete owner module after the Officina relocation.

**Tech Stack:** Python 3.11+, pytest, MkDocs, GitHub Actions, GitHub Pages, GitHub CLI.

## Global Constraints

- Preserve all pre-existing uncommitted math-dependency-graph and visualization work.
- Publish every regular file under `docs/` except files below `docs/plans/`.
- Do not add release, upgrade, or stable-support documentation in this change.
- Stage and commit only the documentation publication plan, assembler, tests, and documentation-system description.
- Push the authorized committed `master` history without force.

---

### Task 1: Production graph-builder import

**Files:**
- Modify: `tests/test_docs_site.py`
- Modify: `docs_tooling/site.py`

**Interfaces:**
- Consumes: `officina.visualization.from_blueprint.visualizer.build_blueprint_graph`
- Produces: `assemble_site(..., graph_builder=None)` resolving the production graph builder from its concrete owner module.

- [x] **Step 1: Write the failing test**

Add a test that monkeypatches `officina.visualization.from_blueprint.visualizer.build_blueprint_graph`, invokes `assemble_site` without a supplied graph builder, and asserts that the fake builder writes `graphs/blueprint/repository.html`.

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest -o 'pythonpath=. src' -q tests/test_docs_site.py::test_assemble_site_resolves_default_graph_builder_from_visualizer`

Expected: FAIL with the current `ImportError` from `officina.visualization.from_blueprint`.

- [x] **Step 3: Implement the minimal import correction**

Change the lazy import in `assemble_site` to:

```python
from officina.visualization.from_blueprint.visualizer import (
    build_blueprint_graph,
)
```

- [x] **Step 4: Run the focused test**

Run the test from Step 2 and expect PASS.

### Task 2: README-backed website homepage

**Files:**
- Modify: `tests/test_docs_site.py`
- Modify: `docs_tooling/site.py`
- Modify: `docs/contributors/documentation-system.md`

**Interfaces:**
- Consumes: repository root `README.md` and the public `docs/` file mapping.
- Produces: site `index.md` from root `README.md`; site `documentation/index.md` from `docs/README.md`.

- [x] **Step 1: Write failing mapping assertions**

Update the site-assembly test fixture so root `README.md` links to public docs. Assert:

```python
assert (output / "index.md").read_text(encoding="utf-8").startswith("# Repository")
assert (output / "documentation" / "index.md").read_text(encoding="utf-8").startswith("# Documentation")
```

Also assert root-README links to `docs/...` are rewritten to the corresponding site routes and `output / "plans"` remains absent.

- [x] **Step 2: Run the focused test to verify it fails**

Run: `pytest -o 'pythonpath=. src' -q tests/test_docs_site.py::test_assemble_site_publishes_docs_tree_except_plans`

Expected: FAIL because `docs/README.md`, rather than root `README.md`, currently owns `index.md`.

- [x] **Step 3: Implement the mapping**

Build the source mapping from both repository root and docs root:

```python
published = {root.joinpath("README.md").resolve(): Path("index.md")}
```

Map `docs/README.md` to `Path("documentation/index.md")`; retain the existing relative destinations for every other published docs file and the exact `docs/plans/` exclusion.

- [x] **Step 4: Update publishing documentation**

Document the README homepage, documentation-index route, recursive docs publication rule, and plans exclusion in `docs/contributors/documentation-system.md`.

- [x] **Step 5: Run focused documentation tests**

Run: `pytest -o 'pythonpath=. src' -q tests/test_docs_site.py tests/validate_documentation_validators.py`

Expected: all tests pass.

### Task 3: Build, commit, publish, and verify

**Files:**
- Commit only the files named in Tasks 1 and 2 plus this plan.
- Do not stage unrelated working-tree files.

**Interfaces:**
- Consumes: committed `master`, `.github/workflows/pages.yml`, GitHub Pages, GitHub repository metadata.
- Produces: one scoped commit, updated `origin/master`, successful Pages deployment, reachable sitemap routes, and configured repository advertising metadata.

- [x] **Step 1: Run local verification**

Run focused tests, `./repo_checks.py --suite validators`, and `./scripts/docs-site.py build`. Record any unrelated validator failure separately; the production site build must exit zero.

- [ ] **Step 2: Inspect and commit exact scope**

Stage only:

```text
docs/plans/2026-08-17-docs-publication-fix.md
docs/contributors/documentation-system.md
docs_tooling/site.py
tests/test_docs_site.py
```

Commit with message `fix(docs): restore Pages publication`.

- [ ] **Step 3: Push without force**

Push `master` to `origin/master`, publishing the 71 previously authorized commits plus the new scoped commit.

- [ ] **Step 4: Set repository metadata**

Set description to `Cross-LLM skills for personal planning, research writing, mathematical review, and assistant development.`, homepage to `https://moeennehzati.github.io/famulus/`, and topics to `codex`, `claude-code`, `llm-skills`, `productivity`, `research`, `latex`, `automation`, and `ai-assistant`.

- [ ] **Step 5: Verify Pages and crawl routes**

Wait for the Pages workflow for the pushed commit to succeed. Fetch `sitemap.xml`, request every listed URL, and require HTTP 200. Confirm the homepage contains the root README's `What It Is Good At` and `Quick Start` headings and `/documentation/` contains `Documentation Index`.
