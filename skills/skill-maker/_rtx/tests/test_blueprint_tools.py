#!/usr/bin/env python3
"""Focused tests for canonical version-5 blueprint synchronization."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


SYNCER_PATH = REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
V5_SCHEMA_ROOT = REPO_ROOT / "tests" / "fixtures" / "blueprint_schemas" / "v5"
V5_AUTHORIZATION_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "blueprint_v5" / "authorization"
)


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def syncer():
    return load_module("sync_module_blueprints_v5_tests", SYNCER_PATH)


def _copy_managed_skill(repo_root: Path) -> Path:
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def _copy_v5_managed_skill(repo_root: Path) -> tuple[Path, dict[str, object]]:
    root = copy_v5_fixture_tree(V5_AUTHORIZATION_FIXTURE, repo_root)

    runtime_path = (
        root
        / "skills"
        / "demo"
        / "_rtx"
        / "blueprints"
        / "runtime.yaml"
    )
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    dependency = {
        "kind": "python-package",
        "name": "PyYAML",
        "version": ">=6",
        "platforms": {"linux": True, "macos": True, "windows": True},
        "reason": "Parses YAML.",
    }
    runtime["platform_support"] = {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    runtime["runtime_dependencies"] = [dependency]
    runtime["interfaces"][
        "demo-rtx.source.runtime.interface.execute"
    ]["process_binding"] = {
        "kind": "process",
        "entry": "Interface",
        "arguments": {},
        "fixed": [],
    }
    runtime_path.write_text(
        yaml.safe_dump(runtime, sort_keys=False),
        encoding="utf-8",
    )
    return root, dependency


def test_syncer_loads_canonical_module_and_generates_export_blocks(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", tmp_path / "skills")
    blueprint = syncer.load_blueprints()["loose-mode"]

    assert blueprint.data["node_type"] == "module"
    assert blueprint.repository_graph.nodes[
        "loose-mode.source.gateway"
    ].node_type == "behavioral_source"

    contract = syncer.generated_contract_block(
        blueprint.name,
        blueprint.data,
        blueprint.repository_graph,
    )
    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )

    assert (
        "Catalog: assistant-interaction; topics: reasoning-control; "
        "visibility: featured"
    ) in contract
    assert "Activation: user-request; persistent modifier: yes" in contract
    assert "Skill Version: 2" in contract
    assert "`loose-mode.interface.default`" in contract
    assert "Instruction Interfaces:" in interfaces
    assert "`loose-mode.interface.default`" in interfaces


def test_generated_contract_keeps_setup_requirements_separate(syncer) -> None:
    module_id = "consumer"
    source_id = f"{module_id}.source.gateway"
    setup_id = f"{module_id}.interface.setup"
    prerequisite_id = "provider.interface.setup"
    ordinary_use_id = "provider.interface.run"
    graph = SimpleNamespace(
        schema_version=6,
        module_sources={module_id: (source_id,)},
        nodes={
            source_id: SimpleNamespace(
                declaration={
                    "uses_interfaces": [
                        {"interface": ordinary_use_id, "version": 2}
                    ]
                }
            )
        },
        exports={
            setup_id: SimpleNamespace(module_node_id=module_id),
            ordinary_use_id: SimpleNamespace(module_node_id="provider"),
        },
        setup_requirements={setup_id: ()},
    )
    data = {
        "version": 1,
        "discovery": {
            "catalog": {
                "domain": "test",
                "topics": ["setup"],
                "visibility": "listed",
            },
            "activated_by": ["user-request"],
            "persistent_modifier": False,
        },
    }

    contract_without_prerequisite = syncer.generated_contract_block(
        module_id,
        data,
        graph,
    )
    assert "Setup Requires Setup Of: none" in contract_without_prerequisite

    graph.setup_requirements = {
        prerequisite_id: (),
        setup_id: ((prerequisite_id, 1),),
    }
    contract = syncer.generated_contract_block(module_id, data, graph)

    assert f"`{source_id} -> {ordinary_use_id}@2`" in contract
    assert f"`{prerequisite_id}@1`" in contract
    assert (
        "Setup Order:\n"
        f"1. `{prerequisite_id}`\n"
        f"2. `{setup_id}`"
    ) in contract
    uses, setup = contract.split("Setup Requires Setup Of:", 1)
    assert prerequisite_id not in uses
    assert prerequisite_id in setup


def test_generated_setup_order_deduplicates_transitive_dependencies(syncer) -> None:
    module_id = "root"
    graph = SimpleNamespace(
        schema_version=6,
        module_sources={},
        nodes={},
        exports={
            f"{module_id}.interface.setup": SimpleNamespace(module_node_id=module_id)
        },
        setup_requirements={
            "root.interface.setup": (
                ("left.interface.setup", 1),
                ("right.interface.setup", 1),
            ),
            "left.interface.setup": (("leaf.interface.setup", 1),),
            "right.interface.setup": (("leaf.interface.setup", 1),),
            "leaf.interface.setup": (),
        },
    )
    data = {
        "version": 1,
        "discovery": {
            "catalog": {
                "domain": "test",
                "topics": ["setup"],
                "visibility": "listed",
            },
            "activated_by": ["user-request"],
            "persistent_modifier": False,
        },
    }

    contract = syncer.generated_contract_block(module_id, data, graph)

    assert (
        "Setup Order:\n"
        "1. `leaf.interface.setup`\n"
        "2. `left.interface.setup`\n"
        "3. `right.interface.setup`\n"
        "4. `root.interface.setup`"
    ) in contract
    assert contract.count("`leaf.interface.setup`") == 1


def test_v5_generated_views_are_parent_only_and_derive_facade_contract(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, dependency = _copy_v5_managed_skill(tmp_path / "repo")
    monkeypatch.setattr(syncer, "SKILLS_ROOT", root / "skills")

    blueprints = syncer.load_blueprints(
        schema_version=5,
        schema_root=V5_SCHEMA_ROOT,
    )

    assert set(blueprints) == {"demo"}
    blueprint = blueprints["demo"]
    contract = syncer.generated_contract_block(
        blueprint.name,
        blueprint.data,
        blueprint.repository_graph,
    )
    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )
    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert "`demo.interface.execute`" in contract
    assert "demo.source.gateway -> demo.interface.execute@3" not in contract
    assert "demo-rtx.interface.execute" not in contract + interfaces
    assert "`demo.interface.execute` — Execute the demo." in interfaces
    assert "dispatcher --caller-skill demo demo.interface.execute" in interfaces
    assert set(manifest["skills"]) == {"demo"}
    assert manifest["version"] == 2
    assert manifest["skills"]["demo"]["interfaces"]["demo.interface.execute"] == {
        "dependencies": [dependency],
    }
    terminal = blueprint.repository_graph.exports[
        "demo-rtx.interface.execute"
    ]
    assert isinstance(terminal.export_declaration, dict)
    original_access = terminal.export_declaration["access"]
    terminal.export_declaration["access"] = {
        "allow_all_modules": False,
        "allowed_callers": ["outsider"],
    }

    interfaces = syncer.generated_interface_block(
        blueprint.name,
        blueprint.repository_graph,
    )

    assert "`demo.interface.execute` — Execute the demo." in interfaces
    assert "demo-rtx.interface.execute" not in interfaces
    terminal.export_declaration["access"] = original_access

    gateway = blueprint.repository_graph.nodes["demo.source.gateway"].declaration
    original_uses = gateway["uses_interfaces"]
    gateway["uses_interfaces"] = [
        entry
        for entry in gateway["uses_interfaces"]
        if entry["interface"] != "demo.interface.execute"
    ]

    assert syncer.validate_gateway_declares_generated_dispatches(
        blueprint.name,
        blueprint.repository_graph,
    ) == [
        "demo.source.gateway: generated dispatcher exports are missing from "
        "uses_interfaces: demo.interface.execute@3"
    ]
    gateway["uses_interfaces"] = original_uses


def test_generated_contract_requires_catalog_discovery(syncer) -> None:
    graph = SimpleNamespace(module_sources={}, nodes={}, exports={})

    with pytest.raises(syncer.BlueprintError, match="discovery.*mapping"):
        syncer.generated_contract_block(
            "demo-skill",
            {"version": 1},
            graph,
        )


def test_runtime_dependency_manifest_uses_export_source_closure(syncer) -> None:
    dependency = {
        "kind": "python-package",
        "name": "PyYAML",
        "version": ">=6",
        "platforms": {"linux": True, "macos": True, "windows": True},
        "reason": "Parses YAML.",
    }
    source = SimpleNamespace(
        node_id="demo-skill.source.runner",
        declaration={"runtime_dependencies": [dependency]},
    )
    export = SimpleNamespace(
        module_node_id="demo-skill",
        local_name="run",
        source_node_id=source.node_id,
        declaration={"process_binding": {"kind": "process"}},
    )
    graph = SimpleNamespace(
        exports={"demo-skill.interface.run": export},
        nodes={source.node_id: source},
        node_edges=(),
    )
    blueprints = {
        "demo-skill": syncer.ModuleBlueprint(
            "demo-skill",
            Path("skills/demo-skill/blueprint.yaml"),
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "demo-skill",
                "maturity": "stable",
                "installation_tier": "core",
                "personal_preference": {"applies": False},
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert manifest["version"] == 2
    module = manifest["skills"]["demo-skill"]
    assert module["maturity"] == "stable"
    assert module["installation_tier"] == "core"
    assert module["personal_preference"] == {"applies": False}
    assert module["interfaces"]["demo-skill.interface.run"] == {
        "dependencies": [dependency],
    }
    assert manifest["all"]["python-package"] == ["PyYAML"]


def test_runtime_dependency_manifest_keeps_module_without_executable_interfaces(
    syncer,
) -> None:
    graph = SimpleNamespace(exports={}, nodes={}, node_edges=())
    blueprints = {
        "metadata-only": syncer.ModuleBlueprint(
            "metadata-only",
            Path("skills/metadata-only/blueprint.yaml"),
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "metadata-only",
                "maturity": "experimental",
                "installation_tier": "optional",
                "personal_preference": {"applies": False},
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    assert manifest["skills"] == {
        "metadata-only": {
            "maturity": "experimental",
            "installation_tier": "optional",
            "personal_preference": {"applies": False},
            "interfaces": {},
        }
    }
    assert manifest["all"] == {
        kind: [] for kind in syncer.RUNTIME_DEPENDENCY_KINDS
    }


def test_runtime_dependency_manifest_v2_keeps_all_descendant_interface_ids(syncer) -> None:
    """Canonical IDs prevent equal child-local names from overwriting, and
    aggregation follows ownership rather than namespace exposure."""
    dependencies = {
        "demo._rtx": {
            "kind": "python-package", "name": "PyYAML", "version": ">=6",
            "platforms": {"linux": True, "macos": True, "windows": True},
            "reason": "Private child parser.",
        },
        "demo.worker": {
            "kind": "python-package", "name": "rich", "version": "any",
            "platforms": {"linux": True, "macos": True, "windows": True},
            "reason": "Worker output.",
        },
    }
    exports = {}
    nodes = {}
    for owner, dependency in dependencies.items():
        source_id = f"{owner}.source.runner"
        interface_id = f"{owner}.interface.run"
        nodes[source_id] = SimpleNamespace(
            node_id=source_id,
            declaration={"runtime_dependencies": [dependency]},
        )
        exports[interface_id] = SimpleNamespace(
            module_node_id=owner,
            local_name="run",
            source_node_id=source_id,
            declaration={"process_binding": {"kind": "process"}},
        )
    graph = SimpleNamespace(
        exports=exports,
        nodes=nodes,
        node_edges=(),
        module_ancestry={
            "demo": ("demo",),
            "demo._rtx": ("demo", "demo._rtx"),
            "demo.worker": ("demo", "demo.worker"),
        },
    )
    blueprints = {
        "demo": syncer.ModuleBlueprint(
            "demo", Path("skills/demo/blueprint.yaml"),
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "demo",
                "maturity": "stable",
                "installation_tier": "optional",
                "personal_preference": {
                    "applies": True,
                    "description": "The user selected this optional workflow.",
                },
            },
            graph,
        )
    }

    manifest = syncer.generated_runtime_dependencies_manifest(blueprints)

    module = manifest["skills"]["demo"]
    assert module["maturity"] == "stable"
    assert module["installation_tier"] == "optional"
    assert module["personal_preference"] == {
        "applies": True,
        "description": "The user selected this optional workflow.",
    }
    interfaces = module["interfaces"]
    assert set(interfaces) == {
        "demo._rtx.interface.run",
        "demo.worker.interface.run",
    }
    assert interfaces["demo._rtx.interface.run"]["dependencies"] == [dependencies["demo._rtx"]]
    assert interfaces["demo.worker.interface.run"]["dependencies"] == [dependencies["demo.worker"]]


def test_consumer_blocks_use_root_and_named_gateway_placement(
    tmp_path: Path,
    syncer,
) -> None:
    module_root = tmp_path / "demo-skill"
    root_gateway = module_root / "SKILL.md"
    named_gateway = module_root / "instructions" / "coach.md"
    named_gateway.parent.mkdir(parents=True)
    root_gateway.write_text(
        "---\nname: demo-skill\n---\n"
        f"{syncer.CONTRACT_START}\nContract\n{syncer.CONTRACT_END}\n"
        "Root body.\n",
        encoding="utf-8",
    )
    named_gateway.write_text("Named body.\n", encoding="utf-8")
    graph = SimpleNamespace(
        nodes={
            "demo-skill.source.gateway": SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=root_gateway,
                module_root=module_root,
            ),
            "demo-skill.source.coach": SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=named_gateway,
                module_root=module_root,
            ),
        }
    )
    selected = {
        "schema_version": 2,
        "consumer": "demo-skill.source.gateway",
        "interfaces": {"provider.interface.run": {"id": "provider.interface.run"}},
        "helper_interfaces": {},
        "definitions": {},
    }
    projections = {
        "demo-skill.source.gateway": SimpleNamespace(document=selected),
        "demo-skill.source.coach": SimpleNamespace(
            document={**selected, "consumer": "demo-skill.source.coach"}
        ),
    }

    planned = syncer.plan_consumer_interface_updates(graph, projections)

    assert planned[root_gateway].index(syncer.USED_INTERFACES_START) > planned[
        root_gateway
    ].index(syncer.CONTRACT_END)
    assert planned[named_gateway].startswith(syncer.USED_INTERFACES_START)
    assert planned[named_gateway].endswith("Named body.\n")


def test_consumer_update_planning_rejects_shared_gateway(
    tmp_path: Path,
    syncer,
) -> None:
    module_root = tmp_path / "demo-skill"
    module_root.mkdir()
    gateway = module_root / "instructions.md"
    gateway.write_text("Body.\n", encoding="utf-8")
    graph = SimpleNamespace(
        nodes={
            node_id: SimpleNamespace(
                node_type="behavioral_source",
                gateway_path=gateway,
                module_root=module_root,
            )
            for node_id in ("demo-skill.source.one", "demo-skill.source.two")
        }
    )
    projections = {
        node_id: SimpleNamespace(
            document={
                "schema_version": 2,
                "consumer": node_id,
                "interfaces": {},
                "helper_interfaces": {},
                "definitions": {},
            }
        )
        for node_id in graph.nodes
    }

    with pytest.raises(syncer.BlueprintError, match="shared by consumers"):
        syncer.plan_consumer_interface_updates(graph, projections)


def test_generated_used_interface_block_is_deterministic(syncer) -> None:
    document = {
        "schema_version": 2,
        "consumer": "demo-skill.source.gateway",
        "interfaces": {"provider.interface.run": {"version": 1}},
        "helper_interfaces": {},
        "definitions": {},
    }

    first = syncer.generated_used_interfaces_block(document)
    second = syncer.generated_used_interfaces_block(
        json.loads(json.dumps(document))
    )

    assert first == second
    assert first.startswith(syncer.USED_INTERFACES_START)
    assert first.endswith(f"{syncer.USED_INTERFACES_END}\n")


def test_sync_does_not_create_dispatch_routing_state(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root, _dependency = _copy_v5_managed_skill(tmp_path / "repo")
    skill_file = repo_root / "skills" / "demo" / "SKILL.md"
    skill_file.write_text(
        "---\nname: demo\ndescription: Test fixture.\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    manifest = repo_root / "references" / "blueprint-schema" / "runtime_dependencies.json"
    manifest.parent.mkdir(parents=True)
    monkeypatch.setattr(syncer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", repo_root / "skills")
    monkeypatch.setattr(syncer, "RUNTIME_DEPENDENCIES_PATH", manifest)
    original_load_blueprints = syncer.load_blueprints
    monkeypatch.setattr(
        syncer,
        "load_blueprints",
        lambda **kwargs: original_load_blueprints(
            schema_root=V5_SCHEMA_ROOT,
            **kwargs,
        ),
    )
    data_home = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    assert not manifest.exists()
    assert syncer.run_sync(check_only=False, schema_version=5) == 0
    assert manifest.is_file()
    written_manifest = manifest.read_bytes()
    assert syncer.run_sync(check_only=True, schema_version=5) == 0
    assert manifest.read_bytes() == written_manifest
    assert not data_home.exists()


def test_validate_sync_state_reuses_the_provided_graph(
    tmp_path: Path,
    syncer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _copy_managed_skill(tmp_path)
    skills_root = tmp_path / "skills"
    monkeypatch.setattr(syncer, "SKILLS_ROOT", skills_root)
    graph = syncer.load_blueprints()["loose-mode"].repository_graph
    runtime_dependencies_path = (
        tmp_path / "references" / "blueprint-schema" / "runtime_dependencies.json"
    )
    runtime_dependencies_path.parent.mkdir(parents=True)
    blueprints = syncer.blueprints_from_graph(
        graph,
        skills_root=skills_root,
        schema_version=6,
    )
    runtime_dependencies_path.write_text(
        json.dumps(
            syncer.generated_runtime_dependencies_manifest(blueprints),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    def _unexpected_graph_load(*_args: object, **_kwargs: object) -> None:
        pytest.fail("sync-state checking must use the supplied graph")

    monkeypatch.setattr(
        syncer,
        "load_repository_blueprint_graph",
        _unexpected_graph_load,
    )

    assert syncer.validate_sync_state(
        repository_graph=graph,
        repository_root=tmp_path,
        skills_root=skills_root,
        runtime_dependencies_path=runtime_dependencies_path,
        schema_version=6,
    ) == []
