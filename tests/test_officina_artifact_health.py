from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
import yaml

import officina.common.artifact_health as artifact_health
from officina.common.artifact_health import (
    ArtifactHealthError,
    CERTIFIER_CHECK_REGISTRY,
    CERTIFIER_INTERFACE_ID,
    CERTIFIER_INTERFACE_VERSION,
    CERTIFIER_NODE_ID,
    NodeHashState,
    NodeHealthStatus,
    blueprint_schema_hash,
    build_node_health_record,
    certify_graph,
    check_graph_health,
    compute_certification_basis_hash,
    compute_node_hash_states,
    derive_certifier_identity,
    expected_certifier_checks,
    health_path_for_node,
    local_input_paths_for_node,
    node_requires_refresh,
    normalize_node_checks,
    resolve_certification_basis_paths,
)
from officina.common.audit_records import (
    attach_record_authentication,
    record_authentication_matches,
)
from officina.common.blueprint_graph import (
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graphs,
    load_skill_blueprint_graph,
    resolve_repository_skill_graph,
)


KEY = bytes(range(32))


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _commit_fixture(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Tests"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)


def _graph(tmp_path: Path):
    skill = tmp_path / "skills" / "demo-skill"
    source = skill / "references" / "policy.md"
    source.parent.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\nBody.\n")
    source.write_text("Policy one.\n")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "interfaces": [
                {
                    "interface": "demo-skill.llm.default",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": ".SKILL.md.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_yaml(
        skill / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary interface.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [],
            "behavior_sources": [
                {
                    "source": "demo-skill.source.policy",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "references/.policy.md.blueprint.yaml",
                    },
                    "reason": "Defines policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        skill / "references" / ".policy.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.policy",
            "version": 1,
            "description": "Policy.",
            "binding": {"kind": "file", "path": "references/policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )
    return load_skill_blueprint_graph(skill), source


def _inline_graph(tmp_path: Path):
    graph, source = _graph(tmp_path)
    skill = graph.skill_root
    default = yaml.safe_load((skill / ".SKILL.md.blueprint.yaml").read_text())
    root = yaml.safe_load((skill / "blueprint.yaml").read_text())
    root["default_interface"] = {
        key: value
        for key, value in default.items()
        if key not in {"schema_version", "blueprint_type", "id", "binding"}
    }
    root["interfaces"] = []
    _write_yaml(skill / "blueprint.yaml", root)
    (skill / ".SKILL.md.blueprint.yaml").unlink()
    return load_skill_blueprint_graph(skill), source


def _certify(graph, timestamp: str = "2026-07-13T12:00:00-04:00"):
    return certify_graph(
        graph,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        checks=[{"id": "schema", "version": 1, "passed": True, "findings": []}],
        key=KEY,
        certified_at=timestamp,
    )


def _states(graph, checks_by_node=None):
    return compute_node_hash_states(
        graph,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        checks_by_node=checks_by_node or {},
        schema_root=Path("references/blueprint").resolve(),
        certifier={"interface": "skill-audit.machine.certify", "version": 1},
    )


def _source_for_node(graph, node_id: str, commit: str) -> dict[str, object]:
    repo_root = graph.skill_root.parent.parent
    return {
        "vcs": "git",
        "commit": commit,
        "input_paths": [
            path.relative_to(repo_root).as_posix()
            for path in local_input_paths_for_node(graph.nodes[node_id])
        ],
    }


def _records_with_checks(graph, checks_by_node):
    states = _states(graph, checks_by_node)
    return {
        node_id: build_node_health_record(
            graph,
            node_id,
            states,
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=checks_by_node.get(node_id, []),
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )
        for node_id in graph.nodes
    }


def test_certification_writes_authenticated_bottom_up_records(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)

    records = _certify(graph)

    assert set(records) == set(graph.nodes)
    assert all(record_authentication_matches(record, KEY) for record in records.values())
    root = records["demo-skill"]
    child = records["demo-skill.llm.default"]
    assert root["dependencies"][0]["certified_health_hash"] == child["hashes"]["certified_health_hash"]
    assert root["certifier"] == {
        "interface": "skill-audit.machine.certify",
        "version": 1,
    }


def test_inline_default_is_certified_as_part_of_skill_root(tmp_path: Path) -> None:
    graph, _source = _inline_graph(tmp_path)

    records = _certify(graph)
    states = _states(graph)

    assert set(records) == {"demo-skill", "demo-skill.source.policy"}
    assert set(states) == set(records)
    assert records["demo-skill"]["subject"]["binding_path"] == "SKILL.md"
    assert records["demo-skill"]["dependencies"][0]["target"] == (
        "demo-skill.source.policy"
    )
    with pytest.raises(ArtifactHealthError, match="embedded default"):
        health_path_for_node(graph.nodes["demo-skill.llm.default"])


def test_raw_command_output_does_not_change_stable_checks() -> None:
    first = normalize_node_checks(
        [
            {
                "id": "tests",
                "version": 1,
                "passed": True,
                "findings": [],
                "stdout": "69 passed in 20.05s",
                "stderr": "temporary path /tmp/first",
            }
        ]
    )
    second = normalize_node_checks(
        [
            {
                "id": "tests",
                "version": 1,
                "passed": True,
                "findings": [],
                "stdout": "69 passed in 20.06s",
                "stderr": "temporary path /tmp/second",
            }
        ]
    )

    assert first == second == (
        {"id": "tests", "version": 1, "passed": True, "findings": []},
    )


def test_stable_checks_are_sorted_by_id_and_version() -> None:
    checks = [
        {"id": "zeta", "version": 1, "passed": True, "findings": []},
        {"id": "alpha", "version": 2, "passed": True, "findings": ["b"]},
        {"id": "alpha", "version": 1, "passed": True, "findings": ["a"]},
    ]

    assert [(item["id"], item["version"]) for item in normalize_node_checks(checks)] == [
        ("alpha", 1),
        ("alpha", 2),
        ("zeta", 1),
    ]


def test_failed_node_check_cannot_be_certified() -> None:
    with pytest.raises(ArtifactHealthError, match="failed node check"):
        normalize_node_checks(
            [{"id": "tests", "version": 1, "passed": False, "findings": []}]
        )


def test_duplicate_node_check_identity_is_rejected() -> None:
    with pytest.raises(ArtifactHealthError, match="duplicate node check"):
        normalize_node_checks(
            [
                {"id": "tests", "version": 1, "passed": True, "findings": ["a"]},
                {"id": "tests", "version": 1, "passed": True, "findings": ["b"]},
            ]
        )


def test_v4_basis_is_resolved_only_from_the_canonical_manifest(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '["references/certification/node-hash-policy.yaml", "basis/*.txt"]\n',
        encoding="utf-8",
    )
    policy = tmp_path / "references" / "certification" / "node-hash-policy.yaml"
    _write_yaml(
        policy,
        {
            "policy_version": 1,
            "path_syntax": "gitignore",
            "starting_set": "git-tracked-directly-owned-regular-files",
            "rules": [],
        },
    )
    basis = tmp_path / "basis" / "check.txt"
    basis.parent.mkdir()
    basis.write_text("one\n", encoding="utf-8")
    outside_manifest = tmp_path / "not-authoritative.txt"
    outside_manifest.write_text("ignored one\n", encoding="utf-8")

    paths = resolve_certification_basis_paths(tmp_path)
    first = compute_certification_basis_hash(tmp_path)
    outside_manifest.write_text("ignored two\n", encoding="utf-8")

    assert paths == tuple(sorted((manifest, policy, basis), key=lambda path: path.as_posix()))
    assert compute_certification_basis_hash(tmp_path) == first
    basis.write_text("two\n", encoding="utf-8")
    assert compute_certification_basis_hash(tmp_path) != first


def test_canonical_basis_includes_nested_dispatcher_python_package() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    paths = resolve_certification_basis_paths(repo_root)

    assert (
        repo_root / "src" / "officina" / "dispatcher" / "platforms" / "__init__.py"
    ) in paths


def test_canonical_basis_includes_every_v4_validator_implementation() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    paths = set(resolve_certification_basis_paths(repo_root))

    expected = {
        *repo_root.glob("validators/*.py"),
        *repo_root.glob("skills/skill-maker/validators/*.py"),
    }
    assert expected
    assert expected <= paths


def test_canonical_basis_includes_v4_blueprint_sync_implementation() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    paths = set(resolve_certification_basis_paths(repo_root))

    assert (
        repo_root / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    ) in paths


def test_canonical_basis_uses_reviewed_files_not_broad_common_or_runtime_globs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (
            repo_root
            / "skills"
            / "skill-drift"
            / "references"
            / "certification-basis-roots.json"
        ).read_text(encoding="utf-8")
    )

    assert "src/officina/common/*.py" not in manifest
    assert "src/officina/*.py" not in manifest
    assert "src/officina/common/oauth_json.py" not in manifest
    assert "src/officina/common/atomic_files.py" in manifest
    assert "skills/skill-certifier/blueprints/*.yaml" in manifest
    assert "skills/skill-drift/blueprints/*.yaml" in manifest


def test_v4_basis_rejects_deleted_tracked_literal_member(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["basis/required.txt"]\n', encoding="utf-8")
    required = tmp_path / "basis" / "required.txt"
    required.parent.mkdir()
    required.write_text("required\n", encoding="utf-8")
    _commit_fixture(tmp_path)
    required.unlink()

    with pytest.raises(
        ArtifactHealthError,
        match="tracked certification basis input is missing",
    ):
        resolve_certification_basis_paths(tmp_path)


def test_v4_basis_rejects_missing_untracked_literal_member(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["basis/required.txt"]\n', encoding="utf-8")

    with pytest.raises(
        ArtifactHealthError,
        match="certification basis pattern matched no files",
    ):
        resolve_certification_basis_paths(tmp_path)


def test_v4_basis_rejects_glob_that_matches_no_files(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["basis/**/*.py"]\n', encoding="utf-8")

    with pytest.raises(
        ArtifactHealthError,
        match="certification basis pattern matched no files",
    ):
        resolve_certification_basis_paths(tmp_path)


def test_v4_basis_rejects_deleted_tracked_glob_member(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["basis/*.txt"]\n', encoding="utf-8")
    basis = tmp_path / "basis"
    basis.mkdir()
    (basis / "kept.txt").write_text("kept\n", encoding="utf-8")
    deleted = basis / "deleted.txt"
    deleted.write_text("deleted\n", encoding="utf-8")
    _commit_fixture(tmp_path)
    deleted.unlink()

    with pytest.raises(
        ArtifactHealthError,
        match="tracked certification basis input is missing",
    ):
        resolve_certification_basis_paths(tmp_path)


def test_v4_basis_propagates_explicit_non_atomic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["basis.txt"]\n', encoding="utf-8")
    (tmp_path / "basis.txt").write_text("basis\n", encoding="utf-8")
    observed: list[bool] = []
    real_read = artifact_health.read_regular_file_bytes

    def capture_read(
        path: Path,
        *,
        allowed_root: Path,
        allow_non_atomic: bool = False,
    ) -> bytes:
        observed.append(allow_non_atomic)
        return real_read(
            path,
            allowed_root=allowed_root,
            allow_non_atomic=allow_non_atomic,
        )

    monkeypatch.setattr(artifact_health, "read_regular_file_bytes", capture_read)

    resolve_certification_basis_paths(tmp_path, allow_non_atomic=True)
    compute_certification_basis_hash(tmp_path, allow_non_atomic=True)

    assert observed and all(observed)


def test_v4_basis_rejects_manifest_escape(tmp_path: Path) -> None:
    manifest = (
        tmp_path
        / "skills"
        / "skill-drift"
        / "references"
        / "certification-basis-roots.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('["../outside"]\n', encoding="utf-8")

    with pytest.raises(ArtifactHealthError, match="stay under target package"):
        resolve_certification_basis_paths(tmp_path)


def test_v4_certifier_identity_and_checks_are_derived_from_registry() -> None:
    root = Path("/repository")
    node = BlueprintNode(
        node_id=CERTIFIER_NODE_ID,
        node_type="module",
        version=1,
        skill_root=root / "skills" / CERTIFIER_NODE_ID,
        blueprint_path=root / "skills" / CERTIFIER_NODE_ID / "blueprint.yaml",
        gateway_path=root / "skills" / CERTIFIER_NODE_ID / "SKILL.md",
        declaration={},
    )
    export = InterfaceExport(
        interface_id=CERTIFIER_INTERFACE_ID,
        version=CERTIFIER_INTERFACE_VERSION,
        local_name="certify",
        module_node_id=CERTIFIER_NODE_ID,
        declaration={},
    )
    graph = RepositoryBlueprintGraph(
        nodes={CERTIFIER_NODE_ID: node},
        node_edges=(),
        exports={CERTIFIER_INTERFACE_ID: export},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )
    states = {
        CERTIFIER_NODE_ID: NodeHashState(node_hash="sha256:" + "a" * 64)
    }

    assert derive_certifier_identity(graph, states, "b" * 40) == {
        "interface": CERTIFIER_INTERFACE_ID,
        "version": CERTIFIER_INTERFACE_VERSION,
        "node_hash": "sha256:" + "a" * 64,
        "source_commit": "b" * 40,
    }
    assert tuple(CERTIFIER_CHECK_REGISTRY) == ("deterministic", "semantic-audit")
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


@pytest.mark.parametrize("missing", ["node", "export", "state"])
def test_v4_certifier_identity_fails_closed_when_authority_is_missing(missing: str) -> None:
    root = Path("/repository")
    node = BlueprintNode(
        node_id=CERTIFIER_NODE_ID,
        node_type="module",
        version=1,
        skill_root=root / "skills" / CERTIFIER_NODE_ID,
        blueprint_path=root / "skills" / CERTIFIER_NODE_ID / "blueprint.yaml",
        gateway_path=root / "skills" / CERTIFIER_NODE_ID / "SKILL.md",
        declaration={},
    )
    export = InterfaceExport(
        interface_id=CERTIFIER_INTERFACE_ID,
        version=CERTIFIER_INTERFACE_VERSION,
        local_name="certify",
        module_node_id=CERTIFIER_NODE_ID,
        declaration={},
    )
    graph = RepositoryBlueprintGraph(
        nodes={} if missing == "node" else {CERTIFIER_NODE_ID: node},
        node_edges=(),
        exports={} if missing == "export" else {CERTIFIER_INTERFACE_ID: export},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )
    states = {} if missing == "state" else {
        CERTIFIER_NODE_ID: NodeHashState(node_hash="sha256:" + "a" * 64)
    }

    with pytest.raises(ArtifactHealthError, match="certifier"):
        derive_certifier_identity(graph, states, "b" * 40)


def test_local_input_paths_are_exactly_node_owned_inputs(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = "demo-skill.llm.default"
    node = graph.nodes[node_id]
    extra = node.skill_root / "references" / "local-policy.json"
    extra.write_text("{}\n", encoding="utf-8")
    node = replace(
        node,
        declaration={**node.declaration, "local_hash_inputs": ["references/local-policy.json"]},
    )

    assert local_input_paths_for_node(graph.root) == (graph.root.blueprint_path,)
    assert local_input_paths_for_node(node) == tuple(
        sorted((node.blueprint_path, node.binding_path, extra))
    )
    assert graph.nodes["demo-skill.source.policy"].binding_path not in local_input_paths_for_node(
        node
    )


def test_declared_local_hash_input_changes_local_hash(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = "demo-skill.llm.default"
    extra = graph.skill_root / "references" / "local-policy.json"
    extra.write_text("one\n", encoding="utf-8")
    node = replace(
        graph.nodes[node_id],
        declaration={
            **graph.nodes[node_id].declaration,
            "local_hash_inputs": ["references/local-policy.json"],
        },
    )
    graph = replace(graph, nodes={**graph.nodes, node_id: node})
    first = _states(graph)[node_id]

    extra.write_text("two\n", encoding="utf-8")
    second = _states(graph)[node_id]

    assert first.local_hash != second.local_hash


def test_inline_default_local_hash_input_is_owned_by_skill_health(
    tmp_path: Path,
) -> None:
    graph, _source = _inline_graph(tmp_path)
    extra = graph.skill_root / "references" / "inline-policy.json"
    extra.write_text("one\n", encoding="utf-8")
    root = replace(
        graph.root,
        declaration={
            **graph.root.declaration,
            "default_interface": {
                **graph.root.declaration["default_interface"],
                "local_hash_inputs": ["references/inline-policy.json"],
            },
        },
    )
    graph = replace(graph, root=root, nodes={**graph.nodes, root.node_id: root})

    assert extra in local_input_paths_for_node(root)
    first = _states(graph)[root.node_id]
    extra.write_text("two\n", encoding="utf-8")
    second = _states(graph)[root.node_id]

    assert first.local_hash != second.local_hash


def test_local_hash_input_cannot_escape_node_owner(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node = replace(
        graph.nodes["demo-skill.llm.default"],
        declaration={
            **graph.nodes["demo-skill.llm.default"].declaration,
            "local_hash_inputs": ["../outside.txt"],
        },
    )
    (node.skill_root.parent / "outside.txt").write_text("outside\n", encoding="utf-8")

    with pytest.raises(ArtifactHealthError, match="owner-relative"):
        local_input_paths_for_node(node)


@pytest.mark.parametrize("kind", ["final", "intermediate"])
def test_local_hash_input_rejects_symlink_components(
    tmp_path: Path,
    kind: str,
) -> None:
    graph, _source = _graph(tmp_path)
    node = graph.nodes["demo-skill.llm.default"]
    real_directory = node.skill_root / "real-local-inputs"
    real_directory.mkdir()
    real_file = real_directory / "policy.txt"
    real_file.write_text("policy\n", encoding="utf-8")
    if kind == "final":
        declared_path = "references/linked-policy.txt"
        (node.skill_root / declared_path).symlink_to(real_file)
    else:
        declared_path = "linked-inputs/policy.txt"
        (node.skill_root / "linked-inputs").symlink_to(
            real_directory,
            target_is_directory=True,
        )
    node = replace(
        node,
        declaration={**node.declaration, "local_hash_inputs": [declared_path]},
    )

    with pytest.raises(ArtifactHealthError, match="symlink"):
        local_input_paths_for_node(node)


def test_source_commit_changes_record_hash_not_certified_health_hash(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]
    states = _states(graph, {node_id: checks})

    first = build_node_health_record(
        graph,
        node_id,
        states,
        source=_source_for_node(graph, node_id, "a" * 40),
        checks=checks,
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    second = build_node_health_record(
        graph,
        node_id,
        states,
        source=_source_for_node(graph, node_id, "b" * 40),
        checks=checks,
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )

    assert first["hashes"]["certified_health_hash"] == second["hashes"][
        "certified_health_hash"
    ]
    assert first["record_hash"] != second["record_hash"]
    assert record_authentication_matches(first, KEY)
    assert record_authentication_matches(second, KEY)


@pytest.mark.parametrize(
    "source_update",
    [
        {"vcs": "svn"},
        {"commit": "not-a-commit"},
        {"unexpected": True},
    ],
)
def test_builder_rejects_malformed_source(
    tmp_path: Path,
    source_update: dict[str, object],
) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]
    source = {**_source_for_node(graph, node_id, "a" * 40), **source_update}

    with pytest.raises(ArtifactHealthError, match="invalid node health record"):
        build_node_health_record(
            graph,
            node_id,
            _states(graph, {node_id: checks}),
            source=source,
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_non_mapping_source(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]

    with pytest.raises(ArtifactHealthError, match="source must be a mapping"):
        build_node_health_record(
            graph,
            node_id,
            _states(graph, {node_id: checks}),
            source=[],
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_malformed_stable_check(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": [1]}]

    with pytest.raises(ArtifactHealthError, match="invalid node health record"):
        build_node_health_record(
            graph,
            node_id,
            _states(graph, {node_id: checks}),
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_non_mapping_check(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id

    with pytest.raises(ArtifactHealthError, match="node check must be a mapping"):
        build_node_health_record(
            graph,
            node_id,
            _states(graph),
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=["not-a-check"],
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_checks_inconsistent_with_state(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]

    with pytest.raises(ArtifactHealthError, match="certified_health_hash"):
        build_node_health_record(
            graph,
            node_id,
            _states(graph),
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_malformed_state_hash(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]
    states = _states(graph, {node_id: checks})
    states[node_id] = replace(states[node_id], policy_hash="not-a-hash")

    with pytest.raises(ArtifactHealthError, match="policy_hash"):
        build_node_health_record(
            graph,
            node_id,
            states,
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_builder_rejects_dependency_projection_inconsistent_with_graph(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    node_id = graph.root.node_id
    checks = [{"id": "schema", "version": 1, "passed": True, "findings": []}]
    states = _states(graph, {node_id: checks})
    states[node_id] = replace(states[node_id], dependencies=())

    with pytest.raises(ArtifactHealthError, match="dependencies"):
        build_node_health_record(
            graph,
            node_id,
            states,
            source=_source_for_node(graph, node_id, "a" * 40),
            checks=checks,
            key=KEY,
            certified_at="2026-07-13T12:00:00-04:00",
        )


def test_dependency_projection_contains_only_authenticated_health_summary(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    record = _certify(graph)[graph.root.node_id]

    assert set(record["dependencies"][0]) == {
        "relation",
        "target",
        "version",
        "artifact_graph_hash",
        "certified_health_hash",
    }
    assert "source" not in record["dependencies"][0]
    assert "checks" not in record["dependencies"][0]


def test_certify_graph_compatibility_wrapper_keeps_checks_node_local(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)

    assert records[graph.root.node_id]["checks"] == [
        {"id": "schema", "version": 1, "passed": True, "findings": []}
    ]
    assert all(
        records[node_id]["checks"] == []
        for node_id in graph.nodes
        if node_id != graph.root.node_id
    )
    assert all("source" in record for record in records.values())


def test_certify_graph_compatibility_wrapper_accepts_legacy_check_shape(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)

    records = certify_graph(
        graph,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        checks=[{"id": "schema", "passed": True}],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )

    assert records[graph.root.node_id]["checks"] == [
        {"id": "schema", "version": 1, "passed": True, "findings": []}
    ]


def test_shared_node_subject_paths_are_relative_to_its_owner(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    node_id = "demo-skill.source.policy"
    original = graph.nodes[node_id]
    owner = tmp_path / "skills" / "provider-skill"
    blueprint_path = owner / "references" / ".policy.md.blueprint.yaml"
    binding_path = owner / "references" / "policy.md"
    blueprint_path.parent.mkdir(parents=True)
    blueprint_path.write_bytes(original.blueprint_path.read_bytes())
    binding_path.write_bytes(original.binding_path.read_bytes())
    shared = replace(
        original,
        skill_root=owner,
        blueprint_path=blueprint_path,
        gateway_path=binding_path,
    )
    graph = replace(graph, nodes={**graph.nodes, node_id: shared})

    records = _certify(graph)

    assert records[node_id]["subject"]["blueprint_path"] == "references/.policy.md.blueprint.yaml"
    assert records[node_id]["subject"]["binding_path"] == "references/policy.md"


def test_leaf_change_propagates_unhealthy_status_to_all_ancestors(tmp_path: Path) -> None:
    graph, source = _graph(tmp_path)
    records = _certify(graph)
    source.write_text("Policy two.\n", encoding="utf-8")

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert not report.healthy
    assert not report.nodes["demo-skill.source.policy"].healthy
    assert not report.nodes["demo-skill.llm.default"].healthy
    assert not report.nodes["demo-skill"].healthy


def test_certification_timestamp_does_not_change_certified_health_hash(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)

    first = _certify(graph, "2026-07-13T12:00:00-04:00")
    second = _certify(graph, "2026-07-14T12:00:00-04:00")

    assert first["demo-skill"]["hashes"]["certified_health_hash"] == second["demo-skill"]["hashes"]["certified_health_hash"]
    assert first["demo-skill"]["record_hash"] != second["demo-skill"]["record_hash"]


def test_manual_record_edit_is_reported_as_authentication_failure(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)
    records["demo-skill.source.policy"]["source"]["commit"] = "f" * 40

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert "authentication-failed" in report.nodes["demo-skill.source.policy"].concerns
    assert "downstream-unhealthy" in report.nodes["demo-skill.llm.default"].concerns


def test_authenticated_schema_invalid_record_is_never_current(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)
    node_id = "demo-skill.source.policy"
    malformed = dict(records[node_id])
    malformed["record_type"] = "skill-health"
    records[node_id] = attach_record_authentication(malformed, KEY)

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert "invalid-health-record" in report.nodes[node_id].concerns
    assert not report.healthy


def test_authenticated_failed_check_is_never_current(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)
    node_id = "demo-skill.source.policy"
    malformed = dict(records[node_id])
    malformed["checks"] = [{"id": "schema", "passed": False}]
    records[node_id] = attach_record_authentication(malformed, KEY)

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert "invalid-health-record" in report.nodes[node_id].concerns
    assert not report.healthy


@pytest.mark.parametrize(
    "field",
    ["source", "subject", "certifier", "dependencies", "checks"],
)
def test_admission_requires_exact_stable_node_record_fields(
    tmp_path: Path,
    field: str,
) -> None:
    graph, _source = _graph(tmp_path)
    records = certify_graph(
        graph,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        checks=[
            {"id": "zeta", "version": 1, "passed": True, "findings": []},
            {"id": "alpha", "version": 1, "passed": True, "findings": []},
        ],
        key=KEY,
        certified_at="2026-07-13T12:00:00-04:00",
    )
    node_id = graph.root.node_id
    malformed = dict(records[node_id])
    if field == "source":
        malformed[field] = {
            **malformed[field],
            "input_paths": [
                *malformed[field]["input_paths"],
                "skills/demo-skill/SKILL.md",
            ],
        }
    elif field == "subject":
        malformed[field] = {**malformed[field], "blueprint_path": "wrong.yaml"}
    elif field == "certifier":
        malformed[field] = {**malformed[field], "version": 2}
    elif field == "dependencies":
        malformed[field] = []
    else:
        malformed[field] = list(reversed(malformed[field]))
    records[node_id] = attach_record_authentication(malformed, KEY)

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert "invalid-health-record" in report.nodes[node_id].concerns


@pytest.mark.parametrize(
    "concern",
    [
        "missing-health-record",
        "authentication-failed",
        "invalid-health-record",
        "artifact-stale",
        "dependency-stale",
        "schema-stale",
        "policy-stale",
        "checks-stale",
        "blueprint-file-changed",
    ],
)
def test_refresh_required_for_record_local_concerns(concern: str) -> None:
    status = NodeHealthStatus("demo", False, (concern,), "sha256:expected", None)

    assert node_requires_refresh(status)


def test_downstream_unhealthy_alone_does_not_refresh_parent() -> None:
    status = NodeHealthStatus(
        "demo", False, ("downstream-unhealthy",), "sha256:expected", None
    )

    assert not node_requires_refresh(status)


def test_unchanged_authenticated_records_require_no_refresh(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert report.healthy
    assert all(status.concerns == () for status in report.nodes.values())
    assert all(not node_requires_refresh(status) for status in report.nodes.values())
    assert {
        node_id: status.admitted_record_hash
        for node_id, status in report.nodes.items()
    } == {
        node_id: record["record_hash"]
        for node_id, record in records.items()
    }


def test_unauthenticated_check_bytes_do_not_control_expected_ancestor_hashes(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    records = _certify(graph)
    node_id = "demo-skill.source.policy"
    first = {key: dict(value) for key, value in records.items()}
    second = {key: dict(value) for key, value in records.items()}
    first[node_id]["checks"] = [{"id": "attacker-one", "passed": True}]
    second[node_id]["checks"] = [{"id": "attacker-two", "passed": True}]

    first_report = check_graph_health(
        graph,
        first,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )
    second_report = check_graph_health(
        graph,
        second,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert (
        first_report.nodes[graph.root.node_id].expected_certified_health_hash
        == second_report.nodes[graph.root.node_id].expected_certified_health_hash
    )


def test_unreadable_checked_child_does_not_refresh_unchanged_parents(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    child_id = "demo-skill.source.policy"
    checks_by_node = {
        child_id: [
            {"id": "semantic", "version": 1, "passed": True, "findings": []}
        ]
    }
    records = _records_with_checks(graph, checks_by_node)
    records[child_id] = {
        **records[child_id],
        "authentication": {
            **records[child_id]["authentication"],
            "mac": "base64:" + "A" * 43 + "=",
        },
    }

    report = check_graph_health(
        graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert report.nodes[child_id].concerns == ("authentication-failed",)
    for parent_id in ("demo-skill.llm.default", graph.root.node_id):
        status = report.nodes[parent_id]
        assert status.concerns == ("downstream-unhealthy",)
        assert status.expected_certified_health_hash == status.recorded_certified_health_hash
        assert not node_requires_refresh(status)


def test_health_sidecar_names_follow_blueprint_sidecars(tmp_path: Path) -> None:
    graph, _source = _graph(tmp_path)

    assert health_path_for_node(graph.root) == graph.skill_root / ".last_audit.json"
    assert health_path_for_node(graph.nodes["demo-skill.llm.default"]) == graph.skill_root / ".SKILL.md.health.json"
    assert health_path_for_node(graph.nodes["demo-skill.source.policy"]) == graph.skill_root / "references" / ".policy.md.health.json"


def test_legacy_virtual_health_sidecars_are_qualified_by_interface_name(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "legacy-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Legacy.\n", encoding="utf-8")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    }
                }
            }
        },
    )
    graph = load_skill_blueprint_graph(skill)

    assert health_path_for_node(graph.nodes["legacy-skill.llm.default"]) == (
        skill / ".SKILL.md.default.health.json"
    )


def test_schema_hash_covers_all_authoritative_schema_inputs(tmp_path: Path) -> None:
    schema_root = tmp_path / "schema"
    schema_root.mkdir()
    live_root = Path("references/blueprint")
    for relative in artifact_health.CANONICAL_GRAPH_SCHEMA_INPUTS:
        source = live_root / relative
        target = schema_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (schema_root / "README.md").write_text("Not schema input.\n", encoding="utf-8")
    original = blueprint_schema_hash(schema_root)

    (schema_root / "skill.schema.json").write_text('{"type": "string"}\n', encoding="utf-8")
    assert blueprint_schema_hash(schema_root) != original

    after_skill_change = blueprint_schema_hash(schema_root)
    (schema_root / "machine-module.schema.json").write_text(
        '{"type": "string"}\n',
        encoding="utf-8",
    )
    assert blueprint_schema_hash(schema_root) != after_skill_change

    after_machine_module_change = blueprint_schema_hash(schema_root)
    (schema_root / "conformance-operations" / "filesystem.schema.json").write_text(
        '{"type": "string"}\n',
        encoding="utf-8",
    )
    assert blueprint_schema_hash(schema_root) != after_machine_module_change

    after_target_change = blueprint_schema_hash(schema_root)
    (schema_root / "legacy-skill.schema.json").write_text(
        '{"type": "string"}\n',
        encoding="utf-8",
    )
    assert blueprint_schema_hash(schema_root) != after_target_change

    after_schema_change = blueprint_schema_hash(schema_root)
    (schema_root / "README.md").write_text("Changed documentation.\n", encoding="utf-8")
    assert blueprint_schema_hash(schema_root) == after_schema_change

    (schema_root / "template.yaml").write_text("changed template\n", encoding="utf-8")
    assert blueprint_schema_hash(schema_root) != after_schema_change

    (schema_root / "common.schema.json").unlink()
    with pytest.raises(ArtifactHealthError, match="common.schema.json"):
        blueprint_schema_hash(schema_root)


def test_pooled_schema_change_does_not_change_root_schema_hash(tmp_path: Path) -> None:
    schema_root = tmp_path / "schema"
    schema_root.mkdir()
    live_root = Path("references/blueprint")
    for relative in artifact_health.CANONICAL_GRAPH_SCHEMA_INPUTS:
        source = live_root / relative
        target = schema_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    original = blueprint_schema_hash(schema_root)

    (schema_root / "pooled-review.schema.json").write_text(
        '{"type": "string"}\n',
        encoding="utf-8",
    )

    assert blueprint_schema_hash(schema_root) == original


def test_schema_excluded_direct_io_change_does_not_change_certified_hash(
    tmp_path: Path,
) -> None:
    graph, _source = _graph(tmp_path)
    first = _certify(graph)
    sidecar = graph.skill_root / ".SKILL.md.blueprint.yaml"
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["direct_io"] = {
        "reads": [],
        "writes": [],
        "network": [
            {
                "host": "example.test",
                "protocol": "https",
                "reason": "Descriptive only.",
            }
        ],
    }
    _write_yaml(sidecar, declaration)
    changed_graph = load_skill_blueprint_graph(graph.skill_root)
    second = _certify(changed_graph)

    assert first["demo-skill.llm.default"]["hashes"]["blueprint_file_hash"] != second["demo-skill.llm.default"]["hashes"]["blueprint_file_hash"]
    assert first["demo-skill.llm.default"]["hashes"]["certified_health_hash"] == second["demo-skill.llm.default"]["hashes"]["certified_health_hash"]


def test_cross_skill_downstream_change_propagates_to_consumer_root(tmp_path: Path) -> None:
    for name in ["provider-skill", "consumer-skill"]:
        skill = tmp_path / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"{name} body one.\n", encoding="utf-8")
    _write_yaml(
        tmp_path / "skills" / "provider-skill" / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "provider-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "skill_interface": {"inputs": [], "outputs": [], "side_effects": []},
            "default_interface": {
                "version": 1,
                "description": "Provider interface.",
                "allow_all_skills": True,
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": [],
        },
    )
    _write_yaml(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "consumer-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "skill_interface": {"inputs": [], "outputs": [], "side_effects": []},
            "default_interface": {
                "version": 1,
                "description": "Consumer interface.",
                "allow_all_skills": True,
                "uses_interfaces": [
                    {"interface": "provider-skill.llm.default", "version": 1}
                ],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": [],
        },
    )
    graph = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(tmp_path),
        "consumer-skill",
    )
    records = _certify(graph)
    (tmp_path / "skills" / "provider-skill" / "SKILL.md").write_text(
        "provider body two.\n",
        encoding="utf-8",
    )
    changed_graph = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(tmp_path),
        "consumer-skill",
    )

    report = check_graph_health(
        changed_graph,
        records,
        policy_hash="sha256:" + "1" * 64,
        schema_hash="sha256:" + "2" * 64,
        key=KEY,
    )

    assert "provider-skill.llm.default" not in records
    assert "consumer-skill.llm.default" not in records
    assert not report.nodes["provider-skill"].healthy
    assert not report.nodes["consumer-skill"].healthy
