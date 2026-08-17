"""Graph extraction, payload processing, rendering, and serving.

This package owns the graph model and concrete blueprint and docstring visualization pipelines. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``artifacts.py``
    Defines and writes the HTML and JSON artifacts produced by visualization runs.
``base_extractor.py``
    Defines the common graph-source extraction contract.
``base_renderer.py``
    Coordinates graph payload reduction, HTML rendering, and output writing.
``base_renderer_cli.py``
    Implements the shared renderer command-line interface.
``base_visualizer.py``
    Resolves graph sources and connects extractors to renderers.
``elk_html_renderer.py``
    Converts graph payloads into the self-contained ELK HTML viewer.
``from_blueprint/``
    Extracts and presents dependency graphs from Officina blueprints.
``from_docstring/``
    Extracts and presents dependency graphs from structured docstrings.
``graph.py``
    Defines graph nodes, edges, validation, filtering, and serialization.
``graph_specification.schema.json``
    Defines the JSON graph payload accepted by renderers.
``html_renderer/``
    Owns the HTML shell, browser runtime, styles, and vendored renderer assets.
``payload.py``
    Validates and normalizes renderer payload mappings.
``server.py``
    Serves generated visualization artifacts from a bounded local HTTP server.
"""
