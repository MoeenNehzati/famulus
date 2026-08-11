from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.common.pooled_blueprint as pooled_blueprint
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.certification_view import CertificateRecordView
from officina.common.pooled_blueprint import (
    PooledReviewValidationError,
    render_pooled_review,
)
from test_support.v4_certification_fixtures import create_v4_repository

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"


def test_pooled_review_does_not_expose_legacy_health_authority() -> None:
    for name in (
        "PooledReviewHealth",
        "certify_pooled_review",
        "check_pooled_review",
        "pooled_review_health_path",
    ):
        assert not hasattr(pooled_blueprint, name)


def _certificate_view(
    tmp_path: Path,
    *,
    extra_modules: tuple[str, ...] = (),
):
    graph, states, _commit = create_v4_repository(
        tmp_path,
        extra_modules=extra_modules,
    )
    records = {
        node_id: {
            "payload": {
                "subject": {"id": node_id},
                "node_hash": state.node_hash,
                "certified_at": "2026-07-21T12:00:00-04:00",
            },
            "signature": {"value": "fixture"},
        }
        for node_id, state in states.items()
    }
    view = CertificateRecordView(
        records,
        expected_node_hashes={
            node_id: state.node_hash
            for node_id, state in states.items()
            if state.node_hash is not None
        },
    )
    return graph, states, records, view


def test_pooled_review_is_deterministic_certificate_backed_and_schema_valid(
    tmp_path: Path,
) -> None:
    graph, states, _records, view = _certificate_view(tmp_path)

    first = render_pooled_review(graph, view, root_id="demo-skill")
    second = render_pooled_review(graph, view, root_id="demo-skill")
    document = yaml.safe_load(first)

    assert first == second
    assert document["schema_version"] == 2
    assert document["root"]["node_hash"] == states["demo-skill"].node_hash
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


def test_pooled_review_fails_closed_without_current_certificate(
    tmp_path: Path,
) -> None:
    graph, states, records, _view = _certificate_view(tmp_path)
    records.pop("demo-skill")
    view = CertificateRecordView(
        records,
        expected_node_hashes={
            node_id: state.node_hash
            for node_id, state in states.items()
            if state.node_hash is not None
        },
    )

    with pytest.raises(
        PooledReviewValidationError,
        match="requires a current certificate",
    ):
        render_pooled_review(graph, view, root_id="demo-skill")


def test_pooled_review_exposes_missing_contained_source_certificate(
    tmp_path: Path,
) -> None:
    graph, states, records, _view = _certificate_view(tmp_path)
    records.pop("demo-skill.source.gateway")
    view = CertificateRecordView(
        records,
        expected_node_hashes={
            node_id: state.node_hash
            for node_id, state in states.items()
            if state.node_hash is not None
        },
    )

    with pytest.raises(
        PooledReviewValidationError,
        match=(
            "demo-skill.source.gateway: pooled review "
            "requires a current certificate"
        ),
    ):
        render_pooled_review(graph, view, root_id="demo-skill")


def test_pooled_review_requires_read_only_certificate_view(
    tmp_path: Path,
) -> None:
    graph, _states, records, _view = _certificate_view(tmp_path)

    with pytest.raises(
        PooledReviewValidationError,
        match="read-only certification view",
    ):
        render_pooled_review(graph, records, root_id="demo-skill")  # type: ignore[arg-type]


def test_pooled_review_rejects_stale_certificate(tmp_path: Path) -> None:
    graph, states, records, _view = _certificate_view(tmp_path)
    records["demo-skill"]["payload"]["node_hash"] = "sha256:" + "9" * 64
    view = CertificateRecordView(
        records,
        expected_node_hashes={
            node_id: state.node_hash
            for node_id, state in states.items()
            if state.node_hash is not None
        },
    )

    with pytest.raises(
        PooledReviewValidationError,
        match="requires a current certificate",
    ):
        render_pooled_review(graph, view, root_id="demo-skill")


def test_pooled_review_requires_exact_root_for_multi_module_graph(
    tmp_path: Path,
) -> None:
    graph, _states, _records, view = _certificate_view(
        tmp_path,
        extra_modules=("other-skill",),
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


def test_pooled_review_path_is_module_local(tmp_path: Path) -> None:
    assert pooled_blueprint.pooled_review_path(tmp_path) == (
        tmp_path / ".pooled-blueprint-review.yaml"
    )
