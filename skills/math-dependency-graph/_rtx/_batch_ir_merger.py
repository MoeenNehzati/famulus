#!/usr/bin/env python3
"""Pool inventory and validate whole-document extract reconciliation.

Inventory workers own discovery inside source chunks.  This module validates
their fragment vocabulary, checks ownership and accounting mechanically, then
qualifies fragment-local handles for the one document-wide extract worker.  It
then checks that the worker accounts for those handles exactly and applies only
keyed corrections authored by that worker. It makes no mathematical decisions.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Iterable

import jsonschema

from officina.runtime.python_machine_interface import PythonArgvMachineInterface


SKILL_DIR = Path(__file__).resolve().parents[1]
INVENTORY_SCHEMA_PATH = SKILL_DIR / "inventory.schema.json"
SEMANTIC_SCHEMA_PATH = SKILL_DIR / "semantic-graph.schema.json"
SEMANTIC_REPAIR_SCHEMA_PATH = SKILL_DIR / "semantic-repair.schema.json"
_PACKET_LINE_RE = re.compile(r"^(?P<line>[0-9]+) \|(?: ?.*)?$")
_REPAIRABLE_COLLECTIONS = {
    "entities",
    "exclusions",
    "unresolved_resolutions",
    "relationships",
    "hint_decisions",
    "reference_decisions",
    "gap_decisions",
    "gaps",
}


class ValidationReportError(ValueError):
    """Carry safe structured validation diagnostics across durable handoffs."""

    def __init__(
        self,
        label: str,
        diagnostics: list[dict],
        *,
        repairable: bool,
        display_messages: list[str] | None = None,
    ) -> None:
        self.diagnostics = deepcopy(diagnostics)
        self.repairable = repairable
        summaries = display_messages or [item["message"] for item in diagnostics]
        super().__init__(label + (":\n- " + "\n- ".join(summaries) if summaries else ""))


class WorkerFragmentLoadError(ValidationReportError):
    """Identify one unreadable worker artifact without retaining its contents."""

    def __init__(self, fragment_path: Path, diagnostic: dict) -> None:
        self.fragment_path = fragment_path.resolve()
        super().__init__(
            "invalid worker fragment",
            [diagnostic],
            repairable=False,
        )


class InventoryFragmentValidationError(ValueError):
    """Identify an inventory fragment whose validation failure can be retried."""

    def __init__(self, chunk_id: str, message: str) -> None:
        self.chunk_id = chunk_id
        super().__init__(message)


def _safe_schema_diagnostic(error: jsonschema.ValidationError, *, label: str) -> dict:
    """Project a library validation error without persisting its instance value."""

    path_parts = [str(part) for part in error.absolute_path]
    record_path = ".".join(path_parts) or "<root>"
    keyword = str(error.validator or "schema")
    if (
        keyword == "not"
        and path_parts
        and path_parts[0] == "unresolved_resolutions"
    ):
        summary = f"{label} {record_path}: entity_id is forbidden for a nonretained disposition"
        fields = ["entity_id"]
    elif keyword == "required" and isinstance(error.instance, dict):
        missing = sorted(set(error.validator_value) - set(error.instance))
        summary = f"{label} {record_path}: {', '.join(missing)} is required"
        fields = missing
    else:
        summary = f"{label} {record_path}: violates {keyword}"
        fields = []
    diagnostic = {
        "code": f"schema-{keyword.lower()}",
        "path": path_parts,
        "message": summary,
    }
    if fields:
        diagnostic["fields"] = fields
    return diagnostic


def _safe_cross_diagnostic(message: str) -> dict:
    """Reduce a detailed invariant failure to a stable non-content-bearing code."""

    phrases = (
        "unknown candidate endpoint",
        "unknown unresolved handle",
        "unknown relationship source",
        "unknown relationship target",
        "unknown evidence handle",
        "unknown reference handle",
        "unknown hint handle",
        "unknown inventory gap",
        "unreconciled candidate",
        "unreconciled unresolved handle",
        "unreconciled hint handle",
        "unreconciled reference handle",
        "unreconciled inventory gap",
        "candidate reconciled more than once",
        "unresolved handle reconciled more than once",
        "hint handle reconciled more than once",
        "reference handle reconciled more than once",
        "inventory gap reconciled more than once",
        "inventory candidate_count does not match candidate_ids",
        "semantic inventory candidate_ids do not exactly match pooled inventory",
        "duplicate direct relationship",
        "direct endpoint pair",
        "self-edge is not a direct relationship",
        "low-confidence relationship must be reported as a gap",
        "edgeless multi-entity semantic graph requires a source-grounded justification",
        "isolated semantic entity",
        "description contains a raw TeX environment wrapper",
        "explicit/inferred source is inconsistent",
        "required external-result candidate",
        "candidate-free entity",
        "retains unknown entity",
        "created unresolved handle",
        "does not retain two resolved endpoints",
        "does not retain resolved hint endpoints",
        "explicit/inferred mismatch",
        "has no outgoing supports edge",
        "has no incoming illustrated-by edge",
        "pooled inventory",
        "extract reconciliation requires",
    )
    lowered = message.lower()
    phrase = next(
        (item for item in phrases if item.lower() in lowered),
        "validation invariant failed",
    )
    code = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
    return {"code": code, "path": [], "message": phrase}


def canonical_json_bytes(payload: dict) -> bytes:
    """Return the stable serialized form used for diagnostic size measurement."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _inventory_validator() -> jsonschema.protocols.Validator:
    """Build the inventory-fragment schema validator from the owned contract."""

    schema = json.loads(INVENTORY_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validate_inventory_fragment(fragment: dict) -> None:
    """Reject a worker output that is not an inventory version-3 fragment."""

    _inventory_validator().validate(fragment)


def _validator_for(path: Path) -> jsonschema.protocols.Validator:
    """Build a checked JSON Schema validator for one skill-owned contract."""

    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def _schema_diagnostics(
    payload: dict, path: Path, *, label: str
) -> list[dict]:
    """Return every local shape failure in stable record-path order."""

    errors = sorted(
        _validator_for(path).iter_errors(payload),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    return [_safe_schema_diagnostic(error, label=label) for error in errors]


def _append_partition_errors(
    errors: list[str],
    *,
    known: set[str],
    uses: list[str],
    label: str,
) -> None:
    """Append exact-partition failures with handle-specific diagnostics."""

    counts = Counter(uses)
    for identifier in sorted(set(uses) - known):
        errors.append(f"unknown {label}: {identifier}")
    for identifier in sorted(identifier for identifier, count in counts.items() if count > 1):
        errors.append(f"{label} reconciled more than once: {identifier}")
    for identifier in sorted(known - set(uses)):
        errors.append(f"unreconciled {label}: {identifier}")


def _checked_registry(records: object, field: str, label: str, errors: list[str]) -> dict[str, dict]:
    """Index one pooled registry while retaining duplicate diagnostics."""

    if not isinstance(records, list):
        errors.append(f"pooled inventory {label} must be an array")
        return {}
    indexed: dict[str, dict] = {}
    counts: Counter[str] = Counter()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(field), str):
            errors.append(f"pooled inventory {label} contains an invalid record")
            continue
        identifier = record[field]
        counts[identifier] += 1
        indexed.setdefault(identifier, record)
    for identifier in sorted(item for item, count in counts.items() if count > 1):
        errors.append(f"pooled inventory has duplicate {label} id: {identifier}")
    return indexed


def _location_error(location: object, files: list[str]) -> str | None:
    """Return a compact diagnostic for an invalid pooled source coordinate."""

    if not isinstance(location, list) or len(location) != 3:
        return "must be [file_index, start_line, end_line]"
    file_index, start_line, end_line = location
    if not isinstance(file_index, int) or isinstance(file_index, bool):
        return "has a non-integer file index"
    if file_index < 0 or file_index >= len(files):
        return f"has out-of-bounds file index {file_index}"
    if (
        not isinstance(start_line, int)
        or isinstance(start_line, bool)
        or not isinstance(end_line, int)
        or isinstance(end_line, bool)
        or start_line < 1
        or end_line < start_line
    ):
        return "has invalid inclusive line bounds"
    return None


def _resolved_endpoint(
    endpoint: object,
    *,
    candidate_entities: dict[str, str],
    resolutions: dict[str, dict],
) -> str | None:
    """Resolve one pooled hint endpoint through the extract reconciliation maps."""

    if not isinstance(endpoint, dict):
        return None
    if isinstance(endpoint.get("candidate_id"), str):
        return candidate_entities.get(endpoint["candidate_id"])
    unresolved_id = endpoint.get("unresolved_key")
    if not isinstance(unresolved_id, str):
        return None
    resolution = resolutions.get(unresolved_id)
    if resolution and resolution.get("disposition") in {"matched", "created"}:
        entity_id = resolution.get("entity_id")
        return entity_id if isinstance(entity_id, str) else None
    return None


def validate_extract_reconciliation(payload: dict, inventory: dict) -> None:
    """Validate one schema-v2 extract against the complete pooled inventory.

    JSON Schema is the first fail-closed boundary.  Once every record is safe to
    inspect, this function reports all independently detectable cross-record
    errors together so one narrow correction can repair them in one pass.
    """

    if not isinstance(payload, dict):
        raise ValueError("semantic extract must be a JSON object")
    schema_errors = _schema_diagnostics(
        payload, SEMANTIC_SCHEMA_PATH, label="semantic schema"
    )
    if schema_errors:
        repairable = all(
            item["path"]
            and item["path"][0] in _REPAIRABLE_COLLECTIONS
            and len(item["path"]) >= 2
            for item in schema_errors
        )
        raise ValidationReportError(
            "extract reconciliation failed",
            schema_errors,
            repairable=repairable,
        )
    if not isinstance(inventory, dict):
        raise ValueError("pooled inventory is required for extract reconciliation")

    errors: list[str] = []
    if inventory.get("ir_version") != 2 or inventory.get("chunk_id") != "pooled":
        errors.append("extract reconciliation requires pooled inventory version 2")
    raw_files = inventory.get("files")
    files = raw_files if isinstance(raw_files, list) and all(
        isinstance(item, str) and item for item in raw_files
    ) else []
    if not files:
        errors.append("pooled inventory requires a nonempty file table")

    candidates = _checked_registry(
        inventory.get("candidates"), "id", "candidate", errors
    )
    unresolved = _checked_registry(
        inventory.get("unresolved_entities"), "key", "unresolved", errors
    )
    hints = _checked_registry(
        inventory.get("relationship_hints"), "id", "hint", errors
    )
    references = _checked_registry(
        inventory.get("references"), "id", "reference", errors
    )
    inventory_gaps = _checked_registry(
        inventory.get("gaps"), "id", "inventory gap", errors
    )
    evidence = _checked_registry(
        inventory.get("evidence"), "id", "evidence", errors
    )

    for label, registry in (
        ("candidate", candidates),
        ("reference", references),
        ("evidence", evidence),
    ):
        for identifier, record in registry.items():
            location_error = _location_error(record.get("location"), files)
            if location_error:
                errors.append(f"{label} {identifier} location {location_error}")

    inventory_candidate_ids = list(candidates)
    semantic_inventory = payload["inventory"]
    if semantic_inventory["candidate_count"] != len(semantic_inventory["candidate_ids"]):
        errors.append("inventory candidate_count does not match candidate_ids")
    if semantic_inventory["candidate_ids"] != inventory_candidate_ids:
        errors.append("semantic inventory candidate_ids do not exactly match pooled inventory")

    entity_ids = [entity["id"] for entity in payload["entities"]]
    for identifier, count in Counter(entity_ids).items():
        if count > 1:
            errors.append(f"semantic entity id appears more than once: {identifier}")
    known_entities = set(entity_ids)
    candidate_entities: dict[str, str] = {}
    candidate_uses: list[str] = []
    for entity in payload["entities"]:
        if re.search(r"\\begin\{[^{}]+\}|\\end\{[^{}]+\}", entity["description"]):
            errors.append(
                f"entity {entity['id']!r} description contains a raw TeX environment wrapper"
            )
        for candidate_id in entity["candidate_ids"]:
            candidate_uses.append(candidate_id)
            candidate_entities.setdefault(candidate_id, entity["id"])
        known_candidates = [
            candidates[candidate_id]
            for candidate_id in entity["candidate_ids"]
            if candidate_id in candidates
        ]
        if known_candidates:
            expected_source = (
                "explicit"
                if any(item.get("provenance") == "explicit" for item in known_candidates)
                else "inferred"
            )
            if entity["source"] != expected_source:
                errors.append(
                    f"entity {entity['id']!r} explicit/inferred source is inconsistent with its candidates"
                )
    candidate_uses.extend(item["candidate_id"] for item in payload["exclusions"])
    _append_partition_errors(
        errors,
        known=set(candidates),
        uses=candidate_uses,
        label="candidate",
    )
    entities_by_id = {entity["id"]: entity for entity in payload["entities"]}
    for candidate_id, candidate in candidates.items():
        if "named-indispensable-external-result" not in candidate.get(
            "retention_reasons", []
        ):
            continue
        entity_id = candidate_entities.get(candidate_id)
        if entity_id is None:
            errors.append(
                f"required external-result candidate {candidate_id} is not retained"
            )
        elif entities_by_id[entity_id]["type"] != "external-result":
            errors.append(
                f"required external-result candidate {candidate_id} is retained with the wrong type"
            )

    resolution_records = payload["unresolved_resolutions"]
    resolution_uses = [item["unresolved_id"] for item in resolution_records]
    _append_partition_errors(
        errors,
        known=set(unresolved),
        uses=resolution_uses,
        label="unresolved handle",
    )
    resolutions: dict[str, dict] = {}
    created_entity_ids: set[str] = set()
    for resolution in resolution_records:
        resolutions.setdefault(resolution["unresolved_id"], resolution)
        if resolution["disposition"] not in {"matched", "created"}:
            continue
        entity_id = resolution["entity_id"]
        if entity_id not in known_entities:
            errors.append(
                f"unresolved handle {resolution['unresolved_id']} retains unknown entity {entity_id}"
            )
        if resolution["disposition"] == "created":
            created_entity_ids.add(entity_id)
            entity = next(
                (item for item in payload["entities"] if item["id"] == entity_id), None
            )
            if entity is not None and entity["candidate_ids"]:
                errors.append(
                    f"created unresolved handle {resolution['unresolved_id']} must target a candidate-free entity"
                )
            if entity is not None and entity["source"] != "inferred":
                errors.append(
                    f"created unresolved handle {resolution['unresolved_id']} must target an inferred entity"
                )
    for entity in payload["entities"]:
        if not entity["candidate_ids"] and entity["id"] not in created_entity_ids:
            errors.append(
                f"candidate-free entity {entity['id']!r} is not backed by a created unresolved handle"
            )

    evidence_uses: list[tuple[str, str]] = []
    hint_uses: list[str] = []
    reference_uses: list[str] = []
    edge_keys: list[tuple[str, str, str]] = []
    endpoint_pairs: list[tuple[str, str]] = []
    hint_by_id = hints
    for index, relationship in enumerate(payload["relationships"]):
        source = relationship["from"]
        target = relationship["to"]
        edge_key = (source, target, relationship["type"])
        edge_keys.append(edge_key)
        endpoint_pairs.append((source, target))
        if source not in known_entities:
            errors.append(f"unknown relationship source: {source}")
        if target not in known_entities:
            errors.append(f"unknown relationship target: {target}")
        if source == target:
            errors.append(f"self-edge is not a direct relationship: {source}")
        if relationship.get("confidence") in {"Low", "Unknown"}:
            errors.append(
                f"low-confidence relationship must be reported as a gap: {source} -> {target}"
            )
        evidence_uses.extend(
            (evidence_id, f"relationship {index}")
            for evidence_id in relationship["evidence_ids"]
        )
        hint_uses.extend(relationship["hint_ids"])
        reference_uses.extend(relationship.get("reference_ids", []))
        accepted_hints = [
            hint_by_id[hint_id]
            for hint_id in relationship["hint_ids"]
            if hint_id in hint_by_id
        ]
        for hint in accepted_hints:
            resolved_from = _resolved_endpoint(
                hint.get("from"),
                candidate_entities=candidate_entities,
                resolutions=resolutions,
            )
            resolved_to = _resolved_endpoint(
                hint.get("to"),
                candidate_entities=candidate_entities,
                resolutions=resolutions,
            )
            if resolved_from is None or resolved_to is None:
                errors.append(
                    f"accepted hint {hint['id']} does not retain two resolved endpoints"
                )
            elif (source, target) != (resolved_from, resolved_to):
                errors.append(
                    f"relationship {source} -> {target} does not retain resolved hint endpoints for {hint['id']}"
                )
        if accepted_hints:
            expected_implicit = not any(
                hint.get("assertion") == "explicit" for hint in accepted_hints
            )
            if relationship["implicit"] != expected_implicit:
                errors.append(
                    f"relationship {source} -> {target} has an explicit/inferred mismatch with its hints"
                )

    for edge_key, count in Counter(edge_keys).items():
        if count > 1:
            errors.append(
                "duplicate direct relationship: " + " -> ".join(edge_key[:2]) + f" ({edge_key[2]})"
            )
    pair_types: dict[tuple[str, str], set[str]] = {}
    for source, target, edge_type in edge_keys:
        pair_types.setdefault((source, target), set()).add(edge_type)
    for pair, edge_types in pair_types.items():
        if len(edge_types) > 1:
            errors.append(
                f"direct endpoint pair {pair[0]} -> {pair[1]} has multiple relationship types"
            )

    hint_uses.extend(item["hint_id"] for item in payload["hint_decisions"])
    _append_partition_errors(
        errors,
        known=set(hints),
        uses=hint_uses,
        label="hint handle",
    )
    reference_uses.extend(item["reference_id"] for item in payload["reference_decisions"])
    for index, decision in enumerate(payload["reference_decisions"]):
        evidence_uses.extend(
            (evidence_id, f"reference decision {index}")
            for evidence_id in decision["evidence_ids"]
        )
    inventory_gap_uses: list[str] = []
    inventory_gap_uses.extend(
        item["gap_id"] for item in payload["gap_decisions"]
    )
    gap_ids = [gap["id"] for gap in payload["gaps"]]
    for gap_id, count in Counter(gap_ids).items():
        if count > 1:
            errors.append(f"semantic gap id appears more than once: {gap_id}")
    for index, gap in enumerate(payload["gaps"]):
        evidence_uses.extend(
            (evidence_id, f"gap {index}") for evidence_id in gap["evidence_ids"]
        )
        if gap["category"] == "reference":
            reference_uses.append(gap["reference_id"])
        inventory_gap_uses.extend(gap.get("inventory_gap_ids", []))
    _append_partition_errors(
        errors,
        known=set(inventory_gaps),
        uses=inventory_gap_uses,
        label="inventory gap",
    )
    _append_partition_errors(
        errors,
        known=set(references),
        uses=reference_uses,
        label="reference handle",
    )
    for evidence_id, owner in evidence_uses:
        if evidence_id not in evidence:
            errors.append(f"unknown evidence handle {evidence_id} in {owner}")

    outgoing_support = {
        relationship["from"]
        for relationship in payload["relationships"]
        if relationship["type"] == "supports"
    }
    incoming_illustration = {
        relationship["to"]
        for relationship in payload["relationships"]
        if relationship["type"] == "illustrated-by"
    }
    for entity in payload["entities"]:
        if entity["type"] == "external-result" and entity["id"] not in outgoing_support:
            errors.append(f"external-result entity {entity['id']!r} has no outgoing supports edge")
        if (
            entity["type"] == "exposition"
            and entity.get("kind") == "example"
            and entity["id"] not in incoming_illustration
        ):
            errors.append(f"example entity {entity['id']!r} has no incoming illustrated-by edge")

    if len(entity_ids) > 1 and not payload["relationships"]:
        if not payload.get("edgeless_justification"):
            errors.append(
                "edgeless multi-entity semantic graph requires a source-grounded justification"
            )
    elif payload["relationships"]:
        incident = {identifier for pair in endpoint_pairs for identifier in pair}
        for entity_id in entity_ids:
            if entity_id not in incident:
                errors.append(f"isolated semantic entity: {entity_id}")

    if errors:
        global_prefixes = (
            "inventory candidate_count",
            "semantic inventory candidate_ids",
            "extract reconciliation requires",
            "pooled inventory",
        )
        raise ValidationReportError(
            "extract reconciliation failed",
            [_safe_cross_diagnostic(message) for message in errors],
            repairable=not any(message.startswith(global_prefixes) for message in errors),
            display_messages=errors,
        )


def _apply_keyed_updates(
    records: list[dict],
    *,
    remove: set[object],
    upserts: list[dict],
    key,
) -> list[dict]:
    """Apply deterministic keyed record changes while preserving unaffected order."""

    upsert_keys = [key(record) for record in upserts]
    duplicate_keys = sorted(
        (identifier for identifier, count in Counter(upsert_keys).items() if count > 1),
        key=repr,
    )
    if duplicate_keys:
        raise ValueError(f"duplicate repair upsert key: {duplicate_keys[0]!r}")
    updates = {key(record): deepcopy(record) for record in upserts}
    result: list[dict] = []
    for record in records:
        identifier = key(record)
        if identifier in updates:
            result.append(updates.pop(identifier))
        elif identifier not in remove:
            result.append(deepcopy(record))
    for record in upserts:
        identifier = key(record)
        if identifier in updates:
            result.append(updates.pop(identifier))
    return result


def apply_semantic_repair(
    payload: dict,
    repair: dict,
    inventory: dict,
) -> dict:
    """Apply one narrow correction and require exact reconciliation before return."""

    if not isinstance(repair, dict):
        raise ValueError("semantic repair must be a JSON object")
    schema_errors = _schema_diagnostics(
        repair, SEMANTIC_REPAIR_SCHEMA_PATH, label="semantic repair schema"
    )
    if schema_errors:
        raise ValidationReportError(
            "semantic repair failed", schema_errors, repairable=False
        )
    result = deepcopy(payload)
    result["entities"] = _apply_keyed_updates(
        result["entities"],
        remove=set(repair["remove_entity_ids"]),
        upserts=repair["upsert_entities"],
        key=lambda item: item["id"],
    )
    result["exclusions"] = _apply_keyed_updates(
        result["exclusions"],
        remove=set(repair["remove_exclusion_candidate_ids"]),
        upserts=repair["upsert_exclusions"],
        key=lambda item: item["candidate_id"],
    )
    result["unresolved_resolutions"] = _apply_keyed_updates(
        result["unresolved_resolutions"],
        remove=set(repair["remove_unresolved_ids"]),
        upserts=repair["upsert_unresolved_resolutions"],
        key=lambda item: item["unresolved_id"],
    )
    result["relationships"] = _apply_keyed_updates(
        result["relationships"],
        remove={
            tuple(item[field] for field in ("from", "to", "type"))
            for item in repair["remove_relationships"]
        },
        upserts=repair["upsert_relationships"],
        key=lambda item: tuple(item[field] for field in ("from", "to", "type")),
    )
    result["hint_decisions"] = _apply_keyed_updates(
        result["hint_decisions"],
        remove=set(repair["remove_hint_ids"]),
        upserts=repair["upsert_hint_decisions"],
        key=lambda item: item["hint_id"],
    )
    result["reference_decisions"] = _apply_keyed_updates(
        result["reference_decisions"],
        remove=set(repair["remove_reference_ids"]),
        upserts=repair["upsert_reference_decisions"],
        key=lambda item: item["reference_id"],
    )
    result["gaps"] = _apply_keyed_updates(
        result["gaps"],
        remove=set(repair["remove_gap_ids"]),
        upserts=repair["upsert_gaps"],
        key=lambda item: item["id"],
    )
    result["gap_decisions"] = _apply_keyed_updates(
        result["gap_decisions"],
        remove=set(repair["remove_inventory_gap_ids"]),
        upserts=repair["upsert_gap_decisions"],
        key=lambda item: item["gap_id"],
    )
    result_schema_errors = _schema_diagnostics(
        result, SEMANTIC_SCHEMA_PATH, label="repaired semantic schema"
    )
    if result_schema_errors:
        raise ValidationReportError(
            "semantic repair produced invalid IR",
            result_schema_errors,
            repairable=False,
        )
    validate_extract_reconciliation(result, inventory)
    return result


def _qualified(chunk_id: str, local_id: str) -> str:
    """Make one fragment-local handle unambiguous in the pooled artifact."""

    return f"{chunk_id}::{local_id}"


def _require_unique_ids(records: list[dict], *, field: str, label: str) -> set[str]:
    """Return record identifiers while rejecting same-kind local handle reuse."""

    ids = [str(record[field]) for record in records]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate {label} id: {duplicates[0]}")
    return set(ids)


def _chunk_indexes(chunk_manifest: dict) -> tuple[list[dict], dict[str, dict]]:
    """Validate inventory ownership metadata and preserve its source order."""

    if chunk_manifest.get("mode") != "inventory":
        raise ValueError("inventory pooling requires an inventory chunk manifest")
    source_path = Path(str(chunk_manifest.get("source", ""))).resolve()
    source_sha256 = chunk_manifest.get("source_sha256")
    if (
        not source_path.is_file()
        or not isinstance(source_sha256, str)
        or hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256
    ):
        raise ValueError("inventory active source identity changed after planning")
    chunks = chunk_manifest.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("inventory chunk manifest requires a nonempty chunks array")
    by_id: dict[str, dict] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("inventory chunk manifest contains a non-object chunk")
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("inventory chunk manifest requires nonempty chunk ids")
        if chunk_id in by_id:
            raise ValueError(f"duplicate inventory chunk id: {chunk_id}")
        spans = chunk.get("spans")
        if not isinstance(spans, list) or not spans:
            raise ValueError(f"inventory chunk {chunk_id!r} requires owned spans")
        if not isinstance(chunk.get("anchors"), list):
            raise ValueError(f"inventory chunk {chunk_id!r} requires an anchors array")
        if not isinstance(chunk.get("packet_path"), str) or not chunk["packet_path"]:
            raise ValueError(f"inventory chunk {chunk_id!r} requires packet_path")
        packet_path = Path(chunk["packet_path"]).resolve()
        packet_sha256 = chunk.get("packet_sha256")
        if (
            not packet_path.is_file()
            or not isinstance(packet_sha256, str)
            or hashlib.sha256(packet_path.read_bytes()).hexdigest() != packet_sha256
        ):
            raise ValueError(
                f"inventory chunk packet identity changed after planning: {chunk_id}"
            )
        by_id[chunk_id] = chunk
    return chunks, by_id


def _spans_by_file(chunk: dict) -> dict[str, list[tuple[int, int]]]:
    """Project one chunk's declared inclusive source ownership by file."""

    spans_by_file: dict[str, list[tuple[int, int]]] = {}
    for span in chunk["spans"]:
        if not isinstance(span, dict):
            raise ValueError("inventory chunk span must be an object")
        source_file = span.get("source_file")
        start_line = span.get("start_line")
        end_line = span.get("end_line")
        if (
            not isinstance(source_file, str)
            or not source_file
            or not isinstance(start_line, int)
            or not isinstance(end_line, int)
            or start_line < 1
            or end_line < start_line
        ):
            raise ValueError("inventory chunk span is invalid")
        spans_by_file.setdefault(source_file, []).append((start_line, end_line))
    return spans_by_file


def _location_is_owned(location: object, fragment: dict, spans_by_file: dict[str, list[tuple[int, int]]]) -> bool:
    """Return whether a compact fragment location belongs to its assigned span."""

    if not isinstance(location, list) or len(location) != 3:
        return False
    file_index, start_line, end_line = location
    if (
        not isinstance(file_index, int)
        or not isinstance(start_line, int)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
        or file_index < 0
        or file_index >= len(fragment["files"])
    ):
        return False
    source_file = fragment["files"][file_index]
    return any(
        start <= start_line and end_line <= end
        for start, end in spans_by_file.get(source_file, [])
    )


def _check_owned_locations(fragment: dict, chunk: dict) -> None:
    """Reject worker findings that point outside the fragment's source ownership."""

    spans_by_file = _spans_by_file(chunk)
    for source_file in fragment["files"]:
        if source_file not in spans_by_file:
            raise ValueError(
                f"inventory fragment names file outside its owned chunk: {source_file}"
            )
    locations: list[tuple[str, object]] = []

    def collect_scope(label: str, record: dict) -> None:
        scope = record.get("scope_hint")
        if scope is not None:
            locations.append((f"{label} scope start", scope["starts_at"]))
            if "ends_at" in scope:
                locations.append((f"{label} scope end", scope["ends_at"]))

    def collect_endpoint(endpoint: dict) -> None:
        unresolved = endpoint.get("unresolved")
        if unresolved is not None:
            collect_scope("unresolved", unresolved)

    for node in fragment["nodes"]:
        locations.append(("node location", node["location"]))
        collect_scope("node", node)
    for edge in fragment["edges"]:
        locations.append(("edge location", edge["location"]))
        collect_endpoint(edge["from"])
        collect_endpoint(edge["to"])
        if "reference" in edge:
            locations.append(("edge reference location", edge["reference"]["location"]))
    for gap in fragment["gaps"]:
        locations.append(("gap location", gap["location"]))
        if "subject" in gap:
            collect_endpoint(gap["subject"])
        if "reference" in gap:
            locations.append(("gap reference location", gap["reference"]["location"]))
    for label, location in locations:
        if not _location_is_owned(location, fragment, spans_by_file):
            raise ValueError(f"{label} is outside its owned chunk span")


def _check_fragment_accounting(fragment: dict) -> None:
    """Validate discovery-local ids and endpoints before deterministic pooling."""

    node_ids = _require_unique_ids(fragment["nodes"], field="local_id", label="node")
    _require_unique_ids(fragment["edges"], field="local_id", label="edge")
    _require_unique_ids(fragment["gaps"], field="local_id", label="gap")

    def check_endpoint(endpoint: dict) -> None:
        if "local_node" in endpoint and endpoint["local_node"] not in node_ids:
            raise ValueError(f"unknown local node endpoint: {endpoint['local_node']}")

    for edge in fragment["edges"]:
        check_endpoint(edge["from"])
        check_endpoint(edge["to"])
    for gap in fragment["gaps"]:
        if "subject" in gap:
            check_endpoint(gap["subject"])


def _check_visible_anchor_coverage(fragment: dict, chunk: dict) -> None:
    """Require every hidden formal-environment anchor after worker discovery."""

    node_anchors = {
        (
            fragment["files"][node["location"][0]],
            node["location"][1],
            node["location"][2],
            node.get("environment"),
        )
        for node in fragment["nodes"]
    }
    for anchor in chunk["anchors"]:
        key = (
            anchor["source_file"],
            anchor["start_line"],
            anchor["end_line"],
            anchor["environment"],
        )
        if key not in node_anchors:
            raise ValueError(
                "inventory fragment misses visible environment anchor: "
                f"{anchor['source_file']}:{anchor['start_line']}-{anchor['end_line']} "
                f"environment={anchor['environment']}"
            )


def _remap_location(location: list[int], file_indexes: dict[tuple[str, int], int], chunk_id: str) -> list[int]:
    """Map one fragment-local location to the pooled document-wide file table."""

    local_index, start_line, end_line = location
    return [file_indexes[(chunk_id, local_index)], start_line, end_line]


def _remap_scope(scope: dict, file_indexes: dict[tuple[str, int], int], chunk_id: str) -> dict:
    """Copy a scope hint while remapping every compact location it contains."""

    result = deepcopy(scope)
    result["starts_at"] = _remap_location(scope["starts_at"], file_indexes, chunk_id)
    if "ends_at" in scope:
        result["ends_at"] = _remap_location(scope["ends_at"], file_indexes, chunk_id)
    return result


def _remap_endpoint(endpoint: dict, chunk_id: str) -> dict:
    """Qualify an unresolved endpoint while retaining document-wide candidate anchors."""

    if "candidate_id" in endpoint:
        return {"candidate_id": endpoint["candidate_id"]}
    return {"unresolved_key": _qualified(chunk_id, endpoint["unresolved_key"])}


def _candidate_anchor_id(
    fragment: dict, node: dict, *, disambiguate_end: bool = False
) -> str:
    """Derive one stable document candidate id from an owned source span."""

    source_file = fragment["files"][node["location"][0]]
    safe_file = re.sub(r"[^A-Za-z0-9._:-]", "_", source_file)
    start_line = node["location"][1]
    if disambiguate_end:
        return f"{safe_file}:{start_line}-{node['location'][2]}"
    return f"{safe_file}:{start_line}"


def _owned_packet_bytes(chunk: dict) -> int:
    """Count only source lines inside the chunk's declared owned spans.

    Inventory packets may include read-only boundary context.  Comparing a
    fragment with the whole packet would let that context inflate its output
    allowance, so this counts each owned source line at most once.
    """

    packet_path = Path(chunk["packet_path"]).resolve()
    if not packet_path.is_file():
        raise ValueError(f"inventory chunk packet does not exist: {packet_path}")
    spans_by_file = _spans_by_file(chunk)
    seen: set[tuple[str, int]] = set()
    current_source: str | None = None
    total = 0
    for raw_line in packet_path.read_bytes().splitlines(keepends=True):
        text = raw_line.decode("utf-8").rstrip("\r\n")
        if text.startswith("@@ source: "):
            current_source = text.removeprefix("@@ source: ").strip()
            continue
        line_match = _PACKET_LINE_RE.match(text)
        if current_source is None or line_match is None:
            continue
        line_number = int(line_match.group("line"))
        key = (current_source, line_number)
        if key in seen:
            continue
        if any(
            start <= line_number <= end
            for start, end in spans_by_file.get(current_source, [])
        ):
            seen.add(key)
            total += len(raw_line)
    if total == 0:
        raise ValueError(
            f"inventory chunk packet has no measurable owned input bytes: {packet_path}"
        )
    planned_owned_bytes = chunk.get("owned_bytes")
    if not isinstance(planned_owned_bytes, int) or planned_owned_bytes != total:
        raise ValueError(
            "inventory chunk owned-byte snapshot changed after planning: "
            + str(chunk.get("chunk_id", "<unknown>"))
        )
    return total


def pool_inventory_fragments(
    fragments: list[dict],
    *,
    chunk_manifest: dict,
) -> dict:
    """Validate, qualify, and source-order inventory fragments into one IR.

    ``inventory.schema.json`` deliberately remains a fragment-local vocabulary.
    The returned pooled object has the same record layout but qualified local
    handles, so it is validated by this runtime rather than reusing local-id
    JSON Schema patterns that would reject its necessary document-wide keys.
    """

    if not fragments:
        raise ValueError("at least one inventory fragment is required")
    ordered_chunks, chunks_by_id = _chunk_indexes(chunk_manifest)
    fragments_by_id: dict[str, dict] = {}
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise ValueError("inventory fragment must be an object")
        validate_inventory_fragment(fragment)
        chunk_id = fragment["chunk_id"]
        if chunk_id not in chunks_by_id:
            raise ValueError(f"unknown inventory chunk: {chunk_id}")
        if chunk_id in fragments_by_id:
            raise ValueError(f"inventory chunk merged more than once: {chunk_id}")
        _check_owned_locations(fragment, chunks_by_id[chunk_id])
        _check_fragment_accounting(fragment)
        _check_visible_anchor_coverage(fragment, chunks_by_id[chunk_id])
        fragments_by_id[chunk_id] = fragment
    missing = [chunk["chunk_id"] for chunk in ordered_chunks if chunk["chunk_id"] not in fragments_by_id]
    if missing:
        raise ValueError(f"inventory fragments omit chunks: {missing!r}")
    files: list[str] = []
    for chunk in ordered_chunks:
        for span in chunk["spans"]:
            source_file = span["source_file"]
            if source_file not in files:
                files.append(source_file)
    file_indexes: dict[tuple[str, int], int] = {}
    for chunk in ordered_chunks:
        fragment = fragments_by_id[chunk["chunk_id"]]
        for local_index, source_file in enumerate(fragment["files"]):
            try:
                pooled_index = files.index(source_file)
            except ValueError as error:  # Ownership was checked earlier; retain a narrow diagnostic.
                raise ValueError(f"unknown fragment source file: {source_file}") from error
            file_indexes[(chunk["chunk_id"], local_index)] = pooled_index

    pooled = {
        "ir_version": 2,
        "chunk_id": "pooled",
        "files": files,
        "evidence": [],
        "references": [],
        "candidates": [],
        "unresolved_entities": [],
        "relationship_hints": [],
        "reference_decisions": [],
        "gaps": [],
    }
    pooled_candidate_ids: set[str] = set()
    for chunk in ordered_chunks:
        chunk_id = chunk["chunk_id"]
        fragment = fragments_by_id[chunk_id]
        candidate_ids_by_local: dict[str, str] = {}
        candidate_start_counts = Counter(
            (
                fragment["files"][node["location"][0]],
                node["location"][1],
            )
            for node in fragment["nodes"]
        )
        evidence_number = 0
        reference_number = 0
        unresolved_number = 0

        def add_evidence(location: list[int], role: str) -> str:
            nonlocal evidence_number
            evidence_number += 1
            local_id = f"e{evidence_number}"
            pooled["evidence"].append(
                {
                    "id": _qualified(chunk_id, local_id),
                    "location": _remap_location(location, file_indexes, chunk_id),
                    "role": role,
                }
            )
            return local_id

        def add_reference(reference: dict) -> str:
            nonlocal reference_number
            reference_number += 1
            local_id = f"r{reference_number}"
            pooled["references"].append(
                {
                    "id": _qualified(chunk_id, local_id),
                    "location": _remap_location(
                        reference["location"], file_indexes, chunk_id
                    ),
                    "locator": deepcopy(reference["locator"]),
                }
            )
            return local_id

        def remap_discovery_endpoint(endpoint: dict, evidence_id: str) -> dict:
            nonlocal unresolved_number
            if "local_node" in endpoint:
                return {"candidate_id": candidate_ids_by_local[endpoint["local_node"]]}
            unresolved_number += 1
            local_id = f"u{unresolved_number}"
            item = deepcopy(endpoint["unresolved"])
            item["key"] = _qualified(chunk_id, local_id)
            item["evidence_ids"] = [_qualified(chunk_id, evidence_id)]
            if "scope_hint" in item:
                item["scope_hint"] = _remap_scope(
                    item["scope_hint"], file_indexes, chunk_id
                )
            pooled["unresolved_entities"].append(item)
            return {"unresolved_key": item["key"]}

        for node in fragment["nodes"]:
            start_key = (
                fragment["files"][node["location"][0]],
                node["location"][1],
            )
            candidate_id = _candidate_anchor_id(
                fragment,
                node,
                disambiguate_end=candidate_start_counts[start_key] > 1,
            )
            if candidate_id in pooled_candidate_ids:
                raise InventoryFragmentValidationError(
                    chunk_id,
                    f"candidate anchor emitted more than once: {candidate_id}",
                )
            pooled_candidate_ids.add(candidate_id)
            candidate_ids_by_local[node["local_id"]] = candidate_id
            evidence_id = add_evidence(node["location"], "statement")
            item = deepcopy(node)
            del item["local_id"]
            item["id"] = candidate_id
            item["location"] = _remap_location(node["location"], file_indexes, chunk_id)
            item["evidence_ids"] = [_qualified(chunk_id, evidence_id)]
            if "scope_hint" in node:
                item["scope_hint"] = _remap_scope(
                    node["scope_hint"], file_indexes, chunk_id
                )
            if node["type_hint"] == "external-result":
                item["retention_reasons"] = ["named-indispensable-external-result"]
            pooled["candidates"].append(item)
        for hint_number, edge in enumerate(fragment["edges"], 1):
            role = {
                "explicit-reference": "explicit-reference",
                "explicit-prose": "dependency-prose",
                "proof-use": "proof-use",
                "mathematical-inference": "dependency-prose",
            }[edge["basis"]]
            evidence_id = add_evidence(edge["location"], role)
            item = {
                "id": _qualified(chunk_id, f"h{hint_number}"),
                "from": remap_discovery_endpoint(edge["from"], evidence_id),
                "to": remap_discovery_endpoint(edge["to"], evidence_id),
                "type": edge["type"],
                "basis": edge["basis"],
                "assertion": edge["assertion"],
                "evidence_ids": [_qualified(chunk_id, evidence_id)],
                "confidence": edge["confidence"],
                "description": edge["description"],
            }
            if "reason" in edge:
                item["reason"] = edge["reason"]
            if "reference" in edge:
                reference_id = add_reference(edge["reference"])
                item["reference_ids"] = [_qualified(chunk_id, reference_id)]
            pooled["relationship_hints"].append(item)
        for gap_number, gap in enumerate(fragment["gaps"], 1):
            evidence_id = add_evidence(
                gap["location"],
                "explicit-reference" if "reference" in gap else "dependency-prose",
            )
            item = {
                "id": _qualified(chunk_id, f"g{gap_number}"),
                "category": gap["category"],
                "evidence_ids": [_qualified(chunk_id, evidence_id)],
                "description": gap["description"],
            }
            if "subject" in gap:
                item["subject"] = remap_discovery_endpoint(
                    gap["subject"], evidence_id
                )
            if "reference" in gap:
                reference_id = add_reference(gap["reference"])
                item["reference_id"] = _qualified(chunk_id, reference_id)
            pooled["gaps"].append(item)
    return pooled


def _load_fragment_manifest(path: Path) -> tuple[dict, list[dict]]:
    """Load an ordered worker-fragment manifest without interpreting its contents."""

    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("batch fragment manifest must be a JSON object")
    fragment_paths = payload.get("fragments")
    if not isinstance(fragment_paths, list) or not fragment_paths:
        raise ValueError("batch fragment manifest requires a nonempty fragments array")
    fragments: list[dict] = []
    for raw_path in fragment_paths:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("batch fragment paths must be nonempty strings")
        fragment_path = Path(raw_path).resolve()
        try:
            fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkerFragmentLoadError(
                fragment_path,
                {
                    "code": "invalid-fragment-json",
                    "path": [],
                    "message": "extract fragment is not valid JSON",
                },
            ) from error
        if not isinstance(fragment, dict):
            raise WorkerFragmentLoadError(
                fragment_path,
                {
                    "code": "invalid-fragment-shape",
                    "path": [],
                    "message": "extract fragment must be a JSON object",
                },
            )
        fragments.append(fragment)
    return payload, fragments


def write_json_atomic(payload: dict, out_path: Path) -> None:
    """Write one JSON artifact through atomic sibling replacement."""

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            temporary_path = Path(handle.name)
        temporary_path.replace(out_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: Iterable[str] | None = None) -> None:
    """Pool one inventory-fragment manifest and print its machine-readable summary."""

    parser = argparse.ArgumentParser(description="Pool mathematical inventory fragments.")
    parser.add_argument("fragment_manifest", help="JSON object listing fragment paths")
    parser.add_argument("--chunk-manifest", required=True, help="Inventory ownership manifest")
    parser.add_argument("--out", required=True, help="Pooled inventory destination")
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest_path = Path(args.fragment_manifest).resolve()
    _fragment_manifest, fragments = _load_fragment_manifest(manifest_path)
    chunk_manifest = json.loads(Path(args.chunk_manifest).resolve().read_text(encoding="utf-8"))
    if not isinstance(chunk_manifest, dict):
        raise ValueError("inventory chunk manifest must be a JSON object")
    pooled = pool_inventory_fragments(
        fragments,
        chunk_manifest=chunk_manifest,
    )
    out_path = Path(args.out).resolve()
    write_json_atomic(pooled, out_path)
    print(json.dumps({"fragments": len(fragments), "out": str(out_path)}, indent=2))


class Interface(PythonArgvMachineInterface):
    """Expose non-semantic inventory pooling through the machine protocol."""

    prog = "batch_ir_merger.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
