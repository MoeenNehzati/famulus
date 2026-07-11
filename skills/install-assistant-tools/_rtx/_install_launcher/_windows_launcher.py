"""Windows launcher bundle installer."""
from __future__ import annotations

from pathlib import Path

from _state_record import Manifest

from ._base_launcher import (
    DISPATCHER_WORKFLOWS,
    INVOKE_SKILL_WORKFLOWS,
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallResult,
    LauncherInstallerBase,
    log,
)


def _windows_dispatcher_content(repo_root: Path) -> str:
    repo = LauncherInstallerBase._batch_path(repo_root)
    return (
        "@echo off\n"
        "setlocal\n"
        "set \"AI=%AI%\"\n"
        f"if \"%AI%\"==\"\" set \"AI={repo}\"\n"
        "set \"PYTHONPATH=%AI%\\src;%PYTHONPATH%\"\n"
        "py -3 -m officina.dispatcher.cli %*\n"
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
    ) -> LauncherInstallResult:
        bundle = LauncherBundleSpec(
            name="dispatcher",
            workflows=DISPATCHER_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    destination=bin_dir / "dispatcher.bat",
                    mode="generate",
                    content=_windows_dispatcher_content(repo_root),
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
            required=False,
            workflows=INVOKE_SKILL_WORKFLOWS,
            files=[],
            unsupported_reason="recurring-tasks is currently systemd/Unix-only",
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
