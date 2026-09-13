"""Shared dispatcher API."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "InvocationError",
    "ResolvedInvocation",
    "ResolvedInvocationMetadata",
    "authorize_direct_invocation",
    "authorize_host_caller",
    "dispatch",
    "load_direct_setup_projection",
    "materialize_authorized_invocation",
    "resolve_direct_invocation",
    "resolve_dispatch",
    "resolve_dispatch_metadata",
]


def __getattr__(name: str):
    """Load the compatibility API only when a caller requests one symbol."""

    if name not in __all__:
        raise AttributeError(name)
    if name in {"authorize_direct_invocation", "resolve_direct_invocation"}:
        module = import_module(".direct_authorization", __name__)
        value = getattr(module, name)
    elif name == "load_direct_setup_projection":
        from officina.blueprints.direct_setup import load_direct_setup_projection

        value = load_direct_setup_projection
    else:
        value = getattr(import_module(".direct_runtime", __name__), name)
    globals()[name] = value
    return value
