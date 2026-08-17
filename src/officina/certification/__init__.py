"""Certification hashing, records, and currentness views.

This package owns deterministic certificate construction and inspection. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/hashing.yaml``
    Declares the hashing behavioral source contract.
``blueprints/records.yaml``
    Declares the records behavioral source contract.
``blueprints/view.yaml``
    Declares the view behavioral source contract.
``hashing.py``
    Computes canonical node hashes and certification-basis closures.
``records.py``
    Parses, normalizes, and writes certificate-history records.
``view.py``
    Evaluates certificate currentness, exports, and authorization views.
"""
