"""Immutable runtime records for isolated language-model VMs."""
from __future__ import annotations

from dataclasses import dataclass
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
