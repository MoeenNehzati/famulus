"""Shared symlink/copy helpers used by scaffold, dev_link, and launchers.

Extracted from setup_symlinks.py / setup_tools.py, which each had their own
near-identical copy of make_link (and setup_tools.py additionally had
make_copy). One copy avoids the two drifting apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _state_record import Manifest


def log(msg: str = "") -> None:
    print(msg, flush=True)


def make_link(src: Path, dst: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Create or replace the symlink at dst pointing to src.

    Skips with a warning when src does not exist (e.g. optional repo
    directory). On platforms where symlink creation requires elevated
    privileges, reports a clear error instead of crashing. When a manifest is
    given, successful (or already-correct) links are recorded in it.
    """
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

    # Remove an existing symlink so ln -sfn semantics are preserved.
    # Never remove a real file or directory — that would be destructive.
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
        # On Windows without Developer Mode / admin rights symlink creation
        # raises PermissionError. Give a useful hint rather than a traceback.
        if sys.platform == "win32":
            log(
                f"  ERROR: could not create symlink {dst} -> {src}\n"
                f"  On Windows, symlinks require Developer Mode or administrator"
                f" privileges.\n  ({exc})"
            )
        else:
            log(f"  ERROR: could not create symlink {dst} -> {src}: {exc}")


def make_copy(src: Path, dst: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Copy src to dst instead of symlinking.

    Used for files the consuming tool WRITES BACK to (e.g. Codex records
    machine-local state — project trust levels, trusted hook hashes, with
    absolute paths — directly into its config file). A symlink would let
    those writes land in the tracked repo file, leaking machine-local
    personal paths into git. A copy keeps runtime state on the machine.

    - Skips with a warning if src does not exist.
    - Replaces an existing symlink (legacy install) with a copy.
    - Leaves an existing regular file alone: it holds machine-local state
      accumulated since install; overwriting would discard it.
    """
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dry_run:
        log(f"  Would copy: {src} -> {dst}")
        return

    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        log(f"  SKIP (exists, keeping machine-local state): {dst}")
        return

    import shutil
    shutil.copyfile(src, dst)
    log(f"  Copied: {src} -> {dst}")
    if manifest is not None:
        manifest.record("file", path=str(dst))
