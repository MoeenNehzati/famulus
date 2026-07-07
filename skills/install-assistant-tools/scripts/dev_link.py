#!/usr/bin/env python3
"""
dev_link.py — Wire Claude and Codex to a live AI repo checkout (dev mode).

Instead of maintaining duplicate copies of skills, references, and agents
across Claude and Codex config directories, this script creates symlinks so
both tools read from the same checkout. Changes to the repo take effect
everywhere without any copy step. Also registers dev-mode hooks, sets
git core.hooksPath, and exports $AI — all dev-mode-only concerns, distinct
from the plugin-mode-safe scaffold.py/launchers.py subcommands.

repo_root is a required argument: dev mode is an explicit user choice with
an explicit repo path, never inferred from this script's own location.

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
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from install_manifest import Manifest, manifest_path
from link_utils import make_link
from rc_block import ensure_rc_vars


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = "", **kwargs) -> None:
    print(msg, flush=True, **kwargs)


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


def ensure_skills_link(repo_root: Path, src: Path, dst: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Ensure dst is a symlink to the canonical skills tree.

    If dst is an existing real directory, preserve unique local entries by
    migrating them into src, remove redundant per-skill symlinks that already
    point into src, and then replace the directory with a top-level symlink.
    Conflicting entries are left in place for manual resolution.
    """
    if dst.is_symlink() or not dst.exists():
        make_link(src, dst, dry_run, manifest)
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
        make_link(src, dst, dry_run=False, manifest=manifest)
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
    make_link(src, dst, dry_run=False, manifest=manifest)


def install_git_hooks(repo_root: Path, hooks_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Make all hook files executable and register the hooks directory with git."""
    if not hooks_dir.is_dir():
        log(f"ERROR: missing git hooks directory: {hooks_dir}", file=sys.stderr)
        sys.exit(1)

    for hook in hooks_dir.iterdir():
        if not hook.is_file():
            continue
        if dry_run:
            log(f"Would chmod +x {hook}")
        else:
            import stat
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Git hooks only apply to a development checkout. A plugin-cache install
    # (or any non-git copy of the repo) has no git dir — skip with a note
    # instead of crashing the whole install.
    probe = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-dir"],
        capture_output=True,
    )
    if probe.returncode != 0:
        log(f"Note: {repo_root} is not a git checkout; skipping git hooks setup.")
        return

    if dry_run:
        log(f"Would set git -C {repo_root} config core.hooksPath .githooks")
    else:
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "core.hooksPath", ".githooks"],
            check=True,
        )
        if manifest is not None:
            manifest.record("git_hooks_path", path=str(repo_root))


HOOKS_BLOCK_BEGIN = "# >>> skill-system-hooks >>>"
HOOKS_BLOCK_END = "# <<< skill-system-hooks <<<"


def _load_registered_hooks(repo_root: Path, host: str):
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from llmhooks.registry import hooks_for_host

    return hooks_for_host(host)


def _render_hook_command(argv: tuple[str, ...]) -> str:
    return shlex.join(argv)


def _legacy_managed_hook_commands(repo_root: Path) -> set[str]:
    legacy_script = repo_root / "hooks" / "inject_dispatcher_context.py"
    return {f'python3 "{legacy_script}"'}


def _hook_bindings(repo_root: Path, host: str):
    return [hook.install_binding(host, repo_root) for hook in _load_registered_hooks(repo_root, host)]


def _hook_commands_to_replace(repo_root: Path, host: str) -> set[str]:
    commands = {_render_hook_command(binding.argv) for binding in _hook_bindings(repo_root, host)}
    commands.update(_legacy_managed_hook_commands(repo_root))
    return commands


def _claude_hook_entries(repo_root: Path) -> dict[str, list[dict]]:
    entries: dict[str, list[dict]] = {}
    for binding in _hook_bindings(repo_root, "claude"):
        event_entries = entries.setdefault(binding.event, [])
        entry = {
            "hooks": [
                {
                    "type": "command",
                    "command": _render_hook_command(binding.argv),
                }
            ]
        }
        if binding.matcher is not None:
            entry["matcher"] = binding.matcher
        event_entries.append(entry)
    return entries


def _codex_hooks_block(repo_root: Path) -> str:
    lines = [f"\n{HOOKS_BLOCK_BEGIN}\n"]
    for binding in _hook_bindings(repo_root, "codex"):
        lines.append(f"[[hooks.{binding.event}]]\n")
        if binding.matcher is not None:
            lines.append(f"matcher = {json.dumps(binding.matcher)}\n")
        lines.append("\n")
        lines.append(f"[[hooks.{binding.event}.hooks]]\n")
        lines.append('type = "command"\n')
        lines.append(f"command = {json.dumps(_render_hook_command(binding.argv))}\n")
    lines.append(f"{HOOKS_BLOCK_END}\n")
    return "".join(lines)


def install_claude_hooks(claude_home: Path, repo_root: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Merge all managed hook entries into ~/.claude/settings.local.json.

    Commands use absolute repo_root paths so they work regardless of plugin
    installation. Idempotent: re-runs replace any existing managed entries,
    including legacy pre-registry commands.
    """
    managed_entries = _claude_hook_entries(repo_root)
    commands_to_replace = _hook_commands_to_replace(repo_root, "claude")

    settings_file = claude_home / "settings.local.json"
    log(f"\nInstalling Claude dev-mode hook: {settings_file}")

    if dry_run:
        for event, entries in managed_entries.items():
            log(f"  (dry-run) Would write {event} hook(s) to {settings_file}: {len(entries)} entry(s)")
        return

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"  WARN: {settings_file} is not valid JSON — skipping hook install")
            return

    hooks = settings.setdefault("hooks", {})
    for event_name in list(hooks.keys()):
        event_hooks = hooks.get(event_name)
        if not isinstance(event_hooks, list):
            continue
        filtered = [
            entry for entry in event_hooks
            if not any(
                hook.get("command", "") in commands_to_replace
                for hook in entry.get("hooks", [])
                if isinstance(hook, dict)
            )
        ]
        if filtered:
            hooks[event_name] = filtered
        else:
            hooks.pop(event_name, None)

    for event_name, entries in managed_entries.items():
        hooks.setdefault(event_name, []).extend(entries)

    fd, tmp = tempfile.mkstemp(dir=settings_file.parent, prefix=settings_file.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, settings_file)
        if manifest is not None:
            managed_commands = sorted(
                hook["command"]
                for entries in managed_entries.values()
                for entry in entries
                for hook in entry["hooks"]
            )
            manifest.record(
                "json_hook_commands", path=str(settings_file), commands=managed_commands
            )
    except Exception:
        os.unlink(tmp)
        raise

    log("  OK")


def install_codex_hooks(codex_home: Path, repo_root: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Append (or replace) the managed hook block in ~/.codex/config.toml.

    Uses BEGIN/END marker comments so the block can be updated idempotently on
    re-run without duplicating entries or touching other config.
    """
    block = _codex_hooks_block(repo_root)

    config_file = codex_home / "config.toml"
    log(f"\nInstalling Codex dev-mode hook: {config_file}")

    if dry_run:
        log(f"  (dry-run) Would write managed hook block to {config_file}")
        return

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.touch(exist_ok=True)
    original = config_file.read_text(encoding="utf-8")

    # Strip existing managed block (inclusive of markers).
    lines = original.splitlines(keepends=True)
    filtered: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == HOOKS_BLOCK_BEGIN:
            inside = True
            continue
        if stripped == HOOKS_BLOCK_END:
            inside = False
            continue
        if not inside:
            filtered.append(line)

    fd, tmp = tempfile.mkstemp(dir=config_file.parent, prefix=config_file.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(filtered)
            f.write(block)
        os.replace(tmp, config_file)
        if manifest is not None:
            manifest.record(
                "marker_block", path=str(config_file),
                begin=HOOKS_BLOCK_BEGIN, end=HOOKS_BLOCK_END,
            )
    except Exception:
        os.unlink(tmp)
        raise

    log("  OK")


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
    repo_root: Path,
    home: Path | None = None,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    shell_rc: Path | None = None,
    do_claude: bool = True,
    do_codex: bool = True,
    dry_run: bool = False,
    manifest: Manifest | None = None,
) -> None:
    """Create or repair Claude and Codex config dir symlinks, dev-mode hooks,
    git hooksPath, and the $AI env var.

    repo_root is required and must be supplied explicitly by the caller (the
    install.py orchestrator asks the user for it) — it is never derived from
    this script's own location. All other arguments are optional; paths
    default to platform home and standard config locations, and config dirs
    are auto-detected when not supplied.
    """
    home = home or Path.home()

    if manifest is None and not dry_run:
        manifest = Manifest(manifest_path(home))
    if dry_run:
        manifest = None

    profiles_dir = repo_root / "profiles"
    hooks_dir = repo_root / ".githooks"

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
        ensure_skills_link(repo_root, repo_root / "skills", claude_home / "skills", dry_run, manifest)
        make_link(repo_root / "references", claude_home / "references", dry_run, manifest)
        make_link(repo_root / "agents",     claude_home / "agents",     dry_run, manifest)
        make_link(repo_root / "CLAUDE.md",  claude_home / "CLAUDE.md",  dry_run, manifest)

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
            ensure_skills_link(repo_root, repo_root / "skills", codex_home / "skills", dry_run, manifest)
            make_link(repo_root / "references", codex_home / "references", dry_run, manifest)
            make_link(repo_root / "agents",     codex_home / "agents",     dry_run, manifest)
            # AGENTS.md is a tracked symlink to CLAUDE.md in the source repo.
            # Some plugin packaging flows flatten or omit that symlink, so fall
            # back to the real CLAUDE.md file while still creating
            # $CODEX_HOME/AGENTS.md.
            agents_md_source = repo_root / "AGENTS.md"
            if not agents_md_source.exists():
                agents_md_source = repo_root / "CLAUDE.md"
            make_link(agents_md_source, codex_home / "AGENTS.md", dry_run, manifest)

            # Codex loads profiles from individual files directly under $CODEX_HOME.
            if profiles_dir.is_dir():
                for profile in sorted(profiles_dir.glob("*.config.toml")):
                    make_link(profile, codex_home / profile.name, dry_run, manifest)
            else:
                log(f"  SKIP profiles (directory missing): {profiles_dir}")

    install_git_hooks(repo_root, hooks_dir, dry_run, manifest)
    if do_claude:
        install_claude_hooks(claude_home, repo_root, dry_run, manifest)
    if do_codex:
        install_codex_hooks(codex_home, repo_root, dry_run, manifest)

    if sys.platform == "win32":
        if dry_run:
            log(f"  Would set AI={repo_root}")
        else:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                winreg.SetValueEx(key, "AI", 0, winreg.REG_SZ, str(repo_root))
            log(f"  Set AI={repo_root}")
    else:
        if shell_rc is None:
            detected_shell = os.environ.get("SHELL", "")
            shell_rc = home / (".zshrc" if "zsh" in detected_shell else ".bashrc")
        ensure_rc_vars(
            shell_rc,
            {"AI": f'export AI="{repo_root}"'},
            dry_run,
            manifest,
            label="user",
        )

    # ── Summary ──────────────────────────────────────────────────────────────

    if manifest is not None:
        manifest.save()

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
    parser.add_argument("--repo-root",   metavar="DIR", required=True, help="Path to the AI repo checkout")
    parser.add_argument("--home",        metavar="DIR", help="Home directory")
    parser.add_argument("--claude-home", metavar="DIR", help="Override Claude config dir")
    parser.add_argument("--codex-home",  metavar="DIR", help="Override Codex config dir")
    parser.add_argument("--shell-rc",    metavar="FILE", help="Shell rc file (auto-detected on Unix)")
    parser.add_argument("--no-claude",   action="store_true", help="Skip Claude symlinks")
    parser.add_argument("--no-codex",    action="store_true", help="Skip Codex symlinks")
    parser.add_argument("--dry-run",     action="store_true", help="Print planned actions without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        repo_root=Path(args.repo_root),
        home=Path(args.home) if args.home else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        codex_home=Path(args.codex_home) if args.codex_home else None,
        shell_rc=Path(args.shell_rc) if args.shell_rc else None,
        do_claude=not args.no_claude,
        do_codex=not args.no_codex,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
