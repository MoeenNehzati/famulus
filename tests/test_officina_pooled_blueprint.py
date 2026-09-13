from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.blueprints.pooled as pooled_blueprint
from officina.blueprints.graph import (
    BlueprintEdge,
    BlueprintNode,
    RepositoryBlueprintGraph,
)
from officina.blueprints.template import load_schema, schema_validator
from officina.certification.view import CertificateRecordView
from officina.blueprints.pooled import (
    PooledReviewValidationError,
    render_pooled_review,
)

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint-schema"


def test_pooled_review_does_not_expose_legacy_health_authority() -> None:
    for name in (
        "PooledReviewHealth",
        "certify_pooled_review",
        "check_pooled_review",
        "pooled_review_health_path",
    ):
        assert not hasattr(pooled_blueprint, name)


def _renderer_graph(
    root: Path,
    *module_ids: str,
) -> RepositoryBlueprintGraph:
    nodes: dict[str, BlueprintNode] = {}
    node_edges: list[BlueprintEdge] = []
    module_sources: dict[str, tuple[str, ...]] = {}
    source_modules: dict[str, str] = {}
    for module_id in module_ids:
        module_root = root / "skills" / module_id
        source_id = f"{module_id}.source.gateway"
        nodes[module_id] = BlueprintNode(
            node_id=module_id,
            node_type="module",
            version=1,
            module_root=module_root,
            blueprint_path=module_root / "blueprint.yaml",
            gateway_path=module_root / "SKILL.md",
            declaration={
                "schema_version": 6,
                "node_type": "module",
                "id": module_id,
                "version": 1,
            },
        )
        nodes[source_id] = BlueprintNode(
            node_id=source_id,
            node_type="behavioral_source",
            version=1,
            module_root=module_root,
            blueprint_path=module_root / "blueprints" / "gateway.yaml",
            gateway_path=module_root / "gateway.py",
            declaration={
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
            },
        )
        node_edges.append(
            BlueprintEdge(
                relation="contains-source",
                source_id=module_id,
                target_id=source_id,
                required_version=1,
                target_blueprint_path=nodes[source_id].blueprint_path,
            )
        )
        module_sources[module_id] = (source_id,)
        source_modules[source_id] = module_id
    return RepositoryBlueprintGraph(
        nodes=nodes,
        node_edges=tuple(node_edges),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
        module_sources=module_sources,
        schema_version=6,
        source_modules=source_modules,
    )


def _certificate_records(
    node_hashes: dict[str, str],
) -> dict[str, dict[str, object]]:
    return {
        node_id: {
            "payload": {
                "subject": {"id": node_id, "version": 1},
                "node_hash": node_hash,
                "certified_at": "2026-07-21T12:00:00-04:00",
            },
            "signature": {
                "algorithm": "ed25519",
                "key_id": "fixture-key",
                "value": f"verified-envelope-{node_id}",
            },
        }
        for node_id, node_hash in node_hashes.items()
    }


def test_pooled_review_is_deterministic_certificate_backed_and_schema_valid(
    tmp_path: Path,
) -> None:
    graph = _renderer_graph(tmp_path, "demo-skill")
    node_hashes = {
        "demo-skill": "sha256:" + "1" * 64,
        "demo-skill.source.gateway": "sha256:" + "2" * 64,
    }
    view = CertificateRecordView(
        _certificate_records(node_hashes),
        expected_node_hashes=node_hashes,
    )

    first = render_pooled_review(graph, view, root_id="demo-skill")
    second = render_pooled_review(graph, view, root_id="demo-skill")
    document = yaml.safe_load(first)

    assert first == second
    assert document["schema_version"] == 2
    assert document["root"]["node_hash"] == node_hashes["demo-skill"]
    assert [node["id"] for node in document["nodes"]] == [
        "demo-skill",
        "demo-skill.source.gateway",
    ]
    assert all(
        node["certificate"]["status"] == "current"
        for node in document["nodes"]
    )
    assert "health" not in first
    assert "certified_health_hash" not in first
    schema_validator(
        load_schema(SCHEMA_ROOT / "pooled-review.schema.json")
    ).validate(document)


def test_pooled_review_rejects_unusable_certificate_evidence(
    tmp_path: Path,
) -> None:
    graph = _renderer_graph(tmp_path, "demo-skill")
    node_hashes = {
        "demo-skill": "sha256:" + "1" * 64,
        "demo-skill.source.gateway": "sha256:" + "2" * 64,
    }
    stale_records = _certificate_records(node_hashes)
    stale_records["demo-skill"]["payload"]["node_hash"] = (
        "sha256:" + "9" * 64
    )
    scenarios = (
        (
            "missing root certificate",
            CertificateRecordView(
                {
                    "demo-skill.source.gateway": _certificate_records(
                        node_hashes
                    )["demo-skill.source.gateway"]
                },
                expected_node_hashes=node_hashes,
            ),
            "demo-skill: pooled review requires a current certificate",
        ),
        (
            "missing contained source certificate",
            CertificateRecordView(
                {
                    "demo-skill": _certificate_records(node_hashes)[
                        "demo-skill"
                    ]
                },
                expected_node_hashes=node_hashes,
            ),
            (
                "demo-skill.source.gateway: pooled review requires a current "
                "certificate"
            ),
        ),
        (
            "unverified mutable record mapping",
            _certificate_records(node_hashes),
            "pooled review requires a read-only certification view",
        ),
        (
            "stale root certificate",
            CertificateRecordView(
                stale_records,
                expected_node_hashes=node_hashes,
            ),
            "demo-skill: pooled review requires a current certificate",
        ),
    )

    for label, certification, expected in scenarios:
        with pytest.raises(PooledReviewValidationError) as raised:
            render_pooled_review(
                graph,
                certification,  # type: ignore[arg-type]
                root_id="demo-skill",
            )
        assert str(raised.value) == expected, label


def test_pooled_review_requires_exact_root_for_multi_module_graph(
    tmp_path: Path,
) -> None:
    graph = _renderer_graph(tmp_path, "demo-skill", "other-skill")
    node_hashes = {
        "demo-skill": "sha256:" + "1" * 64,
        "demo-skill.source.gateway": "sha256:" + "2" * 64,
        "other-skill": "sha256:" + "3" * 64,
        "other-skill.source.gateway": "sha256:" + "4" * 64,
    }
    view = CertificateRecordView(
        _certificate_records(node_hashes),
        expected_node_hashes=node_hashes,
    )

    with pytest.raises(
        PooledReviewValidationError,
        match="requires root_id",
    ):
        render_pooled_review(graph, view)
    with pytest.raises(
        PooledReviewValidationError,
        match="unknown pooled-review root",
    ):
        render_pooled_review(graph, view, root_id="missing")
