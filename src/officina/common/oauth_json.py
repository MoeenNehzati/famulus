"""Cross-platform atomic writes for private OAuth JSON files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from . import atomic_files


class OAuthJsonError(OSError):
    """Raised when an OAuth JSON file cannot be written safely."""


def write_oauth_json(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write one OAuth JSON mapping with restrictive mode."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise OAuthJsonError(f"destination is a symbolic link: {destination}")

    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    if os.name == "posix":
        try:
            atomic_files.atomic_replace_bytes(
                destination,
                data,
                allowed_root=destination.parent,
                mode=0o600,
            )
        except atomic_files.AtomicWriteError as exc:
            raise OAuthJsonError(str(exc)) from exc
        return

    descriptor, temporary = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if destination.is_symlink():
            raise OAuthJsonError(f"destination is a symbolic link: {destination}")
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
