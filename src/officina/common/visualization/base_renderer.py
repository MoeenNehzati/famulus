"""Renderer contract and shared payload handling for visualization graphs."""

from __future__ import annotations

from pathlib import Path
from .graph import Graph
from .payload import GraphPayloadProcessor, Payload, PayloadValidator


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
        self.payloads = GraphPayloadProcessor(validator=validator, graph=graph)
        self.validator = validator
        self.graph = self.payloads.graph

    def normalize(self, graph_json: Payload) -> dict[str, Any]:
        """Return a normalized payload."""
        return self.payloads.normalize(graph_json)

    def _normalize_graph_json(self, graph_json: Payload) -> dict[str, Any]:
        """Normalize one payload using renderer-independent canonicalization rules."""
        return self.payloads.normalize(graph_json)

    def _edge_from_edge_payload(self, edge: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize one edge payload for strict schema compliance."""
        return self.payloads._normalize_edge(edge)

    def validate(self, graph_json: Payload) -> None:
        """Validate one graph payload."""
        self.payloads.validate_prepared(self.payloads.normalize(graph_json))

    def render_graph(self, graph_json: Payload, *, reduction_note: str = "") -> str:
        """Render graph JSON as a presentation-specific document string."""
        prepared_graph = self.payloads.prepare(graph_json)
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
        prepared_graph = self.payloads.prepare(graph_json, validate=validate)
        if apply_transitive_reduction:
            prepared_graph, removed = self._apply_transitive_reduction(prepared_graph)
            if removed:
                reduction_note = (
                    f"{reduction_note + '. ' if reduction_note else ''}"
                    f"Graph-theoretic transitive reduction applied: removed {len(removed)} edges."
                )
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
        return self.payloads.reduce_transitive_edges(graph_json)

    def _validate_graph_json(self, graph_json: Payload) -> None:
        """Validate a payload with the local JSON schema and graph invariants."""
        self.payloads.validate_prepared(self.payloads.normalize(graph_json))

    def _validate_with_json_schema(self, graph_json: dict[str, Any]) -> None:
        """Validate graph payload against the local JSON schema."""
        self.payloads.validate_prepared(graph_json)

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
        return self.payloads.schema()


__all__ = [
    "BaseRenderer",
    "Payload",
    "PayloadValidator",
]
