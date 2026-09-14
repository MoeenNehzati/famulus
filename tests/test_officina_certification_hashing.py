from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import officina.certification.hashing as certification_hashing
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
from test_support.git_repository import GitTestRepository


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("pattern", [
    "inputs/./policy.txt", "inputs//policy.txt", "inputs/*.txt", "inputs/**/policy.txt",
])
def test_basis_lookup_preserves_normalized_and_missing_tracked_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str,
) -> None:
    repository = GitTestRepository.initialize_existing_empty(tmp_path)
    manifest = tmp_path / certification_hashing.CERTIFICATION_BASIS_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps([pattern]), encoding="utf-8")
    policy = tmp_path / "inputs" / "policy.txt"
    policy.parent.mkdir()
    policy.write_text("policy\n", encoding="utf-8")
    repository.git("add", ".")
    repository.git("commit", "-qm", "basis")
    matcher = certification_hashing._basis_pattern_matches

    def match_glob(path, candidate_pattern):
        assert "*" in str(candidate_pattern), "literal lookup must bypass glob matching"
        return matcher(path, candidate_pattern)

    monkeypatch.setattr(certification_hashing, "_basis_pattern_matches", match_glob)
    assert set(resolve_certification_basis_paths(tmp_path)) == {manifest, policy}
    policy.unlink()
    with pytest.raises(CertificationHashError, match="tracked certification basis input is missing"):
        resolve_certification_basis_paths(tmp_path)


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


def test_v6_certifier_check_registry_is_exact() -> None:
    assert [check["id"] for check in expected_certifier_checks()] == [
        "blueprint-accuracy",
        "route-smoke-dependencies",
        "v6-deterministic",
    ]
    assert [check["version"] for check in expected_certifier_checks()] == [3, 3, 1]


def test_v6_certifier_identity_uses_the_runtime_interface() -> None:
    state = NodeHashState(node_hash=f"sha256:{'a' * 64}")
    node = BlueprintNode(
        "node-certify", "module", 1, Path("/repo"), Path("/repo/blueprint.yaml"), None, {})
    export = InterfaceExport(
        "node-certify._rtx.interface.certify", 2, "certify", "node-certify._rtx", {})
    graph = RepositoryBlueprintGraph(
        nodes={node.node_id: node}, node_edges=(), exports={export.interface_id: export},
        export_edges=(), helper_edges=(), certification_edges=(), schema_version=6,
    )
    assert derive_certifier_identity(graph, {node.node_id: state}, "b" * 40) == {
        "interface": export.interface_id, "version": 2,
        "node_hash": state.node_hash, "source_commit": "b" * 40,
    }


def test_current_canonical_basis_covers_certification_runtime_dependencies() -> None:
    basis = {
        path.relative_to(REPO_ROOT)
        for path in resolve_certification_basis_paths(
            REPO_ROOT,
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
