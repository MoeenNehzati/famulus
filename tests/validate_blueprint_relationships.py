"""Tests for canonical repository relationship validation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    REPO_ROOT
    / "validators"
    / "skill"
    / "blueprint_relationships.py"
)
SPEC = importlib.util.spec_from_file_location("blueprint_relationships", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def _copy_schema_root(repo_root: Path) -> None:
    shutil.copytree(
        REPO_ROOT / "references" / "blueprint",
        repo_root / "references" / "blueprint",
        ignore=shutil.ignore_patterns("blueprint.yaml", "blueprints"),
    )


def _copy_module(repo_root: Path, name: str) -> Path:
    target = repo_root / "skills" / name
    shutil.copytree(REPO_ROOT / "skills" / "loose-mode", target)
    for path in target.rglob("*"):
        if path.is_file():
            path.write_text(
                path.read_text(encoding="utf-8").replace("loose-mode", name),
                encoding="utf-8",
            )
    return target


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _set_export_access(
    module: Path,
    *,
    allow_all_modules: bool,
    allowed_callers: list[str],
) -> None:
    path = module / "blueprint.yaml"
    declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
    declaration["exports"][f"{module.name}.interface.default"]["access"] = {
        "allow_all_modules": allow_all_modules,
        "allowed_callers": allowed_callers,
    }
    _write_yaml(path, declaration)


def _set_gateway_uses(module: Path, uses: list[tuple[str, int]]) -> None:
    path = module / "blueprints" / "gateway.yaml"
    declaration = yaml.safe_load(path.read_text(encoding="utf-8"))
    declaration["uses_interfaces"] = [
        {"interface": interface_id, "version": version}
        for interface_id, version in uses
    ]
    _write_yaml(path, declaration)


def _two_modules(repo_root: Path) -> tuple[Path, Path]:
    _copy_schema_root(repo_root)
    provider = _copy_module(repo_root, "provider-skill")
    consumer = _copy_module(repo_root, "consumer-skill")
    return provider, consumer


def test_repo_without_modules_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    assert MOD.validate(tmp_path) == []


def test_valid_export_use_passes(tmp_path: Path) -> None:
    provider, consumer = _two_modules(tmp_path)
    _set_export_access(
        provider,
        allow_all_modules=False,
        allowed_callers=[consumer.name],
    )
    _set_gateway_uses(
        consumer,
        [("provider-skill.interface.default", 1)],
    )

    assert MOD.validate(tmp_path) == []


def test_unknown_export_is_rejected(tmp_path: Path) -> None:
    _provider, consumer = _two_modules(tmp_path)
    _set_gateway_uses(
        consumer,
        [("missing-skill.interface.default", 1)],
    )

    errors = MOD.validate(tmp_path)

    assert any("unresolved interface" in error for error in errors)


def test_stale_export_version_is_rejected(tmp_path: Path) -> None:
    _provider, consumer = _two_modules(tmp_path)
    _set_gateway_uses(
        consumer,
        [("provider-skill.interface.default", 2)],
    )

    errors = MOD.validate(tmp_path)

    assert any(
        "provider-skill.interface.default" in error
        and "requested=2" in error
        and "available=1" in error
        for error in errors
    ), errors


def test_export_access_control_is_enforced(tmp_path: Path) -> None:
    provider, consumer = _two_modules(tmp_path)
    _set_export_access(
        provider,
        allow_all_modules=False,
        allowed_callers=["other-skill"],
    )
    _set_gateway_uses(
        consumer,
        [("provider-skill.interface.default", 1)],
    )

    errors = MOD.validate(tmp_path)

    assert errors == ["unknown-caller-reference:provider-skill:other-skill"]


def test_private_source_interface_cannot_cross_module(tmp_path: Path) -> None:
    _provider, consumer = _two_modules(tmp_path)
    _set_gateway_uses(
        consumer,
        [("provider-skill.source.gateway.interface.default", 1)],
    )

    errors = MOD.validate(tmp_path)

    assert any("private interface" in error and "cannot be used cross-module" in error for error in errors)


def test_cross_module_cycle_is_rejected(tmp_path: Path) -> None:
    provider, consumer = _two_modules(tmp_path)
    _set_gateway_uses(
        provider,
        [("consumer-skill.interface.default", 1)],
    )
    _set_gateway_uses(
        consumer,
        [("provider-skill.interface.default", 1)],
    )

    errors = MOD.validate(tmp_path)

    assert any("cycle" in error for error in errors)
