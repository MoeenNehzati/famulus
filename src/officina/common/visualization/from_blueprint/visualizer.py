"""Artifact orchestration for repository blueprint visualizations."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from ..elk_html_renderer import ElkHtmlRenderer
from ..artifacts import GraphArtifactWriter
from .extractor import build_blueprint_payload


class BlueprintVisualizer:
    """Build canonical JSON and standalone HTML from repository blueprints."""

    def __init__(self, *, renderer: ElkHtmlRenderer | None = None) -> None:
        self.renderer = renderer or ElkHtmlRenderer()
        self.artifacts = GraphArtifactWriter(self.renderer)

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
        stem = name or ("repository" if not selected else "-".join(item.rsplit(".", 1)[-1] for item in selected))
        return self.artifacts.write(
            payload,
            output_dir=output_dir,
            stem=stem,
            write_payload=write_json,
        ).paths()


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
