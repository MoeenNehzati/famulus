"""Immutable runtime records for isolated language-model VMs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path


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
