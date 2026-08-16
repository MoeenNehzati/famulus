#!/usr/bin/env python3
"""Prepare skill-owned macro inputs and call the shared HTML renderer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from officina.common.visualization.base_renderer_cli import main as render_html

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
    render_argv = [str(source_path), "--profile", "math-dependency"]
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
