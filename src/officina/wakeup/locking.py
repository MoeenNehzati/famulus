"""Small cross-platform advisory file-lock adapter."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockUnavailable(Exception):
    """Raised when a requested non-blocking advisory lock is already held."""


@contextmanager
def locked_file(path: Path, *, blocking: bool = True) -> Iterator[object]:
    """Open *path* and hold one exclusive advisory lock for the context."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as error:
                raise LockUnavailable(str(path)) from error
            try:
                yield handle
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle, flags)
            except BlockingIOError as error:
                raise LockUnavailable(str(path)) from error
            try:
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
