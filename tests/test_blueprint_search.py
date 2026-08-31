from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
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


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "search_blueprints.py"
V6_SCHEMA_ROOT = (
    Path(__file__).parent / "fixtures" / "blueprint_schemas" / "v6"
)
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


def test_iter_blueprints_rejects_pre_v6_skill_records(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "legacy-skill" / "blueprint.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("category: development-assistant\n", encoding="utf-8")

    try:
        list(iter_blueprints(tmp_path))
    except BlueprintSearchError as exc:
        assert "schema_version 6" in str(exc)
    else:
        raise AssertionError("expected BlueprintSearchError")


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

    rows = search_blueprints(
        tmp_path,
        {
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


def test_search_rejects_removed_schema_version_selector(tmp_path: Path) -> None:
    _write_v6_blueprints(tmp_path)

    with pytest.raises(BlueprintSearchError, match="unsupported query key"):
        search_blueprints(tmp_path, {"schema_version": 5})


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


def legacy_search_blueprints_filters_with_and_or_regex_and_selects_values(tmp_path: Path) -> None:
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


def legacy_search_blueprints_explains_all_matching_predicate_values(tmp_path: Path) -> None:
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


def legacy_missing_filter_matches_absent_selector(tmp_path: Path) -> None:
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


def legacy_cli_reads_yaml_query_file_and_emits_json(tmp_path: Path) -> None:
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
