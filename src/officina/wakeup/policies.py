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
    """Build the persistent registry key for one provider session.

    Intent
    ------
    Namespace a conversation identifier by its provider before policy lookup or
    mutation.

    Rationale
    ---------
    Provider-qualified keys prevent equal session identifiers from colliding
    while keeping the on-disk registry a flat JSON object.

    Pseudocode
    ----------
    - return provider joined to session_id by a colon

    Wraps
    -----
    - none
    """

    return f"{provider}:{session_id}"


def _read(path: Path) -> dict[str, dict]:
    """Load and validate the policy registry, treating absence as empty.

    Intent
    ------
    Convert the persisted JSON object into the mutable mapping used by policy
    operations.

    Rationale
    ---------
    A missing registry means that no sessions have opted in. An ``OSError`` from
    reading existing contents or a JSON syntax error is translated to
    ``WakeupError``; the decoded object then must map string keys to mapping
    records. Existence checks and text-decoding failures retain their native
    behavior.

    Pseudocode
    ----------
    - if path does not exist:
      - return empty mapping
    - set parsed_policies = JSON decoded from the UTF-8 file
    - if the content read raises OSError or JSON parsing raises JSONDecodeError:
      - raise WakeupError(error)
    - if parsed_policies is not a mapping of string keys to mapping records:
      - raise WakeupError(path)
    - return parsed_policies

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ..WakeupError:
      why:
        raises: "Constructs the domain failure for content-read OSError, JSON syntax errors, or a decoded object with an invalid mapping shape."
    """

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
    """Replace the complete policy registry through a same-directory temp file.

    Intent
    ------
    Persist one complete, deterministically ordered JSON snapshot at the policy
    path.

    Rationale
    ---------
    Writing beside the destination permits an atomic replacement, while the
    cleanup guard can unlink the temporary file only after serialization and the
    newline write finish and its path is recorded, before the file context exits.
    It therefore covers subsequent close or replacement failures, not earlier
    creation, serialization, or write failures.

    Pseudocode
    ----------
    - set destination = path after ensuring its parent directories exist
    - set handle = named UTF-8 file beside destination
    - set serialized_registry = newline-terminated sorted-key policies written to handle
    - set temporary = handle path after serialization and newline writing finish
    - set handle_status = file context closed
    - set destination = destination atomically replaced by temporary
    - if file close or replacement fails after temporary is recorded:
      - set temporary = removed from filesystem

    Wraps
    -----
    - none
    """

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


@contextmanager
def _locked_policies() -> Iterator[dict[str, dict]]:
    """Expose the mutable registry while holding its independent file lock.

    Intent
    ------
    Serialize one policy operation from registry load through its normal
    write-back.

    Rationale
    ---------
    A lock separate from the wakeup queue prevents policy edits from competing
    with delivery. The mapping is persisted only after the caller leaves the
    yielded context normally; a caller exception releases the lock without
    writing its partial mutation.

    Pseudocode
    ----------
    - policy_root = .store.data_dir()
    - set policy_path = policy_root plus the registry filename
    - set policy_lock = policy_root plus the lock filename
    - @.locking.locked_file(policy_lock)
    - policies = _read(policy_path)
    - set caller_registry = policies exposed until context exit
    - @_write(policy_path, policies)

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .locking.locked_file:
      why:
        orchestrates: "Holds the policy-specific advisory lock across registry loading, caller mutation, and normal write-back."
    ._write:
      why:
        writes: "Commits the complete caller-mutated registry after the yielded context exits without an exception."

    InstantiationsFromRepo
    ----------------------
    .store.data_dir:
      why:
        constructs: "Builds the configured persistent-state root from which the policy and lock paths are derived."
    ._read:
      why:
        constructs: "Builds the mutable registry exposed to the caller from the validated persisted JSON object."
    """

    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "session-policies.json"
    with locked_file(root / "session-policies.lock"):
        policies = _read(path)
        yield policies
        _write(path, policies)


def set_auto_schedule(provider: str, session_id: str, enabled: bool) -> None:
    """Enable or remove automatic scheduling for one provider conversation.

    Intent
    ------
    Replace one conversation's policy record with an enabled timestamp or
    remove that record entirely.

    Rationale
    ---------
    Keeping only explicit opt-ins makes absence the disabled state. Replacing
    the enabled record records the most recent policy change in UTC and avoids
    retaining unrelated fields from older formats.

    Pseudocode
    ----------
    - key = _policy_key(provider, session_id)
    - policies = @_locked_policies()
    - if enabled:
      - set enabled_record = current UTC timestamp plus literal true flag
      - set policies = policies with key mapped to enabled_record
    - else:
      - set policies = policies without key

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._locked_policies:
      why:
        orchestrates: "Serializes the registry mutation and persists the resulting complete mapping on normal context exit."

    InstantiationsFromRepo
    ----------------------
    ._policy_key:
      why:
        constructs: "Builds the provider-qualified mapping key whose record is replaced or removed."
    """

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
    """Test whether one conversation has an explicit boolean opt-in.

    Intent
    ------
    Resolve a provider-qualified policy record and accept only the literal JSON
    boolean ``true`` as enabled.

    Rationale
    ---------
    An exact identity check prevents truthy malformed values from activating an
    automatic wakeup. The shared locked context retains its existing behavior of
    rewriting the unchanged registry after a successful lookup.

    Pseudocode
    ----------
    - policies = @_locked_policies()
    - key = @_policy_key(provider, session_id)
    - set record = policies entry for key or an empty mapping
    - return whether record auto_schedule is exactly true

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._locked_policies:
      why:
        reads: "Loads the registry under its policy lock and writes the unchanged mapping back after the lookup succeeds."
    ._policy_key:
      why:
        computes: "Combines provider and session identifiers into the exact registry lookup key."
    """

    with _locked_policies() as policies:
        record = policies.get(_policy_key(provider, session_id), {})
        return record.get("auto_schedule") is True


def auto_scheduled_sessions(provider: str) -> tuple[str, ...]:
    """List sessions explicitly opted into automatic wakeups for one provider.

    Intent
    ------
    Filter the shared registry to enabled records in the requested provider
    namespace and remove the provider prefix from each result.

    Rationale
    ---------
    Requiring both a matching namespace and the literal boolean ``true`` keeps
    other providers and malformed records out of monitoring targets while
    preserving registry iteration order. The locked context rewrites the
    unchanged registry after a successful scan.

    Pseudocode
    ----------
    - set prefix = provider joined to a colon
    - policies = @_locked_policies()
    - return session suffixes from policies with matching prefix and exact true opt-in

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._locked_policies:
      why:
        reads: "Loads the registry under its policy lock and writes the unchanged mapping back after iteration succeeds."
    """

    prefix = f"{provider}:"
    with _locked_policies() as policies:
        return tuple(
            key.removeprefix(prefix)
            for key, record in policies.items()
            if key.startswith(prefix) and record.get("auto_schedule") is True
        )


__all__ = [
    "auto_schedule_enabled",
    "auto_scheduled_sessions",
    "set_auto_schedule",
]
