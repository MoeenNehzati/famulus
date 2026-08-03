"""Load source assets and inline them into a standalone graph document.

The source is split for maintainability, but rendering deliberately produces a
single HTML file. Generated graphs therefore do not depend on local CSS or
JavaScript files after they are written.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


_ASSET_DIRECTORY = Path(__file__).parent
_PLACEHOLDER_PREFIX = "@@OFFICINA_"
_RUNTIME_ASSETS = (
    "runtime/bootstrap.js",
    "runtime/sidebar_layout.js",
    "runtime/viewer_state.js",
    "runtime/core.js",
    "runtime/selection.js",
    "runtime/filtering.js",
    "runtime/graph_actions.js",
    "runtime/math_typesetter.js",
    "runtime/geometry.js",
    "runtime/legend.js",
    "runtime/visibility.js",
    "runtime/inspector.js",
    "runtime/projection.js",
    "runtime/edge_presentation.js",
    "runtime/layout.js",
    "runtime/node_renderer.js",
    "runtime/interactions.js",
    "runtime/render_pipeline.js",
    "runtime/controls.js",
)


@lru_cache(maxsize=None)
def _read_asset(name: str) -> str:
    """Read one immutable renderer asset once per Python process."""
    return (_ASSET_DIRECTORY / name).read_text(encoding="utf-8")


def render_document(**values: str) -> str:
    """Return a standalone HTML document populated with serialized graph data.

    ``values`` are already serialized or escaped by the public renderer. Keeping
    serialization at that boundary makes this assembler deliberately unaware of
    the graph schema and prevents accidental double encoding.
    """
    runtime = "\n".join(_read_asset(name) for name in _RUNTIME_ASSETS)
    document = _read_asset("page.html").replace(
        "@@OFFICINA_VIEWER_STYLES@@", _read_asset("viewer.css")
    ).replace(
        "@@OFFICINA_ELK_RUNTIME@@", _read_asset("vendor/elk.bundled.js")
    ).replace(
        "@@OFFICINA_ELK_WORKER_SOURCE@@",
        json.dumps(_read_asset("vendor/elk-worker.min.js")).replace("</", "<\\/"),
    ).replace("@@OFFICINA_VIEWER_RUNTIME@@", runtime)
    for name, value in values.items():
        document = document.replace(f"@@OFFICINA_{name.upper()}@@", value)
    if _PLACEHOLDER_PREFIX in document:
        unresolved = document.split(_PLACEHOLDER_PREFIX, 1)[1].split("@@", 1)[0]
        raise ValueError(f"unresolved HTML renderer placeholder: {unresolved}")
    return document
