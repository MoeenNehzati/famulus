from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import officina.common.blueprint_graph as blueprint_graph
from officina.common.artifact_health import (
    build_node_health_record,
    compute_node_hash_states,
    health_path_for_node,
    local_input_paths_for_node,
)

from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintGraphError,
    BlueprintNode,
    SkillBlueprintGraph,
    expanded_legacy_blueprint,
    graph_contract_errors,
    load_reachable_repository_skill_graph,
    load_repository_blueprint_graphs,
    load_skill_blueprint_graph,
    resolve_repository_skill_graph,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_skill_file(skill: Path) -> None:
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\nBody.\n", encoding="utf-8")


def _write_shared_skill(shared_repo: Path, skill_id: str) -> None:
    skill = shared_repo / "skills" / skill_id
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": skill_id,
            "interfaces": [
                {
                    "interface": f"{skill_id}.llm.default",
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
            "id": f"{skill_id}.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [],
            "behavior_sources": [
                {
                    "source": "references.source.shared",
                    "version": 1,
                    "blueprint": {
                        "base": "repository-root",
                        "path": "references/.shared.md.blueprint.yaml",
                    },
                    "reason": "Uses shared policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )


@pytest.fixture
def shared_repo(tmp_path: Path) -> Path:
    (tmp_path / "references" / "shared.md").parent.mkdir(parents=True)
    (tmp_path / "references" / "shared.md").write_text("Shared policy.\n", encoding="utf-8")
    _write_yaml(
        tmp_path / "references" / ".shared.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "references.source.shared",
            "version": 1,
            "description": "Shared policy.",
            "binding": {"kind": "file", "path": "references/shared.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )
    _write_shared_skill(tmp_path, "first-skill")
    _write_shared_skill(tmp_path, "second-skill")
    return tmp_path


def edge_projection(graph: object, source_id: str) -> tuple[object, ...]:
    return tuple(
        (
            edge.relation,
            edge.source_id,
            edge.target_id,
            edge.required_version,
            edge.target_blueprint_path,
        )
        for edge in graph.edges
        if edge.source_id == source_id
    )


def replace_id(path: Path, node_id: str) -> None:
    declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
    declaration["id"] = node_id
    _write_yaml(path, declaration)


def test_repository_resolution_deduplicates_shared_source_edges(shared_repo: Path) -> None:
    targeted = load_reachable_repository_skill_graph(shared_repo, "first-skill")
    resolved = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(shared_repo), {"first-skill", "second-skill"}
    )

    assert edge_projection(targeted, "references.source.shared") == edge_projection(
        resolved, "references.source.shared"
    )


def test_shared_source_certification_is_independent_of_last_consumer(
    shared_repo: Path,
) -> None:
    first = load_reachable_repository_skill_graph(shared_repo, "first-skill")
    second = load_reachable_repository_skill_graph(shared_repo, "second-skill")
    schema_root = Path("references/blueprint").resolve()

    def record_for(graph: object) -> dict[str, object]:
        states = compute_node_hash_states(
            graph,
            policy_hash="sha256:" + "1" * 64,
            schema_hash="sha256:" + "2" * 64,
            checks_by_node={},
            schema_root=schema_root,
            certifier={"interface": "skill-audit.machine.certify", "version": 1},
        )
        node_id = "references.source.shared"
        return build_node_health_record(
            graph,
            node_id,
            states,
            source={
                "vcs": "git",
                "commit": "a" * 40,
                "input_paths": [
                    path.relative_to(shared_repo).as_posix()
                    for path in local_input_paths_for_node(graph.nodes[node_id])
                ],
            },
            checks=[],
            key=b"k" * 32,
            certified_at="2026-07-13T12:00:00-04:00",
            schema_root=schema_root,
        )

    first_record = record_for(first)
    second_record = record_for(second)

    assert first_record == second_record
    assert health_path_for_node(first.nodes["references.source.shared"]) == (
        health_path_for_node(second.nodes["references.source.shared"])
    )


def test_multi_root_postorder_includes_every_selected_component(shared_repo: Path) -> None:
    graph = resolve_repository_skill_graph(
        load_repository_blueprint_graphs(shared_repo), {"first-skill", "second-skill"}
    )

    assert blueprint_graph.postorder_node_ids(graph) == (
        "references.source.shared",
        "first-skill.llm.default",
        "first-skill",
        "second-skill.llm.default",
        "second-skill",
    )


def test_repository_reference_namespace_is_required(shared_repo: Path) -> None:
    sidecar = shared_repo / "references" / ".shared.md.blueprint.yaml"
    replace_id(sidecar, "alien-skill.source.shared")

    with pytest.raises(BlueprintGraphError, match="references.source"):
        load_reachable_repository_skill_graph(shared_repo, "first-skill")


def test_repository_behavior_sources_keep_repository_binding_root(shared_repo: Path) -> None:
    (shared_repo / "references" / "child.md").write_text("Child policy.\n", encoding="utf-8")
    _write_yaml(
        shared_repo / "references" / ".child.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "references.source.child",
            "version": 1,
            "description": "Child policy.",
            "binding": {"kind": "file", "path": "references/child.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )
    sidecar = shared_repo / "references" / ".shared.md.blueprint.yaml"
    declaration = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    declaration["uses_behavior_sources"] = [
        {
            "source": "references.source.child",
            "version": 1,
            "blueprint": {
                "base": "repository-root",
                "path": "references/.child.md.blueprint.yaml",
            },
            "reason": "Adds child policy.",
        }
    ]
    _write_yaml(sidecar, declaration)

    graph = load_reachable_repository_skill_graph(shared_repo, "first-skill")

    assert graph.nodes["references.source.child"].binding_path == (
        shared_repo / "references" / "child.md"
    )


def test_edge_key_and_postorder_are_canonical_and_deterministic(shared_repo: Path) -> None:
    graph = load_reachable_repository_skill_graph(shared_repo, "first-skill")

    assert callable(getattr(blueprint_graph, "edge_key", None))
    assert callable(getattr(blueprint_graph, "postorder_node_ids", None))
    assert blueprint_graph.postorder_node_ids(graph) == (
        "references.source.shared",
        "first-skill.llm.default",
        "first-skill",
    )


def test_inline_default_is_normalized_as_logical_llm_interface(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "default_interface": {
                "version": 1,
                "description": "Primary instructions.",
                "allow_all_skills": True,
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": [],
        },
    )

    graph = load_skill_blueprint_graph(skill, SCHEMA_ROOT)
    default = graph.nodes["demo-skill.llm.default"]

    assert default.blueprint_type == "llm-interface"
    assert default.binding_path == skill / "SKILL.md"
    assert default.blueprint_path == skill / "blueprint.yaml"
    assert default.embedded is True
    assert edge_projection(graph, "demo-skill") == (
        ("declares-interface", "demo-skill", "demo-skill.llm.default", 1, None),
    )
    assert expanded_legacy_blueprint(graph)["interfaces"]["llm"]["default"]["binding"] == {
        "kind": "skill_file",
        "path": "SKILL.md",
    }


def test_legacy_root_expands_interfaces_without_writing_sidecars(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "description": "Primary interface.",
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                        "behavior_sources": [],
                        "direct_io": {"reads": [], "writes": [], "network": []},
                        "owns_filesystem": [],
                    }
                }
            },
        },
    )

    graph = load_skill_blueprint_graph(skill)

    assert graph.root.blueprint_type == "skill"
    node = graph.nodes["demo-skill.llm.default"]
    assert node.virtual is True
    assert node.binding_path == skill / "SKILL.md"
    assert not (skill / ".SKILL.md.blueprint.yaml").exists()


def test_typed_root_loads_hidden_file_backed_node(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
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
                    "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
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
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )

    graph = load_skill_blueprint_graph(skill)

    node = graph.nodes["demo-skill.llm.default"]
    assert node.virtual is False
    assert node.blueprint_path == skill / ".SKILL.md.blueprint.yaml"
    assert node.binding_path == skill / "SKILL.md"
    assert graph.edges[0].target_id == node.node_id


def test_typed_default_llm_binding_must_be_skill_md(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "other.md").write_text("Other instructions.\n", encoding="utf-8")
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.llm.default",
                "version": 1,
                "blueprint": {"base": "skill-root", "path": ".other.md.blueprint.yaml"},
            }
        ],
    )
    _write_yaml(
        skill / ".other.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary interface.",
            "binding": {"kind": "instruction-file", "path": "other.md"},
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )

    with pytest.raises(BlueprintGraphError, match="default LLM interface must bind SKILL.md"):
        load_skill_blueprint_graph(skill)


def test_typed_root_rejects_missing_subordinate_blueprint(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "interfaces": [
                {
                    "interface": "demo-skill.llm.default",
                    "version": 1,
                    "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
                }
            ],
        },
    )

    with pytest.raises(BlueprintGraphError, match="missing subordinate blueprint"):
        load_skill_blueprint_graph(skill)


def test_typed_locator_rejects_parent_traversal(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "interfaces": [{"interface": "demo-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": "../outside/.SKILL.md.blueprint.yaml"}}],
        },
    )

    with pytest.raises(BlueprintGraphError, match="locator path must be relative without parent traversal"):
        load_skill_blueprint_graph(skill)


def test_typed_locator_rejects_symlink_escape(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    outside = tmp_path / "outside"
    _write_skill_file(skill)
    outside.mkdir()
    _write_yaml(
        outside / "sidecar.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
        },
    )
    (skill / ".SKILL.md.blueprint.yaml").symlink_to(outside / "sidecar.yaml")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "interfaces": [{"interface": "demo-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"}}],
        },
    )

    with pytest.raises(BlueprintGraphError, match="locator must resolve under skill-root"):
        load_skill_blueprint_graph(skill)


def test_reachable_loader_ignores_unrelated_malformed_skill(tmp_path: Path) -> None:
    target = tmp_path / "skills" / "target-skill"
    _write_skill_file(target)
    _write_yaml(
        target / "blueprint.yaml",
        {
            "category": "development-assistant",
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    }
                }
            },
        },
    )
    unrelated = tmp_path / "skills" / "broken-skill"
    unrelated.mkdir(parents=True)
    (unrelated / "blueprint.yaml").write_text("interfaces: [\n", encoding="utf-8")

    graph = load_reachable_repository_skill_graph(tmp_path, "target-skill")

    assert graph.root.node_id == "target-skill"
    assert "broken-skill" not in graph.nodes


def test_reachable_loader_loads_cross_skill_interface_provider(tmp_path: Path) -> None:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(consumer)
    _write_skill_file(provider)
    (provider / "_rtx").mkdir()
    (provider / "_rtx" / "_run.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_typed_root(
        consumer,
        [{"interface": "consumer-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"}}],
    )
    consumer_root = yaml.safe_load((consumer / "blueprint.yaml").read_text())
    consumer_root["id"] = "consumer-skill"
    _write_yaml(consumer / "blueprint.yaml", consumer_root)
    _write_yaml(
        consumer / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "consumer-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [{"interface": "provider-skill.machine.run", "version": 1}],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        provider / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "provider-skill",
            "interfaces": [{"interface": "provider-skill.machine.run", "version": 1, "blueprint": {"base": "skill-root", "path": "_rtx/._run.py.blueprint.yaml"}}],
        },
    )
    _write_minimal_typed_interface(
        provider,
        node_id="provider-skill.machine.run",
        binding_path="_rtx/_run.py",
        blueprint_path="_rtx/._run.py.blueprint.yaml",
    )

    graph = load_reachable_repository_skill_graph(tmp_path, "consumer-skill")

    assert "provider-skill.machine.run" in graph.nodes
    assert graph.nodes["provider-skill.machine.run"].skill_root == provider


@pytest.mark.parametrize(
    "unused_sidecar_state",
    ["valid-with-missing-dependency", "malformed", "missing"],
)
def test_reachable_loader_ignores_unreachable_provider_interface_sidecar(
    tmp_path: Path,
    unused_sidecar_state: str,
) -> None:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(consumer)
    _write_skill_file(provider)
    runtime = provider / "_rtx"
    runtime.mkdir()
    (runtime / "_selected.py").write_text("class Interface: pass\n", encoding="utf-8")
    (runtime / "_unreachable.py").write_text("class Interface: pass\n", encoding="utf-8")
    _write_yaml(
        consumer / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "consumer-skill",
            "interfaces": [
                {
                    "interface": "consumer-skill.llm.default",
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
        consumer / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "consumer-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [
                {"interface": "provider-skill.machine.selected", "version": 1}
            ],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        provider / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "provider-skill",
            "interfaces": [
                {
                    "interface": "provider-skill.machine.selected",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._selected.py.blueprint.yaml",
                    },
                },
                {
                    "interface": "provider-skill.machine.unreachable",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._unreachable.py.blueprint.yaml",
                    },
                },
            ],
        },
    )
    _write_minimal_typed_interface(
        provider,
        node_id="provider-skill.machine.selected",
        binding_path="_rtx/_selected.py",
        blueprint_path="_rtx/._selected.py.blueprint.yaml",
    )
    unused_sidecar = runtime / "._unreachable.py.blueprint.yaml"
    if unused_sidecar_state == "malformed":
        unused_sidecar.write_text("schema_version: [\n", encoding="utf-8")
    elif unused_sidecar_state == "valid-with-missing-dependency":
        _write_yaml(
            unused_sidecar,
            {
                "schema_version": 2,
                "blueprint_type": "machine-interface",
                "id": "provider-skill.machine.unreachable",
                "version": 1,
                "description": "Unreachable.",
                "usage": "run",
                "binding": {
                    "kind": "python-entrypoint",
                    "path": "_rtx/_unreachable.py",
                    "symbol": "Interface",
                },
                "platform_support": {"linux": True, "macos": True, "windows": True},
                "dependencies": [],
                "uses_interfaces": [
                    {"interface": "missing-skill.machine.run", "version": 1}
                ],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
        )

    graph = load_reachable_repository_skill_graph(tmp_path, "consumer-skill")

    assert "provider-skill.machine.selected" in graph.nodes
    assert "provider-skill.machine.unreachable" not in graph.nodes


def test_reachable_loader_rejects_malformed_selected_provider_sidecar(
    tmp_path: Path,
) -> None:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(consumer)
    _write_skill_file(provider)
    _write_yaml(
        consumer / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "consumer-skill",
            "interfaces": [
                {
                    "interface": "consumer-skill.llm.default",
                    "version": 1,
                    "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
                }
            ],
        },
    )
    _write_yaml(
        consumer / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "consumer-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [
                {"interface": "provider-skill.machine.selected", "version": 1}
            ],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        provider / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "provider-skill",
            "interfaces": [
                {
                    "interface": "provider-skill.machine.selected",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._selected.py.blueprint.yaml",
                    },
                }
            ],
        },
    )
    malformed = provider / "_rtx" / "._selected.py.blueprint.yaml"
    malformed.parent.mkdir()
    malformed.write_text("schema_version: [\n", encoding="utf-8")

    with pytest.raises(BlueprintGraphError, match="cannot load blueprint"):
        load_reachable_repository_skill_graph(tmp_path, "consumer-skill")


def _write_selected_shared_binding_fixture(
    tmp_path: Path,
    provider_interfaces: list[tuple[str, str]],
) -> tuple[Path, Path]:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(consumer)
    _write_skill_file(provider)
    runtime = provider / "_rtx"
    runtime.mkdir()
    (runtime / "_runner.py").write_text("class Interface: pass\n", encoding="utf-8")
    _write_yaml(
        consumer / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "consumer-skill",
            "interfaces": [
                {
                    "interface": "consumer-skill.llm.default",
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
        consumer / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "consumer-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "uses_interfaces": [
                {"interface": provider_interfaces[0][0], "version": 1}
            ],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        provider / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "provider-skill",
            "interfaces": [
                {
                    "interface": interface_id,
                    "version": 1,
                    "blueprint": {"base": "skill-root", "path": sidecar_path},
                }
                for interface_id, sidecar_path in provider_interfaces
            ],
        },
    )
    for interface_id, sidecar_path in provider_interfaces:
        _write_minimal_typed_interface(
            provider,
            node_id=interface_id,
            binding_path="_rtx/_runner.py",
            blueprint_path=sidecar_path,
        )
    return consumer, provider


def test_selected_provider_rejects_qualified_singleton_like_direct_owner(
    tmp_path: Path,
) -> None:
    _consumer, provider = _write_selected_shared_binding_fixture(
        tmp_path,
        [
            (
                "provider-skill.machine.only",
                "_rtx/._runner.py.only.blueprint.yaml",
            )
        ],
    )

    messages: list[str] = []
    with pytest.raises(BlueprintGraphError, match="sidecar name") as direct:
        load_skill_blueprint_graph(provider)
    messages.append(str(direct.value))
    with pytest.raises(BlueprintGraphError, match="sidecar name") as selected:
        load_reachable_repository_skill_graph(tmp_path, "consumer-skill")
    messages.append(str(selected.value))

    assert messages[0] == messages[1]


def test_selected_provider_accepts_declared_shared_binding_like_direct_owner(
    tmp_path: Path,
) -> None:
    _consumer, provider = _write_selected_shared_binding_fixture(
        tmp_path,
        [
            (
                "provider-skill.machine.first",
                "_rtx/._runner.py.first.blueprint.yaml",
            ),
            (
                "provider-skill.machine.second",
                "_rtx/._runner.py.second.blueprint.yaml",
            ),
        ],
    )

    direct = load_skill_blueprint_graph(provider)
    selected = load_reachable_repository_skill_graph(tmp_path, "consumer-skill")

    assert "provider-skill.machine.first" in direct.nodes
    assert "provider-skill.machine.second" in direct.nodes
    assert "provider-skill.machine.first" in selected.nodes
    assert "provider-skill.machine.second" not in selected.nodes


def _platform_contract_graph(
    tmp_path: Path,
    *,
    source_dependencies: list[dict[str, object]] | None = None,
    target_windows: bool = False,
) -> SkillBlueprintGraph:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    root = BlueprintNode(
        "consumer-skill",
        "skill",
        1,
        consumer,
        consumer / "blueprint.yaml",
        None,
        {},
    )
    source = BlueprintNode(
        "consumer-skill.machine.run",
        "machine-interface",
        1,
        consumer,
        consumer / "_rtx" / "._run.py.blueprint.yaml",
        consumer / "_rtx" / "_run.py",
        {
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "dependencies": source_dependencies or [],
        },
    )
    target = BlueprintNode(
        "provider-skill.machine.help",
        "machine-interface",
        1,
        provider,
        provider / "_rtx" / "._help.py.blueprint.yaml",
        provider / "_rtx" / "_help.py",
        {
            "platform_support": {
                "linux": True,
                "macos": True,
                "windows": target_windows,
            },
            "allowed_callers": ["consumer-skill"],
        },
    )
    return SkillBlueprintGraph(
        consumer,
        root,
        {node.node_id: node for node in (root, source, target)},
        (
            BlueprintEdge("declares-interface", root.node_id, source.node_id, 1),
            BlueprintEdge("uses-interface", source.node_id, target.node_id, 1),
        ),
    )


def test_graph_contract_rejects_platform_support_absent_from_required_interface(
    tmp_path: Path,
) -> None:
    errors = graph_contract_errors(_platform_contract_graph(tmp_path), SCHEMA_ROOT)

    assert any(
        "consumer-skill.machine.run" in error
        and "windows" in error
        and "provider-skill.machine.help" in error
        for error in errors
    )


def test_graph_contract_allows_platform_conditioned_runtime_dependencies(
    tmp_path: Path,
) -> None:
    graph = _platform_contract_graph(
        tmp_path,
        source_dependencies=[
            {
                "kind": "system-service",
                "name": "systemd-user",
                "platforms": {"linux": True, "macos": False, "windows": False},
            },
            {
                "kind": "system-service",
                "name": "launchd",
                "platforms": {"linux": False, "macos": True, "windows": False},
            },
            {
                "kind": "system-service",
                "name": "task-scheduler",
                "platforms": {"linux": False, "macos": False, "windows": True},
            },
        ],
        target_windows=True,
    )

    errors = graph_contract_errors(graph, SCHEMA_ROOT)

    assert errors == []


def test_behavior_source_edges_are_recursive(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "policy.md").write_text("Policy.\n", encoding="utf-8")
    (skill / "rules.md").write_text("Rules.\n", encoding="utf-8")
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "interfaces": [
                {
                    "interface": "demo-skill.llm.default",
                    "version": 1,
                    "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"},
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
                    "blueprint": {"base": "skill-root", "path": ".policy.md.blueprint.yaml"},
                    "reason": "Defines policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        skill / ".policy.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.policy",
            "version": 1,
            "description": "Policy index.",
            "binding": {"kind": "file", "path": "policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [
                {
                    "source": "demo-skill.source.rules",
                    "version": 1,
                    "blueprint": {"base": "skill-root", "path": ".rules.md.blueprint.yaml"},
                    "reason": "Supplies rules.",
                }
            ],
        },
    )
    _write_yaml(
        skill / ".rules.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.rules",
            "version": 1,
            "description": "Rules.",
            "binding": {"kind": "file", "path": "rules.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )

    graph = load_skill_blueprint_graph(skill)

    assert set(graph.nodes) == {
        "demo-skill",
        "demo-skill.llm.default",
        "demo-skill.source.policy",
        "demo-skill.source.rules",
    }
    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        ("demo-skill", "demo-skill.llm.default"),
        ("demo-skill.llm.default", "demo-skill.source.policy"),
        ("demo-skill.source.policy", "demo-skill.source.rules"),
    ]


def test_repository_locator_uses_shared_node_owners_skill_root(tmp_path: Path) -> None:
    consumer = tmp_path / "skills" / "consumer-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(consumer)
    (provider / "references").mkdir(parents=True)
    (provider / "references" / "policy.md").write_text("Shared policy.\n", encoding="utf-8")
    _write_yaml(
        consumer / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "consumer-skill",
            "interfaces": [{"interface": "consumer-skill.llm.default", "version": 1, "blueprint": {"base": "skill-root", "path": ".SKILL.md.blueprint.yaml"}}],
        },
    )
    _write_yaml(
        consumer / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "consumer-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "behavior_sources": [{"source": "provider-skill.source.policy", "version": 1, "blueprint": {"base": "repository-root", "path": "skills/provider-skill/references/.policy.md.blueprint.yaml"}, "reason": "Uses shared policy."}],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        provider / "references" / ".policy.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "provider-skill.source.policy",
            "version": 1,
            "description": "Shared policy.",
            "binding": {"kind": "file", "path": "references/policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )

    graph = load_skill_blueprint_graph(consumer)
    node = graph.nodes["provider-skill.source.policy"]

    assert node.skill_root == provider
    assert node.binding_path == provider / "references" / "policy.md"


def _write_minimal_typed_interface(
    skill: Path,
    *,
    node_id: str,
    binding_path: str,
    blueprint_path: str,
) -> None:
    _write_yaml(
        skill / blueprint_path,
        {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": node_id,
            "version": 1,
            "description": "Run the operation.",
            "usage": "run",
            "binding": {
                "kind": "python-entrypoint",
                "path": binding_path,
                "symbol": "Interface",
            },
            "dependencies": [],
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )


def _write_typed_root(skill: Path, interfaces: list[dict[str, object]]) -> None:
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": "demo-skill",
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "interfaces": interfaces,
        },
    )


def test_typed_node_binding_must_be_an_existing_regular_file(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "_rtx").mkdir()
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.machine.run",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": "_rtx/._rtx.blueprint.yaml",
                },
            }
        ],
    )
    _write_minimal_typed_interface(
        skill,
        node_id="demo-skill.machine.run",
        binding_path="_rtx",
        blueprint_path="_rtx/._rtx.blueprint.yaml",
    )

    with pytest.raises(BlueprintGraphError, match="binding must be an existing regular file"):
        load_skill_blueprint_graph(skill)


def test_typed_machine_binding_rejects_parent_traversal(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "_rtx").mkdir()
    (skill / "escape.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_typed_root(skill, [{"interface": "demo-skill.machine.run", "version": 1, "blueprint": {"base": "skill-root", "path": "_rtx/.escape.py.blueprint.yaml"}}])
    _write_minimal_typed_interface(skill, node_id="demo-skill.machine.run", binding_path="_rtx/../escape.py", blueprint_path="_rtx/.escape.py.blueprint.yaml")

    with pytest.raises(BlueprintGraphError, match="parent traversal"):
        load_skill_blueprint_graph(skill)


def test_typed_machine_binding_rejects_symlink_escape(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "_rtx").mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    (skill / "_rtx" / "_escape.py").symlink_to(outside)
    _write_typed_root(skill, [{"interface": "demo-skill.machine.run", "version": 1, "blueprint": {"base": "skill-root", "path": "_rtx/._escape.py.blueprint.yaml"}}])
    _write_minimal_typed_interface(skill, node_id="demo-skill.machine.run", binding_path="_rtx/_escape.py", blueprint_path="_rtx/._escape.py.blueprint.yaml")

    with pytest.raises(BlueprintGraphError, match="must resolve under _rtx"):
        load_skill_blueprint_graph(skill)


def test_typed_command_file_binding_must_be_executable(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    command_dir = skill / "_cx"
    command_dir.mkdir()
    command = command_dir / "run-task"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o644)
    _write_typed_root(skill, [{"interface": "demo-skill.machine.run", "version": 1, "blueprint": {"base": "skill-root", "path": "_cx/.run-task.blueprint.yaml"}}])
    _write_yaml(
        command_dir / ".run-task.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": "demo-skill.machine.run",
            "version": 1,
            "description": "Run.",
            "binding": {"kind": "command-file", "path": "_cx/run-task"},
            "dependencies": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )

    with pytest.raises(BlueprintGraphError, match="command file must be executable"):
        load_skill_blueprint_graph(skill)


@pytest.mark.parametrize("forbidden_name", [".state.health.json", ".state.blueprint.yaml"])
def test_typed_node_cannot_bind_generated_or_contract_artifacts(
    tmp_path: Path,
    forbidden_name: str,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / forbidden_name).write_text("artifact\n", encoding="utf-8")
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.machine.run",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": f".{forbidden_name}.blueprint.yaml",
                },
            }
        ],
    )
    _write_minimal_typed_interface(
        skill,
        node_id="demo-skill.machine.run",
        binding_path=forbidden_name,
        blueprint_path=f".{forbidden_name}.blueprint.yaml",
    )

    with pytest.raises(BlueprintGraphError, match="cannot be a blueprint or health artifact"):
        load_skill_blueprint_graph(skill)


def test_shared_binding_requires_qualified_sidecar_names(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    runtime = skill / "_rtx"
    runtime.mkdir()
    (runtime / "_runner.py").write_text("class Interface: pass\n", encoding="utf-8")
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.machine.first",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": "_rtx/._runner.py.blueprint.yaml",
                },
            },
            {
                "interface": "demo-skill.machine.second",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": "_rtx/._runner.py.second.blueprint.yaml",
                },
            },
        ],
    )
    _write_minimal_typed_interface(
        skill,
        node_id="demo-skill.machine.first",
        binding_path="_rtx/_runner.py",
        blueprint_path="_rtx/._runner.py.blueprint.yaml",
    )
    _write_minimal_typed_interface(
        skill,
        node_id="demo-skill.machine.second",
        binding_path="_rtx/_runner.py",
        blueprint_path="_rtx/._runner.py.second.blueprint.yaml",
    )

    with pytest.raises(BlueprintGraphError, match="shared binding requires qualified sidecar"):
        load_skill_blueprint_graph(skill)


def test_unreferenced_pooled_review_is_not_discovered(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.llm.default",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": ".SKILL.md.blueprint.yaml",
                },
            }
        ],
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
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    (skill / ".pooled-blueprint-review.yaml").write_text("not: [valid", encoding="utf-8")

    graph = load_skill_blueprint_graph(skill)

    assert set(graph.nodes) == {"demo-skill", "demo-skill.llm.default"}


def test_repository_resolution_follows_cross_skill_interface_edges(tmp_path: Path) -> None:
    provider = tmp_path / "skills" / "provider-skill"
    _write_skill_file(provider)
    _write_yaml(
        provider / "blueprint.yaml",
        {
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                        "behavior_sources": [],
                    }
                }
            }
        },
    )
    consumer = tmp_path / "skills" / "consumer-skill"
    _write_skill_file(consumer)
    _write_yaml(
        consumer / "blueprint.yaml",
        {
            "interfaces": {
                "llm": {
                    "default": {
                        "version": 1,
                        "binding": {"kind": "skill_file", "path": "SKILL.md"},
                        "behavior_sources": [],
                        "uses_interfaces": [
                            {"interface": "provider-skill.llm.default", "version": 1}
                        ],
                    }
                }
            }
        },
    )

    graphs = load_repository_blueprint_graphs(tmp_path)
    resolved = resolve_repository_skill_graph(graphs, "consumer-skill")

    assert set(resolved.nodes) == {
        "consumer-skill",
        "consumer-skill.llm.default",
        "provider-skill.llm.default",
    }
    assert "provider-skill" not in resolved.nodes


def test_typed_graph_expands_to_legacy_consumer_view(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "policy.md").write_text("Policy.\n", encoding="utf-8")
    _write_typed_root(
        skill,
        [
            {
                "interface": "demo-skill.llm.default",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": ".SKILL.md.blueprint.yaml",
                },
            }
        ],
    )
    _write_yaml(
        skill / ".SKILL.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "behavior_sources": [
                {
                    "source": "demo-skill.source.policy",
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": ".policy.md.blueprint.yaml",
                    },
                    "reason": "Defines policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )
    _write_yaml(
        skill / ".policy.md.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.policy",
            "version": 1,
            "description": "Policy.",
            "binding": {"kind": "file", "path": "policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    )
    graph = load_skill_blueprint_graph(skill)

    expanded = expanded_legacy_blueprint(graph)

    default = expanded["interfaces"]["llm"]["default"]
    assert default["binding"] == {"kind": "skill_file", "path": "SKILL.md"}
    assert default["behavior_sources"] == [
        {
            "path": "policy.md",
            "content": "config",
            "format": "markdown",
            "reason": "Defines policy.",
        }
    ]
