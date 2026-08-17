"""Docstring parsing, policy, and validation.

This package owns the structured docstring format and its validators. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``config.yaml``
    Configures the active structured-docstring schema and policy inputs.
``parser.py``
    Parses structured module, callable, graph, ownership, and pipeline sections.
``policy.py``
    Loads the configured docstring rules used by repository validation.
``validation.py``
    Applies semantic checks to parsed structured docstrings.
"""
