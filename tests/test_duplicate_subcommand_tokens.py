from pathlib import Path
from types import SimpleNamespace

import yaml

from validators.duplicate_subcommand_tokens import find_duplicate_fixed_subcommands


def test_validator_uses_supplied_graph_and_standalone_loader_once(
    tmp_path, monkeypatch
):
    from validators import duplicate_subcommand_tokens as module_under_test

    graph = SimpleNamespace(
        nodes={
            "fixture.source.duplicate": SimpleNamespace(
                node_type="behavioral_source",
                declaration={
                    "interfaces": {
                        "fixture.interface.one": {
                            "process_binding": {
                                "args_prefix": ["same"],
                                "entry": "One",
                            }
                        },
                        "fixture.interface.two": {
                            "process_binding": {
                                "args_prefix": ["same"],
                                "entry": "Two",
                            }
                        },
                    }
                },
            ),
            "fixture.source.shared.one": SimpleNamespace(
                node_type="behavioral_source",
                declaration={"interfaces": {"one": {"args_prefix": ["shared"]}}},
            ),
            "fixture.source.shared.two": SimpleNamespace(
                node_type="behavioral_source",
                declaration={"interfaces": {"two": {"args_prefix": ["shared"]}}},
            ),
        }
    )
    loaded_with = []

    def load_graph(repo_root, schema_root=None):
        loaded_with.append((repo_root, schema_root))
        return graph

    monkeypatch.setattr(module_under_test, "load_repository_blueprint_graph", load_graph)

    skill = tmp_path / "skills" / "fixture"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("{}\n", encoding="utf-8")
    schema_root = tmp_path / "references" / "blueprint-schema"
    schema_root.mkdir(parents=True)
    (schema_root / "module.schema.json").write_text("{}\n", encoding="utf-8")

    expected = [
        "source 'fixture.source.duplicate': duplicate fixed dispatcher subcommand token "
        "`same` used by interfaces: fixture.interface.one, fixture.interface.two"
    ]
    assert module_under_test.REQUIRES_BLUEPRINT_GRAPH is True
    assert module_under_test.validate_with_graph(tmp_path, graph) == expected
    assert loaded_with == []
    assert module_under_test.validate(tmp_path) == expected
    assert loaded_with == [(tmp_path, schema_root)]


def test_finds_duplicates_across_supported_interface_shapes():
    flat_clean = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["write-config"], "min_positionals": 0, "max_positionals": 0},
    }
    flat_duplicate = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
    }
    process_binding_duplicate = {
        "cloud-files.source.rtx-ensure-oauth.interface.ensure-oauth": {
            "process_binding": {
                "args_prefix": ["ensure-oauth"],
                "patterns": [{"min_positionals": 0, "max_positionals": 0}],
            },
        },
        "cloud-files.source.rtx-write-config.interface.write-config": {
            "process_binding": {
                "args_prefix": ["ensure-oauth"],
                "patterns": [{"min_positionals": 0, "max_positionals": 0}],
            },
        },
    }
    missing_prefix = {
        "a": {"process_binding": {"patterns": []}},
        "b": {},
    }

    assert find_duplicate_fixed_subcommands(flat_clean) == []
    assert find_duplicate_fixed_subcommands(flat_duplicate) == [
        ("ensure-oauth", ["a", "b"])
    ]
    assert find_duplicate_fixed_subcommands(process_binding_duplicate) == [
        (
            "ensure-oauth",
            [
                "cloud-files.source.rtx-ensure-oauth.interface.ensure-oauth",
                "cloud-files.source.rtx-write-config.interface.write-config",
            ],
        )
    ]
    assert find_duplicate_fixed_subcommands(missing_prefix) == []


def test_real_cloud_files_module_has_no_duplicates():
    payload = yaml.safe_load(
        Path("skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml").read_text()
    )
    assert find_duplicate_fixed_subcommands(payload.get("interfaces", {})) == []


def test_same_entry_with_different_required_flags_is_not_flagged():
    """Accept one shared process entry despite different required flags."""
    interfaces = {
        "list-manager.source.rtx-yaml-store.interface.cloud-create-entry": {
            "process_binding": {
                "args_prefix": ["create-entry"],
                "entry": "Interface",
                "patterns": [{"required_flags": ["--cloud", "--entries"]}],
            },
        },
        "list-manager.source.rtx-yaml-store.interface.create-entry": {
            "process_binding": {
                "args_prefix": ["create-entry"],
                "entry": "Interface",
                "patterns": [{"required_flags": ["--entries"]}],
            },
        },
    }
    assert find_duplicate_fixed_subcommands(interfaces) == []
