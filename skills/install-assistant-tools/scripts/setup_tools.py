#!/usr/bin/env python3
"""
setup_tools.py — LEGACY, superseded by scaffold.py + launchers.py + dev_link.py.

install.py no longer calls this file at all; it remains only as the
standalone `scripts-setup-tools` interface for targeted repairs during the
transition, and is slated for deletion once nothing else depends on it.

What it still does:
  - Bin symlinks: assistant, collab, coauthor, _agent_launch, tmux-workspace, tw
  - Worker directories for each agent (assistant, collab, coauthor)
  - Profile symlinks (profiles/*.config.toml -> Codex and Claude homes)
  - Claude settings symlinks (profiles/*_claude_setting.json -> Claude home)
  - dispatcher/invoke-skill launchers
  - Managed PATH/ASSISTANT_DEFAULT block in the user (and optionally system) shell rc

What it used to do but no longer does (moved elsewhere):
  - git hooks / dev-mode hook registration / $AI export -> dev_link.py
  - recurring-tasks env.sh / systemd AI_AGENT_COMMAND_TEMPLATE -> recurring-tasks/scripts/ensure_agent_env.py
  - cloud-files config.json + OAuth guidance -> cloud-files/scripts/ensure_oauth.py
  - g-calendar OAuth guidance -> g-calendar/scripts/ensure_oauth.py

The managed block written to shell rc files looks like:

    # >>> assistant-tools >>>
    export PATH="/path/to/bin:$PATH"
    export ASSISTANT_DEFAULT=claude
    export AI="/path/to/repo"
    # <<< assistant-tools <<<

(This file's own rc-block writer is untouched legacy code, still writing all
three vars in one shot — unlike scaffold.py/launchers.py/dev_link.py, which
each own exactly one via the shared rc_block.py merge writer.)

Re-running is safe: symlinks are replaced, the rc block is replaced in-place.

Shell rc management is a Linux/macOS feature. On Windows it's skipped with a note.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_manifest import Manifest, manifest_path
from link_utils import make_copy, make_link

# ── Constants ─────────────────────────────────────────────────────────────────

BLOCK_BEGIN = "# >>> assistant-tools >>>"
BLOCK_END   = "# <<< assistant-tools <<<"

# Scripts whose --help must succeed to pass verification.
VERIFY_CMDS = ["assistant", "collab", "coauthor", "tw"]

# Bin scripts to symlink (tw is a separate alias for tmux-workspace).
BIN_SCRIPTS = ["_agent_launch.py", "assistant", "collab", "coauthor", "tmux-workspace"]

# Windows .bat wrappers — installed on all platforms (harmless on Unix, required on Windows
# so that typing 'assistant' in cmd/PowerShell finds the script via PATHEXT).
BAT_WRAPPERS = ["assistant.bat", "collab.bat", "coauthor.bat"]

# Agent names that get worker directories under <repo>/workers/.
AGENTS = ["assistant", "collab", "coauthor"]

# Legacy symlink targets to clean up (filenames only, checked in bin and homes).
LEGACY_NAMES = ["coder", "coder.config.toml"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── Argument parsing ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--home", metavar="DIR",
        help="Home directory (default: $HOME or platform equivalent)")
    parser.add_argument("--bin-dir", metavar="DIR",
        help="Directory for installed symlinks (default: $HOME/Documents/scripts/bin)")
    parser.add_argument("--shell-rc", metavar="FILE",
        help="Shell rc file to update (auto-detected: ~/.zshrc for zsh, ~/.bashrc otherwise; Windows uses registry)")
    parser.add_argument("--system-shell-rc", metavar="FILE", default="/etc/bash.bashrc",
        help="System shell rc file to update when writable (default: /etc/bash.bashrc)")
    parser.add_argument("--codex-home", metavar="DIR",
        help="Codex config dir for profile symlinks (default: $CODEX_HOME or ~/.codex)")
    parser.add_argument("--claude-home", metavar="DIR",
        help="Claude config dir for profile symlinks (default: $CLAUDE_HOME or ~/.claude)")
    parser.add_argument("--default-llm", choices=["claude", "codex"],
        help="Default backend for assistant (prompted if omitted)")
    parser.add_argument("--no-system-shell-rc", action="store_true",
        help="Do not update the system shell rc file")
    parser.add_argument("--dry-run", action="store_true",
        help="Print planned actions without writing files")
    return parser.parse_args()


# ── Installation steps ────────────────────────────────────────────────────────

def install_worker_dirs(repo_root: Path, dry_run: bool) -> None:
    """Create per-agent worker directories under <repo>/workers/."""
    for agent in AGENTS:
        wdir = repo_root / "workers" / agent
        if dry_run:
            log(f"Would create worker dir {wdir}")
        else:
            wdir.mkdir(parents=True, exist_ok=True)


def install_bin_scripts(source_bin_dir: Path, bin_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Symlink all bin scripts into bin_dir. tw is an alias for tmux-workspace."""
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)

    for name in BIN_SCRIPTS:
        make_link(source_bin_dir / name, bin_dir / name, dry_run, manifest)

    # tw is a convenience alias for tmux-workspace
    make_link(source_bin_dir / "tmux-workspace", bin_dir / "tw", dry_run, manifest)

    # Windows .bat wrappers: installed on all platforms so the repo stays
    # consistent. On Windows, cmd/PowerShell finds 'assistant' via PATHEXT
    # because .bat is included by default. On Unix these files are ignored.
    for name in BAT_WRAPPERS:
        make_link(source_bin_dir / name, bin_dir / name, dry_run, manifest)


def remove_legacy_coder_links(
    source_bin_dir: Path,
    profiles_dir: Path,
    bin_dir: Path,
    codex_home: Path,
    claude_home: Path,
    dry_run: bool,
) -> None:
    """Remove legacy 'coder' symlinks that point back into this repo."""
    # Map each candidate legacy path to the repo target it should have pointed at
    candidates = {
        bin_dir      / "coder":              source_bin_dir / "coder",
        codex_home   / "coder.config.toml":  profiles_dir / "coder.config.toml",
        claude_home  / "coder.config.toml":  profiles_dir / "coder.config.toml",
    }
    for legacy, expected_target in candidates.items():
        if not legacy.is_symlink():
            continue
        if legacy.resolve() == expected_target.resolve():
            if dry_run:
                log(f"Would remove legacy link {legacy}")
            else:
                legacy.unlink()


def install_profile_links(
    profiles_dir: Path,
    codex_home: Path,
    claude_home: Path,
    dry_run: bool,
    manifest: Manifest | None = None,
) -> None:
    """Install repo-owned profiles into Codex and Claude config dirs.

    .config.toml profiles are COPIED, not symlinked: Codex writes
    machine-local state (project trust levels, trusted hook hashes — keyed
    by absolute paths) back into its config file. Through a symlink those
    writes would land in the tracked repo file and leak personal paths
    into git. See make_copy.
    """
    if not profiles_dir.is_dir():
        log(f"Warning: profiles directory is missing: {profiles_dir}")
        return

    if not dry_run:
        codex_home.mkdir(parents=True, exist_ok=True)
        claude_home.mkdir(parents=True, exist_ok=True)

    linked_any = False

    # .config.toml profiles are copied into both Codex and Claude homes
    # (copy, not symlink: the tool writes machine-local state back)
    for profile in sorted(profiles_dir.glob("*.config.toml")):
        linked_any = True
        make_copy(profile, codex_home  / profile.name, dry_run, manifest)
        make_copy(profile, claude_home / profile.name, dry_run, manifest)

    # Claude settings files go into Claude home only
    for settings in sorted(profiles_dir.glob("*_claude_setting.json")):
        linked_any = True
        make_link(settings, claude_home / settings.name, dry_run, manifest)

    if not linked_any:
        log(f"Warning: no profile files found in {profiles_dir}")


def install_dispatcher_launcher(repo_root: Path, bin_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Write a `dispatcher` launcher into the managed bin dir.

    First-party code is never pip-installed: the repo ($AI) is the single
    source of truth, and installed copies drift (and have been clobbered by
    test runs). The launcher runs script_dispatcher straight from the repo
    source. $AI is used when set (shell contexts); the install-time repo
    path is baked in as a fallback for contexts without it (systemd, cron).

    Requirement: a `python3` on PATH with PyYAML (script_dispatcher's only
    dependency). Revisit-trigger: if script_dispatcher ever grows pinned or
    compiled dependencies, switch to a dedicated venv (pipx-style) instead.
    """
    if sys.platform == "win32":
        log("Note: skipping dispatcher launcher (managed bin launchers are POSIX shell).")
        return

    launcher = bin_dir / "dispatcher"
    content = (
        "#!/bin/bash\n"
        "# Generated by install-assistant-tools - do not edit manually.\n"
        "# Runs script_dispatcher directly from the repo ($AI): first-party code\n"
        "# is never pip-installed, so there is no second copy to drift or break.\n"
        f'AI="${{AI:-{repo_root}}}"\n'
        'export PYTHONPATH="$AI/script_dispatcher/src${PYTHONPATH:+:$PYTHONPATH}"\n'
        'exec python3 -m script_dispatcher.cli "$@"\n'
    )

    if dry_run:
        log(f"Would write {launcher}")
        return

    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher.write_text(content)
    launcher.chmod(0o755)
    log(f"  Wrote dispatcher launcher: {launcher}")
    if manifest is not None:
        manifest.record("file", path=str(launcher))


def install_invoke_skill_launcher(bin_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Write an `invoke-skill` launcher into the managed bin dir.

    This launcher is used by AI_AGENT_COMMAND_TEMPLATE to invoke skills from
    systemd timers and cron jobs. Instead of storing an absolute path to
    invoke-agent.sh in the environment (which breaks when paths change),
    we use a launcher on PATH that finds invoke-agent.sh at runtime.

    The launcher:
    1. Sources env.sh from the recurring-tasks skill to set up PATH
    2. Calls invoke-agent.sh with the skill name
    3. Resolves $CLAUDE_HOME at runtime with fallback to ~/.claude

    This ensures that test installations (with overridden --home or temporary
    paths) don't clobber the real systemd user session's environment.
    """
    if sys.platform == "win32":
        log("Note: skipping invoke-skill launcher (managed bin launchers are POSIX shell).")
        return

    launcher = bin_dir / "invoke-skill"
    content = (
        "#!/bin/bash\n"
        "# Generated by install-assistant-tools - do not edit manually.\n"
        "# Invokes a skill via the assistant command for recurring tasks.\n"
        "# Uses $CLAUDE_HOME to find the recurring-tasks skill at runtime.\n"
        "set -euo pipefail\n"
        "\n"
        "if [[ $# -ne 1 ]]; then\n"
        "  echo 'Usage: invoke-skill <skill-name>' >&2\n"
        "  exit 2\n"
        "fi\n"
        "\n"
        "skill=\"$1\"\n"
        "claude_home=\"${CLAUDE_HOME:-$HOME/.claude}\"\n"
        "recurring_tasks_dir=\"$claude_home/skills/recurring-tasks\"\n"
        "invoke_agent=\"$recurring_tasks_dir/scripts/invoke-agent.sh\"\n"
        "\n"
        "if [[ ! -x \"$invoke_agent\" ]]; then\n"
        "  echo \"ERROR: invoke-agent.sh not found at: $invoke_agent\" >&2\n"
        "  exit 1\n"
        "fi\n"
        "\n"
        "exec \"$invoke_agent\" \"$skill\"\n"
    )

    if dry_run:
        log(f"Would write {launcher}")
        return

    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher.write_text(content)
    launcher.chmod(0o755)
    log(f"  Wrote invoke-skill launcher: {launcher}")
    if manifest is not None:
        manifest.record("file", path=str(launcher))


def ensure_rc_block(rc_file: Path, bin_dir: Path, default_llm: str, repo_root: Path, label: str, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Write (or replace) the managed assistant-tools block in a shell rc file.

    Strips any existing block delimited by BLOCK_BEGIN/BLOCK_END, then appends
    a fresh block. Uses an atomic temp-file swap to avoid partial writes.
    """
    if dry_run:
        log(f"Would update {label} rc: {rc_file}")
        return

    rc_file.parent.mkdir(parents=True, exist_ok=True)
    rc_file.touch(exist_ok=True)

    original = rc_file.read_text(encoding="utf-8")

    # Strip the existing managed block (inclusive of delimiters). Also drop
    # the blank separator line immediately before it (written by this same
    # function, and by rc_block.ensure_rc_vars) so repeated writes — from
    # this function or from another writer sharing the same managed block —
    # don't accumulate extra blank lines.
    lines = original.splitlines(keepends=True)
    filtered: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == BLOCK_BEGIN:
            inside = True
            if filtered and not filtered[-1].strip():
                filtered.pop()
            continue
        if stripped == BLOCK_END:
            inside = False
            continue
        if not inside:
            filtered.append(line)

    # Build the new block. $PATH is a shell variable reference — write it literally.
    new_block = (
        f"\n{BLOCK_BEGIN}\n"
        f'export PATH="{bin_dir}:$PATH"\n'
        f"export ASSISTANT_DEFAULT={default_llm}\n"
        f'export AI="{repo_root}"\n'
        f"{BLOCK_END}\n"
    )

    # Atomic write via temp file in the same directory
    fd, tmp_path = tempfile.mkstemp(dir=rc_file.parent, prefix=rc_file.name + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(filtered)
            f.write(new_block)
        os.replace(tmp_path, rc_file)
        if manifest is not None:
            manifest.record("marker_block", path=str(rc_file), begin=BLOCK_BEGIN, end=BLOCK_END)
    except Exception:
        os.unlink(tmp_path)
        raise


def maybe_ensure_system_rc_block(
    system_rc: Path,
    bin_dir: Path,
    default_llm: str,
    repo_root: Path,
    update: bool,
    dry_run: bool,
    manifest: Manifest | None = None,
) -> None:
    """Update the system shell rc block if allowed and writable."""
    if not update:
        return

    if sys.platform == "win32":
        log("Note: skipping system shell rc update (not applicable on Windows).")
        return

    if dry_run:
        log(f"Would update system rc: {system_rc}")
        return

    if system_rc.exists() and not os.access(system_rc, os.W_OK):
        log(f"Warning: system rc is not writable: {system_rc}")
        log("  Re-run with sudo, or pass --system-shell-rc FILE for another path.")
        return

    if not system_rc.exists() and not os.access(system_rc.parent, os.W_OK):
        log(f"Warning: cannot create system rc: {system_rc}")
        log("  Re-run with sudo, or pass --system-shell-rc FILE for another path.")
        return

    ensure_rc_block(system_rc, bin_dir, default_llm, repo_root, "system", dry_run, manifest)


def verify_install(bin_dir: Path) -> bool:
    """Run --help on each installed command and report results.

    On Windows, tmux-workspace is skipped (tmux is not available) and .bat
    wrappers are used for assistant/collab/coauthor because extension-less
    scripts cannot be executed directly by Windows.
    """
    log("")
    log("Verifying installation...")
    ok = True
    is_windows = sys.platform == "win32"

    for name in VERIFY_CMDS:
        if is_windows and name == "tw":
            log("  SKIP: tw (tmux not available on Windows)")
            continue

        # On Windows, invoke the .bat wrapper; on Unix, invoke the script directly.
        if is_windows and name in ("assistant", "collab", "coauthor"):
            dst = bin_dir / f"{name}.bat"
        else:
            dst = bin_dir / name

        if not dst.exists():
            log(f"  FAIL: {dst} not found")
            ok = False
            continue
        if not is_windows and not os.access(dst, os.X_OK):
            log(f"  FAIL: {dst} is not executable")
            ok = False
            continue
        result = subprocess.run([str(dst), "--help"], capture_output=True)
        if result.returncode == 0:
            log(f"  OK:   {dst} --help")
        else:
            log(f"  FAIL: {dst} --help exited {result.returncode}")
            ok = False

    if not ok:
        log("Warning: one or more verification checks failed.")
    return ok


def _broadcast_env_change_windows() -> None:
    """Tell running Windows processes that user environment variables changed.

    After updating the registry, Explorer and any shell that listens for
    WM_SETTINGCHANGE will refresh their environment. New terminals opened after
    this call will see the updated PATH without requiring a logoff/reboot.
    """
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, None,
        )
    except Exception:
        pass  # non-fatal; new terminal will still pick up the change


def update_windows_user_env(
    bin_dir: Path,
    default_llm: str,
    repo_root: Path,
    dry_run: bool,
    manifest: Manifest | None = None,
) -> None:
    """Permanently add bin_dir to the user PATH via the Windows registry.

    Also sets ASSISTANT_DEFAULT and AI as persistent user environment variables.
    Changes are visible in any new terminal after this call; running terminals
    may need to be restarted.

    Uses HKEY_CURRENT_USER\\Environment (no admin required). Does not touch the
    system PATH.
    """
    import winreg

    REG_PATH = "Environment"

    if dry_run:
        log(f"  Would add to user PATH (registry): {bin_dir}")
        log(f"  Would set ASSISTANT_DEFAULT={default_llm}")
        log(f"  Would set AI={repo_root}")
        return

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, REG_PATH, 0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        # PATH — preserve existing entries, prepend bin_dir if not already present.
        try:
            current_path, path_type = winreg.QueryValueEx(key, "PATH")
        except FileNotFoundError:
            current_path, path_type = "", winreg.REG_EXPAND_SZ

        bin_str = str(bin_dir)
        parts = [p for p in current_path.split(";") if p]
        if bin_str not in parts:
            new_path = ";".join([bin_str] + parts)
            winreg.SetValueEx(key, "PATH", 0, path_type, new_path)
            log(f"  Added to user PATH: {bin_dir}")
        else:
            log(f"  User PATH already contains: {bin_dir}")

        # ASSISTANT_DEFAULT
        winreg.SetValueEx(key, "ASSISTANT_DEFAULT", 0, winreg.REG_SZ, default_llm)
        log(f"  Set ASSISTANT_DEFAULT={default_llm}")

        # AI root
        winreg.SetValueEx(key, "AI", 0, winreg.REG_SZ, str(repo_root))
        log(f"  Set AI={repo_root}")

    if manifest is not None:
        manifest.record(
            "registry_env", path=str(bin_dir), names=["ASSISTANT_DEFAULT", "AI"]
        )
    _broadcast_env_change_windows()
    log("  Environment updated. Open a new terminal to use the installed commands.")


def warn_missing_commands(names: list[str]) -> None:
    """Warn about commands that are not currently on PATH."""
    for name in names:
        if not shutil.which(name):
            log(f"Warning: '{name}' is not currently on PATH.")


# ── Python packages ───────────────────────────────────────────────────────────

# Packages required by skill scripts (cross-platform; installed via pip).
REQUIRED_PYTHON_PACKAGES = [
    "dateparser",  # list-manager: migrate-md deadline resolution
]


def install_python_packages(dry_run: bool, manifest: Manifest | None = None) -> None:
    """Ensure required Python packages are installed."""
    log("\nInstalling required Python packages...")
    # Note: script_dispatcher (first-party) is deliberately NOT pip-installed;
    # it runs from the repo via the generated dispatcher launcher.
    for package in REQUIRED_PYTHON_PACKAGES:
        if dry_run:
            log(f"  (dry-run) Would install: {package}")
            continue
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            log(f"  OK: {package}")
        else:
            log(f"  WARN: failed to install {package}: {result.stderr.strip()}")


# ── Core logic ────────────────────────────────────────────────────────────────

def run(
    *,
    home: Path | None = None,
    bin_dir: Path | None = None,
    shell_rc: Path | None = None,
    system_shell_rc: Path = Path("/etc/bash.bashrc"),
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    default_llm: str | None = None,
    update_system_shell_rc: bool = True,
    dry_run: bool = False,
    install_packages: bool = True,
    manifest: Manifest | None = None,
    repo_root: Path | None = None,
) -> None:
    """Install or update assistant tools.

    All arguments are optional; paths default to platform home and standard
    locations. default_llm is prompted interactively when not supplied.

    repo_root defaults to the repo containing this script. Tests calling
    run() in-process MUST pass a throwaway repo_root: several steps write
    into the repo (recurring-tasks env.sh, git hooksPath, worker dirs), and
    the default would mutate the live checkout.
    """
    home = home or Path.home()

    # Script is at <repo>/skills/install-assistant-tools/scripts/install_assistant_tools.py
    script_path    = Path(__file__).resolve()
    skill_dir      = script_path.parents[1]
    repo_root      = repo_root or script_path.parents[3]
    source_bin_dir = skill_dir / "bin"
    profiles_dir   = repo_root / "profiles"

    bin_dir    = bin_dir or home / "Documents" / "scripts" / "bin"
    codex_home = codex_home  or Path(os.environ.get("CODEX_HOME",  str(home / ".codex")))
    claude_home = claude_home or Path(os.environ.get("CLAUDE_HOME", str(home / ".claude")))

    # Auto-detect the user shell rc file on Unix.
    # On Windows, PATH and env vars are managed via the registry instead.
    if shell_rc is None and sys.platform != "win32":
        detected_shell = os.environ.get("SHELL", "")
        if "zsh" in detected_shell:
            shell_rc = home / ".zshrc"
        else:
            shell_rc = home / ".bashrc"   # bash, dash, or unknown

    # Resolve default LLM (prompt if needed)
    if default_llm is None:
        if dry_run:
            log("(dry-run) Would prompt for default LLM; using 'claude' as placeholder")
            default_llm = "claude"
        elif sys.stdin.isatty():
            try:
                reply = input("Default assistant backend [claude/codex] (default: claude): ").strip()
            except EOFError:  # isatty() can lie (e.g. Windows CI consoles)
                reply = ""
            default_llm = reply if reply in ("claude", "codex") else "claude"
            if reply and reply not in ("claude", "codex"):
                log(f"Invalid choice '{reply}'; defaulting to claude.")
        else:
            log("Non-interactive mode: defaulting to 'claude'. Use --default-llm to override.")
            default_llm = "claude"

    if manifest is None and not dry_run:
        manifest = Manifest(manifest_path(home))
    if dry_run:
        manifest = None

    if install_packages:
        install_python_packages(dry_run, manifest)
    install_worker_dirs(repo_root, dry_run)
    install_bin_scripts(source_bin_dir, bin_dir, dry_run, manifest)
    install_profile_links(profiles_dir, codex_home, claude_home, dry_run, manifest)
    remove_legacy_coder_links(source_bin_dir, profiles_dir, bin_dir, codex_home, claude_home, dry_run)
    install_dispatcher_launcher(repo_root, bin_dir, dry_run, manifest)
    install_invoke_skill_launcher(bin_dir, dry_run, manifest)

    # Platform-specific PATH and environment variable setup.
    if sys.platform == "win32":
        # Windows: write to the user environment registry block.
        # Shell rc files (bashrc/zshrc) don't apply here.
        log("\nUpdating Windows user environment variables...")
        update_windows_user_env(bin_dir, default_llm, repo_root, dry_run, manifest)
    else:
        # Unix: write a managed block to the user (and optionally system) shell rc.
        ensure_rc_block(shell_rc, bin_dir, default_llm, repo_root, "user", dry_run, manifest)
        maybe_ensure_system_rc_block(system_shell_rc, bin_dir, default_llm, repo_root, update_system_shell_rc, dry_run, manifest)

    if not dry_run:
        verify_install(bin_dir)

    warn_missing_commands(["tmux", "codex", "claude"])

    log("")
    log("Installed assistant tools.")
    log(f"  Bin dir:        {bin_dir}")
    log(f"  Source bin:     {source_bin_dir}")
    log(f"  Codex home:     {codex_home}")
    log(f"  Claude home:    {claude_home}")
    log(f"  AI root:        {repo_root}")
    log(f"  Default LLM:    {default_llm}")
    if sys.platform == "win32":
        log("  PATH/env:       HKEY_CURRENT_USER\\Environment (registry)")
    else:
        log(f"  User shell rc:  {shell_rc}")
        if update_system_shell_rc:
            log(f"  System rc:      {system_shell_rc}")
    if manifest is not None:
        manifest.save()
    log("")
    if sys.platform == "win32":
        log("Open a new terminal to use the installed commands.")
    else:
        log(f"Run 'source \"{shell_rc}\"' or open a new shell to apply PATH changes.")


# ── Argument parsing + entry point ────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    home = Path(args.home) if args.home else None
    run(
        home=home,
        bin_dir=Path(args.bin_dir)    if args.bin_dir    else None,
        shell_rc=Path(args.shell_rc)  if args.shell_rc   else None,
        system_shell_rc=Path(args.system_shell_rc),
        codex_home=Path(args.codex_home)  if args.codex_home  else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        default_llm=args.default_llm,
        update_system_shell_rc=not args.no_system_shell_rc,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
