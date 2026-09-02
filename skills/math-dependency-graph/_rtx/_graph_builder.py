#!/usr/bin/env python3
"""Render validated canonical graph JSON through the shared artifact writer."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.visualization.artifacts import GraphArtifactWriter
from officina.visualization.elk_html_renderer import ElkHtmlRenderer


def main(argv: list[str] | None = None) -> None:
    """Render one self-contained canonical graph without rewriting its JSON.

    Intent
    ------
    Render a canonical dependency graph to the exact requested HTML destination.

    Rationale
    ---------
    The skill renderer must consume only canonical JSON and keep that source byte-for-byte
    unchanged while the shared writer produces the standalone presentation.

    Pseudocode
    ----------
    - set render_inputs = explicit source destination and reduction arguments
    - set render_payload = canonical JSON object from render_inputs
    - set rendered_view = optional transitive reduction of render_payload
    - set temporary_presentation = standalone HTML from rendered_view
    - set html_path = atomic replacement from temporary_presentation
    - return render report

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .GraphArtifactWriter:
      why:
        writes: "Writes the standalone presentation inside the sibling temporary directory."

    InstantiationsFromRepo
    ----------------------
    .ElkHtmlRenderer:
      why:
        constructs: "Creates the shared renderer used for validation, reduction, and HTML output."
    """
    parser = argparse.ArgumentParser(
        description="Render an interactive HTML dependency graph from canonical JSON."
    )
    parser.add_argument("source", help="Path to the canonical dependency-graph JSON file")
    parser.add_argument("--html-out", dest="html_out", help="Path to write the standalone HTML viewer")
    parser.add_argument(
        "--reduce-transitive-edges",
        action="store_true",
        help="Apply graph-theoretic transitive reduction to the rendered view only",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source JSON not found: {source_path}")

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Canonical dependency-graph JSON must be an object.")

    if args.html_out:
        html_path = Path(args.html_out).resolve()
    else:
        html_path = source_path.parent / "_build" / source_path.with_suffix(".html").name

    renderer = ElkHtmlRenderer()
    render_payload = payload
    removed_edges: list[dict] = []
    reduction_note = ""
    if args.reduce_transitive_edges:
        render_payload, removed_edges = renderer.reduce_graph_json_transitive_edges(payload)
        reduction_note = (
            "Graph-theoretic transitive reduction enabled: removed "
            f"{len(removed_edges)} redundant edges from the rendered view."
        )

    html_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".officina-render-",
        dir=html_path.parent,
    ) as temporary_directory:
        artifacts = GraphArtifactWriter(renderer).write(
            render_payload,
            output_dir=temporary_directory,
            stem=html_path.stem or "graph",
            write_payload=False,
            reduction_note=reduction_note,
        )
        if artifacts.presentation is None:  # pragma: no cover - writer contract
            raise RuntimeError("Graph artifact writer did not return an HTML artifact.")
        artifacts.presentation.replace(html_path)
    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(payload["entities"]),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
            },
            indent=2,
        )
    )


class Interface(PythonArgvMachineInterface):
    """Expose canonical JSON rendering to the interface runner.

    Intent
    ------
    Bind the registered interface to the graph builder argv contract.

    Rationale
    ---------
    A small adapter preserves one implementation for direct and dispatched use.

    Pseudocode
    ----------
    - set prog = `graph_builder.py`
    - return interface

    Wraps
    -----
    - none
    """

    prog = "graph_builder.py"

    def run(self, argv: list[str]) -> int:
        """Delegate interface arguments to the graph builder CLI.

        Intent
        ------
        Preserve the CLI argument interpretation and successful exit status.

        Rationale
        ---------
        The registered boundary should not duplicate rendering logic.

        Pseudocode
        ----------
        - @.main(argv)
        - set exit_status = success after rendering completes
        - return exit_status

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        .main:
          why:
            orchestrates: "Runs rendering before the adapter maps void completion to interface success."
        """
        main(argv)
        return 0


if __name__ == "__main__":
    main()
