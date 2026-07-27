"""Atomic current.json pointer for the managed Famulus runtime."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from officina.common.atomic_files import atomic_replace_bytes


class RuntimePointerError(Exception):
    """Raised when the runtime pointer cannot be read or activated safely."""


@dataclass(frozen=True)
class RuntimePointer:
    release_id: str
    runtime_source: Path
    python_bin: Path


def _pointer_path(runtime_root: Path) -> Path:
    return runtime_root / "current.json"


def _require_contained(path: Path, *, root: Path, label: str) -> Path:
    """Return ``path`` resolved, or raise if it is not absolute and beneath ``root``."""
    if not path.is_absolute():
        raise RuntimePointerError(f"{label} must be an absolute path: {path}")
    resolved_root = root.resolve()
    try:
        resolved = path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimePointerError(f"{label} must live under runtime_root: {path}") from exc
    return resolved_root / resolved


def load_current_pointer(*, runtime_root: Path) -> RuntimePointer:
    """Load and validate the current release pointer beneath ``runtime_root``."""
    path = _pointer_path(runtime_root)
    if not path.exists():
        raise RuntimePointerError(f"no current.json at {path}")
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise RuntimePointerError(
            f"unsupported current.json schema_version: {payload.get('schema_version')!r}"
        )
    try:
        python_bin = Path(payload["python_bin"])
        runtime_source = Path(payload["runtime_source"])
        release_id = payload["release_id"]
    except KeyError as exc:
        raise RuntimePointerError(f"current.json missing required key: {exc}") from exc
    _require_contained(python_bin, root=runtime_root, label="python_bin")
    _require_contained(runtime_source, root=runtime_root, label="runtime_source")
    return RuntimePointer(release_id=release_id, runtime_source=runtime_source, python_bin=python_bin)


def activate_release(*, runtime_root: Path, release_dir: Path, python_bin: Path) -> RuntimePointer:
    """Atomically point current.json at ``release_dir``'s ``python_bin``.

    Validates that release_dir and python_bin are absolute paths beneath
    runtime_root and that python_bin exists before writing anything, then
    durably replaces current.json through the shared confined atomic-write
    primitive (fsync of file and parent directory, symlink rejection) so a
    failed or interrupted activation never leaves current.json missing or
    partially written; any prior pointer is left untouched on failure.
    """
    _require_contained(release_dir, root=runtime_root, label="release_dir")
    _require_contained(python_bin, root=runtime_root, label="python_bin")
    if not python_bin.exists():
        raise RuntimePointerError(f"candidate python_bin does not exist: {python_bin}")
    runtime_root.mkdir(parents=True, exist_ok=True)
    pointer = RuntimePointer(release_id=release_dir.name, runtime_source=release_dir, python_bin=python_bin)
    payload = {
        "schema_version": 1,
        "release_id": pointer.release_id,
        "runtime_source": str(pointer.runtime_source),
        "python_bin": str(pointer.python_bin),
    }
    atomic_replace_bytes(
        _pointer_path(runtime_root),
        json.dumps(payload, indent=2).encode("utf-8"),
        allowed_root=runtime_root,
        mode=0o600,
    )
    return pointer


__all__ = [
    "RuntimePointer",
    "RuntimePointerError",
    "activate_release",
    "load_current_pointer",
]
