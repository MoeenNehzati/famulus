#!/usr/bin/env python3
"""Durable home-scoped manifest and transaction-journal records.

install.py / setup_symlinks.py / setup_tools.py record what they change here;
uninstall.py replays the manifest in reverse. This makes uninstall exact even
when the installing tree is gone (e.g. an old plugin-cache version dir).

Manifest schema (JSON):
    {"version": 2, "entries": [{"kind": ..., "path": ..., ...}, ...]}

Entry kinds:
    symlink            {path, target}
    marker_block       {path, begin, end}
    json_hook_commands {path, commands: [str]}
    git_hooks_path     {path: repo_root}
    file               {path}
    config_dir         {path, purge_only: true}
    pip_editable       {path: package name}
    registry_env       {path: bin_dir, names: [env var names]}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from officina.common.atomic_files import (
    AtomicWriteError,
    atomic_replace_bytes,
    ensure_secure_directory,
    read_regular_file_bytes,
)
from officina.common.famulus_paths import resolve_famulus_paths

MANIFEST_VERSION = 2
JOURNAL_VERSION = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class StateRecordError(RuntimeError):
    """Raised when an installer state record cannot be trusted."""


def _confined_record_path(path: Path, state_root: Path) -> tuple[Path, Path]:
    """Validate one record path against an explicit trusted home-state root."""
    destination = Path(path).absolute()
    root = Path(state_root).absolute()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise StateRecordError(f"state record is outside state_root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise StateRecordError(f"state record is outside state_root: {path}")
    try:
        ensure_secure_directory(root)
    except (AtomicWriteError, OSError) as exc:
        raise StateRecordError(f"cannot securely prepare state_root {root}: {exc}") from exc
    return destination, root


def _open_snapshot_descriptor(path: Path) -> int:
    """Open one path without link traversal for descriptor-bound inspection."""
    if os.name == "nt":
        import msvcrt

        from officina.common.atomic_files import (
            _WIN_FILE_OPTIONS,
            _WIN_READ_ACCESS,
            _windows_close_chain,
            _windows_close_handle,
            _windows_open_parent,
            _windows_open_validated,
        )

        parents, parts = _windows_open_parent(path, path.parent)
        handle = -1
        try:
            handle, _information = _windows_open_validated(
                parents[-1],
                parts[-1],
                access=_WIN_READ_ACCESS,
                disposition=1,
                options=_WIN_FILE_OPTIONS,
                directory=False,
            )
            try:
                descriptor = msvcrt.open_osfhandle(
                    handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                )
            except BaseException:
                _windows_close_handle(handle)
                raise
            handle = -1
            os.set_inheritable(descriptor, False)
            return descriptor
        finally:
            _windows_close_chain(
                parents + ([handle] if handle >= 0 else [])
            )

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _read_json_object(
    path: Path, *, state_root: Path, label: str
) -> dict[str, object]:
    destination, root = _confined_record_path(path, state_root)
    try:
        raw = read_regular_file_bytes(destination, allowed_root=root)
        value = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise
    except (AtomicWriteError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateRecordError(f"invalid {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateRecordError(f"invalid {label} at {path}: expected JSON object")
    return value


def _atomic_json_replace(
    path: Path, payload: Mapping[str, object], *, state_root: Path
) -> None:
    destination, root = _confined_record_path(path, state_root)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        atomic_replace_bytes(
            destination,
            encoded,
            allowed_root=root,
            mode=0o600,
        )
    except (AtomicWriteError, OSError) as exc:
        raise StateRecordError(f"cannot durably write state record {path}: {exc}") from exc


def _require_string(value: object, *, field: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise StateRecordError(f"{field} must be a non-empty string")
    return value


def _require_state(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise StateRecordError(f"{field} must be a JSON object")
    kind = value.get("kind")
    if kind not in {"absent", "file", "directory", "symlink", "other"}:
        raise StateRecordError(f"{field}.kind is invalid")
    required_fields = {
        "absent": {"kind"},
        "file": {"kind", "mode", "size", "sha256"},
        "directory": {"kind", "mode"},
        "symlink": {"kind", "target"},
        "other": {"kind", "mode"},
    }[kind]
    if set(value) != required_fields:
        raise StateRecordError(f"{field} fields do not match {kind} state")
    if kind in {"file", "directory", "other"}:
        mode = value["mode"]
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
            raise StateRecordError(f"{field}.mode is invalid")
    if kind == "file":
        size = value["size"]
        digest = value["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StateRecordError(f"{field}.size is invalid")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise StateRecordError(f"{field}.sha256 is invalid")
    if kind == "symlink" and not isinstance(value["target"], str):
        raise StateRecordError(f"{field}.target is invalid")
    return dict(value)


@dataclass(frozen=True)
class JournalMutation:
    """One exact, recoverable filesystem transition and its ownership record."""

    mutation_id: str
    kind: str
    path: str
    expected_before: dict[str, object]
    intended_after: dict[str, object]
    ownership_entry: dict[str, object] | None

    def __post_init__(self) -> None:
        _require_string(self.mutation_id, field="mutation_id")
        _require_string(self.kind, field="kind")
        _require_string(self.path, field="path")
        if not Path(self.path).is_absolute():
            raise StateRecordError("mutation path must be absolute")
        _require_state(self.expected_before, field="expected_before")
        _require_state(self.intended_after, field="intended_after")
        if self.ownership_entry is not None:
            if not isinstance(self.ownership_entry, dict):
                raise StateRecordError("ownership_entry must be a JSON object or null")
            if self.ownership_entry.get("path") != self.path:
                raise StateRecordError("ownership_entry path must equal mutation path")
            _require_string(self.ownership_entry.get("kind"), field="ownership_entry.kind")

    @classmethod
    def from_dict(cls, payload: object) -> "JournalMutation":
        if not isinstance(payload, dict):
            raise StateRecordError("pending_mutation must be a JSON object or null")
        required = {
            "mutation_id",
            "kind",
            "path",
            "expected_before",
            "intended_after",
            "ownership_entry",
        }
        if set(payload) != required:
            raise StateRecordError("pending_mutation fields are incomplete or unknown")
        ownership = payload["ownership_entry"]
        if ownership is not None and not isinstance(ownership, dict):
            raise StateRecordError("ownership_entry must be a JSON object or null")
        return cls(
            mutation_id=_require_string(payload["mutation_id"], field="mutation_id"),  # type: ignore[arg-type]
            kind=_require_string(payload["kind"], field="kind"),  # type: ignore[arg-type]
            path=_require_string(payload["path"], field="path"),  # type: ignore[arg-type]
            expected_before=_require_state(
                payload["expected_before"], field="expected_before"
            ),
            intended_after=_require_state(
                payload["intended_after"], field="intended_after"
            ),
            ownership_entry=None if ownership is None else dict(ownership),
        )


@dataclass(frozen=True)
class TransactionJournal:
    """Durable progress record for one managed installer transaction."""

    transaction_id: str
    phase: Literal["prepared", "committed", "complete"]
    prior_release_id: str | None
    candidate_release_id: str
    resolver_bundle_id: str
    staged_key_id: str | None
    pending_mutation: JournalMutation | None
    completed_mutation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_string(self.transaction_id, field="transaction_id")
        if self.phase not in {"prepared", "committed", "complete"}:
            raise StateRecordError("phase must be prepared, committed, or complete")
        _require_string(self.prior_release_id, field="prior_release_id", nullable=True)
        _require_string(self.candidate_release_id, field="candidate_release_id")
        _require_string(self.resolver_bundle_id, field="resolver_bundle_id")
        _require_string(self.staged_key_id, field="staged_key_id", nullable=True)
        if not isinstance(self.completed_mutation_ids, tuple) or not all(
            isinstance(value, str) and value for value in self.completed_mutation_ids
        ):
            raise StateRecordError("completed_mutation_ids must contain non-empty strings")
        if len(set(self.completed_mutation_ids)) != len(self.completed_mutation_ids):
            raise StateRecordError("completed_mutation_ids must not contain duplicates")
        if self.phase == "complete" and self.pending_mutation is not None:
            raise StateRecordError("complete journal cannot contain a pending mutation")
        if (
            self.pending_mutation is not None
            and self.pending_mutation.mutation_id in self.completed_mutation_ids
        ):
            raise StateRecordError("pending mutation cannot already be completed")

    def to_dict(self) -> dict[str, object]:
        return {"version": JOURNAL_VERSION, **asdict(self)}

    def save(self, path: Path, *, state_root: Path) -> None:
        _atomic_json_replace(Path(path), self.to_dict(), state_root=state_root)

    @classmethod
    def load(cls, path: Path, *, state_root: Path) -> "TransactionJournal":
        try:
            payload = _read_json_object(
                Path(path), state_root=state_root, label="transaction journal"
            )
            if payload.get("version") != JOURNAL_VERSION:
                raise StateRecordError("unsupported transaction journal version")
            required = {
                "version",
                "transaction_id",
                "phase",
                "prior_release_id",
                "candidate_release_id",
                "resolver_bundle_id",
                "staged_key_id",
                "pending_mutation",
                "completed_mutation_ids",
            }
            if set(payload) != required:
                raise StateRecordError("transaction journal fields are incomplete or unknown")
            pending = payload["pending_mutation"]
            completed = payload["completed_mutation_ids"]
            if not isinstance(completed, list):
                raise StateRecordError("completed_mutation_ids must be a JSON array")
            return cls(
                transaction_id=_require_string(payload["transaction_id"], field="transaction_id"),  # type: ignore[arg-type]
                phase=payload["phase"],  # type: ignore[arg-type]
                prior_release_id=_require_string(
                    payload["prior_release_id"], field="prior_release_id", nullable=True
                ),
                candidate_release_id=_require_string(
                    payload["candidate_release_id"], field="candidate_release_id"
                ),  # type: ignore[arg-type]
                resolver_bundle_id=_require_string(
                    payload["resolver_bundle_id"], field="resolver_bundle_id"
                ),  # type: ignore[arg-type]
                staged_key_id=_require_string(
                    payload["staged_key_id"], field="staged_key_id", nullable=True
                ),
                pending_mutation=None
                if pending is None
                else JournalMutation.from_dict(pending),
                completed_mutation_ids=tuple(completed),  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            raise
        except StateRecordError as exc:
            if str(exc).startswith("invalid transaction journal"):
                raise
            raise StateRecordError(f"invalid transaction journal at {path}: {exc}") from exc


def snapshot_path_state(path: Path) -> dict[str, object]:
    """Capture exact type, permission, size, and digest state without following links."""
    path = Path(path)
    try:
        descriptor = _open_snapshot_descriptor(path)
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            return {"kind": "symlink", "target": os.readlink(path)}
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            return {"kind": "directory", "mode": mode}
        if not stat.S_ISREG(metadata.st_mode):
            return {"kind": "other", "mode": mode}
        raise
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            return {
                "kind": "file",
                "mode": mode,
                "size": metadata.st_size,
                "sha256": digest.hexdigest(),
            }
        if stat.S_ISDIR(metadata.st_mode):
            return {"kind": "directory", "mode": mode}
        return {"kind": "other", "mode": mode}
    finally:
        os.close(descriptor)


def recover_pending_mutation(
    journal: TransactionJournal,
    *,
    manifest: "Manifest",
    apply_mutation: Callable[[JournalMutation], None],
) -> TransactionJournal:
    """Adopt, apply, or fail closed by comparing one pending path's exact state."""
    mutation = journal.pending_mutation
    if mutation is None:
        return journal
    actual = snapshot_path_state(Path(mutation.path))
    if actual == mutation.intended_after:
        pass
    elif actual == mutation.expected_before:
        apply_mutation(mutation)
        actual = snapshot_path_state(Path(mutation.path))
        if actual != mutation.intended_after:
            raise StateRecordError(
                f"pending mutation {mutation.mutation_id} did not reach intended state"
            )
    else:
        raise StateRecordError(
            f"pending mutation {mutation.mutation_id} is in a third state"
        )
    if mutation.ownership_entry is not None:
        ownership = dict(mutation.ownership_entry)
        kind = ownership.pop("kind")
        path = ownership.pop("path")
        manifest.record(kind, path=path, **ownership)
    return replace(
        journal,
        pending_mutation=None,
        completed_mutation_ids=(
            journal.completed_mutation_ids
            if mutation.mutation_id in journal.completed_mutation_ids
            else (*journal.completed_mutation_ids, mutation.mutation_id)
        ),
    )


def manifest_path(home: Path) -> Path:
    """Canonical manifest location for a given home directory."""
    return manifest_state_root(home) / "install-manifest.json"


def manifest_state_root(home: Path) -> Path:
    """Return the canonical state root confining install records for one home."""
    return resolve_famulus_paths(
        platform=sys.platform, home=Path(home).absolute()
    ).install_state_root


class Manifest:
    """Load/record/save install side effects. Dedupes on (kind, path)."""

    def __init__(self, path: Path, *, state_root: Path) -> None:
        self.path, self.state_root = _confined_record_path(path, state_root)
        self.entries: list[dict] = []
        try:
            data = _read_json_object(
                self.path, state_root=self.state_root, label="install manifest"
            )
        except FileNotFoundError:
            return
        version = data.get("version")
        if version not in {1, MANIFEST_VERSION}:
            raise StateRecordError(f"unsupported install manifest version: {version!r}")
        entries = data.get("entries")
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise StateRecordError("install manifest entries must be a JSON array of objects")
        self.entries = [dict(entry) for entry in entries]

    def record(self, kind: str, *, path: str, **fields: object) -> None:
        entry = {"kind": kind, "path": path, **fields}
        for i, existing in enumerate(self.entries):
            if existing.get("kind") == kind and existing.get("path") == path:
                self.entries[i] = entry
                break
        else:
            self.entries.append(entry)
        # Persist immediately: a mid-install crash must not lose the record
        # of side effects already applied (uninstall depends on it).
        self.save()

    def remove(self, entry: dict) -> None:
        self.entries = [e for e in self.entries if e is not entry]

    def forget(self, kind: str, *, path: str) -> None:
        """Drop a stale ownership record identified by kind and path."""
        remaining = [
            entry
            for entry in self.entries
            if not (entry.get("kind") == kind and entry.get("path") == path)
        ]
        if len(remaining) == len(self.entries):
            return
        self.entries = remaining
        self.save()

    def save(self) -> None:
        payload = {"version": MANIFEST_VERSION, "entries": self.entries}
        _atomic_json_replace(self.path, payload, state_root=self.state_root)

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
