from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
import yaml

import officina.common.interface_injection_migration as migration
from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintNode,
    MachineInterfaceExport,
    RepositoryBlueprintGraph,
)
from officina.common.interface_injection_migration import (
    InterfaceInjectionMigrationError,
    build_interface_injection_migration_report,
)


_EXACT_PYTHON_PACKAGE_SUPPORT_POLICY = {
    "initializer_disposition": "create-behavioral-source",
    "sibling_python_disposition": "same-source-unless-claimed",
    "nested_packages": "distinct-support-sources",
    "imported_source_dependencies": "ast-resolved-exact",
    "non_python_and_blueprint_files": "exclude",
    "collision_behavior": "reject",
    "created_path_predecessor": "initializer-and-unclaimed-direct-python-siblings",
    "import_search_roots": {
        "default": ["module-root"],
        "by_gateway": {
            "skills/skill-drift/_rtx/_check_drift_state.py": [
                "gateway-parent",
                "module-root",
            ]
        },
    },
}


def _graph() -> RepositoryBlueprintGraph:
    root = Path("/repo/skills/consumer-skill")
    consumer = BlueprintNode(
        node_id="consumer-skill.llm.default",
        node_type="llm-interface",
        version=1,
        skill_root=root,
        blueprint_path=root / "blueprint.yaml",
        gateway_path=root / "SKILL.md",
        declaration={},
    )
    module = BlueprintNode(
        node_id="provider-skill.machine-module.worker",
        node_type="machine-module",
        version=1,
        skill_root=Path("/repo/skills/provider-skill"),
        blueprint_path=Path("/repo/skills/provider-skill/_rtx/._worker.py.blueprint.yaml"),
        gateway_path=Path("/repo/skills/provider-skill/_rtx/_worker.py"),
        declaration={},
    )
    export = MachineInterfaceExport(
        interface_id="provider-skill.machine.run",
        version=1,
        local_name="run",
        module_node_id=module.node_id,
        declaration={},
    )
    return RepositoryBlueprintGraph(
        nodes={consumer.node_id: consumer, module.node_id: module},
        node_edges=(
            BlueprintEdge(
                "uses-interface", consumer.node_id, export.interface_id, 1
            ),
        ),
        machine_exports={export.interface_id: export},
        export_edges=(),
        helper_edges=(),
        certification_edges=(),
    )


def test_report_is_complete_deterministic_and_machine_readable() -> None:
    report = build_interface_injection_migration_report(
        _graph(),
        ["stale-skill.machine.old", "provider-skill.machine.run"],
        {
            "provider-skill.machine.run": "add-direct-edge",
            "stale-skill.machine.old": "retire",
        },
    )

    assert [entry.interface_id for entry in report.entries] == [
        "provider-skill.machine.run",
        "stale-skill.machine.old",
    ]
    assert report.entries[0].authored_consumers == (
        "consumer-skill.llm.default",
    )
    assert report.entries[1].target_exists is False
    assert report.as_document()["schema_version"] == 1


@pytest.mark.parametrize(
    ("legacy", "dispositions", "message"),
    [
        (["provider-skill.machine.run"], {}, "missing dispositions"),
        ([], {"provider-skill.machine.run": "retire"}, "unexpected dispositions"),
        (["x", "x"], {"x": "retire"}, "duplicate interface IDs"),
        (["x"], {"x": "unknown"}, "invalid disposition"),
        (["x"], {"x": "add-direct-edge"}, "requires a target export"),
    ],
)
def test_report_rejects_incomplete_duplicate_or_invalid_dispositions(
    legacy: list[str], dispositions: dict[str, str], message: str
) -> None:
    with pytest.raises(InterfaceInjectionMigrationError, match=message):
        build_interface_injection_migration_report(
            _graph(), legacy, dispositions
        )


def test_conversion_exposes_only_planned_declaration_paths() -> None:
    conversion = migration.BlueprintDeclarationConversion(
        documents={Path("skills/demo/blueprints/run.yaml"): {}},
        removed_paths=(
            Path("skills/demo/blueprint.yaml"),
            Path("skills/demo/blueprints/run.yaml"),
        ),
        public_graph_projection={},
        runtime_dependency_projection={},
        behavioral_source_dependency_projection={},
    )

    assert conversion.declaration_paths == (
        Path("skills/demo/blueprint.yaml"),
        Path("skills/demo/blueprints/run.yaml"),
    )


def test_converter_preserves_authored_evidence_without_inventing_semantics(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (skill / "_rtx" / "_runner.py").write_text(
        "from _other import Other\nclass Interface:\n    pass\n", encoding="utf-8"
    )
    (skill / "_rtx" / "_other.py").write_text(
        "class Other:\n    pass\n", encoding="utf-8"
    )
    (skill / "_rtx" / "__init__.py").write_text(
        "from ._helper import VALUE\n", encoding="utf-8"
    )
    (skill / "_rtx" / "_helper.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (skill / "_rtx" / "data.json").write_text("{}\n", encoding="utf-8")
    (skill / "_rtx" / "._helper.py.blueprint.yaml").write_text(
        "schema_version: 2\n", encoding="utf-8"
    )
    (skill / "_rtx" / "nested").mkdir()
    (skill / "_rtx" / "nested" / "__init__.py").write_text(
        "NESTED = 1\n", encoding="utf-8"
    )
    (skill / "_rtx" / "nested" / "_nested.py").write_text(
        "NESTED = 1\n", encoding="utf-8"
    )
    blueprint = {
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "interfaces": {
            "machine": {
                "run": {
                    "version": 2,
                    "description": "Run the operation.",
                    "usage": "<name> --cloud",
                    "patterns": [
                        {
                            "min_positionals": 1,
                            "max_positionals": 1,
                            "allow_stdin": False,
                            "required_flags": ["--cloud"],
                        }
                    ],
                    "allow_all_skills": False,
                    "allowed_callers": [],
                    "platform_support": {
                        "linux": True,
                        "macos": True,
                        "windows": True,
                    },
                    "dependencies": [],
                    "uses_interfaces": [
                        {"interface": "demo-skill.machine.other", "version": 1}
                    ],
                    "direct_io": {
                        "reads": [
                            {
                                "medium": "remote-filesystem",
                                "access": "read",
                                "system": "google-drive",
                                "content": "document",
                                "format": "text",
                                "path": "lists/<name>",
                                "sensitivity": "user-private",
                            }
                        ],
                        "writes": [],
                        "network": [
                            {
                                "medium": "network-request",
                                "access": "download",
                                "system": "google-drive",
                                "content": "document",
                                "formats": ["text", "markdown"],
                                "sensitivity": "user-private",
                            }
                        ],
                    },
                    "owns_filesystem": [],
                    "invocation": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_runner.py:Interface",
                        "args_prefix": ["run"],
                        "behavior_sources": [],
                    },
                },
                "other": {
                    "version": 1,
                    "description": "Run the other operation.",
                    "allow_all_skills": False,
                    "allowed_callers": [],
                    "platform_support": {
                        "linux": True,
                        "macos": True,
                        "windows": True,
                    },
                    "dependencies": [],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                    "owns_filesystem": [],
                    "invocation": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_other.py:Other",
                        "behavior_sources": [],
                    },
                },
            },
            "llm": {
                "default": {
                    "version": 1,
                    "description": "Primary instructions.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "allow_all_skills": True,
                    "behavior_sources": [],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                    "owns_filesystem": [],
                }
            },
        },
    }
    path = skill / "blueprint.yaml"
    path.write_text(yaml.safe_dump(blueprint, sort_keys=False), encoding="utf-8")

    conversion = migration.convert_blueprint_declarations(
        tmp_path, [Path("skills/demo-skill/blueprint.yaml")]
    )

    module = conversion.documents[Path("skills/demo-skill/blueprint.yaml")]
    source_path = Path("skills/demo-skill/blueprints/rtx-runner.yaml")
    source = conversion.documents[source_path]
    interface = source["interfaces"][
        "demo-skill.source.rtx-runner.interface.run"
    ]
    direct_io = interface["contract"]["direct_io"]
    assert module["exports"]["demo-skill.interface.run"]["access"] == {
        "allow_all_modules": False,
        "allowed_callers": [],
    }
    assert interface["usage"] == "<name> --cloud"
    assert interface["process_binding"] == {
        "kind": "process",
        "entry": "Interface",
        "args_prefix": ["run"],
        "patterns": blueprint["interfaces"]["machine"]["run"]["patterns"],
    }
    assert direct_io["reads"][0] == {
        "id": "read-1",
        "medium": "remote-filesystem",
        "access": "read",
        "system": "google-drive",
        "content": "document",
        "formats": ["text"],
        "path": "lists/<name>",
        "path_match": "exact",
        "sensitivity": "user-private",
    }
    assert direct_io["network"][0]["formats"] == ["text", "markdown"]
    assert "endpoint" not in direct_io["network"][0]
    assert set(interface["contract"]) == {"direct_io"}
    assert {"outcomes", "execution", "helpers"}.isdisjoint(interface["contract"])
    support_id = "demo-skill.source.rtx-init"
    support = conversion.documents[
        Path("skills/demo-skill/blueprints/rtx-init.yaml")
    ]
    assert support["gateway"] == {
        "path": "_rtx/__init__.py",
        "language": "Python",
    }
    assert support["content"] == [
        r"_rtx/__init__\.py",
        r"_rtx/_helper\.py",
    ]
    nested_support = conversion.documents[
        Path("skills/demo-skill/blueprints/rtx-nested-init.yaml")
    ]
    assert nested_support["content"] == [
        r"_rtx/nested/__init__\.py",
        r"_rtx/nested/_nested\.py",
    ]
    assert {
        Path("skills/demo-skill/blueprints/rtx-init.yaml"),
        Path("skills/demo-skill/blueprints/rtx-nested-init.yaml"),
    }.issubset(conversion.package_support_paths)
    assert source["dependencies"] == [
        {
            "source": support_id,
            "version": 1,
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/rtx-init.yaml",
            },
            "reason": "Loads Python package support from _rtx/__init__.py.",
        }
    ]
    assert source["uses_interfaces"] == [
        {
            "interface": "demo-skill.source.rtx-other.interface.other",
            "version": 1,
        }
    ]


def test_converter_merges_v2_sidecars_by_gateway_and_resolves_sources(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text("Instructions.\n", encoding="utf-8")
    (skill / "_rtx" / "_runner.py").write_text(
        "class First: pass\nclass Second: pass\n", encoding="utf-8"
    )
    (skill / "references" / "policy.md").write_text(
        "Policy.\n", encoding="utf-8"
    )
    declarations = {
        "blueprint.yaml": {
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
                },
                {
                    "interface": "demo-skill.machine.first",
                    "version": 2,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._runner.py.first.blueprint.yaml",
                    },
                },
                {
                    "interface": "demo-skill.machine.second",
                    "version": 3,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "_rtx/._runner.py.second.blueprint.yaml",
                    },
                },
            ],
        },
        ".SKILL.md.blueprint.yaml": {
            "schema_version": 2,
            "blueprint_type": "llm-interface",
            "id": "demo-skill.llm.default",
            "version": 1,
            "description": "Primary instructions.",
            "binding": {"kind": "instruction-file", "path": "SKILL.md"},
            "allow_all_skills": True,
            "uses_interfaces": [],
            "behavior_sources": [
                {
                    "source": "demo-skill.source.policy",
                    "version": 4,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "references/.policy.md.blueprint.yaml",
                    },
                    "reason": "Supplies policy.",
                }
            ],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
        "_rtx/._runner.py.first.blueprint.yaml": {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": "demo-skill.machine.first",
            "version": 2,
            "description": "Run first.",
            "usage": "first",
            "binding": {
                "kind": "python-entrypoint",
                "path": "_rtx/_runner.py",
                "symbol": "First",
            },
            "patterns": [{"min_positionals": 0, "allow_stdin": False}],
            "allow_all_skills": False,
            "allowed_callers": [],
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "dependencies": [],
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
        "_rtx/._runner.py.second.blueprint.yaml": {
            "schema_version": 2,
            "blueprint_type": "machine-interface",
            "id": "demo-skill.machine.second",
            "version": 3,
            "description": "Run second.",
            "binding": {
                "kind": "python-entrypoint",
                "path": "_rtx/_runner.py",
                "symbol": "Second",
                "args_prefix": ["second"],
            },
            "allow_all_skills": True,
            "allowed_callers": [],
            "platform_support": {"linux": True, "macos": True, "windows": True},
            "dependencies": [],
            "uses_interfaces": [],
            "behavior_sources": [],
            "direct_io": {"reads": [], "writes": [], "network": []},
            "owns_filesystem": [],
        },
        "references/.policy.md.blueprint.yaml": {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.policy",
            "version": 4,
            "description": "Defines policy.",
            "binding": {"kind": "file", "path": "references/policy.md"},
            "content": "config",
            "format": "markdown",
            "uses_behavior_sources": [],
        },
    }
    mapped = []
    for relative, value in declarations.items():
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        mapped.append(Path("skills/demo-skill") / relative)

    conversion = migration.convert_blueprint_declarations(tmp_path, mapped)

    runner = conversion.documents[
        Path("skills/demo-skill/blueprints/rtx-runner.yaml")
    ]
    gateway = conversion.documents[
        Path("skills/demo-skill/blueprints/gateway.yaml")
    ]
    policy = conversion.documents[
        Path("skills/demo-skill/blueprints/references-policy.yaml")
    ]
    assert set(runner["interfaces"]) == {
        "demo-skill.source.rtx-runner.interface.first",
        "demo-skill.source.rtx-runner.interface.second",
    }
    assert runner["interfaces"][
        "demo-skill.source.rtx-runner.interface.first"
    ]["process_binding"]["entry"] == "First"
    assert runner["interfaces"][
        "demo-skill.source.rtx-runner.interface.second"
    ]["process_binding"]["entry"] == "Second"
    assert gateway["dependencies"] == [
        {
            "source": "demo-skill.source.policy",
            "version": 4,
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/references-policy.yaml",
            },
            "reason": "Supplies policy.",
        }
    ]
    assert policy["id"] == "demo-skill.source.policy"
    assert policy["version"] == 4
    assert policy["description"] == "Defines policy."


def _reference_module_map() -> dict[str, object]:
    return {
        "declarations": {
            "mechanical_conversion": {
                "python_package_support": dict(
                    _EXACT_PYTHON_PACKAGE_SUPPORT_POLICY
                ),
                "reference_modules": [
                    {
                        "root": "references/standards",
                        "id": "standards",
                        "gateway": {"path": "index.yaml", "language": "YAML"},
                        "sources": [
                            {
                                "id": "standards.source.policy",
                                "version": 1,
                                "blueprint": "blueprints/policy.yaml",
                                "gateway": {
                                    "path": "policy.md",
                                    "language": "Markdown",
                                },
                                "legacy": {
                                    "path": "$repo/references/standards/policy.md",
                                    "content": "config",
                                    "format": "markdown",
                                },
                            }
                        ],
                    }
                ],
                "legacy_behavior_source_dependencies": [
                    {
                        "consumer": "consumer-skill.llm.default",
                        "authored": {
                            "path": "$repo/references/standards/policy.md",
                            "content": "config",
                            "format": "markdown",
                            "reason": "Supplies the exact shared policy.",
                        },
                        "target": {
                            "source": "standards.source.policy",
                            "version": 1,
                            "blueprint": {
                                "base": "repository-root",
                                "path": "references/standards/blueprints/policy.yaml",
                            },
                        },
                    }
                ],
            }
        }
    }


def _write_reference_module_conversion_fixture(tmp_path: Path) -> tuple[Path, str]:
    reference_root = tmp_path / "references" / "standards"
    reference_root.mkdir(parents=True)
    (reference_root / "index.yaml").write_text("version: 1\n", encoding="utf-8")
    policy_text = "The exact shared policy.\n"
    (reference_root / "policy.md").write_text(policy_text, encoding="utf-8")

    skill = tmp_path / "skills" / "consumer-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Consumer.\n", encoding="utf-8")
    blueprint = {
        "category": "development-assistant",
        "role": "automation",
        "kind": "tool",
        "interfaces": {
            "machine": {},
            "llm": {
                "default": {
                    "version": 1,
                    "description": "Consumes the policy.",
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "allow_all_skills": True,
                    "behavior_sources": [
                        {
                            "path": "$repo/references/standards/policy.md",
                            "content": "config",
                            "format": "markdown",
                            "reason": "Supplies the exact shared policy.",
                        }
                    ],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                    "owns_filesystem": [],
                }
            },
        },
    }
    declaration = skill / "blueprint.yaml"
    declaration.write_text(
        yaml.safe_dump(blueprint, sort_keys=False), encoding="utf-8"
    )
    return declaration, policy_text


def test_converter_materializes_map_declared_reference_modules(tmp_path: Path) -> None:
    declaration, _ = _write_reference_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_reference_module_map(),
    )

    module = conversion.documents[Path("references/standards/blueprint.yaml")]
    source = conversion.documents[
        Path("references/standards/blueprints/policy.yaml")
    ]
    assert module == {
        "schema_version": 4,
        "node_type": "module",
        "id": "standards",
        "version": 1,
        "gateway": {"path": "index.yaml", "language": "YAML"},
        "content": ["index\\.yaml", "policy\\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {
            "standards.source.policy": {
                "blueprint": {
                    "base": "module-root",
                    "path": "blueprints/policy.yaml",
                }
            }
        },
        "exports": {},
    }
    assert source == {
        "schema_version": 4,
        "node_type": "behavioral_source",
        "id": "standards.source.policy",
        "version": 1,
        "gateway": {"path": "policy.md", "language": "Markdown"},
        "content": ["policy\\.md"],
        "dependencies": [],
        "uses_interfaces": [],
        "interfaces": {},
    }


def test_converter_maps_exact_legacy_behavior_dependency_without_moving_content(
    tmp_path: Path,
) -> None:
    declaration, policy_text = _write_reference_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_reference_module_map(),
    )

    consumer = conversion.documents[
        Path("skills/consumer-skill/blueprints/gateway.yaml")
    ]
    expected_dependency = {
        "source": "standards.source.policy",
        "version": 1,
        "blueprint": {
            "base": "repository-root",
            "path": "references/standards/blueprints/policy.yaml",
        },
        "reason": "Supplies the exact shared policy.",
    }
    assert consumer["dependencies"] == [expected_dependency]
    assert conversion.behavioral_source_dependency_projection == {
        "consumer-skill.source.gateway": [expected_dependency]
    }
    assert (tmp_path / "references" / "standards" / "policy.md").read_text(
        encoding="utf-8"
    ) == policy_text
    assert not (tmp_path / "references" / "standards" / "blueprint.yaml").exists()
    assert not (tmp_path / "references" / "standards" / "blueprints").exists()


def test_emitted_v4_mutation_cannot_mutate_legacy_dependency_expectations(
    tmp_path: Path,
) -> None:
    declaration, _ = _write_reference_module_conversion_fixture(tmp_path)
    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_reference_module_map(),
    )
    expected = deepcopy(conversion.behavioral_source_dependency_projection)

    consumer = conversion.documents[
        Path("skills/consumer-skill/blueprints/gateway.yaml")
    ]
    consumer["dependencies"][0]["reason"] = "mutated emitted v4"

    assert conversion.behavioral_source_dependency_projection == expected


def test_reference_modules_add_no_public_or_runtime_exports(tmp_path: Path) -> None:
    declaration, _ = _write_reference_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_reference_module_map(),
    )

    assert Path("references/standards/blueprint.yaml") in conversion.documents
    assert set(conversion.public_graph_projection["exports"]) == {
        "consumer-skill.interface.default"
    }
    assert not any(
        source_id.startswith("standards.")
        for source_id in conversion.runtime_dependency_projection
    )


def test_live_map_excludes_only_reviewed_stale_list_manager_sidecar() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )

    validation = migration.validate_blueprint_migration_map(repo_root, map_document)

    stale = Path("skills/list-manager/.SKILL.md.blueprint.yaml")
    assert stale not in validation.mapped_declaration_paths
    assert validation.non_live_local_paths == (stale,)


def test_excluded_list_manager_sidecar_emits_no_fake_preference_source_or_io() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    live_map = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )
    conversion_map = {
        "declarations": {
                "mechanical_conversion": {
                    "python_package_support": dict(
                        _EXACT_PYTHON_PACKAGE_SUPPORT_POLICY
                    ),
                    "shared_gateway_merge": live_map["declarations"]
                ["mechanical_conversion"]["shared_gateway_merge"]
            },
            "version_2": {"merge_decisions": []},
        }
    }
    conversion = migration.convert_blueprint_declarations(
        repo_root,
        [Path("skills/list-manager/blueprint.yaml")],
        migration_map=conversion_map,
    )

    module = conversion.documents[Path("skills/list-manager/blueprint.yaml")]
    gateway = conversion.documents[
        Path("skills/list-manager/blueprints/gateway.yaml")
    ]
    interface = gateway["interfaces"][
        "list-manager.source.gateway.interface.default"
    ]
    assert "list-manager.source.personal-preferences-default" not in module["sources"]
    assert gateway["dependencies"] == []
    assert all(
        entry.get("path") != "personal-preferences/default.md"
        for entry in interface["contract"]["direct_io"]["reads"]
    )


def test_active_mapped_input_with_unresolved_behavior_source_is_rejected(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Demo.\n", encoding="utf-8")
    root = {
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
    }
    sidecar = {
        "schema_version": 2,
        "blueprint_type": "llm-interface",
        "id": "demo-skill.llm.default",
        "version": 1,
        "description": "Demo.",
        "binding": {"kind": "instruction-file", "path": "SKILL.md"},
        "allow_all_skills": True,
        "uses_interfaces": [],
        "behavior_sources": [
            {
                "source": "demo-skill.source.missing",
                "version": 1,
                "blueprint": {
                    "base": "skill-root",
                    "path": "references/.missing.md.blueprint.yaml",
                },
                "reason": "Missing on purpose.",
            }
        ],
        "direct_io": {"reads": [], "writes": [], "network": []},
        "owns_filesystem": [],
    }
    root_path = skill / "blueprint.yaml"
    sidecar_path = skill / ".SKILL.md.blueprint.yaml"
    root_path.write_text(yaml.safe_dump(root), encoding="utf-8")
    sidecar_path.write_text(yaml.safe_dump(sidecar), encoding="utf-8")

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="unresolved behavioral source 'demo-skill.source.missing'",
    ):
        migration.convert_blueprint_declarations(
            tmp_path,
            [root_path.relative_to(tmp_path), sidecar_path.relative_to(tmp_path)],
        )


def test_live_map_reviews_skill_drift_shared_dependency_reason_union() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )
    conflicts = map_document["declarations"]["mechanical_conversion"][
        "shared_gateway_merge"
    ]["reviewed_conflicts"]

    assert any(
        conflict.get("module") == "skill-drift"
        and conflict.get("gateway") == "_rtx/_check_drift_state.py"
        and conflict.get("runtime_dependencies") == "set-union"
        for conflict in conflicts
    )


def test_live_map_authors_exact_python_package_support_policy() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert map_document["declarations"]["mechanical_conversion"][
        "python_package_support"
    ] == _EXACT_PYTHON_PACKAGE_SUPPORT_POLICY


def test_converter_rejects_unsupported_python_package_support_policy(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="python_package_support must authorize the exact supported",
    ):
        migration.convert_blueprint_declarations(
            tmp_path,
            [],
            migration_map={
                "declarations": {
                    "mechanical_conversion": {
                        "python_package_support": {
                            "initializer_disposition": "module-owned"
                        }
                    }
                }
            },
        )


def test_live_map_converts_every_declaration_without_collisions() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )
    declarations = map_document["declarations"]
    mapped = [
        Path(path)
        for path in (
            declarations["unversioned"]["paths"]
            + declarations["version_2"]["paths"]
        )
    ]

    conversion = migration.convert_blueprint_declarations(
        repo_root, mapped, migration_map=map_document
    )

    modules = [
        document
        for document in conversion.documents.values()
        if document["node_type"] == "module"
    ]
    assert len(modules) == (
        len(map_document["public_ids"]["llm_ids"]["default_modules"])
        + len(
            map_document["declarations"]["mechanical_conversion"][
                "reference_modules"
            ]
        )
    )
    assert {
        document["id"] for document in modules if "discovery" not in document
    } == {"blueprint", "skill-standards"}
    assert all(document["schema_version"] == 4 for document in conversion.documents.values())
    assert Path("skills/skill-certifier/blueprint.yaml") in conversion.documents
    assert set(conversion.removed_paths) == set(mapped)
    unresolved_skill_interfaces = [
        finding
        for finding in conversion.findings
        if finding.field.startswith("skill_interface.")
    ]
    assert len({finding.source_path for finding in unresolved_skill_interfaces}) == 34
    assert all(finding.code == "NEEDS_CONTEXT" for finding in unresolved_skill_interfaces)
    assert all(
        finding.target_id and finding.claim
        for finding in unresolved_skill_interfaces
    )


def test_converter_excludes_blueprint_and_transient_files_from_module_content(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    (skill / "__pycache__").mkdir(parents=True)
    (skill / ".pytest_cache").mkdir()
    (skill / "blueprints").mkdir()
    (skill / "SKILL.md").write_text("Demo.\n", encoding="utf-8")
    (skill / "__pycache__" / "cached.pyc").write_bytes(b"cache")
    (skill / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    (skill / ".stale.blueprint.yaml").write_text("stale: true\n", encoding="utf-8")
    (skill / "blueprints" / "old.yaml").write_text("old: true\n", encoding="utf-8")
    declaration = skill / "blueprint.yaml"
    declaration.write_text(
        yaml.safe_dump(
            {
                "category": "development-assistant",
                "role": "automation",
                "kind": "tool",
                "interfaces": {
                    "machine": {},
                    "llm": {
                        "default": {
                            "version": 1,
                            "binding": {"kind": "skill_file", "path": "SKILL.md"},
                            "allow_all_skills": True,
                            "behavior_sources": [],
                            "direct_io": {"reads": [], "writes": [], "network": []},
                            "owns_filesystem": [],
                        }
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    conversion = migration.convert_blueprint_declarations(
        tmp_path, [declaration.relative_to(tmp_path)]
    )

    assert conversion.documents[Path("skills/demo-skill/blueprint.yaml")][
        "content"
    ] == ["SKILL\\.md"]


def test_materializer_rejects_an_existing_candidate_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "already-exists"
    output.mkdir()

    with pytest.raises(
        InterfaceInjectionMigrationError, match="candidate output already exists"
    ):
        migration.materialize_blueprint_v4_candidate(
            repo,
            {},
            output_dir=output,
        )


def test_map_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "map.yaml"
    path.write_text("map_version: 1\nmap_version: 2\n", encoding="utf-8")

    with pytest.raises(
        InterfaceInjectionMigrationError, match="duplicate YAML key.*map_version"
    ):
        migration.load_blueprint_migration_map(path)


def test_live_map_field_coverage_and_public_id_inventory_are_exact() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = migration.load_blueprint_migration_map(
        repo_root / "docs/plans/unified-architecture-migration-map.yaml"
    )

    validation = migration.validate_blueprint_migration_map(repo_root, map_document)

    assert validation.schema_file_count == map_document["coverage_contract"][
        "field_enumeration"
    ]["observed_schema_files"]
    assert validation.field_occurrence_count == map_document["coverage_contract"][
        "field_enumeration"
    ]["observed_field_occurrences"]
    assert validation.public_id_count == (
        len(map_document["public_ids"]["machine_ids"]["ids"])
        + len(map_document["public_ids"]["llm_ids"]["default_modules"])
        + len(map_document["public_ids"]["llm_ids"]["named"])
        + len(map_document["public_ids"]["behavior_source_ids"]["ids"])
    )


def test_live_skill_drift_preserves_gateway_parent_helpers_and_package_dependency() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    migration_map = migration.load_blueprint_migration_map(
        repo_root / "docs/plans/unified-architecture-migration-map.yaml"
    )
    validation = migration.validate_blueprint_migration_map(repo_root, migration_map)

    conversion = migration.convert_blueprint_declarations(
        repo_root,
        validation.mapped_declaration_paths,
        migration_map=migration_map,
    )

    source = conversion.documents[
        Path("skills/skill-drift/blueprints/rtx-check-drift-state.yaml")
    ]
    assert r"_rtx/_drift_hashes\.py" in source["content"]
    assert any(
        dependency["source"] == "skill-drift.source.rtx-skill-sources-init"
        for dependency in source["dependencies"]
    )


def test_live_map_assigns_skill_interface_to_audit_gated_retirement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    migration_map = migration.load_blueprint_migration_map(
        repo_root / "docs/plans/unified-architecture-migration-map.yaml"
    )

    groups = migration_map["schema_inventory"]["field_groups"]
    retirement = next(
        group for group in groups if group.get("id") == "legacy-skill-interface-retirement"
    )
    assert retirement["disposition"] == "retire"
    assert retirement["pointer_prefixes"] == ["/properties/skill_interface"]
    assert "certifier-owned reconciliation" in retirement["target"]


def test_candidate_caller_renames_are_exact_and_idempotent(tmp_path: Path) -> None:
    caller = tmp_path / "skills" / "demo-skill" / "_rtx" / "runner.py"
    caller.parent.mkdir(parents=True)
    caller.write_text(
        'from officina.runtime.python_machine_interface import DispatchCall\n'
        'call = DispatchCall(caller_skill="demo-skill", '
        'target_skill="provider-skill", interface="run")\n'
        'note = \'interface="run" provider-skill.machine.run\'\n',
        encoding="utf-8",
    )
    migration_map = {
        "callers": {
            "live_edge_count": 1,
            "live_declarations": [
                {
                    "file": "skills/demo-skill/_rtx/runner.py",
                    "caller": "demo-skill",
                    "old_targets": ["provider-skill.machine.run"],
                    "target": ["provider-skill.interface.run"],
                }
            ],
        }
    }

    first = migration._apply_candidate_caller_renames(tmp_path, migration_map)
    second = migration._apply_candidate_caller_renames(tmp_path, migration_map)

    assert first == (Path("skills/demo-skill/_rtx/runner.py"),)
    assert second == ()
    assert 'interface="provider-skill.interface.run"' in caller.read_text(
        encoding="utf-8"
    )
    assert 'note = \'interface="run" provider-skill.machine.run\'' in (
        caller.read_text(encoding="utf-8")
    )


def test_candidate_conversion_rejects_symlinked_parent(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    outside = tmp_path / "outside"
    candidate.mkdir()
    outside.mkdir()
    (candidate / "skills").symlink_to(outside, target_is_directory=True)
    conversion = migration.BlueprintDeclarationConversion(
        documents={Path("skills/demo/blueprint.yaml"): {"schema_version": 4}},
        removed_paths=(),
        public_graph_projection={},
        runtime_dependency_projection={},
        behavioral_source_dependency_projection={},
    )

    with pytest.raises(
        InterfaceInjectionMigrationError, match="unsafe converted declaration parent"
    ):
        migration._apply_candidate_conversion(candidate, conversion)

    assert not (outside / "demo" / "blueprint.yaml").exists()


def _write_candidate_certifier_api_fixture(root: Path) -> str:
    certifier = root / "skills" / "skill-certifier" / "_rtx" / "_audit_certifier.py"
    certifier.parent.mkdir(parents=True)
    (root / "src" / "officina").mkdir(parents=True)
    (root / "src" / "officina" / "__init__.py").write_text("")
    migration_map = root / "docs/plans/unified-architecture-migration-map.yaml"
    migration_map.parent.mkdir(parents=True)
    migration_map.write_text(
        "declarations:\n"
        "  version_2:\n"
        "    merge_decisions:\n"
        "      - inputs:\n"
        "          - skills/skill-audit/blueprint.yaml\n"
        "        target_module: skill-certifier\n"
    )
    certifier.write_text(
        """from pathlib import Path
import subprocess
from types import SimpleNamespace

def _head(root):
    return subprocess.check_output(
        [\"git\", \"-C\", str(root), \"rev-parse\", \"HEAD\"], text=True
    ).strip()

def inspect_v4_migration_candidate(root):
    return SimpleNamespace(
        node_ids=(\"candidate-node\",), source_commit=_head(root), findings=(
            SimpleNamespace(
                subject_id=\"candidate-node\",
                blueprint_path=Path(root) / \"blueprint.yaml\",
                field=\"description\",
                message=\"candidate-owned finding\",
            ),
        )
    )

def certify_v4_migration_candidate(root, *, reviewed_commit, certified_at):
    mechanical_commit = subprocess.check_output(
        [\"git\", \"-C\", str(root), \"rev-parse\", \"refs/famulus/blueprint-v4-mechanical\"],
        text=True,
    ).strip()
    (Path(root) / \"candidate-certifier-ran\").write_text(mechanical_commit + \"\\n\" + certified_at)
    return SimpleNamespace(node_ids=(\"candidate-node\",), source_commit=reviewed_commit)
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "candidate"], check=True
    )
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "update-ref",
            "refs/famulus/blueprint-v4-mechanical",
            commit,
        ],
        check=True,
    )
    return commit


def test_candidate_source_materialization_excludes_ignored_private_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    (source / "skills/demo").mkdir(parents=True)
    (source / "skills/demo/tracked.txt").write_text("tracked\n")
    (source / "skills/demo/state").mkdir()
    (source / "skills/demo/state/private.txt").write_text("private\n")
    (source / "reviewed-local.txt").write_text("reviewed\n")
    (source / "reviewed-local.txt").chmod(0o775)
    (source / ".gitignore").write_text("skills/demo/state/\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "add",
            ".gitignore",
            "skills/demo/tracked.txt",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    plan = migration.CompiledMigrationPlan(
        module_renames={},
        local_source_includes=(Path("reviewed-local.txt"),),
        authorized_overlay={Path("reviewed-local.txt"): "added"},
    )

    snapshot = migration._copy_candidate_tree(source.resolve(), candidate, plan)
    migration._apply_source_overlay(candidate, snapshot)

    assert (candidate / "skills/demo/tracked.txt").is_file()
    assert (candidate / "reviewed-local.txt").is_file()
    assert (candidate / "reviewed-local.txt").stat().st_mode & 0o777 == 0o775
    assert not (candidate / "skills/demo/state").exists()
    assert Path("skills/demo/state/private.txt") not in snapshot.entries


def test_candidate_source_rejects_undeclared_tracked_change(tmp_path: Path) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    tracked.write_text("after\n")

    with pytest.raises(InterfaceInjectionMigrationError, match="overlay policy"):
        migration._copy_candidate_tree(
            source.resolve(), candidate, migration.CompiledMigrationPlan({}, ())
        )


@pytest.mark.parametrize("staged_state", ["modified", "added", "deleted"])
def test_candidate_source_rejects_every_staged_overlay_state(
    tmp_path: Path, staged_state: str
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    relative = Path("tracked.txt")
    expected = "modified"
    if staged_state == "modified":
        tracked.write_text("after\n")
    elif staged_state == "added":
        relative = Path("added.txt")
        (source / relative).write_text("added\n")
        expected = "added"
    else:
        tracked.unlink()
        expected = "deleted"
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
    plan = migration.CompiledMigrationPlan(
        {}, (), authorized_overlay={relative: expected}
    )

    with pytest.raises(InterfaceInjectionMigrationError, match="rejects staged index state"):
        migration._copy_candidate_tree(source.resolve(), candidate, plan)


def test_candidate_source_accepts_only_unstaged_worktree_deletion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    tracked.unlink()
    plan = migration.CompiledMigrationPlan(
        {}, (), authorized_overlay={Path("tracked.txt"): "deleted"}
    )

    snapshot = migration._copy_candidate_tree(source.resolve(), candidate, plan)
    assert snapshot.entries[Path("tracked.txt")][0] == "deleted"


@pytest.mark.parametrize(
    "target", ["../../outside", "/tmp/outside", "C:/outside", "\\\\?\\C:\\outside"]
)
def test_candidate_source_rejects_escaping_overlay_symlink(
    tmp_path: Path, target: str
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    (source / ".gitignore").write_text("# tracked\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    link = source / "overlay-link"
    link.symlink_to(target)
    plan = migration.CompiledMigrationPlan(
        {}, (Path("overlay-link"),),
        authorized_overlay={Path("overlay-link"): "added"},
    )

    with pytest.raises(InterfaceInjectionMigrationError, match="escaping symlink"):
        migration._copy_candidate_tree(source.resolve(), candidate, plan)


def test_candidate_source_rejects_ignored_authorized_include(tmp_path: Path) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    (source / ".gitignore").write_text("private.txt\n")
    (source / "private.txt").write_text("secret\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    plan = migration.CompiledMigrationPlan(
        {},
        (Path("private.txt"),),
        authorized_overlay={Path("private.txt"): "added"},
    )

    with pytest.raises(InterfaceInjectionMigrationError, match="ignored"):
        migration._copy_candidate_tree(source.resolve(), candidate, plan)

def test_candidate_source_snapshot_detects_post_capture_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    plan = migration.CompiledMigrationPlan({}, ())
    snapshot = migration._copy_candidate_tree(source.resolve(), candidate, plan)
    tracked.write_text("after\n")

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="source (?:input|Git/index state) changed during materialization",
    ):
        migration._verify_source_materialization_snapshot(source.resolve(), snapshot)


def test_cutover_manifest_rejects_paths_without_map_derived_authority(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "skills/old").mkdir(parents=True)
    (repo / "skills/old/SKILL.md").write_text("old\n")
    (repo / "approved.py").write_text("before\n")
    (repo / "unreviewed.py").write_text("before\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "skills/old").rename(repo / "skills/new")
    (repo / "approved.py").write_text("after\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "final"], check=True)
    final = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    plan = migration.CompiledMigrationPlan(
        {"old": "new"},
        (),
    )
    migration._assert_cutover_manifest_authorized(
        (
            migration.CutoverChange("M", Path("approved.py")),
            migration.CutoverChange("D", Path("skills/old/SKILL.md")),
            migration.CutoverChange("A", Path("skills/new/SKILL.md")),
        ),
        candidate_root=repo,
        source_overlay_commit=base,
        final_commit=final,
        exact_paths=(Path("approved.py"),),
        migration_plan=plan,
    )

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="exact map-authorized operations.*unreviewed.py",
    ):
        migration._assert_cutover_manifest_authorized(
            (migration.CutoverChange("M", Path("unreviewed.py")),),
            candidate_root=repo,
            source_overlay_commit=base,
            final_commit=final,
            exact_paths=(Path("approved.py"),),
            migration_plan=plan,
        )


def test_candidate_inspection_executes_candidate_local_certifier(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = _write_candidate_certifier_api_fixture(candidate)

    inspection = migration.inspect_candidate_v4(candidate)

    assert inspection["source_commit"] == commit
    assert inspection["node_ids"] == ["candidate-node"]
    assert inspection["findings"] == [
        {
            "subject_id": "candidate-node",
            "blueprint_path": "blueprint.yaml",
            "field": "description",
            "message": "candidate-owned finding",
        }
    ]


def test_candidate_finalization_requires_exact_reviewed_head(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = _write_candidate_certifier_api_fixture(candidate)

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="reviewed commit does not match candidate HEAD",
    ):
        migration.finalize_candidate_v4(
            candidate,
            reviewed_commit="0" * len(commit),
            certified_at="2026-07-22T00:00:00+00:00",
        )

    assert not (candidate / "candidate-certifier-ran").exists()


def test_candidate_inspection_rejects_dirty_execution_bytes(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    _write_candidate_certifier_api_fixture(candidate)
    certifier = candidate / "skills/skill-certifier/_rtx/_audit_certifier.py"
    certifier.write_text(certifier.read_text() + "\nDIRTY = True\n")

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="worktree and index must exactly match HEAD",
    ):
        migration.inspect_candidate_v4(candidate)


def test_candidate_inspection_and_finalization_reject_non_atomic_provenance(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = _write_candidate_certifier_api_fixture(candidate)
    subprocess.run(
        [
            "git", "-C", str(candidate), "config",
            "famulus.candidateAtomicGuarantee", "false",
        ],
        check=True,
    )

    with pytest.raises(InterfaceInjectionMigrationError, match="non-certifiable"):
        migration.inspect_candidate_v4(candidate)
    with pytest.raises(InterfaceInjectionMigrationError, match="non-certifiable"):
        migration.finalize_candidate_v4(
            candidate,
            reviewed_commit=commit,
            certified_at="2026-07-22T00:00:00+00:00",
        )
    assert not (candidate / "candidate-certifier-ran").exists()


def test_candidate_finalization_executes_candidate_local_certifier(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = _write_candidate_certifier_api_fixture(candidate)

    result = migration.finalize_candidate_v4(
        candidate,
        reviewed_commit=commit,
        certified_at="2026-07-22T00:00:00+00:00",
    )

    assert result == {"node_ids": ["candidate-node"], "source_commit": commit}
    assert not (candidate / "candidate-certifier-ran").exists()


def test_candidate_certifier_rejects_symlinked_owner_path(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    commit = _write_candidate_certifier_api_fixture(candidate)
    outside = tmp_path / "outside.py"
    certifier = candidate / "skills/skill-certifier/_rtx/_audit_certifier.py"
    outside.write_bytes(certifier.read_bytes())
    certifier.unlink()
    certifier.symlink_to(outside)

    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="worktree and index must exactly match HEAD|unsafe candidate execution",
    ):
        migration.finalize_candidate_v4(
            candidate,
            reviewed_commit=commit,
            certified_at="2026-07-22T00:00:00+00:00",
        )


def test_python_import_resolution_uses_one_exact_search_anchor(tmp_path: Path) -> None:
    module_root = tmp_path / "skills" / "demo"
    (module_root / "_rtx").mkdir(parents=True)
    (module_root / "_rtx" / "__init__.py").write_text("")
    (module_root / "_rtx" / "runner.py").write_text("import helper\n")
    (module_root / "_rtx" / "helper.py").write_text("NESTED = 1\n")
    (module_root / "helper.py").write_text("ROOT = 1\n")
    documents: dict[Path, dict[str, object]] = {
        Path("skills/demo/blueprint.yaml"): {
            "schema_version": 4,
            "node_type": "module",
            "id": "demo",
            "sources": {
                "demo.source.runner": {"blueprint": {"base": "module-root", "path": "blueprints/runner.yaml"}},
                "demo.source.nested-helper": {"blueprint": {"base": "module-root", "path": "blueprints/nested-helper.yaml"}},
                "demo.source.root-helper": {"blueprint": {"base": "module-root", "path": "blueprints/root-helper.yaml"}},
            },
        },
    }
    for source_id, blueprint, gateway in (
        ("demo.source.runner", "runner.yaml", "_rtx/runner.py"),
        ("demo.source.nested-helper", "nested-helper.yaml", "_rtx/helper.py"),
        ("demo.source.root-helper", "root-helper.yaml", "helper.py"),
    ):
        documents[Path("skills/demo/blueprints") / blueprint] = {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": 1,
            "gateway": {"path": gateway, "language": "Python"},
            "content": [gateway.replace(".", r"\.")],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": {},
        }
    projection: dict[str, object] = {}

    migration._add_python_package_support_sources(
        tmp_path,
        documents,
        projection,
        set(),
        import_search_roots={"default": ["module-root"], "by_gateway": {}},
        allowed_source_paths=None,
    )

    targets = {
        dependency["source"]
        for dependency in documents[Path("skills/demo/blueprints/runner.yaml")]["dependencies"]
    }
    assert "demo.source.nested-helper" not in targets
    assert "demo.source.root-helper" in targets

    (module_root / "_rtx" / "runner.py").write_text(
        "import importlib\nname = 'helper'\nimportlib.import_module(name)\n"
    )
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="dynamic Python import has no literal module name",
    ):
        migration._add_python_package_support_sources(
            tmp_path,
            documents,
            projection,
            set(),
            import_search_roots={"default": ["module-root"], "by_gateway": {}},
            allowed_source_paths=None,
        )
