---
name: math-dependency-graph
description: |
  Extract a direct mathematical dependency graph from a LaTeX math document, including standing assumptions, definitions, lemmas, propositions, theorems, corollaries, notation blocks, and dependency edges with evidence.

  Use when the user wants document-internal assumptions-to-results structure, a canonical JSON artifact, or an interactive HTML graph of direct dependencies.

  Do not use when the main goal is proof validation, notation cleanup, prose review, or a literature map.

  Success criteria:
  - produce JSON as the source of truth for entities and direct dependencies
  - represent only direct dependencies, not inherited transitive closure
  - identify ambient assumptions from notation or scope phrases
  - attach short descriptions and evidence to dependency links
  - render a standalone HTML graph from the JSON
---

When this skill is used, begin with:

Skill: math-dependency-graph

Category: document-oriented

Dependencies: none
Category: mathematical-analysis

## 1. Goal

Build a direct-dependency graph for a mathematical document.

The canonical artifact is JSON.
The HTML graph is a visualization of that JSON and must not invent structure.
The model is responsible for building the JSON.
Python is responsible for rendering and view-layer interaction only.

The JSON must be presentation-ready.
Do not rely on the renderer to repair malformed math, infer math mode, or normalize prose.

## 2. Dependency semantics

Record only direct dependencies.

If entity `X` depends on entity `Y`, and `Y` depends on standing assumption `A`,
do not also connect `A` directly to `X` unless `X` independently uses `A`
in its own statement, proof, or ambient notation.

Each dependency edge must include:
- the prerequisite id
- a short description of how it is used
- a use type
- a confidence level
- a short evidence string

All string fields intended for display must already be valid MathJax-compatible text.
If a symbol such as `\in`, `\Rightarrow`, `\Omega`, or `\Pi_i` should render as math, put it in math mode in the JSON.

## 3. What counts as an entity

Prefer these entity types:
- `standing-assumption`
- `local-assumption`
- `definition`
- `notation`
- `lemma`
- `proposition`
- `theorem`
- `corollary`
- `remark`

Standing assumptions may come from:
- explicit assumption environments
- ambient scope phrases such as `Throughout`, `Fix ... throughout`, `In this section we assume`
- notation that imposes mathematical conditions in a reusable way

## 4. Output

Start with:

- `Mode: Explore`
- `Skill: math-dependency-graph`

Then report briefly:
- where the JSON was written
- where the HTML was written
- main extraction gaps or ambiguous edges

## 5. Workflow

1. Read the document and identify theorem-like environments and ambient assumption paragraphs.
2. Construct the canonical JSON directly by understanding the mathematical structure.
3. Add only direct dependencies with descriptions and evidence.
4. Write or propose that JSON first.
5. If the document uses local TeX macros, give the active TeX entrypoint to the
   macro extractor:
   ```
   python scripts/extract_mathjax_macros.py <entrypoint.tex>
   ```
   This writes `_build/<entrypoint>-mathjax-macros.json` by default. The
   extractor recursively follows `\input`/`\include`, scans macro definitions
   throughout the reachable TeX source, and writes the transitive closure needed
   by MathJax.
6. Once the JSON is written, invoke the renderer:
   ```
   python scripts/build_math_dependency_graph.py <source.json> --tex-entry <entrypoint.tex>
   ```
   Output defaults to `_build/<name>.html` next to the JSON. Use `--html-out <path>` to override.
7. Flag uncertain or heuristic edges explicitly.

## 6. Canonical fields

Use `type`, not `kind`.

For entities, use these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier for the entity. |
| `type` | string | Entity type: `definition`, `lemma`, `theorem`, etc. |
| `ref` | string | Document-assigned number as it appears in the paper, e.g. `4.2` or `A.3`. Empty if unnumbered. |
| `short_title` | string | Short identifier for display in the cell, e.g. `Barbalat`. No type prefix. |
| `title` | string | Full descriptive name shown in the side panel. |
| `description` | string | One-sentence mathematical summary. MathJax-compatible. |
| `defined` | string | Where the entity is introduced, e.g. `Section 4, Assumption 4.1`. |
| `active_in` | string | Region of the document where the entity is in force, e.g. `Section 4–5`. |
| `source` | string | `explicit` if stated in the paper, `inferred` if introduced by the model. |
| `depends_on` | array | Direct dependency edges. |
| `position` | int | Ordering position within the document. Layout-only, not displayed. |

For dependencies, use these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Id of the prerequisite entity. |
| `use_type` | string | How the prerequisite is used: `ambient`, `applies`, `uses`, `invokes`, etc. |
| `description` | string | Short description of how the prerequisite is used in this context. |
| `confidence` | string | `Verified`, `Likely`, or `Speculative`. |
| `evidence` | string | Short quote or pointer from the proof or statement. |

Prefer the model to justify difficult edges from mathematical content, not from string matching.
If the document is ambiguous, keep the JSON conservative and mark uncertainty explicitly.

If the document uses local TeX macros, set the active entrypoint under:

- `document.source_entrypoint`

For example:

```json
{
  "document": {
    "source_entrypoint": "or.tex"
  }
}
```

The renderer will use `_build/<entrypoint>-mathjax-macros.json` by default when
`document.source_entrypoint` is present. If the file is missing and the
entrypoint can be resolved, it regenerates the macro file from the TeX source.

If any extracted macro is not MathJax-compatible, override it explicitly under:

- `document.mathjax_macros`

For example:

```json
{
  "document": {
    "mathjax_macros": {
      "R": "\\mathbb{R}",
      "G": "\\mathcal{G}"
    }
  }
}
```

Do not emit bare local macros unless they are declared there.
Do not emit malformed TeX such as ambiguous subscripts or superscripts.
Graph-local `document.mathjax_macros` overrides extracted macros, so use it for
manual corrections.

## 7. Rendering rules

The HTML graph should:
- use different node shapes for different entity types
- color-code entity types
- encode dependency confidence through edge style
- use a layered left-to-right layout for distinct implications
- place same-result vertical variants such as attached corollaries directly below their parent when appropriate
- show extra details on hover
- show fuller metadata in a side panel on click
- support hiding entity categories from the legend without breaking visible causality
- support temporarily removing individual nodes from the graph without breaking visible causality
- support ancestor-focused viewing for a selected node
- persist viewer state locally when feasible

The current renderer uses `elkjs` in the browser for layout and routing.
The renderer may optionally apply graph-theoretic transitive reduction to the rendered view for readability, but this does not change the JSON source of truth.

Do not treat inferred notation-imposed assumptions as fully verified facts.
Mark them as inferred or unclear when appropriate.

## 8. Tool split

The model should do:
- entity identification
- standing-assumption identification
- direct dependency judgment
- link descriptions
- confidence assignment
- production of valid display-ready strings
- declaration of any required MathJax macros

The Python renderer should do only:
- load canonical JSON
- validate basic structure
- merge extracted MathJax macros from `_build/<entrypoint>-mathjax-macros.json`
- optionally apply graph-theoretic transitive reduction for readability
- render the interactive HTML graph
- manage visualization-only state such as filters, local hiding, focus modes, and persistence

Do not delegate semantic graph extraction to the renderer script.

## 9. Field-to-HTML rendering map

Use this table to verify that the JSON is correct and to debug rendering issues.
Each row shows a JSON field and exactly where its value appears in the HTML.

### Node cell (the colored shape in the graph)

| JSON field | Rendered as |
|---|---|
| `short_title` | Large bold white text, top line of the cell |
| `type` + `ref` | Small subtitle, bottom line of the cell (e.g. `theorem 5.1`) |
| `type` | Determines node shape and fill color (see shape table below) |

### Hover tooltip (shown while the mouse is over a node)

| JSON field | Rendered as |
|---|---|
| `short_title` | Bold first line |
| `ref` | Second line (empty string if `""`) |
| `type` | Third line |
| `description` | Fourth line |

### Side panel on click (right-hand panel when a node is selected)

| JSON field | Rendered as |
|---|---|
| `short_title` | `<h2>` heading |
| `ref` | **Ref:** row (shows `—` if empty) |
| `type` | **Type:** row in `<code>` |
| `title` | **Title:** row (shows `—` if empty) |
| `active_in` | **Active in:** row (shows `—` if empty) |
| `source` | **Source:** row (shows `—` if empty) |
| `defined` | **Defined:** row (shows `—` if empty) |
| `description` | Paragraph body below the metadata rows |

### Dependency list (in the side panel, under "Direct dependencies")

Each entry in `depends_on` renders as one `<li>`:

| `depends_on` field | Rendered as |
|---|---|
| `id` | Looked up → target's `short_title` shown in bold |
| `use_type` | `<code>` label after the name |
| `description` | Text on the second line |
| `evidence` | Small grey text, with confidence in parentheses |
| `confidence` | Appended as `(confidence: …)` in small grey text |

### Edge visual encoding

| `depends_on.confidence` | Edge style |
|---|---|
| `"Verified"` | Solid line |
| `"Likely"` | Long-dashed (`8 5`) |
| `"Speculative"` | Short-dashed (`3 5`) |
| *(bridge, auto-generated)* | Medium-dashed (`6 4`) |

Edge color is assigned by `position` order, cycling through a fixed 6-color palette.
Lower `position` values get earlier colors.

### Node shapes by `type`

| `type` | Shape |
|---|---|
| `standing-assumption` | Hexagon |
| `local-assumption` | Diamond |
| `definition` | Rounded rectangle |
| `notation` | Parallelogram |
| `lemma` | Ellipse |
| `proposition` | Rectangle |
| `theorem` | Double rectangle |
| `corollary` | Circle |
| `remark` | Rectangle |
