#!/usr/bin/env python3
"""Support inventory Voyage packet iteration and durable chunk output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from jsonschema import ValidationError
from officina.common.atomic_files import atomic_replace_bytes
from officina.rutter import (
    EvolutionContext,
    JsonObject,
    LLMResponseContext,
    MachineContext,
    MachineResult,
    Rutter,
    RutterDefinitionError,
    RutterRegistry,
    ValidationIssue,
    ValidationReport,
    VoyageResult,
)

from ._chunk_pooler import validate_inventory_fragment


_RUTTER_NAME = "inventory-voyage"
_RECKONING_NAME = "inventory-voyage.reckoning.json"
_REPORT_EVOLUTION = "report"


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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _invalid(
    path: tuple[str | int, ...], code: str, message: str
) -> ValidationReport:
    return ValidationReport(False, (ValidationIssue(path, code, message),))


def _frozen_text(context: EvolutionContext, name: str) -> str:
    text = context.charter.data.get(f"{name}_text")
    digest = context.charter.data.get(f"{name}_sha256")
    if type(text) is not str or type(digest) is not str:
        raise RutterDefinitionError(f"inventory Charter {name} snapshot is invalid")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RutterDefinitionError(f"inventory Charter {name} snapshot hash is invalid")
    return text


def _chunk(context: EvolutionContext) -> dict[str, object]:
    return _json_object(context.charter.data.get("chunk"), "inventory chunk")


def _packets(context: EvolutionContext) -> list[dict[str, object]]:
    packets = _chunk(context).get("packets")
    if not isinstance(packets, (list, tuple)) or not packets:
        raise RutterDefinitionError("inventory chunk packets are unavailable")
    return [_json_object(packet, "inventory packet") for packet in packets]


def _packet_index(context: EvolutionContext) -> int:
    return len(context.history.turns(_REPORT_EVOLUTION))


def _initial_inventory(chunk: dict[str, object]) -> dict[str, object]:
    return {
        "ir_version": 3,
        "chunk_id": chunk["chunk_id"],
        "files": list(chunk["files"]),
        "nodes": [],
        "edges": [],
        "gaps": [],
    }


def _prior_inventory(context: EvolutionContext) -> dict[str, object]:
    latest = context.history.latest_turn(_REPORT_EVOLUTION)
    if latest is None or latest.response is None:
        return _initial_inventory(_chunk(context))
    return _json_object(latest.response.get("inventory"), "prior inventory")


def report_data(context: EvolutionContext) -> JsonObject:
    """Present exactly one packet from the Voyage's immutable chunk."""

    packets = _packets(context)
    index = _packet_index(context)
    if index >= len(packets):
        raise RutterDefinitionError("no inventory packet remains")
    packet = packets[index]
    schema_text = _frozen_text(context, "inventory_schema")
    try:
        schema = _json_object(json.loads(schema_text), "inventory schema")
    except json.JSONDecodeError as error:
        raise RutterDefinitionError("inventory schema snapshot is invalid") from error
    return {
        "chunk_id": _chunk(context)["chunk_id"],
        "inventory_instruction": _frozen_text(context, "inventory_instruction"),
        "packet": packet,
        "packet_count": len(packets),
        "prior_inventory": _prior_inventory(context),
        "output_schema": schema,
    }


def assess_report(context: LLMResponseContext) -> ValidationReport:
    """Require a cumulative chunk inventory for the displayed packet."""

    packets = _packets(context.evolution)
    index = _packet_index(context.evolution)
    if index >= len(packets):
        return _invalid(("packet_id",), "packet-exhausted", "no packet remains")
    expected_packet_id = packets[index]["packet_id"]
    if context.response["packet_id"] != expected_packet_id:
        return _invalid(
            ("packet_id",),
            "wrong-packet",
            "packet_id must match the displayed packet",
        )
    try:
        inventory = _json_object(context.response["inventory"], "reported inventory")
        validate_inventory_fragment(inventory)
    except (TypeError, ValueError, ValidationError) as error:
        return _invalid(("inventory",), "invalid-inventory", str(error))
    chunk = _chunk(context.evolution)
    if inventory.get("chunk_id") != chunk["chunk_id"]:
        return _invalid(
            ("inventory", "chunk_id"),
            "wrong-chunk",
            "inventory chunk_id must match the Voyage chunk",
        )
    if inventory.get("files") != chunk["files"]:
        return _invalid(
            ("inventory", "files"),
            "wrong-files",
            "inventory files must exactly match the Voyage chunk files",
        )
    return ValidationReport(True)


def _write_inventory(context: EvolutionContext, inventory: dict[str, object]) -> None:
    output_value = context.charter.data.get("inventory_path")
    run_value = context.charter.data.get("run_dir")
    if type(output_value) is not str or type(run_value) is not str:
        raise RutterDefinitionError("inventory output boundary is unavailable")
    output_path = Path(output_value).resolve()
    run_dir = Path(run_value).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace_bytes(
        output_path,
        _canonical_bytes(inventory) + b"\n",
        allowed_root=run_dir,
        mode=0o600,
    )


def record_iteration(context: MachineContext) -> MachineResult:
    """Persist the cumulative inventory and advance to the next packet."""

    turn = context.evolution.history.require_latest_turn(_REPORT_EVOLUTION)
    if turn.response is None:
        raise RutterDefinitionError("inventory record step requires a response")
    inventory = _json_object(turn.response["inventory"], "reported inventory")
    _write_inventory(context.evolution, inventory)
    completed = len(context.evolution.history.turns(_REPORT_EVOLUTION))
    total = len(_packets(context.evolution))
    return MachineResult(
        "done" if completed >= total else "more",
        {
            "chunk_id": inventory["chunk_id"],
            "packet_id": turn.response["packet_id"],
            "packets_completed": completed,
            "packets_total": total,
        },
    )


def complete_result(context: EvolutionContext) -> VoyageResult:
    latest = context.history.require_latest_turn(_REPORT_EVOLUTION)
    if latest.response is None:
        raise RutterDefinitionError("completed inventory Voyage has no response")
    inventory = _json_object(latest.response["inventory"], "completed inventory")
    return VoyageResult(
        "complete",
        {
            "chunk_id": inventory["chunk_id"],
            "inventory_path": context.charter.data["inventory_path"],
            "packets": len(context.history.turns(_REPORT_EVOLUTION)),
            "nodes": len(inventory["nodes"]),
            "edges": len(inventory["edges"]),
            "gaps": len(inventory["gaps"]),
        },
    )


def _freeze_utf8_file(path: Path, label: str) -> tuple[str, str]:
    try:
        content = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is not readable UTF-8") from error
    if not content:
        raise ValueError(f"{label} must not be empty")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


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
    expected_ids = [
        f"{chunk_id}-packet-{index:03d}"
        for index in range(1, len(packets) + 1)
    ]
    actual_ids: list[str] = []
    for index, packet_value in enumerate(packets, start=1):
        packet = _json_object(packet_value, "inventory packet")
        if packet.get("packet_index") != index:
            raise ValueError("inventory packet indexes must be contiguous")
        packet_id = packet.get("packet_id")
        text = packet.get("text")
        coordinates = packet.get("coordinates")
        if type(packet_id) is not str:
            raise ValueError("inventory packet_id must be a string")
        if type(text) is not str or not text:
            raise ValueError("inventory packet text must be nonempty")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("inventory packet coordinates must be nonempty")
        actual_ids.append(packet_id)
    if actual_ids != expected_ids:
        raise ValueError("inventory packet IDs must be ordered and chunk-qualified")
    _canonical_bytes(chunk)
    return chunk


def setup_voyage(
    definition: Rutter,
    chunk: object,
    voyage_dir: Path,
    inventory_path: Path,
    *,
    rutter_name: str = _RUTTER_NAME,
    run_dir: Path,
    inventory_instruction_path: Path,
    inventory_schema_path: Path,
    extra_charter_data: Mapping[str, object] | None = None,
):
    """Create one durable Voyage for one immutable worker chunk."""

    voyage_dir = voyage_dir.resolve()
    run_dir = run_dir.resolve()
    if voyage_dir.exists():
        raise FileExistsError(f"inventory Voyage already exists: {voyage_dir}")
    sealed_chunk = _seal_chunk(chunk)
    instruction, instruction_sha256 = _freeze_utf8_file(
        inventory_instruction_path, "inventory instructions"
    )
    schema, schema_sha256 = _freeze_utf8_file(
        inventory_schema_path, "inventory schema"
    )
    try:
        _json_object(json.loads(schema), "inventory schema")
    except json.JSONDecodeError as error:
        raise ValueError("inventory schema is invalid JSON") from error
    extras = dict(extra_charter_data or {})
    reserved = {
        "run_dir",
        "inventory_path",
        "chunk",
        "chunk_sha256",
        "inventory_instruction_text",
        "inventory_instruction_sha256",
        "inventory_schema_text",
        "inventory_schema_sha256",
    }
    if reserved.intersection(extras):
        raise ValueError("extra inventory Charter data conflicts with owned fields")
    _canonical_bytes(extras)
    voyage_dir.mkdir(parents=True)
    return RutterRegistry({rutter_name: definition}, voyage_dir).create(
        rutter_name,
        Path(_RECKONING_NAME),
        {
            "run_dir": str(run_dir),
            "inventory_path": str(inventory_path.resolve()),
            "chunk": sealed_chunk,
            "chunk_sha256": hashlib.sha256(_canonical_bytes(sealed_chunk)).hexdigest(),
            "inventory_instruction_text": instruction,
            "inventory_instruction_sha256": instruction_sha256,
            "inventory_schema_text": schema,
            "inventory_schema_sha256": schema_sha256,
            **extras,
        },
    )


def open_voyage(definitions: Mapping[str, Rutter], voyage_dir: Path):
    root = voyage_dir.resolve()
    return RutterRegistry(definitions, root).open(Path(_RECKONING_NAME))


def reckoning_name() -> str:
    return _RECKONING_NAME
