"""Tests for canonical repository relationship validation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil

import pytest
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
        REPO_ROOT / "references" / "blueprint-schema",
        repo_root / "references" / "blueprint-schema",
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


@pytest.fixture(scope="function")
def two_module_repository(tmp_path: Path) -> tuple[Path, Path]:
    _copy_schema_root(tmp_path)
    provider = _copy_module(tmp_path, "provider-skill")
    consumer = _copy_module(tmp_path, "consumer-skill")
    return provider, consumer


def test_repo_without_modules_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    assert MOD.validate(tmp_path) == []


def test_export_relationship_scenarios(
    two_module_repository: tuple[Path, Path],
) -> None:
    """Keep each relationship scenario independent on one exact v6 repository."""

    provider, consumer = two_module_repository
    repository = provider.parents[1]
    blueprint_paths = tuple(
        path
        for module in (provider, consumer)
        for path in module.rglob("*.yaml")
    )

    def allow_consumer() -> None:
        _set_export_access(
            provider,
            allow_all_modules=False,
            allowed_callers=[consumer.name],
        )

    def valid_relation() -> None:
        allow_consumer()
        _set_gateway_uses(
            consumer,
            [("provider-skill.interface.default", 1)],
        )

    def stale_version() -> None:
        _set_gateway_uses(
            consumer,
            [("provider-skill.interface.default", 2)],
        )

    def unknown_caller() -> None:
        _set_export_access(
            provider,
            allow_all_modules=False,
            allowed_callers=["other-skill"],
        )
        _set_gateway_uses(
            consumer,
            [("provider-skill.interface.default", 1)],
        )

    def private_interface() -> None:
        _set_gateway_uses(
            consumer,
            [("provider-skill.source.gateway.interface.default", 1)],
        )

    def export_cycle() -> None:
        _set_gateway_uses(
            provider,
            [("consumer-skill.interface.default", 1)],
        )
        _set_gateway_uses(
            consumer,
            [("provider-skill.interface.default", 1)],
        )

    scenarios = (
        ("valid relation", valid_relation, []),
        (
            "stale version",
            stale_version,
            [
                "consumer-skill.source.gateway: version-mismatch:"
                "provider-skill.interface.default:requested=2:available=1"
            ],
        ),
        (
            "unknown caller",
            unknown_caller,
            ["unknown-caller-reference:provider-skill:other-skill"],
        ),
        (
            "private interface",
            private_interface,
            [
                "consumer-skill.source.gateway: private interface "
                "'provider-skill.source.gateway.interface.default' "
                "cannot be used cross-module"
            ],
        ),
        (
            "export cycle",
            export_cycle,
            [
                "certification dependency cycle: consumer-skill.source.gateway "
                "-> provider-skill.source.gateway -> consumer-skill.source.gateway"
            ],
        ),
    )
    for label, mutate, expected in scenarios:
        snapshots = {path: path.read_bytes() for path in blueprint_paths}
        try:
            try:
                mutate()
                assert MOD.validate(repository) == expected
            except BaseException as exc:
                exc.add_note(f"scenario: {label}")
                raise
        finally:
            try:
                current_paths = {
                    path
                    for module in (provider, consumer)
                    for path in module.rglob("*.yaml")
                }
                for path in current_paths - snapshots.keys():
                    path.unlink()
                for path, contents in snapshots.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(contents)
            except BaseException as exc:
                exc.add_note(f"scenario: {label}; restoration")
                raise
