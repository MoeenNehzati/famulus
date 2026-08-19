"""Resolve explicit direct Rutter definitions beneath one Reckoning root.

The registry is the only name-to-definition boundary.  It validates and copies
one caller-provided mapping, confines every caller path beneath its configured
root, and returns an already-bound Rutter.  Compass operates that instance and
does not participate in registry or storage resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, TypeAlias

from officina.rutter.engine import BaseRutter
from officina.rutter.model import (
    Charter,
    JsonValue,
    RutterDefinitionError,
    RutterStateError,
)
from officina.rutter.storage import _ReckoningStore, _confined_reckoning_path


__all__ = ("RutterRegistry",)

_Registration: TypeAlias = tuple[type[BaseRutter], str, int]


class RutterRegistry:
    """Create named direct Rutters or open them from persisted identity."""

    def __init__(
        self,
        rutters: Mapping[str, type[BaseRutter]],
        reckoning_root: Path,
    ) -> None:
        """Validate and detach one explicit direct-definition mapping."""

        if not isinstance(rutters, Mapping):
            raise RutterDefinitionError("rutters must be a mapping")
        if not isinstance(reckoning_root, Path):
            raise RutterDefinitionError("reckoning_root must be a Path")

        by_name: dict[str, _Registration] = {}
        by_identity: dict[str, _Registration] = {}
        for name, rutter_type in rutters.items():
            if not isinstance(name, str) or not name:
                raise RutterDefinitionError(
                    "Rutter registry name must be a non-empty string"
                )
            rutter_id, definition_version = self._definition_identity(rutter_type)
            if rutter_id in by_identity:
                raise RutterDefinitionError(f"duplicate rutter_id {rutter_id!r}")
            registration = (rutter_type, rutter_id, definition_version)
            by_name[name] = registration
            by_identity[rutter_id] = registration

        self._by_name = by_name
        self._by_identity = by_identity
        self._reckoning_root = reckoning_root.absolute()

    @staticmethod
    def _definition_identity(rutter_type: object) -> tuple[str, int]:
        """Require one exact direct subclass with valid immutable identity."""

        if (
            not isinstance(rutter_type, type)
            or not issubclass(rutter_type, BaseRutter)
            or rutter_type.__bases__ != (BaseRutter,)
        ):
            raise RutterDefinitionError(
                "Rutter registrants must be a direct BaseRutter subclass"
            )
        rutter_id = getattr(rutter_type, "rutter_id", None)
        definition_version = getattr(rutter_type, "definition_version", None)
        Charter(rutter_id, definition_version, {})
        return rutter_id, definition_version

    @classmethod
    def _current_registration(cls, registration: _Registration) -> _Registration:
        """Reject class metadata changed after the registry froze its binding."""

        rutter_type, rutter_id, definition_version = registration
        if cls._definition_identity(rutter_type) != (rutter_id, definition_version):
            raise RutterDefinitionError(
                "Rutter identity or definition version changed after registration"
            )
        return registration

    def _path(self, reckoning_path: Path) -> Path:
        """Resolve one caller path as a lexical descendant of the registry root."""

        return _confined_reckoning_path(self._reckoning_root, reckoning_path)

    def create(
        self,
        name: str,
        reckoning_path: Path,
        charter_data: Mapping[str, JsonValue],
    ) -> BaseRutter:
        """Create and return one exact Rutter bound to a new Reckoning."""

        if not isinstance(name, str) or name not in self._by_name:
            raise RutterStateError(f"unknown Rutter {name!r}")
        rutter_type, rutter_id, definition_version = self._current_registration(
            self._by_name[name]
        )
        charter = Charter(
            rutter_id=rutter_id,
            definition_version=definition_version,
            data=charter_data,
        )
        return rutter_type.create(self._path(reckoning_path), charter)

    def open(self, reckoning_path: Path) -> BaseRutter:
        """Resolve a strict Reckoning identity and return its exact bound Rutter."""

        path = self._path(reckoning_path)
        reckoning = _ReckoningStore(path).read()
        registration = self._by_identity.get(reckoning.charter.rutter_id)
        if registration is None:
            raise RutterStateError(
                f"unknown Rutter identity {reckoning.charter.rutter_id!r}"
            )
        rutter_type, _rutter_id, definition_version = self._current_registration(
            registration
        )
        if reckoning.charter.definition_version != definition_version:
            raise RutterStateError(
                "Reckoning Charter definition_version does not match the registered "
                "Rutter definition"
            )
        return rutter_type.open(path)
