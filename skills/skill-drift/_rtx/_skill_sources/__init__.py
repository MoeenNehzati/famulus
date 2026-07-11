"""Aggregate installed-skill sources across supported assistant hosts."""
from __future__ import annotations

from . import _claude_skill_source, _codex_skill_source
from ._skill_source_common import SkillSource, current_skill_source, dedupe_skill_sources


def observed_skill_sources() -> list[SkillSource]:
    """Return all installed skill roots visible to this machine."""

    sources: list[SkillSource] = []
    sources.extend(_codex_skill_source.sources())
    sources.extend(_claude_skill_source.sources())
    current = current_skill_source()
    if current is not None:
        sources.append(current)
    return dedupe_skill_sources(sources)
