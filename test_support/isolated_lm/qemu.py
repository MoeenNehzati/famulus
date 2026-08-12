"""Shell-free QEMU launch, bounded readiness, and identity-safe shutdown."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import time
from typing import Any

from test_support.isolated_lm.model import RunRecord


READINESS_TIMEOUT_SECONDS = 600.0
READINESS_POLL_SECONDS = 2.0
LAUNCH_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 60.0
SHUTDOWN_POLL_SECONDS = 1.0

RunProcess = Callable[..., subprocess.CompletedProcess[object]]
ProcCmdlineReader = Callable[[int], bytes | None]
ProcPidLister = Callable[[], Iterable[int]]
QmpQuitter = Callable[[Path, float], None]
RunDirectoryIdentity = tuple[int, int]

_QMP_CAPABILITIES_ID = "isolated-lm-capabilities"
_QMP_QUIT_ID = "isolated-lm-quit"


def _open_absolute_directory(
    path: Path, *, open_file: Callable[..., int] = os.open
) -> int:
    """Open an absolute directory one no-follow component at a time.

    Intent
    ------
    Establish directory authority from the filesystem root without allowing a
    symlink, nondirectory, or lexical traversal component anywhere in the path.

    Rationale
    ---------
    ``O_NOFOLLOW`` protects only the component named by one open call. Starting
    at ``/`` and resolving every child relative to the prior descriptor extends
    that protection to all ancestors while closing superseded descriptors.

    Pseudocode
    ----------
    - if path is not absolute or contains dot traversal:
      - raise invalid absolute directory
    - set current_descriptor = no-follow directory open for filesystem root
    - for component in nonempty path components:
      - set child_descriptor = relative no-follow directory open
      - if child_descriptor metadata is not a directory:
        - raise invalid directory component
      - set current_descriptor = closed
      - set current_descriptor = child_descriptor
    - return final directory descriptor to caller ownership

    Wraps
    -----
    none
    """
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("directory path must be absolute")
    components = path.parts[1:]
    if any(component in {".", ".."} for component in components):
        raise ValueError("directory path must not contain dot traversal")
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    current_fd = -1
    try:
        current_fd = open_file("/", flags)
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise ValueError("filesystem root must be a directory")
        for component in components:
            if not component:
                continue
            child_fd = open_file(component, flags, dir_fd=current_fd)
            try:
                if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                    raise ValueError("directory path component must be a directory")
            except BaseException:
                os.close(child_fd)
                raise
            os.close(current_fd)
            current_fd = child_fd
        descriptor = current_fd
        current_fd = -1
        return descriptor
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def allocate_loopback_port(
    *, socket_factory: Callable[..., Any] = socket.socket
) -> int:
    """Return a briefly reserved IPv4 loopback port for QEMU forwarding.

    Intent
    ------
    Ask the kernel for an available loopback TCP port and release the probe
    socket before QEMU claims that port.

    Rationale
    ---------
    Binding only IPv4 loopback preserves the inbound network boundary, while
    closing before return makes the unavoidable allocation race explicit.

    Pseudocode
    ----------
    - set listener = IPv4 loopback socket bound to port zero
    - set port = listener assigned port
    - if port is outside the TCP range:
      - raise invalid allocated port
    - return port

    Wraps
    -----
    none
    """
    with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RuntimeError("loopback allocator returned an invalid port")
    return port


def build_qemu_command(run: RunRecord, ssh_port: int) -> list[str]:
    """Build the complete fixed QEMU argv without a host shell.

    Intent
    ------
    Encode the supported KVM machine, fixed devices, dedicated artifacts, and
    sole loopback SSH forward into one argument vector.

    Rationale
    ---------
    Constructing every option centrally prevents callers from injecting extra
    QEMU suboptions or widening guest networking and filesystem authority.

    Pseudocode
    ----------
    - if ssh_port is outside the TCP range:
      - raise invalid SSH port
    - vm_name = _qemu_comma_safe(run name)
    - overlay = _qemu_comma_safe(overlay path)
    - seed_iso = _qemu_comma_safe(seed path)
    - qmp_socket = _qemu_comma_safe(QMP path)
    - return fixed QEMU argument vector

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._qemu_comma_safe:
      why:
        transforms: "Returns each value only after excluding QEMU comma-delimited option injection."
    """
    if not isinstance(ssh_port, int) or isinstance(ssh_port, bool) or not 1 <= ssh_port <= 65535:
        raise ValueError("SSH port must be an integer in 1..65535")
    vm_name = _qemu_comma_safe("VM name", f"isolated-lm-{run.run_id}")
    overlay = _qemu_comma_safe("overlay path", str(run.overlay))
    seed_iso = _qemu_comma_safe("seed ISO path", str(run.seed_iso))
    qmp_socket = _qemu_comma_safe("QMP socket path", str(run.qmp_socket))
    return [
        "qemu-system-x86_64",
        "-name", vm_name,
        "-machine", "q35,accel=kvm",
        "-cpu", "host",
        "-smp", str(run.resources.vcpus),
        "-m", str(run.resources.memory_mib),
        "-drive", f"file={overlay},if=virtio,format=qcow2",
        "-drive", f"file={seed_iso},if=virtio,format=raw,readonly=on",
        "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-display", "none",
        "-serial", f"file:{run.serial_log}",
        "-qmp", f"unix:{qmp_socket},server=on,wait=off",
        "-pidfile", str(run.pid_file),
        "-daemonize",
    ]


def _qemu_comma_safe(label: str, value: str) -> str:
    """Reject data QEMU would split as another comma-delimited suboption.

    Intent
    ------
    Preserve one validated string as a single QEMU suboption value.

    Rationale
    ---------
    Several QEMU arguments parse commas internally, so an embedded comma could
    add semantics beyond the fixed command builder's reviewed surface.

    Pseudocode
    ----------
    - if value contains a comma:
      - raise unsafe QEMU value
    - return value

    Wraps
    -----
    none
    """
    if "," in value:
        raise ValueError(f"{label} must not contain a comma")
    return value


def validate_identity_file(identity_file: Path) -> Path:
    """Return an explicit resolved owner-only private-key path.

    Intent
    ------
    Convert an operator-supplied identity path into one canonical regular-file
    authority with no symlink or group/other access.

    Rationale
    ---------
    SSH control must not follow a redirectable key path or expose private-key
    material through permissive filesystem mode bits.

    Pseudocode
    ----------
    - if identity path is not absolute and non-symlink:
      - raise invalid identity path
    - set resolved_identity = strict path resolution
    - if resolved_identity is not the supplied regular file:
      - raise noncanonical identity path
    - if identity permissions expose group or others:
      - raise unsafe identity permissions
    - return resolved_identity

    Wraps
    -----
    none
    """
    if not isinstance(identity_file, Path):
        raise ValueError("identity file must be an explicit path")
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


def _validate_run_artifacts(
    run: RunRecord,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
) -> None:
    """Revalidate run identity and artifacts through a no-follow directory fd.

    Intent
    ------
    Reopen the selected run directory component by component, optionally bind
    it to the device/inode captured at manifest load, and verify every fixed
    artifact name and filesystem type relative to the final descriptor.

    Rationale
    ---------
    Artifact types alone cannot distinguish a replacement real directory. The
    optional loaded identity detects that substitution; callers without it
    validate only the directory currently occupying the canonical path.

    Pseudocode
    ----------
    - if run directory path is not absolute and lexical:
      - raise invalid run directory
    - set run_descriptor = component-wise no-follow directory open
    - if expected identity exists and device or inode differs:
      - raise replaced run directory
    - for fixed_artifact in run record:
      - if artifact path or relative metadata is invalid:
        - raise invalid run artifact
    - for runtime_artifact in optional PID and QMP artifacts:
      - if present artifact has the wrong type:
        - raise invalid runtime artifact
    - return none

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._open_absolute_directory:
      why:
        constructs: "Returns the component-wise no-follow descriptor used for identity and artifact checks."
    """
    run_dir = run.run_dir
    if not run_dir.is_absolute() or ".." in run_dir.parts:
        raise ValueError("run directory must be a resolved absolute directory")
    try:
        run_fd = _open_absolute_directory(run_dir)
    except (OSError, ValueError) as error:
        raise ValueError("run directory must be a real non-symlink directory") from error
    try:
        run_metadata = os.fstat(run_fd)
        if not stat.S_ISDIR(run_metadata.st_mode):
            raise ValueError("run directory must be a real non-symlink directory")
        actual_identity = (run_metadata.st_dev, run_metadata.st_ino)
        if (
            expected_run_dir_identity is not None
            and actual_identity != expected_run_dir_identity
        ):
            raise ValueError("run directory changed since manifest load")
        expected = {
            "overlay": (run.overlay, "overlay.qcow2"),
            "seed ISO": (run.seed_iso, "seed.iso"),
            "known-hosts file": (run.known_hosts, "known_hosts"),
            "serial log": (run.serial_log, "serial.log"),
            "run record": (run.record_path, "run.json"),
        }
        for label, (path, name) in expected.items():
            if not path.is_absolute() or path.parent != run_dir or path.name != name:
                raise ValueError(f"{label} does not use its dedicated run path")
            try:
                metadata = os.stat(name, dir_fd=run_fd, follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"{label} is missing or unreadable") from error
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"{label} must be a regular non-symlink run artifact"
                )
        for label, path, name, predicate in (
            ("PID file", run.pid_file, "qemu.pid", stat.S_ISREG),
            ("QMP socket", run.qmp_socket, "qmp.sock", stat.S_ISSOCK),
        ):
            if not path.is_absolute() or path.parent != run_dir or path.name != name:
                raise ValueError(f"{label} does not use its dedicated run path")
            try:
                metadata = os.stat(name, dir_fd=run_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise RuntimeError(f"{label} is unreadable") from error
            if not predicate(metadata.st_mode):
                expected_type = "regular file" if label == "PID file" else "Unix socket"
                raise RuntimeError(f"{label} must be a {expected_type}")
    finally:
        os.close(run_fd)


def _validate_prepared_run(
    run: RunRecord,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
) -> None:
    """Fail closed before launch if the Task 3 artifact contract has drifted.

    Intent
    ------
    Admit only an untouched prepared record whose fixed artifacts remain valid
    and whose launch-owned PID, QMP, port, key, and command facts are unused.

    Rationale
    ---------
    Launch must not reuse stale runtime control objects or overwrite authority
    already associated with a prior QEMU process.

    Pseudocode
    ----------
    - if lifecycle is not prepared:
      - raise invalid launch lifecycle
    - @_qemu_comma_safe(run-controlled QEMU values)
    - @_validate_run_artifacts(run and loaded identity)
    - for control_path in PID and QMP paths:
      - if control path is occupied or noncanonical:
        - raise reused runtime control path
    - if launch facts already exist:
      - raise stale launch facts
    - return none

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._qemu_comma_safe:
      why:
        validates: "Rejects prepared record values that could inject QEMU suboptions."
    ._validate_run_artifacts:
      why:
        validates: "Rechecks fixed run artifacts immediately before launch authority is persisted."
    """
    if run.lifecycle != "prepared":
        raise ValueError("start_run requires lifecycle prepared")
    _qemu_comma_safe("VM name", f"isolated-lm-{run.run_id}")
    _qemu_comma_safe("overlay path", str(run.overlay))
    _qemu_comma_safe("seed ISO path", str(run.seed_iso))
    _qemu_comma_safe("QMP socket path", str(run.qmp_socket))
    _validate_run_artifacts(run, expected_run_dir_identity)
    run_dir = run.run_dir
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


def build_ssh_command(
    run: RunRecord,
    remote_argv: Sequence[str],
    *,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
) -> list[str]:
    """Build one OpenSSH argv with isolated host/key state and no local shell.

    Intent
    ------
    Bind SSH to the recorded loopback port, private identity, dedicated
    known-hosts file, fixed guest user, and a quoted remote argument vector.

    Rationale
    ---------
    OpenSSH necessarily hands its command to the guest login shell; shlex
    quoting preserves argument boundaries without introducing a local shell.

    Pseudocode
    ----------
    - if SSH port or recorded identity is absent:
      - raise incomplete SSH authority
    - @_validate_run_artifacts(run and optional loaded directory identity)
    - identity = validate_identity_file(recorded identity)
    - if remote argument vector is empty or non-string:
      - raise invalid remote arguments
    - set remote_command = POSIX-quoted remote arguments
    - return fixed OpenSSH argument vector

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._validate_run_artifacts:
      why:
        validates: "Rechecks the selected run artifacts before SSH receives their paths."

    InstantiationsFromRepo
    ----------------------
    .validate_identity_file:
      why:
        transforms: "Returns the canonical private identity path placed in the SSH vector."
    """
    if run.ssh_port is None or not 1 <= run.ssh_port <= 65535:
        raise ValueError("run has no valid SSH port")
    if run.identity_file is None:
        raise ValueError("run has no identity file")
    _validate_run_artifacts(run, expected_run_dir_identity)
    identity = validate_identity_file(run.identity_file)
    if not remote_argv or any(not isinstance(argument, str) for argument in remote_argv):
        raise ValueError("remote argv must contain at least one string argument")
    remote_command = shlex.join(remote_argv)
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
        remote_command,
    ]


def start_run(
    run: RunRecord,
    identity_file: Path,
    *,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
    allocate_port: Callable[[], int] = allocate_loopback_port,
    run_process: RunProcess = subprocess.run,
    launch_timeout_seconds: float = LAUNCH_TIMEOUT_SECONDS,
    list_proc_pids: ProcPidLister | None = None,
    read_proc_cmdline: ProcCmdlineReader | None = None,
) -> RunRecord:
    """Persist launch authority, start QEMU, and record the truthful outcome.

    Intent
    ------
    Durably bind a prepared run to its canonical key, allocated port, and exact
    QEMU command before invoking the captured child-process boundary.

    Rationale
    ---------
    Recording authority first makes crashes diagnosable, while explicit
    launch-failed and running transitions keep the manifest truthful.

    Pseudocode
    ----------
    - @_validate_prepared_run(run and optional loaded directory identity)
    - identity = validate_identity_file(identity_file)
    - set ssh_port = allocated loopback port
    - command = build_qemu_command(run and ssh_port)
    - set launch_record = prepared record with launch facts
    - set launch_record = durably written
    - set completed = captured QEMU invocation
    - if completed status is nonzero:
      - raise captured process failure after launch-failed write
    - return running record after durable write

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._validate_prepared_run:
      why:
        validates: "Checks lifecycle, fixed artifacts, and unused runtime paths before launch mutation."

    InstantiationsFromRepo
    ----------------------
    .validate_identity_file:
      why:
        transforms: "Returns the canonical owner-only key path persisted as launch authority."
    .build_qemu_command:
      why:
        constructs: "Returns the exact QEMU vector persisted before the child process starts."
    ._positive_duration:
      why:
        transforms: "Returns the finite positive QEMU launch deadline duration."
    ._find_unique_exact_process:
      why:
        transforms: "Returns the sole exact VM after an uncertain or failed launcher result."
    """
    _validate_prepared_run(run, expected_run_dir_identity)
    identity = validate_identity_file(identity_file)
    ssh_port = allocate_port()
    command = tuple(build_qemu_command(run, ssh_port))
    launch_timeout = _positive_duration(launch_timeout_seconds, "QEMU launch timeout")
    launch_record = replace(
        run,
        lifecycle="launching",
        ssh_port=ssh_port,
        identity_file=identity,
        qemu_command=command,
    )
    launch_record.write_atomic()
    pid_lister = list_proc_pids or _list_proc_pids
    cmdline_reader = read_proc_cmdline or _read_proc_cmdline
    try:
        completed = run_process(
            list(command),
            check=False,
            capture_output=True,
            timeout=launch_timeout,
        )
    except Exception as error:
        try:
            exact_pid = _find_unique_exact_process(
                launch_record,
                list_proc_pids=pid_lister,
                read_proc_cmdline=cmdline_reader,
            )
        except RuntimeError as scan_error:
            raise RuntimeError(
                "QEMU launch outcome is uncertain and exact process recovery failed"
            ) from scan_error
        if exact_pid is None:
            replace(launch_record, lifecycle="launch-failed").write_atomic()
        raise
    if completed.returncode != 0:
        exact_pid = _find_unique_exact_process(
            launch_record,
            list_proc_pids=pid_lister,
            read_proc_cmdline=cmdline_reader,
        )
        if exact_pid is None:
            replace(launch_record, lifecycle="launch-failed").write_atomic()
        raise subprocess.CalledProcessError(
            completed.returncode,
            list(command),
            output=completed.stdout,
            stderr=completed.stderr,
        )
    running = replace(launch_record, lifecycle="running")
    running.write_atomic()
    return running


def _positive_duration(value: float, label: str, *, allow_zero: bool = False) -> float:
    """Validate a finite lifecycle duration before constructing a deadline.

    Intent
    ------
    Normalize numeric timeout input to a finite float satisfying the caller's
    positive or nonnegative policy.

    Rationale
    ---------
    Invalid, infinite, or boolean durations can disable lifecycle bounds or
    produce deadline behavior unrelated to the operator's request.

    Pseudocode
    ----------
    - if duration input is not numeric:
      - raise invalid duration type
    - set duration = floating-point input
    - if duration violates sign or finiteness policy:
      - raise invalid duration range
    - return duration

    Wraps
    -----
    none
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not (value >= 0 if allow_zero else value > 0) or value == float("inf"):
        raise ValueError(f"{label} must be {'nonnegative' if allow_zero else 'positive'}")
    return value


def wait_for_ssh(
    run: RunRecord,
    *,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
    timeout_seconds: float = READINESS_TIMEOUT_SECONDS,
    poll_interval_seconds: float = READINESS_POLL_SECONDS,
    run_process: RunProcess = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunRecord:
    """Wait within one deadline for SSH and then cloud-init completion.

    Intent
    ------
    Prove guest SSH reachability and cloud-init completion inside one monotonic
    budget before publishing lifecycle ready.

    Rationale
    ---------
    Independent full timeouts could exceed the operator bound, and capturing
    every probe keeps SSH diagnostics out of the CLI JSON stream.

    Pseudocode
    ----------
    - if lifecycle is not running:
      - raise invalid readiness lifecycle
    - timeout = _positive_duration(timeout input)
    - poll = _positive_duration(poll input)
    - probe_command = build_ssh_command(run, true, and loaded directory identity)
    - while probe status is nonzero and deadline remains:
      - set completed = captured bounded SSH probe
    - cloud_init_command = build_ssh_command(run, cloud-init wait, and loaded directory identity)
    - set completed = captured command within remaining deadline
    - if completed status is nonzero:
      - raise captured cloud-init failure
    - return ready record after durable write

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._positive_duration:
      why:
        transforms: "Returns finite readiness timeout and polling durations used by one deadline."
    .build_ssh_command:
      why:
        constructs: "Returns isolated SSH vectors for the reachability probe and cloud-init wait."
    """
    if run.lifecycle != "running":
        raise ValueError("wait_for_ssh requires lifecycle running")
    timeout = _positive_duration(timeout_seconds, "readiness timeout")
    poll = _positive_duration(poll_interval_seconds, "readiness poll interval")
    deadline = monotonic() + timeout
    probe_command = build_ssh_command(
        run,
        ["true"],
        expected_run_dir_identity=expected_run_dir_identity,
    )
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("SSH readiness deadline expired")
        try:
            completed = run_process(
                probe_command, check=False, capture_output=True, timeout=remaining
            )
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
    cloud_init_command = build_ssh_command(
        run,
        ["cloud-init", "status", "--wait"],
        expected_run_dir_identity=expected_run_dir_identity,
    )
    try:
        completed = run_process(
            cloud_init_command, check=False, capture_output=True, timeout=remaining
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("cloud-init readiness deadline expired") from error
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            cloud_init_command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    ready = replace(run, lifecycle="ready")
    ready.write_atomic()
    return ready


def _read_proc_cmdline(pid: int) -> bytes | None:
    """Read one live Linux process argv, returning None only when it is absent.

    Intent
    ------
    Obtain the kernel's NUL-delimited argv bytes for exactly one recorded PID
    without translating process absence into an operational failure.

    Rationale
    ---------
    Shutdown authority depends on the live argv identity; an absent process is
    the idempotent completion case rather than malformed process evidence.

    Pseudocode
    ----------
    - set cmdline_path = proc entry for pid
    - if process entry is absent:
      - return none
    - return cmdline bytes

    Wraps
    -----
    none
    """
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None


def _list_proc_pids() -> tuple[int, ...]:
    """List positive numeric Linux process identifiers for exact recovery.

    Intent
    ------
    Enumerate only numeric entries directly below procfs without interpreting
    PID-file content or invoking a process-listing command.

    Rationale
    ---------
    A QEMU daemon may survive while its PID file is absent or stale. Direct
    procfs enumeration supplies the complete candidate set for argv matching.

    Pseudocode
    ----------
    - set process_ids = positive decimal names directly below procfs
    - return process_ids in numeric order

    Wraps
    -----
    none
    """
    with os.scandir("/proc") as entries:
        return tuple(
            sorted(
                int(entry.name)
                for entry in entries
                if entry.name.isascii()
                and entry.name.isdigit()
                and int(entry.name) > 0
            )
        )


def _read_recorded_pid(pid_file: Path) -> int | None:
    """Read one bounded regular no-follow PID file without FIFO/device blocking.

    Intent
    ------
    Convert an optional QEMU PID artifact into one positive integer only after
    descriptor-level type, size, encoding, and syntax checks.

    Rationale
    ---------
    Nonblocking no-follow open plus regular-file fstat prevents FIFO, device,
    symlink, and oversized inputs from blocking or redirecting shutdown.

    Pseudocode
    ----------
    - set descriptor = nonblocking no-follow PID file open
    - if PID file is absent:
      - return none
    - if descriptor is not a bounded regular file:
      - raise invalid PID artifact
    - set pid_text = bounded ASCII payload
    - if pid_text is not one positive decimal integer:
      - raise malformed PID
    - return parsed PID

    Wraps
    -----
    none
    """
    flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(pid_file, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError("PID file is unreadable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("PID file must be a regular file")
        if metadata.st_size > 32:
            raise RuntimeError("PID file is too large")
        payload = os.read(descriptor, 33)
        if len(payload) > 32:
            raise RuntimeError("PID file is too large")
    finally:
        os.close(descriptor)
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise RuntimeError("PID file is unreadable") from error
    stripped = text.strip()
    if not stripped.isascii() or not stripped.isdigit():
        raise RuntimeError("PID file is malformed")
    pid = int(stripped)
    if pid <= 0:
        raise RuntimeError("PID file must contain a positive PID")
    return pid


def _matches_run_process(run: RunRecord, cmdline: bytes) -> bool:
    """Require exact QEMU name and overlay facts in NUL-separated process argv.

    Intent
    ------
    Decide whether live process bytes contain both the exact dedicated VM name
    and exact overlay suboption recorded for this run.

    Rationale
    ---------
    PID reuse makes numeric identity insufficient; exact argv components avoid
    substring matches against another QEMU process or similar path.

    Pseudocode
    ----------
    - if cmdline components are not UTF-8:
      - return false
    - set name_match = exact name option and run value are present
    - set overlay_match = exact drive suboption is present
    - return name_match and overlay_match

    Wraps
    -----
    none
    """
    try:
        arguments = [part.decode("utf-8") for part in cmdline.split(b"\0") if part]
    except UnicodeDecodeError:
        return False
    if not arguments or Path(arguments[0]).name != "qemu-system-x86_64":
        return False
    expected_name = f"isolated-lm-{run.run_id}"
    has_name = any(
        arguments[index] == "-name" and arguments[index + 1] == expected_name
        for index in range(len(arguments) - 1)
    )
    expected_file = f"file={run.overlay}"
    has_overlay = any(expected_file in argument.split(",") for argument in arguments)
    return has_name and has_overlay


def _find_unique_exact_process(
    run: RunRecord,
    *,
    list_proc_pids: ProcPidLister,
    read_proc_cmdline: ProcCmdlineReader,
) -> int | None:
    """Resolve procfs candidates to zero or one exact QEMU process.

    Intent
    ------
    Recover a run's process identity from executable, VM name, and overlay argv
    facts without trusting a missing, malformed, or reused PID file.

    Rationale
    ---------
    Numeric PIDs are reusable and QEMU removes its PID file during exit. A
    complete exact scan distinguishes verified absence from one recoverable VM
    while treating duplicate exact matches as ambiguous authority.

    Pseudocode
    ----------
    - set exact_matches = empty PID collection
    - for pid in unique positive procfs PIDs:
      - set cmdline = bounded process argv read
      - if cmdline exactly matches run process identity:
        - set exact_matches = exact_matches plus pid
    - if more than one exact match exists:
      - raise ambiguous process identity
    - return sole PID or none

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._matches_run_process:
      why:
        validates: "Requires the exact QEMU executable, VM name, and overlay for every candidate."
    """
    matches: list[int] = []
    seen: set[int] = set()
    for pid in list_proc_pids():
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or pid in seen
        ):
            continue
        seen.add(pid)
        cmdline = read_proc_cmdline(pid)
        if cmdline is not None and _matches_run_process(run, cmdline):
            matches.append(pid)
            if len(matches) > 1:
                raise RuntimeError("multiple exact QEMU processes match this run")
    return matches[0] if matches else None


def _recover_run_process(
    run: RunRecord,
    *,
    list_proc_pids: ProcPidLister,
    read_proc_cmdline: ProcCmdlineReader,
) -> int | None:
    """Validate PID evidence, then resolve authority from a complete exact scan.

    Intent
    ------
    Deliberately consume the optional PID artifact while making procfs argv
    identity, rather than PID syntax or numeric reuse, the control authority.

    Rationale
    ---------
    A malformed or mismatched PID must not target an unrelated process, but it
    also must not make a surviving exact VM unrecoverable.

    Pseudocode
    ----------
    - set recorded_pid = optional PID artifact or none after malformed input
    - return @_find_unique_exact_process(run and procfs boundaries)

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._read_recorded_pid:
      why:
        reads: "Consumes optional PID evidence without granting its numeric value authority."

    InstantiationsFromRepo
    ----------------------
    ._find_unique_exact_process:
      why:
        transforms: "Returns the sole exact live VM independently of PID-file correctness."
    """
    try:
        _read_recorded_pid(run.pid_file)
    except RuntimeError:
        pass
    return _find_unique_exact_process(
        run,
        list_proc_pids=list_proc_pids,
        read_proc_cmdline=read_proc_cmdline,
    )


def _require_matching_process(
    run: RunRecord, pid: int, read_proc_cmdline: ProcCmdlineReader
) -> bool:
    """Return false for absence or reject a live PID that is not this exact VM.

    Intent
    ------
    Turn process cmdline evidence into a three-way result: absent, exact match,
    or unsafe mismatch.

    Rationale
    ---------
    Callers may treat absence as shutdown completion, but must never continue
    control operations against a live PID whose argv belongs to another process.

    Pseudocode
    ----------
    - set cmdline = process reader result for pid
    - if cmdline is absent:
      - return false
    - if not @_matches_run_process(run and cmdline):
      - raise mismatched process identity
    - return true

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._matches_run_process:
      why:
        validates: "Compares exact VM name and overlay facts against the live process argv."
    """
    cmdline = read_proc_cmdline(pid)
    if cmdline is None:
        return False
    if not _matches_run_process(run, cmdline):
        raise RuntimeError("recorded PID process identity does not match this VM")
    return True


def _wait_for_process_exit(
    run: RunRecord,
    deadline: float,
    poll: float,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    read_proc_cmdline: ProcCmdlineReader,
    list_proc_pids: ProcPidLister,
) -> bool:
    """Poll only the already-validated exact process until one bounded deadline.

    Intent
    ------
    Wait for the recorded QEMU identity to disappear without crossing the
    supplied monotonic deadline.

    Rationale
    ---------
    Revalidating identity on every poll detects PID reuse during shutdown and
    prevents a stale PID from authorizing continued waiting or control.

    Pseudocode
    ----------
    - while true:
      - if @_recover_run_process(run and procfs boundaries) is absent:
        - return true
      - set remaining = deadline minus monotonic clock
      - if remaining is nonpositive:
        - return false
      - set sleep_duration = lesser of poll and remaining

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._recover_run_process:
      why:
        validates: "Rescans exact process identity before every shutdown poll."
    """
    while True:
        if _recover_run_process(
            run,
            list_proc_pids=list_proc_pids,
            read_proc_cmdline=read_proc_cmdline,
        ) is None:
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep(min(poll, remaining))


class _QmpConnection:
    """Frame one QMP stream under a single connection-wide deadline.

    Intent
    ------
    Share one deadline and retained byte buffer across QMP connect, send, and
    newline-framed receive operations.

    Rationale
    ---------
    QMP replies may be fragmented, coalesced, or interleaved with events; shared
    state preserves frames without resetting the caller's timeout budget.

    Pseudocode
    ----------
    - set connection_state = socket deadline clock and empty receive buffer
    - return connection_state

    Wraps
    -----
    none
    """

    def __init__(
        self,
        connection: Any,
        deadline: float,
        monotonic: Callable[[], float],
    ) -> None:
        """Initialize one QMP stream wrapper around an existing Unix socket.

        Intent
        ------
        Retain the socket, absolute deadline, monotonic clock, and initially
        empty frame buffer required by later stream operations.

        Rationale
        ---------
        Keeping deadline and buffer state together prevents individual protocol
        operations from accidentally resetting time or discarding later frames.

        Pseudocode
        ----------
        - set connection = supplied socket
        - set deadline = supplied absolute deadline
        - set monotonic = supplied clock
        - set buffer = empty byte buffer

        Wraps
        -----
        none
        """
        self.connection = connection
        self.deadline = deadline
        self.monotonic = monotonic
        self.buffer = bytearray()

    def _time_left(self) -> float:
        """Return positive time left, including before consuming buffered frames.

        Intent
        ------
        Enforce the overall QMP deadline before both blocking I/O and local
        processing of already-buffered protocol frames.

        Rationale
        ---------
        A buffered terminal reply must not be accepted after an earlier socket
        operation has exhausted the lifecycle timeout.

        Pseudocode
        ----------
        - set remaining = deadline minus monotonic clock
        - if remaining is nonpositive:
          - raise QMP deadline expired
        - return remaining

        Wraps
        -----
        none
        """
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise TimeoutError("QMP overall deadline expired")
        return remaining

    def _remaining(self) -> float:
        """Set and return the positive time left before one blocking operation.

        Intent
        ------
        Apply the connection-wide remaining budget as the socket timeout for the
        next connect, send, or receive call.

        Rationale
        ---------
        Updating the socket before each operation preserves one overall bound
        while allowing fragmented protocol traffic to consume only time left.

        Pseudocode
        ----------
        - set remaining = overall positive time left
        - set socket_timeout = remaining
        - return remaining

        Wraps
        -----
        none
        """
        remaining = self._time_left()
        self.connection.settimeout(remaining)
        return remaining

    def connect(self, path: Path) -> None:
        """Connect to the QMP Unix path without resetting the overall deadline.

        Intent
        ------
        Establish the existing socket connection under the shared remaining
        timeout and normalize socket timeout exceptions.

        Rationale
        ---------
        A dedicated timeout error lets lifecycle callers distinguish deadline
        exhaustion from filesystem-type validation performed before connection.

        Pseudocode
        ----------
        - set socket_timeout = shared remaining duration
        - if Unix socket connection times out:
          - raise QMP deadline expired during connect
        - return none

        Wraps
        -----
        none
        """
        self._remaining()
        try:
            self.connection.connect(str(path))
        except (TimeoutError, socket.timeout) as error:
            raise TimeoutError("QMP overall deadline expired during connect") from error

    def send(self, payload: dict[str, object]) -> None:
        """Send one compact newline-framed request within the time remaining.

        Intent
        ------
        Serialize one QMP request as UTF-8 JSON with protocol framing and write
        it under the shared deadline.

        Rationale
        ---------
        Compact deterministic framing avoids ambiguous request boundaries while
        timeout normalization preserves the shutdown contract.

        Pseudocode
        ----------
        - set socket_timeout = shared remaining duration
        - set encoded_payload = compact JSON plus QMP newline frame
        - if sending encoded_payload times out:
          - raise QMP deadline expired during send
        - return none

        Wraps
        -----
        none
        """
        self._remaining()
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\r\n"
        try:
            self.connection.sendall(encoded)
        except (TimeoutError, socket.timeout) as error:
            raise TimeoutError("QMP overall deadline expired during send") from error

    def receive(self) -> dict[str, object]:
        """Return one complete JSON frame while retaining later buffered frames.

        Intent
        ------
        Parse the earliest nonempty newline-delimited QMP object and retain any
        coalesced later frames for subsequent calls.

        Rationale
        ---------
        Unix stream reads do not preserve message boundaries, so explicit
        buffering is required for fragmentation, coalescing, and asynchronous events.

        Pseudocode
        ----------
        - while true:
          - set remaining = overall positive time left
          - if buffer contains a complete nonempty frame:
            - return parsed JSON object
          - set socket_timeout = remaining
          - set chunk = socket receive
          - if chunk is empty or malformed:
            - raise invalid QMP response
          - set buffer = buffer plus chunk

        Wraps
        -----
        none
        """
        while True:
            self._time_left()
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                frame = bytes(self.buffer[:newline]).rstrip(b"\r")
                del self.buffer[:newline + 1]
                if not frame.strip():
                    continue
                try:
                    parsed = json.loads(frame)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError("QMP received a malformed JSON frame") from error
                if not isinstance(parsed, dict):
                    raise RuntimeError("QMP received a malformed non-object frame")
                return parsed
            self._remaining()
            try:
                chunk = self.connection.recv(4096)
            except (TimeoutError, socket.timeout) as error:
                raise TimeoutError("QMP overall deadline expired during receive") from error
            if not chunk:
                raise RuntimeError("QMP connection closed before a complete response")
            self.buffer.extend(chunk)


def _wait_for_qmp_reply(stream: _QmpConnection, request_id: str) -> None:
    """Ignore asynchronous frames until this request's terminal QMP reply.

    Intent
    ------
    Consume framed protocol objects until the response matching one request ID
    reports either a return value or an error.

    Rationale
    ---------
    QMP may interleave events and unrelated replies, so accepting the next frame
    could falsely authorize the capabilities or quit transition.

    Pseudocode
    ----------
    - while true:
      - set message = next framed QMP object
      - if message identifier differs from request_id:
        - continue
      - if message contains an error:
        - raise QMP request failure
      - if message contains a terminal payload:
        - return none

    Wraps
    -----
    none
    """
    while True:
        message = stream.receive()
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(f"QMP request {request_id} failed: {message['error']!r}")
        if "return" in message:
            return


def _send_qmp_quit(
    qmp_socket: Path,
    timeout_seconds: float,
    *,
    socket_factory: Callable[..., Any] = socket.socket,
    monotonic: Callable[[], float] = time.monotonic,
    path_lstat: Callable[[Path], os.stat_result] = lambda path: path.lstat(),
) -> None:
    """Complete QMP capabilities and quit replies under one monotonic deadline.

    Intent
    ------
    Validate the control path as a Unix socket, negotiate QMP capabilities, and
    receive the exact terminal reply for a quit request.

    Rationale
    ---------
    Type checking prevents connection to a substituted filesystem object, while
    reply IDs and one deadline prevent ambiguous or unbounded forced shutdown.

    Pseudocode
    ----------
    - if QMP path metadata is absent or not a Unix socket:
      - raise invalid QMP socket
    - timeout = _positive_duration(timeout input)
    - stream = _QmpConnection(socket deadline and clock)
    - set stream = connected to QMP path
    - while greeting is absent:
      - set greeting_frame = next QMP object
    - set stream = capabilities request sent
    - @_wait_for_qmp_reply(stream and capabilities identifier)
    - set stream = quit request sent
    - @_wait_for_qmp_reply(stream and quit identifier)
    - return none

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._wait_for_qmp_reply:
      why:
        validates: "Matches each capabilities and quit response to its exact request identifier."

    InstantiationsFromRepo
    ----------------------
    ._positive_duration:
      why:
        transforms: "Returns the finite duration used to establish the single QMP deadline."
    ._QmpConnection:
      why:
        constructs: "Constructs the buffered protocol stream carrying the shared deadline."
    """
    try:
        metadata = path_lstat(qmp_socket)
    except OSError as error:
        raise RuntimeError("QMP socket is missing or unreadable") from error
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("QMP path must be a Unix socket")
    timeout = _positive_duration(timeout_seconds, "QMP timeout")
    deadline = monotonic() + timeout
    with socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        stream = _QmpConnection(connection, deadline, monotonic)
        stream.connect(qmp_socket)
        while "QMP" not in stream.receive():
            pass
        stream.send({"execute": "qmp_capabilities", "id": _QMP_CAPABILITIES_ID})
        _wait_for_qmp_reply(stream, _QMP_CAPABILITIES_ID)
        stream.send({"execute": "quit", "id": _QMP_QUIT_ID})
        _wait_for_qmp_reply(stream, _QMP_QUIT_ID)


def stop_run(
    run: RunRecord,
    *,
    expected_run_dir_identity: RunDirectoryIdentity | None = None,
    graceful_timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
    poll_interval_seconds: float = SHUTDOWN_POLL_SECONDS,
    run_process: RunProcess = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    read_proc_cmdline: ProcCmdlineReader = _read_proc_cmdline,
    list_proc_pids: ProcPidLister = _list_proc_pids,
    qmp_quit: QmpQuitter = _send_qmp_quit,
) -> RunRecord:
    """Stop only this exact QEMU process through two bounded shutdown phases.

    Intent
    ------
    Validate all recorded authority, attempt captured guest poweroff, then use
    QMP quit only after the same process survives its graceful deadline.

    Rationale
    ---------
    PID reuse and stale artifacts make shutdown security-sensitive; every phase
    rechecks process identity and records stopped only after verified absence.

    Pseudocode
    ----------
    - if recorded identity is absent:
      - raise missing stop identity
    - @validate_identity_file(recorded identity)
    - @_validate_run_artifacts(run and optional loaded directory identity)
    - durations = _positive_duration(shutdown inputs)
    - exact_pid = _recover_run_process(run and procfs boundaries)
    - if exact_pid is absent:
      - return durably stopped record
    - poweroff_command = build_ssh_command(run and poweroff)
    - if @_wait_for_process_exit(run and graceful deadline):
      - return durably stopped record
    - set qmp_result = bounded quit request
    - if @_wait_for_process_exit(run and forced deadline):
      - return durably stopped record
    - raise shutdown deadline expired

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    .validate_identity_file:
      why:
        validates: "Revalidates the recorded private-key authority before even an absent-PID shortcut."
    ._validate_run_artifacts:
      why:
        validates: "Rechecks fixed and optional runtime artifacts at the stop lifecycle boundary."
    ._recover_run_process:
      why:
        validates: "Resolves exact process presence before SSH, QMP, and stopped persistence."
    ._wait_for_process_exit:
      why:
        validates: "Polls verified process identity within each graceful and forced deadline."

    InstantiationsFromRepo
    ----------------------
    ._positive_duration:
      why:
        transforms: "Returns finite timeout and polling values for both shutdown phases."
    ._recover_run_process:
      why:
        transforms: "Returns the sole exact live VM or verified absence after a complete scan."
    .build_ssh_command:
      why:
        constructs: "Returns the isolated guest poweroff vector executed before QMP fallback."
    """
    if run.identity_file is None:
        raise ValueError("identity file is required for stop")
    validate_identity_file(run.identity_file)
    _validate_run_artifacts(run, expected_run_dir_identity)
    timeout = _positive_duration(graceful_timeout_seconds, "shutdown timeout")
    poll = _positive_duration(poll_interval_seconds, "shutdown poll interval")
    exact_pid = _recover_run_process(
        run,
        list_proc_pids=list_proc_pids,
        read_proc_cmdline=read_proc_cmdline,
    )
    if exact_pid is None:
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped

    graceful_deadline = monotonic() + timeout
    poweroff_command = build_ssh_command(
        run,
        ["sudo", "-n", "poweroff"],
        expected_run_dir_identity=expected_run_dir_identity,
    )
    try:
        run_process(
            poweroff_command, check=False, capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        pass
    if _wait_for_process_exit(
        run,
        graceful_deadline,
        poll,
        monotonic=monotonic,
        sleep=sleep,
        read_proc_cmdline=read_proc_cmdline,
        list_proc_pids=list_proc_pids,
    ):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped

    if _recover_run_process(
        run,
        list_proc_pids=list_proc_pids,
        read_proc_cmdline=read_proc_cmdline,
    ) is None:
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped
    qmp_quit(run.qmp_socket, timeout)
    forced_deadline = monotonic() + timeout
    if _wait_for_process_exit(
        run,
        forced_deadline,
        poll,
        monotonic=monotonic,
        sleep=sleep,
        read_proc_cmdline=read_proc_cmdline,
        list_proc_pids=list_proc_pids,
    ):
        stopped = replace(run, lifecycle="stopped")
        stopped.write_atomic()
        return stopped
    raise TimeoutError("QEMU process remained after graceful and QMP shutdown deadlines")
