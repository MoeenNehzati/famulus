"""Native, home-scoped serialization for Famulus installer mutations.

The lock file is a persistent rendezvous point.  Ownership belongs to the
kernel lock held on one open descriptor, not to file contents or a recorded
process identifier, so process death releases ownership without stale-file
inspection or lock-file deletion.
"""
from __future__ import annotations

import errno
import os
import stat
import sys
import time
from pathlib import Path
from types import TracebackType

from officina.common.atomic_files import (
    AtomicWriteError,
    _open_parent,
    ensure_secure_directory,
)
from officina.common.famulus_paths import resolve_famulus_paths

_LOCK_POLL_SECONDS = 0.05
class InstallBusyError(RuntimeError):
    """Raised when another home-scoped installer operation owns the lock."""

    code = "install_busy"

    def __init__(self, path: Path, timeout_seconds: float) -> None:
        super().__init__(
            f"install_busy: another installer operation holds {path} "
            f"after {timeout_seconds:g} seconds"
        )
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds


def _open_windows_confined_lock(path: Path, state_root: Path) -> int:
    """Open the leaf relative to a retained, validated native root chain."""
    import msvcrt

    from officina.common.atomic_files import (
        _WIN_MUTATE_ACCESS,
        _windows_close_chain,
        _windows_open_parent,
        _windows_open_validated,
    )

    handles, parts = _windows_open_parent(path, state_root)
    try:
        handle, _information = _windows_open_validated(
            handles[-1],
            parts[-1],
            access=_WIN_MUTATE_ACCESS,
            disposition=3,
            options=0x40,
            directory=False,
        )
        try:
            descriptor = msvcrt.open_osfhandle(
                handle, os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
        except BaseException:
            from officina.common.atomic_files import _windows_close_handle

            _windows_close_handle(handle)
            raise
        os.set_inheritable(descriptor, False)
        return descriptor
    finally:
        _windows_close_chain(handles)


def _open_confined_lock(path: Path, state_root: Path) -> int:
    """Open a regular lock below one explicit, securely walked state root."""
    path = Path(path).absolute()
    state_root = Path(state_root).absolute()
    try:
        relative = path.relative_to(state_root)
    except ValueError as exc:
        raise ValueError(f"installer lock is outside state_root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"installer lock is outside state_root: {path}")
    ensure_secure_directory(state_root)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if os.name == "posix":
        parent_descriptor, leaf = _open_parent(path, state_root)
        try:
            descriptor = os.open(leaf, flags, 0o600, dir_fd=parent_descriptor)
        finally:
            os.close(parent_descriptor)
    elif os.name == "nt":
        descriptor = _open_windows_confined_lock(path, state_root)
    else:
        raise AtomicWriteError("secure installer locking is unavailable")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"installer lock is not a regular file: {path}")
        if os.name == "nt" and metadata.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_platform_lock(
    descriptor: int, timeout_seconds: float, *, path: Path
) -> None:
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            busy_errors = {errno.EACCES, errno.EAGAIN}
            if os.name == "nt":
                busy_errors.add(errno.EDEADLK)
            if exc.errno not in busy_errors:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InstallBusyError(path, timeout_seconds) from exc
            time.sleep(min(_LOCK_POLL_SECONDS, remaining))


def _release_platform_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


class InstallLock:
    """Hold one native exclusive lock for a complete installer operation."""

    def __init__(
        self,
        path: Path,
        timeout_seconds: float = 30.0,
        *,
        state_root: Path,
    ) -> None:
        self.path = Path(path)
        self.state_root = Path(state_root)
        self.timeout_seconds = float(timeout_seconds)
        self._descriptor: int | None = None

    @classmethod
    def for_home(
        cls, home: Path, timeout_seconds: float = 30.0
    ) -> "InstallLock":
        """Derive the one canonical rendezvous path for a selected home."""
        root = resolve_famulus_paths(
            platform=sys.platform, home=Path(home).absolute()
        ).install_state_root
        return cls(
            root / "operation.lock",
            timeout_seconds=timeout_seconds,
            state_root=root,
        )

    def __enter__(self) -> "InstallLock":
        if self._descriptor is not None:
            raise RuntimeError("InstallLock instances cannot be entered twice")
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        descriptor = _open_confined_lock(self.path, self.state_root)
        try:
            _acquire_platform_lock(
                descriptor, self.timeout_seconds, path=self.path
            )
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            _release_platform_lock(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["InstallBusyError", "InstallLock"]
