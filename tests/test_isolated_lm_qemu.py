"""Tests for shell-free QEMU control and bounded isolated-VM lifecycle."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
from subprocess import CalledProcessError, CompletedProcess
from typing import Any

import pytest

import test_support.isolated_lm.qemu as qemu_module
import test_support.isolated_lm.model as model_module
from test_support.isolated_lm.model import RunRecord, RuntimePaths, VmResources
from test_support.isolated_lm.qemu import (
    allocate_loopback_port,
    build_qemu_command,
    build_ssh_command,
    start_run,
    stop_run,
    wait_for_ssh,
)


def _unix_socket_metadata(path: Path) -> os.stat_result:
    """Provide socket metadata at the injectable QMP filesystem boundary."""
    del path
    return os.stat_result((stat.S_IFSOCK, 0, 0, 0, 0, 0, 0, 0, 0, 0))


@pytest.fixture
def prepared_run(tmp_path: Path) -> RunRecord:
    """Create real private artifacts without invoking a VM or subprocess."""
    run_dir = (tmp_path / "runs" / "run-42").resolve()
    run_dir.mkdir(parents=True, mode=0o700)
    for name, content in (
        ("overlay.qcow2", b"overlay"),
        ("seed.iso", b"seed"),
        ("known_hosts", b""),
        ("serial.log", b"serial evidence"),
    ):
        path = run_dir / name
        path.write_bytes(content)
        path.chmod(0o600)
    record = RunRecord(
        schema_version=1,
        run_id="run-42",
        run_dir=run_dir,
        resources=VmResources(vcpus=2, memory_mib=1024, disk_gib=7),
        source_image_digest="a" * 64,
        overlay=run_dir / "overlay.qcow2",
        seed_iso=run_dir / "seed.iso",
        known_hosts=run_dir / "known_hosts",
        serial_log=run_dir / "serial.log",
        qmp_socket=run_dir / "qmp.sock",
        pid_file=run_dir / "qemu.pid",
        record_path=run_dir / "run.json",
        ssh_user="famulus-test",
        created_at_utc="2026-08-11T12:00:00+00:00",
        lifecycle="prepared",
    )
    record.record_path.write_text(record.to_json(), encoding="utf-8")
    record.record_path.chmod(0o600)
    return record


@pytest.fixture
def identity_file(tmp_path: Path) -> Path:
    """Create an explicit owner-only private identity outside the run directory."""
    identity = (tmp_path / "operator-identity").resolve()
    identity.write_text("private key", encoding="utf-8")
    identity.chmod(0o600)
    return identity


def _launched(run: RunRecord, identity: Path, lifecycle: str = "running") -> RunRecord:
    """Return a record with the literal launch facts used by command tests."""
    command = tuple(build_qemu_command(run, ssh_port=40222))
    launched = replace(
        run,
        ssh_port=40222,
        identity_file=identity,
        qemu_command=command,
        lifecycle=lifecycle,
    )
    launched.record_path.write_text(launched.to_json(), encoding="utf-8")
    return launched


def _matching_cmdline(run: RunRecord) -> bytes:
    """Render the two exact identity-bearing QEMU arguments from the real argv."""
    return b"\0".join(
        (
            b"qemu-system-x86_64",
            b"-name",
            f"isolated-lm-{run.run_id}".encode(),
            b"-drive",
            f"file={run.overlay},if=virtio,format=qcow2".encode(),
        )
    ) + b"\0"


class _Clock:
    """Deterministic monotonic clock advanced only by injected sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_qemu_command_is_the_exact_approved_shell_free_boundary(
    prepared_run: RunRecord,
) -> None:
    """Catch an exposed service, unapproved device/share, or changed VM boundary."""
    command = build_qemu_command(prepared_run, ssh_port=40222)

    assert command == [
        "qemu-system-x86_64",
        "-name", "isolated-lm-run-42",
        "-machine", "q35,accel=kvm",
        "-cpu", "host",
        "-smp", "2",
        "-m", "1024",
        "-drive", f"file={prepared_run.overlay},if=virtio,format=qcow2",
        "-drive", f"file={prepared_run.seed_iso},if=virtio,format=raw,readonly=on",
        "-netdev", "user,id=net0,hostfwd=tcp:127.0.0.1:40222-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-display", "none",
        "-serial", f"file:{prepared_run.serial_log}",
        "-qmp", f"unix:{prepared_run.qmp_socket},server=on,wait=off",
        "-pidfile", str(prepared_run.pid_file),
        "-daemonize",
    ]
    joined = " ".join(command).lower()
    for forbidden in ("virtiofs", "9p", "mount", "share", "-net bridge", "0.0.0.0"):
        assert forbidden not in joined


def test_ssh_command_uses_only_the_recorded_loopback_identity_and_remote_argv(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch ambient keys, host-key state, a changing guest user, or local shell use."""
    run = _launched(prepared_run, identity_file)

    assert build_ssh_command(run, ["printf", "%s", "two words"]) == [
        "ssh",
        "-p", "40222",
        "-i", str(identity_file),
        "-o", f"UserKnownHostsFile={prepared_run.known_hosts}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "--",
        "famulus-test@127.0.0.1",
        "printf %s 'two words'",
    ]


def test_ssh_command_posix_quotes_empty_whitespace_and_metacharacter_arguments(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch OpenSSH's remote shell changing argument boundaries or expanding input."""
    run = _launched(prepared_run, identity_file)

    command = build_ssh_command(
        run,
        ["printf", "%s|%s|%s", "", "two words", ";$HOME"],
    )

    assert command[-2:] == [
        "famulus-test@127.0.0.1",
        "printf '%s|%s|%s' '' 'two words' ';$HOME'",
    ]


def test_start_rejects_comma_bearing_qemu_values_before_allocation_or_persistence(
    tmp_path: Path, identity_file: Path
) -> None:
    """Catch QEMU parsing a comma in a state-root path as another suboption."""
    paths = RuntimePaths.from_root((tmp_path / "state,unsafe").resolve())
    run_dir = paths.runs / "run-42"
    run_dir.mkdir(parents=True)
    for name, content in (
        ("overlay.qcow2", b"overlay"),
        ("seed.iso", b"seed"),
        ("known_hosts", b""),
        ("serial.log", b""),
    ):
        path = run_dir / name
        path.write_bytes(content)
        path.chmod(0o600)
    run = RunRecord(
        schema_version=1,
        run_id="run-42",
        run_dir=run_dir,
        resources=VmResources(),
        source_image_digest="a" * 64,
        overlay=run_dir / "overlay.qcow2",
        seed_iso=run_dir / "seed.iso",
        known_hosts=run_dir / "known_hosts",
        serial_log=run_dir / "serial.log",
        qmp_socket=run_dir / "qmp.sock",
        pid_file=run_dir / "qemu.pid",
        record_path=run_dir / "run.json",
        ssh_user="famulus-test",
        created_at_utc="2026-08-11T12:00:00+00:00",
        lifecycle="prepared",
    )
    run.record_path.write_text(run.to_json(), encoding="utf-8")
    before = run.record_path.read_bytes()
    events: list[str] = []

    with pytest.raises(ValueError, match="comma"):
        start_run(
            run,
            identity_file,
            allocate_port=lambda: events.append("port") or 40222,
            run_process=lambda argv, **kwargs: events.append("qemu"),
        )

    assert events == []
    assert run.record_path.read_bytes() == before


def test_allocate_loopback_port_binds_ipv4_port_zero_and_closes_before_return() -> None:
    """Catch non-loopback/listening allocation or a reservation held across launch."""
    events: list[object] = []

    class FakeSocket:
        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, *args: object) -> None:
            events.append("closed")

        def bind(self, address: tuple[str, int]) -> None:
            events.append(("bind", address))

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 40222)

    def socket_factory(family: int, kind: int) -> FakeSocket:
        events.append(("socket", family, kind))
        return FakeSocket()

    assert allocate_loopback_port(socket_factory=socket_factory) == 40222
    assert events == [
        ("socket", socket.AF_INET, socket.SOCK_STREAM),
        ("bind", ("127.0.0.1", 0)),
        "closed",
    ]


def test_start_persists_launch_facts_before_qemu_then_marks_running(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch QEMU starting before its exact authority and command are durable."""
    observed: list[dict[str, Any]] = []

    def run_process(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        observed.append(json.loads(prepared_run.record_path.read_text(encoding="utf-8")))
        assert kwargs == {"capture_output": True, "check": False, "timeout": 30.0}
        return CompletedProcess(argv, 0)

    running = start_run(
        prepared_run,
        identity_file,
        allocate_port=lambda: 40222,
        run_process=run_process,
    )

    assert len(observed) == 1
    assert observed[0]["lifecycle"] == "launching"
    assert observed[0]["ssh_port"] == 40222
    assert observed[0]["identity_file"] == str(identity_file)
    assert observed[0]["qemu_command"] == build_qemu_command(prepared_run, 40222)
    assert running.lifecycle == "running"
    assert prepared_run.record_path.read_text(encoding="utf-8") == running.to_json()
    assert prepared_run.record_path.stat().st_mode & 0o777 == 0o600


def test_run_record_replace_fsyncs_retained_parent_directory(
    prepared_run: RunRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order manifest durability as file fsync, replace, then directory fsync."""
    events: list[str] = []
    real_fsync = model_module.os.fsync
    real_replace = model_module.os.replace

    def record_fsync(descriptor: int) -> None:
        events.append(
            "directory-fsync"
            if stat.S_ISDIR(os.fstat(descriptor).st_mode)
            else "file-fsync"
        )
        real_fsync(descriptor)

    def record_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(model_module.os, "fsync", record_fsync)
    monkeypatch.setattr(model_module.os, "replace", record_replace)
    replace(prepared_run, lifecycle="launching").write_atomic()

    assert events == ["file-fsync", "replace", "directory-fsync"]


def test_run_record_directory_fsync_failure_leaves_no_temporary_manifest(
    prepared_run: RunRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean temporary entries even when post-replace directory durability fails."""
    real_fsync = model_module.os.fsync

    def fail_directory(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(model_module.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory fsync"):
        replace(prepared_run, lifecycle="launching").write_atomic()

    assert list(prepared_run.run_dir.glob(".run.json-*")) == []


def test_start_interruption_after_launching_persistence_never_invokes_qemu(
    prepared_run: RunRecord,
    identity_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve recoverable launch authority if interrupted before invocation."""
    real_write = RunRecord.write_atomic

    def interrupt_after_write(record: RunRecord) -> None:
        real_write(record)
        if record.lifecycle == "launching":
            raise KeyboardInterrupt

    monkeypatch.setattr(RunRecord, "write_atomic", interrupt_after_write)
    with pytest.raises(KeyboardInterrupt):
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda *args, **kwargs: pytest.fail("QEMU must not run"),
        )

    persisted = json.loads(prepared_run.record_path.read_text(encoding="utf-8"))
    assert persisted["lifecycle"] == "launching"
    assert persisted["qemu_command"] == build_qemu_command(prepared_run, 40222)


def test_start_interruption_during_qemu_invocation_retains_launching(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Keep an interrupt-uncertain launch recoverable instead of calling it failed."""
    with pytest.raises(KeyboardInterrupt):
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda *args, **kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt()
            ),
        )

    assert json.loads(prepared_run.record_path.read_text())["lifecycle"] == "launching"


def test_start_interruption_before_running_persistence_retains_launching(
    prepared_run: RunRecord,
    identity_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave successful-but-unpublished launch state recoverable after interruption."""
    real_write = RunRecord.write_atomic

    def interrupt_running_write(record: RunRecord) -> None:
        if record.lifecycle == "running":
            raise KeyboardInterrupt
        real_write(record)

    monkeypatch.setattr(RunRecord, "write_atomic", interrupt_running_write)
    with pytest.raises(KeyboardInterrupt):
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
        )

    assert json.loads(prepared_run.record_path.read_text())["lifecycle"] == "launching"


def test_start_marks_launch_failed_without_removing_logs(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch a failed QEMU invocation recorded as running or evidence deletion."""
    with pytest.raises(CalledProcessError) as failure:
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda argv, **kwargs: CompletedProcess(
                argv, 9, stdout=b"qemu out", stderr=b"qemu rejected"
            ),
        )

    persisted = json.loads(prepared_run.record_path.read_text(encoding="utf-8"))
    assert failure.value.returncode == 9
    assert failure.value.stdout == b"qemu out"
    assert failure.value.stderr == b"qemu rejected"
    assert persisted["lifecycle"] == "launch-failed"
    assert persisted["qemu_command"] == build_qemu_command(prepared_run, 40222)
    assert prepared_run.serial_log.read_bytes() == b"serial evidence"


@pytest.mark.parametrize(
    "launch_error",
    [
        subprocess.TimeoutExpired(["qemu-system-x86_64"], 30),
        OSError("launch boundary failed"),
    ],
    ids=["timeout", "exception"],
)
def test_start_launch_uncertainty_retains_launching_when_exact_vm_exists(
    prepared_run: RunRecord,
    identity_file: Path,
    launch_error: BaseException,
) -> None:
    """Never report launch failure while an exact QEMU process may be live."""
    with pytest.raises(type(launch_error)):
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda *args, **kwargs: (_ for _ in ()).throw(launch_error),
            list_proc_pids=lambda: [4312],
            read_proc_cmdline=lambda pid: _matching_cmdline(prepared_run),
        )

    assert json.loads(prepared_run.record_path.read_text())["lifecycle"] == "launching"


def test_start_launch_timeout_records_failed_only_after_exact_scan_finds_none(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Publish launch-failed only after process identity is verified absent."""
    with pytest.raises(subprocess.TimeoutExpired):
        start_run(
            prepared_run,
            identity_file,
            allocate_port=lambda: 40222,
            run_process=lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["qemu-system-x86_64"], 30)
            ),
            list_proc_pids=lambda: [4312],
            read_proc_cmdline=lambda pid: b"other-program\0",
        )

    assert json.loads(prepared_run.record_path.read_text())["lifecycle"] == "launch-failed"


@pytest.mark.parametrize("defect", ["relative", "symlink", "group-readable", "owner-unreadable"])
def test_start_rejects_an_unconfined_identity_before_port_allocation_or_launch(
    prepared_run: RunRecord, tmp_path: Path, defect: str
) -> None:
    """Catch a non-explicit, redirectable, or permission-exposed private key."""
    real = (tmp_path / "real-key").resolve()
    real.write_text("private", encoding="utf-8")
    real.chmod(0o600)
    identity = real
    if defect == "relative":
        identity = Path("real-key")
    elif defect == "symlink":
        identity = tmp_path / "key-link"
        identity.symlink_to(real)
    elif defect == "group-readable":
        real.chmod(0o640)
    else:
        real.chmod(0o200)
    events: list[str] = []

    with pytest.raises(ValueError, match="identity"):
        start_run(
            prepared_run,
            identity,
            allocate_port=lambda: events.append("port") or 40222,
            run_process=lambda argv, **kwargs: pytest.fail("QEMU must not run"),
        )

    assert events == []
    assert json.loads(prepared_run.record_path.read_text())["lifecycle"] == "prepared"


def test_wait_for_ssh_polls_then_requires_cloud_init_before_ready(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch readiness before both SSH and cloud-init complete within one deadline."""
    run = _launched(prepared_run, identity_file)
    clock = _Clock()
    calls: list[tuple[list[str], dict[str, object]]] = []
    returncodes = iter((255, 255, 0, 0))

    def run_process(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        calls.append((argv, kwargs))
        return CompletedProcess(argv, next(returncodes))

    ready = wait_for_ssh(
        run,
        timeout_seconds=10,
        poll_interval_seconds=2,
        run_process=run_process,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert [call[0] for call in calls] == [
        build_ssh_command(run, ["true"]),
        build_ssh_command(run, ["true"]),
        build_ssh_command(run, ["true"]),
        build_ssh_command(run, ["cloud-init", "status", "--wait"]),
    ]
    assert [call[0][-1] for call in calls] == [
        "true", "true", "true", "cloud-init status --wait",
    ]
    assert all(call[1]["check"] is False for call in calls)
    assert all(call[1]["capture_output"] is True for call in calls)
    assert [call[1]["timeout"] for call in calls] == [10, 8, 6, 6]
    assert clock.sleeps == [2, 2]
    assert ready.lifecycle == "ready"
    assert run.record_path.read_text(encoding="utf-8") == ready.to_json()


def test_wait_for_ssh_timeout_is_bounded_and_leaves_running(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch polling beyond the deadline or a VM falsely recorded ready/stopped."""
    run = _launched(prepared_run, identity_file)
    clock = _Clock()
    calls: list[list[str]] = []

    def unavailable(argv: list[str], **kwargs: object) -> CompletedProcess[object]:
        calls.append(argv)
        return CompletedProcess(argv, 255)

    with pytest.raises(TimeoutError, match="SSH readiness"):
        wait_for_ssh(
            run,
            timeout_seconds=5,
            poll_interval_seconds=2,
            run_process=unavailable,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert len(calls) == 3
    assert clock.sleeps == [2, 2, 1]
    assert json.loads(run.record_path.read_text())["lifecycle"] == "running"


def test_cloud_init_failure_leaves_the_existing_vm_running(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch a nonzero cloud-init result being promoted to lifecycle ready."""
    run = _launched(prepared_run, identity_file)
    results = iter((0, 3))

    with pytest.raises(CalledProcessError) as failure:
        wait_for_ssh(
            run,
            timeout_seconds=5,
            run_process=lambda argv, **kwargs: CompletedProcess(argv, next(results)),
        )

    assert failure.value.returncode == 3
    assert json.loads(run.record_path.read_text())["lifecycle"] == "running"


def test_stop_powers_off_then_marks_stopped_when_the_exact_process_disappears(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch QMP use before a graceful poweroff has a chance to complete."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123\n", encoding="ascii")
    cmdlines = iter((_matching_cmdline(run), None))
    calls: list[tuple[list[str], dict[str, object]]] = []

    stopped = stop_run(
        run,
        graceful_timeout_seconds=5,
        run_process=lambda argv, **kwargs: (
            calls.append((argv, kwargs)) or CompletedProcess(argv, 0)
        ),
        list_proc_pids=lambda: [123],
        read_proc_cmdline=lambda pid: next(cmdlines),
        qmp_quit=lambda path, timeout: pytest.fail("QMP must not run"),
    )

    assert calls == [(
        build_ssh_command(run, ["sudo", "-n", "poweroff"]),
        {"capture_output": True, "check": False, "timeout": 5.0},
    )]
    assert calls[0][0][-1] == "sudo -n poweroff"
    assert stopped.lifecycle == "stopped"
    assert run.record_path.read_text(encoding="utf-8") == stopped.to_json()


def test_stop_recovers_live_exact_process_when_pid_file_is_deleted(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Scan exact process identity before SSH when QEMU's PID file is lost."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    events: list[str] = []
    reads = iter((_matching_cmdline(run), None))

    def list_pids() -> list[int]:
        events.append("scan")
        return [4312]

    def read_cmdline(pid: int) -> bytes | None:
        assert pid == 4312
        return next(reads)

    stopped = stop_run(
        run,
        run_process=lambda argv, **kwargs: (
            events.append("ssh") or CompletedProcess(argv, 0)
        ),
        list_proc_pids=list_pids,
        read_proc_cmdline=read_cmdline,
        qmp_quit=lambda *args: pytest.fail("QMP must not run"),
    )

    assert events[:2] == ["scan", "ssh"]
    assert stopped.lifecycle == "stopped"


def test_stop_missing_pid_with_no_exact_match_records_verified_absence(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Treat a lost PID as absent only after the exact scan finds no match."""
    run = _launched(prepared_run, identity_file, lifecycle="launching")
    stopped = stop_run(
        run,
        run_process=lambda *args, **kwargs: pytest.fail("SSH must not run"),
        list_proc_pids=lambda: [88],
        read_proc_cmdline=lambda pid: b"qemu-system-x86_64\0-name\0other-vm\0",
        qmp_quit=lambda *args: pytest.fail("QMP must not run"),
    )

    assert stopped.lifecycle == "stopped"


def test_stop_fails_closed_when_exact_scan_finds_multiple_processes(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Refuse SSH and QMP when VM identity is not unique in procfs."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    with pytest.raises(RuntimeError, match="multiple"):
        stop_run(
            run,
            run_process=lambda *args, **kwargs: pytest.fail("SSH must not run"),
            list_proc_pids=lambda: [4312, 4313],
            read_proc_cmdline=lambda pid: _matching_cmdline(run),
            qmp_quit=lambda *args: pytest.fail("QMP must not run"),
        )

    assert json.loads(run.record_path.read_text())["lifecycle"] == "ready"


def test_stop_recovers_from_reused_recorded_pid_without_targeting_it(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Use the sole exact scan match instead of an unrelated reused PID."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123\n", encoding="ascii")
    events: list[object] = []
    exact_live = True

    def read_cmdline(pid: int) -> bytes | None:
        nonlocal exact_live
        events.append(("read", pid))
        if pid == 123:
            return b"unrelated-process\0"
        if exact_live:
            exact_live = False
            return _matching_cmdline(run)
        return None

    stopped = stop_run(
        run,
        run_process=lambda argv, **kwargs: (
            events.append("ssh") or CompletedProcess(argv, 0)
        ),
        list_proc_pids=lambda: [123, 456],
        read_proc_cmdline=read_cmdline,
        qmp_quit=lambda *args: pytest.fail("QMP must not run"),
    )

    assert "ssh" in events
    assert events.index(("read", 123)) < events.index("ssh")
    assert stopped.lifecycle == "stopped"


def test_stop_exact_scan_requires_qemu_executable_as_well_as_name_and_overlay(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Do not authorize control from name and overlay arguments alone."""
    run = _launched(prepared_run, identity_file, lifecycle="launching")
    impostor = _matching_cmdline(run).replace(
        b"qemu-system-x86_64\0", b"not-qemu-system-x86_64\0", 1
    )
    stopped = stop_run(
        run,
        run_process=lambda *args, **kwargs: pytest.fail("SSH must not run"),
        list_proc_pids=lambda: [4312],
        read_proc_cmdline=lambda pid: impostor,
        qmp_quit=lambda *args: pytest.fail("QMP must not run"),
    )

    assert stopped.lifecycle == "stopped"


def test_stop_uses_qmp_only_after_the_graceful_deadline_then_waits_again(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch early QMP fallback or no bounded post-QMP disappearance check."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123", encoding="ascii")
    clock = _Clock()
    events: list[str] = []
    qmp_sent = False

    def read_cmdline(pid: int) -> bytes | None:
        assert pid == 123
        if qmp_sent and clock.now >= 4:
            return None
        return _matching_cmdline(run)

    def qmp_quit(path: Path, timeout: float) -> None:
        nonlocal qmp_sent
        assert path == run.qmp_socket
        assert timeout == 4
        events.append("qmp")
        qmp_sent = True

    stopped = stop_run(
        run,
        graceful_timeout_seconds=4,
        poll_interval_seconds=2,
        run_process=lambda argv, **kwargs: (
            events.append("ssh") or CompletedProcess(argv, 0)
        ),
        monotonic=clock.monotonic,
        sleep=lambda seconds: (events.append(f"sleep:{seconds}"), clock.sleep(seconds))[1],
        list_proc_pids=lambda: [123],
        read_proc_cmdline=read_cmdline,
        qmp_quit=qmp_quit,
    )

    assert events == ["ssh", "sleep:2.0", "sleep:2.0", "qmp"]
    assert stopped.lifecycle == "stopped"


@pytest.mark.parametrize("pid_text", ["", "not-a-pid", "0", "-2", "12 13"])
def test_stop_recovers_from_malformed_pid_when_exact_scan_finds_none(
    prepared_run: RunRecord, identity_file: Path, pid_text: str
) -> None:
    """Ignore malformed numeric authority and require exact-scan absence."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text(pid_text, encoding="ascii")
    events: list[str] = []

    stopped = stop_run(
        run,
        run_process=lambda argv, **kwargs: events.append("ssh"),
        list_proc_pids=lambda: [],
        read_proc_cmdline=lambda pid: pytest.fail("empty scan must not read a PID"),
        qmp_quit=lambda path, timeout: events.append("qmp"),
    )

    assert events == []
    assert stopped.lifecycle == "stopped"


def test_stop_rejects_fifo_pid_without_path_read_or_blocking(
    prepared_run: RunRecord,
    identity_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a FIFO through descriptor metadata before any potentially blocking read."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    os.mkfifo(run.pid_file)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("PID reading must not use Path.read_text"),
    )

    with pytest.raises(RuntimeError, match="PID"):
        stop_run(
            run,
            run_process=lambda argv, **kwargs: pytest.fail("SSH must not run"),
            read_proc_cmdline=lambda pid: pytest.fail("FIFO must not select a PID"),
            qmp_quit=lambda path, timeout: pytest.fail("QMP must not run"),
        )


def test_stop_rejects_regular_qmp_path_before_connecting(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Do not connect QMP to a substituted regular filesystem object."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123", encoding="ascii")
    run.qmp_socket.write_text("not a socket", encoding="utf-8")
    clock = _Clock()

    with pytest.raises(RuntimeError, match="QMP.*socket"):
        stop_run(
            run,
            graceful_timeout_seconds=1,
            poll_interval_seconds=1,
            run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            read_proc_cmdline=lambda pid: _matching_cmdline(run),
        )


@pytest.mark.parametrize("defect", ["group-readable", "symlink"])
def test_stop_validates_recorded_identity_before_missing_pid_shortcut(
    prepared_run: RunRecord, identity_file: Path, tmp_path: Path, defect: str
) -> None:
    """A missing PID must not bypass Task 4's private-key authority validation."""
    identity = identity_file
    if defect == "group-readable":
        identity.chmod(0o640)
    else:
        link = tmp_path / "identity-link"
        link.symlink_to(identity)
        identity = link
    run = _launched(prepared_run, identity, lifecycle="launch-failed")

    with pytest.raises(ValueError, match="identity"):
        stop_run(
            run,
            run_process=lambda argv, **kwargs: pytest.fail("SSH must not run"),
            read_proc_cmdline=lambda pid: pytest.fail("missing PID must short-circuit"),
            qmp_quit=lambda path, timeout: pytest.fail("QMP must not run"),
        )


def test_stop_does_not_target_inexact_recorded_pid_and_verifies_scan_absence(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch substring matches authorizing shutdown of a different process."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123", encoding="ascii")
    events: list[str] = []
    wrong = (
        b"qemu-system-x86_64\0-name\0isolated-lm-run-42-evil\0-drive\0file="
        + str(run.overlay).encode()
        + b".bak,if=virtio\0"
    )

    stopped = stop_run(
        run,
        run_process=lambda argv, **kwargs: events.append("ssh"),
        list_proc_pids=lambda: [123],
        read_proc_cmdline=lambda pid: wrong,
        qmp_quit=lambda path, timeout: events.append("qmp"),
    )

    assert events == []
    assert stopped.lifecycle == "stopped"


def test_stop_is_idempotent_when_the_recorded_process_is_already_absent(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch stale PID cleanup attempting to control an absent process."""
    run = _launched(prepared_run, identity_file, lifecycle="launch-failed")
    run.pid_file.write_text("123", encoding="ascii")

    stopped = stop_run(
        run,
        run_process=lambda argv, **kwargs: pytest.fail("SSH must not run"),
        read_proc_cmdline=lambda pid: None,
        qmp_quit=lambda path, timeout: pytest.fail("QMP must not run"),
    )

    assert stopped.lifecycle == "stopped"


def test_stop_timeout_leaves_lifecycle_unchanged(
    prepared_run: RunRecord, identity_file: Path
) -> None:
    """Catch shutdown being recorded complete while the exact QEMU process remains."""
    run = _launched(prepared_run, identity_file, lifecycle="ready")
    run.pid_file.write_text("123", encoding="ascii")
    clock = _Clock()

    with pytest.raises(TimeoutError, match="QEMU"):
        stop_run(
            run,
            graceful_timeout_seconds=2,
            poll_interval_seconds=1,
            run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            list_proc_pids=lambda: [123],
            read_proc_cmdline=lambda pid: _matching_cmdline(run),
            qmp_quit=lambda path, timeout: None,
        )

    assert clock.now == 4
    assert json.loads(run.record_path.read_text())["lifecycle"] == "ready"


class _FakeQmpSocket:
    """Offline stream-socket double with explicit fragmented receive frames."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        clock: _Clock | None = None,
        operation_seconds: float = 0,
    ) -> None:
        self.chunks = iter(chunks)
        self.clock = clock
        self.operation_seconds = operation_seconds
        self.timeouts: list[float] = []
        self.sent: list[bytes] = []
        self.connected: list[str] = []
        self.recv_count = 0

    def __enter__(self) -> "_FakeQmpSocket":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def _advance(self) -> None:
        if self.clock is not None:
            self.clock.now += self.operation_seconds

    def settimeout(self, seconds: float) -> None:
        self.timeouts.append(seconds)

    def connect(self, path: str) -> None:
        self.connected.append(path)
        self._advance()

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)
        self._advance()

    def recv(self, size: int) -> bytes:
        assert size == 4096
        self.recv_count += 1
        chunk = next(self.chunks)
        self._advance()
        return chunk


def test_qmp_quit_buffers_fragments_ignores_async_frames_and_matches_reply_ids(
    tmp_path: Path,
) -> None:
    """Catch fragment parsing, lost coalesced frames, or accepting the wrong reply."""
    qmp_socket = tmp_path / "qmp.sock"
    fake = _FakeQmpSocket(
        [
            b'{"QM',
            b'P":{"version":{"qemu":{"major":9}}}}\r\n',
            (
                b'{"event":"RESET"}\r\n'
                b'{"return":{},"id":"other"}\r\n'
                b'{"return":{},"id":"isolated-lm-capabilities"}\r\n'
            ),
            (
                b'{"event":"STOP"}\r\n'
                b'{"return":{},"id":"isolated-lm-quit"}\r\n'
            ),
        ]
    )

    qemu_module._send_qmp_quit(
        qmp_socket,
        10,
        socket_factory=lambda family, kind: fake,
        monotonic=lambda: 0,
        path_lstat=_unix_socket_metadata,
    )

    assert fake.connected == [str(qmp_socket)]
    assert fake.recv_count == 4
    assert fake.sent == [
        b'{"execute":"qmp_capabilities","id":"isolated-lm-capabilities"}\r\n',
        b'{"execute":"quit","id":"isolated-lm-quit"}\r\n',
    ]
    assert fake.timeouts and all(0 < timeout <= 10 for timeout in fake.timeouts)


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([b"not-json\r\n"], "malformed"),
        ([b""], "closed"),
    ],
)
def test_qmp_quit_rejects_malformed_frames_and_eof(
    tmp_path: Path, chunks: list[bytes], message: str
) -> None:
    """Catch invalid or truncated QMP input being treated as a valid handshake."""
    fake = _FakeQmpSocket(chunks)

    with pytest.raises(RuntimeError, match=message):
        qemu_module._send_qmp_quit(
            tmp_path / "qmp.sock",
            10,
            socket_factory=lambda family, kind: fake,
            monotonic=lambda: 0,
            path_lstat=_unix_socket_metadata,
        )

    assert fake.sent == []


def test_qmp_quit_uses_one_deadline_across_fragmented_blocking_operations(
    tmp_path: Path,
) -> None:
    """Catch each connect/recv/send resetting the full QMP timeout budget."""
    clock = _Clock()
    fake = _FakeQmpSocket(
        [b'{"QM'],
        clock=clock,
        operation_seconds=3,
    )

    with pytest.raises(TimeoutError, match="QMP"):
        qemu_module._send_qmp_quit(
            tmp_path / "qmp.sock",
            5,
            socket_factory=lambda family, kind: fake,
            monotonic=clock.monotonic,
            path_lstat=_unix_socket_metadata,
        )

    assert fake.connected == [str(tmp_path / "qmp.sock")]
    assert fake.recv_count == 1
    assert fake.timeouts == [5, 2]


def test_qmp_quit_rejects_a_buffered_reply_after_the_overall_deadline(
    tmp_path: Path,
) -> None:
    """Catch buffered frames bypassing a deadline exhausted by the preceding send."""
    clock = _Clock()
    fake = _FakeQmpSocket(
        [
            (
                b'{"QMP":{"version":{}}}\r\n'
                b'{"return":{},"id":"isolated-lm-capabilities"}\r\n'
                b'{"return":{},"id":"isolated-lm-quit"}\r\n'
            )
        ],
        clock=clock,
        operation_seconds=1,
    )

    with pytest.raises(TimeoutError, match="QMP"):
        qemu_module._send_qmp_quit(
            tmp_path / "qmp.sock",
            3.5,
            socket_factory=lambda family, kind: fake,
            monotonic=clock.monotonic,
            path_lstat=_unix_socket_metadata,
        )

    assert fake.sent == [
        b'{"execute":"qmp_capabilities","id":"isolated-lm-capabilities"}\r\n',
        b'{"execute":"quit","id":"isolated-lm-quit"}\r\n',
    ]
    assert clock.now == 4
