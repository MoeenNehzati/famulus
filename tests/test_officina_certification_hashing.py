from __future__ import annotations

import ast
from pathlib import Path

import pytest

from officina.blueprints.graph import (
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
)
from officina.certification.hashing import (
    CertificationHashError,
    NodeHashState,
    derive_certifier_identity,
    expected_certifier_checks,
    normalize_node_checks,
    resolve_certification_basis_paths,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_v4_and_v6_certifier_identities_are_derived_from_minimal_state() -> None:
    state = NodeHashState(node_hash=f"sha256:{'a' * 64}")
    commit = "b" * 40
    cases = (
        (
            "v4 facade",
            4,
            InterfaceExport(
                interface_id="node-certify.interface.certify",
                version=1,
                local_name="certify",
                module_node_id="node-certify",
                declaration={},
            ),
            {
                "interface": "node-certify.interface.certify",
                "version": 1,
                "node_hash": state.node_hash,
                "source_commit": commit,
            },
        ),
        (
            "v6 runtime",
            6,
            InterfaceExport(
                interface_id="node-certify._rtx.interface.certify",
                version=2,
                local_name="certify",
                module_node_id="node-certify._rtx",
                declaration={},
            ),
            {
                "interface": "node-certify._rtx.interface.certify",
                "version": 2,
                "node_hash": state.node_hash,
                "source_commit": commit,
            },
        ),
    )

    for label, schema_version, export, expected in cases:
        node = BlueprintNode(
            node_id="node-certify",
            node_type="module",
            version=1,
            module_root=Path("/repo/skills/node-certify"),
            blueprint_path=Path("/repo/skills/node-certify/blueprint.yaml"),
            gateway_path=None,
            declaration={"schema_version": schema_version},
        )
        graph = RepositoryBlueprintGraph(
            nodes={node.node_id: node},
            node_edges=(),
            exports={export.interface_id: export},
            export_edges=(),
            helper_edges=(),
            certification_edges=(),
            schema_version=schema_version,
        )
        assert derive_certifier_identity(
            graph,
            {node.node_id: state},
            commit,
        ) == expected, label


def test_versioned_certifier_check_registries_are_exact() -> None:
    expected_by_schema = {
        4: (
            ("blueprint-accuracy", 1),
            ("route-smoke-dependencies", 1),
            ("v4-deterministic", 1),
        ),
        5: (
            ("blueprint-accuracy", 2),
            ("route-smoke-dependencies", 2),
            ("v5-deterministic", 1),
        ),
        6: (
            ("blueprint-accuracy", 3),
            ("route-smoke-dependencies", 3),
            ("v6-deterministic", 1),
        ),
    }
    for schema_version, expected_entries in expected_by_schema.items():
        expected = tuple(
            {
                "id": check_id,
                "version": version,
                "passed": True,
                "findings": [],
            }
            for check_id, version in expected_entries
        )
        assert (
            expected_certifier_checks(expected_schema_version=schema_version)
            == expected
        ), f"schema v{schema_version}"


def test_current_canonical_basis_covers_certification_runtime_dependencies() -> None:
    basis = {
        path.relative_to(REPO_ROOT)
        for path in resolve_certification_basis_paths(
            REPO_ROOT,
            expected_schema_version=6,
        )
    }
    assert Path(
        "references/certification-policy/certification-basis-roots.json"
    ) in basis
    assert Path("src/officina/blueprints/authorization.py") in basis
    validator_paths = tuple(sorted((REPO_ROOT / "validators").rglob("*.py")))
    validator_relative_paths = {
        path.relative_to(REPO_ROOT) for path in validator_paths
    }
    assert validator_relative_paths
    assert validator_relative_paths <= basis
    imported_paths: set[Path] = set()
    for validator_path in validator_paths:
        tree = ast.parse(
            validator_path.read_text(encoding="utf-8"),
            filename=str(validator_path),
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module.startswith("officina."):
                relative = Path("src", *node.module.split(".")).with_suffix(".py")
            elif node.module.startswith("docs_tooling."):
                relative = Path(*node.module.split(".")).with_suffix(".py")
            elif node.module.startswith("validators."):
                relative = Path(*node.module.split(".")).with_suffix(".py")
            else:
                continue
            imported_paths.add(relative)

    assert imported_paths
    assert imported_paths <= basis
    assert {
        Path("src/officina/blueprints/__init__.py"),
        Path("src/officina/certification/__init__.py"),
        Path("src/officina/configuration/__init__.py"),
        Path("src/officina/credentials/__init__.py"),
        Path("src/officina/git/__init__.py"),
    } <= basis
