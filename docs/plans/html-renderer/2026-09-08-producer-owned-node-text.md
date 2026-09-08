# Producer-Owned Node Text Implementation Plan

**Goal:** Make every HTML graph cell display the title and subtitle supplied by its producer, while removing the core renderer's current `type + ref` subtitle synthesis. Repository graphs should show only the local node name and no subtitle; other extractors should state their own display policy explicitly.

**Architecture:** Keep the graph schema's established visible-title contract: `label` is an optional producer-authored override and `short_title` is the canonical visible title. Add an optional string `subtitle`. The HTML renderer may select `label ?? short_title`, but it must not derive visible text from `id`, `type`, or `ref`. Producers own `short_title`/`label` and `subtitle`; identifiers, types, references, and long `title` values remain semantic or inspector data.

**Scope:** This plan covers every first-party canonical graph producer found under `src/officina/visualization/from_*` plus the math-dependency finalizer that turns extracted drafts into canonical graph JSON. It does not change graph topology, layout, colors, edge attenuation, inspectors, filtering, Graphviz/DOT output, or frozen math gold artifacts.

## Contract decisions

- `short_title` remains required and is the ordinary visible title. Renaming it to `title` would collide with the existing long-title metadata field and cause unrelated schema and consumer churn.
- `label`, when present, remains an explicit producer-authored visible-title override. This is not renderer synthesis.
- `subtitle` is optional and must be a string when present. An absent subtitle and `subtitle: ""` both render as no subtitle.
- The renderer must not fall back from a missing subtitle to `type`, `ref`, `id`, or any concatenation of those fields.
- Empty subtitles should not leave a visible blank subtitle row or reserve its height. Measurement and final rendering must use the same title/subtitle resolution.
- `type`, `kind`, `category`, `ref`, `id`, and long `title` remain available to legends, filters, search, and inspectors.
- Producer policies:
  - repository blueprint: local path segment as `short_title`; empty `subtitle`;
  - blueprint presentation group: configured value label as `short_title`; configured facet label as `subtitle`;
  - docstring graph: existing concise symbol/name as `short_title`; human-readable entity kind as `subtitle`;
  - Rutter graph: evolution id as `short_title`; human-readable evolution kind as `subtitle`;
  - math dependency graph: mathematical display name as `short_title`/`label`; numbered kind such as `theorem 4.3` as `subtitle`, computed by the finalizer after reference numbering.

## Complete producer and consumer inventory

| Surface | Current behavior | Required adjustment |
|---|---|---|
| `from_blueprint/payload_builder.py` | Already derives local module/source/interface titles, but carries full ids in `title` and `ref`; the renderer repeats those ids in subtitles. | Emit `subtitle: ""` for canonical and out-of-scope boundary entities. Preserve ids, `title`, and `ref` for semantics/inspection. |
| `from_blueprint/presentation_nodes.py` | Emits the value label as `short_title`; runtime derives the facet label. | Emit the facet label as `subtitle` so presentation-node text is producer-owned too. |
| `from_docstring/payload_builder.py` | Central `_build_entity` emits title fields but no subtitle. | Add one centralized, human-readable type subtitle; do not repeat the full id/ref. |
| `from_rutter/payload_builder.py` | Emits evolution id as both `short_title` and `label`; renderer supplies the kind subtitle. | Emit the humanized evolution kind explicitly as `subtitle`. |
| `math-dependency-graph/_extraction_finalizer.py` | Resolves TeX references and fills `ref`; renderer currently combines `type + ref`. | After numbering, fill only missing subtitles from the finalized type/ref and preserve explicit subtitles, including empty strings. Include subtitle in rendered-text TeX resolution. |
| `html_renderer/runtime/bootstrap.js` | Measures nodes using `type + ref`. | Measure only producer-supplied visible title/subtitle. |
| `html_renderer/runtime/node_renderer.js` | Draws canonical cells using `type + ref`. | Draw the exact same resolved title/subtitle used for measurement and omit an empty subtitle element. |
| `html_renderer/runtime/presentation_nodes.js` | Injects a runtime `facetLabel` subtitle. | Carry producer-supplied `node.subtitle`; append `collapsed` only as transient UI state. |
| `graph_specification.schema.json` and `graph.py` | Do not define or validate entity subtitles; presentation nodes reject unknown properties. | Define the optional field on both node kinds and reject non-string values consistently. |
| Direct canonical JSON loaded by `BaseVisualizer` | Relies implicitly on renderer fallback today. | No migration shim: missing subtitle means blank. Producers that want a subtitle must state it. |
| `from_docstring/renderer.py` | Separate Graphviz/DOT renderer labels nodes by id. | Leave unchanged; it is not the core HTML cell renderer. |
| Math `semantic-gold.json` / `final-gold.json` | Frozen evaluation fixtures. | Never rewrite them. Test transformations on copies/fixtures and regenerate only ignored working output for visual acceptance. |

The repository-wide source search found no other first-party `short_title` payload producers. `payload.py`, `base_renderer_cli.py`, and the math graph builder transport or normalize already-produced payloads and need no text policy.

## Three-dimensional LOC budget

“3D” means added, deleted, and net lines. Hard churn is additions plus deletions. Budgets below are incremental to the current worktree snapshot; pre-existing renderer simplification changes are excluded. Moving budget between tasks requires editing this plan first.

| Task | Add | Delete | Net | Expected churn | Hard ceiling |
|---|---:|---:|---:|---:|---:|
| 1. Define and validate the payload contract | 52 | 2 | +50 | 54 | 65 |
| 2. Make core HTML rendering literal | 72 | 8 | +64 | 80 | 100 |
| 3. Adjust repository blueprint producers | 60 | 4 | +56 | 64 | 80 |
| 4. Adjust docstring and Rutter producers | 89 | 1 | +88 | 90 | 105 |
| 5. Adjust math-dependency finalization | 113 | 5 | +108 | 118 | 140 |
| 6. Document and verify the end-to-end contract | 20 | 10 | +10 | 30 | 30 |
| **Total** | **406** | **30** | **+376** | **436** | **520** |

The implementation hard ceiling is therefore **520 changed lines**. Generated HTML/JSON outputs and this plan file are verification artifacts and do not count toward implementation churn; if a tracked generated artifact becomes necessary, add it to this budget before changing it.

## Task 1: Define and validate the payload contract

**Budget:** +52 / -2 / net +50; expected churn 54; hard ceiling 65.

**Files:**

- `src/officina/visualization/graph_specification.schema.json`
- `src/officina/visualization/graph.py`
- `tests/test_visualization_graph.py`

**Steps:**

- [ ] Add failing schema/graph-validation tests first:
  - entity `subtitle: ""` and non-empty string are accepted;
  - presentation-node `subtitle: "Domain"` is accepted;
  - non-string subtitles on either node kind are rejected;
  - subtitle remains optional for direct canonical JSON.
- [ ] Add optional `subtitle: {"type": "string"}` properties to both schema definitions. Deliberately allow an empty string; unlike `short_title`, absence of content is meaningful.
- [ ] Add matching lightweight type checks to `Graph.validate_graph()` and `_validate_presentation_nodes()` so direct callers and schema-backed rendering fail consistently.
- [ ] Remove the stale schema wording that says `ref` is drawn beside `type`; describe `ref` as semantic/inspector metadata.
- [ ] Run:
  - `./repo_checks.py --task tests:shared --selector tests/test_visualization_graph.py --jobs 8`

**Checkpoint:** The data contract accepts an explicitly blank subtitle, rejects non-strings, and does not require producers to provide the field.

## Task 2: Make core HTML rendering literal

**Budget:** +72 / -8 / net +64; expected churn 80; hard ceiling 100.

**Files:**

- `src/officina/visualization/html_renderer/runtime/bootstrap.js`
- `src/officina/visualization/html_renderer/runtime/node_renderer.js`
- `tests/test_visualization_node_readability_browser.py`

**Steps:**

- [ ] Add a browser regression fixture with misleading `id`, `type`, `ref`, and long `title`, but explicit visible title/subtitle values.
- [ ] Assert the cell contains the supplied title and subtitle and does not contain any renderer-built `type ref` string.
- [ ] Add cases for `subtitle: ""` and a missing subtitle; assert neither produces text nor a blank subtitle row, and their measured/rendered height is the single-line variant.
- [ ] Add a case for `label` overriding `short_title` to preserve the existing explicit producer override.
- [ ] Centralize visible text resolution in `node_renderer.js` (or one existing shared runtime boundary) and use it from both default dimension measurement and final drawing. Do not duplicate fallback rules in two functions.
- [ ] Resolve visible title only from `label` then `short_title`; resolve subtitle only from `subtitle` then the empty string.
- [ ] Render the subtitle element conditionally. Do not alter inspector rendering, filtering fields, tooltip content, or transient state badges.
- [ ] Run:
  - `./repo_checks.py --task tests:browser --selector tests/test_visualization_node_readability_browser.py --jobs 8`

**Checkpoint:** Changing only `type`, `ref`, `id`, or long `title` cannot change cell text or dimensions; changing producer-supplied title/subtitle does.

## Task 3: Adjust repository blueprint producers

**Budget:** +60 / -4 / net +56; expected churn 64; hard ceiling 80.

**Files:**

- `src/officina/visualization/from_blueprint/payload_builder.py`
- `src/officina/visualization/from_blueprint/presentation_nodes.py`
- `src/officina/visualization/html_renderer/runtime/presentation_nodes.js`
- `tests/test_blueprint_visualization.py`

**Steps:**

- [ ] Extend existing blueprint payload tests before production changes. Cover:
  - nested module `skills.email-triage._rtx` -> `short_title: "_rtx"`, `subtitle: ""`;
  - behavioral source -> its local segment and blank subtitle;
  - private and exported interfaces -> `interface.local_name` and blank subtitle;
  - out-of-scope boundary -> outside-root title and blank subtitle;
  - grouping presentation node -> value label title and facet label subtitle.
- [ ] Add `subtitle: ""` at the canonical entity construction sites without changing `id`, `type`, `category`, `ref`, `title`, container ownership, or relationship extraction.
- [ ] Add the configured facet label to each first-class presentation node as `subtitle`. Preserve its `short_title` value label.
- [ ] Replace runtime `facetLabel` subtitle synthesis with the presentation node's producer-supplied `subtitle`; keep the transient `collapsed` suffix as renderer-owned UI state.
- [ ] Do not add renderer-specific formatting words such as `module`, `interface`, or `source`; the legend already communicates kind.
- [ ] Run:
  - `./repo_checks.py --task tests:shared --selector tests/test_blueprint_visualization.py --jobs 8`

**Checkpoint:** In a repository payload, `x.y.z` displays as `z` inside its hierarchy with no subtitle, while full identity and type remain present for non-cell consumers.

## Task 4: Adjust docstring and Rutter producers

**Budget:** +89 / -1 / net +88; expected churn 90; hard ceiling 105.

**Files:**

- `src/officina/visualization/from_docstring/payload_builder.py`
- `src/officina/visualization/from_rutter/payload_builder.py`
- `tests/test_visualization_payload_text.py` (new)
- `src/officina/rutter/tests/test_rutter_visualization.py`

**Steps:**

- [ ] Add producer-level tests first, asserting complete title/subtitle pairs rather than only the presence of entities.
- [ ] In docstring `_build_entity`, emit a subtitle from the entity kind in one centralized place. Humanize separators for display (`external-module` -> `external module`) without changing the canonical `type` value.
- [ ] Verify representative module, class, callable, external-module, and source entities. The subtitle must never contain the full dotted id.
- [ ] In `build_rutter_payload`, emit the human-readable evolution kind as `subtitle`. Keep the evolution id as `short_title`/`label` and keep the canonical kind in `type`.
- [ ] Verify at least one LLM step and one non-LLM/control evolution so the policy is not accidentally tailored to a single Rutter class.
- [ ] Avoid a general display-name utility: the two producer policies are tiny and domain-specific, and sharing them would couple unrelated extractors.
- [ ] Run:
  - `./repo_checks.py --task tests:shared --selector tests/test_visualization_payload_text.py --selector src/officina/rutter/tests/test_rutter_visualization.py --jobs 8`

**Checkpoint:** Both extractors remain understandable when rendered by a literal renderer, without repeating ids and without depending on core knowledge of their types.

## Task 5: Adjust math-dependency finalization

**Budget:** +113 / -5 / net +108; expected churn 118; hard ceiling 140.

**Files:**

- `skills/math-dependency-graph/_rtx/_extraction_finalizer.py`
- `skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py`
- `skills/math-dependency-graph/instructions/extract.md`

**Steps:**

- [ ] Add failing unit tests for a pure subtitle finalization step:
  - missing subtitle plus `type: theorem`, `ref: 4.3` -> `theorem 4.3`;
  - missing subtitle plus type only -> human-readable type;
  - explicit non-empty subtitle is preserved;
  - explicit empty subtitle is preserved and not refilled;
  - input payload is not mutated.
- [ ] Add `subtitle` to `_ENTITY_RENDERED_TEXT_FIELDS` so resolved TeX label references and MathJax macro discovery see producer-visible subtitles.
- [ ] Implement a focused `apply_entity_subtitles()` transformation. It may humanize separators in type names but must not infer from ids, labels, descriptions, or topology.
- [ ] Call the transformation after `apply_label_numbering()` and before `apply_presentation_base()`/validation. This ordering ensures document numbers are available and presentation defaults cannot own semantic text.
- [ ] Update `extract.md`: extraction authors may provide a deliberate subtitle; otherwise the finalizer supplies the numbered type. Remove the statement that the viewer constructs `type + ref`.
- [ ] Test `finalize_extraction()` end to end with a numbered fixture and assert the written payload contains the subtitle.
- [ ] Do not modify `assets/inference-from-random-restarts/results/semantic-gold.json` or `final-gold.json`. After tests pass, regenerate only the ignored/local `extraction-latest.json` and HTML for manual acceptance.
- [ ] Run:
  - `./repo_checks.py --task tests:shared --selector skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py --jobs 8`
  - the skill's existing inference-from-random-restarts finalization/render command documented beside its asset, writing only working output.

**Checkpoint:** Math graphs retain theorem/assumption numbers in subtitles even though the core renderer has no type/ref synthesis.

## Task 6: Document and verify the end-to-end contract

**Budget:** +20 / -10 / net +10; expected and hard churn 30.

**Files:**

- `src/officina/visualization/html_renderer/README.md`
- no tracked generated graph output

**Steps:**

- [ ] Document that cell text is producer-owned, `short_title` is the visible title, `label` is its explicit override, and missing/empty subtitle renders nothing.
- [ ] Search all active production and instruction sources for hidden synthesis:
  - `rg -n 'type.*ref|ref.*type|node-subtitle|facetLabel|subtitle' src/officina/visualization skills/math-dependency-graph --glob '!**/assets/**'`
- [ ] Render the repository graph from the updated blueprint extractor. Inspect representative module, nested `_rtx`, source, interface, boundary, and grouping cells.
- [ ] Render inference-from-random-restarts from finalized output. Inspect a numbered theorem/result and a deliberately blank subtitle if present.
- [ ] Confirm legends, filtering, search, and inspectors still expose type/full identity where they did before; this guards against deleting semantic data merely to clean up cells.
- [ ] Run the focused union:
  - `./repo_checks.py --task tests:shared --selector tests/test_visualization_graph.py --selector tests/test_blueprint_visualization.py --selector tests/test_visualization_payload_text.py --selector src/officina/rutter/tests/test_rutter_visualization.py --selector skills/math-dependency-graph/_rtx/tests/test_extraction_finalizer.py --jobs 8`
  - `./repo_checks.py --task tests:browser --selector tests/test_visualization_node_readability_browser.py --jobs 8`
- [ ] Review `git diff --stat` and compute task-local churn. Stop if total implementation churn exceeds 520 lines or any task exceeds its ceiling; revise this plan before expanding.

**Final acceptance:**

- Repository cells show the final local name exactly once and no subtitle.
- Presentation groups show value title plus producer-provided facet subtitle.
- Docstring, Rutter, and math graphs retain useful domain-specific subtitles.
- The core renderer contains no `type + ref` or equivalent semantic fallback for cell text.
- Empty subtitles do not consume a row or inflate node height.
- No topology, inspector, filtering, legend, edge, or frozen-gold changes enter the diff.
- Only named paths are staged; unrelated dirty worktree changes remain untouched.

## Task 7: Close presentation-shell empty-subtitle parity

**Budget:** +18 / -12 / net +6; expected and hard churn 30,
drawn from the unused whole-plan contingency so the **520-line implementation
ceiling is unchanged**.

An independent whole-branch audit found that canonical cells omit an absent or
empty subtitle, but presentation-node shells still create a blank
`.node-subtitle`; collapsed shells can therefore display ` · collapsed` with
no producer subtitle. Make presentation shells use the same literal/absent-row
contract without changing non-empty producer subtitles or the transient
collapsed state.

- Add a browser regression that distinguishes a missing element from an empty
  element for absent and explicit-empty presentation subtitles.
- Render no subtitle element when both producer subtitle and transient state
  are empty; when collapsed without a producer subtitle, render only the
  transient `collapsed` text, without a leading separator.
- Preserve non-empty `Domain · collapsed` behavior and node measurement parity.
- Run the focused presentation-node browser tests and the affected union, then
  obtain independent re-audit before committing.
