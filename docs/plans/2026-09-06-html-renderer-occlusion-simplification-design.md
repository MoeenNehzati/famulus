# Proposal: eliminate computed edge-occlusion for large graphs

## Problem

`html_renderer`'s edge-occlusion masking (`geometry.js: refreshEdgeOcclusionMasks`) dims edges where
they pass under a node or label, so routes stay traceable. It works by testing every visible edge
against every visible node's bounding box (O(edges × nodes)) and rebuilding an SVG `<mask>` per edge
from scratch. On the 757-node / 907-edge repo dependency graph this is ~686K bounding-box tests plus
~1,500 forced-layout reads (`getBBox`/`getBoundingClientRect`), run synchronously on the main thread
on drag-end, routing changes, layout changes, and filter changes — this is the direct cause of the
page freezing during interaction.

Faster versions of the same computation (spatial index, incremental invalidation) reduce the constant
factor but keep the same shape of cost, and would need re-optimizing again as graphs grow further.
This proposal instead removes the computation for most of the graph.

## Logic

The masking system exists to solve a compositing problem — "make X appear to pass behind Y" — which
SVG's paint order and opacity already solve for free, without JS.

**Regular (non-container) nodes — the majority of nodes in any graph:**
- Reorder SVG layers so `edge-layer` paints *before* `node-layer`/`container-layer`
  (`page.html:171-174` currently has edges paint last, on top — that's why masking is needed today).
- Give plain node shapes a translucent fill instead of opaque.
- Result: an edge passing under a node is dimmed by ordinary alpha compositing, computed by the
  browser's rasterizer. Zero JS, zero per-edge work, no index, no dirty tracking, no recompute
  triggers to maintain.

**Containers** (rare — tens per graph, not hundreds): their decorative shell is deliberately near-
transparent (fill-opacity 0.055–0.16, so contents show through), which conflicts with wanting strong
edge-dimming (today: 0.74). Compositing alone can't satisfy both. Keep today's explicit mask technique
here, but scoped to containers × edges instead of all-nodes × edges. Because containers are rare, this
cross-product is cheap enough to just recompute in full on every relevant change — no incremental
tracking needed, since the expensive term (leaf-node count) is gone.

**Label text:** today gets extra-strong dimming (0.86) via a dedicated per-label mask. Labels have no
opaque backing — legibility already comes from a `text-shadow` halo (`viewer.css` ~line 805), the same
mechanism already relied on for gradient-filled nodes. Dropping label-specific masking and letting
labels dim the same as their parent node's shape removes this case entirely, at the cost of edges
under text being slightly more visible than today.

**Viewport culling** (mount/unmount DOM outside the visible area, so large graphs don't hold thousands
of live elements at once): a plain `O(n)` linear scan of node positions against the viewport rect,
run once per pan/zoom-settle (debounced), not per frame. No spatial index — a grid only pays for
itself against a large repeated cross-product, which no longer exists once per-node occlusion is free.
Requires event delegation (listeners on `nodeLayer`/`edgeLayer` instead of per-element) since mount/
unmount now happens on every viewport settle, not just at hide/show time.

**Drag / hide / show:** reroute only edges topologically incident to the changed node(s), via the
`incoming`/`outgoing` maps `layout.js` already builds. No geometric search needed for this either,
once occlusion doesn't depend on spatial proximity.

**Layout changes** (spacing/algorithm): unchanged — ELK reruns, full rebuild follows, same as today.

Net effect: no spatial index, no dirty-set, no invalidation taxonomy. What's left is either free
(compositing), naturally small (containers, viewport scan), or already-existing (incident-edge
rerouting) — nothing needs new machinery to stay fast as the graph grows.

## Pros

- Removes the O(edges × nodes) computation at its source instead of optimizing it — no ceiling to hit
  again as graphs grow past this one.
- Net reduction in code: `refreshEdgeOcclusionMasks`, `appendEdgeMaskBlocker`,
  `appendNodeEdgeMaskBlocker`, `boundsIntersect`, and `svgBoundsForElement`'s occlusion use shrink to a
  small container-only pass; no new indexing/dirty-tracking module is added to replace them.
- The remaining pieces (container masking, viewport scan, incident-edge rerouting) are each simple
  enough to reason about independently and don't require dirty-tracking discipline to stay correct.
- Event delegation, required anyway for frequent mount/unmount, also removes ~9,000 individually-bound
  listeners in favor of a handful — smaller and easier to audit on its own terms.

## Cons / risks

- **Visual behavior change, disclosed and accepted in this session:** edges under regular node shapes
  and under label text will be somewhat more visible than today's exact occlusion strengths (0.78/0.86).
  Tunable via node fill-opacity, but won't be pixel-identical to today.
- **Container occlusion keeps computed masking**, so it isn't a fully computation-free design — just a
  much smaller one. If a graph ever has an unusually large number of containers, this piece could
  become a cost center again (not expected for current adapters).
- **Not yet verified against presentation-node compartments** (`presentation_nodes.js`). Grep found no
  existing occlusion/masking logic there — presentation-node shells currently receive no dimming
  treatment at all (edges already render fully on top of them, un-composited). Flipping global paint
  order would newly dim edges under presentation-node shells too; this is expected to be a visual
  improvement (consistent with regular nodes) rather than a regression, but hasn't been checked
  against a live example with active presentation-node grouping.
- **Foreign-object labels and per-element listener volume for already-mounted elements are separate,
  pre-existing costs** this proposal doesn't touch beyond the delegation change — it targets the
  occlusion computation and DOM-count problem specifically, not overall paint cost per element.

## Out of scope

- Rendering substrate change (canvas/WebGL) — explicitly ruled out earlier in favor of staying on
  SVG/DOM.
- Semantic zoom / node clustering / LOD beyond viewport culling.
- Replacing `foreignObject` HTML labels with SVG `<text>`.
