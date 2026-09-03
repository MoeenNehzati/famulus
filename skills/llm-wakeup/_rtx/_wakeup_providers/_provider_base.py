"""Provider adapter contracts shared by transcript and delivery orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RateLimit:
    """One structured exhausted-limit observation from a provider transcript."""

    reset_at: datetime
    observed_at: datetime
    context: str


@dataclass(frozen=True)
class Cutoff:
    """One provider record proving a turn was refused for lack of quota.

    Distinct from the session-level ``Cutoff`` the detector builds, which pairs
    a refusal with the session it belongs to and with what followed it.

    ``reset_at`` is ``None`` when the provider stated no recoverable reset
    time. That is a real case rather than a parse failure to hide: one provider
    records the reset only as English prose, and in a local survey 20 of that
    provider's 38 refusals carried no numeric window anywhere in the transcript.
    """

    reset_at: datetime | None
    observed_at: datetime
    context: str


@dataclass(frozen=True)
class DetectedRateLimit:
    """A rate limit associated with an exact persisted session."""

    provider: str
    session_id: str
    reset_at: datetime
    context: str


class ProviderAdapter(Protocol):
    """Provider-specific filesystem, transcript, and resume behavior."""

    name: str

    def transcript_root(self) -> Path:
        """Return the provider's local transcript root."""
        ...

    def include_log(self, path: Path) -> bool:
        """Return whether a discovered file is a provider session log."""
        ...

    def session_id(self, event: dict) -> str | None:
        """Extract a canonical session identifier from one transcript event."""
        ...

    def alias(self, event: dict) -> str | None:
        """Extract a user-facing session alias when the provider records one."""
        ...

    def indexed_sessions(self, alias: str) -> list[str]:
        """Return canonical session IDs associated with an external alias."""
        ...

    def rate_limit(self, event: dict) -> RateLimit | None:
        """Normalize a provider quota/reset event, or return ``None``."""
        ...

    def cutoff(self, event: dict) -> Cutoff | None:
        """Identify an event proving this turn was refused for lack of quota.

        Distinct from :meth:`rate_limit`, which reports that a limit exists.
        This reports that one was enforced against a specific turn.
        """
        ...

    def self_continuation(self, event: dict) -> bool | None:
        """Report whether the provider armed (True) or abandoned (False) its
        own automatic resume, or ``None`` when the event says nothing."""
        ...

    def meaningful(self, event: dict) -> bool:
        """Return whether an event proves user-visible session progress."""
        ...

    def cwd(self, event: dict) -> Path | None:
        """Extract the session working directory when available."""
        ...

    def executable_override(self) -> str | None:
        """Return a provider-specific executable override from the environment."""
        ...

    def executable_candidates(self) -> tuple[Path, ...]:
        """Return known executable locations used after PATH discovery fails."""
        ...
    def resume_command(
        self, executable: str, session_id: str, message: str
    ) -> list[str]:
        """Compile unattended argv that resumes the exact existing session."""
        ...


def string_leaves(value: object) -> list[str]:
    """Return nested string values from one bounded transcript event."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in string_leaves(child)]
    if isinstance(value, list):
        return [item for child in value for item in string_leaves(child)]
    return []
