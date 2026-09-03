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
    """Copy a payload without applying domain-specific presentation policy.

    The profile argument remains accepted for CLI compatibility. Domain skills
    now provide resolved catalogs in canonical JSON, keeping this renderer
    generic across extractors and machines.
    """
    prepared = dict(doc)
    entities = [dict(entity) for entity in prepared.get("entities", [])]
    prepared["entities"] = entities
    return prepared


def validate_document(doc: dict) -> None:
    """Validate the canonical payload expected by the shared renderer."""
    _DEFAULT_RENDERER.validate(doc)


def merge_mathjax_macros(doc: dict, macro_file: Path | None) -> int:
    """Merge extracted MathJax macros from a macro JSON file."""
    if macro_file is None:
        return 0
    if not macro_file.exists():
        raise SystemExit(f"Macro file not found: {macro_file}")
    file_macros = json.loads(macro_file.read_text(encoding="utf-8"))
    if not isinstance(file_macros, dict):
        raise SystemExit(f"Macro file must contain a JSON object: {macro_file}")
    dependencies = doc.setdefault("renderer_dependencies", [])
    mathjax = next((item for item in dependencies if item.get("id") == "mathjax"), None)
    if mathjax is None:
        mathjax = {"id": "mathjax", "version": "3", "configuration": {}}
        dependencies.append(mathjax)
    configuration = mathjax.setdefault("configuration", {})
    json_macros = configuration.get("macros", {})
    if json_macros and not isinstance(json_macros, dict):
        raise SystemExit("MathJax renderer dependency macros must be an object.")
    configuration.update({"input": "tex", "output": "svg", "macros": {**file_macros, **json_macros}})
    return len(file_macros)


def reduce_transitive_edges(doc: dict) -> tuple[dict, list[dict]]:
    """Apply graph-theoretic transitive reduction to the rendered view only."""
    return _DEFAULT_RENDERER.reduce_graph_json_transitive_edges(doc)


def main(
    argv: list[str] | None = None,
    *,
    renderer: ElkHtmlRenderer | None = None,
) -> int:
    """CLI entry point for rendering canonical dependency JSON to HTML."""
    parser = argparse.ArgumentParser(
        description="Render an interactive dependency graph from canonical JSON."
    )
    parser.add_argument("source", help="Path to the canonical dependency-graph JSON file")
    parser.add_argument("--html-out", dest="html_out", help="Path to write the standalone HTML viewer")
    parser.add_argument(
        "--macro-file",
        dest="macro_file",
        help="Optional MathJax macro JSON file to merge before rendering.",
    )
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
    selected_renderer = renderer or _DEFAULT_RENDERER

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source JSON not found: {source_path}")

    doc = prepare_render_payload(
        json.loads(source_path.read_text(encoding="utf-8")),
        profile=args.profile,
    )
    validate_document(doc)
    macro_path = Path(args.macro_file).resolve() if args.macro_file else None
    macro_count = merge_mathjax_macros(doc, macro_path)
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
    html_path.write_text(
        selected_renderer.render_graph_html(doc, reduction_note=reduction_note),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(doc["entities"]),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
                "macro_file": str(macro_path) if macro_path else None,
                "macros_from_file": macro_count,
            },
            indent=2,
        )
    )
    return 0


class Interface(PythonArgvMachineInterface):
    """Runtime-compatible entrypoint object."""

    prog = "graph_builder.py"

    def run(self, argv: list[str]) -> int:  # pragma: no cover - simple dispatch
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
