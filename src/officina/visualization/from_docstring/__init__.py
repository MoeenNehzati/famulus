"""Docstring-to-graph extraction and visualization.

This package converts structured Python docstrings into renderable dependency graphs.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``__main__.py``
    Provides the docstring visualization command-line entry point.
``io.py``
    Loads Python modules and writes docstring visualization outputs.
``json_extractor.py``
    Converts parsed Python and docstring metadata into dependency JSON.
``parser.py``
    Parses Python modules and their structured graph annotations.
``payload_builder.py``
    Assembles validated renderer payloads for docstring graphs.
``renderer.py``
    Renders docstring dependency payloads through the shared HTML renderer.
``visualizer.py``
    Orchestrates module discovery, extraction, graph building, and rendering.
"""
