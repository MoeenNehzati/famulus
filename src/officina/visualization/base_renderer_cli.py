#!/usr/bin/env python3
"""CLI entrypoint for rendering canonical dependency graphs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .elk_html_renderer import ElkHtmlRenderer, build_html_with_elk

_DEFAULT_RENDERER = ElkHtmlRenderer()


def prepare_render_payload(doc: dict, *, profile: str | None = None) -> dict:
    """Copy a payload without applying domain presentation policy.

    Intent
    ------
    Prepare an isolated renderer payload while preserving canonical content.

    Rationale
    ---------
    Domain skills resolve their own catalogs, so the shared renderer only needs a shallow
    payload copy with independently copied entity mappings. The profile argument remains
    accepted for CLI compatibility across extractors and machines.

    Pseudocode
    ----------
    - set prepared = shallow copy of doc
    - set entities = shallow copies of doc entities
    - return prepared

    Wraps
    -----
    - none
    """
    prepared = dict(doc)
    entities = [dict(entity) for entity in prepared.get("entities", [])]
    prepared["entities"] = entities
    return prepared


def validate_document(doc: dict) -> None:
    """Validate the payload expected by the shared renderer.

    Intent
    ------
    Apply the shared renderer schema checks to one canonical payload.

    Rationale
    ---------
    A named boundary keeps CLI validation behavior reusable and independently testable.

    Pseudocode
    ----------
    - set validation_result = default renderer validation of doc
    - return validation_result

    Wraps
    -----
    - none
    """
    _DEFAULT_RENDERER.validate(doc)


def reduce_transitive_edges(doc: dict) -> tuple[dict, list[dict]]:
    """Reduce transitive edges in the rendered view only.

    Intent
    ------
    Return a reduced presentation copy and the redundant edges removed from it.

    Rationale
    ---------
    Reduction is presentation policy and must never mutate the canonical graph payload.

    Pseudocode
    ----------
    - return the default renderer transitive reduction result for doc

    Wraps
    -----
    - none
    """
    return _DEFAULT_RENDERER.reduce_graph_json_transitive_edges(doc)


def main(argv: list[str] | None = None) -> int:
    """Render canonical dependency JSON to HTML.

    Intent
    ------
    Validate an explicit canonical JSON source and render its standalone HTML view.

    Rationale
    ---------
    The generic CLI provides shared rendering without access to extractor-specific source
    files or sidecars.

    Pseudocode
    ----------
    - set render_inputs = explicit source destination profile and reduction arguments
    - set doc = prepared and validated canonical source JSON
    - set rendered_view = optional transitive reduction of doc
    - set html = standalone presentation from rendered_view
    - set html_path = resolved presentation destination containing html
    - set report = source and presentation paths
    - return success

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .elk_html_renderer.build_html_with_elk:
      why:
        serializes: "Builds the standalone presentation from the prepared canonical payload."
    .validate_document:
      why:
        validates: "Checks the prepared payload before rendering it."

    InstantiationsFromRepo
    ----------------------
    .prepare_render_payload:
      why:
        constructs: "Creates the isolated payload passed to validation and rendering."
    .reduce_transitive_edges:
      why:
        constructs: "Creates an optional reduced presentation view and removal report."
    """
    parser = argparse.ArgumentParser(
        description="Render an interactive dependency graph from canonical JSON."
    )
    parser.add_argument("source", help="Path to the canonical dependency-graph JSON file")
    parser.add_argument("--html-out", dest="html_out", help="Path to write the standalone HTML viewer")
    parser.add_argument(
        "--profile",
        choices=("math-dependency",),
        help="Optional domain presentation profile applied before rendering.",
    )
    parser.add_argument(
        "--reduce-transitive-edges",
        action="store_true",
        help="Apply graph-theoretic transitive reduction before rendering",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source JSON not found: {source_path}")

    doc = prepare_render_payload(
        json.loads(source_path.read_text(encoding="utf-8")),
        profile=args.profile,
    )
    validate_document(doc)
    reduction_note = ""
    removed_edges: list[dict] = []
    if args.reduce_transitive_edges:
        doc, removed_edges = reduce_transitive_edges(doc)
        reduction_note = (
            f"Graph-theoretic transitive reduction enabled: removed {len(removed_edges)} redundant edges "
            "from the rendered view."
        )

    if args.html_out:
        html_path = Path(args.html_out).resolve()
    else:
        build_dir = source_path.parent / "_build"
        build_dir.mkdir(exist_ok=True)
        html_path = build_dir / source_path.with_suffix(".html").name
    html_path.write_text(build_html_with_elk(doc, reduction_note=reduction_note), encoding="utf-8")

    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(doc["entities"]),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
            },
            indent=2,
        )
    )
    return 0


class Interface(PythonArgvMachineInterface):
    """Expose generic canonical JSON rendering to the interface runner.

    Intent
    ------
    Bind the machine interface to the generic renderer argv contract.

    Rationale
    ---------
    The adapter keeps direct and registered execution on the same CLI implementation.

    Pseudocode
    ----------
    - set prog = `graph_builder.py`
    - return interface

    Wraps
    -----
    - none
    """

    prog = "graph_builder.py"

    def run(self, argv: list[str]) -> int:  # pragma: no cover - simple dispatch
        """Delegate interface arguments to the generic renderer CLI.

        Intent
        ------
        Preserve CLI argument interpretation and exit status.

        Rationale
        ---------
        The registered boundary should not duplicate rendering logic.

        Pseudocode
        ----------
        - return @.main(argv)

        Wraps
        -----
        main -> preprocess: pass argv unchanged; postprocess: return status unchanged; fixed_arguments: none
        """
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
