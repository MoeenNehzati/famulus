#!/usr/bin/env python3
"""Compatibility shim for plugin-mode and legacy registrations.

Preferred entrypoint: ``llmhooks/inject_dispatcher_context.py`` with explicit
``--codex`` / ``--claude`` / ``--cursor`` flags written by the dev-mode
installer.

Plugin mode still routes through this shim because Claude plugin installs and
Codex plugin installs share one ``hooks/hooks.json`` file. In that mode we use
host-provided plugin-root variables only to choose the host adapter:

- ``PLUGIN_ROOT`` present          -> Codex plugin mode
- ``CLAUDE_PLUGIN_ROOT`` present   -> Claude plugin mode
- else                             -> default to Claude-compatible shape
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmhooks.inject_dispatcher_context import main


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        if os.environ.get("PLUGIN_ROOT"):
            argv = ["--codex"]
        elif os.environ.get("CLAUDE_PLUGIN_ROOT"):
            argv = ["--claude"]
        else:
            argv = ["--claude"]
    raise SystemExit(main(argv))
