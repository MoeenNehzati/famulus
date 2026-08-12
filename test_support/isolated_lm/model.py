"""Immutable runtime records for isolated language-model VMs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class VmResources:
    """The fixed resource profile for one isolated VM.

    Intent
    ------
    Carry the supported CPU, memory, and disk allocation as immutable values.

    Rationale
    ---------
    A named value object keeps resource facts explicit in every run record.

    Pseudocode
    ----------
    - set fields = fixed VM resource defaults

    Wraps
    -----
    none
    """

    vcpus: int = 4
    memory_mib: int = 8192
    disk_gib: int = 40


@dataclass(frozen=True)
class RuntimePaths:
    """State paths derived from one explicitly selected absolute root.

    Intent
    ------
    Carry the root and its canonical runtime subdirectories as immutable paths.

    Rationale
    ---------
    Explicit path authority prevents ambient environment state from selecting
    storage locations.

    Pseudocode
    ----------
    - set fields = root and canonical runtime subdirectories

    Wraps
    -----
    none
    """

    root: Path
    downloads: Path
    images: Path
    runs: Path

    @classmethod
    def from_root(cls, root: Path) -> "RuntimePaths":
        """Derive runtime paths without consulting the process environment.

        Intent
        ------
        Validate one absolute state root and derive its fixed child paths.

        Rationale
        ---------
        Central derivation keeps every command on the same explicit authority.

        Pseudocode
        ----------
        - if root is not absolute:
          - raise invalid state root
        - set resolved = canonical root
        - return runtime paths below resolved

        Wraps
        -----
        none
        """
        if not root.is_absolute():
            raise ValueError("state root must be absolute")
        resolved = root.resolve()
        return cls(
            root=resolved,
            downloads=resolved / "downloads",
            images=resolved / "images",
            runs=resolved / "runs",
        )


@dataclass(frozen=True)
class CloudImageRecord:
    """Preserve the authenticated provenance of one cached cloud image.

    Intent
    ------
    Carry the authenticated source and cached-image facts as immutable data.

    Rationale
    ---------
    Later VM runs must be able to name the exact signed source and byte digest
    they used without reconstructing provenance from mutable network state.

    Pseudocode
    ----------
    - set fields = approved source URLs and verified image facts

    Wraps
    -----
    none
    """

    schema_version: int
    image_url: str
    checksums_url: str
    signature_url: str
    filename: str
    verified_source_digest: str
    byte_size: int
    retrieved_at: datetime
    cached_path: Path

    def to_json(self) -> str:
        """Render a deterministic, newline-terminated record for evidence files.

        Intent
        ------
        Serialize the complete authenticated-image record deterministically.

        Rationale
        ---------
        Stable serialization makes records comparable across invocations and
        prevents an evidence writer from selecting ad hoc JSON formatting.

        Pseudocode
        ----------
        - set payload = complete record with JSON scalar path and time fields
        - return sorted compact JSON plus one newline

        Wraps
        -----
        none
        """
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "image_url": self.image_url,
                "checksums_url": self.checksums_url,
                "signature_url": self.signature_url,
                "filename": self.filename,
                "verified_source_digest": self.verified_source_digest,
                "byte_size": self.byte_size,
                "retrieved_at": self.retrieved_at.isoformat(),
                "cached_path": str(self.cached_path),
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"


@dataclass(frozen=True)
class RunRecord:
    """Freeze the complete artifact contract for one disposable VM run.

    Intent
    ------
    Carry complete immutable artifact, provenance, and lifecycle authority.

    Rationale
    ---------
    A run must retain the verified backing-image digest and every private host
    artifact path so later lifecycle operations cannot infer them from ambient
    state or a mutable image cache.

    Pseudocode
    ----------
    - set fields = immutable run identity, provenance, artifacts, and lifecycle

    Wraps
    -----
    none
    """

    schema_version: int
    run_id: str
    run_dir: Path
    resources: VmResources
    source_image_digest: str
    overlay: Path
    seed_iso: Path
    known_hosts: Path
    serial_log: Path
    qmp_socket: Path
    pid_file: Path
    record_path: Path
    ssh_user: str
    created_at_utc: str
    lifecycle: str
    ssh_port: int | None = None
    identity_file: Path | None = None
    qemu_command: tuple[str, ...] = ()

    def write_atomic(self) -> None:
        """Privately and atomically replace this run's evidence record.

        Intent
        ------
        Durably replace the canonical run record with private complete JSON.

        Rationale
        ---------
        Launch and lifecycle transitions must never expose a partial JSON file
        or silently weaken its permissions. Keeping the write boundary beside
        the canonical serializer ensures every Task 4 transition persists the
        same complete frozen record.

        Pseudocode
        ----------
        - if record parent is not a real absolute directory:
          - raise invalid record parent
        - set parent_descriptor = retained no-follow directory descriptor
        - set temporary_path = private sibling temporary file
        - set temporary_file = written canonical JSON with synchronized bytes
        - set record_path = atomic replacement of temporary path
        - set parent_directory = synchronized retained descriptor
        - return after temporary cleanup

        Wraps
        -----
        none
        """
        path = self.record_path
        parent = path.parent
        if (
            not path.is_absolute()
            or parent.is_symlink()
            or not parent.is_dir()
            or parent.resolve() != parent
        ):
            raise ValueError("run record must have a real absolute parent directory")
        if path.is_symlink():
            raise ValueError("run record must not be a symlink")
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        parent_descriptor = os.open(parent, directory_flags)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}-", dir=parent
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.chmod(output.fileno(), 0o600)
                output.write(self.to_json())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            os.fsync(parent_descriptor)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
            os.close(parent_descriptor)

    def to_json(self) -> str:
        """Render a deterministic, newline-terminated VM run record.

        Intent
        ------
        Serialize the complete run authority deterministically.

        Rationale
        ---------
        Lifecycle code needs an auditable record it can update atomically
        without varying the serialization across Python or host environments.

        Pseudocode
        ----------
        - set payload = complete run fields with explicit path and resource scalars
        - return sorted compact JSON plus one newline

        Wraps
        -----
        none
        """
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "run_dir": str(self.run_dir),
                "resources": {
                    "vcpus": self.resources.vcpus,
                    "memory_mib": self.resources.memory_mib,
                    "disk_gib": self.resources.disk_gib,
                },
                "source_image_digest": self.source_image_digest,
                "overlay": str(self.overlay),
                "seed_iso": str(self.seed_iso),
                "known_hosts": str(self.known_hosts),
                "serial_log": str(self.serial_log),
                "qmp_socket": str(self.qmp_socket),
                "pid_file": str(self.pid_file),
                "record_path": str(self.record_path),
                "ssh_user": self.ssh_user,
                "created_at_utc": self.created_at_utc,
                "lifecycle": self.lifecycle,
                "ssh_port": self.ssh_port,
                "identity_file": (
                    str(self.identity_file) if self.identity_file is not None else None
                ),
                "qemu_command": list(self.qemu_command),
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
