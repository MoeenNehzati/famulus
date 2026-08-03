"""Thin extraction facade for repository-blueprint visualizations."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...blueprint_graph import load_repository_blueprint_graph
from .payload_builder import build_payload_from_repository_graph


def build_blueprint_payload(
    repo_root: str | Path,
    *,
    skills: Iterable[str] | None = None,
) -> dict[str, object]:
    """Load the canonical repository graph and project the requested scope."""
    root = Path(repo_root).resolve()
    graph = load_repository_blueprint_graph(root)
    return build_payload_from_repository_graph(graph, repo_root=root, skills=skills)


__all__ = ["build_blueprint_payload", "build_payload_from_repository_graph"]
