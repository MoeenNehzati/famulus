#!/usr/bin/env python3
"""
install.py — Phase-1 orchestrator: scaffold, then optionally dev-link, then
launchers.

Asks explicitly whether the user wants development mode (never inferred from
filesystem probes) and, if so, asks for the repo path directly rather than
deriving it from this script's own location. Plugin-mode installs use the
repo root implied by wherever this script itself is running from (the
plugin-cache checkout), which is a reasonable default there because there is
no separate "live checkout" concept to get wrong in plugin mode.

Does NOT handle connecting remotes (cloud-files/g-calendar/email-client) or
recurring-tasks automation — see SKILL.md for that conversational Phase 2,
which happens after this script exits successfully.

Run individual scripts directly for targeted repairs:
  python3 _rtx/_install_scaffold.py --help
  python3 _rtx/_config_bridge.py --help
  python3 _rtx/_agent_launchers.py --help
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(Path(__file__).parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.install_info import load_install_info
import officina.install.managed_runtime as managed_runtime

import _config_bridge as dev_link
import _agent_launchers as launchers
import _install_scaffold as scaffold

ALL_AGENTS = launchers.ALL_AGENTS


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _prompt_yes_no(question: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        reply = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        reply = ""
    if not reply:
        return default
    return reply in ("y", "yes")


def _prompt_repo_path() -> Path:
    while True:
        reply = input("Path to your repo checkout: ").strip()
        if reply:
            return Path(reply).expanduser()
        log("A repo path is required for development mode.")


def _prompt_agents() -> list[str]:
    log(f"Which agent launchers do you want? Available: {', '.join(ALL_AGENTS)}")
    reply = input("Comma-separated list (blank for none): ").strip()
    if not reply:
        return []
    chosen = [a.strip() for a in reply.split(",") if a.strip()]
    invalid = set(chosen) - set(ALL_AGENTS)
    if invalid:
        log(f"Ignoring unknown agent(s): {', '.join(sorted(invalid))}")
    return [a for a in chosen if a in ALL_AGENTS]


def _prompt_default_llm() -> str:
    reply = input("Default backend for launchers [claude/codex] (default: claude): ").strip().lower()
    return reply if reply in ("claude", "codex") else "claude"


def _build_managed_runtime_candidate(*, repo_root: Path, home: Path) -> int:
    """Build and activate a managed-runtime candidate release before scaffold
    runs, so the dispatcher/invoke-skill launchers scaffold.run installs have
    a real release to exec into as soon as they exist on disk.

    Returns 0 on success. On a ManagedRuntimeError (bad manifest, failed venv
    creation, failed batch install), logs the failure and returns nonzero
    without raising -- build_candidate_release itself guarantees no partial
    pointer is written in that case, so any prior current.json is left
    untouched.
    """
    platform_name = scaffold._platform_name()
    if platform_name is None:
        log("Skipping managed-runtime candidate build: unsupported platform.")
        return 0

    info = load_install_info(repo_root / "install-info.toml")
    paths = resolve_famulus_paths(platform=sys.platform, home=home)
    manifest_path = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST

    log("Building managed-runtime candidate release...")
    try:
        managed_runtime.build_candidate_release(
            runtime_root=paths.runtime_root,
            manifest_path=manifest_path,
            platform=platform_name,
            uv_bin=paths.uv_bin,
            python_version=info.managed_python,
        )
    except managed_runtime.ManagedRuntimeError as exc:
        log(f"Managed-runtime candidate build failed: {exc}")
        if not paths.uv_bin.exists():
            # Expected until a machine-local uv bootstrap step lands
            # (separately scoped) -- distinguish it from a genuine failure
            # of an already-bootstrapped uv so this doesn't read as alarming
            # on every fresh, not-yet-bootstrapped machine.
            log(
                f"  Hint: no managed uv binary found at {paths.uv_bin} -- "
                "this machine has not been bootstrapped with a managed uv yet."
            )
        return 1
    return 0


def run(
    *,
    home: Path | None = None,
    bin_dir: Path | None = None,
    shell_rc: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    dry_run: bool = False,
    non_interactive: bool = False,
    dev_mode: bool | None = None,
    repo_path: Path | None = None,
    agents: list[str] | None = None,
    default_llm: str | None = None,
) -> int:
    home = home or Path.home()

    if dev_mode is None:
        if non_interactive:
            dev_mode = False
        else:
            dev_mode = _prompt_yes_no(
                "Do you want development mode? This wires ~/.claude/~/.codex to a "
                "live repo checkout so skill/hook edits take effect immediately, "
                "instead of a static plugin install.",
                default=False,
            )

    if dev_mode:
        if repo_path is None:
            if non_interactive:
                raise SystemExit("--repo-path is required with --dev-mode in non-interactive mode")
            repo_path = _prompt_repo_path()
        repo_root = Path(repo_path)
    else:
        # Plugin mode: derive from this script's own location, same as the
        # pre-redesign behavior. <repo>/skills/install-assistant-tools/_rtx/_phase_entry.py
        repo_root = Path(__file__).resolve().parents[3]

    if dry_run:
        log("(dry-run) Would build and activate a managed-runtime candidate release.")
    else:
        candidate_status = _build_managed_runtime_candidate(repo_root=repo_root, home=home)
        if candidate_status:
            log()
            log("Installation stopped because the managed-runtime candidate build failed.")
            return candidate_status

    scaffold_status = scaffold.run(repo_root=repo_root, home=home, bin_dir=bin_dir, shell_rc=shell_rc, dry_run=dry_run)
    if scaffold_status:
        log()
        log("Installation stopped because scaffold failed.")
        return scaffold_status

    log()

    if dev_mode:
        dev_link.run(
            repo_root=repo_root, home=home,
            claude_home=claude_home, codex_home=codex_home,
            shell_rc=shell_rc, dry_run=dry_run,
        )
        log()

    if agents is None:
        agents = [] if non_interactive else _prompt_agents()

    if default_llm is None:
        default_llm = "claude" if non_interactive else _prompt_default_llm()

    launchers.run(
        repo_root=repo_root, agents=agents, home=home,
        bin_dir=bin_dir, codex_home=codex_home, claude_home=claude_home,
        shell_rc=shell_rc, default_llm=default_llm, dry_run=dry_run,
        mode="development" if dev_mode else "plugin",
        install_invoke_skill=True,
    )

    log()
    log("Installation complete.")
    if not dry_run:
        log(
            "Next: connect your remotes (cloud-files, g-calendar, email-client) "
            "and set up recurring triage/planning — ask your assistant to walk "
            "you through it."
        )
    return 0


class Interface(PythonArgvMachineInterface):
    prog = "phase_entry.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--bin-dir", metavar="DIR")
    parser.add_argument("--shell-rc", metavar="FILE")
    parser.add_argument("--codex-home", metavar="DIR")
    parser.add_argument("--claude-home", metavar="DIR")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-interactive", action="store_true",
        help="Never prompt; requires --dev-mode/--no-dev-mode and, if dev mode, --repo-path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dev-mode", dest="dev_mode", action="store_true", default=None)
    mode.add_argument("--no-dev-mode", dest="dev_mode", action="store_false")
    parser.add_argument("--repo-path", metavar="DIR")
    parser.add_argument("--agents", metavar="LIST",
        help="Comma-separated subset of: " + ",".join(ALL_AGENTS))
    parser.add_argument("--default-llm", choices=["claude", "codex"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    agents = None
    if args.agents is not None:
        agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    return run(
        home=Path(args.home) if args.home else None,
        bin_dir=Path(args.bin_dir) if args.bin_dir else None,
        shell_rc=Path(args.shell_rc) if args.shell_rc else None,
        codex_home=Path(args.codex_home) if args.codex_home else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        dry_run=args.dry_run,
        non_interactive=args.non_interactive,
        dev_mode=args.dev_mode,
        repo_path=Path(args.repo_path) if args.repo_path else None,
        agents=agents,
        default_llm=args.default_llm,
    )


if __name__ == "__main__":
    raise SystemExit(main())
