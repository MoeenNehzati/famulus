"""Neutral cross-platform command-file installation primitives."""
from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

LauncherFileMode = Literal["generate", "copy", "link"]
LauncherStatus = Literal["installed", "would-install", "unsupported", "skipped", "failed"]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def make_link(
    src: Path,
    dst: Path,
    dry_run: bool,
    manifest: Any | None = None,
    *,
    on_error: Callable[[OSError], None] | None = None,
) -> None:
    """Create or replace a command-file symlink without replacing a real path."""
    def record() -> None:
        if manifest is not None:
            manifest.record("symlink", path=str(dst), target=str(src))

    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dst.is_symlink():
        try:
            if dst.resolve() == src.resolve():
                log(f"  OK (already linked): {dst} -> {src}")
                record()
                return
        except OSError:
            pass

    if dry_run:
        log(f"  Would link: {dst} -> {src}")
        return

    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        log(f"  SKIP (already exists as real path, not a symlink): {dst}")
        return

    try:
        dst.symlink_to(src)
        log(f"  Linked: {dst} -> {src}")
        record()
    except OSError as exc:
        if on_error is not None:
            on_error(exc)
        else:
            log(f"  ERROR: could not create symlink {dst} -> {src}: {exc}")


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
    manifest: Any | None,
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
    if executable:
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
    manifest: Any | None,
) -> None:
    """Install caller-selected static command content by copying or linking it."""
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
        manifest: Any | None,
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


CommandInstallResult = LauncherInstallResult
CommandFileSpec = LauncherFileSpec
CommandBundleSpec = LauncherBundleSpec
CommandFileInstaller = LauncherInstallerBase
write_generated_command_file = write_generated_launcher_file
install_static_command_file = install_static_launcher_file


@dataclass(frozen=True)
class CommandFileSnapshot:
    """Exact restorable state for one feature-owned command path."""

    content: bytes | None
    mode: int | None
    symlink_target: str | None


def snapshot_command_file(path: Path) -> CommandFileSnapshot:
    """Capture absence, regular-file bytes/mode, or symlink identity."""

    if path.is_symlink():
        return CommandFileSnapshot(None, None, str(path.readlink()))
    if not path.exists():
        return CommandFileSnapshot(None, None, None)
    return CommandFileSnapshot(path.read_bytes(), stat.S_IMODE(path.stat().st_mode), None)


def restore_command_file(path: Path, snapshot: CommandFileSnapshot) -> None:
    """Restore a command path exactly after a failed feature transaction."""

    if path.is_symlink() or path.exists():
        path.unlink()
    if snapshot.symlink_target is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(snapshot.symlink_target)
    elif snapshot.content is not None and snapshot.mode is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(snapshot.content)
        path.chmod(snapshot.mode)
