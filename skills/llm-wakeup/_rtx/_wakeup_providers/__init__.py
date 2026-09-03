"""Registry for supported provider adapters."""

from __future__ import annotations

from .. import WakeupError
from ._provider_base import ProviderAdapter
from ._provider_claude import ClaudeAdapter
from ._provider_codex import CodexAdapter


_PROVIDERS: dict[str, ProviderAdapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
}


def provider_for(name: str) -> ProviderAdapter:
    """Return the named adapter or raise a user-facing configuration error."""

    try:
        return _PROVIDERS[name]
    except KeyError as error:
        raise WakeupError(f"unsupported provider: {name}") from error


def all_providers() -> tuple[ProviderAdapter, ...]:
    """Return adapters in stable command-line display order."""

    return tuple(_PROVIDERS.values())


def provider_names() -> tuple[str, ...]:
    """Return stable provider names for argument validation."""

    return tuple(_PROVIDERS)


__all__ = ["all_providers", "provider_for", "provider_names"]
