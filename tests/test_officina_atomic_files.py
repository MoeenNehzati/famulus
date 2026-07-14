from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import officina.common.atomic_files as atomic_files
from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_create_bytes,
    atomic_replace_bytes,
)


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


def test_atomic_interfaces_are_exported_from_common_package() -> None:
    assert atomic_files.AtomicWriteError is AtomicWriteError

    from officina.common import AtomicWriteError as exported_error
    from officina.common import atomic_create_bytes as exported_create
    from officina.common import atomic_replace_bytes as exported_replace

    assert exported_error is AtomicWriteError
    assert exported_create is atomic_create_bytes
    assert exported_replace is atomic_replace_bytes
