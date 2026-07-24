"""Generated certificate-backed pooled blueprint reviews."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .blueprint_graph import RepositoryBlueprintGraph
from .certification_view import CertificationView


class PooledReviewValidationError(ValueError):
    """Raised when a certificate-backed pooled review cannot be rendered."""


def pooled_review_path(module_root: Path) -> Path:
    """Return the generated review path for one module."""

    return Path(module_root) / ".pooled-blueprint-review.yaml"


def _review_path(path: Path, module_root: Path) -> str:
    try:
        return path.relative_to(module_root).as_posix()
    except ValueError as exc:
        raise PooledReviewValidationError(
            f"reviewed path is outside its module: {path}"
        ) from exc


def render_pooled_review(
    graph: RepositoryBlueprintGraph,
    certification: CertificationView,
    *,
    root_id: str | None = None,
) -> str:
    """Render a deterministic v4 review from current public certificates."""

    if not isinstance(graph, RepositoryBlueprintGraph):
        raise PooledReviewValidationError(
            "pooled review accepts only a v4 repository graph"
        )
    if not hasattr(certification, "certificate_for"):
        raise PooledReviewValidationError(
            "pooled review requires a read-only certification view"
        )

    modules = sorted(
        node_id
        for node_id, node in graph.nodes.items()
        if node.node_type == "module"
    )
    selected_root = root_id
    if selected_root is None:
        if len(modules) != 1:
            raise PooledReviewValidationError(
                "pooled review requires root_id when the graph has multiple modules"
            )
        selected_root = modules[0]
    root_node = graph.nodes.get(selected_root)
    if root_node is None or root_node.node_type != "module":
        raise PooledReviewValidationError(
            f"unknown pooled-review root module {selected_root!r}"
        )

    children: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    for edge in graph.certification_edges:
        children.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
    selected: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in selected:
            return
        selected.add(node_id)
        for target_id in sorted(children.get(node_id, ())):
            visit(target_id)

    for node_id in (selected_root, *graph.module_sources[selected_root]):
        visit(node_id)
    certificates = {}
    for node_id in sorted(selected):
        certificate = certification.certificate_for(node_id)
        if certificate is None:
            raise PooledReviewValidationError(
                f"{node_id}: pooled review requires a current certificate"
            )
        certificates[node_id] = certificate

    root_certificate = certificates[selected_root]
    nodes: list[dict[str, Any]] = []
    for node_id in sorted(selected):
        node = graph.nodes[node_id]
        certificate = certificates[node_id]
        if node.gateway_path is None:
            raise PooledReviewValidationError(
                f"{node_id}: reviewed node has no gateway"
            )
        nodes.append(
            {
                "id": node.node_id,
                "node_type": node.node_type,
                "version": node.version,
                "blueprint_path": _review_path(
                    node.blueprint_path, node.skill_root
                ),
                "gateway_path": _review_path(
                    node.gateway_path, node.skill_root
                ),
                "declaration": deepcopy(node.declaration),
                "certificate": {
                    "status": "current",
                    "node_hash": certificate.node_hash,
                    "certificate_hash": certificate.certificate_hash,
                },
            }
        )
    document = {
        "schema_version": 2,
        "document_type": "pooled-blueprint-review",
        "generated_at": root_certificate.certified_at,
        "root": {
            "id": selected_root,
            "blueprint_path": _review_path(
                root_node.blueprint_path, root_node.skill_root
            ),
            "node_hash": root_certificate.node_hash,
            "certificate_hash": root_certificate.certificate_hash,
        },
        "nodes": nodes,
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
