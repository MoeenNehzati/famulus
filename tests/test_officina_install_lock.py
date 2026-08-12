"""Concurrency regressions for the home-scoped installer operation lock."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from officina.install.install_lock import InstallBusyError, InstallLock

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_RTX = REPO_ROOT / "skills" / "install-assistant-tools" / "_rtx"
sys.path.insert(0, str(INSTALL_RTX))

import _install_scaffold as repair_entrypoint  # noqa: E402
import _install_uninstall as uninstall_entrypoint  # noqa: E402
import _phase_entry as install_entrypoint  # noqa: E402


def _tree_digest(root: Path) -> str:
    """Return a hand-independent digest of names, types, and file bytes below root."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        if path.is_symlink():
            digest.update(b"L\0" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F\0" + path.read_bytes())
        elif path.is_dir():
            digest.update(b"D\0")
    return digest.hexdigest()


def _start_operation(
    operation: Callable[[], object],
) -> tuple[threading.Thread, list[object], list[BaseException]]:
    results: list[object] = []
    errors: list[BaseException] = []

    def invoke() -> None:
        try:
            results.append(operation())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    return thread, results, errors


def test_fresh_install_dry_run_does_not_create_home_or_lock(tmp_path: Path) -> None:
    home = tmp_path / "absent-home"

    status = install_entrypoint.run(
        home=home,
        dry_run=True,
        non_interactive=True,
        dev_mode=False,
        agents=[],
        default_llm="codex",
        include_optional_dependencies=False,
    )

    assert status == 0
    assert not home.exists()


def test_repair_dry_run_keeps_home_byte_identical_without_state(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "user-owned.txt").write_text("unchanged\n", encoding="utf-8")
    before = _tree_digest(home)

    status = repair_entrypoint.run(
        repo_root=REPO_ROOT,
        home=home,
        dry_run=True,
    )

    assert status == 0
    assert _tree_digest(home) == before
    assert not InstallLock.for_home(home).state_root.exists()


def test_install_install_contention_precedes_candidate_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    protected = home / "protected.txt"
    protected.write_text("unchanged\n", encoding="utf-8")

    entered = threading.Event()
    release = threading.Event()

    def hold_first_install(**_kwargs: object) -> int:
        entered.set()
        assert release.wait(timeout=5)
        return 1

    monkeypatch.setattr(
        install_entrypoint, "_build_managed_runtime_candidate", hold_first_install
    )
    first, results, errors = _start_operation(
        lambda: install_entrypoint.run(
            home=home,
            non_interactive=True,
            dev_mode=False,
            agents=[],
            default_llm="codex",
            include_optional_dependencies=False,
        )
    )
    assert entered.wait(timeout=5)
    monkeypatch.setattr(install_entrypoint, "INSTALL_LOCK_TIMEOUT_SECONDS", 0)

    try:
        before = _tree_digest(home)
        with pytest.raises(InstallBusyError, match="install_busy"):
            install_entrypoint.run(
                home=home,
                non_interactive=True,
                dev_mode=False,
                agents=[],
                default_llm="codex",
                include_optional_dependencies=False,
            )
        assert _tree_digest(home) == before
    finally:
        release.set()
        first.join(timeout=5)
    assert not first.is_alive()
    assert results == [1]
    assert errors == []


def test_repair_repair_contention_precedes_scaffold_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    protected = home / "protected.txt"
    protected.write_text("unchanged\n", encoding="utf-8")

    entered = threading.Event()
    release = threading.Event()

    class StopFirstRepair(RuntimeError):
        pass

    def hold_first_repair(**_kwargs: object) -> None:
        entered.set()
        assert release.wait(timeout=5)
        raise StopFirstRepair

    monkeypatch.setattr(
        repair_entrypoint, "warn_if_managed_release_missing", hold_first_repair
    )
    first, _results, errors = _start_operation(
        lambda: repair_entrypoint.run(repo_root=REPO_ROOT, home=home)
    )
    assert entered.wait(timeout=5)
    monkeypatch.setattr(repair_entrypoint, "INSTALL_LOCK_TIMEOUT_SECONDS", 0)

    try:
        before = _tree_digest(home)
        with pytest.raises(InstallBusyError, match="install_busy"):
            repair_entrypoint.run(repo_root=REPO_ROOT, home=home)
        assert _tree_digest(home) == before
    finally:
        release.set()
        first.join(timeout=5)
    assert not first.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], StopFirstRepair)


def test_install_uninstall_contention_precedes_manifest_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    protected = home / "protected.txt"
    protected.write_text("unchanged\n", encoding="utf-8")

    entered = threading.Event()
    release = threading.Event()

    def hold_first_install(**_kwargs: object) -> int:
        entered.set()
        assert release.wait(timeout=5)
        return 1

    def mutate_then_fail(*_args: object, **_kwargs: object) -> object:
        protected.write_text("uninstall entered\n", encoding="utf-8")
        raise AssertionError("manifest read entered")

    monkeypatch.setattr(uninstall_entrypoint, "Manifest", mutate_then_fail)
    monkeypatch.setattr(
        install_entrypoint, "_build_managed_runtime_candidate", hold_first_install
    )
    first, results, errors = _start_operation(
        lambda: install_entrypoint.run(
            home=home,
            non_interactive=True,
            dev_mode=False,
            agents=[],
            default_llm="codex",
            include_optional_dependencies=False,
        )
    )
    assert entered.wait(timeout=5)
    monkeypatch.setattr(
        uninstall_entrypoint, "INSTALL_LOCK_TIMEOUT_SECONDS", 0, raising=False
    )
    monkeypatch.setattr(
        uninstall_entrypoint,
        "parse_args",
        lambda: SimpleNamespace(
            home=str(home),
            repo_root=str(REPO_ROOT),
            claude_home=None,
            codex_home=None,
            bin_dir=None,
            shell_rc=None,
            system_shell_rc=None,
            no_system_shell_rc=True,
            manifest=None,
            no_pip=True,
            no_git_hooks=True,
            purge=False,
            dry_run=False,
        ),
    )

    try:
        before = _tree_digest(home)
        with pytest.raises(InstallBusyError, match="install_busy"):
            uninstall_entrypoint.main()
        assert _tree_digest(home) == before
    finally:
        release.set()
        first.join(timeout=5)
    assert not first.is_alive()
    assert results == [1]
    assert errors == []


def _start_lock_holder(lock_path: Path, state_root: Path) -> subprocess.Popen[str]:
    script = """
import sys
from pathlib import Path
from officina.install.install_lock import InstallLock

lock_path = Path(sys.argv[1])
state_root = Path(sys.argv[2])
ready_path = Path(sys.argv[3])
with InstallLock(lock_path, timeout_seconds=5, state_root=state_root):
    ready_path.write_text("locked", encoding="utf-8")
    sys.stdin.read(1)
"""
    ready_path = lock_path.with_name("holder-ready")
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), str(state_root), str(ready_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(500):
        if ready_path.is_file():
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"lock holder exited {process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.01)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError("lock holder did not report readiness")


def test_dead_owner_releases_kernel_lock(tmp_path: Path) -> None:
    state_root = tmp_path / "install"
    lock_path = state_root / "operation.lock"
    process = _start_lock_holder(lock_path, state_root)
    process.kill()
    process.wait(timeout=5)

    with InstallLock(lock_path, timeout_seconds=1, state_root=state_root):
        assert lock_path.is_file()


def test_two_serialized_processes_preserve_both_manifest_entries(tmp_path: Path) -> None:
    """Loading and recording under the home lock cannot lose a racing writer."""
    lock_path = tmp_path / "state" / "operation.lock"
    manifest_path = tmp_path / "state" / "install-manifest.json"
    active_path = tmp_path / "state" / "active-writer"
    script = """
import sys
import time
from pathlib import Path

from officina.install.install_lock import InstallLock

rtx = Path(sys.argv[1])
sys.path.insert(0, str(rtx))
from _state_record import Manifest

lock_path = Path(sys.argv[2])
manifest_path = Path(sys.argv[3])
active_path = Path(sys.argv[4])
entry_path = sys.argv[5]
with InstallLock(lock_path, timeout_seconds=5, state_root=lock_path.parent):
    if active_path.exists():
        raise SystemExit("two writers entered the critical section")
    active_path.write_text(entry_path, encoding="utf-8")
    try:
        time.sleep(0.1)
        Manifest(manifest_path, state_root=manifest_path.parent).record("file", path=entry_path)
    finally:
        active_path.unlink(missing_ok=True)
"""
    repository_root = Path(__file__).resolve().parents[1]
    rtx = repository_root / "skills" / "install-assistant-tools" / "_rtx"
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(rtx),
                str(lock_path),
                str(manifest_path),
                str(active_path),
                str(tmp_path / f"installed-{number}"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for number in range(2)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {entry["path"] for entry in payload["entries"]} == {
        str(tmp_path / "installed-0"),
        str(tmp_path / "installed-1"),
    }


def test_install_lock_rejects_symlink_path(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    real_lock = tmp_path / "real.lock"
    real_lock.write_bytes(b"")
    symlink = state_root / "operation.lock"
    try:
        symlink.symlink_to(real_lock)
    except (OSError, NotImplementedError):
        # famulus-skip: category=capability-unavailable; reason=this regression requires native link creation; alternate=outside-root and ordinary lock tests cover confinement without links
        pytest.skip("symlinks unavailable")

    with pytest.raises(OSError):
        with InstallLock(symlink, timeout_seconds=0, state_root=state_root):
            pytest.fail("symlink lock entered")


def test_install_lock_rejects_path_outside_trusted_state_root(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    outside_lock = tmp_path / "outside" / "operation.lock"

    with pytest.raises(ValueError, match="outside state_root"):
        with InstallLock(outside_lock, timeout_seconds=0, state_root=state_root):
            pytest.fail("outside lock entered")

    assert not outside_lock.parent.exists()


def test_install_lock_rejects_intermediate_directory_symlink(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    outside = tmp_path / "outside"
    state_root.mkdir()
    outside.mkdir()
    redirected_parent = state_root / "redirected"
    try:
        redirected_parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # famulus-skip: category=capability-unavailable; reason=this regression requires native directory-link creation; alternate=outside-root lock rejection covers confinement without links
        pytest.skip("directory symlinks unavailable")

    with pytest.raises(OSError):
        with InstallLock(
            redirected_parent / "operation.lock",
            timeout_seconds=0,
            state_root=state_root,
        ):
            pytest.fail("redirected lock entered")

    assert not (outside / "operation.lock").exists()


def test_negative_timeout_is_rejected_before_lock_creation(tmp_path: Path) -> None:
    state_root = tmp_path / "install"
    lock_path = state_root / "operation.lock"

    with pytest.raises(ValueError, match="non-negative"):
        with InstallLock(lock_path, timeout_seconds=-1, state_root=state_root):
            pytest.fail("negative-timeout lock entered")

    assert not lock_path.exists()
