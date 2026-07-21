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
    CertificationEdge,
    InterfaceExport,
    SkillBlueprintGraph,
    authored_node_input_paths,
    expanded_legacy_blueprint,
    graph_contract_errors,
    load_reachable_repository_skill_graph,
    load_repository_blueprint_graph,
    load_repository_blueprint_graphs,
    load_skill_blueprint_graph,
    resolved_node_content_paths,
    resolve_repository_skill_graph,
    resolve_export,
    resolve_machine_export,
    runtime_authority_for_export,
)


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "references" / "blueprint"


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_skill_file(skill: Path) -> None:
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\nBody.\n", encoding="utf-8")


def _write_v3_skill(
    skill: Path,
    *,
    content: list[str] | None = None,
    interfaces: list[dict[str, object]] | None = None,
) -> None:
    _write_skill_file(skill)
    _write_yaml(
        skill / "blueprint.yaml",
        {
            "schema_version": 3,
            "node_type": "skill",
            "id": skill.name,
            "category": "development-assistant",
            "role": "automation",
            "kind": "tool",
            "gateway": {"kind": "instruction-file", "path": "SKILL.md"},
            "content": content if content is not None else [r"SKILL\.md"],
            "default_interface": {
                "version": 1,
                "description": "Primary instructions.",
                "allow_all_skills": True,
                "uses_interfaces": [],
                "behavior_sources": [],
                "direct_io": {"reads": [], "writes": [], "network": []},
                "owns_filesystem": [],
            },
            "interfaces": interfaces if interfaces is not None else [],
        },
    )
def _write_v3_machine(
    skill: Path,
    *,
    content: list[str] | None = None,
    gateway: str = "_rtx/_runner.py",
) -> None:
    runner = skill / gateway
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("class Interface:\n    pass\n", encoding="utf-8")
    sidecar = runner.with_name(f".{runner.name}.blueprint.yaml")
    _write_yaml(
        sidecar,
        {
            "schema_version": 3,
            "node_type": "machine-interface",
            "id": f"{skill.name}.machine.run",
            "version": 1,
            "description": "Run the operation.",
            "usage": "run",
            "gateway": {
                "kind": "python-entrypoint",
                "path": gateway,
                "symbol": "Interface",
                "args_prefix": [],
            },
            "content": content if content is not None else [r"_rtx/_runner\.py"],
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "dependencies": [],
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )


def _write_machine_module(
    skill: Path,
    module_name: str,
    interfaces: dict[str, dict[str, object]],
    *,
    uses_interfaces: list[dict[str, object]] | None = None,
    behavior_sources: list[dict[str, object]] | None = None,
) -> Path:
    gateway = skill / "_rtx" / f"_{module_name}.py"
    gateway.parent.mkdir(parents=True, exist_ok=True)
    gateway.write_text("class Interface:\n    pass\n", encoding="utf-8")
    conformance = skill / f"{module_name}-conformance.yaml"
    conformance.write_text("schema_version: 1\n", encoding="utf-8")
    path = gateway.with_name(f".{gateway.name}.blueprint.yaml")
    _write_yaml(
        path,
        {
            "schema_version": 3,
            "node_type": "machine-module",
            "id": f"{skill.name}.machine-module.{module_name}",
            "version": 1,
            "description": f"{module_name} module.",
            "gateway": {
                "kind": "python-entrypoint",
                "path": f"_rtx/_{module_name}.py",
                "symbol": "Interface",
                "args_prefix": [],
                "conformance": {
                    "adapter_protocol": "officina-python-adapters@1",
                    "bind_method": "bind_conformance_adapters",
                    "sandbox_profile": "officina-isolated-effects@1",
                },
            },
            "content": [rf"_rtx/_{module_name}\.py"],
            "conformance_manifest": {
                "base": "skill-root",
                "path": conformance.name,
            },
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "dependencies": [],
            "behavior_sources": behavior_sources or [],
            "owns_filesystem": [],
            "uses_interfaces": uses_interfaces or [],
            "interfaces": interfaces,
        },
    )
    return path


def _export(
    interface_id: str,
    *,
    version: int = 1,
    allowed_callers: list[str] | None = None,
    uses_interfaces: list[dict[str, object]] | None = None,
    helpers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    allowed = allowed_callers or []
    return {
        "id": interface_id,
        "version": version,
        "description": f"Call {interface_id}.",
        "allow_all_skills": not allowed,
        "allowed_callers": allowed,
        "invocation_binding": {"fixed": []},
        "uses_interfaces": uses_interfaces or [],
        "helpers": helpers or [],
        "direct_io": {"reads": [], "writes": [], "network": []},
        "owns_filesystem": [],
        "contract": {
            "arguments": {},
            "preconditions": [],
            "interaction": {"mode": "unattended"},
            "caller_warnings": [],
            "outputs": [],
            "outcomes": [],
            "execution": {
                "state_effect": "read-only",
                "lifecycle": "finite",
                "consistency": {"snapshot": "One snapshot."},
                "verification": [],
            },
        },
    }


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


def _v4_contract(*, helper: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "arguments": {},
        "preconditions": [],
        "interaction": {"mode": "unattended"},
        "caller_warnings": [],
        "outputs": [
            {
                "id": "result",
                "audience": "machine",
                "description": "Result.",
                "type": {"kind": "string"},
                "direct_io_ref": "stdout",
                "cardinality": {"minimum": 1, "maximum": 1},
                "ordering": "stable",
                "pagination": {"kind": "none"},
                "truncation": {"kind": "none"},
                "empty": "Never empty.",
            }
        ],
        "outcomes": [
            {
                "id": "success",
                "class": "success",
                "outputs": ["result"],
                "effects": [],
                "caller_action": "Continue.",
            }
        ],
        "execution": {
            "state_effect": "read-only",
            "lifecycle": "finite",
            "consistency": {"snapshot": "One invocation snapshot."},
            "verification": [{"method": "output-schema", "output_ref": "result"}],
        },
        "helpers": [helper] if helper is not None else [],
        "direct_io": {
            "reads": [],
            "writes": [
                {
                    "id": "stdout",
                    "medium": "stdout",
                    "access": "write",
                    "content": "Result.",
                    "format": "text",
                    "sensitivity": "public",
                }
            ],
            "network": [],
        },
    }


def _write_v4_module(
    root: Path,
    module_id: str,
    *,
    caller_export: str | None = None,
    allow_callers: list[str] | None = None,
) -> None:
    module = root / "skills" / module_id
    (module / "_rtx").mkdir(parents=True)
    (module / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (module / "README.md").write_text("Module-owned.\n", encoding="utf-8")
    (module / "_rtx" / "worker.py").write_text(
        "class Interface:\n    pass\n", encoding="utf-8"
    )
    gateway_source_id = f"{module_id}.source.gateway"
    worker_source_id = f"{module_id}.source.worker"
    run_source_interface = f"{worker_source_id}.interface.run"
    uses = (
        [{"interface": caller_export, "version": 1}]
        if caller_export is not None
        else []
    )
    _write_yaml(
        module / "blueprints" / "gateway.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": gateway_source_id,
            "version": 1,
            "description": "Primary instructions.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md"],
            "dependencies": [],
            "uses_interfaces": uses,
            "interfaces": {},
        },
    )
    _write_yaml(
        module / "blueprints" / "worker.yaml",
        {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": worker_source_id,
            "version": 1,
            "description": "Python worker.",
            "gateway": {"path": "_rtx/worker.py", "language": "Python>=3.11"},
            "content": [r"_rtx/worker\.py"],
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "runtime_dependencies": [],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {
                run_source_interface: {
                    "version": 1,
                    "description": "Run the worker.",
                    "contract": _v4_contract(),
                    "process_binding": {
                        "kind": "process",
                        "entry": "Interface",
                        "arguments": {},
                        "fixed": [],
                    },
                }
            },
        },
    )
    exports: dict[str, object] = {}
    if allow_callers is not None:
        exports[f"{module_id}.interface.run"] = {
            "source_interface": run_source_interface,
            "access": {
                "allow_all_modules": not allow_callers,
                "allowed_callers": allow_callers,
            },
        }
    _write_yaml(
        module / "blueprint.yaml",
        {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "description": f"{module_id} module.",
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": [r"SKILL\.md", r"README\.md", r"_rtx/worker\.py"],
            "authority": {"owns_filesystem": []},
            "sources": {
                gateway_source_id: {
                    "blueprint": {"base": "module-root", "path": "blueprints/gateway.yaml"}
                },
                worker_source_id: {
                    "blueprint": {"base": "module-root", "path": "blueprints/worker.yaml"}
                },
            },
            "exports": exports,
        },
    )


def test_v4_repository_graph_uses_one_generic_export_and_direct_ownership(
    tmp_path: Path,
) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        allow_callers=["consumer-skill"],
    )
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=None,
    )

    graph = load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)
    module, source, export = resolve_export(
        graph, "provider-skill.interface.run", 1
    )

    assert isinstance(export, InterfaceExport)
    assert graph.machine_exports is graph.exports
    assert module.node_id == "provider-skill"
    assert source.node_id == "provider-skill.source.worker"
    assert export.source_interface_id == (
        "provider-skill.source.worker.interface.run"
    )
    assert export.version == 1
    assert export.declaration is source.declaration["interfaces"][export.source_interface_id]
    assert graph.direct_file_owners[
        tmp_path / "skills" / "provider-skill" / "SKILL.md"
    ] == "provider-skill.source.gateway"
    assert graph.direct_file_owners[
        tmp_path / "skills" / "provider-skill" / "README.md"
    ] == "provider-skill"
    assert set(graph.module_sources["provider-skill"]) == {
        "provider-skill.source.gateway",
        "provider-skill.source.worker",
    }
    assert {
        (edge.relation, edge.source_node_id, edge.target_node_id)
        for edge in graph.certification_edges
    } >= {
        (
            "contains-source",
            "provider-skill",
            "provider-skill.source.gateway",
        ),
        (
            "contains-source",
            "provider-skill",
            "provider-skill.source.worker",
        ),
        (
            "uses-export",
            "consumer-skill.source.gateway",
            "provider-skill",
        ),
    }
    assert all(isinstance(edge, CertificationEdge) for edge in graph.certification_edges)


def test_v4_repository_graph_enforces_export_authorization_and_private_ownership(
    tmp_path: Path,
) -> None:
    _write_v4_module(
        tmp_path,
        "provider-skill",
        allow_callers=["different-skill"],
    )
    _write_v4_module(
        tmp_path,
        "consumer-skill",
        caller_export="provider-skill.interface.run",
        allow_callers=None,
    )

    with pytest.raises(BlueprintGraphError, match="consumer-skill.*not allowed"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)

    consumer = tmp_path / "skills" / "consumer-skill"
    source_path = consumer / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["uses_interfaces"] = [
        {
            "interface": "provider-skill.source.worker.interface.run",
            "version": 1,
        }
    ]
    _write_yaml(source_path, source)
    with pytest.raises(BlueprintGraphError, match="private interface.*cross-module"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


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


def test_version_three_nodes_use_normalized_gateway_and_content(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    interfaces = [
        {
            "interface": "demo-skill.machine.run",
            "version": 1,
            "blueprint": {
                "base": "skill-root",
                "path": "_rtx/._runner.py.blueprint.yaml",
            },
        }
    ]
    _write_v3_skill(skill, interfaces=interfaces)
    _write_v3_machine(skill)

    graph = load_skill_blueprint_graph(skill, SCHEMA_ROOT)
    root = graph.root
    machine = graph.nodes["demo-skill.machine.run"]

    assert root.node_type == "skill"
    assert root.gateway_path == skill / "SKILL.md"
    assert resolved_node_content_paths(root, tmp_path) == (skill / "SKILL.md",)
    assert authored_node_input_paths(root) == (
        skill / "SKILL.md",
        skill / "blueprint.yaml",
    )
    assert machine.node_type == "machine-interface"
    assert machine.gateway_path == skill / "_rtx" / "_runner.py"
    assert resolved_node_content_paths(machine, tmp_path) == (
        skill / "_rtx" / "_runner.py",
    )
    legacy = expanded_legacy_blueprint(graph)
    assert not ({"node_type", "gateway", "content"} & set(legacy))
    assert legacy["interfaces"]["machine"]["run"]["invocation"] == {
        "kind": "python_machine_interface",
        "entrypoint": "_rtx/_runner.py:Interface",
        "args_prefix": [],
        "behavior_sources": [],
    }


def test_version_two_nodes_normalize_binding_and_local_inputs_as_content(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_skill_file(skill)
    (skill / "notes.md").write_text("Notes.\n", encoding="utf-8")
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
                "local_hash_inputs": ["notes.md"],
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

    assert graph.root.node_type == "skill"
    assert graph.root.gateway_path == skill / "SKILL.md"
    assert resolved_node_content_paths(graph.root, tmp_path) == (
        skill / "SKILL.md",
        skill / "notes.md",
    )


@pytest.mark.parametrize(
    ("content", "extra_path", "message"),
    [
        (["["], None, "invalid content regex"),
        ([r"missing\.md"], None, "matched no files"),
        ([r"notes\.md"], "notes.md", "gateway must be included in content"),
        ([r"docs"], "docs", "matched no files"),
        ([r"linked\.md"], "linked.md", "matched no files"),
        ([r".*"], None, "content cannot include a blueprint or health artifact"),
    ],
)
def test_version_three_content_patterns_reject_invalid_ownership(
    tmp_path: Path,
    content: list[str],
    extra_path: str | None,
    message: str,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_v3_skill(skill, content=content)
    if extra_path == "docs":
        (skill / extra_path).mkdir()
    elif extra_path == "linked.md":
        target = tmp_path / "outside.md"
        target.write_text("Outside.\n", encoding="utf-8")
        (skill / extra_path).symlink_to(target)
    elif extra_path is not None:
        (skill / extra_path).write_text("Extra.\n", encoding="utf-8")

    with pytest.raises(BlueprintGraphError, match=message):
        load_skill_blueprint_graph(skill, SCHEMA_ROOT)


def test_version_three_content_ownership_is_exclusive(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    interfaces = [
        {
            "interface": "demo-skill.machine.run",
            "version": 1,
            "blueprint": {
                "base": "skill-root",
                "path": "_rtx/._runner.py.blueprint.yaml",
            },
        }
    ]
    _write_v3_skill(
        skill,
        content=[r"SKILL\.md", r"_rtx/_runner\.py"],
        interfaces=interfaces,
    )
    _write_v3_machine(skill)

    with pytest.raises(
        BlueprintGraphError,
        match=r"content file .*_runner\.py.*owned by both demo-skill and demo-skill\.machine\.run",
    ):
        load_skill_blueprint_graph(skill, SCHEMA_ROOT)


def test_repository_behavior_source_content_uses_repository_root(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    _write_v3_skill(skill)
    source_path = tmp_path / "references" / "policy.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("Policy.\n", encoding="utf-8")
    _write_yaml(
        tmp_path / "references" / ".policy.md.blueprint.yaml",
        {
            "schema_version": 3,
            "node_type": "behavior-source",
            "id": "references.source.policy",
            "version": 1,
            "description": "Shared policy.",
            "gateway": {"kind": "file", "path": "references/policy.md"},
            "content": [r"references/policy\.md"],
            "semantic_type": "policy",
            "format": "markdown",
            "uses_behavior_sources": [],
            "uses_interfaces": [],
        },
    )
    root = yaml.safe_load((skill / "blueprint.yaml").read_text(encoding="utf-8"))
    root["default_interface"]["behavior_sources"] = [
        {
            "source": "references.source.policy",
            "version": 1,
            "blueprint": {
                "base": "repository-root",
                "path": "references/.policy.md.blueprint.yaml",
            },
            "reason": "Supplies shared policy.",
        }
    ]
    _write_yaml(skill / "blueprint.yaml", root)

    graph = load_skill_blueprint_graph(skill, SCHEMA_ROOT)
    source = graph.nodes["references.source.policy"]

    assert source.gateway_path == source_path
    assert resolved_node_content_paths(source, tmp_path) == (source_path,)


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

    with pytest.raises(
        BlueprintGraphError,
        match="default LLM interface gateway must be SKILL.md",
    ):
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

    with pytest.raises(
        BlueprintGraphError,
        match="gateway must be an existing regular file",
    ):
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


def test_repository_graph_normalizes_nested_exports_and_scoped_edges(
    tmp_path: Path,
) -> None:
    caller = tmp_path / "skills" / "caller-skill"
    provider = tmp_path / "skills" / "provider-skill"
    _write_machine_module(
        provider,
        "worker",
        {
            "shared": _export("provider-skill.machine.shared"),
            "private": _export("provider-skill.machine.private"),
        },
    )
    helper = {
        "id": "lookup",
        "role": "Resolve one value.",
        "interface": "provider-skill.machine.shared",
        "version": 1,
        "inputs": {},
        "result": {"output_ref": "result", "selector": {"kind": "whole-output"}},
        "route": {"kind": "precondition", "target": "ready"},
        "empty": {"outcome": "empty", "caller_action": "Stop."},
        "failure": {"outcome": "failed"},
    }
    _write_machine_module(
        caller,
        "client",
        {
            "run": _export(
                "caller-skill.machine.run",
                uses_interfaces=[
                    {"interface": "provider-skill.machine.private", "version": 1}
                ],
                helpers=[helper],
            ),
            "sibling": _export("caller-skill.machine.sibling"),
        },
        uses_interfaces=[
            {"interface": "provider-skill.machine.shared", "version": 1}
        ],
    )

    graph = load_repository_blueprint_graph(tmp_path)
    module, export = resolve_machine_export(graph, "caller-skill.machine.run", 1)

    assert module.node_id == "caller-skill.machine-module.client"
    assert export.local_name == "run"
    assert runtime_authority_for_export(graph, export.interface_id) == (
        "provider-skill.machine.private",
        "provider-skill.machine.shared",
    )
    assert runtime_authority_for_export(graph, "caller-skill.machine.sibling") == (
        "provider-skill.machine.shared",
    )
    assert [(edge.source_export_id, edge.local_helper_id) for edge in graph.helper_edges] == [
        ("caller-skill.machine.run", "lookup")
    ]
    assert (
        "caller-skill.machine-module.client",
        "provider-skill.machine-module.worker",
    ) in {
        (edge.source_module_id, edge.target_node_id)
        for edge in graph.certification_edges
    }


def test_repository_graph_rejects_duplicate_exports_and_version_mismatch(
    tmp_path: Path,
) -> None:
    first = tmp_path / "skills" / "first-skill"
    second = tmp_path / "skills" / "second-skill"
    _write_machine_module(
        first,
        "worker",
        {"run": _export("first-skill.machine.run")},
    )
    _write_machine_module(
        second,
        "worker",
        {
            "call": _export(
                "second-skill.machine.call",
                uses_interfaces=[
                    {"interface": "first-skill.machine.run", "version": 2}
                ],
            )
        },
    )

    with pytest.raises(BlueprintGraphError, match="version 2.*version 1"):
        load_repository_blueprint_graph(tmp_path)

    second_path = next(second.rglob("*.blueprint.yaml"))
    declaration = yaml.safe_load(second_path.read_text(encoding="utf-8"))
    declaration["interfaces"]["call"]["uses_interfaces"][0]["version"] = 1
    _write_yaml(second_path, declaration)
    _write_machine_module(
        first,
        "other",
        {"again": _export("first-skill.machine.run")},
    )
    with pytest.raises(BlueprintGraphError, match="duplicate public export id"):
        load_repository_blueprint_graph(tmp_path)


def test_repository_graph_validates_target_documents_before_normalizing(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "invalid-skill"
    path = _write_machine_module(
        skill,
        "worker",
        {"run": _export("invalid-skill.machine.run")},
    )
    declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
    del declaration["description"]
    _write_yaml(path, declaration)

    with pytest.raises(BlueprintGraphError, match=r"schema error at \$\.description"):
        load_repository_blueprint_graph(tmp_path, schema_root=SCHEMA_ROOT)


def test_repository_graph_rejects_target_content_overlap(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "overlap-skill"
    first = _write_machine_module(
        skill,
        "first",
        {"run": _export("overlap-skill.machine.run")},
    )
    second = _write_machine_module(
        skill,
        "second",
        {"other": _export("overlap-skill.machine.other")},
    )
    (skill / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    for path in (first, second):
        declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
        declaration["content"].append(r"shared\.py")
        _write_yaml(path, declaration)

    with pytest.raises(BlueprintGraphError, match=r"shared\.py.*owned by both"):
        load_repository_blueprint_graph(tmp_path)


def test_repository_graph_rejects_export_platform_mismatch(tmp_path: Path) -> None:
    provider = tmp_path / "skills" / "provider-skill"
    provider_path = _write_machine_module(
        provider,
        "worker",
        {"run": _export("provider-skill.machine.run")},
    )
    provider_declaration = yaml.safe_load(
        provider_path.read_text(encoding="utf-8")
    )
    provider_declaration["platform_support"]["windows"] = False
    _write_yaml(provider_path, provider_declaration)
    caller = tmp_path / "skills" / "caller-skill"
    _write_machine_module(
        caller,
        "worker",
        {
            "run": _export(
                "caller-skill.machine.run",
                uses_interfaces=[
                    {"interface": "provider-skill.machine.run", "version": 1}
                ],
            )
        },
    )

    with pytest.raises(BlueprintGraphError, match=r"does not support.*windows"):
        load_repository_blueprint_graph(tmp_path)


def test_repository_graph_rejects_module_dispatch_and_runtime_cycles(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "cycle-skill"
    _write_machine_module(
        skill,
        "worker",
        {
            "one": _export(
                "cycle-skill.machine.one",
                uses_interfaces=[
                    {"interface": "cycle-skill.machine.two", "version": 1}
                ],
            ),
            "two": _export(
                "cycle-skill.machine.two",
                uses_interfaces=[
                    {"interface": "cycle-skill.machine.one", "version": 1}
                ],
            ),
        },
    )

    with pytest.raises(BlueprintGraphError, match="runtime export dependency cycle"):
        load_repository_blueprint_graph(tmp_path)

    clean_root = tmp_path / "clean"
    clean_skill = clean_root / "skills" / "clean-skill"
    _write_machine_module(
        clean_skill,
        "worker",
        {"run": _export("clean-skill.machine.run")},
    )
    graph = load_repository_blueprint_graph(clean_root)
    with pytest.raises(BlueprintGraphError, match="module id.*not callable"):
        resolve_machine_export(graph, "clean-skill.machine-module.worker")
