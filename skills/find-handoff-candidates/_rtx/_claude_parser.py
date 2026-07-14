"""Session transcript parser for Claude Code.

Transcript layout: ~/.claude/projects/<escaped-cwd>/<session_id>.jsonl
(overridable via the CLAUDE_HOME env var, which points at ~/.claude by
default -- matching the convention used elsewhere in this repo, e.g.
skills/install-assistant-tools). One JSON object per line; each line
carries a top-level "cwd" field and a top-level "timestamp" field. The
session id is not stored in the JSON -- it's the filename stem.

Exempt from validators/platform_neutral.py because this filename itself
names the host (see references/skill-standards/skill-guidelines.md, guideline 13).
"""
from __future__ import annotations

import glob
import os


class ClaudeParser:
    id = "claude"
    default_threshold = 100_000  # calibrated from real transcripts; see scan.py docstring
    opaque_field = "signature"  # Claude Code's opaque thinking-block crypto blob

    def home_dir(self) -> str:
        return os.environ.get("CLAUDE_HOME", os.path.expanduser("~/.claude"))

    def list_session_files(self) -> list[str]:
        return glob.glob(os.path.join(self.home_dir(), "projects", "*", "*.jsonl"))

    def extract_project(self, obj: dict) -> str | None:
        return obj.get("cwd")

    def extract_session_id(self, path: str, first_obj: dict | None) -> str:
        return os.path.splitext(os.path.basename(path))[0]

    def resume_hint(self, session_id: str) -> str:
        return f"/resume {session_id}"
