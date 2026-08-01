#!/usr/bin/env python3
"""Lightweight wrapper entrypoint for rendering dependency graphs.

Layout, node/edge rendering behavior, containment, and supernode/subnode handling
are owned by the shared renderer in ``officina.common.visualization.base_renderer``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from officina.common.visualization.elk_html_renderer import (
    build_html_with_elk,
)
from officina.common.visualization.base_renderer_cli import (
    reduce_transitive_edges,
    validate_document,
)

TYPE_STYLES = {
    "standing-assumption": {"shape": "hexagon", "color": "#c0392b"},
    "local-assumption": {"shape": "diamond", "color": "#d35400"},
    "definition": {"shape": "roundrect", "color": "#2471a3"},
    "notation": {"shape": "parallelogram", "color": "#148f77"},
    "lemma": {"shape": "ellipse", "color": "#1e8449"},
    "proposition": {"shape": "rect", "color": "#7d6608"},
    "theorem": {"shape": "rect", "color": "#6c3483"},
    "corollary": {"shape": "circle", "color": "#b7950b"},
    "remark": {"shape": "rect", "color": "#616a6b"},
}

try:
    from ._tex_macro_reader import default_output_path, extract_macros, write_macros
except ImportError:  # pragma: no cover - fallback for alternate import paths
    try:
        from _tex_macro_reader import default_output_path, extract_macros, write_macros
    except ImportError:
        default_output_path = None
        extract_macros = None
        write_macros = None


def resolve_entrypoint(entrypoint_text: str, source_path: Path) -> Path:
    """Resolve an entrypoint from CLI/JSON relative to useful roots."""
    entrypoint = Path(entrypoint_text)
    candidates: list[Path] = []
    if entrypoint.is_absolute():
        candidates.append(entrypoint)
    else:
        candidates.extend(
            [
                Path.cwd() / entrypoint,
                source_path.parent / entrypoint,
                source_path.parent.parent / entrypoint,
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def prepare_macro_file(args: argparse.Namespace, source_path: Path, doc: dict) -> Path | None:
    """Find or create the macro file to merge for this render."""
    if args.macro_file:
        return Path(args.macro_file).resolve()

    entrypoint_text = args.tex_entry or doc.get("document", {}).get("source_entrypoint")
    if not entrypoint_text:
        return None

    entrypoint = resolve_entrypoint(entrypoint_text, source_path)
    if not entrypoint.exists():
        if args.tex_entry:
            raise SystemExit(f"TeX entrypoint not found: {entrypoint}")
        return None

    if default_output_path is None or extract_macros is None or write_macros is None:
        raise SystemExit("Macro extraction helper is unavailable.")

    macro_path = default_output_path(entrypoint)
    if args.refresh_macros or args.tex_entry or not macro_path.exists():
        macros = extract_macros(entrypoint)
        write_macros(macros, macro_path)
    return macro_path


def merge_mathjax_macros(doc: dict, macro_file: Path | None) -> int:
    """Merge extracted MathJax macros into ``doc``.

    Macros already present in the graph JSON take precedence because they may be
    hand-normalized for MathJax compatibility.
    """
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


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for rendering canonical dependency JSON to HTML."""
    parser = argparse.ArgumentParser(
        description="Render an interactive HTML dependency graph from canonical JSON."
    )
    parser.add_argument("source", help="Path to the canonical dependency-graph JSON file")
    parser.add_argument("--html-out", dest="html_out", help="Path to write the standalone HTML viewer")
    parser.add_argument(
        "--tex-entry",
        dest="tex_entry",
        help="TeX entrypoint used to extract MathJax macros. Defaults to document.source_entrypoint when present.",
    )
    parser.add_argument(
        "--macro-file",
        dest="macro_file",
        help="MathJax macro JSON file to merge before rendering. Defaults to _build/<entry>-mathjax-macros.json.",
    )
    parser.add_argument(
        "--refresh-macros",
        action="store_true",
        help="Regenerate the default macro file from the TeX entrypoint before rendering.",
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

    doc = json.loads(source_path.read_text(encoding="utf-8"))
    validate_document(doc)

    macro_path = prepare_macro_file(args, source_path, doc)
    macro_count = merge_mathjax_macros(doc, macro_path)
    reduction_note = ""
    removed_edges: list[dict] = []
    if args.reduce_transitive_edges:
        doc, removed_edges = reduce_transitive_edges(doc)
        reduction_note = (
            "Graph-theoretic transitive reduction enabled: "
            f"removed {len(removed_edges)} redundant edges from the rendered view."
        )

    if args.html_out:
        html_path = Path(args.html_out).resolve()
    else:
        build_dir = source_path.parent / "_build"
        build_dir.mkdir(exist_ok=True)
        html_path = build_dir / source_path.with_suffix(".html").name

    html_path.write_text(
        build_html_with_elk(doc, reduction_note=reduction_note),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "json": str(source_path),
                "html": str(html_path),
                "entities": len(doc.get("entities", [])),
                "reduced": args.reduce_transitive_edges,
                "removed_edges": len(removed_edges),
                "macro_file": str(macro_path) if macro_path else None,
                "macros_from_file": macro_count,
            },
            indent=2,
        )
    )


class Interface(PythonArgvMachineInterface):
    """Compatibility interface for automation hooks that invoke this renderer."""

    prog = "graph_builder.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
