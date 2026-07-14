"""Smoke tests for skills/skill-maker/validators/blueprint_relationships.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-maker" / "validators" / "blueprint_relationships.py"
)
_spec = importlib.util.spec_from_file_location("blueprint_relationships", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_blueprint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def _machine_interface(version: int = 1, **extra: object) -> dict:
    interface = {
        "version": version,
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_tool_entry.py:Interface",
            "behavior_sources": [],
        },
        "dependencies": [],
    }
    interface.update(extra)
    return interface


def _llm_interface(version: int = 1, **extra: object) -> dict:
    interface = {
        "version": version,
        "description": "Primary LLM-facing skill instructions.",
        "binding": {"kind": "skill_file", "path": "SKILL.md"},
        "behavior_sources": [],
    }
    interface.update(extra)
    return interface


def test_no_blueprints_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_legacy_depends_on_is_rejected(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "my-skill" / "blueprint.yaml",
        {"depends_on": {"other-skill": {}}},
    )
    errors = _mod.validate(tmp_path)
    assert any("top-level `depends_on`" in e for e in errors)


def test_valid_machine_use_passes(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "read-data": _machine_interface(allowed_callers=["consumer-skill"]),
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    assert _mod.validate(tmp_path) == []


def test_unknown_and_stale_interface_uses_are_rejected(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(version=2, allow_all_skills=True)},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1},
                            {"interface": "missing-skill.machine.read-data", "version": 1},
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("target version is 2" in e for e in errors)
    assert any("unknown interface" in e for e in errors)


def test_cross_skill_llm_to_machine_use_follows_relationship_matrix(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(allow_all_skills=True)},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {},
                "llm": {
                    "default": _llm_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert errors == []


def test_access_control_is_enforced(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path / "skills" / "producer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {"read-data": _machine_interface(allowed_callers=["other-skill"])},
                "llm": {"default": _llm_interface()},
            },
        },
    )
    _write_blueprint(
        tmp_path / "skills" / "consumer-skill" / "blueprint.yaml",
        {
            "interfaces": {
                "machine": {
                    "consume": _machine_interface(
                        uses_interfaces=[
                            {"interface": "producer-skill.machine.read-data", "version": 1}
                        ]
                    )
                },
                "llm": {"default": _llm_interface()},
            },
        },
    )
    errors = _mod.validate(tmp_path)
    assert any("not allowed by target access control" in e for e in errors)


def _typed_machine_skill(
    tmp_path: Path,
    name: str,
    *,
    uses: list[str] | None = None,
    allow_all_skills: bool = False,
    allowed_callers: list[str] | None = None,
) -> None:
    skill = tmp_path / "skills" / name
    runtime = skill / "_rtx"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\nBody.\n")
    (runtime / "_runner.py").write_text("class Interface: pass\n")
    interface_id = f"{name}.machine.run"
    _write_blueprint(
        skill / "blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "skill",
            "id": name,
            "interfaces": [
                {
                    "interface": interface_id,
                    "version": 1,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._runner.py.blueprint.yaml",
                    },
                }
            ],
        },
    )
    _write_blueprint(
        runtime / "._runner.py.blueprint.yaml",
        {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": interface_id,
            "version": 1,
            "description": "Run.",
            "usage": "run",
            "binding": {
                "kind": "python-entrypoint",
                "path": "_rtx/_runner.py",
                "symbol": "Interface",
            },
            "allow_all_skills": allow_all_skills,
            "allowed_callers": allowed_callers or [],
            "dependencies": [],
            "uses_interfaces": [
                {"interface": target, "version": 1} for target in uses or []
            ],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
    )


def test_typed_graph_relationships_use_the_same_access_rules(tmp_path: Path) -> None:
    _typed_machine_skill(
        tmp_path,
        "producer-skill",
        allowed_callers=["consumer-skill"],
    )
    _typed_machine_skill(
        tmp_path,
        "consumer-skill",
        uses=["producer-skill.machine.run"],
    )

    assert _mod.validate(tmp_path) == []


def test_shared_source_edges_repeated_across_root_graphs_are_not_duplicates(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "skills" / "provider-skill"
    shared = _mod.BlueprintNode("provider-skill.source.shared", "behavior-source", 1, provider, provider / ".shared.blueprint.yaml", provider / "shared.md", {})
    child = _mod.BlueprintNode("provider-skill.source.child", "behavior-source", 1, provider, provider / ".child.blueprint.yaml", provider / "child.md", {})
    edge = _mod.BlueprintEdge("uses-behavior-source", shared.node_id, child.node_id, 1)
    graphs = {}
    for name in ("first-skill", "second-skill"):
        skill_root = tmp_path / "skills" / name
        root = _mod.BlueprintNode(name, "skill", 1, skill_root, skill_root / "blueprint.yaml", None, {})
        graphs[name] = _mod.SkillBlueprintGraph(
            skill_root,
            root,
            {name: root, shared.node_id: shared, child.node_id: child},
            (edge,),
        )

    assert _mod.validate_graphs(graphs) == []


def test_duplicate_edge_within_one_root_graph_is_rejected(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "demo-skill"
    root = _mod.BlueprintNode("demo-skill", "skill", 1, skill_root, skill_root / "blueprint.yaml", None, {})
    source = _mod.BlueprintNode("demo-skill.source.one", "behavior-source", 1, skill_root, skill_root / ".one.blueprint.yaml", skill_root / "one.md", {})
    target = _mod.BlueprintNode("demo-skill.source.two", "behavior-source", 1, skill_root, skill_root / ".two.blueprint.yaml", skill_root / "two.md", {})
    edge = _mod.BlueprintEdge("uses-behavior-source", source.node_id, target.node_id, 1)
    graph = _mod.SkillBlueprintGraph(skill_root, root, {root.node_id: root, source.node_id: source, target.node_id: target}, (edge, edge))

    errors = _mod.validate_graphs({root.node_id: graph})

    assert any("duplicates an existing relationship" in error for error in errors)


def test_skill_cannot_directly_reference_other_skill_behavior_source(tmp_path: Path) -> None:
    first = tmp_path / "skills" / "first-skill"
    second = tmp_path / "skills" / "second-skill"
    root = _mod.BlueprintNode(
        "first-skill",
        "skill",
        1,
        first,
        first / "blueprint.yaml",
        None,
        {},
    )
    interface = _mod.BlueprintNode(
        "first-skill.llm.default",
        "llm-interface",
        1,
        first,
        first / ".SKILL.md.blueprint.yaml",
        first / "SKILL.md",
        {},
    )
    private_source = _mod.BlueprintNode(
        "second-skill.source.private",
        "behavior-source",
        1,
        second,
        second / "references" / ".private.md.blueprint.yaml",
        second / "references" / "private.md",
        {},
    )
    graph = _mod.SkillBlueprintGraph(
        first,
        root,
        {root.node_id: root, interface.node_id: interface, private_source.node_id: private_source},
        (
            _mod.BlueprintEdge("declares-interface", root.node_id, interface.node_id, 1),
            _mod.BlueprintEdge(
                "uses-behavior-source",
                interface.node_id,
                private_source.node_id,
                1,
            ),
        ),
    )

    errors = _mod.validate_graphs({root.node_id: graph})

    assert any(
        "behavior source outside declaring skill or repository references" in error
        for error in errors
    )


def test_typed_cross_skill_cycle_is_rejected(tmp_path: Path) -> None:
    _typed_machine_skill(
        tmp_path,
        "alpha-skill",
        uses=["beta-skill.machine.run"],
        allow_all_skills=True,
    )
    _typed_machine_skill(
        tmp_path,
        "beta-skill",
        uses=["alpha-skill.machine.run"],
        allow_all_skills=True,
    )

    errors = _mod.validate(tmp_path)
    assert any("blueprint graph cycle" in error for error in errors)


def test_relationship_decisions_are_loaded_from_schema_meta(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills" / "demo-skill"
    root = _mod.BlueprintNode(
        "demo-skill", "skill", 1, skill_root, skill_root / "blueprint.yaml", None, {}
    )
    source = _mod.BlueprintNode(
        "demo-skill.machine.one",
        "machine-interface",
        1,
        skill_root,
        skill_root / "_rtx" / ".one.blueprint.yaml",
        skill_root / "_rtx" / "one.py",
        {},
    )
    target = _mod.BlueprintNode(
        "demo-skill.machine.two",
        "machine-interface",
        1,
        skill_root,
        skill_root / "_rtx" / ".two.blueprint.yaml",
        skill_root / "_rtx" / "two.py",
        {},
    )
    graph = _mod.SkillBlueprintGraph(
        skill_root,
        root,
        {node.node_id: node for node in (root, source, target)},
        (_mod.BlueprintEdge("uses-interface", source.node_id, target.node_id, 1),),
    )
    schema_root = tmp_path / "schema"
    schema_root.mkdir()
    (schema_root / "schema-meta.json").write_text(
        '{"x-famulus":{"relationship_matrix":{"skill":{},'
        '"llm-interface":{},"machine-interface":{},"behavior-source":{}}}}',
        encoding="utf-8",
    )

    errors = _mod.validate_graphs({root.node_id: graph}, schema_root=schema_root)

    assert any("relationship matrix" in error for error in errors)
