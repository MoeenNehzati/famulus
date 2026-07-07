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
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_manifest import Manifest, manifest_path
from link_utils import make_copy, make_link

# Cloud-files path normalization (inlined to avoid cross-skill imports)
def normalize_llm_root(root: str) -> str:
    raw = root.strip()
    if not raw:
        return ""
    if raw.startswith("/") or "\\" in raw:
        raise ValueError(f"invalid remote_llm_root: {root}")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"invalid remote_llm_root: {root}")
        parts.append(part)
    return "/".join(parts) if parts else ""


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

GOOGLE_OAUTH_SERVICE_ORDER = ["cloud-files", "g-calendar"]

GOOGLE_OAUTH_SERVICES: dict[str, dict[str, str]] = {
    "cloud-files": {
        "label": "Google Drive (cloud-files)",
        "status_label": "Cloud-files OAuth",
        "config_dir": "cloud-files",
        "skill_dir": "cloud-files",
    },
    "g-calendar": {
        "label": "Google Calendar (g-calendar)",
        "status_label": "g-calendar OAuth",
        "config_dir": "g-calendar",
        "skill_dir": "g-calendar",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = "") -> None:
    print(msg, flush=True)


def dispatch_skill_interface(
    *,
    target_skill: str,
    script_interface: str,
    args: list[str] | None = None,
    stdin: str | bytes | None = None,
):
    try:
        from script_dispatcher import dispatch
    except ImportError:
        # First-party code is never pip-installed; it runs from the repo.
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "script_dispatcher" / "src"))
        try:
            from script_dispatcher import dispatch
        except ImportError as exc:
            raise RuntimeError(
                "script_dispatcher source not found next to this repo checkout."
            ) from exc
    return dispatch(
        caller_skill="install-assistant-tools",
        target_skill=target_skill,
        script_interface=script_interface,
        args=args or [],
        stdin=stdin,
        capture_output=True,
        check=False,
    )


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


def install_git_hooks(repo_root: Path, hooks_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
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


def install_cloud_files_config(home: Path, remote_llm_root: str, dry_run: bool, manifest: Manifest | None = None) -> None:
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
        normalized_llm_root = normalize_llm_root(remote_llm_root)
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
    if manifest is not None:
        manifest.record("config_dir", path=str(config_dir), purge_only=True)


def google_oauth_publish_guidance_lines() -> list[str]:
    return [
        '  If the app stays in Google OAuth "Testing", Google may require repeated re-authorization again after about 7 days.',
        '  If you do not want repeated re-authorization, use Google Cloud OAuth -> Audience and click "Publish app" / move it to "In production".',
    ]


def google_service_client_setup_lines(home: Path, *, service_key: str) -> list[str]:
    spec = GOOGLE_OAUTH_SERVICES[service_key]
    client_json = home / ".config" / spec["config_dir"] / "client.json"
    lines = [
        f'{spec["label"]} OAuth client setup still needed.',
        "  In Google Cloud Console, create or download an OAuth client JSON for a Desktop app.",
        f"  Save that file as: {client_json}",
    ]
    lines.extend(google_oauth_publish_guidance_lines())
    return lines


def cloud_files_client_setup_lines(home: Path) -> list[str]:
    return google_service_client_setup_lines(home, service_key="cloud-files")


def g_calendar_client_setup_lines(home: Path) -> list[str]:
    return google_service_client_setup_lines(home, service_key="g-calendar")


def maybe_run_google_oauth_setup(
    home: Path,
    repo_root: Path,
    *,
    service_key: str,
    dry_run: bool,
    stdin_isatty: bool | None = None,
) -> str:
    spec = GOOGLE_OAUTH_SERVICES[service_key]
    credentials_path = home / ".config" / spec["config_dir"] / "credentials.json"
    if credentials_path.exists():
        return "already_configured"

    client_json = home / ".config" / spec["config_dir"] / "client.json"
    setup_script = repo_root / "skills" / spec["skill_dir"] / "scripts" / "setup_oauth.py"
    setup_lines = google_service_client_setup_lines(home, service_key=service_key)

    if dry_run:
        if client_json.exists():
            log(f'Would run {spec["status_label"]}: {sys.executable} {setup_script}')
            return "would_run"
        for line in setup_lines:
            log(line)
        log("  Then re-run the installer or choose this service in the optional Google setup step to launch browser authorization.")
        return "needs_client_json"

    if not client_json.exists():
        for line in setup_lines:
            log(line)
        if stdin_isatty is None:
            stdin_isatty = sys.stdin.isatty()
        if not stdin_isatty:
            log(f'  {spec["status_label"]} skipped for now: client.json is still missing.')
            return "needs_client_json"
        reply = input(
            f'Press Enter after saving {client_json.name} to launch browser authorization for {spec["label"]}, '
            "or type 'skip' to continue without it: "
        ).strip().lower()
        if reply == "skip":
            log(f'  {spec["status_label"]} skipped.')
            return "skipped"
        if not client_json.exists():
            log(f'  {spec["status_label"]} skipped: client.json is still missing.')
            return "needs_client_json"

    log(f'Launching {spec["label"]} browser authorization...')
    result = dispatch_skill_interface(
        target_skill=spec["skill_dir"],
        script_interface="setup-oauth",
    )
    if result.returncode == 0:
        return "configured"

    log(f'Warning: {spec["status_label"]} exited {result.returncode}.')
    return "failed"


def maybe_run_cloud_files_oauth_setup(
    home: Path,
    repo_root: Path,
    *,
    dry_run: bool,
    stdin_isatty: bool | None = None,
) -> str:
    return maybe_run_google_oauth_setup(
        home,
        repo_root,
        service_key="cloud-files",
        dry_run=dry_run,
        stdin_isatty=stdin_isatty,
    )


def maybe_run_g_calendar_oauth_setup(
    home: Path,
    repo_root: Path,
    *,
    dry_run: bool,
    stdin_isatty: bool | None = None,
) -> str:
    return maybe_run_google_oauth_setup(
        home,
        repo_root,
        service_key="g-calendar",
        dry_run=dry_run,
        stdin_isatty=stdin_isatty,
    )


def choose_optional_google_services(
    pending_services: list[str],
    *,
    stdin_isatty: bool | None = None,
    input_func=input,
) -> set[str]:
    if not pending_services:
        return set()

    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()
    if not stdin_isatty:
        log("Optional Google service setup skipped in non-interactive mode.")
        return set()

    log("")
    log("Optional Google services step:")
    for service_key in pending_services:
        log(f'  - {GOOGLE_OAUTH_SERVICES[service_key]["label"]}')
    log('  Keeping an OAuth app in "Testing" may cause repeated re-authorization; publish it if you want longer-lived access.')

    # EOF while prompting means stdin is not really interactive (e.g. Windows
    # CI consoles report isatty()=True with no input attached) — treat as skip.
    try:
        if pending_services == ["cloud-files"]:
            reply = input_func("Connect Google Drive for cloud-files now? [y/N]: ").strip().lower()
            return {"cloud-files"} if reply in {"y", "yes"} else set()

        if pending_services == ["g-calendar"]:
            reply = input_func("Connect Google Calendar for g-calendar now? [y/N]: ").strip().lower()
            return {"g-calendar"} if reply in {"y", "yes"} else set()

        while True:
            reply = input_func(
                "Connect optional Google services now? [b]oth / [d]rive / [c]alendar / [s]kip [s]: "
            ).strip().lower()
            if reply in {"", "s", "skip"}:
                return set()
            if reply in {"b", "both"}:
                return set(pending_services)
            if reply in {"d", "drive"}:
                return {"cloud-files"}
            if reply in {"c", "calendar"}:
                return {"g-calendar"}
            log("Please answer with b, d, c, or s.")
    except EOFError:
        log("Optional Google service setup skipped (stdin closed).")
        return set()


def maybe_run_optional_google_oauth_setups(
    home: Path,
    repo_root: Path,
    *,
    dry_run: bool,
    stdin_isatty: bool | None = None,
    input_func=input,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    pending_services: list[str] = []

    for service_key in GOOGLE_OAUTH_SERVICE_ORDER:
        spec = GOOGLE_OAUTH_SERVICES[service_key]
        credentials_path = home / ".config" / spec["config_dir"] / "credentials.json"
        if credentials_path.exists():
            statuses[service_key] = "already_configured"
        else:
            pending_services.append(service_key)

    if not pending_services:
        return statuses

    if dry_run:
        log("")
        log("Optional Google services step:")
        log("  Would ask whether to connect Google Drive (cloud-files) and Google Calendar (g-calendar).")
        for service_key in pending_services:
            spec = GOOGLE_OAUTH_SERVICES[service_key]
            client_json = home / ".config" / spec["config_dir"] / "client.json"
            if client_json.exists():
                log(f'  {spec["label"]}: would launch browser authorization if selected.')
            else:
                for line in google_service_client_setup_lines(home, service_key=service_key):
                    log(line)
        return statuses

    chosen_services = choose_optional_google_services(
        pending_services,
        stdin_isatty=stdin_isatty,
        input_func=input_func,
    )

    for service_key in pending_services:
        if service_key not in chosen_services:
            statuses[service_key] = "skipped"
            continue
        statuses[service_key] = maybe_run_google_oauth_setup(
            home,
            repo_root,
            service_key=service_key,
            dry_run=dry_run,
            stdin_isatty=stdin_isatty,
        )

    return statuses


def install_recurring_tasks_env_script(repo_root: Path, home: Path, bin_dir: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Write recurring-tasks/scripts/env.sh with the paths needed by jobs.

    This keeps recurring-task launcher PATH bootstrapping in sync with the
    installer, so reinstall/update flows preserve access to assistant, codex,
    and claude from non-interactive service contexts.
    """
    if sys.platform == "win32":
        log("Note: skipping recurring-tasks env.sh setup (not supported on Windows).")
        return

    env_script = repo_root / "skills" / "recurring-tasks" / "scripts" / "env.sh"
    content = (
        "# Generated by install_assistant_tools.sh — do not edit manually.\n"
        "# Sourced by invoke-agent.sh to ensure assistant, claude, and codex are on PATH\n"
        "# in login-shell and systemd service contexts without requiring changes to\n"
        "# system profiles.\n"
        f'export PATH="{bin_dir}:{home / ".npm-global" / "bin"}:{home / ".local" / "bin"}:$PATH"\n'
    )

    if dry_run:
        log(f"Would write {env_script}")
        return

    env_script.parent.mkdir(parents=True, exist_ok=True)
    env_script.write_text(content)
    env_script.chmod(0o755)
    if manifest is not None:
        manifest.record("file", path=str(env_script))

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


def install_ai_agent_env(home: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Write the systemd user environment file for AI_AGENT_COMMAND_TEMPLATE.

    This tells automated skill jobs how to invoke Claude. Skipped on non-Linux
    systems where systemd user sessions don't exist.

    Safety: `systemctl --user set-environment` mutates the caller's *live*
    systemd user session — it is not scoped to `home` at all. If this
    function runs with an overridden --home (e.g. a sandboxed or ephemeral
    install for testing, as opposed to a real install onto this machine),
    blindly calling set-environment would overwrite the real session's
    AI_AGENT_COMMAND_TEMPLATE with a path inside that temporary home, which
    then breaks every scheduled job as soon as the temporary directory is
    cleaned up. So: always write the environment.d file scoped to `home`
    (harmless either way), but only touch the live systemd session when
    `home` actually resolves to the real $HOME of the user running this.
    """
    if sys.platform == "win32":
        log("Note: skipping systemd environment setup (not supported on Windows).")
        return

    env_dir  = home / ".config" / "environment.d"
    env_file = env_dir / "20-ai-agent.conf"
    # Use the invoke-skill launcher (on PATH) instead of an absolute path.
    # This avoids breaking when test installations with temporary paths
    # clobber the real systemd session's environment variable.
    content = "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}\n"

    if dry_run:
        log(f"Would write {env_file}")
        return

    env_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text(content)
    if manifest is not None:
        manifest.record("file", path=str(env_file))

    is_real_home = home.expanduser().resolve() == Path.home().resolve()
    if not is_real_home:
        log(
            f"Note: --home {home} is not this user's real $HOME "
            f"({Path.home()}) — skipping live systemd session update to avoid "
            "overwriting the real AI_AGENT_COMMAND_TEMPLATE with a path that "
            "won't outlive this install."
        )
        return

    # Also apply to the live systemd user session if one is running
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "default.target"],
            capture_output=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["systemctl", "--user", "set-environment",
                 "AI_AGENT_COMMAND_TEMPLATE=invoke-skill {skill}"],
                check=False,  # non-fatal: session may not support this
            )


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


# ── Hook installation ─────────────────────────────────────────────────────────

HOOKS_BLOCK_BEGIN = "# >>> skill-system-hooks >>>"
HOOKS_BLOCK_END   = "# <<< skill-system-hooks <<<"


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
    install_packages: bool = True,
    run_oauth_setups: bool = True,
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
    install_git_hooks(repo_root, hooks_dir, dry_run, manifest)
    remove_legacy_coder_links(source_bin_dir, profiles_dir, bin_dir, codex_home, claude_home, dry_run)
    install_claude_hooks(claude_home, repo_root, dry_run, manifest)
    install_codex_hooks(codex_home, repo_root, dry_run, manifest)
    install_recurring_tasks_env_script(repo_root, home, bin_dir, dry_run, manifest)
    install_dispatcher_launcher(repo_root, bin_dir, dry_run, manifest)
    install_invoke_skill_launcher(bin_dir, dry_run, manifest)
    install_ai_agent_env(home, dry_run, manifest)
    install_cloud_files_config(
        home,
        remote_llm_root=cloud_files_remote_llm_root,
        dry_run=dry_run,
        manifest=manifest,
    )

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
    log(f"  Git hooks:      {hooks_dir}")
    log(f"  AI root:        {repo_root}")
    log(f"  Default LLM:    {default_llm}")
    if sys.platform == "win32":
        log("  PATH/env:       HKEY_CURRENT_USER\\Environment (registry)")
    else:
        log(f"  User shell rc:  {shell_rc}")
        if update_system_shell_rc:
            log(f"  System rc:      {system_shell_rc}")
    google_oauth_statuses = {}
    if run_oauth_setups:
        google_oauth_statuses = maybe_run_optional_google_oauth_setups(
            home,
            repo_root,
            dry_run=dry_run,
        )
    if manifest is not None:
        for service_key in GOOGLE_OAUTH_SERVICE_ORDER:
            service_dir = home / ".config" / GOOGLE_OAUTH_SERVICES[service_key]["config_dir"]
            if service_dir.exists():
                manifest.record("config_dir", path=str(service_dir), purge_only=True)
        manifest.save()
    for service_key in GOOGLE_OAUTH_SERVICE_ORDER:
        status = google_oauth_statuses.get(service_key)
        if status is None:
            continue
        label = GOOGLE_OAUTH_SERVICES[service_key]["status_label"]
        if status == "already_configured":
            log(f"{label} already configured.")
        elif status == "configured":
            log(f"{label} configured.")
        elif status == "skipped":
            log(f"{label} skipped.")
        elif status == "needs_client_json":
            log(f"{label} still needs client.json.")
        elif status == "failed":
            log(f"{label} failed.")
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
