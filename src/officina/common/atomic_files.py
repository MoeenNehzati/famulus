"""Fail-closed atomic writes confined to an allowed directory tree."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
from enum import Enum
import hashlib
import os
import secrets
import stat
from pathlib import Path
from typing import Callable, Mapping


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
    """coordinate _fields_ through POINTER, and ctypes with one closed state transition.

    Intent
    ------
    coordinate _fields_ through POINTER, and ctypes with one closed state transition. The boundary coordinates _fields_ through POINTER, and ctypes with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through POINTER, and ctypes with one closed state transition. Keep POINTER, and ctypes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Length", ctypes.c_uint16), ("MaximumLength", ctypes.c_uint16),
                ("Buffer", ctypes.POINTER(ctypes.c_uint16))]


class _WinObjectAttributes(ctypes.Structure):
    """coordinate _fields_ through POINTER, ctypes, _WinHandle, and _WinUnicodeString with one closed state transition.

    Intent
    ------
    coordinate _fields_ through POINTER, ctypes, _WinHandle, and _WinUnicodeString with one closed state transition. The boundary coordinates _fields_ through POINTER, ctypes, _WinHandle, and _WinUnicodeString with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through POINTER, ctypes, _WinHandle, and _WinUnicodeString with one closed state transition. Keep POINTER, ctypes, _WinHandle, and _WinUnicodeString inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Length", ctypes.c_uint32), ("RootDirectory", _WinHandle),
                ("ObjectName", ctypes.POINTER(_WinUnicodeString)),
                ("Attributes", ctypes.c_uint32),
                ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p)]


class _WinIoStatusValue(ctypes.Union):
    """Represent the native completion status or pointer union returned by NT calls.

    Intent
    ------
    Preserve the overlaid signed status and pointer layout expected by IO_STATUS_BLOCK.

    Rationale
    ---------
    The union prevents Python-side field layout from diverging from the native ABI.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Status", ctypes.c_int32), ("Pointer", ctypes.c_void_p)]


class _WinIoStatusBlock(ctypes.Structure):
    """coordinate _anonymous_, and _fields_ through ctypes, _WinIoStatusValue, and _WinUlongPtr with one closed state transition.

    Intent
    ------
    coordinate _anonymous_, and _fields_ through ctypes, _WinIoStatusValue, and _WinUlongPtr with one closed state transition. The boundary coordinates _anonymous_, and _fields_ through ctypes, _WinIoStatusValue, and _WinUlongPtr with one closed state transition.

    Rationale
    ---------
    Because coordinate _anonymous_, and _fields_ through ctypes, _WinIoStatusValue, and _WinUlongPtr with one closed state transition. Keep ctypes, _WinIoStatusValue, and _WinUlongPtr inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _anonymous_ = ("Value",)
    _fields_ = [("Value", _WinIoStatusValue), ("Information", _WinUlongPtr)]


class _WinFileAttributeTagInfo(ctypes.Structure):
    """Represent native file attributes together with the no-follow reparse tag.

    Intent
    ------
    Carry the two fields needed to classify a retained name without following reparse points.

    Rationale
    ---------
    Keeping attributes and tag in one native record makes type checks coherent.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("FileAttributes", ctypes.c_uint32), ("ReparseTag", ctypes.c_uint32)]


class _WinFileBasicInfo(ctypes.Structure):
    """Represent native timestamps and the observable file-attribute word.

    Intent
    ------
    Expose FILE_BASIC_INFO so publication can set and verify the supported read-only mode policy.

    Rationale
    ---------
    The exact native layout avoids claiming permission bits this platform cannot observe.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [
        ("CreationTime", ctypes.c_int64),
        ("LastAccessTime", ctypes.c_int64),
        ("LastWriteTime", ctypes.c_int64),
        ("ChangeTime", ctypes.c_int64),
        ("FileAttributes", ctypes.c_uint32),
    ]


class _WinFileStandardInfo(ctypes.Structure):
    """Represent native file size, link count, and deletion/type state.

    Intent
    ------
    Expose FILE_STANDARD_INFO so an existing private build can prove exclusive
    single-name ownership before any permission or byte mutation.

    Rationale
    ---------
    A stable file identifier does not reveal whether another hard-link name
    designates the same object; NumberOfLinks closes that mutation boundary.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [
        ("AllocationSize", ctypes.c_int64),
        ("EndOfFile", ctypes.c_int64),
        ("NumberOfLinks", ctypes.c_uint32),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class _WinFileIdInfo(ctypes.Structure):
    """Represent a volume serial number and stable 128-bit native file identity.

    Intent
    ------
    Bind a retained handle to the identity later rechecked through its live name.

    Rationale
    ---------
    Both volume and file identifier are required to reject cross-volume aliasing.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("VolumeSerialNumber", ctypes.c_uint64),
                ("FileId", ctypes.c_ubyte * 16)]


class _WinOverlappedOffsets(ctypes.Structure):
    """Represent the offset-or-pointer arm of a native OVERLAPPED request.

    Intent
    ------
    Preserve the native union arm used by bounded positional handle IO.

    Rationale
    ---------
    Exact offsets keep asynchronous structures ABI-compatible even for synchronous waits.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32)]


class _WinOverlappedValue(ctypes.Union):
    """coordinate _fields_ through ctypes, and _WinOverlappedOffsets with one closed state transition.

    Intent
    ------
    coordinate _fields_ through ctypes, and _WinOverlappedOffsets with one closed state transition. The boundary coordinates _fields_ through ctypes, and _WinOverlappedOffsets with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through ctypes, and _WinOverlappedOffsets with one closed state transition. Keep ctypes, and _WinOverlappedOffsets inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Offsets", _WinOverlappedOffsets), ("Pointer", ctypes.c_void_p)]


class _WinOverlapped(ctypes.Structure):
    """coordinate _anonymous_, and _fields_ through ctypes, _WinUlongPtr, _WinOverlappedValue, and _WinHandle with one closed state transition.

    Intent
    ------
    coordinate _anonymous_, and _fields_ through ctypes, _WinUlongPtr, _WinOverlappedValue, and _WinHandle with one closed state transition. The boundary coordinates _anonymous_, and _fields_ through ctypes, _WinUlongPtr, _WinOverlappedValue, and _WinHandle with one closed state transition.

    Rationale
    ---------
    Because coordinate _anonymous_, and _fields_ through ctypes, _WinUlongPtr, _WinOverlappedValue, and _WinHandle with one closed state transition. Keep ctypes, _WinUlongPtr, _WinOverlappedValue, and _WinHandle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _anonymous_ = ("Value",)
    _fields_ = [("Internal", _WinUlongPtr), ("InternalHigh", _WinUlongPtr),
                ("Value", _WinOverlappedValue), ("hEvent", _WinHandle)]


class _WinSecurityDescriptor(ctypes.Structure):
    """Represent the owner, group, control, SACL, and DACL pointers of a security descriptor.

    Intent
    ------
    Expose the fields inspected when proving a retained handle has restrictive ownership.

    Rationale
    ---------
    A typed descriptor keeps ACL validation aligned with the native self-relative layout.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Revision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
                ("Control", ctypes.c_uint16), ("Owner", ctypes.c_void_p),
                ("Group", ctypes.c_void_p), ("Sacl", ctypes.c_void_p),
                ("Dacl", ctypes.c_void_p)]


class _WinAclSizeInformation(ctypes.Structure):
    """Represent ACE count plus used and free byte totals for a native ACL.

    Intent
    ------
    Bound ACL iteration by native entry count and allocation size before reading ACEs.

    Rationale
    ---------
    Explicit size accounting prevents an ACL walk from trusting unbounded pointer data.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("AceCount", ctypes.c_uint32),
                ("AclBytesInUse", ctypes.c_uint32),
                ("AclBytesFree", ctypes.c_uint32)]


class _WinAceHeader(ctypes.Structure):
    """Represent the type, flags, and byte size prefix shared by native ACEs.

    Intent
    ------
    Read the common header before interpreting an access-control entry body.

    Rationale
    ---------
    The prefix permits bounded advancement across variable-sized ACE records.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                ("AceSize", ctypes.c_uint16)]


class _WinAccessAllowedAce(ctypes.Structure):
    """coordinate _fields_ through ctypes, and _WinAceHeader with one closed state transition.

    Intent
    ------
    coordinate _fields_ through ctypes, and _WinAceHeader with one closed state transition. The boundary coordinates _fields_ through ctypes, and _WinAceHeader with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through ctypes, and _WinAceHeader with one closed state transition. Keep ctypes, and _WinAceHeader inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Header", _WinAceHeader), ("Mask", ctypes.c_uint32),
                ("SidStart", ctypes.c_uint32)]


class _WinSidAndAttributes(ctypes.Structure):
    """Represent a native SID pointer paired with its token attribute mask.

    Intent
    ------
    Carry the retained process-owner identity returned by token inspection.

    Rationale
    ---------
    Pairing the SID and mask preserves the TOKEN_USER ABI contract.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]


class _WinTokenUser(ctypes.Structure):
    """coordinate _fields_ through ctypes, and _WinSidAndAttributes with one closed state transition.

    Intent
    ------
    coordinate _fields_ through ctypes, and _WinSidAndAttributes with one closed state transition. The boundary coordinates _fields_ through ctypes, and _WinSidAndAttributes with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through ctypes, and _WinSidAndAttributes with one closed state transition. Keep ctypes, and _WinSidAndAttributes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("User", _WinSidAndAttributes)]


class _WinFileDispositionInformation(ctypes.Structure):
    """Represent the delete-on-close flag used for exact retained-handle unlink.

    Intent
    ------
    Supply the one native boolean that marks the opened reparse point for deletion.

    Rationale
    ---------
    Handle-scoped disposition avoids reopening an attacker-selected lexical name.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


class _WinFileRenameInfo(ctypes.Structure):
    """coordinate _fields_ through ctypes, and _WinHandle with one closed state transition.

    Intent
    ------
    coordinate _fields_ through ctypes, and _WinHandle with one closed state transition. The boundary coordinates _fields_ through ctypes, and _WinHandle with one closed state transition.

    Rationale
    ---------
    Because coordinate _fields_ through ctypes, and _WinHandle with one closed state transition. Keep ctypes, and _WinHandle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    _fields_ = [("Flags", ctypes.c_uint32), ("RootDirectory", _WinHandle),
                ("FileNameLength", ctypes.c_uint32),
                ("FileName", ctypes.c_uint16 * 1)]


def _windows_component_utf16(name: str) -> bytes:
    """Validate one relative component and return its bounded UTF-16 form.

    Intent
    ------
    Validate one relative component and return its bounded UTF-16 form. The boundary coordinates name, and encoded through rstrip, AtomicWriteError, encode, str, name, and UnicodeEncodeError with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals.

    Rationale
    ---------
    Because Validate one relative component and return its bounded UTF-16 form. Keep rstrip, AtomicWriteError, encode, str, name, and UnicodeEncodeError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Validate one relative component and return its bounded UTF-16 form."
    """

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
    """Build the shared FILE_RENAME_INFO/EX variable-length buffer.

    Intent
    ------
    Build the shared FILE_RENAME_INFO/EX variable-length buffer. The boundary coordinates name, parent_handle, replace, encoded, and size through _windows_component_utf16, sizeof, create_string_buffer, cast, POINTER, and memmove with 1 guarded checks.

    Rationale
    ---------
    Because Build the shared FILE_RENAME_INFO/EX variable-length buffer. Keep _windows_component_utf16, sizeof, create_string_buffer, cast, POINTER, and memmove inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_component_utf16:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Build the shared FILE_RENAME_INFO/EX variable-length buffer."
    """
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
    """coordinate closed local state through OSError with one closed state transition.

    Intent
    ------
    coordinate closed local state through OSError with one closed state transition. The boundary coordinates closed local state through OSError with one closed state transition.

    Rationale
    ---------
    Because coordinate closed local state through OSError with one closed state transition. Keep OSError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    pass


@dataclass(frozen=True)
class ConfinedFileIdentity:
    """Stable native identity for one confined regular file.

    Intent
    ------
    Stable native identity for one confined regular file. The boundary coordinates platform, volume, and file_id through str, int, and bytes with one closed state transition.

    Rationale
    ---------
    Because Stable native identity for one confined regular file. Keep str, int, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    platform: str
    volume: int
    file_id: int | bytes


@dataclass(frozen=True)
class ConfinedRegularFile:
    """One regular file read relative to a retained directory handle.

    Intent
    ------
    One regular file read relative to a retained directory handle. The boundary coordinates name, data, and identity through str, bytes, and ConfinedFileIdentity with one closed state transition.

    Rationale
    ---------
    Because One regular file read relative to a retained directory handle. Keep str, bytes, and ConfinedFileIdentity inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    name: str
    data: bytes
    identity: ConfinedFileIdentity


class TrackedFileCreation:
    """Own the retained handles needed to clean up one exact created file.

    Intent
    ------
    Own the retained handles needed to clean up one exact created file. The boundary coordinates closed local state through closed local state with one closed state transition.

    Rationale
    ---------
    Because Own the retained handles needed to clean up one exact created file. Keep closed local state inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        identity: ConfinedFileIdentity,
        *,
        remove: Callable[[], None],
        release: Callable[[], None],
    ) -> None:
        """Retain exact identity plus removal and release callbacks for one created file.

        Intent
        ------
        Within Own the retained handles needed to clean up one exact created file, coordinate identity, remove, release, _remove, and _release through ConfinedFileIdentity, Callable, self, identity, remove, and release with. The boundary coordinates identity, remove, release, _remove, and _release through ConfinedFileIdentity, Callable, self, identity, remove, and release with one closed state transition.

        Rationale
        ---------
        Because Within Own the retained handles needed to clean up one exact created file, coordinate identity, remove, release, _remove, and _release through ConfinedFileIdentity, Callable, self, identity, remove, and release with. Keep ConfinedFileIdentity, Callable, self, identity, remove, and release inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self.identity = identity
        self._remove = remove
        self._release = release
        self._closed = False

    def remove(self) -> None:
        """Remove only the exact created file, then release retained handles.

        Intent
        ------
        Remove only the exact created file, then release retained handles. The boundary coordinates _closed through AtomicWriteError, _remove, _release, and self with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

        Rationale
        ---------
        Because Remove only the exact created file, then release retained handles. Keep AtomicWriteError, _remove, _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Remove only the exact created file, then release retained handles."
        """

        if self._closed:
            raise AtomicWriteError("tracked file creation is already closed")
        try:
            self._remove()
        finally:
            self._closed = True
            self._release()

    def release(self) -> None:
        """Retain the created file and release its cleanup handles.

        Intent
        ------
        Retain the created file and release its cleanup handles. The boundary coordinates _closed through _release, and self with 1 guarded checks.

        Rationale
        ---------
        Because Retain the created file and release its cleanup handles. Keep _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        if self._closed:
            return
        self._closed = True
        self._release()


class TrackedFileLocation(str, Enum):
    """Closed location state for one journal-addressable recovery file.

    Intent
    ------
    Closed location state for one journal-addressable recovery file. The boundary coordinates CANONICAL, and QUARANTINE through str, and Enum with one closed state transition.

    Rationale
    ---------
    Because Closed location state for one journal-addressable recovery file. Keep str, and Enum inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    CANONICAL = "canonical"
    QUARANTINE = "quarantine"


class TrackedExistingFile:
    """Retain platform-scoped authority over one recovery-file transaction.

    Intent
    ------
    Retain platform-scoped authority over one recovery-file transaction. The boundary coordinates closed local state through closed local state with one closed state transition.

    Rationale
    ---------
    Because Retain platform-scoped authority over one recovery-file transaction. Keep closed local state inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        identity: ConfinedFileIdentity,
        location: TrackedFileLocation,
        *,
        relocate: Callable[[], None],
        dispose: Callable[[], None],
        release: Callable[[], None],
        after_relocate: Callable[[], None] | None = None,
        after_dispose: Callable[[], None] | None = None,
    ) -> None:
        """Retain identity and lifecycle callbacks for one recovery-file transaction.

        Intent
        ------
        Within Retain platform-scoped authority over one recovery-file transaction, coordinate identity, location, relocate, dispose, and release through ConfinedFileIdentity, TrackedFileLocation, Callable, self, identity, a. The boundary coordinates identity, location, relocate, dispose, and release through ConfinedFileIdentity, TrackedFileLocation, Callable, self, identity, and location with one closed state transition.

        Rationale
        ---------
        Because Within Retain platform-scoped authority over one recovery-file transaction, coordinate identity, location, relocate, dispose, and release through ConfinedFileIdentity, TrackedFileLocation, Callable, self, identity, a. Keep ConfinedFileIdentity, TrackedFileLocation, Callable, self, identity, and location inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self.identity = identity
        self._location = location
        self._relocate = relocate
        self._dispose = dispose
        self._release = release
        self._after_relocate = after_relocate or (lambda: None)
        self._after_dispose = after_dispose or (lambda: None)
        self._closed = False

    @property
    def location(self) -> TrackedFileLocation:
        """Return the currently verified canonical or quarantine location.

        Intent
        ------
        Return the currently verified canonical or quarantine location. The boundary coordinates closed local state through self, property, and TrackedFileLocation with one closed state transition.

        Rationale
        ---------
        Because Return the currently verified canonical or quarantine location. Keep self, property, and TrackedFileLocation inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        return self._location

    def _close_after_failure(self) -> None:
        """Within Retain platform-scoped authority over one recovery-file transaction, coordinate _closed through _release, and self with one closed state transition.

        Intent
        ------
        Within Retain platform-scoped authority over one recovery-file transaction, coordinate _closed through _release, and self with one closed state transition. The boundary coordinates _closed through _release, and self with one closed state transition.

        Rationale
        ---------
        Because Within Retain platform-scoped authority over one recovery-file transaction, coordinate _closed through _release, and self with one closed state transition. Keep _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self._closed = True
        self._release()

    def relocate(self) -> None:
        """Move the verified canonical entry to its journaled quarantine.

        Intent
        ------
        Move the verified canonical entry to its journaled quarantine. The boundary coordinates _location through AtomicWriteError, _relocate, _after_relocate, _close_after_failure, self, and BaseException with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

        Rationale
        ---------
        Because Move the verified canonical entry to its journaled quarantine. Keep AtomicWriteError, _relocate, _after_relocate, _close_after_failure, self, and BaseException inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Move the verified canonical entry to its journaled quarantine."
        """

        if self._closed:
            raise AtomicWriteError("tracked existing file is already closed")
        try:
            self._relocate()
            self._after_relocate()
        except BaseException:
            self._close_after_failure()
            raise
        self._location = TrackedFileLocation.QUARANTINE

    def dispose(self) -> None:
        """Dispose from quarantine under the documented platform precondition.

        Intent
        ------
        Dispose from quarantine under the documented platform precondition. The boundary coordinates _closed through AtomicWriteError, _close_after_failure, _dispose, _after_dispose, _release, and self with 2 guarded checks, 2 cleanup or failure regions, and 2 typed refusals.

        Rationale
        ---------
        Because Dispose from quarantine under the documented platform precondition. Keep AtomicWriteError, _close_after_failure, _dispose, _after_dispose, _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - set verified_quarantine_disposal = local_decisions
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Dispose from quarantine under the documented platform precondition."
        """

        if self._closed:
            raise AtomicWriteError("tracked existing file is already closed")
        if self._location is not TrackedFileLocation.QUARANTINE:
            try:
                raise AtomicWriteError("tracked existing file must be quarantined")
            finally:
                self._close_after_failure()
        try:
            self._dispose()
            self._after_dispose()
        finally:
            self._closed = True
            self._release()

    def release(self) -> None:
        """Preserve the verified entry and release retained native authority.

        Intent
        ------
        Preserve the verified entry and release retained native authority. The boundary coordinates _closed through _release, and self with 1 guarded checks.

        Rationale
        ---------
        Because Preserve the verified entry and release retained native authority. Keep _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        if self._closed:
            return
        self._closed = True
        self._release()


class RetainedBoundedDirectoryInventory:
    """One closed directory-name set bound to retained native root authority.

    Intent
    ------
    One closed directory-name set bound to retained native root authority. The boundary coordinates closed local state through closed local state with one closed state transition.

    Rationale
    ---------
    Because One closed directory-name set bound to retained native root authority. Keep closed local state inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    def __init__(
        self,
        names: tuple[str, ...],
        *,
        read_regular_file: Callable[[str, int], ConfinedRegularFile],
        track_existing: Callable[
            [str, bytes, str, Callable[[], None], Callable[[], None]],
            TrackedExistingFile,
        ],
        replace_regular_file: Callable[
            [
                str,
                bytes,
                int,
                str,
                str,
                Callable[[], None],
                Callable[[], None],
                Callable[[], None],
            ],
            None,
        ],
        discard_staged_regular_file: Callable[
            [str, bytes, str, str, Callable[[], None]], None
        ],
        revalidate: Callable[[tuple[str, ...]], None],
        release: Callable[[], None],
    ) -> None:
        """Within One closed directory-name set bound to retained native root authority, coordinate names, read_regular_file, track_existing, replace_regular_file, and discard_staged_regular_file through tuple, str, Callable, i.

        Intent
        ------
        Within One closed directory-name set bound to retained native root authority, coordinate names, read_regular_file, track_existing, replace_regular_file, and discard_staged_regular_file through tuple, str, Callable, i. The boundary coordinates names, read_regular_file, track_existing, replace_regular_file, and discard_staged_regular_file through tuple, str, Callable, int, ConfinedRegularFile, and bytes with one closed state transition.

        Rationale
        ---------
        Because Within One closed directory-name set bound to retained native root authority, coordinate names, read_regular_file, track_existing, replace_regular_file, and discard_staged_regular_file through tuple, str, Callable, i. Keep tuple, str, Callable, int, ConfinedRegularFile, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self._names = names
        self._read_regular_file = read_regular_file
        self._track_existing = track_existing
        self._replace_regular_file = replace_regular_file
        self._discard_staged_regular_file = discard_staged_regular_file
        self._revalidate = revalidate
        self._release = release
        self._closed = False

    def _require_open(self) -> None:
        """Within One closed directory-name set bound to retained native root authority, coordinate closed local state through AtomicWriteError, and self with 1 guarded checks, and 1 typed refusals.

        Intent
        ------
        Within One closed directory-name set bound to retained native root authority, coordinate closed local state through AtomicWriteError, and self with 1 guarded checks, and 1 typed refusals. The boundary coordinates closed local state through AtomicWriteError, and self with 1 guarded checks, and 1 typed refusals.

        Rationale
        ---------
        Because Within One closed directory-name set bound to retained native root authority, coordinate closed local state through AtomicWriteError, and self with 1 guarded checks, and 1 typed refusals. Keep AtomicWriteError, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Within One closed directory-name set bound to retained native root authority, coordinate closed local state through AtomicWriteError, and self with 1 guarded checks, and 1 typed refusals."
        """
        if self._closed:
            raise AtomicWriteError("retained directory inventory is closed")

    @property
    def names(self) -> tuple[str, ...]:
        """Return the exact bounded names currently authorized by the owner.

        Intent
        ------
        Return the exact bounded names currently authorized by the owner. The boundary coordinates closed local state through _require_open, self, property, tuple, and str with one closed state transition.

        Rationale
        ---------
        Because Return the exact bounded names currently authorized by the owner. Keep _require_open, self, property, tuple, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        self._require_open()
        return self._names

    def read_regular_file(
        self,
        name: str,
        *,
        maximum_bytes: int,
    ) -> ConfinedRegularFile:
        """Read at most ``maximum_bytes`` from one initially observed name.

        Intent
        ------
        Read at most ``maximum_bytes`` from one initially observed name. The boundary coordinates name, and maximum_bytes through _require_open, AtomicWriteError, _read_regular_file, str, int, and self with 1 guarded checks, and 1 typed refusals.

        Rationale
        ---------
        Because Read at most ``maximum_bytes`` from one initially observed name. Keep _require_open, AtomicWriteError, _read_regular_file, str, int, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read at most ``maximum_bytes`` from one initially observed name."
        """

        self._require_open()
        if name not in self._names:
            raise AtomicWriteError("relative name is not in retained inventory")
        return self._read_regular_file(name, maximum_bytes)

    def track_existing_regular_file(
        self,
        canonical_name: str,
        expected_bytes: bytes,
        *,
        quarantine_id: str,
    ) -> TrackedExistingFile:
        """Track one expected canonical-or-quarantine file under this root.

        Intent
        ------
        Track one expected canonical-or-quarantine file under this root. The boundary coordinates canonical_name, expected_bytes, quarantine_id, and quarantine through _require_open, _quarantine_name, AtomicWriteError, _track_existing, str, and bytes with 1 guarded checks, and 1 typed refusals.

        Rationale
        ---------
        Because Track one expected canonical-or-quarantine file under this root. Keep _require_open, _quarantine_name, AtomicWriteError, _track_existing, str, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Track one expected canonical-or-quarantine file under this root."
        ._quarantine_name:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Track one expected canonical-or-quarantine file under this root."
        """

        self._require_open()
        quarantine = _quarantine_name(quarantine_id)
        if canonical_name not in self._names and quarantine not in self._names:
            raise AtomicWriteError("relative name is not in retained inventory")

        def after_relocate() -> None:
            """Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Intent
            ------
            Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. The boundary coordinates current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Rationale
            ---------
            Because Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. Keep set, discard, add, tuple, sorted, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.discard(canonical_name)
            current.add(quarantine)
            self._names = tuple(sorted(current))

        def after_dispose() -> None:
            """Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition.

            Intent
            ------
            Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition. The boundary coordinates current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition.

            Rationale
            ---------
            Because Within Track one expected canonical-or-quarantine file under this root, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition. Keep set, discard, tuple, sorted, self, and current inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.discard(canonical_name)
            current.discard(quarantine)
            self._names = tuple(sorted(current))

        return self._track_existing(
            canonical_name,
            expected_bytes,
            quarantine_id,
            after_relocate,
            after_dispose,
        )

    def replace_regular_file(
        self,
        name: str,
        data: bytes,
        *,
        mode: int,
        staging_capability: str,
    ) -> None:
        """Build, publish, and replace through restart-addressable names.

        Intent
        ------
        Build, publish, and replace through restart-addressable names. The boundary coordinates name, data, mode, staging_capability, and build_name through _require_open, build_file_name, staged_file_name, AtomicWriteError, _replace_regular_file, and str with 1 guarded checks, and 1 typed refusals.

        Rationale
        ---------
        Because Build, publish, and replace through restart-addressable names. Keep _require_open, build_file_name, staged_file_name, AtomicWriteError, _replace_regular_file, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Build, publish, and replace through restart-addressable names."
        .build_file_name:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Build, publish, and replace through restart-addressable names."
        .staged_file_name:
          why:
            constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Build, publish, and replace through restart-addressable names."
        """

        self._require_open()
        build_name = build_file_name(staging_capability)
        staging_name = staged_file_name(staging_capability)
        if build_name in self._names and staging_name in self._names:
            raise AtomicWriteError("selector build and stage are ambiguous")

        def after_built() -> None:
            """Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, add, tuple, sorted, self, and current with one closed state transition.

            Intent
            ------
            Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, add, tuple, sorted, self, and current with one closed state transition. The boundary coordinates current, and _names through set, add, tuple, sorted, self, and current with one closed state transition.

            Rationale
            ---------
            Because Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, add, tuple, sorted, self, and current with one closed state transition. Keep set, add, tuple, sorted, self, and current inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.add(build_name)
            self._names = tuple(sorted(current))

        def after_staged() -> None:
            """Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Intent
            ------
            Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. The boundary coordinates current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Rationale
            ---------
            Because Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. Keep set, discard, add, tuple, sorted, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.discard(build_name)
            current.add(staging_name)
            self._names = tuple(sorted(current))

        def after_replaced() -> None:
            """Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Intent
            ------
            Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. The boundary coordinates current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition.

            Rationale
            ---------
            Because Within Build, publish, and replace through restart-addressable names, coordinate current, and _names through set, discard, add, tuple, sorted, and self with one closed state transition. Keep set, discard, add, tuple, sorted, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.discard(staging_name)
            current.add(name)
            self._names = tuple(sorted(current))

        self._replace_regular_file(
            name,
            data,
            mode,
            build_name,
            staging_name,
            after_built,
            after_staged,
            after_replaced,
        )

    def discard_selector_transaction(
        self,
        name: str,
        expected_bytes: bytes,
        *,
        staging_capability: str,
    ) -> None:
        """Discard a partial build or exact published selector stage.

        Intent
        ------
        Discard a partial build or exact published selector stage. The boundary coordinates name, expected_bytes, staging_capability, build_name, and staging_name through _require_open, build_file_name, staged_file_name, AtomicWriteError, _discard_staged_regular_file, and str with 2 guarded checks, and 2 typed refusals.

        Rationale
        ---------
        Because Discard a partial build or exact published selector stage. Keep _require_open, build_file_name, staged_file_name, AtomicWriteError, _discard_staged_regular_file, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .AtomicWriteError:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Discard a partial build or exact published selector stage."
        .build_file_name:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Discard a partial build or exact published selector stage."
        .staged_file_name:
          why:
            constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Discard a partial build or exact published selector stage."
        """

        self._require_open()
        build_name = build_file_name(staging_capability)
        staging_name = staged_file_name(staging_capability)
        if build_name in self._names and staging_name in self._names:
            raise AtomicWriteError("selector build and stage are ambiguous")
        if build_name not in self._names and staging_name not in self._names:
            raise AtomicWriteError("selector transaction is not in retained inventory")

        def after_discarded() -> None:
            """Within Discard a partial build or exact published selector stage, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition.

            Intent
            ------
            Within Discard a partial build or exact published selector stage, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition. The boundary coordinates current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition.

            Rationale
            ---------
            Because Within Discard a partial build or exact published selector stage, coordinate current, and _names through set, discard, tuple, sorted, self, and current with one closed state transition. Keep set, discard, tuple, sorted, self, and current inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current = set(self._names)
            current.discard(build_name)
            current.discard(staging_name)
            self._names = tuple(sorted(current))

        self._discard_staged_regular_file(
            name,
            expected_bytes,
            build_name,
            staging_name,
            after_discarded,
        )

    def discard_staged_regular_file(
        self,
        name: str,
        expected_bytes: bytes,
        *,
        staging_capability: str,
    ) -> None:
        """Compatibility alias for selector-transaction disposal.

        Intent
        ------
        Compatibility alias for selector-transaction disposal. The boundary coordinates name, expected_bytes, and staging_capability through discard_selector_transaction, str, bytes, self, name, and expected_bytes with one closed state transition.

        Rationale
        ---------
        Because Compatibility alias for selector-transaction disposal. Keep discard_selector_transaction, str, bytes, self, name, and expected_bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        self.discard_selector_transaction(
            name,
            expected_bytes,
            staging_capability=staging_capability,
        )

    def revalidate(self) -> None:
        """Revalidate the retained root identity and complete authorized names.

        Intent
        ------
        Revalidate the retained root identity and complete authorized names. The boundary coordinates closed local state through _require_open, _revalidate, and self with one closed state transition.

        Rationale
        ---------
        Because Revalidate the retained root identity and complete authorized names. Keep _require_open, _revalidate, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        self._require_open()
        self._revalidate(self._names)

    def release(self) -> None:
        """Release retained native authority without mutating directory entries.

        Intent
        ------
        Release retained native authority without mutating directory entries. The boundary coordinates _closed through _release, and self with 1 guarded checks.

        Rationale
        ---------
        Because Release retained native authority without mutating directory entries. Keep _release, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """

        if self._closed:
            return
        self._closed = True
        self._release()

    def __enter__(self) -> "RetainedBoundedDirectoryInventory":
        """Within One closed directory-name set bound to retained native root authority, coordinate closed local state through _require_open, and self with one closed state transition.

        Intent
        ------
        Within One closed directory-name set bound to retained native root authority, coordinate closed local state through _require_open, and self with one closed state transition. The boundary coordinates closed local state through _require_open, and self with one closed state transition.

        Rationale
        ---------
        Because Within One closed directory-name set bound to retained native root authority, coordinate closed local state through _require_open, and self with one closed state transition. Keep _require_open, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self._require_open()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> None:
        """Within One closed directory-name set bound to retained native root authority, coordinate _exception_type, _exception, and _traceback through release, type, BaseException, object, and self with one closed state transi.

        Intent
        ------
        Within One closed directory-name set bound to retained native root authority, coordinate _exception_type, _exception, and _traceback through release, type, BaseException, object, and self with one closed state transi. The boundary coordinates _exception_type, _exception, and _traceback through release, type, BaseException, object, and self with one closed state transition.

        Rationale
        ---------
        Because Within One closed directory-name set bound to retained native root authority, coordinate _exception_type, _exception, and _traceback through release, type, BaseException, object, and self with one closed state transi. Keep release, type, BaseException, object, and self inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        self.release()


def _posix_file_identity(metadata: os.stat_result) -> ConfinedFileIdentity:
    """coordinate metadata through ConfinedFileIdentity, os, int, and metadata with one closed state transition.

    Intent
    ------
    coordinate metadata through ConfinedFileIdentity, os, int, and metadata with one closed state transition. The boundary coordinates metadata through ConfinedFileIdentity, os, int, and metadata with one closed state transition.

    Rationale
    ---------
    Because coordinate metadata through ConfinedFileIdentity, os, int, and metadata with one closed state transition. Keep ConfinedFileIdentity, os, int, and metadata inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ConfinedFileIdentity:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate metadata through ConfinedFileIdentity, os, int, and metadata with one closed state transition."
    """
    return ConfinedFileIdentity(
        platform="posix",
        volume=int(metadata.st_dev),
        file_id=int(metadata.st_ino),
    )


def _require_secure_operations() -> None:
    """coordinate supports_dir_fd, supports_follow_symlinks, required_functions, operation, and name through getattr, set, any, hasattr, AtomicWriteError, and os with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate supports_dir_fd, supports_follow_symlinks, required_functions, operation, and name through getattr, set, any, hasattr, AtomicWriteError, and os with 1 guarded checks, and 1 typed refusals. The boundary coordinates supports_dir_fd, supports_follow_symlinks, required_functions, operation, and name through getattr, set, any, hasattr, AtomicWriteError, and os with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate supports_dir_fd, supports_follow_symlinks, required_functions, operation, and name through getattr, set, any, hasattr, AtomicWriteError, and os with 1 guarded checks, and 1 typed refusals. Keep getattr, set, any, hasattr, AtomicWriteError, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate supports_dir_fd, supports_follow_symlinks, required_functions, operation, and name through getattr, set, any, hasattr, AtomicWriteError, and os with 1 guarded checks, and 1 typed refusals."
    """
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
    """coordinate path, flags, mode, and dir_fd through open, AtomicWriteError, str, Path, int, and os with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate path, flags, mode, and dir_fd through open, AtomicWriteError, str, Path, int, and os with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates path, flags, mode, and dir_fd through open, AtomicWriteError, str, Path, int, and os with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate path, flags, mode, and dir_fd through open, AtomicWriteError, str, Path, int, and os with 1 cleanup or failure regions, and 1 typed refusals. Keep open, AtomicWriteError, str, Path, int, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, flags, mode, and dir_fd through open, AtomicWriteError, str, Path, int, and os with 1 cleanup or failure regions, and 1 typed refusals."
    """
    try:
        return os.open(path, flags, mode, dir_fd=dir_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_stat(parent_fd: int, name: str) -> os.stat_result:
    """coordinate parent_fd, and name through stat, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate parent_fd, and name through stat, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates parent_fd, and name through stat, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, and name through stat, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals. Keep stat, AtomicWriteError, int, str, os, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, and name through stat, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals."
    """
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_fchmod(descriptor: int, mode: int) -> None:
    """coordinate descriptor, and mode through fchmod, AtomicWriteError, int, os, descriptor, and mode with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate descriptor, and mode through fchmod, AtomicWriteError, int, os, descriptor, and mode with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates descriptor, and mode through fchmod, AtomicWriteError, int, os, descriptor, and mode with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, and mode through fchmod, AtomicWriteError, int, os, descriptor, and mode with 1 cleanup or failure regions, and 1 typed refusals. Keep fchmod, AtomicWriteError, int, os, descriptor, and mode inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, and mode through fchmod, AtomicWriteError, int, os, descriptor, and mode with 1 cleanup or failure regions, and 1 typed refusals."
    """
    try:
        os.fchmod(descriptor, mode)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_replace(parent_fd: int, source: str, destination: str) -> None:
    """coordinate parent_fd, source, and destination through replace, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate parent_fd, source, and destination through replace, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates parent_fd, source, and destination through replace, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, source, and destination through replace, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals. Keep replace, AtomicWriteError, int, str, os, and source inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, source, and destination through replace, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals."
    """
    try:
        os.replace(source, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_link(parent_fd: int, source: str, destination: str) -> None:
    """coordinate parent_fd, source, and destination through link, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate parent_fd, source, and destination through link, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates parent_fd, source, and destination through link, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, source, and destination through link, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals. Keep link, AtomicWriteError, int, str, os, and source inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, source, and destination through link, AtomicWriteError, int, str, os, and source with 1 cleanup or failure regions, and 1 typed refusals."
    """
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
    """coordinate parent_fd, and name through unlink, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate parent_fd, and name through unlink, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals. The boundary coordinates parent_fd, and name through unlink, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, and name through unlink, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals. Keep unlink, AtomicWriteError, int, str, os, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, and name through unlink, AtomicWriteError, int, str, os, and name with 1 cleanup or failure regions, and 1 typed refusals."
    """
    try:
        os.unlink(name, dir_fd=parent_fd)
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc


def _secure_rename_noreplace(
    parent_fd: int,
    source: str,
    destination: str,
) -> None:
    """Atomically move one relative entry without replacing another entry.

    Intent
    ------
    Atomically move one relative entry without replacing another entry. The boundary coordinates parent_fd, source, destination, library, and source_bytes through CDLL, fsencode, getattr, set_errno, function, and get_errno with 5 guarded checks, 1 bounded iterations, and 4 typed refusals.

    Rationale
    ---------
    Because Atomically move one relative entry without replacing another entry. Keep CDLL, fsencode, getattr, set_errno, function, and get_errno inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Atomically move one relative entry without replacing another entry."
    """

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
    """Derive one bounded internal name from a durable 128-bit capability.

    Intent
    ------
    Derive one bounded internal name from a durable 128-bit capability. The boundary coordinates quarantine_id, and character through isinstance, TypeError, any, ValueError, str, and quarantine_id with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Derive one bounded internal name from a durable 128-bit capability. Keep isinstance, TypeError, any, ValueError, str, and quarantine_id inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    if not isinstance(quarantine_id, str):
        raise TypeError("quarantine_id must be a string")
    if (
        len(quarantine_id) != 32
        or any(character not in "0123456789abcdef" for character in quarantine_id)
    ):
        raise ValueError("quarantine_id must be 32 lowercase hexadecimal characters")
    return f".famulus-quarantine-{quarantine_id}"


def staged_file_name(staging_capability: str) -> str:
    """Derive one deterministic confined name from a durable 128-bit capability.

    Intent
    ------
    Derive one deterministic confined name from a durable 128-bit capability. The boundary coordinates staging_capability, and character through isinstance, TypeError, any, ValueError, str, and staging_capability with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Derive one deterministic confined name from a durable 128-bit capability. Keep isinstance, TypeError, any, ValueError, str, and staging_capability inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    if not isinstance(staging_capability, str):
        raise TypeError("staging_capability must be a string")
    if (
        len(staging_capability) != 32
        or any(
            character not in "0123456789abcdef"
            for character in staging_capability
        )
    ):
        raise ValueError(
            "staging_capability must be 32 lowercase hexadecimal characters"
        )
    return f".famulus-staged-{staging_capability}"


def build_file_name(staging_capability: str) -> str:
    """Derive the deterministic private selector-build name.

    Intent
    ------
    Derive the deterministic private selector-build name. The boundary coordinates staging_capability through staged_file_name, str, and staging_capability with one closed state transition.

    Rationale
    ---------
    Because Derive the deterministic private selector-build name. Keep staged_file_name, str, and staging_capability inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set validated_build_selector = received_context
    - return validated_build_selector

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .staged_file_name:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Derive the deterministic private selector-build name."
    """

    staged_file_name(staging_capability)
    return f".famulus-build-{staging_capability}"


def _open_parent(path: Path, allowed_root: Path) -> tuple[int, str]:
    """coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded.

    Intent
    ------
    coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded. The boundary coordinates path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded iterations, and 7 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded. Keep _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._require_secure_operations:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded."
    ._open_absolute_directory:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through _require_secure_operations, absolute, Path, relative_to, AtomicWriteError, and any with 1 guarded checks, 5 cleanup or failure regions, 1 bounded."
    """
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
    """Walk an absolute POSIX directory from ``/`` without following links.

    Intent
    ------
    Walk an absolute POSIX directory from ``/`` without following links. The boundary coordinates path, create, absolute, flags, and descriptor through _require_secure_operations, absolute, Path, is_absolute, AtomicWriteError, and _secure_open with 3 guarded checks, 3 cleanup or failure regions, 1 bounded iterations, and 6 typed refusals.

    Rationale
    ---------
    Because Walk an absolute POSIX directory from ``/`` without following links. Keep _require_secure_operations, absolute, Path, is_absolute, AtomicWriteError, and _secure_open inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._require_secure_operations:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Walk an absolute POSIX directory from ``/`` without following links."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Walk an absolute POSIX directory from ``/`` without following links."
    ._secure_open:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Walk an absolute POSIX directory from ``/`` without following links."
    """
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
    """Create and validate an absolute directory through retained no-follow handles.

    Intent
    ------
    Create and validate an absolute directory through retained no-follow handles. The boundary coordinates path, absolute, descriptor, and handle through absolute, Path, _open_absolute_directory, close, _windows_open_root, and _windows_close_handle with 2 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Create and validate an absolute directory through retained no-follow handles. Keep absolute, Path, _open_absolute_directory, close, _windows_open_root, and _windows_close_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create and validate an absolute directory through retained no-follow handles."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Create and validate an absolute directory through retained no-follow handles."
    ._open_absolute_directory:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Create and validate an absolute directory through retained no-follow handles."
    ._windows_open_root:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Create and validate an absolute directory through retained no-follow handles."
    """
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
    """coordinate parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Intent
    ------
    coordinate parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals. The boundary coordinates parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals. Keep _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate parent_fd, name, and entry through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals."
    """
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
    """coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals.

    Intent
    ------
    coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals. The boundary coordinates parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals. Keep _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._secure_fchmod:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals."
    ._unlink_if_present:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals."

    InstantiationsFromRepo
    ----------------------
    ._secure_open:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate parent_fd, temp_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 3 cleanup or failure regions, and 1 typed refusals."
    """
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


def _open_inventory_build(parent_fd: int, build_name: str, mode: int) -> int:
    """Create one deterministic private build with read/write authority.

    Intent
    ------
    Create one deterministic private build with read/write authority. The boundary coordinates parent_fd, build_name, mode, and descriptor through _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str with 2 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Create one deterministic private build with read/write authority. Keep _secure_open, _secure_fchmod, close, _unlink_if_present, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._secure_fchmod:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create one deterministic private build with read/write authority."
    ._unlink_if_present:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Create one deterministic private build with read/write authority."

    InstantiationsFromRepo
    ----------------------
    ._secure_open:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Create one deterministic private build with read/write authority."
    """

    descriptor = _secure_open(
        build_name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=parent_fd,
    )
    try:
        _secure_fchmod(descriptor, mode)
    except BaseException:
        try:
            os.close(descriptor)
        finally:
            _unlink_if_present(parent_fd, build_name)
        raise
    return descriptor


def _write_and_sync(descriptor: int, data: bytes) -> None:
    """coordinate descriptor, data, handle, cleanup_error, and primary_error through fdopen, close, write, flush, fsync, and fileno with 4 guarded checks, 4 cleanup or failure regions, and 4 typed refusals.

    Intent
    ------
    coordinate descriptor, data, handle, cleanup_error, and primary_error through fdopen, close, write, flush, fsync, and fileno with 4 guarded checks, 4 cleanup or failure regions, and 4 typed refusals. The boundary coordinates descriptor, data, handle, cleanup_error, and primary_error through fdopen, close, write, flush, fsync, and fileno with 4 guarded checks, 4 cleanup or failure regions, and 4 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, data, handle, cleanup_error, and primary_error through fdopen, close, write, flush, fsync, and fileno with 4 guarded checks, 4 cleanup or failure regions, and 4 typed refusals. Keep fdopen, close, write, flush, fsync, and fileno inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
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


def _posix_publication_state(
    parent_fd: int,
    name: str,
    *,
    expected_file_size: int | None = None,
) -> dict[str, object]:
    """Observe one retained-parent entry without following its final name.

    Intent
    ------
    Observe one retained-parent entry without following its final name. The boundary coordinates parent_fd, name, expected_file_size, metadata, and mode through _secure_stat, S_IMODE, S_ISLNK, readlink, AtomicWriteError, and S_ISDIR with 6 guarded checks, 3 cleanup or failure regions, and 4 typed refusals.

    Rationale
    ---------
    Because Observe one retained-parent entry without following its final name. Keep _secure_stat, S_IMODE, S_ISLNK, readlink, AtomicWriteError, and S_ISDIR inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."
    ._read_descriptor_bytes_bounded:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Observe one retained-parent entry without following its final name."
    """

    try:
        metadata = _secure_stat(parent_fd, name)
    except FileNotFoundError:
        return {"kind": "absent"}
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        try:
            return {"kind": "symlink", "target": os.readlink(name, dir_fd=parent_fd)}
        except OSError as exc:
            raise AtomicWriteError("cannot observe publication symlink") from exc
    if stat.S_ISDIR(metadata.st_mode):
        return {"kind": "directory", "mode": mode}
    if not stat.S_ISREG(metadata.st_mode):
        return {"kind": "other", "mode": mode}

    descriptor = -1
    try:
        descriptor = _secure_open(
            name,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        identity = _posix_file_identity(opened)
        if not stat.S_ISREG(opened.st_mode):
            raise AtomicWriteError("publication target changed during observation")
        maximum = min(
            max(opened.st_size, expected_file_size or 0), 1024 * 1024
        ) + 1
        data = _read_descriptor_bytes_bounded(descriptor, maximum)
        linked = _secure_stat(parent_fd, name)
        if (
            not stat.S_ISREG(linked.st_mode)
            or _posix_file_identity(linked) != identity
            or len(data) != opened.st_size
        ):
            raise AtomicWriteError("publication target changed during observation")
        return {
            "kind": "file",
            "mode": stat.S_IMODE(opened.st_mode),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    except FileNotFoundError as exc:
        raise AtomicWriteError("publication target changed during observation") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _posix_rewrite_publication_build(
    parent_fd: int,
    build_name: str,
    data: bytes,
    mode: int,
) -> tuple[ConfinedFileIdentity, int]:
    """Rewrite one deterministic build and retain its validated descriptor.

    Intent
    ------
    Validate an existing no-follow name and single-link identity before repairing
    owner permissions, reopen and revalidate it for rewriting, then retain the
    descriptor after exact bytes and the requested final mode are durable.

    Rationale
    ---------
    The retained descriptor permits exact byte and mode verification even when
    the final mode removes every read bit. Permission repair is relative to the
    retained parent and occurs only after regular-file, identity, and link-count
    checks under the caller-held lock and private-namespace contract.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._read_descriptor_bytes_bounded:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._secure_fchmod:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._write_descriptor_bytes:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._open_inventory_build:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Create or rewrite the one durable-mutation-addressed regular build."
    """

    descriptor = -1
    temporary_mode = mode | stat.S_IRUSR | stat.S_IWUSR
    try:
        try:
            metadata = _secure_stat(parent_fd, build_name)
        except FileNotFoundError:
            descriptor = _open_inventory_build(
                parent_fd, build_name, temporary_mode
            )
            metadata = os.fstat(descriptor)
            identity = _posix_file_identity(metadata)
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise AtomicWriteError("publication build is not a regular file")
            if metadata.st_nlink != 1:
                raise AtomicWriteError("publication build has an unsafe hard link")
            identity = _posix_file_identity(metadata)
            live = _secure_stat(parent_fd, build_name)
            if (
                not stat.S_ISREG(live.st_mode)
                or live.st_nlink != 1
                or _posix_file_identity(live) != identity
            ):
                raise AtomicWriteError("publication build changed before permission repair")
            if stat.S_IMODE(live.st_mode) != temporary_mode:
                try:
                    os.chmod(build_name, temporary_mode, dir_fd=parent_fd)
                except (NotImplementedError, TypeError) as exc:
                    raise AtomicWriteError(_CAPABILITY_ERROR) from exc
                except OSError as exc:
                    raise AtomicWriteError(
                        "cannot restore publication build permissions"
                    ) from exc
            repaired = _secure_stat(parent_fd, build_name)
            if (
                not stat.S_ISREG(repaired.st_mode)
                or repaired.st_nlink != 1
                or _posix_file_identity(repaired) != identity
                or stat.S_IMODE(repaired.st_mode) != temporary_mode
            ):
                raise AtomicWriteError("publication build changed during permission repair")
            try:
                descriptor = _secure_open(
                    build_name,
                    os.O_RDWR
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise AtomicWriteError(
                    "cannot reopen publication build for rewrite"
                ) from exc
            opened = os.fstat(descriptor)
            linked = _secure_stat(parent_fd, build_name)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _posix_file_identity(opened) != identity
                or not stat.S_ISREG(linked.st_mode)
                or linked.st_nlink != 1
                or _posix_file_identity(linked) != identity
            ):
                raise AtomicWriteError("publication build changed before rewrite")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        _write_descriptor_bytes(descriptor, data, Path(build_name))
        os.fsync(descriptor)
        _secure_fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if _read_descriptor_bytes_bounded(descriptor, len(data) + 1) != data:
            raise AtomicWriteError("publication build reread failed")
        linked = _secure_stat(parent_fd, build_name)
        if (
            not stat.S_ISREG(linked.st_mode)
            or _posix_file_identity(linked) != identity
            or linked.st_nlink != 1
            or stat.S_IMODE(linked.st_mode) != mode
        ):
            raise AtomicWriteError("publication build changed during write")
        retained_descriptor = descriptor
        descriptor = -1
        return identity, retained_descriptor
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _posix_require_exact_publication_build(
    parent_fd: int,
    name: str,
    data: bytes,
    mode: int,
    identity: ConfinedFileIdentity,
    descriptor: int,
) -> None:
    """Verify a deterministic build through its retained descriptor and name.

    Intent
    ------
    Verify the retained regular-file identity, link count, exact mode and bytes,
    then prove that the current parent-relative name still denotes that identity.

    Rationale
    ---------
    Retained authority avoids reopening a mode-0000 build while the independent
    no-follow name observation detects namespace drift before and after rename.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "Compares retained and parent-relative objects by native identity."
    ._read_descriptor_bytes_bounded:
      why:
        computes: "Reads the retained descriptor through the exact intended-byte bound."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "Constructs a typed refusal for identity, mode, byte, link, or name drift."
    ._secure_stat:
      why:
        constructs: "Constructs the no-follow parent-relative name snapshot used for comparison."
    """

    try:
        opened = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _posix_file_identity(opened) != identity
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != mode
            or _read_descriptor_bytes_bounded(descriptor, len(data) + 1) != data
        ):
            raise AtomicWriteError("publication build changed before publication")
        linked = _secure_stat(parent_fd, name)
        if (
            not stat.S_ISREG(linked.st_mode)
            or _posix_file_identity(linked) != identity
            or linked.st_nlink != 1
        ):
            raise AtomicWriteError("publication build changed before publication")
    except FileNotFoundError as exc:
        raise AtomicWriteError("publication build changed before publication") from exc


def _posix_atomic_publish_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish exact bytes on POSIX through one deterministic retained build.

    Intent
    ------
    coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re. The boundary coordinates path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re. Keep _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_require_exact_publication_build:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    ._secure_replace:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    ._posix_publication_state:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    ._posix_rewrite_publication_build:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _open_parent, build_file_name, _posix_rewrite_publication_build, fsync, get, and _posix_publication_state with 1 guarded checks, 1 cleanup or failure re."
    """
    parent_fd, name = _open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    build_descriptor = -1
    try:
        identity, build_descriptor = _posix_rewrite_publication_build(
            parent_fd, build_name, data, mode
        )
        os.fsync(parent_fd)
        expected_size = expected_before.get("size")
        actual = _posix_publication_state(
            parent_fd,
            name,
            expected_file_size=(
                expected_size if isinstance(expected_size, int) else None
            ),
        )
        if actual != dict(expected_before):
            raise AtomicWriteError("publication target differs from expected state")
        _posix_require_exact_publication_build(
            parent_fd, build_name, data, mode, identity, build_descriptor
        )
        _secure_replace(parent_fd, build_name, name)
        _posix_require_exact_publication_build(
            parent_fd, name, data, mode, identity, build_descriptor
        )
        os.fsync(parent_fd)
    finally:
        try:
            if build_descriptor >= 0:
                os.close(build_descriptor)
        finally:
            os.close(parent_fd)


def _posix_atomic_publish_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish one exact lexical symlink on POSIX through a deterministic build.

    Intent
    ------
    coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and. The boundary coordinates path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and. Keep _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    ._secure_replace:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    ._posix_publication_state:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, build_id, and expected_before through _open_parent, build_file_name, _secure_stat, symlink, AtomicWriteError, and fsync with 4 guarded checks, 4 cleanup or failure regions, and."
    """
    parent_fd, name = _open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    try:
        try:
            build = _secure_stat(parent_fd, build_name)
        except FileNotFoundError:
            try:
                os.symlink(target, build_name, dir_fd=parent_fd)
            except OSError as exc:
                raise AtomicWriteError("cannot create publication symlink build") from exc
            os.fsync(parent_fd)
            build = _secure_stat(parent_fd, build_name)
        if not stat.S_ISLNK(build.st_mode):
            raise AtomicWriteError("publication symlink build is not a symlink")
        try:
            build_target = os.readlink(build_name, dir_fd=parent_fd)
        except OSError as exc:
            raise AtomicWriteError("cannot observe publication symlink build") from exc
        if build_target != target:
            raise AtomicWriteError("publication symlink build has the wrong target")
        actual = _posix_publication_state(parent_fd, name)
        if actual != dict(expected_before):
            raise AtomicWriteError("publication target differs from expected state")
        retained = _secure_stat(parent_fd, build_name)
        if (
            not stat.S_ISLNK(retained.st_mode)
            or _posix_file_identity(retained) != _posix_file_identity(build)
        ):
            raise AtomicWriteError("publication symlink build changed before replacement")
        _secure_replace(parent_fd, build_name, name)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _posix_atomic_publish_empty_directory(
    path: Path,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions.

    Intent
    ------
    coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions. The boundary coordinates path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions, and 8 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions. Keep _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._secure_fchmod:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._secure_rename_noreplace:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._posix_publication_state:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _open_parent, build_file_name, get, _posix_publication_state, dict, and AtomicWriteError with 8 guarded checks, 4 cleanup or failure regions."
    """
    parent_fd, name = _open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    build_fd = -1
    try:
        if expected_before.get("kind") == "directory":
            actual = _posix_publication_state(parent_fd, name)
            if actual != dict(expected_before):
                raise AtomicWriteError("publication target differs from expected state")
            return
        try:
            metadata = _secure_stat(parent_fd, build_name)
        except FileNotFoundError:
            try:
                os.mkdir(build_name, mode, dir_fd=parent_fd)
            except OSError as exc:
                raise AtomicWriteError("cannot create publication directory build") from exc
            os.fsync(parent_fd)
            metadata = _secure_stat(parent_fd, build_name)
        if not stat.S_ISDIR(metadata.st_mode):
            raise AtomicWriteError("publication directory build is not a directory")
        try:
            build_fd = _secure_open(
                build_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise AtomicWriteError("cannot open publication directory build") from exc
        opened = os.fstat(build_fd)
        if os.listdir(build_fd):
            raise AtomicWriteError("publication directory build is not empty")
        _secure_fchmod(build_fd, mode)
        opened = os.fstat(build_fd)
        linked = _secure_stat(parent_fd, build_name)
        if (
            not stat.S_ISDIR(linked.st_mode)
            or _posix_file_identity(linked) != _posix_file_identity(opened)
            or stat.S_IMODE(opened.st_mode) != mode
            or stat.S_IMODE(linked.st_mode) != mode
        ):
            raise AtomicWriteError("publication directory build changed")
        os.fsync(build_fd)
        actual = _posix_publication_state(parent_fd, name)
        if actual != dict(expected_before):
            raise AtomicWriteError("publication target differs from expected state")
        if actual != {"kind": "absent"}:
            raise AtomicWriteError("directory publication requires an absent target")
        _secure_rename_noreplace(parent_fd, build_name, name)
        os.fsync(parent_fd)
    finally:
        if build_fd >= 0:
            os.close(build_fd)
        os.close(parent_fd)


def _posix_atomic_unlink_exact_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    expected_before: Mapping[str, object],
) -> None:
    """Unlink only the exact expected POSIX symlink from a retained parent.

    Intent
    ------
    coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi. The boundary coordinates path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regions, and 4 typed refusals.

    Rationale
    ---------
    Because coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi. Keep _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."
    ._secure_unlink:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."
    ._posix_publication_state:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, target, allowed_root, expected_before, and parent_fd through _open_parent, _secure_stat, AtomicWriteError, S_ISLNK, _posix_publication_state, and dict with 3 guarded checks, 2 cleanup or failure regi."
    """
    parent_fd, name = _open_parent(path, allowed_root)
    try:
        try:
            metadata = _secure_stat(parent_fd, name)
        except FileNotFoundError as exc:
            raise AtomicWriteError("exact symlink target is absent") from exc
        if not stat.S_ISLNK(metadata.st_mode):
            raise AtomicWriteError("exact symlink target is not a symlink")
        actual = _posix_publication_state(parent_fd, name)
        if actual != dict(expected_before) or actual != {
            "kind": "symlink",
            "target": target,
        }:
            raise AtomicWriteError("exact symlink differs from expected target")
        retained = _secure_stat(parent_fd, name)
        if (
            not stat.S_ISLNK(retained.st_mode)
            or _posix_file_identity(retained) != _posix_file_identity(metadata)
        ):
            raise AtomicWriteError("exact symlink changed before unlink")
        _secure_unlink(parent_fd, name)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _unlink_if_present(parent_fd: int, name: str) -> None:
    """coordinate parent_fd, and name through _secure_unlink, int, str, parent_fd, name, and FileNotFoundError with 1 cleanup or failure regions.

    Intent
    ------
    coordinate parent_fd, and name through _secure_unlink, int, str, parent_fd, name, and FileNotFoundError with 1 cleanup or failure regions. The boundary coordinates parent_fd, and name through _secure_unlink, int, str, parent_fd, name, and FileNotFoundError with 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate parent_fd, and name through _secure_unlink, int, str, parent_fd, name, and FileNotFoundError with 1 cleanup or failure regions. Keep _secure_unlink, int, str, parent_fd, name, and FileNotFoundError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set absence_tolerant_cleanup = local_decisions
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._secure_unlink:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, and name through _secure_unlink, int, str, parent_fd, name, and FileNotFoundError with 1 cleanup or failure regions."
    """
    try:
        _secure_unlink(parent_fd, name)
    except FileNotFoundError:
        pass


def _cleanup_write(
    parent_fd: int,
    temp_name: str,
    temp_created: bool,
) -> BaseException | None:
    """coordinate parent_fd, temp_name, temp_created, and cleanup_error through _unlink_if_present, close, int, str, bool, and BaseException with 2 guarded checks, and 2 cleanup or failure regions.

    Intent
    ------
    coordinate parent_fd, temp_name, temp_created, and cleanup_error through _unlink_if_present, close, int, str, bool, and BaseException with 2 guarded checks, and 2 cleanup or failure regions. The boundary coordinates parent_fd, temp_name, temp_created, and cleanup_error through _unlink_if_present, close, int, str, bool, and BaseException with 2 guarded checks, and 2 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate parent_fd, temp_name, temp_created, and cleanup_error through _unlink_if_present, close, int, str, bool, and BaseException with 2 guarded checks, and 2 cleanup or failure regions. Keep _unlink_if_present, close, int, str, bool, and BaseException inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set tracked_build_cleanup = local_decisions
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._unlink_if_present:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_fd, temp_name, temp_created, and cleanup_error through _unlink_if_present, close, int, str, bool, and BaseException with 2 guarded checks, and 2 cleanup or failure regions."
    """
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
    """coordinate descriptor, parent_fd, cleanup_error, and current through close, int, BaseException, descriptor, parent_fd, and current with 2 guarded checks, 1 cleanup or failure regions, and 1 bounded iterations.

    Intent
    ------
    coordinate descriptor, parent_fd, cleanup_error, and current through close, int, BaseException, descriptor, parent_fd, and current with 2 guarded checks, 1 cleanup or failure regions, and 1 bounded iterations. The boundary coordinates descriptor, parent_fd, cleanup_error, and current through close, int, BaseException, descriptor, parent_fd, and current with 2 guarded checks, 1 cleanup or failure regions, and 1 bounded iterations.

    Rationale
    ---------
    Because coordinate descriptor, parent_fd, cleanup_error, and current through close, int, BaseException, descriptor, parent_fd, and current with 2 guarded checks, 1 cleanup or failure regions, and 1 bounded iterations. Keep close, int, BaseException, descriptor, parent_fd, and current inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
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
    """Read one regular file through a confined, no-follow descriptor walk.

    Intent
    ------
    Read one regular file through a confined, no-follow descriptor walk. The boundary coordinates path, allowed_root, parent_fd, name, and descriptor through _open_parent, getattr, _secure_open, AtomicWriteError, fstat, and S_ISREG with 3 guarded checks, 2 cleanup or failure regions, 1 bounded iterations, and 6 typed refusals.

    Rationale
    ---------
    Because Read one regular file through a confined, no-follow descriptor walk. Keep _open_parent, getattr, _secure_open, AtomicWriteError, fstat, and S_ISREG inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read one regular file through a confined, no-follow descriptor walk."
    ._cleanup_read:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read one regular file through a confined, no-follow descriptor walk."
    ._open_parent:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read one regular file through a confined, no-follow descriptor walk."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Read one regular file through a confined, no-follow descriptor walk."
    """

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


def _posix_read_regular_file_bytes_bounded(
    path: Path,
    *,
    allowed_root: Path,
    maximum_bytes: int,
) -> bytes:
    """Read one regular file without ever retaining more than the caller cap.

    Intent
    ------
    Read one regular file without ever retaining more than the caller cap. The boundary coordinates path, allowed_root, maximum_bytes, parent_fd, and name through _open_parent, _secure_open, getattr, AtomicWriteError, fstat, and S_ISREG with 5 guarded checks, 2 cleanup or failure regions, and 7 typed refusals.

    Rationale
    ---------
    Because Read one regular file without ever retaining more than the caller cap. Keep _open_parent, _secure_open, getattr, AtomicWriteError, fstat, and S_ISREG inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    ._cleanup_read:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    ._read_descriptor_bytes_bounded:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Read one regular file without ever retaining more than the caller cap."
    """

    parent_fd, name = _open_parent(path, allowed_root)
    descriptor = -1
    failure: BaseException | None = None
    try:
        try:
            descriptor = _secure_open(
                name,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno == getattr(os, "ELOOP", 40):
                raise AtomicWriteError(f"source is a symbolic link: {name}") from exc
            raise AtomicWriteError(f"cannot securely open source file: {path}") from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError(f"source is not a regular file: {name}")
        data = _read_descriptor_bytes_bounded(descriptor, maximum_bytes + 1)
        if len(data) > maximum_bytes:
            raise AtomicWriteError("confined file exceeds the caller byte bound")
        linked = _secure_stat(parent_fd, name)
        if (
            not stat.S_ISREG(linked.st_mode)
            or _posix_file_identity(linked) != _posix_file_identity(metadata)
            or len(data) != metadata.st_size
        ):
            raise AtomicWriteError("confined file changed during bounded read")
        return data
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
    """Atomically replace a regular file through a securely opened parent.

    Intent
    ------
    Atomically replace a regular file through a securely opened parent. The boundary coordinates path, data, allowed_root, mode, and parent_fd through _open_parent, token_hex, _reject_unsafe_final, _open_temp, _write_and_sync, and _secure_replace with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Atomically replace a regular file through a securely opened parent. Keep _open_parent, token_hex, _reject_unsafe_final, _open_temp, _write_and_sync, and _secure_replace inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._reject_unsafe_final:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."
    ._secure_replace:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."
    ._write_and_sync:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."

    InstantiationsFromRepo
    ----------------------
    ._cleanup_write:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."
    ._open_temp:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Atomically replace a regular file through a securely opened parent."
    """

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
    """Atomically create a file without ever replacing an existing entry.

    Intent
    ------
    Atomically create a file without ever replacing an existing entry. The boundary coordinates path, data, allowed_root, mode, and parent_fd through _open_parent, token_hex, _reject_unsafe_final, _open_temp, _write_and_sync, and _secure_link with 2 guarded checks, 2 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Atomically create a file without ever replacing an existing entry. Keep _open_parent, token_hex, _reject_unsafe_final, _open_temp, _write_and_sync, and _secure_link inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._reject_unsafe_final:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    ._secure_link:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    ._unlink_if_present:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    ._write_and_sync:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."

    InstantiationsFromRepo
    ----------------------
    ._cleanup_write:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    ._open_temp:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Atomically create a file without ever replacing an existing entry."
    """

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
    """Create a file and retain its parent descriptor for exact cleanup.

    Intent
    ------
    Create a file and retain its parent descriptor for exact cleanup. The boundary coordinates path, data, allowed_root, mode, and parent_fd through _open_parent, token_hex, _reject_unsafe_final, _open_temp, _posix_file_identity, and fstat with 6 guarded checks, 4 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because Create a file and retain its parent descriptor for exact cleanup. Keep _open_parent, token_hex, _reject_unsafe_final, _open_temp, _posix_file_identity, and fstat inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._reject_unsafe_final:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._secure_link:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._secure_unlink:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._unlink_if_present:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._write_and_sync:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    .TrackedFileCreation:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._cleanup_write:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._open_temp:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: Create a file and retain its parent descriptor for exact cleanup."
    """

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
            """Within Create a file and retain its parent descriptor for exact cleanup, coordinate closed local state through close, os, and parent_fd with one closed state transition.

            Intent
            ------
            Within Create a file and retain its parent descriptor for exact cleanup, coordinate closed local state through close, os, and parent_fd with one closed state transition. The boundary coordinates closed local state through close, os, and parent_fd with one closed state transition.

            Rationale
            ---------
            Because Within Create a file and retain its parent descriptor for exact cleanup, coordinate closed local state through close, os, and parent_fd with one closed state transition. Keep close, os, and parent_fd inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            os.close(parent_fd)

        def remove() -> None:
            """Within Create a file and retain its parent descriptor for exact cleanup, coordinate current through _secure_stat, AtomicWriteError, S_ISREG, _posix_file_identity, _secure_unlink, and fsync with 1 guarded checks, 1 cl.

            Intent
            ------
            Within Create a file and retain its parent descriptor for exact cleanup, coordinate current through _secure_stat, AtomicWriteError, S_ISREG, _posix_file_identity, _secure_unlink, and fsync with 1 guarded checks, 1 cl. The boundary coordinates current through _secure_stat, AtomicWriteError, S_ISREG, _posix_file_identity, _secure_unlink, and fsync with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

            Rationale
            ---------
            Because Within Create a file and retain its parent descriptor for exact cleanup, coordinate current through _secure_stat, AtomicWriteError, S_ISREG, _posix_file_identity, _secure_unlink, and fsync with 1 guarded checks, 1 cl. Keep _secure_stat, AtomicWriteError, S_ISREG, _posix_file_identity, _secure_unlink, and fsync inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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


def _posix_track_existing_regular_file_at(
    parent_fd: int,
    name: str,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    display_path: Path,
    after_relocate: Callable[[], None] | None = None,
    after_dispose: Callable[[], None] | None = None,
) -> TrackedExistingFile:
    """Retain authority over one canonical-or-quarantined regular file.

    Intent
    ------
    Retain authority over one canonical-or-quarantined regular file. The boundary coordinates parent_fd, name, expected_bytes, quarantine_id, and display_path through _quarantine_name, stat_optional, AtomicWriteError, FileNotFoundError, getattr, and _secure_open with 9 guarded checks, 3 cleanup or failure regions, and 12 typed refusals.

    Rationale
    ---------
    Because Retain authority over one canonical-or-quarantined regular file. Keep _quarantine_name, stat_optional, AtomicWriteError, FileNotFoundError, getattr, and _secure_open inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._read_descriptor_bytes_bounded:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._secure_rename_noreplace:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._secure_unlink:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    .TrackedExistingFile:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._cleanup_read:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._quarantine_name:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Retain authority over one canonical-or-quarantined regular file."
    """

    quarantine = _quarantine_name(quarantine_id)
    descriptor = -1
    transferred = False
    failure: BaseException | None = None
    try:
        def stat_optional(candidate: str) -> os.stat_result | None:
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate through _secure_stat, str, parent_fd, candidate, FileNotFoundError, and os with 1 cleanup or failure regions.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate through _secure_stat, str, parent_fd, candidate, FileNotFoundError, and os with 1 cleanup or failure regions. The boundary coordinates candidate through _secure_stat, str, parent_fd, candidate, FileNotFoundError, and os with 1 cleanup or failure regions.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate through _secure_stat, str, parent_fd, candidate, FileNotFoundError, and os with 1 cleanup or failure regions. Keep _secure_stat, str, parent_fd, candidate, FileNotFoundError, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
                f"cannot securely open observed file: {display_path}"
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
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate cleanup_error through _cleanup_read, descriptor, parent_fd, and cleanup_error with 1 guarded checks, and 1 typed refusals.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate cleanup_error through _cleanup_read, descriptor, parent_fd, and cleanup_error with 1 guarded checks, and 1 typed refusals. The boundary coordinates cleanup_error through _cleanup_read, descriptor, parent_fd, and cleanup_error with 1 guarded checks, and 1 typed refusals.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate cleanup_error through _cleanup_read, descriptor, parent_fd, and cleanup_error with 1 guarded checks, and 1 typed refusals. Keep _cleanup_read, descriptor, parent_fd, and cleanup_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            cleanup_error = _cleanup_read(descriptor, parent_fd)
            if cleanup_error is not None:
                raise cleanup_error

        def exact_named_identity(candidate: str) -> bool:
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate, and current through _secure_stat, S_ISREG, _posix_file_identity, str, parent_fd, and candidate with 1 cleanup or failure r.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate, and current through _secure_stat, S_ISREG, _posix_file_identity, str, parent_fd, and candidate with 1 cleanup or failure r. The boundary coordinates candidate, and current through _secure_stat, S_ISREG, _posix_file_identity, str, parent_fd, and candidate with 1 cleanup or failure regions.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate candidate, and current through _secure_stat, S_ISREG, _posix_file_identity, str, parent_fd, and candidate with 1 cleanup or failure r. Keep _secure_stat, S_ISREG, _posix_file_identity, str, parent_fd, and candidate inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            try:
                current = _secure_stat(parent_fd, candidate)
            except FileNotFoundError:
                return False
            return (
                stat.S_ISREG(current.st_mode)
                and _posix_file_identity(current) == identity
            )

        def exact_retained_bytes() -> bool:
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through _read_descriptor_bytes_bounded, descriptor, len, expected_bytes, and bool with one closed state transition.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through _read_descriptor_bytes_bounded, descriptor, len, expected_bytes, and bool with one closed state transition. The boundary coordinates closed local state through _read_descriptor_bytes_bounded, descriptor, len, expected_bytes, and bool with one closed state transition.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through _read_descriptor_bytes_bounded, descriptor, len, expected_bytes, and bool with one closed state transition. Keep _read_descriptor_bytes_bounded, descriptor, len, expected_bytes, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            return (
                _read_descriptor_bytes_bounded(
                    descriptor,
                    len(expected_bytes) + 1,
                )
                == expected_bytes
            )

        def relocate() -> None:
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, fsync, and _secure_re.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, fsync, and _secure_re. The boundary coordinates matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, fsync, and _secure_rename_noreplace with 7 guarded checks, 2 cleanup or failure regions, and 8 typed refusals.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, fsync, and _secure_re. Keep stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, fsync, and _secure_rename_noreplace inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
                """Within Coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, and exact_retained_bytes with 7 guarded checks, 2 cleanup or failure regions, and 8 typed refusals, coordina.

                Intent
                ------
                Within Coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, and exact_retained_bytes with 7 guarded checks, 2 cleanup or failure regions, and 8 typed refusals, coordina. The boundary coordinates closed local state through _secure_rename_noreplace, AtomicWriteError, fsync, parent_fd, quarantine, and name with 1 cleanup or failure regions, and 1 typed refusals.

                Rationale
                ---------
                Because Within Coordinate matches, and at_quarantine through stat_optional, AtomicWriteError, exact_named_identity, and exact_retained_bytes with 7 guarded checks, 2 cleanup or failure regions, and 8 typed refusals, coordina. Keep _secure_rename_noreplace, AtomicWriteError, fsync, parent_fd, quarantine, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

                Pseudocode
                ----------
                - return

                Wraps
                -----
                - none
                """
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
            """Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, _secure_unlink, and fsync wit.

            Intent
            ------
            Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, _secure_unlink, and fsync wit. The boundary coordinates closed local state through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, _secure_unlink, and fsync with 2 guarded checks, and 2 typed refusals.

            Rationale
            ---------
            Because Within Retain authority over one canonical-or-quarantined regular file, coordinate closed local state through stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, _secure_unlink, and fsync wit. Keep stat_optional, AtomicWriteError, exact_named_identity, exact_retained_bytes, _secure_unlink, and fsync inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            after_relocate=after_relocate,
            after_dispose=after_dispose,
        )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if not transferred:
            cleanup_error = _cleanup_read(descriptor, parent_fd)
            if failure is None and cleanup_error is not None:
                raise cleanup_error


def _posix_track_existing_regular_file(
    path: Path,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    allowed_root: Path,
) -> TrackedExistingFile:
    """coordinate path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition.

    Intent
    ------
    coordinate path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition. The boundary coordinates path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition.

    Rationale
    ---------
    Because coordinate path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition. Keep _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._open_parent:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition."
    ._posix_track_existing_regular_file_at:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, expected_bytes, quarantine_id, allowed_root, and parent_fd through _open_parent, _posix_track_existing_regular_file_at, Path, bytes, str, and path with one closed state transition."
    """
    parent_fd, name = _open_parent(path, allowed_root)
    return _posix_track_existing_regular_file_at(
        parent_fd,
        name,
        expected_bytes,
        quarantine_id=quarantine_id,
        display_path=path,
    )


def _posix_read_regular_directory_entries(
    root: Path,
) -> tuple[ConfinedRegularFile, ...]:
    """Read a directory through one retained no-follow descriptor walk.

    Intent
    ------
    Read a directory through one retained no-follow descriptor walk. The boundary coordinates root, descriptor, names, entries, and name through _open_absolute_directory, sorted, listdir, AtomicWriteError, _secure_open, and getattr with 2 guarded checks, 4 cleanup or failure regions, 1 bounded iterations, and 4 typed refusals.

    Rationale
    ---------
    Because Read a directory through one retained no-follow descriptor walk. Keep _open_absolute_directory, sorted, listdir, AtomicWriteError, _secure_open, and getattr inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    .ConfinedRegularFile:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    ._open_absolute_directory:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    ._read_descriptor_bytes:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Read a directory through one retained no-follow descriptor walk."
    """

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


def _validate_directory_name_bounds(
    max_entries: int,
    max_name_bytes: int,
) -> None:
    """coordinate max_entries, and max_name_bytes through isinstance, ValueError, int, max_entries, bool, and max_name_bytes with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate max_entries, and max_name_bytes through isinstance, ValueError, int, max_entries, bool, and max_name_bytes with 1 guarded checks, and 1 typed refusals. The boundary coordinates max_entries, and max_name_bytes through isinstance, ValueError, int, max_entries, bool, and max_name_bytes with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate max_entries, and max_name_bytes through isinstance, ValueError, int, max_entries, bool, and max_name_bytes with 1 guarded checks, and 1 typed refusals. Keep isinstance, ValueError, int, max_entries, bool, and max_name_bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    if (
        isinstance(max_entries, bool)
        or not isinstance(max_entries, int)
        or not 1 <= max_entries <= 4096
        or isinstance(max_name_bytes, bool)
        or not isinstance(max_name_bytes, int)
        or not 1 <= max_name_bytes <= 4096
    ):
        raise ValueError("directory name bounds are invalid")


def _bounded_directory_name(
    name: str,
    *,
    max_name_bytes: int,
) -> str:
    """coordinate name, max_name_bytes, and encoded through AtomicWriteError, fsencode, str, int, name, and os with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals.

    Intent
    ------
    coordinate name, max_name_bytes, and encoded through AtomicWriteError, fsencode, str, int, name, and os with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals. The boundary coordinates name, max_name_bytes, and encoded through AtomicWriteError, fsencode, str, int, name, and os with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate name, max_name_bytes, and encoded through AtomicWriteError, fsencode, str, int, name, and os with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals. Keep AtomicWriteError, fsencode, str, int, name, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate name, max_name_bytes, and encoded through AtomicWriteError, fsencode, str, int, name, and os with 2 guarded checks, 1 cleanup or failure regions, and 3 typed refusals."
    """
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise AtomicWriteError("invalid confined directory entry name")
    try:
        encoded = os.fsencode(name)
    except (TypeError, UnicodeError):
        raise AtomicWriteError("invalid confined directory entry name") from None
    if len(encoded) > max_name_bytes:
        raise AtomicWriteError("confined directory name limit exceeded")
    return name


def _posix_bounded_directory_names_at(
    descriptor: int,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[str, ...]:
    """Enumerate bounded names through one already retained descriptor.

    Intent
    ------
    Enumerate bounded names through one already retained descriptor. The boundary coordinates descriptor, max_entries, max_name_bytes, names, and iterator through scandir, append, _bounded_directory_name, AtomicWriteError, tuple, and sorted with 1 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 2 typed refusals.

    Rationale
    ---------
    Because Enumerate bounded names through one already retained descriptor. Keep scandir, append, _bounded_directory_name, AtomicWriteError, tuple, and sorted inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Enumerate bounded names through one already retained descriptor."
    ._bounded_directory_name:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Enumerate bounded names through one already retained descriptor."
    """

    names: list[str] = []
    try:
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                names.append(
                    _bounded_directory_name(
                        entry.name,
                        max_name_bytes=max_name_bytes,
                    )
                )
                if len(names) > max_entries:
                    raise AtomicWriteError(
                        "confined directory entry limit exceeded"
                    )
    except (NotImplementedError, TypeError) as exc:
        raise AtomicWriteError(_CAPABILITY_ERROR) from exc
    return tuple(sorted(names))


def _posix_read_bounded_directory_names(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[str, ...]:
    """Enumerate bounded names without opening or reading any child entry.

    Intent
    ------
    Enumerate bounded names without opening or reading any child entry. The boundary coordinates root, max_entries, max_name_bytes, and descriptor through _open_absolute_directory, _posix_bounded_directory_names_at, close, Path, int, and root with 1 cleanup or failure regions.

    Rationale
    ---------
    Because Enumerate bounded names without opening or reading any child entry. Keep _open_absolute_directory, _posix_bounded_directory_names_at, close, Path, int, and root inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._open_absolute_directory:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Enumerate bounded names without opening or reading any child entry."
    ._posix_bounded_directory_names_at:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Enumerate bounded names without opening or reading any child entry."
    """

    descriptor = _open_absolute_directory(root, create=False)
    try:
        return _posix_bounded_directory_names_at(
            descriptor,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )
    finally:
        os.close(descriptor)


def _posix_read_inventory_regular_file(
    descriptor: int,
    name: str,
    maximum_bytes: int,
) -> ConfinedRegularFile:
    """coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type.

    Intent
    ------
    coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type. The boundary coordinates descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type. Keep _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type."
    .ConfinedRegularFile:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type."
    ._read_descriptor_bytes_bounded:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate descriptor, name, maximum_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and ConfinedRegularFile with 2 guarded checks, 1 cleanup or failure regions, and 2 type."
    """
    child = -1
    try:
        child = _secure_open(
            name,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=descriptor,
        )
        metadata = os.fstat(child)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError("confined directory entry is not a regular file")
        return ConfinedRegularFile(
            name=name,
            data=_read_descriptor_bytes_bounded(child, maximum_bytes),
            identity=_posix_file_identity(metadata),
        )
    except FileNotFoundError as exc:
        raise AtomicWriteError("confined directory entry disappeared") from exc
    finally:
        if child >= 0:
            os.close(child)


def _posix_require_exact_inventory_file(
    descriptor: int,
    name: str,
    expected_bytes: bytes,
) -> ConfinedFileIdentity:
    """Open and retain one exact regular inventory file after bounded verification.

    Intent
    ------
    coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty. The boundary coordinates descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty. Keep _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."
    ._read_descriptor_bytes_bounded:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate descriptor, name, expected_bytes, child, and metadata through _secure_open, getattr, fstat, S_ISREG, AtomicWriteError, and _posix_file_identity with 5 guarded checks, 1 cleanup or failure regions, and 6 ty."
    """
    child = -1
    try:
        child = _secure_open(
            name,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=descriptor,
        )
        metadata = os.fstat(child)
        if not stat.S_ISREG(metadata.st_mode):
            raise AtomicWriteError("staged entry is not a regular file")
        identity = _posix_file_identity(metadata)
        if _read_descriptor_bytes_bounded(child, len(expected_bytes) + 1) != expected_bytes:
            raise AtomicWriteError("staged file bytes do not match expectation")
        linked = _secure_stat(descriptor, name)
        if not stat.S_ISREG(linked.st_mode) or _posix_file_identity(linked) != identity:
            raise AtomicWriteError("staged file changed during observation")
        return identity
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno == getattr(os, "ELOOP", 40):
            raise AtomicWriteError("staged entry is a symbolic link") from exc
        raise
    finally:
        if child >= 0:
            os.close(child)


def _posix_replace_inventory_regular_file(
    descriptor: int,
    name: str,
    data: bytes,
    mode: int,
    build_name: str,
    staging_name: str,
    after_built: Callable[[], None],
    after_staged: Callable[[], None],
    after_replaced: Callable[[], None],
) -> None:
    """coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c.

    Intent
    ------
    coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c. The boundary coordinates descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded checks, 2 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c. Keep _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._posix_require_exact_inventory_file:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._read_descriptor_bytes_bounded:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._reject_unsafe_final:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._secure_fchmod:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._secure_rename_noreplace:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._secure_replace:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._write_descriptor_bytes:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._open_inventory_build:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._posix_inventory_entry_present:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: coordinate descriptor, name, data, mode, and build_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_open, getattr, and _open_inventory_build with 9 guarded c."
    """
    build_present = _posix_inventory_entry_present(descriptor, build_name)
    stage_present = _posix_inventory_entry_present(descriptor, staging_name)
    if build_present and stage_present:
        raise AtomicWriteError("selector build and stage are ambiguous")
    if stage_present:
        _posix_require_exact_inventory_file(descriptor, staging_name, data)
    else:
        child = -1
        if build_present:
            try:
                child = _secure_open(
                    build_name,
                    os.O_RDWR
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
            except OSError as exc:
                if exc.errno == getattr(os, "ELOOP", 40):
                    raise AtomicWriteError("selector build is a symbolic link") from exc
                raise
        else:
            child = _open_inventory_build(descriptor, build_name, mode)
            after_built()
        try:
            metadata = os.fstat(child)
            if not stat.S_ISREG(metadata.st_mode):
                raise AtomicWriteError("selector build is not a regular file")
            identity = _posix_file_identity(metadata)
            _secure_fchmod(child, mode)
            os.ftruncate(child, 0)
            os.lseek(child, 0, os.SEEK_SET)
            _write_descriptor_bytes(child, data, Path(build_name))
            os.fsync(child)
            if _read_descriptor_bytes_bounded(child, len(data) + 1) != data:
                raise AtomicWriteError("selector build reread failed")
            linked = _secure_stat(descriptor, build_name)
            if (
                not stat.S_ISREG(linked.st_mode)
                or _posix_file_identity(linked) != identity
            ):
                raise AtomicWriteError("selector build changed during write")
        finally:
            if child >= 0:
                os.close(child)
        os.fsync(descriptor)
        _secure_rename_noreplace(descriptor, build_name, staging_name)
        os.fsync(descriptor)
        after_staged()
        _posix_require_exact_inventory_file(descriptor, staging_name, data)
    if stage_present:
        after_staged()
    _reject_unsafe_final(descriptor, name)
    _posix_require_exact_inventory_file(descriptor, staging_name, data)
    _secure_replace(descriptor, staging_name, name)
    os.fsync(descriptor)
    after_replaced()


def _posix_inventory_entry_present(descriptor: int, name: str) -> bool:
    """coordinate descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Intent
    ------
    coordinate descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals. The boundary coordinates descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals. Keep _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate descriptor, name, and metadata through _secure_stat, S_ISLNK, AtomicWriteError, S_ISREG, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals."
    """
    try:
        metadata = _secure_stat(descriptor, name)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        raise AtomicWriteError("selector private entry is a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise AtomicWriteError("selector private entry is not a regular file")
    return True


def _posix_discard_staged_inventory_regular_file(
    descriptor: int,
    _name: str,
    expected_bytes: bytes,
    build_name: str,
    staging_name: str,
    after_discarded: Callable[[], None],
) -> None:
    """coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE.

    Intent
    ------
    coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE. The boundary coordinates descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISREG with 4 guarded checks, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE. Keep _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISREG inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_file_identity:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    ._secure_unlink:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    ._posix_inventory_entry_present:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    ._posix_require_exact_inventory_file:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate descriptor, _name, expected_bytes, build_name, and staging_name through _posix_inventory_entry_present, AtomicWriteError, _posix_require_exact_inventory_file, _secure_stat, _posix_file_identity, and S_ISRE."
    """
    build_present = _posix_inventory_entry_present(descriptor, build_name)
    stage_present = _posix_inventory_entry_present(descriptor, staging_name)
    if build_present and stage_present:
        raise AtomicWriteError("selector build and stage are ambiguous")
    private_name = build_name if build_present else staging_name
    if not build_present and not stage_present:
        raise AtomicWriteError("selector transaction disappeared")
    if stage_present:
        identity = _posix_require_exact_inventory_file(
            descriptor,
            staging_name,
            expected_bytes,
        )
    else:
        current = _secure_stat(descriptor, build_name)
        identity = _posix_file_identity(current)
    current = _secure_stat(descriptor, private_name)
    if not stat.S_ISREG(current.st_mode) or _posix_file_identity(current) != identity:
        raise AtomicWriteError("selector private file changed before disposal")
    _secure_unlink(descriptor, private_name)
    os.fsync(descriptor)
    after_discarded()


def _posix_retain_bounded_directory_inventory(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> RetainedBoundedDirectoryInventory:
    """coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close.

    Intent
    ------
    coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close. The boundary coordinates root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close with 1 guarded checks, and 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close. Keep _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._bounded_directory_name:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_discard_staged_inventory_regular_file:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_file_identity:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_replace_inventory_regular_file:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    .RetainedBoundedDirectoryInventory:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._open_absolute_directory:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_bounded_directory_names_at:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_file_identity:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_read_inventory_regular_file:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    ._posix_track_existing_regular_file_at:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, _posix_bounded_directory_names_at, RetainedBoundedDirectoryInventory, and close."
    """
    descriptor = _open_absolute_directory(root, create=False)
    transferred = False
    try:
        root_identity = _posix_file_identity(os.fstat(descriptor))
        names = _posix_bounded_directory_names_at(
            descriptor,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )

        def read_regular_file(name: str, maximum_bytes: int) -> ConfinedRegularFile:
            """Read one bounded regular child through the retained directory descriptor.

            Intent
            ------
            Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. The boundary coordinates name, and maximum_bytes through _bounded_directory_name, _posix_read_inventory_regular_file, str, int, name, and max_name_bytes with one closed state transition.

            Rationale
            ---------
            Because Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. Keep _bounded_directory_name, _posix_read_inventory_regular_file, str, int, name, and max_name_bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            return _posix_read_inventory_regular_file(
                descriptor,
                name,
                maximum_bytes,
            )

        def track_existing(
            name: str,
            expected_bytes: bytes,
            quarantine_id: str,
            after_relocate: Callable[[], None],
            after_dispose: Callable[[], None],
        ) -> TrackedExistingFile:
            """Retain recovery authority over one exact existing regular child.

            Intent
            ------
            Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. The boundary coordinates name, expected_bytes, quarantine_id, after_relocate, and after_dispose through _bounded_directory_name, dup, _posix_track_existing_regular_file_at, str, bytes, and Callable with one closed state transition.

            Rationale
            ---------
            Because Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. Keep _bounded_directory_name, dup, _posix_track_existing_regular_file_at, str, bytes, and Callable inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            owned_descriptor = os.dup(descriptor)
            return _posix_track_existing_regular_file_at(
                owned_descriptor,
                name,
                expected_bytes,
                quarantine_id=quarantine_id,
                display_path=root / name,
                after_relocate=after_relocate,
                after_dispose=after_dispose,
            )

        def replace_regular_file(
            name: str,
            data: bytes,
            mode: int,
            build_name: str,
            staging_name: str,
            after_built: Callable[[], None],
            after_staged: Callable[[], None],
            after_replaced: Callable[[], None],
        ) -> None:
            """Replace one exact regular child through the retained directory descriptor.

            Intent
            ------
            Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. The boundary coordinates name, data, mode, build_name, and staging_name through _bounded_directory_name, _posix_replace_inventory_regular_file, str, bytes, int, and Callable with one closed state transition.

            Rationale
            ---------
            Because Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. Keep _bounded_directory_name, _posix_replace_inventory_regular_file, str, bytes, int, and Callable inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(build_name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(staging_name, max_name_bytes=max_name_bytes)
            _posix_replace_inventory_regular_file(
                descriptor,
                name,
                data,
                mode,
                build_name,
                staging_name,
                after_built,
                after_staged,
                after_replaced,
            )

        def discard_staged_regular_file(
            name: str,
            expected_bytes: bytes,
            build_name: str,
            staging_name: str,
            after_discarded: Callable[[], None],
        ) -> None:
            """Discard one exact staged regular child through retained authority.

            Intent
            ------
            Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. The boundary coordinates name, expected_bytes, build_name, staging_name, and after_discarded through _bounded_directory_name, _posix_discard_staged_inventory_regular_file, str, bytes, Callable, and name with one closed state transition.

            Rationale
            ---------
            Because Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. Keep _bounded_directory_name, _posix_discard_staged_inventory_regular_file, str, bytes, Callable, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(build_name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(staging_name, max_name_bytes=max_name_bytes)
            _posix_discard_staged_inventory_regular_file(
                descriptor,
                name,
                expected_bytes,
                build_name,
                staging_name,
                after_discarded,
            )

        def revalidate(expected_names: tuple[str, ...]) -> None:
            """Revalidate the retained root identity and bounded child-name set.

            Intent
            ------
            Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. The boundary coordinates expected_names, current_descriptor, and current_names through _open_absolute_directory, _posix_file_identity, fstat, AtomicWriteError, close, and _posix_bounded_directory_names_at with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

            Rationale
            ---------
            Because Within Coordinate root, max_entries, max_name_bytes, descriptor, and transferred through _open_absolute_directory, _posix_file_identity, fstat, and _posix_bounded_directory_names_at with 1 guarded checks, and 1 clean. Keep _open_absolute_directory, _posix_file_identity, fstat, AtomicWriteError, close, and _posix_bounded_directory_names_at inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current_descriptor = _open_absolute_directory(root, create=False)
            try:
                if _posix_file_identity(os.fstat(current_descriptor)) != root_identity:
                    raise AtomicWriteError("retained directory root changed")
            finally:
                os.close(current_descriptor)
            current_names = _posix_bounded_directory_names_at(
                descriptor,
                max_entries=max_entries,
                max_name_bytes=max_name_bytes,
            )
            if current_names != expected_names:
                raise AtomicWriteError("retained directory inventory changed")

        transferred = True
        return RetainedBoundedDirectoryInventory(
            names,
            read_regular_file=read_regular_file,
            track_existing=track_existing,
            replace_regular_file=replace_regular_file,
            discard_staged_regular_file=discard_staged_regular_file,
            revalidate=revalidate,
            release=lambda: os.close(descriptor),
        )
    finally:
        if not transferred:
            os.close(descriptor)


def _read_descriptor_bytes(descriptor: int) -> bytes:
    """coordinate descriptor, chunks, and chunk through lseek, read, join, append, int, and os with 1 guarded checks, and 1 bounded iterations.

    Intent
    ------
    coordinate descriptor, chunks, and chunk through lseek, read, join, append, int, and os with 1 guarded checks, and 1 bounded iterations. The boundary coordinates descriptor, chunks, and chunk through lseek, read, join, append, int, and os with 1 guarded checks, and 1 bounded iterations.

    Rationale
    ---------
    Because coordinate descriptor, chunks, and chunk through lseek, read, join, append, int, and os with 1 guarded checks, and 1 bounded iterations. Keep lseek, read, join, append, int, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_descriptor_bytes_bounded(descriptor: int, maximum_bytes: int) -> bytes:
    """Read no more than one caller-supplied exact-match bound.

    Intent
    ------
    Read no more than one caller-supplied exact-match bound. The boundary coordinates descriptor, maximum_bytes, chunks, remaining, and chunk through isinstance, TypeError, ValueError, lseek, read, and min with 3 guarded checks, 1 bounded iterations, and 2 typed refusals.

    Rationale
    ---------
    Because Read no more than one caller-supplied exact-match bound. Keep isinstance, TypeError, ValueError, lseek, read, and min inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

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
    """coordinate descriptor, data, path, written, and count through write, AtomicWriteError, int, bytes, Path, and written with 1 guarded checks, 1 bounded iterations, and 1 typed refusals.

    Intent
    ------
    coordinate descriptor, data, path, written, and count through write, AtomicWriteError, int, bytes, Path, and written with 1 guarded checks, 1 bounded iterations, and 1 typed refusals. The boundary coordinates descriptor, data, path, written, and count through write, AtomicWriteError, int, bytes, Path, and written with 1 guarded checks, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate descriptor, data, path, written, and count through write, AtomicWriteError, int, bytes, Path, and written with 1 guarded checks, 1 bounded iterations, and 1 typed refusals. Keep write, AtomicWriteError, int, bytes, Path, and written inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate descriptor, data, path, written, and count through write, AtomicWriteError, int, bytes, Path, and written with 1 guarded checks, 1 bounded iterations, and 1 typed refusals."
    """
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
    """Append under a native lock after an optional complete-predecessor compare.

    Intent
    ------
    Append under a native lock after an optional complete-predecessor compare. The boundary coordinates path, data, allowed_root, mode, and expected_previous_bytes through _open_parent, _reject_unsafe_final, isinstance, AtomicWriteError, _secure_open, and fstat with 11 guarded checks, 3 cleanup or failure regions, and 9 typed refusals.

    Rationale
    ---------
    Because Append under a native lock after an optional complete-predecessor compare. Keep _open_parent, _reject_unsafe_final, isinstance, AtomicWriteError, _secure_open, and fstat inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._read_descriptor_bytes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._secure_fchmod:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._write_descriptor_bytes:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._cleanup_read:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._open_parent:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._read_descriptor_bytes:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._reject_unsafe_final:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._secure_open:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    ._secure_stat:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Append under a native lock after an optional complete-predecessor compare."
    """

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
    """Resolve a fallback destination while rejecting escapes and symlink ancestors.

    Intent
    ------
    coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, any, and lstat with 6 guarded checks, 4 cleanup or failure regions, 1 bounded iterations, and 9 ty. The boundary coordinates path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, any, and lstat with 6 guarded checks, 4 cleanup or failure regions, 1 bounded iterations, and 9 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, any, and lstat with 6 guarded checks, 4 cleanup or failure regions, 1 bounded iterations, and 9 ty. Keep absolute, Path, relative_to, AtomicWriteError, any, and lstat inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, any, and lstat with 6 guarded checks, 4 cleanup or failure regions, 1 bounded iterations, and 9 ty."
    """
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
    """coordinate path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals.

    Intent
    ------
    coordinate path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals. The boundary coordinates path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals. Keep _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals."
    ._confined_fallback_path:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, and destination through _confined_fallback_path, read_bytes, AtomicWriteError, Path, path, and allowed_root with 1 cleanup or failure regions, and 2 typed refusals."
    """
    destination = _confined_fallback_path(path, allowed_root)
    try:
        return destination.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AtomicWriteError(f"cannot read confined file: {path}") from exc


def _fallback_chmod(path: Path, mode: int) -> None:
    """coordinate path, and mode through chmod, Path, int, os, path, and mode with 1 cleanup or failure regions.

    Intent
    ------
    coordinate path, and mode through chmod, Path, int, os, path, and mode with 1 cleanup or failure regions. The boundary coordinates path, and mode through chmod, Path, int, os, path, and mode with 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate path, and mode through chmod, Path, int, os, path, and mode with 1 cleanup or failure regions. Keep chmod, Path, int, os, path, and mode inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
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
    """Perform the explicitly authorized non-atomic confined file write.

    Intent
    ------
    coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref. The boundary coordinates path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref. Keep _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._confined_fallback_path:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref."
    ._fallback_chmod:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref."
    ._confined_fallback_path:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref."
    ._fallback_read_regular_file_bytes:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and operation through _confined_fallback_path, open, _fallback_chmod, write, AtomicWriteError, and flush with 4 guarded checks, 2 cleanup or failure regions, and 3 typed ref."
    """
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
    """Best-effort compare/append used only after explicit capability opt-in.

    Intent
    ------
    Best-effort compare/append used only after explicit capability opt-in. The boundary coordinates path, data, expected_previous_bytes, allowed_root, and mode through _confined_fallback_path, exists, AtomicWriteError, open, _fallback_chmod, and seek with 6 guarded checks, 2 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because Best-effort compare/append used only after explicit capability opt-in. Keep _confined_fallback_path, exists, AtomicWriteError, open, _fallback_chmod, and seek inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._fallback_chmod:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Best-effort compare/append used only after explicit capability opt-in."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Best-effort compare/append used only after explicit capability opt-in."
    ._confined_fallback_path:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Best-effort compare/append used only after explicit capability opt-in."
    """

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
    0x1
    | 0x2
    | 0x4
    | 0x80
    | 0x100
    | 0x00010000
    | 0x00020000
    | 0x00040000
    | 0x00100000
)
_WIN_FILE_OPTIONS = 0x20 | 0x40


def _windows_modules():
    """Load the native APIs and declare fixed-width, pointer-safe signatures.

    Intent
    ------
    Load the native APIs and declare fixed-width, pointer-safe signatures. The boundary coordinates kernel32, advapi32, ntdll, dword, and boolean through WinDLL, declare, POINTER, AtomicWriteError, _WINDOWS_APIS, and ctypes with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Load the native APIs and declare fixed-width, pointer-safe signatures. Keep WinDLL, declare, POINTER, AtomicWriteError, _WINDOWS_APIS, and ctypes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Load the native APIs and declare fixed-width, pointer-safe signatures."
    """
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
            """Attach one fixed-width ctypes signature to a native function.

            Intent
            ------
            Within Load the native APIs and declare fixed-width, pointer-safe signatures, coordinate library, name, arguments, result, and function through getattr, str, list, object, library, and name with one closed state tran. The boundary coordinates library, name, arguments, result, and function through getattr, str, list, object, library, and name with one closed state transition.

            Rationale
            ---------
            Because Within Load the native APIs and declare fixed-width, pointer-safe signatures, coordinate library, name, arguments, result, and function through getattr, str, list, object, library, and name with one closed state tran. Keep getattr, str, list, object, library, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
        declare(kernel32, "DeviceIoControl", [_WinHandle, dword, pointer, dword, pointer, dword, ctypes.POINTER(dword), pointer], boolean)
        declare(kernel32, "LockFileEx", [_WinHandle, dword, dword, dword, dword, ctypes.POINTER(_WinOverlapped)], boolean)
        declare(kernel32, "UnlockFileEx", [_WinHandle, dword, dword, dword, ctypes.POINTER(_WinOverlapped)], boolean)
        declare(kernel32, "GetCurrentProcess", [], _WinHandle)
        declare(kernel32, "DuplicateHandle", [_WinHandle, _WinHandle, _WinHandle, ctypes.POINTER(_WinHandle), dword, boolean, dword], boolean)
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
    """coordinate message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks.

    Intent
    ------
    coordinate message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks. The boundary coordinates message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks.

    Rationale
    ---------
    Because coordinate message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks. Keep _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate message, winerror, _kernel32, _advapi32, and _ntdll through _windows_modules, get_last_error, AtomicWriteError, str, int, and winerror with 1 guarded checks."
    """
    _kernel32, _advapi32, _ntdll = _windows_modules()
    error = ctypes.get_last_error() if winerror is None else int(winerror)
    if error in {1, 50, 120}:
        return AtomicWriteError(_CAPABILITY_ERROR)
    return AtomicWriteError(f"{message}: winerror {error}")


def _windows_nt_error(status: int, message: str) -> BaseException:
    """Translate one native NT status into the helper's closed Python error taxonomy.

    Intent
    ------
    coordinate status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks. The boundary coordinates status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks.

    Rationale
    ---------
    Because coordinate status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks. Keep _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate status, message, _kernel32, _advapi32, and ntdll through _windows_modules, RtlNtStatusToDosError, FileNotFoundError, FileExistsError, AtomicWriteError, and _windows_call_error with 3 guarded checks."
    """
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
    """coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded.

    Intent
    ------
    coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded. The boundary coordinates path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded. Keep absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_component_utf16:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, destination, root, and relative through absolute, Path, relative_to, AtomicWriteError, _windows_component_utf16, and path with 1 guarded checks, 1 cleanup or failure regions, 1 bounded."
    """
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
    """coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals. Keep _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, CloseHandle, _WinHandle, _windows_call_error, int, and handle with 1 guarded checks, and 1 typed refusals."
    """
    kernel32, _advapi32, _ntdll = _windows_modules()
    if handle >= 0 and not kernel32.CloseHandle(_WinHandle(handle)):
        raise _windows_call_error("cannot close native handle")


def _windows_duplicate_handle(handle: int) -> int:
    """Duplicate one retained native handle in the current process.

    Intent
    ------
    Duplicate one retained native handle in the current process. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and process through _windows_modules, GetCurrentProcess, _WinHandle, DuplicateHandle, byref, and _windows_call_error with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Duplicate one retained native handle in the current process. Keep _windows_modules, GetCurrentProcess, _WinHandle, DuplicateHandle, byref, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Duplicate one retained native handle in the current process."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Duplicate one retained native handle in the current process."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Duplicate one retained native handle in the current process."
    """

    kernel32, _advapi32, _ntdll = _windows_modules()
    process = kernel32.GetCurrentProcess()
    duplicate = _WinHandle()
    if not kernel32.DuplicateHandle(
        process,
        _WinHandle(handle),
        process,
        ctypes.byref(duplicate),
        0,
        False,
        0x2,
    ):
        raise _windows_call_error("cannot duplicate native directory handle")
    if not duplicate.value:
        raise AtomicWriteError(_CAPABILITY_ERROR)
    return int(duplicate.value)


def _windows_validate_handle(
    handle: int,
    *,
    expect_directory: bool,
    display: str | Path,
) -> None:
    """coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t.

    Intent
    ------
    coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t. The boundary coordinates handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 typed refusals.

    Rationale
    ---------
    Because coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t. Keep _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t."
    ._WinFileAttributeTagInfo:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate handle, expect_directory, display, kernel32, and _advapi32 through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 4 guarded checks, and 4 t."
    """
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


def _windows_attribute_tag(handle: int) -> tuple[int, int]:
    """Return native attributes and reparse tag for one retained handle.

    Intent
    ------
    Return native attributes and reparse tag for one retained handle. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and _windows_call_error with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Return native attributes and reparse tag for one retained handle. Keep _windows_modules, _WinFileAttributeTagInfo, GetFileInformationByHandleEx, byref, sizeof, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._WinFileAttributeTagInfo:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Return native attributes and reparse tag for one retained handle."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Return native attributes and reparse tag for one retained handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Return native attributes and reparse tag for one retained handle."
    """

    kernel32, _advapi32, _ntdll = _windows_modules()
    information = _WinFileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        raise _windows_call_error("cannot inspect native publication handle")
    return int(information.FileAttributes), int(information.ReparseTag)


def _windows_link_count(handle: int) -> int:
    """Return the documented native hard-link count for a retained handle.

    Intent
    ------
    Query FILE_STANDARD_INFO without following another name and fail closed
    when the platform cannot provide a positive NumberOfLinks value.

    Rationale
    ---------
    Existing deterministic builds must prove they have one name before either
    READONLY repair or byte mutation can affect a second hard-link victim.

    Pseudocode
    ----------
    - set information = FileStandardInfo(handle)
    - if information.NumberOfLinks is invalid:
      - raise AtomicWriteError
    - return information.NumberOfLinks

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "Fails closed when native single-link ownership cannot be proved."
    ._WinFileStandardInfo:
      why:
        constructs: "Carries the documented NumberOfLinks field returned for the retained handle."
    ._windows_call_error:
      why:
        constructs: "Converts native query failure into the typed atomic-write boundary."
    ._windows_modules:
      why:
        constructs: "Provides the configured GetFileInformationByHandleEx entry point."
    """
    kernel32, _advapi32, _ntdll = _windows_modules()
    information = _WinFileStandardInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 1, ctypes.byref(information), ctypes.sizeof(information)
    ):
        error = ctypes.get_last_error()
        if error == 87:
            raise AtomicWriteError(_CAPABILITY_ERROR)
        raise _windows_call_error("cannot read native file link count", error)
    count = int(information.NumberOfLinks)
    if count < 1:
        raise AtomicWriteError(_CAPABILITY_ERROR)
    return count


def _normalized_publication_mode(
    mode: int, *, directory: bool, windows: bool
) -> int:
    """Map a requested portable mode to the bits the platform can observe.

    Intent
    ------
    Map a requested portable mode to the bits the platform can observe. The boundary coordinates mode, directory, windows, and read_execute through int, bool, windows, mode, directory, and read_execute with 1 guarded checks.

    Rationale
    ---------
    Because Map a requested portable mode to the bits the platform can observe. Keep int, bool, windows, mode, directory, and read_execute inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    if not windows:
        return mode
    read_execute = 0o555 if directory else 0o444
    return read_execute | (0o222 if mode & 0o222 else 0)


def normalize_publication_mode(mode: int, *, directory: bool = False) -> int:
    """Return the exact observable mode policy used by publication primitives.

    Intent
    ------
    Return the exact observable mode policy used by publication primitives. The boundary coordinates mode, and directory through isinstance, TypeError, ValueError, _normalized_publication_mode, int, and bool with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Return the exact observable mode policy used by publication primitives. Keep isinstance, TypeError, ValueError, _normalized_publication_mode, int, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._normalized_publication_mode:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Return the exact observable mode policy used by publication primitives."
    """

    if isinstance(mode, bool) or not isinstance(mode, int):
        raise TypeError("mode must be an integer")
    if mode < 0 or mode > 0o777:
        raise ValueError("mode must be a portable permission mode")
    return _normalized_publication_mode(
        mode, directory=directory, windows=os.name == "nt"
    )


def _windows_mode_from_attributes(attributes: int, *, directory: bool) -> int:
    """coordinate attributes, directory, and requested through int, bool, directory, attributes, and requested with 1 guarded checks.

    Intent
    ------
    coordinate attributes, directory, and requested through int, bool, directory, attributes, and requested with 1 guarded checks. The boundary coordinates attributes, directory, and requested through int, bool, directory, attributes, and requested with 1 guarded checks.

    Rationale
    ---------
    Because coordinate attributes, directory, and requested through int, bool, directory, attributes, and requested with 1 guarded checks. Keep int, bool, directory, attributes, and requested inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    requested = 0o555 if directory else 0o444
    if not attributes & 0x1:
        requested |= 0o222
    return requested


def _windows_verify_supported_mode(
    handle: int, mode: int, *, directory: bool
) -> None:
    """Verify only the read-only attribute represented by the native mode policy.

    Intent
    ------
    Verify only the read-only attribute represented by the native mode policy. The boundary coordinates handle, mode, directory, attributes, and _tag through _windows_attribute_tag, AtomicWriteError, _windows_mode_from_attributes, int, bool, and handle with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Verify only the read-only attribute represented by the native mode policy. Keep _windows_attribute_tag, AtomicWriteError, _windows_mode_from_attributes, int, bool, and handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_mode_from_attributes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Verify only the read-only attribute represented by the native mode policy."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Verify only the read-only attribute represented by the native mode policy."
    ._windows_attribute_tag:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Verify only the read-only attribute represented by the native mode policy."
    """

    attributes, _tag = _windows_attribute_tag(handle)
    if bool(attributes & 0x10) != directory:
        raise AtomicWriteError("native publication object has the wrong type")
    if _windows_mode_from_attributes(attributes, directory=directory) != mode:
        raise AtomicWriteError("native publication object has the wrong mode policy")


def _windows_set_supported_mode(handle: int, mode: int, *, directory: bool) -> None:
    """Apply and verify the native read-only attribute for a normalized mode.

    Intent
    ------
    Apply and verify the native read-only attribute for a normalized mode. The boundary coordinates handle, mode, directory, kernel32, and _advapi32 through _windows_modules, _WinFileBasicInfo, GetFileInformationByHandleEx, byref, sizeof, and _windows_call_error with 3 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Apply and verify the native read-only attribute for a normalized mode. Keep _windows_modules, _WinFileBasicInfo, GetFileInformationByHandleEx, byref, sizeof, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_verify_supported_mode:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Apply and verify the native read-only attribute for a normalized mode."

    InstantiationsFromRepo
    ----------------------
    ._WinFileBasicInfo:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Apply and verify the native read-only attribute for a normalized mode."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Apply and verify the native read-only attribute for a normalized mode."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Apply and verify the native read-only attribute for a normalized mode."
    """

    kernel32, _advapi32, _ntdll = _windows_modules()
    information = _WinFileBasicInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle, 0, ctypes.byref(information), ctypes.sizeof(information)
    ):
        raise _windows_call_error("cannot read native publication attributes")
    if mode & 0o222:
        information.FileAttributes &= ~0x1
    else:
        information.FileAttributes |= 0x1
    if not kernel32.SetFileInformationByHandle(
        _WinHandle(handle),
        0,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise _windows_call_error("cannot set native publication attributes")
    _windows_verify_supported_mode(handle, mode, directory=directory)


def _windows_open_reparse_point(
    parent_handle: int, name: str, *, access: int = _WIN_READ_ACCESS
) -> int:
    """Open one exact symbolic-link reparse point relative to a retained parent.

    Intent
    ------
    Open one exact symbolic-link reparse point relative to a retained parent. The boundary coordinates parent_handle, name, access, handle, and _information through _windows_open_relative, _windows_attribute_tag, AtomicWriteError, _windows_close_handle, int, and str with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Open one exact symbolic-link reparse point relative to a retained parent. Keep _windows_open_relative, _windows_attribute_tag, AtomicWriteError, _windows_close_handle, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Open one exact symbolic-link reparse point relative to a retained parent."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Open one exact symbolic-link reparse point relative to a retained parent."
    ._windows_attribute_tag:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Open one exact symbolic-link reparse point relative to a retained parent."
    ._windows_open_relative:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Open one exact symbolic-link reparse point relative to a retained parent."
    """

    handle, _information = _windows_open_relative(
        parent_handle,
        name,
        access=access,
        disposition=1,
        options=0x20,
    )
    try:
        attributes, tag = _windows_attribute_tag(handle)
        if not attributes & 0x400 or tag != 0xA000000C:
            raise AtomicWriteError("native publication entry is not a symbolic link")
    except BaseException:
        _windows_close_handle(handle)
        raise
    return handle


def _windows_read_symlink_target(handle: int) -> str:
    """Read the lexical print name from one retained symbolic-link handle.

    Intent
    ------
    Read the lexical print name from one retained symbolic-link handle. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and output through _windows_modules, create_string_buffer, c_uint32, DeviceIoControl, _WinHandle, and byref with 4 guarded checks, 1 cleanup or failure regions, and 5 typed refusals.

    Rationale
    ---------
    Because Read the lexical print name from one retained symbolic-link handle. Keep _windows_modules, create_string_buffer, c_uint32, DeviceIoControl, _WinHandle, and byref inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read the lexical print name from one retained symbolic-link handle."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read the lexical print name from one retained symbolic-link handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read the lexical print name from one retained symbolic-link handle."
    """

    kernel32, _advapi32, _ntdll = _windows_modules()
    output = ctypes.create_string_buffer(16 * 1024)
    returned = ctypes.c_uint32()
    if not kernel32.DeviceIoControl(
        _WinHandle(handle),
        0x000900A8,
        None,
        0,
        output,
        len(output),
        ctypes.byref(returned),
        None,
    ):
        raise _windows_call_error("cannot read native symbolic-link target")
    raw = output.raw[: int(returned.value)]
    if len(raw) < 20 or int.from_bytes(raw[0:4], "little") != 0xA000000C:
        raise AtomicWriteError("native symbolic-link reparse data is invalid")
    print_offset = int.from_bytes(raw[12:14], "little")
    print_length = int.from_bytes(raw[14:16], "little")
    start = 20 + print_offset
    end = start + print_length
    if print_length == 0 or print_offset % 2 or print_length % 2 or end > len(raw):
        raise AtomicWriteError("native symbolic-link reparse data is invalid")
    try:
        target = raw[start:end].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise AtomicWriteError("native symbolic-link target is invalid") from exc
    if not target or "\x00" in target:
        raise AtomicWriteError("native symbolic-link target is invalid")
    return target


def _windows_verify_named_reparse_handle(
    parent_handle: int, name: str, expected_handle: int
) -> None:
    """Require a live relative name to designate the retained symbolic link.

    Intent
    ------
    Require a live relative name to designate the retained symbolic link. The boundary coordinates parent_handle, name, expected_handle, and verifier through _windows_open_reparse_point, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Require a live relative name to designate the retained symbolic link. Keep _windows_open_reparse_point, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Require a live relative name to designate the retained symbolic link."
    ._windows_file_id:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Require a live relative name to designate the retained symbolic link."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Require a live relative name to designate the retained symbolic link."
    ._windows_open_reparse_point:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Require a live relative name to designate the retained symbolic link."
    """

    verifier = -1
    try:
        verifier = _windows_open_reparse_point(parent_handle, name)
        if _windows_file_id(verifier) != _windows_file_id(expected_handle):
            raise AtomicWriteError("native symbolic-link name changed")
    finally:
        if verifier >= 0:
            _windows_close_handle(verifier)


def _windows_open_or_create_symlink_build(
    build_path: Path,
    parent_handle: int,
    build_name: str,
    target: str,
) -> int:
    """Create if absent, then retain the exact deterministic symlink build.

    Intent
    ------
    Create if absent, then retain the exact deterministic symlink build. The boundary coordinates build_path, parent_handle, build_name, and target through symlink, is_absolute, Path, is_dir, getattr, and AtomicWriteError with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Create if absent, then retain the exact deterministic symlink build. Keep symlink, is_absolute, Path, is_dir, getattr, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Create if absent, then retain the exact deterministic symlink build."
    ._windows_open_reparse_point:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Create if absent, then retain the exact deterministic symlink build."
    """

    try:
        os.symlink(
            target,
            build_path,
            target_is_directory=Path(target).is_absolute() and Path(target).is_dir(),
        )
    except FileExistsError:
        pass
    except OSError as exc:
        if getattr(exc, "winerror", None) in {1, 50, 120, 1314}:
            raise AtomicWriteError(_CAPABILITY_ERROR) from exc
        raise AtomicWriteError("cannot create native symbolic-link build") from exc
    return _windows_open_reparse_point(
        parent_handle, build_name, access=_WIN_MUTATE_ACCESS
    )


def _windows_open_root(
    root: Path,
    *,
    create: bool = False,
    final_access: int | None = None,
) -> int:
    """Walk a native root from its volume/share handle without reparse traversal.

    Intent
    ------
    Walk a native root from its volume/share handle without reparse traversal. The boundary coordinates root, create, final_access, absolute, and anchor through absolute, Path, is_absolute, AtomicWriteError, _windows_modules, and CreateFileW with 3 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 4 typed refusals.

    Rationale
    ---------
    Because Walk a native root from its volume/share handle without reparse traversal. Keep absolute, Path, is_absolute, AtomicWriteError, _windows_modules, and CreateFileW inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."
    ._windows_validate_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Walk a native root from its volume/share handle without reparse traversal."
    """
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

    Intent
    ------
    NtCreateFile relative to an already validated directory handle. The boundary coordinates parent_handle, name, access, share, and disposition through _windows_component_utf16, _windows_modules, memmove, _WinUnicodeString, cast, and POINTER with 4 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because NtCreateFile relative to an already validated directory handle. Keep _windows_component_utf16, _windows_modules, memmove, _WinUnicodeString, cast, and POINTER inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._WinIoStatusBlock:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._WinObjectAttributes:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._WinUnicodeString:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._windows_component_utf16:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
    ._windows_nt_error:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: NtCreateFile relative to an already validated directory handle."
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
    """coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu.

    Intent
    ------
    coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu. The boundary coordinates parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu. Keep _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu."
    ._windows_validate_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu."

    InstantiationsFromRepo
    ----------------------
    ._windows_open_relative:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate parent_handle, name, access, disposition, and options through _windows_open_relative, _windows_validate_handle, _windows_close_handle, int, str, and bool with 1 cleanup or failure regions, and 1 typed refu."
    """
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
    """coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it.

    Intent
    ------
    coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it. The boundary coordinates path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it. Keep _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it."
    ._windows_file_id:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it."

    InstantiationsFromRepo
    ----------------------
    ._windows_open_root:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it."
    ._windows_path_parts:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, allowed_root, root, relative, and handles through _windows_path_parts, _windows_open_root, tuple, _windows_file_id, _windows_open_validated, and append with 1 cleanup or failure regions, 1 bounded it."
    """
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
    """Reopen each retained child from its retained parent and compare IDs.

    Intent
    ------
    Reopen each retained child from its retained parent and compare IDs. The boundary coordinates handles, parts, index, expected_handle, and component through enumerate, _windows_open_validated, AtomicWriteError, _windows_file_id, _windows_close_handle, and list with 1 guarded checks, 2 cleanup or failure regions, 1 bounded iterations, and 2 typed refusals.

    Rationale
    ---------
    Because Reopen each retained child from its retained parent and compare IDs. Keep enumerate, _windows_open_validated, AtomicWriteError, _windows_file_id, _windows_close_handle, and list inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Reopen each retained child from its retained parent and compare IDs."
    ._windows_file_id:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Reopen each retained child from its retained parent and compare IDs."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Reopen each retained child from its retained parent and compare IDs."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Reopen each retained child from its retained parent and compare IDs."
    """

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
    """coordinate handles, failure, and handle through reversed, _windows_close_handle, list, int, BaseException, and handles with 2 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals.

    Intent
    ------
    coordinate handles, failure, and handle through reversed, _windows_close_handle, list, int, BaseException, and handles with 2 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals. The boundary coordinates handles, failure, and handle through reversed, _windows_close_handle, list, int, BaseException, and handles with 2 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handles, failure, and handle through reversed, _windows_close_handle, list, int, BaseException, and handles with 2 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals. Keep reversed, _windows_close_handle, list, int, BaseException, and handles inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set closed_handle_chain = local_decisions
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate handles, failure, and handle through reversed, _windows_close_handle, list, int, BaseException, and handles with 2 guarded checks, 1 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals."
    """
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
    """coordinate handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals. Keep _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, offset, origin, kernel32, and _advapi32 through _windows_modules, c_int64, SetFilePointerEx, _WinHandle, byref, and _windows_call_error with 1 guarded checks, and 1 typed refusals."
    """
    kernel32, _advapi32, _ntdll = _windows_modules()
    result = ctypes.c_int64()
    if not kernel32.SetFilePointerEx(
        _WinHandle(handle), offset, ctypes.byref(result), origin
    ):
        raise _windows_call_error("cannot seek native file handle")
    return int(result.value)


def _windows_read_handle(handle: int) -> bytes:
    """coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals. Keep _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_seek:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals."

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and chunks through _windows_modules, _windows_seek, create_string_buffer, c_uint32, ReadFile, and byref with 2 guarded checks, 1 bounded iterations, and 1 typed refusals."
    """
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
    """Read at most one exact-match bound from a retained native handle.

    Intent
    ------
    Read at most one exact-match bound from a retained native handle. The boundary coordinates handle, maximum_bytes, kernel32, _advapi32, and _ntdll through isinstance, TypeError, ValueError, _windows_modules, _windows_seek, and create_string_buffer with 4 guarded checks, 1 bounded iterations, and 3 typed refusals.

    Rationale
    ---------
    Because Read at most one exact-match bound from a retained native handle. Keep isinstance, TypeError, ValueError, _windows_modules, _windows_seek, and create_string_buffer inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_seek:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Read at most one exact-match bound from a retained native handle."

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read at most one exact-match bound from a retained native handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read at most one exact-match bound from a retained native handle."
    """

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
    """Write all bytes to a retained native handle with bounded chunks.

    Intent
    ------
    coordinate handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed re. The boundary coordinates handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed re. Keep _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed re."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed re."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, data, kernel32, _advapi32, and _ntdll through _windows_modules, create_string_buffer, c_uint32, WriteFile, byref, and _windows_call_error with 2 guarded checks, 1 bounded iterations, and 2 typed re."
    """
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


def _windows_truncate_handle(handle: int) -> None:
    """Truncate one retained writable native handle at offset zero.

    Intent
    ------
    Truncate one retained writable native handle at offset zero. The boundary coordinates handle, kernel32, _advapi32, and _ntdll through _windows_modules, _windows_seek, SetEndOfFile, _WinHandle, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Truncate one retained writable native handle at offset zero. Keep _windows_modules, _windows_seek, SetEndOfFile, _WinHandle, _windows_call_error, and int inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_seek:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Truncate one retained writable native handle at offset zero."

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Truncate one retained writable native handle at offset zero."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Truncate one retained writable native handle at offset zero."
    """

    kernel32, _advapi32, _ntdll = _windows_modules()
    _windows_seek(handle, 0, 0)
    if not kernel32.SetEndOfFile(_WinHandle(handle)):
        raise _windows_call_error("cannot truncate native file handle")


def _windows_flush_handle(handle: int) -> None:
    """coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals. Keep _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, and _ntdll through _windows_modules, FlushFileBuffers, _windows_call_error, int, kernel32, and handle with 1 guarded checks, and 1 typed refusals."
    """
    kernel32, _advapi32, _ntdll = _windows_modules()
    # FlushFileBuffers is the documented user-mode durability boundary for
    # system-buffered file data; files are also opened FILE_WRITE_THROUGH.
    if not kernel32.FlushFileBuffers(handle):
        raise _windows_call_error("cannot flush native file handle")


def _windows_file_id(handle: int) -> tuple[int, bytes]:
    """coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals. Keep _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals."
    ._WinFileIdInfo:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and information through _windows_modules, _WinFileIdInfo, GetFileInformationByHandleEx, byref, sizeof, and get_last_error with 3 guarded checks, and 3 typed refusals."
    """
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
    """coordinate handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition.

    Intent
    ------
    coordinate handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition. The boundary coordinates handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition.

    Rationale
    ---------
    Because coordinate handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition. Keep _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .ConfinedFileIdentity:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition."
    ._windows_file_id:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, volume, and file_id through _windows_file_id, ConfinedFileIdentity, int, handle, volume, and file_id with one closed state transition."
    """
    volume, file_id = _windows_file_id(handle)
    return ConfinedFileIdentity(
        platform="windows",
        volume=volume,
        file_id=file_id,
    )


def _windows_directory_entry_names(
    handle: int,
    *,
    max_entries: int | None = None,
    max_name_bytes: int | None = None,
) -> tuple[str, ...]:
    """Enumerate names through a retained native directory handle.

    Intent
    ------
    Enumerate names through a retained native directory handle. The boundary coordinates handle, max_entries, max_name_bytes, _kernel32, and _advapi32 through _windows_modules, create_string_buffer, _WinIoStatusBlock, NtQueryDirectoryFile, _WinHandle, and byref with 9 guarded checks, 1 cleanup or failure regions, 2 bounded iterations, and 6 typed refusals.

    Rationale
    ---------
    Because Enumerate names through a retained native directory handle. Keep _windows_modules, create_string_buffer, _WinIoStatusBlock, NtQueryDirectoryFile, _WinHandle, and byref inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._bounded_directory_name:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."
    ._windows_component_utf16:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."
    ._WinIoStatusBlock:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."
    ._windows_nt_error:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Enumerate names through a retained native directory handle."
    """

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
                if max_name_bytes is not None:
                    _bounded_directory_name(
                        name,
                        max_name_bytes=max_name_bytes,
                    )
                names.append(name)
                if max_entries is not None and len(names) > max_entries:
                    raise AtomicWriteError(
                        "confined directory entry limit exceeded"
                    )
            if next_offset == 0:
                break
            if next_offset < 12 or offset + next_offset > used:
                raise AtomicWriteError("invalid native directory entry offset")
            offset += next_offset
        restart = False
    return tuple(sorted(names))


def _windows_read_bounded_directory_names(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[str, ...]:
    """Enumerate bounded names through one retained no-reparse root handle.

    Intent
    ------
    Enumerate bounded names through one retained no-reparse root handle. The boundary coordinates root, max_entries, max_name_bytes, and root_handle through _windows_open_root, _windows_directory_entry_names, _windows_close_handle, Path, int, and root with 1 cleanup or failure regions.

    Rationale
    ---------
    Because Enumerate bounded names through one retained no-reparse root handle. Keep _windows_open_root, _windows_directory_entry_names, _windows_close_handle, Path, int, and root inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Enumerate bounded names through one retained no-reparse root handle."

    InstantiationsFromRepo
    ----------------------
    ._windows_directory_entry_names:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Enumerate bounded names through one retained no-reparse root handle."
    ._windows_open_root:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Enumerate bounded names through one retained no-reparse root handle."
    """

    root_handle = _windows_open_root(
        root,
        final_access=_WIN_DIR_ACCESS | 0x00020000 | _WIN_LIST_DIRECTORY,
    )
    try:
        return _windows_directory_entry_names(
            root_handle,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )
    finally:
        _windows_close_handle(root_handle)


def _windows_read_inventory_regular_file(
    root_handle: int,
    name: str,
    maximum_bytes: int,
) -> ConfinedRegularFile:
    """coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos.

    Intent
    ------
    coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos. The boundary coordinates root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_close_handle with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos. Keep _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_close_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."
    .ConfinedRegularFile:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."
    ._windows_confined_identity:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."
    ._windows_read_handle_bounded:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate root_handle, name, maximum_bytes, child, and _information through _windows_open_validated, ConfinedRegularFile, _windows_read_handle_bounded, _windows_confined_identity, AtomicWriteError, and _windows_clos."
    """
    child = -1
    try:
        child, _information = _windows_open_validated(
            root_handle,
            name,
            access=_WIN_READ_ACCESS,
            disposition=1,
            options=_WIN_FILE_OPTIONS,
            directory=False,
        )
        return ConfinedRegularFile(
            name=name,
            data=_windows_read_handle_bounded(child, maximum_bytes),
            identity=_windows_confined_identity(child),
        )
    except FileNotFoundError as exc:
        raise AtomicWriteError("confined directory entry disappeared") from exc
    finally:
        if child >= 0:
            _windows_close_handle(child)


def _windows_inventory_private_entry_present(
    root_handle: int,
    name: str,
) -> bool:
    """coordinate root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions.

    Intent
    ------
    coordinate root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions. The boundary coordinates root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions. Keep _windows_open_validated, _windows_close_handle, int, str, root_handle, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions."

    InstantiationsFromRepo
    ----------------------
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate root_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, root_handle, and name with 1 guarded checks, and 1 cleanup or failure regions."
    """
    handle = -1
    try:
        handle, _information = _windows_open_validated(
            root_handle,
            name,
            access=_WIN_READ_ACCESS,
            disposition=1,
            options=_WIN_FILE_OPTIONS,
            directory=False,
        )
        return True
    except FileNotFoundError:
        return False
    finally:
        if handle >= 0:
            _windows_close_handle(handle)


def _windows_open_rewritten_selector_build(
    root_handle: int,
    build_name: str,
    expected_bytes: bytes,
    *,
    create: bool,
    after_created: Callable[[], None],
) -> tuple[int, object]:
    """Create or resumably rewrite one private deterministic build entry.

    Intent
    ------
    Create a writable private build, or inspect an existing build without
    write-data rights, validate its identity, name, bounded bytes, attributes,
    and ACL, clear READONLY only after validation, then reopen and revalidate
    the same object before rewriting it.

    Rationale
    ---------
    An interrupted publication may leave an exact READONLY build. Separating
    inspection from write-data authority makes that build resumable without
    granting mutation authority to a wrong-type or changed native object.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_attribute_tag:
      why:
        computes: "Reads native type and READONLY attributes from retained inspection and rewrite handles."
    ._windows_confined_identity:
      why:
        computes: "Computes the native identity compared across inspection and write-data handles."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_read_handle_bounded:
      why:
        reads: "Reads bounded build bytes before permission repair, after reopen, and after rewrite."
    ._windows_link_count:
      why:
        computes: "Proves the existing native build has one name during inspection, immediately before permission repair, after write-data reopen, and immediately before truncate."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_set_supported_mode:
      why:
        transforms: "Clears READONLY on a validated existing build before the write-data reopen."
    ._windows_truncate_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_write_handle:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_attribute_tag:
      why:
        constructs: "Constructs retained native attribute snapshots used to reject wrong-type or still-READONLY builds."
    ._windows_confined_identity:
      why:
        constructs: "Constructs the native identity compared across inspection and write-data handles."
    ._windows_lock_handle:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    ._windows_read_handle_bounded:
      why:
        constructs: "Constructs bounded byte snapshots before permission repair, after reopen, and after rewrite."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: Create or securely rewrite one private selector build entry."
    """

    security: _WinSecurityDescriptor | None = None
    existing_identity: ConfinedFileIdentity | None = None
    existing_bytes: bytes | None = None
    if create:
        _sid_buffer, _sid, _acl, security = _windows_security_material()
    else:
        inspection_handle = -1
        try:
            inspection_handle, _information = _windows_open_validated(
                root_handle,
                build_name,
                access=_WIN_READ_ACCESS | 0x100,
                disposition=1,
                options=_WIN_FILE_OPTIONS,
                directory=False,
                share=_WIN_SHARE_ALL & ~0x2,
            )
            existing_identity = _windows_confined_identity(inspection_handle)
            attributes, tag = _windows_attribute_tag(inspection_handle)
            if attributes & (0x10 | 0x400) or tag:
                raise AtomicWriteError("publication build is not a regular file")
            if _windows_link_count(inspection_handle) != 1:
                raise AtomicWriteError("publication build is hard-linked")
            _windows_require_restrictive_acl(inspection_handle, build_name)
            existing_bytes = _windows_read_handle_bounded(
                inspection_handle, len(expected_bytes) + 1
            )
            _windows_verify_named_handle(
                root_handle, build_name, inspection_handle
            )
            if attributes & 0x1:
                if _windows_link_count(inspection_handle) != 1:
                    raise AtomicWriteError("publication build is hard-linked")
                _windows_set_supported_mode(
                    inspection_handle, 0o666, directory=False
                )
                _windows_verify_named_handle(
                    root_handle, build_name, inspection_handle
                )
        except FileNotFoundError as exc:
            raise AtomicWriteError(
                "publication build changed before permission repair"
            ) from exc
        except AtomicWriteError:
            raise
        except OSError as exc:
            raise AtomicWriteError(
                "cannot inspect publication build for permission repair"
            ) from exc
        finally:
            if inspection_handle >= 0:
                _windows_close_handle(inspection_handle)
    try:
        handle, _information = _windows_open_validated(
            root_handle,
            build_name,
            access=_WIN_MUTATE_ACCESS,
            disposition=2 if create else 1,
            options=(0x2 if create else 0) | _WIN_FILE_OPTIONS,
            directory=False,
            security_descriptor=security,
            share=_WIN_SHARE_ALL & ~0x2,
        )
    except FileNotFoundError as exc:
        raise AtomicWriteError("publication build changed before rewrite") from exc
    except AtomicWriteError:
        raise
    except OSError as exc:
        if create:
            raise
        raise AtomicWriteError("cannot reopen publication build for rewrite") from exc
    if create:
        after_created()
    lock: object | None = None
    complete = False
    try:
        lock = _windows_lock_handle(handle)
        if not create:
            if (
                _windows_confined_identity(handle) != existing_identity
                or _windows_attribute_tag(handle)[0] & (0x1 | 0x10 | 0x400)
                or _windows_link_count(handle) != 1
                or _windows_read_handle_bounded(
                    handle, len(expected_bytes) + 1
                )
                != existing_bytes
            ):
                raise AtomicWriteError("publication build changed before rewrite")
            _windows_verify_named_handle(root_handle, build_name, handle)
        _windows_require_restrictive_acl(handle, build_name)
        if not create and _windows_link_count(handle) != 1:
            raise AtomicWriteError("publication build is hard-linked")
        _windows_truncate_handle(handle)
        _windows_write_handle(handle, expected_bytes)
        _windows_flush_handle(handle)
        if _windows_read_handle_bounded(handle, len(expected_bytes) + 1) != expected_bytes:
            raise AtomicWriteError("selector build reread failed")
        _windows_verify_named_handle(root_handle, build_name, handle)
        complete = True
        return handle, lock
    except AtomicWriteError:
        raise
    except OSError as exc:
        if create:
            raise
        raise AtomicWriteError("cannot rewrite existing publication build") from exc
    finally:
        if not complete:
            try:
                if lock is not None:
                    _windows_unlock_handle(handle, lock)
            finally:
                _windows_close_handle(handle)


def _windows_open_exact_selector_stage(
    root_handle: int,
    staging_name: str,
    expected_bytes: bytes,
) -> tuple[int, object]:
    """Open one already published exact selector stage.

    Intent
    ------
    Open one already published exact selector stage. The boundary coordinates root_handle, staging_name, expected_bytes, handle, and _information through _windows_open_validated, _windows_lock_handle, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_named_handle, and _windows_require_restrictive_acl with 3 guarded checks, 2 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Open one already published exact selector stage. Keep _windows_open_validated, _windows_lock_handle, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_named_handle, and _windows_require_restrictive_acl inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_read_handle_bounded:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Open one already published exact selector stage."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_lock_handle:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Open one already published exact selector stage."
    """

    handle, _information = _windows_open_validated(
        root_handle,
        staging_name,
        access=_WIN_READ_ACCESS | 0x00010000 | _WIN_GENERIC_WRITE,
        disposition=1,
        options=_WIN_FILE_OPTIONS,
        directory=False,
        share=_WIN_SHARE_ALL & ~0x2,
    )
    lock: object | None = None
    complete = False
    try:
        lock = _windows_lock_handle(handle)
        if _windows_read_handle_bounded(handle, len(expected_bytes) + 1) != expected_bytes:
            raise AtomicWriteError("staged file bytes do not match expectation")
        _windows_verify_named_handle(root_handle, staging_name, handle)
        _windows_require_restrictive_acl(handle, staging_name)
        complete = True
        return handle, lock
    finally:
        if not complete:
            try:
                if lock is not None:
                    _windows_unlock_handle(handle, lock)
            finally:
                _windows_close_handle(handle)


def _windows_publish_selector_build(
    handle: int,
    root_handle: int,
    staging_name: str,
    expected_bytes: bytes,
) -> None:
    """coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi.

    Intent
    ------
    coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi. The boundary coordinates handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_require_restrictive_acl with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi. Keep _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_require_restrictive_acl inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."
    ._windows_read_handle_bounded:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate handle, root_handle, staging_name, and expected_bytes through _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, _windows_read_handle_bounded, and _windows_requi."
    """
    if not _windows_rename_handle(handle, root_handle, staging_name, replace=False):
        raise AtomicWriteError("native selector stage publish collided")
    _windows_flush_handle(handle)
    _windows_verify_named_handle(root_handle, staging_name, handle)
    if _windows_read_handle_bounded(handle, len(expected_bytes) + 1) != expected_bytes:
        raise AtomicWriteError("published selector stage changed")
    _windows_require_restrictive_acl(handle, staging_name)


def _windows_publish_selector_stage(
    handle: int,
    root_handle: int,
    name: str,
    expected_bytes: bytes,
) -> None:
    """coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun.

    Intent
    ------
    coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun. The boundary coordinates handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_bounded with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun. Keep _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_bounded inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_existing_regular:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    ._windows_read_handle_bounded:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate handle, root_handle, name, and expected_bytes through _windows_existing_regular, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, _windows_verify_named_handle, and _windows_read_handle_boun."
    """
    _windows_existing_regular(root_handle, name)
    if not _windows_rename_handle(handle, root_handle, name, replace=True):
        raise AtomicWriteError("native staged selector replace collided")
    _windows_flush_handle(handle)
    _windows_verify_named_handle(root_handle, name, handle)
    if _windows_read_handle_bounded(handle, len(expected_bytes) + 1) != expected_bytes:
        raise AtomicWriteError("staged selector changed during replacement")
    _windows_require_restrictive_acl(handle, name)


def _windows_replace_inventory_regular_file(
    root_handle: int,
    name: str,
    data: bytes,
    _mode: int,
    build_name: str,
    staging_name: str,
    after_built: Callable[[], None],
    after_staged: Callable[[], None],
    after_replaced: Callable[[], None],
) -> None:
    """coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged.

    Intent
    ------
    coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged. The boundary coordinates root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged, and _windows_publish_selector_build with 3 guarded checks, 2 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged. Keep _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged, and _windows_publish_selector_build inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_publish_selector_build:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_publish_selector_stage:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_inventory_private_entry_present:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_open_exact_selector_stage:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    ._windows_open_rewritten_selector_build:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate root_handle, name, data, _mode, and build_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_open_exact_selector_stage, _windows_open_rewritten_selector_build, after_staged."
    """
    build_present = _windows_inventory_private_entry_present(root_handle, build_name)
    stage_present = _windows_inventory_private_entry_present(root_handle, staging_name)
    if build_present and stage_present:
        raise AtomicWriteError("selector build and stage are ambiguous")
    if stage_present:
        handle, lock = _windows_open_exact_selector_stage(
            root_handle, staging_name, data
        )
    else:
        handle, lock = _windows_open_rewritten_selector_build(
            root_handle,
            build_name,
            data,
            create=not build_present,
            after_created=after_built,
        )
    try:
        if stage_present:
            after_staged()
        else:
            _windows_publish_selector_build(handle, root_handle, staging_name, data)
            after_staged()
        _windows_publish_selector_stage(handle, root_handle, name, data)
        after_replaced()
    finally:
        try:
            _windows_unlock_handle(handle, lock)
        finally:
            _windows_close_handle(handle)


def _windows_discard_selector_private_entry(
    root_handle: int,
    private_name: str,
    expected_bytes: bytes,
    *,
    require_exact: bool,
) -> None:
    """Discard one exact private selector entry through retained native handles.

    Intent
    ------
    coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and. The boundary coordinates root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and _windows_verify_named_handle with 4 guarded checks, 7 cleanup or failure regions, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and. Keep _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and _windows_verify_named_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_lock_handle:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_open_exact_selector_stage:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate root_handle, private_name, expected_bytes, require_exact, and handle through _windows_open_exact_selector_stage, _windows_open_validated, _windows_lock_handle, AtomicWriteError, _windows_close_handle, and."
    """
    if require_exact:
        handle, lock = _windows_open_exact_selector_stage(
            root_handle, private_name, expected_bytes
        )
    else:
        handle = -1
        try:
            handle, _information = _windows_open_validated(
                root_handle,
                private_name,
                access=_WIN_MUTATE_ACCESS,
                disposition=1,
                options=_WIN_FILE_OPTIONS,
                directory=False,
                share=_WIN_SHARE_ALL & ~0x2,
            )
            try:
                lock = _windows_lock_handle(handle)
            except BaseException:
                raise AtomicWriteError(
                    "selector private entry lock failed"
                ) from None
        except BaseException:
            if handle >= 0:
                try:
                    _windows_close_handle(handle)
                except BaseException:
                    pass
            raise
    try:
        _windows_verify_named_handle(root_handle, private_name, handle)
        _windows_mark_delete(handle)
        _windows_unlock_handle(handle, lock)
        lock = None
        _windows_close_handle(handle)
        handle = -1
        try:
            verifier, _information = _windows_open_validated(
                root_handle,
                private_name,
                access=_WIN_READ_ACCESS,
                disposition=1,
                options=_WIN_FILE_OPTIONS,
                directory=False,
            )
        except FileNotFoundError:
            return
        try:
            raise AtomicWriteError("staged file remained after disposal")
        finally:
            _windows_close_handle(verifier)
    finally:
        if handle >= 0:
            try:
                if lock is not None:
                    _windows_unlock_handle(handle, lock)
            finally:
                _windows_close_handle(handle)


def _windows_retain_bounded_directory_inventory(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> RetainedBoundedDirectoryInventory:
    """coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle.

    Intent
    ------
    coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle. The boundary coordinates root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle, and Path with 1 guarded checks, and 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle. Keep _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle, and Path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._bounded_directory_name:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_confined_identity:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_discard_staged_inventory_regular_file:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_replace_inventory_regular_file:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    .RetainedBoundedDirectoryInventory:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_confined_identity:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_directory_entry_names:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_duplicate_handle:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_open_root:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_read_inventory_regular_file:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    ._windows_track_existing_regular_file_with_parents:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_handle."
    """
    access = _WIN_DIR_ACCESS | 0x00020000 | _WIN_LIST_DIRECTORY
    root_handle = _windows_open_root(root, final_access=access)
    transferred = False
    try:
        root_identity = _windows_confined_identity(root_handle)
        names = _windows_directory_entry_names(
            root_handle,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )

        def read_regular_file(name: str, maximum_bytes: int) -> ConfinedRegularFile:
            """Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h.

            Intent
            ------
            Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. The boundary coordinates name, and maximum_bytes through _bounded_directory_name, _windows_read_inventory_regular_file, str, int, name, and max_name_bytes with one closed state transition.

            Rationale
            ---------
            Because Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. Keep _bounded_directory_name, _windows_read_inventory_regular_file, str, int, name, and max_name_bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            return _windows_read_inventory_regular_file(
                root_handle,
                name,
                maximum_bytes,
            )

        def track_existing(
            name: str,
            expected_bytes: bytes,
            quarantine_id: str,
            after_relocate: Callable[[], None],
            after_dispose: Callable[[], None],
        ) -> TrackedExistingFile:
            """Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h.

            Intent
            ------
            Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. The boundary coordinates name, expected_bytes, quarantine_id, after_relocate, and after_dispose through _bounded_directory_name, _windows_duplicate_handle, _windows_track_existing_regular_file_with_parents, str, bytes, and Callable with one closed state transition.

            Rationale
            ---------
            Because Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. Keep _bounded_directory_name, _windows_duplicate_handle, _windows_track_existing_regular_file_with_parents, str, bytes, and Callable inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            duplicate = _windows_duplicate_handle(root_handle)
            return _windows_track_existing_regular_file_with_parents(
                [duplicate],
                (name,),
                expected_bytes,
                quarantine_id=quarantine_id,
                after_relocate=after_relocate,
                after_dispose=after_dispose,
            )

        def replace_regular_file(
            name: str,
            data: bytes,
            mode: int,
            build_name: str,
            staging_name: str,
            after_built: Callable[[], None],
            after_staged: Callable[[], None],
            after_replaced: Callable[[], None],
        ) -> None:
            """Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h.

            Intent
            ------
            Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. The boundary coordinates name, data, mode, build_name, and staging_name through _bounded_directory_name, _windows_replace_inventory_regular_file, str, bytes, int, and Callable with one closed state transition.

            Rationale
            ---------
            Because Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. Keep _bounded_directory_name, _windows_replace_inventory_regular_file, str, bytes, int, and Callable inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(build_name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(staging_name, max_name_bytes=max_name_bytes)
            _windows_replace_inventory_regular_file(
                root_handle,
                name,
                data,
                mode,
                build_name,
                staging_name,
                after_built,
                after_staged,
                after_replaced,
            )

        def discard_staged_regular_file(
            name: str,
            expected_bytes: bytes,
            build_name: str,
            staging_name: str,
            after_discarded: Callable[[], None],
        ) -> None:
            """Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h.

            Intent
            ------
            Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. The boundary coordinates name, expected_bytes, build_name, staging_name, and after_discarded through _bounded_directory_name, _windows_discard_staged_inventory_regular_file, str, bytes, Callable, and name with one closed state transition.

            Rationale
            ---------
            Because Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. Keep _bounded_directory_name, _windows_discard_staged_inventory_regular_file, str, bytes, Callable, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _bounded_directory_name(name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(build_name, max_name_bytes=max_name_bytes)
            _bounded_directory_name(staging_name, max_name_bytes=max_name_bytes)
            _windows_discard_staged_inventory_regular_file(
                root_handle,
                name,
                expected_bytes,
                build_name,
                staging_name,
                after_discarded,
            )

        def revalidate(expected_names: tuple[str, ...]) -> None:
            """Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h.

            Intent
            ------
            Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. The boundary coordinates expected_names, current_handle, and current_names through _windows_open_root, _windows_confined_identity, AtomicWriteError, _windows_close_handle, _windows_directory_entry_names, and tuple with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

            Rationale
            ---------
            Because Within coordinate root, max_entries, max_name_bytes, access, and root_handle through _windows_open_root, _windows_confined_identity, _windows_directory_entry_names, RetainedBoundedDirectoryInventory, _windows_close_h. Keep _windows_open_root, _windows_confined_identity, AtomicWriteError, _windows_close_handle, _windows_directory_entry_names, and tuple inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            current_handle = _windows_open_root(root, final_access=access)
            try:
                if _windows_confined_identity(current_handle) != root_identity:
                    raise AtomicWriteError("retained directory root changed")
            finally:
                _windows_close_handle(current_handle)
            current_names = _windows_directory_entry_names(
                root_handle,
                max_entries=max_entries,
                max_name_bytes=max_name_bytes,
            )
            if current_names != expected_names:
                raise AtomicWriteError("retained directory inventory changed")

        transferred = True
        return RetainedBoundedDirectoryInventory(
            names,
            read_regular_file=read_regular_file,
            track_existing=track_existing,
            replace_regular_file=replace_regular_file,
            discard_staged_regular_file=discard_staged_regular_file,
            revalidate=revalidate,
            release=lambda: _windows_close_handle(root_handle),
        )
    finally:
        if not transferred:
            _windows_close_handle(root_handle)


def _windows_read_regular_directory_entries(
    root: Path,
) -> tuple[ConfinedRegularFile, ...]:
    """Read files relative to one retained no-reparse directory handle.

    Intent
    ------
    Read files relative to one retained no-reparse directory handle. The boundary coordinates root, root_handle, entries, name, and child through _windows_open_root, _windows_directory_entry_names, _windows_open_validated, AtomicWriteError, append, and ConfinedRegularFile with 1 guarded checks, 3 cleanup or failure regions, 1 bounded iterations, and 1 typed refusals.

    Rationale
    ---------
    Because Read files relative to one retained no-reparse directory handle. Keep _windows_open_root, _windows_directory_entry_names, _windows_open_validated, AtomicWriteError, append, and ConfinedRegularFile inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    ._windows_directory_entry_names:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    .ConfinedRegularFile:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    ._windows_confined_identity:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    ._windows_open_root:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    ._windows_read_handle:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Read files relative to one retained no-reparse directory handle."
    """

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
    """Build a protected one-ACE DACL and absolute descriptor for this user.

    Intent
    ------
    Build a protected one-ACE DACL and absolute descriptor for this user. The boundary coordinates kernel32, advapi32, _ntdll, token, and size through _windows_modules, _WinHandle, OpenProcessToken, GetCurrentProcess, byref, and _windows_call_error with 7 guarded checks, 1 cleanup or failure regions, and 7 typed refusals.

    Rationale
    ---------
    Because Build a protected one-ACE DACL and absolute descriptor for this user. Keep _windows_modules, _WinHandle, OpenProcessToken, GetCurrentProcess, byref, and _windows_call_error inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Build a protected one-ACE DACL and absolute descriptor for this user."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Build a protected one-ACE DACL and absolute descriptor for this user."
    ._WinSecurityDescriptor:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Build a protected one-ACE DACL and absolute descriptor for this user."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Build a protected one-ACE DACL and absolute descriptor for this user."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Build a protected one-ACE DACL and absolute descriptor for this user."
    """

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
    """coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals. The boundary coordinates handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals. Keep _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, acl, _kernel32, advapi32, and _ntdll through _windows_modules, _windows_security_material, SetSecurityInfo, _WinHandle, _windows_call_error, and int with 2 guarded checks, and 1 typed refusals."
    """
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
    """coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio.

    Intent
    ------
    coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio. The boundary coordinates handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regions, and 5 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio. Keep _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio."
    ._WinAclSizeInformation:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate handle, kernel32, advapi32, _ntdll, and _sid_buffer through _windows_modules, _windows_security_material, c_void_p, GetSecurityInfo, _WinHandle, and byref with 12 guarded checks, 1 cleanup or failure regio."
    """
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
    """coordinate handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals. Keep _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_verify_handle_user_restrictive_acl:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, and name through _windows_verify_handle_user_restrictive_acl, AtomicWriteError, int, str, handle, and name with 1 guarded checks, and 1 typed refusals."
    """
    if not _windows_verify_handle_user_restrictive_acl(handle):
        raise AtomicWriteError(
            f"restrictive native ACL verification failed: {name}"
        )


def _windows_read_regular_file_bytes(path: Path, *, allowed_root: Path) -> bytes:
    """coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch.

    Intent
    ------
    coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch. The boundary coordinates path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_chain with 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch. Keep _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_chain inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."

    InstantiationsFromRepo
    ----------------------
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."
    ._windows_read_handle:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, allowed_root, parents, parts, and handle through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle, _windows_verify_named_handle, and _windows_close_ch."
    """
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


def _windows_read_regular_file_bytes_bounded(
    path: Path,
    *,
    allowed_root: Path,
    maximum_bytes: int,
) -> bytes:
    """coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif.

    Intent
    ------
    coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif. The boundary coordinates path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verify_named_handle with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif. Keep _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verify_named_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    ._windows_read_handle_bounded:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, allowed_root, maximum_bytes, parents, and parts through _windows_open_parent, _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, AtomicWriteError, and _windows_verif."
    """
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
        content = _windows_read_handle_bounded(handle, maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise AtomicWriteError("confined file exceeds the caller byte bound")
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parents[-1], parts[-1], handle)
        return content
    finally:
        _windows_close_chain(parents + ([handle] if handle >= 0 else []))


def _windows_existing_regular(parent_handle: int, name: str) -> bool:
    """coordinate parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions.

    Intent
    ------
    coordinate parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions. The boundary coordinates parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions.

    Rationale
    ---------
    Because coordinate parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions. Keep _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions."

    InstantiationsFromRepo
    ----------------------
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate parent_handle, name, handle, and _information through _windows_open_validated, _windows_close_handle, int, str, parent_handle, and name with 1 guarded checks, and 1 cleanup or failure regions."
    """
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


def _windows_discard_staged_inventory_regular_file(
    root_handle: int,
    _name: str,
    expected_bytes: bytes,
    build_name: str,
    staging_name: str,
    after_discarded: Callable[[], None],
) -> None:
    """coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w.

    Intent
    ------
    coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w. The boundary coordinates root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str with 2 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w. Keep _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_discard_selector_private_entry:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w."
    ._windows_inventory_private_entry_present:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate root_handle, _name, expected_bytes, build_name, and staging_name through _windows_inventory_private_entry_present, AtomicWriteError, _windows_discard_selector_private_entry, after_discarded, int, and str w."
    """
    build_present = _windows_inventory_private_entry_present(root_handle, build_name)
    stage_present = _windows_inventory_private_entry_present(root_handle, staging_name)
    if build_present and stage_present:
        raise AtomicWriteError("selector build and stage are ambiguous")
    if not build_present and not stage_present:
        raise AtomicWriteError("selector transaction disappeared")
    _windows_discard_selector_private_entry(
        root_handle,
        build_name if build_present else staging_name,
        expected_bytes,
        require_exact=stage_present,
    )
    after_discarded()


def _windows_mark_delete(handle: int) -> None:
    """coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r. Keep _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._WinFileDispositionInformation:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and disposition through _windows_modules, _WinFileDispositionInformation, SetFileInformationByHandle, _WinHandle, byref, and sizeof with 1 guarded checks, and 1 typed r."
    """
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
    """Atomically rename relative to a retained 64-bit directory handle.

    Intent
    ------
    Atomically rename relative to a retained 64-bit directory handle. The boundary coordinates handle, parent_handle, name, replace, and _kernel32 through _windows_modules, _windows_file_rename_info, _WinIoStatusBlock, NtSetInformationFile, _WinHandle, and byref with 4 guarded checks, 1 bounded iterations, and 3 typed refusals.

    Rationale
    ---------
    Because Atomically rename relative to a retained 64-bit directory handle. Keep _windows_modules, _windows_file_rename_info, _WinIoStatusBlock, NtSetInformationFile, _WinHandle, and byref inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Atomically rename relative to a retained 64-bit directory handle."
    ._WinIoStatusBlock:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Atomically rename relative to a retained 64-bit directory handle."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Atomically rename relative to a retained 64-bit directory handle."
    ._windows_file_rename_info:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Atomically rename relative to a retained 64-bit directory handle."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Atomically rename relative to a retained 64-bit directory handle."
    """

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
    """coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3.

    Intent
    ------
    coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3. The boundary coordinates parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3 cleanup or failure regions, 1 bounded iterations, and 3 typed refusals.

    Rationale
    ---------
    Because coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3. Keep _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_read_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_seek:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_write_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate parent_handle, name, data, _sid_buffer, and _sid through _windows_security_material, range, token_hex, _windows_open_validated, _windows_require_restrictive_acl, and _windows_seek with 1 guarded checks, 3."
    """
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
    """Prove a live native name identifies the expected retained regular handle.

    Intent
    ------
    coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or. The boundary coordinates parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or. Keep _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or."
    ._windows_file_id:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: coordinate parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or."
    """
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


def _windows_verify_named_directory_handle(
    parent_handle: int,
    name: str,
    expected_handle: int,
) -> None:
    """Reopen one retained directory name and require the same native identity.

    Intent
    ------
    Reopen one retained directory name and require the same native identity. The boundary coordinates parent_handle, name, expected_handle, verifier, and _information through _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Reopen one retained directory name and require the same native identity. Keep _windows_open_validated, _windows_file_id, AtomicWriteError, _windows_close_handle, int, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Reopen one retained directory name and require the same native identity."
    ._windows_file_id:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Reopen one retained directory name and require the same native identity."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Reopen one retained directory name and require the same native identity."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Reopen one retained directory name and require the same native identity."
    """

    verifier = -1
    try:
        verifier, _information = _windows_open_validated(
            parent_handle,
            name,
            access=_WIN_DIR_ACCESS,
            disposition=1,
            options=0x1 | 0x20,
            directory=True,
        )
        if _windows_file_id(verifier) != _windows_file_id(expected_handle):
            raise AtomicWriteError(f"directory changed during native write: {name}")
    finally:
        if verifier >= 0:
            _windows_close_handle(verifier)


def _windows_publication_file_state(
    path: Path,
    parents: list[int],
    parts: tuple[str, ...],
    *,
    expected_file_size: int | None,
) -> dict[str, object]:
    """Observe one native regular file while retaining its complete parent chain.

    Intent
    ------
    Observe one native regular file while retaining its complete parent chain. The boundary coordinates path, parents, parts, expected_file_size, and handle through _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, _windows_attribute_tag, _windows_verify_named_handle, and _windows_mode_from_attributes with 1 guarded checks, 2 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Observe one native regular file while retaining its complete parent chain. Keep _windows_verify_parent_chain, _windows_open_validated, _windows_read_handle_bounded, _windows_attribute_tag, _windows_verify_named_handle, and _windows_mode_from_attributes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_attribute_tag:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_mode_from_attributes:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    ._windows_read_handle_bounded:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Observe one native regular file while retaining its complete parent chain."
    """

    handle = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        try:
            handle, _information = _windows_open_validated(
                parents[-1],
                parts[-1],
                access=_WIN_READ_ACCESS,
                disposition=1,
                options=_WIN_FILE_OPTIONS,
                directory=False,
            )
        except FileNotFoundError:
            return {"kind": "absent"}
        maximum = (
            expected_file_size + 1
            if expected_file_size is not None
            else 1024 * 1024 + 1
        )
        data = _windows_read_handle_bounded(handle, maximum)
        attributes, _tag = _windows_attribute_tag(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parents[-1], parts[-1], handle)
        return {
            "kind": "file",
            "mode": _windows_mode_from_attributes(attributes, directory=False),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    except AtomicWriteError:
        raise
    except OSError as exc:
        raise AtomicWriteError("cannot observe native publication target") from exc
    finally:
        if handle >= 0:
            _windows_close_handle(handle)


def _windows_publication_state(
    parents: list[int],
    parts: tuple[str, ...],
    *,
    expected_file_size: int | None = None,
) -> dict[str, object]:
    """Observe one final native name without following a reparse point.

    Intent
    ------
    Observe one final native name without following a reparse point. The boundary coordinates parents, parts, expected_file_size, handle, and _information through _windows_verify_parent_chain, _windows_open_relative, _windows_attribute_tag, _windows_verify_named_reparse_handle, _windows_read_symlink_target, and _windows_verify_named_directory_handle with 4 guarded checks, and 2 cleanup or failure regions.

    Rationale
    ---------
    Because Observe one final native name without following a reparse point. Keep _windows_verify_parent_chain, _windows_open_relative, _windows_attribute_tag, _windows_verify_named_reparse_handle, _windows_read_symlink_target, and _windows_verify_named_directory_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_handle:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_verify_named_directory_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_verify_named_reparse_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."

    InstantiationsFromRepo
    ----------------------
    ._windows_attribute_tag:
      why:
        constructs: "This constructs edge is the number 6 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_mode_from_attributes:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_open_relative:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_read_handle_bounded:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    ._windows_read_symlink_target:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Observe one final native name without following a reparse point."
    """

    handle = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        try:
            handle, _information = _windows_open_relative(
                parents[-1],
                parts[-1],
                access=_WIN_READ_ACCESS,
                disposition=1,
                options=0x20,
            )
        except FileNotFoundError:
            return {"kind": "absent"}
        attributes, tag = _windows_attribute_tag(handle)
        _windows_verify_parent_chain(parents, parts)
        if attributes & 0x400:
            if tag != 0xA000000C:
                return {"kind": "other", "mode": 0}
            _windows_verify_named_reparse_handle(
                parents[-1], parts[-1], handle
            )
            return {"kind": "symlink", "target": _windows_read_symlink_target(handle)}
        if attributes & 0x10:
            _windows_verify_named_directory_handle(
                parents[-1], parts[-1], handle
            )
            return {
                "kind": "directory",
                "mode": _windows_mode_from_attributes(attributes, directory=True),
            }
        maximum = (
            expected_file_size + 1
            if expected_file_size is not None
            else 1024 * 1024 + 1
        )
        data = _windows_read_handle_bounded(handle, maximum)
        _windows_verify_named_handle(parents[-1], parts[-1], handle)
        return {
            "kind": "file",
            "mode": _windows_mode_from_attributes(attributes, directory=False),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    finally:
        if handle >= 0:
            _windows_close_handle(handle)


def _windows_atomic_publish_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish bytes through one retained deterministic native build handle.

    Intent
    ------
    Rewrite and lock the deterministic regular build, validate the exact eligible
    predecessor through retained native authority, and replace the final name.

    Rationale
    ---------
    Retaining build and legacy-symlink handles keeps identity, lexical-target,
    content, and supported-mode checks tied to the objects being published.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_read_handle_bounded:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_read_symlink_target:
      why:
        computes: "Reads the retained predecessor's exact lexical reparse target."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_set_supported_mode:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_verify_named_reparse_handle:
      why:
        computes: "Proves the live final name still identifies the retained predecessor."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 9 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_verify_supported_mode:
      why:
        computes: "This computes edge is the number 10 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_inventory_private_entry_present:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_open_rewritten_selector_build:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_open_reparse_point:
      why:
        constructs: "Retains no-follow authority over an eligible legacy symlink predecessor."
    ._windows_publication_file_state:
      why:
        constructs: "This constructs edge is the number 15 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    ._windows_read_symlink_target:
      why:
        constructs: "Builds the exact predecessor state used for comparison."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 16 repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, mode, and build_id through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_inventory_private_entry_present, _windows_open_rewritten_selector_build, a."
    """
    parents, parts = _windows_open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    handle = -1
    expected_symlink_handle = -1
    lock: object | None = None
    try:
        _windows_verify_parent_chain(parents, parts)
        build_present = _windows_inventory_private_entry_present(
            parents[-1], build_name
        )
        handle, lock = _windows_open_rewritten_selector_build(
            parents[-1],
            build_name,
            data,
            create=not build_present,
            after_created=lambda: None,
        )
        _windows_set_supported_mode(handle, mode, directory=False)
        if expected_before.get("kind") == "symlink":
            expected_symlink_handle = _windows_open_reparse_point(
                parents[-1], parts[-1]
            )
            actual = {
                "kind": "symlink",
                "target": _windows_read_symlink_target(expected_symlink_handle),
            }
            _windows_verify_named_reparse_handle(
                parents[-1], parts[-1], expected_symlink_handle
            )
        else:
            expected_size = expected_before.get("size")
            actual = _windows_publication_file_state(
                path,
                parents,
                parts,
                expected_file_size=(
                    expected_size if isinstance(expected_size, int) else None
                ),
            )
        if actual != dict(expected_before):
            raise AtomicWriteError("publication target differs from expected state")
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parents[-1], build_name, handle)
        if expected_symlink_handle >= 0:
            if (
                _windows_read_symlink_target(expected_symlink_handle)
                != expected_before["target"]
            ):
                raise AtomicWriteError("publication target differs from expected state")
            _windows_verify_named_reparse_handle(
                parents[-1], parts[-1], expected_symlink_handle
            )
        if not _windows_rename_handle(
            handle, parents[-1], parts[-1], replace=True
        ):
            raise AtomicWriteError("native publication replacement collided")
        _windows_flush_handle(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_handle(parents[-1], parts[-1], handle)
        if _windows_read_handle_bounded(handle, len(data) + 1) != data:
            raise AtomicWriteError("native published file changed")
        _windows_verify_supported_mode(handle, mode, directory=False)
    finally:
        try:
            if handle >= 0 and lock is not None:
                _windows_unlock_handle(handle, lock)
        finally:
            try:
                if handle >= 0:
                    _windows_close_handle(handle)
            finally:
                try:
                    if expected_symlink_handle >= 0:
                        _windows_close_handle(expected_symlink_handle)
                finally:
                    _windows_close_chain(parents)


def _windows_atomic_publish_empty_directory(
    path: Path,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish one empty directory from a retained native build handle.

    Intent
    ------
    Validate or create the deterministic empty build, apply its supported mode,
    and rename without replacement only when the destination remains absent.

    Rationale
    ---------
    Retained parent and directory handles keep emptiness, identity, and mode
    verification bound to the namespace objects involved in publication.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_directory_entry_names:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_set_supported_mode:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_verify_named_directory_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_verify_supported_mode:
      why:
        computes: "This computes edge is the number 9 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_publication_state:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 15 repository dependency used to uphold this guarantee: coordinate path, allowed_root, mode, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, get, _windows_publication_state, and dict with 6 guarded checks, 4 clean."
    """
    parents, parts = _windows_open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    handle = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        if expected_before.get("kind") == "directory":
            actual = _windows_publication_state(parents, parts)
            if actual != dict(expected_before):
                raise AtomicWriteError("publication target differs from expected state")
            return
        try:
            handle, _information = _windows_open_validated(
                parents[-1],
                build_name,
                access=_WIN_MUTATE_ACCESS | _WIN_DIR_ACCESS,
                disposition=1,
                options=0x1 | 0x20,
                directory=True,
            )
        except FileNotFoundError:
            _sid_buffer, _sid, _acl, security = _windows_security_material()
            handle, _information = _windows_open_validated(
                parents[-1],
                build_name,
                access=_WIN_MUTATE_ACCESS | _WIN_DIR_ACCESS,
                disposition=2,
                options=0x1 | 0x2 | 0x20,
                directory=True,
                security_descriptor=security,
            )
        if _windows_directory_entry_names(
            handle, max_entries=1, max_name_bytes=1024
        ):
            raise AtomicWriteError("publication directory build is not empty")
        _windows_set_supported_mode(handle, mode, directory=True)
        _windows_verify_parent_chain(parents, parts)
        try:
            target_handle, _information = _windows_open_validated(
                parents[-1],
                parts[-1],
                access=_WIN_DIR_ACCESS,
                disposition=1,
                options=0x1 | 0x20,
                directory=True,
            )
        except FileNotFoundError:
            actual = {"kind": "absent"}
        else:
            _windows_close_handle(target_handle)
            actual = {"kind": "directory"}
        if actual != dict(expected_before) or actual != {"kind": "absent"}:
            raise AtomicWriteError("publication target differs from expected state")
        _windows_verify_named_directory_handle(parents[-1], build_name, handle)
        if not _windows_rename_handle(
            handle, parents[-1], parts[-1], replace=False
        ):
            raise AtomicWriteError("native directory publication collided")
        _windows_flush_handle(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_directory_handle(parents[-1], parts[-1], handle)
        _windows_verify_supported_mode(handle, mode, directory=True)
    finally:
        try:
            if handle >= 0:
                _windows_close_handle(handle)
        finally:
            _windows_close_chain(parents)


def _windows_atomic_publish_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish one exact symlink while retaining parent and build authority.

    Intent
    ------
    Publish one exact symlink while retaining parent and build authority. The boundary coordinates path, target, allowed_root, build_id, and expected_before through _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_open_or_create_symlink_build, _windows_read_symlink_target, and AtomicWriteError with 6 guarded checks, 2 cleanup or failure regions, and 5 typed refusals.

    Rationale
    ---------
    Because Publish one exact symlink while retaining parent and build authority. Keep _windows_open_parent, build_file_name, _windows_verify_parent_chain, _windows_open_or_create_symlink_build, _windows_read_symlink_target, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_read_symlink_target:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_verify_named_reparse_handle:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_open_or_create_symlink_build:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    ._windows_publication_state:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    .build_file_name:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: Publish one exact symlink while retaining parent and build authority."
    """

    parents, parts = _windows_open_parent(path, allowed_root)
    build_name = build_file_name(build_id)
    handle = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        handle = _windows_open_or_create_symlink_build(
            path.parent / build_name,
            parents[-1],
            build_name,
            target,
        )
        if _windows_read_symlink_target(handle) != target:
            raise AtomicWriteError("publication symlink build has the wrong target")
        actual = _windows_publication_state(parents, parts)
        if actual != dict(expected_before):
            raise AtomicWriteError("publication target differs from expected state")
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_reparse_handle(parents[-1], build_name, handle)
        if _windows_read_symlink_target(handle) != target:
            raise AtomicWriteError("publication symlink build changed")
        if not _windows_rename_handle(
            handle, parents[-1], parts[-1], replace=True
        ):
            raise AtomicWriteError("native symlink publication collided")
        _windows_flush_handle(handle)
        _windows_verify_parent_chain(parents, parts)
        _windows_verify_named_reparse_handle(parents[-1], parts[-1], handle)
        if _windows_read_symlink_target(handle) != target:
            raise AtomicWriteError("native published symlink changed")
    finally:
        try:
            if handle >= 0:
                _windows_close_handle(handle)
        finally:
            _windows_close_chain(parents)


def _windows_atomic_unlink_exact_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    expected_before: Mapping[str, object],
) -> None:
    """Delete-mark one retained exact symlink and prove the name absent.

    Intent
    ------
    Delete-mark one retained exact symlink and prove the name absent. The boundary coordinates path, target, allowed_root, expected_before, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_open_reparse_point, _windows_read_symlink_target, dict, and AtomicWriteError with 3 guarded checks, 4 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Delete-mark one retained exact symlink and prove the name absent. Keep _windows_open_parent, _windows_verify_parent_chain, _windows_open_reparse_point, _windows_read_symlink_target, dict, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_read_symlink_target:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_verify_named_reparse_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 7 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 8 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    ._windows_open_reparse_point:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: Delete-mark one retained exact symlink and prove the name absent."
    """

    parents, parts = _windows_open_parent(path, allowed_root)
    handle = -1
    verifier = -1
    try:
        _windows_verify_parent_chain(parents, parts)
        handle = _windows_open_reparse_point(
            parents[-1], parts[-1], access=_WIN_MUTATE_ACCESS
        )
        if _windows_read_symlink_target(handle) != target or dict(
            expected_before
        ) != {"kind": "symlink", "target": target}:
            raise AtomicWriteError("exact symlink differs from expected target")
        _windows_verify_named_reparse_handle(parents[-1], parts[-1], handle)
        _windows_mark_delete(handle)
        _windows_close_handle(handle)
        handle = -1
        _windows_verify_parent_chain(parents, parts)
        try:
            verifier = _windows_open_reparse_point(parents[-1], parts[-1])
        except FileNotFoundError:
            return
        raise AtomicWriteError("exact symlink name remained after native unlink")
    finally:
        try:
            if verifier >= 0:
                _windows_close_handle(verifier)
        finally:
            try:
                if handle >= 0:
                    _windows_close_handle(handle)
            finally:
                _windows_close_chain(parents)


def _windows_atomic_write_bytes_with_parents(
    parents: list[int],
    parts: tuple[str, ...],
    data: bytes,
    *,
    replace: bool,
) -> bool:
    """Write bytes atomically through an already retained native parent chain.

    Intent
    ------
    coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi. The boundary coordinates parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle with 7 guarded checks, 2 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi. Keep _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_existing_regular:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_read_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 9 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_rename_handle:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    ._windows_write_temp:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: coordinate parents, parts, data, replace, and parent_handle through _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_rename_handle, AtomicWriteError, and _windows_flush_handle wi."
    """
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


def _windows_atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    replace: bool,
) -> bool:
    """coordinate path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition.

    Intent
    ------
    coordinate path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition. The boundary coordinates path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition.

    Rationale
    ---------
    Because coordinate path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition. Keep _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_atomic_write_bytes_with_parents:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, replace, and parents through _windows_open_parent, _windows_atomic_write_bytes_with_parents, Path, bytes, bool, and path with one closed state transition."
    """
    parents, parts = _windows_open_parent(path, allowed_root)
    return _windows_atomic_write_bytes_with_parents(
        parents,
        parts,
        data,
        replace=replace,
    )


def _windows_atomic_replace_bytes(
    path: Path, data: bytes, *, allowed_root: Path, mode: int
) -> None:
    """Replace one native byte-file leaf after the public layer normalizes its mode.

    Intent
    ------
    Discard the already-consumed mode and request the shared native writer's replace branch.

    Rationale
    ---------
    This adapter keeps replace semantics distinct from create-only publication at the platform dispatch boundary.

    Pseudocode
    ----------
    - set normalized_replace_request = received_context
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_atomic_write_bytes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition."
    """
    del mode
    _windows_atomic_write_bytes(path, data, allowed_root=allowed_root, replace=True)


def _windows_atomic_create_bytes(
    path: Path, data: bytes, *, allowed_root: Path, mode: int
) -> bool:
    """coordinate path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition.

    Intent
    ------
    coordinate path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition. The boundary coordinates path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition.

    Rationale
    ---------
    Because coordinate path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition. Keep _windows_atomic_write_bytes, Path, bytes, int, path, and data inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_atomic_write_bytes:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, and mode through _windows_atomic_write_bytes, Path, bytes, int, path, and data with one closed state transition."
    """
    del mode
    return _windows_atomic_write_bytes(path, data, allowed_root=allowed_root, replace=False)


def _windows_atomic_create_bytes_tracked(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
) -> TrackedFileCreation | None:
    """Create a file while retaining native handles for exact cleanup.

    Intent
    ------
    Create a file while retaining native handles for exact cleanup. The boundary coordinates path, data, allowed_root, mode, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_confined_identity, and _windows_rename_handle with 5 guarded checks, 2 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Create a file while retaining native handles for exact cleanup. Keep _windows_open_parent, _windows_verify_parent_chain, _windows_existing_regular, _windows_write_temp, _windows_confined_identity, and _windows_rename_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_confined_identity:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_existing_regular:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_read_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 9 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 10 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    .TrackedFileCreation:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_confined_identity:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 15 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_rename_handle:
      why:
        constructs: "This constructs edge is the number 16 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    ._windows_write_temp:
      why:
        constructs: "This constructs edge is the number 17 repository dependency used to uphold this guarantee: Create a file while retaining native handles for exact cleanup."
    """

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
            """Within Create a file while retaining native handles for exact cleanup, coordinate closed local state through _windows_close_chain, parents, and temp_handle with one closed state transition.

            Intent
            ------
            Within Create a file while retaining native handles for exact cleanup, coordinate closed local state through _windows_close_chain, parents, and temp_handle with one closed state transition. The boundary coordinates closed local state through _windows_close_chain, parents, and temp_handle with one closed state transition.

            Rationale
            ---------
            Because Within Create a file while retaining native handles for exact cleanup, coordinate closed local state through _windows_close_chain, parents, and temp_handle with one closed state transition. Keep _windows_close_chain, parents, and temp_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
            _windows_close_chain(parents + [temp_handle])

        def remove() -> None:
            """Within Create a file while retaining native handles for exact cleanup, coordinate verifier, and _information through _windows_open_validated, _windows_confined_identity, AtomicWriteError, _windows_mark_delete, _windo.

            Intent
            ------
            Within Create a file while retaining native handles for exact cleanup, coordinate verifier, and _information through _windows_open_validated, _windows_confined_identity, AtomicWriteError, _windows_mark_delete, _windo. The boundary coordinates verifier, and _information through _windows_open_validated, _windows_confined_identity, AtomicWriteError, _windows_mark_delete, _windows_close_handle, and parent_handle with 2 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

            Rationale
            ---------
            Because Within Create a file while retaining native handles for exact cleanup, coordinate verifier, and _information through _windows_open_validated, _windows_confined_identity, AtomicWriteError, _windows_mark_delete, _windo. Keep _windows_open_validated, _windows_confined_identity, AtomicWriteError, _windows_mark_delete, _windows_close_handle, and parent_handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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


def _windows_track_existing_regular_file_with_parents(
    parents: list[int],
    parts: tuple[str, ...],
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    after_relocate: Callable[[], None] | None = None,
    after_dispose: Callable[[], None] | None = None,
) -> TrackedExistingFile:
    """Retain native handle authority over one recovery-file transaction.

    Intent
    ------
    Retain native handle authority over one recovery-file transaction. The boundary coordinates parents, parts, expected_bytes, quarantine_id, and after_relocate through _quarantine_name, _windows_verify_parent_chain, open_optional, _windows_close_handle, _windows_close_chain, and AtomicWriteError with 8 guarded checks, 5 cleanup or failure regions, and 6 typed refusals.

    Rationale
    ---------
    Because Retain native handle authority over one recovery-file transaction. Keep _quarantine_name, _windows_verify_parent_chain, open_optional, _windows_close_handle, _windows_close_chain, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_close_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_read_handle_bounded:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_rename_handle:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 9 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 10 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    .TrackedExistingFile:
      why:
        constructs: "This constructs edge is the number 11 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._quarantine_name:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_confined_identity:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_lock_handle:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 15 repository dependency used to uphold this guarantee: Retain native handle authority over one recovery-file transaction."
    """

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
            """Open one optional recovery candidate and retain its exact identity.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate candidate, candidate_handle, and _information through _windows_open_validated, str, parent_handle, candidate, _WIN_READ_ACCESS, and. The boundary coordinates candidate, candidate_handle, and _information through _windows_open_validated, str, parent_handle, candidate, _WIN_READ_ACCESS, and _WIN_GENERIC_WRITE with 1 cleanup or failure regions.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate candidate, candidate_handle, and _information through _windows_open_validated, str, parent_handle, candidate, _WIN_READ_ACCESS, and. Keep _windows_open_validated, str, parent_handle, candidate, _WIN_READ_ACCESS, and _WIN_GENERIC_WRITE inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            """Within Retain native handle authority over one recovery-file transaction, coordinate first_error, lock_held, handle_open, and parents_open through _windows_unlock_handle, _windows_close_handle, _windows_close_chain.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate first_error, lock_held, handle_open, and parents_open through _windows_unlock_handle, _windows_close_handle, _windows_close_chain. The boundary coordinates first_error, lock_held, handle_open, and parents_open through _windows_unlock_handle, _windows_close_handle, _windows_close_chain, BaseException, lock_held, and handle with 6 guarded checks, 3 cleanup or failure regions, and 1 typed refusals.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate first_error, lock_held, handle_open, and parents_open through _windows_unlock_handle, _windows_close_handle, _windows_close_chain. Keep _windows_unlock_handle, _windows_close_handle, _windows_close_chain, BaseException, lock_held, and handle inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            """Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and other through open_optional, AtomicWriteError, _windows_close_handle, str, candidate, and other with 1 guarded check.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and other through open_optional, AtomicWriteError, _windows_close_handle, str, candidate, and other with 1 guarded check. The boundary coordinates candidate, and other through open_optional, AtomicWriteError, _windows_close_handle, str, candidate, and other with 1 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and other through open_optional, AtomicWriteError, _windows_close_handle, str, candidate, and other with 1 guarded check. Keep open_optional, AtomicWriteError, _windows_close_handle, str, candidate, and other inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            """Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and transition through _windows_verify_parent_chain, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_nam.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and transition through _windows_verify_parent_chain, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_nam. The boundary coordinates candidate, and transition through _windows_verify_parent_chain, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_named_handle, str, and parents with 1 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate candidate, and transition through _windows_verify_parent_chain, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_nam. Keep _windows_verify_parent_chain, _windows_read_handle_bounded, AtomicWriteError, _windows_verify_named_handle, str, and parents inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            """Within Retain native handle authority over one recovery-file transaction, coordinate at_quarantine through require_other_absent, require_exact, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, and at_.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate at_quarantine through require_other_absent, require_exact, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, and at_. The boundary coordinates at_quarantine through require_other_absent, require_exact, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, and at_quarantine with 2 guarded checks, and 1 typed refusals.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate at_quarantine through require_other_absent, require_exact, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, and at_. Keep require_other_absent, require_exact, _windows_rename_handle, AtomicWriteError, _windows_flush_handle, and at_quarantine inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            """Within Retain native handle authority over one recovery-file transaction, coordinate lock_held, handle_open, and remaining through require_other_absent, require_exact, _windows_mark_delete, _windows_unlock_handle, _w.

            Intent
            ------
            Within Retain native handle authority over one recovery-file transaction, coordinate lock_held, handle_open, and remaining through require_other_absent, require_exact, _windows_mark_delete, _windows_unlock_handle, _w. The boundary coordinates lock_held, handle_open, and remaining through require_other_absent, require_exact, _windows_mark_delete, _windows_unlock_handle, _windows_close_handle, and open_optional with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

            Rationale
            ---------
            Because Within Retain native handle authority over one recovery-file transaction, coordinate lock_held, handle_open, and remaining through require_other_absent, require_exact, _windows_mark_delete, _windows_unlock_handle, _w. Keep require_other_absent, require_exact, _windows_mark_delete, _windows_unlock_handle, _windows_close_handle, and open_optional inside this boundary so authority or partial state cannot escape before final verification or typed failure.

            Pseudocode
            ----------
            - return

            Wraps
            -----
            - none
            """
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
            after_relocate=after_relocate,
            after_dispose=after_dispose,
        )
    finally:
        if not transferred:
            try:
                if handle >= 0 and lock is not None:
                    _windows_unlock_handle(handle, lock)
            finally:
                _windows_close_chain(parents + ([handle] if handle >= 0 else []))


def _windows_track_existing_regular_file(
    path: Path,
    expected_bytes: bytes,
    *,
    quarantine_id: str,
    allowed_root: Path,
) -> TrackedExistingFile:
    """coordinate path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition.

    Intent
    ------
    coordinate path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition. The boundary coordinates path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition.

    Rationale
    ---------
    Because coordinate path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition. Keep _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition."
    ._windows_track_existing_regular_file_with_parents:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate path, expected_bytes, quarantine_id, allowed_root, and parents through _windows_open_parent, _windows_track_existing_regular_file_with_parents, Path, bytes, str, and path with one closed state transition."
    """
    parents, parts = _windows_open_parent(path, allowed_root)
    return _windows_track_existing_regular_file_with_parents(
        parents,
        parts,
        expected_bytes,
        quarantine_id=quarantine_id,
    )


def _windows_lock_handle(handle: int) -> _WinOverlapped:
    # LockFileEx serializes cooperative writers on the complete file range.
    """coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals. Keep _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._WinOverlapped:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals."
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: coordinate handle, kernel32, _advapi32, _ntdll, and overlapped through _windows_modules, _WinOverlapped, LockFileEx, byref, _windows_call_error, and int with 1 guarded checks, and 1 typed refusals."
    """
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
    """coordinate handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals.

    Intent
    ------
    coordinate handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals. The boundary coordinates handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because coordinate handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals. Keep _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_call_error:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: coordinate handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals."
    ._windows_modules:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: coordinate handle, overlapped, kernel32, _advapi32, and _ntdll through _windows_modules, UnlockFileEx, byref, _windows_call_error, int, and object with 1 guarded checks, and 1 typed refusals."
    """
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
    """coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr.

    Intent
    ------
    coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr. The boundary coordinates path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteError with 7 guarded checks, 4 cleanup or failure regions, and 4 typed refusals.

    Rationale
    ---------
    Because coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr. Keep _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_close_chain:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_flush_handle:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_mark_delete:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_read_handle:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_require_restrictive_acl:
      why:
        computes: "This computes edge is the number 5 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_seek:
      why:
        computes: "This computes edge is the number 6 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_set_user_restrictive_acl:
      why:
        computes: "This computes edge is the number 7 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_unlock_handle:
      why:
        computes: "This computes edge is the number 8 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_verify_named_handle:
      why:
        computes: "This computes edge is the number 9 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_verify_parent_chain:
      why:
        computes: "This computes edge is the number 10 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_write_handle:
      why:
        computes: "This computes edge is the number 11 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 12 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_lock_handle:
      why:
        constructs: "This constructs edge is the number 13 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_open_parent:
      why:
        constructs: "This constructs edge is the number 14 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_open_validated:
      why:
        constructs: "This constructs edge is the number 15 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_read_handle:
      why:
        constructs: "This constructs edge is the number 16 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    ._windows_security_material:
      why:
        constructs: "This constructs edge is the number 17 repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and parents through _windows_open_parent, _windows_verify_parent_chain, _windows_security_material, isinstance, _windows_open_validated, and AtomicWriteEr."
    """
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
    """Append bytes natively without requiring a predecessor snapshot.

    Intent
    ------
    Route the request through the locked append implementation with its unconditional sentinel.

    Rationale
    ---------
    A dedicated adapter prevents unconditional append from being confused with compare-and-append.

    Pseudocode
    ----------
    - set validated_append_request = received_context
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_append_bytes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, allowed_root, and mode through _windows_append_bytes, Path, bytes, int, path, and data with one closed state transition."
    """
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
    """Append bytes natively only after the complete predecessor matches.

    Intent
    ------
    Forward the caller's closed predecessor snapshot to the locked append implementation.

    Rationale
    ---------
    Keeping this adapter explicit exposes the conflict-detecting contract separately from unconditional append.

    Pseudocode
    ----------
    - set matched_predecessor_append = local_decisions
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._windows_append_bytes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: coordinate path, data, expected_previous_bytes, allowed_root, and mode through _windows_append_bytes, Path, bytes, int, path, and data with one closed state transition."
    """
    del mode
    _windows_append_bytes(
        path,
        data,
        expected_previous_bytes=expected_previous_bytes,
        allowed_root=allowed_root,
    )


def _is_capability_error(error: BaseException) -> bool:
    """coordinate error through isinstance, BaseException, error, AtomicWriteError, str, and _CAPABILITY_ERROR with one closed state transition.

    Intent
    ------
    coordinate error through isinstance, BaseException, error, AtomicWriteError, str, and _CAPABILITY_ERROR with one closed state transition. The boundary coordinates error through isinstance, BaseException, error, AtomicWriteError, str, and _CAPABILITY_ERROR with one closed state transition.

    Rationale
    ---------
    Because coordinate error through isinstance, BaseException, error, AtomicWriteError, str, and _CAPABILITY_ERROR with one closed state transition. Keep isinstance, BaseException, error, AtomicWriteError, str, and _CAPABILITY_ERROR inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    return isinstance(error, AtomicWriteError) and str(error) == _CAPABILITY_ERROR


def read_regular_file_bytes(
    path: Path,
    *,
    allowed_root: Path,
    allow_non_atomic: bool = False,
) -> bytes:
    """Read one confined regular file, failing closed unless fallback is explicit.

    Intent
    ------
    Read one confined regular file, failing closed unless fallback is explicit. The boundary coordinates path, allowed_root, and allow_non_atomic through _windows_read_regular_file_bytes, _posix_read_regular_file_bytes, _is_capability_error, _fallback_read_regular_file_bytes, Path, and bool with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Read one confined regular file, failing closed unless fallback is explicit. Keep _windows_read_regular_file_bytes, _posix_read_regular_file_bytes, _is_capability_error, _fallback_read_regular_file_bytes, Path, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_capability_error:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Read one confined regular file, failing closed unless fallback is explicit."

    InstantiationsFromRepo
    ----------------------
    ._fallback_read_regular_file_bytes:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read one confined regular file, failing closed unless fallback is explicit."
    ._posix_read_regular_file_bytes:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Read one confined regular file, failing closed unless fallback is explicit."
    ._windows_read_regular_file_bytes:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Read one confined regular file, failing closed unless fallback is explicit."
    """

    try:
        if os.name == "nt":
            return _windows_read_regular_file_bytes(path, allowed_root=allowed_root)
        return _posix_read_regular_file_bytes(path, allowed_root=allowed_root)
    except AtomicWriteError as exc:
        if not allow_non_atomic or not _is_capability_error(exc):
            raise
        return _fallback_read_regular_file_bytes(path, allowed_root=allowed_root)


def read_regular_file_bytes_bounded(
    path: Path,
    *,
    allowed_root: Path,
    maximum_bytes: int,
) -> bytes:
    """Read a confined regular file while enforcing a preallocation-safe cap.

    Intent
    ------
    Read a confined regular file while enforcing a preallocation-safe cap. The boundary coordinates path, allowed_root, and maximum_bytes through isinstance, TypeError, ValueError, _windows_read_regular_file_bytes_bounded, _posix_read_regular_file_bytes_bounded, and Path with 3 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Read a confined regular file while enforcing a preallocation-safe cap. Keep isinstance, TypeError, ValueError, _windows_read_regular_file_bytes_bounded, _posix_read_regular_file_bytes_bounded, and Path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._posix_read_regular_file_bytes_bounded:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read a confined regular file while enforcing a preallocation-safe cap."
    ._windows_read_regular_file_bytes_bounded:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read a confined regular file while enforcing a preallocation-safe cap."
    """

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
        raise TypeError("maximum_bytes must be an integer")
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must not be negative")
    if os.name == "nt":
        return _windows_read_regular_file_bytes_bounded(
            path,
            allowed_root=allowed_root,
            maximum_bytes=maximum_bytes,
        )
    return _posix_read_regular_file_bytes_bounded(
        path,
        allowed_root=allowed_root,
        maximum_bytes=maximum_bytes,
    )


def atomic_replace_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> None:
    """Securely replace a file, with only an explicit non-atomic fallback.

    Intent
    ------
    Securely replace a file, with only an explicit non-atomic fallback. The boundary coordinates path, data, allowed_root, mode, and allow_non_atomic through _windows_atomic_replace_bytes, _posix_atomic_replace_bytes, _is_capability_error, _fallback_write, Path, and bytes with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Securely replace a file, with only an explicit non-atomic fallback. Keep _windows_atomic_replace_bytes, _posix_atomic_replace_bytes, _is_capability_error, _fallback_write, Path, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._fallback_write:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Securely replace a file, with only an explicit non-atomic fallback."
    ._is_capability_error:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Securely replace a file, with only an explicit non-atomic fallback."
    ._posix_atomic_replace_bytes:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Securely replace a file, with only an explicit non-atomic fallback."
    ._windows_atomic_replace_bytes:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Securely replace a file, with only an explicit non-atomic fallback."
    """

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


def atomic_publish_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish exact bytes through one deterministic, restartable build name.

    Intent
    ------
    Publish exact bytes through one deterministic, restartable build name. The boundary coordinates path, data, allowed_root, mode, and build_id through isinstance, TypeError, normalize_publication_mode, build_file_name, get, and AtomicWriteError with 4 guarded checks, and 3 typed refusals.

    Rationale
    ---------
    Because Publish exact bytes through one deterministic, restartable build name. Keep isinstance, TypeError, normalize_publication_mode, build_file_name, get, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_atomic_publish_bytes:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Publish exact bytes through one deterministic, restartable build name."
    ._windows_atomic_publish_bytes:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Publish exact bytes through one deterministic, restartable build name."
    .build_file_name:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Publish exact bytes through one deterministic, restartable build name."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Publish exact bytes through one deterministic, restartable build name."
    .normalize_publication_mode:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Publish exact bytes through one deterministic, restartable build name."
    """

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    mode = normalize_publication_mode(mode)
    build_file_name(build_id)
    if not isinstance(expected_before, Mapping):
        raise TypeError("expected_before must be a mapping")
    if expected_before.get("kind") not in {"absent", "file", "symlink"}:
        raise AtomicWriteError(
            "expected_before kind is ineligible for byte publication"
        )
    if os.name == "nt":
        _windows_atomic_publish_bytes(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            build_id=build_id,
            expected_before=expected_before,
        )
    else:
        _posix_atomic_publish_bytes(
            path,
            data,
            allowed_root=allowed_root,
            mode=mode,
            build_id=build_id,
            expected_before=expected_before,
        )


def atomic_publish_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Atomically publish one exact lexical symlink through a durable build.

    Intent
    ------
    Atomically publish one exact lexical symlink through a durable build. The boundary coordinates path, target, allowed_root, build_id, and expected_before through isinstance, ValueError, build_file_name, TypeError, get, and AtomicWriteError with 4 guarded checks, and 3 typed refusals.

    Rationale
    ---------
    Because Atomically publish one exact lexical symlink through a durable build. Keep isinstance, ValueError, build_file_name, TypeError, get, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_atomic_publish_symlink:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Atomically publish one exact lexical symlink through a durable build."
    ._windows_atomic_publish_symlink:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Atomically publish one exact lexical symlink through a durable build."
    .build_file_name:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Atomically publish one exact lexical symlink through a durable build."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Atomically publish one exact lexical symlink through a durable build."
    """

    if not isinstance(target, str) or not target:
        raise ValueError("target must be a nonempty string")
    build_file_name(build_id)
    if not isinstance(expected_before, Mapping):
        raise TypeError("expected_before must be a mapping")
    if expected_before.get("kind") not in {"absent", "symlink"}:
        raise AtomicWriteError(
            "expected_before kind is ineligible for symlink publication"
        )
    if os.name == "nt":
        _windows_atomic_publish_symlink(
            path,
            target,
            allowed_root=allowed_root,
            build_id=build_id,
            expected_before=expected_before,
        )
        return
    _posix_atomic_publish_symlink(
        path,
        target,
        allowed_root=allowed_root,
        build_id=build_id,
        expected_before=expected_before,
    )


def atomic_publish_empty_directory(
    path: Path,
    *,
    allowed_root: Path,
    mode: int,
    build_id: str,
    expected_before: Mapping[str, object],
) -> None:
    """Publish one empty directory without recursively creating ancestors.

    Intent
    ------
    Publish one empty directory without recursively creating ancestors. The boundary coordinates path, allowed_root, mode, build_id, and expected_before through normalize_publication_mode, build_file_name, isinstance, TypeError, get, and AtomicWriteError with 3 guarded checks, and 2 typed refusals.

    Rationale
    ---------
    Because Publish one empty directory without recursively creating ancestors. Keep normalize_publication_mode, build_file_name, isinstance, TypeError, get, and AtomicWriteError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_atomic_publish_empty_directory:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Publish one empty directory without recursively creating ancestors."
    ._windows_atomic_publish_empty_directory:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Publish one empty directory without recursively creating ancestors."
    .build_file_name:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Publish one empty directory without recursively creating ancestors."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Publish one empty directory without recursively creating ancestors."
    .normalize_publication_mode:
      why:
        constructs: "This constructs edge is the number 5 repository dependency used to uphold this guarantee: Publish one empty directory without recursively creating ancestors."
    """

    mode = normalize_publication_mode(mode, directory=True)
    build_file_name(build_id)
    if not isinstance(expected_before, Mapping):
        raise TypeError("expected_before must be a mapping")
    if expected_before.get("kind") not in {"absent", "directory"}:
        raise AtomicWriteError(
            "expected_before kind is ineligible for directory publication"
        )
    if os.name == "nt":
        _windows_atomic_publish_empty_directory(
            path,
            allowed_root=allowed_root,
            mode=mode,
            build_id=build_id,
            expected_before=expected_before,
        )
    else:
        _posix_atomic_publish_empty_directory(
            path,
            allowed_root=allowed_root,
            mode=mode,
            build_id=build_id,
            expected_before=expected_before,
        )


def atomic_unlink_exact_symlink(
    path: Path,
    target: str,
    *,
    allowed_root: Path,
    expected_before: Mapping[str, object],
) -> None:
    """Unlink one confined symlink only after exact lexical revalidation.

    Intent
    ------
    Unlink one confined symlink only after exact lexical revalidation. The boundary coordinates path, target, allowed_root, and expected_before through isinstance, ValueError, TypeError, get, AtomicWriteError, and _windows_atomic_unlink_exact_symlink with 4 guarded checks, and 3 typed refusals.

    Rationale
    ---------
    Because Unlink one confined symlink only after exact lexical revalidation. Keep isinstance, ValueError, TypeError, get, AtomicWriteError, and _windows_atomic_unlink_exact_symlink inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._posix_atomic_unlink_exact_symlink:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Unlink one confined symlink only after exact lexical revalidation."
    ._windows_atomic_unlink_exact_symlink:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Unlink one confined symlink only after exact lexical revalidation."

    InstantiationsFromRepo
    ----------------------
    .AtomicWriteError:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Unlink one confined symlink only after exact lexical revalidation."
    """

    if not isinstance(target, str) or not target:
        raise ValueError("target must be a nonempty string")
    if not isinstance(expected_before, Mapping):
        raise TypeError("expected_before must be a mapping")
    if expected_before.get("kind") != "symlink":
        raise AtomicWriteError(
            "expected_before kind is ineligible for exact symlink unlink"
        )
    if os.name == "nt":
        _windows_atomic_unlink_exact_symlink(
            path,
            target,
            allowed_root=allowed_root,
            expected_before=expected_before,
        )
        return
    _posix_atomic_unlink_exact_symlink(
        path,
        target,
        allowed_root=allowed_root,
        expected_before=expected_before,
    )


def atomic_create_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> bool:
    """Securely create a file, with only an explicit non-atomic fallback.

    Intent
    ------
    Securely create a file, with only an explicit non-atomic fallback. The boundary coordinates path, data, allowed_root, mode, and allow_non_atomic through _windows_atomic_create_bytes, _posix_atomic_create_bytes, _is_capability_error, _fallback_write, Path, and bytes with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Securely create a file, with only an explicit non-atomic fallback. Keep _windows_atomic_create_bytes, _posix_atomic_create_bytes, _is_capability_error, _fallback_write, Path, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._is_capability_error:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Securely create a file, with only an explicit non-atomic fallback."

    InstantiationsFromRepo
    ----------------------
    ._fallback_write:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Securely create a file, with only an explicit non-atomic fallback."
    ._posix_atomic_create_bytes:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Securely create a file, with only an explicit non-atomic fallback."
    ._windows_atomic_create_bytes:
      why:
        constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Securely create a file, with only an explicit non-atomic fallback."
    """

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
    """Create a file and retain native cleanup authority for that exact identity.

    Intent
    ------
    Create a file and retain native cleanup authority for that exact identity. The boundary coordinates path, data, allowed_root, and mode through _windows_atomic_create_bytes_tracked, _posix_atomic_create_bytes_tracked, Path, bytes, int, and os with 1 guarded checks.

    Rationale
    ---------
    Because Create a file and retain native cleanup authority for that exact identity. Keep _windows_atomic_create_bytes_tracked, _posix_atomic_create_bytes_tracked, Path, bytes, int, and os inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._posix_atomic_create_bytes_tracked:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Create a file and retain native cleanup authority for that exact identity."
    ._windows_atomic_create_bytes_tracked:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Create a file and retain native cleanup authority for that exact identity."
    """

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

    Intent
    ------
    Retain platform-scoped recovery authority over one expected file. The boundary coordinates path, expected_bytes, quarantine_id, and allowed_root through isinstance, TypeError, _quarantine_name, _windows_track_existing_regular_file, _posix_track_existing_regular_file, and Path with 2 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Retain platform-scoped recovery authority over one expected file. Keep isinstance, TypeError, _quarantine_name, _windows_track_existing_regular_file, _posix_track_existing_regular_file, and Path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._quarantine_name:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Retain platform-scoped recovery authority over one expected file."

    InstantiationsFromRepo
    ----------------------
    ._posix_track_existing_regular_file:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Retain platform-scoped recovery authority over one expected file."
    ._windows_track_existing_regular_file:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Retain platform-scoped recovery authority over one expected file."
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
    """Read one directory entirely through retained no-follow native handles.

    Intent
    ------
    Read one directory entirely through retained no-follow native handles. The boundary coordinates root through _windows_read_regular_directory_entries, _posix_read_regular_directory_entries, Path, os, root, and tuple with 1 guarded checks.

    Rationale
    ---------
    Because Read one directory entirely through retained no-follow native handles. Keep _windows_read_regular_directory_entries, _posix_read_regular_directory_entries, Path, os, root, and tuple inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._posix_read_regular_directory_entries:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Read one directory entirely through retained no-follow native handles."
    ._windows_read_regular_directory_entries:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Read one directory entirely through retained no-follow native handles."
    """

    if os.name == "nt":
        return _windows_read_regular_directory_entries(root)
    return _posix_read_regular_directory_entries(root)


def read_bounded_directory_names(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[str, ...]:
    """Enumerate only bounded child names through retained native authority.

    Intent
    ------
    Enumerate only bounded child names through retained native authority. The boundary coordinates root, max_entries, and max_name_bytes through _validate_directory_name_bounds, _windows_read_bounded_directory_names, _posix_read_bounded_directory_names, Path, int, and max_entries with 1 guarded checks.

    Rationale
    ---------
    Because Enumerate only bounded child names through retained native authority. Keep _validate_directory_name_bounds, _windows_read_bounded_directory_names, _posix_read_bounded_directory_names, Path, int, and max_entries inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_directory_name_bounds:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Enumerate only bounded child names through retained native authority."

    InstantiationsFromRepo
    ----------------------
    ._posix_read_bounded_directory_names:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Enumerate only bounded child names through retained native authority."
    ._windows_read_bounded_directory_names:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Enumerate only bounded child names through retained native authority."
    """

    _validate_directory_name_bounds(max_entries, max_name_bytes)
    if os.name == "nt":
        return _windows_read_bounded_directory_names(
            root,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )
    return _posix_read_bounded_directory_names(
        root,
        max_entries=max_entries,
        max_name_bytes=max_name_bytes,
    )


def retain_bounded_directory_inventory(
    root: Path,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> RetainedBoundedDirectoryInventory:
    """Retain one bounded, revalidatable native directory inventory.

    Intent
    ------
    Retain one bounded, revalidatable native directory inventory. The boundary coordinates root, max_entries, and max_name_bytes through _validate_directory_name_bounds, _windows_retain_bounded_directory_inventory, _posix_retain_bounded_directory_inventory, Path, int, and max_entries with 1 guarded checks.

    Rationale
    ---------
    Because Retain one bounded, revalidatable native directory inventory. Keep _validate_directory_name_bounds, _windows_retain_bounded_directory_inventory, _posix_retain_bounded_directory_inventory, Path, int, and max_entries inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._validate_directory_name_bounds:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Retain one bounded, revalidatable native directory inventory."

    InstantiationsFromRepo
    ----------------------
    ._posix_retain_bounded_directory_inventory:
      why:
        constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Retain one bounded, revalidatable native directory inventory."
    ._windows_retain_bounded_directory_inventory:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Retain one bounded, revalidatable native directory inventory."
    """

    _validate_directory_name_bounds(max_entries, max_name_bytes)
    if os.name == "nt":
        return _windows_retain_bounded_directory_inventory(
            root,
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
        )
    return _posix_retain_bounded_directory_inventory(
        root,
        max_entries=max_entries,
        max_name_bytes=max_name_bytes,
    )


def atomic_append_bytes(
    path: Path,
    data: bytes,
    *,
    allowed_root: Path,
    mode: int,
    allow_non_atomic: bool = False,
) -> None:
    """Append one complete frame, never selecting non-atomic behavior silently.

    Intent
    ------
    Append one complete frame, never selecting non-atomic behavior silently. The boundary coordinates path, data, allowed_root, mode, and allow_non_atomic through _windows_atomic_append_bytes, _posix_atomic_append_bytes, _is_capability_error, _fallback_write, Path, and bytes with 2 guarded checks, 1 cleanup or failure regions, and 1 typed refusals.

    Rationale
    ---------
    Because Append one complete frame, never selecting non-atomic behavior silently. Keep _windows_atomic_append_bytes, _posix_atomic_append_bytes, _is_capability_error, _fallback_write, Path, and bytes inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._fallback_write:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Append one complete frame, never selecting non-atomic behavior silently."
    ._is_capability_error:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Append one complete frame, never selecting non-atomic behavior silently."
    ._posix_atomic_append_bytes:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Append one complete frame, never selecting non-atomic behavior silently."
    ._windows_atomic_append_bytes:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Append one complete frame, never selecting non-atomic behavior silently."
    """

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
    """Lock, compare complete predecessor bytes, append one frame, and reread.

    Intent
    ------
    Lock, compare complete predecessor bytes, append one frame, and reread. The boundary coordinates path, data, expected_previous_bytes, allowed_root, and mode through isinstance, TypeError, _windows_atomic_compare_and_append_bytes, _posix_atomic_append_bytes, _is_capability_error, and _fallback_compare_and_append with 3 guarded checks, 1 cleanup or failure regions, and 2 typed refusals.

    Rationale
    ---------
    Because Lock, compare complete predecessor bytes, append one frame, and reread. Keep isinstance, TypeError, _windows_atomic_compare_and_append_bytes, _posix_atomic_append_bytes, _is_capability_error, and _fallback_compare_and_append inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._fallback_compare_and_append:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Lock, compare complete predecessor bytes, append one frame, and reread."
    ._is_capability_error:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Lock, compare complete predecessor bytes, append one frame, and reread."
    ._posix_atomic_append_bytes:
      why:
        computes: "This computes edge is the third repository dependency used to uphold this guarantee: Lock, compare complete predecessor bytes, append one frame, and reread."
    ._windows_atomic_compare_and_append_bytes:
      why:
        computes: "This computes edge is the number 4 repository dependency used to uphold this guarantee: Lock, compare complete predecessor bytes, append one frame, and reread."
    """

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
