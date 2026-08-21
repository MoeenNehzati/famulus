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
    Charter,
    Fix,
    Reckoning,
    RutterDefinitionError,
    RutterStateError,
    ValidationIssue,
    _EffectRecovery,
)


_SemanticValidator: TypeAlias = Callable[[Reckoning], None]

_RECKONING_KEYS = frozenset({"storage_version", "charter", "fix"})
_CHARTER_KEYS = frozenset({"rutter_id", "definition_version", "data"})
_FIX_KEYS = frozenset(
    {"current_state_id", "revision", "lifecycle", "effect", "diagnostics"}
)
_EFFECT_KEYS = frozenset(
    {"state_id", "revision", "disposition", "repeat_safe"}
)
_DIAGNOSTIC_KEYS = frozenset({"path", "code", "message"})
_RECKONING_SUFFIX = ".reckoning.json"


def _require_reckoning_filename(path: Path) -> None:
    """Require one named ``*.reckoning.json`` durable-authority file."""

    name = path.name
    if not name.endswith(_RECKONING_SUFFIX) or len(name) == len(
        _RECKONING_SUFFIX
    ):
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


def _record(
    value: object,
    *,
    label: str,
    keys: frozenset[str],
) -> Mapping[str, object]:
    """Require an object with exactly one declared set of member names."""

    if not isinstance(value, Mapping):
        raise RutterStateError(f"{label} must be a JSON object")
    actual = set(value)
    if actual != keys:
        detail = []
        missing = sorted(keys.difference(actual))
        unknown = sorted(actual.difference(keys))
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unknown:
            detail.append("unknown " + ", ".join(unknown))
        raise RutterStateError(f"{label} fields are invalid: {'; '.join(detail)}")
    return value


def _string(value: object, *, label: str) -> str:
    """Require one nonempty persisted string."""

    if not isinstance(value, str) or not value:
        raise RutterStateError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    """Require a non-boolean persisted integer at or above ``minimum``."""

    if type(value) is not int or value < minimum:
        raise RutterStateError(f"{label} must be an integer at least {minimum}")
    return value


def _diagnostic_to_mapping(issue: ValidationIssue) -> dict[str, object]:
    """Encode one exact observational diagnostic record."""

    if not isinstance(issue, ValidationIssue):
        raise RutterStateError("Fix diagnostics must be ValidationIssue values")
    return {"path": issue.path, "code": issue.code, "message": issue.message}


def _diagnostic_from_mapping(value: object, *, label: str) -> ValidationIssue:
    """Decode one exact observational diagnostic record."""

    raw = _record(value, label=label, keys=_DIAGNOSTIC_KEYS)
    try:
        return ValidationIssue(
            path=_string(raw["path"], label=f"{label}.path"),
            code=_string(raw["code"], label=f"{label}.code"),
            message=_string(raw["message"], label=f"{label}.message"),
        )
    except ValueError as exc:
        raise RutterStateError(f"{label} is invalid") from exc


def _effect_to_mapping(effect: _EffectRecovery) -> dict[str, object]:
    """Encode one exact private effect-recovery record."""

    if not isinstance(effect, _EffectRecovery):
        raise RutterStateError("Fix effect must be framework recovery data")
    return {
        "state_id": effect.state_id,
        "revision": effect.revision,
        "disposition": effect.disposition,
        "repeat_safe": effect.repeat_safe,
    }


def _effect_from_mapping(value: object, *, label: str) -> _EffectRecovery:
    """Decode one exact private effect-recovery record."""

    raw = _record(value, label=label, keys=_EFFECT_KEYS)
    repeat_safe = raw["repeat_safe"]
    if type(repeat_safe) is not bool:
        raise RutterStateError(f"{label}.repeat_safe must be a boolean")
    return _EffectRecovery(
        state_id=_string(raw["state_id"], label=f"{label}.state_id"),
        revision=_integer(raw["revision"], label=f"{label}.revision"),
        disposition=_string(raw["disposition"], label=f"{label}.disposition"),
        repeat_safe=repeat_safe,
    )


def _charter_to_mapping(charter: Charter) -> dict[str, object]:
    """Encode every Charter field without reflection."""

    if not isinstance(charter, Charter):
        raise RutterStateError("Reckoning charter must be a Charter")
    return {
        "rutter_id": charter.rutter_id,
        "definition_version": charter.definition_version,
        "data": _json_value(charter.data, label="Charter data"),
    }


def _charter_from_mapping(value: object) -> Charter:
    """Decode every Charter field with exact structural types."""

    raw = _record(value, label="Charter", keys=_CHARTER_KEYS)
    data = raw["data"]
    if not isinstance(data, Mapping):
        raise RutterStateError("Charter.data must be a JSON object")
    try:
        return Charter(
            rutter_id=_string(raw["rutter_id"], label="Charter.rutter_id"),
            definition_version=_integer(
                raw["definition_version"],
                label="Charter.definition_version",
                minimum=1,
            ),
            data=data,
        )
    except RutterDefinitionError as exc:
        raise RutterStateError(f"Charter is invalid: {exc}") from exc


def _fix_to_mapping(fix: Fix) -> dict[str, object]:
    """Encode every Fix field without reflection."""

    if not isinstance(fix, Fix):
        raise RutterStateError("Reckoning fix must be a Fix")
    return {
        "current_state_id": fix.current_state_id,
        "revision": fix.revision,
        "lifecycle": fix.lifecycle,
        "effect": None if fix.effect is None else _effect_to_mapping(fix.effect),
        "diagnostics": [
            _diagnostic_to_mapping(issue) for issue in fix.diagnostics
        ],
    }


def _fix_from_mapping(value: object) -> Fix:
    """Decode every Fix field with exact structural types."""

    raw = _record(value, label="Fix", keys=_FIX_KEYS)
    diagnostics = raw["diagnostics"]
    if not isinstance(diagnostics, list):
        raise RutterStateError("Fix.diagnostics must be a JSON array")
    effect = raw["effect"]
    return Fix(
        current_state_id=_string(
            raw["current_state_id"], label="Fix.current_state_id"
        ),
        revision=_integer(raw["revision"], label="Fix.revision"),
        lifecycle=_string(raw["lifecycle"], label="Fix.lifecycle"),
        effect=(
            None
            if effect is None
            else _effect_from_mapping(effect, label="Fix.effect")
        ),
        diagnostics=tuple(
            _diagnostic_from_mapping(item, label=f"Fix.diagnostics[{index}]")
            for index, item in enumerate(diagnostics)
        ),
    )


def _reckoning_to_mapping(reckoning: Reckoning) -> dict[str, object]:
    """Encode the exact schema-version-1 Reckoning hierarchy."""

    if not isinstance(reckoning, Reckoning):
        raise RutterStateError("value must be a Reckoning")
    return {
        "storage_version": reckoning.storage_version,
        "charter": _charter_to_mapping(reckoning.charter),
        "fix": _fix_to_mapping(reckoning.fix),
    }


def _reckoning_from_mapping(
    value: object,
    *,
    semantic_validator: _SemanticValidator | None = None,
) -> Reckoning:
    """Decode exact records, then apply definition-owned semantic validation."""

    raw = _record(value, label="Reckoning", keys=_RECKONING_KEYS)
    storage_version = raw["storage_version"]
    if type(storage_version) is not int or storage_version != 1:
        raise RutterStateError("Reckoning.storage_version must be 1")
    reckoning = Reckoning(
        storage_version=storage_version,
        charter=_charter_from_mapping(raw["charter"]),
        fix=_fix_from_mapping(raw["fix"]),
    )
    if semantic_validator is not None:
        if not callable(semantic_validator):
            raise RutterDefinitionError("semantic_validator must be callable")
        semantic_validator(reckoning)
    return reckoning


def _canonical_reckoning_bytes(reckoning: Reckoning) -> bytes:
    """Encode sorted compact UTF-8 JSON with exactly one trailing newline."""

    return (
        json.dumps(
            _reckoning_to_mapping(reckoning),
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
            raise RutterStateError(
                f"Reckoning JSON contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _reject_non_finite(constant: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""

    raise RutterStateError(
        f"Reckoning JSON contains non-finite number {constant}"
    )


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

    if (
        not isinstance(path, Path)
        or path.is_absolute()
        or bool(path.anchor)
        or not path.parts
    ):
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
