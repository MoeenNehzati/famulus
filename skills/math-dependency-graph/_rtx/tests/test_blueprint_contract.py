"""Blueprint contract tests owned by the math-dependency-graph runtime."""

from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


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
        ("rutter.source.dispenser", 3),
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
        {"interface": "rutter.interface.dispenser", "version": 3},
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
