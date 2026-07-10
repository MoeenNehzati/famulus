"""Creates a CLAUDE.md -> AGENTS.md compatibility symlink in a scaffolded
project, so a host that looks for CLAUDE.md specifically finds the same
content other hosts read via AGENTS.md.

Exempt from validators/platform_neutral.py because this filename itself
names the host (see references/skill-guidelines.md, guideline 13).
"""
from __future__ import annotations

import os


def create_alias(project_dir: str) -> None:
    target = os.path.join(project_dir, "CLAUDE.md")
    if os.path.lexists(target):
        os.remove(target)
    os.symlink("AGENTS.md", target)
