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
    atomic_compare_and_delete,
    atomic_compare_and_replace_bytes,
    atomic_append_bytes,
    atomic_compare_and_append_bytes,
    atomic_create_bytes,
    atomic_replace_bytes,
)


def test_compare_replace_and_delete_reject_changed_preimages(tmp_path: Path) -> None:
    target = tmp_path / "managed.json"
    target.write_bytes(b"external")

    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_replace_bytes(
            target,
            b"replacement",
            expected_previous_bytes=b"planned",
            expected_previous_mode=0o600,
            allowed_root=tmp_path,
            mode=0o600,
        )
    with pytest.raises(AtomicWriteError, match="predecessor mismatch"):
        atomic_compare_and_delete(
            target,
            expected_previous_bytes=b"planned",
            expected_previous_mode=0o600,
            allowed_root=tmp_path,
        )

    assert target.read_bytes() == b"external"


def test_compare_replace_rejects_leaf_replacement_after_preimage_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "managed.json"
    target.write_bytes(b"planned")
    target.chmod(0o600)
    real_write = atomic_files._write_and_sync

    def swap_after_read(descriptor: int, data: bytes) -> None:
        real_write(descriptor, data)
        target.unlink()
        target.write_bytes(b"external")

    monkeypatch.setattr(atomic_files, "_write_and_sync", swap_after_read)

    with pytest.raises(AtomicWriteError, match="changed"):
        atomic_compare_and_replace_bytes(
            target,
            b"replacement",
            expected_previous_bytes=b"planned",
            expected_previous_mode=0o600,
            allowed_root=tmp_path,
            mode=0o600,
        )

    assert target.read_bytes() == b"external"


# famulus-skip: category=platform-contract; reason=these cases inject POSIX descriptor internals; alternate=the native Windows contract cases below exercise the corresponding Windows branches
_POSIX_DESCRIPTOR_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="POSIX descriptor implementation contract"
)


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
        if dir_fd is None and Path(path) == tmp_path:
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
        if dir_fd is None and Path(path) == allowed_root:
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


def test_windows_directory_handle_requests_relative_rename_target_access() -> None:
    # FileRenameInformation resolves a relative destination by opening its
    # directory with FILE_ADD_FILE.  A retained RootDirectory handle without
    # that granted right fails with STATUS_ACCESS_DENIED on Windows.
    assert atomic_files._WIN_DIR_ACCESS & 0x2


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


def test_windows_rename_retries_legacy_after_extended_class_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    classes: list[int] = []

    class Ntdll:
        def NtSetInformationFile(
            self,
            _handle: object,
            _io_status: object,
            _information: object,
            _size: int,
            information_class: int,
        ) -> int:
            classes.append(information_class)
            return -1 if information_class == 65 else 0

        def RtlNtStatusToDosError(self, _status: int) -> int:
            return 5

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
    assert classes == [65, 10]


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
