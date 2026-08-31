#!/usr/bin/env python3
"""Focused tests for canonical blueprint synchronization."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from validators.platform_neutral import _validate


REPO_ROOT = Path(__file__).resolve().parents[4]


SYNCER_PATH = REPO_ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"


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
    return load_module("sync_module_blueprints_tests", SYNCER_PATH)


def _copy_managed_skill(repo_root: Path) -> Path:
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


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


def test_generated_executable_interface_uses_famulus_metadata(syncer) -> None:
    """Break caught: generated skill guidance falls back to Dispatcher syntax."""
    blueprints = syncer.load_blueprints()

    interfaces = syncer.generated_interface_block(
        "milestone-logging",
        blueprints["milestone-logging"].repository_graph,
    )

    assert "Executable Interfaces:" in interfaces
    assert "Caller: `milestone-logging`" in interfaces
    assert "Version: 1" in interfaces
    assert '"positionals": ["DOING", "PREV"]' in interfaces
    assert '"--role": "ROLE"' in interfaces
    assert '"--done": "PREV"' in interfaces
    assert '"--path": true' in interfaces
    assert "Omit optional positionals and options that are not needed." in interfaces
    assert "Ordered outer JSON" not in interfaces
    assert "Alternative: `milestone`" in interfaces
    assert "dispatcher --caller-skill" not in interfaces


def test_generated_interface_block_requires_direct_source_backing(syncer) -> None:
    """Break caught: direct gateway uses do not reach public MCP guidance."""
    gateway = "consumer.source.gateway"
    process = "provider.interface.run"
    owner = "consumer.interface.owner"
    unused = "provider.interface.unused"
    instructions = "provider.interface.instructions"
    source = "provider.source.cli"
    unused_source = "provider.source.unused"
    process_spec = {
        "description": "Run the provider.",
        "usage": "",
        "process_binding": {
            "kind": "process",
            "entry": "Interface",
            "patterns": [
                {
                    "name": "default",
                    "min_positionals": 0,
                    "max_positionals": 0,
                    "allow_stdin": False,
                }
            ],
        },
    }
    graph = SimpleNamespace(
        schema_version=6,
        module_sources={"consumer": (gateway,)},
        module_ancestry={"consumer": ("consumer",), "provider": ("provider",)},
        nodes={
            "consumer": SimpleNamespace(gateway_path=Path("SKILL.md")),
            gateway: SimpleNamespace(node_id=gateway, gateway_path=Path("SKILL.md")),
            source: SimpleNamespace(version=1),
            unused_source: SimpleNamespace(version=1),
        },
        exports={
            process: SimpleNamespace(
                interface_id=process,
                version=1,
                module_node_id="provider",
                declaration=process_spec,
                source_node_id=source,
            ),
            instructions: SimpleNamespace(
                interface_id=instructions,
                version=1,
                module_node_id="provider",
                declaration={"description": "Read the provider."},
                source_node_id=source,
            ),
            owner: SimpleNamespace(
                interface_id=owner,
                version=1,
                module_node_id="consumer",
                declaration=process_spec,
                source_node_id=source,
            ),
            unused: SimpleNamespace(
                interface_id=unused,
                version=1,
                module_node_id="provider",
                declaration=process_spec,
                source_node_id=unused_source,
            ),
        },
        source_interfaces={},
        node_edges=(
            SimpleNamespace(
                relation="uses-source",
                source_id=gateway,
                target_id=source,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-source",
                source_id=gateway,
                target_id=unused_source,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=process,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=owner,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=process,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id=gateway,
                target_id=instructions,
                required_version=1,
            ),
            SimpleNamespace(
                relation="uses-export",
                source_id="consumer.source.worker",
                target_id="provider.interface.transitive",
                required_version=1,
            ),
        ),
    )

    rendered = syncer.generated_interface_block("consumer", graph)

    assert "`provider.interface.run`" in rendered
    assert rendered.count("`provider.interface.run`") == 1
    assert rendered.count("`consumer.interface.owner`") == 1
    assert "Caller: `consumer`" in rendered
    assert instructions not in rendered
    assert unused not in rendered
    assert "provider.interface.transitive" not in rendered

    original_edges = graph.node_edges
    graph.node_edges = tuple(
        edge for edge in graph.node_edges if edge.relation != "uses-source"
    )
    assert process not in syncer.generated_interface_block("consumer", graph)

    graph.node_edges = tuple(
        SimpleNamespace(
            relation=edge.relation,
            source_id=edge.source_id,
            target_id=edge.target_id,
            required_version=(
                2
                if edge.relation == "uses-export" and edge.target_id == process
                else edge.required_version
            ),
        )
        for edge in original_edges
    )
    with pytest.raises(syncer.BlueprintError, match="use version"):
        syncer.generated_interface_block("consumer", graph)


def test_llm_wakeup_generated_contract_and_interfaces_are_exact(syncer) -> None:
    blueprint = syncer.load_blueprints()["llm-wakeup"]
    skill = blueprint.path.parent / "SKILL.md"
    generated = skill.read_text(encoding="utf-8")

    assert syncer.generated_contract_block(
        blueprint.name, blueprint.data, blueprint.repository_graph
    ) in generated
    assert syncer.generated_interface_block(
        blueprint.name, blueprint.repository_graph
    ) in generated


def test_generated_llm_wakeup_arguments_are_platform_neutral(
    syncer, tmp_path: Path
) -> None:
    graph = syncer.load_blueprints()["llm-wakeup"].repository_graph
    skill = tmp_path / "skills" / "llm-wakeup" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        syncer.generated_interface_block("llm-wakeup", graph), encoding="utf-8"
    )

    assert _validate(tmp_path, frozenset()) == []


def test_public_syncer_repairs_corrupt_llm_wakeup_entry(
    syncer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: public sync check does not repair a selected external use."""
    repository = tmp_path / "repository"
    shutil.copytree(REPO_ROOT / "skills", repository / "skills")
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        repository / "references" / "blueprint-schema",
    )
    shutil.copytree(REPO_ROOT / "src", repository / "src")
    shutil.copytree(
        REPO_ROOT / "references" / "node-standards",
        repository / "references" / "node-standards",
    )
    shutil.copy2(REPO_ROOT / "officina.toml", repository / "officina.toml")
    monkeypatch.setattr(syncer, "REPO_ROOT", repository)
    monkeypatch.setattr(syncer, "SKILLS_ROOT", repository / "skills")
    monkeypatch.setattr(
        syncer,
        "BLUEPRINT_SCHEMA_ROOT",
        repository / "references" / "blueprint-schema",
    )
    monkeypatch.setattr(
        syncer,
        "RUNTIME_DEPENDENCIES_PATH",
        repository / "references" / "blueprint-schema" / "runtime_dependencies.json",
    )
    skill = repository / "skills" / "llm-wakeup" / "SKILL.md"
    original = skill.read_text(encoding="utf-8")
    start = original.index(syncer.INTERFACES_START)
    skill.write_text(
        original[:start]
        + original[start:].replace(
            "`wakeup.interface.explicit-schedule`", "`wakeup.interface.removed`", 1
        ),
        encoding="utf-8",
    )

    check = SimpleNamespace(check=True, schema_version=6)
    assert syncer.Interface().run(check) == 1
    assert syncer.Interface().run(SimpleNamespace(check=False, schema_version=6)) == 0

    blueprint = syncer.load_blueprints()["llm-wakeup"]
    repaired = skill.read_text(encoding="utf-8")
    assert syncer.generated_contract_block(
        blueprint.name, blueprint.data, blueprint.repository_graph
    ) in repaired
    assert syncer.generated_interface_block(
        blueprint.name, blueprint.repository_graph
    ) in repaired
    assert syncer.Interface().run(check) == 0


def test_generated_executable_patterns_preserve_alternatives_and_arity(syncer) -> None:
    """Break caught: a short-account template admits the forbidden long form."""
    graph = syncer.load_blueprints()["email-client"].repository_graph

    interfaces = syncer.generated_interface_block("email-client._rtx", graph)
    start = interfaces.index("email-client._rtx.interface.mail-attachments")
    end = interfaces.index("email-client._rtx.interface.mail-folders")
    attachments = interfaces[start:end]

    assert "Alternative: `short-account`" in attachments
    short_attachments = attachments[:attachments.index("Alternative: `long-account`")]
    assert 'Required options: ["-a"]; positional arity: 1..unbounded; stdin: forbidden' in short_attachments
    assert '"positionals": ["uid", "uid..."]' in short_attachments
    assert '"-a": "nickname"' in short_attachments
    assert '"--folder": "inbox|sent|drafts|trash|all|<literal>"' in short_attachments
    assert '"--account":' not in short_attachments
    long_attachments = attachments[attachments.index("Alternative: `long-account`"):]
    assert '"positionals": ["uid", "uid..."]' in long_attachments
    assert '"--account": "nickname"' in long_attachments
    assert '"-a":' not in long_attachments
    folders = interfaces[interfaces.index("email-client._rtx.interface.mail-folders"):]
    assert "Alternative: `long-account`" in folders
    assert 'Required options: ["--account"]' in folders
    assert "stdin: permitted" in interfaces


def test_generated_executable_rejects_ambiguous_usage(syncer) -> None:
    graph = syncer.load_blueprints()["email-client"].repository_graph
    export = graph.exports["email-client._rtx.interface.mail-attachments"]
    spec, _source_id = syncer._generated_export_binding(
        graph, export.interface_id, export
    )
    spec["usage"] = ""

    with pytest.raises(ValueError, match="usage cannot be projected unambiguously"):
        syncer.generated_interface_block("email-client", graph)


def test_generated_executable_preserves_nested_placeholders_without_fallbacks(syncer) -> None:
    blueprints = syncer.load_blueprints()
    graph = blueprints["email-client"].repository_graph

    interfaces = syncer.generated_interface_block("email-client._rtx", graph)

    assert '"--attach": "/path[:DisplayName]"' in interfaces
    for skill in ("email-client", "daily-plan", "node-certify", "node-drift"):
        blueprint = blueprints[skill]
        generated = syncer.generated_interface_block(
            skill, blueprint.repository_graph
        )
        assert "POSITIONAL_" not in generated

    daily = syncer.generated_interface_block(
        "daily-plan", blueprints["daily-plan"].repository_graph
    )
    indexed = daily[daily.index("Alternative: `indexed-or-add`"):daily.index("Alternative: `set-deadline`")]
    assert "set-deadline" not in indexed
    assert '"positionals": ["set-deadline", "actions|triage", "indices-or-item-id", "deadline-for-set-deadline"]' in daily

    triage = syncer.generated_interface_block(
        "email-triage", blueprints["email-triage"].repository_graph
    )
    assert '"--total-scanned": "N"' in triage
    assert '"--added-todo": "N"' in triage


def test_generated_executable_rejects_ambiguous_option_alias(syncer) -> None:
    graph = syncer.load_blueprints()["email-client"].repository_graph
    export = graph.exports["email-client._rtx.interface.mail-folders"]
    spec, _source_id = syncer._generated_export_binding(
        graph, export.interface_id, export
    )
    long_pattern = spec["process_binding"]["patterns"][1]
    spec["process_binding"]["patterns"] = [long_pattern]
    spec["usage"] = "-a <nickname> -b <other>"
    long_pattern["forbidden_flags"] = ["-a", "-b"]

    with pytest.raises(ValueError, match="ambiguous option alias"):
        syncer.generated_interface_block("email-client", graph)


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


def test_real_setup_contracts_do_not_cross_contaminate(syncer) -> None:
    blueprints = syncer.load_blueprints()
    google = syncer.generated_contract_block(
        "connect-google",
        blueprints["connect-google"].data,
        blueprints["connect-google"].repository_graph,
    )
    lists = syncer.generated_contract_block(
        "list-manager",
        blueprints["list-manager"].data,
        blueprints["list-manager"].repository_graph,
    )

    assert "Setup Requires Setup Of: none" in google
    assert "Setup Requires Setup Of: none" in lists
    assert "connect-google.interface.setup" not in lists


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
    ) == []
