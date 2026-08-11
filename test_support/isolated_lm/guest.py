"""Deterministic NoCloud seed and disposable disk preparation for isolated VMs."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from test_support.isolated_lm.image import create_overlay
from test_support.isolated_lm.model import CloudImageRecord, RunRecord, RuntimePaths, VmResources


GUEST_USER = "famulus-test"
"""The sole non-root account created inside every disposable guest."""

_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SSH_ED25519_PREFIX = "ssh-ed25519 "


def validate_run_id(run_id: str) -> str:
    """Return a closed-namespace run ID or reject it before filesystem use.

    Rationale
    ---------
    A run ID becomes a directory component, so accepting separators or dot
    segments would let a caller direct disposable artifacts outside state.

    Pseudocode
    ----------
    - require a full match of the lowercase 1--63-character run-ID grammar
    - return the unchanged ID only after that validation succeeds

    Call boundary
    -------------
    ``prepare_run`` validates this value before it creates the state root or
    any descendant, and ``render_meta_data`` uses the same guest identity.
    """
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run ID must match ^[a-z0-9][a-z0-9-]{0,62}$")
    return run_id


def _validate_public_key(public_key: str) -> str:
    """Require one non-empty Ed25519 public-key line for guest authorization."""
    if (
        not isinstance(public_key, str)
        or "\n" in public_key
        or "\r" in public_key
        or not public_key.startswith(_SSH_ED25519_PREFIX)
        or not public_key[len(_SSH_ED25519_PREFIX):].strip()
    ):
        raise ValueError("SSH public key must be one non-empty ssh-ed25519 line")
    return public_key


def render_user_data(public_key: str) -> str:
    """Render the fixed, generic cloud-init guest configuration.

    Rationale
    ---------
    Guest setup must enable only bootstrap prerequisites and one supplied
    public key; host paths, repositories, credentials, and project tooling do
    not belong in an isolated baseline.

    Pseudocode
    ----------
    - validate one Ed25519 public-key line before interpolation
    - render the approved package floor and locked non-root user contract
    - return the fixed newline-terminated NoCloud user-data text

    Call boundary
    -------------
    ``write_nocloud_seed`` persists the returned content with private file
    permissions; callers may also inspect it before preparing a run.
    """
    key = _validate_public_key(public_key)
    return "\n".join(
        (
            "#cloud-config",
            "package_update: true",
            "packages:",
            "  - openssh-server",
            "  - ca-certificates",
            "  - curl",
            "  - python3",
            "users:",
            f"  - name: {GUEST_USER}",
            "    groups: [sudo]",
            "    shell: /bin/bash",
            "    lock_passwd: true",
            "    sudo: ALL=(ALL) NOPASSWD:ALL",
            "    ssh_authorized_keys:",
            f"      - {key}",
            "ssh_pwauth: false",
            "disable_root: true",
            "",
        )
    )


def render_meta_data(run_id: str) -> str:
    """Render the two fixed NoCloud metadata fields for one validated run."""
    run_id = validate_run_id(run_id)
    identity = f"isolated-lm-{run_id}"
    return f"instance-id: {identity}\nlocal-hostname: {identity}\n"


def _occupied(path: Path) -> bool:
    """Treat regular files and dangling symlinks alike as occupied paths."""
    return path.exists() or path.is_symlink()


def _write_private_new(path: Path, content: str) -> None:
    """Create one non-reusable private text file without following a symlink."""
    with path.open("x", encoding="utf-8") as output:
        os.chmod(output.fileno(), 0o600)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def write_nocloud_seed(
    run_dir: Path,
    user_data: str,
    meta_data: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> Path:
    """Write private NoCloud inputs and generate their ISO without a shell.

    Rationale
    ---------
    The ISO generator receives exactly two controlled input files. Refusing
    reuse and deleting failed outputs prevents a partial seed from becoming a
    later run's boot configuration.

    Pseudocode
    ----------
    - require an existing real run directory and three unused artifact names
    - create mode-0600 user-data and meta-data files with exclusive creation
    - invoke cloud-localds with its exact argv and retain a private seed ISO
    - remove any artifacts made by this call if generation fails

    Call boundary
    -------------
    ``prepare_run`` calls this after the overlay exists. Tests inject the
    subprocess boundary; production invokes ``cloud-localds`` directly.
    """
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("run directory must be a real directory")
    run_dir = run_dir.resolve()
    user_path = run_dir / "user-data"
    meta_path = run_dir / "meta-data"
    seed_iso = run_dir / "seed.iso"
    if any(_occupied(path) for path in (user_path, meta_path, seed_iso)):
        raise FileExistsError("NoCloud seed artifact already exists")
    created: list[Path] = []
    try:
        _write_private_new(user_path, user_data)
        created.append(user_path)
        _write_private_new(meta_path, meta_data)
        created.append(meta_path)
        run(["cloud-localds", str(seed_iso), str(user_path), str(meta_path)], check=True)
        if not _occupied(seed_iso):
            raise RuntimeError("cloud-localds did not create seed ISO")
        if seed_iso.is_symlink() or not seed_iso.is_file():
            raise ValueError("cloud-localds created an invalid seed ISO")
        os.chmod(seed_iso, 0o600)
        return seed_iso.resolve()
    except BaseException:
        for path in (seed_iso, *reversed(created)):
            if path.is_file() or path.is_symlink():
                path.unlink()
        raise


def _prepare_runs_directory(paths: RuntimePaths) -> Path:
    """Create and validate the real state/runs directory without symlink hops."""
    root = paths.root
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("runtime root must be an absolute real directory")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("runtime root must be a real directory")
    try:
        relative = paths.runs.relative_to(root)
    except ValueError as error:
        raise ValueError("runs directory must be below runtime root") from error
    if not relative.parts or any(component in {".", ".."} for component in relative.parts):
        raise ValueError("runs directory must use real descendant components")
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"runs directory component is a symlink: {current}")
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"runs directory is not a real directory: {current}")
    resolved_root = root.resolve()
    resolved_runs = current.resolve()
    if not resolved_runs.is_relative_to(resolved_root):
        raise ValueError("runs directory resolves outside runtime root")
    return resolved_runs


def _atomic_write_private(path: Path, content: str) -> None:
    """Atomically replace one private evidence file in its existing directory."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.chmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def prepare_run(
    paths: RuntimePaths,
    image: CloudImageRecord,
    run_id: str,
    public_key: str,
    resources: VmResources,
    *,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    """Create one fully described disposable VM run below the selected state root.

    Rationale
    ---------
    Every mutable VM artifact is contained in a fresh run directory while its
    immutable record links the overlay to a previously verified image digest.

    Pseudocode
    ----------
    - validate run ID and key before any filesystem mutation
    - create one non-reusable real run directory below state/runs
    - create the verified overlay, NoCloud ISO, and private host-key file
    - atomically persist the immutable prepared RunRecord
    - remove the newly created run directory if any preparation step fails

    Call boundary
    -------------
    Task 4 consumes the returned record to construct and control QEMU. It may
    replace optional launch fields but must retain this run's artifact paths.
    """
    run_id = validate_run_id(run_id)
    public_key = _validate_public_key(public_key)
    runs = _prepare_runs_directory(paths)
    run_dir = runs / run_id
    try:
        run_dir.mkdir(mode=0o700)
    except FileExistsError:
        raise FileExistsError(f"run directory already exists: {run_dir}") from None
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("run directory must be a real directory")
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to(runs):
        raise ValueError("run directory resolves outside runs root")
    try:
        overlay = create_overlay(image, run_dir / "overlay.qcow2", resources, run=run)
        seed_iso = write_nocloud_seed(
            run_dir,
            render_user_data(public_key),
            render_meta_data(run_id),
            run=run,
        )
        known_hosts = run_dir / "known_hosts"
        known_hosts.touch(exist_ok=False)
        os.chmod(known_hosts, 0o600)
        serial_log = run_dir / "serial.log"
        serial_log.touch(exist_ok=False)
        os.chmod(serial_log, 0o600)
        record = RunRecord(
            schema_version=1,
            run_id=run_id,
            run_dir=run_dir,
            resources=resources,
            source_image_digest=image.verified_source_digest,
            overlay=overlay,
            seed_iso=seed_iso,
            known_hosts=known_hosts,
            serial_log=serial_log,
            qmp_socket=run_dir / "qmp.sock",
            pid_file=run_dir / "qemu.pid",
            record_path=run_dir / "run.json",
            ssh_user=GUEST_USER,
            created_at_utc=now().astimezone(UTC).isoformat(),
            lifecycle="prepared",
        )
        _atomic_write_private(record.record_path, record.to_json())
        return record
    except BaseException:
        if run_dir.is_dir() and not run_dir.is_symlink():
            shutil.rmtree(run_dir)
        raise
