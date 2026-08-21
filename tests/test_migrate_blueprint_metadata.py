"""Contracts for the authored-blueprint metadata migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_blueprint_metadata.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("migrate_blueprint_metadata", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_blueprint(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _module(node_id: str, *, discoverable: bool) -> dict:
    document = {"schema_version": 6, "node_type": "module", "id": node_id}
    if discoverable:
        document["discovery"] = {"mechanism": "skill"}
    return document


def _source(node_id: str) -> dict:
    return {"schema_version": 6, "node_type": "behavioral_source", "id": node_id}


def test_migration_defaults_and_overrides_are_idempotent(tmp_path: Path) -> None:
    """The migration owns defaults, named overrides, and absence reporting."""

    _write_blueprint(
        tmp_path / "skills" / "demo" / "blueprint.yaml",
        _module("demo", discoverable=True),
    )
    _write_blueprint(
        tmp_path / "skills" / "pdf-to-markdown" / "blueprint.yaml",
        _module("pdf-to-markdown", discoverable=True),
    )
    _write_blueprint(
        tmp_path / "src" / "officina" / "demo" / "blueprints" / "worker.yaml",
        _source("demo.source.worker"),
    )

    migration = _migration_module()
    first = migration.migrate_repository(tmp_path)
    second = migration.migrate_repository(tmp_path)

    demo = yaml.safe_load(
        (tmp_path / "skills" / "demo" / "blueprint.yaml").read_text(encoding="utf-8")
    )
    pdf = yaml.safe_load(
        (tmp_path / "skills" / "pdf-to-markdown" / "blueprint.yaml").read_text(
            encoding="utf-8"
        )
    )
    source = yaml.safe_load(
        (tmp_path / "src" / "officina" / "demo" / "blueprints" / "worker.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert demo["maturity"] == "stable"
    assert demo["installation_tier"] == "core"
    assert demo["personal_preference"] == {"applies": False}
    assert pdf["installation_tier"] == "optional"
    assert source == {
        "schema_version": 6,
        "node_type": "behavioral_source",
        "id": "demo.source.worker",
        "maturity": "stable",
    }
    assert first.absent_experimental_overrides == ("rutter", "using-compass")
    assert second.changed_paths == ()
