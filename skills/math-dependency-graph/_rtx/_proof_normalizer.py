#!/usr/bin/env python3
"""Collapse transitional proof entities into strict proof-free semantic IR."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


SKILL_DIR = Path(__file__).resolve().parents[1]
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_QUALIFIED_ID_RE = {
    "evidence": re.compile(r"^[^:]+::e[1-9][0-9]*$"),
    "hint": re.compile(r"^[^:]+::h[1-9][0-9]*$"),
    "reference": re.compile(r"^[^:]+::r[1-9][0-9]*$"),
}
_CONFIDENCE_ORDER = {
    "Verified": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3,
    "Likely": 4,
    "Unknown": 5,
}


def _require_keys(record: object, required: set[str], allowed: set[str], label: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError(f"{label} must be an object")
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"{label} is missing required field: {missing[0]}")
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise ValueError(f"{label} has unknown field: {unknown[0]}")
    return record


def _require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must be a nonempty identifier")
    return value


def _require_handle_list(value: object, kind: str, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{label} must be {'a nonempty ' if nonempty else 'an '}array")
    if not all(isinstance(item, str) and _QUALIFIED_ID_RE[kind].fullmatch(item) for item in value):
        raise ValueError(f"{label} has malformed {kind} handle")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} has duplicate {kind} handle")
    return value


def _validate_relationship(relationship: object, label: str) -> dict:
    record = _require_keys(
        relationship,
        {"from", "to", "type", "description", "hint_ids", "evidence_ids", "implicit"},
        {"from", "to", "type", "description", "hint_ids", "evidence_ids", "reference_ids", "implicit", "confidence"},
        label,
    )
    _require_identifier(record["from"], f"{label}.from")
    _require_identifier(record["to"], f"{label}.to")
    if record["type"] not in {"supports", "illustrated-by", "proves"}:
        raise ValueError(f"{label} has unsupported relationship type")
    if not isinstance(record["description"], str) or not record["description"]:
        raise ValueError(f"{label}.description must be nonempty")
    _require_handle_list(record["hint_ids"], "hint", f"{label}.hint_ids")
    _require_handle_list(record["evidence_ids"], "evidence", f"{label}.evidence_ids", nonempty=True)
    if "reference_ids" in record:
        _require_handle_list(record["reference_ids"], "reference", f"{label}.reference_ids")
    if not isinstance(record["implicit"], bool):
        raise ValueError(f"{label}.implicit must be boolean")
    if "confidence" in record and record["confidence"] not in _CONFIDENCE_ORDER:
        raise ValueError(f"{label}.confidence is invalid")
    return record


def _validate_semantic_ir(payload: object) -> dict:
    record = _require_keys(
        payload,
        {"ir_version", "document", "inventory", "entities", "exclusions", "unresolved_resolutions", "relationships", "hint_decisions", "reference_decisions", "gap_decisions", "gaps"},
        {"ir_version", "document", "inventory", "entities", "exclusions", "unresolved_resolutions", "relationships", "hint_decisions", "reference_decisions", "gap_decisions", "gaps", "edgeless_justification"},
        "transitional semantic IR",
    )
    if record["ir_version"] != 2:
        raise ValueError("transitional semantic IR has unsupported version")
    document = _require_keys(record["document"], {"source_file"}, {"title", "source_file"}, "semantic document")
    if not isinstance(document["source_file"], str) or not document["source_file"]:
        raise ValueError("semantic document source_file must be nonempty")
    inventory = _require_keys(record["inventory"], {"candidate_ids", "candidate_count"}, {"candidate_ids", "candidate_count"}, "semantic inventory")
    if not isinstance(inventory["candidate_ids"], list) or not isinstance(inventory["candidate_count"], int):
        raise ValueError("semantic inventory is malformed")
    if not all(isinstance(item, str) and item for item in inventory["candidate_ids"]):
        raise ValueError("semantic inventory has malformed candidate id")
    for name in ("entities", "exclusions", "unresolved_resolutions", "relationships", "hint_decisions", "reference_decisions", "gap_decisions", "gaps"):
        if not isinstance(record[name], list):
            raise ValueError(f"transitional semantic IR {name} must be an array")
    entity_ids: set[str] = set()
    for index, entity in enumerate(record["entities"]):
        entity_record = _require_keys(
            entity,
            {"candidate_ids", "id", "type", "short_title", "description", "source"},
            {"candidate_ids", "id", "type", "kind", "category_label", "short_title", "title", "description", "source", "ref"},
            f"semantic entity {index}",
        )
        _require_identifier(entity_record["id"], f"semantic entity {index}.id")
        if entity_record["id"] in entity_ids:
            raise ValueError(f"transitional semantic IR has duplicate entity id: {entity_record['id']}")
        entity_ids.add(entity_record["id"])
        if not isinstance(entity_record["candidate_ids"], list) or len(entity_record["candidate_ids"]) != len(set(entity_record["candidate_ids"])):
            raise ValueError(f"semantic entity {index}.candidate_ids is malformed")
        if not isinstance(entity_record["type"], str) or not entity_record["type"]:
            raise ValueError(f"semantic entity {index}.type must be nonempty")
        if entity_record["type"] == "proof" and entity_record.get("kind") not in {"formal", "informal", "sketch"}:
            raise ValueError(f"semantic proof entity {entity_record['id']} has invalid kind")
        if not isinstance(entity_record["short_title"], str) or not entity_record["short_title"]:
            raise ValueError(f"semantic entity {index}.short_title must be nonempty")
        if not isinstance(entity_record["description"], str) or not entity_record["description"]:
            raise ValueError(f"semantic entity {index}.description must be nonempty")
        if entity_record["source"] not in {"explicit", "inferred"}:
            raise ValueError(f"semantic entity {index}.source is invalid")
    for index, relationship in enumerate(record["relationships"]):
        relationship = _validate_relationship(relationship, f"semantic relationship {index}")
        if relationship["from"] not in entity_ids or relationship["to"] not in entity_ids:
            raise ValueError(f"semantic relationship {index} has unknown endpoint")
        if relationship["from"] == relationship["to"]:
            raise ValueError(f"semantic relationship {index} is a self-edge")
    return record


def _stable_union(records: Iterable[Iterable[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for values in records:
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def _most_conservative_confidence(relationships: list[dict]) -> str | None:
    supplied = [item["confidence"] for item in relationships if "confidence" in item]
    return max(supplied, key=lambda item: _CONFIDENCE_ORDER[item]) if supplied else None


def validate_normalized_semantic_profile(payload: object) -> None:
    """Reject transitional proof records before canonical compilation."""

    if not isinstance(payload, dict):
        raise ValueError("normalized semantic profile requires an object")
    for entity in payload.get("entities", []):
        if isinstance(entity, dict) and entity.get("type") == "proof":
            raise ValueError(
                f"normalized semantic profile rejects transitional proof entity: {entity.get('id', '<unknown>')}"
            )
    for relationship in payload.get("relationships", []):
        if isinstance(relationship, dict) and relationship.get("type") == "proves":
            raise ValueError("normalized semantic profile rejects transitional proves relationship")


def _validate_decisions(decisions: object, proof_ids: set[str]) -> dict[str, dict]:
    if not isinstance(decisions, dict) or decisions.get("document_kind") != "proof-normalization-decisions":
        raise ValueError("proof-normalization decisions must be a decisions document")
    _require_keys(decisions, {"document_kind", "ir_version", "decisions"}, {"document_kind", "ir_version", "decisions"}, "proof-normalization decisions")
    if decisions["ir_version"] != 1 or not isinstance(decisions["decisions"], list):
        raise ValueError("proof-normalization decisions have invalid version or records")
    indexed: dict[str, dict] = {}
    for index, decision in enumerate(decisions["decisions"]):
        decision = _require_keys(decision, {"proof_id", "disposition", "reason"}, {"proof_id", "disposition", "bundle_id", "target_id", "reason"}, f"proof decision {index}")
        _require_identifier(decision["proof_id"], f"proof decision {index}.proof_id")
        if decision["disposition"] not in {"accepted", "excluded"}:
            raise ValueError(f"proof decision {index} has invalid disposition")
        if not isinstance(decision["reason"], str) or not decision["reason"]:
            raise ValueError(f"proof decision {index}.reason must be nonempty")
        if decision["disposition"] == "accepted":
            _require_identifier(decision.get("bundle_id"), f"proof decision {index}.bundle_id")
            _require_identifier(decision.get("target_id"), f"proof decision {index}.target_id")
        elif "bundle_id" in decision or "target_id" in decision:
            raise ValueError(f"excluded proof decision {index} must not name bundle or target")
        proof_id = decision["proof_id"]
        if proof_id not in proof_ids:
            raise ValueError(f"unknown proof normalization decision: {proof_id}")
        if proof_id in indexed:
            raise ValueError(f"duplicate proof normalization decision: {proof_id}")
        indexed[proof_id] = decision
    missing = sorted(proof_ids - set(indexed))
    if missing:
        raise ValueError(f"unaccounted proof normalization decision: {missing[0]}")
    return indexed


def _relationship_snapshot(relationship: dict) -> dict:
    return deepcopy(relationship)


def _validate_proof_ownership(
    semantic_ir: dict,
    proof_ids: set[str],
    decisions: dict[str, dict],
) -> dict[str, dict]:
    entities = {entity["id"]: entity for entity in semantic_ir["entities"]}
    outgoing: dict[str, list[dict]] = {proof_id: [] for proof_id in proof_ids}
    for relationship in semantic_ir["relationships"]:
        source = relationship["from"]
        target = relationship["to"]
        if relationship["type"] == "proves":
            if source not in proof_ids:
                raise ValueError(f"proves relationship has non-proof source: {source}")
            if target not in entities:
                raise ValueError(f"proof target is absent: {target}")
            if target in proof_ids:
                raise ValueError(f"proof target is transitional proof: {target}")
            if entities[target]["type"] != "result":
                raise ValueError(f"proof target is not an eligible result: {target}")
            if source == target:
                raise ValueError(f"proof has self-target: {source}")
            outgoing[source].append(relationship)
        elif source in proof_ids:
            raise ValueError(f"unrelated outgoing proof edge: {source} -> {target}")

    accepted: dict[str, dict] = {}
    for proof_id, decision in decisions.items():
        edges = outgoing[proof_id]
        if decision["disposition"] == "excluded":
            continue
        if len(edges) != 1:
            raise ValueError(f"accepted proof must have exactly one proves target: {proof_id}")
        edge = edges[0]
        if edge["to"] != decision["target_id"]:
            raise ValueError(f"proof decision target disagrees with proves relationship: {proof_id}")
        accepted[proof_id] = edge
    return accepted


def _merge_relationships(routes: list[dict]) -> tuple[list[dict], list[dict]]:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    order: list[tuple[str, str, str]] = []
    for route in routes:
        relationship = route["relationship"]
        key = (relationship["from"], relationship["to"], relationship["type"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(route)

    normalized: list[dict] = []
    report: list[dict] = []
    for key in order:
        group = grouped[key]
        relationships = [route["relationship"] for route in group]
        direct = [route for route in group if "proof_id" not in route]
        selected_description = (direct[0] if direct else group[0])["relationship"]["description"]
        merged = {
            "from": key[0],
            "to": key[1],
            "type": key[2],
            "description": selected_description,
            "hint_ids": _stable_union(item["hint_ids"] for item in relationships),
            "evidence_ids": _stable_union(item["evidence_ids"] for item in relationships),
            "implicit": all(item["implicit"] for item in relationships),
        }
        reference_ids = _stable_union(item.get("reference_ids", []) for item in relationships)
        if reference_ids:
            merged["reference_ids"] = reference_ids
        confidence = _most_conservative_confidence(relationships)
        if confidence is not None:
            merged["confidence"] = confidence
        normalized.append(merged)
        report.append(
            {
                "from": key[0],
                "to": key[1],
                "type": key[2],
                "proof_ids": _stable_union(
                    [[route["proof_id"]] for route in group if "proof_id" in route]
                ),
                "bundle_ids": _stable_union(
                    [[route["bundle_id"]] for route in group if "bundle_id" in route]
                ),
                "source_relationships": deepcopy(group),
            }
        )
    return normalized, report


def _validate_provenance_report(report: object) -> None:
    record = _require_keys(
        report,
        {"document_kind", "ir_version", "proof_entities", "bundles", "relationships", "exclusions"},
        {"document_kind", "ir_version", "proof_entities", "bundles", "relationships", "exclusions", "compiler_inventory"},
        "proof-normalization report",
    )
    if record["document_kind"] != "proof-normalization-report" or record["ir_version"] != 1:
        raise ValueError("proof-normalization report has unsupported identity")
    for name in ("proof_entities", "bundles", "relationships", "exclusions"):
        if not isinstance(record[name], list):
            raise ValueError(f"proof-normalization report {name} must be an array")
    for index, entity in enumerate(record["proof_entities"]):
        item = _require_keys(
            entity,
            {"candidate_ids", "id", "type", "kind", "short_title", "description", "source"},
            {
                "candidate_ids", "id", "type", "kind", "category_label", "short_title",
                "title", "description", "source", "ref",
            },
            f"proof report entity {index}",
        )
        if item["type"] != "proof" or item["kind"] not in {"formal", "informal", "sketch"}:
            raise ValueError(f"proof report entity {index} has invalid proof identity")
        _require_identifier(item["id"], f"proof report entity {index}.id")
        if not isinstance(item["candidate_ids"], list) or len(item["candidate_ids"]) != len(set(item["candidate_ids"])):
            raise ValueError(f"proof report entity {index}.candidate_ids is malformed")
        for candidate_id in item["candidate_ids"]:
            _require_identifier(candidate_id, f"proof report entity {index}.candidate_id")
        for name in ("short_title", "description"):
            if not isinstance(item[name], str) or not item[name]:
                raise ValueError(f"proof report entity {index}.{name} must be nonempty")
        if item["source"] not in {"explicit", "inferred"}:
            raise ValueError(f"proof report entity {index}.source is invalid")
        for name in ("category_label", "title", "ref"):
            if name in item and (not isinstance(item[name], str) or not item[name]):
                raise ValueError(f"proof report entity {index}.{name} must be nonempty")
    for index, bundle in enumerate(record["bundles"]):
        item = _require_keys(bundle, {"bundle_id", "target_id", "proof_ids", "proves_relationships"}, {"bundle_id", "target_id", "proof_ids", "proves_relationships"}, f"proof report bundle {index}")
        _require_identifier(item["bundle_id"], f"proof report bundle {index}.bundle_id")
        _require_identifier(item["target_id"], f"proof report bundle {index}.target_id")
        if not isinstance(item["proof_ids"], list) or not item["proof_ids"]:
            raise ValueError(f"proof report bundle {index}.proof_ids must be nonempty")
        for proof_id in item["proof_ids"]:
            _require_identifier(proof_id, f"proof report bundle {index}.proof_id")
        for relationship in item["proves_relationships"]:
            _validate_relationship(relationship, f"proof report bundle {index} proves relationship")
    for index, relationship in enumerate(record["relationships"]):
        item = _require_keys(relationship, {"from", "to", "type", "proof_ids", "bundle_ids", "source_relationships"}, {"from", "to", "type", "proof_ids", "bundle_ids", "source_relationships"}, f"proof report relationship {index}")
        _require_identifier(item["from"], f"proof report relationship {index}.from")
        _require_identifier(item["to"], f"proof report relationship {index}.to")
        if item["type"] not in {"supports", "illustrated-by"}:
            raise ValueError(f"proof report relationship {index} has invalid type")
        for name in ("proof_ids", "bundle_ids", "source_relationships"):
            if not isinstance(item[name], list):
                raise ValueError(f"proof report relationship {index}.{name} must be an array")
        for route in item["source_relationships"]:
            route_record = _require_keys(route, {"relationship"}, {"relationship", "proof_id", "bundle_id"}, f"proof report relationship {index} route")
            _validate_relationship(route_record["relationship"], f"proof report relationship {index} route relationship")
            for name in ("proof_id", "bundle_id"):
                if name in route_record:
                    _require_identifier(route_record[name], f"proof report relationship {index} route {name}")
    for index, exclusion in enumerate(record["exclusions"]):
        item = _require_keys(exclusion, {"proof_id", "reason", "incident_relationships"}, {"proof_id", "reason", "incident_relationships"}, f"proof report exclusion {index}")
        _require_identifier(item["proof_id"], f"proof report exclusion {index}.proof_id")
        if not isinstance(item["reason"], str) or not item["reason"]:
            raise ValueError(f"proof report exclusion {index}.reason must be nonempty")
        if not isinstance(item["incident_relationships"], list):
            raise ValueError(f"proof report exclusion {index}.incident_relationships must be an array")
        for relationship in item["incident_relationships"]:
            _validate_relationship(relationship, f"proof report exclusion {index} relationship")
    if "compiler_inventory" in record:
        compiler_inventory = _require_keys(
            record["compiler_inventory"],
            {
                "removed_proof_candidate_ids", "removed_proof_hint_ids",
                "removed_proof_reference_ids", "projected_candidate_ids",
            },
            {
                "removed_proof_candidate_ids", "removed_proof_hint_ids",
                "removed_proof_reference_ids", "projected_candidate_ids",
            },
            "proof report compiler inventory",
        )
        for name in ("removed_proof_candidate_ids", "projected_candidate_ids"):
            values = compiler_inventory[name]
            if not isinstance(values, list) or len(values) != len(set(values)):
                raise ValueError(f"proof report compiler inventory {name} must be a unique array")
            for value in values:
                _require_identifier(value, f"proof report compiler inventory {name}")
        _require_handle_list(
            compiler_inventory["removed_proof_hint_ids"],
            "hint",
            "proof report compiler inventory removed_proof_hint_ids",
        )
        _require_handle_list(
            compiler_inventory["removed_proof_reference_ids"],
            "reference",
            "proof report compiler inventory removed_proof_reference_ids",
        )


def normalize_proof_entities(semantic_ir: object, decisions: object) -> tuple[dict, dict]:
    """Apply complete reconciliation decisions without making semantic judgments."""

    semantic_ir = _validate_semantic_ir(semantic_ir)
    entities = semantic_ir["entities"]
    entity_ids = [entity["id"] for entity in entities]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("transitional semantic IR has duplicate entity ids")
    proof_ids = {entity["id"] for entity in entities if entity["type"] == "proof"}
    indexed_decisions = _validate_decisions(decisions, proof_ids)
    proves_by_proof = _validate_proof_ownership(semantic_ir, proof_ids, indexed_decisions)

    routes: list[dict] = []
    excluded_incident: dict[str, list[dict]] = {
        proof_id: []
        for proof_id, decision in indexed_decisions.items()
        if decision["disposition"] == "excluded"
    }
    for relationship in semantic_ir["relationships"]:
        source = relationship["from"]
        target = relationship["to"]
        incident_proofs = [identifier for identifier in (source, target) if identifier in proof_ids]
        if not incident_proofs:
            routes.append({"relationship": _relationship_snapshot(relationship)})
            continue
        for proof_id in incident_proofs:
            if proof_id in excluded_incident:
                excluded_incident[proof_id].append(_relationship_snapshot(relationship))
        if relationship["type"] == "proves":
            continue
        if target in proof_ids and indexed_decisions[target]["disposition"] == "accepted":
            decision = indexed_decisions[target]
            redirected = _relationship_snapshot(relationship)
            redirected["to"] = decision["target_id"]
            if redirected["from"] == redirected["to"]:
                raise ValueError(f"proof normalization would create a self-edge: {redirected['from']}")
            routes.append(
                {
                    "relationship": redirected,
                    "proof_id": target,
                    "bundle_id": decision["bundle_id"],
                }
            )

    normalized_relationships, relationship_report = _merge_relationships(routes)
    normalized = deepcopy(semantic_ir)
    normalized["entities"] = [entity for entity in entities if entity["id"] not in proof_ids]
    normalized["relationships"] = normalized_relationships
    if (
        len(normalized["entities"]) > 1
        and not normalized_relationships
        and not normalized.get("edgeless_justification")
    ):
        raise ValueError(
            "proof normalization requires a preexisting source-grounded edgeless_justification"
        )
    validate_normalized_semantic_profile(normalized)
    _validate_semantic_ir(normalized)

    bundle_members: dict[str, list[str]] = {}
    bundle_targets: dict[str, str] = {}
    bundle_proves: dict[str, list[dict]] = {}
    bundle_order: list[str] = []
    for proof_id, decision in indexed_decisions.items():
        if decision["disposition"] != "accepted":
            continue
        bundle_id = decision["bundle_id"]
        target_id = decision["target_id"]
        if bundle_id in bundle_targets and bundle_targets[bundle_id] != target_id:
            raise ValueError(f"proof bundle has conflicting targets: {bundle_id}")
        if bundle_id not in bundle_members:
            bundle_members[bundle_id] = []
            bundle_targets[bundle_id] = target_id
            bundle_proves[bundle_id] = []
            bundle_order.append(bundle_id)
        bundle_members[bundle_id].append(proof_id)
        bundle_proves[bundle_id].append(_relationship_snapshot(proves_by_proof[proof_id]))
    report = {
        "document_kind": "proof-normalization-report",
        "ir_version": 1,
        "proof_entities": [deepcopy(entity) for entity in entities if entity["id"] in proof_ids],
        "bundles": [
            {
                "bundle_id": bundle_id,
                "target_id": bundle_targets[bundle_id],
                "proof_ids": bundle_members[bundle_id],
                "proves_relationships": bundle_proves[bundle_id],
            }
            for bundle_id in bundle_order
        ],
        "relationships": relationship_report,
        "exclusions": [
            {
                "proof_id": proof_id,
                "reason": indexed_decisions[proof_id]["reason"],
                "incident_relationships": excluded_incident[proof_id],
            }
            for proof_id in indexed_decisions
            if indexed_decisions[proof_id]["disposition"] == "excluded"
        ],
    }
    _validate_provenance_report(report)
    return normalized, report


def normalize_with_inventory(semantic_ir: object, decisions: object, inventory: object) -> tuple[dict, dict, dict]:
    """Normalize proof IR and its exact compiler-facing pooled-inventory projection."""

    try:
        from ._batch_ir_merger import validate_extract_reconciliation
    except ImportError:  # pragma: no cover
        from _batch_ir_merger import validate_extract_reconciliation
    if not isinstance(semantic_ir, dict) or not isinstance(inventory, dict):
        raise ValueError("normalization requires semantic IR and pooled inventory objects")
    validate_extract_reconciliation(semantic_ir, inventory)
    normalized, report = normalize_proof_entities(semantic_ir, decisions)
    decision_by_proof = {item["proof_id"]: item for item in decisions["decisions"]}
    entities = {item["id"]: item for item in semantic_ir["entities"]}
    proof_candidates = {
        candidate_id
        for proof_id in decision_by_proof
        for candidate_id in entities[proof_id]["candidate_ids"]
    }
    target_endpoints: dict[str, dict] = {}
    for proof_id, decision in decision_by_proof.items():
        if decision["disposition"] != "accepted":
            continue
        target = entities[decision["target_id"]]
        if target["type"] != "result":
            raise ValueError(f"proof target is not an eligible result: {target['id']}")
        if target["candidate_ids"]:
            target_endpoints[proof_id] = {"candidate_id": target["candidate_ids"][0]}
            continue
        created = next(
            (item for item in semantic_ir["unresolved_resolutions"]
             if item.get("disposition") == "created" and item.get("entity_id") == target["id"]),
            None,
        )
        known_unresolved = {item.get("key") for item in inventory.get("unresolved_entities", [])}
        if created is None or created.get("unresolved_id") not in known_unresolved:
            raise ValueError(f"proof target has no compiler-facing candidate or created unresolved handle: {target['id']}")
        target_endpoints[proof_id] = {"unresolved_key": created["unresolved_id"]}
    projected = deepcopy(inventory)
    projected["candidates"] = [item for item in projected["candidates"] if item["id"] not in proof_candidates]
    proof_by_candidate = {
        candidate_id: proof_id
        for proof_id in decision_by_proof
        for candidate_id in entities[proof_id]["candidate_ids"]
    }
    proves_hint_ids = {
        hint_id
        for relationship in semantic_ir["relationships"]
        if relationship["type"] == "proves"
        for hint_id in relationship["hint_ids"]
    }
    excluded_proofs = {
        proof_id
        for proof_id, decision in decision_by_proof.items()
        if decision["disposition"] == "excluded"
    }
    retained_hints: list[dict] = []
    removed_hint_ids: list[str] = []
    removed_hint_reference_ids: set[str] = set()
    for hint in projected["relationship_hints"]:
        incident_proofs = {
            proof_by_candidate[candidate_id]
            for endpoint in (hint.get("from"), hint.get("to"))
            if isinstance(endpoint, dict)
            for candidate_id in [endpoint.get("candidate_id")]
            if candidate_id in proof_by_candidate
        }
        if hint["id"] in proves_hint_ids or incident_proofs & excluded_proofs:
            removed_hint_ids.append(hint["id"])
            removed_hint_reference_ids.update(hint.get("reference_ids", []))
            continue
        retained_hints.append(hint)
    projected["relationship_hints"] = retained_hints
    removed_hint_id_set = set(removed_hint_ids)
    normalized["hint_decisions"] = [
        item for item in normalized["hint_decisions"]
        if item["hint_id"] not in removed_hint_id_set
    ]
    for hint in projected["relationship_hints"]:
        target = hint.get("to", {})
        candidate_id = target.get("candidate_id") if isinstance(target, dict) else None
        for proof_id, replacement in target_endpoints.items():
            if candidate_id in entities[proof_id]["candidate_ids"]:
                hint["to"] = replacement
    proof_relationship_reference_ids = {
        reference_id
        for relationship in semantic_ir["relationships"]
        if relationship["from"] in decision_by_proof or relationship["to"] in decision_by_proof
        for reference_id in relationship.get("reference_ids", [])
    }
    surviving_reference_ids = {
        reference_id
        for relationship in normalized["relationships"]
        for reference_id in relationship.get("reference_ids", [])
    }
    surviving_reference_ids.update(
        gap["reference_id"]
        for gap in normalized["gaps"]
        if gap.get("category") == "reference" and "reference_id" in gap
    )
    removable_reference_ids = (
        removed_hint_reference_ids | proof_relationship_reference_ids
    ) - surviving_reference_ids
    removed_reference_ids = [
        item["id"] for item in projected["references"]
        if item["id"] in removable_reference_ids
    ]
    removed_reference_id_set = set(removed_reference_ids)
    projected["references"] = [
        item for item in projected["references"]
        if item["id"] not in removed_reference_id_set
    ]
    normalized["reference_decisions"] = [
        item for item in normalized["reference_decisions"]
        if item["reference_id"] not in removed_reference_id_set
    ]
    normalized["inventory"]["candidate_ids"] = [item["id"] for item in projected["candidates"]]
    normalized["inventory"]["candidate_count"] = len(projected["candidates"])
    validate_extract_reconciliation(normalized, projected)
    report["compiler_inventory"] = {
        "removed_proof_candidate_ids": sorted(proof_candidates),
        "removed_proof_hint_ids": removed_hint_ids,
        "removed_proof_reference_ids": removed_reference_ids,
        "projected_candidate_ids": normalized["inventory"]["candidate_ids"],
    }
    _validate_provenance_report(report)
    return normalized, report, projected


def _write_temp_json(payload: dict, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent,
        prefix=f".{destination.name}.", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        return Path(handle.name)


def write_normalized_outputs_atomic(
    normalized: dict,
    report: dict,
    normalized_path: Path,
    provenance_path: Path,
    inventory: dict | None = None,
    inventory_path: Path | None = None,
) -> None:
    """Stage three validated outputs and restore prior destinations on replacement failure."""

    normalized_path = normalized_path.resolve()
    provenance_path = provenance_path.resolve()
    destinations = [normalized_path, provenance_path]
    if (inventory is None) != (inventory_path is None):
        raise ValueError("inventory payload and destination must be supplied together")
    if inventory_path is not None:
        inventory_path = inventory_path.resolve()
        destinations.append(inventory_path)
    if len(set(destinations)) != len(destinations):
        raise ValueError("normalized IR, provenance, and inventory destinations must differ")
    temporary_normalized = _write_temp_json(normalized, normalized_path)
    temporary_provenance = _write_temp_json(report, provenance_path)
    temporary_inventory = _write_temp_json(inventory, inventory_path) if inventory_path else None
    originals = {path: path.read_bytes() if path.exists() else None for path in destinations}
    replaced: list[Path] = []
    try:
        os.replace(temporary_normalized, normalized_path)
        replaced.append(normalized_path)
        os.replace(temporary_provenance, provenance_path)
        replaced.append(provenance_path)
        if temporary_inventory is not None:
            os.replace(temporary_inventory, inventory_path)
            replaced.append(inventory_path)
    except Exception:
        for destination in reversed(replaced):
            original = originals[destination]
            if original is None:
                destination.unlink(missing_ok=True)
                continue
            restore = tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.", suffix=".rollback", delete=False
            )
            try:
                restore.write(original)
                restore.close()
                os.replace(restore.name, destination)
            finally:
                if not restore.closed:
                    restore.close()
                Path(restore.name).unlink(missing_ok=True)
        raise
    finally:
        temporary_normalized.unlink(missing_ok=True)
        temporary_provenance.unlink(missing_ok=True)
        if temporary_inventory is not None:
            temporary_inventory.unlink(missing_ok=True)


def _load_json_object(path: Path, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def normalize_files(
    semantic_path: Path,
    decisions_path: Path,
    normalized_path: Path,
    provenance_path: Path,
    inventory_path: Path,
    inventory_out_path: Path,
) -> dict:
    """Normalize two JSON inputs and publish all three outputs only after validation."""

    semantic = _load_json_object(semantic_path, "transitional semantic IR")
    decisions = _load_json_object(decisions_path, "proof-normalization decisions")
    normalized, report, projected = normalize_with_inventory(
        semantic, decisions, _load_json_object(inventory_path, "pooled inventory")
    )
    write_normalized_outputs_atomic(normalized, report, normalized_path, provenance_path, projected, inventory_out_path)
    return {
        "semantic_ir": str(semantic_path.resolve()),
        "decisions": str(decisions_path.resolve()),
        "out": str(normalized_path.resolve()),
        "provenance_out": str(provenance_path.resolve()),
        "entities": len(normalized["entities"]),
        "relationships": len(normalized["relationships"]),
    }


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Normalize transitional proof entities.")
    parser.add_argument("semantic_ir", help="Transitional semantic-graph IR JSON")
    parser.add_argument("--decisions", required=True, help="Proof-normalization decisions JSON")
    parser.add_argument("--out", required=True, help="Normalized semantic-graph IR destination")
    parser.add_argument("--provenance-out", required=True, help="Proof-normalization provenance destination")
    parser.add_argument("--inventory", required=True, help="Pooled inventory for compiler-facing projection")
    parser.add_argument("--inventory-out", required=True, help="Projected pooled inventory destination")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = normalize_files(
        Path(args.semantic_ir), Path(args.decisions), Path(args.out), Path(args.provenance_out),
        Path(args.inventory), Path(args.inventory_out)
    )
    print(json.dumps(report, indent=2))


class Interface(PythonArgvMachineInterface):
    """Expose deterministic proof normalization through the machine protocol."""

    prog = "proof_normalizer.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
