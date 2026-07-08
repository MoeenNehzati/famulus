#!/usr/bin/env python3
"""Render an interactive math-dependency graph from canonical JSON.

This script does not infer mathematical structure from LaTeX. The JSON input is
the semantic source of truth and is expected to be authored by the model under
the accompanying ``SKILL.md`` contract.

UI design philosophy
--------------------
The viewer is split into two zones:

**Canvas toolbar** (top-left, always visible even when the panel is collapsed)
    Holds primary actions that a user needs at any moment regardless of context:
    - Ancestor focus cycle (off → dim → hide non-ancestors)
    - Delete selected node (remove it from the visible graph)
    - Redraw all (reset manual positions and rerun the automatic ELK layout)

    Rule: if a user might want to invoke it while staring at the graph, it
    belongs in the toolbar, not the panel.

**Side panel** (collapsible)
    Holds contextual information that is useful but not always needed:
    legend, entity details on click, raw JSON, removed-node list, cheatsheet.
    The panel can be fully collapsed without losing access to any action.

Current active path:
- ``build_html_with_elk(...)`` generates a standalone HTML viewer that uses
  ``elkjs`` in the browser for layered layout and edge routing.
"""
from __future__ import annotations

import argparse
import html
import json
import copy
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict

try:
    from extract_mathjax_macros import default_output_path, extract_macros, write_macros
except ImportError:  # pragma: no cover - only relevant when imported unusually
    default_output_path = None
    extract_macros = None
    write_macros = None


TYPE_STYLES = {
    "standing-assumption": {"shape": "hexagon", "color": "#c0392b"},
    "local-assumption": {"shape": "diamond", "color": "#d35400"},
    "definition": {"shape": "roundrect", "color": "#2471a3"},
    "notation": {"shape": "parallelogram", "color": "#148f77"},
    "lemma": {"shape": "ellipse", "color": "#1e8449"},
    "proposition": {"shape": "rect", "color": "#7d6608"},
    "theorem": {"shape": "rect", "color": "#6c3483"},
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
        for key in ("id", "type", "short_title", "ref", "position", "depends_on"):
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


def merge_mathjax_macros(doc: dict, macro_file: Path | None) -> int:
    """Merge extracted MathJax macros into ``doc``.

    Macros already present in the graph JSON take precedence because they may
    be hand-normalized for MathJax compatibility.
    """
    if macro_file is None:
        return 0
    if not macro_file.exists():
        raise SystemExit(f"Macro file not found: {macro_file}")
    file_macros = json.loads(macro_file.read_text(encoding="utf-8"))
    if not isinstance(file_macros, dict):
        raise SystemExit(f"Macro file must contain a JSON object: {macro_file}")
    document_meta = doc.setdefault("document", {})
    json_macros = document_meta.get("mathjax_macros", {})
    if json_macros and not isinstance(json_macros, dict):
        raise SystemExit("'document.mathjax_macros' must be an object when present.")
    document_meta["mathjax_macros"] = {**file_macros, **json_macros}
    return len(file_macros)


def resolve_entrypoint(entrypoint_text: str, source_path: Path) -> Path:
    """Resolve an entrypoint from CLI/JSON relative to useful roots."""
    entrypoint = Path(entrypoint_text)
    candidates = []
    if entrypoint.is_absolute():
        candidates.append(entrypoint)
    else:
        candidates.extend(
            [
                Path.cwd() / entrypoint,
                source_path.parent / entrypoint,
                source_path.parent.parent / entrypoint,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def prepare_macro_file(args: argparse.Namespace, source_path: Path, doc: dict) -> Path | None:
    """Find or create the macro file to merge for this render."""
    if args.macro_file:
        return Path(args.macro_file).resolve()

    entrypoint_text = args.tex_entry or doc.get("document", {}).get("source_entrypoint")
    if not entrypoint_text:
        return None

    entrypoint = resolve_entrypoint(entrypoint_text, source_path)
    if not entrypoint.exists():
        if args.tex_entry:
            raise SystemExit(f"TeX entrypoint not found: {entrypoint}")
        return None

    if default_output_path is None or extract_macros is None or write_macros is None:
        raise SystemExit("Macro extraction helper is unavailable.")

    macro_path = default_output_path(entrypoint)
    if args.refresh_macros or args.tex_entry or not macro_path.exists():
        macros = extract_macros(entrypoint)
        write_macros(macros, macro_path)
    return macro_path

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
                        "source_label": entity_map[source_id]["short_title"],
                        "target_label": entity["short_title"],
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
    graph_build_id = str(int(time.time() * 1000))
    doc_title = doc.get("document", {}).get("title", "")
    page_title = f"{doc_title} — Math dependency graph" if doc_title else "Math dependency graph"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(page_title)}</title>
  <script>
    const GRAPH_BUILD_ID = "{graph_build_id}";
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
    .canvas-area {{
      position: relative;
      min-height: 100vh;
    }}
    .canvas-toolbar {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 10px;
      border: 1px solid #d5d8dc;
      border-radius: 8px;
      background: rgba(249, 247, 241, 0.93);
      backdrop-filter: blur(6px);
      flex-wrap: wrap;
      position: absolute;
      top: 10px;
      left: 10px;
      z-index: 5;
    }}
    .routing-controls {{
      position: absolute;
      left: 10px;
      bottom: 10px;
      z-index: 5;
      width: min(340px, calc(100vw - 40px));
      max-height: calc(100vh - 90px);
      overflow: auto;
      border: 1px solid #d5d8dc;
      border-radius: 8px;
      background: rgba(249, 247, 241, 0.94);
      backdrop-filter: blur(6px);
      box-shadow: 0 10px 24px rgba(17,24,39,0.12);
      font-size: 0.86rem;
    }}
    .routing-controls details {{
      border-top: 1px solid #e1e5e8;
    }}
    .routing-controls details:first-child {{
      border-top: none;
    }}
    .routing-controls summary {{
      cursor: pointer;
      padding: 7px 10px;
      font-weight: 700;
      user-select: none;
    }}
    .routing-controls-body {{
      padding: 0 10px 9px;
      display: grid;
      gap: 8px;
    }}
    .routing-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }}
    .routing-row label {{
      min-width: 0;
    }}
    .routing-row input[type="range"] {{
      width: 145px;
    }}
    .routing-row select {{
      max-width: 165px;
      border: 1px solid #aeb6bf;
      border-radius: 6px;
      background: #f8f9f9;
      padding: 3px 6px;
      font-family: inherit;
    }}
    .routing-value {{
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
      color: #566573;
      margin-left: 6px;
    }}
    .toolbar-btn {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 10px;
      border: 1px solid #aeb6bf;
      border-radius: 999px;
      background: #f8f9f9;
      color: #17202a;
      cursor: pointer;
      font-size: 0.88rem;
      font-family: inherit;
      transition: background 0.1s, border-color 0.1s;
      white-space: nowrap;
    }}
    .toolbar-tip {{
      position: relative;
      display: inline-flex;
    }}
    .toolbar-tip::after {{
      content: attr(data-tooltip);
      position: absolute;
      left: 50%;
      top: calc(100% + 8px);
      transform: translateX(-50%);
      width: max-content;
      max-width: 260px;
      padding: 6px 8px;
      border-radius: 6px;
      background: rgba(17, 24, 39, 0.94);
      color: #f9fafb;
      font-size: 0.78rem;
      line-height: 1.25;
      box-shadow: 0 8px 20px rgba(0,0,0,0.18);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.08s ease;
      z-index: 20;
    }}
    .toolbar-tip:hover::after {{
      opacity: 1;
    }}
    .toolbar-btn:hover:not(:disabled) {{
      background: #eef3f7;
      border-color: #7f8c8d;
    }}
    .toolbar-btn.active {{
      background: #e8f1fb;
      border-color: #6a8fb7;
    }}
    .toolbar-btn:disabled {{
      opacity: 0.4;
      cursor: default;
    }}
    .toolbar-sep {{
      width: 1px;
      height: 22px;
      background: #d5d8dc;
      margin: 0 2px;
    }}
    .canvas-wrap {{
      overflow: hidden;
      position: absolute;
      inset: 0;
      padding: 0;
      cursor: grab;
      user-select: none;
    }}
    .canvas-wrap.panning {{
      cursor: grabbing;
    }}
    .panel {{
      border-left: 1px solid #d5d8dc;
      background: rgba(255,255,255,0.9);
      backdrop-filter: blur(8px);
      padding: 20px;
      box-sizing: border-box;
      position: relative;
      overflow-y: auto;
      max-height: 100vh;
    }}
    .layout.panel-collapsed .panel {{
      padding: 20px 10px 20px 8px;
      overflow: hidden;
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
    h2.section-heading {{
      font-size: 1.05rem;
      margin: 0 0 0.4rem;
      color: #2c3e50;
    }}
    .sidebar-section {{
      position: relative;
      padding-left: 18px;
      margin-bottom: 0.9rem;
      border-radius: 6px;
    }}
    .sidebar-section.drag-over {{
      background: rgba(106, 143, 183, 0.13);
      outline: 2px dashed #6a8fb7;
    }}
    .sidebar-section.dragging {{
      opacity: 0.45;
    }}
    .drag-handle {{
      position: absolute;
      left: 1px;
      top: 3px;
      color: #bdc3c7;
      cursor: grab;
      font-size: 13px;
      user-select: none;
      line-height: 1.2;
      padding: 2px 0;
    }}
    .drag-handle:hover {{
      color: #7f8c8d;
    }}
    .drag-handle:active {{
      cursor: grabbing;
    }}
    details > summary {{
      cursor: pointer;
      font-size: 0.92rem;
      font-weight: 600;
      color: #34495e;
      user-select: none;
      padding: 2px 0 4px;
      list-style: none;
    }}
    details > summary::before {{
      content: "▶ ";
      font-size: 0.7rem;
      color: #7f8c8d;
    }}
    details[open] > summary::before {{
      content: "▼ ";
    }}
    .legend {{
      display: grid;
      gap: 8px;
      margin-bottom: 0.4rem;
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
      transition: filter 0.12s ease, opacity 0.12s ease;
    }}
    .graph-node.hovered {{
      filter: brightness(1.18) saturate(1.08) drop-shadow(0 6px 18px rgba(0,0,0,0.28));
    }}
    .graph-node.selected {{
      filter: brightness(1.08) saturate(1.04) drop-shadow(0 5px 14px rgba(0,0,0,0.24));
    }}
    .graph-node.selected.hovered {{
      filter: brightness(1.18) saturate(1.08) drop-shadow(0 6px 18px rgba(0,0,0,0.28));
    }}
    .graph-node.dragging-node {{
      cursor: grabbing;
      filter: drop-shadow(0 8px 18px rgba(0,0,0,0.3));
    }}
    .selection-ring {{
      fill: none;
      stroke: #111111;
      stroke-width: 3;
      stroke-linejoin: round;
      stroke-linecap: round;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.12s ease;
    }}
    .graph-node.selected .selection-ring {{
      opacity: 1;
    }}
    .graph-node.selected .node-shape {{
      filter: brightness(1.18) saturate(1.06);
    }}
    .node-label {{
      display: block;
      text-align: center;
      font-size: 12px;
      font-weight: 700;
      color: #fff;
      text-shadow:
        0 1px 1px rgba(17, 24, 39, 0.74),
        0 0 2px rgba(17, 24, 39, 0.72);
      line-height: 1.1;
      padding: 0 8px;
      box-sizing: border-box;
      white-space: normal;
    }}
    .node-subtitle {{
      text-align: center;
      font-size: 11px;
      color: rgba(255,255,255,0.92);
      text-shadow:
        0 1px 1px rgba(17, 24, 39, 0.62),
        0 0 2px rgba(17, 24, 39, 0.62);
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
      margin-top: 0.4rem;
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
    .details-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
    }}
    .details-header h2 {{
      margin: 0 0 0.35rem;
      font-size: 1.1rem;
    }}
    .deselect-btn {{
      background: none;
      border: 1px solid #bdc3c7;
      border-radius: 999px;
      width: 22px;
      height: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 12px;
      color: #7f8c8d;
      flex-shrink: 0;
      margin-top: 2px;
    }}
    .deselect-btn:hover {{
      background: #f2f3f4;
      color: #17202a;
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
      font-size: 0.8rem;
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
    .edge-arrow {{
      opacity: 0.92;
      pointer-events: none;
      transition: opacity 0.12s ease, filter 0.12s ease, fill 0.12s ease;
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
    <div class="canvas-area">
      <div class="canvas-toolbar" id="canvas-toolbar">
        <span class="toolbar-tip" data-tooltip="Cycle ancestor focus for the selected node: highlight, hide, then show the full graph.">
          <button id="focus-toggle" class="toolbar-btn" type="button" aria-label="Cycle ancestor focus">Highlight ancestors</button>
        </span>
        <span class="toolbar-tip" data-tooltip="Hide the selected node from the visible graph. Double-click a node to hide it directly.">
          <button id="delete-node-btn" class="toolbar-btn" type="button" disabled aria-label="Hide selected node">Delete node</button>
        </span>
        <div class="toolbar-sep"></div>
        <span class="toolbar-tip" data-tooltip="Reset manual node positions and rerun the automatic layout. Shortcut: r.">
          <button id="redraw-btn" class="toolbar-btn" type="button" aria-label="Redraw graph layout">Redraw all</button>
        </span>
        <div class="toolbar-sep"></div>
        <span class="toolbar-tip" data-tooltip="Click: restore individually hidden and focus-hidden nodes while keeping hidden legend categories. Double-click: reset everything, including legend categories. Shortcut: c.">
          <button id="reset-btn" class="toolbar-btn" type="button" aria-label="Reset graph state">Reset</button>
        </span>
        <div class="toolbar-sep"></div>
        <span class="toolbar-tip" data-tooltip="Zoom in. Shortcut: + or =">
          <button id="zoom-in-btn" class="toolbar-btn" type="button" aria-label="Zoom in">+</button>
        </span>
        <span class="toolbar-tip" data-tooltip="Zoom out. Shortcut: −">
          <button id="zoom-out-btn" class="toolbar-btn" type="button" aria-label="Zoom out">−</button>
        </span>
        <span class="toolbar-tip" data-tooltip="Fit graph in viewport. Shortcut: f">
          <button id="fit-btn" class="toolbar-btn" type="button" aria-label="Fit graph in viewport">Fit</button>
        </span>
      </div>
      <div class="routing-controls" id="routing-controls">
        <details open>
          <summary>Presets</summary>
          <div class="routing-controls-body">
            <div class="routing-row">
              <label for="routing-compactness">Graph spread</label>
              <select id="routing-compactness" aria-label="Spacing preset">
                <option value="compact">Compact</option>
                <option value="balanced" selected>Balanced</option>
                <option value="spacious">Spacious</option>
              </select>
            </div>
            <div class="routing-row">
              <label for="routing-shape">Shape</label>
              <select id="routing-shape" aria-label="Edge shape preset">
                <option value="sharp">Sharp</option>
                <option value="soft" selected>Soft</option>
                <option value="curvy">Curvy</option>
              </select>
            </div>
          </div>
        </details>
        <details>
          <summary>Advanced</summary>
          <div class="routing-controls-body">
            <div class="routing-row">
              <label for="routing-clearance">Node gap <span id="routing-clearance-value" class="routing-value"></span></label>
              <input id="routing-clearance" type="range" min="0" max="30" step="1" aria-label="Extra edge clearance">
            </div>
            <div class="routing-row">
              <label for="routing-radius">Curve <span id="routing-radius-value" class="routing-value"></span></label>
              <input id="routing-radius" type="range" min="0" max="80" step="1" aria-label="Edge corner radius">
            </div>
            <div class="routing-row">
              <label for="routing-parallel">Parallel gap <span id="routing-parallel-value" class="routing-value"></span></label>
              <input id="routing-parallel" type="range" min="0" max="60" step="1" aria-label="Parallel edge spacing">
            </div>
            <div class="routing-row">
              <label for="routing-merge">Merge lane <span id="routing-merge-value" class="routing-value"></span></label>
              <input id="routing-merge" type="range" min="0" max="140" step="1" aria-label="Merged target lane distance">
            </div>
            <div class="routing-row">
              <label for="routing-node-spacing">Vertical spacing <span id="routing-node-spacing-value" class="routing-value"></span></label>
              <input id="routing-node-spacing" type="range" min="8" max="220" step="1" aria-label="Vertical cell spacing">
            </div>
            <div class="routing-row">
              <label for="routing-layer-spacing">Layer spacing <span id="routing-layer-spacing-value" class="routing-value"></span></label>
              <input id="routing-layer-spacing" type="range" min="35" max="260" step="1" aria-label="Layer spacing">
            </div>
            <div class="routing-row">
              <label for="routing-edge-node-spacing">Edge-node spacing <span id="routing-edge-node-spacing-value" class="routing-value"></span></label>
              <input id="routing-edge-node-spacing" type="range" min="0" max="160" step="1" aria-label="ELK edge-node spacing">
            </div>
          </div>
        </details>
      </div>
      <div class="canvas-wrap" id="canvas-wrap">
        <div id="elk-status" class="elk-status">Rendering graph layout...</div>
        <svg id="graph-svg" width="1200" height="800" viewBox="0 0 1200 800">
          <defs>
          </defs>
          <g id="edge-layer"></g>
          <g id="node-layer"></g>
        </svg>
      </div>
    </div>
    <aside class="panel">
      <button class="panel-toggle" id="panel-toggle" type="button" aria-expanded="true" aria-controls="panel-content" title="Collapse side panel">⟩</button>
      <div class="collapsed-label">Panel</div>
      <div class="panel-content" id="panel-content">
        <h1 id="panel-title">Math dependency graph</h1>

        <div class="sidebar-section" draggable="true" data-section-id="cheatsheet">
          <div class="drag-handle" title="Drag to reorder">⠿</div>
          <details id="cheatsheet-details">
            <summary>How to use</summary>
            <div class="small" style="margin-top:0.4rem;"><strong>Double-click</strong> a node to hide it; double-click again in the <em>Removed nodes</em> list to restore it.</div>
            <div class="small" style="margin-top:0.25rem;"><strong>Toolbar</strong> (top-left, always visible): <em>Highlight ancestors</em> cycles focus (off → dim → hide); <em>Delete node</em> removes the selected node; <em>Redraw all</em> resets layout.</div>
            <div class="small" style="margin-top:0.25rem;">Hover a node or edge to preview metadata. Click to pin details here.</div>
            <div class="small" style="margin-top:0.25rem;">Click a type in the legend to hide or show that category. Causality is preserved via bridge edges.</div>
            <div class="small" style="margin-top:0.25rem;"><kbd>h</kbd> cycles ancestor focus (off → dim → hide). <kbd>Esc</kbd> or ✕ deselects. <kbd>r</kbd> redraws. <kbd>c</kbd> clears everything. <kbd>f</kbd> fits the graph.</div>
            <div class="small" style="margin-top:0.25rem;">Drag a node to reposition it. Drag the ⠿ handle on each section to reorder this panel.</div>
            <div class="small" style="margin-top:0.25rem;"><strong>Scroll</strong> to zoom in/out (centered at cursor). <strong>Drag empty space</strong> to pan. Use the toolbar +/−/Fit buttons or keyboard <kbd>+</kbd> <kbd>−</kbd> <kbd>f</kbd>.</div>
            <div class="small" style="margin-top:0.25rem;"><strong>Edge styles:</strong> solid = Verified; long-dashed = Likely; short-dashed = Speculative. <strong>Node borders:</strong> solid = explicit; dashed = model-introduced.</div>
            {f'<div class="small" style="margin-top:0.35rem;">{html.escape(reduction_note)}</div>' if reduction_note else ''}
          </details>
        </div>

        <div class="sidebar-section" draggable="true" data-section-id="legend">
          <div class="drag-handle" title="Drag to reorder">⠿</div>
          <h2 class="section-heading">Legend</h2>
          <div class="legend" id="legend"></div>
        </div>

        <div class="sidebar-section" draggable="true" data-section-id="details">
          <div class="drag-handle" title="Drag to reorder">⠿</div>
          <div id="details" class="small">Select a node or edge to inspect its metadata.</div>
        </div>

        <div class="sidebar-section" draggable="true" data-section-id="removed-nodes">
          <div class="drag-handle" title="Drag to reorder">⠿</div>
          <h2 class="section-heading">Removed nodes</h2>
          <div id="removed-nodes" class="removed-list small">None</div>
        </div>

        <div class="sidebar-section" draggable="true" data-section-id="raw-json">
          <div class="drag-handle" title="Drag to reorder">⠿</div>
          <h2 class="section-heading" id="raw-json-title">Raw JSON</h2>
          <pre><code id="raw-json-code"></code></pre>
        </div>

      </div>
    </aside>
  </div>
  <script>
    const docData = {json.dumps(doc, indent=2)};
    const typeStyles = {json.dumps(TYPE_STYLES, indent=2)};
    const edgePalette = {json.dumps(EDGE_PALETTE, indent=2)};
    const edgeData = {json.dumps(edge_payload, indent=2)};
    const renderTypeOverrides = {json.dumps(render_type_overrides, indent=2)};

    // DOM refs
    const layoutEl = document.getElementById("layout");
    const panelToggle = document.getElementById("panel-toggle");
    const svgEl = document.getElementById("graph-svg");
    const canvasWrapEl = document.getElementById("canvas-wrap");
    const edgeLayer = document.getElementById("edge-layer");
    const nodeLayer = document.getElementById("node-layer");
    const tooltip = document.getElementById("tooltip");
    const details = document.getElementById("details");
    const legend = document.getElementById("legend");
    const removedNodesEl = document.getElementById("removed-nodes");
    const focusToggle = document.getElementById("focus-toggle");
    const deleteNodeBtn = document.getElementById("delete-node-btn");
    const elkStatus = document.getElementById("elk-status");
    const rawJsonCodeEl = document.getElementById("raw-json-code");
    const panelContent = document.getElementById("panel-content");
    const routingCompactnessSelect = document.getElementById("routing-compactness");
    const routingShapeSelect = document.getElementById("routing-shape");
    const routingInputs = {{
      extraClearance: document.getElementById("routing-clearance"),
      cornerRadius: document.getElementById("routing-radius"),
      parallelSpacing: document.getElementById("routing-parallel"),
      mergeLaneDistance: document.getElementById("routing-merge"),
      nodeSpacing: document.getElementById("routing-node-spacing"),
      layerSpacing: document.getElementById("routing-layer-spacing"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing")
    }};
    const routingValueEls = {{
      extraClearance: document.getElementById("routing-clearance-value"),
      cornerRadius: document.getElementById("routing-radius-value"),
      parallelSpacing: document.getElementById("routing-parallel-value"),
      mergeLaneDistance: document.getElementById("routing-merge-value"),
      nodeSpacing: document.getElementById("routing-node-spacing-value"),
      layerSpacing: document.getElementById("routing-layer-spacing-value"),
      edgeNodeSpacing: document.getElementById("routing-edge-node-spacing-value")
    }};

    // Core state
    const entityMap = new Map(docData.entities.map(e => [e.id, e]));
    const outgoing = new Map();
    const incoming = new Map();
    const hiddenTypes = new Set();
    const hiddenNodes = new Set();
    const nodeTypes = new Map(docData.entities.map(e => [e.id, e.type]));
    let selectedNodeId = null;
    let focusNodeId = null;
    let ancestorFocusMode = 0; // 0=off, 1=dim non-ancestors, 2=hide non-ancestors
    const ancestorHiddenByFocus = new Set(); // nodes temporarily hidden in mode 2
    const nodeColorIndex = new Map(
      docData.entities
        .slice()
        .sort((a, b) => {{
          const aPos = a.position || 0;
          const bPos = b.position || 0;
          if (aPos !== bPos) return aPos - bPos;
          return a.short_title.localeCompare(b.short_title);
        }})
        .map((e, idx) => [e.id, idx])
    );

    // Manual node positions (overrides ELK layout after drag)
    const manualPositions = new Map();
    // ELK-computed positions from last full render
    let lastNodePositions = new Map();
    // Track whether full layout has run at least once
    let hasFullLayout = false;

    // Drag state for nodes
    let draggingNodeId = null;
    let nodeDragMoved = false;
    let nodeClickTimer = null;
    let dragStartClientX = 0;
    let dragStartClientY = 0;
    let dragStartOffsetX = 0;
    let dragStartOffsetY = 0;
    const DRAG_THRESHOLD = 5;

    // Pan/zoom state
    let panX = 0, panY = 0, zoomLevel = 1;
    let isPanning = false;
    let panStartClientX = 0, panStartClientY = 0, panStartX = 0, panStartY = 0;
    let hasFittedOnce = false;
    const MIN_ZOOM = 0.08, MAX_ZOOM = 5;

    let renderVersion = 0;
    let lastRenderedEdges = [];
    let elk = null;
    const viewerStateKey = `math-dependency-graph::${{docData.document?.source_file || docData.document?.title || "document"}}`;

    function startBuildRefreshWatcher() {{
      if (!/^https?:$/.test(window.location.protocol)) return;
      const matchBuildId = text => {{
        const match = text.match(/const GRAPH_BUILD_ID = "([^"]+)"/);
        return match ? match[1] : null;
      }};
      const checkForNewBuild = async () => {{
        try {{
          const url = new URL(window.location.href);
          url.searchParams.set("graph_probe", String(Date.now()));
          const response = await fetch(url.toString(), {{ cache: "no-store" }});
          if (!response.ok) return;
          const nextBuildId = matchBuildId(await response.text());
          if (!nextBuildId || nextBuildId === GRAPH_BUILD_ID) return;
          const reloadUrl = new URL(window.location.href);
          reloadUrl.searchParams.delete("graph_probe");
          reloadUrl.searchParams.set("graph_v", nextBuildId);
          window.location.replace(reloadUrl.toString());
        }} catch (error) {{}}
      }};
      window.setInterval(checkForNewBuild, 1500);
    }}

    const routingPresets = {{
      compact: {{ extraClearance: 0, parallelSpacing: 4, mergeLaneDistance: 18, nodeSpacing: 12, layerSpacing: 45, edgeNodeSpacing: 8 }},
      balanced: {{ extraClearance: 3, parallelSpacing: 12, mergeLaneDistance: 34, nodeSpacing: 46, layerSpacing: 150, edgeNodeSpacing: 40 }},
      spacious: {{ extraClearance: 16, parallelSpacing: 36, mergeLaneDistance: 80, nodeSpacing: 120, layerSpacing: 210, edgeNodeSpacing: 110 }}
    }};
    const shapePresets = {{
      sharp: {{ cornerRadius: 0 }},
      soft: {{ cornerRadius: 18 }},
      curvy: {{ cornerRadius: 60 }}
    }};
    const routingConfig = {{
      compactnessPreset: "balanced",
      shapePreset: "soft",
      ...routingPresets.balanced,
      ...shapePresets.soft
    }};

    edgeData.forEach(edge => {{
      if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
      outgoing.get(edge.source).push(edge);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      incoming.get(edge.target).push(edge);
    }});

    function syncRoutingControls() {{
      routingCompactnessSelect.value = routingConfig.compactnessPreset;
      routingShapeSelect.value = routingConfig.shapePreset;
      Object.entries(routingInputs).forEach(([key, input]) => {{
        input.value = routingConfig[key];
        routingValueEls[key].textContent = routingConfig[key];
      }});
    }}

    function applyRoutingPatch(patch) {{
      Object.assign(routingConfig, patch);
      syncRoutingControls();
    }}

    // ── State persistence ────────────────────────────────────────────────────

    function saveViewerState() {{
      try {{
        const payload = {{
          hiddenTypes: Array.from(hiddenTypes),
          hiddenNodes: Array.from(hiddenNodes),
          selectedNodeId,
          focusNodeId,
          ancestorFocusMode,
          panelCollapsed: layoutEl.classList.contains("panel-collapsed"),
          manualPositions: Array.from(manualPositions.entries()),
          routingConfig,
          panX, panY, zoomLevel
        }};
        window.localStorage.setItem(viewerStateKey, JSON.stringify(payload));
      }} catch (e) {{}}
    }}

    function restoreViewerState() {{
      try {{
        const raw = window.localStorage.getItem(viewerStateKey);
        if (!raw) return;
        const payload = JSON.parse(raw);
        (payload.hiddenTypes || []).forEach(t => {{ if (typeStyles[t]) hiddenTypes.add(t); }});
        (payload.hiddenNodes || []).forEach(id => {{ if (entityMap.has(id)) hiddenNodes.add(id); }});
        if (payload.routingConfig) applyRoutingPatch(payload.routingConfig);
        if (payload.selectedNodeId && entityMap.has(payload.selectedNodeId)) {{
          selectedNodeId = payload.selectedNodeId;
        }}
        if (payload.focusNodeId && entityMap.has(payload.focusNodeId)) {{
          focusNodeId = payload.focusNodeId;
        }}
        // Support both old boolean and new numeric format
        ancestorFocusMode = typeof payload.ancestorFocusMode === "number"
          ? payload.ancestorFocusMode
          : (payload.ancestorFocusEnabled ? 1 : 0);
        if (payload.panelCollapsed) layoutEl.classList.add("panel-collapsed");
        (payload.manualPositions || []).forEach(([id, pos]) => {{
          if (entityMap.has(id)) manualPositions.set(id, pos);
        }});
        if (typeof payload.panX === "number") {{
          panX = payload.panX; panY = payload.panY; zoomLevel = payload.zoomLevel || 1;
          applyTransform();
          hasFittedOnce = true;
        }}
      }} catch (e) {{}}
    }}

    function saveSidebarOrder() {{
      try {{
        const order = Array.from(panelContent.querySelectorAll(".sidebar-section")).map(el => el.dataset.sectionId);
        window.localStorage.setItem(viewerStateKey + "::sidebar", JSON.stringify(order));
      }} catch (e) {{}}
    }}

    function restoreSidebarOrder() {{
      try {{
        const raw = window.localStorage.getItem(viewerStateKey + "::sidebar");
        if (!raw) return;
        const order = JSON.parse(raw);
        order.forEach(sectionId => {{
          const el = panelContent.querySelector(`[data-section-id="${{sectionId}}"]`);
          if (el) panelContent.appendChild(el);
        }});
      }} catch (e) {{}}
    }}

    // ── Panel toggle ─────────────────────────────────────────────────────────

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

    // ── Pan / zoom ────────────────────────────────────────────────────────────

    function applyTransform() {{
      svgEl.style.transformOrigin = "0 0";
      svgEl.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoomLevel}})`;
    }}

    function fitGraph() {{
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      const svgW = parseFloat(svgEl.getAttribute("width")) || 1200;
      const svgH = parseFloat(svgEl.getAttribute("height")) || 800;
      const padding = 40;
      const availW = Math.max(1, canvasRect.width - padding * 2);
      const availH = Math.max(1, canvasRect.height - padding * 2);
      zoomLevel = Math.min(availW / svgW, availH / svgH, 1);
      panX = (canvasRect.width - svgW * zoomLevel) / 2;
      panY = padding;
      applyTransform();
    }}

    function zoomAt(newZoom, clientX, clientY) {{
      newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, newZoom));
      const canvasRect = canvasWrapEl.getBoundingClientRect();
      const cx = clientX - canvasRect.left;
      const cy = clientY - canvasRect.top;
      const svgX = (cx - panX) / zoomLevel;
      const svgY = (cy - panY) / zoomLevel;
      zoomLevel = newZoom;
      panX = cx - svgX * zoomLevel;
      panY = cy - svgY * zoomLevel;
      applyTransform();
    }}

    // Wheel zoom
    canvasWrapEl.addEventListener("wheel", event => {{
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(zoomLevel * factor, event.clientX, event.clientY);
      saveViewerState();
    }}, {{ passive: false }});

    // Pan: mousedown on empty canvas space
    canvasWrapEl.addEventListener("mousedown", event => {{
      if (event.button !== 0) return;
      if (draggingNodeId !== null) return;
      const target = event.target;
      if (target.closest && target.closest(".graph-node")) return;
      if (target.dataset && target.dataset.edgeId) return;
      if (target.classList && (target.classList.contains("edge-arrow") || target.classList.contains("edge-path"))) return;
      isPanning = true;
      panStartClientX = event.clientX;
      panStartClientY = event.clientY;
      panStartX = panX;
      panStartY = panY;
      canvasWrapEl.classList.add("panning");
      event.preventDefault();
    }});

    // ── Utilities ────────────────────────────────────────────────────────────

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

    // Effective node position accounting for manual drag offset
    function getEffectivePos(nodeId) {{
      return manualPositions.get(nodeId) || lastNodePositions.get(nodeId) || null;
    }}

    const SELECTION_RING_GAP = 6;
    const SELECTION_RING_STROKE_WIDTH = 3;
    const MIN_ARROW_LANDING_RUN = 18;
    function edgeNodeGap() {{
      return SELECTION_RING_GAP + SELECTION_RING_STROKE_WIDTH + routingConfig.extraClearance;
    }}

    // Simple straight-line path between two node bounding boxes during live drag.
    // Clip a straight line to node boundaries using proper rect intersection.
    // Returns an SVG path string that starts just outside srcPos and ends just
    // outside dstPos, so arrowheads sit at the target boundary and are not
    // hidden under node shapes.
    function simpleEdgePath(srcPos, dstPos) {{
      const sx = srcPos.x + srcPos.width / 2;
      const sy = srcPos.y + srcPos.height / 2;
      const tx = dstPos.x + dstPos.width / 2;
      const ty = dstPos.y + dstPos.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const dist = Math.hypot(dx, dy);
      if (dist < 1) return `M ${{sx}} ${{sy}}`;
      const ndx = dx / dist;
      const ndy = dy / dist;

      // Distance from source center to source boundary along (ndx, ndy)
      let ts = Infinity;
      if (Math.abs(ndx) > 1e-6) ts = Math.min(ts, (srcPos.width  / 2) / Math.abs(ndx));
      if (Math.abs(ndy) > 1e-6) ts = Math.min(ts, (srcPos.height / 2) / Math.abs(ndy));
      const p0x = sx + ndx * (ts + edgeNodeGap());
      const p0y = sy + ndy * (ts + edgeNodeGap());

      // Distance from target center to target boundary along (-ndx, -ndy)
      let tt = Infinity;
      if (Math.abs(ndx) > 1e-6) tt = Math.min(tt, (dstPos.width  / 2) / Math.abs(ndx));
      if (Math.abs(ndy) > 1e-6) tt = Math.min(tt, (dstPos.height / 2) / Math.abs(ndy));
      const p1x = tx - ndx * (tt + edgeNodeGap());
      const p1y = ty - ndy * (tt + edgeNodeGap());

      return `M ${{p0x}} ${{p0y}} L ${{p1x}} ${{p1y}}`;
    }}

    function manualDoglegPath(srcPos, dstPos, routeIndex = 0, routeCount = 1) {{
      const sx = srcPos.x + srcPos.width / 2;
      const sy = srcPos.y + srcPos.height / 2;
      const tx = dstPos.x + dstPos.width / 2;
      const ty = dstPos.y + dstPos.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const routeOffset = (routeIndex - (routeCount - 1) / 2) * routingConfig.parallelSpacing;
      const mergeLane = edgeNodeGap() + Math.max(MIN_ARROW_LANDING_RUN, routingConfig.mergeLaneDistance);

      if (Math.abs(dx) >= Math.abs(dy)) {{
        const sourceOnRight = dx >= 0;
        const startX = sourceOnRight ? srcPos.x + srcPos.width + edgeNodeGap() : srcPos.x - edgeNodeGap();
        const startY = sy + routeOffset;
        const endX = sourceOnRight ? dstPos.x - edgeNodeGap() : dstPos.x + dstPos.width + edgeNodeGap();
        const endY = ty + routeOffset;
        const laneX = sourceOnRight ? dstPos.x - mergeLane : dstPos.x + dstPos.width + mergeLane;
        return roundedPathForPoints([
          {{x: startX, y: startY}},
          {{x: laneX, y: startY}},
          {{x: laneX, y: endY}},
          {{x: endX, y: endY}}
        ]);
      }}

      const sourceBelow = dy >= 0;
      const startX = sx + routeOffset;
      const startY = sourceBelow ? srcPos.y + srcPos.height + edgeNodeGap() : srcPos.y - edgeNodeGap();
      const endX = tx + routeOffset;
      const endY = sourceBelow ? dstPos.y - edgeNodeGap() : dstPos.y + dstPos.height + edgeNodeGap();
      const laneY = sourceBelow ? dstPos.y - mergeLane : dstPos.y + dstPos.height + mergeLane;
      return roundedPathForPoints([
        {{x: startX, y: startY}},
        {{x: startX, y: laneY}},
        {{x: endX, y: laneY}},
        {{x: endX, y: endY}}
      ]);
    }}

    function incidentEdgePaths(nodeId) {{
      return Array.from(document.querySelectorAll(`.edge-path[data-source-node-id="${{nodeId}}"], .edge-path[data-target-node-id="${{nodeId}}"]`))
        .filter(pathEl => pathEl.style.display !== "none");
    }}

    function rerouteIncidentEdgesFromCurrentPositions(nodeId) {{
      const visiblePaths = incidentEdgePaths(nodeId);
      const routeCounts = new Map();
      visiblePaths.forEach(pathEl => {{
        const key = pathEl.dataset.targetNodeId;
        routeCounts.set(key, (routeCounts.get(key) || 0) + 1);
      }});
      const routeSeen = new Map();
      visiblePaths.forEach(pathEl => {{
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (!srcPos || !dstPos) return;
        const key = dstId;
        const routeIndex = routeSeen.get(key) || 0;
        routeSeen.set(key, routeIndex + 1);
        pathEl.setAttribute("d", manualDoglegPath(srcPos, dstPos, routeIndex, routeCounts.get(key) || 1));
        syncArrowheadForPath(pathEl);
      }});
    }}

    function rerouteAllVisibleEdgesFromCurrentPositions() {{
      const visiblePaths = Array.from(document.querySelectorAll(".edge-path"))
        .filter(pathEl => pathEl.style.display !== "none");
      const routeCounts = new Map();
      visiblePaths.forEach(pathEl => {{
        const key = pathEl.dataset.targetNodeId;
        routeCounts.set(key, (routeCounts.get(key) || 0) + 1);
      }});
      const routeSeen = new Map();
      visiblePaths.forEach(pathEl => {{
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (!srcPos || !dstPos) return;
        const key = dstId;
        const routeIndex = routeSeen.get(key) || 0;
        routeSeen.set(key, routeIndex + 1);
        pathEl.setAttribute("d", manualDoglegPath(srcPos, dstPos, routeIndex, routeCounts.get(key) || 1));
        syncArrowheadForPath(pathEl);
      }});
    }}

    // Redraw all edges incident to a node (including bridges) to follow drag.
    function updateEdgesForNode(nodeId) {{
      incidentEdgePaths(nodeId).forEach(pathEl => {{
        const srcId = pathEl.dataset.sourceNodeId;
        const dstId = pathEl.dataset.targetNodeId;
        const srcPos = getEffectivePos(srcId);
        const dstPos = getEffectivePos(dstId);
        if (srcPos && dstPos) {{
          pathEl.setAttribute("d", simpleEdgePath(srcPos, dstPos));
          syncArrowheadForPath(pathEl);
        }}
      }});
    }}

    // ── Legend ───────────────────────────────────────────────────────────────

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
        el.setAttribute("cx", "12"); el.setAttribute("cy", "10");
        el.setAttribute("rx", "10"); el.setAttribute("ry", "7");
        add(el); return svg;
      }}
      if (shape === "circle") {{
        const el = createSvgElement("circle");
        el.setAttribute("cx", "12"); el.setAttribute("cy", "10"); el.setAttribute("r", "7");
        add(el); return svg;
      }}
      if (shape === "diamond") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "12,2 22,10 12,18 2,10");
        add(el); return svg;
      }}
      if (shape === "hexagon") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 18,2 22,10 18,18 6,18 2,10");
        add(el); return svg;
      }}
      if (shape === "parallelogram") {{
        const el = createSvgElement("polygon");
        el.setAttribute("points", "6,2 22,2 18,18 2,18");
        add(el); return svg;
      }}
      if (shape === "roundrect") {{
        const el = createSvgElement("rect");
        el.setAttribute("x", "2"); el.setAttribute("y", "2");
        el.setAttribute("width", "20"); el.setAttribute("height", "16");
        el.setAttribute("rx", "5"); el.setAttribute("ry", "5");
        add(el); return svg;
      }}
      if (shape === "double-rect") {{
        const outer = createSvgElement("rect");
        outer.setAttribute("x", "2"); outer.setAttribute("y", "2");
        outer.setAttribute("width", "20"); outer.setAttribute("height", "16");
        add(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", "4.5"); inner.setAttribute("y", "4.5");
        inner.setAttribute("width", "15"); inner.setAttribute("height", "11");
        inner.setAttribute("fill", "none"); inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "1.4");
        svg.appendChild(inner); return svg;
      }}
      const el = createSvgElement("rect");
      el.setAttribute("x", "2"); el.setAttribute("y", "2");
      el.setAttribute("width", "20"); el.setAttribute("height", "16");
      add(el); return svg;
    }}

    // Only build legend rows for types actually present in the document
    const presentTypes = new Set(docData.entities.map(e => e.type));
    Object.entries(typeStyles).forEach(([type, style]) => {{
      if (!presentTypes.has(type)) return;
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
        updateVisibilityFast();
      }});
      if (hiddenTypes.has(type)) row.classList.add("inactive");
      legend.appendChild(row);
    }});

    // ── Visibility helpers ───────────────────────────────────────────────────

    function isHiddenNode(nodeId) {{
      const type = nodeTypes.get(nodeId);
      return hiddenTypes.has(type) || hiddenNodes.has(nodeId);
    }}

    // ── Removed nodes list ───────────────────────────────────────────────────

    function renderRemovedNodes() {{
      const removedEntities = docData.entities
        .filter(e => hiddenNodes.has(e.id))
        .sort((a, b) => (a.position || 0) - (b.position || 0) || a.short_title.localeCompare(b.short_title));
      removedNodesEl.innerHTML = "";

      const hasFocusHidden = ancestorHiddenByFocus.size > 0;
      const hasRemoved = removedEntities.length > 0;

      if (!hasFocusHidden && !hasRemoved) {{ removedNodesEl.textContent = "None"; return; }}

      // Ancestor-focus group (mode 2): one collapsed item for all focus-hidden nodes
      if (hasFocusHidden) {{
        const count = ancestorHiddenByFocus.size;
        const focusEntity = focusNodeId ? entityMap.get(focusNodeId) : null;
        const focusLabel = focusEntity ? focusEntity.short_title : "selection";
        const groupItem = document.createElement("div");
        groupItem.className = "removed-item";
        groupItem.innerHTML = `
          <div><strong>Non-ancestors of ${{escapeHtml(focusLabel)}}</strong></div>
          <div class="removed-item-number">${{count}} node${{count !== 1 ? "s" : ""}} hidden — click to restore all</div>
        `;
        groupItem.addEventListener("click", () => {{
          ancestorFocusMode = 0;
          focusNodeId = null;
          saveViewerState();
          applyAncestorFocus();
        }});
        removedNodesEl.appendChild(groupItem);
      }}

      // Individually double-click-hidden nodes
      removedEntities.forEach(entity => {{
        const item = document.createElement("div");
        item.className = "removed-item";
        item.innerHTML = `
          <div><strong>${{escapeHtml(entity.short_title)}}</strong></div>
          <div class="removed-item-number">${{escapeHtml(entity.ref || "")}}</div>
        `;
        item.addEventListener("dblclick", () => {{
          hiddenNodes.delete(entity.id);
          saveViewerState();
          updateVisibilityFast();
          rerouteIncidentEdgesFromCurrentPositions(entity.id);
        }});
        item.addEventListener("click", () => {{
          showEntityDetails(entity);
        }});
        removedNodesEl.appendChild(item);
      }});
      typesetElement(removedNodesEl);
    }}

    // ── Ancestor focus ───────────────────────────────────────────────────────

    function collectAncestors(nodeId) {{
      const keep = new Set();
      const stack = [nodeId];
      while (stack.length) {{
        const current = stack.pop();
        if (!current || keep.has(current)) continue;
        keep.add(current);
        for (const edge of incoming.get(current) || []) stack.push(edge.source);
      }}
      return keep;
    }}

    function syncToolbar() {{
      const hasSelection = !!selectedNodeId && !isHiddenNode(selectedNodeId);
      const hasFocus = !!focusNodeId && !isHiddenNode(focusNodeId);
      // Focus toggle
      focusToggle.disabled = !hasSelection && !hasFocus;
      if ((!hasSelection && !hasFocus) || ancestorFocusMode === 0) {{
        focusToggle.textContent = "Highlight ancestors";
        focusToggle.classList.remove("active");
      }} else if (ancestorFocusMode === 1) {{
        focusToggle.textContent = "Hide non-ancestors";
        focusToggle.classList.add("active");
      }} else {{
        focusToggle.textContent = "Show full graph";
        focusToggle.classList.add("active");
      }}
      // Delete node
      deleteNodeBtn.disabled = !hasSelection;
    }}

    // Keep syncFocusToggle as an alias so existing callsites still work
    function syncFocusToggle() {{ syncToolbar(); }}

    function applyAncestorFocus() {{
      syncFocusToggle();
      ancestorHiddenByFocus.clear();
      const active = ancestorFocusMode > 0 && focusNodeId && !isHiddenNode(focusNodeId);
      const keep = active ? collectAncestors(focusNodeId) : null;

      document.querySelectorAll(".graph-node").forEach(nodeEl => {{
        const nodeId = nodeEl.dataset.nodeId;
        if (active && !keep.has(nodeId)) {{
          if (ancestorFocusMode === 1) {{
            nodeEl.style.opacity = "0.18";
            nodeEl.style.display = "";
          }} else {{
            nodeEl.style.display = "none";
            ancestorHiddenByFocus.add(nodeId);
          }}
        }} else {{
          nodeEl.style.opacity = "1";
          nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
        }}
      }});

      document.querySelectorAll(".edge-path").forEach(pathEl => {{
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        const endpointHidden = isHiddenNode(src) || isHiddenNode(dst);
        if (endpointHidden) {{
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        }} else if (active && ancestorFocusMode === 2 && (ancestorHiddenByFocus.has(src) || ancestorHiddenByFocus.has(dst))) {{
          pathEl.style.display = "none";
          pathEl.style.opacity = "";
        }} else if (active && ancestorFocusMode === 1 && (!keep.has(src) || !keep.has(dst))) {{
          pathEl.style.opacity = "0.08";
          pathEl.style.display = "";
        }} else {{
          pathEl.style.opacity = "0.96";
          pathEl.style.display = "";
        }}
        syncArrowheadForPath(pathEl);
      }});

      renderRemovedNodes();
    }}

    // ── Entity/edge display ──────────────────────────────────────────────────

    function formatEntity(entity) {{
      const description = escapeHtml(entity.description || "");
      const deps = (entity.depends_on || []).map(dep => {{
        const other = entityMap.get(dep.id);
        const name = other ? other.short_title : dep.id;
        const confidence = dep.confidence ? ` (confidence: ${{escapeHtml(dep.confidence)}})` : "";
        return `<li><strong>${{escapeHtml(name)}}</strong> <code>${{escapeHtml(dep.use_type || "")}}</code><br>${{escapeHtml(dep.description || "")}}${{confidence ? `<br><span class="small">${{escapeHtml(dep.evidence || "")}}${{confidence}}</span>` : ""}}</li>`;
      }}).join("");
      return `
        <div class="details-header">
          <h2>${{escapeHtml(entity.short_title)}}</h2>
          <button class="deselect-btn" type="button" title="Deselect (Esc)">✕</button>
        </div>
        <div><strong>Ref:</strong> ${{escapeHtml(entity.ref || "—")}}</div>
        <div><strong>Type:</strong> <code>${{escapeHtml(entity.type)}}</code></div>
        <div><strong>Title:</strong> ${{escapeHtml(entity.title || "—")}}</div>
        <div><strong>Active in:</strong> ${{escapeHtml(entity.active_in || "—")}}</div>
        <div><strong>Source:</strong> ${{escapeHtml(entity.source || "—")}}</div>
        <div><strong>Defined:</strong> ${{escapeHtml(entity.defined || "—")}}</div>
        <p>${{description}}</p>
        <h3>Direct dependencies</h3>
        <ul>${{deps || "<li>None</li>"}}</ul>
      `;
    }}

    function markSelectedNode(nodeId) {{
      nodeLayer.querySelectorAll(".graph-node.selected").forEach(el => el.classList.remove("selected"));
      if (nodeId) {{
        const nodeEl = nodeLayer.querySelector(`[data-node-id="${{nodeId}}"]`);
        if (nodeEl) nodeEl.classList.add("selected");
      }}
      syncToolbar();
    }}

    function showEntityDetails(entity) {{
      details.innerHTML = formatEntity(entity);
      details.querySelector(".deselect-btn")?.addEventListener("click", deselect);
      typesetElement(details);
      rawJsonCodeEl.textContent = JSON.stringify(entity, null, 2);
    }}

    function deselect() {{
      selectedNodeId = null;
      focusNodeId = null;
      ancestorFocusMode = 0;
      markSelectedNode(null);
      details.innerHTML = "Select a node or edge to inspect its metadata.";
      rawJsonCodeEl.textContent = JSON.stringify(docData, null, 2);
      saveViewerState();
      applyAncestorFocus();
    }}

    function clearSelectionDetails() {{
      selectedNodeId = null;
      markSelectedNode(null);
      details.innerHTML = "Select a node or edge to inspect its metadata.";
      rawJsonCodeEl.textContent = JSON.stringify(docData, null, 2);
    }}

    function tooltipText(entity) {{
      return `
        <strong>${{escapeHtml(entity.short_title)}}</strong><br>
        ${{escapeHtml(entity.ref || "")}}<br>
        ${{escapeHtml(entity.type)}}<br>
        ${{escapeHtml(entity.description || "")}}
      `;
    }}

    function edgeTooltipText(edge) {{
      const source = entityMap.get(edge.source);
      const target = entityMap.get(edge.target);
      const sourceLabel = source ? source.short_title : edge.source;
      const targetLabel = target ? target.short_title : edge.target;
      const confidence = edge.confidence ? ` (confidence: ${{escapeHtml(edge.confidence)}})` : "";
      return `
        <strong>${{escapeHtml(sourceLabel)}} → ${{escapeHtml(targetLabel)}}</strong><br>
        <code>${{escapeHtml(edge.use_type || "")}}</code><br>
        ${{escapeHtml(edge.description || "")}}<br>
        <span class="small">${{escapeHtml(edge.evidence || "")}}${{confidence}}</span>
      `;
    }}

    // ── Bridge edge helpers ──────────────────────────────────────────────────

    function confidenceRank(value) {{
      if (value === "Verified") return 3;
      if (value === "Likely") return 2;
      if (value === "Speculative") return 1;
      return 0;
    }}

    function bridgeEdge(sourceId, targetId, hiddenPath, seedEdge) {{
      const hiddenLabels = hiddenPath.map(id => entityMap.get(id)?.short_title || id);
      return {{
        edge_id: `bridge_${{sourceId}}_${{targetId}}_${{hiddenPath.join("_") || "direct"}}`,
        source: sourceId,
        target: targetId,
        use_type: "hidden-bridge",
        description: `Preserves causal reachability across hidden nodes: ${{hiddenLabels.join(" → ")}}.`,
        confidence: seedEdge?.confidence || "Likely",
        evidence: `Bridge path: ${{hiddenLabels.join(" → ")}}`,
        bridge: true
      }};
    }}

    function computeVisibleEdges() {{
      const rendered = new Map();
      function addRendered(edge) {{
        const key = `${{edge.source}}->${{edge.target}}`;
        const existing = rendered.get(key);
        if (!existing) {{ rendered.set(key, edge); return; }}
        const existingScore = (existing.bridge ? 0 : 10) + confidenceRank(existing.confidence);
        const newScore = (edge.bridge ? 0 : 10) + confidenceRank(edge.confidence);
        if (newScore > existingScore) rendered.set(key, edge);
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
        return {{ shape: typeStyles[renderTypeOverrides[entity.id]].shape, color: typeStyles.corollary.color }};
      }}
      const style = typeStyles[entity.type] || {{ shape: "rect", color: "#566573" }};
      return {{ shape: style.shape, color: style.color }};
    }}

    // ── ELK layout ───────────────────────────────────────────────────────────

    function ensureElk() {{
      if (elk) return elk;
      if (typeof ELK === "undefined") return null;
      elk = new ELK();
      return elk;
    }}

    async function computeLayout(visibleEntities, visibleEdges) {{
      const elkInstance = ensureElk();
      if (!elkInstance) throw new Error("ELK failed to load.");
      const graph = {{
        id: "root",
        layoutOptions: {{
          "elk.algorithm": "layered",
          "elk.direction": "RIGHT",
          "elk.edgeRouting": "ORTHOGONAL",
          "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
          "elk.separateConnectedComponents": "true",
          "elk.spacing.nodeNode": String(routingConfig.nodeSpacing),
          "elk.layered.spacing.nodeNode": String(routingConfig.nodeSpacing),
          "elk.layered.spacing.nodeNodeBetweenLayers": String(routingConfig.layerSpacing),
          "elk.layered.spacing.edgeNodeBetweenLayers": String(routingConfig.edgeNodeSpacing),
          "elk.padding": "[left=40,top=40,right=40,bottom=40]"
        }},
        children: visibleEntities.map(e => ({{ id: e.id, width: 210, height: 68 }})),
        edges: visibleEdges.map((edge, idx) => ({{ id: `elk_edge_${{idx}}`, sources: [edge.source], targets: [edge.target] }}))
      }};
      return elkInstance.layout(graph);
    }}

    function pointsForSection(section) {{
      return [section.startPoint, ...(section.bendPoints || []), section.endPoint].filter(Boolean);
    }}

    function offsetEndpointAwayFromNode(points, endpointIndex, nodeId) {{
      if (points.length === 0) return points;
      const nodePos = lastNodePositions.get(nodeId);
      if (!nodePos) return points;
      const updated = points.map(p => ({{ x: p.x, y: p.y }}));
      const point = updated[endpointIndex];
      const cx = nodePos.x + nodePos.width / 2;
      const cy = nodePos.y + nodePos.height / 2;
      const dx = point.x - cx;
      const dy = point.y - cy;
      const length = Math.hypot(dx, dy) || 1;
      updated[endpointIndex] = {{
        x: point.x + edgeNodeGap() * dx / length,
        y: point.y + edgeNodeGap() * dy / length
      }};
      return updated;
    }}

    function offsetEdgeEndpoints(points, sourceId, targetId) {{
      let updated = offsetEndpointAwayFromNode(points, 0, sourceId);
      updated = offsetEndpointAwayFromNode(updated, updated.length - 1, targetId);
      return updated;
    }}

    function enforceVerticalNodeSpacing(children) {{
      const layers = new Map();
      (children || []).forEach(node => {{
        const key = String(Math.round((node.x || 0) / 20) * 20);
        if (!layers.has(key)) layers.set(key, []);
        layers.get(key).push(node);
      }});
      layers.forEach(layer => {{
        layer.sort((a, b) => (a.y || 0) - (b.y || 0));
        let nextY = null;
        layer.forEach(node => {{
          if (nextY !== null && (node.y || 0) < nextY) node.y = nextY;
          nextY = (node.y || 0) + (node.height || 68) + routingConfig.nodeSpacing;
        }});
      }});
    }}

    function roundedPathForPoints(points, radius = routingConfig.cornerRadius) {{
      if (!points.length) return "";
      if (points.length < 3) {{
        let d = `M ${{points[0].x}} ${{points[0].y}}`;
        for (let i = 1; i < points.length; i++) d += ` L ${{points[i].x}} ${{points[i].y}}`;
        return d;
      }}
      let d = `M ${{points[0].x}} ${{points[0].y}}`;
      for (let i = 1; i < points.length - 1; i++) {{
        const prev = points[i - 1], curr = points[i], next = points[i + 1];
        const inDx = curr.x - prev.x, inDy = curr.y - prev.y;
        const outDx = next.x - curr.x, outDy = next.y - curr.y;
        const inLen = Math.hypot(inDx, inDy), outLen = Math.hypot(outDx, outDy);
        if (inLen < 1e-6 || outLen < 1e-6) {{ d += ` L ${{curr.x}} ${{curr.y}}`; continue; }}
        const r = Math.min(radius, inLen / 2, outLen / 2);
        const p1x = curr.x - (inDx / inLen) * r, p1y = curr.y - (inDy / inLen) * r;
        const p2x = curr.x + (outDx / outLen) * r, p2y = curr.y + (outDy / outLen) * r;
        d += ` L ${{p1x}} ${{p1y}} Q ${{curr.x}} ${{curr.y}} ${{p2x}} ${{p2y}}`;
      }}
      const last = points[points.length - 1];
      d += ` L ${{last.x}} ${{last.y}}`;
      return d;
    }}

    function pathPointsForArrow(pathEl) {{
      try {{
        const length = pathEl.getTotalLength();
        if (length <= 0) return null;
        const tip = pathEl.getPointAtLength(length);
        const tail = pathEl.getPointAtLength(Math.max(0, length - 14));
        return {{ tip, tail }};
      }} catch (error) {{
        return null;
      }}
    }}

    function arrowForPath(pathEl) {{
      if (!pathEl || !pathEl.dataset.edgeId) return null;
      return edgeLayer.querySelector(`.edge-arrow[data-edge-id="${{pathEl.dataset.edgeId}}"]`);
    }}

    function syncArrowheadForPath(pathEl) {{
      const arrowEl = arrowForPath(pathEl);
      if (!arrowEl) return;
      if (pathEl.style.display === "none") {{
        arrowEl.style.display = "none";
        return;
      }}
      const points = pathPointsForArrow(pathEl);
      if (!points) return;
      const dx = points.tip.x - points.tail.x;
      const dy = points.tip.y - points.tail.y;
      const length = Math.hypot(dx, dy) || 1;
      const ux = dx / length;
      const uy = dy / length;
      const size = 8;
      const halfWidth = 4;
      const baseX = points.tip.x - ux * size;
      const baseY = points.tip.y - uy * size;
      const leftX = baseX + -uy * halfWidth;
      const leftY = baseY + ux * halfWidth;
      const rightX = baseX - -uy * halfWidth;
      const rightY = baseY - ux * halfWidth;
      arrowEl.setAttribute("points", `${{points.tip.x}},${{points.tip.y}} ${{leftX}},${{leftY}} ${{rightX}},${{rightY}}`);
      arrowEl.style.display = pathEl.style.display;
      arrowEl.style.opacity = pathEl.style.opacity;
      arrowEl.style.filter = pathEl.style.filter;
      arrowEl.setAttribute("fill", pathEl.style.stroke || pathEl.getAttribute("stroke") || "#111111");
      arrowEl.dataset.sourceNodeId = pathEl.dataset.sourceNodeId;
      arrowEl.dataset.targetNodeId = pathEl.dataset.targetNodeId;
      arrowEl.dataset.bridge = pathEl.dataset.bridge;
    }}

    function attachArrowhead(pathEl) {{
      const existing = arrowForPath(pathEl);
      if (existing) existing.remove();
      const arrowEl = createSvgElement("polygon");
      arrowEl.setAttribute("class", "edge-arrow");
      arrowEl.dataset.edgeId = pathEl.dataset.edgeId;
      edgeLayer.appendChild(arrowEl);
      syncArrowheadForPath(pathEl);
      return arrowEl;
    }}

    function mergedTargetPoints(edge, points, targetCounts) {{
      if ((targetCounts.get(edge.target) || 0) <= 1) return points;
      if (points.length < 2) return points;
      const targetPos = lastNodePositions.get(edge.target);
      if (!targetPos) return points;
      const mergedEnd = {{ x: targetPos.x, y: targetPos.y + targetPos.height / 2 }};
      const mergedEntry = {{ x: targetPos.x - edgeNodeGap() - Math.max(MIN_ARROW_LANDING_RUN, routingConfig.mergeLaneDistance), y: mergedEnd.y }};
      const updated = points.map(p => ({{ x: p.x, y: p.y }}));
      if (updated.length === 2) return [updated[0], mergedEntry, mergedEnd];
      updated[updated.length - 2] = mergedEntry;
      updated[updated.length - 1] = mergedEnd;
      return updated;
    }}

    // ── Node rendering ───────────────────────────────────────────────────────

    function expandSelectionRing(ring, x, y, w, h, shape) {{
      const gap = SELECTION_RING_GAP;
      const tag = ring.tagName.toLowerCase();
      if (tag === "rect") {{
        ring.setAttribute("x", x - gap);
        ring.setAttribute("y", y - gap);
        ring.setAttribute("width", w + 2 * gap);
        ring.setAttribute("height", h + 2 * gap);
        if (shape === "roundrect") {{
          ring.setAttribute("rx", 18 + gap);
          ring.setAttribute("ry", 18 + gap);
        }}
        return;
      }}
      if (tag === "circle") {{
        const r = Number(ring.getAttribute("r") || 0);
        ring.setAttribute("r", r + gap);
        return;
      }}
      if (tag === "ellipse") {{
        const rx = Number(ring.getAttribute("rx") || 0);
        const ry = Number(ring.getAttribute("ry") || 0);
        ring.setAttribute("rx", rx + gap);
        ring.setAttribute("ry", ry + gap);
        return;
      }}
      if (tag === "polygon") {{
        const rawPoints = (ring.getAttribute("points") || "")
          .trim()
          .split(/\\s+/)
          .map(pair => pair.split(",").map(Number))
          .filter(pair => pair.length === 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]));
        if (rawPoints.length < 3) return;

        const signedArea = rawPoints.reduce((sum, [x1, y1], idx) => {{
          const [x2, y2] = rawPoints[(idx + 1) % rawPoints.length];
          return sum + x1 * y2 - x2 * y1;
        }}, 0);
        const outwardSign = signedArea >= 0 ? 1 : -1;

        const offsetLines = rawPoints.map(([x1, y1], idx) => {{
          const [x2, y2] = rawPoints[(idx + 1) % rawPoints.length];
          const dx = x2 - x1;
          const dy = y2 - y1;
          const length = Math.hypot(dx, dy) || 1;
          const nx = outwardSign * dy / length;
          const ny = -outwardSign * dx / length;
          return {{
            p: {{ x: x1 + gap * nx, y: y1 + gap * ny }},
            d: {{ x: dx, y: dy }}
          }};
        }});

        function lineIntersection(lineA, lineB, fallback) {{
          const cross = lineA.d.x * lineB.d.y - lineA.d.y * lineB.d.x;
          if (Math.abs(cross) < 1e-6) return fallback;
          const px = lineB.p.x - lineA.p.x;
          const py = lineB.p.y - lineA.p.y;
          const t = (px * lineB.d.y - py * lineB.d.x) / cross;
          return {{ x: lineA.p.x + t * lineA.d.x, y: lineA.p.y + t * lineA.d.y }};
        }}

        const expanded = rawPoints.map((point, idx) => {{
          const prev = offsetLines[(idx + rawPoints.length - 1) % rawPoints.length];
          const current = offsetLines[idx];
          return lineIntersection(prev, current, {{ x: point[0], y: point[1] }});
        }}).map(point => `${{point.x}},${{point.y}}`);
        if (expanded.length) ring.setAttribute("points", expanded.join(" "));
      }}
    }}

    function renderNode(entity, position) {{
      const style = nodeStyle(entity);
      const x = position.x, y = position.y, w = 210, h = 68;
      const stroke = "#1f2933";
      const isInferred = entity.source === "inferred";
      const group = createSvgElement("g");
      group.setAttribute("class", "graph-node");
      group.dataset.nodeId = entity.id;

      let shapeEl = null;
      let selectionRing = null;
      if (style.shape === "ellipse") {{
        shapeEl = createSvgElement("ellipse");
        shapeEl.setAttribute("cx", x + w / 2); shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("rx", w / 2); shapeEl.setAttribute("ry", h / 2);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "circle") {{
        shapeEl = createSvgElement("circle");
        shapeEl.setAttribute("cx", x + w / 2); shapeEl.setAttribute("cy", y + h / 2);
        shapeEl.setAttribute("r", Math.min(w, h) / 2);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "diamond") {{
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${{x + w / 2}},${{y}} ${{x + w}},${{y + h / 2}} ${{x + w / 2}},${{y + h}} ${{x}},${{y + h / 2}}`);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "hexagon") {{
        const inset = 26;
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${{x + inset}},${{y}} ${{x + w - inset}},${{y}} ${{x + w}},${{y + h / 2}} ${{x + w - inset}},${{y + h}} ${{x + inset}},${{y + h}} ${{x}},${{y + h / 2}}`);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "parallelogram") {{
        const skew = 20;
        shapeEl = createSvgElement("polygon");
        shapeEl.setAttribute("points", `${{x + skew}},${{y}} ${{x + w}},${{y}} ${{x + w - skew}},${{y + h}} ${{x}},${{y + h}}`);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "roundrect") {{
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x); shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w); shapeEl.setAttribute("height", h);
        shapeEl.setAttribute("rx", 18); shapeEl.setAttribute("ry", 18);
        selectionRing = shapeEl.cloneNode(false);
      }} else if (style.shape === "double-rect") {{
        const outer = createSvgElement("rect");
        outer.setAttribute("x", x); outer.setAttribute("y", y);
        outer.setAttribute("width", w); outer.setAttribute("height", h);
        outer.setAttribute("class", "node-shape");
        outer.setAttribute("fill", style.color); outer.setAttribute("stroke", stroke);
        outer.setAttribute("stroke-width", "2");
        if (isInferred) outer.setAttribute("stroke-dasharray", "6 3");
        group.appendChild(outer);
        const inner = createSvgElement("rect");
        inner.setAttribute("x", x + 6); inner.setAttribute("y", y + 6);
        inner.setAttribute("width", w - 12); inner.setAttribute("height", h - 12);
        inner.setAttribute("fill", "none"); inner.setAttribute("stroke", stroke);
        inner.setAttribute("stroke-width", "2");
        group.appendChild(inner);
        selectionRing = outer.cloneNode(false);
      }} else {{
        shapeEl = createSvgElement("rect");
        shapeEl.setAttribute("x", x); shapeEl.setAttribute("y", y);
        shapeEl.setAttribute("width", w); shapeEl.setAttribute("height", h);
        selectionRing = shapeEl.cloneNode(false);
      }}

      if (selectionRing) {{
        expandSelectionRing(selectionRing, x, y, w, h, style.shape);
        selectionRing.setAttribute("class", "selection-ring");
        group.appendChild(selectionRing);
      }}

      if (shapeEl) {{
        shapeEl.setAttribute("class", "node-shape");
        shapeEl.setAttribute("fill", style.color);
        shapeEl.setAttribute("stroke", stroke);
        shapeEl.setAttribute("stroke-width", "2");
        if (isInferred) shapeEl.setAttribute("stroke-dasharray", "6 3");
        group.appendChild(shapeEl);
      }}

      const foreignObject = createSvgElement("foreignObject");
      foreignObject.setAttribute("x", x); foreignObject.setAttribute("y", y);
      foreignObject.setAttribute("width", w); foreignObject.setAttribute("height", h);
      const body = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
      body.setAttribute("class", "node-fo-body");
      body.innerHTML = `<div class="node-label">${{escapeHtml(entity.short_title)}}</div><div class="node-subtitle">${{escapeHtml(entity.type + (entity.ref ? " " + entity.ref : ""))}}</div>`;
      foreignObject.appendChild(body);
      group.appendChild(foreignObject);
      return group;
    }}

    // ── Edge emphasis ────────────────────────────────────────────────────────

    function emphasizeEdge(pathEl, strokeColor = null) {{
      if (!pathEl) return;
      if (pathEl.parentNode === edgeLayer) edgeLayer.appendChild(pathEl);
      const arrowEl = arrowForPath(pathEl);
      if (arrowEl && arrowEl.parentNode === edgeLayer) edgeLayer.appendChild(arrowEl);
      if (strokeColor) pathEl.style.stroke = strokeColor;
      pathEl.style.strokeWidth = "3";
      pathEl.style.opacity = "0.98";
      pathEl.style.filter = "drop-shadow(0 0 1px rgba(17, 24, 39, 0.18))";
      syncArrowheadForPath(pathEl);
    }}

    function clearEdgeEmphasis(pathEl) {{
      if (!pathEl) return;
      pathEl.style.stroke = "";
      pathEl.style.strokeWidth = "";
      pathEl.style.opacity = "";
      pathEl.style.filter = "";
      syncArrowheadForPath(pathEl);
    }}

    function setIncomingEdgeHighlight(nodeId, active) {{
      document.querySelectorAll(`.edge-path[data-target-node-id="${{nodeId}}"]`).forEach(pathEl => {{
        if (active) emphasizeEdge(pathEl);
        else clearEdgeEmphasis(pathEl);
      }});
      if (!active) applyAncestorFocus();
    }}

    // ── Event binding for edges/nodes ────────────────────────────────────────

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
        if (draggingNodeId) return;
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
        if (nodeDragMoved) return; // suppress click after drag
        if (nodeClickTimer) clearTimeout(nodeClickTimer);
        nodeClickTimer = setTimeout(() => {{
          nodeClickTimer = null;
          if (selectedNodeId === entity.id) {{ deselect(); return; }} // click again to deselect
          selectedNodeId = entity.id;
          markSelectedNode(entity.id);
          showEntityDetails(entity);
          saveViewerState();
          applyAncestorFocus();
        }}, 180);
      }});
      nodeEl.addEventListener("dblclick", event => {{
        event.preventDefault();
        if (nodeClickTimer) {{
          clearTimeout(nodeClickTimer);
          nodeClickTimer = null;
        }}
        hiddenNodes.add(entity.id);
        if (selectedNodeId === entity.id) {{
          clearSelectionDetails();
        }}
        if (focusNodeId === entity.id) {{
          focusNodeId = null;
          if (ancestorFocusMode > 0) ancestorFocusMode = 0;
        }}
        saveViewerState();
        updateVisibilityFast();
      }});
      // Node drag (mousedown)
      nodeEl.addEventListener("mousedown", event => {{
        if (event.button !== 0) return;
        draggingNodeId = entity.id;
        nodeDragMoved = false;
        dragStartClientX = event.clientX;
        dragStartClientY = event.clientY;
        const cur = manualPositions.get(entity.id);
        const orig = lastNodePositions.get(entity.id) || {{x: 0, y: 0}};
        dragStartOffsetX = cur ? cur.x - orig.x : 0;
        dragStartOffsetY = cur ? cur.y - orig.y : 0;
        nodeEl.classList.add("dragging-node");
        event.stopPropagation();
      }});
    }}

    // ── Full ELK-based layout/render ─────────────────────────────────────────

    async function updateVisibilityFull() {{
      renderRemovedNodes();
      edgeLayer.innerHTML = "";
      nodeLayer.innerHTML = "";
      lastRenderedEdges = [];
      const visibleEntities = docData.entities.filter(e => !isHiddenNode(e.id));
      const visibleEdges = computeVisibleEdges().filter(e => !isHiddenNode(e.source) && !isHiddenNode(e.target));
      const currentVersion = ++renderVersion;

      if (visibleEntities.length === 0) {{
        elkStatus.textContent = "No visible nodes.";
        svgEl.setAttribute("width", "800"); svgEl.setAttribute("height", "200");
        svgEl.setAttribute("viewBox", "0 0 800 200");
        return;
      }}

      try {{
        elkStatus.textContent = "Rendering graph layout...";
        const graph = await computeLayout(visibleEntities, visibleEdges);
        if (currentVersion !== renderVersion) return;
        elkStatus.textContent = "";
        enforceVerticalNodeSpacing(graph.children || []);

        const nodeLookup = new Map((graph.children || []).map(n => [n.id, n]));
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
        hasFullLayout = true;

        const graphWidth = Math.max(
          900,
          ...Array.from(lastNodePositions.values()).map(pos => pos.x + pos.width + 80)
        );
        const graphHeight = Math.max(
          500,
          ...Array.from(lastNodePositions.values()).map(pos => pos.y + pos.height + 80)
        );
        svgEl.setAttribute("width", String(graphWidth));
        svgEl.setAttribute("height", String(graphHeight));
        svgEl.setAttribute("viewBox", `0 0 ${{graphWidth}} ${{graphHeight}}`);
        if (!hasFittedOnce) {{ fitGraph(); hasFittedOnce = true; }}

        const targetCounts = new Map();
        visibleEdges.forEach(edge => targetCounts.set(edge.target, (targetCounts.get(edge.target) || 0) + 1));

        (graph.edges || []).forEach((elkEdge) => {{
          const idx = parseInt(elkEdge.id.replace("elk_edge_", ""), 10);
          const meta = visibleEdges[idx];
          const section = (elkEdge.sections || [])[0];
          if (!section || !meta) return;
          const points = offsetEdgeEndpoints(
            mergedTargetPoints(meta, pointsForSection(section), targetCounts),
            meta.source,
            meta.target
          );
          const path = createSvgElement("path");
          path.setAttribute("class", "edge-path");
          path.setAttribute("d", roundedPathForPoints(points));
          path.setAttribute("stroke", edgeColorForTarget(meta.target));
          if (meta.confidence === "Likely") path.setAttribute("stroke-dasharray", "8 5");
          else if (meta.confidence === "Speculative") path.setAttribute("stroke-dasharray", "3 5");
          else if (meta.bridge) path.setAttribute("stroke-dasharray", "6 4");
          path.dataset.edgeId = meta.edge_id || elkEdge.id;
          path.dataset.targetNodeId = meta.target;
          path.dataset.sourceNodeId = meta.source;
          path.dataset.bridge = meta.bridge ? "true" : "false";
          edgeLayer.appendChild(path);
          attachArrowhead(path);
          bindEdgeHover(path, meta);
          lastRenderedEdges.push(meta);
        }});

        visibleEntities.forEach(entity => {{
          const positioned = lastNodePositions.get(entity.id);
          if (!positioned) return;
          const nodeEl = renderNode(entity, positioned);
          // Restore manual position as transform offset
          const manual = manualPositions.get(entity.id);
          if (manual) {{
            const dx = manual.x - positioned.x;
            const dy = manual.y - positioned.y;
            if (dx !== 0 || dy !== 0) nodeEl.setAttribute("transform", `translate(${{dx}},${{dy}})`);
          }}
          bindNodeInteractions(nodeEl, entity);
          nodeLayer.appendChild(nodeEl);
        }});

        manualPositions.forEach((_, nodeId) => {{
          if (!isHiddenNode(nodeId)) rerouteIncidentEdgesFromCurrentPositions(nodeId);
        }});
        rerouteAllVisibleEdgesFromCurrentPositions();

        applyAncestorFocus();
        typesetElement(nodeLayer);

        // Restore selection highlight and details
        if (selectedNodeId && entityMap.has(selectedNodeId)) {{
          markSelectedNode(selectedNodeId);
          showEntityDetails(entityMap.get(selectedNodeId));
        }} else {{
          syncToolbar();
          rawJsonCodeEl.textContent = JSON.stringify(docData, null, 2);
        }}
      }} catch (error) {{
        elkStatus.textContent = `ELK layout failed: ${{error.message || error}}`;
      }}
    }}

    // ── Fast visibility toggle (no ELK re-run) ───────────────────────────────

    function updateVisibilityFast() {{
      if (!hasFullLayout) {{ updateVisibilityFull(); return; }}
      renderRemovedNodes();

      // Toggle node elements
      nodeLayer.querySelectorAll(".graph-node").forEach(nodeEl => {{
        const nodeId = nodeEl.dataset.nodeId;
        nodeEl.style.display = isHiddenNode(nodeId) ? "none" : "";
      }});

      // Toggle non-bridge edge elements
      edgeLayer.querySelectorAll(".edge-path[data-bridge='false']").forEach(pathEl => {{
        const src = pathEl.dataset.sourceNodeId;
        const dst = pathEl.dataset.targetNodeId;
        pathEl.style.display = (!isHiddenNode(src) && !isHiddenNode(dst)) ? "" : "none";
        syncArrowheadForPath(pathEl);
      }});

      // Remove all bridge edges; recompute them from current node positions.
      edgeLayer.querySelectorAll(".edge-path[data-bridge='true']").forEach(el => el.remove());
      edgeLayer.querySelectorAll(".edge-arrow[data-bridge='true']").forEach(el => el.remove());

      const visibleEdges = computeVisibleEdges();
      visibleEdges.forEach(edge => {{
        if (!edge.bridge) return;
        const srcPos = getEffectivePos(edge.source);
        const dstPos = getEffectivePos(edge.target);
        if (!srcPos || !dstPos) return;
        const path = createSvgElement("path");
        path.setAttribute("class", "edge-path");
        path.setAttribute("d", manualDoglegPath(srcPos, dstPos));
        path.setAttribute("stroke", edgeColorForTarget(edge.target));
        path.setAttribute("stroke-dasharray", "6 4");
        path.dataset.edgeId = edge.edge_id || `bridge_${{edge.source}}_${{edge.target}}`;
        path.dataset.targetNodeId = edge.target;
        path.dataset.sourceNodeId = edge.source;
        path.dataset.bridge = "true";
        edgeLayer.appendChild(path);
        attachArrowhead(path);
        bindEdgeHover(path, edge);
      }});

      applyAncestorFocus();
    }}

    // ── Node dragging (SVG mouse events) ─────────────────────────────────────

    document.addEventListener("mousemove", event => {{
      if (isPanning) {{
        panX = panStartX + (event.clientX - panStartClientX);
        panY = panStartY + (event.clientY - panStartClientY);
        applyTransform();
        return;
      }}
      if (draggingNodeId === null) return;
      const clientDx = event.clientX - dragStartClientX;
      const clientDy = event.clientY - dragStartClientY;
      if (!nodeDragMoved && Math.hypot(clientDx, clientDy) < DRAG_THRESHOLD) return;
      nodeDragMoved = true;
      tooltip.style.display = "none";

      // Convert client delta to SVG coordinate delta
      const rect = svgEl.getBoundingClientRect();
      const viewBox = svgEl.viewBox.baseVal;
      const scaleX = viewBox.width / rect.width;
      const scaleY = viewBox.height / rect.height;
      const svgDx = clientDx * scaleX;
      const svgDy = clientDy * scaleY;

      const offsetX = dragStartOffsetX + svgDx;
      const offsetY = dragStartOffsetY + svgDy;

      const nodeEl = nodeLayer.querySelector(`[data-node-id="${{draggingNodeId}}"]`);
      if (nodeEl) nodeEl.setAttribute("transform", `translate(${{offsetX}},${{offsetY}})`);

      const origPos = lastNodePositions.get(draggingNodeId);
      if (origPos) {{
        manualPositions.set(draggingNodeId, {{
          x: origPos.x + offsetX,
          y: origPos.y + offsetY,
          width: origPos.width,
          height: origPos.height
        }});
      }}

      updateEdgesForNode(draggingNodeId);
    }});

    document.addEventListener("mouseup", event => {{
      if (isPanning) {{
        isPanning = false;
        canvasWrapEl.classList.remove("panning");
        saveViewerState();
        return;
      }}
      if (draggingNodeId !== null) {{
        const droppedNodeId = draggingNodeId;
        const nodeEl = nodeLayer.querySelector(`[data-node-id="${{draggingNodeId}}"]`);
        if (nodeEl) nodeEl.classList.remove("dragging-node");
        if (nodeDragMoved) {{
          rerouteIncidentEdgesFromCurrentPositions(droppedNodeId);
          saveViewerState();
        }}
        draggingNodeId = null;
        // Keep nodeDragMoved true briefly to suppress the click event
        setTimeout(() => {{ nodeDragMoved = false; }}, 0);
      }}
    }});

    // ── Toolbar button handlers ───────────────────────────────────────────────

    deleteNodeBtn.addEventListener("click", () => {{
      if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
      const nodeId = selectedNodeId;
      hiddenNodes.add(nodeId);
      clearSelectionDetails();
      if (focusNodeId === nodeId) {{
        focusNodeId = null;
        if (ancestorFocusMode > 0) ancestorFocusMode = 0;
      }}
      saveViewerState();
      updateVisibilityFast();
    }});

    document.getElementById("redraw-btn").addEventListener("click", () => {{
      manualPositions.clear();
      hasFittedOnce = false;
      nodeLayer.querySelectorAll(".graph-node").forEach(el => el.removeAttribute("transform"));
      updateVisibilityFull();
      saveViewerState();
    }});

    function syncLegendRows() {{
      document.querySelectorAll(".legend-row").forEach(row => {{
        row.classList.toggle("inactive", hiddenTypes.has(row.dataset.type));
      }});
    }}

    function resetViewState({{includeCategories = false}} = {{}}) {{
      hiddenNodes.clear();
      selectedNodeId = null;
      focusNodeId = null;
      ancestorFocusMode = 0;
      ancestorHiddenByFocus.clear();
      manualPositions.clear();
      hasFittedOnce = false;
      if (includeCategories) {{
        hiddenTypes.clear();
        syncLegendRows();
      }}
      if (includeCategories) localStorage.removeItem(viewerStateKey);
      else saveViewerState();
      updateVisibilityFull();
    }}

    const resetBtn = document.getElementById("reset-btn");
    let resetClickTimer = null;
    resetBtn.addEventListener("click", () => {{
      if (resetClickTimer) clearTimeout(resetClickTimer);
      resetClickTimer = setTimeout(() => {{
        resetClickTimer = null;
        resetViewState({{includeCategories: false}});
      }}, 180);
    }});
    resetBtn.addEventListener("dblclick", event => {{
      event.preventDefault();
      if (resetClickTimer) {{
        clearTimeout(resetClickTimer);
        resetClickTimer = null;
      }}
      resetViewState({{includeCategories: true}});
    }});

    document.getElementById("zoom-in-btn").addEventListener("click", () => {{
      const r = canvasWrapEl.getBoundingClientRect();
      zoomAt(zoomLevel * 1.3, r.left + r.width / 2, r.top + r.height / 2);
      saveViewerState();
    }});

    document.getElementById("zoom-out-btn").addEventListener("click", () => {{
      const r = canvasWrapEl.getBoundingClientRect();
      zoomAt(zoomLevel / 1.3, r.left + r.width / 2, r.top + r.height / 2);
      saveViewerState();
    }});

    document.getElementById("fit-btn").addEventListener("click", () => {{
      fitGraph();
      saveViewerState();
    }});

    // ── Sidebar drag-to-reorder ───────────────────────────────────────────────

    let dragSrcSection = null;
    panelContent.addEventListener("dragstart", event => {{
      const handle = event.target.closest(".drag-handle");
      if (!handle) {{ event.preventDefault(); return; }}
      const section = handle.closest(".sidebar-section");
      if (!section) {{ event.preventDefault(); return; }}
      dragSrcSection = section;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", section.dataset.sectionId);
      section.classList.add("dragging");
    }});
    panelContent.addEventListener("dragend", () => {{
      panelContent.querySelectorAll(".sidebar-section.dragging").forEach(el => el.classList.remove("dragging"));
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
      dragSrcSection = null;
      saveSidebarOrder();
    }});
    panelContent.addEventListener("dragover", event => {{
      event.preventDefault();
      const target = event.target.closest(".sidebar-section");
      if (!target || target === dragSrcSection) return;
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
      target.classList.add("drag-over");
      event.dataTransfer.dropEffect = "move";
    }});
    panelContent.addEventListener("drop", event => {{
      event.preventDefault();
      const target = event.target.closest(".sidebar-section");
      if (!target || target === dragSrcSection || !dragSrcSection) return;
      panelContent.insertBefore(dragSrcSection, target);
      panelContent.querySelectorAll(".sidebar-section.drag-over").forEach(el => el.classList.remove("drag-over"));
    }});

    // ── Routing controls ─────────────────────────────────────────────────────

    function applyEdgeRoutingChange(patch) {{
      applyRoutingPatch(patch);
      saveViewerState();
      rerouteAllVisibleEdgesFromCurrentPositions();
    }}

    function applyLayoutRoutingChange(patch) {{
      applyRoutingPatch(patch);
      saveViewerState();
      updateVisibilityFull();
    }}

    routingCompactnessSelect.addEventListener("change", () => {{
      const presetName = routingCompactnessSelect.value;
      applyLayoutRoutingChange({{
        compactnessPreset: presetName,
        ...routingPresets[presetName]
      }});
    }});

    routingShapeSelect.addEventListener("change", () => {{
      const presetName = routingShapeSelect.value;
      applyEdgeRoutingChange({{
        shapePreset: presetName,
        ...shapePresets[presetName]
      }});
    }});

    ["extraClearance", "cornerRadius", "parallelSpacing", "mergeLaneDistance"].forEach(key => {{
      routingInputs[key].addEventListener("input", () => {{
        applyEdgeRoutingChange({{ [key]: Number(routingInputs[key].value) }});
      }});
    }});

    ["nodeSpacing", "layerSpacing", "edgeNodeSpacing"].forEach(key => {{
      routingInputs[key].addEventListener("input", () => {{
        applyLayoutRoutingChange({{ [key]: Number(routingInputs[key].value) }});
      }});
    }});

    // ── Other event listeners ────────────────────────────────────────────────

    focusToggle.addEventListener("click", () => {{
      if (ancestorFocusMode === 0) {{
        if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
        focusNodeId = selectedNodeId;
      }} else if (!focusNodeId || isHiddenNode(focusNodeId)) {{
        if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
        focusNodeId = selectedNodeId;
      }}
      ancestorFocusMode = (ancestorFocusMode + 1) % 3;
      if (ancestorFocusMode === 0) focusNodeId = null;
      saveViewerState();
      applyAncestorFocus();
    }});

    document.addEventListener("keydown", event => {{
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (event.key === "Escape") {{
        event.preventDefault();
        deselect();
        return;
      }}
      if (event.key.toLowerCase() === "h") {{
        if (ancestorFocusMode === 0) {{
          if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
          focusNodeId = selectedNodeId;
        }} else if (!focusNodeId || isHiddenNode(focusNodeId)) {{
          if (!selectedNodeId || isHiddenNode(selectedNodeId)) return;
          focusNodeId = selectedNodeId;
        }}
        event.preventDefault();
        ancestorFocusMode = (ancestorFocusMode + 1) % 3;
        if (ancestorFocusMode === 0) focusNodeId = null;
        saveViewerState();
        applyAncestorFocus();
        return;
      }}
      if (event.key.toLowerCase() === "r") {{
        event.preventDefault();
        manualPositions.clear();
        nodeLayer.querySelectorAll(".graph-node").forEach(el => el.removeAttribute("transform"));
        updateVisibilityFull();
        saveViewerState();
        return;
      }}
      if (event.key.toLowerCase() === "c") {{
        event.preventDefault();
        resetViewState({{includeCategories: event.shiftKey}});
        return;
      }}
      if (event.key === "+" || event.key === "=") {{
        event.preventDefault();
        const r = canvasWrapEl.getBoundingClientRect();
        zoomAt(zoomLevel * 1.2, r.left + r.width / 2, r.top + r.height / 2);
        saveViewerState();
        return;
      }}
      if (event.key === "-") {{
        event.preventDefault();
        const r = canvasWrapEl.getBoundingClientRect();
        zoomAt(zoomLevel / 1.2, r.left + r.width / 2, r.top + r.height / 2);
        saveViewerState();
        return;
      }}
      if (event.key.toLowerCase() === "f") {{
        event.preventDefault();
        fitGraph();
        saveViewerState();
        return;
      }}
    }});

    // ── Initialization ────────────────────────────────────────────────────────

    (function applyDocumentTitle() {{
      const t = docData.document?.title;
      if (!t) return;
      document.getElementById("panel-title").textContent = t;
    }})();

    startBuildRefreshWatcher();
    restoreViewerState();
    syncRoutingControls();
    syncPanelToggle();
    restoreSidebarOrder();

    window.addEventListener("load", () => {{
      if (window.MathJax && window.MathJax.typesetPromise) {{
        window.MathJax.typesetPromise().catch(() => {{}});
      }}
      updateVisibilityFull();
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
        "--tex-entry",
        dest="tex_entry",
        help="TeX entrypoint used to extract MathJax macros. Defaults to document.source_entrypoint when present.",
    )
    parser.add_argument(
        "--macro-file",
        dest="macro_file",
        help="MathJax macro JSON file to merge before rendering. Defaults to _build/<entry>-mathjax-macros.json.",
    )
    parser.add_argument(
        "--refresh-macros",
        action="store_true",
        help="Regenerate the default macro file from the TeX entrypoint before rendering.",
    )
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
    macro_path = prepare_macro_file(args, source_path, doc)
    macro_count = merge_mathjax_macros(doc, macro_path)
    reduction_note = ""
    removed_edges: list[dict] = []
    if args.reduce_transitive_edges:
        doc, removed_edges = reduce_transitive_edges(doc)
        reduction_note = f"Graph-theoretic transitive reduction enabled: removed {len(removed_edges)} redundant edges from the rendered view."

    if args.html_out:
        html_path = Path(args.html_out).resolve()
    else:
        build_dir = source_path.parent / "_build"
        build_dir.mkdir(exist_ok=True)
        html_path = build_dir / source_path.with_suffix(".html").name
    html_path.write_text(build_html_with_elk(doc, reduction_note=reduction_note), encoding="utf-8")

    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(doc["entities"]),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
                "macro_file": str(macro_path) if macro_path else None,
                "macros_from_file": macro_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
