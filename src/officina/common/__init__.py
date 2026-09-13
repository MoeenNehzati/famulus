"""Cross-cutting Officina primitives.

This package contains small shared utilities that do not constitute an independent subsystem. Callers import concrete owning modules.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``atomic_files.py``
    Performs confined regular-file reads and atomic create/replace writes.
``blueprint.yaml``
    Declares this package's registered Officina module boundary.
``blueprints/atomic-files.yaml``
    Declares the atomic files behavioral source contract.
``blueprints/codex-toml.yaml``
    Declares the codex toml behavioral source contract.
``blueprints/dates.yaml``
    Declares the dates behavioral source contract.
``blueprints/famulus-paths.yaml``
    Declares the famulus paths behavioral source contract.
``blueprints/famulus-paths-get.yaml``
    Declares the finite executable path-selection contract.
``blueprints/repository-paths.yaml``
    Declares the repository paths behavioral source contract.
``blueprints/toml-io.yaml``
    Declares the toml io behavioral source contract.
``codex_toml.py``
    Reads and updates Codex configuration without discarding unrelated TOML content.
``command_files.py``
    Installs caller-rendered command files and static command helpers across hosts.
``dates.py``
    Normalizes repository date values and date-oriented filenames.
``famulus_paths/``
    Resolves installed Famulus and repository paths across supported hosts.
``famulus_paths/_get_interface.py``
    Prints one declared plugin path for Dispatcher callers.
``python_source_cache.py``
    Caches parsed Python source while preserving path and content identity.
``repository_paths.py``
    Resolves and normalizes paths within a repository boundary.
``toml_io.py``
    Provides the shared TOML parsing and serialization primitives.
"""
