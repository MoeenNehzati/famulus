"""Blueprint-to-graph extraction and visualization.

This package converts repository blueprint graphs into renderable graph payloads.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``__main__.py``
    Provides the blueprint visualization command-line entry point.
``catalog.py``
    Builds presentation catalogs for modules, sources, interfaces, and files.
``details.py``
    Formats detailed inspector content for blueprint graph entities.
``extractor.py``
    Extracts graph nodes and edges from the repository blueprint graph.
``payload_builder.py``
    Assembles validated renderer payloads from extracted blueprint data.
``presentation_nodes.py``
    Adds presentation-only grouping and explanatory nodes.
``scope.py``
    Selects the requested blueprint subgraph and traversal boundary.
``visualizer.py``
    Orchestrates blueprint extraction, payload construction, and rendering.
"""
