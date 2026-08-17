"""Pinned-standard extraction and querying.

This package validates standard import closures and exposes deterministic queries over them. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/extractor.yaml``
    Declares the extractor behavioral source contract.
``blueprints/query.yaml``
    Declares the query behavioral source contract.
``extractor.py``
    Resolves a standard and its pinned import closure into one validated view.
``query.py``
    Answers deterministic task and requirements queries over extracted standards.
"""
