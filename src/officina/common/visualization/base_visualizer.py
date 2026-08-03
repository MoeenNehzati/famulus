"""Core orchestration for visualization workflows.

This module defines the generic contract that producers of graph payloads implement and
the orchestrator that ties extraction, validation, rendering, and serving together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from ..repository_paths import resolve_logical_module_path, resolve_python_source_path
from .base_extractor import BaseJsonExtractor
from .artifacts import GraphArtifactWriter
from .base_renderer import BaseRenderer
from .server import GraphServer, start_graph_server


Payload = dict[str, Any]
GraphValidator = Callable[[Payload], None]


class GraphBuildError(RuntimeError):
    """Raised for any fatal failure in graph payload-to-render operations."""


class GraphSourceResolutionError(GraphBuildError):
    """Raised when a source address cannot be mapped to a concrete file."""


class GraphBuildValidationError(GraphBuildError):
    """Raised when payload validation fails."""


class GraphRenderError(GraphBuildError):
    """Raised when rendering output artifacts fails."""


class GraphSourceKind(str, Enum):
    """Kinds of source spec accepted by graph extraction."""

    FILE = "file"
    MODULE = "module"


@dataclass(frozen=True)
class GraphSource:
    """Resolved source descriptor for extraction."""

    kind: GraphSourceKind
    value: str
    resolved_path: Path
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphBuildResult:
    """Result of one graph build."""

    source: GraphSource
    payload: Payload
    payload_path: Path | None
    html_path: Path | None
    served_url: str | None
    server: GraphServer | None = None


def resolve_graph_source(
    source: GraphSource | str | Path,
    *,
    repo_roots: tuple[Path, ...] | None = None,
) -> GraphSource:
    """Normalize caller input into a concrete :class:`GraphSource`."""
    if isinstance(source, GraphSource):
        return source

    raw = str(source).strip()
    if not raw:
        raise GraphSourceResolutionError("source is required")

    raw_path = Path(raw).expanduser()
    if raw_path.is_absolute():
        candidate = raw_path
    else:
        candidate = raw_path.resolve()

    if candidate.exists():
        resolved = resolve_python_source_path(candidate)
        if resolved is None:
            raise GraphSourceResolutionError(f"no .py source found at: {raw}")
        return GraphSource(
            kind=GraphSourceKind.FILE,
            value=raw,
            resolved_path=resolved,
        )

    resolved = resolve_logical_module_path(raw, repo_roots=repo_roots)
    if resolved is None:
        raise GraphSourceResolutionError(f"could not resolve logical module source: {raw}")

    return GraphSource(
        kind=GraphSourceKind.MODULE,
        value=raw,
        resolved_path=resolved,
    )


class BaseVisualizer:
    """Orchestrate extraction, validation, rendering, and serving for one source."""

    def __init__(
        self,
        *,
        extractor: BaseJsonExtractor,
        renderer: BaseRenderer,
        schema: Mapping[str, Any] | str | Path | None = None,
        validator: GraphValidator | None = None,
        strict: bool = True,
        repo_roots: tuple[Path, ...] | None = None,
    ) -> None:
        """Create an orchestrator for one extractor-renderer pair."""
        self.extractor = extractor
        self.renderer = renderer
        self.artifacts = GraphArtifactWriter(renderer)
        self.repo_roots = tuple(repo_roots) if repo_roots is not None else None
        if schema is not None and validator is not None:
            raise ValueError("only one of schema or validator may be provided")
        self.validator = validator if validator is not None else self._coerce_schema_validator(schema)
        self.strict = strict

    @staticmethod
    def _coerce_schema_validator(
        schema: Mapping[str, Any] | str | Path | None,
    ) -> GraphValidator | None:
        """Build a validator function from a schema map or path."""
        if schema is None:
            return None
        if isinstance(schema, (str, Path)):
            schema_path = Path(schema)
            if not schema_path.is_file():
                raise ValueError(f"graph schema is not a readable file: {schema}")
            raw_schema = schema_path.read_text(encoding="utf-8")
            try:
                import json

                parsed = json.loads(raw_schema)
            except Exception as exc:
                raise ValueError(f"could not parse schema JSON from {schema_path}") from exc
        else:
            parsed = dict(schema)
        if not isinstance(parsed, dict):
            raise ValueError("schema must be a mapping for JSON schema validation")

        try:
            from jsonschema import validate
        except Exception as exc:
            raise RuntimeError(
                "jsonschema is required to validate with custom schema"
            ) from exc

        def _validator(payload: dict[str, Any]) -> None:
            validate(instance=payload, schema=parsed)

        return _validator

    def resolve_source(
        self,
        source: GraphSource | str | Path,
        *,
        repo_roots: tuple[Path, ...] | None = None,
    ) -> GraphSource:
        """Normalize a source using standard path/module resolution."""
        return resolve_graph_source(source, repo_roots=repo_roots or self.repo_roots)

    def build_payload(
        self,
        source: GraphSource | str | Path,
        *,
        repo_roots: tuple[Path, ...] | None = None,
        options: dict[str, object] | None = None,
    ) -> Payload:
        """Run extraction, then validate and return payload."""
        resolved_source = self.resolve_source(source, repo_roots=repo_roots)
        if options:
            resolved_source = GraphSource(
                kind=resolved_source.kind,
                value=resolved_source.value,
                resolved_path=resolved_source.resolved_path,
                options=options,
            )
        try:
            payload = self.extractor.extract(resolved_source)
        except GraphBuildError:
            raise
        except Exception as exc:  # pragma: no cover - preserves extractor-specific semantics
            raise GraphBuildError(
                f"failed to extract graph payload from {resolved_source.value}"
            ) from exc

        if self.strict:
            if self.validator is not None:
                try:
                    self.validator(payload)
                except Exception as exc:
                    raise GraphBuildValidationError(
                        f"graph payload invalid for source {resolved_source.value}"
                    ) from exc
            else:
                self.renderer.validate(payload)
        return payload

    def render_payload(
        self,
        source: GraphSource,
        payload: Payload,
        *,
        output_dir: Path | str,
        output_name: str,
        render_html: bool = True,
        write_payload: bool = False,
        write_payload_path: Path | str | None = None,
        reduction_note: str = "",
        apply_transitive_reduction: bool = False,
    ) -> GraphBuildResult:
        """Render one validated payload and return artifact paths."""
        try:
            artifacts = self.artifacts.write(
                payload,
                output_dir=output_dir,
                stem=output_name,
                write_payload=write_payload,
                write_presentation=render_html,
                payload_target=write_payload_path,
                reduction_note=reduction_note,
                apply_transitive_reduction=apply_transitive_reduction,
            )
        except Exception as exc:
            raise GraphRenderError(f"failed to write graph artifacts for {output_name}") from exc

        return GraphBuildResult(
            source=source,
            payload=payload,
            payload_path=artifacts.payload,
            html_path=artifacts.presentation,
            served_url=None,
            server=None,
        )

    def serve(self, directory: Path | str, host: str, port: int) -> GraphServer:
        """Start and return a server for a rendered directory."""
        return start_graph_server(directory, host=host, port=port)

    def build(
        self,
        source: GraphSource | str | Path,
        *,
        output_dir: Path | str,
        output_name: str,
        render_html: bool = True,
        write_payload: bool = False,
        write_payload_path: Path | str | None = None,
        reduction_note: str = "",
        apply_transitive_reduction: bool = False,
        serve: bool = False,
        serve_host: str = "127.0.0.1",
        serve_port: int = 8765,
        repo_roots: tuple[Path, ...] | None = None,
    ) -> GraphBuildResult:
        """End-to-end extract-validate-render for one source."""
        resolved_source = self.resolve_source(source, repo_roots=repo_roots)
        payload = self.build_payload(resolved_source, repo_roots=repo_roots)
        result = self.render_payload(
            resolved_source,
            payload,
            output_dir=output_dir,
            output_name=output_name,
            render_html=render_html,
            write_payload=write_payload,
            write_payload_path=write_payload_path,
            reduction_note=reduction_note,
            apply_transitive_reduction=apply_transitive_reduction,
        )
        if serve and result.html_path is not None:
            server = self.serve(result.html_path.parent, serve_host, serve_port)
            result = GraphBuildResult(
                source=result.source,
                payload=result.payload,
                payload_path=result.payload_path,
                html_path=result.html_path,
                served_url=server.url,
                server=server,
            )
        return result


__all__ = [
    "BaseJsonExtractor",
    "BaseVisualizer",
    "GraphBuildError",
    "GraphBuildResult",
    "GraphBuildValidationError",
    "GraphRenderError",
    "GraphSource",
    "GraphSourceKind",
    "GraphSourceResolutionError",
    "resolve_graph_source",
]
