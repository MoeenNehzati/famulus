"""Credential files, OAuth JSON, and secret storage.

This package owns host-neutral credential persistence and lookup. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/google.yaml``
    Declares the google behavioral source contract.
``blueprints/oauth.yaml``
    Declares the oauth behavioral source contract.
``blueprints/secret-store.yaml``
    Declares the secret store behavioral source contract.
``google.py``
    Resolves and validates Google credential-file bindings.
``oauth.py``
    Reads and writes OAuth client and token JSON documents safely.
``secret_store.py``
    Stores and retrieves namespaced secrets through the supported backend.
"""
