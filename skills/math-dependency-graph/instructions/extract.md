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
- `short_title`: a concise nonempty display title
- `position`: a nonnegative integer preserving source order

Put the source-faithful mathematical statement in `description`. Use `label` only when a display label should differ from `short_title`. Set `source` to exactly `explicit` or `inferred` when provenance is useful. Preserve TeX notation in MathJax-ready strings. Never silently normalize notation in a way that changes scope or meaning.

## Relationship extraction

Record only direct dependencies. Add `A -> B` when understanding or establishing `B` directly requires `A` in the source argument. Do not add a transitive edge merely because a path already implies it.

Encode each relationship inside the supporting source entity's `connects_to` array. For every edge:

- set `to` to the target entity's emitted `id`
- set `type` to a stable, nonempty, kebab-case description of the mathematical use
- state the dependency in `description`
- put the smallest supporting source passage or location in `evidence`
- set `implicit` to `true` only when the relationship is inferred rather than asserted by the source
- when confidence is useful, use exactly `Verified`, `High`, `Medium`, `Low`, `Likely`, or `Unknown`

Do not emit a top-level `relationships`, `edges`, `source`, or `target` collection. The containing entity is the edge source; `to` is its target.

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

Write one JSON object accepted by `src/officina/common/visualization/graph_specification.schema.json`:

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
