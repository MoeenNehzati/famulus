from pathlib import Path
from types import SimpleNamespace

import yaml

from validators.duplicate_subcommand_tokens import find_duplicate_fixed_subcommands


def test_graph_entry_reuses_supplied_repository_graph(tmp_path, monkeypatch):
    from validators import duplicate_subcommand_tokens as module_under_test

    interfaces = {
        "fixture.interface.one": {
            "process_binding": {"args_prefix": ["same"], "entry": "One"}
        },
        "fixture.interface.two": {
            "process_binding": {"args_prefix": ["same"], "entry": "Two"}
        },
    }
    graph = SimpleNamespace(
        nodes={
            "fixture.source": SimpleNamespace(
                node_type="behavioral_source",
                declaration={"interfaces": interfaces},
            )
        }
    )
    load_count = 0

    def load_graph(*args, **kwargs):
        nonlocal load_count
        load_count += 1
        return graph

    monkeypatch.setattr(module_under_test, "load_repository_blueprint_graph", load_graph)

    skill = tmp_path / "skills" / "fixture"
    skill.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("{}\n", encoding="utf-8")

    expected = [
        "source 'fixture.source': duplicate fixed dispatcher subcommand token "
        "`same` used by interfaces: fixture.interface.one, fixture.interface.two"
    ]
    assert module_under_test.REQUIRES_BLUEPRINT_GRAPH is True
    assert module_under_test.validate_with_graph(tmp_path, graph) == expected
    assert load_count == 0
    assert module_under_test.validate(tmp_path) == expected
    assert load_count == 1


def test_no_duplicates_in_clean_module():
    interfaces = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["write-config"], "min_positionals": 0, "max_positionals": 0},
    }
    assert find_duplicate_fixed_subcommands(interfaces) == []


def test_detects_duplicate_fixed_subcommand_token():
    interfaces = {
        "a": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
        "b": {"args_prefix": ["ensure-oauth"], "min_positionals": 0, "max_positionals": 0},
    }
    duplicates = find_duplicate_fixed_subcommands(interfaces)
    assert duplicates == [("ensure-oauth", ["a", "b"])]


def test_real_cloud_files_module_has_no_duplicates():
    payload = yaml.safe_load(
        Path("skills/cloud-files/_rtx/blueprints/rtx-ensure-oauth.yaml").read_text()
    )
    assert find_duplicate_fixed_subcommands(payload.get("interfaces", {})) == []


def test_extracts_args_prefix_nested_under_process_binding():
    interfaces = {
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
    duplicates = find_duplicate_fixed_subcommands(interfaces)
    assert duplicates == [
        (
            "ensure-oauth",
            [
                "cloud-files.source.rtx-ensure-oauth.interface.ensure-oauth",
                "cloud-files.source.rtx-write-config.interface.write-config",
            ],
        )
    ]


def test_ignores_interfaces_without_args_prefix():
    interfaces = {
        "a": {"process_binding": {"patterns": []}},
        "b": {},
    }
    assert find_duplicate_fixed_subcommands(interfaces) == []


def test_validate_does_not_flag_same_token_across_different_source_files():
    """Do not report equal tokens declared by separate behavioral sources."""
    from validators.duplicate_subcommand_tokens import validate

    repo_root = Path(__file__).resolve().parents[1]
    errors = validate(repo_root)
    offending = [
        error
        for error in errors
        if "list-manager" in error or "email-client" in error
    ]
    assert offending == [], f"false positives reintroduced: {offending}"


def test_validate_still_flags_same_source_file_different_entry_duplicate(
    tmp_path, monkeypatch
):
    """Report one source token claimed by different process entries."""
    import yaml as yaml_module

    from validators import duplicate_subcommand_tokens as module_under_test

    class _FakeNode:
        def __init__(self, node_type, declaration):
            self.node_type = node_type
            self.declaration = declaration

    class _FakeGraph:
        def __init__(self, nodes):
            self.nodes = nodes
            self.module_sources = {}

    duplicated_interfaces = {
        "fixture.source.dup.interface.one": {
            "process_binding": {
                "args_prefix": ["same-token"],
                "entry": "InterfaceOne",
            },
        },
        "fixture.source.dup.interface.two": {
            "process_binding": {
                "args_prefix": ["same-token"],
                "entry": "InterfaceTwo",
            },
        },
    }
    fake_graph = _FakeGraph(
        {
            "fixture.source.dup": _FakeNode(
                "behavioral_source", {"interfaces": duplicated_interfaces}
            ),
        }
    )

    monkeypatch.setattr(
        module_under_test,
        "load_repository_blueprint_graph",
        lambda repo_root, schema_root=None: fake_graph,
    )

    skills_root = tmp_path / "skills" / "fixture-module"
    skills_root.mkdir(parents=True)
    (skills_root / "blueprint.yaml").write_text(
        yaml_module.dump({"node_type": "module"}), encoding="utf-8"
    )

    errors = module_under_test.validate(tmp_path)
    assert len(errors) == 1
    assert "fixture.source.dup" in errors[0]
    assert "same-token" in errors[0]
    assert "fixture.source.dup.interface.one" in errors[0]
    assert "fixture.source.dup.interface.two" in errors[0]


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


def test_same_entry_with_identical_required_flags_is_not_flagged():
    """Accept one shared process entry with identical or absent flag rules."""
    interfaces = {
        "a": {
            "process_binding": {
                "args_prefix": ["same-token"],
                "entry": "Interface",
            },
        },
        "b": {
            "process_binding": {
                "args_prefix": ["same-token"],
                "entry": "Interface",
            },
        },
    }
    assert find_duplicate_fixed_subcommands(interfaces) == []
