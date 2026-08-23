#!/usr/bin/env python3
"""Compose frozen inventory reports with semantic diagnosis and a ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, cast

from officina.common.atomic_files import atomic_create_bytes, read_regular_file_bytes
from officina.rutter import (
    Action,
    ActionContext,
    ActionResult,
    AnswerContext,
    AnswerSpec,
    DiagnosisCase,
    DiagnoseAnswer,
    Done,
    EdgeContext,
    JsonObject,
    Prompt,
    QuestionCase,
    RunResult,
    Rutter,
    RutterDefinitionError,
    RutterRegistry,
    StateContext,
    Turn,
    ValidationIssue,
    ValidationReport,
    case_sequence_after,
)


_RUTTER_NAME = "inquisitive-inventory"
_RUTTER_ID = "math-graph-inquisitive-inventory"
_RECKONING_NAME = "inquisitive-inventory.reckoning.json"
_MAKER_ID = "inventory-diagnosis"
_REPORT_STATE = "report"
_RECORD_STATE = "record"
_MAX_APPENDIX_CASES = 64
_CASE_SLOTS = tuple({"index": index} for index in range(_MAX_APPENDIX_CASES))
_CHANGES = ("added", "changed", "deleted")
_MINIMAL_FIX = "minimal_fix must be paper-independent and target inventory.md"
_DIAGNOSIS_GUIDANCE = (
    "Distinguish an omitted entity from a reported unresolved endpoint.",
    "Treat a reported edge with an unresolved endpoint as recovered, not omitted.",
    "Distinguish a partially recovered dependency chain from a wholly omitted chain.",
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


def _inventory_profile(value: Mapping[str, object]) -> dict[str, object]:
    node_aliases: dict[str, dict[str, object]] = {}
    node_sections = value.get("delta_nodes")
    endpoint_context = value.get("endpoint_context")
    if not isinstance(node_sections, Mapping) or not isinstance(
        endpoint_context, Mapping
    ):
        return {"invalid": _without_entry_aliases(value)}

    for entries in node_sections.values():
        if not isinstance(entries, (list, tuple)):
            return {"invalid": _without_entry_aliases(value)}
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("alias"), str
            ):
                return {"invalid": _without_entry_aliases(value)}
            alias = entry["alias"]
            assert isinstance(alias, str)
            node_aliases.setdefault(alias, {}).update(
                {
                    side: _without_entry_aliases(entry[side])
                    for side in ("before", "after")
                    if side in entry
                }
            )
    for alias, sides in endpoint_context.items():
        if not isinstance(alias, str) or not isinstance(sides, Mapping):
            return {"invalid": _without_entry_aliases(value)}
        node_aliases.setdefault(alias, {}).update(
            {
                side: _without_entry_aliases(record)
                for side, record in sides.items()
                if side in {"before", "after"}
            }
        )

    def endpoint(alias: object, side: str) -> object:
        if not isinstance(alias, str) or side not in node_aliases.get(alias, {}):
            return {"missing_endpoint": alias, "side": side}
        return node_aliases[alias][side]

    normalized_nodes: dict[str, list[object]] = {}
    for change, entries in node_sections.items():
        assert isinstance(entries, (list, tuple))
        normalized = [_without_entry_aliases(entry) for entry in entries]
        normalized_nodes[str(change)] = sorted(normalized, key=_canonical_text)

    edge_sections = value.get("delta_edges")
    if not isinstance(edge_sections, Mapping):
        return {"invalid": _without_entry_aliases(value)}
    normalized_edges: dict[str, list[object]] = {}
    for change, entries in edge_sections.items():
        if not isinstance(entries, (list, tuple)):
            return {"invalid": _without_entry_aliases(value)}
        normalized_entries: list[object] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                return {"invalid": _without_entry_aliases(value)}
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
                projected["from"] = endpoint(record.get("from"), side)
                projected["to"] = endpoint(record.get("to"), side)
                normalized_entry[side] = projected
            normalized_entries.append(normalized_entry)
        normalized_edges[str(change)] = sorted(
            normalized_entries, key=_canonical_text
        )

    normalized_context = sorted(
        (
            {
                side: _without_entry_aliases(record)
                for side, record in sides.items()
            }
            for sides in endpoint_context.values()
            if isinstance(sides, Mapping)
        ),
        key=_canonical_text,
    )
    return {
        "delta_nodes": normalized_nodes,
        "delta_edges": normalized_edges,
        "endpoint_context": normalized_context,
    }


def semantic_inventory_equal(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    return _inventory_profile(actual) == _inventory_profile(expected)


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


def _charter_cases(context: StateContext) -> list[dict[str, object]]:
    cases = context.charter.data.get("cases")
    if not isinstance(cases, (list, tuple)):
        raise RutterDefinitionError("inventory Charter cases must be an array")
    return [_json_object(case, "inventory case") for case in cases]


def _case_index(context: StateContext) -> int:
    return len(context.history.attached_calls(case_maker_id=_MAKER_ID))


def _report_data(context: StateContext) -> JsonObject:
    cases = _charter_cases(context)
    index = _case_index(context)
    if index >= len(cases):
        raise RutterDefinitionError("no inventory case remains")
    case = cases[index]
    source = _json_object(case.get("source"), "inventory source case")
    return {
        "sequence": {
            "sequence_id": source["sequence_id"],
            "source_sha256": hashlib.sha256(_canonical_bytes(source)).hexdigest(),
            "before": source["before"],
            "after": source["after"],
        },
        "minimal_fix_constraint": _MINIMAL_FIX,
    }


def _validate_report(context: AnswerContext) -> ValidationReport:
    response = context.response
    if response.outcome != "reported":
        return _invalid(("outcome",), "invalid-outcome", "outcome must be reported")
    evidence = response.evidence
    if set(evidence) != {"sequence_id", "node_ids", "edge_ids"}:
        return _invalid(
            ("evidence",),
            "invalid-inventory-report",
            "evidence must contain sequence_id, node_ids, and edge_ids",
        )
    cases = _charter_cases(context.state)
    index = _case_index(context.state)
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
    for field in ("node_ids", "edge_ids"):
        if not _valid_change_ids(evidence[field]):
            return _invalid(
                ("evidence", field),
                "invalid-change-ids",
                f"{field} must contain sorted disjoint added, changed, and deleted IDs",
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
    after = _json_object(source.get("after"), "after snapshot")
    before_nodes = _record_map(before, "nodes")
    after_nodes = _record_map(after, "nodes")
    before_edges = _record_map(before, "edges")
    after_edges = _record_map(after, "edges")
    before_files = _files(before)
    after_files = _files(after)
    node_changes = _json_object(report.get("node_ids"), "reported node IDs")
    edge_changes = _json_object(report.get("edge_ids"), "reported edge IDs")
    if not _valid_change_ids(node_changes) or not _valid_change_ids(edge_changes):
        raise ValueError("reported inventory IDs are invalid")

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


def _diagnosis_charter(item: JsonObject, context: EdgeContext) -> JsonObject:
    index = item.get("index")
    cases = _charter_cases(context.state)
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
    ).to_json()


def _write_ledger_row(root: Path, action_id: str, row: dict[str, object]) -> None:
    ledger = root / "ledger"
    path = ledger / f"{action_id}.json"
    serialized = _canonical_bytes(row) + b"\n"
    created = atomic_create_bytes(path, serialized, allowed_root=root, mode=0o600)
    if not created and read_regular_file_bytes(path, allowed_root=root) != serialized:
        raise ValueError("existing inventory ledger row differs from the replay")


def _record_iteration(context: ActionContext) -> ActionResult:
    turn = context.state.history.require_latest_turn(_REPORT_STATE)
    if turn.response is None:
        raise RutterDefinitionError("inventory ledger requires an accepted report")
    calls = context.state.history.attached_calls(
        case_maker_id=_MAKER_ID,
        edge_id=turn.record_id,
    )
    if len(calls) != 1:
        raise RutterDefinitionError(
            "inventory ledger requires exactly one attached diagnosis"
        )
    call = calls[0]
    root_value = context.state.charter.data.get("experiment_dir")
    if type(root_value) is not str:
        raise RutterDefinitionError("inventory experiment directory is unavailable")
    row = {
        "ledger_version": 1,
        "action_id": context.action_id,
        "maker_id": _MAKER_ID,
        "edge_id": turn.record_id,
        "sequence_id": turn.response.evidence["sequence_id"],
        "message": _plain_json(turn.message.to_json()),
        "response": _plain_json(turn.response.to_json()),
        "verdict": call.result.outcome,
        "child_result": _plain_json(call.result.to_json()),
    }
    _write_ledger_row(Path(root_value), context.action_id, row)
    total = len(_charter_cases(context.state))
    completed = len(context.state.history.attached_calls(case_maker_id=_MAKER_ID))
    return ActionResult("done" if completed == total else "more", row)


def _complete_result(context: StateContext) -> RunResult:
    completed = len(context.history.attached_calls(case_maker_id=_MAKER_ID))
    return RunResult("complete", {"iterations": completed})


class InquisitiveInventoryRutter(Rutter):
    rutter_id = _RUTTER_ID
    definition_version = 1
    start_state = _REPORT_STATE

    def define_states(self) -> Mapping[str, object]:
        return {
            _REPORT_STATE: Prompt(
                (
                    "Report the displayed sequence_id and the sorted added, changed, "
                    "and deleted local node and edge IDs."
                ),
                answer=AnswerSpec(
                    {
                        "reported": {
                            "sequence_id": "positive integer",
                            "node_ids": {change: ["local ID"] for change in _CHANGES},
                            "edge_ids": {change: ["local ID"] for change in _CHANGES},
                        }
                    }
                ),
                data=_report_data,
                validate=_validate_report,
                then=_RECORD_STATE,
            ),
            _RECORD_STATE: Action(
                _record_iteration,
                mode="repeat-safe",
                then={"more": _REPORT_STATE, "done": "complete"},
            ),
            "complete": Done(_complete_result),
        }

    def define_case_makers(self) -> tuple[object, ...]:
        return (
            case_sequence_after(
                id=_MAKER_ID,
                after_states={_REPORT_STATE},
                items=_CASE_SLOTS,
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
        if set(source) != {"sequence_id", "before", "after"}:
            raise ValueError("source cases must contain sequence_id, before, and after")
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


def setup_experiment(
    source_cases: object,
    gold_cases: object,
    experiment_dir: Path,
):
    experiment_dir = Path(experiment_dir).resolve()
    if experiment_dir.exists():
        raise FileExistsError("experiment directory already exists")
    cases = _seal_cases(_seal_source_cases(source_cases), gold_cases)
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "ledger").mkdir()
    return _registry(experiment_dir).create(
        _RUTTER_NAME,
        Path(_RECKONING_NAME),
        {
            "experiment_dir": str(experiment_dir),
            "gold_commitment_sha256": hashlib.sha256(
                _canonical_bytes([case["gold"] for case in cases])
            ).hexdigest(),
            "cases": cases,
        },
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
    action_ids: set[str] = set()
    for path in sorted(ledger.glob("*.json")):
        if path.is_symlink():
            raise ValueError("inventory ledger rows must not be symlinks")
        row = json.loads(read_regular_file_bytes(path, allowed_root=root))
        materialized = _json_object(row, "inventory ledger row")
        action_id = materialized.get("action_id")
        if type(action_id) is not str or path.stem != action_id or action_id in action_ids:
            raise ValueError("inventory ledger row identity is invalid")
        action_ids.add(action_id)
        rows.append(materialized)
    return rows
