#!/usr/bin/env python3
"""
ensure_agent_env.py — Idempotently ensure recurring-tasks' own prerequisites.

Writes the systemd AI_AGENT_COMMAND_TEMPLATE environment file. Moved here from
install-assistant-tools: this state is consumed by recurring-tasks job
execution, not by the general skill ecosystem, so recurring-tasks owns ensuring
it rather than install-assistant-tools doing it upfront for every install
regardless of whether the user wants automation.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

if __package__:
    from ._schedule_backend._linux_backend import _session_env
else:  # invoked directly by path, as the systemd units and dispatcher do
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _schedule_backend._linux_backend import _session_env  # noqa: E402


def log(msg: str = "") -> None:
    print(msg, flush=True)


def install_ai_agent_env(home: Path, dry_run: bool) -> None:
    """Write the systemd user environment file for AI_AGENT_COMMAND_TEMPLATE.

    Safety: `systemctl --user set-environment` mutates the caller's *live*
    systemd user session, not anything scoped to `home`. Only touch the live
    session when `home` resolves to this user's real $HOME, so test/sandboxed
    installs with an overridden --home never clobber the real session.
    """
    if sys.platform == "win32":
        log("Note: skipping systemd environment setup (not supported on Windows).")
        return

    env_dir = home / ".config" / "environment.d"
    env_file = env_dir / "20-ai-agent.conf"
    content = "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n"

    if dry_run:
        log(f"Would write {env_file}")
        return

    env_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text(content)

    is_real_home = home.expanduser().resolve() == Path.home().resolve()
    if not is_real_home:
        log(
            f"Note: --home {home} is not this user's real $HOME "
            f"({Path.home()}) — skipping live systemd session update."
        )
        return

    if shutil.which("systemctl"):
        # Use the same derived session environment as the reader half of this
        # variable (_linux_backend._session_env). Without it, running setup
        # from cron or a non-login ssh session makes is-active fail, silently
        # skips set-environment, and leaves the manager without the very
        # variable the health check then reports as "not set".
        session_env = _session_env()
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "default.target"],
            capture_output=True,
            env=session_env,
        )
        if result.returncode != 0:
            log(
                "Note: systemd user manager not reachable "
                f"({result.stderr.decode('utf-8', 'replace').strip() or 'no detail'}) "
                "— AI_AGENT_COMMAND_TEMPLATE not set in the live session."
            )
            return
        applied = subprocess.run(
            ["systemctl", "--user", "set-environment",
             "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}"],
            capture_output=True,
            env=session_env,
        )
        if applied.returncode != 0:
            log(
                "Warning: could not set AI_AGENT_COMMAND_TEMPLATE: "
                f"{applied.stderr.decode('utf-8', 'replace').strip() or 'no detail'}"
            )


def run(*, repo_root: Path, home: Path, bin_dir: Path, dry_run: bool = False) -> None:
    install_ai_agent_env(home, dry_run)


class Interface(PythonArgvMachineInterface):
    prog = "ensure_agent_env.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", metavar="DIR", required=True)
    parser.add_argument("--home", metavar="DIR", required=True)
    parser.add_argument("--bin-dir", metavar="DIR", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run(
        repo_root=Path(args.repo_root),
        home=Path(args.home),
        bin_dir=Path(args.bin_dir),
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
