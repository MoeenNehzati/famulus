"""Codex installed-skill source discovery."""
from __future__ import annotations

import os
from pathlib import Path

from ._skill_source_common import SkillSource, host_skill_sources


def sources() -> list[SkillSource]:
    """Return installed skills roots visible from the Codex home."""

    return host_skill_sources("codex", Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
