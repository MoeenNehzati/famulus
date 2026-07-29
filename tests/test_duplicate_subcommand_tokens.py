from pathlib import Path

import yaml

from validators.duplicate_subcommand_tokens import find_duplicate_fixed_subcommands


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
        Path("skills/cloud-files/blueprints/rtx-ensure-oauth.yaml").read_text()
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
    """Regression test: interfaces in different behavioral-source files never
    share one dispatcher process, so the same args_prefix token in two
    different source files within the same module must NOT be flagged.

    This mirrors the real repository state: list-manager's `create-entry`
    subcommand is shared by the `cloud-create-entry`/`create-entry` interface
    pair (same source file, same process_binding.entry, disambiguated by the
    real dispatch target rather than by argv re-derivation) and, similarly,
    `init`/`read`/`update`; email-client's `list` token is shared across two
    genuinely different source files (accounts-list vs mail-list).
    """
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
    """True-positive regression: two interfaces sharing one source file's
    own interfaces mapping AND a different process_binding.entry are
    genuinely ambiguous — two different Python dispatch targets could both
    claim the same token — and must still be flagged.
    """
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
    """Mirrors the real list-manager `create-entry`/`cloud-create-entry`
    pair: same token, same process_binding.entry (one Python class handles
    both), but different required_flags (`--cloud` distinguishes the two
    documented contracts at the call site). Same-entry alone is the sound
    invariant here, so this must NOT be flagged regardless of the flags.
    """
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
    """Same entry, same token, and identical (or absent) required_flags is
    still not flagged: the dispatcher's real routing property is the
    explicit process_binding.entry target, not argv re-derivation, so one
    shared handler is safe no matter how its own patterns are documented.
    This also covers interfaces with no patterns/required_flags at all.
    """
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
