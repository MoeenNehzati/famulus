"""Windows launcher bundle installer."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest
else:
    from _state_record import Manifest

from officina.common.famulus_paths import resolve_famulus_paths

from ._base_launcher import (
    DISPATCHER_WORKFLOWS,
    INVOKE_SKILL_WORKFLOWS,
    WAKEUP_COMMANDS,
    WAKEUP_WORKFLOWS,
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallResult,
    LauncherInstallerBase,
    log,
)

# Fixed, immutable location of the stable launch resolver beneath a given
# runtime_root. The file deployed there is officina.install.resolvers.launch's
# source (a dependency-free, stdlib-only script). Generated shims invoke
# this path instead of embedding a release-specific repo checkout or
# interpreter: this path does not change when the repo moves or a new
# release is activated.
_RESOLVER_RELATIVE_PATH = ("bootstrap", "resolvers", "v1", "launch.py")


class WindowsPythonNotFoundError(RuntimeError):
    """Raised when no ``python``/``py`` interpreter can be resolved on PATH."""


def _resolve_python_interpreter() -> str:
    """Resolve a concrete, absolute path to a python interpreter on PATH.

    Tries ``python`` then falls back to the ``py`` launcher, raising a clear
    error if neither is found rather than silently baking a bare,
    unqualified name into the generated dispatcher.bat that Windows can't
    validate without relying on ambient PATH resolution at run time.

    Mirrors ``_schedule_backend._windows_backend._resolve_python_interpreter``
    (recurring-tasks' equivalent fix for the same underlying problem); not
    imported directly since install-assistant-tools and recurring-tasks keep
    their ``_rtx`` internals independent of one another.
    """
    resolved = shutil.which("python") or shutil.which("py")
    if not resolved:
        raise WindowsPythonNotFoundError(
            "could not resolve a python interpreter on PATH (tried 'python' and 'py'); "
            "the generated dispatcher.bat requires a concrete, validatable interpreter path"
        )
    return resolved


def _resolver_path(*, home: Path | None = None) -> Path:
    """Return the fixed resolver path beneath this host's runtime_root."""
    home = home or Path.home()
    runtime_root = resolve_famulus_paths(platform=sys.platform, home=home).runtime_root
    return runtime_root.joinpath(*_RESOLVER_RELATIVE_PATH)


def _windows_module_content(module: str, *, home: Path | None = None) -> str:
    """Render one batch shim that delegates a module to the active release.

    Windows needs a concrete interpreter to start the resolver's Python source.
    The resolved interpreter is only the stable bootstrap interpreter; the
    resolver still selects and enters the active managed release itself.
    """
    resolver = LauncherInstallerBase._batch_path(_resolver_path(home=home))
    interpreter = LauncherInstallerBase._batch_path(Path(_resolve_python_interpreter()))
    return (
        "@echo off\n"
        "setlocal\n"
        f'"{interpreter}" "{resolver}" -m {module} %*\n'
    )


def _windows_dispatcher_content(repo_root: Path, *, home: Path | None = None) -> str:
    """Preserve the established dispatcher renderer API for external tests."""
    return _windows_module_content("officina.dispatcher.cli", home=home)


def _windows_invoke_skill_content() -> str:
    return (
        "@echo off\n"
        "setlocal\n"
        "if \"%~1\"==\"\" (\n"
        "  echo Usage: invoke-skill ^<skill-name^> 1>&2\n"
        "  exit /b 2\n"
        ")\n"
        "if not \"%~2\"==\"\" (\n"
        "  echo Usage: invoke-skill ^<skill-name^> 1>&2\n"
        "  exit /b 2\n"
        ")\n"
        "set \"SKILL=%~1\"\n"
        "if \"%ASSISTANT_DEFAULT%\"==\"\" set \"ASSISTANT_DEFAULT=claude\"\n"
        "if /I \"%ASSISTANT_DEFAULT%\"==\"claude\" (\n"
        "  assistant --local --claude --permission-mode bypassPermissions -p \"/%SKILL%\"\n"
        "  exit /b %ERRORLEVEL%\n"
        ")\n"
        "if /I \"%ASSISTANT_DEFAULT%\"==\"codex\" (\n"
        "  set \"CODEX_SKILL=$%SKILL%\"\n"
        "  assistant --local --codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \"%CODEX_SKILL%\"\n"
        "  exit /b %ERRORLEVEL%\n"
        ")\n"
        "echo Unknown ASSISTANT_DEFAULT backend: %ASSISTANT_DEFAULT% 1>&2\n"
        "exit /b 2\n"
    )


class WindowsLauncherInstaller(LauncherInstallerBase):
    """Install launcher bundles on Windows without relying on symlink support."""

    static_launcher_mode = "copy"

    def install_dispatcher_launcher(
        self,
        repo_root: Path,
        bin_dir: Path,
        dry_run: bool,
        manifest: Manifest | None = None,
        *,
        home: Path | None = None,
    ) -> LauncherInstallResult:
        bundle = LauncherBundleSpec(
            name="dispatcher",
            workflows=DISPATCHER_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / "dispatcher.bat",
                    mode="generate",
                    content=_windows_dispatcher_content(repo_root, home=home),
                )
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)

    def install_invoke_skill_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        manifest: Manifest | None = None,
    ) -> LauncherInstallResult:
        bundle = LauncherBundleSpec(
            name="invoke-skill",
            workflows=INVOKE_SKILL_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / "invoke-skill.bat",
                    mode="generate",
                    content=_windows_invoke_skill_content(),
                )
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)

    def install_wakeup_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        manifest: Manifest | None = None,
        *,
        home: Path | None = None,
    ) -> LauncherInstallResult:
        """Install both public wakeup names as resolver-backed batch shims."""
        content = _windows_module_content("officina.wakeup.cli", home=home)
        bundle = LauncherBundleSpec(
            name="llm-wakeup",
            workflows=WAKEUP_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / f"{command}.bat",
                    mode="generate",
                    content=content,
                )
                for command in WAKEUP_COMMANDS
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)

    def install_agent_launcher_files(
        self,
        *,
        source_bin_dir: Path,
        bin_dir: Path,
        agent: str,
        dry_run: bool,
        manifest: Manifest | None,
    ) -> None:
        if agent == "tw":
            log("  SKIP: tw (tmux not available on Windows)")
            return

        bundle = LauncherBundleSpec(
            name=agent,
            required=False,
            workflows=("agent launcher",),
            files=self._agent_launcher_files(source_bin_dir, bin_dir, agent),
        )
        self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)
