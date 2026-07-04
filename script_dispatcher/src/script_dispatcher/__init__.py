"""Shared dispatcher API for skill script interfaces."""

from .core import InvocationError, ResolvedInvocation, dispatch, resolve_dispatch

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "dispatch",
    "resolve_dispatch",
]
