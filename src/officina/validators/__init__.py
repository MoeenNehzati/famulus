"""Validation helpers for repository code-quality checks.

This package owns docstring validation and exact staged-repository snapshots. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``docstring_validator.py``
    Validates structured docstrings and package file inventories.
``snapshot.py``
    Runs validators against an exact staged-tree snapshot.
"""
