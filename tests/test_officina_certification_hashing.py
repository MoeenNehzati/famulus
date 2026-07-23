from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

import officina.common.certification_hashing as certification_hashing
from officina.common.certification_hashing import (
    CertificationHashError,
    NodeHashState,
    compute_certification_basis_hash,
    derive_certifier_identity,
    expected_certifier_checks,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from v4_certification_fixtures import create_v4_repository


def test_hash_owner_does_not_expose_legacy_health_authority() -> None:
    for name in (
        "NodeHealthStatus",
        "GraphHealthReport",
        "health_node_ids",
        "health_owner_node_id",
        "health_edges",
        "health_postorder_node_ids",
        "build_node_health_record",
        "certify_graph",
        "check_graph_health",
        "node_requires_refresh",
        "health_path_for_node",
    ):
        assert not hasattr(certification_hashing, name)


def test_node_hash_state_contains_only_v4_certificate_inputs() -> None:
    assert {field.name for field in fields(NodeHashState)} == {
        "node_hash",
        "input_manifest",
        "dependency_hashes",
        "certification_basis_hash",
    }


def test_stable_checks_are_canonical_and_reject_failed_or_duplicate_checks() -> None:
    checks = normalize_node_checks(
        [
            {
                "id": "semantic",
                "version": 1,
                "passed": True,
                "findings": [],
                "stdout": "ephemeral",
            },
            {
                "id": "deterministic",
                "version": 1,
                "passed": True,
                "findings": [],
            },
        ]
    )
    assert checks == (
        {
            "id": "deterministic",
            "version": 1,
            "passed": True,
            "findings": [],
        },
        {
            "id": "semantic",
            "version": 1,
            "passed": True,
            "findings": [],
        },
    )
    with pytest.raises(CertificationHashError, match="failed node check"):
        normalize_node_checks(
            [
                {
                    "id": "semantic",
                    "version": 1,
                    "passed": False,
                    "findings": [],
                }
            ]
        )
    with pytest.raises(CertificationHashError, match="duplicate node check"):
        normalize_node_checks([checks[0], checks[0]])


def test_v4_basis_and_certifier_identity_are_derived_from_one_state(
    tmp_path: Path,
) -> None:
    graph, states, commit = create_v4_repository(tmp_path)

    basis_paths = resolve_certification_basis_paths(tmp_path)
    basis_hash = compute_certification_basis_hash(tmp_path)
    identity = derive_certifier_identity(graph, states, commit)

    assert basis_paths
    assert all(state.certification_basis_hash == basis_hash for state in states.values())
    assert identity == {
        "interface": "skill-certifier.interface.certify",
        "version": 1,
        "node_hash": states["skill-certifier"].node_hash,
        "source_commit": commit,
    }
    assert expected_certifier_checks() == (
        {
            "id": "blueprint-accuracy",
            "version": 1,
            "passed": True,
            "findings": [],
        },
        {
            "id": "v4-deterministic",
            "version": 1,
            "passed": True,
            "findings": [],
        },
    )
