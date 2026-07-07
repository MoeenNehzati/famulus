#!/usr/bin/env python3
"""
install.py — Full installation of assistant tools on a new machine.

Runs both setup steps in order:
  1. dev_link    — wire Claude and Codex config dirs to the repo
  2. setup_tools — install bin scripts, rc block, git hooks

NOTE: this is a stopgap wiring fix only (repo_root is still auto-derived
from this script's own location, same as the old behavior) — the full
Phase-1 orchestrator rewrite (explicit dev-mode question, scaffold/
dev-link/launchers split) is a separate, later change.

Run individual scripts directly for targeted repairs:
  python3 scripts/dev_link.py --help
  python3 scripts/setup_tools.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the scripts directory importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

import dev_link
import setup_tools


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Common ────────────────────────────────────────────────────────────────
    parser.add_argument("--home",        metavar="DIR",
        help="Home directory (default: platform home)")
    parser.add_argument("--claude-home", metavar="DIR",
        help="Claude config dir (auto-detected from $CLAUDE_HOME or ~/.claude)")
    parser.add_argument("--codex-home",  metavar="DIR",
        help="Codex config dir (auto-detected from $CODEX_HOME or ~/.codex)")
    parser.add_argument("--dry-run",     action="store_true",
        help="Print planned actions without writing files")

    # ── Symlink step ─────────────────────────────────────────────────────────
    parser.add_argument("--no-claude", action="store_true",
        help="Skip Claude symlinks (dev_link step)")
    parser.add_argument("--no-codex",  action="store_true",
        help="Skip Codex symlinks (dev_link step)")

    # ── Install step ─────────────────────────────────────────────────────────
    parser.add_argument("--bin-dir",         metavar="DIR",
        help="Directory for installed bin symlinks (default: ~/Documents/scripts/bin)")
    parser.add_argument("--shell-rc",        metavar="FILE",
        help="Shell rc file to update (auto-detected: ~/.zshrc for zsh, ~/.bashrc otherwise; Windows uses registry)")
    parser.add_argument("--system-shell-rc", metavar="FILE", default="/etc/bash.bashrc",
        help="System shell rc file (default: /etc/bash.bashrc)")
    parser.add_argument("--no-system-shell-rc", action="store_true",
        help="Skip updating the system shell rc")
    parser.add_argument("--default-llm",    choices=["claude", "codex"],
        help="Default assistant backend (prompted if omitted)")
    parser.add_argument("--cloud-files-remote-llm-root", metavar="PATH", default="assistant/",
        help="Path under the Drive root reserved for LLM files (default: assistant/)")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    home        = Path(args.home)        if args.home        else None
    claude_home = Path(args.claude_home) if args.claude_home else None
    codex_home  = Path(args.codex_home)  if args.codex_home  else None

    # Repo root is three levels above this script:
    #   <repo>/skills/install-assistant-tools/scripts/install.py
    repo_root = Path(__file__).resolve().parents[3]

    # Step 1: wire config dirs to the repo
    dev_link.run(
        repo_root=repo_root,
        home=home,
        claude_home=claude_home,
        codex_home=codex_home,
        do_claude=not args.no_claude,
        do_codex=not args.no_codex,
        dry_run=args.dry_run,
    )

    print()  # visual separator between the two steps

    # Step 2: install bin scripts, profiles, rc block, git hooks
    setup_tools.run(
        home=home,
        bin_dir=Path(args.bin_dir)   if args.bin_dir   else None,
        shell_rc=Path(args.shell_rc) if args.shell_rc  else None,
        system_shell_rc=Path(args.system_shell_rc),
        claude_home=claude_home,
        codex_home=codex_home,
        default_llm=args.default_llm,
        cloud_files_remote_llm_root=args.cloud_files_remote_llm_root,
        update_system_shell_rc=not args.no_system_shell_rc,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
