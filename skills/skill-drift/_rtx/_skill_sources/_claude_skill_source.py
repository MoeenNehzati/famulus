"""Claude installed-skill source discovery."""
from __future__ import annotations

import os
from pathlib import Path

from ._skill_source_common import SkillSource, host_skill_sources


def sources() -> list[SkillSource]:
    """Return installed skills roots visible from the Claude home."""

    return host_skill_sources("claude", Path(os.environ.get("CLAUDE_HOME", "~/.claude")).expanduser())
