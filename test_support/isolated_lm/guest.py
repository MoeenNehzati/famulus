"""Deterministic NoCloud seed and disposable disk preparation for isolated VMs."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
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

    Intent
    ------
    Constrain every operator-supplied run identifier to one safe directory and
    guest-hostname component before any caller uses it as authority.

    Rationale
    ---------
    A run ID becomes a directory component, so accepting separators or dot
    segments would let a caller direct disposable artifacts outside state.

    Pseudocode
    ----------
    - if run_id does not match the closed grammar:
      - raise invalid run identifier
    - return run_id

    Wraps
    -----
    none
    """
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run ID must match ^[a-z0-9][a-z0-9-]{0,62}$")
    return run_id


def _validate_public_key(public_key: str) -> str:
    """Require one nonempty Ed25519 public-key line for guest authorization.

    Intent
    ------
    Keep multiline content and unsupported key forms out of rendered cloud-init
    configuration while preserving the validated key text exactly.

    Rationale
    ---------
    The public key crosses into YAML and guest account authority, so accepting
    embedded line breaks would alter the fixed bootstrap document structure.

    Pseudocode
    ----------
    - if public_key is not one nonempty Ed25519 line:
      - raise invalid public key
    - return public_key

    Wraps
    -----
    none
    """
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

    Intent
    ------
    Produce the complete generic cloud-init policy for the sole non-root guest
    account from one previously untrusted public-key string.

    Rationale
    ---------
    Guest setup must enable only bootstrap prerequisites and one supplied
    public key; host paths, repositories, credentials, and project tooling do
    not belong in an isolated baseline.

    Pseudocode
    ----------
    - key = _validate_public_key(public_key)
    - set cloud_config = fixed packages account policy and key
    - return cloud_config

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    ._validate_public_key:
      why:
        transforms: "Returns the validated key text embedded in the fixed cloud-init document."
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
            f"      - {json.dumps(key)}",
            "ssh_pwauth: false",
            "disable_root: true",
            "",
        )
    )


def render_meta_data(run_id: str) -> str:
    """Render the two fixed NoCloud metadata fields for one validated run.

    Intent
    ------
    Give instance-id and local-hostname one shared identity derived only from a
    run identifier that satisfies the closed namespace grammar.

    Rationale
    ---------
    Keeping both NoCloud identity fields identical prevents unrelated host data
    or unsafe path-like text from entering guest metadata.

    Pseudocode
    ----------
    - run_id = validate_run_id(run_id)
    - set guest_identity = isolated prefix plus run_id
    - return rendered metadata

    Wraps
    -----
    none

    InstantiationsFromRepo
    ----------------------
    .validate_run_id:
      why:
        transforms: "Returns the namespace-safe identifier used in both metadata fields."
    """
    run_id = validate_run_id(run_id)
    identity = f"isolated-lm-{run_id}"
    return f"instance-id: {identity}\nlocal-hostname: {identity}\n"


def _occupied(path: Path) -> bool:
    """Treat regular files and dangling symlinks alike as occupied paths.

    Intent
    ------
    Give exclusive artifact creation one predicate that cannot overlook a
    dangling symlink merely because its target is absent.

    Rationale
    ---------
    Reusing any existing directory entry could overwrite evidence or follow an
    attacker-controlled link during disposable-run preparation.

    Pseudocode
    ----------
    - set occupied = path exists or path is a symlink
    - return occupied

    Wraps
    -----
    none
    """
    return path.exists() or path.is_symlink()


def _write_private_new(path: Path, content: str) -> None:
    """Create one non-reusable private text file without following a symlink.

    Intent
    ------
    Persist a new seed input with owner-only permissions and durable contents
    while refusing any pre-existing final path.

    Rationale
    ---------
    Exclusive creation and an fsync keep seed authority from replacing an
    existing artifact or being reported before its bytes reach storage.

    Pseudocode
    ----------
    - set output = exclusively opened path
    - set output_permissions = owner_only
    - set output_content = content
    - set output_durability = synchronized

    Wraps
    -----
    none
    """
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

    Intent
    ------
    Materialize private user-data and meta-data inputs and turn them into one
    validated seed ISO without exposing child-process output to CLI transport.

    Rationale
    ---------
    The ISO generator receives exactly two controlled input files. Refusing
    reuse and deleting failed outputs prevents a partial seed from becoming a
    later run's boot configuration.

    Pseudocode
    ----------
    - if run directory is not real:
      - raise invalid run directory
    - if @_occupied(seed paths):
      - raise occupied seed artifact
    - @_write_private_new(user path and user content)
    - @_write_private_new(metadata path and metadata content)
    - set seed_result = captured cloud-localds invocation
    - if seed result is missing or has wrong type:
      - raise invalid seed result
    - return resolved seed path

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    ._occupied:
      why:
        validates: "Rejects existing files and dangling symlinks at every seed artifact path."
    ._write_private_new:
      why:
        writes: "Persists each cloud-localds input exclusively with owner-only permissions."
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
        run(
            ["cloud-localds", str(seed_iso), str(user_path), str(meta_path)],
            capture_output=True,
            check=True,
        )
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
    """Create and validate the real state/runs directory without symlink hops.

    Intent
    ------
    Establish the one canonical parent beneath which disposable run directories
    may be created, checking each descendant component independently.

    Rationale
    ---------
    Component-wise symlink rejection prevents a concurrently supplied runtime
    layout from redirecting run creation outside the explicit state root.

    Pseudocode
    ----------
    - if runtime root is not absolute and real:
      - raise invalid runtime root
    - set relative_runs = runs path relative to root
    - for component in relative_runs:
      - if component is a symlink:
        - raise redirected runs directory
      - set component = existing or newly created directory
    - if resolved runs directory escapes root:
      - raise escaped runs directory
    - return resolved runs directory

    Wraps
    -----
    none
    """
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
    """Atomically replace one private evidence file in its existing directory.

    Intent
    ------
    Publish a complete owner-only manifest only after its temporary bytes have
    been flushed and synchronized.

    Rationale
    ---------
    A same-directory replace prevents readers from observing a partial record
    and preserves evidence confidentiality across successful transitions.

    Pseudocode
    ----------
    - set temporary_manifest = private file beside destination
    - set temporary_content = content
    - set temporary_durability = synchronized
    - set destination = atomic replacement of temporary_manifest
    - if temporary_manifest remains:
      - set temporary_manifest = removed

    Wraps
    -----
    none
    """
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(path.parent, directory_flags)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}-", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.chmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.fsync(parent_descriptor)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        os.close(parent_descriptor)


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

    Intent
    ------
    Assemble every immutable and mutable artifact for one fresh VM run and
    publish its prepared lifecycle record as a single transaction boundary.

    Rationale
    ---------
    Every mutable VM artifact is contained in a fresh run directory while its
    immutable record links the overlay to a previously verified image digest.

    Pseudocode
    ----------
    - run_id = validate_run_id(run_id)
    - public_key = _validate_public_key(public_key)
    - runs = _prepare_runs_directory(paths)
    - set run_directory = fresh private child of runs
    - set overlay = verified sparse overlay
    - @render_user_data(public_key)
    - @render_meta_data(run_id)
    - seed_iso = write_nocloud_seed(run directory and rendered inputs)
    - set run_record = prepared manifest with fixed artifact paths
    - @_atomic_write_private(record path and serialized record)
    - return run_record

    Wraps
    -----
    none

    CallsFromRepo
    -------------
    .render_user_data:
      why:
        transforms: "Builds the fixed guest bootstrap policy from the validated public key."
    .render_meta_data:
      why:
        transforms: "Builds NoCloud identity fields from the validated run identifier."
    ._atomic_write_private:
      why:
        writes: "Publishes the complete prepared run record with private durable replacement."

    InstantiationsFromRepo
    ----------------------
    .validate_run_id:
      why:
        transforms: "Returns the namespace-safe identifier carried into run paths and records."
    ._validate_public_key:
      why:
        transforms: "Returns the single-line public key carried into seed rendering."
    ._prepare_runs_directory:
      why:
        constructs: "Returns the validated runs parent used to create the dedicated run directory."
    .write_nocloud_seed:
      why:
        constructs: "Returns the generated private seed path recorded as VM boot authority."
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
