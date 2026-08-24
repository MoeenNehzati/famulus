#!/usr/bin/env python3
"""Compose frozen inventory interactions with semantic diagnosis and a ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from copy import deepcopy
from typing import Iterable, Mapping, Sequence, cast

from officina.common.atomic_files import atomic_create_bytes, read_regular_file_bytes
from officina.rutter import (
    AnswerSpec,
    DiagnosisCase,
    DiagnoseAnswer,
    EvolutionContext,
    JsonObject,
    LLMStep,
    LLMResponseContext,
    MachineContext,
    MachineResult,
    MachineStep,
    QuestionCase,
    Rutter,
    RutterDefinitionError,
    RutterRegistry,
    Terminal,
    TransitionContext,
    TransitionHook,
    Turn,
    ValidationIssue,
    ValidationReport,
    VoyageResult,
    hook_sequence_after,
)


_RUTTER_NAME = "inquisitive-inventory"
_RUTTER_ID = "math-graph-inquisitive-inventory"
_RECKONING_NAME = "inquisitive-inventory.reckoning.json"
_TRANSITION_HOOK_ID = "inventory-diagnosis"
_REPORT_EVOLUTION = "report"
_RECORD_EVOLUTION = "record"
_MAX_APPENDIX_CASES = 64
_INTERACTION_SLOTS = tuple(
    {"index": index} for index in range(_MAX_APPENDIX_CASES)
)
_CHANGES = ("added", "changed", "deleted")
_FROZEN_GOLD_ASSET_DIR = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "inference-from-random-restarts"
    / "gold"
)
_SKILL_ROOT = Path(__file__).resolve().parents[1]
_INVENTORY_INSTRUCTION_PATH = _SKILL_ROOT / "instructions" / "inventory.md"
_INVENTORY_SCHEMA_PATH = _SKILL_ROOT / "inventory.schema.json"
_BUNDLED_GOLD_ANNOTATION = _FROZEN_GOLD_ASSET_DIR / "gold-annotation.json"
_BUNDLED_GOLD_OVERLAY = _FROZEN_GOLD_ASSET_DIR / "gold-correction-v2.json"
_MINIMAL_FIX = "minimal_fix must be paper-independent and target inventory.md"
_REPORT_REQUEST = (
    "Read the displayed source text, update the prior inventory using the normal "
    "inventory rules, and return the complete cumulative inventory snapshot for "
    "this interaction."
)
_REPORT_TEXT = (
    f"{_INVENTORY_INSTRUCTION_PATH.read_text(encoding='utf-8')}\n\n"
    f"{_REPORT_REQUEST}"
)
_DIAGNOSIS_GUIDANCE = (
    "Distinguish an omitted entity from a reported unresolved endpoint.",
    "Treat a reported edge with an unresolved endpoint as recovered, not omitted.",
    "Distinguish a partially recovered dependency chain from a wholly omitted chain.",
    (
        "Gold truth does not represent a proof environment separately from the "
        "result environment it proves. Do not diagnose the worker's separate "
        "proof node or its proves edge as a mistake, and do not adjust the "
        "worker's path to remove them."
    ),
)


def _without_entry_aliases(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _without_entry_aliases(item)
            for key, item in value.items()
            if key != "alias"
        }
    if isinstance(value, (list, tuple)):
        return [_without_entry_aliases(item) for item in value]
    return value


def _canonical_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_bytes(value: object) -> bytes:
    return _canonical_text(value).encode("utf-8")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _json_object(value: object, label: str) -> dict[str, object]:
    materialized = _plain_json(value)
    if not isinstance(materialized, dict):
        raise ValueError(f"{label} must be a finite JSON object")
    _canonical_bytes(materialized)
    return materialized


def _inventory_alias_signatures(
    value: Mapping[str, object],
) -> dict[str, str] | None:
    alias_profiles: dict[str, dict[str, object]] = {}
    node_sections = value.get("delta_nodes")
    endpoint_context = value.get("endpoint_context")
    if not isinstance(node_sections, Mapping) or not isinstance(
        endpoint_context, Mapping
    ):
        return None

    def profile(alias: str) -> dict[str, object]:
        return alias_profiles.setdefault(
            alias,
            {"delta_nodes": [], "endpoint_context": None, "edge_roles": []},
        )

    for change, entries in node_sections.items():
        if not isinstance(entries, (list, tuple)):
            return None
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("alias"), str
            ):
                return None
            alias = entry["alias"]
            assert isinstance(alias, str)
            delta_nodes = profile(alias)["delta_nodes"]
            assert isinstance(delta_nodes, list)
            delta_nodes.append(
                {
                    "change": str(change),
                    "entry": _without_entry_aliases(entry),
                }
            )
    for alias, sides in endpoint_context.items():
        if not isinstance(alias, str) or not isinstance(sides, Mapping):
            return None
        profile(alias)["endpoint_context"] = _without_entry_aliases(sides)

    edge_sections = value.get("delta_edges")
    if not isinstance(edge_sections, Mapping):
        return None
    for change, entries in edge_sections.items():
        if not isinstance(entries, (list, tuple)):
            return None
        for entry in entries:
            if not isinstance(entry, Mapping):
                return None
            entry_payload = {
                key: _without_entry_aliases(item)
                for key, item in entry.items()
                if key not in {"alias", "before", "after"}
            }
            for side in ("before", "after"):
                record = entry.get(side)
                if not isinstance(record, Mapping):
                    continue
                record_payload = {
                    key: _without_entry_aliases(item)
                    for key, item in record.items()
                    if key not in {"from", "to"}
                }
                for role in ("from", "to"):
                    alias = record.get(role)
                    if not isinstance(alias, str):
                        continue
                    edge_roles = profile(alias)["edge_roles"]
                    assert isinstance(edge_roles, list)
                    edge_roles.append(
                        {
                            "change": str(change),
                            "side": side,
                            "role": role,
                            "entry": entry_payload,
                            "record": record_payload,
                        }
                    )

    signatures: dict[str, str] = {}
    for alias, alias_profile in alias_profiles.items():
        for key in ("delta_nodes", "edge_roles"):
            values = alias_profile[key]
            assert isinstance(values, list)
            alias_profile[key] = sorted(values, key=_canonical_text)
        signatures[alias] = _canonical_text(alias_profile)
    return signatures


def _inventory_profile(
    value: Mapping[str, object], alias_mapping: Mapping[str, str]
) -> dict[str, object]:
    node_sections = value["delta_nodes"]
    endpoint_context = value["endpoint_context"]
    edge_sections = value["delta_edges"]
    assert isinstance(node_sections, Mapping)
    assert isinstance(endpoint_context, Mapping)
    assert isinstance(edge_sections, Mapping)

    normalized_nodes: dict[str, list[object]] = {}
    for change, entries in node_sections.items():
        assert isinstance(entries, (list, tuple))
        normalized_entries: list[object] = []
        for entry in entries:
            assert isinstance(entry, Mapping)
            alias = entry["alias"]
            assert isinstance(alias, str)
            normalized_entry = {
                key: _without_entry_aliases(item)
                for key, item in entry.items()
                if key != "alias"
            }
            normalized_entry["alias"] = alias_mapping[alias]
            normalized_entries.append(normalized_entry)
        normalized_nodes[str(change)] = sorted(
            normalized_entries, key=_canonical_text
        )

    normalized_edges: dict[str, list[object]] = {}
    for change, entries in edge_sections.items():
        assert isinstance(entries, (list, tuple))
        normalized_entries: list[object] = []
        for entry in entries:
            assert isinstance(entry, Mapping)
            normalized_entry = {
                key: _without_entry_aliases(item)
                for key, item in entry.items()
                if key != "alias"
            }
            for side in ("before", "after"):
                record = normalized_entry.get(side)
                if not isinstance(record, Mapping):
                    continue
                projected = dict(record)
                for role in ("from", "to"):
                    alias = record.get(role)
                    projected[role] = (
                        {"node_alias": alias_mapping[alias]}
                        if isinstance(alias, str)
                        else {"missing_endpoint": alias, "side": side}
                    )
                normalized_entry[side] = projected
            normalized_entries.append(normalized_entry)
        normalized_edges[str(change)] = sorted(
            normalized_entries, key=_canonical_text
        )

    normalized_context = []
    for alias, sides in endpoint_context.items():
        assert isinstance(alias, str)
        assert isinstance(sides, Mapping)
        normalized_context.append(
            {
                "alias": alias_mapping[alias],
                "value": _without_entry_aliases(sides),
            }
        )
    normalized_context.sort(key=_canonical_text)
    return {
        "delta_nodes": normalized_nodes,
        "delta_edges": normalized_edges,
        "endpoint_context": normalized_context,
    }


def semantic_inventory_equal(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    actual_signatures = _inventory_alias_signatures(actual)
    expected_signatures = _inventory_alias_signatures(expected)
    if actual_signatures is None or expected_signatures is None:
        return _without_entry_aliases(actual) == _without_entry_aliases(expected)
    if sorted(actual_signatures.values()) != sorted(expected_signatures.values()):
        return False

    candidates = {
        actual_alias: tuple(
            expected_alias
            for expected_alias, expected_signature in expected_signatures.items()
            if expected_signature == actual_signature
        )
        for actual_alias, actual_signature in actual_signatures.items()
    }
    ordered_actual = sorted(
        actual_signatures,
        key=lambda alias: (len(candidates[alias]), alias),
    )
    expected_profile = _inventory_profile(
        expected,
        {alias: alias for alias in expected_signatures},
    )
    mapping: dict[str, str] = {}
    used_expected: set[str] = set()

    def profiles_match(index: int) -> bool:
        if index == len(ordered_actual):
            return _inventory_profile(actual, mapping) == expected_profile
        actual_alias = ordered_actual[index]
        for expected_alias in candidates[actual_alias]:
            if expected_alias in used_expected:
                continue
            mapping[actual_alias] = expected_alias
            used_expected.add(expected_alias)
            if profiles_match(index + 1):
                return True
            used_expected.remove(expected_alias)
            del mapping[actual_alias]
        return False

    return profiles_match(0)


def inventory_verdict(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> str:
    return "equal" if semantic_inventory_equal(actual, expected) else "different"


def _valid_change_ids(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(_CHANGES):
        return False
    seen: set[str] = set()
    for change in _CHANGES:
        ids = value[change]
        if (
            not isinstance(ids, (list, tuple))
            or any(type(item) is not str or not item for item in ids)
            or list(ids) != sorted(ids)
            or len(ids) != len(set(ids))
            or seen.intersection(ids)
        ):
            return False
        seen.update(cast(list[str] | tuple[str, ...], ids))
    return True


def _invalid(path: tuple[str | int, ...], code: str, message: str) -> ValidationReport:
    return ValidationReport(False, (ValidationIssue(path, code, message),))


def _charter_cases(context: EvolutionContext) -> list[dict[str, object]]:
    cases = context.charter.data.get("cases")
    if not isinstance(cases, (list, tuple)):
        raise RutterDefinitionError("inventory Charter cases must be an array")
    return [_json_object(case, "inventory case") for case in cases]


def _frozen_charter_text(context: EvolutionContext, name: str) -> str:
    text = context.charter.data.get(f"{name}_text")
    digest = context.charter.data.get(f"{name}_sha256")
    if type(text) is not str or type(digest) is not str:
        raise RutterDefinitionError(f"inventory Charter {name} snapshot is invalid")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RutterDefinitionError(f"inventory Charter {name} snapshot hash is invalid")
    return text


def _output_schema(context: EvolutionContext) -> JsonObject:
    schema_text = _frozen_charter_text(context, "inventory_schema")
    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as error:
        raise RutterDefinitionError("inventory Charter schema snapshot is invalid") from error
    return _json_object(schema, "inventory Charter schema")


def _case_index(context: EvolutionContext) -> int:
    return len(context.history.subrutters(transition_hook_id=_TRANSITION_HOOK_ID))


def _report_data(context: EvolutionContext) -> JsonObject:
    cases = _charter_cases(context)
    index = _case_index(context)
    if index >= len(cases):
        raise RutterDefinitionError("no inventory case remains")
    case = cases[index]
    source = _json_object(case.get("source"), "inventory source case")
    return {
        "interaction": {
            "interaction_index": index + 1,
            "sequence_id": source["sequence_id"],
            "source_sha256": hashlib.sha256(_canonical_bytes(source)).hexdigest(),
            "text": source["text"],
            "prior_inventory": source["before"],
        },
        "output_schema": _output_schema(context),
        "minimal_fix_constraint": _MINIMAL_FIX,
    }


def _validate_report(context: LLMResponseContext) -> ValidationReport:
    response = context.response
    if response.outcome != "reported":
        return _invalid(("outcome",), "invalid-outcome", "outcome must be reported")
    evidence = response.evidence
    if set(evidence) != {"sequence_id", "inventory"}:
        return _invalid(
            ("evidence",),
            "invalid-inventory-report",
            "evidence must contain sequence_id and inventory",
        )
    cases = _charter_cases(context.evolution)
    index = _case_index(context.evolution)
    if index >= len(cases):
        return _invalid(
            ("evidence", "sequence_id"),
            "sequence-exhausted",
            "no inventory sequence remains",
        )
    source = _json_object(cases[index].get("source"), "inventory source case")
    if evidence["sequence_id"] != source["sequence_id"]:
        return _invalid(
            ("evidence", "sequence_id"),
            "wrong-sequence",
            "sequence_id must match the displayed sequence",
        )
    try:
        inventory = _json_object(evidence["inventory"], "reported inventory")
        _files(inventory)
        _record_map(inventory, "nodes")
        _record_map(inventory, "edges")
        _canonical_bytes(inventory)
    except (TypeError, ValueError) as error:
        return _invalid(
            ("evidence", "inventory"),
            "invalid-inventory",
            str(error),
        )
    return ValidationReport(True)


def _record_map(snapshot: Mapping[str, object], kind: str) -> dict[str, dict[str, object]]:
    records = snapshot.get(kind)
    if not isinstance(records, (list, tuple)):
        raise ValueError(f"inventory snapshot {kind} must be an array")
    indexed: dict[str, dict[str, object]] = {}
    for value in records:
        record = _json_object(value, f"inventory {kind} record")
        local_id = record.get("local_id")
        if type(local_id) is not str or not local_id or local_id in indexed:
            raise ValueError(f"inventory snapshot {kind} IDs must be unique")
        indexed[local_id] = record
    return indexed


def _files(snapshot: Mapping[str, object]) -> list[str]:
    files = snapshot.get("files")
    if not isinstance(files, (list, tuple)) or any(type(item) is not str for item in files):
        raise ValueError("inventory snapshot files must be a string array")
    return list(cast(list[str] | tuple[str, ...], files))


def _locations(record: Mapping[str, object], files: list[str]) -> list[dict[str, object]]:
    location = record.get("location")
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or any(type(item) is not int for item in location)
    ):
        raise ValueError("inventory record location must contain three integers")
    file_index, start, end = cast(tuple[int, int, int], tuple(location))
    if file_index < 0 or file_index >= len(files) or start < 1 or end < start:
        raise ValueError("inventory record location is outside its source files")
    return [{"file": files[file_index], "start_line": start, "end_line": end}]


def _node_view(record: Mapping[str, object], files: list[str]) -> dict[str, object]:
    description = record.get("summary", record.get("description"))
    if type(description) is not str or not description:
        raise ValueError("inventory node description must be nonempty")
    return {
        "description": description,
        "locations": _locations(record, files),
        "properties": {
            key: _plain_json(item)
            for key, item in record.items()
            if key not in {"local_id", "location", "summary", "description"}
        },
    }


def _endpoint_view(record: Mapping[str, object], files: list[str]) -> dict[str, object]:
    node = _node_view(record, files)
    return {"description": node["description"], "locations": node["locations"]}


def _render_actual_answer(
    source: Mapping[str, object], report: Mapping[str, object]
) -> dict[str, object]:
    before = _json_object(source.get("before"), "before snapshot")
    after = _json_object(report.get("inventory"), "reported inventory")
    before_nodes = _record_map(before, "nodes")
    after_nodes = _record_map(after, "nodes")
    before_edges = _record_map(before, "edges")
    after_edges = _record_map(after, "edges")
    before_files = _files(before)
    after_files = _files(after)
    def changes(
        before_records: Mapping[str, Mapping[str, object]],
        after_records: Mapping[str, Mapping[str, object]],
    ) -> dict[str, list[str]]:
        before_ids = set(before_records)
        after_ids = set(after_records)
        return {
            "added": sorted(after_ids - before_ids),
            "changed": sorted(
                local_id
                for local_id in before_ids & after_ids
                if _canonical_bytes(before_records[local_id])
                != _canonical_bytes(after_records[local_id])
            ),
            "deleted": sorted(before_ids - after_ids),
        }

    node_changes = changes(before_nodes, after_nodes)
    edge_changes = changes(before_edges, after_edges)

    node_aliases: dict[str, str] = {}
    unresolved: dict[tuple[str, str], tuple[str, dict[str, object], list[dict[str, object]]]] = {}

    def node_alias(local_id: str) -> str:
        return node_aliases.setdefault(local_id, f"actual-node-{len(node_aliases) + 1:03d}")

    def endpoint_alias(
        endpoint: object,
        *,
        side: str,
        edge_record: Mapping[str, object],
        files: list[str],
    ) -> str:
        if not isinstance(endpoint, Mapping):
            raise ValueError("inventory edge endpoint must be an object")
        if set(endpoint) == {"local_node"} and type(endpoint["local_node"]) is str:
            return node_alias(cast(str, endpoint["local_node"]))
        payload = endpoint.get("unresolved")
        if set(endpoint) != {"unresolved"} or not isinstance(payload, Mapping):
            raise ValueError("inventory edge endpoint has an invalid form")
        materialized = _json_object(payload, "unresolved endpoint")
        key = (side, _canonical_text(materialized))
        if key not in unresolved:
            unresolved[key] = (
                f"actual-unresolved-{side}-{len(unresolved) + 1:03d}",
                materialized,
                _locations(edge_record, files),
            )
        return unresolved[key][0]

    required_sides = {
        "added": (("after", after_nodes, after_edges, after_files),),
        "changed": (
            ("before", before_nodes, before_edges, before_files),
            ("after", after_nodes, after_edges, after_files),
        ),
        "deleted": (("before", before_nodes, before_edges, before_files),),
    }

    rendered_nodes: dict[str, list[dict[str, object]]] = {}
    for change in _CHANGES:
        entries: list[dict[str, object]] = []
        for local_id in cast(list[str], node_changes[change]):
            entry: dict[str, object] = {"alias": node_alias(local_id)}
            missing: list[str] = []
            for side, nodes, _edges, files in required_sides[change]:
                if local_id in nodes:
                    entry[side] = _node_view(nodes[local_id], files)
                else:
                    missing.append(side)
            if missing:
                entry["diagnostic"] = {
                    "code": "missing-record-side",
                    "reported_id": local_id,
                    "missing_sides": missing,
                }
            entries.append(entry)
        rendered_nodes[change] = entries

    rendered_edges: dict[str, list[dict[str, object]]] = {}
    referenced_nodes: set[str] = set()
    for change in _CHANGES:
        entries = []
        for local_id in cast(list[str], edge_changes[change]):
            entry = {"alias": f"actual-edge-{local_id}"}
            missing = []
            for side, _nodes, edges, files in required_sides[change]:
                edge = edges.get(local_id)
                if edge is None:
                    missing.append(side)
                    continue
                source_endpoint = edge.get("from")
                target_endpoint = edge.get("to")
                for endpoint in (source_endpoint, target_endpoint):
                    if isinstance(endpoint, Mapping) and type(endpoint.get("local_node")) is str:
                        referenced_nodes.add(cast(str, endpoint["local_node"]))
                entry[side] = {
                    "from": endpoint_alias(
                        source_endpoint, side=side, edge_record=edge, files=files
                    ),
                    "to": endpoint_alias(
                        target_endpoint, side=side, edge_record=edge, files=files
                    ),
                    "description": edge.get("description"),
                    "locations": _locations(edge, files),
                    "properties": {
                        key: _plain_json(item)
                        for key, item in edge.items()
                        if key
                        not in {
                            "local_id",
                            "location",
                            "description",
                            "from",
                            "to",
                        }
                    },
                }
            if missing:
                entry["diagnostic"] = {
                    "code": "missing-record-side",
                    "reported_id": local_id,
                    "missing_sides": missing,
                }
            entries.append(entry)
        rendered_edges[change] = entries

    endpoint_context: dict[str, object] = {}
    for local_id in sorted(referenced_nodes):
        sides: dict[str, object] = {}
        if local_id in before_nodes:
            sides["before"] = _endpoint_view(before_nodes[local_id], before_files)
        if local_id in after_nodes:
            sides["after"] = _endpoint_view(after_nodes[local_id], after_files)
        if not sides:
            sides["diagnostic"] = {
                "code": "unknown-endpoint-id",
                "reported_id": local_id,
            }
        endpoint_context[node_alias(local_id)] = sides
    for alias, payload, locations in unresolved.values():
        statement = payload.get("statement")
        if type(statement) is not str or not statement:
            raise ValueError("unresolved endpoint statement must be nonempty")
        endpoint_context[alias] = {
            alias.split("-")[2]: {
                "description": statement,
                "locations": locations,
                "properties": {
                    key: value for key, value in payload.items() if key != "statement"
                },
            }
        }
    return {
        "delta_nodes": rendered_nodes,
        "delta_edges": rendered_edges,
        "endpoint_context": endpoint_context,
    }


def _diagnosis_charter(item: JsonObject, context: TransitionContext) -> JsonObject:
    index = item.get("index")
    cases = _charter_cases(context.evolution)
    if type(index) is not int or index < 0 or index >= len(cases):
        raise RutterDefinitionError("inventory case sequence is exhausted")
    if not isinstance(context.record, Turn) or context.record.response is None:
        raise RutterDefinitionError("inventory diagnosis requires an accepted report")
    case = cases[index]
    source = _json_object(case.get("source"), "inventory source case")
    gold = _json_object(case.get("gold"), "inventory gold case")
    response = context.record.response
    if response.evidence.get("sequence_id") != source.get("sequence_id"):
        raise RutterDefinitionError("accepted report does not match the selected case")
    actual = _render_actual_answer(source, response.evidence)
    expected = {
        "delta_nodes": gold["delta_nodes"],
        "delta_edges": gold["delta_edges"],
        "endpoint_context": gold["endpoint_context"],
    }
    question = QuestionCase(
        f"inventory-sequence-{source['sequence_id']}",
        "Which new nodes and direct dependency edges appear in this sequence?",
        _canonical_text(expected),
        format_hint={
            "delta_nodes": {change: [] for change in _CHANGES},
            "delta_edges": {change: [] for change in _CHANGES},
            "endpoint_context": {},
        },
        metadata={
            "sequence_id": source["sequence_id"],
            "minimal_fix_constraint": _MINIMAL_FIX,
            "diagnosis_guidance": _DIAGNOSIS_GUIDANCE,
        },
    )
    return DiagnosisCase(
        question,
        _canonical_text(actual),
        semantic_inventory_equal(actual, expected),
        ask_for_fix=True,
    ).to_json()


def _write_ledger_row(root: Path, machine_id: str, row: dict[str, object]) -> None:
    ledger = root / "ledger"
    path = ledger / f"{machine_id}.json"
    serialized = _canonical_bytes(row) + b"\n"
    created = atomic_create_bytes(path, serialized, allowed_root=root, mode=0o600)
    if not created and read_regular_file_bytes(path, allowed_root=root) != serialized:
        raise ValueError("existing inventory ledger row differs from the replay")


def _record_iteration(context: MachineContext) -> MachineResult:
    """Persist the accepted inventory report and choose whether another remains."""
    turn = context.evolution.history.require_latest_turn(_REPORT_EVOLUTION)
    if turn.response is None:
        raise RutterDefinitionError("inventory ledger requires an accepted report")
    hook_runs = context.evolution.history.hook_runs(
        transition_hook_id=_TRANSITION_HOOK_ID,
        transition_id=turn.record_id,
    )
    if len(hook_runs) != 1:
        raise RutterDefinitionError(
            "inventory ledger requires exactly one attached diagnosis"
        )
    hook_run = hook_runs[0]
    root_value = context.evolution.charter.data.get("experiment_dir")
    if type(root_value) is not str:
        raise RutterDefinitionError("inventory experiment directory is unavailable")
    row = {
        "ledger_version": 1,
        "machine_id": context.machine_id,
        "transition_hook_id": _TRANSITION_HOOK_ID,
        "transition_id": turn.record_id,
        "sequence_id": turn.response.evidence["sequence_id"],
        "message": _plain_json(turn.message.to_json()),
        "response": _plain_json(turn.response.to_json()),
        "verdict": hook_run.result.outcome,
        "child_result": _plain_json(hook_run.result.to_json()),
    }
    _write_ledger_row(Path(root_value), context.machine_id, row)
    total = len(_charter_cases(context.evolution))
    completed = len(
        context.evolution.history.subrutters(
            transition_hook_id=_TRANSITION_HOOK_ID
        )
    )
    return MachineResult("done" if completed == total else "more", row)


def _complete_result(context: EvolutionContext) -> VoyageResult:
    completed = len(
        context.history.subrutters(transition_hook_id=_TRANSITION_HOOK_ID)
    )
    return VoyageResult("complete", {"iterations": completed})


class InquisitiveInventoryRutter(Rutter):
    rutter_id = _RUTTER_ID
    definition_version = 4
    initial_evolution_id = _REPORT_EVOLUTION

    def define_evolutions(self) -> Mapping[str, object]:
        return {
            _REPORT_EVOLUTION: LLMStep(
                _REPORT_TEXT,
                answer=AnswerSpec(
                    {
                        "reported": {
                            "sequence_id": "positive integer",
                            "inventory": "inventory JSON object",
                        }
                    }
                ),
                data=_report_data,
                validate=_validate_report,
                next_on_outcome=_RECORD_EVOLUTION,
            ),
            _RECORD_EVOLUTION: MachineStep(
                _record_iteration,
                mode="repeat-safe",
                next_on_outcome={"more": _REPORT_EVOLUTION, "done": "complete"},
            ),
            "complete": Terminal(_complete_result),
        }

    def define_transition_hooks(self) -> tuple[TransitionHook, ...]:
        return (
            hook_sequence_after(
                id=_TRANSITION_HOOK_ID,
                after_evolutions={_REPORT_EVOLUTION},
                items=_INTERACTION_SLOTS,
                child=DiagnoseAnswer,
                charter=_diagnosis_charter,
            ),
        )


def _registry(experiment_dir: Path) -> RutterRegistry:
    return RutterRegistry({_RUTTER_NAME: InquisitiveInventoryRutter}, experiment_dir)


def _seal_source_cases(value: object) -> list[dict[str, object]]:
    materialized = _plain_json(value)
    if not isinstance(materialized, list) or not materialized:
        raise ValueError("source cases must be a nonempty frozen JSON array")
    if len(materialized) > _MAX_APPENDIX_CASES:
        raise ValueError("source cases exceed the frozen appendix capacity")
    cases: list[dict[str, object]] = []
    seen: set[int] = set()
    for value in materialized:
        source = _json_object(value, "inventory source case")
        sequence_id = source.get("sequence_id")
        if type(sequence_id) is not int or sequence_id < 1 or sequence_id in seen:
            raise ValueError("source sequence IDs must be unique positive integers")
        if set(source) != {"sequence_id", "text", "before", "after"}:
            raise ValueError(
                "source cases must contain sequence_id, text, before, and after"
            )
        if not isinstance(source["text"], str) or not source["text"].strip():
            raise ValueError("source case text must be nonempty")
        for side in ("before", "after"):
            snapshot = _json_object(source[side], f"inventory {side} snapshot")
            _files(snapshot)
            _record_map(snapshot, "nodes")
            _record_map(snapshot, "edges")
        seen.add(sequence_id)
        cases.append(source)
    if [case["sequence_id"] for case in cases] != sorted(seen):
        raise ValueError("source cases must be ordered by sequence_id")
    _canonical_bytes(cases)
    return cases


def _freeze_utf8_file(path: Path, label: str) -> tuple[str, str]:
    try:
        content = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is not readable UTF-8") from error
    if not content:
        raise ValueError(f"{label} must not be empty")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _frozen_worker_contract() -> dict[str, object]:
    instruction, instruction_sha256 = _freeze_utf8_file(
        _INVENTORY_INSTRUCTION_PATH, "canonical inventory instructions"
    )
    schema, schema_sha256 = _freeze_utf8_file(
        _INVENTORY_SCHEMA_PATH, "canonical inventory schema"
    )
    try:
        _json_object(json.loads(schema), "canonical inventory schema")
    except json.JSONDecodeError as error:
        raise ValueError("canonical inventory schema is invalid JSON") from error
    return {
        "inventory_instruction_text": instruction,
        "inventory_instruction_sha256": instruction_sha256,
        "inventory_schema_text": schema,
        "inventory_schema_sha256": schema_sha256,
    }


def _seal_cases(
    source_cases: list[dict[str, object]], gold_cases: object
) -> list[dict[str, object]]:
    materialized = _plain_json(gold_cases)
    if not isinstance(materialized, list) or len(materialized) != len(source_cases):
        raise ValueError("gold cases must match the completed source sequences")
    if len(materialized) > _MAX_APPENDIX_CASES:
        raise ValueError("gold cases exceed the frozen appendix capacity")
    sealed: list[dict[str, object]] = []
    for source, value in zip(source_cases, materialized, strict=True):
        gold = _json_object(value, "inventory gold case")
        if gold.get("sequence_id") != source["sequence_id"] or not {
            "delta_nodes",
            "delta_edges",
            "endpoint_context",
        }.issubset(gold):
            raise ValueError("gold case does not match its source sequence")
        sealed.append({"source": source, "gold": gold})
    _canonical_bytes(sealed)
    return sealed


def _load_iterator_source_cases(
    iterator_state_dir: Path, worker_index: int
) -> list[dict[str, object]]:
    """Load and authenticate one worker's immutable closed-sequence snapshots."""

    if type(worker_index) is not int or worker_index < 1:
        raise ValueError("worker_index must be a positive integer")
    database = Path(iterator_state_dir).resolve() / "iterator.sqlite3"
    if not database.is_file():
        raise ValueError("iterator database is missing")
    try:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT artifact.sequence_id, artifact.before_snapshot_json, "
                "artifact.after_snapshot_json, artifact.before_manifest_json, "
                "artifact.after_manifest_json, artifact.delta_json, "
                "first_unit.ordinal, last_unit.ordinal "
                "FROM sequence_delta_artifacts AS artifact "
                "JOIN attention_sequences AS sequence "
                "ON sequence.id = artifact.sequence_id "
                "JOIN units AS first_unit ON first_unit.id = sequence.first_unit_id "
                "JOIN units AS last_unit ON last_unit.id = sequence.last_unit_id "
                "WHERE sequence.worker_index = ? ORDER BY artifact.sequence_id",
                (worker_index,),
            ).fetchall()
            unit_rows = connection.execute(
                "SELECT ordinal, text FROM units ORDER BY ordinal"
            ).fetchall()
    except sqlite3.Error as error:
        raise ValueError("iterator sequence delta artifacts are unavailable") from error
    cases: list[dict[str, object]] = []
    unit_text = {int(ordinal): str(value) for ordinal, value in unit_rows}
    for row in rows:
        (
            sequence_id,
            before_raw,
            after_raw,
            before_manifest_raw,
            after_manifest_raw,
            delta_raw,
            first_ordinal,
            last_ordinal,
        ) = row
        try:
            before = json.loads(before_raw)
            after = json.loads(after_raw)
            before_manifest = json.loads(before_manifest_raw)
            after_manifest = json.loads(after_manifest_raw)
            delta = json.loads(delta_raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("iterator sequence delta artifact is not valid JSON") from error
        for side, snapshot, manifest in (
            ("before", before, before_manifest),
            ("after", after, after_manifest),
        ):
            if not isinstance(manifest, Mapping) or manifest.get(
                "payload_sha256"
            ) != hashlib.sha256(_canonical_bytes(snapshot)).hexdigest():
                raise ValueError(
                    f"iterator {side} manifest does not authenticate its snapshot"
                )
        if not isinstance(delta, Mapping):
            raise ValueError("iterator sequence delta classification is invalid")
        for kind in ("nodes", "edges"):
            changes = delta.get(kind)
            if not isinstance(changes, Mapping) or not _valid_change_ids(
                {change: changes.get(change) for change in _CHANGES}
            ):
                raise ValueError(
                    f"iterator sequence delta {kind} classification is invalid"
                )
        cases.append(
            {
                "sequence_id": int(sequence_id),
                "text": "\n".join(
                    unit_text[ordinal]
                    for ordinal in range(int(first_ordinal), int(last_ordinal) + 1)
                ),
                "before": before,
                "after": after,
            }
        )
    if not cases:
        raise ValueError("worker has no closed sequence delta artifacts")
    return _seal_source_cases(cases)


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error
    return _json_object(value, label)


def _gold_locations(value: Iterable[object]) -> list[dict[str, object]]:
    locations: dict[str, dict[str, object]] = {}
    for raw in value:
        location = _json_object(raw, "gold location")
        if (
            set(location) != {"file", "start_line", "end_line"}
            or not isinstance(location["file"], str)
            or type(location["start_line"]) is not int
            or type(location["end_line"]) is not int
            or location["start_line"] < 1
            or location["end_line"] < location["start_line"]
        ):
            raise ValueError("gold locations must be finite source intervals")
        locations[_canonical_text(location)] = location
    return sorted(
        locations.values(),
        key=lambda item: (item["file"], item["start_line"], item["end_line"]),
    )


def _apply_gold_overlay(
    base: dict[str, object], overlay: dict[str, object], base_bytes: bytes
) -> dict[str, object]:
    if overlay.get("status") != "accepted-adjudicated-overlay":
        raise ValueError("gold correction overlay is not accepted")
    if overlay.get("base_gold_sha256") != hashlib.sha256(base_bytes).hexdigest():
        raise ValueError("gold correction overlay does not authenticate its base")
    if overlay.get("base_benchmark_version") != base.get("benchmark_version"):
        raise ValueError("gold correction overlay targets another benchmark version")
    nodes_raw = base.get("nodes")
    edges_raw = base.get("edges")
    operations = overlay.get("operations")
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list) or not isinstance(operations, list):
        raise ValueError("gold annotation or correction overlay has invalid records")
    nodes = {
        str(node["id"]): deepcopy(_json_object(node, "gold node"))
        for node in nodes_raw
        if isinstance(node, Mapping) and isinstance(node.get("id"), str)
    }
    edges = {
        str(edge["id"]): deepcopy(_json_object(edge, "gold edge"))
        for edge in edges_raw
        if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)
    }
    if len(nodes) != len(nodes_raw) or len(edges) != len(edges_raw):
        raise ValueError("gold annotation IDs must be unique strings")
    for raw in operations:
        operation = _json_object(raw, "gold correction operation")
        kind = operation.get("operation")
        record_id = operation.get("id")
        if not isinstance(record_id, str):
            raise ValueError("gold correction operation requires an ID")
        if kind == "delete-node":
            if nodes.pop(record_id, None) is None:
                raise ValueError("gold correction deletes an absent node")
        elif kind == "delete-edge":
            if edges.pop(record_id, None) is None:
                raise ValueError("gold correction deletes an absent edge")
        elif kind == "add-node-location":
            node = nodes.get(record_id)
            if node is None:
                raise ValueError("gold correction locates an absent node")
            current = node.get("locations")
            if not isinstance(current, list):
                raise ValueError("gold node locations are invalid")
            node["locations"] = _gold_locations(
                [*current, operation.get("location")]
            )
        elif kind == "add-edge":
            if record_id in edges:
                raise ValueError("gold correction adds a duplicate edge")
            if not isinstance(operation.get("from"), str) or not isinstance(operation.get("to"), str):
                raise ValueError("gold correction edge endpoints are invalid")
            if operation["from"] not in nodes or operation["to"] not in nodes:
                raise ValueError("gold correction edge endpoint is absent")
            edges[record_id] = {
                key: deepcopy(value)
                for key, value in operation.items()
                if key != "operation"
            }
            edges[record_id]["directness_reason"] = str(
                operation.get("reason", "Accepted adjudicated direct dependency.")
            )
            edges[record_id].setdefault("confidence", "high")
            edges[record_id].setdefault("explicit_reference_labels", [])
        else:
            raise ValueError("gold correction operation is unsupported")
    counts = overlay.get("derived_counts")
    if not isinstance(counts, Mapping) or counts.get("nodes") != len(nodes) or counts.get("edges") != len(edges):
        raise ValueError("gold correction derived counts do not match its result")
    result = deepcopy(base)
    result["nodes"] = list(nodes.values())
    result["edges"] = list(edges.values())
    return result


def _slice_snapshot(fragment: Mapping[str, object], counts: Mapping[str, object]) -> dict[str, object]:
    snapshot = deepcopy(dict(fragment))
    for kind in ("nodes", "edges", "gaps"):
        records = fragment.get(kind)
        count = counts.get(kind)
        if not isinstance(records, list) or type(count) is not int or count < 0 or count > len(records):
            raise ValueError("legacy iterator counts cannot reconstruct a snapshot")
        snapshot[kind] = deepcopy(records[:count])
    return snapshot


def _legacy_source_cases(
    iterator_state_dir: Path,
    worker_index: int,
    inventory_fragment_paths: Sequence[Path],
) -> tuple[list[dict[str, object]], dict[int, set[tuple[str, int]]]]:
    fragments: dict[int, dict[str, object]] = {}
    for path in inventory_fragment_paths:
        fragment = _read_json_object(Path(path), "authenticated inventory fragment")
        chunk_id = fragment.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id.startswith("iterator-worker-"):
            raise ValueError("inventory fragment lacks an iterator worker identity")
        try:
            index = int(chunk_id.removeprefix("iterator-worker-"))
        except ValueError as error:
            raise ValueError("inventory fragment worker identity is invalid") from error
        if index in fragments:
            raise ValueError("inventory fragments repeat a worker identity")
        _files(fragment)
        _record_map(fragment, "nodes")
        _record_map(fragment, "edges")
        fragments[index] = fragment
    fragment = fragments.get(worker_index)
    if fragment is None:
        raise ValueError("authenticated inventory fragment for worker is missing")
    database = Path(iterator_state_dir).resolve() / "iterator.sqlite3"
    try:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT sequence.id, first_unit.ordinal, last_unit.ordinal, "
                "sequence.before_sha256, sequence.before_counts_json, "
                "sequence.after_sha256, sequence.after_counts_json "
                "FROM attention_sequences AS sequence "
                "JOIN units AS first_unit ON first_unit.id = sequence.first_unit_id "
                "JOIN units AS last_unit ON last_unit.id = sequence.last_unit_id "
                "WHERE sequence.worker_index = ? ORDER BY sequence.id",
                (worker_index,),
            ).fetchall()
            unit_rows = connection.execute(
                "SELECT ordinal, text, metadata_json FROM units ORDER BY ordinal"
            ).fetchall()
    except sqlite3.Error as error:
        raise ValueError("legacy iterator sequence authority is unavailable") from error
    unit_coordinates: dict[int, set[tuple[str, int]]] = {}
    unit_text: dict[int, str] = {}
    for ordinal, text, metadata_raw in unit_rows:
        try:
            metadata = json.loads(metadata_raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("legacy iterator unit metadata is invalid") from error
        coordinates = metadata.get("coordinates") if isinstance(metadata, Mapping) else None
        if not isinstance(coordinates, list):
            raise ValueError("legacy iterator unit coordinates are unavailable")
        unit_coordinates[int(ordinal)] = {
            (str(item["source"]), int(item["line"]))
            for item in coordinates
            if isinstance(item, Mapping)
            and isinstance(item.get("source"), str)
            and type(item.get("line")) is int
        }
        unit_text[int(ordinal)] = str(text)
    cases: list[dict[str, object]] = []
    coverage: dict[int, set[tuple[str, int]]] = {}
    for sequence_id, first_ordinal, last_ordinal, before_hash, before_counts_raw, after_hash, after_counts_raw in rows:
        try:
            before_counts = json.loads(before_counts_raw)
            after_counts = json.loads(after_counts_raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("legacy iterator sequence counts are invalid") from error
        if not isinstance(before_counts, Mapping) or not isinstance(after_counts, Mapping):
            raise ValueError("legacy iterator sequence counts are invalid")
        before = _slice_snapshot(fragment, before_counts)
        after = _slice_snapshot(fragment, after_counts)
        if hashlib.sha256(_canonical_bytes(before)).hexdigest() != before_hash or hashlib.sha256(_canonical_bytes(after)).hexdigest() != after_hash:
            raise ValueError("authenticated fragment does not reproduce iterator snapshots")
        cases.append(
            {
                "sequence_id": int(sequence_id),
                "text": "\n".join(
                    unit_text[ordinal]
                    for ordinal in range(first_ordinal, last_ordinal + 1)
                ),
                "before": before,
                "after": after,
            }
        )
        coverage[int(sequence_id)] = set().union(
            *(unit_coordinates.get(ordinal, set()) for ordinal in range(first_ordinal, last_ordinal + 1))
        )
    if not cases:
        raise ValueError("worker has no closed legacy iterator sequences")
    return _seal_source_cases(cases), coverage


def _location_overlaps(location: Mapping[str, object], coverage: set[tuple[str, int]]) -> bool:
    file = location.get("file")
    start = location.get("start_line")
    end = location.get("end_line")
    return (
        isinstance(file, str)
        and type(start) is int
        and type(end) is int
        and any((file, line) in coverage for line in range(start, end + 1))
    )


def _gold_cases_from_annotation(
    sequence_ids: Sequence[int],
    coverage: Mapping[int, set[tuple[str, int]]],
    corrected: Mapping[str, object],
) -> list[dict[str, object]]:
    nodes_raw = corrected.get("nodes")
    edges_raw = corrected.get("edges")
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        raise ValueError("corrected gold graph is invalid")
    nodes = [_json_object(value, "gold node") for value in nodes_raw]
    edges = [_json_object(value, "gold edge") for value in edges_raw]

    def owner(record: Mapping[str, object], location_key: str) -> int | None:
        raw_locations = record.get(location_key)
        if not isinstance(raw_locations, list):
            raise ValueError("gold record locations are invalid")
        locations = _gold_locations(raw_locations)
        return next(
            (
                sequence_id
                for sequence_id in sequence_ids
                if any(_location_overlaps(location, coverage[sequence_id]) for location in locations)
            ),
            None,
        )

    node_by_id = {str(node["id"]): node for node in nodes}
    node_owner = {str(node["id"]): owner(node, "locations") for node in nodes}
    edge_owner = {str(edge["id"]): owner(edge, "evidence") for edge in edges}

    def node_record(node: Mapping[str, object]) -> dict[str, object]:
        locations = node.get("locations")
        assert isinstance(locations, list)
        return {
            "description": node.get("description"),
            "locations": _gold_locations(locations),
            "properties": {
                key: deepcopy(value)
                for key, value in node.items()
                if key not in {"id", "description", "locations"}
            },
        }

    cases: list[dict[str, object]] = []
    for sequence_id in sequence_ids:
        owned_nodes = [node for node in nodes if node_owner[str(node["id"])] == sequence_id]
        owned_edges = [edge for edge in edges if edge_owner[str(edge["id"])] == sequence_id]
        endpoint_ids = {
            str(edge[role]) for edge in owned_edges for role in ("from", "to")
        }
        endpoint_context = {
            node_id: {"after": {key: value for key, value in node_record(node_by_id[node_id]).items() if key != "properties"}}
            for node_id in sorted(endpoint_ids)
        }
        cases.append(
            {
                "sequence_id": sequence_id,
                "delta_nodes": {
                    "added": [
                        {"alias": str(node["id"]), "after": node_record(node)}
                        for node in owned_nodes
                    ],
                    "changed": [],
                    "deleted": [],
                },
                "delta_edges": {
                    "added": [
                        {
                            "alias": str(edge["id"]),
                            "after": {
                                "from": str(edge["from"]),
                                "to": str(edge["to"]),
                                "description": edge.get("directness_reason", ""),
                                "locations": _gold_locations(
                                    cast(list[object], edge["evidence"])
                                ),
                                "properties": {
                                    key: deepcopy(value)
                                    for key, value in edge.items()
                                    if key not in {"id", "from", "to", "directness_reason", "evidence"}
                                },
                            },
                        }
                        for edge in owned_edges
                    ],
                    "changed": [],
                    "deleted": [],
                },
                "endpoint_context": endpoint_context,
            }
        )
    return cases


def setup_frozen_gold_experiment(
    iterator_state_dir: Path,
    worker_index: int,
    inventory_fragment_paths: Sequence[Path],
    gold_annotation_path: Path,
    gold_overlay_path: Path,
    experiment_dir: Path,
):
    """Create an experiment from authenticated legacy iterator history and full gold."""

    source_cases, coverage = _legacy_source_cases(
        iterator_state_dir, worker_index, inventory_fragment_paths
    )
    base_bytes = Path(gold_annotation_path).read_bytes()
    base = _read_json_object(gold_annotation_path, "gold annotation")
    overlay = _read_json_object(gold_overlay_path, "gold correction overlay")
    corrected = _apply_gold_overlay(base, overlay, base_bytes)
    gold_cases = _gold_cases_from_annotation(
        [cast(int, case["sequence_id"]) for case in source_cases], coverage, corrected
    )
    return setup_experiment(source_cases, gold_cases, experiment_dir)


def setup_bundled_frozen_gold_experiment(
    iterator_state_dir: Path,
    worker_index: int,
    inventory_fragment_paths: Sequence[Path],
    experiment_dir: Path,
):
    """Create an experiment using the skill's frozen appendix gold bundle."""

    return setup_frozen_gold_experiment(
        iterator_state_dir,
        worker_index,
        inventory_fragment_paths,
        _BUNDLED_GOLD_ANNOTATION,
        _BUNDLED_GOLD_OVERLAY,
        experiment_dir,
    )


def setup_experiment(
    source_cases: object,
    gold_cases: object,
    experiment_dir: Path,
):
    experiment_dir = Path(experiment_dir).resolve()
    if experiment_dir.exists():
        raise FileExistsError("experiment directory already exists")
    worker_contract = _frozen_worker_contract()
    cases = _seal_cases(_seal_source_cases(source_cases), gold_cases)
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "ledger").mkdir()
    return _registry(experiment_dir).create(
        _RUTTER_NAME,
        Path(_RECKONING_NAME),
        {
            **worker_contract,
            "experiment_dir": str(experiment_dir),
            "gold_commitment_sha256": hashlib.sha256(
                _canonical_bytes([case["gold"] for case in cases])
            ).hexdigest(),
            "cases": cases,
        },
    )


def setup_iterator_experiment(
    iterator_state_dir: Path,
    worker_index: int,
    gold_cases: object,
    experiment_dir: Path,
):
    """Create a new-Rutter experiment from authenticated iterator history."""

    return setup_experiment(
        _load_iterator_source_cases(iterator_state_dir, worker_index),
        gold_cases,
        experiment_dir,
    )


def open_experiment(experiment_dir: Path):
    root = Path(experiment_dir).resolve()
    return _registry(root).open(Path(_RECKONING_NAME))


def validated_inventory_ledger(experiment_dir: Path) -> list[dict[str, object]]:
    root = Path(experiment_dir).resolve()
    ledger = root / "ledger"
    if not ledger.is_dir() or ledger.is_symlink():
        raise ValueError("inventory ledger directory is unavailable")
    rows: list[dict[str, object]] = []
    machine_ids: set[str] = set()
    for path in sorted(ledger.glob("*.json")):
        if path.is_symlink():
            raise ValueError("inventory ledger rows must not be symlinks")
        row = json.loads(read_regular_file_bytes(path, allowed_root=root))
        materialized = _json_object(row, "inventory ledger row")
        machine_id = materialized.get("machine_id")
        if (
            type(machine_id) is not str
            or path.stem != machine_id
            or machine_id in machine_ids
        ):
            raise ValueError("inventory ledger row identity is invalid")
        machine_ids.add(machine_id)
        rows.append(materialized)
    return rows
