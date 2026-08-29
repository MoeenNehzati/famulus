"""Shared symlink/copy helpers used by scaffold, dev_link, and launchers.

Extracted from setup_symlinks.py / setup_tools.py, which each had their own
near-identical copy of make_link (and setup_tools.py additionally had
make_copy). One copy avoids the two drifting apart.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from officina.common.famulus_paths import resolve_famulus_paths
from officina.common.command_files import make_link as _make_link

if __package__:
    from ._state_record import Manifest
else:
    from _state_record import Manifest


def log(msg: str = "") -> None:
    print(msg, flush=True)


def default_bin_dir(*, home: Path) -> Path:
    """Platform-correct default launcher bin dir.

    Delegates to FamulusPaths.user_bin instead of the old
    ~/Documents/_rtx/bin default, which was wrong on every platform.
    """
    return resolve_famulus_paths(
        platform=sys.platform, home=home, environ=os.environ
    ).user_bin


def make_link(src: Path, dst: Path, dry_run: bool, manifest: Manifest | None = None) -> None:
    """Create a link with feature-local Windows remediation guidance."""
    if sys.platform == "win32":
        def on_error(exc: OSError) -> None:
            log(
                f"  ERROR: could not create symlink {dst} -> {src}\n"
                f"  On Windows, symlinks require Developer Mode or administrator"
                f" privileges.\n  ({exc})"
            )
    else:
        on_error = None
    _make_link(src, dst, dry_run, manifest, on_error=on_error)


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
