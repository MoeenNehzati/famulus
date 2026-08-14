from __future__ import annotations

import ctypes
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import officina.common.atomic_files as atomic_files
from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_append_bytes,
    atomic_compare_and_append_bytes,
    atomic_create_bytes,
    atomic_create_bytes_tracked,
    atomic_replace_bytes,
)


# famulus-skip: category=platform-contract; reason=these cases inject POSIX descriptor internals; alternate=the native Windows contract cases below exercise the corresponding Windows branches
_POSIX_DESCRIPTOR_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="POSIX descriptor implementation contract"
)
_QUARANTINE_ID = "1234567890abcdef1234567890abcdef"


def _quarantine_path(target: Path, quarantine_id: str = _QUARANTINE_ID) -> Path:
    return target.parent / f".famulus-quarantine-{quarantine_id}"


def _force_atomic_capability_error(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        def unavailable(*_args: object, **_kwargs: object) -> None:
            raise AtomicWriteError(atomic_files._CAPABILITY_ERROR)

        monkeypatch.setattr(atomic_files, "_windows_open_parent", unavailable)
        return
    monkeypatch.setattr(atomic_files.os, "supports_dir_fd", set())


class ParentSwapFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.allowed_root = tmp_path / "allowed"
        self.allowed_root.mkdir()
        self.parent = self.allowed_root / "parent"
        self.parent.mkdir()
        self.displaced_parent = self.allowed_root / "displaced-parent"
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.target = self.parent / "health.json"
        self.displaced_target = self.displaced_parent / "health.json"
        self.outside_target = self.outside / "health.json"

    def swap_after_parent_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = atomic_files._reject_unsafe_final
        swapped = False

        def swap(parent_fd: int, name: str) -> bool:
            nonlocal swapped
            result = original(parent_fd, name)
            if not swapped:
                self.parent.rename(self.displaced_parent)
                self.parent.symlink_to(self.outside, target_is_directory=True)
                swapped = True
            return result

        monkeypatch.setattr(atomic_files, "_reject_unsafe_final", swap)


def _temp_entries(parent: Path, name: str) -> list[Path]:
    return list(parent.glob(f".{name}.tmp-*"))


def _windows_native_acl_is_restrictive(path: Path, allowed_root: Path) -> bool:
    parents, parts = atomic_files._windows_open_parent(path, allowed_root)
    handle = -1
    try:
        handle, _information = atomic_files._windows_open_relative(
            parents[-1],
            parts[-1],
            access=0x80 | 0x00020000 | 0x00100000,
            share=0x1 | 0x2 | 0x4,
            disposition=1,
            options=0x20 | 0x40,
        )
        return atomic_files._windows_verify_handle_user_restrictive_acl(handle)
    finally:
        try:
            if handle >= 0:
                atomic_files._windows_close_handle(handle)
        finally:
            atomic_files._windows_close_chain(parents)


def test_windows_directory_enumeration_requests_list_directory_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model NtQueryDirectoryFile rejecting a root without FILE_LIST_DIRECTORY."""

    closed: list[int] = []

    def open_root(
        _root: Path,
        *,
        create: bool = False,
        final_access: int | None = None,
    ) -> int:
        assert not create
        if final_access is None or not final_access & 0x1:
            raise PermissionError("simulated STATUS_ACCESS_DENIED")
        return 47

    monkeypatch.setattr(atomic_files, "_windows_open_root", open_root)
    monkeypatch.setattr(
        atomic_files,
        "_windows_directory_entry_names",
        lambda _handle: (),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_handle",
        closed.append,
    )

    assert atomic_files._windows_read_regular_directory_entries(tmp_path) == ()
    assert closed == [47]


@_POSIX_DESCRIPTOR_ONLY
def test_directory_enumeration_rejects_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public-keys"
    root.mkdir()
    fifo = root / "unexpected.pub"
    os.mkfifo(fifo)
    outcomes: list[BaseException | tuple[object, ...]] = []

    def enumerate_root() -> None:
        try:
            outcomes.append(atomic_files.read_regular_directory_entries(root))
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=enumerate_root, daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    finished_promptly = not worker.is_alive()
    if not finished_promptly:
        # Unblock the deliberately vulnerable RED implementation so this
        # bounded regression cannot strand a reader thread in the test run.
        descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        os.close(descriptor)
        worker.join(timeout=1)

    assert finished_promptly, "FIFO classification blocked in open()"
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], AtomicWriteError)
    assert "not a regular file" in str(outcomes[0])


def test_existing_final_symlink_is_rejected(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("safe", encoding="utf-8")
    target = tmp_path / "health.json"
    target.symlink_to(victim)

    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert victim.read_text(encoding="utf-8") == "safe"
    assert target.is_symlink()


def test_create_rejects_existing_final_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_bytes(b"safe")
    target = tmp_path / "key"
    target.symlink_to(victim)

    with pytest.raises(AtomicWriteError):
        atomic_create_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert victim.read_bytes() == b"safe"


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_create_identity_verification_failure_removes_linked_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "key"
    other = tmp_path / "other"
    other.write_bytes(b"other")
    real_secure_stat = atomic_files._secure_stat
    injected = False

    def corrupt_link_identity(parent_fd: int, name: str) -> os.stat_result:
        nonlocal injected
        if name == target.name and target.exists() and not injected:
            injected = True
            return other.stat()
        return real_secure_stat(parent_fd, name)

    monkeypatch.setattr(atomic_files, "_secure_stat", corrupt_link_identity)

    with pytest.raises(AtomicWriteError) as captured:
        atomic_create_bytes_tracked(
            target,
            b"secret-free-public-key",
            allowed_root=tmp_path,
            mode=0o600,
        )

    assert str(captured.value) == "tracked file creation failed"
    assert injected
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_create_directory_fsync_failure_removes_linked_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "key"
    real_fsync = atomic_files.os.fsync
    injected = False
    directory_syncs = 0

    def fail_first_publish_directory_sync(descriptor: int) -> None:
        nonlocal injected, directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
            if target.exists() and not injected:
                injected = True
                raise OSError("directory sync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(atomic_files.os, "fsync", fail_first_publish_directory_sync)

    with pytest.raises(AtomicWriteError) as captured:
        atomic_create_bytes_tracked(
            target,
            b"secret-free-public-key",
            allowed_root=tmp_path,
            mode=0o600,
        )

    assert str(captured.value) == "tracked file creation failed"
    assert injected
    assert directory_syncs == 2
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_create_post_link_cleanup_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "key"
    replacement = b"replacement-public-key"
    real_fsync = atomic_files.os.fsync
    injected = False

    def replace_before_publish_directory_sync_fails(descriptor: int) -> None:
        nonlocal injected
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and target.exists() and not injected:
            injected = True
            target.unlink()
            target.write_bytes(replacement)
            raise OSError("directory sync failed after replacement")
        real_fsync(descriptor)

    monkeypatch.setattr(
        atomic_files.os,
        "fsync",
        replace_before_publish_directory_sync_fails,
    )

    with pytest.raises(AtomicWriteError) as captured:
        atomic_create_bytes_tracked(
            target,
            b"secret-free-public-key",
            allowed_root=tmp_path,
            mode=0o600,
        )

    assert str(captured.value) == "tracked file creation failed"
    assert injected
    assert target.read_bytes() == replacement
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_relocates_to_journaled_quarantine(
    tmp_path: Path,
) -> None:
    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    target.write_bytes(expected)

    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )

    assert tracked.location is atomic_files.TrackedFileLocation.CANONICAL
    tracked.relocate()
    tracked.relocate()
    assert tracked.location is atomic_files.TrackedFileLocation.QUARANTINE
    assert not target.exists()
    assert _quarantine_path(target).read_bytes() == expected
    tracked.release()


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_resumes_after_death_immediately_after_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProcessDeath(BaseException):
        pass

    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    target.write_bytes(expected)
    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    real_move = atomic_files._secure_rename_noreplace

    def die_after_move(parent_fd: int, source: str, destination: str) -> None:
        real_move(parent_fd, source, destination)
        raise ProcessDeath

    monkeypatch.setattr(
        atomic_files,
        "_secure_rename_noreplace",
        die_after_move,
    )
    with pytest.raises(ProcessDeath):
        tracked.relocate()

    assert not target.exists()
    assert _quarantine_path(target).read_bytes() == expected
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        _quarantine_path(target).name
    ]

    monkeypatch.setattr(atomic_files, "_secure_rename_noreplace", real_move)
    recovered = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    assert recovered.location is atomic_files.TrackedFileLocation.QUARANTINE
    recovered.relocate()
    recovered.dispose()

    assert list(tmp_path.iterdir()) == []


@_POSIX_DESCRIPTOR_ONLY
def test_relocation_fails_closed_when_canonical_reappears_after_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    quarantine = _quarantine_path(target)
    expected = b"exact-public-key"
    replacement = b"replacement-public-key"
    target.write_bytes(expected)
    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    real_move = atomic_files._secure_rename_noreplace

    def replace_after_move(parent_fd: int, source: str, destination: str) -> None:
        real_move(parent_fd, source, destination)
        target.write_bytes(replacement)

    monkeypatch.setattr(
        atomic_files,
        "_secure_rename_noreplace",
        replace_after_move,
    )

    with pytest.raises(AtomicWriteError, match="canonical and quarantine"):
        tracked.relocate()

    assert target.read_bytes() == replacement
    assert quarantine.read_bytes() == expected


@_POSIX_DESCRIPTOR_ONLY
def test_recovered_quarantine_revalidates_before_idempotent_relocation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "public-key.pub"
    quarantine = _quarantine_path(target)
    quarantine.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    quarantine.write_bytes(b"mutated-public!!")

    with pytest.raises(AtomicWriteError, match="changed before relocation"):
        tracked.relocate()

    assert quarantine.read_bytes() == b"mutated-public!!"
    with pytest.raises(AtomicWriteError, match="already closed"):
        tracked.dispose()


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_rejects_conflicting_or_third_quarantine(
    tmp_path: Path,
) -> None:
    target = tmp_path / "public-key.pub"
    quarantine = _quarantine_path(target)
    expected = b"exact-public-key"

    target.write_bytes(expected)
    quarantine.write_bytes(expected)
    with pytest.raises(AtomicWriteError, match="canonical and quarantine"):
        atomic_files.track_existing_regular_file(
            target,
            expected,
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    target.unlink()
    quarantine.write_bytes(b"third-state")
    with pytest.raises(AtomicWriteError, match="bytes do not match"):
        atomic_files.track_existing_regular_file(
            target,
            expected,
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_reports_explicit_disposed_absence(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="canonical or quarantine"):
        atomic_files.track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"exact-public-key",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_supports_complete_set_prevalidation(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.pub"
    second = tmp_path / "second.pub"
    first.write_bytes(b"first")
    second.write_bytes(b"mismatch")
    first_authority = atomic_files.track_existing_regular_file(
        first,
        b"first",
        quarantine_id="1" * 32,
        allowed_root=tmp_path,
    )

    with pytest.raises(AtomicWriteError, match="bytes do not match"):
        atomic_files.track_existing_regular_file(
            second,
            b"second",
            quarantine_id="2" * 32,
            allowed_root=tmp_path,
        )

    first_authority.release()
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"mismatch"
    assert not _quarantine_path(first, "1" * 32).exists()


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_dispose_requires_quarantine_and_is_terminal(
    tmp_path: Path,
) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )

    with pytest.raises(AtomicWriteError, match="must be quarantined"):
        tracked.dispose()

    assert target.read_bytes() == b"exact-public-key"
    with pytest.raises(AtomicWriteError, match="already closed"):
        tracked.relocate()


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_releases_without_removal(tmp_path: Path) -> None:
    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    target.write_bytes(expected)

    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.release()
    tracked.release()

    assert target.read_bytes() == expected
    with pytest.raises(AtomicWriteError, match="already closed"):
        tracked.relocate()


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_relocates_and_disposes_under_precondition(
    tmp_path: Path,
) -> None:
    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    target.write_bytes(expected)

    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    assert tracked.identity == atomic_files.ConfinedFileIdentity(
        platform="posix",
        volume=target.stat().st_dev,
        file_id=target.stat().st_ino,
    )
    tracked.relocate()
    tracked.dispose()

    assert not target.exists()
    with pytest.raises(AtomicWriteError, match="already closed"):
        tracked.dispose()
    tracked.release()


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_rejects_absent_mismatch_symlink_and_special(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.pub"
    with pytest.raises(FileNotFoundError, match="canonical or quarantine"):
        atomic_files.track_existing_regular_file(
            missing,
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    regular = tmp_path / "regular.pub"
    regular.write_bytes(b"different")
    with pytest.raises(AtomicWriteError, match="bytes do not match"):
        atomic_files.track_existing_regular_file(
            regular,
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    symlink = tmp_path / "symlink.pub"
    symlink.symlink_to(regular)
    with pytest.raises(AtomicWriteError, match="symbolic link"):
        atomic_files.track_existing_regular_file(
            symlink,
            b"different",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    fifo = tmp_path / "special.pub"
    os.mkfifo(fifo)
    with pytest.raises(AtomicWriteError, match="not a regular file"):
        atomic_files.track_existing_regular_file(
            fifo,
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_preserves_final_replacement(tmp_path: Path) -> None:
    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    replacement = b"replacement-public-key"
    target.write_bytes(expected)
    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )

    target.unlink()
    target.write_bytes(replacement)

    with pytest.raises(AtomicWriteError, match="changed before relocation"):
        tracked.relocate()
    assert target.read_bytes() == replacement


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_preserves_canonical_entry_before_quarantine_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    replacement = b"replacement-public-key"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.relocate()
    real_unlink = atomic_files._secure_unlink
    injected = False

    def replace_at_disposal(parent_fd: int, name: str) -> None:
        nonlocal injected
        if not injected:
            injected = True
            if target.exists():
                target.unlink()
            target.write_bytes(replacement)
        real_unlink(parent_fd, name)

    monkeypatch.setattr(atomic_files, "_secure_unlink", replace_at_disposal)

    tracked.dispose()

    assert injected
    assert target.read_bytes() == replacement


@_POSIX_DESCRIPTOR_ONLY
def test_relocation_race_restores_canonical_replacement_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    replacement = b"replacement-public-key"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    move = getattr(atomic_files, "_secure_rename_noreplace", None)
    assert move is not None, "no no-overwrite quarantine move exists"
    injected = False

    def replace_before_move(parent_fd: int, source: str, destination: str) -> None:
        nonlocal injected
        if not injected:
            injected = True
            target.unlink()
            target.write_bytes(replacement)
        move(parent_fd, source, destination)

    monkeypatch.setattr(
        atomic_files,
        "_secure_rename_noreplace",
        replace_before_move,
    )

    with pytest.raises(AtomicWriteError, match="changed during relocation"):
        tracked.relocate()

    assert injected
    assert target.read_bytes() == replacement


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_retains_known_quarantine_when_disposal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    expected = b"exact-public-key"
    target.write_bytes(expected)
    tracked = atomic_files.track_existing_regular_file(
        target,
        expected,
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.relocate()

    monkeypatch.setattr(
        atomic_files,
        "_secure_unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("injected unlink failure")),
    )

    with pytest.raises(OSError, match="injected unlink failure"):
        tracked.dispose()

    assert not target.exists()
    assert _quarantine_path(target).read_bytes() == expected
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        _quarantine_path(target).name
    ]


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_rejects_same_inode_byte_mutation(tmp_path: Path) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    identity = target.stat().st_ino
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    target.write_bytes(b"mutated-public!!")
    assert target.stat().st_ino == identity

    with pytest.raises(AtomicWriteError, match="changed before relocation"):
        tracked.relocate()

    assert target.read_bytes() == b"mutated-public!!"


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_bounds_mismatch_read_to_expected_length_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "oversized.pub"
    target.write_bytes(b"x" * (1024 * 1024))
    real_read = atomic_files.os.read
    requested: list[int] = []

    def bounded_read(descriptor: int, count: int) -> bytes:
        requested.append(count)
        return real_read(descriptor, count)

    monkeypatch.setattr(atomic_files.os, "read", bounded_read)

    with pytest.raises(AtomicWriteError, match="bytes do not match"):
        atomic_files.track_existing_regular_file(
            target,
            b"x",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert requested
    assert max(requested) <= 2


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_rejects_disappearance_before_remove(tmp_path: Path) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    target.unlink()

    with pytest.raises(AtomicWriteError, match="changed before relocation"):
        tracked.relocate()
    assert not target.exists()


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_parent_replacement_cannot_redirect_cleanup(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    parent = allowed / "keys"
    parent.mkdir(parents=True)
    target = parent / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=allowed,
    )
    displaced = allowed / "displaced"
    parent.rename(displaced)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.write_bytes(b"outside")
    parent.symlink_to(outside, target_is_directory=True)

    tracked.relocate()
    tracked.dispose()

    assert not (displaced / target.name).exists()
    assert outside_target.read_bytes() == b"outside"


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_closes_partial_setup_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    real_close = atomic_files.os.close
    closed: list[int] = []
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(atomic_files.os, "close", close)
    monkeypatch.setattr(
        atomic_files,
        "_open_parent",
        lambda _path, _root: (parent_fd, target.name),
    )
    monkeypatch.setattr(
        atomic_files,
        "_read_descriptor_bytes_bounded",
        lambda _descriptor, _limit: (_ for _ in ()).throw(
            OSError("injected read failure")
        ),
    )

    with pytest.raises(OSError, match="injected read failure"):
        atomic_files.track_existing_regular_file(
            target,
            b"exact-public-key",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert len(closed) == 2


@_POSIX_DESCRIPTOR_ONLY
def test_track_existing_regular_file_detects_final_file_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    real_read = atomic_files._read_descriptor_bytes_bounded

    def replace_after_read(descriptor: int, limit: int) -> bytes:
        data = real_read(descriptor, limit)
        target.unlink()
        target.write_bytes(b"replacement")
        return data

    monkeypatch.setattr(
        atomic_files,
        "_read_descriptor_bytes_bounded",
        replace_after_read,
    )

    with pytest.raises(AtomicWriteError, match="changed during observation"):
        atomic_files.track_existing_regular_file(
            target,
            b"exact-public-key",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )
    assert target.read_bytes() == b"replacement"


@_POSIX_DESCRIPTOR_ONLY
def test_tracked_existing_file_fsync_failure_is_closed_and_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "public-key.pub"
    target.write_bytes(b"exact-public-key")
    tracked = atomic_files.track_existing_regular_file(
        target,
        b"exact-public-key",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    real_fsync = atomic_files.os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(atomic_files.os, "fsync", fail_directory_sync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        tracked.relocate()
    assert not target.exists()
    assert _quarantine_path(target).read_bytes() == b"exact-public-key"
    with pytest.raises(AtomicWriteError, match="already closed"):
        tracked.relocate()


def test_windows_track_existing_regular_file_retains_and_releases_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    closed: list[list[int]] = []
    closed_single: list[int] = []
    requested_access: list[int] = []
    requested_share: list[int] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10, 11], ("keys", "public-key.pub")),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    def open_validated(*_args: object, **kwargs: object) -> tuple[int, object]:
        requested_access.append(kwargs["access"])  # type: ignore[arg-type]
        requested_share.append(kwargs["share"])  # type: ignore[arg-type]
        if _args[1] == _quarantine_path(Path("public-key.pub")).name:
            raise FileNotFoundError
        return 20, object()

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    bounded_reads: list[int] = []

    def read_bounded(_handle: int, maximum_bytes: int) -> bytes:
        bounded_reads.append(maximum_bytes)
        return b"expected"

    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        read_bounded,
        raising=False,
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle",
        lambda _handle: pytest.fail("unbounded native read was used"),
    )
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", closed_single.append)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "keys" / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.release()

    assert tracked.identity == identity
    assert tracked.location is atomic_files.TrackedFileLocation.CANONICAL
    assert requested_access == [
        atomic_files._WIN_READ_ACCESS | 0x00010000 | 0x40000000
    ] * 3
    assert requested_share == [atomic_files._WIN_SHARE_ALL & ~0x2] * 3
    assert bounded_reads == [9]
    assert closed_single == [20]
    assert closed == [[10, 11]]


def test_windows_track_existing_regular_file_rejects_initial_name_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarantine = _quarantine_path(Path("public-key.pub")).name
    closed: list[list[int]] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda _parent, candidate, **_kwargs: (
            (20, object()) if candidate == "public-key.pub" else (21, object())
        ),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_lock_handle",
        lambda _handle: pytest.fail("conflicting names must be rejected before locking"),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda *_: pytest.fail("conflicting names must be rejected before reading"),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed.append(handles),
    )

    with pytest.raises(AtomicWriteError, match="canonical and quarantine"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert quarantine != "public-key.pub"
    assert closed == [[20, 21], [10]]


def test_windows_track_existing_regular_file_reports_initial_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[list[int]] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed.append(handles),
    )

    with pytest.raises(FileNotFoundError, match="no canonical or quarantine"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert closed == [[10]]


def test_windows_track_existing_regular_file_rejects_initial_quarantine_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarantine = _quarantine_path(Path("public-key.pub")).name
    closed: list[list[int]] = []
    unlocks: list[int] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda _parent, candidate, **_kwargs: (
            (20, object())
            if candidate == quarantine
            else (_ for _ in ()).throw(FileNotFoundError())
        ),
    )
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(
        atomic_files,
        "_windows_unlock_handle",
        lambda handle, _lock: unlocks.append(handle),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_confined_identity",
        lambda _handle: atomic_files.ConfinedFileIdentity(
            platform="windows",
            volume=7,
            file_id=b"i" * 16,
        ),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: b"wrong",
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed.append(handles),
    )

    with pytest.raises(AtomicWriteError, match="bytes do not match"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert unlocks == [20]
    assert closed == [[10, 20]]


@pytest.mark.parametrize("selected", ["canonical", "quarantine"])
def test_windows_track_existing_regular_file_rechecks_initial_name_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
) -> None:
    canonical = "public-key.pub"
    quarantine = _quarantine_path(Path(canonical)).name
    calls = {canonical: 0, quarantine: 0}
    closed_single: list[int] = []
    closed: list[list[int]] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], (canonical,)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)

    def open_validated(_parent: int, candidate: str, **_kwargs: object):
        calls[candidate] += 1
        is_selected = candidate == (canonical if selected == "canonical" else quarantine)
        if is_selected:
            return 20, object()
        if calls[candidate] == 1:
            raise FileNotFoundError
        return 21, object()

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_confined_identity",
        lambda _handle: atomic_files.ConfinedFileIdentity(
            platform="windows",
            volume=7,
            file_id=b"i" * 16,
        ),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: b"expected",
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", closed_single.append)
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed.append(handles),
    )

    with pytest.raises(AtomicWriteError, match="canonical and quarantine"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / canonical,
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    other = quarantine if selected == "canonical" else canonical
    assert calls[other] == 2
    assert closed_single == [21]
    assert closed == [[10, 20]]


def test_windows_tracked_existing_file_uses_flush_then_close_and_absence_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    named_verifications = 0
    deleted: list[int] = []
    closed: list[list[int]] = []
    closed_single: list[int] = []
    flushed: list[int] = []
    events: list[str] = []
    location = "canonical"
    delete_pending = False

    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda _parent, candidate, **_kwargs: (
            (20, object())
            if candidate == "public-key.pub" and location == "canonical"
            else (_ for _ in ()).throw(FileNotFoundError())
        ),
    )
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)
    def read_bounded(_handle: int, _limit: int) -> bytes:
        events.append("read")
        return b"expected"

    monkeypatch.setattr(atomic_files, "_windows_read_handle_bounded", read_bounded)
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(
        atomic_files,
        "_windows_unlock_handle",
        lambda *_: events.append("unlock"),
    )

    def verify_named(*_args: object) -> None:
        nonlocal named_verifications
        named_verifications += 1
        events.append("verify")

    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", verify_named)
    def rename_handle(
        _handle: int,
        _parent: int,
        destination: str,
        *,
        replace: bool,
    ) -> bool:
        nonlocal location
        assert destination == _quarantine_path(Path("public-key.pub")).name
        assert not replace
        events.append("rename")
        location = "quarantine"
        return True

    monkeypatch.setattr(atomic_files, "_windows_rename_handle", rename_handle)
    def mark_delete(handle: int) -> None:
        nonlocal delete_pending
        events.append("mark-delete")
        delete_pending = True
        deleted.append(handle)

    def close_handle(handle: int) -> None:
        nonlocal location
        events.append("close-file")
        closed_single.append(handle)
        if delete_pending:
            location = "absent"

    def flush_handle(handle: int) -> None:
        events.append("flush-file")
        flushed.append(handle)

    monkeypatch.setattr(atomic_files, "_windows_mark_delete", mark_delete)
    monkeypatch.setattr(atomic_files, "_windows_flush_handle", flush_handle)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", close_handle)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.relocate()
    rename_index = events.index("rename")
    flush_index = events.index("flush-file")
    post_rename_read_index = events.index("read", flush_index + 1)
    post_rename_verify_index = events.index("verify", post_rename_read_index + 1)
    assert (
        rename_index
        < flush_index
        < post_rename_read_index
        < post_rename_verify_index
    )

    tracked.dispose()

    assert named_verifications == 4
    assert deleted == [20]
    assert flushed == [20]
    assert closed_single == [20]
    assert closed == [[10]]
    assert events.index("mark-delete") < events.index("close-file")
    assert events.index("unlock") < events.index("close-file")


def test_windows_dispose_rejects_quarantine_remaining_after_handle_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    quarantine = _quarantine_path(Path("public-key.pub")).name
    retained_closed = False
    events: list[str] = []
    closed_single: list[int] = []
    closed: list[list[int]] = []

    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)

    def open_validated(_parent: int, candidate: str, **_kwargs: object):
        if candidate != quarantine:
            raise FileNotFoundError
        if retained_closed:
            events.append("post-close-probe")
            return 21, object()
        return 20, object()

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: b"expected",
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(
        atomic_files,
        "_windows_unlock_handle",
        lambda *_: events.append("unlock"),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_mark_delete",
        lambda _handle: events.append("mark-delete"),
    )

    def close_handle(handle: int) -> None:
        nonlocal retained_closed
        closed_single.append(handle)
        if handle == 20:
            events.append("close-retained")
            retained_closed = True
        else:
            events.append("close-probe")

    monkeypatch.setattr(atomic_files, "_windows_close_handle", close_handle)
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed.append(handles),
    )

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.relocate()
    with pytest.raises(AtomicWriteError) as error:
        tracked.dispose()

    assert str(error.value) == "tracked quarantine remained after disposal"
    assert events.index("unlock") < events.index("close-retained")
    assert events.index("close-retained") < events.index("post-close-probe")
    assert closed_single == [20, 21]
    assert closed == [[10]]
    tracked.release()


def test_windows_tracked_existing_file_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    deleted: list[int] = []
    closed: list[list[int]] = []
    closed_single: list[int] = []
    location = "canonical"
    canonical_replacement = False

    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    def open_validated(_parent: int, candidate: str, **_kwargs: object):
        if candidate == "public-key.pub":
            if location == "canonical":
                return 20, object()
            if canonical_replacement:
                return 21, object()
        if (
            candidate == _quarantine_path(Path("public-key.pub")).name
            and location == "quarantine"
        ):
            return 20, object()
        raise FileNotFoundError

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: b"expected",
    )
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)

    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    def rename_handle(*_args: object, **_kwargs: object) -> bool:
        nonlocal location
        location = "quarantine"
        return True

    monkeypatch.setattr(atomic_files, "_windows_rename_handle", rename_handle)
    monkeypatch.setattr(atomic_files, "_windows_flush_handle", lambda _handle: None)
    monkeypatch.setattr(atomic_files, "_windows_mark_delete", deleted.append)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", closed_single.append)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    tracked.relocate()
    canonical_replacement = True
    with pytest.raises(AtomicWriteError, match="canonical and quarantine"):
        tracked.dispose()

    assert deleted == []
    assert closed_single == [21, 20]
    assert closed == [[10]]


def test_windows_tracked_existing_file_rejects_expected_byte_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    reads = iter((b"expected", b"mutated!"))
    limits: list[int] = []
    deleted: list[int] = []
    closed: list[list[int]] = []
    closed_single: list[int] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda _parent, candidate, **_kwargs: (
            (20, object())
            if candidate == "public-key.pub"
            else (_ for _ in ()).throw(FileNotFoundError())
        ),
    )
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)

    def read_bounded(_handle: int, maximum_bytes: int) -> bytes:
        limits.append(maximum_bytes)
        return next(reads)

    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        read_bounded,
        raising=False,
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle",
        lambda _handle: pytest.fail("unbounded native read was used"),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_mark_delete", deleted.append)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", closed_single.append)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    with pytest.raises(AtomicWriteError, match="bytes changed"):
        tracked.relocate()

    assert limits == [9, 9]
    assert deleted == []
    assert closed_single == [20]
    assert closed == [[10]]


def test_windows_tracked_existing_file_resumes_from_known_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = atomic_files.ConfinedFileIdentity(
        platform="windows",
        volume=7,
        file_id=b"i" * 16,
    )
    quarantine = _quarantine_path(Path("public-key.pub")).name
    deleted: list[int] = []
    renamed: list[str] = []
    flushed: list[int] = []
    closed: list[list[int]] = []
    closed_single: list[int] = []
    quarantine_present = True
    delete_pending = False
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    def open_validated(_parent: int, candidate: str, **_kwargs: object):
        if candidate == quarantine and quarantine_present:
            return 20, object()
        raise FileNotFoundError

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    monkeypatch.setattr(atomic_files, "_windows_confined_identity", lambda _handle: identity)
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: b"expected",
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_named_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_rename_handle",
        lambda _handle, _parent, name, **_kwargs: renamed.append(name) or True,
    )
    def mark_delete(handle: int) -> None:
        nonlocal delete_pending
        delete_pending = True
        deleted.append(handle)

    def close_handle(handle: int) -> None:
        nonlocal quarantine_present
        closed_single.append(handle)
        if handle == 20 and delete_pending:
            quarantine_present = False

    monkeypatch.setattr(atomic_files, "_windows_mark_delete", mark_delete)
    monkeypatch.setattr(atomic_files, "_windows_flush_handle", flushed.append)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", close_handle)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    tracked = atomic_files._windows_track_existing_regular_file(
        tmp_path / "public-key.pub",
        b"expected",
        quarantine_id=_QUARANTINE_ID,
        allowed_root=tmp_path,
    )
    assert tracked.location is atomic_files.TrackedFileLocation.QUARANTINE
    tracked.relocate()
    tracked.dispose()
    tracked.release()

    assert renamed == []
    assert deleted == [20]
    assert flushed == []
    assert closed_single == [20]
    assert closed == [[10]]


def test_windows_bounded_handle_read_never_requests_past_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    class Kernel32:
        def ReadFile(
            self,
            _handle: object,
            buffer: object,
            size: int,
            count: object,
            _overlapped: object,
        ) -> int:
            requested.append(size)
            ctypes.memmove(buffer, b"xy", size)
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
            return 1

    monkeypatch.setattr(
        atomic_files,
        "_windows_modules",
        lambda: (Kernel32(), object(), object()),
    )
    monkeypatch.setattr(atomic_files, "_windows_seek", lambda *_args: 0)

    assert atomic_files._windows_read_handle_bounded(20, 2) == b"xy"
    assert requested == [2]


def test_windows_track_existing_regular_file_rejects_junction_and_closes_partial_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: (_ for _ in ()).throw(
            AtomicWriteError("destination parent is a reparse point")
        ),
    )

    with pytest.raises(AtomicWriteError, match="reparse point"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "junction" / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    closed: list[list[int]] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_validated",
        lambda _parent, candidate, **_kwargs: (
            (20, object())
            if candidate == "public-key.pub"
            else (_ for _ in ()).throw(FileNotFoundError())
        ),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_confined_identity",
        lambda _handle: atomic_files.ConfinedFileIdentity(
            platform="windows",
            volume=7,
            file_id=b"i" * 16,
        ),
    )
    monkeypatch.setattr(
        atomic_files,
        "_windows_read_handle_bounded",
        lambda _handle, _limit: (_ for _ in ()).throw(
            OSError("injected read failure")
        ),
    )
    monkeypatch.setattr(atomic_files, "_windows_lock_handle", lambda _handle: object())
    monkeypatch.setattr(atomic_files, "_windows_unlock_handle", lambda *_: None)
    monkeypatch.setattr(atomic_files, "_windows_close_chain", lambda handles: closed.append(handles))

    with pytest.raises(OSError, match="injected read failure"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )
    assert closed == [[10, 20]]


def test_windows_track_existing_regular_file_closes_canonical_probe_on_quarantine_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarantine = _quarantine_path(Path("public-key.pub")).name
    closed_single: list[int] = []
    closed_chains: list[list[int]] = []
    monkeypatch.setattr(
        atomic_files,
        "_windows_open_parent",
        lambda _path, _root: ([10], ("public-key.pub",)),
    )
    monkeypatch.setattr(atomic_files, "_windows_verify_parent_chain", lambda *_: None)

    def open_validated(_parent: int, candidate: str, **_kwargs: object):
        if candidate == "public-key.pub":
            return 20, object()
        assert candidate == quarantine
        raise AtomicWriteError("injected quarantine probe failure")

    monkeypatch.setattr(atomic_files, "_windows_open_validated", open_validated)
    monkeypatch.setattr(atomic_files, "_windows_close_handle", closed_single.append)
    monkeypatch.setattr(
        atomic_files,
        "_windows_close_chain",
        lambda handles: closed_chains.append(handles),
    )

    with pytest.raises(AtomicWriteError, match="injected quarantine probe failure"):
        atomic_files._windows_track_existing_regular_file(
            tmp_path / "public-key.pub",
            b"expected",
            quarantine_id=_QUARANTINE_ID,
            allowed_root=tmp_path,
        )

    assert closed_single == [20]
    assert closed_chains == [[10]]


@_POSIX_DESCRIPTOR_ONLY
def test_parent_swap_cannot_redirect_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = ParentSwapFixture(tmp_path)
    fixture.swap_after_parent_open(monkeypatch)

    atomic_replace_bytes(
        fixture.target, b"new", allowed_root=fixture.allowed_root, mode=0o600
    )

    assert not fixture.outside_target.exists()
    assert fixture.displaced_target.read_bytes() == b"new"


def test_intermediate_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    (allowed_root / "parent").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(
            allowed_root / "parent" / "health.json",
            b"new",
            allowed_root=allowed_root,
            mode=0o600,
        )

    assert not (outside / "health.json").exists()


def test_destination_outside_allowed_root_is_rejected(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.json"

    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(outside, b"new", allowed_root=allowed_root, mode=0o600)

    assert not outside.exists()


# famulus-skip: category=platform-contract; reason=FIFO creation is unavailable on some hosts; alternate=directory and symlink destination tests cover non-regular targets
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are unavailable")
def test_existing_fifo_is_rejected_without_opening_it(tmp_path: Path) -> None:
    target = tmp_path / "health.json"
    os.mkfifo(target)

    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert stat.S_ISFIFO(target.lstat().st_mode)


def test_existing_directory_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "health.json"
    target.mkdir()

    with pytest.raises(AtomicWriteError):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert target.is_dir()


@_POSIX_DESCRIPTOR_ONLY
def test_interrupted_replace_preserves_previous_complete_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    target.write_bytes(b"old")

    def interrupt(*args: object, **kwargs: object) -> None:
        raise OSError("injected interruption")

    monkeypatch.setattr(atomic_files.os, "replace", interrupt)

    with pytest.raises(OSError, match="injected interruption"):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert target.read_bytes() == b"old"
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_interrupted_create_leaves_no_destination_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "key"

    def interrupt(*args: object, **kwargs: object) -> None:
        raise OSError("injected interruption")

    monkeypatch.setattr(atomic_files.os, "link", interrupt)

    with pytest.raises(OSError, match="injected interruption"):
        atomic_create_bytes(target, b"candidate", allowed_root=tmp_path, mode=0o600)

    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_mode_failure_cleans_up_exclusively_created_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"

    def fail_mode(*args: object, **kwargs: object) -> None:
        raise OSError("injected mode failure")

    monkeypatch.setattr(atomic_files.os, "fchmod", fail_mode)

    with pytest.raises(OSError, match="injected mode failure"):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_fdopen_failure_is_preserved_when_raw_descriptor_close_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    real_close = atomic_files.os.close
    temp_descriptor: int | None = None
    temp_descriptor_closed = False

    def fail_fdopen(descriptor: int, *args: object, **kwargs: object):
        nonlocal temp_descriptor
        temp_descriptor = descriptor
        raise OSError("primary fdopen failure")

    def close_then_fail(descriptor: int) -> None:
        nonlocal temp_descriptor_closed
        real_close(descriptor)
        if descriptor == temp_descriptor:
            temp_descriptor_closed = True
            raise OSError("cleanup raw close failure")

    monkeypatch.setattr(atomic_files.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(atomic_files.os, "close", close_then_fail)

    with pytest.raises(OSError, match="primary fdopen failure"):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert temp_descriptor_closed
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_write_failure_is_preserved_when_handle_close_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    real_fdopen = atomic_files.os.fdopen
    handle_closed = False

    class FailingHandle:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def write(self, data: bytes) -> None:
            raise OSError("primary write failure")

        def flush(self) -> None:
            self._handle.flush()

        def fileno(self) -> int:
            return self._handle.fileno()

        def close(self) -> None:
            nonlocal handle_closed
            self._handle.close()
            handle_closed = True
            raise OSError("cleanup handle close failure")

    def failing_handle(descriptor: int, *args: object, **kwargs: object):
        return FailingHandle(real_fdopen(descriptor, *args, **kwargs))

    monkeypatch.setattr(atomic_files.os, "fdopen", failing_handle)

    with pytest.raises(OSError, match="primary write failure"):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert handle_closed
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


def test_atomic_create_never_replaces_existing_destination(tmp_path: Path) -> None:
    target = tmp_path / "key"
    target.write_bytes(b"winner")

    created = atomic_create_bytes(target, b"loser", allowed_root=tmp_path, mode=0o600)

    assert created is False
    assert target.read_bytes() == b"winner"
    assert _temp_entries(tmp_path, target.name) == []


def test_concurrent_atomic_create_has_exactly_one_winner(tmp_path: Path) -> None:
    target = tmp_path / "key"
    payloads = [f"candidate-{index}".encode("ascii") for index in range(12)]

    def create(payload: bytes) -> bool:
        return atomic_create_bytes(target, payload, allowed_root=tmp_path, mode=0o600)

    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        results = list(executor.map(create, payloads))

    assert results.count(True) == 1
    assert results.count(False) == len(payloads) - 1
    assert target.read_bytes() in payloads
    assert _temp_entries(tmp_path, target.name) == []


# famulus-skip: category=platform-contract; reason=exact POSIX mode bits do not model Windows ACLs; alternate=atomic content and no-follow tests cover cross-platform write semantics
@pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics")
@pytest.mark.parametrize("operation", [atomic_replace_bytes, atomic_create_bytes])
def test_created_destination_has_exact_requested_mode(
    tmp_path: Path, operation: object
) -> None:
    target = tmp_path / "destination"

    result = operation(target, b"data", allowed_root=tmp_path, mode=0o640)

    assert result in {None, True}
    assert target.stat().st_mode & 0o777 == 0o640


@_POSIX_DESCRIPTOR_ONLY
def test_replace_fsync_order_is_file_replace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    events: list[str] = []
    real_fsync = atomic_files.os.fsync
    real_replace = atomic_files.os.replace

    def record_fsync(fd: int) -> None:
        events.append("directory-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-fsync")
        real_fsync(fd)

    def record_replace(*args: object, **kwargs: object) -> None:
        events.append("replace")
        real_replace(*args, **kwargs)

    monkeypatch.setattr(atomic_files.os, "fsync", record_fsync)
    monkeypatch.setattr(atomic_files.os, "replace", record_replace)

    atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert events == ["file-fsync", "replace", "directory-fsync"]


@_POSIX_DESCRIPTOR_ONLY
def test_create_fsyncs_directory_after_link_and_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "key"
    events: list[str] = []
    real_fsync = atomic_files.os.fsync
    real_link = atomic_files.os.link
    real_unlink = atomic_files.os.unlink

    def record_fsync(fd: int) -> None:
        events.append("directory-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-fsync")
        real_fsync(fd)

    def record_link(*args: object, **kwargs: object) -> None:
        events.append("link")
        real_link(*args, **kwargs)

    def record_unlink(*args: object, **kwargs: object) -> None:
        events.append("unlink-temp")
        real_unlink(*args, **kwargs)

    monkeypatch.setattr(atomic_files.os, "fsync", record_fsync)
    monkeypatch.setattr(atomic_files.os, "link", record_link)
    monkeypatch.setattr(atomic_files.os, "unlink", record_unlink)

    assert atomic_create_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert events == ["file-fsync", "link", "unlink-temp", "directory-fsync"]


@_POSIX_DESCRIPTOR_ONLY
def test_missing_directory_fd_capability_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    monkeypatch.setattr(atomic_files.os, "supports_dir_fd", set())

    with pytest.raises(
        AtomicWriteError, match="secure directory-relative replacement is unavailable"
    ):
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert not target.exists()


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_replace_dir_fd_fails_closed_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"

    def unsupported_replace(*args: object, **kwargs: object) -> None:
        raise NotImplementedError("replace dir_fd is unavailable")

    monkeypatch.setattr(atomic_files.os, "replace", unsupported_replace)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_root_nofollow_open_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    real_open = atomic_files.os.open

    def unsupported_open(path, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        if dir_fd is not None and path == tmp_path.name:
            raise NotImplementedError("root no-follow open is unavailable")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(atomic_files.os, "open", unsupported_open)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_intermediate_dir_fd_open_closes_root_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = tmp_path / "allowed"
    parent = allowed_root / "parent"
    parent.mkdir(parents=True)
    target = parent / "health.json"
    real_open = atomic_files.os.open
    root_fd: int | None = None

    def unsupported_open(path, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        nonlocal root_fd
        if dir_fd is not None and path == "parent":
            raise TypeError("intermediate dir_fd open is unavailable")
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and path == allowed_root.name:
            root_fd = descriptor
        return descriptor

    monkeypatch.setattr(atomic_files.os, "open", unsupported_open)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=allowed_root, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert root_fd is not None
    with pytest.raises(OSError):
        os.fstat(root_fd)
    assert not target.exists()


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_nofollow_stat_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    real_stat = atomic_files.os.stat

    def unsupported_stat(path, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is not None and follow_symlinks is False:
            raise TypeError("no-follow stat is unavailable")
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(atomic_files.os, "stat", unsupported_stat)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_temp_dir_fd_open_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"
    real_open = atomic_files.os.open

    def unsupported_open(path, flags: int, mode: int = 0o777, *, dir_fd=None) -> int:
        if dir_fd is not None and str(path).startswith(".health.json.tmp-"):
            raise NotImplementedError("temp dir_fd open is unavailable")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(atomic_files.os, "open", unsupported_open)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_mode_application_fails_closed_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "health.json"

    def unsupported_fchmod(*args: object, **kwargs: object) -> None:
        raise NotImplementedError("fchmod is unavailable")

    monkeypatch.setattr(atomic_files.os, "fchmod", unsupported_fchmod)

    with pytest.raises(AtomicWriteError) as error:
        atomic_replace_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_link_dir_fd_fails_closed_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "key"

    def unsupported_link(*args: object, **kwargs: object) -> None:
        raise NotImplementedError("link dir_fd is unavailable")

    monkeypatch.setattr(atomic_files.os, "link", unsupported_link)

    with pytest.raises(AtomicWriteError) as error:
        atomic_create_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


@_POSIX_DESCRIPTOR_ONLY
def test_runtime_missing_unlink_is_normalized_and_cleanup_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "key"
    real_unlink = atomic_files.os.unlink
    temp_unlink_attempts = 0

    def unavailable_once(path, *, dir_fd=None) -> None:
        nonlocal temp_unlink_attempts
        if dir_fd is not None and str(path).startswith(".key.tmp-"):
            temp_unlink_attempts += 1
            if temp_unlink_attempts == 1:
                raise NotImplementedError("unlink dir_fd is unavailable")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(atomic_files.os, "unlink", unavailable_once)

    with pytest.raises(AtomicWriteError) as error:
        atomic_create_bytes(target, b"new", allowed_root=tmp_path, mode=0o600)

    assert str(error.value) == "secure directory-relative replacement is unavailable"
    assert target.read_bytes() == b"new"
    assert temp_unlink_attempts == 2
    assert _temp_entries(tmp_path, target.name) == []


def test_secure_append_creates_then_appends_complete_framed_records(tmp_path: Path) -> None:
    target = tmp_path / "certificates" / "demo-skill.jsonl"
    target.parent.mkdir()
    first = b'{"entry":1}\n'
    second = b'{"entry":2}\n'

    atomic_append_bytes(target, first, allowed_root=tmp_path, mode=0o600)
    atomic_append_bytes(target, second, allowed_root=tmp_path, mode=0o600)

    assert target.read_bytes() == first + second
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


def test_compare_and_append_distinguishes_missing_empty_and_exact_predecessor(
    tmp_path: Path,
) -> None:
    target = tmp_path / "certificate.jsonl"
    target.write_bytes(b"")

    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_append_bytes(
            target,
            b"first\n",
            expected_previous_bytes=None,
            allowed_root=tmp_path,
            mode=0o600,
        )
    atomic_compare_and_append_bytes(
        target,
        b"first\n",
        expected_previous_bytes=b"",
        allowed_root=tmp_path,
        mode=0o600,
    )
    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_append_bytes(
            target,
            b"wrong\n",
            expected_previous_bytes=b"",
            allowed_root=tmp_path,
            mode=0o600,
        )
    atomic_compare_and_append_bytes(
        target,
        b"second\n",
        expected_previous_bytes=b"first\n",
        allowed_root=tmp_path,
        mode=0o600,
    )

    assert target.read_bytes() == b"first\nsecond\n"


def test_compare_and_append_expected_empty_rejects_missing_without_creation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "certificate.jsonl"

    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_append_bytes(
            target,
            b"first\n",
            expected_previous_bytes=b"",
            allowed_root=tmp_path,
            mode=0o600,
        )

    assert not target.exists()


def test_compare_and_append_serializes_concurrent_missing_predecessor(
    tmp_path: Path,
) -> None:
    target = tmp_path / "certificate.jsonl"
    barrier = threading.Barrier(2)

    def append(frame: bytes) -> bool:
        barrier.wait()
        try:
            atomic_compare_and_append_bytes(
                target,
                frame,
                expected_previous_bytes=None,
                allowed_root=tmp_path,
                mode=0o600,
            )
        except AtomicWriteError as exc:
            assert "predecessor mismatch" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, (b"first\n", b"second\n")))

    assert sorted(outcomes) == [False, True]
    assert target.read_bytes() in {b"first\n", b"second\n"}


def test_secure_append_rejects_outside_and_symlink_destinations(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-certificate.jsonl"
    victim = tmp_path / "victim.jsonl"
    victim.write_bytes(b"safe\n")
    symlink = tmp_path / "certificate.jsonl"
    symlink.symlink_to(victim)

    with pytest.raises(AtomicWriteError):
        atomic_append_bytes(outside, b"outside\n", allowed_root=tmp_path, mode=0o600)
    with pytest.raises(AtomicWriteError):
        atomic_append_bytes(symlink, b"unsafe\n", allowed_root=tmp_path, mode=0o600)

    assert not outside.exists()
    assert victim.read_bytes() == b"safe\n"


def test_secure_append_rejects_intermediate_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "certificates").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AtomicWriteError):
        atomic_append_bytes(
            allowed / "certificates" / "demo.jsonl",
            b"entry\n",
            allowed_root=allowed,
            mode=0o600,
        )

    assert not (outside / "demo.jsonl").exists()


def test_secure_append_fails_closed_when_capability_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"
    _force_atomic_capability_error(monkeypatch)

    with pytest.raises(AtomicWriteError, match="secure directory-relative replacement"):
        atomic_append_bytes(target, b"entry\n", allowed_root=tmp_path, mode=0o600)

    assert not target.exists()


def test_explicit_non_atomic_append_fallback_is_framed_flushed_and_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"
    _force_atomic_capability_error(monkeypatch)

    atomic_append_bytes(
        target,
        b"entry\n",
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )

    assert target.read_bytes() == b"entry\n"


def test_explicit_non_atomic_fallback_uses_plain_chmod_when_nofollow_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "health.json"
    real_chmod = atomic_files.os.chmod

    def windows_compatible_chmod(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        if follow_symlinks is False:
            raise NotImplementedError("chmod: follow_symlinks unavailable")
        real_chmod(path, mode)

    _force_atomic_capability_error(monkeypatch)
    monkeypatch.setattr(atomic_files.os, "chmod", windows_compatible_chmod)

    atomic_replace_bytes(
        target,
        b"new",
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )

    assert target.read_bytes() == b"new"


def test_explicit_non_atomic_read_preserves_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "missing.json"
    _force_atomic_capability_error(monkeypatch)

    with pytest.raises(FileNotFoundError):
        atomic_files.read_regular_file_bytes(
            target,
            allowed_root=tmp_path,
            allow_non_atomic=True,
        )


def test_explicit_non_atomic_compare_and_append_fallback_checks_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"
    _force_atomic_capability_error(monkeypatch)

    atomic_compare_and_append_bytes(
        target,
        b"first\n",
        expected_previous_bytes=None,
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )
    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_append_bytes(
            target,
            b"wrong\n",
            expected_previous_bytes=b"",
            allowed_root=tmp_path,
            mode=0o600,
            allow_non_atomic=True,
        )
    atomic_compare_and_append_bytes(
        target,
        b"second\n",
        expected_previous_bytes=b"first\n",
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )

    assert target.read_bytes() == b"first\nsecond\n"


def test_explicit_non_atomic_append_fallback_still_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = tmp_path / "victim.jsonl"
    victim.write_bytes(b"safe\n")
    target = tmp_path / "certificate.jsonl"
    target.symlink_to(victim)
    _force_atomic_capability_error(monkeypatch)

    with pytest.raises(AtomicWriteError):
        atomic_append_bytes(
            target,
            b"entry\n",
            allowed_root=tmp_path,
            mode=0o600,
            allow_non_atomic=True,
        )

    assert victim.read_bytes() == b"safe\n"


def test_windows_ffi_structures_keep_handle_fields_pointer_width() -> None:
    assert ctypes.sizeof(atomic_files._WinHandle) == ctypes.sizeof(ctypes.c_void_p)
    assert (
        atomic_files._WinObjectAttributes.RootDirectory.offset
        % ctypes.sizeof(ctypes.c_void_p)
        == 0
    )
    rename = atomic_files._windows_file_rename_info("certificate.jsonl", 0)
    assert type(rename).RootDirectory.offset % ctypes.sizeof(ctypes.c_void_p) == 0
    rename_fields = dict(type(rename)._fields_)
    assert ctypes.sizeof(rename_fields["RootDirectory"]) == ctypes.sizeof(
        ctypes.c_void_p
    )
    assert (
        atomic_files._WinIoStatusBlock.Information.offset
        == ctypes.sizeof(ctypes.c_void_p)
    )


def test_windows_file_disposition_boolean_has_native_one_byte_abi() -> None:
    fields = dict(atomic_files._WinFileDispositionInformation._fields_)

    assert fields["DeleteFile"] is ctypes.c_ubyte
    assert ctypes.sizeof(atomic_files._WinFileDispositionInformation) == 1


def test_windows_rename_retries_legacy_handle_relative_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots: list[int | None] = []

    class Ntdll:
        def NtSetInformationFile(
            self,
            _handle: object,
            _io_status: object,
            information: object,
            _size: int,
            information_class: int,
        ) -> int:
            rename = ctypes.cast(
                information,
                ctypes.POINTER(atomic_files._WinFileRenameInfo),
            ).contents
            roots.append(rename.RootDirectory)
            if information_class == 65:
                return -1
            if information_class == 10:
                return 0
            raise AssertionError(f"unexpected information class {information_class}")

        def RtlNtStatusToDosError(self, _status: int) -> int:
            return 87

    monkeypatch.setattr(
        atomic_files,
        "_windows_modules",
        lambda: (object(), object(), Ntdll()),
    )

    assert atomic_files._windows_rename_handle(
        123,
        456,
        "certificate.jsonl",
        replace=True,
    )
    assert roots == [456, 456]


def test_windows_mark_delete_reports_native_failure_after_one_byte_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    class Kernel32:
        def SetFileInformationByHandle(
            self,
            _handle: object,
            _information_class: int,
            information: object,
            size: int,
        ) -> int:
            value = ctypes.cast(
                information,
                ctypes.POINTER(atomic_files._WinFileDispositionInformation),
            ).contents.DeleteFile
            calls.append((int(value), size))
            return 0

    monkeypatch.setattr(
        atomic_files,
        "_windows_modules",
        lambda: (Kernel32(), object(), object()),
    )
    monkeypatch.setattr(
        atomic_files.ctypes,
        "get_last_error",
        lambda: 5,
        raising=False,
    )

    with pytest.raises(AtomicWriteError, match="winerror 5"):
        atomic_files._windows_mark_delete(123)

    assert calls == [(1, 1)]


def test_windows_zero_file_id_is_capability_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Kernel32:
        def GetFileInformationByHandleEx(self, *_args: object) -> int:
            return 1

    monkeypatch.setattr(
        atomic_files,
        "_windows_modules",
        lambda: (Kernel32(), object(), object()),
    )

    with pytest.raises(AtomicWriteError) as caught:
        atomic_files._windows_file_id(123)

    assert str(caught.value) == atomic_files._CAPABILITY_ERROR
    assert atomic_files._is_capability_error(caught.value)


def test_windows_component_length_is_rejected_before_native_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    too_long = "a" * 32_767

    def unexpected_native_call() -> tuple[object, object, object]:
        raise AssertionError("native API called before component validation")

    monkeypatch.setattr(atomic_files, "_windows_modules", unexpected_native_call)

    with pytest.raises(AtomicWriteError, match="too long"):
        atomic_files._windows_open_relative(
            123,
            too_long,
            access=0,
            disposition=1,
            options=0,
        )
    with pytest.raises(AtomicWriteError, match="too long"):
        atomic_files._windows_file_rename_info(too_long, 123)
    with pytest.raises(AtomicWriteError, match="too long"):
        atomic_files._windows_path_parts(tmp_path / too_long, tmp_path)


def test_windows_component_maximum_utf16_length_is_accepted() -> None:
    maximum = "a" * 32_766

    information = atomic_files._windows_file_rename_info(maximum, 123)

    assert information.FileNameLength == 0xFFFC


# famulus-skip: category=platform-contract; reason=requires native Win32 handles and ACLs; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_secure_create_replace_append_and_acl(tmp_path: Path) -> None:
    target = tmp_path / "certificate.jsonl"

    assert atomic_create_bytes(target, b"first\n", allowed_root=tmp_path, mode=0o600)
    atomic_append_bytes(target, b"second\n", allowed_root=tmp_path, mode=0o600)
    atomic_compare_and_append_bytes(
        target,
        b"third\n",
        expected_previous_bytes=b"first\nsecond\n",
        allowed_root=tmp_path,
        mode=0o600,
    )
    assert target.read_bytes() == b"first\nsecond\nthird\n"
    assert _windows_native_acl_is_restrictive(target, tmp_path)
    atomic_replace_bytes(target, b"replacement\n", allowed_root=tmp_path, mode=0o600)
    assert target.read_bytes() == b"replacement\n"
    assert _windows_native_acl_is_restrictive(target, tmp_path)


# famulus-skip: category=platform-contract; reason=requires native Win32 delete-on-close cleanup; alternate=the one-byte disposition ABI and failure-reporting tests run on every host
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_failed_temp_write_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"

    def interrupted_write(_handle: int, _data: bytes) -> None:
        raise AtomicWriteError("interrupted native write")

    monkeypatch.setattr(atomic_files, "_windows_write_handle", interrupted_write)

    with pytest.raises(AtomicWriteError, match="interrupted native write"):
        atomic_create_bytes(target, b"entry\n", allowed_root=tmp_path, mode=0o600)

    assert not target.exists()
    assert _temp_entries(tmp_path, target.name) == []


# famulus-skip: category=platform-contract; reason=requires native Win32 reparse-point behavior; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_reparse_destination_is_rejected(tmp_path: Path) -> None:
    victim = tmp_path / "victim.jsonl"
    victim.write_bytes(b"safe\n")
    target = tmp_path / "certificate.jsonl"
    try:
        target.symlink_to(victim)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=native symlink creation may be unavailable; alternate=the native ACL and append contract runs separately
        pytest.skip(f"Windows symlink creation unavailable: {exc}")

    with pytest.raises(AtomicWriteError):
        atomic_append_bytes(
            target,
            b"unsafe\n",
            allowed_root=tmp_path,
            mode=0o600,
            allow_non_atomic=True,
        )

    assert victim.read_bytes() == b"safe\n"


# famulus-skip: category=platform-contract; reason=requires native Win32 junction behavior; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_intermediate_junction_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    junction = allowed / "certificates"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # famulus-skip: category=platform-contract; reason=junction creation is unavailable on this Windows host; alternate=the final-reparse native contract runs separately
        pytest.skip(f"Windows junction creation unavailable: {result.stderr}")

    with pytest.raises(AtomicWriteError, match="reparse"):
        atomic_append_bytes(
            junction / "demo.jsonl",
            b"unsafe\n",
            allowed_root=allowed,
            mode=0o600,
        )

    assert not (outside / "demo.jsonl").exists()


# famulus-skip: category=platform-contract; reason=requires native Win32 parent-handle behavior; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_parent_swap_cannot_redirect_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    parent = allowed / "parent"
    displaced = allowed / "displaced"
    parent.mkdir(parents=True)
    target = parent / "certificate.jsonl"
    original_open = atomic_files._windows_open_relative
    swapped = False

    def swap_after_parent_open(*args: object, **kwargs: object):
        nonlocal swapped
        if not swapped and len(args) > 1 and args[1] == target.name:
            parent.rename(displaced)
            parent.mkdir()
            swapped = True
        return original_open(*args, **kwargs)

    monkeypatch.setattr(atomic_files, "_windows_open_relative", swap_after_parent_open)

    with pytest.raises(AtomicWriteError, match="parent changed"):
        atomic_replace_bytes(
            target, b"bound-to-handle\n", allowed_root=allowed, mode=0o600
        )

    assert not target.exists()
    assert not (displaced / target.name).exists()


# famulus-skip: category=platform-contract; reason=requires native Win32 DACL behavior; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_rejects_nonrestrictive_dacl_then_repairs_on_append(
    tmp_path: Path,
) -> None:
    target = tmp_path / "certificate.jsonl"
    target.write_bytes(b"first\n")
    result = subprocess.run(
        ["icacls", str(target), "/grant", "*S-1-1-0:F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # famulus-skip: category=platform-contract; reason=ACL mutation is unavailable on this Windows host; alternate=the native restrictive-ACL positive contract runs separately
        pytest.skip(f"Windows ACL mutation unavailable: {result.stderr}")
    assert not _windows_native_acl_is_restrictive(target, tmp_path)

    atomic_append_bytes(target, b"second\n", allowed_root=tmp_path, mode=0o600)

    assert _windows_native_acl_is_restrictive(target, tmp_path)


# famulus-skip: category=platform-contract; reason=requires native 64-bit Win32 ctypes declarations; alternate=defined here and run by the Windows suite
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_ctypes_handle_signatures_are_pointer_sized() -> None:
    import ctypes

    kernel32, advapi32, ntdll = atomic_files._windows_modules()

    assert ctypes.sizeof(atomic_files._WinHandle) == ctypes.sizeof(ctypes.c_void_p)
    assert kernel32.CreateFileW.restype is atomic_files._WinHandle
    assert kernel32.CloseHandle.argtypes == [atomic_files._WinHandle]
    assert advapi32.GetSecurityInfo.argtypes[0] is atomic_files._WinHandle
    assert ntdll.NtCreateFile.argtypes[0]._type_ is atomic_files._WinHandle


# famulus-skip: category=platform-contract; reason=requires the native Win32 capability branch; alternate=the POSIX capability fallback contract runs on every non-Windows host
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_capability_failure_requires_explicit_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"

    def unavailable(*args: object, **kwargs: object):
        raise AtomicWriteError(atomic_files._CAPABILITY_ERROR)

    monkeypatch.setattr(atomic_files, "_windows_open_parent", unavailable)

    with pytest.raises(AtomicWriteError, match="secure directory-relative"):
        atomic_create_bytes(target, b"first\n", allowed_root=tmp_path, mode=0o600)
    assert atomic_create_bytes(
        target,
        b"first\n",
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )
    assert target.read_bytes() == b"first\n"


# famulus-skip: category=platform-contract; reason=requires native Win32 handles before forcing unusable file identity; alternate=the zero-ID capability classification test runs on every host
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_zero_file_id_fails_before_mutation_then_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "certificate.jsonl"
    target.write_bytes(b"old\n")

    def unusable_identity(_handle: int) -> tuple[int, bytes]:
        raise AtomicWriteError(atomic_files._CAPABILITY_ERROR)

    monkeypatch.setattr(atomic_files, "_windows_file_id", unusable_identity)

    with pytest.raises(AtomicWriteError, match="secure directory-relative"):
        atomic_replace_bytes(target, b"new\n", allowed_root=tmp_path, mode=0o600)
    assert target.read_bytes() == b"old\n"

    atomic_replace_bytes(
        target,
        b"new\n",
        allowed_root=tmp_path,
        mode=0o600,
        allow_non_atomic=True,
    )
    assert target.read_bytes() == b"new\n"


# famulus-skip: category=platform-contract; reason=requires native LockFileEx behavior; alternate=the POSIX concurrent-predecessor contract runs on every non-Windows host
@pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
def test_windows_native_compare_and_append_serializes_racing_writers(
    tmp_path: Path,
) -> None:
    target = tmp_path / "certificate.jsonl"
    barrier = threading.Barrier(2)

    def append(frame: bytes) -> bool:
        barrier.wait()
        try:
            atomic_compare_and_append_bytes(
                target,
                frame,
                expected_previous_bytes=None,
                allowed_root=tmp_path,
                mode=0o600,
            )
        except AtomicWriteError as exc:
            assert "predecessor mismatch" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, (b"first\n", b"second\n")))

    assert sorted(outcomes) == [False, True]
