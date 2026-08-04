"""Canonical graph-payload preparation and validation.

This module is the format boundary shared by every visualization adapter and
renderer. It contains no repository, blueprint, docstring, or HTML semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .graph import Graph

Payload = dict[str, Any]
PayloadValidator = Callable[[Payload], None]

_SCHEMA_PATH = Path(__file__).resolve().parent / "graph_specification.schema.json"
_SCHEMA: dict[str, Any] | None = None
_VALIDATOR: Any | None = None


class GraphPayloadProcessor:
    """Normalize, validate, and transform canonical graph payloads."""

    def __init__(
        self,
        *,
        validator: PayloadValidator | None = None,
        graph: Graph | None = None,
    ) -> None:
        self.validator = validator
        self.graph = graph or Graph()

    def normalize(self, payload: Payload) -> Payload:
        """Return a detached payload with canonical entity-edge structure."""
        normalized = dict(payload)
        normalized.setdefault("schema_version", 2)
        entities: list[dict[str, Any]] = []
        for entity in normalized.get("entities", []):
            if not isinstance(entity, dict):
                raise TypeError("Each entry in 'entities' must be an object.")
            item = dict(entity)
            if "depends_on" in item:
                raise ValueError(
                    "Deprecated field 'depends_on' is no longer supported; use "
                    "'connects_to' with edge objects containing 'to' and 'type'."
                )
            raw_edges = item.get("connects_to", [])
            if raw_edges is None:
                raw_edges = []
            if not isinstance(raw_edges, list):
                raise TypeError("Entity property 'connects_to' must be a list when present.")
            item["connects_to"] = [
                self._normalize_edge(edge) for edge in raw_edges if isinstance(edge, dict)
            ]
            entities.append(item)
        normalized["entities"] = entities
        return normalized

    @staticmethod
    def _normalize_edge(edge: dict[str, Any]) -> dict[str, Any]:
        target = edge.get("to")
        relation = edge.get("type")
        if target is None:
            raise ValueError("Each edge in 'connects_to' must include 'to'.")
        if relation is None:
            raise ValueError("Each edge in 'connects_to' must include 'type'.")
        return {**edge, "to": str(target), "type": str(relation)}

    def prepare(self, payload: Payload, *, validate: bool = True) -> Payload:
        """Normalize once and optionally validate the resulting payload."""
        prepared = self.normalize(payload)
        if validate:
            self.validate_prepared(prepared)
        return prepared

    def validate_prepared(self, payload: Payload) -> None:
        """Validate an already normalized payload."""
        if self.validator is not None:
            self.validator(payload)
            return
        global _VALIDATOR
        if _VALIDATOR is None:
            _VALIDATOR = jsonschema.Draft7Validator(self.schema())
        _VALIDATOR.validate(payload)
        self.graph.validate_graph(payload)

    def reduce_transitive_edges(self, payload: Payload) -> tuple[Payload, list[Payload]]:
        """Normalize and apply the generic graph transitive-reduction algorithm."""
        return self.graph.reduce_transitive_edges(self.normalize(payload))

    @staticmethod
    def schema() -> dict[str, Any]:
        """Load and cache the canonical graph JSON schema."""
        global _SCHEMA
        if _SCHEMA is None:
            if not _SCHEMA_PATH.is_file():
                raise FileNotFoundError(f"Visualization graph schema not found: {_SCHEMA_PATH}")
            loaded = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Visualization graph schema must be a JSON object")
            _SCHEMA = loaded
        return _SCHEMA


__all__ = ["GraphPayloadProcessor", "Payload", "PayloadValidator"]
