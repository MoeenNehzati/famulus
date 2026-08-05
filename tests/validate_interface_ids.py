"""Tests for the version-4 interface-id validator."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    REPO_ROOT / "validators" / "skill" / "interface_ids.py"
)
SPEC = importlib.util.spec_from_file_location("interface_ids", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def _copy_v4_module(repo_root: Path) -> Path:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )
    target = repo_root / "skills" / "loose-mode"
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    return target


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_repo_without_modules_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    assert MOD.validate(tmp_path) == []


def test_valid_v4_interface_ids_pass(tmp_path: Path) -> None:
    _copy_v4_module(tmp_path)

    assert MOD.validate(tmp_path) == []


def test_export_id_must_use_module_interface_namespace(tmp_path: Path) -> None:
    skill = _copy_v4_module(tmp_path)
    module_path = skill / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["exports"]["loose-mode.bad.default"] = module["exports"].pop(
        "loose-mode.interface.default"
    )
    _write_yaml(module_path, module)

    errors = MOD.validate(tmp_path)

    assert any("loose-mode.bad.default" in error for error in errors)


def test_source_interface_id_must_use_source_namespace(tmp_path: Path) -> None:
    skill = _copy_v4_module(tmp_path)
    source_path = skill / "blueprints" / "gateway.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    declaration = source["interfaces"].pop(
        "loose-mode.source.gateway.interface.default"
    )
    source["interfaces"]["loose-mode.interface.default"] = declaration
    _write_yaml(source_path, source)

    errors = MOD.validate(tmp_path)

    assert any("loose-mode.interface.default" in error for error in errors)


def test_module_id_must_match_directory(tmp_path: Path) -> None:
    skill = _copy_v4_module(tmp_path)
    module_path = skill / "blueprint.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    module["id"] = "other-module"
    _write_yaml(module_path, module)

    errors = MOD.validate(tmp_path)

    assert any("must match direct identity" in error for error in errors)


def test_malformed_inventory_is_returned_as_a_finding(tmp_path: Path) -> None:
    blueprint = tmp_path / "skills" / "bad-skill" / "blueprint.yaml"
    blueprint.parent.mkdir(parents=True)
    blueprint.write_text(
        "schema_version: 4\nnode_type: [\n",
        encoding="utf-8",
    )

    errors = MOD.validate(tmp_path)

    assert errors
