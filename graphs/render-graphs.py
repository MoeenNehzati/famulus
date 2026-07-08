#!/usr/bin/env python3
"""Render local ELK diagrams to SVG."""
from __future__ import annotations

import argparse
import filecmp
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from graph_specs import build_specs

ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "graphs"
RENDERER = GRAPH_DIR / "render_graph_with_elk.cjs"
VENDORED_ELK = GRAPH_DIR / "vendor" / "elk.bundled.js"
GRAPH_SPECS = build_specs()
FORMATS = ("svg",)


def validate_graph_spec(name: str, spec: dict) -> list[str]:
    problems: list[str] = []
    if spec.get("direction") not in {"RIGHT", "DOWN"}:
        problems.append(f"graphs/{name}: direction must be RIGHT or DOWN")
    seen_ids: set[str] = set()

    def walk(children: list[dict]) -> None:
        for child in children:
            child_id = child.get("id")
            if not child_id:
                problems.append(f"graphs/{name}: every graph item needs an id")
                continue
            if child_id in seen_ids:
                problems.append(f"graphs/{name}: duplicate id {child_id!r}")
            seen_ids.add(child_id)
            if child.get("kind") == "group":
                walk(child.get("children", []))

    walk(spec.get("children", []))
    for edge in spec.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source not in seen_ids:
            problems.append(f"graphs/{name}: edge source {source!r} is missing")
        if target not in seen_ids:
            problems.append(f"graphs/{name}: edge target {target!r} is missing")
    return problems


def render_svg(graph_name: str, svg_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="famulus-graph-spec-") as tmpdir:
        spec_path = Path(tmpdir) / f"{graph_name}.json"
        spec_path.write_text(json.dumps(GRAPH_SPECS[graph_name]), encoding="utf-8")
        subprocess.run(
            ["node", str(RENDERER), str(spec_path), str(svg_path)],
            check=True,
        )


def render_one(graph_name: str, out_path: Path) -> None:
    fmt = out_path.suffix.removeprefix(".")
    if fmt == "svg":
        render_svg(graph_name, out_path)
        return
    raise ValueError(f"unsupported graph format: {fmt}")


def expected_outputs(graph_name: str, base_dir: Path) -> list[Path]:
    return [base_dir / f"{graph_name}.{fmt}" for fmt in FORMATS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if rendered graphs are out of date")
    args = parser.parse_args()

    if not RENDERER.exists():
        print(f"missing graph renderer: {RENDERER.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if not VENDORED_ELK.exists():
        print(f"missing vendored ELK bundle: {VENDORED_ELK.relative_to(ROOT)}", file=sys.stderr)
        return 1

    style_problems = []
    for name, spec in GRAPH_SPECS.items():
        style_problems.extend(validate_graph_spec(name, spec))
    if style_problems:
        for problem in style_problems:
            print(problem, file=sys.stderr)
        return 1

    if args.check:
        with tempfile.TemporaryDirectory(prefix="famulus-graphs-") as tmpdir:
            tmp = Path(tmpdir)
            stale = []
            for name in GRAPH_SPECS:
                for real_out, tmp_out in zip(expected_outputs(name, GRAPH_DIR), expected_outputs(name, tmp)):
                    render_one(name, tmp_out)
                    if not real_out.exists() or not filecmp.cmp(tmp_out, real_out, shallow=False):
                        stale.append(real_out.relative_to(ROOT))
            if stale:
                for path in stale:
                    print(f"out of date: {path}", file=sys.stderr)
                print("Run `python3 graphs/render-graphs.py`.", file=sys.stderr)
                return 1
        return 0

    for name in GRAPH_SPECS:
        for out_path in expected_outputs(name, GRAPH_DIR):
            render_one(name, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
