# First-Class Presentation Nodes for Blueprint Grouping

**Date:** 2026-08-04
**Status:** Approved architecture; implementation pending

## Objective

Represent blueprint discovery groups as first-class, interactive instances in
the generic visualization JSON contract. The core HTML renderer must understand
only generic presentation nodes, referenced membership, visibility behavior,
interaction effects, and facet controls. Blueprint-specific field names and
vocabularies remain confined to `from_blueprint`.

The initial blueprint instance emits presentation nodes for:

- domain;
- topics;
- activation modes;
- persistent-modifier state; and
- catalog visibility.

Presentation nodes default to off. Enabling a facet arranges canonical skill
subtrees beneath supernode-style presentation nodes without duplicating or
re-parenting skills.

## Semantic layers

The payload has two first-class node collections with different semantic jobs.

### Canonical entities

`entities` remains the architectural graph. Canonical entities may participate
in structural containment through the single-parent `container` field and may
be canonical edge endpoints.

### Presentation nodes

`presentation_nodes` contains first-class view entities. A presentation node:

- has stable identity and inspector metadata;
- may be selected, searched, dragged, collapsed, hidden, restored, and persisted;
- references canonical root entities through many-to-many `member_ids`;
- cannot be a canonical edge endpoint or structural parent;
- does not enter architectural node counts, category filters, edge projection,
  certification, or blueprint dependency semantics; and
- never changes member visibility merely because the presentation node is
  hidden or collapsed.

This distinction prevents view instances from acquiring architectural meaning
while still making them proper JSON-defined interactive objects.

## Generic JSON contract

The graph schema gains a top-level `presentation_nodes` array and generic
presentation-node controls under `ui`.

```yaml
presentation_nodes:
  - id: discovery.topic.visualization
    type: presentation-group
    short_title: Visualization
    position: 0
    member_ids:
      - math-dependency-graph
      - technical-flow-review
    presentation:
      form: supernode
      tone: subtle
      default_visibility: hidden
    interaction:
      selectable: true
      inspectable: true
      draggable: members
      collapse_effect: self

ui:
  presentation_node_controls:
    - id: skill-grouping
      label: Skill grouping
      selector_label: Group skills by
      default_facet: null
      facets:
        - id: discovery.domain
          label: Domain
          activation: all
          node_ids:
            - discovery.domain.research
        - id: discovery.topics
          label: Topics
          activation: multiple
          node_ids:
            - discovery.topic.visualization
```

### Presentation-node fields

Each presentation node requires:

- `id`: globally unique among presentation nodes and disjoint from canonical
  entity IDs;
- `type`: open generic type used for presentation-node filtering and styling;
- `short_title`: display and inspector label;
- `position`: stable ordering key;
- `member_ids`: unique canonical root entity IDs; and
- `presentation`: renderer behavior described below.

Optional `details`, `description`, `ref`, and domain-neutral metadata use the
same inspector-compatible shapes as canonical entities.

`presentation.form` initially accepts `supernode`. The schema remains open to
future renderer-supported forms through versioned additions, not arbitrary
adapter strings. `tone` accepts the existing `subtle` and `strong` values.
`default_visibility` is `visible` or `hidden`.

### Interaction fields

The initial bounded interaction contract is:

- `selectable`: whether clicking or keyboard activation selects the logical
  presentation node;
- `inspectable`: whether selection opens its JSON-defined details;
- `draggable`: `none`, `self`, or `members`; and
- `collapse_effect`: `none` or `self`.

The blueprint grouping instances use `draggable: members`. Dragging any visible
component translates every currently rendered referenced root subtree exactly
once, then recomputes every affected presentation shell and canonical edge.
Shared members therefore move once; other overlapping presentation nodes
reshape around their updated members.

`collapse_effect: self` compacts only the presentation node's own shell
components into header markers. Referenced members remain visible and keep
their positions. Expanding restores shell components around the same members.
Hiding a presentation node removes its components and header markers while
leaving all members visible.

## Generic facet controls

`ui.presentation_node_controls` is an ordered array. Each control contains an
ordered facet list and selects at most one facet at a time.

A facet declares:

- its stable `id` and label;
- ordered `node_ids` referencing presentation nodes; and
- `activation` as `all` or `multiple`.

Selecting an `all` facet enables every referenced presentation node. Selecting
a `multiple` facet displays a multi-select and enables only chosen nodes. A
`null` `default_facet` starts the complete control off. The blueprint Topics
facet uses `multiple`; the other initial facets use `all`.

Facet activation controls presentation-node visibility but does not write to
canonical `hiddenNodes`. User hiding and facet deactivation are distinct state:
a returning facet restores its previously chosen nodes and their individual
hidden/collapsed states.

## Membership and overlap

`member_ids` is a non-owning many-to-many reference relation. It does not alter
`entity.container`, `parentByNode`, canonical containment, or edge endpoints.
One canonical skill root may be referenced by any number of presentation nodes.

Members must be canonical root entities. Their complete currently rendered
structural subtrees form rigid layout blocks. Child modules and behavioral
sources travel with their owning roots and are never independently listed as
presentation members.

For the active facet, the renderer computes each root's membership signature
over enabled presentation nodes. It packs disjoint signature compartments and
draws one shell component for every enabled presentation node represented in a
compartment. A logical presentation node may therefore have several visual
components but only one identity, selection, inspector record, hidden state,
and collapsed state.

Skills with an empty active signature remain visible in a deterministic
ungrouped region. No component may falsely enclose a visible nonmember merely
to force one rectangle.

## Renderer architecture

The current metadata-specific runtime is replaced by generic boundaries:

1. A presentation-node registry parses and indexes the JSON instances.
2. A generic control registry resolves active facets and presentation-node
   visibility.
3. A reference-group layout pass computes rigid blocks and signature
   compartments from `member_ids`.
4. A presentation-node renderer draws components with the shared supernode
   primitive and binds ordinary selection, inspection, keyboard, drag, hide,
   restore, and self-collapse interactions according to JSON capabilities.
5. Canonical rendering continues to own structural nodes and edges.

No core runtime identifier, label, conditional, or state key may mention
blueprints, skills, discovery, domain, topics, activation, modifier, catalog,
or metadata grouping.

## Blueprint adapter

`from_blueprint` remains the only layer that knows discovery metadata.
It derives eligible top-level skill roots from the canonical repository graph,
loads configured vocabulary order, and emits:

- one `presentation_nodes` instance for each nonempty discovery value; and
- one generic `presentation_node_controls` entry containing the five facets.

Stable presentation-node IDs combine the discovery field and configured value.
The adapter emits Boolean labels for persistent-modifier state and configured
human-readable labels for other values. Scoped graphs trim `member_ids` to
in-scope canonical roots and omit empty instances.

## Validation

JSON Schema validates local shape and enums. Semantic graph validation enforces:

- unique presentation-node IDs disjoint from canonical entity IDs;
- unique, nonempty `member_ids` referencing canonical root entities;
- unique control and facet IDs;
- valid, unique facet `node_ids`;
- one control owner for each presentation node used by controls;
- a valid or null default facet;
- interaction combinations supported by the declared form; and
- no canonical containment or edge targeting of presentation nodes.

Malformed presentation data fails before HTML generation. The generic renderer
does not infer or repair invalid membership.

## State and transactions

Viewer state advances to version 7 and stores presentation state separately
from canonical graph state:

- active facet per control;
- selected presentation-node IDs for `multiple` facets;
- user-hidden presentation nodes;
- collapsed presentation nodes;
- selected presentation-node identity; and
- manual member positions resulting from presentation-node drag.

Version-6 metadata-grouping state is migrated by stable facet and value IDs
when possible; unknown IDs are discarded. Older versions load with every
presentation control at its JSON default.

Facet changes and presentation-node interactions are transactional. Only the
latest asynchronous layout may commit. A failed layout restores the last
committed facet, presentation-node state, canonical positions, history, and
persistence record.

## Interaction invariants

- Enabling, disabling, hiding, or collapsing a presentation node never changes
  canonical node or edge inventory.
- Hiding or collapsing a presentation node never hides referenced members.
- Selecting one visual component selects every component of the same logical
  presentation node.
- Dragging a presentation node with `draggable: members` translates each
  referenced root subtree exactly once.
- Shared members may reshape other overlapping presentation nodes after drag.
- Canonical nodes retain their existing selection and inspector behavior.
- Presentation nodes never become canonical edge endpoints or projection
  representatives.
- Reset restores JSON defaults: no active blueprint grouping facet and all
  presentation nodes hidden.

## Testing

### Schema and validation

- Accept valid first-class presentation nodes and controls.
- Reject duplicate IDs, canonical-ID collisions, contained members, unknown
  members, unknown control references, duplicate control ownership, invalid
  defaults, and unsupported interaction combinations.

### Blueprint adapter

- Emit correct scalar, Boolean, and multi-valued instances.
- Preserve configured facet and node order.
- Support multiple memberships without duplicating canonical entities.
- Include hidden-catalog skills and trim scoped graphs.

### Browser behavior

- Start and Reset with grouping off.
- Enable all-value facets and multi-select topic nodes.
- Select, inspect, keyboard-activate, hide, restore, collapse, expand, and drag
  presentation nodes.
- Keep members visible through presentation-node hide and collapse.
- Translate shared members once and recompute overlapping shells after drag.
- Preserve canonical node/edge identity, counts, selection, filtering, and
  routing.
- Pass repeated activation, movement, hide/restore, undo/redo, persistence,
  rapid-change, and injected-failure stress sequences.
- Render without horizontal overflow at desktop, tablet, and exact 390-pixel
  mobile viewports.

## Migration boundaries

Remove the temporary `ui.metadata_grouping` payload contract and rename the
metadata-specific adapter/runtime/state concepts to the generic
presentation-node contract. Reuse the proven signature packing, rigid subtree
translation, shell rendering primitive, transactional layout, responsive
controls, and restore fallback.

Do not alter canonical blueprint entities, dependency edges, structural
containment, configured blueprint schemas, certification, or unrelated
standards work.

## Non-goals

This refactor does not:

- make presentation nodes architectural graph entities;
- permit canonical edges to target presentation nodes;
- introduce multiple structural parents;
- duplicate canonical skill nodes for overlapping memberships;
- hide members when a presentation node is hidden or collapsed;
- add arbitrary adapter-defined rendering scripts or CSS; or
- generalize beyond the bounded supernode/reference-group form and declared
  interactions required by this feature.
