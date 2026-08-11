"""Shell-free QEMU launch, bounded readiness, and identity-safe shutdown."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import time
from typing import Any

from test_support.isolated_lm.model import RunRecord


READINESS_TIMEOUT_SECONDS = 600.0
READINESS_POLL_SECONDS = 2.0
SHUTDOWN_TIMEOUT_SECONDS = 60.0
SHUTDOWN_POLL_SECONDS = 1.0

RunProcess = Callable[..., subprocess.CompletedProcess[object]]
ProcCmdlineReader = Callable[[int], bytes | None]
QmpQuitter = Callable[[Path, float], None]


def allocate_loopback_port(
    *, socket_factory: Callable[..., Any] = socket.socket
) -> int:
    """Return a briefly reserved IPv4 loopback port for QEMU forwarding.

    The socket binds exactly ``127.0.0.1:0`` and is closed by the context
    manager before this function returns. This deliberately leaves the normal
    bind-to-QEMU race visible instead of retaining a listener QEMU cannot use.
    """
    with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RuntimeError("loopback allocator returned an invalid port")
    return port


def build_qemu_command(run: RunRecord, ssh_port: int) -> list[str]:
    """Build the complete fixed QEMU argv without a host shell.

    Only KVM/q35 with the host CPU, two virtio disks, one user-mode virtio NIC,
    and the run's dedicated serial, QMP, and PID artifacts are authorized.
    The sole inbound mapping binds guest SSH to the selected host-loopback port.
    """
    if not isinstance(ssh_port, int) or isinstance(ssh_port, bool) or not 1 <= ssh_port <= 65535:
        raise ValueError("SSH port must be an integer in 1..65535")
    return [
        "qemu-system-x86_64",
        "-name", f"isolated-lm-{run.run_id}",
        "-machine", "q35,accel=kvm",
        "-cpu", "host",
        "-smp", str(run.resources.vcpus),
        "-m", str(run.resources.memory_mib),
        "-drive", f"file={run.overlay},if=virtio,format=qcow2",
        "-drive", f"file={run.seed_iso},if=virtio,format=raw,readonly=on",
        "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-display", "none",
        "-serial", f"file:{run.serial_log}",
        "-qmp", f"unix:{run.qmp_socket},server=on,wait=off",
        "-pidfile", str(run.pid_file),
        "-daemonize",
    ]


def _validate_identity(identity_file: Path) -> Path:
    """Return an explicit resolved owner-only private-key path."""
    if not identity_file.is_absolute() or identity_file.is_symlink():
        raise ValueError("identity file must be an absolute non-symlink path")
    try:
        resolved = identity_file.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ValueError("identity file must exist") from error
    if resolved != identity_file or not identity_file.is_file():
        raise ValueError("identity file must be a resolved regular file")
    mode = stat.S_IMODE(identity_file.stat().st_mode)
    if mode & stat.S_IRUSR == 0 or mode & 0o077:
        raise ValueError("identity file must be owner-readable with no group/other access")
    return resolved


def _validate_existing_artifact(path: Path, label: str, run_dir: Path) -> None:
    """Require one prepared artifact to be a real file in the run directory."""
    if (
        not path.is_absolute()
        or path.parent != run_dir
        or path.is_symlink()
        or not path.is_file()
        or path.resolve() != path
    ):
        raise ValueError(f"{label} must be a real file directly in the run directory")


def _validate_prepared_run(run: RunRecord) -> None:
    """Fail closed before launch if the Task 3 artifact contract has drifted."""
    if run.lifecycle != "prepared":
        raise ValueError("start_run requires lifecycle prepared")
    run_dir = run.run_dir
    if (
        not run_dir.is_absolute()
        or run_dir.is_symlink()
        or not run_dir.is_dir()
        or run_dir.resolve() != run_dir
    ):
        raise ValueError("run directory must be a real resolved absolute directory")
    expected_existing = {
        "overlay": (run.overlay, "overlay.qcow2"),
        "seed ISO": (run.seed_iso, "seed.iso"),
        "known-hosts file": (run.known_hosts, "known_hosts"),
        "serial log": (run.serial_log, "serial.log"),
        "run record": (run.record_path, "run.json"),
    }
    for label, (path, name) in expected_existing.items():
        if path.name != name:
            raise ValueError(f"{label} does not use its dedicated run path")
        _validate_existing_artifact(path, label, run_dir)
    for label, path, name in (
        ("QMP socket", run.qmp_socket, "qmp.sock"),
        ("PID file", run.pid_file, "qemu.pid"),
    ):
        if (
            not path.is_absolute()
            or path.parent != run_dir
            or path.name != name
            or path.exists()
            or path.is_symlink()
        ):
            raise ValueError(f"{label} must be an unused dedicated run path")
    if run.ssh_port is not None or run.identity_file is not None or run.qemu_command:
        raise ValueError("prepared run already contains launch facts")


def build_ssh_command(run: RunRecord, remote_argv: Sequence[str]) -> list[str]:
    """Build one OpenSSH argv with isolated host/key state and no local shell.

    The destination is the fixed Task 3 guest user on IPv4 loopback. The
    caller's remote arguments remain individual argv elements after the
    destination, so this layer never concatenates or locally interprets them.
    """
    if run.ssh_port is None or not 1 <= run.ssh_port <= 65535:
        raise ValueError("run has no valid SSH port")
    if run.identity_file is None:
        raise ValueError("run has no identity file")
    identity = _validate_identity(run.identity_file)
    if not remote_argv or any(not isinstance(argument, str) for argument in remote_argv):
        raise ValueError("remote argv must contain at least one string argument")
    return [
        "ssh",
        "-p", str(run.ssh_port),
        "-i", str(identity),
        "-o", f"UserKnownHostsFile={run.known_hosts}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "--",
        f"{run.ssh_user}@127.0.0.1",
        *remote_argv,
    ]


def start_run(
    run: RunRecord,
    identity_file: Path,
    *,
    allocate_port: Callable[[], int] = allocate_loopback_port,
    run_process: RunProcess = subprocess.run,
) -> RunRecord:
    """Persist launch authority, start QEMU, and record the truthful outcome.

    Validation occurs before port allocation. The resolved private identity,
    allocated port, and exact QEMU argv are atomically recorded while the run
    remains ``prepared``; only a zero QEMU exit transitions it to ``running``.
    Any launch exception or nonzero exit records ``launch-failed`` while
    preserving the run directory and its logs.
    """
    _validate_prepared_run(run)
    identity = _validate_identity(identity_file)
    ssh_port = allocate_port()
    command = tuple(build_qemu_command(run, ssh_port))
    launch_record = replace(
        run,
        ssh_port=ssh_port,
        identity_file=identity,
        qemu_command=command,
    )
    launch_record.write_atomic()
    try:
        completed = run_process(list(command), check=False)
    except BaseException:
        replace(launch_record, lifecycle="launch-failed").write_atomic()
        raise
    if completed.returncode != 0:
        failed = replace(launch_record, lifecycle="launch-failed")
        failed.write_atomic()
        raise subprocess.CalledProcessError(completed.returncode, list(command))
    running = replace(launch_record, lifecycle="running")
    running.write_atomic()
    return running


def _positive_duration(value: float, label: str, *, allow_zero: bool = False) -> float:
    """Validate a finite lifecycle duration before constructing a deadline."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not (value >= 0 if allow_zero else value > 0) or value == float("inf"):
        raise ValueError(f"{label} must be {'nonnegative' if allow_zero else 'positive'}")
    return value


def wait_for_ssh(
    run: RunRecord,
    *,
    timeout_seconds: float = READINESS_TIMEOUT_SECONDS,
    poll_interval_seconds: float = READINESS_POLL_SECONDS,
    run_process: RunProcess = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunRecord:
    """Wait within one deadline for SSH and then cloud-init completion.

    SSH probes execute remote ``true`` until success. The remaining time from
    the same monotonic deadline bounds ``cloud-init status --wait``. Only both
    zero exits produce and persist ``ready``; timeout or cloud-init failure
    leaves the still-existing VM recorded as ``running``.
    """
    if run.lifecycle != "running":
        raise ValueError("wait_for_ssh requires lifecycle running")
    timeout = _positive_duration(timeout_seconds, "readiness timeout")
    poll = _positive_duration(poll_interval_seconds, "readiness poll interval")
    deadline = monotonic() + timeout
    probe_command = build_ssh_command(run, ["true"])
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("SSH readiness deadline expired")
        try:
            completed = run_process(probe_command, check=False, timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("SSH readiness deadline expired") from error
        if completed.returncode == 0:
            break
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("SSH readiness deadline expired")
        sleep(min(poll, remaining))

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("cloud-init readiness deadline expired")
    cloud_init_command = build_ssh_command(run, ["cloud-init", "status", "--wait"])
    try:
        completed = run_process(cloud_init_command, check=False, timeout=remaining)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("cloud-init readiness deadline expired") from error
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cloud_init_command)
    ready = replace(run, lifecycle="ready")
    ready.write_atomic()
    return ready


def _read_proc_cmdline(pid: int) -> bytes | None:
    """Read one live Linux process argv, returning None only when it is absent."""
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None


def _read_recorded_pid(pid_file: Path) -> int | None:
    """Read one positive decimal PID, distinguishing absence from corruption."""
    if pid_file.is_symlink():
        raise RuntimeError("PID file must not be a symlink")
    try:
        text = pid_file.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise RuntimeError("PID file is unreadable") from error
    stripped = text.strip()
    if not stripped.isascii() or not stripped.isdigit():
        raise RuntimeError("PID file is malformed")
    pid = int(stripped)
    if pid <= 0:
        raise RuntimeError("PID file must contain a positive PID")
    return pid


def _matches_run_process(run: RunRecord, cmdline: bytes) -> bool:
    """Require exact QEMU name and overlay facts in NUL-separated process argv."""
    try:
        arguments = [part.decode("utf-8") for part in cmdline.split(b"\0") if part]
    except UnicodeDecodeError:
        return False
    expected_name = f"isolated-lm-{run.run_id}"
    has_name = any(
        arguments[index] == "-name" and arguments[index + 1] == expected_name
        for index in range(len(arguments) - 1)
    )
    expected_file = f"file={run.overlay}"
    has_overlay = any(expected_file in argument.split(",") for argument in arguments)
    return has_name and has_overlay


def _require_matching_process(
    run: RunRecord, pid: int, read_proc_cmdline: ProcCmdlineReader
) -> bool:
    """Return False for absence or reject a live PID that is not this exact VM."""
    cmdline = read_proc_cmdline(pid)
    if cmdline is None:
        return False
    if not _matches_run_process(run, cmdline):
        raise RuntimeError("recorded PID process identity does not match this VM")
    return True


def _wait_for_process_exit(
    run: RunRecord,
    pid: int,
    deadline: float,
    poll: float,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    read_proc_cmdline: ProcCmdlineReader,
) -> bool:
    """Poll only the already-validated exact process until one bounded deadline."""
    while True:
        if not _require_matching_process(run, pid, read_proc_cmdline):
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep(min(poll, remaining))


def _recv_qmp_object(connection: Any) -> dict[str, object]:
    """Receive one newline-framed QMP JSON object from a bounded socket."""
    buffered = b""
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("QMP connection closed before a response")
        buffered += chunk
        lines = buffered.splitlines()
        for line in lines:
            if line.strip():
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    raise RuntimeError("QMP response is not an object")
                return parsed


def _send_qmp_quit(
    qmp_socket: Path,
    timeout_seconds: float,
    *,
    socket_factory: Callable[..., Any] = socket.socket,
) -> None:
    """Perform a bounded QMP greeting/capabilities handshake and request quit."""
    timeout = _positive_duration(timeout_seconds, "QMP timeout")
    with socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(qmp_socket))
        greeting = _recv_qmp_object(connection)
        if "QMP" not in greeting:
            raise RuntimeError("QMP greeting is missing")
        connection.sendall(b'{"execute":"qmp_capabilities"}\r\n')
        capabilities = _recv_qmp_object(connection)
        if "return" not in capabilities:
            raise RuntimeError("QMP capabilities negotiation failed")
        connection.sendall(b'{"execute":"quit"}\r\n')


def stop_run(
    run: RunRecord,
    *,
    graceful_timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
    poll_interval_seconds: float = SHUTDOWN_POLL_SECONDS,
    run_process: RunProcess = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    read_proc_cmdline: ProcCmdlineReader = _read_proc_cmdline,
    qmp_quit: QmpQuitter = _send_qmp_quit,
) -> RunRecord:
    """Stop only this exact QEMU process through two bounded shutdown phases.

    A missing PID/process is idempotently recorded ``stopped``. A live PID must
    expose the exact ``-name`` value and overlay drive path before any SSH or
    QMP action. The guest first receives ``sudo -n poweroff`` and gets one
    graceful deadline. Only if it remains does a bounded QMP ``quit`` run,
    followed by a second equal deadline. The prior lifecycle is retained if
    identity validation fails or the exact process survives both phases.
    """
    timeout = _positive_duration(graceful_timeout_seconds, "shutdown timeout")
    poll = _positive_duration(poll_interval_seconds, "shutdown poll interval")
    pid = _read_recorded_pid(run.pid_file)
    if pid is None or not _require_matching_process(run, pid, read_proc_cmdline):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped

    graceful_deadline = monotonic() + timeout
    poweroff_command = build_ssh_command(run, ["sudo", "-n", "poweroff"])
    try:
        run_process(poweroff_command, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass
    if _wait_for_process_exit(
        run,
        pid,
        graceful_deadline,
        poll,
        monotonic=monotonic,
        sleep=sleep,
        read_proc_cmdline=read_proc_cmdline,
    ):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped

    if not _require_matching_process(run, pid, read_proc_cmdline):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped
    qmp_quit(run.qmp_socket, timeout)
    forced_deadline = monotonic() + timeout
    if _wait_for_process_exit(
        run,
        pid,
        forced_deadline,
        poll,
        monotonic=monotonic,
        sleep=sleep,
        read_proc_cmdline=read_proc_cmdline,
    ):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped
    raise TimeoutError("QEMU process remained after graceful and QMP shutdown deadlines")
