#!/usr/bin/env python3
"""Contract tests for the skill-owned math dependency graph base payload."""

from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest
import yaml


RUNTIME_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = RUNTIME_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(RUNTIME_DIR))

from _graph_builder import load_base_payload, validate_math_payload  # noqa: E402
from officina.visualization.base_renderer import BaseRenderer  # noqa: E402


def test_inventory_instruction_contract_uses_only_public_iterator_source_delivery() -> None:
    """Reintroducing packet input would bypass the durable next/ack traversal contract."""

    inventory = yaml.safe_load(
        (RUNTIME_DIR.parent / "blueprints" / "instructions-inventory.yaml").read_text(
            encoding="utf-8"
        )
    )
    interface = inventory["interfaces"][
        "math-dependency-graph.source.instructions-inventory.interface.inventory"
    ]

    assert inventory["uses_interfaces"] == [
        {
            "interface": "math-dependency-graph._rtx.interface.scripts-next-inventory-unit",
            "version": 1,
        }
    ]
    assert interface["uses_interfaces"] == inventory["uses_interfaces"]
    assert "packet" not in interface["contract"]["arguments"]
    assert {
        item.get("path")
        for item in interface["contract"]["direct_io"]["reads"]
        if item.get("medium") == "local-filesystem"
    } == {"$skill/inventory.schema.json"}


def test_gateway_contract_routes_inventory_through_iterator_and_measured_diagnostics() -> None:
    """Dropping one facade dependency makes the documented control path uncallable."""

    gateway = yaml.safe_load(
        (RUNTIME_DIR.parent / "blueprints" / "gateway.yaml").read_text(
            encoding="utf-8"
        )
    )
    uses = {
        item["interface"]: item["version"] for item in gateway["uses_interfaces"]
    }

    assert uses["math-dependency-graph.interface.inventory"] == 30
    assert uses[
        "math-dependency-graph._rtx.interface.scripts-setup-inventory-iterator"
    ] == 2
    assert uses[
        "math-dependency-graph._rtx.interface.scripts-next-inventory-unit"
    ] == 1
    assert uses[
        "math-dependency-graph._rtx.interface.scripts-record-run-diagnostics"
    ] == 6


def test_base_payload_defines_the_complete_shared_math_vocabulary() -> None:
    base = load_base_payload()

    BaseRenderer().validate(base)

    assert [(item["id"], item.get("parent")) for item in base["categories"]] == [
        ("assumption", None),
        ("assumption:standing", "assumption"),
        ("assumption:local", "assumption"),
        ("setup", None),
        ("setup:definition", "setup"),
        ("setup:notation", "setup"),
        ("result", None),
        ("result:lemma", "result"),
        ("result:proposition", "result"),
        ("result:theorem", "result"),
        ("result:corollary", "result"),
        ("exposition", None),
        ("exposition:remark", "exposition"),
        ("exposition:example", "exposition"),
        ("external-result", None),
    ]
    category_by_id = {item["id"]: item for item in base["categories"]}
    assert category_by_id["result"]["shape"] == "roundrect"
    assert category_by_id["result:theorem"]["shape"] == "roundrect"
    assert category_by_id["assumption:standing"]["color"] == "#c0392b"
    assert category_by_id["assumption:local"]["color"] == "#2e86c1"
    assert category_by_id["exposition:remark"]["color"] == "#616a6b"
    assert category_by_id["exposition:example"]["color"] == "#117864"
    assert [item["id"] for item in base["edge_categories"]] == [
        "supports",
        "illustrated-by",
    ]
    provenance = base["ui"]["edge_presentation"]["facets"][0]
    assert provenance["field"] == "implicit"
    assert [variant["style"]["line_pattern"] for variant in provenance["variants"]] == [
        "solid",
        "dashed",
    ]


def test_math_payload_accepts_author_visible_family_extension() -> None:
    payload = deepcopy(load_base_payload())
    payload["categories"].append(
        {
            "id": "result:fact",
            "parent": "result",
            "label": "Fact",
            "shape": "roundrect",
            "color": "#2874a6",
        }
    )
    payload["entities"] = [
        {
            "id": "fact-one",
            "type": "result",
            "kind": "fact",
            "category": "result:fact",
            "short_title": "Fact 1",
            "position": 0,
            "source": "explicit",
            "connects_to": [],
        }
    ]

    validate_math_payload(payload)


def test_math_payload_rejects_family_extension_with_different_shape() -> None:
    payload = deepcopy(load_base_payload())
    payload["categories"].append(
        {
            "id": "result:fact",
            "parent": "result",
            "label": "Fact",
            "shape": "diamond",
            "color": "#2874a6",
        }
    )

    with pytest.raises(ValueError, match="must use parent shape 'roundrect'"):
        validate_math_payload(payload)


def test_math_payload_rejects_redefinition_of_base_category() -> None:
    payload = deepcopy(load_base_payload())
    payload["categories"][0]["shape"] = "ellipse"

    with pytest.raises(ValueError, match="redefines base category 'assumption'"):
        validate_math_payload(payload)


def test_math_payload_rejects_edge_outside_shared_vocabulary() -> None:
    payload = deepcopy(load_base_payload())
    payload["entities"] = [
        {
            "id": "a",
            "type": "assumption",
            "kind": "standing",
            "category": "assumption:standing",
            "short_title": "A",
            "position": 0,
            "source": "explicit",
            "connects_to": [{"to": "b", "type": "assumption-for"}],
        },
        {
            "id": "b",
            "type": "result",
            "kind": "theorem",
            "category": "result:theorem",
            "short_title": "B",
            "position": 1,
            "source": "explicit",
            "connects_to": [],
        },
    ]

    with pytest.raises(ValueError, match="unsupported mathematical edge type"):
        validate_math_payload(payload)


def test_math_payload_rejects_nonmonotone_or_duplicate_source_positions() -> None:
    payload = deepcopy(load_base_payload())
    payload["entities"] = [
        {
            "id": "later",
            "type": "setup",
            "kind": "definition",
            "category": "setup:definition",
            "short_title": "Later",
            "position": 2,
            "source": "explicit",
            "connects_to": [],
        },
        {
            "id": "earlier",
            "type": "setup",
            "kind": "notation",
            "category": "setup:notation",
            "short_title": "Earlier",
            "position": 1,
            "source": "explicit",
            "connects_to": [],
        },
    ]

    with pytest.raises(ValueError, match="strictly increasing source-order positions"):
        validate_math_payload(payload)

    payload["entities"][0]["position"] = 1
    with pytest.raises(ValueError, match="strictly increasing source-order positions"):
        validate_math_payload(payload)
