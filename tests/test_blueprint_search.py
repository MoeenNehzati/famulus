from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import officina.blueprints.search as blueprint_search_module  # noqa: E402
from officina.blueprints.search import (  # noqa: E402
    BlueprintSearchError,
    iter_blueprints,
    load_blueprint_record,
    search_blueprints,
    select_values,
    strip_selected_paths,
)
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "search_blueprints.py"
V5_AUTHORIZATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "authorization"
)
V5_SCHEMA_ROOT = (
    Path(__file__).parent / "fixtures" / "blueprint_schemas" / "v5"
)
V6_SCHEMA_ROOT = (
    Path(__file__).parent / "fixtures" / "blueprint_schemas" / "v6"
)
_canonical_iter_blueprints = iter_blueprints
_canonical_search_blueprints = search_blueprints


def iter_blueprints(
    repo_root: Path | str,
    *,
    include_hidden: bool = False,
    schema_version: int = 4,
):
    """Keep frozen-v4 search fixtures explicit in this mixed module."""

    return _canonical_iter_blueprints(
        repo_root,
        include_hidden=include_hidden,
        schema_version=schema_version,
    )


def search_blueprints(
    repo_root: Path | str,
    query: dict[str, object] | None = None,
):
    """Keep frozen-v4 search fixtures explicit in this mixed module."""

    selected = dict(query or {})
    selected.setdefault("schema_version", 4)
    return _canonical_search_blueprints(repo_root, selected)


def _write_blueprint(root: Path, skill: str, body: str) -> None:
    path = root / "skills" / skill / "blueprint.yaml"
    path.parent.mkdir(parents=True)
    body_text = dedent(body).lstrip()
    supplied = yaml.safe_load(body_text) or {}
    declaration = {
        "schema_version": 4,
        "node_type": "module",
        "id": skill,
        "version": 1,
        "gateway": {"path": "SKILL.md", "language": "Markdown"},
        "content": [r"SKILL\.md"],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "exports": {},
        **supplied,
    }
    comments = "\n".join(
        line for line in body_text.splitlines() if line.lstrip().startswith("#")
    )
    rendered = yaml.safe_dump(declaration, sort_keys=False)
    path.write_text(
        f"{comments}\n{rendered}" if comments else rendered,
        encoding="utf-8",
    )


def _write_v4_blueprints(root: Path) -> None:
    _write_blueprint(
        root,
        "demo-module",
        """
        schema_version: 4
        node_type: module
        id: demo-module
        version: 1
        exports:
          demo-module.interface.run:
            source_interface: demo-module.source.runner.interface.run
        """,
    )
    source = root / "skills" / "demo-module" / "blueprints" / "runner.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        dedent(
            """
            schema_version: 4
            node_type: behavioral_source
            id: demo-module.source.runner
            version: 1
            interfaces:
              demo-module.source.runner.interface.run:
                version: 1
                description: Run the module.
            """
        ).lstrip(),
        encoding="utf-8",
    )


def _write_v6_blueprints(root: Path) -> None:
    module_root = root / "skills" / "node-drift"
    module_root.mkdir(parents=True)
    (module_root / "SKILL.md").write_text("# Node drift\n", encoding="utf-8")
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "node-drift",
                "version": 1,
                "maturity": "stable",
                "description": "Report signed-certificate currentness.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    "node-drift.source.gateway": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/gateway.yaml",
                        }
                    }
                },
                "children": {},
                "namespace_exports": {},
                "exports": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    source = module_root / "blueprints" / "gateway.yaml"
    source.parent.mkdir()
    source.write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "node-drift.source.gateway",
                "version": 1,
                "maturity": "stable",
                "description": "Define the currentness query rules.",
                "gateway": {"path": "SKILL.md", "language": "Markdown"},
                "content": [r"SKILL\.md"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_v4_module(
    root: Path,
    relative_root: str,
    module_id: str,
    source_name: str,
) -> None:
    module_root = root / relative_root
    source_id = f"{module_id}.source.{source_name}"
    module_root.mkdir(parents=True)
    (module_root / "blueprint.yaml").write_text(
        dedent(
            f"""
            schema_version: 4
            node_type: module
            id: {module_id}
            version: 1
            sources:
              {source_id}:
                blueprint:
                  base: module-root
                  path: blueprints/{source_name}.yaml
            exports: {{}}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    source = module_root / "blueprints" / f"{source_name}.yaml"
    source.parent.mkdir()
    source.write_text(
        dedent(
            f"""
            schema_version: 4
            node_type: behavioral_source
            id: {source_id}
            version: 1
            interfaces: {{}}
            """
        ).lstrip(),
        encoding="utf-8",
    )


def test_iter_blueprints_rejects_pre_v4_skill_records(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "legacy-skill" / "blueprint.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("category: development-assistant\n", encoding="utf-8")

    try:
        list(iter_blueprints(tmp_path))
    except BlueprintSearchError as exc:
        assert "schema_version 4" in str(exc)
    else:
        raise AssertionError("expected BlueprintSearchError")


def test_v4_search_discovers_repository_modules_outside_skills(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    _write_v4_module(
        tmp_path,
        "references/blueprint-schema",
        "blueprint-schema",
        "schema-annotated-draft",
    )
    _write_v4_module(
        tmp_path,
        "references/skill-standards",
        "skill-standards",
        "skill-guidelines",
    )
    _write_v4_module(
        tmp_path,
        "src/officina/common",
        "common",
        "blueprint-graph",
    )

    rows = search_blueprints(tmp_path)

    assert {
        (row["module"], row["id"], row["node_type"], row["path"])
        for row in rows
    } == {
        (
            "blueprint-schema",
            "blueprint-schema",
            "module",
            "references/blueprint-schema/blueprint.yaml",
        ),
        (
            "blueprint-schema",
            "blueprint-schema.source.schema-annotated-draft",
            "behavioral_source",
            "references/blueprint-schema/blueprints/schema-annotated-draft.yaml",
        ),
        (
            "skill-standards",
            "skill-standards",
            "module",
            "references/skill-standards/blueprint.yaml",
        ),
        (
            "skill-standards",
            "skill-standards.source.skill-guidelines",
            "behavioral_source",
            "references/skill-standards/blueprints/skill-guidelines.yaml",
        ),
        (
            "common",
            "common",
            "module",
            "src/officina/common/blueprint.yaml",
        ),
        (
            "common",
            "common.source.blueprint-graph",
            "behavioral_source",
            "src/officina/common/blueprints/blueprint-graph.yaml",
        ),
    }


def test_v4_search_discovers_modules_and_direct_source_blueprints(tmp_path: Path) -> None:
    _write_v4_blueprints(tmp_path)

    records = list(iter_blueprints(tmp_path))
    rows = search_blueprints(
        tmp_path,
        {
            "filter": {"path": "node_type", "op": "eq", "value": "behavioral_source"},
            "select": [
                "module",
                "path",
                "id",
                "node_type",
                {"as": "interface_descriptions", "path": "interfaces.*.description"},
            ],
        },
    )

    assert [(record.module, record.data["id"], record.path) for record in records] == [
        ("demo-module", "demo-module", "skills/demo-module/blueprint.yaml"),
        (
            "demo-module",
            "demo-module.source.runner",
            "skills/demo-module/blueprints/runner.yaml",
        ),
    ]
    assert rows == [
        {
            "module": "demo-module",
            "path": "skills/demo-module/blueprints/runner.yaml",
            "values": {
                "id": "demo-module.source.runner",
                "node_type": "behavioral_source",
                "interface_descriptions": ["Run the module."],
            },
        }
    ]


def test_v4_default_search_result_uses_generic_node_metadata(tmp_path: Path) -> None:
    _write_v4_blueprints(tmp_path)

    assert search_blueprints(tmp_path) == [
        {
            "module": "demo-module",
            "id": "demo-module",
            "node_type": "module",
            "path": "skills/demo-module/blueprint.yaml",
        },
        {
            "module": "demo-module",
            "id": "demo-module.source.runner",
            "node_type": "behavioral_source",
            "path": "skills/demo-module/blueprints/runner.yaml",
        },
    ]


def test_v6_search_exposes_registered_descriptions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    canonical_loader = blueprint_search_module.load_repository_blueprint_graph
    monkeypatch.setattr(
        blueprint_search_module,
        "load_repository_blueprint_graph",
        lambda repo_root, **kwargs: canonical_loader(
            repo_root,
            schema_root=V6_SCHEMA_ROOT,
            **kwargs,
        ),
    )
    _write_v6_blueprints(tmp_path)

    rows = _canonical_search_blueprints(
        tmp_path,
        {
            "schema_version": 6,
            "filter": {
                "any": [
                    {"path": "id", "op": "eq", "value": "node-drift"},
                    {
                        "path": "id",
                        "op": "eq",
                        "value": "node-drift.source.gateway",
                    },
                ]
            },
            "select": ["id", "description"],
        },
    )

    assert rows == [
        {
            "module": "node-drift",
            "path": "skills/node-drift/blueprint.yaml",
            "values": {
                "id": "node-drift",
                "description": "Report signed-certificate currentness.",
            },
        },
        {
            "module": "node-drift",
            "path": "skills/node-drift/blueprints/gateway.yaml",
            "values": {
                "id": "node-drift.source.gateway",
                "description": "Define the currentness query rules.",
            },
        },
    ]


def test_v5_search_uses_global_module_ids_and_registered_ancestry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    canonical_loader = blueprint_search_module.load_repository_blueprint_graph
    monkeypatch.setattr(
        blueprint_search_module,
        "load_repository_blueprint_graph",
        lambda repo_root, **kwargs: canonical_loader(
            repo_root,
            schema_root=V5_SCHEMA_ROOT,
            **kwargs,
        ),
    )
    root = copy_v5_fixture_tree(
        V5_AUTHORIZATION_FIXTURE,
        tmp_path / "repo",
    )

    rows = search_blueprints(
        root,
        {
            "schema_version": 5,
            "filter": {
                "path": "$ancestry",
                "op": "contains",
                "value": "demo-rtx",
            },
            "select": ["module", "ancestry", "id", "node_type", "path"],
        },
    )

    assert rows == [
        {
            "module": "demo-rtx",
            "ancestry": ["demo", "demo-rtx"],
            "path": "skills/demo/_rtx/blueprint.yaml",
            "values": {
                "id": "demo-rtx",
                "node_type": "module",
            },
        },
        {
            "module": "demo-rtx",
            "ancestry": ["demo", "demo-rtx"],
            "path": "skills/demo/_rtx/blueprints/runtime.yaml",
            "values": {
                "id": "demo-rtx.source.runtime",
                "node_type": "behavioral_source",
            },
        },
    ]


def test_load_blueprint_record_reads_exact_path_with_repo_relative_path(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "alpha",
        """
        category: development-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    record = load_blueprint_record(
        tmp_path / "skills" / "alpha" / "blueprint.yaml",
        repo_root=tmp_path,
    )

    assert record.module == "alpha"
    assert record.path == "skills/alpha/blueprint.yaml"
    assert record.data["category"] == "development-assistant"


def test_search_rejects_legacy_skill_selector(tmp_path: Path) -> None:
    _write_v4_blueprints(tmp_path)

    try:
        search_blueprints(tmp_path, {"select": ["skill"]})
    except BlueprintSearchError as exc:
        assert "legacy selector" in str(exc)
    else:
        raise AssertionError("expected BlueprintSearchError")


def test_load_blueprint_record_reports_invalid_yaml_path(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "broken" / "blueprint.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("category: [\n", encoding="utf-8")

    try:
        load_blueprint_record(path, repo_root=tmp_path)
    except BlueprintSearchError as exc:
        assert "skills/broken/blueprint.yaml: invalid YAML" in str(exc)
    else:
        raise AssertionError("expected BlueprintSearchError")


def test_select_values_resolves_nested_wildcards_and_list_indexes() -> None:
    data = {
        "interfaces": {
            "example.source.reader.interface.read": {
                "process_binding": {"kind": "process"}
            },
            "example.source.writer.interface.write": {
                "process_binding": {"kind": "process"}
            },
        },
        "suggested_permissions": {
            "bash": [
                {"command": ["dispatcher"], "reason": "run interface"},
                {"command": ["curl", "-I"], "reason": "check endpoint"},
            ]
        },
    }

    assert select_values(data, "interfaces.*.process_binding.kind") == [
        ("interfaces.example.source.reader.interface.read.process_binding.kind", "process"),
        ("interfaces.example.source.writer.interface.write.process_binding.kind", "process"),
    ]
    assert select_values(data, "suggested_permissions.bash.*.command.0") == [
        ("suggested_permissions.bash.0.command.0", "dispatcher"),
        ("suggested_permissions.bash.1.command.0", "curl"),
    ]


def test_select_values_supports_recursive_wildcard() -> None:
    data = {
        "interfaces": {
            "example.source.gateway.interface.default": {
                "contract": {"direct_io": {"reads": []}}
            },
            "example.source.scanner.interface.scan": {
                "contract": {"direct_io": {"writes": []}}
            },
        },
        "direct_io": {"network": []},
    }

    assert select_values(data, "**.direct_io") == [
        ("direct_io", {"network": []}),
        (
            "interfaces.example.source.gateway.interface.default.contract.direct_io",
            {"reads": []},
        ),
        (
            "interfaces.example.source.scanner.interface.scan.contract.direct_io",
            {"writes": []},
        ),
    ]


def test_strip_selected_paths_removes_recursive_matches_without_mutating_input() -> None:
    data = {
        "interfaces": {
            "gateway": {
                "contract": {
                    "description": "Default interface.",
                    "direct_io": {"reads": [{"path": "/tmp/input"}]},
                }
            },
            "scanner": {
                "contract": {
                    "direct_io": {"writes": []},
                },
                "process_binding": {"kind": "process"},
            },
        }
    }

    stripped = strip_selected_paths(data, "**.direct_io")

    assert stripped == {
        "interfaces": {
            "gateway": {
                "contract": {"description": "Default interface."}
            },
            "scanner": {
                "contract": {},
                "process_binding": {"kind": "process"},
            },
        }
    }
    assert (
        "direct_io"
        in data["interfaces"]["gateway"]["contract"]
    )


def test_search_blueprints_filters_with_and_or_regex_and_selects_values(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "linux-skill",
        """
        category: system-assistant
        interface_version: 1
        interfaces:
          linux-skill.source.sync.interface.run:
            description: Sync systemd units from jobs.yaml.
            contract:
              execution:
                state_effect: mutating
            process_binding:
              kind: process
        """,
    )
    _write_blueprint(
        tmp_path,
        "portable-skill",
        """
        category: development-assistant
        interface_version: 1
        interfaces:
          portable-skill.source.inspect.interface.run:
            description: Inspect blueprint data.
            contract:
              execution:
                state_effect: read-only
            process_binding:
              kind: process
        """,
    )

    rows = search_blueprints(
        tmp_path,
        {
            "filter": {
                "all": [
                    {
                        "path": "interfaces.*.contract.execution.state_effect",
                        "op": "eq",
                        "value": "mutating",
                    },
                    {
                        "any": [
                            {
                                "path": "interfaces.*.description",
                                "op": "regex",
                                "pattern": "linux|systemd",
                                "flags": "i",
                            },
                            {"path": "category", "op": "regex", "pattern": "development"},
                        ]
                    },
                ]
            },
            "select": [
                "module",
                "path",
                "category",
                {
                    "as": "state_effects",
                    "path": "interfaces.*.contract.execution.state_effect",
                },
                {
                    "as": "process_kinds",
                    "path": "interfaces.*.process_binding.kind",
                },
            ],
            "explain": True,
        },
    )

    assert rows == [
        {
            "module": "linux-skill",
            "path": "skills/linux-skill/blueprint.yaml",
            "values": {
                "category": "system-assistant",
                "state_effects": ["mutating"],
                "process_kinds": ["process"],
            },
            "matches": [
                {
                    "selector": "interfaces.*.contract.execution.state_effect",
                    "op": "eq",
                    "path": (
                        "interfaces.linux-skill.source.sync.interface.run."
                        "contract.execution.state_effect"
                    ),
                    "value": "mutating",
                },
                {
                    "selector": "interfaces.*.description",
                    "op": "regex",
                    "path": "interfaces.linux-skill.source.sync.interface.run.description",
                    "value": "Sync systemd units from jobs.yaml.",
                },
            ],
        }
    ]


def test_search_blueprints_explains_all_matching_predicate_values(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "storage-skill",
        """
        category: system-assistant
        interface_version: 1
        interfaces:
          storage-skill.source.reader.interface.read-list:
            contract:
              direct_io:
                network:
                  - system: google-drive
          storage-skill.source.writer.interface.write-list:
            contract:
              direct_io:
                network:
                  - system: google-drive
          storage-skill.source.auth.interface.check:
            contract:
              direct_io:
                network:
                  - system: oauth
        """,
    )

    rows = search_blueprints(
        tmp_path,
        {
            "filter": {
                "path": "interfaces.*.contract.direct_io.network.*.system",
                "op": "eq",
                "value": "google-drive",
            },
            "explain": True,
        },
    )

    assert rows[0]["matches"] == [
        {
            "selector": "interfaces.*.contract.direct_io.network.*.system",
            "op": "eq",
            "path": (
                "interfaces.storage-skill.source.reader.interface.read-list."
                "contract.direct_io.network.0.system"
            ),
            "value": "google-drive",
        },
        {
            "selector": "interfaces.*.contract.direct_io.network.*.system",
            "op": "eq",
            "path": (
                "interfaces.storage-skill.source.writer.interface.write-list."
                "contract.direct_io.network.0.system"
            ),
            "value": "google-drive",
        },
    ]


def test_search_blueprints_select_all_and_raw_comments(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "commented",
        """
        # keep this comment in raw output only
        category: research-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    rows = search_blueprints(tmp_path, {"select": "all", "comments": "raw"})

    assert rows[0]["module"] == "commented"
    assert rows[0]["path"] == "skills/commented/blueprint.yaml"
    assert {
        key: rows[0]["data"][key]
        for key in ("category", "interface_version", "interfaces")
    } == {
        "category": "research-assistant",
        "interface_version": 1,
        "interfaces": {},
    }
    assert "# keep this comment" in rows[0]["raw"]


def test_missing_filter_matches_absent_selector(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "minimal",
        """
        category: general-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    rows = search_blueprints(
        tmp_path,
        {
            "filter": {"path": "display_description", "op": "missing"},
            "select": ["module", "display_description"],
            "explain": True,
        },
    )

    assert rows == [
        {
            "module": "minimal",
            "path": "skills/minimal/blueprint.yaml",
            "values": {"display_description": []},
            "matches": [
                {
                    "selector": "display_description",
                    "op": "missing",
                    "path": "display_description",
                    "value": None,
                }
            ],
        }
    ]


def test_invalid_query_raises_useful_error(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "demo",
        """
        category: general-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    try:
        search_blueprints(tmp_path, {"filter": {"path": "category", "op": "unknown"}})
    except BlueprintSearchError as exc:
        assert "unsupported filter op" in str(exc)
    else:
        raise AssertionError("expected BlueprintSearchError")


def test_cli_reads_yaml_query_file_and_emits_json(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "cli-skill",
        """
        category: coding-development-assistant
        interface_version: 1
        interfaces: {}
        """,
    )
    query_file = tmp_path / "query.yaml"
    query_file.write_text(
        yaml.safe_dump(
            {
                "schema_version": 4,
                "filter": {"path": "category", "op": "regex", "pattern": "coding"},
                "select": ["module", "category"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--repo-root",
            str(tmp_path),
            "--query-file",
            str(query_file),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {
            "module": "cli-skill",
            "path": "skills/cli-skill/blueprint.yaml",
            "values": {"category": "coding-development-assistant"},
        }
    ]
