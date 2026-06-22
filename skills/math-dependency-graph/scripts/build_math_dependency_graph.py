#!/usr/bin/env python3
"""Render an interactive math-dependency graph from canonical JSON.

This script does not infer mathematical structure from LaTeX. The JSON input is
the semantic source of truth and is expected to be authored by the model under
the accompanying ``SKILL.md`` contract.

Current active path:
- ``build_html_with_elk(...)`` generates a standalone HTML viewer that uses
  ``elkjs`` in the browser for layered layout and edge routing.
"""
from __future__ import annotations

import argparse
import html
import json
import copy
from collections import defaultdict
from pathlib import Path
from typing import Dict


TYPE_STYLES = {
    "standing-assumption": {"shape": "hexagon", "color": "#c0392b"},
    "local-assumption": {"shape": "diamond", "color": "#d35400"},
    "definition": {"shape": "roundrect", "color": "#2471a3"},
    "notation": {"shape": "parallelogram", "color": "#148f77"},
    "lemma": {"shape": "ellipse", "color": "#1e8449"},
    "proposition": {"shape": "rect", "color": "#7d6608"},
    "theorem": {"shape": "double-rect", "color": "#6c3483"},
    "corollary": {"shape": "circle", "color": "#b7950b"},
    "remark": {"shape": "rect", "color": "#616a6b"},
}

EDGE_PALETTE = [
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#6A3D9A",
    "#111111",
]


def validate_document(doc: dict) -> None:
    """Validate the minimal schema expected by the renderer.

    The renderer intentionally performs only structural checks. Semantic
    decisions such as node selection, direct-dependency judgment, proof-use
    descriptions, and MathJax-ready text belong in the JSON authoring step.
    """
    if not isinstance(doc, dict):
        raise SystemExit("Top-level JSON must be an object.")
    if "entities" not in doc or not isinstance(doc["entities"], list):
        raise SystemExit("JSON must contain an 'entities' list.")
    document_meta = doc.get("document", {})
    if document_meta and not isinstance(document_meta, dict):
        raise SystemExit("'document' must be an object when present.")
    mathjax_macros = document_meta.get("mathjax_macros", {})
    if mathjax_macros and not isinstance(mathjax_macros, dict):
        raise SystemExit("'document.mathjax_macros' must be an object when present.")

    seen_ids = set()
    for idx, entity in enumerate(doc["entities"], start=1):
        if not isinstance(entity, dict):
            raise SystemExit(f"Entity {idx} must be an object.")
        for key in ("id", "type", "label", "within_document_number", "within_document_pos", "depends_on"):
            if key not in entity:
                raise SystemExit(f"Entity {idx} is missing required key '{key}'.")
        if entity["id"] in seen_ids:
            raise SystemExit(f"Duplicate entity id: {entity['id']}")
        seen_ids.add(entity["id"])
        if not isinstance(entity["depends_on"], list):
            raise SystemExit(f"Entity '{entity['id']}' has non-list 'depends_on'.")

    for entity in doc["entities"]:
        for dep in entity["depends_on"]:
            if not isinstance(dep, dict):
                raise SystemExit(f"Dependency entry on '{entity['id']}' must be an object.")
            if "id" not in dep:
                raise SystemExit(f"Dependency entry on '{entity['id']}' is missing 'id'.")
            if dep["id"] not in seen_ids:
                raise SystemExit(f"Dependency '{dep['id']}' referenced by '{entity['id']}' is not defined.")

def reduce_transitive_edges(doc: dict) -> tuple[dict, list[dict]]:
    """Apply graph-theoretic transitive reduction to the rendered view only.

    This operates on the directed dependency graph encoded in ``depends_on`` and
    removes an edge ``A -> B`` when there is already another directed path from
    ``A`` to ``B``. The source JSON on disk is not modified.

    The reduction is purely graph-theoretic. It improves readability, but it is
    not a substitute for mathematical review of whether a dependency is truly
    direct.
    """
    reduced = copy.deepcopy(doc)
    entities = reduced["entities"]
    entity_map = {entity["id"]: entity for entity in entities}

    adjacency: Dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        for dep in entity.get("depends_on", []):
            adjacency[dep["id"]].add(entity["id"])

    def has_alternate_path(source_id: str, target_id: str) -> bool:
        stack = [child for child in adjacency.get(source_id, set()) if child != target_id]
        seen = {source_id}
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current == target_id:
                return True
            stack.extend(adjacency.get(current, set()))
        return False

    removed: list[dict] = []
    for entity in entities:
        kept = []
        for dep in entity.get("depends_on", []):
            source_id = dep["id"]
            target_id = entity["id"]
            if has_alternate_path(source_id, target_id):
                removed.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "source_label": entity_map[source_id]["label"],
                        "target_label": entity["label"],
                        "dependency": dep,
                    }
                )
            else:
                kept.append(dep)
        entity["depends_on"] = kept

    return reduced, removed


def build_html_with_elk(doc: dict, reduction_note: str = "") -> str:
    """Build the active standalone HTML viewer using browser-side ELK layout.

    Responsibilities of this renderer:
    - validate and serialize the canonical JSON graph
    - hand node/edge structure to ``elkjs`` for layered layout
    - render the interactive viewer shell
    - preserve viewer state such as hidden categories, removed nodes, selected
      node, ancestor-focus mode, and panel collapse state in ``localStorage``

    Non-responsibilities:
    - inferring entities or dependencies from TeX
    - repairing malformed math strings
    - upgrading heuristic semantic content in the JSON
    """
    entities = doc["entities"]
    mathjax_macros = doc.get("document", {}).get("mathjax_macros", {})
    entity_map = {entity["id"]: entity for entity in entities}
    edges = []
    for node in entities:
        for dep in node.get("depends_on", []):
            edges.append({"source": dep["id"], "target": node["id"], **dep})

    render_type_overrides = {}

    def resolved_render_type(node_id: str, seen: set[str] | None = None) -> str:
        if node_id in render_type_overrides:
            return render_type_overrides[node_id]
        node = entity_map[node_id]
        if node["type"] != "corollary":
            return node["type"]
        if seen is None:
            seen = set()
        seen.add(node_id)
        for dep in node.get("depends_on", []):
            parent_id = dep["id"]
            if parent_id in seen:
                continue
            parent = entity_map.get(parent_id)
            if not parent or parent["type"] == "remark":
                continue
            parent_render_type = resolved_render_type(parent_id, seen.copy())
            render_type_overrides[node_id] = parent_render_type
            return parent_render_type
        render_type_overrides[node_id] = node["type"]
        return node["type"]

    for node in entities:
        if node["type"] == "corollary":
            resolved_render_type(node["id"])

    edge_payload = []
    for idx, edge in enumerate(edges, start=1):
        edge_payload.append({"edge_id": f"edge_{idx}", **edge})

    payload = html.escape(json.dumps(doc, indent=2))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Math dependency graph</title>
  <script>
    window.MathJax = {{
      tex: {{
        macros: {json.dumps(mathjax_macros, indent=10)},
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
      }},
      svg: {{
        fontCache: 'global'
      }}
    }};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/elkjs/lib/elk.bundled.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      background: linear-gradient(135deg, #f9f7f1 0%, #eef3f7 100%);
      color: #17202a;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 1fr 360px;
      min-height: 100vh;
      transition: grid-template-columns 160ms ease;
    }}
    .layout.panel-collapsed {{
      grid-template-columns: 1fr 44px;
    }}
    .canvas-wrap {{
      overflow: auto;
      padding: 24px;
    }}
    .panel {{
      border-left: 1px solid #d5d8dc;
      background: rgba(255,255,255,0.9);
      backdrop-filter: blur(8px);
      padding: 20px;
      box-sizing: border-box;
      position: relative;
      overflow: hidden;
    }}
    .layout.panel-collapsed .panel {{
      padding: 20px 10px 20px 8px;
    }}
    .panel-toggle {{
      position: absolute;
      top: 12px;
      left: 12px;
      width: 28px;
      height: 28px;
      border: 1px solid #aeb6bf;
      background: #f8f9f9;
      color: #17202a;
      border-radius: 999px;
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
    }}
    .panel-content {{
      margin-top: 28px;
    }}
    .layout.panel-collapsed .panel-content {{
      display: none;
    }}
    .collapsed-label {{
      display: none;
      writing-mode: vertical-rl;
      transform: rotate(180deg);
      letter-spacing: 0.08em;
      font-size: 0.8rem;
      color: #566573;
      margin: 44px auto 0;
      user-select: none;
    }}
    .layout.panel-collapsed .collapsed-label {{
      display: block;
    }}
    h1 {{
      font-size: 1.35rem;
      margin: 0 0 0.75rem;
    }}
    .legend {{
      display: grid;
      gap: 8px;
      margin-bottom: 1rem;
      font-size: 0.95rem;
    }}
    .legend-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      user-select: none;
    }}
    .legend-row.inactive {{
      opacity: 0.4;
    }}
    .legend-icon {{
      width: 24px;
      height: 20px;
      flex: 0 0 auto;
    }}
    .small {{
      color: #566573;
      font-size: 0.9rem;
    }}
    .graph-node {{
      cursor: pointer;
      transition: filter 0.12s ease, transform 0.12s ease, opacity 0.12s ease;
    }}
    .graph-node.hovered {{
      filter: brightness(1.18) saturate(1.08) drop-shadow(0 10px 22px rgba(0,0,0,0.26));
      transform: translateY(-2px) scale(1.02);
    }}
    .node-label {{
      display: block;
      text-align: center;
      font-size: 12px;
      font-weight: 700;
      color: #fff;
      line-height: 1.1;
      padding: 0 8px;
      box-sizing: border-box;
      white-space: normal;
    }}
    .node-subtitle {{
      text-align: center;
      font-size: 11px;
      color: rgba(255,255,255,0.92);
      line-height: 1.1;
      padding: 0 8px;
      box-sizing: border-box;
    }}
    .node-fo-body {{
      width: 210px;
      height: 68px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: stretch;
      pointer-events: none;
    }}
    #tooltip {{
      position: fixed;
      display: none;
      pointer-events: none;
      max-width: 320px;
      padding: 10px 12px;
      border-radius: 10px;
      background: rgba(17, 24, 39, 0.92);
      color: #f9fafb;
      font-size: 0.85rem;
      box-shadow: 0 12px 30px rgba(0,0,0,0.18);
      z-index: 10;
    }}
    .removed-list {{
      display: grid;
      gap: 6px;
      margin-top: 0.5rem;
    }}
    .removed-item {{
      border: 1px solid #d5d8dc;
      background: #f8f9f9;
      border-radius: 8px;
      padding: 8px 10px;
      cursor: pointer;
    }}
    .removed-item:hover {{
      background: #eef3f7;
    }}
    .removed-item-number {{
      font-size: 0.8rem;
      color: #566573;
    }}
    .focus-toggle {{
      display: inline-block;
      margin: 0.5rem 0 0.9rem;
      padding: 6px 10px;
      border: 1px solid #aeb6bf;
      border-radius: 999px;
      background: #f8f9f9;
      color: #17202a;
      cursor: pointer;
      font-size: 0.9rem;
    }}
    .focus-toggle.active {{
      background: #e8f1fb;
      border-color: #6a8fb7;
    }}
    code {{
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
      font-size: 0.85rem;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8f9f9;
      padding: 12px;
      border-radius: 8px;
      max-height: 240px;
      overflow: auto;
    }}
    svg {{
      overflow: visible;
    }}
    .edge-path {{
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      opacity: 0.92;
      transition: stroke-width 0.12s ease, opacity 0.12s ease, filter 0.12s ease, stroke 0.12s ease;
    }}
    .elk-status {{
      font-size: 0.95rem;
      color: #566573;
      margin-bottom: 1rem;
    }}
    mjx-container[jax="SVG"][display="false"] {{
      margin: 0 0.12em;
    }}
  </style>
</head>
<body>
  <div id="tooltip"></div>
  <div class="layout" id="layout">
    <div class="canvas-wrap">
      <div id="elk-status" class="elk-status">Rendering graph layout...</div>
      <svg id="graph-svg" width="1200" height="800" viewBox="0 0 1200 800">
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="5" refX="5.4" refY="2.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L6,2.5 L0,5 z" fill="context-stroke"></path>
          </marker>
        </defs>
        <g id="edge-layer"></g>
        <g id="node-layer"></g>
      </svg>
    </div>
    <aside class="panel">
      <button class="panel-toggle" id="panel-toggle" type="button" aria-expanded="true" aria-controls="panel-content" title="Collapse side panel">⟩</button>
      <div class="collapsed-label">Panel</div>
      <div class="panel-content" id="panel-content">
        <h1>Math dependency graph</h1>
        <div class="small" style="margin-top: 0.35rem;">Cheat sheet:</div>
        <div class="small" style="margin-top: 0.25rem;">Hover or click a node or edge to inspect more information.</div>
        <div class="small" style="margin-top: 0.25rem;">Click a sign in the legend to hide or restore that category of nodes.</div>
        <div class="small" style="margin-top: 0.25rem;">Double-click a visible node to hide it. Double-click it again in the removed-nodes pane below to restore it.</div>
        <div class="small" style="margin-top: 0.25rem;">Click a node to select it, then use the <code>Highlight ancestors</code> button or press <code>h</code> to toggle ancestor focus.</div>
        <div class="small" style="margin-top: 0.25rem;">Dashed edges mean the dependency is inferred rather than stated explicitly.</div>
        <div class="small" style="margin: 0.5rem 0 0;">This HTML renders the canonical JSON inferred from the document.</div>
        {f'<div class="small" style="margin-top: 0.35rem;">{html.escape(reduction_note)}</div>' if reduction_note else ''}
        <div class="small" style="margin: 0.35rem 0 0.8rem;">Dependencies run left-to-right.</div>
        <div class="legend" id="legend"></div>
        <div id="details" class="small">Select a node or edge to inspect its metadata.</div>
        <button id="focus-toggle" class="focus-toggle" type="button">Highlight ancestors</button>
        <h2>Raw JSON</h2>
        <pre><code>{payload}</code></pre>
        <h2>Removed nodes</h2>
        <div id="removed-nodes" class="removed-list small">None</div>
      </div>
    </aside>
  </div>
  <script>
    const docData = {json.dumps(doc, indent=2)};
    const typeStyles = {json.dumps(TYPE_STYLES, indent=2)};
    const edgePalette = {json.dumps(EDGE_PALETTE, indent=2)};
    const edgeData = {json.dumps(edge_payload, indent=2)};
    const renderTypeOverrides = {json.dumps(render_type_overrides, indent=2)};
    const layoutEl = document.getElementById("layout");
    const panelToggle = document.getElementById("panel-toggle");
    const svg = document.getElementById("graph-svg");
    const edgeLayer = document.getElementById("edge-layer");
    const nodeLayer = document.getElementById("node-layer");
    const tooltip = document.getElementById("tooltip");
    const details = document.getElementById("details");
    const legend = document.getElementById("legend");
    const removedNodesEl = document.getElementById("removed-nodes");
    const focusToggle = document.getElementById("focus-toggle");
    const elkStatus = document.getElementById("elk-status");
    const entityMap = new Map(docData.entities.map(entity => [entity.id, entity]));
    const outgoing = new Map();
    const incoming = new Map();
    const hiddenTypes = new Set();
    const hiddenNodes = new Set();
    const nodeTypes = new Map(docData.entities.map(entity => [entity.id, entity.type]));
    let selectedNodeId = null;
    let ancestorFocusEnabled = false;
    const nodeColorIndex = new Map(
      docData.entities
        .slice()
        .sort((a, b) => {{
          const aPos = a.within_document_pos || 0;
          const bPos = b.within_document_pos || 0;
          if (aPos !== bPos) return aPos - bPos;
          return a.label.localeCompare(b.label);
        }})
        .map((entity, idx) => [entity.id, idx])
    );
    edgeData.forEach(edge => {{
      if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
      outgoing.get(edge.source).push(edge);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      incoming.get(edge.target).push(edge);
    }});
    let renderVersion = 0;
    let lastRenderedEdges = [];
    let lastNodePositions = new Map();
    let elk = null;
    const viewerStateKey = `math-dependency-graph::${{docData.document?.source_file || docData.document?.title || "document"}}`;

    function saveViewerState() {{
      try {{
        const payload = {{
          hiddenTypes: Array.from(hiddenTypes),
          hiddenNodes: Array.from(hiddenNodes),
          selectedNodeId,
          ancestorFocusEnabled,
          panelCollapsed: layoutEl.classList.contains("panel-collapsed")
        }};
        window.localStorage.setItem(viewerStateKey, JSON.stringify(payload));
      }} catch (error) {{
        // Ignore storage failures.
      }}
    }}

    function restoreViewerState() {{
      try {{
        const raw = window.localStorage.getItem(viewerStateKey);
        if (!raw) return;
        const payload = JSON.parse(raw);
        (payload.hiddenTypes || []).forEach(type => {{
          if (typeStyles[type]) hiddenTypes.add(type);
        }});
        (payload.hiddenNodes || []).forEach(nodeId => {{
          if (entityMap.has(nodeId)) hiddenNodes.add(nodeId);
        }});
        if (payload.selectedNodeId && entityMap.has(payload.selectedNodeId)) {{
          selectedNodeId = payload.selectedNodeId;
        }}
        ancestorFocusEnabled = !!payload.ancestorFocusEnabled;
        if (payload.panelCollapsed) {{
          layoutEl.classList.add("panel-collapsed");
        }}
      }} catch (error) {{
        // Ignore malformed saved state.
      }}
    }}

    function syncPanelToggle() {{
      const collapsed = layoutEl.classList.contains("panel-collapsed");
      panelToggle.textContent = collapsed ? "⟨" : "⟩";
      panelToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      panelToggle.setAttribute("title", collapsed ? "Expand side panel" : "Collapse side panel");
    }}

    panelToggle.addEventListener("click", () => {{
      layoutEl.classList.toggle("panel-collapsed");
      syncPanelToggle();
      saveViewerState();
    }});
    restoreViewerState();
    syncPanelToggle();

    function escapeHtml(text) {{
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }}

    function typesetElement(element) {{
      if (!window.MathJax || !window.MathJax.typesetPromise) return;
      window.MathJax.typesetClear && window.MathJax.typesetClear([element]);
      window.MathJax.typesetPromise([element]).catch(() => {{}});
    }}

    function createSvgElement(name) {{
      return document.createElementNS("http://www.w3.org/2000/svg", name);
    }}

    function createLegendIcon(type, style) {{
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 20");
      svg.setAttribute("class", "legend-icon");
      const shape = style.shape || "rect";
      const stroke = "#1f2933";
      function add(el) {{
        el.setAttribute("fill", style.color);
        el.setAttribute("stroke", stroke);
        el.setAttribute("stroke-width", "1.6");
        svg.appendChild(el);
      }}
      if (shape === "ellipse") {{
        const el = createSvgElement("ellipse");
        el.setAttribute("cx", "12");
        el.setAttribute("cy", "10");
        el.setAttribute("rx", "10");
        el.setAttribute("ry", "7");
        add(el);
        return svg;
      }}
      if (shape === "circle") {{
        const el = createSvgElement("circle");
        el.setAttribute("cx", "12");
        el.setAttribute("cy", "10");
        el.setAttribute("r", "7");
        add(el);
        return svg;
      }}
      if (shape === "diamond") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "12,2 22,10 12,18 2,10");
        add(el);
        return svg;
      }}
      if (shape === "hexagon") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 18,2 22,10 18,18 6,18 2,10");
        add(el);
        return svg;
      }}
      if (shape === "parallelogram") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 22,2 18,18 2,18");
        add(el);
        return svg;
      }}
      if (shape === "roundrect") {{
        const el = createSvgElement("rect");
        el.setAttribute("x", "2");
        el.setAttribute("y", "2");
        el.setAttribute("width", "20");
        el.setAttribute("height", "16");
        el.setAttribute("rx", "5");
        el.setAttribute("ry", "5");
        add(el);
        return svg;
      }}
      if (shape === "double-rect") {{
        const outer = createSvgElement("rect");
        outer.setAttribute("x", "2");
        outer.setAttribute("y", "2");
        outer.setAttribute("width", "20");
        outer.setAttribute("height", "16");
        add(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", "4.5");
        inner.setAttribute("y", "4.5");
        inner.setAttribute("width", "15");
        inner.setAttribute("height", "11");
        inner.setAttribute("fill", "none");
        inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "1.4");
        svg.appendChild(inner);
        return svg;
      }}
      const el = createSvgElement("rect");
      el.setAttribute("x", "2");
      el.setAttribute("y", "2");
      el.setAttribute("width", "20");
      el.setAttribute("height", "16");
      add(el);
      return svg;
    }}

    Object.entries(typeStyles).forEach(([type, style]) => {{
      const row = document.createElement("div");
      row.className = "legend-row";
      row.dataset.type = type;
      row.appendChild(createLegendIcon(type, style));
      const label = document.createElement("div");
      label.textContent = type;
      row.appendChild(label);
      row.addEventListener("click", () => {{
        if (hiddenTypes.has(type)) {{
          hiddenTypes.delete(type);
          row.classList.remove("inactive");
        }} else {{
          hiddenTypes.add(type);
          row.classList.add("inactive");
        }}
        saveViewerState();
        updateVisibility();
      }});
      if (hiddenTypes.has(type)) {{
        row.classList.add("inactive");
      }}
      legend.appendChild(row);
    }});

    function isHiddenNode(nodeId) {{
      const type = nodeTypes.get(nodeId);
      return hiddenTypes.has(type) || hiddenNodes.has(nodeId);
    }}

    function renderRemovedNodes() {{
      const removedEntities = docData.entities
        .filter(entity => hiddenNodes.has(entity.id))
        .sort((a, b) => (a.within_document_pos || 0) - (b.within_document_pos || 0) || a.label.localeCompare(b.label));
      removedNodesEl.innerHTML = "";
      if (removedEntities.length === 0) {{
        removedNodesEl.textContent = "None";
        return;
      }}
      removedEntities.forEach(entity => {{
        const item = document.createElement("div");
        item.className = "removed-item";
        item.innerHTML = `
          <div><strong>${{escapeHtml(entity.label)}}</strong></div>
          <div class="removed-item-number">${{escapeHtml(entity.within_document_number || "")}}</div>
        `;
        item.addEventListener("dblclick", () => {{
          hiddenNodes.delete(entity.id);
          saveViewerState();
          updateVisibility();
        }});
        item.addEventListener("click", () => {{
          details.innerHTML = formatEntity(entity);
          typesetElement(details);
        }});
        removedNodesEl.appendChild(item);
      }});
      typesetElement(removedNodesEl);
    }}

    function collectAncestors(nodeId) {{
      const keep = new Set();
      const stack = [nodeId];
      while (stack.length) {{
        const current = stack.pop();
        if (!current || keep.has(current)) continue;
        keep.add(current);
        for (const edge of incoming.get(current) || []) {{
          stack.push(edge.source);
        }}
      }}
      return keep;
    }}

    function syncFocusToggle() {{
      const hasSelection = !!selectedNodeId && !isHiddenNode(selectedNodeId);
      focusToggle.disabled = !hasSelection;
      focusToggle.classList.toggle("active", hasSelection && ancestorFocusEnabled);
      focusToggle.textContent = hasSelection && ancestorFocusEnabled ? "Show full graph" : "Highlight ancestors";
      focusToggle.style.opacity = hasSelection ? "1" : "0.5";
      focusToggle.style.cursor = hasSelection ? "pointer" : "default";
    }}

    function formatEntity(entity) {{
      const shortDescription = escapeHtml(entity.short_description || "");
      const deps = (entity.depends_on || []).map(dep => {{
        const other = entityMap.get(dep.id);
        const name = other ? other.label : dep.id;
        return `<li><strong>${{escapeHtml(name)}}</strong> <code>${{escapeHtml(dep.use_type || "")}}</code><br>${{escapeHtml(dep.description || "")}}<br><span class="small">${{escapeHtml(dep.confidence || "")}} | ${{escapeHtml(dep.evidence || "")}}</span></li>`;
      }}).join("");
      const locRaw = entity.location || "";
      const locText = typeof locRaw === "string" ? locRaw : (locRaw.line_start ? `${{locRaw.file || ""}} lines ${{locRaw.line_start}}-${{locRaw.line_end || locRaw.line_start}}` : (locRaw.file || ""));
      return `
        <h2>${{escapeHtml(entity.label)}}</h2>
        <div><strong>Within document:</strong> ${{escapeHtml(entity.within_document_number || "")}}</div>
        <div><strong>Line:</strong> ${{escapeHtml(String(entity.within_document_pos || ""))}}</div>
        <div><strong>Type:</strong> <code>${{escapeHtml(entity.type)}}</code></div>
        <div><strong>Title:</strong> ${{escapeHtml(entity.title || "None")}}</div>
        <div><strong>Scope:</strong> ${{escapeHtml(entity.scope || "unknown")}}</div>
        <div><strong>Origin:</strong> ${{escapeHtml(entity.origin || "n/a")}}</div>
        <div><strong>Location:</strong> ${{escapeHtml(locText)}}</div>
        <p>${{shortDescription}}</p>
        <h3>Direct dependencies</h3>
        <ul>${{deps || "<li>None</li>"}}</ul>
      `;
    }}

    function tooltipText(entity) {{
      return `
        <strong>${{escapeHtml(entity.label)}}</strong><br>
        ${{escapeHtml(entity.within_document_number || "")}}<br>
        line ${{escapeHtml(String(entity.within_document_pos || ""))}}<br>
        ${{escapeHtml(entity.type)}}<br>
        ${{escapeHtml(entity.short_description || "")}}
      `;
    }}

    function edgeTooltipText(edge) {{
      const source = entityMap.get(edge.source);
      const target = entityMap.get(edge.target);
      const sourceLabel = source ? source.label : edge.source;
      const targetLabel = target ? target.label : edge.target;
      return `
        <strong>${{escapeHtml(sourceLabel)}} → ${{escapeHtml(targetLabel)}}</strong><br>
        <code>${{escapeHtml(edge.use_type || "")}}</code><br>
        ${{escapeHtml(edge.description || "")}}<br>
        <span class="small">${{escapeHtml(edge.confidence || "")}} | ${{escapeHtml(edge.evidence || "")}}</span>
      `;
    }}

    function confidenceRank(value) {{
      if (value === "explicit") return 3;
      if (value === "inferred") return 2;
      if (value === "unclear") return 1;
      return 0;
    }}

    function bridgeEdge(sourceId, targetId, hiddenPath, seedEdge) {{
      const hiddenLabels = hiddenPath.map(id => entityMap.get(id)?.label || id);
      return {{
        source: sourceId,
        target: targetId,
        use_type: "hidden-bridge",
        description: `Preserves causal reachability across hidden nodes: ${{hiddenLabels.join(" -> ")}}.`,
        confidence: seedEdge?.confidence || "inferred",
        evidence: `Bridge path through hidden categories: ${{hiddenLabels.join(" -> ")}}`,
        bridge: true
      }};
    }}

    function computeVisibleEdges() {{
      const rendered = new Map();
      function addRendered(edge) {{
        const key = `${{edge.source}}->${{edge.target}}`;
        const existing = rendered.get(key);
        if (!existing) {{
          rendered.set(key, edge);
          return;
        }}
        const existingScore = (existing.bridge ? 0 : 10) + confidenceRank(existing.confidence);
        const newScore = (edge.bridge ? 0 : 10) + confidenceRank(edge.confidence);
        if (newScore > existingScore) {{
          rendered.set(key, edge);
        }}
      }}
      function traverse(sourceId, currentId, seedEdge, hiddenPath, seenHidden) {{
        if (currentId === sourceId) return;
        if (!entityMap.has(currentId)) return;
        if (!isHiddenNode(currentId)) {{
          if (hiddenPath.length === 0 && seedEdge && sourceId === seedEdge.source && currentId === seedEdge.target) {{
            addRendered({{...seedEdge, bridge: false}});
          }} else {{
            addRendered(bridgeEdge(sourceId, currentId, hiddenPath, seedEdge));
          }}
          return;
        }}
        if (seenHidden.has(currentId)) return;
        const nextSeen = new Set(seenHidden);
        nextSeen.add(currentId);
        for (const outEdge of outgoing.get(currentId) || []) {{
          traverse(sourceId, outEdge.target, seedEdge || outEdge, hiddenPath.concat(currentId), nextSeen);
        }}
      }}
      docData.entities.forEach(entity => {{
        if (isHiddenNode(entity.id)) return;
        for (const outEdge of outgoing.get(entity.id) || []) {{
          traverse(entity.id, outEdge.target, outEdge, [], new Set());
        }}
      }});
      return Array.from(rendered.values());
    }}

    function edgeColorForTarget(targetId) {{
      const idx = nodeColorIndex.get(targetId) || 0;
      return edgePalette[idx % edgePalette.length];
    }}

    function nodeStyle(entity) {{
      if (entity.type === "corollary" && renderTypeOverrides[entity.id] && renderTypeOverrides[entity.id] !== "corollary") {{
        return {{
          shape: typeStyles[renderTypeOverrides[entity.id]].shape,
          color: typeStyles.corollary.color
        }};
      }}
      const style = typeStyles[entity.type] || {{ shape: "rect", color: "#566573" }};
      return {{ shape: style.shape, color: style.color }};
    }}

    function ensureElk() {{
      if (elk) return elk;
      if (typeof ELK === "undefined") return null;
      elk = new ELK();
      return elk;
    }}

    async function computeLayout(visibleEntities, visibleEdges) {{
      const elkInstance = ensureElk();
      if (!elkInstance) {{
        throw new Error("ELK failed to load. This renderer needs the elkjs browser bundle.");
      }}
      const graph = {{
        id: "root",
        layoutOptions: {{
          "elk.algorithm": "layered",
          "elk.direction": "RIGHT",
          "elk.edgeRouting": "ORTHOGONAL",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.separateConnectedComponents": "true",
          "elk.spacing.nodeNode": "46",
          "elk.layered.spacing.nodeNodeBetweenLayers": "90",
          "elk.layered.spacing.edgeNodeBetweenLayers": "40",
          "elk.padding": "[left=40,top=40,right=40,bottom=40]"
        }},
        children: visibleEntities.map(entity => ({{
          id: entity.id,
          width: 210,
          height: 68
        }})),
        edges: visibleEdges.map((edge, idx) => ({{
          id: `elk_edge_${{idx}}`,
          sources: [edge.source],
          targets: [edge.target]
        }}))
      }};
      return elkInstance.layout(graph);
    }}

    function pointsForSection(section) {{
      return [section.startPoint, ...(section.bendPoints || []), section.endPoint].filter(Boolean);
    }}

    function roundedPathForPoints(points, radius = 14) {{
      if (!points.length) return "";
      if (points.length < 3) {{
        let d = `M ${{points[0].x}} ${{points[0].y}}`;
        for (let i = 1; i < points.length; i += 1) {{
          d += ` L ${{points[i].x}} ${{points[i].y}}`;
        }}
        return d;
      }}

      let d = `M ${{points[0].x}} ${{points[0].y}}`;
      for (let i = 1; i < points.length - 1; i += 1) {{
        const prev = points[i - 1];
        const curr = points[i];
        const next = points[i + 1];
        const inDx = curr.x - prev.x;
        const inDy = curr.y - prev.y;
        const outDx = next.x - curr.x;
        const outDy = next.y - curr.y;
        const inLen = Math.hypot(inDx, inDy);
        const outLen = Math.hypot(outDx, outDy);
        if (inLen < 1e-6 || outLen < 1e-6) {{
          d += ` L ${{curr.x}} ${{curr.y}}`;
          continue;
        }}
        const r = Math.min(radius, inLen / 2, outLen / 2);
        const p1x = curr.x - (inDx / inLen) * r;
        const p1y = curr.y - (inDy / inLen) * r;
        const p2x = curr.x + (outDx / outLen) * r;
        const p2y = curr.y + (outDy / outLen) * r;
        d += ` L ${{p1x}} ${{p1y}} Q ${{curr.x}} ${{curr.y}} ${{p2x}} ${{p2y}}`;
      }}
      const last = points[points.length - 1];
      d += ` L ${{last.x}} ${{last.y}}`;
      return d;
    }}

    function mergedTargetPoints(edge, points, targetCounts) {{
      if ((targetCounts.get(edge.target) || 0) <= 1) return points;
      if (points.length < 2) return points;
      const targetPos = lastNodePositions.get(edge.target);
      if (!targetPos) return points;
      const mergedEnd = {{
        x: targetPos.x,
        y: targetPos.y + targetPos.height / 2
      }};
      const mergedEntry = {{
        x: targetPos.x - 30,
        y: mergedEnd.y
      }};
      const updated = points.map(point => ({{ x: point.x, y: point.y }}));
      if (updated.length === 2) {{
        return [updated[0], mergedEntry, mergedEnd];
      }}
      updated[updated.length - 2] = mergedEntry;
      updated[updated.length - 1] = mergedEnd;
      return updated;
    }}

    function renderNode(entity, position) {{
      const style = nodeStyle(entity);
      const x = position.x;
      const y = position.y;
      const w = 210;
      const h = 68;
      const stroke = "#1f2933";
      const group = createSvgElement("g");
      group.setAttribute("class", "graph-node");
      group.dataset.nodeId = entity.id;

      let shapeEl = null;
      if (style.shape === "ellipse") {{
        shapeEl = createSvgElement("ellipse");
        shapeEl.setAttribute("cx", x + w / 2);
        shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("rx", w / 2);
        shapeEl.setAttribute("ry", h / 2);
      }} else if (style.shape === "circle") {{
        shapeEl = createSvgElement("circle");
        shapeEl.setAttribute("cx", x + w / 2);
        shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("r", Math.min(w, h) / 2);
      }} else if (style.shape === "diamond") {{
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${{x + w / 2}},${{y}} ${{x + w}},${{y + h / 2}} ${{x + w / 2}},${{y + h}} ${{x}},${{y + h / 2}}`);
      }} else if (style.shape === "hexagon") {{
        shapeEl = createSvgElement("polygon");
        const inset = 26;
        shapeEl.setAttribute("points", `${{x + inset}},${{y}} ${{x + w - inset}},${{y}} ${{x + w}},${{y + h / 2}} ${{x + w - inset}},${{y + h}} ${{x + inset}},${{y + h}} ${{x}},${{y + h / 2}}`);
      }} else if (style.shape === "parallelogram") {{
        shapeEl = createSvgElement("polygon");
        const skew = 20;
        shapeEl.setAttribute("points", `${{x + skew}},${{y}} ${{x + w}},${{y}} ${{x + w - skew}},${{y + h}} ${{x}},${{y + h}}`);
      }} else if (style.shape === "roundrect") {{
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x);
        shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w);
        shapeEl.setAttribute("height", h);
        shapeEl.setAttribute("rx", 18);
        shapeEl.setAttribute("ry", 18);
      }} else if (style.shape === "double-rect") {{
        const outer = createSvgElement("rect");
        outer.setAttribute("x", x);
        outer.setAttribute("y", y);
        outer.setAttribute("width", w);
        outer.setAttribute("height", h);
        outer.setAttribute("fill", style.color);
        outer.setAttribute("stroke", stroke);
        outer.setAttribute("stroke-width", "2");
        group.appendChild(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", x + 6);
        inner.setAttribute("y", y + 6);
        inner.setAttribute("width", w - 12);
        inner.setAttribute("height", h - 12);
        inner.setAttribute("fill", "none");
        inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "2");
        group.appendChild(inner);
      }} else {{
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x);
        shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w);
        shapeEl.setAttribute("height", h);
      }}

      if (shapeEl) {{
        shapeEl.setAttribute("fill", style.color);
        shapeEl.setAttribute("stroke", stroke);
        shapeEl.setAttribute("stroke-width", "2");
        group.appendChild(shapeEl);
      }}

      const foreignObject = createSvgElement("foreignObject");
      foreignObject.setAttribute("x", x);
      foreignObject.setAttribute("y", y);
      foreignObject.setAttribute("width", w);
      foreignObject.setAttribute("height", h);
      const body = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
      body.setAttribute("class", "node-fo-body");
      body.innerHTML = `<div class="node-label">${{escapeHtml(entity.label)}}</div><div class="node-subtitle">${{escapeHtml(entity.type)}}</div>`;
      foreignObject.appendChild(body);
      group.appendChild(foreignObject);
      return group;
    }}

    function emphasizeEdge(pathEl, strokeColor = null) {{
      if (!pathEl) return;
      if (pathEl.parentNode === edgeLayer) {{
        edgeLayer.appendChild(pathEl);
      }}
      if (strokeColor) {{
        pathEl.style.stroke = strokeColor;
      }}
      pathEl.style.strokeWidth = "3";
      pathEl.style.opacity = "0.98";
      pathEl.style.filter = "drop-shadow(0 0 1px rgba(17, 24, 39, 0.18))";
    }}

    function clearEdgeEmphasis(pathEl) {{
      if (!pathEl) return;
      pathEl.style.stroke = "";
      pathEl.style.strokeWidth = "";
      pathEl.style.opacity = "";
      pathEl.style.filter = "";
    }}

    function setIncomingEdgeHighlight(nodeId, active) {{
      document.querySelectorAll(`[data-target-node-id="${{nodeId}}"]`).forEach(pathEl => {{
        if (active) {{
          emphasizeEdge(pathEl);
        }} else {{
          clearEdgeEmphasis(pathEl);
        }}
      }});
      if (!active) {{
        applyAncestorFocus();
      }}
    }}

    function bindEdgeHover(pathEl, edge) {{
      const baseColor = edgeColorForTarget(edge.target);
      pathEl.addEventListener("mouseenter", event => {{
        tooltip.innerHTML = edgeTooltipText(edge);
        tooltip.style.display = "block";
        emphasizeEdge(pathEl, "#1f2933");
        typesetElement(tooltip);
      }});
      pathEl.addEventListener("mousemove", event => {{
        tooltip.style.left = `${{event.clientX + 16}}px`;
        tooltip.style.top = `${{event.clientY + 16}}px`;
      }});
      pathEl.addEventListener("mouseleave", () => {{
        tooltip.style.display = "none";
        clearEdgeEmphasis(pathEl);
        pathEl.style.stroke = baseColor;
        applyAncestorFocus();
      }});
    }}

    function bindNodeInteractions(nodeEl, entity) {{
      nodeEl.addEventListener("mouseenter", event => {{
        tooltip.innerHTML = tooltipText(entity);
        tooltip.style.display = "block";
        typesetElement(tooltip);
        nodeEl.classList.add("hovered");
        setIncomingEdgeHighlight(entity.id, true);
      }});
      nodeEl.addEventListener("mousemove", event => {{
        tooltip.style.left = `${{event.clientX + 16}}px`;
        tooltip.style.top = `${{event.clientY + 16}}px`;
      }});
      nodeEl.addEventListener("mouseleave", () => {{
        tooltip.style.display = "none";
        nodeEl.classList.remove("hovered");
        setIncomingEdgeHighlight(entity.id, false);
      }});
      nodeEl.addEventListener("click", () => {{
        selectedNodeId = entity.id;
        details.innerHTML = formatEntity(entity);
        typesetElement(details);
        saveViewerState();
        applyAncestorFocus();
      }});
      nodeEl.addEventListener("dblclick", event => {{
        event.preventDefault();
        if (selectedNodeId === entity.id) {{
          ancestorFocusEnabled = false;
        }}
        hiddenNodes.add(entity.id);
        saveViewerState();
        updateVisibility();
      }});
    }}

    function applyAncestorFocus() {{
      syncFocusToggle();
      const active = ancestorFocusEnabled && selectedNodeId && !isHiddenNode(selectedNodeId);
      const keep = active ? collectAncestors(selectedNodeId) : null;
      document.querySelectorAll(".graph-node").forEach(nodeEl => {{
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.style.opacity = active && !keep.has(nodeId) ? "0.18" : "1";
      }});
      document.querySelectorAll(".edge-path").forEach(pathEl => {{
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        pathEl.style.opacity = active && (!keep.has(src) || !keep.has(dst)) ? "0.08" : "0.96";
      }});
    }}

    async function updateVisibility() {{
      renderRemovedNodes();
      edgeLayer.innerHTML = "";
      nodeLayer.innerHTML = "";
      lastRenderedEdges = [];
      lastNodePositions = new Map();
      const visibleEntities = docData.entities.filter(entity => !isHiddenNode(entity.id));
      const visibleEdges = computeVisibleEdges().filter(edge => !isHiddenNode(edge.source) && !isHiddenNode(edge.target));
      const currentVersion = ++renderVersion;

      if (visibleEntities.length === 0) {{
        elkStatus.textContent = "No visible nodes.";
        svg.setAttribute("width", "800");
        svg.setAttribute("height", "200");
        svg.setAttribute("viewBox", "0 0 800 200");
        return;
      }}

      try {{
        elkStatus.textContent = "Rendering graph layout...";
        const graph = await computeLayout(visibleEntities, visibleEdges);
        if (currentVersion !== renderVersion) return;
        elkStatus.textContent = "";

        const nodeLookup = new Map((graph.children || []).map(node => [node.id, node]));
        visibleEntities.forEach(entity => {{
          const positioned = nodeLookup.get(entity.id);
          if (positioned) {{
            lastNodePositions.set(entity.id, {{
              x: positioned.x || 0,
              y: positioned.y || 0,
              width: positioned.width || 210,
              height: positioned.height || 68
            }});
          }}
        }});

        const graphWidth = Math.max(900, (graph.width || 0) + 80);
        const graphHeight = Math.max(500, (graph.height || 0) + 80);
        svg.setAttribute("width", String(graphWidth));
        svg.setAttribute("height", String(graphHeight));
        svg.setAttribute("viewBox", `0 0 ${{graphWidth}} ${{graphHeight}}`);
        const targetCounts = new Map();
        visibleEdges.forEach(edge => {{
          targetCounts.set(edge.target, (targetCounts.get(edge.target) || 0) + 1);
        }});

        (graph.edges || []).forEach((elkEdge, idx) => {{
          const meta = visibleEdges[idx];
          const section = (elkEdge.sections || [])[0];
          if (!section || !meta) return;
          const points = mergedTargetPoints(meta, pointsForSection(section), targetCounts);
          const path = createSvgElement("path");
          path.setAttribute("class", "edge-path");
          path.setAttribute("d", roundedPathForPoints(points));
          path.setAttribute("stroke", edgeColorForTarget(meta.target));
          path.setAttribute("marker-end", "url(#arrow)");
          if (meta.confidence === "inferred") {{
            path.setAttribute("stroke-dasharray", "8 5");
          }} else if (meta.confidence === "unclear") {{
            path.setAttribute("stroke-dasharray", "3 5");
          }} else if (meta.bridge) {{
            path.setAttribute("stroke-dasharray", "6 4");
          }}
          path.dataset.targetNodeId = meta.target;
          path.dataset.sourceNodeId = meta.source;
          edgeLayer.appendChild(path);
          bindEdgeHover(path, meta);
          lastRenderedEdges.push(meta);
        }});

        visibleEntities.forEach(entity => {{
          const positioned = lastNodePositions.get(entity.id);
          if (!positioned) return;
          const nodeEl = renderNode(entity, positioned);
          bindNodeInteractions(nodeEl, entity);
          nodeLayer.appendChild(nodeEl);
        }});

        applyAncestorFocus();
        typesetElement(nodeLayer);
      }} catch (error) {{
        elkStatus.textContent = `ELK layout failed: ${{error.message || error}}`;
      }}
    }}

    focusToggle.addEventListener("click", () => {{
      if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
      ancestorFocusEnabled = !ancestorFocusEnabled;
      saveViewerState();
      applyAncestorFocus();
    }});

    document.addEventListener("keydown", event => {{
      if (event.key.toLowerCase() !== "h") return;
      if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      event.preventDefault();
      ancestorFocusEnabled = !ancestorFocusEnabled;
      saveViewerState();
      applyAncestorFocus();
    }});

    window.addEventListener("load", () => {{
      if (window.MathJax && window.MathJax.typesetPromise) {{
        window.MathJax.typesetPromise().catch(() => {{}});
      }}
      updateVisibility();
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    """CLI entry point for rendering canonical dependency JSON to HTML."""
    parser = argparse.ArgumentParser(description="Render an interactive HTML math dependency graph from canonical JSON.")
    parser.add_argument("source", help="Path to the canonical dependency-graph JSON file")
    parser.add_argument("--html-out", dest="html_out", help="Path to write the standalone HTML viewer")
    parser.add_argument(
        "--reduce-transitive-edges",
        action="store_true",
        help="Apply graph-theoretic transitive reduction before rendering",
    )
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source JSON not found: {source_path}")

    doc = json.loads(source_path.read_text(encoding="utf-8"))
    validate_document(doc)
    reduction_note = ""
    removed_edges: list[dict] = []
    if args.reduce_transitive_edges:
        doc, removed_edges = reduce_transitive_edges(doc)
        reduction_note = f"Graph-theoretic transitive reduction enabled: removed {len(removed_edges)} redundant edges from the rendered view."

    html_path = Path(args.html_out).resolve() if args.html_out else source_path.with_suffix(".html")
    html_path.write_text(build_html_with_elk(doc, reduction_note=reduction_note), encoding="utf-8")

    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(doc["entities"]),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
