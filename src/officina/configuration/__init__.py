"""Configuration loading and repository-boundary validation.

This package owns configuration-aware schemas and explicit repository configuration loading. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/repository.yaml``
    Declares the repository behavioral source contract.
``configured_schema.py``
    Builds and validates schemas whose constraints come from repository configuration.
``repository.py``
    Loads and validates the repository's Officina configuration.
``schema.json``
    Defines the accepted repository-configuration document shape.
"""
