"""Decide whether a session stopped because its usage limit stopped it.

A near-limit percentage says a session *may* soon be refused. It does not say
the session was refused, and it does not say the session stopped. Waking a
conversation that simply ended at 91% hands an idle agent no task, which it
then invents one to fill.

This module answers the narrower question the wakeup policy actually needs:
did the provider refuse a turn for lack of quota, and is that refusal still
the last thing that happened? Both halves come from bytes the provider already
wrote locally; no API call and no LLM is involved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .claude_codex_sessions import transcript_tail_lines
from .providers import provider_for


@dataclass(frozen=True)
class Cutoff:
    """One session's refusal, and what happened in the transcript after it.

    Distinct from the ``Cutoff`` in ``providers.base``, which is the single
    provider record without the session or the aftermath.
    """

    provider: str
    session_id: str
    transcript_path: Path
    reset_at: datetime | None
    observed_at: datetime
    abandoned: bool
    self_continuing: bool

    @property
    def wakeable(self) -> bool:
        """Return whether an external wakeup is both needed and possible."""

        return (
            self.abandoned and not self.self_continuing and self.reset_at is not None
        )


def detect_cutoff(
    provider: str, transcript_path: Path, session_id: str
) -> Cutoff | None:
    """Return the newest quota refusal in one transcript, or ``None``.

    Only the transcript tail is read, so a refusal that has scrolled out of it
    is reported as no refusal at all; a queued job then lapses at delivery as
    ``no-cutoff-evidence`` rather than resuming a session on stale grounds.

    ``abandoned`` compares positions rather than timestamps. Claude records its
    refusal as an ``assistant`` row, so the refusal is itself a "meaningful"
    event and would otherwise appear to be progress past itself. Elapsed time
    is likewise not a discriminator: a session that retries immediately and is
    refused again has a one-second gap, while a genuine resume after a reset
    has a multi-hour one.
    """

    adapter = provider_for(provider)
    cutoff_index: int | None = None
    cutoff_event = None
    meaningful_index: int | None = None
    continuation_index: int | None = None
    continuation: bool | None = None

    for index, line in enumerate(transcript_tail_lines(transcript_path)):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        found = adapter.cutoff(event)
        if found is not None:
            cutoff_index, cutoff_event = index, found
        if adapter.meaningful(event):
            meaningful_index = index
        stance = adapter.self_continuation(event)
        if stance is not None:
            continuation_index, continuation = index, stance

    if cutoff_event is None or cutoff_index is None:
        return None
    return Cutoff(
        provider=provider,
        session_id=session_id,
        transcript_path=transcript_path,
        reset_at=cutoff_event.reset_at,
        observed_at=cutoff_event.observed_at,
        abandoned=meaningful_index is None or meaningful_index <= cutoff_index,
        self_continuing=bool(continuation)
        and continuation_index is not None
        and continuation_index > cutoff_index,
    )


__all__ = ["Cutoff", "detect_cutoff"]
