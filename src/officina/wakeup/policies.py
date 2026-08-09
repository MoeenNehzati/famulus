"""Persistent per-session wakeup policy storage.

Policies are mutable user state, not provider transcript data or source-tree
configuration. They live beside the wakeup queue and use a separate lock so a
policy change never blocks delivery longer than its atomic JSON replacement.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import WakeupError
from .locking import locked_file
from .store import data_dir


def _policy_key(provider: str, session_id: str) -> str:
    """Return the stable cross-provider key for one conversation."""

    return f"{provider}:{session_id}"


def _read(path: Path) -> dict[str, dict]:
    """Read the policy registry; a missing file represents no overrides."""

    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WakeupError(f"could not read session policies {path}: {error}") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(record, dict)
        for key, record in value.items()
    ):
        raise WakeupError(f"invalid session policy format: {path}")
    return value


def _write(path: Path, policies: dict[str, dict]) -> None:
    """Replace the complete policy registry atomically in its own directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, encoding="utf-8"
        ) as handle:
            json.dump(policies, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_policies() -> dict[str, dict]:
    """Read the policy registry without creating persistent state."""

    root = data_dir()
    try:
        root.lstat()
    except FileNotFoundError:
        pass
    else:
        if not root.is_dir():
            root.mkdir(parents=True, exist_ok=True)
    return _read(root / "session-policies.json")


@contextmanager
def _locked_policies() -> Iterator[dict[str, dict]]:
    """Yield the mutable registry while holding its independent write lock."""

    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "session-policies.json"
    with locked_file(root / "session-policies.lock"):
        policies = _read(path)
        yield policies
        _write(path, policies)


def set_auto_schedule(provider: str, session_id: str, enabled: bool) -> None:
    """Enable or remove automatic scheduling for one provider conversation."""

    key = _policy_key(provider, session_id)
    with _locked_policies() as policies:
        if enabled:
            policies[key] = {
                "auto_schedule": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            policies.pop(key, None)


def auto_schedule_enabled(provider: str, session_id: str) -> bool:
    """Return whether automatic scheduling is enabled for one conversation."""

    record = _read_policies().get(_policy_key(provider, session_id), {})
    return record.get("auto_schedule") is True


def auto_scheduled_sessions(provider: str) -> tuple[str, ...]:
    """Return session identifiers explicitly opted into automatic wakeups."""

    prefix = f"{provider}:"
    return tuple(
        key.removeprefix(prefix)
        for key, record in _read_policies().items()
        if key.startswith(prefix) and record.get("auto_schedule") is True
    )


__all__ = [
    "auto_schedule_enabled",
    "auto_scheduled_sessions",
    "set_auto_schedule",
]
