"""Fail-closed atomic writes confined to an allowed directory tree."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


_CAPABILITY_ERROR = "secure directory-relative replacement is unavailable"
_DIR_FD_OPERATIONS = (os.open, os.stat, os.unlink, os.link, os.rename)
_NOFOLLOW_OPERATIONS = (os.stat, os.link)


class AtomicWriteError(OSError):
    pass


def _require_secure_operations() -> None:
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", set())
    required_functions = ("fchmod", "fsync", "link", "replace", "stat", "unlink")
    if (
        os.name != "posix"
        or not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(operation not in supports_dir_fd for operation in _DIR_FD_OPERATIONS)
        or any(operation not in supports_follow_symlinks for operation in _NOFOLLOW_OPERATIONS)
        or any(not hasattr(os, name) for name in required_functions)
    ):
        raise AtomicWriteError(_CAPABILITY_ERROR)


def _secure_open(
    path: str | Path,
    flags: int,
    mode: int = 0o777,
    *,
    dir_fd: int | None = None,
) -> int:
    try:
        return os.open(path, flags, mode, dir_fd=dir_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_stat(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_fchmod(descriptor: int, mode: int) -> None:
    try:
        os.fchmod(descriptor, mode)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_replace(parent_fd: int, source: str, destination: str) -> None:
    try:
        os.replace(source, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_link(parent_fd: int, source: str, destination: str) -> None:
    try:
        os.link(
            source,
            destination,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_unlink(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _open_parent(path: Path, allowed_root: Path) -> tuple[int, str]:
    _require_secure_operations()
    destination = Path(path).absolute()
    root = Path(allowed_root).absolute()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = _secure_open(root, flags)
    except AtomicWriteError:
        raise
    except OSError as exc:
        raise AtomicWriteError(f"cannot securely open allowed root: {allowed_root}") from exc

    try:
        for part in relative.parts[:-1]:
            try:
                next_fd = _secure_open(part, flags, dir_fd=directory_fd)
            except AtomicWriteError:
                raise
            except OSError as exc:
                raise AtomicWriteError(f"cannot securely open destination parent: {path}") from exc
            previous_fd = directory_fd
            directory_fd = next_fd
            os.close(previous_fd)
        return directory_fd, relative.parts[-1]
    except BaseException:
        try:
            os.close(directory_fd)
        except BaseException:
            pass
        raise


def _reject_unsafe_final(parent_fd: int, name: str) -> bool:
    try:
        entry = _secure_stat(parent_fd, name)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(entry.st_mode):
        raise AtomicWriteError(f"destination is a symbolic link: {name}")
    if not stat.S_ISREG(entry.st_mode):
        raise AtomicWriteError(f"destination is not a regular file: {name}")
    return True


def _open_temp(parent_fd: int, temp_name: str, mode: int) -> int:
    descriptor = _secure_open(
        temp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=parent_fd,
    )
    try:
        _secure_fchmod(descriptor, mode)
    except BaseException:
        try:
            os.close(descriptor)
        except BaseException:
            pass
        try:
            _unlink_if_present(parent_fd, temp_name)
        except BaseException:
            pass
        raise
    return descriptor


def _write_and_sync(descriptor: int, data: bytes) -> None:
    try:
        handle = os.fdopen(descriptor, "wb", closefd=True)
    except BaseException as primary_error:
        cleanup_error: BaseException | None = None
        try:
            os.close(descriptor)
        except BaseException as exc:
            cleanup_error = exc
        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error

    primary_error = None
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    except BaseException as exc:
        primary_error = exc

    cleanup_error = None
    try:
        handle.close()
    except BaseException as exc:
        cleanup_error = exc

    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def _unlink_if_present(parent_fd: int, name: str) -> None:
    try:
        _secure_unlink(parent_fd, name)
    except FileNotFoundError:
        pass


def _cleanup_write(
    parent_fd: int,
    temp_name: str,
    temp_created: bool,
) -> BaseException | None:
    cleanup_error: BaseException | None = None
    if temp_created:
        try:
            _unlink_if_present(parent_fd, temp_name)
        except BaseException as exc:
            cleanup_error = exc
    try:
        os.close(parent_fd)
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
    return cleanup_error


def _cleanup_read(descriptor: int, parent_fd: int) -> BaseException | None:
    cleanup_error: BaseException | None = None
    for current in (descriptor, parent_fd):
        if current < 0:
            continue
        try:
            os.close(current)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
    return cleanup_error


def read_regular_file_bytes(path: Path, *, allowed_root: Path) -> bytes:
    """Read one regular file through a confined, no-follow descriptor walk."""

    parent_fd, name = _open_parent(path, allowed_root)
    descriptor = -1
    failure: BaseException | None = None
    try:
        flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = _secure_open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno == getattr(os, "ELOOP", 40):
                raise AtomicWriteError(f"source is a symbolic link: {name}") from exc
            raise AtomicWriteError(f"cannot securely open source file: {path}") from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError(f"source is not a regular file: {name}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error = _cleanup_read(descriptor, parent_fd)
        if failure is None and cleanup_error is not None:
            raise cleanup_error


def atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> None:
    """Atomically replace a regular file through a securely opened parent."""

    parent_fd, name = _open_parent(path, allowed_root)
    temp_name = f".{name}.tmp-{secrets.token_hex(8)}"
    temp_created = False
    failure: BaseException | None = None
    try:
        _reject_unsafe_final(parent_fd, name)
        descriptor = _open_temp(parent_fd, temp_name, mode)
        temp_created = True
        _write_and_sync(descriptor, data)
        _reject_unsafe_final(parent_fd, name)
        _secure_replace(parent_fd, temp_name, name)
        temp_created = False
        os.fsync(parent_fd)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error = _cleanup_write(parent_fd, temp_name, temp_created)
        if failure is None and cleanup_error is not None:
            raise cleanup_error


def atomic_create_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> bool:
    """Atomically create a file without ever replacing an existing entry."""

    parent_fd, name = _open_parent(path, allowed_root)
    temp_name = f".{name}.tmp-{secrets.token_hex(8)}"
    temp_created = False
    failure: BaseException | None = None
    try:
        if _reject_unsafe_final(parent_fd, name):
            return False
        descriptor = _open_temp(parent_fd, temp_name, mode)
        temp_created = True
        _write_and_sync(descriptor, data)
        try:
            _secure_link(parent_fd, temp_name, name)
        except FileExistsError:
            _reject_unsafe_final(parent_fd, name)
            return False
        _unlink_if_present(parent_fd, temp_name)
        temp_created = False
        os.fsync(parent_fd)
        return True
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error = _cleanup_write(parent_fd, temp_name, temp_created)
        if failure is None and cleanup_error is not None:
            raise cleanup_error
