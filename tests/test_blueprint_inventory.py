from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from officina.common import blueprint_inventory
from officina.common.blueprint_inventory import (
    BlueprintInventoryError,
    collect_blueprints,
    iter_blueprints,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def test_inventory_discovers_roots_and_hidden_sidecars_in_lexical_order(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "skills" / "zeta" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: zeta\n")
    _write(tmp_path / "skills" / "alpha" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: alpha\n")
    _write(tmp_path / "skills" / "alpha" / "_rtx" / "._worker.py.blueprint.yaml", "schema_version: 3\nnode_type: machine-module\nid: alpha.machine-module.worker\n")
    _write(tmp_path / "references" / ".policy.md.blueprint.yaml", "schema_version: 3\nnode_type: behavior-source\nid: references.source.policy\n")

    documents = tuple(iter_blueprints(tmp_path))

    assert [item.relative_path.as_posix() for item in documents] == sorted(
        item.relative_path.as_posix() for item in documents
    )
    module = next(item for item in documents if item.node_type == "machine-module")
    assert module.owner_root == tmp_path / "skills" / "alpha"
    repository_source = next(item for item in documents if item.node_id == "references.source.policy")
    assert repository_source.owner_root == tmp_path


@pytest.mark.parametrize(
    ("text", "diagnostic"),
    [
        ("id: one\nid: two\n", "duplicate key"),
        ("id: !custom value\n", "could not determine a constructor"),
        ("? [not, a, string]\n: value\n", "mapping key must be a string"),
        ("- not\n- a\n- mapping\n", "document root must be a mapping"),
        ("created: 2026-07-19\n", "non-JSON value"),
        ("value: .nan\n", "non-JSON number"),
    ],
)
def test_strict_inventory_aggregates_parse_and_normalization_failures(
    tmp_path: Path, text: str, diagnostic: str
) -> None:
    _write(tmp_path / "skills" / "broken" / "blueprint.yaml", text)

    with pytest.raises(BlueprintInventoryError, match=diagnostic):
        tuple(iter_blueprints(tmp_path))


def test_diagnostic_collection_returns_valid_documents_and_all_issues(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "skills" / "valid" / "blueprint.yaml", "schema_version: 3\nnode_type: skill\nid: valid\n")
    _write(tmp_path / "skills" / "a" / "blueprint.yaml", "id: one\nid: two\n")
    _write(tmp_path / "skills" / "b" / "blueprint.yaml", "- invalid\n")

    result = collect_blueprints(tmp_path, skip_parse_errors=True)

    assert [item.node_id for item in result.documents] == ["valid"]
    assert len(result.issues) == 2
    assert [issue.relative_path.as_posix() for issue in result.issues] == sorted(
        issue.relative_path.as_posix() for issue in result.issues
    )


def test_inventory_ignores_nonhidden_sidecars_and_symlinks(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "valid"
    _write(skill / "blueprint.yaml", "id: valid\n")
    _write(skill / "plain.blueprint.yaml", "id: not-a-sidecar\n")
    outside = tmp_path / "outside"
    _write(outside / ".escaped.blueprint.yaml", "id: escaped\n")
    (skill / "linked").symlink_to(outside, target_is_directory=True)
    (skill / ".linked.blueprint.yaml").symlink_to(outside / ".escaped.blueprint.yaml")

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.node_id for document in documents] == ["valid"]


def test_inventory_discovers_v4_behavioral_source_blueprints(tmp_path: Path) -> None:
    module = tmp_path / "skills" / "demo-skill"
    _write(
        module / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: demo-skill\n",
    )
    _write(
        module / "blueprints" / "gateway.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: demo-skill.source.gateway\n",
    )
    _write(
        module / "blueprints" / "nested" / "ignored.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: demo-skill.source.ignored\n",
    )

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.relative_path.as_posix() for document in documents] == [
        "skills/demo-skill/blueprint.yaml",
        "skills/demo-skill/blueprints/gateway.yaml",
    ]
    assert documents[1].owner_root == module


def test_inventory_discovers_generic_v4_module_root_and_follows_sources(
    tmp_path: Path,
) -> None:
    module = tmp_path / "references" / "standards"
    _write(
        module / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: standards\n",
    )
    _write(
        module / "blueprints" / "policy.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: standards.source.policy\n",
    )
    _write(
        module / "blueprints" / "nested" / "ignored.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: standards.source.ignored\n",
    )

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.relative_path.as_posix() for document in documents] == [
        "references/standards/blueprint.yaml",
        "references/standards/blueprints/policy.yaml",
    ]
    assert all(document.owner_root == module for document in documents)


def test_inventory_reports_malformed_source_under_conventional_skill_root(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "skills" / "demo-skill" / "blueprints" / "broken.yaml",
        "schema_version: 4\n? [not, a, string]: invalid\n",
    )

    with pytest.raises(BlueprintInventoryError, match="mapping key must be a string"):
        tuple(iter_blueprints(tmp_path))


def test_inventory_rejects_nested_canonical_module_roots(tmp_path: Path) -> None:
    _write(
        tmp_path / "modules" / "outer" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: outer\n",
    )
    _write(
        tmp_path / "modules" / "outer" / "inner" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: inner\n",
    )

    with pytest.raises(BlueprintInventoryError, match="nested module roots"):
        tuple(iter_blueprints(tmp_path))


def test_inventory_rejects_duplicate_canonical_module_ids(tmp_path: Path) -> None:
    _write(
        tmp_path / "one" / "shared" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: shared\n",
    )
    _write(
        tmp_path / "two" / "shared" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: shared\n",
    )

    with pytest.raises(BlueprintInventoryError, match="duplicate module id"):
        tuple(iter_blueprints(tmp_path))


def test_inventory_rejects_module_root_identity_collision(tmp_path: Path) -> None:
    _write(
        tmp_path / "references" / "standards" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: other-name\n",
    )

    with pytest.raises(BlueprintInventoryError, match="must match its directory"):
        tuple(iter_blueprints(tmp_path))


def test_inventory_prunes_git_ignored_module_directories(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _write(tmp_path / ".gitignore", "ignored-module/\nignored-skill/\n")
    _write(
        tmp_path / "ignored-module" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: ignored-module\n",
    )
    _write(
        tmp_path / "skills" / "ignored-skill" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: ignored-skill\n",
    )
    _write(
        tmp_path / "visible-module" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: visible-module\n",
    )

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.node_id for document in documents] == ["visible-module"]


def test_inventory_prunes_individually_ignored_blueprint_files(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "--quiet")
    module = tmp_path / "visible-module"
    _write(
        tmp_path / ".gitignore",
        "visible-module/blueprints/ignored.yaml\n"
        "visible-module/.ignored.md.blueprint.yaml\n",
    )
    _write(
        module / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: visible-module\n",
    )
    _write(
        module / "blueprints" / "visible.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: visible-module.source.visible\n",
    )
    _write(
        module / "blueprints" / "ignored.yaml",
        "schema_version: 4\nnode_type: behavioral_source\nid: visible-module.source.ignored\n",
    )
    _write(
        module / ".ignored.md.blueprint.yaml",
        "schema_version: 3\nnode_type: behavior-source\nid: ignored-sidecar\n",
    )

    documents = tuple(iter_blueprints(tmp_path))

    assert [document.node_id for document in documents] == [
        "visible-module",
        "visible-module.source.visible",
    ]


def test_inventory_rejects_ancestor_replaced_by_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "module"
    _write(
        module / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: module\n",
    )
    original_blueprint_paths = blueprint_inventory._blueprint_paths

    def replace_after_discovery(*args: object, **kwargs: object) -> tuple[Path, ...]:
        paths = original_blueprint_paths(*args, **kwargs)
        relocated = tmp_path / "relocated-module"
        module.rename(relocated)
        try:
            module.symlink_to(relocated, target_is_directory=True)
        except OSError:
            # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=atomic-files no-follow tests cover ancestor symlink rejection
            pytest.skip("directory symlinks are unavailable")
        return paths

    monkeypatch.setattr(
        blueprint_inventory,
        "_blueprint_paths",
        replace_after_discovery,
    )

    with pytest.raises(BlueprintInventoryError, match="securely open"):
        tuple(iter_blueprints(tmp_path))
