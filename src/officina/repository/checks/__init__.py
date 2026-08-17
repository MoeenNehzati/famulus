"""Repository test and conformance-check execution.

This package owns local suite selection, validator snapshots, test discovery, and remote check helpers.

Includes
--------
``__init__.py``
    Documents this package and its owned files.
``discovery.py``
    Discovers canonical repository test roots and classifies test modules.
``remote.py``
    Submits and monitors remote repository-check jobs.
``remote_macos_windows.py``
    Defines the macOS and Windows remote check matrices.
``runner.py``
    Selects suites and coordinates local or parallel repository checks.
"""
