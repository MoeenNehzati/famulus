#!/usr/bin/env python3
"""Shared launcher for assistant/collab/coauthor. Not intended to be called directly.

On Unix, replaces the current process with exec so signal handling is clean.
On Windows, runs the command as a subprocess and forwards the exit code.
"""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
from pathlib import Path


def launch(agent: str, default_backend: str, args: list[str]) -> None:
    """Launch the given agent with the given backend and extra args.

    Args:
        agent:           Agent name (e.g. 'assistant', 'collab').
        default_backend: 'claude' or 'codex', used when no --claude/--codex flag given.
        args:            Remaining command-line arguments from the caller.
    """
    def usage() -> None:
        print(f"""Usage: {agent} [-l|--local] [--claude|--codex] [-h|--help] [args...]

  -l, --local   Run in current directory instead of $AI/workers/{agent}
  --claude      Use Claude (claude --agent {agent})
  --codex       Use Codex (codex --profile {agent})
  -h, --help    Show this help

Default backend: {default_backend}
Working directory: $AI/workers/{agent} (skip with -l/--local).
Claude settings: $CLAUDE_HOME/{agent}_claude_setting.json""")

    backend = default_backend
    use_local = False

    # Parse flags (consume from front of args list)
    while args:
        flag = args[0]
        if flag in ("-l", "--local"):
            use_local = True
            args = args[1:]
        elif flag == "--claude":
            backend = "claude"
            args = args[1:]
        elif flag == "--codex":
            backend = "codex"
            args = args[1:]
        elif flag in ("-h", "--help"):
            usage()
            sys.exit(0)
        else:
            break   # first non-flag arg: stop consuming

    if not use_local:
        ai_root = os.environ.get("AI")
        if not ai_root:
            print(f"{agent}: AI is not set — re-run the installer", file=sys.stderr)
            sys.exit(1)
        os.chdir(Path(ai_root) / "workers" / agent)

    claude_home = os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude"))

    if backend == "claude":
        cmd = [
            "claude", "--agent", agent,
            "--settings", str(Path(claude_home) / f"{agent}_claude_setting.json"),
            *args,
        ]
    elif backend == "codex":
        cmd = ["codex", "--profile", agent, *args]
    else:
        print(f"{agent}: unknown backend '{backend}'", file=sys.stderr)
        sys.exit(1)

    # On Unix, exec replaces the current process for clean signal handling.
    # On Windows, subprocess + forwarded exit code is the safe equivalent.
    if sys.platform == "win32":
        # npm installs claude/codex as .cmd shims, which CreateProcess
        # cannot spawn from a bare name — resolve through PATH first.
        resolved = shutil.which(cmd[0])
        if resolved is None:
            print(f"{agent}: '{cmd[0]}' not found on PATH", file=sys.stderr)
            sys.exit(1)
        result = subprocess.run([resolved, *cmd[1:]])
        sys.exit(result.returncode)
    else:
        os.execvp(cmd[0], cmd)


def main() -> None:
    """Entry point when called directly: _agent_launch <agent> <backend> [args...]"""
    if len(sys.argv) < 3:
        print("_agent_launch: agent name and backend required", file=sys.stderr)
        sys.exit(1)
    launch(agent=sys.argv[1], default_backend=sys.argv[2], args=sys.argv[3:])


if __name__ == "__main__":
    main()
