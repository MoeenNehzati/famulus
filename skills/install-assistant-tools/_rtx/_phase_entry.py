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
import os
import platform
import subprocess
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.install_info import load_install_info
import officina.install.managed_runtime as managed_runtime
import officina.install.runtime_pointer as runtime_pointer
import officina.install.uv_bootstrap as uv_bootstrap

if __package__:
    from . import _config_bridge as dev_link
else:
    import _config_bridge as dev_link
if __package__:
    from . import _agent_launchers as launchers
else:
    import _agent_launchers as launchers
if __package__:
    from . import _install_scaffold as scaffold
else:
    import _install_scaffold as scaffold
if __package__:
    from . import _google_onboarding as google_onboarding
else:
    import _google_onboarding as google_onboarding

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


def _ensure_managed_uv(*, info, paths, platform_name: str) -> int:
    """Bootstrap a machine-local managed uv binary at paths.uv_bin, before
    build_candidate_release ever needs it.

    Always calls uv_bootstrap.bootstrap_uv rather than short-circuiting
    when paths.uv_bin already exists: bootstrap_uv already no-ops cheaply
    (no network call) when the existing binary's own `--version` output
    matches the pinned info.uv_version (see its `_current_version_matches`),
    and only re-downloads when it's missing or stale. Short-circuiting here
    on mere existence would permanently strand any machine with an
    already-present uv binary on whatever version it happened to have,
    silently ignoring a later bump to the pinned uv_version.

    Returns 0 on success (including the common no-op case where the
    existing binary already matches the pinned version). On an unsupported
    platform/arch (_install_scaffold.UvReleaseTargetError) or a
    UvBootstrapError (network failure, checksum mismatch,
    archive-extraction failure), logs the failure and returns nonzero
    without raising -- bootstrap_uv itself guarantees no partial/bad binary
    is left at paths.uv_bin in that case.
    """
    log(f"Ensuring managed uv {info.uv_version} is available at {paths.uv_bin}...")
    try:
        triple, archive_extension = scaffold.uv_release_target(
            platform_name=platform_name, machine=platform.machine()
        )
        uv_bootstrap.bootstrap_uv(
            uv_bin=paths.uv_bin,
            version=info.uv_version,
            triple=triple,
            archive_extension=archive_extension,
        )
    except (scaffold.UvReleaseTargetError, uv_bootstrap.UvBootstrapError) as exc:
        log(f"uv bootstrap failed: {exc}")
        return 1
    log(f"Managed uv {info.uv_version} is ready at {paths.uv_bin}.")
    return 0


def _build_managed_runtime_candidate(
    *, repo_root: Path, home: Path, optional_module_ids: tuple[str, ...]
) -> int:
    """Build and activate a managed-runtime candidate release before scaffold
    runs, so the dispatcher/invoke-skill launchers scaffold.run installs have
    a real release to exec into as soon as they exist on disk.

    Returns 0 on success. On a UvBootstrapError, ManagedRuntimeError (bad
    manifest, failed venv creation, failed batch install), or
    RuntimePointerError (e.g. a computed python_bin that doesn't exist for
    this platform), logs the failure and returns nonzero without raising --
    bootstrap_uv, build_candidate_release, and activate_release all
    guarantee no partial state is left in that case, so any prior
    current.json is left untouched. RuntimePointerError is caught
    alongside ManagedRuntimeError deliberately: it is raised by
    runtime_pointer.activate_release (called from inside
    build_candidate_release) and is NOT a subclass of ManagedRuntimeError,
    so without this second except clause it would propagate as an
    unhandled crash instead of the same clean, typed failure as every other
    managed-runtime error.
    """
    platform_name = scaffold._platform_name()
    if platform_name is None:
        log("Skipping managed-runtime candidate build: unsupported platform.")
        return 0

    info = load_install_info(repo_root)
    paths = resolve_famulus_paths(platform=sys.platform, home=home)
    manifest_path = repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST

    uv_status = _ensure_managed_uv(info=info, paths=paths, platform_name=platform_name)
    if uv_status:
        return uv_status

    log("Building managed-runtime candidate release...")
    try:
        managed_runtime.build_candidate_release(
            runtime_root=paths.runtime_root,
            manifest_path=manifest_path,
            lock_input_path=repo_root / "references" / "runtime" / "requirements-core.in",
            lock_path=repo_root / "references" / "runtime" / "requirements-core.lock",
            platform=platform_name,
            uv_bin=paths.uv_bin,
            uv_version=info.uv_version,
            python_version=info.managed_python,
            repo_root=repo_root,
            optional_module_ids=optional_module_ids,
        )
    except (managed_runtime.ManagedRuntimeError, runtime_pointer.RuntimePointerError) as exc:
        log(f"Managed-runtime candidate build failed: {exc}")
        return 1
    return 0


def _prompt_optional_modules(*, manifest_path: Path, platform_name: str) -> list[str]:
    """Present manifest-owned optional modules and return approved module IDs.

    Package-index metadata is optional at prompt time.  If the resolver cache
    has no wheel/sdist record, the prompt says so rather than guessing a size.
    """
    modules = managed_runtime.optional_runtime_modules(manifest_path, platform=platform_name)
    if not modules:
        return []
    log("Optional modules (their dependencies are installed only if selected):")
    for module in modules:
        packages = module["packages"]
        assert isinstance(packages, tuple)
        estimates = managed_runtime.package_size_estimates(
            packages,
            package_index_metadata=managed_runtime.load_cached_package_index_metadata(packages),
        )
        size_text = ", ".join(
            f"{estimate.package}: {estimate.bytes} bytes" if estimate.bytes is not None
            else f"{estimate.package}: estimate unavailable"
            for estimate in estimates
        ) or "no additional Python packages"
        log(f"- {module['id']}: {', '.join(packages) or 'no additional Python packages'} ({size_text})")
        known_total = sum(
            estimate.bytes for estimate in estimates if estimate.bytes is not None
        )
        unavailable_count = sum(estimate.bytes is None for estimate in estimates)
        if estimates:
            suffix = f"; {unavailable_count} package size(s) unavailable" if unavailable_count else ""
            log(f"  rough download estimate: {known_total} bytes{suffix}")
    reply = input("Optional module IDs to install (comma-separated, blank for core only): ").strip()
    if not reply:
        return []
    requested = [module_id.strip() for module_id in reply.split(",") if module_id.strip()]
    available = {module["id"] for module in modules}
    unknown = sorted(set(requested) - available)
    if unknown:
        log(f"Unknown optional module(s): {', '.join(unknown)}. Installing core only.")
        return []
    return sorted(set(requested))


def _run_google_onboarding_step(
    google_services: list[str] | None,
    *,
    gmail_nickname: str | None,
    bin_dir: Path | None,
    home: Path,
    dry_run: bool,
) -> None:
    """Self-contained Google onboarding step, run once core install (managed-
    runtime candidate build + scaffold) has succeeded. Never raises and
    never fails the overall install: a partial, deferred, skipped, or
    failed Google-onboarding outcome is only logged here. Connecting Google
    services is optional at install time -- the always-available fallback
    is the conversational Phase 2 flow described in this module's
    docstring, which can run at any later point via connect-google.
    """
    effective_bin_dir = bin_dir or scaffold.default_bin_dir(home=home)
    dispatcher_path = google_onboarding.dispatcher_launcher_path(effective_bin_dir)
    try:
        result = google_onboarding.run_google_onboarding(
            google_services,
            dispatcher_path=dispatcher_path,
            home=home,
            gmail_nickname=gmail_nickname,
            dry_run=dry_run,
            stdin_isatty=False,
        )
    except Exception as exc:  # noqa: BLE001 - must never abort the install
        log(f"Google onboarding: failed unexpectedly ({exc}); continuing installation.")
        return

    if result.status == "completed":
        log(f"Google onboarding: connected {', '.join(result.granted_services)}.")
    elif result.status == "partial":
        parts = []
        if result.bound_services:
            parts.append(f"bound={list(result.bound_services)}")
        if result.denied_services:
            parts.append(f"denied={list(result.denied_services)}")
        if result.deferred_services:
            parts.append(f"deferred={list(result.deferred_services)} (no email account nickname yet)")
        if result.failed_services:
            failed_desc = [f"{service} ({message})" for service, message in result.failed_services]
            parts.append(f"failed={failed_desc}")
        log(f"Google onboarding: partially connected ({', '.join(parts)}).")
    elif result.status == "failed":
        log("Google onboarding: failed; you can retry later via connect-google.")
    # "skipped" (no OAuth client configured yet, or a dry-run) and
    # "needs_selection" (no services requested) are expected, non-error
    # outcomes at install time and are intentionally not logged as
    # warnings -- Google connection remains available afterward via
    # connect-google's conversational flow.


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
    google_services: list[str] | None = None,
    gmail_nickname: str | None = None,
    optional_modules: list[str] | None = None,
) -> int:
    home = home or Path.home()
    if non_interactive and optional_modules:
        log("Optional module selection requires an interactive confirmation.")
        return 2

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

    platform_name = scaffold._platform_name()
    if optional_modules is None:
        optional_modules = [] if non_interactive or platform_name is None else _prompt_optional_modules(
            manifest_path=repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST,
            platform_name=platform_name,
        )
    optional_module_ids = tuple(sorted(set(optional_modules)))

    if dry_run:
        log("(dry-run) Would build and activate a managed-runtime candidate release.")
    else:
        candidate_status = _build_managed_runtime_candidate(
            repo_root=repo_root, home=home, optional_module_ids=optional_module_ids
        )
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

    _run_google_onboarding_step(
        google_services, gmail_nickname=gmail_nickname,
        bin_dir=bin_dir, home=home, dry_run=dry_run,
    )

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
        loaded_runtime = Path(getattr(managed_runtime, "__file__", "")).resolve()
        current_runtime = (
            REPO_SRC / "officina" / "install" / "managed_runtime.py"
        ).resolve()
        if loaded_runtime != current_runtime:
            # A managed-runtime update must be built by the source being
            # installed, never by whatever Officina API happens to be active.
            # A fresh interpreter has no cached ``officina`` package, so
            # prepending REPO_SRC selects this checkout atomically for the
            # complete installer process while retaining the caller's TTY.
            child_env = os.environ.copy()
            inherited_pythonpath = child_env.get("PYTHONPATH")
            child_env["PYTHONPATH"] = (
                str(REPO_SRC)
                if not inherited_pythonpath
                else f"{REPO_SRC}{os.pathsep}{inherited_pythonpath}"
            )
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), *argv],
                env=child_env,
                check=False,
            )
            return result.returncode
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
    parser.add_argument(
        "--optional-modules", metavar="LIST",
        help="Comma-separated optional module IDs; requires interactive confirmation.",
    )
    parser.add_argument("--agents", metavar="LIST",
        help="Comma-separated subset of: " + ",".join(ALL_AGENTS))
    parser.add_argument("--default-llm", choices=["claude", "codex"])
    # Not yet wired to any real caller (no installer wizard exists today --
    # this script always runs Google onboarding with google_services=None,
    # which reports "needs_selection" and never blocks the install). These
    # flags exist ahead of time so a future interactive wizard can pass
    # explicit choices without another CLI change.
    parser.add_argument("--google-services", metavar="LIST",
        help="Comma-separated Google services to onboard: drive,calendar,gmail")
    parser.add_argument("--gmail-nickname", metavar="NICK",
        help="Email-client account nickname to bind a granted gmail credential to")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    agents = None
    if args.agents is not None:
        agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    google_services = None
    if args.google_services is not None:
        google_services = [s.strip() for s in args.google_services.split(",") if s.strip()]
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
        google_services=google_services,
        gmail_nickname=args.gmail_nickname,
        optional_modules=(
            [module_id.strip() for module_id in args.optional_modules.split(",") if module_id.strip()]
            if args.optional_modules is not None else None
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
