# Extract a Mathematical Dependency Graph

Produce canonical graph JSON from the supplied mathematical source. This interface owns semantic interpretation; it does not render HTML.

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

Also set `ref` whenever the document numbers the object: the number exactly as the document assigns it, such as `4.3`, `A.7`, or `C.2`. Leave `ref` absent only for genuinely unnumbered objects. The viewer draws `short_title` as the node's title and `type` plus `ref` as its subtitle, so a missing `ref` costs the reader the object's index in the paper.

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

## Canonical output

Write one JSON object accepted by `src/officina/visualization/graph_specification.schema.json`:

- top-level `schema_version` is the integer `2`
- top-level `entities` is always present, including when empty
- do not add undeclared top-level fields because the schema rejects them
- entity ids are unique
- every edge target names an emitted entity
- every entity position is a nonnegative integer and positions preserve source order
- every optional enum uses the schema's exact spelling and capitalization

Use `graph_kind: "math-dependency"` and include `document.title` and `document.source_file` when known. General audit metadata belongs in top-level `metadata`. Do not emit renderer layout, filtering, containment, degree, or tier fields unless the request explicitly requires them; those are presentation or derived concerns rather than mathematical extraction.

When the graph contains TeX, request MathJax through the schema-supported dependency object:

```json
{
  "id": "mathjax",
  "version": "3",
  "configuration": {
    "input": "tex",
    "output": "svg"
  }
}
```

Place it in top-level `renderer_dependencies`. Add `configuration.macros` only when macro definitions are known and each value follows the schema-supported string or macro-array form.

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
          "type": "assumption-for",
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

Before returning, check the artifact structurally against the current schema and check the semantic invariants the schema cannot express: unique entity ids, valid edge endpoints, source-order positions, direct rather than transitively inferred dependencies, and evidence for every nontrivial edge. Return the path to the completed JSON artifact. Do not return a prose substitute for the artifact. Report evidence gaps alongside the path when the extraction is partial.
