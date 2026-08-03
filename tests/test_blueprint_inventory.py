from __future__ import annotations

from collections.abc import Iterator, Set
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from officina.common import blueprint_inventory
from officina.common.blueprint_inventory import (
    BlueprintInventoryError,
    collect_blueprints,
    iter_blueprints,
)
from test_support.git_repository import GitTestRepository
from v5_blueprint_fixtures import copy_v5_fixture_tree


_V5_INVENTORY_FIXTURES = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "inventory"
)
_canonical_collect_blueprints = collect_blueprints
_canonical_iter_blueprints = iter_blueprints


def collect_blueprints(
    repo_root: Path,
    *,
    expected_schema_version: int = 4,
    skip_parse_errors: bool = False,
):
    """Exercise frozen v4 inventory cases explicitly in this mixed test module."""

    return _canonical_collect_blueprints(
        repo_root,
        expected_schema_version=expected_schema_version,
        skip_parse_errors=skip_parse_errors,
    )


def iter_blueprints(
    repo_root: Path,
    *,
    expected_schema_version: int = 4,
):
    """Exercise frozen v4 inventory cases explicitly in this mixed test module."""

    return _canonical_iter_blueprints(
        repo_root,
        expected_schema_version=expected_schema_version,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_v5_inventory_fixture(name: str, tmp_path: Path) -> Path:
    return copy_v5_fixture_tree(
        _V5_INVENTORY_FIXTURES / name,
        tmp_path / "repo",
    )


def _v5_module_text(
    module_id: str,
    *,
    children: dict[str, dict[str, str]] | None = None,
    discovery: bool = False,
    gateway: str = "README.md",
) -> str:
    declaration: dict[str, object] = {
        "schema_version": 5,
        "node_type": "module",
        "id": module_id,
        "version": 1,
        "gateway": {"path": gateway, "language": "Markdown"},
        "content": [gateway.replace(".", r"\.")],
        "authority": {"owns_filesystem": []},
        "sources": {},
        "children": children or {},
        "namespace_exports": {},
        "exports": {},
    }
    if discovery:
        declaration["discovery"] = {"mechanism": "skill"}
    return yaml.safe_dump(declaration, sort_keys=False)


def _write_v5_module(
    module_root: Path,
    module_id: str,
    *,
    children: dict[str, dict[str, str]] | None = None,
    discovery: bool = False,
    gateway: str = "README.md",
) -> None:
    _write(
        module_root / "blueprint.yaml",
        _v5_module_text(
            module_id,
            children=children,
            discovery=discovery,
            gateway=gateway,
        ),
    )
    _write(module_root / gateway, "")


class _CountingIgnoredPaths(Set[Path]):
    def __init__(self, paths: set[Path]) -> None:
        self._paths = paths
        self.examined = 0

    def __contains__(self, path: object) -> bool:
        return path in self._paths

    def __iter__(self) -> Iterator[Path]:
        for path in self._paths:
            self.examined += 1
            yield path

    def __len__(self) -> int:
        return len(self._paths)


def test_strict_inventory_selects_c_safe_loader_when_available() -> None:
    expected_loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

    assert issubclass(blueprint_inventory._StrictBlueprintLoader, expected_loader)


def test_selected_strict_loader_rejects_duplicate_keys() -> None:
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key"):
        yaml.load(
            "id: one\nid: two\n",
            Loader=blueprint_inventory._StrictBlueprintLoader,
        )


def test_ignored_path_lookup_does_not_scan_all_ignored_entries(
    tmp_path: Path,
) -> None:
    ignored_directory = tmp_path / "ignored-directory"
    ignored_paths = _CountingIgnoredPaths(
        {
            ignored_directory,
            *(tmp_path / "ignored" / f"path-{index}" for index in range(2_000)),
        }
    )

    assert blueprint_inventory._ignored_path(
        ignored_directory / "nested" / "blueprint.yaml",
        ignored_paths,
    )
    assert not blueprint_inventory._ignored_path(
        tmp_path / "visible" / "blueprint.yaml",
        ignored_paths,
    )
    assert ignored_paths.examined < 20


def test_inventory_discovers_only_canonical_v4_modules_and_direct_sources(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "skills" / "zeta" / "blueprint.yaml", "schema_version: 4\nnode_type: module\nid: zeta\n")
    _write(tmp_path / "skills" / "alpha" / "blueprint.yaml", "schema_version: 4\nnode_type: module\nid: alpha\n")
    _write(tmp_path / "skills" / "alpha" / "blueprints" / "worker.yaml", "schema_version: 4\nnode_type: behavioral_source\nid: alpha.source.worker\n")
    _write(
        tmp_path / "skills" / "alpha" / "_rtx" / "._worker.py.blueprint.yaml",
        "schema_version: 3\nnode_type: machine" "-module\nid: alpha.machine"
        "-module.worker\n",
    )
    _write(
        tmp_path / "references" / ".policy.md.blueprint.yaml",
        "schema_version: 3\nnode_type: behavior" "-source\nid: references.source.policy\n",
    )

    documents = tuple(iter_blueprints(tmp_path))

    assert [item.relative_path.as_posix() for item in documents] == sorted(
        item.relative_path.as_posix() for item in documents
    )
    assert [item.node_id for item in documents] == [
        "alpha",
        "alpha.source.worker",
        "zeta",
    ]
    assert documents[1].module_root == tmp_path / "skills" / "alpha"


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
    _write(tmp_path / "skills" / "valid" / "blueprint.yaml", "schema_version: 4\nnode_type: module\nid: valid\n")
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
    _write(skill / "blueprint.yaml", "schema_version: 4\nnode_type: module\nid: valid\n")
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
    assert documents[1].module_root == module


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
    assert all(document.module_root == module for document in documents)


def test_inventory_reports_malformed_source_under_conventional_skill_root(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "skills" / "demo-skill" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: demo-skill\n",
    )
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
    root = GitTestRepository.create(tmp_path / "repo").root
    _write(root / ".gitignore", "ignored-module/\nignored-skill/\n")
    _write(
        root / "ignored-module" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: ignored-module\n",
    )
    _write(
        root / "skills" / "ignored-skill" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: ignored-skill\n",
    )
    _write(
        root / "visible-module" / "blueprint.yaml",
        "schema_version: 4\nnode_type: module\nid: visible-module\n",
    )

    documents = tuple(iter_blueprints(root))

    assert [document.node_id for document in documents] == ["visible-module"]


def test_inventory_prunes_individually_ignored_blueprint_files(
    tmp_path: Path,
) -> None:
    root = GitTestRepository.create(tmp_path / "repo").root
    module = root / "visible-module"
    _write(
        root / ".gitignore",
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
        "schema_version: 3\nnode_type: behavior" "-source\nid: ignored-sidecar\n",
    )

    documents = tuple(iter_blueprints(root))

    assert [document.node_id for document in documents] == [
        "visible-module",
        "visible-module.source.visible",
    ]


def test_inventory_rejects_pre_v4_module_markers(tmp_path: Path) -> None:
    _write(
        tmp_path / "skills" / "legacy" / "blueprint.yaml",
        "schema_version: 3\nnode_type: skill\nid: legacy\n",
    )

    with pytest.raises(BlueprintInventoryError, match="schema_version 4.*node_type module"):
        tuple(iter_blueprints(tmp_path))


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

    with pytest.raises(
        BlueprintInventoryError,
        match="securely open|reparse point",
    ):
        tuple(iter_blueprints(tmp_path))


def test_v5_inventory_follows_registered_children_recursively(tmp_path: Path) -> None:
    root = _copy_v5_inventory_fixture("registered", tmp_path)

    result = collect_blueprints(root, expected_schema_version=5)

    assert [document.node_id for document in result.documents] == [
        "outer",
        "middle",
        "leaf",
        "leaf.source.worker",
    ]
    by_id = {
        document.node_id: document
        for document in result.documents
        if document.node_id is not None
    }
    leaf_root = root / "modules" / "outer" / "middle" / "leaf"
    assert by_id["leaf"].module_root == leaf_root
    assert by_id["leaf.source.worker"].module_root == leaf_root
    assert by_id["leaf.source.worker"].module_root == leaf_root
    assert [field.name for field in fields(type(by_id["leaf"]))].count(
        "module_root"
    ) == 1
    assert "owner_root" not in {
        field.name for field in fields(type(by_id["leaf"]))
    }


def test_canonical_inventory_defaults_to_v5(tmp_path: Path) -> None:
    root = _copy_v5_inventory_fixture("registered", tmp_path)

    result = _canonical_collect_blueprints(root)

    assert [document.node_id for document in result.documents] == [
        "outer",
        "middle",
        "leaf",
        "leaf.source.worker",
    ]


def test_v5_inventory_rejects_unregistered_nested_marker(tmp_path: Path) -> None:
    outer = tmp_path / "modules" / "outer"
    _write_v5_module(outer, "outer")
    _write_v5_module(outer / "child", "child")

    with pytest.raises(
        BlueprintInventoryError,
        match="unregistered nested module marker",
    ):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_v5_inventory_rejects_child_registered_by_duplicate_parents(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "modules" / "outer"
    middle = outer / "middle"
    leaf = middle / "leaf"
    _write_v5_module(
        outer,
        "outer",
        children={
            "middle": {
                "base": "module-root",
                "path": "middle/blueprint.yaml",
            },
            "leaf": {
                "base": "module-root",
                "path": "middle/leaf/blueprint.yaml",
            },
        },
    )
    _write_v5_module(
        middle,
        "middle",
        children={
            "leaf": {
                "base": "module-root",
                "path": "leaf/blueprint.yaml",
            }
        },
    )
    _write_v5_module(leaf, "leaf")

    with pytest.raises(
        BlueprintInventoryError,
        match="registered by multiple parents",
    ):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_v5_inventory_rejects_registration_cycle(tmp_path: Path) -> None:
    alpha = tmp_path / "modules" / "alpha"
    _write_v5_module(
        alpha,
        "alpha",
        children={
            "alpha": {
                "base": "module-root",
                "path": "blueprint.yaml",
            }
        },
    )

    with pytest.raises(BlueprintInventoryError, match="registration cycle"):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_v5_inventory_requires_registration_by_nearest_physical_parent(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "modules" / "outer"
    middle = outer / "middle"
    leaf = middle / "leaf"
    _write_v5_module(
        outer,
        "outer",
        children={
            "middle": {
                "base": "module-root",
                "path": "middle/blueprint.yaml",
            },
            "leaf": {
                "base": "module-root",
                "path": "middle/leaf/blueprint.yaml",
            },
        },
    )
    _write_v5_module(middle, "middle")
    _write_v5_module(leaf, "leaf")

    with pytest.raises(
        BlueprintInventoryError,
        match="nearest physical parent.*middle",
    ):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_v5_inventory_rejects_registration_into_ignored_path(
    tmp_path: Path,
) -> None:
    root = GitTestRepository.create(tmp_path / "repo").root
    outer = root / "modules" / "outer"
    _write(root / ".gitignore", "modules/outer/ignored/\n")
    _write_v5_module(
        outer,
        "outer",
        children={
            "ignored": {
                "base": "module-root",
                "path": "ignored/blueprint.yaml",
            }
        },
    )
    _write_v5_module(outer / "ignored", "ignored")

    with pytest.raises(BlueprintInventoryError, match="ignored path"):
        collect_blueprints(root, expected_schema_version=5)


def test_v5_inventory_rejects_nested_source_control_repository(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "modules" / "outer"
    nested = outer / "nested"
    _write_v5_module(
        outer,
        "outer",
        children={
            "nested": {
                "base": "module-root",
                "path": "nested/blueprint.yaml",
            }
        },
    )
    _write_v5_module(nested, "nested")
    (nested / ".git").mkdir()

    with pytest.raises(
        BlueprintInventoryError,
        match="nested source-control repository",
    ):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_v5_inventory_rejects_symlinked_registered_marker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outer = root / "modules" / "outer"
    _write_v5_module(
        outer,
        "outer",
        children={
            "child": {
                "base": "module-root",
                "path": "child/blueprint.yaml",
            }
        },
    )
    outside = tmp_path / "outside"
    _write_v5_module(outside, "child")
    (outer / "child").mkdir()
    try:
        (outer / "child" / "blueprint.yaml").symlink_to(
            outside / "blueprint.yaml"
        )
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=symlink creation is unavailable on some hosts; alternate=ancestor-replacement test covers no-follow marker reads
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(BlueprintInventoryError, match="symbolic link"):
        collect_blueprints(root, expected_schema_version=5)


def test_v5_inventory_accepts_only_derived_skill_rtx_identity(
    tmp_path: Path,
) -> None:
    root = _copy_v5_inventory_fixture("managed-skill", tmp_path)

    result = collect_blueprints(root, expected_schema_version=5)

    assert [document.node_id for document in result.documents] == [
        "demo-rtx",
        "demo",
    ]
    by_id = {document.node_id: document for document in result.documents}
    assert by_id["demo-rtx"].module_root == root / "skills" / "demo" / "_rtx"

    child_root = root / "skills" / "demo" / "_rtx"
    _write(
        child_root / "blueprint.yaml",
        _v5_module_text("wrong-rtx", gateway="__init__.py"),
    )
    with pytest.raises(
        BlueprintInventoryError,
        match="code child id must be 'demo-rtx'",
    ):
        collect_blueprints(root, expected_schema_version=5)


def test_v5_inventory_accepts_managed_skill_without_rtx(
    tmp_path: Path,
) -> None:
    root = _copy_v5_inventory_fixture("managed-skill", tmp_path)
    skill_root = root / "skills" / "demo"
    parent_path = skill_root / "blueprint.yaml"
    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    parent["children"] = {}
    _write(parent_path, yaml.safe_dump(parent, sort_keys=False))
    (skill_root / "_rtx" / "blueprint.yaml").unlink()
    (skill_root / "_rtx" / "__init__.py").unlink()
    (skill_root / "_rtx").rmdir()

    result = collect_blueprints(root, expected_schema_version=5)

    assert [document.node_id for document in result.documents] == ["demo"]


def test_v5_inventory_rejects_unconfigured_rtx_directory(
    tmp_path: Path,
) -> None:
    root = _copy_v5_inventory_fixture("managed-skill", tmp_path)
    skill_root = root / "skills" / "demo"
    parent_path = skill_root / "blueprint.yaml"
    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    parent["children"] = {}
    _write(parent_path, yaml.safe_dump(parent, sort_keys=False))
    (skill_root / "_rtx" / "blueprint.yaml").unlink()

    with pytest.raises(
        BlueprintInventoryError,
        match="existing _rtx implementation directory must contain",
    ):
        collect_blueprints(root, expected_schema_version=5)


def test_v5_inventory_rejects_partial_repository_skill_predicate(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "partial"
    _write_v5_module(skill_root, "partial", discovery=True)

    with pytest.raises(
        BlueprintInventoryError,
        match="partial repository-managed skill",
    ):
        collect_blueprints(tmp_path, expected_schema_version=5)


def test_common_package_exports_v5_inventory_interface() -> None:
    from officina.common import (  # noqa: PLC0415
        BlueprintDocument,
        BlueprintInventoryResult,
        collect_blueprints as public_collect_blueprints,
    )

    assert BlueprintDocument is blueprint_inventory.BlueprintDocument
    assert BlueprintInventoryResult is blueprint_inventory.BlueprintInventoryResult
    assert public_collect_blueprints is _canonical_collect_blueprints
