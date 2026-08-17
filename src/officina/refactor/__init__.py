"""Mechanical, manifest-driven repository refactoring.

Includes
--------
``__init__.py``
    Documents the refactoring package without re-exporting implementation APIs.
``relocation.py``
    Plans, validates, reports, and atomically publishes blueprint-aware relocations.
``closure.py``
    Materializes projected relocations in a shadow tree and reconciles narrow
    deterministic certification-basis and generated-artifact closure results.
``relocation.schema.json``
    Defines the strict YAML manifest structure accepted by the relocation engine.
"""
