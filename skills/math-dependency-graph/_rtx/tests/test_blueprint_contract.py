"""Blueprint contract tests owned by the math-dependency-graph runtime."""

from __future__ import annotations

from pathlib import Path
import re

import yaml
from officina.dispatcher.direct_runtime import _resolve_host_dispatch_metadata


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_inventory_voyage_dispenser_accepts_implicit_default_initialization() -> None:
    doc_entrypoint = (
        REPOSITORY_ROOT
        / "skills/math-dependency-graph/instructions/inventory.md"
    )

    metadata = _resolve_host_dispatch_metadata(
        caller_skill="math-dependency-graph",
        target=(
            "math-dependency-graph._rtx.interface."
            "inventory-voyage-dispenser"
        ),
        target_version=4,
        args=[
            "initiate",
            "--doc-entrypoint",
            str(doc_entrypoint),
            "--chunk-count",
            "1",
        ],
        repository_config=REPOSITORY_ROOT / "officina.toml",
    )

    assert metadata.command == [
        "initiate",
        "--doc-entrypoint",
        str(doc_entrypoint),
        "--chunk-count",
        "1",
    ]


def test_inventory_voyage_dispenser_routes_run_prefix_options() -> None:
    """Dropping prefix flags at dispatch would collapse isolated runs globally."""

    doc_entrypoint = (
        REPOSITORY_ROOT
        / "skills/math-dependency-graph/instructions/inventory.md"
    )
    initiate = _resolve_host_dispatch_metadata(
        caller_skill="math-dependency-graph",
        target=(
            "math-dependency-graph._rtx.interface."
            "inventory-voyage-dispenser"
        ),
        target_version=4,
        args=[
            "initiate",
            "--run-prefix",
            "baseline",
            "--doc-entrypoint",
            str(doc_entrypoint),
            "--chunk-count",
            "1",
        ],
        repository_config=REPOSITORY_ROOT / "officina.toml",
    )
    listed = _resolve_host_dispatch_metadata(
        caller_skill="math-dependency-graph",
        target=(
            "math-dependency-graph._rtx.interface."
            "inventory-voyage-dispenser"
        ),
        target_version=4,
        args=["list", "--run-prefix", "baseline"],
        repository_config=REPOSITORY_ROOT / "officina.toml",
    )

    assert initiate.command == [
        "initiate",
        "--run-prefix",
        "baseline",
        "--doc-entrypoint",
        str(doc_entrypoint),
        "--chunk-count",
        "1",
    ]
    assert listed.command == ["list", "--run-prefix", "baseline"]


def test_inventory_voyage_dispenser_declares_its_exact_dependencies() -> None:
    blueprint = yaml.safe_load(
        (
            REPOSITORY_ROOT
            / "skills/math-dependency-graph/_rtx/blueprints/rtx-inventory-voyage-dispenser.yaml"
        ).read_text(encoding="utf-8")
    )

    assert [
        (entry["source"], entry["version"])
        for entry in blueprint["dependencies"]
    ] == [
        ("common.source.atomic-files", 1),
        ("rutter.source.engine", 3),
        ("rutter.source.model", 2),
        ("rutter.source.dispenser", 4),
        ("rutter.source.diagnostic", 3),
        (
            "math-dependency-graph._rtx.source.rtx-inventory-chunk-extractor",
            8,
        ),
    ]
    expected_interfaces = [
        {"interface": "common.interface.atomic-files", "version": 1},
        {"interface": "rutter.interface.bound-operations", "version": 6},
        {"interface": "rutter.interface.model", "version": 2},
        {"interface": "rutter.interface.dispenser", "version": 4},
        {
            "interface": (
                "math-dependency-graph._rtx.source."
                "rtx-inventory-chunk-extractor.interface."
                "scripts-extract-inventory-chunks"
            ),
            "version": 1,
        },
    ]
    assert blueprint["uses_interfaces"] == expected_interfaces
    interface = blueprint["interfaces"][
        "math-dependency-graph._rtx.source."
        "rtx-inventory-voyage-dispenser.interface.inventory-voyages"
    ]
    assert interface["uses_interfaces"] == expected_interfaces


def test_runtime_module_does_not_own_persisted_voyage_state() -> None:
    """Owning generated run state as source makes valid executions fail validation."""

    blueprint = yaml.safe_load(
        (
            REPOSITORY_ROOT
            / "skills/math-dependency-graph/_rtx/blueprint.yaml"
        ).read_text(encoding="utf-8")
    )
    patterns = [re.compile(pattern) for pattern in blueprint["content"]]

    assert any(
        pattern.fullmatch("_inventory_pipeline/_voyage_dispenser.py")
        for pattern in patterns
    )
    assert not any(
        pattern.fullmatch("_inventory_pipeline/artifacts/inventory-chunks.json")
        for pattern in patterns
    )
    assert not any(
        pattern.fullmatch(
            "_inventory_pipeline/voyages/debug/debug-voyage-1/reckoning.json"
        )
        for pattern in patterns
    )
