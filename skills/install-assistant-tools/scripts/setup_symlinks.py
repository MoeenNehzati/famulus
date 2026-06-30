#!/usr/bin/env python3
"""
setup_symlinks.py — Wire Claude and Codex to the canonical AI config repo.

Instead of maintaining duplicate copies of skills, references, and agents
across Claude and Codex config directories, this script creates symlinks so
both tools read from the same checkout. Changes to the repo take effect
everywhere without any copy step.

Links created (documented in README.md § Systemwide Local Setup):

  Claude (~/.claude/ or $CLAUDE_HOME):
    skills     -> <repo>/skills      (skill library shared with Codex)
    references -> <repo>/references  (shared reference docs)
    agents     -> <repo>/agents      (agent definitions)
    CLAUDE.md  -> <repo>/CLAUDE.md   (shared repo instructions)

  Codex (~/.codex/ or $CODEX_HOME):
    references      -> <repo>/references
    agents          -> <repo>/agents
    AGENTS.md       -> <repo>/AGENTS.md  (same content as CLAUDE.md via symlink)
    <p>.config.toml -> <repo>/profiles/<p>.config.toml (one per profile)

NOTE: ~/.codex itself must be a real directory, not a symlink. Codex's Linux
sandbox may reject read-only mounts that cross a writable symlink at the
home-directory boundary. The script detects and warns about this case.

On Windows, creating symlinks requires either Developer Mode or administrator
privileges. The script will report a clear error if symlink creation fails.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = "") -> None:
    print(msg, flush=True)


def make_link(src: Path, dst: Path, dry_run: bool) -> None:
    """Create or replace the symlink at dst pointing to src.

    Skips with a warning when src does not exist (e.g. optional repo
    directory). On platforms where symlink creation requires elevated
    privileges, reports a clear error instead of crashing.
    """
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dry_run:
        log(f"  Would link: {dst} -> {src}")
        return

    # Remove an existing symlink so ln -sfn semantics are preserved.
    # Never remove a real file or directory — that would be destructive.
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        log(f"  SKIP (already exists as real path, not a symlink): {dst}")
        return

    try:
        dst.symlink_to(src)
        log(f"  Linked: {dst} -> {src}")
    except OSError as exc:
        # On Windows without Developer Mode / admin rights symlink creation
        # raises PermissionError. Give a useful hint rather than a traceback.
        if sys.platform == "win32":
            log(
                f"  ERROR: could not create symlink {dst} -> {src}\n"
                f"  On Windows, symlinks require Developer Mode or administrator"
                f" privileges.\n  ({exc})"
            )
        else:
            log(f"  ERROR: could not create symlink {dst} -> {src}: {exc}")


def resolve_dir(tool: str, env_var: str, default: Path) -> Path:
    """Return the config directory for a tool.

    Resolution order:
      1. Environment variable (e.g. $CLAUDE_HOME)
      2. Default path if it already exists (e.g. ~/.claude)
      3. Interactive prompt when stdin is a terminal
      4. Non-interactive fallback: default path (will be created)
    """
    val = os.environ.get(env_var, "").strip()
    if val:
        return Path(val)

    if default.exists():
        return default

    if sys.stdin.isatty():
        answer = input(
            f"{tool} config directory not found. Enter path [{default}]: "
        ).strip()
        return Path(answer) if answer else default

    log(f"Note: {tool} config dir not found; will use {default} (will be created).")
    return default


# ── Core logic ────────────────────────────────────────────────────────────────

def run(
    *,
    home: Path | None = None,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    do_claude: bool = True,
    do_codex: bool = True,
    dry_run: bool = False,
) -> None:
    """Create or repair Claude and Codex config dir symlinks.

    All arguments are optional. Paths default to platform home and standard
    config locations; config dirs are auto-detected when not supplied.
    """
    home = home or Path.home()

    # Repo root is three levels above this script:
    #   <repo>/skills/install-assistant-tools/scripts/setup_symlinks.py
    repo_root = Path(__file__).resolve().parents[3]
    profiles_dir = repo_root / "profiles"

    # Resolve config dirs (auto-detect from env vars or common paths)
    if do_claude:
        claude_home = claude_home or resolve_dir("Claude", "CLAUDE_HOME", home / ".claude")

    if do_codex:
        codex_home = codex_home or resolve_dir("Codex", "CODEX_HOME", home / ".codex")

    # ── Claude symlinks ──────────────────────────────────────────────────────
    # Wire the four shared trees into the Claude config dir so Claude reads
    # from the repo without needing a separate copy.

    if do_claude:
        log(f"Setting up Claude symlinks in {claude_home} ...")
        if not dry_run:
            claude_home.mkdir(parents=True, exist_ok=True)
        make_link(repo_root / "skills",     claude_home / "skills",     dry_run)
        make_link(repo_root / "references", claude_home / "references", dry_run)
        make_link(repo_root / "agents",     claude_home / "agents",     dry_run)
        make_link(repo_root / "CLAUDE.md",  claude_home / "CLAUDE.md",  dry_run)

    # ── Codex symlinks ───────────────────────────────────────────────────────
    # codex_home itself must be a REAL directory (never a symlink). Codex's
    # sandbox may reject mounts that cross a writable symlink at the home
    # boundary.

    if do_codex:
        if codex_home.is_symlink():
            log(f"Warning: {codex_home} is a symlink, not a real directory.")
            log("  Codex requires a real directory here. Remove the symlink and re-run.")
            log("  Skipping Codex directory links.")
        else:
            log(f"Setting up Codex symlinks in {codex_home} ...")
            if not dry_run:
                codex_home.mkdir(parents=True, exist_ok=True)
            make_link(repo_root / "references", codex_home / "references", dry_run)
            make_link(repo_root / "agents",     codex_home / "agents",     dry_run)
            # AGENTS.md is a tracked symlink to CLAUDE.md in the source repo.
            # Some plugin packaging flows flatten or omit that symlink, so fall
            # back to the real CLAUDE.md file while still creating
            # $CODEX_HOME/AGENTS.md.
            agents_md_source = repo_root / "AGENTS.md"
            if not agents_md_source.exists():
                agents_md_source = repo_root / "CLAUDE.md"
            make_link(agents_md_source, codex_home / "AGENTS.md", dry_run)

            # Codex loads profiles from individual files directly under $CODEX_HOME.
            if profiles_dir.is_dir():
                for profile in sorted(profiles_dir.glob("*.config.toml")):
                    make_link(profile, codex_home / profile.name, dry_run)
            else:
                log(f"  SKIP profiles (directory missing): {profiles_dir}")

    # ── Summary ──────────────────────────────────────────────────────────────

    log()
    log("Symlink setup complete.")
    log(f"  Repo root: {repo_root}")
    if do_claude:
        log(f"  Claude:    {claude_home}")
    if do_codex:
        log(f"  Codex:     {codex_home}")


# ── Argument parsing + entry point ────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--home",        metavar="DIR", help="Home directory")
    parser.add_argument("--claude-home", metavar="DIR", help="Override Claude config dir")
    parser.add_argument("--codex-home",  metavar="DIR", help="Override Codex config dir")
    parser.add_argument("--no-claude",   action="store_true", help="Skip Claude symlinks")
    parser.add_argument("--no-codex",    action="store_true", help="Skip Codex symlinks")
    parser.add_argument("--dry-run",     action="store_true", help="Print planned actions without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        home=Path(args.home) if args.home else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        codex_home=Path(args.codex_home) if args.codex_home else None,
        do_claude=not args.no_claude,
        do_codex=not args.no_codex,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
