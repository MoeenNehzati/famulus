"""Shared dispatcher API."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "dispatch",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]


def __getattr__(name: str):
    """Load the compatibility API only when a caller requests one symbol."""

    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module(".core", __name__), name)
    globals()[name] = value
    return value
