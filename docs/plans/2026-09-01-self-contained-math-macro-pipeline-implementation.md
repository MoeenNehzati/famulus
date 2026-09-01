# Self-Contained Math Macro Pipeline Implementation Plan

**Goal:** Make every completed math-dependency graph JSON self-contained with the relevant TeX macro definitions, while keeping the generic HTML renderer TeX-blind and able to render every schema-valid macro form correctly.

**Architecture:** The skill-owned extraction layer reads the root TeX project, computes the relevant recursive macro closure, and embeds it in the canonical graph payload at `renderer_dependencies[].configuration.macros`. The generic visualization layer accepts only JSON, normalizes schema-supported macro encodings at its MathJax adapter boundary, and renders them. The docs publisher remains a generic JSON-to-HTML consumer.

**Tech stack:** Python 3, JSON Schema draft-07, MathJax 3.2 (offline bundle), pytest, Playwright/Chromium browser checks, Famulus registered interfaces and generated blueprints.

**Spec:** The user's requested responsibility boundary and the existing `graph_specification.schema.json` v2 contract. No schema version bump is planned unless implementation proves the existing macro field cannot express a required definition.

## Global constraints

- Preserve all unrelated dirty work and all frozen/gold math-dependency artifacts.
- The extractor is the only layer allowed to open `.tex`, `.sty`, or `.cls` sources or invoke TeX lookup tools such as `kpsewhich`. Here “extractor” includes its skill-owned macro reader, label reader, and finalizer support; it excludes the JSON graph builder, generic renderer, and docs publisher.
- A completed extraction is one schema-valid, render-ready JSON artifact. HTML generation must not need the source tree, a TeX entrypoint, or a macro sidecar.
- Reuse `_tex_macro_reader.py`, the existing schema location, `GraphArtifactWriter`, `ElkHtmlRenderer`, and the existing MathJax lifecycle. Do not add a parallel macro format or renderer-specific extraction path.
- Do not hard-code macro names, definitions, document-specific allowlists, or substitutions. The mechanism must derive its roots from commands used in graph-visible text and resolve their definitions from the supplied document's TeX dependency closure.
- Keep `docs_tooling/site.py` generic: it maps a graph name to canonical JSON and renders that JSON.
- Keep the renderer contract to one self-contained JSON payload. If the generic CLI's `--macro-file` compatibility option remains, isolate it as a deprecated outer preprocessor that merges and validates a self-contained payload before calling the renderer; it is not a second renderer input.
- Treat conflicting macro definitions as errors with the macro name and both sources identified. Do not silently choose one.
- Run generated-file regeneration only through registered Famulus interfaces. Do not edit generated blueprint blocks by hand.
- Do not commit unless the user separately authorizes a commit.

## Task 0: Protect the existing working state

**Files:** No repository changes.

- [ ] Record `git status --short` and save baseline diffs for every already-dirty path this work must touch, especially `src/officina/visualization/html_renderer/README.md`, `tests/test_visualization_inspector_and_bezier_browser.py`, and the untracked `extraction-latest-relations.html`.
- [ ] Treat those baseline hunks as preserved work: edit only task-owned hunks, and compare the final per-file diffs against the saved baselines. If clean separation is not possible, use an isolated worktree and explicitly transfer the required baseline changes; do not silently omit or overwrite them.
- [ ] Record SHA-256 digests for every tracked gold/provenance file listed in the benchmark README and for tracked `results/final-gold-shortcuts.html`. Recheck those exact digests after regeneration.

## Verified starting point

- `graph_specification.schema.json` v2 already stores MathJax macros under `renderer_dependencies[].configuration.macros`; it accepts string definitions and both supported parameterized tuple orders.
- The current published source JSON, `skills/math-dependency-graph/assets/inference-from-random-restarts/results/extraction-latest.json`, has no embedded macros. Its metadata records the resulting macro gap.
- The skill currently extracts a macro sidecar during rendering, after the canonical JSON has already been written. This lets HTML receive information absent from the JSON and violates the requested boundary.
- `_tex_macro_reader.py` is the existing reusable parser and recursive dependency-closure implementation. Its current behavior needs focused regression coverage before it becomes the canonical finalization path.
- `html_renderer/dependencies.py` embeds MathJax configuration without reading TeX. It currently forwards the schema's integer-first parameterized macro tuple unchanged even though MathJax expects replacement-first ordering.
- `docs_tooling/site.py` already renders the tracked canonical JSON through the generic renderer and should not gain skill-specific or TeX-specific logic.

## Target data flow

```text
root TeX project + semantic graph draft
                  |
                  v
math-dependency extraction finalizer
  - resolve labels/presentation owned by the skill
  - find macros used by graph text
  - compute recursive relevant macro closure
  - normalize package-command definitions
  - embed macros in schema-native renderer_dependencies
  - validate and write canonical JSON
                  |
                  v
self-contained graph JSON
          |                    |
          v                    v
generic HTML renderer     generic docs publisher
  - no TeX access           - no TeX access
  - normalize JSON macro    - delegates to renderer
    tuples for MathJax
```

## Task 1: Lock the self-contained extraction contract with failing tests

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py`
- Create: `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py`
- Create: `skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py`
- Inspect/modify if required: `src/officina/visualization/graph_specification.schema.json`

- [ ] Add a synthetic TeX fixture with arbitrary custom names not present in the benchmark document. Its graph-visible expression must exercise direct, nested, zero-argument, required-argument, and optional-argument macros so the test cannot pass through document-specific special cases.
- [ ] Add a failing finalizer test that starts with a semantic graph draft and a root TeX entrypoint, then asserts that the written canonical JSON contains only the relevant recursive closure under the existing MathJax dependency configuration.
- [ ] Assert that definitions needed only by another relevant definition are retained and unrelated project macros are excluded.
- [ ] Add a second synthetic document with a disjoint custom macro vocabulary and prove the same extractor code handles it without configuration changes. Assert that neither document's names occur as constants in production extraction or renderer code.
- [ ] Assert that the canonical JSON passes `graph_specification.schema.json` validation and can be copied away from the TeX project without losing any render dependency.
- [ ] Add failure cases for an unresolved used project macro, a cyclic/unsupported relevant definition, a genuinely conflicting pre-existing macro value, and duplicate MathJax dependency entries. Require actionable diagnostics; do not permit silent omission. Also test that semantically identical legacy/native tuples are not treated as a conflict.
- [ ] Add a graph-builder regression test proving the builder/render step accepts no TeX entrypoint, macro sidecar, or label sidecar and produces the same HTML from the copied canonical JSON.
- [ ] Run the focused tests and confirm they fail for the intended missing finalizer/boundary behavior:

  ```bash
  pytest -q skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py skills/math-dependency-graph/_rtx/tests/test_graph_builder.py
  ```

## Task 2: Consolidate reusable macro extraction and canonical finalization

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/_tex_macro_reader.py`
- Create: `skills/math-dependency-graph/_rtx/_extraction_finalizer.py`
- Create: `skills/math-dependency-graph/_rtx/blueprints/rtx-extraction-finalizer.yaml`
- Modify: `skills/math-dependency-graph/_rtx/blueprint.yaml`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py`
- Modify: `skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py`

- [ ] Introduce one public macro-extraction function used by all skill paths. Its contract should be structurally equivalent to:

  ```python
  def extract_renderable_macros(
      *, tex_entrypoint: Path, graph_text: Iterable[str]
  ) -> dict[str, str | list[object]]:
      """Return the normalized recursive closure of project macros used by graph text."""
  ```

- [ ] Implement it by composing the existing include/package traversal, definition parsing, recursive closure, and package-command normalization. Remove the current divergence where the macro-reader CLI normalizes definitions but `_graph_builder.prepare_macro_file()` does not.
- [ ] Preserve source path and location in an internal macro-definition record while parsing and merging. Use that record for duplicate/conflict diagnostics, then project only normalized values into the schema macro map returned by `extract_renderable_macros()`.
- [ ] Verify and restore, where tests expose regressions, support for local `\input`/`\include`, local packages/classes, TeX-distribution packages/classes, aliases, legacy encodings, optional arguments, and recursive macro references. Keep only behavior required to resolve macros reachable from graph-visible text.
- [ ] Add a finalizer API with explicit inputs and one output, for example:

  ```python
  def finalize_extraction(
      *, draft_path: Path, tex_entrypoint: Path, output_path: Path,
      label_map_path: Path | None = None,
  ) -> None:
      """Write a validated, self-contained canonical graph JSON artifact."""
  ```

- [ ] Move `apply_presentation_base()`, `apply_label_numbering()`, and `resolve_label_references()` out of `_graph_builder.py` into pure helpers owned by `_extraction_finalizer.py`. Remove their file-writing side effects, reuse their existing behavior in the finalizer, and do not keep duplicate builder copies.
- [ ] Have the finalizer gather graph-visible strings from entities, relations, labels, and other rendered text; extract their relevant macro closure; merge it into the existing MathJax dependency; apply those pure label/presentation helpers; validate with the existing draft-07 validator; write a sibling temporary file; and replace the canonical JSON atomically. Do not rely on `GraphArtifactWriter` to provide validation or atomicity it does not currently provide.
- [ ] Reuse the existing dependency/configuration merge helpers where their precedence is appropriate. Add one small skill-owned helper only if the generic helpers cannot report definition conflicts.
- [ ] Normalize both embedded and extracted definitions to MathJax-native semantic form before conflict comparison. Semantically identical legacy/native tuples such as `[2, "#1+#2"]` and `["#1+#2", 2]` are accepted and written once in native form; genuinely differing definitions fail. Preserve unrelated renderer dependencies and MathJax configuration keys.
- [ ] Enforce exactly one MathJax renderer dependency in finalized output: create it when absent, merge it when exactly one exists, and fail with indexed actionable diagnostics when multiple `id: mathjax` entries exist. Do not rely on schema `uniqueItems`, which permits differently configured duplicates that the renderer later rejects.
- [ ] Ensure the finalized payload uses MathJax-native replacement-first tuples for new parameterized definitions. Legacy integer-first tuples remain schema-compatible input for the renderer, not new extractor output.
- [ ] Register a versioned executable finalization interface. Its arguments must name the semantic draft, root TeX entrypoint, optional label map, and canonical output explicitly.
- [ ] Run the focused tests to green:

  ```bash
  pytest -q skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py
  ```

## Task 3: Make the generic renderer honor the complete schema without TeX knowledge

**Files:**

- Modify: `src/officina/visualization/html_renderer/dependencies.py`
- Inspect/modify for compatibility shim only: `src/officina/visualization/base_renderer_cli.py`
- Modify: `tests/test_visualization_renderer_cli.py`
- Modify: `tests/test_visualization_inspector_and_bezier_browser.py`
- Modify only for description clarity: `src/officina/visualization/graph_specification.schema.json`
- Modify: `src/officina/visualization/html_renderer/README.md`

- [ ] Add a failing unit test for the JSON-to-MathJax adapter covering strings, replacement-first tuples, integer-first legacy tuples, and optional-default tuples. Invalid values must still be rejected by schema validation or by an explicit adapter error.
- [ ] Add a failing browser test using nested and parameterized macros. For both tuple orders, render a deliberately simple definition such as `Pair: [2, "#1+#2"]`/`Pair: ["#1+#2", 2]`, wait for `MathJax.startup.promise` and the renderer typesetting queue, and assert from assistive MathML or SVG content that `\Pair{a}{b}` represents `a+b`, not `2` or a literal command. Also assert no `mjx-merror` and no unresolved-TeX banner.
- [ ] Implement a single normalizer at the final renderer adapter boundary, conceptually:

  ```python
  def normalize_mathjax_macros(macros: Mapping[str, object]) -> dict[str, object]:
      """Convert every schema-supported macro encoding to MathJax-native form."""
  ```

- [ ] Pass the normalized mapping to the existing pinned offline MathJax loader. Do not add filesystem, TeX parsing, skill dispatch, or network behavior to the renderer.
- [ ] Keep the existing dynamic typesetting and unresolved-command diagnostics unchanged except where tests demonstrate a necessary integration fix.
- [ ] If useful, clarify the schema descriptions to call replacement-first tuples canonical and integer-first tuples legacy-compatible. Do not change schema version or add a second macro field.
- [ ] Document that renderer inputs must be self-contained JSON. Either remove `--macro-file` after searching consumers, or retain it only as a deprecated CLI preprocessor that first creates and validates one self-contained payload; it must not enter the renderer API or the math-dependency workflow.
- [ ] Run renderer unit and browser tests:

  ```bash
  pytest -q tests/test_visualization_renderer_cli.py tests/test_visualization_inspector_and_bezier_browser.py
  ```

## Task 4: Refactor the skill workflow so rendering is JSON-only

**Files:**

- Modify: `skills/math-dependency-graph/_rtx/_graph_builder.py`
- Modify: `skills/math-dependency-graph/_rtx/blueprints/rtx-graph-builder.yaml`
- Modify: `skills/math-dependency-graph/blueprints/instructions-extract.yaml`
- Modify: `skills/math-dependency-graph/_rtx/blueprint.yaml`
- Modify: `skills/math-dependency-graph/blueprint.yaml`
- Modify: handwritten workflow text in `skills/math-dependency-graph/SKILL.md`; regenerate only its marker-bounded interface block
- Modify: `skills/math-dependency-graph/_rtx/tests/test_graph_builder.py`
- Modify relevant interface/registration tests found by `rg -n "build-math-dependency|extract-math-depend|tex-entry|macro-file|label-file|refresh-macros" skills tests src`

- [ ] Change the extraction instruction so its required completion sequence is: write semantic draft, invoke the registered finalizer with the root TeX entrypoint, validate canonical JSON, then optionally render that JSON.
- [ ] Make the distinction between the included content file and the TeX project entrypoint explicit. For the bundled example, macro traversal must begin at `source/main.tex`, not `source/appendix.tex`.
- [ ] Reduce `_graph_builder.py` to consuming validated canonical JSON unchanged and delegating to the existing `GraphArtifactWriter(ElkHtmlRenderer())` path. Permit only explicitly transient render-view reduction that does not mutate or rewrite canonical JSON.
- [ ] Remove skill-builder arguments and code paths for `--tex-entry`, `--macro-file`, `--label-file`, `--refresh-macros`, macro-sidecar generation, label resolution, and direct `_tex_macro_reader` calls. Search all consumers before changing the registered interface and update them in the same task.
- [ ] Search consumers of the generic base renderer CLI's JSON `--macro-file`. Remove it if unused; otherwise retain it only as the deprecated preprocessor defined in Task 3. It must never inspect TeX or become a second renderer input.
- [ ] Delete the builder copies of presentation/label transformations moved in Task 2. The builder may apply only transient HTML layout/reduction that does not alter the canonical payload.
- [ ] Bump only interfaces/modules whose contracts actually change. Regenerate the skill blueprint via `famulus:regenerate-blueprints`; do not hand-edit generated interface blocks.
- [ ] Run the skill tests and repository registration checks:

  ```bash
  pytest -q skills/math-dependency-graph/_rtx/tests
  ./repo_checks.py --suite validators --jobs 1
  ```

## Task 5: Regenerate and test the published math-dependency example

**Files:**

- Modify: `skills/math-dependency-graph/assets/inference-from-random-restarts/results/extraction-latest.json`
- Create if generated for local review: `_build/math-dependency/extraction-latest-relations.html`
- Modify: `tests/test_docs_site.py`
- Do not modify: frozen/gold files listed by `skills/math-dependency-graph/assets/inference-from-random-restarts/README.md`

- [ ] Preflight the ignored local source before regeneration. Require `source/main.tex` to exist with SHA-256 `4227cceda2816a135f2aa27f1dff44ab3ead5fb96aba24f2ba33f26561e5badb`, and record that it came from the archive whose README SHA-256 is `3ae8dac2d58786a80761999a80d0304e19af74d7ee2132d714e3a7a043b7ab96`. A clean checkout does not contain this ignored source; provide it to an isolated lane without adding it to Git if such a lane is used.
- [ ] Run the updated finalizer with `results/extraction-latest.json` (the skill-produced, non-gold graph) as the draft input and `source/main.tex` as the root entrypoint, writing first to a temporary candidate. Explicitly forbid `semantic-gold.json`, `final-gold.json`, or any other frozen artifact as input or output.
- [ ] Verify generically that the temporary candidate is schema-valid and that every custom command reachable from its graph-visible text is either represented by the embedded recursive macro closure or classified as a supported MathJax/TeX primitive. Then verify the current benchmark expressions render and remove the obsolete `metadata.macro_gap`; named benchmark macros are regression evidence, not extraction configuration.
- [ ] Add a benchmark-only size/scope guard comparing the temporary candidate with the original `extraction-latest.json`: the serialized macro map must be at most 4 KiB, the candidate may grow by at most 8 KiB from the verified 77,454-byte baseline, and the parsed graphs must be identical after removing the candidate's macro configuration and the baseline's obsolete `metadata.macro_gap`. These thresholds detect accidental over-extraction for this fixture and must not impose a general macro-count or payload-size limit on future documents.
- [ ] Add a docs-site regression proving the publisher consumes this canonical JSON unchanged and produces nonempty HTML with its embedded macro configuration. Keep `docs_tooling/site.py` unchanged unless the test identifies a generic publication defect.
- [ ] Add or extend a browser regression that can render a supplied canonical candidate with the generic renderer, waits for MathJax and the renderer queue, and checks the semantic MathML/SVG output of representative affected labels in addition to `mjx-container`, no `mjx-merror`, and no unresolved-TeX marker. Run it against the temporary candidate before promotion and the tracked path afterward.
- [ ] Render and browser-check the temporary candidate first. After its schema, semantic-delta, size, browser, and immutable-digest guards pass, atomically replace tracked `results/extraction-latest.json` with that candidate. Never overwrite the existing untracked `results/extraction-latest-relations.html`; write review HTML only under `_build/math-dependency/`.
- [ ] Build the docs site and inspect the generated page locally:

  ```bash
  ./scripts/docs-site.py build
  ```

- [ ] Record and report the exact generated HTML path. The expected path should be verified from the build output rather than assumed.
- [ ] Verify both generated stages: `_build/docs-site/source/graphs/math-dependency.html` and `_build/docs-site/site/graphs/math-dependency.html`. Run this only in the protected implementation lane because docs generation may update other generated files.

## Task 6: Verification and scope audit

**Files:** No new production files expected.

- [ ] Run focused schema, extractor, renderer, browser, and publication tests:

  ```bash
  pytest -q skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py skills/math-dependency-graph/_rtx/tests/test_graph_builder.py tests/test_visualization_renderer_cli.py tests/test_visualization_inspector_and_bezier_browser.py tests/test_docs_site.py
  ```

- [ ] Run the shared repository gate for the affected paths:

  ```bash
  ./repo_checks.py --task tests:shared --selector skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py --selector skills/math-dependency-graph/_rtx/tests/test_mathjax_macros.py --selector skills/math-dependency-graph/_rtx/tests/test_graph_builder.py --selector tests/test_visualization_renderer_cli.py --selector tests/test_docs_site.py --jobs 1
  ```

- [ ] Run the real-browser test through the repository's browser gate in a host-capable environment:

  ```bash
  ./repo_checks.py --task tests:browser --selector tests/test_visualization_inspector_and_bezier_browser.py --jobs 1
  ```

- [ ] Run the repository validators and broader precommit gate required by changed schema or interfaces:

  ```bash
  ./repo_checks.py --suite validators --jobs 1
  ./repo_checks.py --suite precommit --repository-view working
  ```

- [ ] If browser execution fails because of sandbox/host capability, rerun the defined browser gate in a host-capable environment and report that distinction; do not classify the capability failure as a renderer defect.
- [ ] Search for responsibility leaks:

  ```bash
  rg -n "tex-entry|refresh-macros|_tex_macro_reader|kpsewhich|\\.tex|\\.sty|\\.cls" src/officina/visualization docs_tooling skills/math-dependency-graph/_rtx/_graph_builder.py
  ```

  The acceptable result is documentation/test text plus extractor-owned code only; no generic renderer, docs publisher, or JSON-only graph-builder TeX access.
- [ ] Inspect `git status --short`, changed paths, and the final diff. Confirm frozen gold files and unrelated dirty files are untouched.
- [ ] Recompute all benchmark gold/provenance digests and compare them byte-for-byte with the Task 0 baseline before declaring completion.
- [ ] Ask independent reviewers to audit the implemented diff for extractor/renderer separation, reuse, simplicity, publication reproducibility, and test sufficiency. Resolve concrete findings and rerun affected gates until green.

## Acceptance criteria

- A math-dependency extraction is incomplete unless its canonical schema-valid JSON contains every project macro required to render its graph-visible text, including recursive dependencies.
- The canonical JSON alone can be moved to a TeX-free directory and rendered successfully.
- The renderer accepts both macro tuple encodings allowed by the schema and adapts them to MathJax without parsing TeX.
- The generic docs publisher renders the canonical JSON without skill-specific logic.
- Two synthetic documents with disjoint, arbitrary custom macro vocabularies render without code or configuration changes, demonstrating that extraction is name-agnostic.
- The regenerated non-gold example renders every graph-used custom macro—including the currently failing examples—without MathJax errors or unresolved-command diagnostics; those names appear only in fixture data/assertions, never production logic.
- No frozen/gold artifact, unrelated dirty file, or hand-generated blueprint block is changed.
