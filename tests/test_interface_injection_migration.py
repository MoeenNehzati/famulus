from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml

import officina.common.interface_injection_migration as migration
from officina.common.blueprint_graph import (
    BlueprintEdge,
    BlueprintNode,
    InterfaceExport,
    RepositoryBlueprintGraph,
)
from officina.common.blueprint_template import load_schema, schema_validator
from officina.common.interface_injection_migration import (
    InterfaceInjectionMigrationError,
    build_interface_injection_migration_report,
)
from test_support.git_repository import GitTestRepository


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
            "skills/install-assistant-tools/_rtx/_agent_launchers.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/install-assistant-tools/_rtx/_install_scaffold.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/install-assistant-tools/_rtx/_phase_entry.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_healthcheck_probe.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_job_control.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_setup_runner.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_unit_writer.py": [
                "gateway-parent",
                "module-root",
            ],
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
        module_root=root,
        blueprint_path=root / "blueprint.yaml",
        gateway_path=root / "SKILL.md",
        declaration={},
    )
    module = BlueprintNode(
        node_id="provider-skill.machine-module.worker",
        node_type="machine-module",
        version=1,
        module_root=Path("/repo/skills/provider-skill"),
        blueprint_path=Path("/repo/skills/provider-skill/_rtx/._worker.py.blueprint.yaml"),
        gateway_path=Path("/repo/skills/provider-skill/_rtx/_worker.py"),
        declaration={},
    )
    export = InterfaceExport(
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
        exports={export.interface_id: export},
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


def test_unversioned_converter_rejects_version_pinned_interface_id_shorthand() -> None:
    declaration = {
        "interfaces": {
            "machine": {
                "run": {
                    "version": 1,
                    "uses_interfaces": ["provider-skill.machine.read@2"],
                    "direct_io": {"reads": [], "writes": [], "network": []},
                    "invocation": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_runner.py:Interface",
                        "behavior_sources": [],
                    },
                }
            },
            "llm": {},
        }
    }

    with pytest.raises(
        InterfaceInjectionMigrationError, match="invalid uses_interfaces"
    ):
        migration._unversioned_interfaces("demo-skill", declaration)


def test_converter_drops_only_exact_map_reviewed_generated_uses_overlay() -> None:
    path = Path("skills/daily-plan/blueprint.yaml")
    declaration = {
        "interfaces": {
            "machine": {
                "orchestrate": {
                    "uses_interfaces": ["provider-skill.machine.read@1"]
                }
            }
        }
    }
    migration_map = {
        "declarations": {
            "mechanical_conversion": {
                "reviewed_generated_field_ignores": [
                    {
                        "path": path.as_posix(),
                        "field": "uses_interfaces",
                        "interface_ids": ["daily-plan.machine.orchestrate"],
                        "exact_value": ["provider-skill.machine.read@1"],
                        "disposition": "ignore",
                    }
                ]
            }
        }
    }

    cleaned = migration._apply_reviewed_generated_field_ignores(
        path, declaration, migration_map
    )

    assert cleaned["interfaces"]["machine"]["orchestrate"]["uses_interfaces"] == []
    assert declaration["interfaces"]["machine"]["orchestrate"]["uses_interfaces"] == [
        "provider-skill.machine.read@1"
    ]
    v4_module = {"schema_version": 4, "node_type": "module"}
    assert migration._apply_reviewed_generated_field_ignores(
        path, v4_module, migration_map
    ) == v4_module
    migration_map["declarations"]["mechanical_conversion"][
        "reviewed_generated_field_ignores"
    ][0]["exact_value"] = ["different.machine.edge@1"]
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="reviewed generated-field ignore does not match",
    ):
        migration._apply_reviewed_generated_field_ignores(
            path, declaration, migration_map
        )
    malformed = deepcopy(migration_map)
    malformed["declarations"]["mechanical_conversion"][
        "reviewed_generated_field_ignores"
    ][0]["path"] = "skills/other-skill/blueprint.yaml"
    malformed["declarations"]["mechanical_conversion"][
        "reviewed_generated_field_ignores"
    ][0]["exact_value"] = []
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="invalid reviewed generated-field ignore",
    ):
        migration._apply_reviewed_generated_field_ignores(
            path, declaration, malformed
        )
    wrong_owner = deepcopy(migration_map)
    wrong_owner["declarations"]["mechanical_conversion"][
        "reviewed_generated_field_ignores"
    ][0]["interface_ids"] = ["other-skill.machine.orchestrate"]
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="does not belong to daily-plan",
    ):
        migration._apply_reviewed_generated_field_ignores(
            path, declaration, wrong_owner
        )
    with pytest.raises(
        InterfaceInjectionMigrationError,
        match="reviewed generated-field ignore was not consumed exactly once",
    ):
        migration._validate_reviewed_generated_field_ignore_consumption(
            [Path("skills/other-skill/blueprint.yaml")], migration_map
        )
    migration._validate_reviewed_generated_field_ignore_consumption(
        [path], migration_map
    )


def test_live_repository_uses_parent_and_code_child_cutover() -> None:
    root = Path(__file__).resolve().parents[1]
    module = yaml.safe_load(
        (root / "skills" / "daily-plan" / "blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    daily_init = yaml.safe_load(
        (
            root
            / "skills"
            / "daily-plan"
            / "_rtx"
            / "blueprints"
            / "rtx-init.yaml"
        ).read_text(encoding="utf-8")
    )

    expected_uses = [
        {"interface": "cloud-files.interface.lists-read", "version": 1},
        {"interface": "cloud-files.interface.lists-write", "version": 1},
        {"interface": "cloud-files.interface.plans-read", "version": 1},
        {"interface": "cloud-files.interface.plans-write", "version": 1},
        {"interface": "common.interface.dates", "version": 1},
        {"interface": "g-calendar.interface.scripts-gcal", "version": 1},
        {"interface": "get-weather.interface.scripts-weather", "version": 1},
        {"interface": "list-manager.interface.read-beautify", "version": 1},
        {"interface": "list-manager.interface.update-list", "version": 1},
    ]
    assert (module["schema_version"], module["node_type"]) == (5, "module")
    assert daily_init["uses_interfaces"] == expected_uses
    for source_name in ("rtx-plan-orchestrate", "rtx-state-patch"):
        source = yaml.safe_load(
            (
                root
                / "skills"
                / "daily-plan"
                / "_rtx"
                / "blueprints"
                / f"{source_name}.yaml"
            ).read_text(encoding="utf-8")
        )
        assert source["uses_interfaces"] == expected_uses
    assert not (root / "skills" / "skill-audit" / "SKILL.md").exists()
    assert not (
        root / "skills" / "skill-audit" / "_rtx" / "_audit_certifier.py"
    ).exists()
    assert (root / "skills" / "skill-certifier" / "SKILL.md").is_file()


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
    assert conversion.predecessor_semantic_edge_projection[
        "demo-skill.source.rtx-runner"
    ]["uses_interfaces"] == source["uses_interfaces"]


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
    (skill / "references" / "base.md").write_text(
        "Base policy.\n", encoding="utf-8"
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
            "uses_behavior_sources": [
                {
                    "source": "demo-skill.source.base",
                    "version": 5,
                    "blueprint": {
                        "base": "skill-root",
                        "path": "references/.base.md.blueprint.yaml",
                    },
                    "reason": "Supplies the base policy.",
                }
            ],
        },
        "references/.base.md.blueprint.yaml": {
            "schema_version": 2,
            "blueprint_type": "behavior-source",
            "id": "demo-skill.source.base",
            "version": 5,
            "description": "Defines the base policy.",
            "binding": {"kind": "file", "path": "references/base.md"},
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
    base_policy = conversion.documents[
        Path("skills/demo-skill/blueprints/references-base.yaml")
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
    assert policy["dependencies"] == [
        {
            "source": "demo-skill.source.base",
            "version": 5,
            "blueprint": {
                "base": "module-root",
                "path": "blueprints/references-base.yaml",
            },
            "reason": "Supplies the base policy.",
        }
    ]
    assert base_policy["id"] == "demo-skill.source.base"
    assert conversion.predecessor_public_graph_projection == {
        "exports": {
            "demo-skill.interface.default": {
                "version": 1,
                "source_interface": (
                    "demo-skill.source.gateway.interface.default"
                ),
                "access": {
                    "allow_all_modules": True,
                    "allowed_callers": [],
                },
            },
            "demo-skill.interface.first": {
                "version": 2,
                "source_interface": (
                    "demo-skill.source.rtx-runner.interface.first"
                ),
                "access": {
                    "allow_all_modules": False,
                    "allowed_callers": [],
                },
            },
            "demo-skill.interface.second": {
                "version": 3,
                "source_interface": (
                    "demo-skill.source.rtx-runner.interface.second"
                ),
                "access": {
                    "allow_all_modules": True,
                    "allowed_callers": [],
                },
            },
        }
    }
    assert conversion.predecessor_runtime_dependency_projection == {
        "demo-skill.source.rtx-runner": {
            "platform_support": {
                "linux": True,
                "macos": True,
                "windows": True,
            },
            "runtime_dependencies": [],
        }
    }
    assert conversion.predecessor_semantic_edge_projection == {
        "demo-skill.source.gateway": {
            "dependencies": [
                {
                    "source": "demo-skill.source.policy",
                    "version": 4,
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/references-policy.yaml",
                    },
                    "reason": "Supplies policy.",
                }
            ],
            "uses_interfaces": [],
            "content": [r"SKILL\.md"],
        },
        "demo-skill.source.policy": {
            "dependencies": [
                {
                    "source": "demo-skill.source.base",
                    "version": 5,
                    "blueprint": {
                        "base": "module-root",
                        "path": "blueprints/references-base.yaml",
                    },
                    "reason": "Supplies the base policy.",
                }
            ],
            "uses_interfaces": [],
            "content": [r"references/policy\.md"],
        },
        "demo-skill.source.base": {
            "dependencies": [],
            "uses_interfaces": [],
            "content": [r"references/base\.md"],
        },
        "demo-skill.source.rtx-runner": {
            "dependencies": [],
            "uses_interfaces": [],
            "content": [r"_rtx/_runner\.py"],
        },
    }


def _supplemental_module_map() -> dict[str, object]:
    return {
        "declarations": {
            "mechanical_conversion": {
                "python_package_support": dict(
                    _EXACT_PYTHON_PACKAGE_SUPPORT_POLICY
                ),
                "supplemental_modules": [
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


def _write_supplemental_module_conversion_fixture(tmp_path: Path) -> tuple[Path, str]:
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


def test_converter_materializes_map_declared_supplemental_modules(tmp_path: Path) -> None:
    declaration, _ = _write_supplemental_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_supplemental_module_map(),
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
    declaration, policy_text = _write_supplemental_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_supplemental_module_map(),
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
    declaration, _ = _write_supplemental_module_conversion_fixture(tmp_path)
    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_supplemental_module_map(),
    )
    expected = deepcopy(conversion.behavioral_source_dependency_projection)

    consumer = conversion.documents[
        Path("skills/consumer-skill/blueprints/gateway.yaml")
    ]
    consumer["dependencies"][0]["reason"] = "mutated emitted v4"

    assert conversion.behavioral_source_dependency_projection == expected


def test_unexported_supplemental_modules_add_no_public_or_runtime_exports(tmp_path: Path) -> None:
    declaration, _ = _write_supplemental_module_conversion_fixture(tmp_path)

    conversion = migration.convert_blueprint_declarations(
        tmp_path,
        [declaration.relative_to(tmp_path)],
        migration_map=_supplemental_module_map(),
    )

    assert Path("references/standards/blueprint.yaml") in conversion.documents
    assert set(conversion.public_graph_projection["exports"]) == {
        "consumer-skill.interface.default"
    }
    assert not any(
        source_id.startswith("standards.")
        for source_id in conversion.runtime_dependency_projection
    )


def test_cutover_has_no_stale_list_manager_sidecar() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )

    non_live = map_document["declarations"]["non_live_local_artifacts"]
    assert non_live == []
    assert not (
        repo_root / "skills/list-manager/.SKILL.md.blueprint.yaml"
    ).exists()


def test_excluded_list_manager_sidecar_emits_no_fake_preference_source_or_io() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    module = yaml.safe_load(
        (repo_root / "skills/list-manager/blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    gateway = yaml.safe_load(
        (
            repo_root / "skills/list-manager/blueprints/gateway.yaml"
        ).read_text(encoding="utf-8")
    )

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


def test_live_map_owns_cryptography_on_certificate_records_source() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )
    conflicts = map_document["declarations"]["mechanical_conversion"][
        "shared_gateway_merge"
    ]["reviewed_conflicts"]

    assert not any(
        conflict.get("module") == "skill-drift" for conflict in conflicts
    )
    common_module = next(
        module
        for module in map_document["declarations"]["mechanical_conversion"][
            "supplemental_modules"
        ]
        if module["id"] == "common"
    )
    certificate_records = next(
        source
        for source in common_module["sources"]
        if source["id"] == "common.source.certificate-records"
    )
    assert {
        dependency["name"]
        for dependency in certificate_records["runtime_dependencies"]
        if dependency["kind"] == "python-package"
    } == {"cryptography"}


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


def _assert_pre_nested_parent_cutover_without_collisions() -> None:
    """Retain the reviewed v4 parent-cutover assertions as historical evidence."""
    repo_root = Path(__file__).resolve().parents[1]
    map_document = yaml.safe_load(
        (repo_root / "docs/plans/unified-architecture-migration-map.yaml").read_text(
            encoding="utf-8"
        )
    )
    live_paths = []
    for pattern in (
        "skills/*/blueprint.yaml",
        "skills/*/blueprints/*.yaml",
        "src/officina/common/blueprint.yaml",
        "src/officina/common/blueprints/*.yaml",
        "references/blueprint/blueprint.yaml",
        "references/blueprint/blueprints/*.yaml",
        "references/skill-standards/blueprint.yaml",
        "references/skill-standards/blueprints/*.yaml",
    ):
        live_paths.extend(repo_root.glob(pattern))
    documents = {
        path.relative_to(repo_root): yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in live_paths
    }

    modules = [
        document
        for document in documents.values()
        if document["node_type"] == "module"
    ]
    assert len(modules) == (
        len(map_document["public_ids"]["llm_ids"]["default_modules"])
        + len(
            map_document["declarations"]["mechanical_conversion"][
                "supplemental_modules"
            ]
        )
    )
    assert {
        document["id"] for document in modules if "discovery" not in document
    } == {"blueprint", "common", "skill-standards"}
    common = documents[Path("src/officina/common/blueprint.yaml")]
    assert set(common["exports"]) == {
        "common.interface.certification-hashing",
        "common.interface.atomic-files",
        "common.interface.certificate-records",
        "common.interface.blueprint-graph",
        "common.interface.blueprint-template",
        "common.interface.certification-view",
        "common.interface.codex-toml",
        "common.interface.dates",
        "common.interface.famulus-paths",
        "common.interface.git-provenance",
        "common.interface.google-credentials",
        "common.interface.oauth-json",
        "common.interface.pooled-blueprint",
        "common.interface.repository-paths",
        "common.interface.secret-store",
        "common.interface.toml-io",
    }
    expected_common_dependencies = {
        "certification-hashing": {
            "atomic-files",
            "blueprint-graph",
            "git-provenance",
            "repository-paths",
        },
        "certificate-records": {"atomic-files", "secret-store"},
        "blueprint-graph": {"blueprint-inventory", "repository-paths"},
        "blueprint-inventory": {"atomic-files", "git-provenance"},
        "blueprint-template": set(),
        "certification-view": {
            "certification-hashing",
            "atomic-files",
            "certificate-records",
            "blueprint-graph",
            "blueprint-template",
            "git-provenance",
            "repository-paths",
        },
        "git-provenance": {"atomic-files", "repository-paths"},
        "pooled-blueprint": {
            "blueprint-graph",
            "certification-view",
        },
        "process-binding-compiler": {"blueprint-graph"},
        "nested-module-migration": {
            "atomic-files",
            "blueprint-graph",
            "blueprint-inventory",
            "certificate-records",
            "certification-hashing",
            "git-provenance",
        },
    }
    expected_common_sources = set(expected_common_dependencies) | {
        "atomic-files",
        "codex-toml",
        "dates",
        "famulus-paths",
        "google-credentials",
        "oauth-json",
        "repository-paths",
        "secret-store",
        "toml-io",
    }
    assert {
        source_id.removeprefix("common.source.")
        for source_id in common["sources"]
    } == expected_common_sources
    for source_name, expected_dependencies in expected_common_dependencies.items():
        source = documents[
            Path(f"src/officina/common/blueprints/{source_name}.yaml")
        ]
        assert {
            dependency["source"].removeprefix("common.source.")
            for dependency in source["dependencies"]
        } == expected_dependencies
    for private_source in ("blueprint-inventory", "process-binding-compiler"):
        assert documents[
            Path(f"src/officina/common/blueprints/{private_source}.yaml")
        ]["interfaces"] == {}
    expected_export_sources = {
        interface_id: (
            f"common.source.{interface_id.removeprefix('common.interface.')}"
            ".interface.python-api"
        )
        for interface_id in common["exports"]
    }
    assert {
        interface_id: export["source_interface"]
        for interface_id, export in common["exports"].items()
    } == expected_export_sources
    assert common["exports"]["common.interface.blueprint-template"]["access"] == {
        "allow_all_modules": False,
        "allowed_callers": [
            "regenerate-blueprints",
            "regenerate-blueprints-rtx",
        ],
    }
    assert common["exports"]["common.interface.blueprint-graph"]["access"] == {
        "allow_all_modules": False,
        "allowed_callers": ["skill-maker", "skill-certifier", "skill-drift"],
    }
    for interface_id in (
        "common.interface.certification-hashing",
        "common.interface.certification-view",
        "common.interface.git-provenance",
        "common.interface.pooled-blueprint",
    ):
        assert common["exports"][interface_id]["access"] == {
            "allow_all_modules": False,
            "allowed_callers": ["skill-certifier", "skill-drift"],
        }
    assert common["exports"]["common.interface.certificate-records"]["access"] == {
        "allow_all_modules": False,
        "allowed_callers": [
            "install-assistant-tools",
            "skill-certifier",
            "skill-drift",
        ],
    }
    common_oauth = documents[
        Path("src/officina/common/blueprints/oauth-json.yaml")
    ]
    assert common_oauth["dependencies"] == []
    assert common_oauth["uses_interfaces"] == [
        {
            "interface": "common.source.atomic-files.interface.python-api",
            "version": 1,
        }
    ]
    connect_google = documents[
        Path("skills/connect-google/blueprints/rtx-client-config.yaml")
    ]
    assert {tuple(sorted(use.items())) for use in connect_google["uses_interfaces"]} >= {
        tuple(sorted({"interface": "common.interface.google-credentials", "version": 1}.items()))
    }
    reviewed_common_uses = {
        "regenerate-blueprints": {
            "common.interface.blueprint-template",
        },
        "skill-maker": {
            "common.interface.atomic-files",
            "common.interface.blueprint-graph",
        },
        "skill-certifier": {
            "common.interface.certification-hashing",
            "common.interface.atomic-files",
            "common.interface.certificate-records",
            "common.interface.blueprint-graph",
            "common.interface.certification-view",
                "common.interface.pooled-blueprint",
                "common.interface.git-provenance",
                "common.interface.repository-paths",
            },
        "skill-drift": {
            "common.interface.certification-hashing",
            "common.interface.certificate-records",
            "common.interface.blueprint-graph",
            "common.interface.certification-view",
        },
    }
    reviewed_source_paths = {
        "regenerate-blueprints": Path(
            "skills/regenerate-blueprints/blueprints/rtx-blueprint-regenerator.yaml"
        ),
        "skill-maker": Path(
            "skills/skill-maker/blueprints/rtx-blueprint-syncer.yaml"
        ),
        "skill-certifier": Path(
            "skills/skill-certifier/blueprints/rtx-certifier.yaml"
        ),
        "skill-drift": Path(
            "skills/skill-drift/blueprints/rtx-check-drift-state.yaml"
        ),
    }
    for consumer, expected_uses in reviewed_common_uses.items():
        source = documents[reviewed_source_paths[consumer]]
        assert {
            use["interface"]
            for use in source["uses_interfaces"]
            if use["interface"].startswith("common.interface.")
        } == expected_uses
    daily_plan_uses = sorted(
        (
            {"interface": interface, "version": 1}
            for interface in (
            "cloud-files.interface.plans-read",
            "cloud-files.interface.plans-write",
            "cloud-files.interface.lists-read",
            "cloud-files.interface.lists-write",
            "g-calendar.interface.scripts-gcal",
            "get-weather.interface.scripts-weather",
            "list-manager.interface.read-beautify",
            "list-manager.interface.update-list",
            )
        ),
        key=lambda item: item["interface"],
    )
    daily_init = documents[
        Path("skills/daily-plan/blueprints/rtx-init.yaml")
    ]
    assert r"_rtx/_day_model\.py" in daily_init["content"]
    assert [
        use
        for use in daily_init["uses_interfaces"]
        if not use["interface"].startswith("common.")
    ] == daily_plan_uses
    assert {"interface": "common.interface.dates", "version": 1} in daily_init[
        "uses_interfaces"
    ]
    for source_name in (
        "rtx-plan-orchestrate",
        "rtx-state-patch",
        "rtx-plan-storage",
        "rtx-render-plan",
    ):
        assert documents[
            Path(f"skills/daily-plan/blueprints/{source_name}.yaml")
        ]["uses_interfaces"] == []
    daily_render = documents[
        Path("skills/daily-plan/blueprints/rtx-render-plan.yaml")
    ]
    assert daily_render["interfaces"][
        "daily-plan.source.rtx-render-plan.interface.render-plan"
    ]["process_binding"]["patterns"] == [
        {
            "min_positionals": 3,
            "max_positionals": 3,
            "allow_stdin": False,
            "positional_patterns": {"0": "^(extract|reassemble)$"},
        }
    ]
    list_init = documents[
        Path("skills/list-manager/blueprints/rtx-init.yaml")
    ]
    assert r"_rtx/_cloud_transport\.py" in list_init["content"]
    assert list_init["uses_interfaces"] == [
        {"interface": "cloud-files.interface.lists-read", "version": 1},
        {"interface": "cloud-files.interface.lists-write", "version": 1},
    ]
    for source_name in ("rtx-category-cache", "rtx-render-bridge", "rtx-yaml-store"):
        assert documents[
            Path(f"skills/list-manager/blueprints/{source_name}.yaml")
        ]["uses_interfaces"] == []

    def dependency_targets(path: str) -> set[str]:
        return {
            dependency["source"]
            for dependency in documents[Path(path)]["dependencies"]
        }

    exact_local_dependencies = {
        "skills/recurring-tasks/blueprints/rtx-healthcheck-probe.yaml": {
            "recurring-tasks.source.rtx-schedule-backend-init",
        },
        "skills/recurring-tasks/blueprints/rtx-job-control.yaml": {
            "recurring-tasks.source.rtx-schedule-backend-init",
        },
        "skills/recurring-tasks/blueprints/rtx-unit-writer.yaml": {
            "recurring-tasks.source.rtx-schedule-backend-init",
        },
        "skills/recurring-tasks/blueprints/rtx-setup-runner.yaml": {
            "recurring-tasks.source.rtx-ensure-agent-env",
            "recurring-tasks.source.rtx-healthcheck-probe",
            "recurring-tasks.source.rtx-schedule-backend-init",
            "recurring-tasks.source.rtx-unit-writer",
        },
        "skills/install-assistant-tools/blueprints/rtx-agent-launchers.yaml": {
            "install-assistant-tools.source.rtx-install-launcher-init",
        },
        "skills/install-assistant-tools/blueprints/rtx-install-scaffold.yaml": {
            "install-assistant-tools.source.rtx-install-launcher-init",
        },
        "skills/install-assistant-tools/blueprints/rtx-phase-entry.yaml": {
            "install-assistant-tools.source.rtx-agent-launchers",
            "install-assistant-tools.source.rtx-config-bridge",
            "install-assistant-tools.source.rtx-install-scaffold",
        },
        "skills/g-calendar/blueprints/rtx-ensure-oauth.yaml": {
            "g-calendar.source.rtx-oauth-bootstrap",
        },
        "skills/cloud-files/blueprints/rtx-ensure-oauth.yaml": {
            "cloud-files.source.rtx-oauth-bootstrap",
        },
        "skills/email-client/blueprints/rtx-imap-gateway.yaml": {
            "email-client.source.rtx-email-accounts",
        },
        "skills/list-manager/blueprints/rtx-render-bridge.yaml": {
            "list-manager.source.rtx-list-beautify",
            "list-manager.source.rtx-yaml-store",
        },
    }
    for path, targets in exact_local_dependencies.items():
        assert dependency_targets(path) >= targets

    source_content = {
        path: set(documents[Path(path)]["content"])
        for path in (
            "skills/hook-maker/blueprints/gateway.yaml",
            "skills/initialize-tdd/blueprints/gateway.yaml",
            "skills/initialize-tdd/blueprints/rtx-host-links.yaml",
            "skills/list-manager/blueprints/rtx-yaml-store.yaml",
            "skills/skill-drift/blueprints/rtx-check-drift-state.yaml",
            "skills/install-assistant-tools/blueprints/rtx-agent-launchers.yaml",
            "skills/technical-flow-review/blueprints/gateway.yaml",
            "skills/skill-maker/blueprints/rtx-blueprint-syncer.yaml",
            "skills/bib-audit/blueprints/gateway.yaml",
        )
    }
    assert r"references/cross\-host\-hook\-scaffold\.md" in source_content[
        "skills/hook-maker/blueprints/gateway.yaml"
    ]
    assert {
        r"assets/common/AGENTS\.md",
        r"assets/common/README\.md",
        r"assets/python/pyproject\.toml",
        r"assets/python/src/project/logger\.py",
    } <= source_content["skills/initialize-tdd/blueprints/gateway.yaml"]
    assert r"_rtx/_claude_compat_symlink\.py" in source_content[
        "skills/initialize-tdd/blueprints/rtx-host-links.yaml"
    ]
    assert {
        r"schemas/lists/default\.json",
        r"schemas/lists/task\-list\.json",
        r"schemas/lists/task\-list\-personal\.json",
        r"schemas/lists/todo\.json",
        r"schemas/lists/triage\.json",
        r"schemas/types/action\.json",
        r"schemas/types/entry\.json",
        r"schemas/types/task_entry\.json",
        r"schemas/types/triage_action\.json",
    } <= source_content["skills/list-manager/blueprints/rtx-yaml-store.yaml"]
    assert r"references/certification\-basis\-roots\.json" not in source_content[
        "skills/skill-drift/blueprints/rtx-check-drift-state.yaml"
    ]
    assert {
        r"bin/_agent_launch\.py",
        "bin/assistant",
        r"bin/assistant\.bat",
        "bin/collab",
        r"bin/collab\.bat",
        "bin/coauthor",
        r"bin/coauthor\.bat",
        r"bin/tmux\-workspace",
    } <= source_content[
        "skills/install-assistant-tools/blueprints/rtx-agent-launchers.yaml"
    ]
    assert {
        r"references/audience\-familiarity\.md",
        r"references/document\-types/journal\-article/general\.md",
        r"references/document\-types/research\-presentation/math\.md",
    } <= source_content["skills/technical-flow-review/blueprints/gateway.yaml"]
    assert r"tests/test_blueprint_tools\.py" in source_content[
        "skills/skill-maker/blueprints/rtx-blueprint-syncer.yaml"
    ]
    assert {
        r"test/test_biblatex\.bib",
        r"test/test_modification\.bib",
        r"test/test_modification\.tex",
        r"test/test_multifile_main\.tex",
        r"test/test_multifile_section\.tex",
        r"test/test_natbib\.bib",
        r"test/test_natbib_commands\.tex",
    } <= source_content["skills/bib-audit/blueprints/gateway.yaml"]

    jobs_config = documents[
        Path("skills/recurring-tasks/blueprints/jobs-config.yaml")
    ]
    assert jobs_config["content"] == [r"jobs\.yaml"]
    assert r"jobs\.yaml" in documents[
        Path("skills/recurring-tasks/blueprint.yaml")
    ]["content"]
    for source_name in (
        "rtx-healthcheck-probe",
        "rtx-init",
        "rtx-job-control",
        "rtx-job-executor",
        "rtx-unit-writer",
    ):
        assert "recurring-tasks.source.jobs-config" in dependency_targets(
            f"skills/recurring-tasks/blueprints/{source_name}.yaml"
        )
    job_executor = documents[
        Path("skills/recurring-tasks/blueprints/rtx-job-executor.yaml")
    ]
    assert job_executor["content"] == [r"_rtx/_job_executor\.py"]
    assert job_executor["platform_support"] == {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    assert {
        dependency["name"] for dependency in job_executor["runtime_dependencies"]
    } == {"PyYAML"}
    private_executor = (
        "recurring-tasks.source.rtx-job-executor.interface.execute-job"
    )
    assert private_executor in job_executor["interfaces"]
    assert all(
        export["source_interface"] != private_executor
        for export in documents[
            Path("skills/recurring-tasks/blueprint.yaml")
        ]["exports"].values()
    )
    schedule_backend = documents[
        Path("skills/recurring-tasks/blueprints/rtx-schedule-backend-init.yaml")
    ]
    assert "recurring-tasks.source.rtx-job-executor" in {
        dependency["source"] for dependency in schedule_backend["dependencies"]
    }
    assert r"_rtx/_job_executor\.py" not in documents[
        Path("skills/recurring-tasks/blueprints/rtx-init.yaml")
    ]["content"]

    standards = {
        path: set(documents[Path(path)]["content"])
        for path in (
            "references/skill-standards/blueprints/skill-guidelines.yaml",
            "references/skill-standards/blueprints/skill-refactoring.yaml",
        )
    }
    assert standards[
        "references/skill-standards/blueprints/skill-guidelines.yaml"
    ] == {r"skill\-guidelines\.md", r"skill\-guidelines\.standard\.yaml"}
    assert standards[
        "references/skill-standards/blueprints/skill-refactoring.yaml"
    ] == {r"skill\-refactoring\.md", r"skill\-refactoring\.standard\.yaml"}
    secret_store = documents[
        Path("src/officina/common/blueprints/secret-store.yaml")
    ]
    assert secret_store["platform_support"] == {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    assert {
        dependency["name"] for dependency in secret_store["runtime_dependencies"]
    } == {"keyring"}
    source_validator = schema_validator(
        load_schema(repo_root / "references/blueprint/behavioral-source.schema.json")
    )
    for document in (
        jobs_config,
        job_executor,
        secret_store,
        documents[
            Path("references/skill-standards/blueprints/skill-guidelines.yaml")
        ],
        documents[
            Path("references/skill-standards/blueprints/skill-refactoring.yaml")
        ],
    ):
        source_validator.validate(document)
    assert all(
        dependency.get("source") != "common"
        for document in documents.values()
        if document["node_type"] == "behavioral_source"
        for dependency in document["dependencies"]
    )
    assert all(document["schema_version"] == 4 for document in documents.values())
    assert Path("skills/skill-certifier/blueprint.yaml") in documents


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


def test_active_reference_check_reports_only_nonhistorical_tracked_references(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    active = repo / "src" / "active.py"
    historical = repo / "docs" / "plans" / "history" / "old.md"
    migration_test = repo / "tests" / "test_interface_injection_migration.py"
    for path in (active, historical, migration_test):
        path.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(
        'TARGET = "demo.machine.run"\nOWNER = "skill-audit"\n',
        encoding="utf-8",
    )
    historical.write_text("demo.machine.run\n", encoding="utf-8")
    migration_test.write_text("demo.machine.run\n", encoding="utf-8")
    repository.git("add", ".")
    migration_map = {
        "documents": [
            {
                "paths": ["docs/plans/history/**"],
                "disposition": "preserve",
                "execution_status": "frozen_history",
                "target": "historical evidence",
            }
        ]
    }

    findings = migration.check_active_migration_references(repo, migration_map)

    assert [finding.as_document() for finding in findings] == [
        {
            "code": "legacy-public-interface-namespace",
            "path": "src/active.py",
            "line": 1,
            "reference": ".machine.",
        },
        {
            "code": "legacy-certifier-name",
            "path": "src/active.py",
            "line": 2,
            "reference": "skill-audit",
        },
    ]


def test_active_reference_check_reports_mixed_case_legacy_markers(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    active = repo / "src" / "active.py"
    legacy_path = repo / "References" / "Blueprint" / "Health.Schema.Json"
    active.parent.mkdir(parents=True)
    legacy_path.parent.mkdir(parents=True)
    active.write_text(
        'TARGET = "demo.Machine.run"\nOWNER = "Skill-audit"\n',
        encoding="utf-8",
    )
    legacy_path.write_text("{}\n", encoding="utf-8")
    repository.git("add", ".")

    findings = migration.check_active_migration_references(repo, {})

    assert [finding.as_document() for finding in findings] == [
        {
            "code": "legacy-health-authority",
            "path": "References/Blueprint/Health.Schema.Json",
            "line": 0,
            "reference": "health.schema.json",
        },
        {
            "code": "legacy-public-interface-namespace",
            "path": "src/active.py",
            "line": 1,
            "reference": ".machine.",
        },
        {
            "code": "legacy-certifier-name",
            "path": "src/active.py",
            "line": 2,
            "reference": "skill-audit",
        },
    ]


def test_active_reference_check_reports_legacy_paths_without_reading_untracked_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    tracked = repo / "references" / "blueprint" / "health.schema.json"
    untracked = repo / "src" / "untracked.py"
    tracked.parent.mkdir(parents=True)
    untracked.parent.mkdir(parents=True)
    tracked.write_text("{}\n", encoding="utf-8")
    untracked.write_text('TARGET = "demo.llm.default"\n', encoding="utf-8")
    repository.git("add", "references/blueprint/health.schema.json")

    findings = migration.check_active_migration_references(repo, {})

    assert [finding.as_document() for finding in findings] == [
        {
            "code": "legacy-health-authority",
            "path": "references/blueprint/health.schema.json",
            "line": 0,
            "reference": "health.schema.json",
        }
    ]


def test_active_reference_check_ignores_tracked_files_deleted_from_worktree(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    retired = repo / "references" / "blueprint" / "health.schema.json"
    retired.parent.mkdir(parents=True)
    retired.write_text("{}\n", encoding="utf-8")
    repository.git("add", ".")
    retired.unlink()

    assert migration.check_active_migration_references(repo, {}) == ()


def test_post_adoption_cli_checks_map_and_references_without_materializing_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repository = GitTestRepository.create(repo)
    map_path = repo / "docs" / "plans" / "unified-architecture-migration-map.yaml"
    map_path.parent.mkdir(parents=True)
    map_path.write_text(
        yaml.safe_dump(
            {
                "map_version": 1,
                "authority": {
                    "version_contract": {"final_runtime_schema_version": 4}
                },
                "candidate_source": {},
                "declarations": {"version_2": {"merge_decisions": []}},
                "documents": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    repository.git("add", ".")
    script_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "migrate-blueprints-v4.py"
    )
    spec = importlib.util.spec_from_file_location("migrate_blueprints_v4", script_path)
    assert spec is not None and spec.loader is not None
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)

    before = sorted(path.relative_to(repo) for path in repo.rglob("*"))
    status = command.main(
        ["--check-map", "--check-active-references"],
        repo_root=repo,
    )
    after = sorted(path.relative_to(repo) for path in repo.rglob("*"))

    assert status == 0
    assert before == after
    assert capsys.readouterr().out.splitlines() == [
        "migration_map_status=valid-post-adoption",
        "active_reference_findings=0",
    ]


def test_live_cutover_inventory_is_v5_only_and_has_unique_public_ids() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    graph = migration.load_repository_blueprint_graph(repo_root)

    assert len(graph.nodes) == 221
    assert all(node.declaration["schema_version"] == 5 for node in graph.nodes.values())
    assert len(graph.exports) == len(set(graph.exports))


def test_live_skill_drift_retires_drift_hash_helper_and_keeps_package_dependency() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = yaml.safe_load(
        (
            repo_root
            / "skills"
            / "skill-drift"
            / "_rtx"
            / "blueprints"
            / "rtx-check-drift-state.yaml"
        ).read_text(encoding="utf-8")
    )

    assert source["content"] == [r"_check_drift_state\.py"]
    assert not (
        repo_root / "skills" / "skill-drift" / "_rtx" / "_drift_hashes.py"
    ).exists()
    assert any(
        dependency["source"] == "skill-drift-rtx.source.rtx-skill-sources-init"
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
    repository = GitTestRepository.initialize_existing_empty(root)
    certifier = root / "skills" / "skill-certifier" / "_rtx" / "_node_certifier.py"
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
    repository.git("add", "-A")
    repository.git("commit", "-qm", "candidate")
    commit = repository.git("rev-parse", "HEAD").stdout.decode().strip()
    repository.git(
        "update-ref",
        "refs/famulus/blueprint-v4-mechanical",
        commit,
    )
    return commit


def test_candidate_fixture_preserves_exact_head_under_ambient_autocrlf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    _write_candidate_certifier_api_fixture(candidate)
    certifier_path = (
        candidate / "skills" / "skill-certifier" / "_rtx" / "_node_certifier.py"
    )
    certifier_path.unlink()
    GitTestRepository(candidate).git(
        "checkout",
        "--",
        certifier_path.relative_to(candidate).as_posix(),
    )

    inspection = migration.inspect_candidate_v4(candidate)

    assert inspection["node_ids"] == ["candidate-node"]


def test_candidate_source_materialization_excludes_ignored_private_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    (source / "skills/demo").mkdir(parents=True)
    (source / "skills/demo/tracked.txt").write_text("tracked\n")
    (source / "skills/demo/state").mkdir()
    (source / "skills/demo/state/private.txt").write_text("private\n")
    (source / "reviewed-local.txt").write_text("reviewed\n")
    (source / "reviewed-local.txt").chmod(0o775)
    (source / ".gitignore").write_text("skills/demo/state/\n")
    repository.git(
        "add",
        ".gitignore",
        "skills/demo/tracked.txt",
    )
    repository.git("commit", "-qm", "source")
    plan = migration.CompiledMigrationPlan(
        module_renames={},
        local_source_includes=(Path("reviewed-local.txt"),),
        authorized_overlay={Path("reviewed-local.txt"): "added"},
    )

    snapshot = migration._copy_candidate_tree(source.resolve(), candidate, plan)
    migration._apply_source_overlay(candidate, snapshot)

    assert (candidate / "skills/demo/tracked.txt").is_file()
    assert (candidate / "reviewed-local.txt").is_file()
    if os.name == "posix":
        assert (candidate / "reviewed-local.txt").stat().st_mode & 0o777 == 0o775
    assert not (candidate / "skills/demo/state").exists()
    assert Path("skills/demo/state/private.txt") not in snapshot.entries


def test_candidate_source_rejects_undeclared_tracked_change(tmp_path: Path) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    repository.git("add", "tracked.txt")
    repository.git("commit", "-qm", "source")
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
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    repository.git("add", "tracked.txt")
    repository.git("commit", "-qm", "source")
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
    repository.git("add", "-A")
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
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    repository.git("add", "tracked.txt")
    repository.git("commit", "-qm", "source")
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
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    (source / ".gitignore").write_text("# tracked\n")
    repository.git("add", ".gitignore")
    repository.git("commit", "-qm", "source")
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
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    (source / ".gitignore").write_text("private.txt\n")
    (source / "private.txt").write_text("secret\n")
    repository.git("add", ".gitignore")
    repository.git("commit", "-qm", "source")
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
    repository = GitTestRepository.create(source)
    candidate.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("before\n")
    repository.git("add", "tracked.txt")
    repository.git("commit", "-qm", "source")
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
    repository = GitTestRepository.create(repo)
    (repo / "skills/old").mkdir(parents=True)
    (repo / "skills/old/SKILL.md").write_text("old\n")
    (repo / "approved.py").write_text("before\n")
    (repo / "unreviewed.py").write_text("before\n")
    repository.git("add", ".")
    repository.git("commit", "-qm", "base")
    base = repository.git("rev-parse", "HEAD").stdout.decode().strip()
    (repo / "skills/old").rename(repo / "skills/new")
    (repo / "approved.py").write_text("after\n")
    repository.git("add", "-A")
    repository.git("commit", "-qm", "final")
    final = repository.git("rev-parse", "HEAD").stdout.decode().strip()
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
    certifier = candidate / "skills/skill-certifier/_rtx/_node_certifier.py"
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
    GitTestRepository(candidate).git(
        "config",
        "famulus.candidateAtomicGuarantee",
        "false",
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
    certifier = candidate / "skills/skill-certifier/_rtx/_node_certifier.py"
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
