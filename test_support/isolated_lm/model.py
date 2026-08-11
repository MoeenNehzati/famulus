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
    """The fixed resource profile for one isolated VM."""

    vcpus: int = 4
    memory_mib: int = 8192
    disk_gib: int = 40


@dataclass(frozen=True)
class RuntimePaths:
    """State paths derived from one explicitly selected absolute root."""

    root: Path
    downloads: Path
    images: Path
    runs: Path

    @classmethod
    def from_root(cls, root: Path) -> "RuntimePaths":
        """Derive runtime paths without consulting the process environment."""
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

    Rationale
    ---------
    Later VM runs must be able to name the exact signed source and byte digest
    they used without reconstructing provenance from mutable network state.

    Pseudocode
    ----------
    - retain the three approved source URLs and verified image facts
    - render paths and the UTC retrieval time as JSON scalar values
    - serialize the mapping with stable key ordering and one trailing newline

    Call boundary
    -------------
    ``prepare_cloud_image`` creates this immutable record after signature and
    image-digest verification; Task 3 consumes its digest and cached path.
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

        Rationale
        ---------
        Stable serialization makes records comparable across invocations and
        prevents an evidence writer from selecting ad hoc JSON formatting.

        Pseudocode
        ----------
        - convert the datetime and path fields to JSON scalar values
        - encode the complete record with sorted keys and compact separators
        - append one newline required by line-oriented record files

        Call boundary
        -------------
        Orchestration code may write the returned text directly to a run or
        image manifest; this model method does not perform filesystem I/O.
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

    Rationale
    ---------
    A run must retain the verified backing-image digest and every private host
    artifact path so later lifecycle operations cannot infer them from ambient
    state or a mutable image cache.

    Pseudocode
    ----------
    - retain immutable run identity, resource, provenance, and artifact facts
    - render paths as strings and resources as explicit scalar fields
    - serialize with sorted keys and a final newline for stable evidence files

    Call boundary
    -------------
    ``prepare_run`` creates the initial ``prepared`` record. Task 4 replaces
    the optional launch fields and atomically rewrites the same record path.
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

        Rationale
        ---------
        Launch and lifecycle transitions must never expose a partial JSON file
        or silently weaken its permissions. Keeping the write boundary beside
        the canonical serializer ensures every Task 4 transition persists the
        same complete frozen record.

        Pseudocode
        ----------
        - require the existing record directory to be a real absolute directory
        - create a mode-0600 temporary file beside the final record
        - write and fsync the canonical JSON, then atomically replace the record
        - remove the temporary path on every incomplete write

        Call boundary
        -------------
        QEMU lifecycle functions call this after ``dataclasses.replace``. The
        initial guest-preparation writer remains responsible for creating the
        first record before Task 4 can consume it.
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
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.chmod(output.fileno(), 0o600)
                output.write(self.to_json())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def to_json(self) -> str:
        """Render a deterministic, newline-terminated VM run record.

        Rationale
        ---------
        Lifecycle code needs an auditable record it can update atomically
        without varying the serialization across Python or host environments.

        Pseudocode
        ----------
        - convert every path to its already-confined absolute string form
        - expand resource values rather than depending on dataclass encoding
        - encode sorted compact JSON and append the record-file newline

        Call boundary
        -------------
        ``prepare_run`` and later lifecycle code write this exact text to
        ``record_path`` through their atomic file-writing boundary.
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
