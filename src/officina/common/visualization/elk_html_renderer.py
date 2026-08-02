#!/usr/bin/env python3
"""Public ELK HTML renderer facade.

``build_html_with_elk`` converts a normalized graph payload into one standalone
HTML document. It prepares renderer-neutral graph data; the private
``html_renderer`` package owns document assembly and browser behavior. Domain
adapters must describe semantics through the graph payload rather than adding
special cases here.

See ``html_renderer/README.md`` for the payload contract, browser architecture,
extension rules, and a minimal usage example.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from .base_renderer import BaseRenderer, Payload
from .html_renderer import render_document
from .html_renderer.dependencies import render_dependency_head


CATEGORY_SHAPES = [
    "rect",
    "roundrect",
    "ellipse",
    "diamond",
    "hexagon",
    "parallelogram",
    "double-rect",
]

CATEGORY_PALETTE = [
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#6A3D9A",
    "#E69F00",
    "#56B4E9",
    "#8C564B",
    "#17BECF",
    "#BCBD22",
    "#E377C2",
    "#4D4D4D",
    "#1B9E77",
    "#E7298A",
]

EDGE_PALETTE = [
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#6A3D9A",
    "#111111",
]


def _script_json(value: object) -> str:
    """Serialize data for an inline script without allowing script termination."""
    return json.dumps(value, indent=2).replace("</", "<\\/")


def _build_html_with_elk(doc: dict, reduction_note: str = "") -> str:
    """Build HTML from an already normalized and validated graph payload.

    Responsibilities of this renderer:
    - validate and serialize the canonical JSON graph
    - hand node/edge structure to ``elkjs`` for layered layout
    - render the interactive viewer shell
    - preserve viewer state such as hidden categories, hidden nodes, selected
      node, ancestor-focus mode, and panel collapse state in ``localStorage``

    Non-responsibilities:
    - inferring entities or dependencies from TeX
    - repairing malformed math strings
    - upgrading heuristic semantic content in the JSON
    """
    entities = doc["entities"]
    entity_map = {entity["id"]: entity for entity in entities}
    edges = []
    for node in entities:
        source_id = node["id"]
        for dep in node.get("connects_to", []):
            dep_payload = dict(dep)
            provenance = dep_payload.pop("source", None)
            edges.append(
                {
                    "edge_id": f"edge_{len(edges) + 1}",
                    "source": source_id,
                    "target": dep_payload["to"],
                    "annotation_source": provenance,
                    **dep_payload,
                }
            )

    # Re-label edges now that we no longer rely on `source` for provenance.
    for edge in edges:
        if "annotation_source" in edge and edge["annotation_source"] is None:
            edge["annotation_source"] = "explicit"

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
        for dep in node.get("connects_to", []):
            parent_id = dep["to"]
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

    edge_payload = list(edges)
    graph_build_id = str(int(time.time() * 1000))
    doc_title = doc.get("document", {}).get("title", "")
    page_title = f"{doc_title} — Dependency graph" if doc_title else "Dependency graph"

    return render_document(
        page_title=html.escape(page_title),
        graph_build_id=str(graph_build_id),
        renderer_dependency_head=render_dependency_head(doc),
        reduction_note=(
            f'<div class="small" style="margin-top:0.35rem;">'
            f'{html.escape(reduction_note)}</div>'
            if reduction_note
            else ""
        ),
        graph_document=_script_json(doc),
        category_shapes=_script_json(CATEGORY_SHAPES),
        category_palette=_script_json(CATEGORY_PALETTE),
        edge_palette=_script_json(EDGE_PALETTE),
        edge_data=_script_json(edge_payload),
        render_type_overrides=_script_json(render_type_overrides),
    )


def build_html_with_elk(doc: dict, reduction_note: str = "") -> str:
    """Validate and render one graph payload as a standalone ELK document."""
    renderer = BaseRenderer()
    prepared = renderer.normalize(doc)
    renderer.validate(prepared)
    return _build_html_with_elk(prepared, reduction_note=reduction_note)


class ElkHtmlRenderer(BaseRenderer):
    """Render canonical graph payloads as standalone ELK-backed HTML documents."""

    def _render_graph(self, graph_json: Payload, reduction_note: str = "") -> str:
        """Render one normalized payload as an interactive ELK HTML document."""
        return _build_html_with_elk(graph_json, reduction_note=reduction_note)


__all__ = [
    "CATEGORY_PALETTE",
    "CATEGORY_SHAPES",
    "EDGE_PALETTE",
    "ElkHtmlRenderer",
    "build_html_with_elk",
]
