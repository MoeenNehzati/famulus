#!/usr/bin/env python3
"""Prepare skill-owned macro inputs and call the shared HTML renderer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from officina.visualization.base_renderer_cli import main as render_html


BASE_PAYLOAD_PATH = Path(__file__).resolve().parents[1] / "base.json"


def load_base_payload() -> dict:
    """Load the skill-owned canonical vocabulary used by every extraction.

    Intent
    ------
    Give extraction instructions, validation, and tests one portable source of
    truth for node categories, relationships, and presentation facets.

    Rationale
    ---------
    Keeping domain vocabulary beside the skill prevents the shared renderer
    from inventing math-specific types and styles on each run.

    Pseudocode
    ----------
    - read the adjacent base JSON
    - require one JSON object
    - return the decoded base payload

    Wraps
    -----
    - pathlib.Path.read_text
    - json.loads
    """
    payload = json.loads(BASE_PAYLOAD_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Math dependency graph base must be an object: {BASE_PAYLOAD_PATH}")
    return payload


def _require_base_catalog(
    payload: dict, base: dict, field: str, item_label: str
) -> None:
    """Require every base catalog entry verbatim while allowing appended entries."""
    actual = {
        str(item.get("id")): item
        for item in payload.get(field, [])
        if isinstance(item, dict) and item.get("id")
    }
    for expected in base[field]:
        identifier = str(expected["id"])
        if identifier not in actual:
            raise ValueError(f"math dependency graph is missing base {item_label} {identifier!r}")
        if actual[identifier] != expected:
            raise ValueError(f"math dependency graph redefines base {item_label} {identifier!r}")


def validate_math_payload(payload: dict) -> None:
    """Validate the resolved math-specific contract before shared rendering.

    Intent
    ------
    Ensure an extracted math graph retained the stable base vocabulary and uses
    provenance fields consistently while permitting documented node extensions.

    Rationale
    ---------
    The generic visualization schema cannot prescribe one extractor's semantic
    taxonomy. This boundary rejects drift without teaching the core renderer
    about mathematical environments.

    Pseudocode
    ----------
    - return for payloads outside the math-dependency graph kind
    - require base node, edge, and presentation catalogs unchanged
    - validate base and extended category/type/kind alignment
    - require explicit node and edge provenance and the two relationship types

    Wraps
    -----
    - load_base_payload
    """
    if payload.get("graph_kind") != "math-dependency":
        return
    base = load_base_payload()
    _require_base_catalog(payload, base, "categories", "category")
    _require_base_catalog(payload, base, "edge_categories", "edge category")
    if payload.get("ui", {}).get("edge_styles") != base["ui"]["edge_styles"]:
        raise ValueError("math dependency graph redefines base edge styles")
    if payload.get("ui", {}).get("edge_presentation") != base["ui"]["edge_presentation"]:
        raise ValueError("math dependency graph redefines base edge presentation")

    categories = {
        str(item["id"]): item
        for item in payload.get("categories", [])
        if isinstance(item, dict) and item.get("id")
    }
    for category_id, category in categories.items():
        parent_id = category.get("parent")
        if not parent_id:
            continue
        parent = categories.get(str(parent_id))
        if parent is None:
            raise ValueError(
                f"math category {category_id!r} references unknown parent {parent_id!r}"
            )
        parent_shape = parent.get("shape")
        if category.get("shape") != parent_shape:
            raise ValueError(
                f"math category {category_id!r} must use parent shape {parent_shape!r}"
            )
    supported_edge_types = {item["id"] for item in base["edge_categories"]}
    source_positions: list[int] = []
    for entity in payload.get("entities", []):
        entity_id = str(entity.get("id", ""))
        category_id = str(entity.get("category", ""))
        if category_id not in categories:
            raise ValueError(f"entity {entity_id!r} has unknown math category {category_id!r}")
        category = categories[category_id]
        parent = category.get("parent")
        expected_type = str(parent or category_id)
        if str(entity.get("type", "")) != expected_type:
            raise ValueError(
                f"entity {entity_id!r} type must be {expected_type!r} for category {category_id!r}"
            )
        if parent:
            expected_kind = category_id.split(":", 1)[1] if ":" in category_id else category_id
            if str(entity.get("kind", "")) != expected_kind:
                raise ValueError(
                    f"entity {entity_id!r} kind must be {expected_kind!r} for category {category_id!r}"
                )
        if entity.get("source") not in {"explicit", "inferred"}:
            raise ValueError(f"entity {entity_id!r} must declare source as explicit or inferred")
        position = entity.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position < 0:
            raise ValueError(f"entity {entity_id!r} must declare a nonnegative integer position")
        source_positions.append(position)
        for edge in entity.get("connects_to", []):
            edge_type = edge.get("type")
            if edge_type not in supported_edge_types:
                raise ValueError(f"unsupported mathematical edge type: {edge_type!r}")
            if not isinstance(edge.get("implicit"), bool):
                raise ValueError(
                    f"edge from {entity_id!r} to {edge.get('to')!r} must declare boolean implicit"
                )
    if any(
        current <= previous
        for previous, current in zip(source_positions, source_positions[1:])
    ):
        raise ValueError(
            "math dependency graph entities must use strictly increasing source-order positions"
        )

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
    parser.add_argument(
        "--semantic-ir",
        required=True,
        help="Exact semantic IR compiled into this canonical graph; its SHA-256 must match.",
    )
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
    try:
        validate_math_payload(doc)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    semantic_path = Path(args.semantic_ir).resolve()
    if not semantic_path.exists():
        raise SystemExit(f"Semantic IR not found: {semantic_path}")
    expected_semantic_hash = doc.get("metadata", {}).get("semantic_ir_sha256")
    actual_semantic_hash = hashlib.sha256(semantic_path.read_bytes()).hexdigest()
    if expected_semantic_hash != actual_semantic_hash:
        raise SystemExit(
            "Canonical graph does not match the supplied semantic IR; "
            "recompile the current run instead of reusing another artifact."
        )
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
