#!/usr/bin/env python3
"""
Asks explicitly whether the user wants development mode (never inferred from
filesystem probes) and, if so, asks for the repo path directly rather than
deriving it from this script's own location. Plugin-mode installs use the
repo root implied by wherever this script itself is running from (the
plugin-cache checkout), which is a reasonable default there because there is
no separate "live checkout" concept to get wrong in plugin mode.

Does NOT handle connecting remotes (cloud-files/online-calendar/email-client) or
recurring-tasks automation — see SKILL.md for that conversational Phase 2,
which happens after this script exits successfully.

Run individual scripts directly for targeted repairs:
  python3 _rtx/_install_scaffold.py --help
  python3 _rtx/_config_bridge.py --help
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).parent))

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.install.context import (
    InstallationContext,
    build_development_environment,
    load_or_create_development_installation_id,
    resolve_installation_context,
)
from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.assistant_access import resolve_assistant_access_roots
from officina.install.doctor import (
    DiagnosticReport,
    diagnose_installation,
    render_diagnostic_text,
)
from officina.install.install_info import load_install_info
import officina.install.managed_runtime as managed_runtime
import officina.install.runtime_pointer as runtime_pointer
import officina.install.uv_bootstrap as uv_bootstrap

if __package__:
    from . import _config_bridge as dev_link
else:
    import _config_bridge as dev_link
if __package__:
    from . import _install_scaffold as scaffold
else:
    import _install_scaffold as scaffold
if __package__:
    from ._assistant_access_config import (
        AssistantAccessConfigError,
        reconcile_assistant_access,
    )
else:
    from _assistant_access_config import (  # noqa: E402
        AssistantAccessConfigError,
        reconcile_assistant_access,
    )
@dataclass(frozen=True)
class ApplyChoices:
    optional_module_ids: tuple[str, ...] = ()
    home: Path | None = None
    shell_rc: Path | None = None


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _is_linked_worktree(source_root: Path) -> bool:
    marker = source_root / ".git"
    if not marker.is_file():
        return False
    try:
        prefix, target = marker.read_text(encoding="utf-8").strip().split(":", 1)
    except (OSError, UnicodeError, ValueError):
        return False
    git_dir = Path(target.strip())
    return prefix == "gitdir" and git_dir.parent.name == "worktrees"


def _all_imported_officina_modules_are_current() -> bool:
    source_root = REPO_SRC.resolve()
    for name, module in tuple(sys.modules.items()):
        if name != "officina" and not name.startswith("officina."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        try:
            Path(module_file).resolve().relative_to(source_root)
        except ValueError:
            return False
    return True


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
    *, context: InstallationContext, optional_module_ids: tuple[str, ...]
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

    repo_root = context.source_root
    editable = context.mode == "development"
    info = load_install_info(repo_root)
    paths = context.paths
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
            editable=editable,
            installation_context=context,
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
    reply = input(
        "Optional module IDs to install "
        "(comma-separated, 'all' for all, blank for core only): "
    ).strip()
    if not reply:
        return []
    if reply.casefold() == "all":
        return sorted(module["id"] for module in modules)
    requested = [module_id.strip() for module_id in reply.split(",") if module_id.strip()]
    available = {module["id"] for module in modules}
    unknown = sorted(set(requested) - available)
    if unknown:
        log(f"Unknown optional module(s): {', '.join(unknown)}. Installing core only.")
        return []
    return sorted(set(requested))


def _manifest_path(context: InstallationContext) -> Path:
    return context.paths.install_state_root / "install-manifest.json"


def _record_managed_runtime_state(
    *, context: InstallationContext, manifest: scaffold.Manifest
) -> None:
    """Record exact immutable runtime artifacts published by candidate build."""
    if not context.paths.current_pointer.is_file():
        return
    manifest.record(
        "file",
        path=str(context.paths.current_pointer),
        purge_only=True,
    )
    pointer_payload = json.loads(context.paths.current_pointer.read_text(encoding="utf-8"))
    runtime_source = pointer_payload.get("runtime_source")
    if isinstance(runtime_source, str) and Path(runtime_source).is_dir():
        manifest.record("tree", path=runtime_source, purge_only=True)
    resolver_root = context.paths.runtime_root / "bootstrap" / "resolvers"
    if resolver_root.is_dir():
        manifest.record("tree", path=str(resolver_root), purge_only=True)


def apply(
    *,
    context: InstallationContext,
    choices: ApplyChoices,
    environ: Mapping[str, str],
) -> int:
    """Apply fresh install, reinstall, update, or repair to one exact context."""
    try:
        manifest = scaffold.Manifest(_manifest_path(context))
    except scaffold.InstallManifestError as exc:
        log(f"Install manifest is invalid; preserving it unchanged: {exc}")
        return 1
    manifest.bind_context(
        mode=context.mode,
        installation_id=context.installation_id,
        development_root=context.development_root,
        codex_home=context.codex_home if context.mode == "standard" else None,
        claude_home=context.claude_home if context.mode == "standard" else None,
    )
    candidate_status = _build_managed_runtime_candidate(
        context=context,
        optional_module_ids=choices.optional_module_ids,
    )
    if candidate_status:
        return candidate_status
    _record_managed_runtime_state(context=context, manifest=manifest)

    scaffold_status = scaffold.run(
        context=context,
        environ=environ,
        home=choices.home,
        shell_rc=choices.shell_rc,
        manifest=manifest,
    )
    if scaffold_status:
        return scaffold_status

    if context.mode == "development":
        dev_link.run(context=context, environ=environ, manifest=manifest)

    try:
        reconcile_assistant_access(context, manifest)
    except AssistantAccessConfigError as exc:
        log(f"Assistant access configuration failed without replacing concurrent edits: {exc}")
        return 1

    log("Stage 4/5: Verify and report")
    diagnostic_environ = dict(environ)
    prior_path = diagnostic_environ.get("PATH", "")
    diagnostic_environ["PATH"] = os.pathsep.join(
        part for part in (str(context.paths.user_bin), prior_path) if part
    )
    report = diagnose_installation(
        context=context,
        environ=diagnostic_environ,
        platform=sys.platform,
    )
    log(render_diagnostic_text(report).rstrip())
    return 0 if report.status == "healthy" else 1


def _preview_context_lines(
    *, mode: str, source_root: Path, home: Path, environ: Mapping[str, str]
) -> tuple[str, ...]:
    if mode == "standard":
        preview = resolve_installation_context(
            mode="standard",
            source_root=source_root,
            development_root=None,
            platform=sys.platform,
            home=home,
            environ=environ,
        )
        return (
            f"Mode: {preview.mode}",
            f"Source: {preview.source_root}",
            f"Data: {preview.paths.data_root}",
            f"Config: {preview.paths.config_root}",
            f"State: {preview.paths.state_root}",
            f"Runtime: {preview.paths.runtime_root}",
            f"Commands: {preview.paths.user_bin} (persisted on PATH)",
            f"Codex home: {preview.codex_home}",
            f"Claude home: {preview.claude_home}",
            *_assistant_access_preview_lines(preview),
        )
    local = source_root / ".famulus"
    selected_home = local / "home"
    preview = InstallationContext(
        mode="development",
        source_root=source_root,
        development_root=source_root,
        paths=resolve_famulus_paths(
            platform=sys.platform,
            home=selected_home,
            environ=build_development_environment(
                source_root, environ=environ, platform=sys.platform
            ),
        ),
        selected_home=selected_home,
        codex_home=local / "homes" / "codex",
        claude_home=local / "homes" / "claude",
        installation_id="preview",
    )
    return (
        "Mode: development",
        f"Source: {source_root}",
        f"Data/config/state/runtime: beneath {local}",
        f"Commands: beneath {local} (child-process PATH only)",
        f"Codex home: {local / 'homes' / 'codex'}",
        f"Claude home: {local / 'homes' / 'claude'}",
        *_assistant_access_preview_lines(preview),
        "Isolation warning: separate homes are not an OS security sandbox.",
    )


def _assistant_access_preview_lines(context: InstallationContext) -> tuple[str, ...]:
    names = (
        "assistant_logs_root",
        "recurring_config_root",
        "recurring_state_root",
        "email_triage_state_root",
        "list_manager_lock_root",
        "list_manager_cache_root",
        "llm_wakeup_root",
    )
    roots = resolve_assistant_access_roots(context)
    return (
        "Assistant access roots (managed writable):",
        *(f"  {name}: {root}" for name, root in zip(names, roots, strict=True)),
        "Security warning: recurring_config_root is writable scheduled-command authority; "
        "secrets embedded in job strings may be exposed. Use indirect credential references.",
    )


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
    optional_modules: list[str] | None = None,
    yes: bool = False,
    environ: Mapping[str, str] | None = None,
) -> int:
    home = home or Path.home()
    selected_environ = dict(os.environ if environ is None else environ)
    if non_interactive and optional_modules:
        log("Optional module selection requires an interactive confirmation.")
        return 2

    log("Stage 1/5: Choose mode")

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

    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        log(f"Selected source does not exist: {repo_root}")
        return 2
    if not dev_mode and _is_linked_worktree(repo_root):
        log("Standard installation cannot use a linked Git worktree.")
        log(f"Use --dev-mode --repo-path {repo_root}")
        return 2
    platform_name = scaffold._platform_name()
    if optional_modules is None:
        optional_modules = [] if non_interactive or platform_name is None else _prompt_optional_modules(
            manifest_path=repo_root / scaffold.RUNTIME_DEPENDENCIES_MANIFEST,
            platform_name=platform_name,
        )
    optional_module_ids = tuple(sorted(set(optional_modules)))
    mode_name = "development" if dev_mode else "standard"
    log("Stage 2/5: Confirm choices")
    for line in _preview_context_lines(
        mode=mode_name, source_root=repo_root, home=home, environ=selected_environ
    ):
        log(f"  {line}")
    if dry_run:
        log("Dry-run complete; no installation state was changed.")
        return 0
    if not yes:
        if non_interactive:
            log("Non-interactive installation requires --yes.")
            return 2
        if not _prompt_yes_no("Apply these choices?", default=False):
            log("Installation cancelled before changes.")
            return 2

    if dev_mode:
        installation_id = load_or_create_development_installation_id(
            repo_root,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
        )
        context = resolve_installation_context(
            mode="development",
            source_root=repo_root,
            development_root=repo_root,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
            installation_id=installation_id,
        )
    else:
        context = resolve_installation_context(
            mode="standard",
            source_root=repo_root,
            development_root=None,
            platform=sys.platform,
            home=home,
            environ=selected_environ,
        )
    if bin_dir is not None and Path(bin_dir).resolve(strict=False) != context.paths.user_bin.resolve(strict=False):
        log("--bin-dir must equal the selected context command directory.")
        return 2
    if codex_home is not None and Path(codex_home).resolve(strict=False) != context.codex_home.resolve(strict=False):
        log("--codex-home must equal the selected context Codex home.")
        return 2
    if claude_home is not None and Path(claude_home).resolve(strict=False) != context.claude_home.resolve(strict=False):
        log("--claude-home must equal the selected context Claude home.")
        return 2

    log("Stage 3/5: Install")
    status = apply(
        context=context,
        choices=ApplyChoices(
            optional_module_ids=optional_module_ids,
            home=home,
            shell_rc=shell_rc,
        ),
        environ=selected_environ,
    )
    if status:
        log("Installation failed; use the recovery guidance above.")
        return status
    log("Stage 5/5: Optional next steps")
    log("  connect-google connects Famulus to Google services when you choose to invoke it.")
    log("  recurring-tasks creates and manages recurring AI jobs when you choose to invoke it.")
    if context.mode == "development":
        log("  Recurring jobs are pinned to this checkout context; isolation is not an OS security sandbox.")
    return 0


class Interface(PythonArgvMachineInterface):
    prog = "phase_entry.py"

    def run(self, argv: list[str]) -> int:
        if not _all_imported_officina_modules_are_current():
            # A managed-runtime update must be built by the source being
            # installed, never by whatever Officina API happens to be active.
            # A fresh interpreter has no cached ``officina`` package, so
            # prepending REPO_SRC selects this checkout atomically for the
            # complete installer process while retaining the caller's TTY.
            child_env = os.environ.copy()
            child_env["PYTHONPATH"] = str(REPO_SRC)
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
    parser.add_argument("--yes", action="store_true", help="Confirm the displayed choices")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dev-mode", dest="dev_mode", action="store_true", default=None)
    mode.add_argument("--no-dev-mode", dest="dev_mode", action="store_false")
    parser.add_argument("--repo-path", metavar="DIR")
    parser.add_argument(
        "--optional-modules", metavar="LIST",
        help="Comma-separated optional module IDs; requires interactive confirmation.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
        optional_modules=(
            [module_id.strip() for module_id in args.optional_modules.split(",") if module_id.strip()]
            if args.optional_modules is not None else None
        ),
        yes=args.yes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
