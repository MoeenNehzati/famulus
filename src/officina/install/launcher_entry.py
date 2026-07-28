"""Thin, ordinarily-importable wrapper around the dependency-free resolver
script at ``resolvers/launch.py``.

Not used in production. The file actually deployed to
``<runtime_root>/bootstrap/resolvers/v1/launch.py`` is
``resolvers/launch.py``'s own source, copied and executed standalone under
the user's ambient Python -- it must never import ``officina`` (see that
file's docstring for why: an import of ``officina`` at that point would run
under the user's ambient interpreter, before control ever transfers to the
managed release's interpreter, which is exactly the ambient-python
invocation this program forbids elsewhere).

This wrapper exists solely so ordinary Python code (chiefly
``tests/test_officina_launcher_entry.py``'s cross-check against
``officina.install.runtime_pointer``) can reach the resolver's internals via
a normal import instead of repeating
``importlib.util.spec_from_file_location`` loading boilerplate in every
caller. Loading the resolver this way, rather than a plain ``import``, keeps
production code (and this package's own ``__init__.py``) from ever having a
reason to import ``resolvers.launch`` as a package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_RESOLVER_SOURCE = Path(__file__).resolve().parent / "resolvers" / "launch.py"


def _load_resolver_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_famulus_launch_resolver", _RESOLVER_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load resolver module from {_RESOLVER_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_resolver = _load_resolver_module()

main = _resolver.main
ResolverError = _resolver.ResolverError
_require_contained_or_trusted = _resolver._require_contained_or_trusted
_load_current_pointer = _resolver._load_current_pointer
_trusted_interpreter_roots = _resolver._trusted_interpreter_roots

__all__ = ["main", "ResolverError"]
