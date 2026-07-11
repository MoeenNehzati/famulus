"""Shared launcher-bundle primitives for the installer-local platform layer."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fs_links import make_link
from _state_record import Manifest

LauncherFileMode = Literal["generate", "copy", "link"]
LauncherStatus = Literal["installed", "would-install", "unsupported", "skipped", "failed"]

DISPATCHER_WORKFLOWS = (
    "machine-interface dispatch",
    "SKILL.md interface invocation",
)
INVOKE_SKILL_WORKFLOWS = (
    "recurring automation",
    "systemd/cron skill invocation",
)


def log(msg: str = "") -> None:
    print(msg, flush=True)


@dataclass
class LauncherInstallResult:
    """Outcome for one launcher capability that downstream workflows rely on."""

    name: str
    required: bool
    status: LauncherStatus
    workflows: tuple[str, ...]
    path: Path | None = None
    reason: str = ""

    def blocks_install(self) -> bool:
        """Return whether this outcome leaves a required capability unavailable."""
        return self.required and self.status in {"skipped", "failed"}


@dataclass
class LauncherFileSpec:
    """One file in a launcher bundle."""

    destination: Path
    mode: LauncherFileMode
    source: Path | None = None
    content: str | None = None
    executable: bool = False


@dataclass
class LauncherBundleSpec:
    """A launcher entrypoint plus any helper files it needs."""

    name: str
    files: list[LauncherFileSpec]
    workflows: tuple[str, ...]
    required: bool = True
    unsupported_reason: str = ""


def write_generated_launcher_file(
    path: Path,
    content: str,
    *,
    executable: bool,
    dry_run: bool,
    manifest: Manifest | None,
    label: str,
) -> None:
    """Write one generated launcher file into the managed bin dir."""
    if dry_run:
        log(f"Would write {label}: {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content, encoding="utf-8")
    if executable and sys.platform != "win32":
        path.chmod(0o755)
    log(f"  Wrote {label}: {path}")
    if manifest is not None:
        manifest.record("file", path=str(path))


def install_static_launcher_file(
    src: Path,
    dst: Path,
    *,
    mode: Literal["copy", "link"],
    dry_run: bool,
    manifest: Manifest | None,
) -> None:
    """Install a repo-owned launcher helper by copying or symlinking it."""
    if mode == "link":
        make_link(src, dst, dry_run, manifest)
        return

    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dry_run:
        log(f"  Would copy launcher: {src} -> {dst}")
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        log(f"  SKIP (already exists as real path, not a symlink): {dst}")
        return

    shutil.copy2(src, dst)
    log(f"  Copied launcher: {src} -> {dst}")
    if manifest is not None:
        manifest.record("file", path=str(dst))


class LauncherInstallerBase:
    """Base class for platform-specific launcher bundle installers."""

    static_launcher_mode: Literal["copy", "link"] = "link"

    def install_bundle(
        self,
        bundle: LauncherBundleSpec,
        *,
        dry_run: bool,
        manifest: Manifest | None,
    ) -> LauncherInstallResult:
        if bundle.unsupported_reason:
            log(f"  SKIP: {bundle.name} ({bundle.unsupported_reason})")
            return LauncherInstallResult(
                name=bundle.name,
                required=bundle.required,
                status="unsupported",
                workflows=bundle.workflows,
                reason=bundle.unsupported_reason,
            )

        for spec in bundle.files:
            if spec.mode == "generate":
                if spec.content is None:
                    raise ValueError(f"generated launcher file needs content: {spec.destination}")
                write_generated_launcher_file(
                    spec.destination,
                    spec.content,
                    executable=spec.executable,
                    dry_run=dry_run,
                    manifest=manifest,
                    label=bundle.name,
                )
            elif spec.mode in {"copy", "link"}:
                if spec.source is None:
                    raise ValueError(f"static launcher file needs source: {spec.destination}")
                install_static_launcher_file(
                    spec.source,
                    spec.destination,
                    mode=spec.mode,
                    dry_run=dry_run,
                    manifest=manifest,
                )
            else:
                raise ValueError(f"unknown launcher file mode: {spec.mode}")

        return LauncherInstallResult(
            name=bundle.name,
            required=bundle.required,
            status="would-install" if dry_run else "installed",
            workflows=bundle.workflows,
            path=bundle.files[0].destination if bundle.files else None,
        )

    def install_agent_launcher_files(
        self,
        *,
        source_bin_dir: Path,
        bin_dir: Path,
        agent: str,
        dry_run: bool,
        manifest: Manifest | None,
    ) -> None:
        raise NotImplementedError

    def _agent_launcher_files(self, source_bin_dir: Path, bin_dir: Path, agent: str) -> list[LauncherFileSpec]:
        files = [
            LauncherFileSpec(
                source=source_bin_dir / agent,
                destination=bin_dir / agent,
                mode=self.static_launcher_mode,
            ),
            LauncherFileSpec(
                source=source_bin_dir / "_agent_launch.py",
                destination=bin_dir / "_agent_launch.py",
                mode=self.static_launcher_mode,
            ),
        ]
        bat = source_bin_dir / f"{agent}.bat"
        if bat.exists():
            files.append(
                LauncherFileSpec(
                    source=bat,
                    destination=bin_dir / f"{agent}.bat",
                    mode=self.static_launcher_mode,
                )
            )
        return files

    @staticmethod
    def _shell_quote_path(path: Path) -> str:
        return str(path).replace('"', '\\"')

    @staticmethod
    def _batch_path(path: Path) -> str:
        return str(path).replace('"', '""')
