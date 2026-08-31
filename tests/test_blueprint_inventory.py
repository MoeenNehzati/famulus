from __future__ import annotations

from collections.abc import Iterator, Set
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

import officina.blueprints.inventory as blueprint_inventory
from officina.blueprints.inventory import (
    BlueprintInventoryError,
    collect_blueprints,
    iter_blueprints,
)
from test_support.git_repository import GitTestRepository
from test_support.v5_blueprint_fixtures import copy_v5_fixture_tree


_V5_INVENTORY_FIXTURES = (
    Path(__file__).parent / "fixtures" / "blueprint_v5" / "inventory"
)
_canonical_collect_blueprints = collect_blueprints


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_v5_inventory_fixture(name: str, tmp_path: Path) -> Path:
    root = copy_v5_fixture_tree(
        _V5_INVENTORY_FIXTURES / name,
        tmp_path / "repo",
    )
    for path in root.rglob("*.yaml"):
        declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(declaration, dict) and declaration.get("schema_version") == 5:
            declaration["schema_version"] = 6
            declaration.setdefault("maturity", "stable")
            _write(path, yaml.safe_dump(declaration, sort_keys=False))
    return root


def _v5_module_text(
    module_id: str,
    *,
    children: dict[str, dict[str, str]] | None = None,
    discovery: bool = False,
    gateway: str = "README.md",
) -> str:
    declaration: dict[str, object] = {
        "schema_version": 6,
        "node_type": "module",
        "id": module_id,
        "version": 1,
        "maturity": "stable",
        "description": f"{module_id} module.",
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


def test_canonical_inventory_defaults_to_v6(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(
        root / "modules" / "outer" / "blueprint.yaml",
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "outer",
                "version": 1,
                "description": "Synthetic v6 module.",
                "gateway": {"path": "README.md", "language": "Markdown"},
                "content": [r"README\.md"],
                "authority": {"owns_filesystem": []},
                "sources": {},
                "children": {},
                "namespace_exports": {},
                "exports": {},
            },
            sort_keys=False,
        ),
    )

    result = _canonical_collect_blueprints(root)

    assert [document.node_id for document in result.documents] == ["outer"]


def test_v5_inventory_rejects_unregistered_nested_marker(tmp_path: Path) -> None:
    outer = tmp_path / "modules" / "outer"
    _write_v5_module(outer, "outer")
    _write_v5_module(outer / "child", "child")

    with pytest.raises(
        BlueprintInventoryError,
        match="unregistered nested module marker",
    ):
        collect_blueprints(tmp_path)


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
    ):
        collect_blueprints(tmp_path)


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

    with pytest.raises(BlueprintInventoryError):
        collect_blueprints(tmp_path)


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
    ):
        collect_blueprints(tmp_path)


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

    with pytest.raises(BlueprintInventoryError):
        collect_blueprints(root)


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
        collect_blueprints(tmp_path)


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

    with pytest.raises(BlueprintInventoryError):
        collect_blueprints(root)


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

    result = collect_blueprints(root)

    assert [document.node_id for document in result.documents] == ["demo"]


def test_inventory_uses_its_relocated_module() -> None:
    from officina.blueprints.inventory import (  # noqa: PLC0415
        BlueprintDocument,
        BlueprintInventoryResult,
        collect_blueprints as public_collect_blueprints,
    )

    assert BlueprintDocument is blueprint_inventory.BlueprintDocument
    assert BlueprintInventoryResult is blueprint_inventory.BlueprintInventoryResult
    assert public_collect_blueprints is _canonical_collect_blueprints
