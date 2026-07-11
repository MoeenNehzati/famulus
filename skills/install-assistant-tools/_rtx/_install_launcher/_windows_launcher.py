"""Windows launcher bundle installer."""
from __future__ import annotations

import sys
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
    python = LauncherInstallerBase._batch_path(Path(sys.executable))
    return (
        "@echo off\n"
        "setlocal\n"
        "set \"AI=%AI%\"\n"
        f"if \"%AI%\"==\"\" set \"AI={repo}\"\n"
        "set \"PYTHONPATH=%AI%\\src;%PYTHONPATH%\"\n"
        f"\"{python}\" -m officina.dispatcher.cli %*\n"
    )


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
