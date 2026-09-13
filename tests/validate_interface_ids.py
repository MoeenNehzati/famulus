"""Tests for interface-ID enforcement by the shared blueprint preflight."""
from __future__ import annotations

from pathlib import Path
import shutil

import yaml

from validators.skill.blueprints import preflight

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_module(repo_root: Path) -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint-schema",
        repo_root / "references" / "blueprint-schema",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_preflight_requires_schema_template(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    errors, graph = preflight(tmp_path)

    assert errors == [
        f"{tmp_path / 'references' / 'blueprint-schema' / 'template.yaml'}: "
        "missing blueprint template reference file"
    ]
    assert graph is None


def test_valid_interface_ids_pass(tmp_path: Path) -> None:
    _copy_module(tmp_path)

    errors, graph = preflight(tmp_path)

    assert errors == []
    assert graph is not None


def test_export_id_must_use_module_interface_namespace(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path)
    module_path = skill / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["exports"]["loose-mode.bad.default"] = module["exports"].pop(
        "loose-mode.interface.default"
    )
    _write_yaml(module_path, module)

    errors, _graph = preflight(tmp_path)

    assert any("loose-mode.bad.default" in error for error in errors)


def test_source_interface_id_must_use_source_namespace(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path)
    source_path = skill / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    declaration = source["interfaces"].pop(
        "loose-mode.source.gateway.interface.default"
    )
    source["interfaces"]["loose-mode.interface.default"] = declaration
    _write_yaml(source_path, source)

    errors, _graph = preflight(tmp_path)

    assert any("loose-mode.interface.default" in error for error in errors)


def test_module_id_must_match_directory(tmp_path: Path) -> None:
    skill = _copy_module(tmp_path)
    module_path = skill / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["id"] = "other-module"
    _write_yaml(module_path, module)

    errors, _graph = preflight(tmp_path)

    assert any("must match direct identity" in error for error in errors)


def test_malformed_inventory_is_returned_as_a_finding(tmp_path: Path) -> None:
    _copy_module(tmp_path)
    blueprint = tmp_path / "skills" / "bad-skill" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text(
        "schema_version: 4\nnode_type: [\n",
        encoding="utf-8",
    )

    errors, graph = preflight(tmp_path)

    assert any("bad-skill" in error for error in errors)
    assert graph is None
