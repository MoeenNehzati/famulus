#!/usr/bin/env python3
"""Support inventory Voyage packet iteration and durable chunk output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping, Sequence, TextIO

from jsonschema import ValidationError, validate as validate_json
from officina.common.atomic_files import (
    atomic_compare_and_append_bytes,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from officina.rutter import (
    DiagnosisCase,
    DiagnoseAnswer,
    EvolutionContext,
    JsonObject,
    JsonValue,
    LLMResponseContext,
    MachineContext,
    MachineResult,
    QuestionCase,
    Rutter,
    RutterDefinitionError,
    RutterRegistry,
    TransitionContext,
    Turn,
    ValidationIssue,
    ValidationReport,
    VoyageResult,
)

from ._chunk_pooler import validate_inventory_fragment
from ._chunk_extractor import extract_inventory_chunks, load_chunk_manifest


_RECKONING_NAME = "inventory-voyage.reckoning.json"
_REPORT_EVOLUTION = "report"
_DIAGNOSIS_GUIDANCE = (
    "Compare only gold records whose every source location is completely covered "
    "by the packets processed so far.",
    "Treat local IDs as opaque; compare mathematical entities and dependencies.",
    "Make any proposed fix paper-independent. Target inventory.md only for a "
    "worker-instruction error; evaluator, source-identity, or gold errors must "
    "name their actual owning component and leave inventory.md unchanged.",
    "If expected content was worker-invisible, including mathematical content "
    "hidden behind an opaque macro, do not presume worker_error. Use gold_error "
    "when the expected record violates the visible-source policy, "
    "allowed_difference when both records comply with policy, or unresolved when "
    "the visible evidence cannot decide. Use both_wrong only when the actual and "
    "expected records have distinct policy errors. For gold_error or both_wrong, "
    "identify the challenged target and source coordinates, the inventory policy, "
    "the actual source-visible support, and the gold or evaluator owner in the "
    "structured gold_challenge; substantive mathematical correctness remains for "
    "later adjudication.",
)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _json_object(value: object, label: str) -> dict[str, object]:
    materialized = _plain_json(value)
    if not isinstance(materialized, dict):
        raise ValueError(f"{label} must be a JSON object")
    return materialized


def canonical_text(value: object) -> str:
    return json.dumps(
        _plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_bytes(value: object) -> bytes:
    return canonical_text(value).encode("utf-8")


def write_json(stream: TextIO, value: object) -> None:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")


def _invalid(path: tuple[str | int, ...], code: str, message: str) -> ValidationReport:
    return ValidationReport(False, (ValidationIssue(path, code, message),))


def _frozen_text(context: EvolutionContext, name: str) -> str:
    text = context.charter.data.get(f"{name}_text")
    digest = context.charter.data.get(f"{name}_sha256")
    if type(text) is not str or type(digest) is not str:
        raise RutterDefinitionError(f"inventory Charter {name} snapshot is invalid")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RutterDefinitionError(
            f"inventory Charter {name} snapshot hash is invalid"
        )
    return text


def _chunk(context: EvolutionContext) -> dict[str, object]:
    return _json_object(context.charter.data.get("chunk"), "inventory chunk")


def _packets(context: EvolutionContext) -> list[dict[str, object]]:
    packets = _chunk(context).get("packets")
    if not isinstance(packets, (list, tuple)) or not packets:
        raise RutterDefinitionError("inventory chunk packets are unavailable")
    return [_json_object(packet, "inventory packet") for packet in packets]


def inventory_report_schema(
    canonical_schema: Mapping[str, object]
) -> dict[str, object]:
    """Adapt canonical record arrays to packet-local diagnostic responses."""

    schema = _json_object(canonical_schema, "inventory schema")
    definitions = _json_object(schema.get("$defs"), "inventory schema definitions")
    definitions["location"] = {
        "type": "array",
        "prefixItems": [
            {"type": "integer", "minimum": 1},
            {"type": "integer", "minimum": 1},
        ],
        "items": False,
        "minItems": 2,
        "maxItems": 2,
    }
    properties = _json_object(schema.get("properties"), "inventory schema properties")
    record_properties = {key: properties[key] for key in ("nodes", "edges", "gaps")}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "nodes", "edges", "gaps"],
        "properties": {
            "outcome": {"const": "reported"},
            **record_properties,
        },
        "$defs": definitions,
    }


def _charter_path(
    context: EvolutionContext, field: str, label: str
) -> tuple[Path, Path]:
    path_value = context.charter.data.get(field)
    run_value = context.charter.data.get("run_dir")
    if type(path_value) is not str or type(run_value) is not str:
        raise RutterDefinitionError(f"{label} boundary is unavailable")
    return Path(path_value).resolve(), Path(run_value).resolve()


def introduction_data(context: EvolutionContext) -> JsonObject:
    """Present immutable run-wide instructions before any packet report."""

    source_path, _ = _charter_path(
        context, "source_packets_path", "source-packet output"
    )
    inventory_path, _ = _charter_path(context, "inventory_path", "inventory output")
    return {
        "inventory_instruction": _frozen_text(context, "inventory_instruction"),
        "cumulative_packets_file": str(source_path),
        "inventory_file": str(inventory_path),
    }


def prepare_packet(context: MachineContext) -> MachineResult:
    """Append the next immutable packet to the cumulative source text."""

    packets = _packets(context.evolution)
    index = len(context.evolution.history.turns(_REPORT_EVOLUTION))
    if index >= len(packets):
        raise RutterDefinitionError("no inventory packet remains")
    packet = packets[index]
    packet_text = packet["text"]
    if type(packet_text) is not str:
        raise RutterDefinitionError("inventory packet text is invalid")
    previous = "".join(str(item["text"]) for item in packets[:index]).encode("utf-8")
    appended = previous + packet_text.encode("utf-8")
    source_path, run_dir = _charter_path(
        context.evolution, "source_packets_path", "source-packet output"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.exists():
        current = read_regular_file_bytes(source_path, allowed_root=run_dir)
        if current == appended:
            return MachineResult("prepared", {})
        if current != previous:
            raise RutterDefinitionError(
                "cumulative source packets do not match the immutable packet prefix"
            )
    atomic_compare_and_append_bytes(
        source_path,
        packet_text.encode("utf-8"),
        expected_previous_bytes=None if index == 0 else previous,
        allowed_root=run_dir,
        mode=0o600,
    )
    return MachineResult("prepared", {})


def report_data(context: EvolutionContext) -> JsonValue:
    """Present the packet prepared by the preceding machine evolution."""

    packets = _packets(context)
    index = len(context.history.turns(_REPORT_EVOLUTION))
    if index >= len(packets):
        raise RutterDefinitionError("no inventory packet remains")
    packet_text = packets[index].get("text")
    if type(packet_text) is not str:
        raise RutterDefinitionError("prepared inventory packet text is invalid")
    return packet_text


def _read_inventory_file(context: EvolutionContext) -> dict[str, object]:
    output_path, run_dir = _charter_path(context, "inventory_path", "inventory output")
    if not output_path.exists():
        raise RutterDefinitionError("worker inventory file is unavailable")
    try:
        return _json_object(
            json.loads(
                read_regular_file_bytes(output_path, allowed_root=run_dir).decode(
                    "utf-8"
                )
            ),
            "worker inventory file",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RutterDefinitionError("worker inventory file is invalid JSON") from error


def _seen_coordinates(
    context: EvolutionContext, packet_count: int
) -> dict[int, tuple[str, int]]:
    packets = _packets(context)
    if packet_count < 1 or packet_count > len(packets):
        raise ValueError("inventory packet count is out of range")
    mapping: dict[int, tuple[str, int]] = {}
    for packet in packets[:packet_count]:
        coordinates = packet.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("inventory packet coordinates are unavailable")
        for value in coordinates:
            coordinate = _json_object(value, "inventory packet coordinate")
            chunk_row = coordinate.get("chunk_row")
            source_file = coordinate.get("source_file")
            line = coordinate.get("line")
            if (
                type(chunk_row) is not int
                or type(source_file) is not str
                or type(line) is not int
            ):
                raise ValueError("inventory packet coordinate is invalid")
            if chunk_row in mapping:
                raise ValueError("inventory packet coordinates repeat a chunk row")
            mapping[chunk_row] = (source_file, line)
    return mapping


def _map_location(
    value: object,
    *,
    coordinates: Mapping[int, tuple[str, int]],
    files: list[object],
) -> list[int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or type(value[0]) is not int
        or type(value[1]) is not int
    ):
        raise ValueError("inventory location must be [start_row, end_row]")
    start, end = value
    if start < 1 or end < start:
        raise ValueError("inventory location rows are invalid")
    rows = [coordinates.get(row) for row in range(start, end + 1)]
    if any(row is None for row in rows):
        raise ValueError("inventory location is outside the displayed packets")
    materialized = [row for row in rows if row is not None]
    source_files = {row[0] for row in materialized}
    if len(source_files) != 1:
        raise ValueError("inventory location crosses source files")
    source_file = materialized[0][0]
    source_lines = [row[1] for row in materialized]
    if source_lines != list(range(source_lines[0], source_lines[-1] + 1)):
        raise ValueError("inventory location is not source-contiguous")
    try:
        file_index = files.index(source_file)
    except ValueError as error:
        raise ValueError("inventory location names an unknown source file") from error
    return [file_index, source_lines[0], source_lines[-1]]


def _map_locations(
    value: object,
    *,
    coordinates: Mapping[int, tuple[str, int]],
    files: list[object],
    key: str | None = None,
) -> object:
    if key in {"statement_location", "location", "starts_at", "ends_at"}:
        return _map_location(value, coordinates=coordinates, files=files)
    if isinstance(value, Mapping):
        return {
            str(nested_key): _map_locations(
                nested_value,
                coordinates=coordinates,
                files=files,
                key=str(nested_key),
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _map_locations(item, coordinates=coordinates, files=files) for item in value
        ]
    return value


def _canonicalize_inventory_file(
    context: EvolutionContext,
    worker_inventory: Mapping[str, object],
    *,
    packet_count: int,
    validate_fragment: bool = True,
) -> dict[str, object]:
    canonical_schema = _json_object(
        json.loads(_frozen_text(context, "inventory_schema")),
        "inventory schema",
    )
    worker_schema = inventory_report_schema(canonical_schema)
    validate_json(
        {"outcome": "reported", **_json_object(worker_inventory, "worker inventory")},
        worker_schema,
    )
    chunk = _chunk(context)
    files = chunk["files"]
    if not isinstance(files, list):
        raise ValueError("inventory chunk files are invalid")
    coordinates = _seen_coordinates(context, packet_count)
    mapped = _map_locations(
        worker_inventory,
        coordinates=coordinates,
        files=files,
    )
    if not isinstance(mapped, dict):
        raise ValueError("reported inventory must be an object")

    canonical_inventory = {
        "ir_version": 3,
        "chunk_id": chunk["chunk_id"],
        "files": files,
        **mapped,
    }
    if validate_fragment:
        validate_inventory_fragment(canonical_inventory)
    return canonical_inventory


def _prior_counts(context: EvolutionContext) -> dict[str, int]:
    counts = {"nodes": 0, "edges": 0, "gaps": 0}
    for turn in context.history.turns(_REPORT_EVOLUTION):
        if turn.response is None:
            raise RutterDefinitionError("prior inventory report is unavailable")
        for field in counts:
            records = turn.response.get(field)
            if not isinstance(records, (list, tuple)):
                raise RutterDefinitionError("prior inventory report is invalid")
            counts[field] += len(records)
    return counts


def assess_report(context: LLMResponseContext) -> ValidationReport:
    """Validate the worker-owned file and its newly appended diagnostic records."""

    packet_count = len(context.evolution.history.turns(_REPORT_EVOLUTION)) + 1
    try:
        worker_inventory = _read_inventory_file(context.evolution)
        _canonicalize_inventory_file(
            context.evolution, worker_inventory, packet_count=packet_count
        )
        prior_counts = _prior_counts(context.evolution)
        for field in ("nodes", "edges", "gaps"):
            records = worker_inventory.get(field)
            reported = context.response.get(field)
            if not isinstance(records, list) or not isinstance(reported, (list, tuple)):
                raise ValueError(f"inventory {field} are invalid")
            prior_count = prior_counts[field]
            if len(records) < prior_count:
                raise ValueError(f"inventory file dropped prior {field}")
            if _plain_json(reported) != records[prior_count:]:
                raise ValueError(
                    f"reported {field} must equal the new inventory-file suffix"
                )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return _invalid(("inventory",), "invalid-inventory", str(error))
    return ValidationReport(True)


def assess_debug_report(context: LLMResponseContext) -> ValidationReport:
    basis = context.response.get("decision_basis")
    if type(basis) is not str or not basis.strip():
        return _invalid(
            ("decision_basis",),
            "empty-decision-basis",
            "decision_basis must be a nonempty pre-reference account",
        )
    return assess_report(context)


def record_iteration(context: MachineContext) -> MachineResult:
    """Checkpoint worker-owned inventory counts and finalize canonical locations."""

    if (
        context.evolution.history.require_latest_turn(_REPORT_EVOLUTION).response
        is None
    ):
        raise RutterDefinitionError("inventory record step requires a response")
    completed = len(context.evolution.history.turns(_REPORT_EVOLUTION))
    worker_inventory = _read_inventory_file(context.evolution)
    inventory = _canonicalize_inventory_file(
        context.evolution, worker_inventory, packet_count=completed
    )
    total = len(_packets(context.evolution))
    if completed >= total:
        output_path, run_dir = _charter_path(
            context.evolution, "inventory_path", "inventory output"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace_bytes(
            output_path,
            _canonical_bytes(inventory) + b"\n",
            allowed_root=run_dir,
            mode=0o600,
        )
    return MachineResult("done" if completed >= total else "more", {})


def complete_result(context: EvolutionContext) -> VoyageResult:
    inventory = _read_inventory_file(context)
    validate_inventory_fragment(inventory)
    return VoyageResult(
        "complete",
        {
            "chunk_id": inventory["chunk_id"],
            "inventory_path": context.charter.data["inventory_path"],
            "source_packets_path": context.charter.data["source_packets_path"],
            "packets": len(context.history.turns(_REPORT_EVOLUTION)),
            "nodes": len(inventory["nodes"]),
            "edges": len(inventory["edges"]),
            "gaps": len(inventory["gaps"]),
        },
    )


def _freeze_utf8_file(path: Path, label: str) -> tuple[str, str]:
    try:
        content_bytes = path.read_bytes()
        content = content_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is not readable UTF-8") from error
    if not content:
        raise ValueError(f"{label} must not be empty")
    return content, hashlib.sha256(content_bytes).hexdigest()


def _freeze_json_file(path: Path, label: str) -> tuple[str, object, str]:
    text, digest = _freeze_utf8_file(path, label)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is invalid JSON") from error
    return text, value, digest


def _validate_packet(
    value: object, *, chunk_id: str, packet_index: int, first_row: int
) -> int:
    packet = _json_object(value, "inventory packet")
    if packet.get("packet_index") != packet_index:
        raise ValueError("inventory packet indexes must be contiguous")
    if packet.get("packet_id") != f"{chunk_id}-packet-{packet_index:03d}":
        raise ValueError("inventory packet IDs must be ordered and chunk-qualified")

    text = packet.get("text")
    coordinates = packet.get("coordinates")
    if type(text) is not str or not text:
        raise ValueError("inventory packet text must be nonempty")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("inventory packet coordinates must be nonempty")
    rendered_lines = text.splitlines()
    if len(rendered_lines) != len(coordinates):
        raise ValueError("inventory packet text and coordinates disagree")

    packet_files: set[str] = set()
    previous_source_line: int | None = None
    next_row = first_row
    for rendered, coordinate_value in zip(rendered_lines, coordinates):
        coordinate = _json_object(coordinate_value, "inventory packet coordinate")
        if set(coordinate) != {"chunk_row", "source_file", "line"}:
            raise ValueError("inventory packet coordinate fields are invalid")
        source_file = coordinate["source_file"]
        source_line = coordinate["line"]
        if (
            coordinate["chunk_row"] != next_row
            or type(source_file) is not str
            or type(source_line) is not int
        ):
            raise ValueError("inventory packet coordinates are not contiguous")
        if not rendered.startswith(f"{next_row:06d} | "):
            raise ValueError("inventory packet row label is invalid")
        if previous_source_line is not None and source_line != previous_source_line + 1:
            raise ValueError("inventory packet source lines are not contiguous")
        packet_files.add(source_file)
        previous_source_line = source_line
        next_row += 1
    if len(packet_files) != 1:
        raise ValueError("inventory packet crosses source files")
    return next_row


def _seal_chunk(value: object) -> dict[str, object]:
    chunk = _json_object(value, "inventory chunk")
    if chunk.get("chunk_version") != 1:
        raise ValueError("inventory chunk version must be 1")
    chunk_id = chunk.get("chunk_id")
    files = chunk.get("files")
    packets = chunk.get("packets")
    if type(chunk_id) is not str or not chunk_id:
        raise ValueError("inventory chunk_id must be nonempty")
    if (
        not isinstance(files, list)
        or not files
        or any(type(item) is not str or not item for item in files)
    ):
        raise ValueError("inventory chunk files must be a nonempty string array")
    if not isinstance(packets, list) or not packets:
        raise ValueError("inventory chunk packets must be nonempty")

    next_row = 1
    for packet_index, packet in enumerate(packets, start=1):
        next_row = _validate_packet(
            packet,
            chunk_id=chunk_id,
            packet_index=packet_index,
            first_row=next_row,
        )
    return chunk


def _create_voyage(
    definition: Rutter,
    chunk: object,
    voyage_dir: Path,
    inventory_path: Path,
    source_packets_path: Path,
    *,
    rutter_name: str,
    run_dir: Path,
    charter_data: Mapping[str, object],
) -> None:
    """Create one durable Voyage for one immutable worker chunk."""

    voyage_dir = voyage_dir.resolve()
    run_dir = run_dir.resolve()
    if voyage_dir.exists():
        raise FileExistsError(f"inventory Voyage already exists: {voyage_dir}")
    sealed_chunk = _seal_chunk(chunk)
    voyage_dir.mkdir(parents=True)
    RutterRegistry({rutter_name: definition}, voyage_dir).create(
        rutter_name,
        Path(_RECKONING_NAME),
        {
            "run_dir": str(run_dir),
            "inventory_path": str(inventory_path.resolve()),
            "source_packets_path": str(source_packets_path.resolve()),
            "chunk": sealed_chunk,
            **charter_data,
        },
    )


def open_voyage(definitions: Mapping[str, Rutter], voyage_dir: Path):
    return RutterRegistry(definitions, voyage_dir.resolve()).open(Path(_RECKONING_NAME))


def _frozen_gold(context: TransitionContext) -> dict[str, object]:
    text = _frozen_text(context.evolution, "inventory_gold_standard")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RutterDefinitionError(
            "debug inventory gold snapshot is invalid"
        ) from error
    if not isinstance(value, dict):
        raise RutterDefinitionError("debug inventory gold must be a JSON object")
    return value


def _frozen_gold_source_map(context: TransitionContext) -> dict[str, str]:
    text = _frozen_text(context.evolution, "inventory_gold_source_map")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise RutterDefinitionError(
            "debug inventory gold source map is invalid"
        ) from error
    if not isinstance(value, dict) or any(
        type(key) is not str or type(item) is not str for key, item in value.items()
    ):
        raise RutterDefinitionError(
            "debug inventory gold source map must be a string mapping"
        )
    return value


def _same_source(left: str, right: str) -> bool:
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


_LOCATION_KEYS = {"statement_location", "location", "starts_at", "ends_at"}


def _gold_location_parts(
    location: object,
    gold_files: list[str],
    source_map: Mapping[str, str],
) -> tuple[str, int, int]:
    if (
        not isinstance(location, list)
        or len(location) != 3
        or any(type(value) is not int for value in location)
    ):
        raise RutterDefinitionError("debug inventory gold record location is invalid")
    file_index, start, end = location
    if file_index < 0 or file_index >= len(gold_files) or start < 1 or end < start:
        raise RutterDefinitionError(
            "debug inventory gold record location is outside its files"
        )
    source = gold_files[file_index]
    mapped_source = source_map.get(source)
    if mapped_source is None:
        raise RutterDefinitionError(
            f"debug inventory gold source is unmapped: {source}"
        )
    return mapped_source, start, end


def _embedded_locations(value: object, key: str | None = None) -> list[object]:
    if key in _LOCATION_KEYS:
        return [value]
    if isinstance(value, Mapping):
        locations: list[object] = []
        for nested_key, nested_value in value.items():
            locations.extend(_embedded_locations(nested_value, str(nested_key)))
        return locations
    if isinstance(value, list):
        locations = []
        for nested_value in value:
            locations.extend(_embedded_locations(nested_value))
        return locations
    return []


def _gold_location_is_covered(
    location: object,
    coverage: set[tuple[str, int]],
    gold_files: list[str],
    source_map: Mapping[str, str],
) -> bool:
    source, start, end = _gold_location_parts(location, gold_files, source_map)
    return all((source, line) in coverage for line in range(start, end + 1))


def _remap_gold_locations(
    value: object,
    *,
    gold_files: list[str],
    source_map: Mapping[str, str],
    chunk_file_indexes: Mapping[str, int],
    key: str | None = None,
) -> object:
    if key in _LOCATION_KEYS:
        source, start, end = _gold_location_parts(value, gold_files, source_map)
        if source not in chunk_file_indexes:
            raise RutterDefinitionError(
                f"debug inventory mapped gold source is outside its chunk: {source}"
            )
        return [chunk_file_indexes[source], start, end]
    if isinstance(value, Mapping):
        return {
            str(nested_key): _remap_gold_locations(
                nested_value,
                gold_files=gold_files,
                source_map=source_map,
                chunk_file_indexes=chunk_file_indexes,
                key=str(nested_key),
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            _remap_gold_locations(
                nested_value,
                gold_files=gold_files,
                source_map=source_map,
                chunk_file_indexes=chunk_file_indexes,
            )
            for nested_value in value
        ]
    return value


def _project_gold_records(
    gold: Mapping[str, object],
    section: str,
    coverage: set[tuple[str, int]],
    gold_files: list[str],
    source_map: Mapping[str, str],
    chunk_file_indexes: Mapping[str, int],
) -> list[dict[str, object]]:
    records = gold.get(section)
    if not isinstance(records, list):
        raise RutterDefinitionError(f"debug inventory gold {section} must be an array")
    projected: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise RutterDefinitionError("debug inventory gold record is invalid")
        locations = _embedded_locations(record)
        if not locations:
            continue
        if not all(
            _gold_location_is_covered(location, coverage, gold_files, source_map)
            for location in locations
        ):
            continue
        remapped = _remap_gold_locations(
            record,
            gold_files=gold_files,
            source_map=source_map,
            chunk_file_indexes=chunk_file_indexes,
        )
        if not isinstance(remapped, dict):
            raise RutterDefinitionError("debug inventory gold record is invalid")
        projected.append(remapped)
    return projected


def _endpoint_available(endpoint: object, node_ids: set[object]) -> bool:
    if not isinstance(endpoint, Mapping):
        raise RutterDefinitionError("debug inventory gold endpoint is invalid")
    local_node = endpoint.get("local_node")
    return local_node is None or (type(local_node) is str and local_node in node_ids)


def _project_gold(
    gold: Mapping[str, object],
    coverage: set[tuple[str, int]],
    source_map: Mapping[str, str],
    *,
    chunk_id: str,
    files: Sequence[str],
) -> dict[str, object]:
    gold_files = gold.get("files")
    if not isinstance(gold_files, list) or any(
        type(path) is not str for path in gold_files
    ):
        raise RutterDefinitionError("debug inventory gold files are invalid")
    if type(chunk_id) is not str or not chunk_id:
        raise RutterDefinitionError("debug inventory chunk ID is invalid")
    if (
        isinstance(files, (str, bytes))
        or not isinstance(files, Sequence)
        or any(type(path) is not str for path in files)
    ):
        raise RutterDefinitionError("debug inventory chunk files are invalid")
    chunk_files = list(files)
    if len(set(chunk_files)) != len(chunk_files):
        raise RutterDefinitionError("debug inventory chunk files are not unique")
    chunk_file_indexes = {
        source: file_index for file_index, source in enumerate(chunk_files)
    }
    projected_nodes = _project_gold_records(
        gold,
        "nodes",
        coverage,
        gold_files,
        source_map,
        chunk_file_indexes,
    )
    node_ids = {
        node.get("local_id")
        for node in projected_nodes
        if type(node.get("local_id")) is str
    }

    projected_edges = [
        edge
        for edge in _project_gold_records(
            gold,
            "edges",
            coverage,
            gold_files,
            source_map,
            chunk_file_indexes,
        )
        if _endpoint_available(edge.get("from"), node_ids)
        and _endpoint_available(edge.get("to"), node_ids)
    ]
    projected_gaps = [
        gap
        for gap in _project_gold_records(
            gold,
            "gaps",
            coverage,
            gold_files,
            source_map,
            chunk_file_indexes,
        )
        if "subject" not in gap or _endpoint_available(gap["subject"], node_ids)
    ]
    return {
        "ir_version": gold.get("ir_version"),
        "chunk_id": chunk_id,
        "files": chunk_files,
        "nodes": projected_nodes,
        "edges": projected_edges,
        "gaps": projected_gaps,
    }


def _new_projection_records(
    current: Mapping[str, object],
    previous: Mapping[str, object],
) -> dict[str, object]:
    """Keep only records that first become visible in the current packet."""

    delta = {key: current.get(key) for key in ("ir_version", "chunk_id", "files")}
    for section in ("nodes", "edges", "gaps"):
        current_records = current.get(section)
        previous_records = previous.get(section)
        if not isinstance(current_records, list) or not isinstance(
            previous_records, list
        ):
            raise RutterDefinitionError(
                f"debug inventory projection {section} must be an array"
            )
        previous_ids = {
            record.get("local_id")
            for record in previous_records
            if isinstance(record, Mapping)
        }
        delta[section] = [
            dict(record)
            for record in current_records
            if isinstance(record, Mapping)
            and record.get("local_id") not in previous_ids
        ]
    return delta


_DIAGNOSIS_RUTTER = DiagnoseAnswer()


def diagnosis_rutter(context: TransitionContext) -> Rutter:
    del context
    return _DIAGNOSIS_RUTTER


def _packet_coverage(
    packets: Sequence[Mapping[str, object]],
) -> set[tuple[str, int]]:
    return {
        (coordinate["source_file"], coordinate["line"])
        for packet in packets
        for coordinate in packet["coordinates"]
    }


def diagnosis_charter(context: TransitionContext) -> JsonObject:
    if not isinstance(context.record, Turn) or context.record.response is None:
        raise RutterDefinitionError("inventory diagnosis requires an accepted report")
    chunk = _chunk(context.evolution)
    packets = _packets(context.evolution)
    completed = len(context.evolution.history.turns(_REPORT_EVOLUTION)) + 1
    coverage = _packet_coverage(packets[:completed])
    prior_coverage = _packet_coverage(packets[: completed - 1])
    chunk_id = chunk.get("chunk_id")
    chunk_files = chunk.get("files")
    if type(chunk_id) is not str or not isinstance(chunk_files, (list, tuple)):
        raise RutterDefinitionError("inventory diagnosis chunk identity is invalid")
    gold = _frozen_gold(context)
    source_map = _frozen_gold_source_map(context)
    expected = _new_projection_records(
        _project_gold(
            gold,
            coverage,
            source_map,
            chunk_id=chunk_id,
            files=chunk_files,
        ),
        _project_gold(
            gold,
            prior_coverage,
            source_map,
            chunk_id=chunk_id,
            files=chunk_files,
        ),
    )
    actual = _canonicalize_inventory_file(
        context.evolution,
        {field: context.record.response[field] for field in ("nodes", "edges", "gaps")},
        packet_count=completed,
        validate_fragment=False,
    )
    packet = packets[completed - 1]
    if not isinstance(packet, Mapping) or type(packet.get("packet_id")) is not str:
        raise RutterDefinitionError("inventory diagnosis packet identity is invalid")
    packet_id = packet["packet_id"]
    decision_basis = context.record.response.get("decision_basis")
    if type(decision_basis) is not str or not decision_basis.strip():
        raise RutterDefinitionError(
            "inventory diagnosis requires a pre-reference decision basis"
        )
    question = QuestionCase(
        f"inventory-{packet_id}",
        "Which inventory entities and direct dependency edges were added for this packet?",
        canonical_text(expected),
        format_hint={
            "ir_version": 3,
            "chunk_id": "...",
            "files": [],
            "nodes": [],
            "edges": [],
            "gaps": [],
        },
        metadata={
            "packet_id": packet_id,
            "decision_basis": decision_basis,
            "covered_coordinates": [
                {"source_file": source, "line": line}
                for source, line in sorted(coverage)
            ],
            "diagnosis_guidance": _DIAGNOSIS_GUIDANCE,
        },
    )
    return DiagnosisCase(
        question,
        canonical_text(actual),
        ask_for_fix=True,
    ).to_json()


def _load_chunk(record: dict[str, object]) -> dict[str, object]:
    chunk_path = Path(str(record.get("chunk_path", ""))).resolve()
    expected = record.get("chunk_sha256")
    if not chunk_path.is_file() or type(expected) is not str:
        raise ValueError(
            f"inventory chunk identity is invalid: {record.get('chunk_id')}"
        )
    chunk_bytes = chunk_path.read_bytes()
    if hashlib.sha256(chunk_bytes).hexdigest() != expected:
        raise ValueError(
            f"inventory chunk identity is invalid: {record.get('chunk_id')}"
        )
    value = json.loads(chunk_bytes)
    if not isinstance(value, dict) or value.get("chunk_id") != record.get("chunk_id"):
        raise ValueError("inventory chunk file does not match its manifest")
    return value


def _gold_charter_data(
    path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    text, value, digest = _freeze_json_file(path, "inventory gold standard")
    if not isinstance(value, dict):
        raise ValueError("inventory gold standard must be a JSON object")
    try:
        validate_inventory_fragment(value)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError("inventory gold standard is schema-invalid") from error
    return value, {
        "inventory_gold_standard_text": text,
        "inventory_gold_standard_sha256": digest,
    }


def _source_aliases_charter_data(
    path: Path,
) -> tuple[dict[str, str], dict[str, object]]:
    text, value, digest = _freeze_json_file(path, "inventory source aliases")
    if not isinstance(value, dict) or any(
        type(key) is not str or not key or type(item) is not str or not item
        for key, item in value.items()
    ):
        raise ValueError("inventory source aliases must be a string mapping")
    return value, {
        "inventory_source_aliases_text": text,
        "inventory_source_aliases_sha256": digest,
    }


def _resolve_gold_source_map(
    gold_sources: object,
    inventory_sources: object,
    aliases: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(gold_sources, list) or any(
        type(source) is not str for source in gold_sources
    ):
        raise ValueError("inventory gold files are invalid")
    if not isinstance(inventory_sources, list) or any(
        type(source) is not str for source in inventory_sources
    ):
        raise ValueError("inventory source manifest is invalid")
    gold_set = set(gold_sources)
    inventory_set = set(inventory_sources)
    unknown_gold = sorted(set(aliases) - gold_set)
    if unknown_gold:
        raise ValueError(
            "inventory source aliases contain unknown gold paths: "
            + ", ".join(unknown_gold)
        )
    unknown_inventory = sorted(set(aliases.values()) - inventory_set)
    if unknown_inventory:
        raise ValueError(
            "inventory source aliases contain unknown inventory paths: "
            + ", ".join(unknown_inventory)
        )

    resolved: dict[str, str] = {}
    for gold_source in gold_sources:
        if gold_source in aliases:
            matches = [aliases[gold_source]]
        else:
            matches = [
                inventory_source
                for inventory_source in inventory_sources
                if _same_source(gold_source, inventory_source)
            ]
        if not matches:
            raise ValueError(
                f"inventory gold source does not map to an inventory source: "
                f"{gold_source}"
            )
        if len(matches) > 1:
            raise ValueError(f"inventory gold source maps ambiguously: {gold_source}")
        resolved[gold_source] = matches[0]
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("inventory gold source map must be one-to-one")
    return resolved


def _manifest_sources(manifest: Mapping[str, object]) -> list[str]:
    records = manifest.get("source_files")
    if not isinstance(records, list):
        raise ValueError("inventory source manifest is invalid")
    sources: list[str] = []
    for record in records:
        if (
            not isinstance(record, Mapping)
            or type(record.get("source_file")) is not str
        ):
            raise ValueError("inventory source manifest is invalid")
        sources.append(record["source_file"])
    return sources


def initiate_run(
    root: Path,
    *,
    mode: str,
    run_id: str,
    doc_entrypoint: str,
    chunk_count: str,
    packet_chars: int,
    definitions: Mapping[str, tuple[Rutter, str]],
    inventory_instruction_path: Path,
    inventory_schema_path: Path,
    inventory_gold_standard: str | None = None,
    inventory_source_aliases: str | None = None,
) -> None:
    """Extract chunks and create one durable Voyage for each chunk."""

    if mode not in definitions:
        raise ValueError(f"unknown inventory Voyage mode {mode!r}")
    if mode == "debug" and inventory_gold_standard is None:
        raise ValueError("debug mode requires an inventory gold standard")
    if mode == "debug" and inventory_source_aliases is None:
        raise ValueError("debug mode requires inventory source aliases")
    if mode == "default" and (
        inventory_gold_standard is not None or inventory_source_aliases is not None
    ):
        raise ValueError(
            "default mode does not accept inventory gold or source aliases"
        )
    definition, rutter_name = definitions[mode]

    try:
        requested_chunks = int(chunk_count)
    except (TypeError, ValueError) as error:
        raise ValueError("chunk_count must be a positive integer") from error
    if requested_chunks < 1:
        raise ValueError("chunk_count must be a positive integer")
    source = Path(doc_entrypoint).resolve()
    if source.suffix.lower() not in {".tex", ".md"}:
        raise ValueError("doc_entrypoint must be a .tex or .md file")
    root = root.resolve()
    report = extract_inventory_chunks(
        source,
        root / "artifacts" / run_id,
        workers=requested_chunks,
        packet_chars=packet_chars,
    )
    chunk_manifest = Path(str(report["manifest_path"])).resolve()

    manifest = load_chunk_manifest(chunk_manifest)
    instruction, instruction_digest = _freeze_utf8_file(
        inventory_instruction_path, "inventory instructions"
    )
    schema, schema_value, schema_digest = _freeze_json_file(
        inventory_schema_path, "inventory schema"
    )
    _json_object(schema_value, "inventory schema")
    charter_data: dict[str, object] = {
        "inventory_instruction_text": instruction,
        "inventory_instruction_sha256": instruction_digest,
        "inventory_schema_text": schema,
        "inventory_schema_sha256": schema_digest,
    }
    if inventory_gold_standard is not None and inventory_source_aliases is not None:
        gold, gold_data = _gold_charter_data(Path(inventory_gold_standard).resolve())
        aliases, alias_data = _source_aliases_charter_data(
            Path(inventory_source_aliases).resolve()
        )
        source_map = _resolve_gold_source_map(
            gold.get("files"),
            _manifest_sources(manifest),
            aliases,
        )
        source_map_text = canonical_text(source_map)
        charter_data.update(gold_data)
        charter_data.update(alias_data)
        charter_data["inventory_gold_source_map_text"] = source_map_text
        charter_data["inventory_gold_source_map_sha256"] = hashlib.sha256(
            source_map_text.encode("utf-8")
        ).hexdigest()
    voyages_dir = root / "voyages" / run_id
    if any(
        voyage_id.rsplit("/", 1)[0] == run_id
        for voyage_id in voyage_paths(root)
    ):
        raise FileExistsError(
            f"inventory Voyages already exist for run ID {run_id!r}"
        )
    voyages_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = root / "artifacts" / run_id
    for index, chunk_value in enumerate(manifest["chunks"], start=1):
        if not isinstance(chunk_value, dict):
            raise ValueError("inventory chunk manifest contains a non-object chunk")
        voyage_id = f"{run_id}/{index}"
        _create_voyage(
            definition,
            _load_chunk(chunk_value),
            voyages_dir / str(index),
            artifacts_dir / "inventories" / f"{index}.json",
            artifacts_dir / "source-packets" / f"{index}.txt",
            rutter_name=rutter_name,
            run_dir=root,
            charter_data=charter_data,
        )


def voyage_paths(
    root: Path,
    run_prefix: str | None = None,
) -> dict[str, Path]:
    root = root.resolve()
    reckoning = _RECKONING_NAME
    if (root / reckoning).is_file():
        return {root.name: root}
    voyages = root / "voyages"
    if not voyages.is_dir():
        return {}
    paths: dict[str, Path] = {}
    for reckoning_path in sorted(voyages.rglob(reckoning)):
        voyage_path = reckoning_path.parent
        relative = voyage_path.relative_to(voyages)
        if len(relative.parts) not in {1, 2, 3}:
            continue
        voyage_id = relative.as_posix()
        if run_prefix is None or voyage_id.startswith(f"{run_prefix}/"):
            paths[voyage_id] = voyage_path
    return paths


def release_voyage(root: Path, voyage_id: str, *, debug_rutter_id: str) -> None:
    voyage_path = voyage_paths(root)[voyage_id]
    voyages_dir = (root / "voyages").resolve()
    relative = voyage_path.resolve().relative_to(voyages_dir)
    if (
        voyage_path.is_symlink()
        or voyage_path.parent.is_symlink()
        or any(
            voyages_dir.joinpath(*relative.parts[:depth]).is_symlink()
            for depth in range(1, len(relative.parts))
        )
        or len(relative.parts) not in {1, 2, 3}
    ):
        raise ValueError("inventory Voyage working directory is unsafe to release")
    reckoning_path = voyage_path / _RECKONING_NAME
    reckoning_bytes = read_regular_file_bytes(reckoning_path, allowed_root=voyage_path)
    reckoning = json.loads(reckoning_bytes)
    reckoning_root = reckoning.get("root") if isinstance(reckoning, Mapping) else None
    rutter_id = (
        reckoning_root.get("rutter_id") if isinstance(reckoning_root, Mapping) else None
    )
    if type(rutter_id) is not str:
        raise ValueError("inventory Voyage reckoning has no Rutter identity")
    if rutter_id == debug_rutter_id:
        artifacts_root = root / "artifacts"
        run_artifacts = artifacts_root.joinpath(*relative.parts[:-1])
        if (
            artifacts_root.is_symlink()
            or not artifacts_root.is_dir()
            or run_artifacts.is_symlink()
            or not run_artifacts.is_dir()
        ):
            raise ValueError("debug inventory diagnostics directory is unsafe")
        diagnostics_dir = run_artifacts / "diagnostics"
        diagnostics_dir.mkdir(exist_ok=True)
        if diagnostics_dir.is_symlink() or not diagnostics_dir.is_dir():
            raise ValueError("debug inventory diagnostics directory is unsafe")
        atomic_replace_bytes(
            diagnostics_dir / f"{relative.parts[-1]}.reckoning.json",
            reckoning_bytes,
            allowed_root=run_artifacts,
            mode=0o600,
        )
    shutil.rmtree(voyage_path)
    run_path = voyage_path.parent
    if len(relative.parts) >= 2 and run_path.is_dir() and not any(run_path.iterdir()):
        run_path.rmdir()
