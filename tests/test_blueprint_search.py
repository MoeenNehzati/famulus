from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.blueprint_search import (  # noqa: E402
    BlueprintSearchError,
    iter_blueprints,
    load_blueprint_record,
    search_blueprints,
    select_values,
    strip_selected_paths,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "search_blueprints.py"


def _write_blueprint(root: Path, skill: str, body: str) -> None:
    path = root / "skills" / skill / "blueprint.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(dedent(body).lstrip(), encoding="utf-8")


def test_iter_blueprints_yields_sorted_skill_records(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "zeta",
        """
        category: system-assistant
        interface_version: 1
        interfaces: {}
        """,
    )
    _write_blueprint(
        tmp_path,
        "alpha",
        """
        category: development-assistant
        interface_version: 1
        interfaces: {}
        """,
    )
    _write_blueprint(
        tmp_path,
        ".hidden",
        """
        category: research-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    records = list(iter_blueprints(tmp_path))

    assert [record.skill for record in records] == ["alpha", "zeta"]
    assert records[0].path == "skills/alpha/blueprint.yaml"
    assert records[0].data["category"] == "development-assistant"


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

    assert record.skill == "alpha"
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
            "machine": {
                "read": {"invocation": {"kind": "python_machine_interface"}},
                "write": {"invocation": {"kind": "python_module"}},
            }
        },
        "suggested_permissions": {
            "bash": [
                {"command": ["dispatcher"], "reason": "run interface"},
                {"command": ["curl", "-I"], "reason": "check endpoint"},
            ]
        },
    }

    assert select_values(data, "interfaces.machine.*.invocation.kind") == [
        ("interfaces.machine.read.invocation.kind", "python_machine_interface"),
        ("interfaces.machine.write.invocation.kind", "python_module"),
    ]
    assert select_values(data, "suggested_permissions.bash.*.command.0") == [
        ("suggested_permissions.bash.0.command.0", "dispatcher"),
        ("suggested_permissions.bash.1.command.0", "curl"),
    ]


def test_select_values_supports_recursive_wildcard() -> None:
    data = {
        "interfaces": {
            "llm": {"default": {"direct_io": {"reads": []}}},
            "machine": {"scan": {"direct_io": {"writes": []}}},
        },
        "direct_io": {"network": []},
    }

    assert select_values(data, "**.direct_io") == [
        ("direct_io", {"network": []}),
        ("interfaces.llm.default.direct_io", {"reads": []}),
        ("interfaces.machine.scan.direct_io", {"writes": []}),
    ]


def test_strip_selected_paths_removes_recursive_matches_without_mutating_input() -> None:
    data = {
        "interfaces": {
            "llm": {
                "default": {
                    "description": "Default interface.",
                    "direct_io": {"reads": [{"path": "/tmp/input"}]},
                }
            },
            "machine": {
                "scan": {
                    "invocation": {"kind": "python_machine_interface"},
                    "direct_io": {"writes": []},
                }
            },
        }
    }

    stripped = strip_selected_paths(data, "**.direct_io")

    assert stripped == {
        "interfaces": {
            "llm": {"default": {"description": "Default interface."}},
            "machine": {"scan": {"invocation": {"kind": "python_machine_interface"}}},
        }
    }
    assert "direct_io" in data["interfaces"]["llm"]["default"]


def test_search_blueprints_filters_with_and_or_regex_and_selects_values(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "linux-skill",
        """
        category: system-assistant
        interface_version: 1
        interfaces:
          machine:
            sync:
              description: Sync systemd units from jobs.yaml.
              platform_support:
                linux: true
                macos: false
                windows: false
              invocation:
                kind: python_machine_interface
        """,
    )
    _write_blueprint(
        tmp_path,
        "portable-skill",
        """
        category: development-assistant
        interface_version: 1
        interfaces:
          machine:
            inspect:
              description: Inspect blueprint data.
              platform_support:
                linux: true
                macos: true
                windows: true
              invocation:
                kind: python_module
        """,
    )

    rows = search_blueprints(
        tmp_path,
        {
            "filter": {
                "all": [
                    {"path": "interfaces.machine.*.platform_support.macos", "op": "eq", "value": False},
                    {
                        "any": [
                            {
                                "path": "interfaces.machine.*.description",
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
                "skill",
                "path",
                "category",
                {"as": "macos_support", "path": "interfaces.machine.*.platform_support.macos"},
                {"as": "invocation_kinds", "path": "interfaces.machine.*.invocation.kind"},
            ],
            "explain": True,
        },
    )

    assert rows == [
        {
            "skill": "linux-skill",
            "path": "skills/linux-skill/blueprint.yaml",
            "values": {
                "category": "system-assistant",
                "macos_support": [False],
                "invocation_kinds": ["python_machine_interface"],
            },
            "matches": [
                {
                    "selector": "interfaces.machine.*.platform_support.macos",
                    "op": "eq",
                    "path": "interfaces.machine.sync.platform_support.macos",
                    "value": False,
                },
                {
                    "selector": "interfaces.machine.*.description",
                    "op": "regex",
                    "path": "interfaces.machine.sync.description",
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
          machine:
            read-list:
              direct_io:
                network:
                  - system: google-drive
            write-list:
              direct_io:
                network:
                  - system: google-drive
            check-auth:
              direct_io:
                network:
                  - system: oauth
        """,
    )

    rows = search_blueprints(
        tmp_path,
        {
            "filter": {
                "path": "interfaces.machine.*.direct_io.network.*.system",
                "op": "eq",
                "value": "google-drive",
            },
            "explain": True,
        },
    )

    assert rows[0]["matches"] == [
        {
            "selector": "interfaces.machine.*.direct_io.network.*.system",
            "op": "eq",
            "path": "interfaces.machine.read-list.direct_io.network.0.system",
            "value": "google-drive",
        },
        {
            "selector": "interfaces.machine.*.direct_io.network.*.system",
            "op": "eq",
            "path": "interfaces.machine.write-list.direct_io.network.0.system",
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

    assert rows[0]["skill"] == "commented"
    assert rows[0]["path"] == "skills/commented/blueprint.yaml"
    assert rows[0]["data"] == {
        "category": "research-assistant",
        "interface_version": 1,
        "interfaces": {},
    }
    assert "# keep this comment" in rows[0]["raw"]


def test_search_blueprints_default_query_returns_metadata_only(tmp_path: Path) -> None:
    _write_blueprint(
        tmp_path,
        "defaulted",
        """
        category: general-assistant
        interface_version: 1
        interfaces: {}
        """,
    )

    assert search_blueprints(tmp_path) == [
        {"skill": "defaulted", "path": "skills/defaulted/blueprint.yaml"}
    ]


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
            "select": ["skill", "display_description"],
            "explain": True,
        },
    )

    assert rows == [
        {
            "skill": "minimal",
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
                "filter": {"path": "category", "op": "regex", "pattern": "coding"},
                "select": ["skill", "category"],
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
            "skill": "cli-skill",
            "path": "skills/cli-skill/blueprint.yaml",
            "values": {"category": "coding-development-assistant"},
        }
    ]
