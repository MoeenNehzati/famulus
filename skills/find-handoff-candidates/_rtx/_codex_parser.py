"""Session transcript parser for Codex.

Transcript layout: ~/.codex/sessions/YYYY/MM/DD/rollout-...-<session_id>.jsonl
(overridable via the CODEX_HOME env var, which points at ~/.codex by
default -- matching the convention used elsewhere in this repo, e.g.
skills/install-assistant-tools). One JSON object per line; the working
directory and session id live nested under a "payload" object (only on
certain event types, e.g. "session_meta"), not at the top level like the
sibling host's format. The YYYY/MM/DD directory reflects the session's
CREATION date, but the file keeps being appended to (mtime advances) as
work continues on later days -- callers that want "sessions touched on
date X" must filter by mtime across all date directories, not just look in
the one directory matching X (verified against 40+ real mismatched files
on this machine; see scan.py's own scan-loop comment).

Exempt from validators/platform_neutral.py because this filename itself
names the host (see references/skill-standards/skill-guidelines.md, guideline 13).
"""
from __future__ import annotations

import glob
import os


class CodexParser:
    id = "codex"
    default_threshold = 1_000_000  # calibrated from real transcripts; see scan.py docstring
    opaque_field = "encrypted_content"  # Codex's opaque reasoning-item crypto blob

    def home_dir(self) -> str:
        return os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))

    def list_session_files(self) -> list[str]:
        return glob.glob(os.path.join(self.home_dir(), "sessions", "*", "*", "*", "*.jsonl"))

    def extract_project(self, obj: dict) -> str | None:
        return (obj.get("payload") or {}).get("cwd")

    def extract_session_id(self, path: str, first_obj: dict | None) -> str:
        payload = (first_obj or {}).get("payload") or {}
        return (
            payload.get("session_id")
            or payload.get("id")
            or os.path.splitext(os.path.basename(path))[0]
        )

    def resume_hint(self, session_id: str) -> str:
        return f"resume {session_id}"
