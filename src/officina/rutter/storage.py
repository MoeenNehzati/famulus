"""Persist complete Reckonings as strict, confined canonical JSON.

The codec is deliberately private and record-specific.  It accepts exactly the
current schema, rejects JSON extensions and duplicate keys, and invokes an
optional definition-owned semantic validator only after constructing the full
typed Reckoning.  File transactions and atomic replacement are implemented by
the store below this codec.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from math import isfinite
import os
from pathlib import Path
import stat
from threading import get_ident
from typing import Callable, Iterator, Mapping, TypeAlias

import officina.common.atomic_files as atomic_files

from officina.rutter.model import (
    ActionRecord,
    ActionResult,
    ActiveRun,
    CallRecord,
    DoneRecord,
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
    Turn,
)


_SemanticValidator: TypeAlias = Callable[[Reckoning], None]

_RECKONING_KEYS = frozenset(
    {
        "storage_version",
        "global_revision",
        "root",
        "completed_runs",
        "active_effect",
        "fault",
    }
)
_EFFECT_KEYS = frozenset(
    {
        "action_id",
        "owner_run_id",
        "node_entry_id",
        "state_id",
        "mode",
        "disposition",
        "result",
    }
)
_MAX_ACTIVE_DEPTH = 64
_RECKONING_SUFFIX = ".reckoning.json"


def _require_reckoning_filename(path: Path) -> None:
    """Require one named ``*.reckoning.json`` durable-authority file."""

    name = path.name
    if not name.endswith(_RECKONING_SUFFIX) or len(name) == len(_RECKONING_SUFFIX):
        raise RutterDefinitionError(
            "reckoning path basename must end with .reckoning.json"
        )


def _json_value(value: object, *, label: str) -> object:
    """Copy one already finite model value into JSON-native containers."""

    if value is None or isinstance(value, (bool, str)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise RutterStateError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, tuple):
        return [
            _json_value(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RutterStateError(f"{label} contains a non-string key")
            result[key] = _json_value(item, label=f"{label}.{key}")
        return result
    raise RutterStateError(f"{label} is not finite JSON")


def _unsupported_version() -> RutterStateError:
    return RutterStateError("unsupported Reckoning storage_version; expected 3")


def _preflight_mapping(value: object) -> Mapping[str, object]:
    """Reject legacy versions and pathological recursion before construction."""

    if not isinstance(value, Mapping):
        raise RutterStateError("Reckoning has invalid fields")
    storage_version = value.get("storage_version")
    if type(storage_version) is int and storage_version in {1, 2}:
        raise _unsupported_version()
    if set(value) != _RECKONING_KEYS:
        raise RutterStateError("Reckoning has invalid fields")
    if type(value["storage_version"]) is not int or value["storage_version"] != 3:
        raise _unsupported_version()
    run = value["root"]
    depth = 1
    while isinstance(run, Mapping):
        child = run.get("active_child")
        if child is None or not isinstance(child, Mapping):
            break
        depth += 1
        if depth > _MAX_ACTIVE_DEPTH:
            raise RutterStateError("Reckoning active-child nesting is too deep")
        run = child.get("run")
    return value


def _history_identity(entry: object) -> str:
    if isinstance(entry, CallRecord):
        return entry.call_id
    assert isinstance(entry, (Turn, ActionRecord, DoneRecord))
    return entry.record_id


def _validate_effect(
    reckoning: Reckoning, leaf: ActiveRun, action_ids: set[str]
) -> None:
    effect = reckoning.active_effect
    if effect is None:
        return
    if set(effect) != _EFFECT_KEYS:
        raise RutterStateError("active effect recovery has invalid fields")
    string_fields = ("action_id", "owner_run_id", "node_entry_id", "state_id")
    if any(
        type(effect[field]) is not str or not effect[field] for field in string_fields
    ):
        raise RutterStateError("active effect recovery has invalid identifiers")
    if effect["mode"] not in {"repeat-safe", "non-repeat-safe"}:
        raise RutterStateError("active effect recovery has invalid mode")
    disposition = effect["disposition"]
    result = effect["result"]
    if disposition not in {"planned", "completed", "uncertain"}:
        raise RutterStateError("active effect recovery has invalid disposition")
    if (disposition == "completed") != (result is not None):
        raise RutterStateError("active effect recovery has inconsistent result")
    if result is not None:
        ActionResult.from_json(result)
    if effect["owner_run_id"] != leaf.run_id:
        raise RutterStateError("active effect owner must be the deepest active run")
    if (
        effect["node_entry_id"] != leaf.entered_node.entry_id
        or effect["state_id"] != leaf.entered_node.state_id
    ):
        raise RutterStateError("active effect recovery has stale node coordinates")
    if effect["action_id"] in action_ids:
        raise RutterStateError("active effect action ID was already consumed")


def _validate_reckoning(reckoning: Reckoning) -> None:
    """Validate cross-record and recursive v3 invariants."""

    if not isinstance(reckoning, Reckoning):
        raise RutterStateError("value must be a Reckoning")
    if reckoning.storage_version != 3:
        raise _unsupported_version()

    active_runs: list[ActiveRun] = []
    active_call_ids: list[str] = []
    current = reckoning.root
    while True:
        active_runs.append(current)
        if len(active_runs) > _MAX_ACTIVE_DEPTH:
            raise RutterStateError("Reckoning active-child nesting is too deep")
        if current.active_child is None:
            break
        active_call_ids.append(current.active_child.call_id)
        current = current.active_child.run

    run_ids = [run.run_id for run in active_runs]
    run_ids.extend(reckoning.completed_runs)
    if len(run_ids) != len(set(run_ids)):
        raise RutterStateError("duplicate run IDs")
    entrances = [run.entered_node.entry_id for run in active_runs]
    if len(entrances) != len(set(entrances)):
        raise RutterStateError("duplicate entrance ID")
    entrance_authorities: dict[str, tuple[str, str]] = {}

    def bind_entrance(entry_id: str, state_id: str, owner_id: str) -> None:
        authority = entrance_authorities.get(entry_id)
        if authority is None:
            entrance_authorities[entry_id] = (owner_id, state_id)
            return
        authority_owner, authority_state = authority
        if authority_owner != owner_id:
            raise RutterStateError("entrance owner is not unique")
        if authority_state != state_id:
            raise RutterStateError("entrance state identity is inconsistent")

    for run in active_runs:
        bind_entrance(
            run.entered_node.entry_id,
            run.entered_node.state_id,
            run.run_id,
        )

    call_ids = set(active_call_ids)
    if len(call_ids) != len(active_call_ids):
        raise RutterStateError("duplicate call ID")
    history_ids: set[str] = set()
    action_ids: set[str] = set()
    references = {run_id: 0 for run_id in reckoning.completed_runs}
    graph = {run_id: set() for run_id in reckoning.completed_runs}
    attachment_authorities: set[tuple[str, str]] = set()

    owners: list[tuple[str, tuple[object, ...], bool]] = [
        (run.run_id, run.history, False) for run in active_runs
    ]
    owners.extend(
        (run_id, run.history, True) for run_id, run in reckoning.completed_runs.items()
    )
    for owner_id, history, owner_completed in owners:
        seen_done = False
        edge_sources: dict[str, Turn | ActionRecord | CallRecord | DoneRecord] = {}
        for entry in history:
            identity = _history_identity(entry)
            if identity in history_ids:
                raise RutterStateError("duplicate history record ID")
            history_ids.add(identity)
            if isinstance(entry, (Turn, ActionRecord, DoneRecord)):
                bind_entrance(entry.node_entry_id, entry.state_id, owner_id)
            if isinstance(entry, DoneRecord):
                seen_done = True
                edge_sources[entry.record_id] = entry
                continue
            if seen_done and not (
                isinstance(entry, CallRecord) and entry.site_kind == "attached_case"
            ):
                raise RutterStateError("non-attached record follows DoneRecord")
            if isinstance(entry, ActionRecord):
                if entry.action_id in action_ids:
                    raise RutterStateError("duplicate action ID")
                action_ids.add(entry.action_id)
                edge_sources[entry.record_id] = entry
                continue
            if isinstance(entry, Turn):
                edge_sources[entry.record_id] = entry
                continue
            if not isinstance(entry, CallRecord):
                continue
            if entry.call_id in call_ids:
                raise RutterStateError("duplicate call ID")
            call_ids.add(entry.call_id)
            if entry.completed_run_id not in references:
                raise RutterStateError("CallRecord references unknown completed run")
            references[entry.completed_run_id] += 1
            if owner_completed:
                graph[owner_id].add(entry.completed_run_id)
            if entry.site_kind == "attached_case":
                assert entry.attached_to_edge_id is not None
                source = edge_sources.get(entry.attached_to_edge_id)
                if source is None:
                    raise RutterStateError(
                        "attached edge source must name exactly one earlier "
                        "record in the same run"
                    )
                if source.node_entry_id != entry.node_entry_id:
                    raise RutterStateError(
                        "attached CallRecord must share its source entrance"
                    )
                authority = (entry.site_id, entry.attached_to_edge_id)
                if authority in attachment_authorities:
                    raise RutterStateError("duplicate attachment authority")
                attachment_authorities.add(authority)
            else:
                edge_sources[entry.call_id] = entry

    if any(count != 1 for count in references.values()):
        raise RutterStateError(
            "every completed run must be referenced by exactly one CallRecord"
        )

    indegree = {run_id: 0 for run_id in graph}
    for children in graph.values():
        for child_id in children:
            indegree[child_id] += 1
    ready = [run_id for run_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        run_id = ready.pop()
        visited += 1
        for child_id in graph[run_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
    if visited != len(graph):
        raise RutterStateError("completed-run references are cyclic")
    _validate_effect(reckoning, active_runs[-1], action_ids)


def _reckoning_from_mapping(
    value: object,
    *,
    semantic_validator: _SemanticValidator | None = None,
) -> Reckoning:
    """Construct exact records, then apply internal and bound semantics."""

    raw = _preflight_mapping(value)
    try:
        reckoning = Reckoning.from_json(raw)
    except RecursionError as exc:
        raise RutterStateError("Reckoning active-child nesting is too deep") from exc
    _validate_reckoning(reckoning)
    if semantic_validator is not None:
        if not callable(semantic_validator):
            raise RutterDefinitionError("semantic_validator must be callable")
        semantic_validator(reckoning)
    return reckoning


def _canonical_reckoning_bytes(reckoning: Reckoning) -> bytes:
    """Encode sorted compact UTF-8 JSON with exactly one trailing newline."""

    _validate_reckoning(reckoning)
    mapping = _json_value(reckoning.to_json(), label="Reckoning")
    return (
        json.dumps(
            mapping,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting duplicate member names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RutterStateError(f"Reckoning JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_non_finite(constant: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""

    raise RutterStateError(f"Reckoning JSON contains non-finite number {constant}")


def _decode_reckoning(
    data: bytes,
    *,
    semantic_validator: _SemanticValidator | None = None,
) -> Reckoning:
    """Decode strict UTF-8 JSON into one fully validated Reckoning."""

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except RutterStateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RutterStateError("Reckoning JSON is corrupt") from exc
    return _reckoning_from_mapping(
        value,
        semantic_validator=semantic_validator,
    )


def _confined_reckoning_path(root: Path, path: Path) -> Path:
    """Bind one relative ``*.reckoning.json`` beneath a storage root."""

    if not isinstance(path, Path) or path.is_absolute() or not path.parts:
        raise RutterDefinitionError(
            "reckoning path must be a relative path beneath its root"
        )
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RutterDefinitionError(
            "reckoning path must be a relative path beneath its root"
        )
    _require_reckoning_filename(path)
    return Path(root).absolute() / path


class _ReckoningStore:
    """Operate one confined ``*.reckoning.json`` and its appended lock."""

    def __init__(
        self,
        path: Path,
        *,
        semantic_validator: _SemanticValidator | None = None,
    ) -> None:
        """Bind one absolute confined path and optional definition validator."""

        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise RutterDefinitionError(
                "Reckoning store path must be an absolute confined path"
            )
        _require_reckoning_filename(path)
        if semantic_validator is not None and not callable(semantic_validator):
            raise RutterDefinitionError("semantic_validator must be callable")
        self._path = path
        self._root = path.parent
        self._semantic_validator = semantic_validator
        self._transaction_owner: tuple[int, int] | None = None

    def read(self) -> Reckoning:
        """Reload and strictly validate the authoritative regular file."""

        if self._transaction_owner == (os.getpid(), get_ident()):
            return self._read_authoritative()
        if not self._root.exists():
            raise RutterStateError("cannot read Reckoning file")
        with self.transaction() as reckoning:
            return reckoning

    def create(self, reckoning: Reckoning) -> None:
        """Create one complete authority without replacing any existing entry."""

        data = self._validated_bytes(reckoning)
        self._ensure_parent()
        try:
            created = atomic_files.atomic_create_bytes(
                self._path,
                data,
                allowed_root=self._root,
                mode=0o600,
            )
        except OSError as exc:
            raise RutterStateError("cannot create Reckoning file") from exc
        if not created:
            raise RutterStateError("Reckoning file already exists")

    def replace(self, previous: Reckoning, replacement: Reckoning) -> None:
        """Atomically replace only the byte-exact live predecessor authority.

        One owning transaction may publish multiple effect phases.  Each call
        must therefore supply the exact complete value published by the prior
        phase as ``previous``.
        """

        self._require_transaction_owner()
        replacement_bytes = self._validated_bytes(replacement)
        previous_bytes = _canonical_reckoning_bytes(previous)
        live_bytes = self._read_bytes()
        live = self._decode(live_bytes)
        if live != previous or live_bytes != previous_bytes:
            raise RutterStateError("Reckoning file changed since it was read")
        try:
            atomic_files.atomic_replace_bytes(
                self._path,
                replacement_bytes,
                allowed_root=self._root,
                mode=0o600,
            )
        except OSError as exc:
            raise RutterStateError(
                "Reckoning replacement failed; reopen and inspect the Reckoning "
                "to determine which complete value is authoritative"
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[Reckoning]:
        """Lock, reload, and yield the authoritative complete Reckoning."""

        owner = (os.getpid(), get_ident())
        if self._transaction_owner == owner:
            raise RutterStateError("Reckoning transaction is not reentrant")
        self._ensure_parent()
        lock_path = self._path.with_name(f"{self._path.name}.lock")
        with atomic_files.exclusive_file_lock(
            lock_path,
            allowed_root=self._root,
            mode=0o600,
        ):
            self._transaction_owner = owner
            try:
                yield self._read_authoritative()
            finally:
                self._transaction_owner = None

    def _require_transaction_owner(self) -> None:
        """Reject replacement outside this store's active lock ownership."""

        if self._transaction_owner != (os.getpid(), get_ident()):
            raise RutterStateError(
                "Reckoning replacement requires an active transaction"
            )

    def _validated_bytes(self, reckoning: Reckoning) -> bytes:
        """Validate typed and definition semantics before encoding any write."""

        if not isinstance(reckoning, Reckoning):
            raise RutterStateError("value must be a Reckoning")
        if self._semantic_validator is not None:
            self._semantic_validator(reckoning)
        return _canonical_reckoning_bytes(reckoning)

    def _decode(self, data: bytes) -> Reckoning:
        """Apply strict structural and bound semantic validation to bytes."""

        return _decode_reckoning(
            data,
            semantic_validator=self._semantic_validator,
        )

    def _read_authoritative(self) -> Reckoning:
        """Decode one read performed under this store's active transaction."""

        self._require_transaction_owner()
        return self._decode(self._read_bytes())

    def _read_bytes(self) -> bytes:
        """Read the configured file through the repository no-follow helper."""

        try:
            return atomic_files.read_regular_file_bytes(
                self._path,
                allowed_root=self._root,
            )
        except OSError as exc:
            raise RutterStateError("cannot read Reckoning file") from exc

    def _ensure_parent(self) -> None:
        """Create and validate the store's non-symlink parent chain."""

        self._walk_directories(self._root)

    @staticmethod
    def _walk_directories(directory: Path) -> None:
        """Create one absolute directory chain without descending through links."""

        absolute = directory.absolute()
        current = Path(absolute.anchor)
        for component in absolute.parts[len(current.parts) :]:
            current /= component
            try:
                entry = os.lstat(current)
            except FileNotFoundError:
                try:
                    os.mkdir(current, mode=0o700)
                except FileExistsError:
                    pass
                try:
                    entry = os.lstat(current)
                except FileNotFoundError as exc:
                    raise RutterStateError(
                        "Reckoning path prefix disappeared during creation: "
                        f"{current}"
                    ) from exc
            if stat.S_ISLNK(entry.st_mode):
                raise RutterStateError(
                    f"Reckoning path prefix is a symbolic link: {current}"
                )
            if not stat.S_ISDIR(entry.st_mode):
                raise RutterStateError(
                    f"Reckoning path prefix is not a directory: {current}"
                )
