#!/usr/bin/env python3
"""Shared launcher for assistant/collab/coauthor. Not intended to be called directly.

On Unix, replaces the current process with exec so signal handling is clean.
On Windows, runs the command as a subprocess and forwards the exit code.

Resolves its own repo root via Path(__file__).resolve() rather than relying
on $AI: this script is only ever reached through a symlink at
<repo>/skills/install-assistant-tools/bin/_agent_launch.py, so resolving the
symlink always finds the real repo root regardless of plugin vs dev mode.
$AI (when set — a dev-mode convenience, see dev_link.py) overrides this.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    # This file lives at <repo>/skills/install-assistant-tools/bin/_agent_launch.py
    return Path(__file__).resolve().parents[3]


def _parse_agent_md(repo_root: Path, agent: str) -> tuple[str, str]:
    """Return (description, prompt) parsed from agents/<agent>.md.

    Frontmatter is a small fixed set of `key: value` lines between `---`
    markers (see agents/*.md) — a full YAML parser isn't needed for this.
    """
    agent_md = repo_root / "agents" / f"{agent}.md"
    text = agent_md.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        # No frontmatter: treat the whole file as the prompt body.
        return "", text.strip()
    frontmatter, body = parts[1], parts[2]
    description = ""
    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
            break
    return description, body.strip()


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

    # $AI (set by dev_link.py) overrides; otherwise resolve from this
    # script's own symlinked location, which works regardless of mode.
    ai_root = os.environ.get("AI") or str(_repo_root())

    if not use_local:
        os.chdir(Path(ai_root) / "workers" / agent)

    claude_home = os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude"))

    if backend == "claude":
        description, prompt = _parse_agent_md(Path(ai_root), agent)
        agents_json = json.dumps({agent: {"description": description, "prompt": prompt}})
        cmd = [
            "claude", "--agent", agent,
            "--agents", agents_json,
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
