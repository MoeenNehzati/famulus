"""Artifact orchestration for repository blueprint visualizations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from ..elk_html_renderer import ElkHtmlRenderer
from .extractor import build_blueprint_payload


class BlueprintVisualizer:
    """Build canonical JSON and standalone HTML from repository blueprints."""

    def __init__(self, *, renderer: ElkHtmlRenderer | None = None) -> None:
        self.renderer = renderer or ElkHtmlRenderer()

    def build(
        self,
        repo_root: str | Path,
        *,
        skills: Iterable[str] | None = None,
        output_dir: str | Path,
        name: str | None = None,
        write_json: bool = True,
    ) -> list[Path]:
        root = Path(repo_root).resolve()
        selected = tuple(skills or ())
        payload = build_blueprint_payload(root, skills=selected)
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        stem = name or ("repository" if not selected else "-".join(item.rsplit(".", 1)[-1] for item in selected))

        produced: list[Path] = []
        if write_json:
            json_path = output / f"{stem}.json"
            json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            produced.append(json_path)
        html_path = output / f"{stem}.html"
        self.renderer.write_graph_html(payload, html_path)
        produced.append(html_path)
        return produced


def build_blueprint_graph(
    repo_root: str | Path,
    *,
    skills: Iterable[str] | None = None,
    output_dir: str | Path,
    name: str | None = None,
    write_json: bool = True,
) -> list[Path]:
    """Write JSON and HTML artifacts for a repository blueprint scope."""
    return BlueprintVisualizer().build(
        repo_root,
        skills=skills,
        output_dir=output_dir,
        name=name,
        write_json=write_json,
    )


def main(argv: list[str] | None = None) -> int:
    """Render a whole repository or selected skills from canonical blueprints."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", nargs="?", default=".")
    parser.add_argument("--skills", nargs="*", default=None)
    parser.add_argument("--output-dir", default="graphs/blueprint")
    parser.add_argument("--name", default=None)
    parser.add_argument("--no-json", action="store_true")
    args = parser.parse_args(argv)
    paths = build_blueprint_graph(
        args.repo_root,
        skills=args.skills,
        output_dir=args.output_dir,
        name=args.name,
        write_json=not args.no_json,
    )
    for path in paths:
        print(path)
    return 0


__all__ = ["BlueprintVisualizer", "build_blueprint_graph", "main"]
