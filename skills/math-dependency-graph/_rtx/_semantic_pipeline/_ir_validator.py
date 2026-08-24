#!/usr/bin/env python3
"""Validate and repair semantic IR against the pooled inventory."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

import jsonschema


_SKILL_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _SKILL_ROOT / "schemas"
SEMANTIC_SCHEMA_PATH = _SCHEMAS_DIR / "semantic-graph.schema.json"
SEMANTIC_REPAIR_SCHEMA_PATH = _SCHEMAS_DIR / "semantic-repair.schema.json"
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
    if payload.get("files") != files:
        errors.append("semantic files must exactly echo pooled inventory files")

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
        location_error = _location_error(entity.get("statement_location"), files)
        if location_error:
            errors.append(
                f"entity {entity['id']!r} statement location {location_error}"
            )
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
