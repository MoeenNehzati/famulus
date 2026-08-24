"""Shared, scoped fixtures for the math-dependency-graph runtime tests."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SKILL_DIR = Path(__file__).resolve().parents[2]
extractor = importlib.import_module(
    "skills.math-dependency-graph._rtx._inventory_pipeline._chunk_extractor"
)


@pytest.fixture(scope="session")
def inventory_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (SKILL_DIR / "schemas" / "inventory.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def canonical_base_payload() -> dict[str, object]:
    converter = importlib.import_module(
        "skills.math-dependency-graph._rtx._semantic_pipeline._to_canonical_json"
    )
    return converter.load_base_payload()


@pytest.fixture
def inventory_run(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "run"
    root.mkdir()
    source = root / "main.md"
    source.write_text(
        "# First result\n" + ("first statement " * 500) + "\n"
        "# Second result\n" + ("second statement " * 500) + "\n",
        encoding="utf-8",
    )
    return source, root


@pytest.fixture(scope="module")
def inventory_gold_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("inventory-gold") / "gold.json"
    path.write_text(
        json.dumps(
            {
                "ir_version": 3,
                "chunk_id": "gold",
                "files": ["main.tex"],
                "nodes": [],
                "edges": [],
                "gaps": [],
            }
        ),
        encoding="utf-8",
    )
    return path
