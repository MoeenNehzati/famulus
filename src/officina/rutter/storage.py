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

from officina.rutter.history import (
    ActiveRun,
    CompletedRun,
    KnownFault,
    OpaqueFault,
    Reckoning,
    _EffectRecovery,
)
from officina.rutter.values import (
    MachineResult,
    RutterDefinitionError,
    RutterStateError,
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
_KNOWN_FAULT_REQUIRED_KEYS = frozenset(
    {"category", "run_id", "state_id", "node_entry_id"}
)
_KNOWN_FAULT_OPTIONAL_KEYS = frozenset({"target_state_id", "case_maker_ids"})
_KNOWN_FAULT_KEYS = _KNOWN_FAULT_REQUIRED_KEYS | _KNOWN_FAULT_OPTIONAL_KEYS
_MAX_ACTIVE_DEPTH = 64
_MAX_RECKONING_BYTES = 16 * 1024 * 1024
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


def _decode_effect(value: object) -> _EffectRecovery | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _EFFECT_KEYS:
        raise RutterStateError("active effect recovery has invalid fields")
    string_fields = ("action_id", "owner_run_id", "node_entry_id", "state_id")
    if any(
        type(value[field]) is not str or not value[field] for field in string_fields
    ):
        raise RutterStateError("active effect recovery has invalid identifiers")
    mode = value["mode"]
    if type(mode) is not str or mode not in {"repeat-safe", "non-repeat-safe"}:
        raise RutterStateError("active effect recovery has invalid mode")
    disposition = value["disposition"]
    result = value["result"]
    if type(disposition) is not str or disposition not in {
        "planned",
        "completed",
        "uncertain",
    }:
        raise RutterStateError("active effect recovery has invalid disposition")
    if (disposition == "completed") != (result is not None):
        raise RutterStateError("active effect recovery has inconsistent result")
    typed_result = None if result is None else MachineResult.from_json(result)
    return _EffectRecovery(
        value["action_id"],
        value["owner_run_id"],
        value["node_entry_id"],
        value["state_id"],
        mode,
        disposition,
        typed_result,
    )


def _decode_fault(value: object) -> KnownFault | OpaqueFault | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RutterStateError("fault must be a finite JSON object")
    if not (set(value) & _KNOWN_FAULT_KEYS):
        return OpaqueFault(value)
    if (
        not _KNOWN_FAULT_REQUIRED_KEYS <= set(value)
        or not set(value) <= _KNOWN_FAULT_KEYS
    ):
        raise RutterStateError("known fault has invalid fields")
    required = ("category", "run_id", "state_id", "node_entry_id")
    if any(type(value[field]) is not str or not value[field] for field in required):
        raise RutterStateError("known fault has invalid identifiers")
    target = value.get("target_state_id")
    if "target_state_id" in value and (type(target) is not str or not target):
        raise RutterStateError("known fault has invalid target state")
    maker_ids = value.get("case_maker_ids", ())
    if not isinstance(maker_ids, (list, tuple)) or any(
        type(maker_id) is not str or not maker_id for maker_id in maker_ids
    ):
        raise RutterStateError("known fault has invalid case maker IDs")
    if "case_maker_ids" in value and not maker_ids:
        raise RutterStateError("known fault has invalid case maker IDs")
    return KnownFault(
        value["category"],
        value["run_id"],
        value["state_id"],
        value["node_entry_id"],
        target,
        tuple(maker_ids),
    )


def _encode_effect(effect: _EffectRecovery | None) -> object:
    if effect is None:
        return None
    return {
        "action_id": effect.machine_id,
        "owner_run_id": effect.owner_run_id,
        "node_entry_id": effect.evolution_entry_id,
        "state_id": effect.evolution_id,
        "mode": effect.mode,
        "disposition": effect.disposition,
        "result": None if effect.result is None else effect.result.to_json(),
    }


def _encode_fault(fault: KnownFault | OpaqueFault | None) -> object:
    if fault is None:
        return None
    if isinstance(fault, OpaqueFault):
        return fault.wire
    encoded: dict[str, object] = {
        "category": fault.category,
        "run_id": fault.run_id,
        "state_id": fault.evolution_id,
        "node_entry_id": fault.evolution_entry_id,
    }
    if fault.target_evolution_id is not None:
        encoded["target_state_id"] = fault.target_evolution_id
    if fault.transition_hook_ids:
        encoded["case_maker_ids"] = fault.transition_hook_ids
    return encoded


def _validate_reckoning(reckoning: Reckoning) -> None:
    """Delegate definition-independent validation to the aggregate owner."""

    if not isinstance(reckoning, Reckoning):
        raise RutterStateError("value must be a Reckoning")
    reckoning.validate()


def _reckoning_from_mapping(
    value: object,
    *,
    semantic_validator: _SemanticValidator | None = None,
) -> Reckoning:
    """Construct exact records, then apply internal and bound semantics."""

    raw = _preflight_mapping(value)
    completed = raw["completed_runs"]
    if not isinstance(completed, Mapping):
        raise RutterStateError("Reckoning completed_runs must be an object")
    try:
        reckoning = Reckoning(
            raw["storage_version"],
            raw["global_revision"],
            ActiveRun.from_json(raw["root"]),
            {
                run_id: CompletedRun.from_json(run)
                for run_id, run in completed.items()
            },
            _decode_effect(raw["active_effect"]),
            _decode_fault(raw["fault"]),
        )
    except RecursionError as exc:
        raise RutterStateError("Reckoning active-child nesting is too deep") from exc
    _validate_reckoning(reckoning)
    if semantic_validator is not None:
        if not callable(semantic_validator):
            raise RutterDefinitionError("semantic_validator must be callable")
        semantic_validator(reckoning)
    return reckoning


def _reckoning_mapping(reckoning: Reckoning) -> Mapping[str, object]:
    """Project one typed Reckoning into its exact version-3 wire mapping."""

    return {
        "storage_version": reckoning.storage_version,
        "global_revision": reckoning.global_revision,
        "root": reckoning.root.to_json(),
        "completed_runs": {
            run_id: run.to_json() for run_id, run in reckoning.completed_runs.items()
        },
        "active_effect": _encode_effect(reckoning.active_effect),
        "fault": _encode_fault(reckoning.fault),
    }


def _canonical_reckoning_bytes(reckoning: Reckoning) -> bytes:
    """Encode sorted compact UTF-8 JSON with exactly one trailing newline."""

    _validate_reckoning(reckoning)
    mapping = _json_value(_reckoning_mapping(reckoning), label="Reckoning")
    encoded = (
        json.dumps(
            mapping,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_RECKONING_BYTES:
        raise RutterStateError("Reckoning JSON exceeds the size limit")
    return encoded


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

    if len(data) > _MAX_RECKONING_BYTES:
        raise RutterStateError("Reckoning JSON exceeds the size limit")

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


class ReckoningStore:
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
