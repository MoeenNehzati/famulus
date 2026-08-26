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
        target_version=11,
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
        target_version=11,
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
        target_version=11,
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


def test_inventory_voyage_dispenser_routes_forced_release() -> None:
    """The public route must preserve an explicit nonterminal deletion request."""

    metadata = _resolve_host_dispatch_metadata(
        caller_skill="math-dependency-graph",
        target=(
            "math-dependency-graph._rtx.interface."
            "inventory-voyage-dispenser"
        ),
        target_version=11,
        args=["release", "spark/r-abc123", "--force"],
        repository_config=REPOSITORY_ROOT / "officina.toml",
    )

    assert metadata.command == ["release", "spark/r-abc123", "--force"]


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
        ("rutter.source.dispenser", 5),
        ("rutter.source.diagnostic", 4),
        (
            "math-dependency-graph._rtx.source.rtx-inventory-chunk-extractor",
            9,
        ),
    ]
    expected_interfaces = [
        {"interface": "common.interface.atomic-files", "version": 1},
        {"interface": "rutter.interface.bound-operations", "version": 6},
        {"interface": "rutter.interface.model", "version": 2},
        {"interface": "rutter.interface.dispenser", "version": 5},
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
    assert interface["version"] == 11
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


def test_inventory_v37_and_voyage_v9_propagate_to_public_route() -> None:
    """A partial bump must not leave workers or Compass on old contracts."""

    skill_root = REPOSITORY_ROOT / "skills/math-dependency-graph"
    source = yaml.safe_load(
        (skill_root / "_rtx/blueprints/rtx-inventory-voyage-dispenser.yaml").read_text(
            encoding="utf-8"
        )
    )
    runtime_module = yaml.safe_load(
        (skill_root / "_rtx/blueprint.yaml").read_text(encoding="utf-8")
    )
    instruction = yaml.safe_load(
        (skill_root / "blueprints/instructions-inventory-voyages.yaml").read_text(
            encoding="utf-8"
        )
    )
    inventory_instruction = yaml.safe_load(
        (skill_root / "blueprints/instructions-inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    gateway = yaml.safe_load(
        (skill_root / "blueprints/gateway.yaml").read_text(encoding="utf-8")
    )
    module = yaml.safe_load(
        (skill_root / "blueprint.yaml").read_text(encoding="utf-8")
    )

    source_interface = source["interfaces"][
        "math-dependency-graph._rtx.source."
        "rtx-inventory-voyage-dispenser.interface.inventory-voyages"
    ]
    instruction_interface = instruction["interfaces"][
        "math-dependency-graph.source.instructions-inventory-voyages."
        "interface.inventory-voyages"
    ]
    assert source["version"] == 11
    assert source_interface["version"] == 11
    assert any(
        "archives" in warning["external-side-effect"]
        and "diagnostic reckoning" in warning["external-side-effect"]
        for warning in source_interface["contract"]["caller_warnings"]
    )
    assert runtime_module["version"] == 85
    assert inventory_instruction["version"] == 38
    assert inventory_instruction["interfaces"][
        "math-dependency-graph.source.instructions-inventory.interface.inventory"
    ]["version"] == 38
    assert instruction["version"] == 11
    assert instruction_interface["version"] == 11
    assert instruction["uses_interfaces"][0] == {
        "interface": "math-dependency-graph._rtx.interface.inventory-voyage-dispenser",
        "version": 11,
    }
    assert instruction_interface["uses_interfaces"][0] == {
        "interface": "math-dependency-graph._rtx.interface.inventory-voyage-dispenser",
        "version": 11,
    }
    assert gateway["version"] == 87
    assert gateway["interfaces"][
        "math-dependency-graph.source.gateway.interface.default"
    ]["version"] == 79
    assert {
        "interface": "math-dependency-graph.interface.inventory",
        "version": 38,
    } in gateway["uses_interfaces"]
    assert {
        "interface": "math-dependency-graph.interface.inventory-voyages",
        "version": 11,
    } in gateway["uses_interfaces"]
    assert module["version"] == 112
    assert module["namespace_exports"]["_rtx"]["version"] == 85
    assert module["namespace_exports"]["_rtx"]["surface"]["only"][
        "math-dependency-graph._rtx.interface.inventory-voyage-dispenser"
    ] == 11

    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert (
        "math-dependency-graph.source.gateway -> "
        "math-dependency-graph.interface.inventory-voyages@11"
    ) in skill_text
    assert (
        "math-dependency-graph.source.instructions-inventory-voyages -> "
        "math-dependency-graph._rtx.interface.inventory-voyage-dispenser@11"
    ) in skill_text
    assert (
        "math-dependency-graph.source.gateway -> "
        "math-dependency-graph.interface.inventory@38"
    ) in skill_text
