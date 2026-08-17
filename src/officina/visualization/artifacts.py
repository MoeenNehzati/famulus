"""Generic graph artifact emission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .base_renderer import BaseRenderer
from .payload import Payload


@dataclass(frozen=True)
class GraphArtifacts:
    """Paths emitted for one graph payload."""

    payload: Path | None
    presentation: Path | None

    def paths(self) -> list[Path]:
        return [path for path in (self.payload, self.presentation) if path is not None]


class GraphArtifactWriter:
    """Write canonical JSON and renderer output without domain assumptions."""

    def __init__(self, renderer: BaseRenderer) -> None:
        self.renderer = renderer

    def write(
        self,
        payload: Payload,
        *,
        output_dir: str | Path,
        stem: str,
        write_payload: bool = True,
        write_presentation: bool = True,
        payload_target: str | Path | None = None,
        reduction_note: str = "",
        apply_transitive_reduction: bool = False,
    ) -> GraphArtifacts:
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        payload_path = Path(payload_target).resolve() if write_payload and payload_target else (output / f"{stem}.json" if write_payload else None)
        presentation_path = output / f"{stem}.html" if write_presentation else None
        if payload_path is not None:
            payload_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if presentation_path is not None:
            self.renderer.write_graph_html(
                payload,
                presentation_path,
                reduction_note=reduction_note,
                apply_transitive_reduction=apply_transitive_reduction,
            )
        return GraphArtifacts(payload=payload_path, presentation=presentation_path)


__all__ = ["GraphArtifacts", "GraphArtifactWriter"]
