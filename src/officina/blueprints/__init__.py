"""Blueprint discovery, validation, authorization, and projection.

This package owns the repository-blueprint model and its mechanical consumers. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``authorization.py``
    Resolves whether a module caller may use a declared blueprint export.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/graph.yaml``
    Declares the graph behavioral source contract.
``blueprints/inventory.yaml``
    Declares the inventory behavioral source contract.
``blueprints/pooled.yaml``
    Declares the pooled behavioral source contract.
``blueprints/process-binding.yaml``
    Declares the process binding behavioral source contract.
``blueprints/template.yaml``
    Declares the template behavioral source contract.
``graph.py``
    Loads registered blueprints into the canonical dependency graph.
``inventory.py``
    Discovers blueprint documents and their owning repository paths.
``pooled.py``
    Combines related blueprint records into deterministic pooled views.
``process_binding.py``
    Compiles declared process bindings into executable argument vectors.
``projection.py``
    Projects blueprint interfaces and authorization into consumer-facing records.
``search.py``
    Searches blueprint metadata and renders stable machine-readable results.
``template.py``
    Validates and expands blueprint template declarations.
"""
