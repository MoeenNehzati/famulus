#!/usr/bin/env python3
"""
launchers.py — Install per-agent bin launchers, profiles, and worker dirs.

For each agent in --agents (assistant, collab, coauthor, tw): symlinks its
bin launcher, copies its profile .config.toml into Codex/Claude homes,
creates its worker directory, and links its Claude settings file. Also sets
ASSISTANT_DEFAULT (this subcommand's one rc-block var — PATH belongs to
scaffold.py, AI belongs to dev_link.py).

The copied .config.toml's `model_instructions_file` is rewritten to an
absolute path pointing at the repo's own agents/<agent>.md, instead of the
relative "agents/<agent>.md" Codex would otherwise resolve against
$CODEX_HOME. This means Codex agent launches work in plugin mode without
needing $CODEX_HOME/agents wired at all (that wiring is dev_link.py's
concern, not a launcher requirement) — confirmed by testing that
model_instructions_file accepts an absolute path.

No agents are preselected: pass --agents explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_manifest import Manifest, manifest_path
from link_utils import log, make_link
from rc_block import ensure_rc_vars

_MODEL_INSTRUCTIONS_RE = re.compile(r'^model_instructions_file\s*=\s*".*"$', re.MULTILINE)

ALL_AGENTS = ["assistant", "collab", "coauthor", "tw"]

# tw is a bin-dir alias for tmux-workspace; it has no separate worker dir,
# profile, or ASSISTANT_DEFAULT relevance (tmux-workspace isn't an LLM backend).
WORKER_AGENTS = ["assistant", "collab", "coauthor"]


def _toml_basic_string(value: str) -> str:
    """Return a TOML-compatible quoted string for a path-like value."""
    return json.dumps(value, ensure_ascii=False)


def install_bin_for_agent(source_bin_dir: Path, bin_dir: Path, agent: str, dry_run: bool, manifest: Manifest | None) -> None:
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)
    if agent == "tw":
        # tw is a convenience alias for tmux-workspace; link both names.
        make_link(source_bin_dir / "tmux-workspace", bin_dir / "tmux-workspace", dry_run, manifest)
        make_link(source_bin_dir / "tmux-workspace", bin_dir / "tw", dry_run, manifest)
        return
    make_link(source_bin_dir / agent, bin_dir / agent, dry_run, manifest)
    make_link(source_bin_dir / "_agent_launch.py", bin_dir / "_agent_launch.py", dry_run, manifest)
    bat = source_bin_dir / f"{agent}.bat"
    if bat.exists():
        make_link(bat, bin_dir / f"{agent}.bat", dry_run, manifest)


def install_worker_dir(repo_root: Path, agent: str, dry_run: bool) -> None:
    if agent not in WORKER_AGENTS:
        return
    wdir = repo_root / "workers" / agent
    if dry_run:
        log(f"Would create worker dir {wdir}")
    else:
        wdir.mkdir(parents=True, exist_ok=True)


def write_config_toml_with_absolute_agent_path(
    src: Path, dst: Path, agent_md_path: Path, dry_run: bool, manifest: Manifest | None = None
) -> None:
    """Copy src (a profile .config.toml) to dst, rewriting model_instructions_file
    to an absolute path pointing at agent_md_path.

    Same skip semantics as make_copy: leaves an existing regular file alone
    (machine-local state), replaces a legacy symlink with a real file.
    """
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dst.is_symlink():
        if dry_run:
            log(f"  Would replace legacy symlink with file: {dst}")
        else:
            dst.unlink()
    elif dst.exists():
        log(f"  SKIP (exists, keeping machine-local state): {dst}")
        return

    if dry_run:
        log(f"  Would write (absolute agent path): {dst}")
        return

    content = src.read_text(encoding="utf-8")
    content = _MODEL_INSTRUCTIONS_RE.sub(
        lambda _match: (
            f"model_instructions_file = {_toml_basic_string(str(agent_md_path))}"
        ),
        content,
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    log(f"  Wrote (absolute agent path): {dst}")
    if manifest is not None:
        manifest.record("file", path=str(dst))


def install_profile_for_agent(repo_root: Path, profiles_dir: Path, codex_home: Path, claude_home: Path, agent: str, dry_run: bool, manifest: Manifest | None) -> None:
    if agent not in WORKER_AGENTS:
        return
    if not profiles_dir.is_dir():
        log(f"Warning: profiles directory is missing: {profiles_dir}")
        return
    if not dry_run:
        codex_home.mkdir(parents=True, exist_ok=True)
        claude_home.mkdir(parents=True, exist_ok=True)

    config = profiles_dir / f"{agent}.config.toml"
    agent_md = repo_root / "agents" / f"{agent}.md"
    if config.exists():
        write_config_toml_with_absolute_agent_path(config, codex_home / config.name, agent_md, dry_run, manifest)
        write_config_toml_with_absolute_agent_path(config, claude_home / config.name, agent_md, dry_run, manifest)

    settings = profiles_dir / f"{agent}_claude_setting.json"
    if settings.exists():
        make_link(settings, claude_home / settings.name, dry_run, manifest)


def remove_legacy_coder_links(source_bin_dir: Path, profiles_dir: Path, bin_dir: Path, codex_home: Path, claude_home: Path, dry_run: bool) -> None:
    """Remove legacy 'coder' symlinks that point back into this repo."""
    candidates = {
        bin_dir     / "coder":             source_bin_dir / "coder",
        codex_home  / "coder.config.toml": profiles_dir / "coder.config.toml",
        claude_home / "coder.config.toml": profiles_dir / "coder.config.toml",
    }
    for legacy, expected_target in candidates.items():
        if not legacy.is_symlink():
            continue
        if legacy.resolve() == expected_target.resolve():
            if dry_run:
                log(f"Would remove legacy link {legacy}")
            else:
                legacy.unlink()


def _ensure_assistant_default_windows(default_llm: str, dry_run: bool, manifest: Manifest | None) -> None:
    if dry_run:
        log(f"  Would set ASSISTANT_DEFAULT={default_llm}")
        return
    import winreg
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        winreg.SetValueEx(key, "ASSISTANT_DEFAULT", 0, winreg.REG_SZ, default_llm)
    log(f"  Set ASSISTANT_DEFAULT={default_llm}")
    if manifest is not None:
        manifest.record("registry_env", path="ASSISTANT_DEFAULT", names=["ASSISTANT_DEFAULT"])


def verify_install(bin_dir: Path, agents: list[str]) -> bool:
    """Run --help on each installed agent command and report results.

    Only verifies the agents actually selected (unlike setup_tools.py's old
    fixed VERIFY_CMDS list) — installing a subset shouldn't report FAIL for
    agents that were never asked for.

    On Windows, tmux-workspace is skipped (tmux is not available) and .bat
    wrappers are used for assistant/collab/coauthor because extension-less
    scripts cannot be executed directly by Windows.
    """
    log("")
    log("Verifying installation...")
    ok = True
    is_windows = sys.platform == "win32"

    for agent in agents:
        name = "tw" if agent == "tw" else agent
        if is_windows and name == "tw":
            log("  SKIP: tw (tmux not available on Windows)")
            continue

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


def run(
    *,
    repo_root: Path,
    agents: list[str],
    home: Path | None = None,
    bin_dir: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    shell_rc: Path | None = None,
    default_llm: str = "claude",
    dry_run: bool = False,
    manifest: Manifest | None = None,
) -> None:
    home = home or Path.home()
    bin_dir = bin_dir or home / "Documents" / "scripts" / "bin"
    source_bin_dir = repo_root / "skills" / "install-assistant-tools" / "bin"
    profiles_dir = repo_root / "profiles"
    codex_home = codex_home or home / ".codex"
    claude_home = claude_home or home / ".claude"

    if manifest is None and not dry_run:
        manifest = Manifest(manifest_path(home))
    if dry_run:
        manifest = None

    for agent in agents:
        install_bin_for_agent(source_bin_dir, bin_dir, agent, dry_run, manifest)
        install_worker_dir(repo_root, agent, dry_run)
        install_profile_for_agent(repo_root, profiles_dir, codex_home, claude_home, agent, dry_run, manifest)

    remove_legacy_coder_links(source_bin_dir, profiles_dir, bin_dir, codex_home, claude_home, dry_run)

    if agents:
        if sys.platform == "win32":
            _ensure_assistant_default_windows(default_llm, dry_run, manifest)
        else:
            if shell_rc is None:
                detected_shell = os.environ.get("SHELL", "")
                shell_rc = home / (".zshrc" if "zsh" in detected_shell else ".bashrc")
            ensure_rc_vars(
                shell_rc,
                {"ASSISTANT_DEFAULT": f"export ASSISTANT_DEFAULT={default_llm}"},
                dry_run,
                manifest,
                label="user",
            )

    if manifest is not None:
        manifest.save()

    if not dry_run and agents:
        verify_install(bin_dir, agents)

    log("")
    log("Launchers complete.")
    log(f"  Agents installed: {', '.join(agents) if agents else '(none)'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", metavar="DIR", required=True)
    parser.add_argument("--agents", metavar="LIST", default="",
        help="Comma-separated subset of: assistant,collab,coauthor,tw (default: none)")
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--bin-dir", metavar="DIR")
    parser.add_argument("--codex-home", metavar="DIR")
    parser.add_argument("--claude-home", metavar="DIR")
    parser.add_argument("--shell-rc", metavar="FILE")
    parser.add_argument("--default-llm", choices=["claude", "codex"], default="claude")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    invalid = set(agents) - set(ALL_AGENTS)
    if invalid:
        raise SystemExit(f"Unknown agent(s): {', '.join(sorted(invalid))}. Valid: {', '.join(ALL_AGENTS)}")
    run(
        repo_root=Path(args.repo_root),
        agents=agents,
        home=Path(args.home) if args.home else None,
        bin_dir=Path(args.bin_dir) if args.bin_dir else None,
        codex_home=Path(args.codex_home) if args.codex_home else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        shell_rc=Path(args.shell_rc) if args.shell_rc else None,
        default_llm=args.default_llm,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
