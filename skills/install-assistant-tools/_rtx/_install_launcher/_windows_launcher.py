"""Windows launcher bundle installer."""
from __future__ import annotations

import shutil
import sys
import os
from pathlib import Path
from typing import Mapping

if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest
else:
    from _state_record import Manifest

from officina.common.famulus_paths import resolve_famulus_paths

from officina.common.command_files import (
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallResult,
    LauncherInstallerBase,
    log,
)
from . import (
    DISPATCHER_WORKFLOWS,
    INVOKE_SKILL_WORKFLOWS,
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
    """Resolve an absolute Python interpreter path for generated launchers."""
    resolved = shutil.which("python") or shutil.which("py")
    if not resolved:
        raise WindowsPythonNotFoundError(
            "could not resolve a python interpreter on PATH (tried 'python' and 'py'); "
            "the generated dispatcher.bat requires a concrete, validatable interpreter path"
        )
    return resolved


def _resolver_path(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
) -> Path:
    """Return the fixed resolver path beneath this host's runtime_root."""
    home = home or Path.home()
    if runtime_root is None:
        runtime_root = resolve_famulus_paths(
            platform=sys.platform,
            home=home,
            environ=os.environ if environ is None else environ,
        ).runtime_root
    return runtime_root.joinpath(*_RESOLVER_RELATIVE_PATH)


def _windows_module_content(
    module: str,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    fixed_args: tuple[str, ...] = (),
    help_name: str | None = None,
    runtime_root: Path | None = None,
) -> str:
    """Render one batch shim that delegates a module to the active release.

    Windows needs a concrete interpreter to start the resolver's Python source.
    The resolved interpreter is only the stable bootstrap interpreter; the
    resolver still selects and enters the active managed release itself.
    """
    resolver = _batch_path(
        _resolver_path(home=home, environ=environ, runtime_root=runtime_root)
    )
    interpreter = _batch_path(Path(_resolve_python_interpreter()))
    rendered_args = " ".join(fixed_args)
    if rendered_args:
        rendered_args += " "
    local_help = (
        f'if /I "%~1"=="--help" (\n'
        f"  echo Usage: {help_name} [--local] [--claude^|--codex] [args...]\n"
        "  exit /b 0\n"
        ")\n"
        f'if /I "%~1"=="-h" (\n'
        f"  echo Usage: {help_name} [--local] [--claude^|--codex] [args...]\n"
        "  exit /b 0\n"
        ")\n"
        if help_name is not None
        else ""
    )
    return (
        "@echo off\n"
        "setlocal\n"
        f"{local_help}"
        f'"{interpreter}" "{resolver}" -m {module} {rendered_args}%*\n'
    )


def _batch_path(path: Path) -> str:
    return str(path).replace('"', '""')


def _windows_dispatcher_content(
    repo_root: Path, *, home: Path | None = None, runtime_root: Path | None = None
) -> str:
    """Preserve the established dispatcher renderer API for external tests."""
    return _windows_module_content("officina.dispatcher.cli", home=home, runtime_root=runtime_root)


def _windows_invoke_skill_content(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
) -> str:
    return _windows_module_content(
        "officina.launchers.agent",
        home=home,
        environ=environ,
        runtime_root=runtime_root,
        fixed_args=("--invoke-skill",),
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
        runtime_root: Path | None = None,
    ) -> LauncherInstallResult:
        bundle = LauncherBundleSpec(
            name="dispatcher",
            workflows=DISPATCHER_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / "dispatcher.bat",
                    mode="generate",
                    content=_windows_dispatcher_content(repo_root, home=home, runtime_root=runtime_root),
                )
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)

    def install_invoke_skill_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        manifest: Manifest | None = None,
        *,
        home: Path | None = None,
        runtime_root: Path | None = None,
    ) -> LauncherInstallResult:
        bundle = LauncherBundleSpec(
            name="invoke-skill",
            workflows=INVOKE_SKILL_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / "invoke-skill.bat",
                    mode="generate",
                    content=_windows_invoke_skill_content(home=home, runtime_root=runtime_root),
                ),
                LauncherFileSpec(
                    destination=bin_dir / "background_run.bat",
                    mode="generate",
                    content=_windows_module_content(
                        "officina.launchers.agent",
                        home=home,
                        runtime_root=runtime_root,
                        fixed_args=("--agent", "background_run"),
                    ),
                ),
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, manifest=manifest)
