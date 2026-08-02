"""Renderer contract and shared payload handling for visualization graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .graph import Graph

Payload = dict[str, Any]
PayloadValidator = Callable[[Payload], None]

_GRAPH_PAYLOAD_SCHEMA_PATH = Path(__file__).resolve().parent / "graph_specification.schema.json"
_GRAPH_PAYLOAD_SCHEMA: dict[str, Any] | None = None
_jsonschema_import_error: Exception | None = None


class BaseRenderer:
    """Base contract for renderers that consume canonical graph payloads.

    The base class owns generic payload normalization, schema-backed validation,
    artifact writing, and graph-level transformations. Concrete subclasses own
    presentation-specific rendering such as ELK/HTML, Graphviz, or another UI.
    """

    def __init__(
        self,
        *,
        validator: PayloadValidator | None = None,
        graph: Graph | None = None,
    ) -> None:
        """Create a renderer with optional validation and graph strategy."""
        self.validator = validator
        self.graph = graph if graph is not None else Graph()

    def normalize(self, graph_json: Payload) -> dict[str, Any]:
        """Return a normalized payload."""
        return self._normalize_graph_json(graph_json)

    def _normalize_graph_json(self, graph_json: Payload) -> dict[str, Any]:
        """Normalize one payload using renderer-independent canonicalization rules."""
        payload = dict(graph_json)
        payload.setdefault("schema_version", 1)
        entities = []
        for entity in payload.get("entities", []):
            if not isinstance(entity, dict):
                raise TypeError("Each entry in 'entities' must be an object.")

            entity_payload = dict(entity)
            raw_connects = entity_payload.get("connects_to", [])

            if "depends_on" in entity_payload:
                raise ValueError(
                    "Deprecated field 'depends_on' is no longer supported; "
                    "use 'connects_to' with edge objects containing 'to' and 'type'."
                )

            canonical_edges: list[dict[str, Any]] = []
            if raw_connects is None:
                raw_connects = []
            if not isinstance(raw_connects, list):
                raise TypeError("Entity property 'connects_to' must be a list when present.")

            for raw_edge in raw_connects:
                if not isinstance(raw_edge, dict):
                    continue
                canonical_edge = self._edge_from_edge_payload(raw_edge)
                canonical_edges.append(canonical_edge)

            entity_payload["connects_to"] = canonical_edges
            entities.append(entity_payload)

        payload["entities"] = entities
        return payload

    def _edge_from_edge_payload(self, edge: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize one edge payload for strict schema compliance."""
        target = edge.get("to")
        if target is None:
            raise ValueError("Each edge in 'connects_to' must include 'to'.")

        relation_type = edge.get("type")
        if relation_type is None:
            raise ValueError("Each edge in 'connects_to' must include 'type'.")

        normalized = {**edge}
        normalized["to"] = str(target)
        normalized["type"] = str(relation_type)
        return normalized

    def validate(self, graph_json: Payload) -> None:
        """Validate one graph payload."""
        if self.validator is None:
            self._validate_graph_json(graph_json)
        else:
            self.validator(graph_json)

    def render_graph(self, graph_json: Payload, *, reduction_note: str = "") -> str:
        """Render graph JSON as a presentation-specific document string."""
        prepared_graph = self.normalize(graph_json)
        self.validate(prepared_graph)
        return self._render_graph(prepared_graph, reduction_note=reduction_note)

    def render_graph_html(
        self,
        graph_json: Payload,
        *,
        validate: bool = True,
        reduction_note: str = "",
        apply_transitive_reduction: bool = False,
    ) -> str:
        """Render complete HTML for one payload."""
        prepared_graph = self.normalize(graph_json)
        if apply_transitive_reduction:
            prepared_graph, removed = self._apply_transitive_reduction(prepared_graph)
            if removed:
                reduction_note = (
                    f"{reduction_note + '. ' if reduction_note else ''}"
                    f"Graph-theoretic transitive reduction applied: removed {len(removed)} edges."
                )
        if validate:
            self.validate(prepared_graph)
        return self._render_graph(prepared_graph, reduction_note=reduction_note)

    def write_graph_html(
        self,
        graph_json: Payload,
        target: str | Path,
        *,
        validate: bool = True,
        reduction_note: str = "",
        apply_transitive_reduction: bool = False,
    ) -> Path:
        """Write rendered HTML to ``target``."""
        resolved_target = Path(target).resolve()
        resolved_target.write_text(
            self.render_graph_html(
                graph_json,
                validate=validate,
                reduction_note=reduction_note,
                apply_transitive_reduction=apply_transitive_reduction,
            ),
            encoding="utf-8",
        )
        return resolved_target

    def reduce_graph_json_transitive_edges(
        self,
        graph_json: Payload,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Perform graph-level transitive reduction and return a reduced payload."""
        prepared_graph = self.normalize(graph_json)
        return self._apply_transitive_reduction(prepared_graph)

    def _validate_graph_json(self, graph_json: Payload) -> None:
        """Validate a payload with the local JSON schema and graph invariants."""
        prepared_graph = self.normalize(graph_json)
        self._validate_with_json_schema(prepared_graph)

    def _validate_with_json_schema(self, graph_json: dict[str, Any]) -> None:
        """Validate graph payload against the local JSON schema."""
        global _jsonschema_import_error
        if _jsonschema_import_error is not None:
            raise RuntimeError(
                "Could not import jsonschema; install jsonschema to run full visual payload validation."
            ) from _jsonschema_import_error
        try:
            from jsonschema import validate
        except Exception as err:  # pragma: no cover - environment-specific
            _jsonschema_import_error = err
            raise RuntimeError(
                "jsonschema is required for full payload validation. Install jsonschema."
            ) from err

        validate(instance=graph_json, schema=self._load_graph_payload_schema())
        self.graph.validate_graph(graph_json)

    def _apply_transitive_reduction(
        self,
        graph_json: Payload,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply the graph transitive-reduction pass to one payload."""
        return self.graph.reduce_transitive_edges(graph_json)

    def _render_graph(self, graph_json: Payload, reduction_note: str = "") -> str:
        """Render one normalized payload. Subclasses must provide presentation output."""
        raise NotImplementedError("Concrete renderers must implement _render_graph().")

    def _load_graph_payload_schema(self) -> dict[str, Any]:
        """Load the shared graph payload schema once."""
        global _GRAPH_PAYLOAD_SCHEMA
        if _GRAPH_PAYLOAD_SCHEMA is not None:
            return _GRAPH_PAYLOAD_SCHEMA

        if not _GRAPH_PAYLOAD_SCHEMA_PATH.exists():
            raise FileNotFoundError(
                "Visualization graph payload schema not available at "
                f"{_GRAPH_PAYLOAD_SCHEMA_PATH}"
            )
        payload = _GRAPH_PAYLOAD_SCHEMA_PATH.read_text(encoding="utf-8")
        _GRAPH_PAYLOAD_SCHEMA = json.loads(payload)
        return _GRAPH_PAYLOAD_SCHEMA


__all__ = [
    "BaseRenderer",
    "Payload",
    "PayloadValidator",
]
