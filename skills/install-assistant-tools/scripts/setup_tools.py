#!/usr/bin/env python3
"""
setup_tools.py — Install or update assistant, collab, coauthor, and tw.

Installs or updates:
  - Bin symlinks: assistant, collab, coauthor, _agent_launch, tmux-workspace, tw
  - Worker directories for each agent (assistant, collab, coauthor)
  - Profile symlinks (profiles/*.config.toml -> Codex and Claude homes)
  - Claude settings symlinks (profiles/*_claude_setting.json -> Claude home)
  - Git hook path for this repository (.githooks)
  - AI agent environment file (~/.config/environment.d/20-ai-agent.conf)
  - Managed PATH/env block in the user (and optionally system) shell rc

The managed block written to shell rc files looks like:

    # >>> assistant-tools >>>
    export PATH="/path/to/bin:$PATH"
    export ASSISTANT_DEFAULT=claude
    export AI="/path/to/repo"
    # <<< assistant-tools <<<

Re-running is safe: symlinks are replaced, the rc block is replaced in-place,
and the git hook path is idempotent.

Shell rc management and the systemd environment file are Linux/macOS features.
On Windows these steps are skipped with a note.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# Make sibling skill scripts importable.
sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "skills" / "cloud-files" / "scripts")))

import cloud_files


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


def make_link(src: Path, dst: Path, dry_run: bool) -> None:
    """Create or replace the symlink at dst pointing to src.

    - Skips with a warning if src does not exist.
    - Replaces an existing symlink atomically.
    - Skips with a warning if dst exists as a real file/directory (won't clobber).
    - Reports a clear error on Windows if symlink creation fails due to permissions.
    """
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dry_run:
        log(f"  Would link: {dst} -> {src}")
        return

    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        log(f"  SKIP (real path exists, not a symlink): {dst}")
        return

    try:
        dst.symlink_to(src)
        log(f"  Linked: {dst} -> {src}")
    except OSError as exc:
        hint = (
            "\n  On Windows, symlinks require Developer Mode or administrator privileges."
            if sys.platform == "win32"
            else ""
        )
        log(f"  ERROR: could not create symlink {dst} -> {src}: {exc}{hint}")


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
    parser.add_argument("--cloud-files-remote-llm-root", metavar="PATH", default="assistant/",
        help="Path under the Drive root reserved for LLM files (default: assistant/)")
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


def install_bin_scripts(source_bin_dir: Path, bin_dir: Path, dry_run: bool) -> None:
    """Symlink all bin scripts into bin_dir. tw is an alias for tmux-workspace."""
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)

    for name in BIN_SCRIPTS:
        make_link(source_bin_dir / name, bin_dir / name, dry_run)

    # tw is a convenience alias for tmux-workspace
    make_link(source_bin_dir / "tmux-workspace", bin_dir / "tw", dry_run)

    # Windows .bat wrappers: installed on all platforms so the repo stays
    # consistent. On Windows, cmd/PowerShell finds 'assistant' via PATHEXT
    # because .bat is included by default. On Unix these files are ignored.
    for name in BAT_WRAPPERS:
        make_link(source_bin_dir / name, bin_dir / name, dry_run)


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
) -> None:
    """Symlink repo-owned profiles into Codex and Claude config dirs."""
    if not profiles_dir.is_dir():
        log(f"Warning: profiles directory is missing: {profiles_dir}")
        return

    if not dry_run:
        codex_home.mkdir(parents=True, exist_ok=True)
        claude_home.mkdir(parents=True, exist_ok=True)

    linked_any = False

    # .config.toml profiles go into both Codex and Claude homes
    for profile in sorted(profiles_dir.glob("*.config.toml")):
        linked_any = True
        make_link(profile, codex_home  / profile.name, dry_run)
        make_link(profile, claude_home / profile.name, dry_run)

    # Claude settings files go into Claude home only
    for settings in sorted(profiles_dir.glob("*_claude_setting.json")):
        linked_any = True
        make_link(settings, claude_home / settings.name, dry_run)

    if not linked_any:
        log(f"Warning: no profile files found in {profiles_dir}")


def install_git_hooks(repo_root: Path, hooks_dir: Path, dry_run: bool) -> None:
    """Make all hook files executable and register the hooks directory with git."""
    if not hooks_dir.is_dir():
        log(f"ERROR: missing git hooks directory: {hooks_dir}", file=sys.stderr)
        sys.exit(1)

    # Make every file in the hooks dir executable
    for hook in hooks_dir.iterdir():
        if not hook.is_file():
            continue
        if dry_run:
            log(f"Would chmod +x {hook}")
        else:
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if dry_run:
        log(f"Would set git -C {repo_root} config core.hooksPath .githooks")
    else:
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "core.hooksPath", ".githooks"],
            check=True,
        )


def install_cloud_files_config(home: Path, remote_llm_root: str, dry_run: bool) -> None:
    """Write the cloud-files config under ~/.config/cloud-files/."""
    config_dir = home / ".config" / "cloud-files"
    config_path = config_dir / "config.json"

    existing: dict[str, object] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    try:
        normalized_llm_root = cloud_files.normalize_llm_root(remote_llm_root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    payload: dict[str, object] = {
        "remote_llm_root": normalized_llm_root,
        "timeout_seconds": int(existing.get("timeout_seconds", 45)),
    }
    if "credentials_path" in existing:
        payload["credentials_path"] = existing["credentials_path"]

    if dry_run:
        log(f"Would write cloud-files config {config_path}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def cloud_files_client_setup_lines(home: Path) -> list[str]:
    client_json = home / ".config" / "cloud-files" / "client.json"
    return [
        "Cloud-files Google OAuth client setup still needed.",
        "  In Google Cloud Console, create or download an OAuth client JSON for a Desktop app.",
        f"  Save that file as: {client_json}",
    ]


def maybe_run_cloud_files_oauth_setup(
    home: Path,
    repo_root: Path,
    *,
    dry_run: bool,
    stdin_isatty: bool | None = None,
) -> str:
    credentials_path = home / ".config" / "cloud-files" / "credentials.json"
    if credentials_path.exists():
        return "already_configured"

    client_json = home / ".config" / "cloud-files" / "client.json"
    setup_script = repo_root / "skills" / "cloud-files" / "scripts" / "setup_oauth.py"

    if dry_run:
        if client_json.exists():
            log(f"Would run cloud-files OAuth setup: {sys.executable} {setup_script}")
            return "would_run"
        for line in cloud_files_client_setup_lines(home):
            log(line)
        log("  Then re-run the installer to launch browser authorization.")
        return "needs_client_json"

    if not client_json.exists():
        for line in cloud_files_client_setup_lines(home):
            log(line)
        if stdin_isatty is None:
            stdin_isatty = sys.stdin.isatty()
        if not stdin_isatty:
            log("  Cloud-files OAuth skipped for now: client.json is still missing.")
            return "needs_client_json"
        reply = input(
            "Press Enter after saving client.json to launch browser authorization, "
            "or type 'skip' to continue without it: "
        ).strip().lower()
        if reply == "skip":
            log("  Cloud-files OAuth skipped.")
            return "skipped"
        if not client_json.exists():
            log("  Cloud-files OAuth skipped: client.json is still missing.")
            return "needs_client_json"

    log("Launching cloud-files browser authorization...")
    result = subprocess.run([sys.executable, str(setup_script)], check=False)
    if result.returncode == 0:
        return "configured"

    log(f"Warning: cloud-files OAuth setup exited {result.returncode}.")
    return "failed"


def install_ai_agent_env(home: Path, dry_run: bool) -> None:
    """Write the systemd user environment file for AI_AGENT_COMMAND_TEMPLATE.

    This tells automated skill jobs how to invoke Claude. Skipped on non-Linux
    systems where systemd user sessions don't exist.
    """
    if sys.platform == "win32":
        log("Note: skipping systemd environment setup (not supported on Windows).")
        return

    env_dir  = home / ".config" / "environment.d"
    env_file = env_dir / "20-ai-agent.conf"
    # The invoke-agent script is always at the installed skills path
    invoke_script = home / ".claude" / "skills" / "recurring-tasks" / "scripts" / "invoke-agent.sh"
    content = f"AI_AGENT_COMMAND_TEMPLATE={invoke_script} {{skill}}\n"

    if dry_run:
        log(f"Would write {env_file}")
        return

    env_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text(content)

    # Also apply to the live systemd user session if one is running
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "default.target"],
            capture_output=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["systemctl", "--user", "set-environment",
                 f"AI_AGENT_COMMAND_TEMPLATE={invoke_script} {{skill}}"],
                check=False,  # non-fatal: session may not support this
            )


def ensure_rc_block(rc_file: Path, bin_dir: Path, default_llm: str, repo_root: Path, label: str, dry_run: bool) -> None:
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

    # Strip the existing managed block (inclusive of delimiters)
    lines = original.splitlines(keepends=True)
    filtered: list[str] = []
    inside = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped == BLOCK_BEGIN:
            inside = True
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

    ensure_rc_block(system_rc, bin_dir, default_llm, repo_root, "system", dry_run)


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


def install_python_packages(dry_run: bool) -> None:
    """Ensure required Python packages are installed."""
    log("\nInstalling required Python packages...")
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
    cloud_files_remote_llm_root: str = "assistant/",
    update_system_shell_rc: bool = True,
    dry_run: bool = False,
) -> None:
    """Install or update assistant tools.

    All arguments are optional; paths default to platform home and standard
    locations. default_llm is prompted interactively when not supplied.
    """
    home = home or Path.home()

    # Script is at <repo>/skills/install-assistant-tools/scripts/install_assistant_tools.py
    script_path    = Path(__file__).resolve()
    skill_dir      = script_path.parents[1]
    repo_root      = script_path.parents[3]
    source_bin_dir = skill_dir / "bin"
    profiles_dir   = repo_root / "profiles"
    hooks_dir      = repo_root / ".githooks"

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
            reply = input("Default assistant backend [claude/codex] (default: claude): ").strip()
            default_llm = reply if reply in ("claude", "codex") else "claude"
            if reply and reply not in ("claude", "codex"):
                log(f"Invalid choice '{reply}'; defaulting to claude.")
        else:
            log("Non-interactive mode: defaulting to 'claude'. Use --default-llm to override.")
            default_llm = "claude"

    install_python_packages(dry_run)
    install_worker_dirs(repo_root, dry_run)
    install_bin_scripts(source_bin_dir, bin_dir, dry_run)
    install_profile_links(profiles_dir, codex_home, claude_home, dry_run)
    install_git_hooks(repo_root, hooks_dir, dry_run)
    remove_legacy_coder_links(source_bin_dir, profiles_dir, bin_dir, codex_home, claude_home, dry_run)
    install_ai_agent_env(home, dry_run)
    install_cloud_files_config(
        home,
        remote_llm_root=cloud_files_remote_llm_root,
        dry_run=dry_run,
    )

    # Platform-specific PATH and environment variable setup.
    if sys.platform == "win32":
        # Windows: write to the user environment registry block.
        # Shell rc files (bashrc/zshrc) don't apply here.
        log("\nUpdating Windows user environment variables...")
        update_windows_user_env(bin_dir, default_llm, repo_root, dry_run)
    else:
        # Unix: write a managed block to the user (and optionally system) shell rc.
        ensure_rc_block(shell_rc, bin_dir, default_llm, repo_root, "user", dry_run)
        maybe_ensure_system_rc_block(system_shell_rc, bin_dir, default_llm, repo_root, update_system_shell_rc, dry_run)

    if not dry_run:
        verify_install(bin_dir)

    warn_missing_commands(["tmux", "codex", "claude"])

    log("")
    log("Installed assistant tools.")
    log(f"  Bin dir:        {bin_dir}")
    log(f"  Source bin:     {source_bin_dir}")
    log(f"  Codex home:     {codex_home}")
    log(f"  Claude home:    {claude_home}")
    log(f"  Git hooks:      {hooks_dir}")
    log(f"  AI root:        {repo_root}")
    log(f"  Default LLM:    {default_llm}")
    if sys.platform == "win32":
        log("  PATH/env:       HKEY_CURRENT_USER\\Environment (registry)")
    else:
        log(f"  User shell rc:  {shell_rc}")
        if update_system_shell_rc:
            log(f"  System rc:      {system_shell_rc}")
    cloud_files_status = maybe_run_cloud_files_oauth_setup(
        home,
        repo_root,
        dry_run=dry_run,
    )
    if cloud_files_status == "already_configured":
        log("Cloud-files OAuth already configured.")
    elif cloud_files_status == "configured":
        log("Cloud-files OAuth configured.")
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
        cloud_files_remote_llm_root=args.cloud_files_remote_llm_root,
        update_system_shell_rc=not args.no_system_shell_rc,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
