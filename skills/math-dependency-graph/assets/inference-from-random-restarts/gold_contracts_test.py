#!/usr/bin/env python3
"""Local contracts for the uncommitted stage-specific benchmark results."""

from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "results"
compiler = importlib.import_module(
    "skills.math-dependency-graph._rtx._semantic_pipeline._to_canonical_json"
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_inventory_gold_is_schema_valid_and_retains_every_explicit_proof() -> None:
    schema = _read(ROOT / "schemas" / "inventory.schema.json")
    gold = _read(GOLD / "inventory-gold.json")

    Draft202012Validator(schema).validate(gold)

    proof_ids = {
        node["local_id"] for node in gold["nodes"] if node["type_hint"] == "proof"
    }
    proves = [edge for edge in gold["edges"] if edge["type"] == "proves"]
    node_ids = {node["local_id"] for node in gold["nodes"]}
    assert len(proof_ids) == 29
    assert len(proves) == 29
    assert {edge["from"]["local_node"] for edge in proves} == proof_ids
    assert all(
        endpoint["local_node"] in node_ids
        for edge in gold["edges"]
        for endpoint in (edge["from"], edge["to"])
    )
    nodes = {node["local_id"]: node for node in gold["nodes"]}
    for edge in proves:
        proof_node = nodes[edge["from"]["local_node"]]
        proof = proof_node["statement_location"]
        result = nodes[edge["to"]["local_node"]]["statement_location"]
        assert proof[0] != result[0] or proof[1] > result[2] or result[1] > proof[2]
        if proof_node.get("environment") == "proof":
            assert edge["location"] != proof
        assert edge["location"][2] - edge["location"][1] <= 3

    explicit_proofs = [
        node
        for node in gold["nodes"]
        if node["type_hint"] == "proof" and node.get("environment") == "proof"
    ]
    sketches = [
        node
        for node in gold["nodes"]
        if node["type_hint"] == "proof" and node.get("environment") != "proof"
    ]
    assert len(explicit_proofs) == 27
    assert len(sketches) == 2
    assert all(node["summary"].startswith("Formal proof ") for node in explicit_proofs)
    assert all(node["summary"].startswith("Sketch ") for node in sketches)
    assert {node["local_id"] for node in explicit_proofs if "visible_title" in node} == {
        "n65",
        "n74",
        "n77",
        "n79",
        "n80",
        "n81",
        "n82",
    }


def test_inventory_gold_uses_source_faithful_entities_edges_and_tags() -> None:
    gold = _read(GOLD / "inventory-gold.json")
    nodes = {node["local_id"]: node for node in gold["nodes"]}
    edges = {edge["local_id"]: edge for edge in gold["edges"]}

    assert not {"n12", "n13", "n14", "n15", "n16", "n58"} & nodes.keys()
    assert nodes["n57"]["type_hint"] == "setup"
    assert "external_identity" not in nodes["n57"]
    assert nodes["n57"]["statement_location"] == [0, 349, 354]
    assert nodes["n19"]["statement_location"] == [1, 43, 44]
    assert nodes["n21"]["statement_location"] == [1, 55, 63]
    assert nodes["n56"]["scope_hint"]["ends_at"] == [3, 59, 59]
    assert nodes["n23"]["type_hint"] == "exposition"
    assert nodes["n1"]["external_identity"] == {
        "name": "Nagurney projected-system well-posedness theorem",
        "citation_key": "nagurney2012projected",
        "theorem": "2.5",
    }
    assert nodes["n44"]["external_identity"]["citation_key"] == "gautschi1959some"
    assert nodes["n53"]["external_identity"]["citation_key"] == "skerlos2010fixed"
    assert nodes["n31"]["external_identity"]["citation_key"] == "GuilleminPollack1974"
    assert {nodes[node_id]["environment"] for node_id in ("n10", "n11", "n20", "n40", "n41", "n43", "n48", "n52")} <= {
        "lemma",
        "proposition",
        "theorem",
    }
    assert {node.get("visible_title") for node in nodes.values()} >= {
        "Bayes' rule",
        "Gamma integral",
    }
    assert edges["d1"]["basis"] == "explicit-reference"
    assert edges["d1"]["reference"]["locator"] == {
        "citation_key": "nagurney2012projected"
    }
    assert edges["d53"]["reference"]["locator"] == {
        "citation_key": "gautschi1959some"
    }
    assert edges["d61"]["reference"]["locator"] == {
        "citation_key": "skerlos2010fixed"
    }
    assert any(
        edge["from"] == {"local_node": "n31"}
        and edge["to"] == {"local_node": "n29"}
        and edge["basis"] == "explicit-reference"
        and edge["reference"]["locator"]
        == {"citation_key": "GuilleminPollack1974"}
        for edge in gold["edges"]
    )
    assert edges["d64"]["from"] == {"local_node": "n9"}
    assert edges["d64"]["to"] == {"local_node": "n66"}
    assert edges["d64"]["location"] == [0, 273, 274]
    assert all(edge["description"] != "Accepted direct dependency." for edge in gold["edges"])
    assert all("inventory:" not in edge["description"] and "gold:" not in edge["description"] for edge in gold["edges"])


def _projected_pooled_inventory(inventory_gold: dict, semantic_gold: dict) -> dict:
    """Project audited inventory gold into the compiler's pooled provenance shape."""

    nodes = {node["local_id"]: node for node in inventory_gold["nodes"]}
    candidate_ids = semantic_gold["inventory"]["candidate_ids"]
    candidate_to_entity = {
        candidate_id.rsplit(":", 1)[-1]: entity["id"]
        for entity in semantic_gold["entities"]
        for candidate_id in entity["candidate_ids"]
    }
    proved_result = {
        edge["from"]["local_node"]: edge["to"]["local_node"]
        for edge in inventory_gold["edges"]
        if edge["type"] == "proves"
    }

    evidence = []
    candidates = []
    for candidate_id in candidate_ids:
        local_id = candidate_id.rsplit(":", 1)[-1]
        node = nodes[local_id]
        evidence_id = f"{candidate_id}::statement"
        evidence.append(
            {
                "id": evidence_id,
                "location": node["statement_location"],
                "role": "statement",
            }
        )
        candidate = {
            key: value
            for key, value in node.items()
            if key not in {"local_id", "statement_location"}
        }
        candidate.update(
            {
                "id": candidate_id,
                "location": node["statement_location"],
                "evidence_ids": [evidence_id],
            }
        )
        if node["type_hint"] == "external-result":
            candidate["retention_reasons"] = [
                "named-indispensable-external-result"
            ]
        candidates.append(candidate)

    def semantic_endpoint(local_id: str) -> str | None:
        return candidate_to_entity.get(
            local_id,
            candidate_to_entity.get(proved_result.get(local_id, "")),
        )

    raw_edges: dict[tuple[str | None, str | None, str], list[dict]] = {}
    for edge in inventory_gold["edges"]:
        if edge["type"] == "proves":
            continue
        key = (
            semantic_endpoint(edge["from"]["local_node"]),
            semantic_endpoint(edge["to"]["local_node"]),
            edge["type"],
        )
        raw_edges.setdefault(key, []).append(edge)

    roles = {
        "explicit-reference": "explicit-reference",
        "explicit-prose": "dependency-prose",
        "proof-use": "proof-use",
        "mathematical-inference": "dependency-prose",
    }
    for relationship in semantic_gold["relationships"]:
        key = (relationship["from"], relationship["to"], relationship["type"])
        matches = raw_edges.get(key, [])
        exact = [
            edge
            for edge in matches
            if edge["description"] == relationship["description"]
        ]
        assert len(exact) == 1 or (not exact and len(matches) == 1), key
        source_edge = (exact or matches)[0]
        for evidence_id in relationship["evidence_ids"]:
            evidence.append(
                {
                    "id": evidence_id,
                    "location": source_edge["location"],
                    "role": roles[source_edge["basis"]],
                }
            )

    return {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": inventory_gold["files"],
        "evidence": evidence,
        "references": [],
        "candidates": candidates,
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }


def _compile_final_gold(inventory_gold: dict, semantic_gold: dict) -> dict:
    final_gold = compiler.compile_semantic_graph(
        semantic_gold,
        compiler.load_base_payload(),
        _projected_pooled_inventory(inventory_gold, semantic_gold),
    )
    final_gold["metadata"]["semantic_ir_sha256"] = hashlib.sha256(
        (GOLD / "semantic-gold.json").read_bytes()
    ).hexdigest()
    return final_gold


def test_semantic_gold_is_schema_valid_and_normalized_proof_free() -> None:
    schema = _read(ROOT / "schemas" / "semantic-graph.schema.json")
    gold = _read(GOLD / "semantic-gold.json")

    Draft202012Validator(schema).validate(gold)
    compiler.validate_normalized_semantic_profile(gold)

    inventory_gold = _read(GOLD / "inventory-gold.json")
    assert gold["files"] == inventory_gold["files"]
    assert all(
        entity["statement_location"][0] < len(gold["files"])
        for entity in gold["entities"]
    )
    assert all(entity["type"] != "proof" for entity in gold["entities"])
    assert all(edge["type"] != "proves" for edge in gold["relationships"])


def test_semantic_gold_has_source_faithful_normalized_entities_and_tags() -> None:
    gold = _read(GOLD / "semantic-gold.json")
    entities = {entity["id"]: entity for entity in gold["entities"]}

    assert all("kind" not in entity for entity in entities.values() if entity["type"] == "external-result")
    assert entities["gold:prev.ass-kkq"]["kind"] == "standing"
    assert entities["gold:prev.ass-convex"]["kind"] == "standing"
    assert entities["gold:bayes.ass-poly-density"]["kind"] == "standing"
    assert entities["gold:prev.boundary-decomposition"]["type"] == "exposition"
    assert entities["gold:prev.boundary-decomposition"]["kind"] == "remark"
    assert entities["gold:app.smooth-domain"]["kind"] == "definition"
    assert entities["gold:app.banach"]["kind"] == "definition"
    assert entities["gold:dyn.finite-rto-roots"]["statement_location"] == [1, 43, 44]
    assert not {
        "gold:dyn.terminal-partition",
        "gold:dyn.infinite-survival",
        "gold:dyn.limsup-liminf",
        "gold:dyn.limit-map",
        "gold:dyn.solver-pieces",
    } & entities.keys()
    assert {"gold:bayes.bayes-rule", "gold:bayes.gamma-integral"} <= entities.keys()
    assert all(relationship["description"] != "Accepted direct dependency." for relationship in gold["relationships"])
    assert {
        (relationship["from"], relationship["to"])
        for relationship in gold["relationships"]
    } >= {
        ("gold:dyn.regularity", "gold:dyn.measurability-result"),
        ("gold:prev.preimage", "gold:app.smooth-domain"),
        ("gold:prev.sard", "gold:prev.parametric-regularity"),
        ("gold:bayes.bayes-rule", "gold:bayes.spike-setup"),
        ("gold:bayes.gamma-integral", "gold:bayes.poly-tail"),
    }


def test_final_gold_is_the_canonical_compilation_of_semantic_gold() -> None:
    inventory_gold = _read(GOLD / "inventory-gold.json")
    semantic_gold = _read(GOLD / "semantic-gold.json")
    final_gold = _read(GOLD / "final-gold.json")

    assert final_gold == _compile_final_gold(inventory_gold, semantic_gold)
    compiler.BaseRenderer().validate(final_gold)
