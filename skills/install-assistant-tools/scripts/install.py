#!/usr/bin/env python3
"""
install.py — Full installation of assistant tools on a new machine.

Runs both setup steps in order:
  1. setup_symlinks — wire Claude and Codex config dirs to the repo
  2. setup_tools    — install bin scripts, rc block, git hooks

Run individual scripts directly for targeted repairs:
  python3 scripts/setup_symlinks.py --help
  python3 scripts/setup_tools.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the scripts directory importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

import setup_symlinks
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
        help="Skip Claude symlinks (setup_symlinks step)")
    parser.add_argument("--no-codex",  action="store_true",
        help="Skip Codex symlinks (setup_symlinks step)")

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

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    home        = Path(args.home)        if args.home        else None
    claude_home = Path(args.claude_home) if args.claude_home else None
    codex_home  = Path(args.codex_home)  if args.codex_home  else None

    # Step 1: wire config dirs to the repo
    setup_symlinks.run(
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
        update_system_shell_rc=not args.no_system_shell_rc,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
