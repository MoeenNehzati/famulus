"""Shared dispatcher API."""

from __future__ import annotations

from typing import Any

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "dispatch",
    "resolve_direct_invocation",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]


def __getattr__(name: str) -> Any:
    """Load the public API lazily so data codecs do not import dispatch."""

    if name not in __all__:
        raise AttributeError(name)
    if name == "resolve_direct_invocation":
        from .direct_authorization import resolve_direct_invocation

        return resolve_direct_invocation
    from . import core

    return getattr(core, name)
