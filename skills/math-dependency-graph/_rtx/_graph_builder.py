#!/usr/bin/env python3
"""Prepare skill-owned macro inputs and call the shared HTML renderer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from officina.visualization.base_renderer_cli import main as render_html

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

    document = doc.get("document", {})
    entrypoint_text = (
        args.tex_entry
        or document.get("source_entrypoint")
        or document.get("source_file")
    )
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


PRESENTATION_BASE = Path(__file__).resolve().parent.parent / "resources" / "graph-base.json"


def apply_presentation_base(doc: dict, source_path: Path) -> Path:
    """Merge the skill's edge catalog into the payload and enforce the closed vocabulary.

    The graph vocabulary is fixed: every edge is `supports` or `exemplifies`. The
    catalog supplies their labels and styling so the renderer can draw them, and
    supplying it makes an invented edge type a hard error here rather than a
    silently unstyled edge in the viewer.
    """
    base = json.loads(PRESENTATION_BASE.read_text(encoding="utf-8"))
    allowed = {category["id"] for category in base["edge_categories"]}

    offenders: dict[str, int] = {}
    for entity in doc.get("entities", []):
        for edge in entity.get("connects_to", []) or []:
            edge_type = str(edge.get("type", ""))
            if edge_type not in allowed:
                offenders[edge_type] = offenders.get(edge_type, 0) + 1
    if offenders:
        listed = ", ".join(f"{name!r} ({count})" for name, count in sorted(offenders.items()))
        raise SystemExit(
            f"Edge types outside the graph vocabulary {sorted(allowed)}: {listed}. "
            "Record the specific character of a dependency in the edge description "
            "and metadata, not as a new type."
        )

    doc.setdefault("edge_categories", base["edge_categories"])
    ui = doc.setdefault("ui", {})
    ui.setdefault("edge_styles", base["ui"]["edge_styles"])

    merged = source_path.parent / f"{source_path.stem}.rendered.json"
    merged.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return merged


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
    if not isinstance(doc, dict):
        raise SystemExit("Canonical dependency-graph JSON must be an object.")
    macro_path = prepare_macro_file(args, source_path, doc)
    render_source = apply_presentation_base(doc, source_path)
    render_argv = [str(render_source), "--profile", "math-dependency"]
    if args.html_out:
        render_argv.extend(["--html-out", args.html_out])
    if macro_path is not None:
        render_argv.extend(["--macro-file", str(macro_path)])
    if args.reduce_transitive_edges:
        render_argv.append("--reduce-transitive-edges")
    render_html(render_argv)


class Interface(PythonArgvMachineInterface):
    """Compatibility interface for automation hooks that invoke this renderer."""

    prog = "graph_builder.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
