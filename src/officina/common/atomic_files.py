"""Fail-closed atomic writes confined to an allowed directory tree."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
from enum import Enum
import os
import secrets
import stat
from pathlib import Path
from typing import Callable


_CAPABILITY_ERROR = "secure directory-relative replacement is unavailable"
_TRACKED_CREATE_ERROR = "tracked file creation failed"
_UNCONDITIONAL_APPEND = object()
_DIR_FD_OPERATIONS = (os.open, os.stat, os.unlink, os.link, os.rename, os.mkdir)
_NOFOLLOW_OPERATIONS = (os.stat, os.link)


# Fixed-width native NT ABI types are intentional.  In particular, HANDLE and
# ULONG_PTR remain pointer-width while DWORD/ULONG stay 32-bit; using host
# ``ctypes.wintypes`` while testing on a non-native 64-bit host can obscure
# precisely the truncation this owner must prevent.
_WinHandle = ctypes.c_void_p
_WinUlongPtr = (
    ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
)


class _WinUnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_uint16), ("MaximumLength", ctypes.c_uint16),
                ("Buffer", ctypes.POINTER(ctypes.c_uint16))]


class _WinObjectAttributes(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_uint32), ("RootDirectory", _WinHandle),
                ("ObjectName", ctypes.POINTER(_WinUnicodeString)),
                ("Attributes", ctypes.c_uint32),
                ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p)]


class _WinIoStatusValue(ctypes.Union):
    _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]


class _WinIoStatusBlock(ctypes.Structure):
    _anonymous_ = ("Value",)
    _fields_ = [("Value", _WinIoStatusValue), ("Information", _WinUlongPtr)]


class _WinFileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]


class _WinFileIdInfo(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_uint64),
                ("FileId", ctypes.c_ubyte * 16)]


class _WinOverlappedOffsets(ctypes.Structure):
    _fields_ = [("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32)]


class _WinOverlappedValue(ctypes.Union):
    _fields_ = [("Offsets", _WinOverlappedOffsets), ("Pointer", ctypes.c_void_p)]


class _WinOverlapped(ctypes.Structure):
    _anonymous_ = ("Value",)
    _fields_ = [("Internal", _WinUlongPtr), ("InternalHigh", _WinUlongPtr),
                ("Value", _WinOverlappedValue), ("hEvent", _WinHandle)]


class _WinSecurityDescriptor(ctypes.Structure):
    _fields_ = [("Revision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
                ("Control", ctypes.c_uint16), ("Owner", ctypes.c_void_p),
                ("Group", ctypes.c_void_p), ("Sacl", ctypes.c_void_p),
                ("Dacl", ctypes.c_void_p)]


class _WinAclSizeInformation(ctypes.Structure):
    _fields_ = [("AceCount", ctypes.c_uint32),
                ("AclBytesInUse", ctypes.c_uint32),
                ("AclBytesFree", ctypes.c_uint32)]


class _WinAceHeader(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                ("AceSize", ctypes.c_uint16)]


class _WinAccessAllowedAce(ctypes.Structure):
    _fields_ = [("Header", _WinAceHeader), ("Mask", ctypes.c_uint32),
                ("SidStart", ctypes.c_uint32)]


class _WinSidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]


class _WinTokenUser(ctypes.Structure):
    _fields_ = [("User", _WinSidAndAttributes)]


class _WinFileDispositionInformation(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _WinFileRenameInfo(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("RootDirectory", _WinHandle),
                ("FileNameLength", ctypes.c_uint32),
                ("FileName", ctypes.c_uint16 * 1)]


def _windows_component_utf16(name: str) -> bytes:
    """Validate one relative component and return its bounded UTF-16 form."""

    if (
        name in {"", ".", ".."}
        or ":" in name
        or "\x00" in name
        or name.rstrip(" .") != name
    ):
        raise AtomicWriteError(f"invalid relative native path component: {name!r}")
    try:
        encoded = name.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise AtomicWriteError(
            f"invalid relative native path component: {name!r}"
        ) from exc
    if len(encoded) > 0xFFFC:
        raise AtomicWriteError(
            f"relative native path component is too long: {name!r}"
        )
    return encoded


def _windows_file_rename_info(
    name: str, parent_handle: int, *, replace: bool = False
) -> _WinFileRenameInfo:
    """Build the shared FILE_RENAME_INFO/EX variable-length buffer."""
    encoded = _windows_component_utf16(name)
    size = ctypes.sizeof(_WinFileRenameInfo) + len(encoded)
    backing = ctypes.create_string_buffer(size)
    information = ctypes.cast(
        backing, ctypes.POINTER(_WinFileRenameInfo)
    ).contents
    information.Flags = int(replace)
    information.RootDirectory = parent_handle
    information.FileNameLength = len(encoded)
    if encoded:
        ctypes.memmove(
            ctypes.addressof(information) + _WinFileRenameInfo.FileName.offset,
            encoded,
            len(encoded),
        )
    information._backing = backing
    information._used_size = size
    return information


class AtomicWriteError(OSError):
    pass


@dataclass(frozen=True)
class ConfinedFileIdentity:
    """Stable native identity for one confined regular file."""

    platform: str
    volume: int
    file_id: int | bytes


@dataclass(frozen=True)
class ConfinedRegularFile:
    """One regular file read relative to a retained directory handle."""

    name: str
    data: bytes
    identity: ConfinedFileIdentity


class TrackedFileCreation:
    """Own the retained handles needed to clean up one exact created file."""

    def __init__(
        self,
        identity: ConfinedFileIdentity,
        *,
        remove: Callable[[], None],
        release: Callable[[], None],
    ) -> None:
        self.identity = identity
        self._remove = remove
        self._release = release
        self._closed = False

    def remove(self) -> None:
        """Remove only the exact created file, then release retained handles."""

        if self._closed:
            raise AtomicWriteError("tracked file creation is already closed")
        try:
            self._remove()
        finally:
            self._closed = True
            self._release()

    def release(self) -> None:
        """Retain the created file and release its cleanup handles."""

        if self._closed:
            return
        self._closed = True
        self._release()


class TrackedFileLocation(str, Enum):
    """Closed location state for one journal-addressable recovery file."""

    CANONICAL = "canonical"
    QUARANTINE = "quarantine"


class TrackedExistingFile:
    """Retain platform-scoped authority over one recovery-file transaction."""

    def __init__(
        self,
        identity: ConfinedFileIdentity,
        location: TrackedFileLocation,
        *,
        relocate: Callable[[], None],
        dispose: Callable[[], None],
        release: Callable[[], None],
    ) -> None:
        self.identity = identity
        self._location = location
        self._relocate = relocate
        self._dispose = dispose
        self._release = release
        self._closed = False

    @property
    def location(self) -> TrackedFileLocation:
        """Return the currently verified canonical or quarantine location."""

        return self._location

    def _close_after_failure(self) -> None:
        self._closed = True
        self._release()

    def relocate(self) -> None:
        """Move the verified canonical entry to its journaled quarantine."""

        if self._closed:
            raise AtomicWriteError("tracked existing file is already closed")
        try:
            self._relocate()
        except BaseException:
            self._close_after_failure()
            raise
        self._location = TrackedFileLocation.QUARANTINE

    def dispose(self) -> None:
        """Dispose from quarantine under the documented platform precondition."""

        if self._closed:
            raise AtomicWriteError("tracked existing file is already closed")
        if self._location is not TrackedFileLocation.QUARANTINE:
            try:
                raise AtomicWriteError("tracked existing file must be quarantined")
            finally:
                self._close_after_failure()
        try:
            self._dispose()
        finally:
            self._closed = True
            self._release()

    def release(self) -> None:
        """Preserve the verified entry and release retained native authority."""

        if self._closed:
            return
        self._closed = True
        self._release()


def _posix_file_identity(metadata: os.stat_result) -> ConfinedFileIdentity:
    return ConfinedFileIdentity(
        platform="posix",
        volume=int(metadata.st_dev),
        file_id=int(metadata.st_ino),
    )


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


def _secure_rename_noreplace(
    parent_fd: int,
    source: str,
    destination: str,
) -> None:
    """Atomically move one relative entry without replacing another entry."""

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    variants = (("renameat2", 1), ("renameatx_np", 4))
    for function_name, flag in variants:
        function = getattr(library, function_name, None)
        if function is None:
            continue
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = function(
            parent_fd,
            source_bytes,
            parent_fd,
            destination_bytes,
            flag,
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, "destination already exists")
        if error == errno.ENOENT:
            raise FileNotFoundError(error, "source does not exist")
        if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            continue
        raise AtomicWriteError("cannot securely quarantine observed file") from None
    raise AtomicWriteError(_CAPABILITY_ERROR)


def _quarantine_name(quarantine_id: str) -> str:
    """Derive one bounded internal name from a durable 128-bit capability."""

    if not isinstance(quarantine_id, str):
        raise TypeError("quarantine_id must be a string")
    if (
        len(quarantine_id) != 32
        or any(character not in "0123456789abcdef" for character in quarantine_id)
    ):
        raise ValueError("quarantine_id must be 32 lowercase hexadecimal characters")
    return f".famulus-quarantine-{quarantine_id}"


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
        directory_fd = _open_absolute_directory(root, create=False)
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


def _open_absolute_directory(path: Path, *, create: bool) -> int:
    """Walk an absolute POSIX directory from ``/`` without following links."""
    _require_secure_operations()
    absolute = Path(path).absolute()
    if not absolute.is_absolute():
        raise AtomicWriteError(f"directory root must be absolute: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = _secure_open(Path(absolute.anchor), flags)
    try:
        for component in absolute.parts[1:]:
            if component in {"", ".", ".."}:
                raise AtomicWriteError(f"invalid directory root component: {component!r}")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                next_descriptor = _secure_open(component, flags, dir_fd=descriptor)
            except AtomicWriteError:
                raise
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise AtomicWriteError(
                    f"cannot securely open directory root: {path}"
                ) from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_secure_directory(path: Path) -> None:
    """Create and validate an absolute directory through retained no-follow handles."""
    absolute = Path(path).absolute()
    if os.name == "posix":
        descriptor = _open_absolute_directory(absolute, create=True)
        os.close(descriptor)
        return
    if os.name == "nt":
        handle = _windows_open_root(absolute, create=True)
        _windows_close_handle(handle)
        return
    raise AtomicWriteError(_CAPABILITY_ERROR)


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


def _posix_read_regular_file_bytes(path: Path, *, allowed_root: Path) -> bytes:
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


def _posix_atomic_replace_bytes(
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


def _posix_atomic_create_bytes(
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


def _posix_atomic_create_bytes_tracked(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> TrackedFileCreation | None:
    """Create a file and retain its parent descriptor for exact cleanup."""

    parent_fd, name = _open_parent(path, allowed_root)
    temp_name = f".{name}.tmp-{secrets.token_hex(8)}"
    temp_created = False
    linked_created = False
    transferred = False
    failure: BaseException | None = None
    try:
        if _reject_unsafe_final(parent_fd, name):
            return None
        descriptor = _open_temp(parent_fd, temp_name, mode)
        temp_created = True
        identity = _posix_file_identity(os.fstat(descriptor))
        _write_and_sync(descriptor, data)
        try:
            _secure_link(parent_fd, temp_name, name)
        except FileExistsError:
            _reject_unsafe_final(parent_fd, name)
            return None
        linked_created = True
        linked = _secure_stat(parent_fd, name)
        if _posix_file_identity(linked) != identity:
            raise AtomicWriteError(
                f"destination changed during tracked create: {name}"
            )
        _unlink_if_present(parent_fd, temp_name)
        temp_created = False
        os.fsync(parent_fd)

        def release() -> None:
            os.close(parent_fd)

        def remove() -> None:
            try:
                current = _secure_stat(parent_fd, name)
            except FileNotFoundError as exc:
                raise AtomicWriteError(
                    f"tracked destination changed before cleanup: {name}"
                ) from exc
            if (
                not stat.S_ISREG(current.st_mode)
                or _posix_file_identity(current) != identity
            ):
                raise AtomicWriteError(
                    f"tracked destination changed before cleanup: {name}"
                )
            _secure_unlink(parent_fd, name)
            os.fsync(parent_fd)

        transferred = True
        return TrackedFileCreation(
            identity,
            remove=remove,
            release=release,
        )
    except BaseException as exc:
        failure = exc
        if not linked_created:
            raise
        try:
            current = _secure_stat(parent_fd, name)
        except FileNotFoundError:
            pass
        except BaseException:
            raise AtomicWriteError(_TRACKED_CREATE_ERROR) from None
        else:
            if (
                stat.S_ISREG(current.st_mode)
                and _posix_file_identity(current) == identity
            ):
                try:
                    _secure_unlink(parent_fd, name)
                    os.fsync(parent_fd)
                except BaseException:
                    raise AtomicWriteError(_TRACKED_CREATE_ERROR) from None
        raise AtomicWriteError(_TRACKED_CREATE_ERROR) from None
    finally:
        if not transferred:
            cleanup_error = _cleanup_write(
                parent_fd,
                temp_name,
                temp_created,
            )
            if failure is None and cleanup_error is not None:
                raise cleanup_error


def _posix_track_existing_regular_file(
    path: Path,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    allowed_root: Path,
) -> TrackedExistingFile:
    """Retain authority over one canonical-or-quarantined regular file.

    The quarantine name is derived entirely from the durable capability.  An
    entry may therefore be rediscovered after process death without scanning or
    accepting an unrecorded pathname. Conditional unlink by retained inode is
    unavailable; after relocation, callers must protect the quarantine name as
    described by :func:`track_existing_regular_file`.
    """

    parent_fd, name = _open_parent(path, allowed_root)
    quarantine = _quarantine_name(quarantine_id)
    descriptor = -1
    transferred = False
    failure: BaseException | None = None
    try:
        def stat_optional(candidate: str) -> os.stat_result | None:
            try:
                return _secure_stat(parent_fd, candidate)
            except FileNotFoundError:
                return None

        canonical_metadata = stat_optional(name)
        quarantine_metadata = stat_optional(quarantine)
        if canonical_metadata is not None and quarantine_metadata is not None:
            raise AtomicWriteError(
                "observed file exists at both canonical and quarantine names"
            )
        if canonical_metadata is None and quarantine_metadata is None:
            raise FileNotFoundError(
                f"observed file has no canonical or quarantine entry: {name}"
            )
        location = (
            TrackedFileLocation.CANONICAL
            if canonical_metadata is not None
            else TrackedFileLocation.QUARANTINE
        )
        observed_name = (
            name
            if location is TrackedFileLocation.CANONICAL
            else quarantine
        )
        flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = _secure_open(observed_name, flags, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise AtomicWriteError(
                f"observed file changed during observation: {name}"
            ) from exc
        except OSError as exc:
            if exc.errno == getattr(os, "ELOOP", 40):
                raise AtomicWriteError(
                    f"observed file is a symbolic link: {observed_name}"
                ) from exc
            raise AtomicWriteError(
                f"cannot securely open observed file: {path}"
            ) from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError(
                f"observed file is not a regular file: {name}"
            )
        identity = _posix_file_identity(metadata)
        if _read_descriptor_bytes_bounded(
            descriptor,
            len(expected_bytes) + 1,
        ) != expected_bytes:
            raise AtomicWriteError("observed file bytes do not match expectation")
        try:
            linked = _secure_stat(parent_fd, observed_name)
        except FileNotFoundError as exc:
            raise AtomicWriteError(
                f"observed file changed during observation: {name}"
            ) from exc
        if (
            not stat.S_ISREG(linked.st_mode)
            or _posix_file_identity(linked) != identity
        ):
            raise AtomicWriteError(
                f"observed file changed during observation: {name}"
            )
        other_name = (
            quarantine
            if location is TrackedFileLocation.CANONICAL
            else name
        )
        if stat_optional(other_name) is not None:
            raise AtomicWriteError(
                "observed file exists at both canonical and quarantine names"
            )
        at_quarantine = location is TrackedFileLocation.QUARANTINE

        def release() -> None:
            cleanup_error = _cleanup_read(descriptor, parent_fd)
            if cleanup_error is not None:
                raise cleanup_error

        def exact_named_identity(candidate: str) -> bool:
            try:
                current = _secure_stat(parent_fd, candidate)
            except FileNotFoundError:
                return False
            return (
                stat.S_ISREG(current.st_mode)
                and _posix_file_identity(current) == identity
            )

        def exact_retained_bytes() -> bool:
            return (
                _read_descriptor_bytes_bounded(
                    descriptor,
                    len(expected_bytes) + 1,
                )
                == expected_bytes
            )

        def relocate() -> None:
            nonlocal at_quarantine
            if at_quarantine:
                if stat_optional(name) is not None:
                    raise AtomicWriteError(
                        "observed file exists at both canonical and quarantine names"
                    )
                if (
                    not exact_named_identity(quarantine)
                    or not exact_retained_bytes()
                ):
                    raise AtomicWriteError(
                        f"tracked destination changed before relocation: {name}"
                    )
                os.fsync(parent_fd)
                return
            if not exact_named_identity(name) or not exact_retained_bytes():
                raise AtomicWriteError(
                    f"tracked destination changed before relocation: {name}"
                )
            if stat_optional(quarantine) is not None:
                raise AtomicWriteError(
                    "observed file exists at both canonical and quarantine names"
                )
            try:
                _secure_rename_noreplace(parent_fd, name, quarantine)
            except (FileExistsError, FileNotFoundError) as exc:
                raise AtomicWriteError(
                    f"tracked destination changed before relocation: {name}"
                ) from exc

            def restore_moved_entry() -> None:
                try:
                    _secure_rename_noreplace(parent_fd, quarantine, name)
                except (FileExistsError, FileNotFoundError):
                    raise AtomicWriteError(
                        f"tracked destination changed before cleanup: {name}"
                    ) from None
                os.fsync(parent_fd)

            if stat_optional(name) is not None:
                raise AtomicWriteError(
                    "observed file exists at both canonical and quarantine names"
                )
            try:
                matches = exact_named_identity(quarantine) and exact_retained_bytes()
            except BaseException:
                restore_moved_entry()
                raise AtomicWriteError(
                    f"tracked destination changed during relocation: {name}"
                ) from None
            if not matches:
                restore_moved_entry()
                raise AtomicWriteError(
                    f"tracked destination changed during relocation: {name}"
                )
            os.fsync(parent_fd)
            at_quarantine = True

        def dispose() -> None:
            if stat_optional(name) is not None:
                raise AtomicWriteError(
                    "observed file exists at both canonical and quarantine names"
                )
            if not exact_named_identity(quarantine) or not exact_retained_bytes():
                raise AtomicWriteError(
                    f"tracked quarantine changed before disposal: {name}"
                )
            _secure_unlink(parent_fd, quarantine)
            os.fsync(parent_fd)

        transferred = True
        return TrackedExistingFile(
            identity,
            location,
            relocate=relocate,
            dispose=dispose,
            release=release,
        )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if not transferred:
            cleanup_error = _cleanup_read(descriptor, parent_fd)
            if failure is None and cleanup_error is not None:
                raise cleanup_error


def _posix_read_regular_directory_entries(
    root: Path,
) -> tuple[ConfinedRegularFile, ...]:
    """Read a directory through one retained no-follow descriptor walk."""

    descriptor = _open_absolute_directory(root, create=False)
    try:
        try:
            names = sorted(os.listdir(descriptor))
        except (NotImplementedError, TypeError) as exc:
            raise AtomicWriteError(_CAPABILITY_ERROR) from exc
        entries: list[ConfinedRegularFile] = []
        for name in names:
            if name in {"", ".", ".."} or "/" in name:
                raise AtomicWriteError(
                    f"invalid confined directory entry: {name!r}"
                )
            try:
                child = _secure_open(
                    name,
                    os.O_RDONLY
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError as exc:
                raise AtomicWriteError(
                    f"confined directory entry disappeared: {name}"
                ) from exc
            try:
                metadata = os.fstat(child)
                if not stat.S_ISREG(metadata.st_mode):
                    raise AtomicWriteError(
                        f"confined directory entry is not a regular file: {name}"
                    )
                entries.append(
                    ConfinedRegularFile(
                        name=name,
                        data=_read_descriptor_bytes(child),
                        identity=_posix_file_identity(metadata),
                    )
                )
            finally:
                os.close(child)
        return tuple(entries)
    finally:
        os.close(descriptor)


def _read_descriptor_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_descriptor_bytes_bounded(descriptor: int, maximum_bytes: int) -> bytes:
    """Read no more than one caller-supplied exact-match bound."""

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
        raise TypeError("maximum_bytes must be an integer")
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must not be negative")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = maximum_bytes
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_descriptor_bytes(descriptor: int, data: bytes, path: Path) -> None:
    written = 0
    while written < len(data):
        count = os.write(descriptor, data[written:])
        if count <= 0:
            raise AtomicWriteError(
                f"append wrote {written} of {len(data)} bytes: {path}"
            )
        written += count


def _posix_atomic_append_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    expected_previous_bytes: bytes | None | object = _UNCONDITIONAL_APPEND,
) -> None:
    """Append under a native lock after an optional complete-predecessor compare."""

    parent_fd, name = _open_parent(path, allowed_root)
    descriptor = -1
    failure: BaseException | None = None
    try:
        existed = _reject_unsafe_final(parent_fd, name)
        created = False
        if not existed and isinstance(expected_previous_bytes, bytes):
            raise AtomicWriteError(
                f"compare-and-append predecessor mismatch: {path}"
            )
        if not existed:
            try:
                descriptor = _secure_open(
                    name,
                    os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode,
                    dir_fd=parent_fd,
                )
                created = True
            except FileExistsError:
                existed = _reject_unsafe_final(parent_fd, name)
        if descriptor < 0:
            descriptor = _secure_open(
                name,
                os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError(f"destination is not a regular file: {name}")
        _secure_fchmod(descriptor, mode)
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - required POSIX stdlib
            raise AtomicWriteError(_CAPABILITY_ERROR) from exc
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        linked = _secure_stat(parent_fd, name)
        if (linked.st_dev, linked.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise AtomicWriteError(f"destination changed while acquiring lock: {path}")
        previous = _read_descriptor_bytes(descriptor)
        if expected_previous_bytes is None:
            matches = created and previous == b""
        elif expected_previous_bytes is _UNCONDITIONAL_APPEND:
            matches = True
        else:
            matches = previous == expected_previous_bytes
        if not matches:
            raise AtomicWriteError(f"compare-and-append predecessor mismatch: {path}")
        os.lseek(descriptor, 0, os.SEEK_END)
        _write_descriptor_bytes(descriptor, data, path)
        os.fsync(descriptor)
        if _read_descriptor_bytes(descriptor) != previous + data:
            raise AtomicWriteError(f"post-append reread failed: {path}")
        linked = _secure_stat(parent_fd, name)
        if (linked.st_dev, linked.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise AtomicWriteError(f"destination changed during append: {path}")
        os.fsync(parent_fd)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_error = _cleanup_read(descriptor, parent_fd)
        if failure is None and cleanup_error is not None:
            raise cleanup_error


def _confined_fallback_path(path: Path, allowed_root: Path) -> Path:
    destination = Path(path).absolute()
    root = Path(allowed_root).absolute()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}")
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise AtomicWriteError(f"cannot inspect allowed root: {root}") from exc
    if (
        stat.S_ISLNK(root_metadata.st_mode)
        or getattr(root_metadata, "st_file_attributes", 0) & 0x400
        or not stat.S_ISDIR(root_metadata.st_mode)
    ):
        raise AtomicWriteError(
            f"allowed root is a symbolic link, reparse point, or non-directory: {root}"
        )
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise AtomicWriteError(f"cannot inspect destination parent: {path}") from exc
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise AtomicWriteError(
                f"destination parent is a symbolic link or reparse point: {current}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise AtomicWriteError(f"destination parent is not a directory: {current}")
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return destination
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise AtomicWriteError(
            f"destination is a symbolic link or reparse point: {destination.name}"
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise AtomicWriteError(f"destination is not a regular file: {destination.name}")
    return destination


def _fallback_read_regular_file_bytes(path: Path, *, allowed_root: Path) -> bytes:
    destination = _confined_fallback_path(path, allowed_root)
    try:
        return destination.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AtomicWriteError(f"cannot read confined file: {path}") from exc


def _fallback_chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)


def _fallback_write(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    operation: str,
) -> bool | None:
    destination = _confined_fallback_path(path, allowed_root)
    file_mode = {"replace": "wb", "create": "xb", "append": "ab"}[operation]
    try:
        handle = destination.open(file_mode, buffering=0)
    except FileExistsError:
        if operation == "create":
            _confined_fallback_path(path, allowed_root)
            return False
        raise
    try:
        _fallback_chmod(destination, mode)
        written = handle.write(data)
        if written != len(data):
            raise AtomicWriteError(
                f"non-atomic {operation} wrote {written} of {len(data)} bytes: {path}"
            )
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    reread = _fallback_read_regular_file_bytes(destination, allowed_root=allowed_root)
    if operation == "append":
        matches = reread.endswith(data)
    else:
        matches = reread == data
    if not matches:
        raise AtomicWriteError(f"post-{operation} reread failed: {path}")
    return True if operation == "create" else None


def _fallback_compare_and_append(
    path: Path,
    data: bytes,
    *,
    expected_previous_bytes: bytes | None,
    allowed_root: Path,
    mode: int,
) -> None:
    """Best-effort compare/append used only after explicit capability opt-in."""

    destination = _confined_fallback_path(path, allowed_root)
    exists = destination.exists()
    if expected_previous_bytes is None:
        if exists:
            raise AtomicWriteError(f"compare-and-append predecessor mismatch: {path}")
        file_mode = "x+b"
    else:
        if not exists:
            raise AtomicWriteError(f"compare-and-append predecessor mismatch: {path}")
        file_mode = "r+b"
    try:
        handle = destination.open(file_mode, buffering=0)
    except FileExistsError as exc:
        raise AtomicWriteError(
            f"compare-and-append predecessor mismatch: {path}"
        ) from exc
    try:
        _fallback_chmod(destination, mode)
        handle.seek(0)
        previous = handle.read()
        if expected_previous_bytes is not None and previous != expected_previous_bytes:
            raise AtomicWriteError(
                f"compare-and-append predecessor mismatch: {path}"
            )
        handle.seek(0, os.SEEK_END)
        written = handle.write(data)
        if written != len(data):
            raise AtomicWriteError(
                f"non-atomic append wrote {written} of {len(data)} bytes: {path}"
            )
        handle.flush()
        os.fsync(handle.fileno())
        handle.seek(0)
        if handle.read() != previous + data:
            raise AtomicWriteError(f"post-append reread failed: {path}")
    finally:
        handle.close()


_WINDOWS_APIS: tuple[object, object, object] | None = None
# Native contracts used below: NtCreateFile with RootDirectory and
# FILE_OPEN_REPARSE_POINT; OBJECT_ATTRIBUTES with OBJ_DONT_REPARSE;
# FileRenameInfoEx and FILE_ID_INFO; LockFileEx and FlushFileBuffers; and
# handle-based GetSecurityInfo/SetSecurityInfo.
_WIN_SHARE_ALL = 0x1 | 0x2 | 0x4
_WIN_LIST_DIRECTORY = 0x1
_WIN_DIR_ACCESS = 0x20 | 0x80 | 0x00100000
_WIN_READ_ACCESS = 0x1 | 0x80 | 0x00020000 | 0x00100000
_WIN_GENERIC_WRITE = 0x40000000
_WIN_MUTATE_ACCESS = (
    0x1 | 0x2 | 0x4 | 0x80 | 0x00010000 | 0x00020000 | 0x00040000 | 0x00100000
)
_WIN_FILE_OPTIONS = 0x20 | 0x40


def _windows_modules():
    """Load the native APIs and declare fixed-width, pointer-safe signatures."""
    global _WINDOWS_APIS
    if _WINDOWS_APIS is not None:
        return _WINDOWS_APIS
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        dword = ctypes.c_uint32
        boolean = ctypes.c_int32
        pointer = ctypes.c_void_p

        def declare(library, name: str, arguments: list[object], result: object) -> None:
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result

        declare(kernel32, "CreateFileW", [ctypes.c_wchar_p, dword, dword, pointer, dword, dword, _WinHandle], _WinHandle)
        declare(kernel32, "CloseHandle", [_WinHandle], boolean)
        declare(kernel32, "GetFileInformationByHandleEx", [_WinHandle, ctypes.c_int32, pointer, dword], boolean)
        declare(kernel32, "SetFileInformationByHandle", [_WinHandle, ctypes.c_int32, pointer, dword], boolean)
        declare(kernel32, "SetFilePointerEx", [_WinHandle, ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), dword], boolean)
        io_arguments = [_WinHandle, pointer, dword, ctypes.POINTER(dword), pointer]
        declare(kernel32, "ReadFile", io_arguments, boolean)
        declare(kernel32, "WriteFile", io_arguments, boolean)
        declare(kernel32, "FlushFileBuffers", [_WinHandle], boolean)
        declare(kernel32, "LockFileEx", [_WinHandle, dword, dword, dword, dword, ctypes.POINTER(_WinOverlapped)], boolean)
        declare(kernel32, "UnlockFileEx", [_WinHandle, dword, dword, dword, ctypes.POINTER(_WinOverlapped)], boolean)
        declare(kernel32, "GetCurrentProcess", [], _WinHandle)
        declare(kernel32, "LocalFree", [pointer], pointer)
        declare(advapi32, "OpenProcessToken", [_WinHandle, dword, ctypes.POINTER(_WinHandle)], boolean)
        declare(advapi32, "GetTokenInformation", [_WinHandle, ctypes.c_int32, pointer, dword, ctypes.POINTER(dword)], boolean)
        declare(advapi32, "GetLengthSid", [pointer], dword)
        declare(advapi32, "IsValidSid", [pointer], boolean)
        declare(advapi32, "EqualSid", [pointer, pointer], boolean)
        declare(advapi32, "InitializeAcl", [pointer, dword, dword], boolean)
        declare(advapi32, "IsValidAcl", [pointer], boolean)
        declare(advapi32, "AddAccessAllowedAceEx", [pointer, dword, dword, dword, pointer], boolean)
        declare(advapi32, "InitializeSecurityDescriptor", [pointer, dword], boolean)
        declare(advapi32, "SetSecurityDescriptorDacl", [pointer, boolean, pointer, boolean], boolean)
        declare(advapi32, "SetSecurityDescriptorControl", [pointer, ctypes.c_uint16, ctypes.c_uint16], boolean)
        declare(advapi32, "SetSecurityInfo", [_WinHandle, ctypes.c_int32, dword, pointer, pointer, pointer, pointer], dword)
        pointer_out = ctypes.POINTER(pointer)
        declare(advapi32, "GetSecurityInfo", [_WinHandle, ctypes.c_int32, dword, pointer_out, pointer_out, pointer_out, pointer_out, pointer_out], dword)
        declare(advapi32, "GetSecurityDescriptorControl", [pointer, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(dword)], boolean)
        declare(advapi32, "GetAclInformation", [pointer, pointer, dword, ctypes.c_int32], boolean)
        declare(advapi32, "GetAce", [pointer, dword, pointer_out], boolean)
        declare(ntdll, "NtCreateFile", [ctypes.POINTER(_WinHandle), dword, ctypes.POINTER(_WinObjectAttributes), ctypes.POINTER(_WinIoStatusBlock), pointer, dword, dword, dword, dword, pointer, dword], ctypes.c_int32)
        declare(ntdll, "NtQueryDirectoryFile", [_WinHandle, _WinHandle, pointer, pointer, ctypes.POINTER(_WinIoStatusBlock), pointer, dword, ctypes.c_int32, ctypes.c_ubyte, pointer, ctypes.c_ubyte], ctypes.c_int32)
        declare(ntdll, "NtSetInformationFile", [_WinHandle, ctypes.POINTER(_WinIoStatusBlock), pointer, dword, ctypes.c_int32], ctypes.c_int32)
        declare(ntdll, "RtlNtStatusToDosError", [ctypes.c_int32], dword)
    except (AttributeError, OSError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc
    _WINDOWS_APIS = (kernel32, advapi32, ntdll)
    return _WINDOWS_APIS


def _windows_call_error(message: str, winerror: int | None = None) -> AtomicWriteError:
    _kernel32, _advapi32, _ntdll = _windows_modules()
    error = ctypes.get_last_error() if winerror is None else int(winerror)
    if error in {1, 50, 120}:
        return AtomicWriteError(_CAPABILITY_ERROR)
    return AtomicWriteError(f"{message}: winerror {error}")


def _windows_nt_error(status: int, message: str) -> BaseException:
    _kernel32, _advapi32, ntdll = _windows_modules()
    error = int(ntdll.RtlNtStatusToDosError(status))
    if error in {2, 3}:
        return FileNotFoundError(error, message)
    if error in {80, 183}:
        return FileExistsError(error, message)
    if error == 87:
        return AtomicWriteError(_CAPABILITY_ERROR)
    return _windows_call_error(message, error)


def _windows_path_parts(path: Path, allowed_root: Path) -> tuple[Path, Path]:
    destination = Path(path).absolute()
    root = Path(allowed_root).absolute()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}") from exc
    if not relative.parts:
        raise AtomicWriteError(f"invalid destination outside allowed root: {path}")
    for part in relative.parts:
        _windows_component_utf16(part)
    return root, relative


def _windows_close_handle(handle: int) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    if handle >= 0 and not kernel32.CloseHandle(_WinHandle(handle)):
        raise _windows_call_error("cannot close native handle")


def _windows_validate_handle(
    handle: int,
    *,
    expect_directory: bool,
    display: str | Path,
) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    information = _WinFileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        if error == 87:
            raise AtomicWriteError(_CAPABILITY_ERROR)
        raise _windows_call_error(
            f"cannot inspect native handle for {display}", error
        )
    attributes = int(information.FileAttributes)
    if attributes & 0x400:
        raise AtomicWriteError(f"reparse point is not allowed: {display}")
    is_directory = bool(attributes & 0x10)
    if is_directory != expect_directory:
        kind = "directory" if expect_directory else "regular file"
        raise AtomicWriteError(f"native handle is not a {kind}: {display}")


def _windows_open_root(
    root: Path,
    *,
    create: bool = False,
    final_access: int | None = None,
) -> int:
    """Walk a native root from its volume/share handle without reparse traversal."""
    absolute = Path(root).absolute()
    anchor = Path(absolute.anchor)
    if not absolute.is_absolute() or not absolute.anchor:
        raise AtomicWriteError(f"directory root must be absolute: {root}")
    kernel32, _advapi32, _ntdll = _windows_modules()
    handle = kernel32.CreateFileW(
        str(anchor),
        _WIN_DIR_ACCESS | 0x1 | 0x00020000,
        _WIN_SHARE_ALL,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(anchor)
        raise _windows_call_error(f"cannot securely open volume root {anchor}", error)
    value = int(handle)
    try:
        _windows_validate_handle(value, expect_directory=True, display=anchor)
        components = absolute.parts[1:]
        for index, component in enumerate(components):
            next_handle, _information = _windows_open_validated(
                value,
                component,
                access=(
                    final_access
                    if final_access is not None and index == len(components) - 1
                    else _WIN_DIR_ACCESS | 0x00020000
                ),
                disposition=3 if create else 1,
                options=0x1 | 0x20,
                directory=True,
            )
            _windows_close_handle(value)
            value = next_handle
    except BaseException:
        _windows_close_handle(value)
        raise
    return value


def _windows_open_relative(
    parent_handle: int,
    name: str,
    *,
    access: int,
    share: int = _WIN_SHARE_ALL,
    disposition: int,
    options: int,
    security_descriptor: _WinSecurityDescriptor | None = None,
) -> tuple[int, int]:
    """NtCreateFile relative to an already validated directory handle.

    The NtCreateFile contract combines OBJECT_ATTRIBUTES.RootDirectory with
    FILE_OPEN_REPARSE_POINT for this no-reparse relative-open path.
    """

    encoded = _windows_component_utf16(name)
    _kernel32, _advapi32, ntdll = _windows_modules()
    encoded_length = len(encoded)
    buffer = (ctypes.c_uint16 * (encoded_length // 2 + 1))()
    if encoded:
        ctypes.memmove(buffer, encoded, encoded_length)
    unicode_name = _WinUnicodeString(
        Length=encoded_length,
        MaximumLength=encoded_length + 2,
        Buffer=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint16)),
    )
    attributes = _WinObjectAttributes(
        Length=ctypes.sizeof(_WinObjectAttributes),
        RootDirectory=_WinHandle(parent_handle),
        ObjectName=ctypes.pointer(unicode_name),
        Attributes=0x40 | 0x1000,
        SecurityDescriptor=(
            ctypes.cast(ctypes.byref(security_descriptor), ctypes.c_void_p)
            if security_descriptor is not None
            else None
        ),
        SecurityQualityOfService=None,
    )
    io_status = _WinIoStatusBlock()
    handle = _WinHandle()
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x80,
            share,
            disposition,
            options | 0x00200000,
            None,
            0,
        )
    )
    if status < 0:
        if handle.value:
            _windows_close_handle(int(handle.value))
        raise _windows_nt_error(status, f"cannot securely open relative path {name}")
    if not handle.value:
        raise AtomicWriteError(_CAPABILITY_ERROR)
    return int(handle.value), int(io_status.Information)


def _windows_open_validated(
    parent_handle: int,
    name: str,
    *,
    access: int,
    disposition: int,
    options: int,
    directory: bool,
    security_descriptor: _WinSecurityDescriptor | None = None,
    share: int = _WIN_SHARE_ALL,
) -> tuple[int, int]:
    handle, information = _windows_open_relative(
        parent_handle,
        name,
        access=access,
        disposition=disposition,
        options=options,
        security_descriptor=security_descriptor,
        share=share,
    )
    try:
        _windows_validate_handle(
            handle, expect_directory=directory, display=name
        )
    except BaseException:
        _windows_close_handle(handle)
        raise
    return handle, information


def _windows_open_parent(
    path: Path, allowed_root: Path
) -> tuple[list[int], tuple[str, ...]]:
    root, relative = _windows_path_parts(path, allowed_root)
    handles = [_windows_open_root(root)]
    parts = tuple(str(part) for part in relative.parts)
    try:
        _windows_file_id(handles[0])
        for component in parts[:-1]:
            next_handle, _information = _windows_open_validated(
                handles[-1],
                component,
                access=_WIN_DIR_ACCESS | 0x00020000,
                disposition=1,
                options=0x1 | 0x20,
                directory=True,
            )
            handles.append(next_handle)
            _windows_file_id(next_handle)
        return handles, parts
    except BaseException:
        _windows_close_chain(handles)
        raise


def _windows_verify_parent_chain(handles: list[int], parts: tuple[str, ...]) -> None:
    """Reopen each retained child from its retained parent and compare IDs."""

    for index, expected_handle in enumerate(handles[1:], start=1):
        component = parts[index - 1]
        try:
            reopened, _information = _windows_open_validated(
                handles[index - 1],
                component,
                access=_WIN_DIR_ACCESS,
                disposition=1,
                options=0x1 | 0x20,
                directory=True,
            )
        except FileNotFoundError as exc:
            raise AtomicWriteError(
                f"destination parent changed during operation: {component}"
            ) from exc
        try:
            if _windows_file_id(reopened) != _windows_file_id(expected_handle):
                raise AtomicWriteError(
                    f"destination parent changed during operation: {component}"
                )
        finally:
            _windows_close_handle(reopened)


def _windows_close_chain(handles: list[int]) -> None:
    failure: BaseException | None = None
    for handle in reversed(handles):
        try:
            _windows_close_handle(handle)
        except BaseException as exc:
            if failure is None:
                failure = exc
    if failure is not None:
        raise failure


def _windows_seek(handle: int, offset: int, origin: int) -> int:
    kernel32, _advapi32, _ntdll = _windows_modules()
    result = ctypes.c_int64()
    if not kernel32.SetFilePointerEx(
        _WinHandle(handle), offset, ctypes.byref(result), origin
    ):
        raise _windows_call_error("cannot seek native file handle")
    return int(result.value)


def _windows_read_handle(handle: int) -> bytes:
    kernel32, _advapi32, _ntdll = _windows_modules()
    _windows_seek(handle, 0, 0)
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        count = ctypes.c_uint32()
        if not kernel32.ReadFile(
            handle,
            buffer,
            len(buffer),
            ctypes.byref(count),
            None,
        ):
            raise _windows_call_error("cannot read native file handle")
        if not count.value:
            return b"".join(chunks)
        chunks.append(buffer.raw[: count.value])


def _windows_read_handle_bounded(handle: int, maximum_bytes: int) -> bytes:
    """Read at most one exact-match bound from a retained native handle."""

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
        raise TypeError("maximum_bytes must be an integer")
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must not be negative")
    kernel32, _advapi32, _ntdll = _windows_modules()
    _windows_seek(handle, 0, 0)
    chunks: list[bytes] = []
    remaining = maximum_bytes
    while remaining:
        buffer = ctypes.create_string_buffer(min(1024 * 1024, remaining))
        count = ctypes.c_uint32()
        if not kernel32.ReadFile(
            handle,
            buffer,
            len(buffer),
            ctypes.byref(count),
            None,
        ):
            raise _windows_call_error("cannot read native file handle")
        size = int(count.value)
        if size == 0:
            break
        chunks.append(buffer.raw[:size])
        remaining -= size
    return b"".join(chunks)


def _windows_write_handle(handle: int, data: bytes) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    written = 0
    while written < len(data):
        chunk = data[written : written + 1024 * 1024]
        buffer = ctypes.create_string_buffer(chunk)
        count = ctypes.c_uint32()
        if not kernel32.WriteFile(
            handle,
            buffer,
            len(chunk),
            ctypes.byref(count),
            None,
        ):
            raise _windows_call_error("cannot write native file handle")
        if not count.value:
            raise AtomicWriteError(
                f"native write wrote {written} of {len(data)} bytes"
            )
        written += int(count.value)


def _windows_flush_handle(handle: int) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    # FlushFileBuffers is the documented user-mode durability boundary for
    # system-buffered file data; files are also opened FILE_WRITE_THROUGH.
    if not kernel32.FlushFileBuffers(handle):
        raise _windows_call_error("cannot flush native file handle")


def _windows_file_id(handle: int) -> tuple[int, bytes]:
    kernel32, _advapi32, _ntdll = _windows_modules()
    information = _WinFileIdInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        18,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        if error == 87:
            raise AtomicWriteError(_CAPABILITY_ERROR)
        raise _windows_call_error("cannot read native file identity", error)
    file_id = bytes(information.FileId)
    if file_id == bytes(16):
        raise AtomicWriteError(_CAPABILITY_ERROR)
    return int(information.VolumeSerialNumber), file_id


def _windows_confined_identity(handle: int) -> ConfinedFileIdentity:
    volume, file_id = _windows_file_id(handle)
    return ConfinedFileIdentity(
        platform="windows",
        volume=volume,
        file_id=file_id,
    )


def _windows_directory_entry_names(handle: int) -> tuple[str, ...]:
    """Enumerate names through a retained native directory handle."""

    _kernel32, _advapi32, ntdll = _windows_modules()
    names: list[str] = []
    restart = True
    while True:
        buffer = ctypes.create_string_buffer(64 * 1024)
        io_status = _WinIoStatusBlock()
        status = int(
            ntdll.NtQueryDirectoryFile(
                _WinHandle(handle),
                None,
                None,
                None,
                ctypes.byref(io_status),
                buffer,
                len(buffer),
                12,
                0,
                None,
                int(restart),
            )
        )
        unsigned_status = status & 0xFFFFFFFF
        if unsigned_status == 0x80000006:  # STATUS_NO_MORE_FILES
            break
        if status < 0 and unsigned_status != 0x80000005:  # STATUS_BUFFER_OVERFLOW
            raise _windows_nt_error(status, "cannot enumerate native directory")
        used = int(io_status.Information)
        if used <= 0:
            raise AtomicWriteError("native directory enumeration made no progress")
        offset = 0
        while offset + 12 <= used:
            next_offset = int.from_bytes(
                buffer.raw[offset : offset + 4], "little"
            )
            name_length = int.from_bytes(
                buffer.raw[offset + 8 : offset + 12], "little"
            )
            end = offset + 12 + name_length
            if name_length % 2 or end > used:
                raise AtomicWriteError("invalid native directory entry")
            try:
                name = buffer.raw[offset + 12 : end].decode("utf-16-le")
            except UnicodeDecodeError as exc:
                raise AtomicWriteError("invalid native directory entry") from exc
            if name not in {".", ".."}:
                _windows_component_utf16(name)
                names.append(name)
            if next_offset == 0:
                break
            if next_offset < 12 or offset + next_offset > used:
                raise AtomicWriteError("invalid native directory entry offset")
            offset += next_offset
        restart = False
    return tuple(sorted(names))


def _windows_read_regular_directory_entries(
    root: Path,
) -> tuple[ConfinedRegularFile, ...]:
    """Read files relative to one retained no-reparse directory handle."""

    root_handle = _windows_open_root(
        root,
        final_access=_WIN_DIR_ACCESS | 0x00020000 | _WIN_LIST_DIRECTORY,
    )
    try:
        entries: list[ConfinedRegularFile] = []
        for name in _windows_directory_entry_names(root_handle):
            child = -1
            try:
                try:
                    child, _information = _windows_open_validated(
                        root_handle,
                        name,
                        access=_WIN_READ_ACCESS,
                        disposition=1,
                        options=_WIN_FILE_OPTIONS,
                        directory=False,
                    )
                except FileNotFoundError as exc:
                    raise AtomicWriteError(
                        f"confined directory entry disappeared: {name}"
                    ) from exc
                entries.append(
                    ConfinedRegularFile(
                        name=name,
                        data=_windows_read_handle(child),
                        identity=_windows_confined_identity(child),
                    )
                )
            finally:
                if child >= 0:
                    _windows_close_handle(child)
        return tuple(entries)
    finally:
        _windows_close_handle(root_handle)


def _windows_security_material() -> tuple[object, ctypes.c_void_p, object, _WinSecurityDescriptor]:
    """Build a protected one-ACE DACL and absolute descriptor for this user."""

    kernel32, advapi32, _ntdll = _windows_modules()
    token = _WinHandle()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x8, ctypes.byref(token)
    ):
        raise _windows_call_error("cannot open current process token")
    try:
        size = ctypes.c_uint32()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise _windows_call_error("cannot size current token user")
        sid_buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, 1, sid_buffer, size.value, ctypes.byref(size)
        ):
            raise _windows_call_error("cannot read current token user")
    finally:
        _windows_close_handle(int(token.value))
    sid = ctypes.cast(
        sid_buffer, ctypes.POINTER(_WinTokenUser)
    ).contents.User.Sid
    if not sid or not advapi32.IsValidSid(sid):
        raise AtomicWriteError("current user SID is invalid")
    sid_length = int(advapi32.GetLengthSid(sid))
    acl_size = 8 + _WinAccessAllowedAce.SidStart.offset + sid_length
    acl = ctypes.create_string_buffer(acl_size)
    if not advapi32.InitializeAcl(acl, acl_size, 2) or not advapi32.AddAccessAllowedAceEx(
        acl, 2, 0, 0x001F01FF, sid
    ):
        raise _windows_call_error("cannot build restrictive native DACL")
    descriptor = _WinSecurityDescriptor()
    if not advapi32.InitializeSecurityDescriptor(ctypes.byref(descriptor), 1):
        raise _windows_call_error("cannot initialize native security descriptor")
    if not advapi32.SetSecurityDescriptorDacl(
        ctypes.byref(descriptor), True, acl, False
    ) or not advapi32.SetSecurityDescriptorControl(
        ctypes.byref(descriptor), 0x1000, 0x1000
    ):
        raise _windows_call_error("cannot protect restrictive native DACL")
    return sid_buffer, ctypes.c_void_p(sid), acl, descriptor


def _windows_set_user_restrictive_acl(
    handle: int, acl: object | None = None
) -> None:
    # SetSecurityInfo mutates the DACL through the retained handle.
    _kernel32, advapi32, _ntdll = _windows_modules()
    if acl is None:
        _sid_buffer, _sid, acl, _descriptor = _windows_security_material()
    result = int(
        advapi32.SetSecurityInfo(
            _WinHandle(handle), 1, 0x4 | 0x80000000, None, None, acl, None
        )
    )
    if result:
        raise _windows_call_error("cannot set restrictive native DACL", result)


def _windows_verify_handle_user_restrictive_acl(handle: int) -> bool:
    # GetSecurityInfo returns the descriptor for this exact retained handle.
    kernel32, advapi32, _ntdll = _windows_modules()
    _sid_buffer, expected_sid, _acl, _descriptor = _windows_security_material()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = int(
        advapi32.GetSecurityInfo(
            _WinHandle(handle),
            1,
            0x4,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if result:
        raise _windows_call_error("cannot read native DACL", result)
    try:
        if not dacl.value:
            return False
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            raise _windows_call_error("cannot inspect native DACL control")
        if not control.value & 0x1000:
            return False
        if not advapi32.IsValidAcl(dacl):
            return False
        acl_information = _WinAclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_information),
            ctypes.sizeof(acl_information),
            2,
        ):
            raise _windows_call_error("cannot inspect native ACL")
        if acl_information.AceCount != 1:
            return False
        ace_pointer = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
            raise _windows_call_error("cannot inspect native ACE")
        ace = ctypes.cast(
            ace_pointer,
            ctypes.POINTER(_WinAccessAllowedAce),
        ).contents
        if ace.Header.AceType != 0 or ace.Header.AceFlags != 0:
            return False
        if int(ace.Mask) != 0x001F01FF:
            return False
        ace_sid = ctypes.c_void_p(
            int(ace_pointer.value) + _WinAccessAllowedAce.SidStart.offset
        )
        if not advapi32.IsValidSid(ace_sid):
            return False
        expected_size = _WinAccessAllowedAce.SidStart.offset + int(
            advapi32.GetLengthSid(ace_sid)
        )
        return (
            ace.Header.AceSize == expected_size
            and bool(advapi32.EqualSid(ace_sid, expected_sid))
        )
    finally:
        if kernel32.LocalFree(descriptor):
            raise AtomicWriteError("cannot release native security descriptor")


def _windows_require_restrictive_acl(handle: int, name: str) -> None:
    if not _windows_verify_handle_user_restrictive_acl(handle):
        raise AtomicWriteError(
            f"restrictive native ACL verification failed: {name}"
        )


def _windows_read_regular_file_bytes(path: Path, *, allowed_root: Path) -> bytes:
    parents, parts = _windows_open_parent(path, allowed_root)
    handle = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        handle, _information = _windows_open_validated(
            parents[-1],
            parts[-1],
            access=_WIN_READ_ACCESS,
            disposition=1,
            options=_WIN_FILE_OPTIONS,
            directory=False,
        )
        content = _windows_read_handle(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parents[-1], parts[-1], handle)
        return content
    finally:
        _windows_close_chain(parents + ([handle] if handle >= 0 else []))


def _windows_existing_regular(parent_handle: int, name: str) -> bool:
    handle = -1
    try:
        handle, _information = _windows_open_validated(
            parent_handle,
            name,
            access=_WIN_READ_ACCESS & ~0x1,
            disposition=1,
            options=0x20,
            directory=False,
        )
        return True
    except FileNotFoundError:
        return False
    finally:
        if handle >= 0:
            _windows_close_handle(handle)


def _windows_mark_delete(handle: int) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    disposition = _WinFileDispositionInformation(DeleteFile=1)
    if not kernel32.SetFileInformationByHandle(
        _WinHandle(handle), 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
    ):
        raise _windows_call_error("cannot remove confined temporary file")


def _windows_rename_handle(
    handle: int,
    parent_handle: int,
    name: str,
    *,
    replace: bool,
) -> bool:
    """Atomically rename relative to a retained 64-bit directory handle."""

    _kernel32, _advapi32, ntdll = _windows_modules()
    information = _windows_file_rename_info(
        name,
        parent_handle,
        replace=replace,
    )
    for information_class in (65, 10):
        io_status = _WinIoStatusBlock()
        status = int(
            ntdll.NtSetInformationFile(
                _WinHandle(handle),
                ctypes.byref(io_status),
                ctypes.byref(information),
                information._used_size,
                information_class,
            )
        )
        if status >= 0:
            return True
        error = int(ntdll.RtlNtStatusToDosError(status))
        if error in {80, 183}:
            return False
        if information_class == 65 and error in {1, 50, 87, 120}:
            continue
        if error in {1, 50, 87, 120}:
            raise AtomicWriteError(_CAPABILITY_ERROR)
        raise _windows_call_error(f"cannot atomically rename to {name}", error)
    raise AtomicWriteError(_CAPABILITY_ERROR)


def _windows_write_temp(
    parent_handle: int,
    name: str,
    data: bytes,
) -> tuple[int, str]:
    _sid_buffer, _sid, _acl, descriptor = _windows_security_material()
    for _attempt in range(16):
        temp_name = f".{name}.tmp-{secrets.token_hex(8)}"
        try:
            handle, _information = _windows_open_validated(
                parent_handle,
                temp_name,
                access=_WIN_MUTATE_ACCESS,
                disposition=2,
                options=0x2 | _WIN_FILE_OPTIONS,
                directory=False,
                security_descriptor=descriptor,
            )
        except FileExistsError:
            continue
        try:
            _windows_require_restrictive_acl(handle, temp_name)
            _windows_seek(handle, 0, 0)
            _windows_write_handle(handle, data)
            _windows_flush_handle(handle)
            if _windows_read_handle(handle) != data:
                raise AtomicWriteError(f"post-write reread failed: {temp_name}")
            return handle, temp_name
        except BaseException:
            try:
                _windows_mark_delete(handle)
            finally:
                _windows_close_handle(handle)
            raise
    raise AtomicWriteError("cannot allocate a unique confined temporary file")


def _windows_verify_named_handle(
    parent_handle: int,
    name: str,
    expected_handle: int,
) -> None:
    verifier = -1
    try:
        verifier, _information = _windows_open_validated(
            parent_handle,
            name,
            access=0x80 | 0x00100000,
            disposition=1,
            options=_WIN_FILE_OPTIONS,
            directory=False,
        )
        if _windows_file_id(verifier) != _windows_file_id(expected_handle):
            raise AtomicWriteError(f"destination changed during native write: {name}")
    finally:
        if verifier >= 0:
            _windows_close_handle(verifier)


def _windows_atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    replace: bool,
) -> bool:
    parents, parts = _windows_open_parent(path, allowed_root)
    parent_handle, name = parents[-1], parts[-1]
    temp_handle = -1
    renamed = False
    try:
        _windows_verify_parent_chain(parents, parts)
        if not replace and _windows_existing_regular(parent_handle, name):
            return False
        if replace:
            _windows_existing_regular(parent_handle, name)
        temp_handle, _temp_name = _windows_write_temp(parent_handle, name, data)
        _windows_verify_parent_chain(parents, parts)
        if replace:
            _windows_existing_regular(parent_handle, name)
        renamed = _windows_rename_handle(
            temp_handle, parent_handle, name, replace=replace
        )
        if not renamed:
            if replace:
                raise AtomicWriteError(f"native replace collision: {name}")
            _windows_existing_regular(parent_handle, name)
            return False
        _windows_flush_handle(temp_handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parent_handle, name, temp_handle)
        if _windows_read_handle(temp_handle) != data:
            raise AtomicWriteError(f"post-write reread failed: {name}")
        _windows_require_restrictive_acl(temp_handle, name)
        return True
    finally:
        try:
            if temp_handle >= 0 and not renamed:
                _windows_mark_delete(temp_handle)
        finally:
            _windows_close_chain(
                parents + ([temp_handle] if temp_handle >= 0 else [])
            )


def _windows_atomic_replace_bytes(
    path: Path, data: bytes, *, allowed_root: Path, mode: int
) -> None:
    del mode
    _windows_atomic_write_bytes(path, data, allowed_root=allowed_root, replace=True)


def _windows_atomic_create_bytes(
    path: Path, data: bytes, *, allowed_root: Path, mode: int
) -> bool:
    del mode
    return _windows_atomic_write_bytes(path, data, allowed_root=allowed_root, replace=False)


def _windows_atomic_create_bytes_tracked(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> TrackedFileCreation | None:
    """Create a file while retaining native handles for exact cleanup."""

    del mode
    parents, parts = _windows_open_parent(path, allowed_root)
    parent_handle, name = parents[-1], parts[-1]
    temp_handle = -1
    renamed = False
    transferred = False
    try:
        _windows_verify_parent_chain(parents, parts)
        if _windows_existing_regular(parent_handle, name):
            return None
        temp_handle, _temp_name = _windows_write_temp(parent_handle, name, data)
        identity = _windows_confined_identity(temp_handle)
        _windows_verify_parent_chain(parents, parts)
        renamed = _windows_rename_handle(
            temp_handle,
            parent_handle,
            name,
            replace=False,
        )
        if not renamed:
            _windows_existing_regular(parent_handle, name)
            return None
        _windows_flush_handle(temp_handle)
        _windows_verify_named_handle(parent_handle, name, temp_handle)
        if _windows_read_handle(temp_handle) != data:
            raise AtomicWriteError(f"post-write reread failed: {name}")
        _windows_require_restrictive_acl(temp_handle, name)

        def release() -> None:
            _windows_close_chain(parents + [temp_handle])

        def remove() -> None:
            verifier = -1
            try:
                verifier, _information = _windows_open_validated(
                    parent_handle,
                    name,
                    access=_WIN_READ_ACCESS,
                    disposition=1,
                    options=_WIN_FILE_OPTIONS,
                    directory=False,
                )
                if _windows_confined_identity(verifier) != identity:
                    raise AtomicWriteError(
                        f"tracked destination changed before cleanup: {name}"
                    )
                _windows_mark_delete(temp_handle)
            except FileNotFoundError as exc:
                raise AtomicWriteError(
                    f"tracked destination changed before cleanup: {name}"
                ) from exc
            finally:
                if verifier >= 0:
                    _windows_close_handle(verifier)

        transferred = True
        return TrackedFileCreation(
            identity,
            remove=remove,
            release=release,
        )
    finally:
        if not transferred:
            try:
                if temp_handle >= 0:
                    _windows_mark_delete(temp_handle)
            finally:
                _windows_close_chain(
                    parents + ([temp_handle] if temp_handle >= 0 else [])
                )


def _windows_track_existing_regular_file(
    path: Path,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    allowed_root: Path,
) -> TrackedExistingFile:
    """Retain native handle authority over one recovery-file transaction.

    Relocation flushes the writable retained file handle and verifies the live
    name and bytes. This branch has no directory-fsync step, so it does not
    claim power-loss durability for the rename metadata.
    Disposal marks the retained handle for deletion, closes it, and proves the
    quarantine name absent through the retained parent before releasing that
    parent authority.
    """

    parents, parts = _windows_open_parent(path, allowed_root)
    parent_handle, name = parents[-1], parts[-1]
    quarantine = _quarantine_name(quarantine_id)
    handle = -1
    lock: object | None = None
    handle_open = False
    lock_held = False
    parents_open = True
    transferred = False
    try:
        _windows_verify_parent_chain(parents, parts)

        def open_optional(candidate: str) -> int:
            try:
                candidate_handle, _information = _windows_open_validated(
                    parent_handle,
                    candidate,
                    access=_WIN_READ_ACCESS | 0x00010000 | _WIN_GENERIC_WRITE,
                    disposition=1,
                    options=_WIN_FILE_OPTIONS,
                    directory=False,
                    share=_WIN_SHARE_ALL & ~0x2,
                )
            except FileNotFoundError:
                return -1
            return candidate_handle

        canonical_handle = open_optional(name)
        try:
            quarantine_handle = open_optional(quarantine)
        except BaseException:
            if canonical_handle >= 0:
                _windows_close_handle(canonical_handle)
            raise
        if canonical_handle >= 0 and quarantine_handle >= 0:
            _windows_close_chain([canonical_handle, quarantine_handle])
            raise AtomicWriteError(
                "observed file exists at both canonical and quarantine names"
            )
        if canonical_handle < 0 and quarantine_handle < 0:
            raise FileNotFoundError(
                f"observed file has no canonical or quarantine entry: {name}"
            )
        if canonical_handle >= 0:
            handle = canonical_handle
            location = TrackedFileLocation.CANONICAL
            observed_name = name
        else:
            handle = quarantine_handle
            location = TrackedFileLocation.QUARANTINE
            observed_name = quarantine

        handle_open = True
        lock = _windows_lock_handle(handle)
        lock_held = True
        identity = _windows_confined_identity(handle)
        if _windows_read_handle_bounded(
            handle,
            len(expected_bytes) + 1,
        ) != expected_bytes:
            raise AtomicWriteError("observed file bytes do not match expectation")
        _windows_verify_parent_chain(parents, parts)
        try:
            _windows_verify_named_handle(parent_handle, observed_name, handle)
        except FileNotFoundError as exc:
            raise AtomicWriteError(
                f"observed file changed during observation: {name}"
            ) from exc
        # The two initial probes are not one atomic snapshot. Recheck the
        # excluded name after locking and validating the selected handle so a
        # name created between the probes cannot escape XOR discovery.
        initial_other = open_optional(
            quarantine
            if location is TrackedFileLocation.CANONICAL
            else name
        )
        if initial_other >= 0:
            try:
                raise AtomicWriteError(
                    "observed file exists at both canonical and quarantine names"
                )
            finally:
                _windows_close_handle(initial_other)
        at_quarantine = location is TrackedFileLocation.QUARANTINE

        def release() -> None:
            nonlocal handle_open, lock_held, parents_open
            first_error: BaseException | None = None
            if lock_held:
                try:
                    _windows_unlock_handle(handle, lock)
                except BaseException as exc:
                    first_error = exc
                finally:
                    lock_held = False
            if handle_open:
                try:
                    _windows_close_handle(handle)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                finally:
                    handle_open = False
            if parents_open:
                try:
                    _windows_close_chain(parents)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                finally:
                    parents_open = False
            if first_error is not None:
                raise first_error

        def require_other_absent(candidate: str) -> None:
            other = open_optional(candidate)
            if other < 0:
                return
            try:
                raise AtomicWriteError(
                    "observed file exists at both canonical and quarantine names"
                )
            finally:
                _windows_close_handle(other)

        def require_exact(candidate: str, *, transition: str) -> None:
            _windows_verify_parent_chain(parents, parts)
            if _windows_read_handle_bounded(
                handle,
                len(expected_bytes) + 1,
            ) != expected_bytes:
                raise AtomicWriteError(
                    f"tracked destination bytes changed before {transition}"
                )
            try:
                _windows_verify_named_handle(parent_handle, candidate, handle)
            except FileNotFoundError as exc:
                raise AtomicWriteError(
                    f"tracked destination changed before {transition}: {name}"
                ) from exc

        def relocate() -> None:
            nonlocal at_quarantine
            if at_quarantine:
                require_other_absent(name)
                require_exact(quarantine, transition="relocation")
                return
            require_exact(name, transition="relocation")
            require_other_absent(quarantine)
            if not _windows_rename_handle(
                handle,
                parent_handle,
                quarantine,
                replace=False,
            ):
                raise AtomicWriteError(
                    f"tracked destination changed during relocation: {name}"
                )
            # Flush the writable retained file handle. This native branch has
            # no directory-fsync equivalent, so the subsequent name and byte
            # checks verify the live rename but do not claim power-loss
            # durability for directory metadata.
            _windows_flush_handle(handle)
            require_other_absent(name)
            require_exact(quarantine, transition="relocation")
            at_quarantine = True

        def dispose() -> None:
            nonlocal handle_open, lock_held
            require_other_absent(name)
            require_exact(quarantine, transition="disposal")
            _windows_mark_delete(handle)
            if lock_held:
                _windows_unlock_handle(handle, lock)
                lock_held = False
            _windows_close_handle(handle)
            handle_open = False
            remaining = open_optional(quarantine)
            if remaining >= 0:
                try:
                    raise AtomicWriteError(
                        "tracked quarantine remained after disposal"
                    )
                finally:
                    _windows_close_handle(remaining)

        transferred = True
        return TrackedExistingFile(
            identity,
            location,
            relocate=relocate,
            dispose=dispose,
            release=release,
        )
    finally:
        if not transferred:
            try:
                if handle >= 0 and lock is not None:
                    _windows_unlock_handle(handle, lock)
            finally:
                _windows_close_chain(parents + ([handle] if handle >= 0 else []))


def _windows_lock_handle(handle: int) -> _WinOverlapped:
    # LockFileEx serializes cooperative writers on the complete file range.
    kernel32, _advapi32, _ntdll = _windows_modules()
    overlapped = _WinOverlapped()
    if not kernel32.LockFileEx(
        handle,
        0x2,
        0,
        0xFFFFFFFF,
        0xFFFFFFFF,
        ctypes.byref(overlapped),
    ):
        raise _windows_call_error("cannot lock native certificate log")
    return overlapped


def _windows_unlock_handle(handle: int, overlapped: object) -> None:
    kernel32, _advapi32, _ntdll = _windows_modules()
    if not kernel32.UnlockFileEx(
        handle,
        0,
        0xFFFFFFFF,
        0xFFFFFFFF,
        ctypes.byref(overlapped),
    ):
        raise _windows_call_error("cannot unlock native certificate log")


def _windows_append_bytes(
    path: Path,
    data: bytes,
    *,
    expected_previous_bytes: bytes | None | object,
    allowed_root: Path,
) -> None:
    parents, parts = _windows_open_parent(path, allowed_root)
    parent_handle, name = parents[-1], parts[-1]
    handle = -1
    lock = None
    created = False
    success = False
    try:
        _windows_verify_parent_chain(parents, parts)
        _sid_buffer, _sid, _acl, descriptor = _windows_security_material()
        disposition = 1 if isinstance(expected_previous_bytes, bytes) else 3
        try:
            handle, information = _windows_open_validated(
                parent_handle,
                name,
                access=_WIN_MUTATE_ACCESS,
                disposition=disposition,
                options=0x2 | _WIN_FILE_OPTIONS,
                directory=False,
                security_descriptor=descriptor,
            )
        except FileNotFoundError as exc:
            if isinstance(expected_previous_bytes, bytes):
                raise AtomicWriteError(
                    f"compare-and-append predecessor mismatch: {path}"
                ) from exc
            raise
        created = information == 2
        lock = _windows_lock_handle(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parent_handle, name, handle)
        _windows_set_user_restrictive_acl(handle, _acl)
        _windows_require_restrictive_acl(handle, name)
        previous = _windows_read_handle(handle)
        if expected_previous_bytes is None:
            matches = created and previous == b""
        elif expected_previous_bytes is _UNCONDITIONAL_APPEND:
            matches = True
        else:
            matches = previous == expected_previous_bytes
        if not matches:
            raise AtomicWriteError(
                f"compare-and-append predecessor mismatch: {path}"
            )
        _windows_seek(handle, 0, 2)
        _windows_write_handle(handle, data)
        _windows_flush_handle(handle)
        if _windows_read_handle(handle) != previous + data:
            raise AtomicWriteError(f"post-append reread failed: {path}")
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parent_handle, name, handle)
        _windows_require_restrictive_acl(handle, name)
        success = True
    finally:
        try:
            if lock is not None:
                _windows_unlock_handle(handle, lock)
        finally:
            try:
                if created and not success and handle >= 0:
                    _windows_mark_delete(handle)
            finally:
                _windows_close_chain(parents + ([handle] if handle >= 0 else []))


def _windows_atomic_append_bytes(
    path: Path, data: bytes, *, allowed_root: Path, mode: int
) -> None:
    del mode
    _windows_append_bytes(
        path,
        data,
        expected_previous_bytes=_UNCONDITIONAL_APPEND,
        allowed_root=allowed_root,
    )


def _windows_atomic_compare_and_append_bytes(
    path: Path,
    data: bytes,
    *,
    expected_previous_bytes: bytes | None,
    allowed_root: Path,
    mode: int,
) -> None:
    del mode
    _windows_append_bytes(
        path,
        data,
        expected_previous_bytes=expected_previous_bytes,
        allowed_root=allowed_root,
    )


def _is_capability_error(error: BaseException) -> bool:
    return isinstance(error, AtomicWriteError) and str(error) == _CAPABILITY_ERROR


def read_regular_file_bytes(
    path: Path,
    *,
    allowed_root: Path,
    allow_non_atomic: bool = False,
) -> bytes:
    """Read one confined regular file, failing closed unless fallback is explicit."""

    try:
        if os.name == "nt":
            return _windows_read_regular_file_bytes(path, allowed_root=allowed_root)
        return _posix_read_regular_file_bytes(path, allowed_root=allowed_root)
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        return _fallback_read_regular_file_bytes(path, allowed_root=allowed_root)


def atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> None:
    """Securely replace a file, with only an explicit non-atomic fallback."""

    try:
        if os.name == "nt":
            _windows_atomic_replace_bytes(path, data, allowed_root=allowed_root, mode=mode)
        else:
            _posix_atomic_replace_bytes(path, data, allowed_root=allowed_root, mode=mode)
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        _fallback_write(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            operation="replace",
        )


def atomic_create_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> bool:
    """Securely create a file, with only an explicit non-atomic fallback."""

    try:
        if os.name == "nt":
            return _windows_atomic_create_bytes(
                path, data, allowed_root=allowed_root, mode=mode
            )
        return _posix_atomic_create_bytes(
            path, data, allowed_root=allowed_root, mode=mode
        )
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        result = _fallback_write(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            operation="create",
        )
        return result is True


def atomic_create_bytes_tracked(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> TrackedFileCreation | None:
    """Create a file and retain native cleanup authority for that exact identity."""

    if os.name == "nt":
        return _windows_atomic_create_bytes_tracked(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
        )
    return _posix_atomic_create_bytes_tracked(
        path,
        data,
        allowed_root=allowed_root,
        mode=mode,
    )


def track_existing_regular_file(
    path: Path,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    allowed_root: Path,
) -> TrackedExistingFile:
    """Retain platform-scoped recovery authority over one expected file.

    Exactly one name must exist with ``expected_bytes``. The returned stateful
    authority supports resumable canonical-to-quarantine relocation, separate
    quarantine-only disposal, or release without mutation. Canonical-path
    replacement before or during relocation remains fail-closed. POSIX callers
    must hold the install lock over a private state root so no uncooperative
    same-identity process retains a writable descriptor or reads, guesses, or
    replaces the quarantine capability name. Portable POSIX APIs cannot
    conditionally unlink by retained inode, so disposal does not protect against
    that explicitly excluded class. The native handle branch retains its
    stronger verified-handle guarantee: relocation flushes the writable file
    handle and verifies the live name and bytes; disposal closes the
    delete-marked handle and proves the quarantine name absent. Because that
    branch does not fsync directory metadata, it makes no power-loss durability
    claim for those namespace transitions.
    """

    if not isinstance(expected_bytes, bytes):
        raise TypeError("expected_bytes must be bytes")
    _quarantine_name(quarantine_id)
    if os.name == "nt":
        return _windows_track_existing_regular_file(
            path,
            expected_bytes,
            quarantine_id=quarantine_id,
            allowed_root=allowed_root,
        )
    return _posix_track_existing_regular_file(
        path,
        expected_bytes,
        quarantine_id=quarantine_id,
        allowed_root=allowed_root,
    )


def read_regular_directory_entries(
    root: Path,
) -> tuple[ConfinedRegularFile, ...]:
    """Read one directory entirely through retained no-follow native handles."""

    if os.name == "nt":
        return _windows_read_regular_directory_entries(root)
    return _posix_read_regular_directory_entries(root)


def atomic_append_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> None:
    """Append one complete frame, never selecting non-atomic behavior silently."""

    try:
        if os.name == "nt":
            _windows_atomic_append_bytes(path, data, allowed_root=allowed_root, mode=mode)
        else:
            _posix_atomic_append_bytes(path, data, allowed_root=allowed_root, mode=mode)
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        _fallback_write(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            operation="append",
        )


def atomic_compare_and_append_bytes(
    path: Path,
    data: bytes,
    *,
    expected_previous_bytes: bytes | None,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> None:
    """Lock, compare complete predecessor bytes, append one frame, and reread."""

    if not isinstance(data, bytes) or (
        expected_previous_bytes is not None
        and not isinstance(expected_previous_bytes, bytes)
    ):
        raise TypeError("compare-and-append requires bytes or a missing predecessor")
    try:
        if os.name == "nt":
            _windows_atomic_compare_and_append_bytes(
                path,
                data,
                expected_previous_bytes=expected_previous_bytes,
                allowed_root=allowed_root,
                mode=mode,
            )
        else:
            _posix_atomic_append_bytes(
                path,
                data,
                expected_previous_bytes=expected_previous_bytes,
                allowed_root=allowed_root,
                mode=mode,
            )
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        _fallback_compare_and_append(
            path,
            data,
            expected_previous_bytes=expected_previous_bytes,
            allowed_root=allowed_root,
            mode=mode,
        )
