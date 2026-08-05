# First-Class Presentation Nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace blueprint-specific metadata grouping with generic, first-class JSON presentation nodes and controls interpreted by the shared HTML renderer.

**Architecture:** Canonical `entities` remain the architectural graph. A new top-level `presentation_nodes` collection supplies interactive many-to-many reference supernodes, while `ui.presentation_node_controls` supplies generic facet activation. The blueprint adapter emits concrete discovery instances; the renderer contains no blueprint vocabulary.

**Tech Stack:** Python 3, JSON Schema draft-07, vanilla JavaScript, SVG, ELK.js, pytest, headless Chrome.

## Global Constraints

- Work directly on the existing named `master` branch.
- Preserve unrelated dirty files and do not stage, commit, stash, restore, or push.
- Use strict red-green TDD for every executable behavior change.
- `entities` remains the only canonical node and edge-endpoint collection.
- Presentation membership is many-to-many and never changes `entity.container`.
- Hiding or self-collapsing a presentation node leaves all members visible.
- Shared members render exactly once and move exactly once during group drag.
- Core renderer code contains no blueprint discovery vocabulary.
- Default and Reset leave the blueprint grouping control off.

---

## File Structure

- `src/officina/common/visualization/graph_specification.schema.json` — generic payload shape.
- `src/officina/common/visualization/graph.py` — cross-reference and semantic validation.
- `src/officina/common/visualization/from_blueprint/presentation_nodes.py` — blueprint discovery projection.
- `src/officina/common/visualization/from_blueprint/payload_builder.py` — attaches adapter output.
- `src/officina/common/visualization/html_renderer/runtime/presentation_nodes.js` — generic registry, controls, state, layout, rendering, and interaction effects.
- Existing renderer runtime files — integrate generic presentation-node state with rendering, selection, details, history, reset, dragging, and assets.
- Visualization tests — schema, adapter, browser, and stress coverage.
- `docs/visualization.md` and renderer README — generic public contract and implementation boundary.

---

### Task 1: Define and validate the generic JSON contract

**Files:**
- Modify: `src/officina/common/visualization/graph_specification.schema.json`
- Modify: `src/officina/common/visualization/graph.py`
- Modify: `tests/test_visualization_graph.py`

**Interfaces:**
- Consumes: schema-version-2 graph payloads with canonical `entities`.
- Produces: validated `presentation_nodes: list[PresentationNode]` and `ui.presentation_node_controls: list[PresentationNodeControl]`.

- [ ] **Step 1: Replace metadata-grouping fixtures with presentation-node fixtures**

Add a valid fixture containing two overlapping supernodes and one control:

```python
"presentation_nodes": [
    {
        "id": "group.research",
        "type": "presentation-group",
        "short_title": "Research",
        "position": 0,
        "member_ids": ["alpha"],
        "presentation": {
            "form": "supernode",
            "tone": "subtle",
            "default_visibility": "hidden",
        },
        "interaction": {
            "selectable": True,
            "inspectable": True,
            "draggable": "members",
            "collapse_effect": "self",
        },
    }
],
"ui": {
    "presentation_node_controls": [{
        "id": "grouping",
        "label": "Grouping",
        "selector_label": "Group by",
        "default_facet": None,
        "facets": [{
            "id": "domain",
            "label": "Domain",
            "activation": "all",
            "node_ids": ["group.research"],
        }],
    }]
}
```

Add literal rejection cases for duplicate presentation IDs, canonical-ID collisions, unknown or contained members, duplicate members, unknown node references, duplicate facet/control IDs, duplicate control ownership, invalid defaults, and unsupported interaction enum values.

- [ ] **Step 2: Run the focused graph tests and verify RED**

Run:

```bash
python3 -m pytest -o pythonpath=src -q tests/test_visualization_graph.py -k presentation_node
```

Expected: failures because the schema and validator do not recognize the new contract.

- [ ] **Step 3: Add strict schema definitions**

Define `presentationNode`, `presentationNodeInteraction`,
`presentationNodeControl`, and `presentationNodeFacet`. Add
`presentation_nodes` at the payload root and `presentation_node_controls` under
`ui`. Remove `metadataGrouping*` definitions and `ui.metadata_grouping`.

- [ ] **Step 4: Replace `_validate_metadata_grouping` with `_validate_presentation_nodes`**

The validator must enforce all cross-reference rules named in Step 1. It must
inspect canonical edge targets and reject any target that names a presentation
node, even if that target is absent from canonical entities.

- [ ] **Step 5: Run graph tests and verify GREEN**

Run the focused command from Step 2, then:

```bash
python3 -m pytest -o pythonpath=src -q tests/test_visualization_graph.py
```

---

### Task 2: Emit blueprint instances through the generic contract

**Files:**
- Create: `src/officina/common/visualization/from_blueprint/presentation_nodes.py`
- Delete: `src/officina/common/visualization/from_blueprint/metadata_grouping.py`
- Modify: `src/officina/common/visualization/from_blueprint/payload_builder.py`
- Modify: `tests/test_blueprint_visualization.py`

**Interfaces:**
- Consumes: `RepositoryBlueprintGraph`, repository root, and scoped module IDs.
- Produces: `build_presentation_nodes(...) -> tuple[list[dict[str, object]], list[dict[str, object]]]`, containing presentation nodes and controls.

- [ ] **Step 1: Rewrite adapter expectations against literal presentation-node JSON**

Assert stable nodes such as `discovery.domain.research` and
`discovery.topic.visualization`, correct `member_ids`, default-hidden
supernode presentation, `draggable: members`, `collapse_effect: self`, and one
`skill-grouping` control with five ordered facets. Assert Topics uses
`activation: multiple`; other facets use `all`.

- [ ] **Step 2: Run focused adapter tests and verify RED**

```bash
python3 -m pytest -o pythonpath=src -q tests/test_blueprint_visualization.py -k presentation_node
```

- [ ] **Step 3: Implement the focused adapter**

Move only blueprint discovery extraction into `presentation_nodes.py`. Reuse
the existing eligibility, configured vocabulary, label, membership, and scope
logic. Emit generic instances and controls without `metadata_grouping` names.

- [ ] **Step 4: Integrate adapter output in `payload_builder.py`**

Assign the returned instances to top-level `presentation_nodes` and the returned
control list to `ui.presentation_node_controls`. Remove `ui.metadata_grouping`.

- [ ] **Step 5: Run adapter and graph tests and verify GREEN**

```bash
python3 -m pytest -o pythonpath=src -q tests/test_blueprint_visualization.py tests/test_visualization_graph.py
```

---

### Task 3: Replace the specialized renderer path with generic presentation nodes

**Files:**
- Create: `src/officina/common/visualization/html_renderer/runtime/presentation_nodes.js`
- Delete: `src/officina/common/visualization/html_renderer/runtime/metadata_grouping.js`
- Modify: `src/officina/common/visualization/html_renderer/assets.py`
- Modify: `src/officina/common/visualization/html_renderer/page.html`
- Modify: `src/officina/common/visualization/html_renderer/runtime/bootstrap.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/render_pipeline.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/geometry.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/node_renderer.js`
- Modify: `tests/test_visualization_browser.py`

**Interfaces:**
- Consumes: validated `docData.presentation_nodes` and `docData.ui.presentation_node_controls`.
- Produces: logical presentation-node registry, active-facet state, signature-compartment layout, SVG components, and generic presentation-node selection/details.

- [ ] **Step 1: Rewrite browser fixture and baseline assertions**

Replace `ui.metadata_grouping` with first-class presentation nodes and controls.
Assert that presentation nodes are absent from canonical `.graph-node` counts
while visible components carry `data-presentation-node-id`, keyboard role, and
shared logical selection identity.

- [ ] **Step 2: Run the browser test and verify RED**

```bash
python3 -m pytest -o pythonpath=src -q tests/test_visualization_browser.py
```

- [ ] **Step 3: Create the generic registry and control state**

Port the proven dimension/value state into generic control/facet/node state.
Use names such as `presentationNodeById`, `activePresentationFacetByControl`,
`enabledPresentationNodeIds`, `hiddenPresentationNodeIds`, and
`presentationNodeComponents`. Render controls only from JSON labels and modes.

- [ ] **Step 4: Port signature layout and shell rendering**

Compute signatures from enabled nodes' `member_ids`, move canonical root
subtrees rigidly, and render components using the shared supernode primitive.
Keep canonical edge routing and inventory unchanged. Rename the SVG layer to
`presentation-node-layer`.

- [ ] **Step 5: Add logical selection and inspection**

Clicking, Enter, or Space on any component selects the presentation-node ID,
highlights every component with that ID, and renders the instance's JSON-defined
details. Canonical selection remains unchanged when presentation selection is
cleared.

- [ ] **Step 6: Run the browser test and verify GREEN**

Run the command from Step 2.

---

### Task 4: Implement generic hide, restore, collapse, drag, history, and state

**Files:**
- Modify: `src/officina/common/visualization/html_renderer/runtime/presentation_nodes.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/interactions.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/graph_actions.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/viewer_state.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/controls.js`
- Modify: `src/officina/common/visualization/html_renderer/runtime/layout.js`
- Modify: `tests/test_visualization_browser.py`

**Interfaces:**
- Consumes: interaction capabilities on each presentation node.
- Produces: transactional presentation-node actions and viewer-state version 7.

- [ ] **Step 1: Add failing browser interactions**

Assert:

- hiding and restoring a presentation node affects only its components;
- self-collapse replaces shell components with header markers and leaves
  members visible;
- member drag translates every referenced root subtree once, including shared
  members, reroutes edges, and reshapes overlapping nodes;
- undo/redo and Reset cover facet, selection, hidden, collapsed, and drag state;
- version-7 state round-trips and version-6 grouping state migrates by stable IDs;
- stale or failed layouts roll back presentation and canonical position state.

- [ ] **Step 2: Run the browser test and verify RED**

Use the Task 3 browser command.

- [ ] **Step 3: Implement capability-driven actions**

Bind component events through the generic interaction fields. Keep presentation
hidden/collapsed sets separate from canonical `hiddenNodes` and
`collapsedContainers`. Deduplicate `member_ids` before translating roots.

- [ ] **Step 4: Replace metadata grouping state with version-7 presentation state**

Persist control facets, multiple selections, hidden nodes, collapsed nodes,
presentation selection, and manual member positions. Add a bounded v6 migration
that maps prior dimension/value shell IDs when matching presentation-node IDs
exist.

- [ ] **Step 5: Run browser and focused visualization tests and verify GREEN**

```bash
python3 -m pytest -o pythonpath=src -q tests/test_visualization_browser.py tests/test_visualization_graph.py tests/test_blueprint_visualization.py
```

---

### Task 5: Documentation, stress verification, and generated site

**Files:**
- Modify: `docs/visualization.md`
- Modify: `src/officina/common/visualization/html_renderer/README.md`
- Modify: `tests/test_docs_site.py` if generated-site assertions require generic names.

**Interfaces:**
- Consumes: the completed generic contract and blueprint instance.
- Produces: documented contract and browser-verified generated site.

- [ ] **Step 1: Replace metadata-grouping documentation**

Document `presentation_nodes`, `presentation_node_controls`, interaction effects,
canonical/presentation separation, overlap, default visibility, and the
blueprint adapter as one instance.

- [ ] **Step 2: Regenerate and run the real-graph interaction probes**

Build the graph, then exercise repeated facet activation/deactivation,
multi-select, presentation selection/inspection, collapse/expand, group drag,
canonical node drag while grouping is off, canonical hide/restore across facet
changes, presentation hide/restore, undo/redo, Reset, rapid changes, and injected
layout failure at desktop, tablet, and exact 390-pixel mobile sizes.

- [ ] **Step 3: Run the complete focused suite**

```bash
python3 -m pytest -o pythonpath=src -q \
  tests/test_blueprint_visualization.py \
  tests/test_visualization_graph.py \
  tests/test_visualization_browser.py \
  tests/test_docs_site.py
```

- [ ] **Step 4: Build the complete documentation site**

```bash
uv run --with-requirements requirements-docs.txt ./scripts/docs-site.py build
```

- [ ] **Step 5: Inspect desktop and mobile screenshots and check diff hygiene**

Run `git diff --check`, compare generated source/site graph hashes, and verify
the exact source diff contains no blueprint vocabulary in generic renderer
runtime files.
