"""Git provenance and isolated repository snapshots.

This package owns Git inspection and materialization behavior. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/provenance.yaml``
    Declares the provenance behavioral source contract.
``provenance.py``
    Captures commit readiness, provenance, and isolated Git snapshots.
"""
