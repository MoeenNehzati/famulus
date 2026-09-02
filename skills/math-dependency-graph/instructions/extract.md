# Extract a Mathematical Dependency Graph

Produce a semantic graph draft from the supplied mathematical content, finalize it as self-contained canonical JSON, and render only when requested. This interface owns semantic interpretation; deterministic finalization and rendering remain delegated boundaries.

## Inputs and completion sequence

Identify the **included content** being graphed separately from the **root TeX entrypoint**. The included content fixes the evidentiary scope. The root entrypoint supplies the document preamble and the recursively reachable macro definitions used only during finalization. Never substitute a scoped fragment for the root. In the bundled example, the included content is under `source/appendix.tex`, but macro traversal must start at `source/main.tex`.

Complete every extraction in this order:

1. Interpret only the included content and write a semantic draft JSON artifact.
2. Invoke `math-dependency-graph._rtx.interface.scripts-finalize-extraction` with the draft path, the explicit root TeX entrypoint, the optional label-map path, and the canonical output path.
3. Read and validate the returned canonical JSON. It must be schema-version 2 and self-contained before it is reported as render-ready.
4. If rendering was requested, invoke `math-dependency-graph._rtx.interface.scripts-build-math-dependency-graph` with only the canonical JSON, optional HTML destination, and optional rendered-view transitive reduction.

Canonical completion does not require an HTML artifact. `render-ready/self-contained` means finalization and schema validation succeeded; it does not mean a browser has verified the render. When browser validation is requested and available, inspect the optional HTML with `window.officinaMathDiagnostics()` and separately report whether it is `browser-verified render-clean` and any unresolved TeX commands.

## Objective

Expose enough precise structure for a human or machine to trace which stated mathematical objects directly support each result. Preserve the source's notation and distinguish explicit evidence from interpretation.

## Entity extraction

Create entities for mathematically meaningful, referable objects within the requested scope:

- standing assumptions and maintained restrictions
- notation blocks that introduce objects used later
- definitions
- lemmas, propositions, theorems, and corollaries
- explicitly named intermediate claims when later arguments depend on them

Do not create entities for headings, prose transitions, proof steps with no independent referential role, or external results that are merely mentioned. If an external result is essential, represent the document-local invocation as an entity and identify the external result in its metadata.

Each entity must provide the schema-required fields:

- `id`: a unique, stable, nonempty identifier
- `type`: a nonempty mathematical category such as `standing-assumption`, `local-assumption`, `notation`, `definition`, `lemma`, `proposition`, `theorem`, `corollary`, or `remark`
- `short_title`: a compact description of what the object says, written to be read inside a small node cell. Aim for a short noun phrase naming the mathematical content, such as `Beta-prior posterior tail bound` or `Agreement before exit` — never a bare environment name, a label key, or a macro name.
- `position`: a nonnegative integer preserving source order

Also set `ref` to the number the document assigns the object, such as `4.3`, `A.7`, or `C.2`. The viewer draws `short_title` as the node's title and `type` plus `ref` as its subtitle, so a missing `ref` costs the reader the object's index in the paper.

That number is **not written in the source**: TeX assigns it while typesetting, and a `\label{...}` records only a key. Take it from a resolved label map when the job supplies one, and otherwise derive it.

**Record the label rather than the number.** Whenever the object's statement carries a `\label{...}`, copy that key verbatim into `tex_label`. Reading a label off the source needs no inference, whereas a number does, and the finalization step resolves `tex_label` to the printed number deterministically when a label map is supplied. Set `tex_label` even when you also know the number.

**Use a supplied label map when one is given.** It was produced by compiling the document, so its numbers are exactly what the paper prints: look the entity up by its `tex_label` and copy the number into `ref`. The map also records the kind TeX assigned, such as `lemma` or `assumption`; treat a disagreement with your own reading as a signal to re-examine the statement rather than as licence to override the map.

**Derive the number only when neither is available.** Which environments share a counter, and what resets them, is decided entirely by the preamble, so read the declarations before numbering anything:

- `\newtheorem{theorem}{Theorem}[section]` starts a counter that resets at each section, so its objects are numbered `<section>.<n>`.
- `\newtheorem{lemma}[theorem]{Lemma}` makes lemmas share the `theorem` counter. Environments sharing a counter advance one sequence between them.
- `\newtheorem{assumption}{Assumption}[section]` starts a counter of its own. Two environments on different counters may hold the same number, so Assumption 4.1 and Lemma 4.1 can both exist; two on the same counter never can.
- After `\appendix`, section numbers become letters, so counters read `A.1`, `B.2`, and so on.

Count in document order within each counter's resetting scope, and follow `\setcounter` or `\numberwithin` when the preamble changes a scheme. Leave `ref` absent for a genuinely unnumbered object, and also when neither a label map nor the preamble and every preceding object on that counter is available: a number guessed without them would be wrong, and a wrong index is worse than none.

Put the source-faithful mathematical statement in `description`, beginning with the mathematics rather than with how the statement was typeset. Set `source` to exactly `explicit` or `inferred` when provenance is useful.

Use `label` only to override the displayed title when `short_title` would read poorly in a node cell, and keep it a display name for the mathematics. A label must never encode a LaTeX label key, a macro name, or typesetting mechanism. When the only in-scope occurrence of a result is a restatement macro or restatement environment, the entity is still the result: name it after the result, and record the restatement — the macro used and the fact that the statement text lies outside the requested scope — in `metadata`. Preserve TeX notation in MathJax-ready strings. Never silently normalize notation in a way that changes scope or meaning.

## Relationship extraction

Record only direct dependencies. Add `A -> B` when understanding or establishing `B` directly requires `A` in the source argument. Do not add a transitive edge merely because a path already implies it.

Encode each relationship inside the supporting source entity's `connects_to` array. For every edge:

- set `to` to the target entity's emitted `id`
- set `type` to exactly one of two values, and never invent another:
  - `supports` — establishing or understanding the target directly requires the source. This is the default and covers every kind of mathematical use: a hypothesis, a cited lemma, a supplied construction, a definition introducing notation the target is stated in, a result invoked inside a proof.
  - `exemplifies` — the source is an example or instance illustrating the target without supporting its validity.
- state the dependency in `description`: one clause naming how the source is actually used, such as "supplies the uniform Lipschitz bound used before the exit time". The specific character of a dependency belongs here, not in a new `type` value. This clause is what a reader sees on hover.
- put any further structured detail in `metadata`
- put the smallest supporting source passage or location in `evidence`
- set `implicit` to `true` only when the relationship is inferred rather than asserted by the source
- when confidence is useful, use exactly `Verified`, `High`, `Medium`, `Low`, `Likely`, or `Unknown`

Do not emit a top-level `relationships`, `edges`, `source`, or `target` collection. The containing entity is the edge source; `to` is its target.

The edge vocabulary is closed on purpose. A presentation layer styles edges by category, so a type it does not know cannot be drawn, and a graph that declares `edge_categories` fails validation on any type absent from them. Distinctions such as "used in proof", "assumption for", or "notation for" are descriptions of one dependency, not separate relations: record them in `description` and `metadata`, where they stay visible on hover and remain available to any later analysis.

Ambient assumptions must not disappear. Attach them directly to every result whose statement or proof uses them, unless the source defines a clearly scoped aggregate assumption entity and explicitly invokes that aggregate.

Direction means dependency-to-dependent: assumptions and supporting results point toward the object that uses them.

## Evidence and uncertainty

Evidence must identify the smallest source span that supports the entity or edge. Prefer exact labels and line ranges when available. Do not invent citations, labels, proof uses, or dependencies.

When the source is ambiguous:

- choose the narrowest defensible interpretation
- lower confidence and explain the ambiguity in metadata
- leave unresolved references as explicit gaps
- never manufacture an edge solely to make the graph connected

## Semantic draft

Write one schema-compatible JSON object for deterministic finalization:

- top-level `schema_version` is the integer `2`
- top-level `entities` is always present, including when empty
- do not add undeclared top-level fields because the schema rejects them
- entity ids are unique
- every edge target names an emitted entity
- every entity position is a nonnegative integer and positions preserve source order
- every optional enum uses the schema's exact spelling and capitalization

Use `graph_kind: "math-dependency"` and include `document.title` and `document.source_file` when known. General audit metadata belongs in top-level `metadata`. Do not emit renderer layout, filtering, containment, degree, or tier fields unless the request explicitly requires them; those are presentation or derived concerns rather than mathematical extraction.

Do not read TeX macro definitions, construct a macro sidecar, or add presentation defaults during semantic extraction. The finalizer scans graph-visible math, traverses the explicit root entrypoint, embeds the required recursive macro closure in the MathJax renderer dependency, applies the canonical presentation catalog, and validates the completed payload before atomically replacing the canonical output.

A minimal valid extraction has this shape:

```json
{
  "schema_version": 2,
  "graph_kind": "math-dependency",
  "entities": [
    {
      "id": "assumption-continuity",
      "type": "standing-assumption",
      "short_title": "Continuity",
      "position": 0,
      "description": "The objective is continuous on the feasible set.",
      "source": "explicit",
      "connects_to": [
        {
          "to": "theorem-existence",
          "type": "supports",
          "description": "Continuity is used to establish existence.",
          "evidence": "Proof of Theorem 1, first paragraph.",
          "confidence": "Verified",
          "implicit": false
        }
      ]
    },
    {
      "id": "theorem-existence",
      "type": "theorem",
      "short_title": "Existence",
      "position": 1,
      "description": "A solution exists.",
      "source": "explicit",
      "connects_to": []
    }
  ]
}
```

If no category catalog is supplied, entity `type` values provide the default categories. If the request supplies a category catalog or explicit entity categories, preserve them rather than replacing them with math defaults.

Before finalization, check the draft's semantic invariants: unique entity ids, valid edge endpoints, source-order positions, direct rather than transitively inferred dependencies, and evidence for every nontrivial edge. After the registered finalizer returns, read the canonical artifact and confirm it satisfies the current schema and contains its renderer dependencies internally. Never repair, enrich, or rewrite canonical JSON in the builder.

Return the canonical JSON path and any evidence gaps. Return an HTML path only when optional rendering was requested. If browser diagnostics were run, report their status separately; otherwise do not imply browser verification. Do not return a prose substitute for the artifacts.
