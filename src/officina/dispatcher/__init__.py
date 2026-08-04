"""Shared dispatcher API."""

from __future__ import annotations

from typing import Any

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "dispatch",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]


def __getattr__(name: str) -> Any:
    """Load the public API lazily so data codecs do not import dispatch."""

    if name not in __all__:
        raise AttributeError(name)
    from . import core

    return getattr(core, name)
