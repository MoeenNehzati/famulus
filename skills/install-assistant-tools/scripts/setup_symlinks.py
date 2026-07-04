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
    skills          -> <repo>/skills
    references      -> <repo>/references
    agents          -> <repo>/agents
    AGENTS.md       -> <repo>/AGENTS.md  (same content as CLAUDE.md via symlink)
    <p>.config.toml -> <repo>/profiles/<p>.config.toml (one per profile)

NOTE: ~/.codex itself must be a real directory, not a symlink. Codex's Linux
sandbox may reject read-only mounts that cross a writable symlink at the
home-directory boundary. The script detects and warns about this case.

If an existing ~/.claude/skills or ~/.codex/skills directory already contains
local skill entries, the script preserves unique entries by migrating them into
the canonical repo skills tree before replacing the user directory with a
top-level symlink. When possible, preserved local entries are recorded in the
repo-local Git exclude file.

On Windows, creating symlinks requires either Developer Mode or administrator
privileges. The script will report a clear error if symlink creation fails.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = "") -> None:
    print(msg, flush=True)


def git_exclude_path(repo_root: Path) -> Path | None:
    """Return the repo-local Git exclude path when this checkout has one."""
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git / "info" / "exclude"
    if dot_git.is_file():
        line = dot_git.read_text(encoding="utf-8").strip()
        if line.startswith("gitdir:"):
            git_dir = Path(line.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = (repo_root / git_dir).resolve()
            return git_dir / "info" / "exclude"
    return None


def record_local_skill_exclude(repo_root: Path, entry_name: str, dry_run: bool) -> None:
    """Record a preserved local skill entry in the repo-local Git exclude file."""
    exclude_path = git_exclude_path(repo_root)
    exclude_line = f"skills/{entry_name}"

    if exclude_path is None:
        log(f"    Note: preserved local skill not auto-ignored (no Git exclude path): {exclude_line}")
        return

    if dry_run:
        log(f"    Would add to repo-local Git exclude: {exclude_line}")
        return

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    if exclude_line in existing_lines:
        return

    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with exclude_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{exclude_line}\n")
    log(f"    Added to repo-local Git exclude: {exclude_line}")


def make_link(src: Path, dst: Path, dry_run: bool) -> None:
    """Create or replace the symlink at dst pointing to src.

    Skips with a warning when src does not exist (e.g. optional repo
    directory). On platforms where symlink creation requires elevated
    privileges, reports a clear error instead of crashing.
    """
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dst.is_symlink():
        try:
            if dst.resolve() == src.resolve():
                log(f"  OK (already linked): {dst} -> {src}")
                return
        except OSError:
            pass

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


def ensure_skills_link(repo_root: Path, src: Path, dst: Path, dry_run: bool) -> None:
    """Ensure dst is a symlink to the canonical skills tree.

    If dst is an existing real directory, preserve unique local entries by
    migrating them into src, remove redundant per-skill symlinks that already
    point into src, and then replace the directory with a top-level symlink.
    Conflicting entries are left in place for manual resolution.
    """
    if dst.is_symlink() or not dst.exists():
        make_link(src, dst, dry_run)
        return

    if not dst.is_dir():
        log(f"  SKIP (already exists as real path, not a directory or symlink): {dst}")
        return

    entries = sorted(dst.iterdir(), key=lambda path: path.name)
    if not entries:
        if dry_run:
            log(f"  Would replace empty skills directory with symlink: {dst} -> {src}")
            return
        dst.rmdir()
        make_link(src, dst, dry_run=False)
        return

    redundant_entries: list[Path] = []
    unique_entries: list[Path] = []
    conflicts: list[str] = []

    for entry in entries:
        canonical_entry = src / entry.name
        if not canonical_entry.exists():
            unique_entries.append(entry)
            continue
        try:
            if entry.resolve() == canonical_entry.resolve():
                redundant_entries.append(entry)
                continue
        except OSError:
            pass
        conflicts.append(entry.name)

    if conflicts:
        log(f"  SKIP (skills directory has conflicting entries; resolve manually): {dst}")
        for name in conflicts:
            log(f"    CONFLICT: {name}")
        return

    if dry_run:
        for entry in redundant_entries:
            log(f"    Would remove redundant skill entry before linking: {entry.name}")
        for entry in unique_entries:
            log(f"    Would preserve local skill entry in canonical tree: {entry.name}")
            record_local_skill_exclude(repo_root, entry.name, dry_run=True)
        log(f"  Would replace migrated skills directory with symlink: {dst} -> {src}")
        return

    for entry in redundant_entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        log(f"    Removed redundant skill entry: {entry.name}")

    for entry in unique_entries:
        target = src / entry.name
        shutil.move(str(entry), str(target))
        log(f"    Preserved local skill entry: {entry.name} -> {target}")
        record_local_skill_exclude(repo_root, entry.name, dry_run=False)

    dst.rmdir()
    make_link(src, dst, dry_run=False)


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
    repo_root: Path | None = None,
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
    repo_root = repo_root or Path(__file__).resolve().parents[3]
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
        ensure_skills_link(repo_root, repo_root / "skills", claude_home / "skills", dry_run)
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
            ensure_skills_link(repo_root, repo_root / "skills", codex_home / "skills", dry_run)
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
