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

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import subprocess
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
from officina.common.certificate_intents import (
    CertificateMutationIntent,
    canonical_certificate_intent_bytes,
)
from officina.common.famulus_paths import resolve_famulus_paths

MANIFEST_VERSION = 2
JOURNAL_VERSION = 3
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MUTATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_TRANSACTION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_OPERATION_KEY_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,127})")
_GIT_CONFIG_KEY_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*)+"
)
_ASCII_LOWER_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)
_KEY_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_CERTIFICATE_SELECTOR_NAME = "active-key-id"
_CERTIFICATE_SELECTOR_MODE = 0o600
_CERTIFICATE_SELECTOR_OPERATION = "certificate.selector"
_RESOURCE_KINDS = {"filesystem", "windows_registry", "git_config"}
_MAX_LOGICAL_VALUE_BYTES = 65536
_MAX_FILESYSTEM_FILE_BYTES = 1048576
_MAX_RESOURCE_ID_BYTES = 8192
_PROCESS_CLEANUP_RESERVE_SECONDS = 0.25
_PIPE_DRAIN_GRACE_SECONDS = 0.1
_PROCESS_EVENT_POLL_SECONDS = 0.01
_GIT_REPOSITORY_SELECTION_ENV = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
_MISSING = object()


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
        def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate state-record field")
                result[key] = item
            return result

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=closed_object,
        )
    except FileNotFoundError:
        raise
    except (
        AtomicWriteError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
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


def _require_bounded_text(
    value: object,
    *,
    field: str,
    maximum: int = _MAX_LOGICAL_VALUE_BYTES,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise StateRecordError(f"{field} must be {qualifier}")
    selected = value
    try:
        encoded = selected.encode("utf-8")
    except UnicodeError as exc:
        raise StateRecordError(f"{field} is not valid UTF-8 text") from exc
    if len(encoded) > maximum or "\x00" in selected:
        raise StateRecordError(f"{field} is outside its closed size or character bounds")
    return selected


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


def _require_resource_state(
    value: object, *, field: str, resource_kind: str
) -> dict[str, object]:
    """Validate a bounded closed state for one physical or logical resource."""
    if not isinstance(value, dict):
        raise StateRecordError(f"{field} must be a JSON object")
    kind = value.get("kind")
    if kind == "absent":
        if set(value) != {"kind"}:
            raise StateRecordError(f"{field} fields do not match absent state")
        return {"kind": "absent"}
    if resource_kind == "filesystem":
        selected = _require_state(value, field=field)
        if (
            selected["kind"] == "file"
            and selected["size"] > _MAX_FILESYSTEM_FILE_BYTES
        ):
            raise StateRecordError(f"{field}.size exceeds its closed bound")
        return selected
    if resource_kind == "windows_registry":
        if set(value) != {"kind", "value_type", "value"} or kind != "windows_registry_value":
            raise StateRecordError(
                f"{field} fields do not match windows_registry value state"
            )
        value_type = value["value_type"]
        if (
            isinstance(value_type, bool)
            or not isinstance(value_type, int)
            or value_type not in {1, 2}
        ):
            raise StateRecordError(f"{field}.value_type is invalid")
        selected = _require_bounded_text(
            value["value"], field=f"{field}.value", allow_empty=True
        )
        return {"kind": kind, "value_type": value_type, "value": selected}
    if resource_kind == "git_config":
        if set(value) != {"kind", "value"} or kind != "git_config_value":
            raise StateRecordError(f"{field} fields do not match git_config value state")
        selected = _require_bounded_text(
            value["value"], field=f"{field}.value", allow_empty=True
        )
        return {"kind": kind, "value": selected}
    raise StateRecordError(f"{field} has invalid resource_kind")


def _canonical_json_text(payload: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StateRecordError("state record contains noncanonical JSON data") from exc


def _canonical_filesystem_path(value: object, *, field: str) -> str:
    selected = _require_bounded_text(value, field=field, maximum=_MAX_RESOURCE_ID_BYTES)
    if not Path(selected).is_absolute():
        raise StateRecordError(f"{field} must be absolute")
    if selected != os.path.normpath(selected) or any(
        part in {".", ".."} for part in Path(selected).parts
    ):
        raise StateRecordError(f"{field} must be lexically canonical")
    return selected


def _canonical_registry_parts(
    *, hive: object, key: object, name: object
) -> tuple[str, str, str]:
    def printable_ascii(value: object, *, field: str, maximum: int) -> str:
        selected = _require_bounded_text(value, field=field, maximum=maximum)
        try:
            encoded = selected.encode("ascii")
        except UnicodeError as exc:
            raise StateRecordError(
                f"{field} must use the closed printable ASCII registry grammar"
            ) from exc
        if any(byte < 0x20 or byte > 0x7E for byte in encoded):
            raise StateRecordError(
                f"{field} must use the closed printable ASCII registry grammar"
            )
        return selected.translate(_ASCII_LOWER_TABLE)

    selected_hive = printable_ascii(hive, field="hive", maximum=64)
    if selected_hive not in {"hkcu", "hkey_current_user"}:
        raise StateRecordError("only HKEY_CURRENT_USER registry resources are supported")
    selected_key = printable_ascii(key, field="key", maximum=1024)
    parts = selected_key.split("\\")
    if any(part in {"", ".", ".."} for part in parts):
        raise StateRecordError("registry key is not canonicalizable")
    selected_name = printable_ascii(name, field="name", maximum=255)
    return (
        "HKEY_CURRENT_USER",
        "\\".join(parts),
        selected_name,
    )


def _canonical_git_config_key(value: object, *, field: str = "key") -> str:
    selected = _require_bounded_text(value, field=field, maximum=255)
    if _GIT_CONFIG_KEY_PATTERN.fullmatch(selected) is None:
        raise StateRecordError("Git config key is invalid")
    parts = selected.split(".")
    return ".".join([parts[0].casefold(), *parts[1:-1], parts[-1].casefold()])


def _require_resource_id(value: object, *, resource_kind: str) -> str:
    resource_id = _require_bounded_text(
        value, field="resource_id", maximum=_MAX_RESOURCE_ID_BYTES
    )
    if resource_kind == "filesystem":
        return _canonical_filesystem_path(resource_id, field="filesystem resource_id")
    try:
        payload = json.loads(resource_id)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StateRecordError("logical resource_id must be canonical JSON") from exc
    if not isinstance(payload, dict) or _canonical_json_text(payload) != resource_id:
        raise StateRecordError("logical resource_id must be canonical JSON")
    if resource_kind == "windows_registry":
        if set(payload) != {"hive", "key", "name"} or payload.get("hive") != "HKEY_CURRENT_USER":
            raise StateRecordError("windows_registry resource_id is invalid")
        hive, key, name = _canonical_registry_parts(
            hive=payload.get("hive"), key=payload.get("key"), name=payload.get("name")
        )
        if payload != {"hive": hive, "key": key, "name": name}:
            raise StateRecordError("windows_registry resource_id is not canonical")
    elif resource_kind == "git_config":
        if set(payload) != {"repo", "scope", "key"} or payload.get("scope") != "local":
            raise StateRecordError("git_config resource_id is invalid")
        _canonical_filesystem_path(payload.get("repo"), field="resource_id.repo")
        key = _canonical_git_config_key(payload.get("key"), field="resource_id.key")
        if payload.get("key") != key:
            raise StateRecordError("git_config resource key is not canonical")
    else:
        raise StateRecordError("resource_kind is invalid")
    return resource_id


def _require_ownership_delta(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise StateRecordError("ownership_delta must be a JSON object")
    action = value.get("action")
    if action == "none":
        if set(value) != {"action"}:
            raise StateRecordError("ownership_delta none fields are invalid")
        return {"action": "none"}
    if action == "upsert":
        if set(value) != {"action", "entry"} or not isinstance(value.get("entry"), dict):
            raise StateRecordError("ownership_delta upsert fields are invalid")
        entry = json.loads(_canonical_json_text(dict(value["entry"])))
        _require_bounded_text(entry.get("kind"), field="ownership_delta.entry.kind", maximum=128)
        _require_bounded_text(entry.get("path"), field="ownership_delta.entry.path", maximum=8192)
        return {"action": "upsert", "entry": entry}
    if action == "forget":
        if set(value) != {"action", "kind", "path"}:
            raise StateRecordError("ownership_delta forget fields are invalid")
        kind = _require_bounded_text(value.get("kind"), field="ownership_delta.kind", maximum=128)
        path = _require_bounded_text(value.get("path"), field="ownership_delta.path", maximum=8192)
        return {"action": "forget", "kind": kind, "path": path}
    raise StateRecordError("ownership_delta action is invalid")


def _certificate_selector_snapshot(key_id: str | None) -> dict[str, object]:
    """Return the exact journal snapshot for one canonical selector value."""

    if key_id is None:
        return {"kind": "absent"}
    encoded = (key_id + "\n").encode("ascii")
    return {
        "kind": "file",
        "mode": _CERTIFICATE_SELECTOR_MODE,
        "size": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def windows_registry_resource_id(*, hive: str, key: str, name: str) -> str:
    """Return a canonical logical identity for one supported registry value."""
    canonical_hive, canonical_key, canonical_name = _canonical_registry_parts(
        hive=hive, key=key, name=name
    )
    payload = {
        "hive": canonical_hive,
        "key": canonical_key,
        "name": canonical_name,
    }
    return _canonical_json_text(payload)


def git_config_resource_id(*, repo: Path, key: str) -> str:
    """Return a canonical local Git-config identity without pretending it is a path."""
    selected_key = _canonical_git_config_key(key)
    payload = {
        "repo": os.path.abspath(os.fspath(repo)),
        "scope": "local",
        "key": selected_key,
    }
    return _canonical_json_text(payload)


def mutation_id_for(
    *,
    transaction_id: str,
    operation_key: str,
    kind: str,
    resource_kind: str,
    resource_id: str,
    intended_after: Mapping[str, object],
    ownership_delta: Mapping[str, object],
) -> str:
    """Derive the canonical request identity; the observed before-state is excluded."""
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None
    ):
        raise StateRecordError("transaction_id must be 32 lowercase hexadecimal characters")
    if _OPERATION_KEY_PATTERN.fullmatch(operation_key) is None:
        raise StateRecordError("operation_key is invalid")
    selected_kind = _require_bounded_text(kind, field="kind", maximum=128)
    if resource_kind not in _RESOURCE_KINDS:
        raise StateRecordError("resource_kind is invalid")
    selected_resource_id = _require_resource_id(
        resource_id, resource_kind=resource_kind
    )
    selected_after = _require_resource_state(
        dict(intended_after), field="intended_after", resource_kind=resource_kind
    )
    selected_delta = _require_ownership_delta(dict(ownership_delta))
    identity = {
        "domain": "famulus-install-mutation-v1",
        "transaction_id": transaction_id,
        "operation_key": operation_key,
        "resource_kind": resource_kind,
        "resource_id": selected_resource_id,
        "kind": selected_kind,
        "intended_after": selected_after,
        "ownership_delta": selected_delta,
    }
    return hashlib.sha256(_canonical_json_text(identity).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, init=False)
class JournalMutation:
    """One exact recoverable transition over a physical or logical resource."""

    mutation_id: str
    operation_key: str
    kind: str
    resource_kind: Literal["filesystem", "windows_registry", "git_config"]
    resource_id: str
    expected_before: dict[str, object]
    intended_after: dict[str, object]
    ownership_delta: dict[str, object]

    def __init__(
        self,
        *,
        mutation_id: str,
        kind: str,
        expected_before: Mapping[str, object],
        intended_after: Mapping[str, object],
        operation_key: str | None = None,
        resource_kind: str | None = None,
        resource_id: str | None = None,
        ownership_delta: Mapping[str, object] | None = None,
        path: str | None = None,
        ownership_entry: object = _MISSING,
    ) -> None:
        """Build v3 records; accept the Task-6B selector constructor temporarily."""
        if path is not None:
            if any(
                value is not None
                for value in (
                    operation_key,
                    resource_kind,
                    resource_id,
                    ownership_delta,
                )
            ):
                raise StateRecordError("legacy selector fields cannot be mixed with v3 fields")
            if kind != "certificate_selector":
                raise StateRecordError("legacy path fields are only valid for certificate_selector")
            operation_key = _CERTIFICATE_SELECTOR_OPERATION
            resource_kind = "filesystem"
            resource_id = path
            ownership_delta = (
                {"action": "none"}
                if ownership_entry is _MISSING or ownership_entry is None
                else {"action": "upsert", "entry": ownership_entry}
            )
        elif ownership_entry is not _MISSING:
            raise StateRecordError("ownership_entry is a version-2-only field")
        object.__setattr__(self, "mutation_id", mutation_id)
        object.__setattr__(self, "operation_key", operation_key)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "resource_kind", resource_kind)
        object.__setattr__(self, "resource_id", resource_id)
        object.__setattr__(self, "expected_before", dict(expected_before))
        object.__setattr__(self, "intended_after", dict(intended_after))
        object.__setattr__(
            self,
            "ownership_delta",
            _require_ownership_delta(
                dict(ownership_delta) if ownership_delta is not None else ownership_delta
            ),
        )
        self.__post_init__()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mutation_id, str)
            or _MUTATION_ID_PATTERN.fullmatch(self.mutation_id) is None
        ):
            raise StateRecordError("mutation_id must be 32 lowercase hexadecimal characters")
        if (
            not isinstance(self.operation_key, str)
            or _OPERATION_KEY_PATTERN.fullmatch(self.operation_key) is None
        ):
            raise StateRecordError("operation_key is invalid")
        _require_bounded_text(self.kind, field="kind", maximum=128)
        if self.resource_kind not in _RESOURCE_KINDS:
            raise StateRecordError("resource_kind is invalid")
        _require_resource_id(self.resource_id, resource_kind=self.resource_kind)
        _require_resource_state(
            self.expected_before,
            field="expected_before",
            resource_kind=self.resource_kind,
        )
        _require_resource_state(
            self.intended_after,
            field="intended_after",
            resource_kind=self.resource_kind,
        )
        _require_ownership_delta(self.ownership_delta)

    @property
    def path(self) -> str:
        """Compatibility view for the certificate-selector Task-6B API only."""
        if self.kind != "certificate_selector" or self.resource_kind != "filesystem":
            raise AttributeError("generic v3 mutations do not expose path")
        return self.resource_id

    @property
    def ownership_entry(self) -> dict[str, object] | None:
        """Compatibility view for the certificate-selector Task-6B API only."""
        if self.kind != "certificate_selector":
            raise AttributeError("generic v3 mutations do not expose ownership_entry")
        if self.ownership_delta["action"] == "upsert":
            return dict(self.ownership_delta["entry"])
        return None

    @classmethod
    def from_dict(cls, payload: object) -> "JournalMutation":
        if not isinstance(payload, dict):
            raise StateRecordError("pending_mutation must be a JSON object or null")
        required = {
            "mutation_id",
            "operation_key",
            "kind",
            "resource_kind",
            "resource_id",
            "expected_before",
            "intended_after",
            "ownership_delta",
        }
        if set(payload) != required:
            raise StateRecordError("pending_mutation fields are incomplete or unknown")
        return cls(
            mutation_id=_require_string(payload["mutation_id"], field="mutation_id"),  # type: ignore[arg-type]
            operation_key=_require_string(payload["operation_key"], field="operation_key"),  # type: ignore[arg-type]
            kind=_require_string(payload["kind"], field="kind"),  # type: ignore[arg-type]
            resource_kind=_require_string(payload["resource_kind"], field="resource_kind"),  # type: ignore[arg-type]
            resource_id=_require_string(payload["resource_id"], field="resource_id"),  # type: ignore[arg-type]
            expected_before=payload["expected_before"],  # type: ignore[arg-type]
            intended_after=payload["intended_after"],  # type: ignore[arg-type]
            ownership_delta=payload["ownership_delta"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_v2_certificate_selector(cls, payload: object) -> "JournalMutation":
        if not isinstance(payload, dict):
            raise StateRecordError("version 2 pending_mutation must be an object")
        required = {
            "mutation_id",
            "kind",
            "path",
            "expected_before",
            "intended_after",
            "ownership_entry",
        }
        if set(payload) != required or payload.get("kind") != "certificate_selector":
            raise StateRecordError(
                "version 2 journals may contain only a certificate selector mutation"
            )
        return cls(
            mutation_id=payload["mutation_id"],  # type: ignore[arg-type]
            kind="certificate_selector",
            path=payload["path"],  # type: ignore[arg-type]
            expected_before=payload["expected_before"],  # type: ignore[arg-type]
            intended_after=payload["intended_after"],  # type: ignore[arg-type]
            ownership_entry=payload["ownership_entry"],
        )


@dataclass(frozen=True)
class TransactionJournal:
    """Durable progress record for one managed installer transaction."""

    transaction_id: str
    phase: Literal["preparing", "prepared", "committed", "complete"]
    prior_release_id: str | None
    candidate_release_id: str
    resolver_bundle_id: str
    certificate_key_id: str | None
    certificate_intent: CertificateMutationIntent | None
    certificate_progress: Literal["none", "planned", "staged", "committed"]
    pending_mutation: JournalMutation | None
    completed_mutation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.transaction_id, str)
            or _TRANSACTION_ID_PATTERN.fullmatch(self.transaction_id) is None
        ):
            raise StateRecordError(
                "transaction_id must be 32 lowercase hexadecimal characters"
            )
        if self.phase not in {"preparing", "prepared", "committed", "complete"}:
            raise StateRecordError(
                "phase must be preparing, prepared, committed, or complete"
            )
        _require_string(self.prior_release_id, field="prior_release_id", nullable=True)
        _require_string(self.candidate_release_id, field="candidate_release_id")
        _require_string(self.resolver_bundle_id, field="resolver_bundle_id")
        if self.certificate_progress not in {
            "none",
            "planned",
            "staged",
            "committed",
        }:
            raise StateRecordError("certificate_progress is invalid")
        if self.certificate_key_id is not None and (
            not isinstance(self.certificate_key_id, str)
            or _KEY_ID_PATTERN.fullmatch(self.certificate_key_id) is None
        ):
            raise StateRecordError("certificate_key_id is invalid")
        if self.certificate_intent is not None and not isinstance(
            self.certificate_intent, CertificateMutationIntent
        ):
            raise StateRecordError(
                "certificate_intent must be a CertificateMutationIntent or null"
            )
        if self.certificate_progress == "none":
            if self.certificate_key_id is not None or self.certificate_intent is not None:
                raise StateRecordError(
                    "certificate progress none requires key and intent to be null"
                )
        elif self.certificate_progress in {"planned", "staged"}:
            if self.certificate_key_id is None:
                raise StateRecordError(
                    f"certificate progress {self.certificate_progress} requires a key"
                )
            if self.certificate_intent is None:
                raise StateRecordError(
                    f"certificate progress {self.certificate_progress} requires an intent"
                )
        elif self.certificate_key_id is None:
            raise StateRecordError("certificate progress committed requires a key")
        if self.certificate_intent is not None:
            if self.transaction_id != self.certificate_intent.transaction_id:
                raise StateRecordError("journal transaction does not match certificate intent")
            if self.certificate_key_id != self.certificate_intent.active_key_id:
                raise StateRecordError("certificate key does not match certificate intent")
        if not isinstance(self.completed_mutation_ids, tuple) or not all(
            isinstance(value, str) and _MUTATION_ID_PATTERN.fullmatch(value)
            for value in self.completed_mutation_ids
        ):
            raise StateRecordError(
                "completed_mutation_ids must contain canonical mutation IDs"
            )
        if len(set(self.completed_mutation_ids)) != len(self.completed_mutation_ids):
            raise StateRecordError("completed_mutation_ids must not contain duplicates")
        if self.phase == "preparing" and self.pending_mutation is not None:
            raise StateRecordError("preparing journal cannot contain a pending mutation")
        if self.phase == "complete" and self.certificate_progress != "committed":
            raise StateRecordError(
                "complete journal requires committed certificate progress"
            )
        if self.phase == "complete" and self.pending_mutation is not None:
            raise StateRecordError("complete journal cannot contain a pending mutation")
        if (
            self.pending_mutation is not None
            and self.pending_mutation.mutation_id in self.completed_mutation_ids
        ):
            raise StateRecordError("pending mutation cannot already be completed")
        if (
            self.pending_mutation is not None
            and self.pending_mutation.kind != "certificate_selector"
        ):
            expected_id = mutation_id_for(
                transaction_id=self.transaction_id,
                operation_key=self.pending_mutation.operation_key,
                kind=self.pending_mutation.kind,
                resource_kind=self.pending_mutation.resource_kind,
                resource_id=self.pending_mutation.resource_id,
                intended_after=self.pending_mutation.intended_after,
                ownership_delta=self.pending_mutation.ownership_delta,
            )
            if self.pending_mutation.mutation_id != expected_id:
                raise StateRecordError(
                    "pending mutation ID does not match its canonical request"
                )
        if (
            self.pending_mutation is not None
            and self.pending_mutation.kind == "certificate_selector"
            and self.certificate_progress != "staged"
        ):
            raise StateRecordError(
                "certificate selector mutation requires staged certificate progress"
            )
        if (
            self.pending_mutation is not None
            and self.pending_mutation.kind == "certificate_selector"
            and self.certificate_intent is not None
        ):
            selector = self.pending_mutation
            selector_path = Path(selector.resource_id)
            if (
                selector.operation_key != _CERTIFICATE_SELECTOR_OPERATION
                or selector.resource_kind != "filesystem"
                or selector_path.name != _CERTIFICATE_SELECTOR_NAME
                or any(part in {"", ".", ".."} for part in selector_path.parts)
            ):
                raise StateRecordError(
                    "certificate selector path has invalid lexical shape"
                )
            if selector.ownership_delta != {"action": "none"}:
                raise StateRecordError(
                    "certificate selector ownership must be recorded by certificate recovery"
                )
            expected_selector_id = hashlib.sha256(
                b"famulus-certificate-selector-mutation-v1\x00"
                + canonical_certificate_intent_bytes(self.certificate_intent)
            ).hexdigest()[:32]
            if selector.mutation_id != expected_selector_id:
                raise StateRecordError(
                    "certificate selector mutation ID does not match intent"
                )
            if selector.expected_before != _certificate_selector_snapshot(
                self.certificate_intent.prior_key_id
            ):
                raise StateRecordError(
                    "certificate selector expected-before snapshot does not match intent"
                )
            if selector.intended_after != _certificate_selector_snapshot(
                self.certificate_intent.active_key_id
            ):
                raise StateRecordError(
                    "certificate selector intended-after snapshot does not match intent"
                )

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
            version = payload.get("version")
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version not in {2, JOURNAL_VERSION}
            ):
                raise StateRecordError("unsupported transaction journal version")
            required = {
                "version",
                "transaction_id",
                "phase",
                "prior_release_id",
                "candidate_release_id",
                "resolver_bundle_id",
                "certificate_key_id",
                "certificate_intent",
                "certificate_progress",
                "pending_mutation",
                "completed_mutation_ids",
            }
            if set(payload) != required:
                raise StateRecordError("transaction journal fields are incomplete or unknown")
            pending = payload["pending_mutation"]
            completed = payload["completed_mutation_ids"]
            intent_payload = payload["certificate_intent"]
            if not isinstance(completed, list):
                raise StateRecordError("completed_mutation_ids must be a JSON array")
            parsed_intent = (
                None
                if intent_payload is None
                else CertificateMutationIntent.from_dict(intent_payload)
            )
            if version == 2 and completed:
                expected_completed = (
                    None
                    if parsed_intent is None
                    else hashlib.sha256(
                        b"famulus-certificate-selector-mutation-v1\x00"
                        + canonical_certificate_intent_bytes(parsed_intent)
                    ).hexdigest()[:32]
                )
                if completed != [expected_completed]:
                    raise StateRecordError(
                        "version 2 journals may contain only the certificate selector ID"
                    )
            if version == 2 and pending is not None:
                pending_record = JournalMutation.from_v2_certificate_selector(pending)
            elif pending is None:
                pending_record = None
            else:
                pending_record = JournalMutation.from_dict(pending)
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
                certificate_key_id=_require_string(
                    payload["certificate_key_id"],
                    field="certificate_key_id",
                    nullable=True,
                ),
                certificate_intent=parsed_intent,
                certificate_progress=payload["certificate_progress"],  # type: ignore[arg-type]
                pending_mutation=pending_record,
                completed_mutation_ids=tuple(completed),  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            raise
        except StateRecordError as exc:
            if str(exc).startswith("invalid transaction journal"):
                raise
            raise StateRecordError(f"invalid transaction journal at {path}: {exc}") from exc
        except (TypeError, ValueError):
            raise StateRecordError(
                f"invalid transaction journal at {path}: certificate intent is invalid"
            ) from None


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
            if metadata.st_size > _MAX_FILESYSTEM_FILE_BYTES:
                raise StateRecordError("filesystem observation exceeds its closed bound")
            digest = hashlib.sha256()
            observed_size = 0
            while chunk := os.read(descriptor, 64 * 1024):
                observed_size += len(chunk)
                if observed_size > _MAX_FILESYSTEM_FILE_BYTES:
                    raise StateRecordError(
                        "filesystem observation exceeds its closed bound"
                    )
                digest.update(chunk)
            if observed_size != metadata.st_size:
                raise StateRecordError("filesystem changed during observation")
            return {
                "kind": "file",
                "mode": mode,
                "size": observed_size,
                "sha256": digest.hexdigest(),
            }
        if stat.S_ISDIR(metadata.st_mode):
            return {"kind": "directory", "mode": mode}
        return {"kind": "other", "mode": mode}
    finally:
        os.close(descriptor)


def snapshot_windows_registry_value(
    *, hive: object, key: str, name: str, winreg_module: object | None = None
) -> dict[str, object]:
    """Observe one Windows registry value as a bounded logical state."""
    if winreg_module is None:
        if os.name != "nt":
            raise StateRecordError("Windows registry observation requires Windows")
        import winreg as selected_winreg
    else:
        selected_winreg = winreg_module
    selected_key = _require_bounded_text(key, field="key", maximum=1024)
    selected_name = _require_bounded_text(name, field="name", maximum=255)
    try:
        with selected_winreg.OpenKey(  # type: ignore[attr-defined]
            hive, selected_key, 0, selected_winreg.KEY_READ  # type: ignore[attr-defined]
        ) as opened:
            try:
                value, value_type = selected_winreg.QueryValueEx(  # type: ignore[attr-defined]
                    opened, selected_name
                )
            except FileNotFoundError:
                return {"kind": "absent"}
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError as exc:
        raise StateRecordError("Windows registry observation failed") from exc
    return _require_resource_state(
        {
            "kind": "windows_registry_value",
            "value_type": value_type,
            "value": value,
        },
        field="windows registry observation",
        resource_kind="windows_registry",
    )


class _BoundedCaptureProtocol(asyncio.SubprocessProtocol):
    """Collect subprocess bytes through one event-loop-owned bounded protocol."""

    def __init__(self, *, stdout_limit: int, stderr_limit: int) -> None:
        self._limits = {1: stdout_limit, 2: stderr_limit}
        self._buffers = {1: bytearray(), 2: bytearray()}
        self._closed_pipes: set[int] = set()
        self._changed = asyncio.Event()
        self._connection_closed = asyncio.Event()
        self.transport: asyncio.SubprocessTransport | None = None
        self.overflow = False
        self.failed = False
        self.process_exited_flag = False

    @property
    def standard_output(self) -> bytes:
        """Return the bounded stdout bytes received so far."""
        return bytes(self._buffers[1])

    @property
    def standard_error(self) -> bytes:
        """Return the bounded stderr bytes received so far."""
        return bytes(self._buffers[2])

    @property
    def pipes_closed(self) -> bool:
        """Return whether both parent-side output transports reported closure."""
        return self._closed_pipes == {1, 2}

    @property
    def connection_closed(self) -> bool:
        """Return whether the subprocess transport completed connection teardown."""
        return self._connection_closed.is_set()

    async def wait_for_change(self, *, deadline: float) -> bool:
        """Wait for one coalesced protocol event without extending the deadline."""
        self._changed.clear()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            async with asyncio.timeout(remaining):
                await self._changed.wait()
        except TimeoutError:
            return False
        return True

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Retain the public subprocess transport used for bounded cleanup."""
        self.transport = transport  # type: ignore[assignment]
        self._changed.set()

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        """Append at most the configured bytes and signal the first overflow."""
        if fd not in self._buffers:
            self.failed = True
            self._changed.set()
            return
        destination = self._buffers[fd]
        remaining = self._limits[fd] - len(destination)
        destination.extend(data[:remaining])
        if len(data) > remaining:
            self.overflow = True
            try:
                pipe_transport = (
                    None
                    if self.transport is None
                    else self.transport.get_pipe_transport(fd)
                )
                if pipe_transport is not None:
                    pipe_transport.pause_reading()
            except (OSError, RuntimeError):
                self.failed = True
        self._changed.set()

    def pipe_connection_lost(self, fd: int, exc: Exception | None) -> None:
        """Record pipe closure and reduce every transport error to a closed flag."""
        if fd not in {1, 2}:
            self.failed = True
        else:
            self._closed_pipes.add(fd)
        if exc is not None:
            self.failed = True
        self._changed.set()

    def process_exited(self) -> None:
        """Record direct-child exit after the event loop has reaped it."""
        self.process_exited_flag = True
        self._changed.set()

    def connection_lost(self, exc: Exception | None) -> None:
        """Record completed transport teardown without exposing raw diagnostics."""
        if exc is not None:
            self.failed = True
        self._connection_closed.set()
        self._changed.set()


async def _close_bounded_capture(
    protocol: _BoundedCaptureProtocol,
    transport: asyncio.SubprocessTransport,
    *,
    deadline: float,
) -> bool:
    """Terminate/reap the direct child and close its transports by one deadline."""
    loop = asyncio.get_running_loop()
    cleanup_failed = False
    if transport.get_returncode() is None:
        try:
            transport.terminate()
        except ProcessLookupError:
            pass
        except OSError:
            cleanup_failed = True
        terminate_deadline = min(deadline, loop.time() + 0.05)
        while transport.get_returncode() is None and loop.time() < terminate_deadline:
            await protocol.wait_for_change(
                deadline=min(
                    terminate_deadline, loop.time() + _PROCESS_EVENT_POLL_SECONDS
                )
            )
    if transport.get_returncode() is None:
        try:
            transport.kill()
        except ProcessLookupError:
            pass
        except OSError:
            cleanup_failed = True
    while transport.get_returncode() is None and loop.time() < deadline:
        await protocol.wait_for_change(
            deadline=min(deadline, loop.time() + _PROCESS_EVENT_POLL_SECONDS)
        )

    for descriptor in (1, 2):
        try:
            pipe_transport = transport.get_pipe_transport(descriptor)
            if pipe_transport is not None:
                pipe_transport.close()
        except (OSError, RuntimeError):
            cleanup_failed = True
    try:
        transport.close()
    except (OSError, RuntimeError):
        cleanup_failed = True
    while not protocol.connection_closed and loop.time() < deadline:
        if not await protocol.wait_for_change(deadline=deadline):
            break
    return (
        not cleanup_failed
        and transport.get_returncode() is not None
        and protocol.pipes_closed
        and protocol.connection_closed
    )


async def _bounded_process_capture_async(
    command: list[str],
    *,
    environment: Mapping[str, str],
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[int, bytes, bytes]:
    """Run one child through cancellable byte-capped asynchronous transports."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    cleanup_reserve = min(
        _PROCESS_CLEANUP_RESERVE_SECONDS, timeout_seconds / 2.0
    )
    work_deadline = deadline - cleanup_reserve
    protocol = _BoundedCaptureProtocol(
        stdout_limit=stdout_limit, stderr_limit=stderr_limit
    )
    transport: asyncio.SubprocessTransport | None = None
    timed_out = False
    inherited_pipe = False
    try:
        remaining = work_deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            transport, _ = await loop.subprocess_exec(
                lambda: protocol,
                *command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(environment),
                shell=False,
                bufsize=0,
            )
    except TimeoutError:
        timed_out = True
        transport = protocol.transport
    except (OSError, RuntimeError, NotImplementedError) as exc:
        transport = protocol.transport
        if transport is not None:
            await _close_bounded_capture(protocol, transport, deadline=deadline)
        raise StateRecordError("Git config observation failed") from exc

    if transport is None:
        raise StateRecordError("Git config observation failed")

    while (
        not timed_out
        and transport.get_returncode() is None
        and not protocol.overflow
        and not protocol.failed
    ):
        if not await protocol.wait_for_change(
            deadline=min(work_deadline, loop.time() + _PROCESS_EVENT_POLL_SECONDS)
        ) and loop.time() >= work_deadline:
            timed_out = True
            break

    if (
        transport.get_returncode() is not None
        and not protocol.overflow
        and not protocol.failed
        and not protocol.pipes_closed
    ):
        drain_deadline = min(deadline, loop.time() + _PIPE_DRAIN_GRACE_SECONDS)
        while (
            not protocol.pipes_closed
            and not protocol.overflow
            and not protocol.failed
        ):
            if not await protocol.wait_for_change(deadline=drain_deadline):
                break
        inherited_pipe = not protocol.pipes_closed

    cleanup_ok = await _close_bounded_capture(protocol, transport, deadline=deadline)
    if not cleanup_ok:
        raise StateRecordError("Git config observation cleanup failed")
    if protocol.overflow:
        raise StateRecordError("Git config observation exceeds its closed bound")
    if timed_out or inherited_pipe or protocol.failed:
        raise StateRecordError("Git config observation failed")
    returncode = transport.get_returncode()
    if returncode is None:
        raise StateRecordError("Git config observation cleanup failed")
    return returncode, protocol.standard_output, protocol.standard_error


def _bounded_process_capture(
    command: list[str],
    *,
    environment: Mapping[str, str],
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[int, bytes, bytes]:
    """Bridge the synchronous installer into one private asynchronous runner."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise StateRecordError(
            "Git config observation cannot run inside an active event loop"
        )
    try:
        return asyncio.run(
            _bounded_process_capture_async(
                command,
                environment=environment,
                timeout_seconds=timeout_seconds,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
        )
    except StateRecordError:
        raise
    except (OSError, RuntimeError, NotImplementedError) as exc:
        raise StateRecordError("Git config observation failed") from exc


def snapshot_git_config_value(
    *, repo: Path, key: str, timeout_seconds: float = 5.0
) -> dict[str, object]:
    """Observe one local Git-config value without shell or ambient repository state."""
    repository = Path(os.path.abspath(os.fspath(repo)))
    selected_key = _canonical_git_config_key(key)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise StateRecordError("Git config observation timeout is invalid")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in _GIT_REPOSITORY_SELECTION_ENV
        and not name.startswith("GIT_CONFIG")
    }
    returncode, standard_output, standard_error = _bounded_process_capture(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "--local",
            "--null",
            "--get-all",
            selected_key,
        ],
        environment=environment,
        timeout_seconds=float(timeout_seconds),
        stdout_limit=_MAX_LOGICAL_VALUE_BYTES + 1,
        stderr_limit=_MAX_LOGICAL_VALUE_BYTES,
    )
    if returncode == 1 and standard_output == b"" and standard_error == b"":
        return {"kind": "absent"}
    if returncode != 0 or standard_error:
        raise StateRecordError("Git config observation failed")
    values = standard_output.split(b"\x00")
    if values and values[-1] == b"":
        values.pop()
    if len(values) != 1:
        raise StateRecordError("Git config resource has multiple values")
    try:
        value = values[0].decode("utf-8")
    except UnicodeError as exc:
        raise StateRecordError("Git config value is not UTF-8") from exc
    return _require_resource_state(
        {"kind": "git_config_value", "value": value},
        field="Git config observation",
        resource_kind="git_config",
    )


def _apply_ownership_delta(manifest: "Manifest", delta: Mapping[str, object]) -> None:
    selected = _require_ownership_delta(dict(delta))
    action = selected["action"]
    if action == "none":
        return
    if action == "upsert":
        entry = dict(selected["entry"])
        kind = entry.pop("kind")
        path = entry.pop("path")
        manifest.record(kind, path=path, **entry)  # type: ignore[arg-type]
        return
    manifest.forget(selected["kind"], path=selected["path"])  # type: ignore[arg-type]


class MutationRecorder:
    """Serialize one owner mutation through durable intent and exact observation.

    The journal and manifest's own atomic state-record writes are deliberately
    internal bookkeeping: recursively recording them would make completion
    impossible. Every owner-supplied live effect still passes through ``mutate``.
    """

    def __init__(
        self,
        *,
        journal: TransactionJournal,
        journal_path: Path,
        state_root: Path,
        manifest: "Manifest",
    ) -> None:
        selected_path, selected_root = _confined_record_path(
            Path(journal_path), Path(state_root)
        )
        if manifest.state_root != selected_root:
            raise StateRecordError("recorder journal and manifest state roots differ")
        self.journal = journal
        self.journal_path = selected_path
        self.state_root = selected_root
        self.manifest = manifest

    def _save_journal(self, journal: TransactionJournal) -> None:
        # Internal atomic journal publication is exempt from recursive recording.
        journal.save(self.journal_path, state_root=self.state_root)

    def _save_manifest_delta(self, delta: Mapping[str, object]) -> None:
        before = [dict(entry) for entry in self.manifest.entries]
        try:
            # Manifest.record/forget perform the one internal atomic save.
            _apply_ownership_delta(self.manifest, delta)
        except BaseException:
            self.manifest.entries = before
            raise

    def mutate(
        self,
        *,
        operation_key: str,
        kind: str,
        resource_kind: str,
        resource_id: str,
        intended_after: Mapping[str, object],
        ownership_delta: Mapping[str, object],
        observe: Callable[[], Mapping[str, object]],
        apply: Callable[[], None],
    ) -> str:
        """Journal, apply, verify, own, and complete one deterministic request."""
        selected_after = _require_resource_state(
            dict(intended_after),
            field="intended_after",
            resource_kind=resource_kind,
        )
        selected_delta = _require_ownership_delta(dict(ownership_delta))
        mutation_id = mutation_id_for(
            transaction_id=self.journal.transaction_id,
            operation_key=operation_key,
            kind=kind,
            resource_kind=resource_kind,
            resource_id=resource_id,
            intended_after=selected_after,
            ownership_delta=selected_delta,
        )
        pending = self.journal.pending_mutation
        if pending is not None and pending.kind == "certificate_selector":
            raise StateRecordError(
                "certificate selector recovery requires a recomputed canonical path"
            )
        if pending is not None and pending.mutation_id != mutation_id:
            raise StateRecordError(
                "owner request differs from the durable pending mutation"
            )
        if mutation_id in self.journal.completed_mutation_ids:
            actual = _require_resource_state(
                dict(observe()), field="actual", resource_kind=resource_kind
            )
            if actual != selected_after:
                raise StateRecordError(
                    f"completed mutation {mutation_id} is not in its intended state"
                )
            return mutation_id

        if pending is None:
            expected = _require_resource_state(
                dict(observe()),
                field="expected_before",
                resource_kind=resource_kind,
            )
            pending = JournalMutation(
                mutation_id=mutation_id,
                operation_key=operation_key,
                kind=kind,
                resource_kind=resource_kind,
                resource_id=resource_id,
                expected_before=expected,
                intended_after=selected_after,
                ownership_delta=selected_delta,
            )
            durable_pending = replace(self.journal, pending_mutation=pending)
            self._save_journal(durable_pending)
            self.journal = durable_pending
        else:
            requested = JournalMutation(
                mutation_id=mutation_id,
                operation_key=operation_key,
                kind=kind,
                resource_kind=resource_kind,
                resource_id=resource_id,
                expected_before=pending.expected_before,
                intended_after=selected_after,
                ownership_delta=selected_delta,
            )
            if requested != pending:
                raise StateRecordError(
                    "owner request differs from the durable pending mutation"
                )

        actual = _require_resource_state(
            dict(observe()), field="actual", resource_kind=resource_kind
        )
        if actual == pending.intended_after:
            pass
        elif actual == pending.expected_before:
            apply()
            actual = _require_resource_state(
                dict(observe()), field="actual", resource_kind=resource_kind
            )
            if actual != pending.intended_after:
                raise StateRecordError(
                    f"pending mutation {mutation_id} did not reach intended state"
                )
        else:
            raise StateRecordError(f"pending mutation {mutation_id} is in a third state")

        self._save_manifest_delta(pending.ownership_delta)
        completed = replace(
            self.journal,
            pending_mutation=None,
            completed_mutation_ids=(*self.journal.completed_mutation_ids, mutation_id),
        )
        self._save_journal(completed)
        self.journal = completed
        return mutation_id


def recover_pending_mutation(
    journal: TransactionJournal,
    *,
    manifest: "Manifest",
) -> TransactionJournal:
    """Classify one generic pending path; only adopt an already-intended state."""
    mutation = journal.pending_mutation
    if mutation is None:
        return journal
    if mutation.kind == "certificate_selector":
        raise StateRecordError(
            "certificate selector recovery requires a recomputed canonical path"
        )
    if mutation.resource_kind != "filesystem":
        raise StateRecordError(
            "logical pending mutation recovery requires its exact owner observer"
        )
    actual = snapshot_path_state(Path(mutation.resource_id))
    if actual == mutation.intended_after:
        pass
    elif actual == mutation.expected_before:
        return journal
    else:
        raise StateRecordError(
            f"pending mutation {mutation.mutation_id} is in a third state"
        )
    _apply_ownership_delta(manifest, mutation.ownership_delta)
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
