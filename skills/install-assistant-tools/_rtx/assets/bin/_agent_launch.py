#!/usr/bin/env python3
"Shared launcher for assistant/collab/coauthor. Not intended to be called directly.\n\nOn Unix, replaces the current process with exec so signal handling is clean.\nOn Windows, runs the command as a subprocess and forwards the exit code.\n\nResolves its own repo root via Path(__file__).resolve() rather than relying\non $AI: this script is only ever reached through a symlink at\n<repo>/skills/install-assistant-tools/_rtx/assets/bin/_agent_launch.py, so resolving the\nsymlink always finds the real repo root regardless of plugin vs dev mode.\n$AI (when set — a dev-mode convenience, see dev_link.py) overrides this.\n"

from __future__ import annotations

import json
import os
import shutil
import sys
import subprocess
from pathlib import Path


def _repo_root() -> Path | None:
    # This file lives at <repo>/skills/install-assistant-tools/bin/_agent_launch.py
    #
    # Only true when the launcher is reached through its symlink. Installs that
    # copy it instead -- Windows always, plugin mode where symlinks are
    # unavailable -- leave it sitting in a bin directory with no repo above it,
    # and walking up five levels then runs off the top of the filesystem.
    # Returns None there rather than raising: a caller that was told where the
    # files are (see _resource_dir) does not need this at all, and one that
    # wasn't should fail describing what is missing, not with IndexError.
    try:
        return Path(__file__).resolve().parents[5]
    except IndexError:
        return None


def _repo_dir(name: str, root: Path | None) -> Path:
    """Resolve one directory of launch inputs beneath the repository root.

    agents/, profiles/, and llmhooks/ ship together in one tree, so the only
    thing worth passing between processes is that tree. A caller that already
    knows it -- the scheduler does, since it resolves it from its own location
    -- exports $FAMULUS_REPO_ROOT, which is what keeps this working across
    install models: a dev checkout, a plugin cache, and a relocated repo differ
    in where the tree sits but not in the caller's ability to name it. Baking
    the answer in at install time freezes it; probing the filesystem guesses.
    """
    if root is None:
        raise SystemExit(
            f"cannot locate {name}/: this launcher was installed as a copy, so it "
            "cannot find the repository from its own path. Set $FAMULUS_REPO_ROOT "
            f"(or $AI) to the directory containing {name}/."
        )
    return root / name


def _worker_dir(agent: str) -> Path:
    """Resolve the working directory to cd into before launching `agent`.

    Dev mode ($AI set by dev_link.py): the user pointed this at a live repo
    checkout they own, so $AI/workers/{agent} is correct there.

    Otherwise (plugin mode, $AI unset): this script is running from a
    public/immutable plugin-cache checkout, so writing runtime session data
    under it is wrong (the same bug the installer's own worker-dir bootstrap
    had — see _agent_launchers.install_worker_dir). Use the platform Famulus
    state dir instead, matching what a plugin-mode install actually creates.
    """
    ai_root = os.environ.get("AI")
    if ai_root:
        return Path(ai_root) / "workers" / agent

    # A copied launcher cannot see the repo from its own path, so there may be
    # no src/ to add; famulus_paths is importable on its own in that case.
    repo_root = _repo_root() or (
        Path(p) if (p := os.environ.get("FAMULUS_REPO_ROOT")) else None
    )
    if repo_root is not None:
        repo_src = repo_root / "src"
        if str(repo_src) not in sys.path:
            sys.path.insert(0, str(repo_src))
    from officina.common.famulus_paths import resolve_famulus_paths

    return resolve_famulus_paths(platform=sys.platform, home=Path.home()).worker_root / agent


def _agent_md_path(repo_root: Path | None, agent: str) -> Path:
    """Resolve the instructions file for `agent`."""
    return _repo_dir("agents", repo_root) / f"{agent}.md"


def _claude_settings_path(repo_root: Path | None, agent: str, claude_home: Path) -> Path:
    """Resolve the Claude settings file for `agent`.

    Prefers the shipped profile, so a run uses the settings that travel with
    the package rather than whatever a particular machine's Claude home holds.
    Falls back to the installed copy, which is what a machine-local override
    looks like.
    """
    if repo_root is not None:
        shipped = repo_root / "profiles" / f"{agent}_claude_setting.json"
        if shipped.is_file():
            return shipped
    return claude_home / f"{agent}_claude_setting.json"


def _parse_agent_md(repo_root: Path, agent: str) -> tuple[str, str]:
    """Return (description, prompt) parsed from agents/<agent>.md.

    Frontmatter is a small fixed set of `key: value` lines between `---`
    markers (see agents/*.md) — a full YAML parser isn't needed for this.
    """
    agent_md = _agent_md_path(repo_root, agent)
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

    # A caller that knows where the tree is wins: the scheduler exports
    # $FAMULUS_REPO_ROOT, which is the only resolution that survives a copied
    # launcher. $AI is dev_link.py's convenience. Falling back to this script's
    # own location works only when it was reached through its symlink, so it
    # can legitimately come back empty.
    # $FAMULUS_REPO_ROOT first: it is set deliberately, for this launch, by a
    # caller that knows. $AI is a long-lived shell convenience that can easily
    # be stale or point at a different checkout, so it must not silently
    # override a caller that was explicit.
    ai_root = (
        os.environ.get("FAMULUS_REPO_ROOT")
        or os.environ.get("AI")
        or _repo_root()
    )
    ai_root = Path(ai_root) if ai_root else None

    if not use_local:
        os.chdir(_worker_dir(agent))

    claude_home = os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude"))

    if backend == "claude":
        description, prompt = _parse_agent_md(ai_root, agent)
        agents_json = json.dumps({agent: {"description": description, "prompt": prompt}})
        cmd = [
            "claude", "--agent", agent,
            "--agents", agents_json,
            "--settings", str(_claude_settings_path(ai_root, agent, Path(claude_home))),
            *args,
        ]
    elif backend == "codex":
        # Supply the instructions path now rather than trusting whatever the
        # installed profile config recorded. Codex resolves a relative
        # model_instructions_file under $CODEX_HOME and fails hard when the
        # file is missing, so a path stored at install time is a cache with no
        # invalidation -- when the repo moves, every launch dies seconds in.
        # `-c` overrides the profile's own value, and the claude branch above
        # already resolves its agent definition this same way.
        agent_md = _agent_md_path(ai_root, agent)
        cmd = [
            "codex",
            "-c", f"model_instructions_file={agent_md}",
            "--profile", agent,
            *args,
        ]
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
