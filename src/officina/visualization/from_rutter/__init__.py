"""Rutter definition visualization."""

from __future__ import annotations

from pathlib import Path

from officina.rutter import Rutter

from ..artifacts import GraphArtifactWriter
from ..elk_html_renderer import ElkHtmlRenderer
from .payload_builder import build_rutter_payload


class RutterVisualizer:
    """Write canonical JSON and interactive HTML for a Rutter class."""

    def __init__(self, *, renderer: ElkHtmlRenderer | None = None) -> None:
        self.artifacts = GraphArtifactWriter(renderer or ElkHtmlRenderer())

    def build(
        self,
        rutter_class: type[Rutter],
        *,
        output_dir: str | Path,
        name: str | None = None,
        write_json: bool = True,
    ) -> list[Path]:
        payload = build_rutter_payload(rutter_class)
        stem = name or str(payload["metadata"]["rutter_id"])
        return self.artifacts.write(
            payload,
            output_dir=output_dir,
            stem=stem,
            write_payload=write_json,
        ).paths()


__all__ = ["RutterVisualizer", "build_rutter_payload"]
