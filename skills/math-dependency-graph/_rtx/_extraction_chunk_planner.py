#!/usr/bin/env python3
"""Plan bounded LLM work packets for mathematical graph extraction.

This module is deliberately non-semantic. It partitions active source spans for
inventory workers and materializes the sole extract packet from pooled
inventory. It records complete assignments before a pool starts and never
classifies entities or infers relationships.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable, Sequence

import jsonschema

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._semantic_graph_compiler import load_json_object
    from ._source_packet import visible_environment_ranges
except ImportError:  # pragma: no cover - supports direct script execution
    from _semantic_graph_compiler import load_json_object
    from _source_packet import visible_environment_ranges


INVENTORY_SOURCE_TARGET_TOKENS = 60_000
TOTAL_CONTEXT_CEILING_TOKENS = 95_000
INVENTORY_FIXED_CONTEXT_TOKENS = 10_000
INVENTORY_MAX_VISIBLE_ANCHORS = 16
DEFAULT_TARGET_TOKENS = INVENTORY_SOURCE_TARGET_TOKENS
DEFAULT_HARD_MAX_TOKENS = 60_700
CHUNK_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "chunk-plan.schema.json"
PROOF_BEGIN_RE = re.compile(r"\\begin\{(?P<name>proof[A-Za-z@*-]*)\}")
PROOF_END_RE = re.compile(r"\\end\{(?P<name>proof[A-Za-z@*-]*)\}")
SOURCE_MARKER_RE = re.compile(r"^@@ source: (?P<source>.+)$")
SOURCE_LINE_RE = re.compile(r"^(?P<line>[0-9]+) \| ?(?P<text>.*)$")


def validate_chunk_manifest(payload: dict) -> None:
    """Validate manifest shape, mode fields, and exactly-once assignments."""

    schema = json.loads(CHUNK_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator_class(schema).validate(payload)
    if payload["target_tokens"] > payload["hard_max_tokens"]:
        raise ValueError("chunk target tokens exceed hard maximum")
    chunk_ids = [chunk["chunk_id"] for chunk in payload["chunks"]]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("chunk ids must be unique")
    if payload["mode"] == "extract":
        if chunk_ids != ["extract-001"]:
            raise ValueError("extract planning requires exactly one extract-001 chunk")
        chunk = payload["chunks"][0]
        required_paths = (
            "packet_path",
            "fragment_path",
            "progress_path",
            "sidecar_path",
            "source_packet_path",
            "entrypoint_path",
        )
        if any(not isinstance(chunk.get(path), str) or not chunk[path] for path in required_paths):
            raise ValueError(
                "extract chunk requires packet, fragment, sidecar, source, and entrypoint paths"
            )
        return
    if not isinstance(payload.get("source_sha256"), str):
        raise ValueError("inventory chunk manifest requires active source_sha256")
    assignments: list[object] = []
    for chunk in payload["chunks"]:
        if "spans" not in chunk:
            raise ValueError("inventory chunk requires spans")
        if not isinstance(chunk.get("packet_sha256"), str):
            raise ValueError("inventory chunk requires packet_sha256")
        if not isinstance(chunk.get("owned_bytes"), int) or chunk["owned_bytes"] < 1:
            raise ValueError("inventory chunk requires positive owned_bytes")
        assignments.extend(
            (
                span["source_file"],
                line_number,
            )
            for span in chunk["spans"]
            for line_number in range(span["start_line"], span["end_line"] + 1)
        )
    if len(assignments) != len(set(assignments)):
        raise ValueError(f"{payload['mode']} chunk assignments overlap")


def estimate_tokens(value: str | object) -> int:
    """Return a conservative deterministic token estimate from UTF-8 text."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, math.ceil(len(text) / 4))


def projected_inventory_context(source_tokens: int) -> int:
    """Reserve instructions and candidate output around active source input."""

    if source_tokens < 0:
        raise ValueError("source token estimate must be nonnegative")
    output_reserve = max(12_000, math.ceil(source_tokens * 0.4))
    return INVENTORY_FIXED_CONTEXT_TOKENS + source_tokens + output_reserve


def _write_text_atomic(text: str, path: Path) -> None:
    """Replace one UTF-8 packet atomically beside its final destination."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _write_json_atomic(payload: dict, path: Path, *, compact: bool = False) -> None:
    """Write one JSON artifact atomically, optionally without presentation space."""

    kwargs = {"ensure_ascii": False}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    _write_text_atomic(json.dumps(payload, **kwargs) + "\n", path)


def index_source_packet(packet_text: str) -> dict[tuple[str, int], tuple[int, str]]:
    """Index packet coordinates without importing the retired ledger normalizer."""

    current_source: str | None = None
    ordinal = 0
    indexed: dict[tuple[str, int], tuple[int, str]] = {}
    for packet_line in packet_text.splitlines():
        marker = SOURCE_MARKER_RE.match(packet_line)
        if marker:
            current_source = marker.group("source")
            continue
        numbered = SOURCE_LINE_RE.match(packet_line)
        if numbered is None or current_source is None:
            continue
        line_number = int(numbered.group("line"))
        indexed.setdefault(
            (current_source, line_number),
            (ordinal, numbered.group("text").lstrip()),
        )
        ordinal += 1
    if not indexed:
        raise ValueError("source packet contains no indexed source lines")
    return indexed


def _ordered_packet_lines(packet_text: str) -> list[tuple[str, int, str, int]]:
    """Return unique packet coordinates in first-expansion order."""

    indexed = index_source_packet(packet_text)
    return [
        (source_file, line_number, source_text, ordinal)
        for (source_file, line_number), (ordinal, source_text) in sorted(
            indexed.items(), key=lambda item: item[1][0]
        )
    ]


def _proof_ranges(numbered_lines: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """Locate balanced proof-like environments without interpreting their content."""

    ranges: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    for line_number, source_text in numbered_lines:
        for match in PROOF_BEGIN_RE.finditer(source_text):
            stack.append((match.group("name"), line_number))
        for match in PROOF_END_RE.finditer(source_text):
            name = match.group("name")
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == name:
                    _matched_name, start_line = stack.pop(index)
                    ranges.append((start_line, line_number))
                    break
    return ranges


def _source_units(packet_text: str) -> list[dict]:
    """Split packet files at blank safe boundaries outside visible blocks and proofs."""

    ordered = _ordered_packet_lines(packet_text)
    declaration_text = "\n".join(source_text for _, _, source_text, _ in ordered)
    file_order: list[str] = []
    by_file: dict[str, list[tuple[int, str]]] = {}
    for source_file, line_number, source_text, _ordinal in ordered:
        if source_file not in by_file:
            file_order.append(source_file)
            by_file[source_file] = []
        by_file[source_file].append((line_number, source_text))

    units: list[dict] = []
    for source_file in file_order:
        numbered = sorted(by_file[source_file])
        visible_ranges = visible_environment_ranges(
            numbered,
            declaration_text=declaration_text,
        )
        protected = [
            (start, end)
            for start, end, _environment, _wrapper in visible_ranges
        ]
        protected.extend(_proof_ranges(numbered))
        start = numbered[0][0]
        accumulated: list[str] = []
        for position, (line_number, source_text) in enumerate(numbered):
            accumulated.append(source_text)
            inside_protected = any(begin <= line_number < end for begin, end in protected)
            at_end = position == len(numbered) - 1
            safe_boundary = at_end or (not source_text.strip() and not inside_protected)
            if not safe_boundary:
                continue
            units.append(
                {
                    "span": {
                        "source_file": source_file,
                        "start_line": start,
                        "end_line": line_number,
                    },
                    "text": "\n".join(accumulated),
                    "anchor_count": sum(
                        start <= anchor_start and anchor_end <= line_number
                        for anchor_start, anchor_end, _environment, _wrapper in visible_ranges
                    ),
                }
            )
            accumulated = []
            if not at_end:
                start = numbered[position + 1][0]
    return units


def _pack_units(
    units: Sequence[dict],
    target_tokens: int,
    hard_max_tokens: int,
    *,
    max_anchor_count: int | None = None,
) -> list[list[dict]]:
    """Pack ordered units within source and structured-output workload limits."""

    if target_tokens < 1 or hard_max_tokens < target_tokens:
        raise ValueError("chunk token limits require 1 <= target <= hard maximum")
    packed: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    current_anchors = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit.get("text", unit))
        unit_anchors = int(unit.get("anchor_count", 0))
        if current and (
            current_tokens >= target_tokens
            or current_tokens + unit_tokens > hard_max_tokens
            or (
                max_anchor_count is not None
                and current_anchors + unit_anchors > max_anchor_count
            )
        ):
            packed.append(current)
            current = []
            current_tokens = 0
            current_anchors = 0
        current.append(unit)
        current_tokens += unit_tokens
        current_anchors += unit_anchors
    if current:
        packed.append(current)
    return packed


def _pack_payload_units(
    units: Sequence[dict],
    build_payload: Callable[[Sequence[dict]], dict],
    target_tokens: int,
    hard_max_tokens: int,
) -> list[list[dict]]:
    """Pack records using the complete repeated packet context in the estimate."""

    if target_tokens < 1 or hard_max_tokens < target_tokens:
        raise ValueError("chunk token limits require 1 <= target <= hard maximum")
    packed: list[list[dict]] = []
    current: list[dict] = []
    current_unit_tokens = 0
    for unit in units:
        proposed = [*current, unit]
        payload = build_payload(proposed)
        proposed_tokens = estimate_tokens(payload)
        if current and (
            current_unit_tokens >= target_tokens
            or proposed_tokens > hard_max_tokens
        ):
            packed.append(current)
            current = [unit]
            current_unit_tokens = estimate_tokens(unit.get("text", unit))
            continue
        current = proposed
        current_unit_tokens += estimate_tokens(unit.get("text", unit))
    if current:
        packed.append(current)
    return packed


def _inventory_packet(
    chunk_id: str,
    units: Sequence[dict],
    source_lines: dict[str, dict[int, str]],
    *,
    padding_lines: int = 20,
) -> str:
    """Materialize owned spans and read-only boundary context for discovery."""

    lines = [
        "# math-dependency-graph inventory chunk",
        f"# chunk-id: {chunk_id}",
        "# Only assigned spans own candidate decisions.",
        "# Boundary-context lines are read-only and may not own candidates.",
        "",
    ]
    for unit in units:
        span = unit["span"]
        source_file = str(span["source_file"])
        start_line = int(span["start_line"])
        end_line = int(span["end_line"])
        available = source_lines[source_file]
        before = [
            line_number
            for line_number in range(max(min(available), start_line - padding_lines), start_line)
            if line_number in available
        ]
        after = [
            line_number
            for line_number in range(end_line + 1, min(max(available), end_line + padding_lines) + 1)
            if line_number in available
        ]
        if before:
            lines.append(
                f"# boundary-context-before: {source_file}:{before[0]}-{before[-1]}"
            )
            lines.append(f"@@ source: {source_file}")
            lines.extend(f"{line_number:04d} | {available[line_number]}" for line_number in before)
        lines.append(
            f"# assigned-span: {source_file}:{start_line}-{end_line}"
        )
        lines.append(f"@@ source: {source_file}")
        for offset, source_text in enumerate(unit["text"].split("\n")):
            line_number = start_line + offset
            lines.append(f"{line_number:04d} | {source_text}")
        if after:
            lines.append(
                f"# boundary-context-after: {source_file}:{after[0]}-{after[-1]}"
            )
            lines.append(f"@@ source: {source_file}")
            lines.extend(f"{line_number:04d} | {available[line_number]}" for line_number in after)
        lines.append("")
    return "\n".join(lines) + "\n"


def _owned_visible_anchors(
    units: Sequence[dict], source_lines: dict[str, dict[int, str]]
) -> list[dict]:
    """Retain formal-environment anchors outside the worker-visible packet."""

    anchors: list[dict] = []
    for source_file, numbered in source_lines.items():
        owned_spans = [
            unit["span"] for unit in units if unit["span"]["source_file"] == source_file
        ]
        for start_line, end_line, environment, wrapper in visible_environment_ranges(
            sorted(numbered.items())
        ):
            if not any(
                int(span["start_line"]) <= start_line
                and end_line <= int(span["end_line"])
                for span in owned_spans
            ):
                continue
            anchor = {
                "source_file": source_file,
                "start_line": start_line,
                "end_line": end_line,
                "environment": environment,
            }
            if wrapper is not None:
                anchor["wrapper"] = wrapper
            anchors.append(anchor)
    return anchors


def _owned_line_bytes(packet_text: str, units: Sequence[dict]) -> int:
    """Measure the exact numbered owned-line bytes used by compactness checks."""

    spans_by_file: dict[str, list[tuple[int, int]]] = {}
    for unit in units:
        span = unit["span"]
        spans_by_file.setdefault(str(span["source_file"]), []).append(
            (int(span["start_line"]), int(span["end_line"]))
        )
    current_source: str | None = None
    seen: set[tuple[str, int]] = set()
    total = 0
    for raw_line in packet_text.encode("utf-8").splitlines(keepends=True):
        text = raw_line.decode("utf-8").rstrip("\r\n")
        marker = SOURCE_MARKER_RE.match(text)
        if marker:
            current_source = marker.group("source")
            continue
        numbered = SOURCE_LINE_RE.match(text)
        if current_source is None or numbered is None:
            continue
        line_number = int(numbered.group("line"))
        key = (current_source, line_number)
        if key in seen:
            continue
        if any(
            start <= line_number <= end
            for start, end in spans_by_file.get(current_source, [])
        ):
            seen.add(key)
            total += len(raw_line)
    if total < 1:
        raise ValueError("inventory packet has no measurable owned line bytes")
    return total


def _coalesce_units(units: Sequence[dict]) -> list[dict]:
    """Join adjacent spans from the same file after workload packing."""

    result: list[dict] = []
    for unit in units:
        span = unit["span"]
        if (
            result
            and result[-1]["span"]["source_file"] == span["source_file"]
            and int(result[-1]["span"]["end_line"]) + 1 == int(span["start_line"])
        ):
            result[-1]["span"]["end_line"] = span["end_line"]
            result[-1]["text"] += "\n" + unit["text"]
            result[-1]["anchor_count"] = int(result[-1].get("anchor_count", 0)) + int(
                unit.get("anchor_count", 0)
            )
        else:
            result.append(deepcopy(unit))
    return result


def plan_inventory_chunks(
    packet_text: str,
    *,
    source_packet_path: Path,
    output_dir: Path,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    hard_max_tokens: int = DEFAULT_HARD_MAX_TOKENS,
) -> dict:
    """Plan and materialize ordered source-span inventory chunks."""

    output_dir = output_dir.resolve()
    ordered_lines = _ordered_packet_lines(packet_text)
    source_lines: dict[str, dict[int, str]] = {}
    for source_file, line_number, source_text, _ordinal in ordered_lines:
        source_lines.setdefault(source_file, {})[line_number] = source_text
    packed = _pack_units(
        _source_units(packet_text),
        target_tokens,
        hard_max_tokens,
        max_anchor_count=INVENTORY_MAX_VISIBLE_ANCHORS,
    )
    chunks: list[dict] = []
    for index, units in enumerate(packed, 1):
        units = _coalesce_units(units)
        chunk_id = f"inventory-{index:03d}"
        packet_path = output_dir / "inventory-packets" / f"{chunk_id}.txt"
        fragment_path = output_dir / "inventory-fragments" / f"{chunk_id}.json"
        progress_path = output_dir / "progress" / f"{chunk_id}.progress.md"
        packet = _inventory_packet(chunk_id, units, source_lines)
        owned_source_tokens = sum(estimate_tokens(unit["text"]) for unit in units)
        projected_total_tokens = projected_inventory_context(owned_source_tokens)
        if projected_total_tokens > TOTAL_CONTEXT_CEILING_TOKENS:
            raise ValueError(
                f"inventory chunk {chunk_id} exceeds projected context ceiling"
            )
        _write_text_atomic(packet, packet_path)
        chunks.append(
            {
                "chunk_id": chunk_id,
                "estimated_tokens": estimate_tokens(packet),
                "projected_total_tokens": projected_total_tokens,
                "packet_path": str(packet_path),
                "packet_sha256": hashlib.sha256(packet.encode("utf-8")).hexdigest(),
                "owned_bytes": _owned_line_bytes(packet, units),
                "anchors": _owned_visible_anchors(units, source_lines),
                "fragment_path": str(fragment_path),
                "progress_path": str(progress_path),
                "spans": [deepcopy(unit["span"]) for unit in units],
            }
        )
    manifest = {
        "plan_version": 1,
        "mode": "inventory",
        "source": str(source_packet_path.resolve()),
        "source_sha256": hashlib.sha256(packet_text.encode("utf-8")).hexdigest(),
        "target_tokens": target_tokens,
        "hard_max_tokens": hard_max_tokens,
        "chunks": chunks,
    }
    validate_chunk_manifest(manifest)
    _write_json_atomic(manifest, output_dir / "inventory-chunks.json")
    return manifest


def plan_extract_packet(
    inventory: dict,
    *,
    source_packet_path: Path,
    entrypoint_path: Path,
    output_dir: Path,
) -> dict:
    """Materialize the sole document-wide extract packet from pooled inventory.

    The inventory is an index of graph-relevant findings, not a source-text
    substitute.  The packet therefore embeds the pooled records but carries the
    immutable source and coordinate sidecar by path only.  The worker may reopen
    only registered locations with the stated bounded lookup limits.
    """

    if inventory.get("ir_version") != 2 or inventory.get("chunk_id") != "pooled":
        raise ValueError("extract planning requires pooled inventory version 2")
    files = inventory.get("files")
    if not isinstance(files, list) or not files or not all(
        isinstance(item, str) and item for item in files
    ):
        raise ValueError("pooled inventory requires a nonempty file table")
    source_path = source_packet_path.resolve()
    if not source_path.is_file():
        raise ValueError("extract planning requires an existing immutable source packet")
    entrypoint = entrypoint_path.resolve()
    if not entrypoint.is_file():
        raise ValueError("extract planning requires the retained TeX entrypoint")
    output_dir = output_dir.resolve()
    chunk_id = "extract-001"
    packet_path = output_dir / "extract-packets" / f"{chunk_id}.json"
    fragment_path = output_dir / "extract-fragments" / f"{chunk_id}.json"
    progress_path = output_dir / "progress" / f"{chunk_id}.progress.md"
    sidecar_path = output_dir / "extract-sidecars" / f"{chunk_id}.json"
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    lookup_rules = {
        "max_context_lines": 20,
        "max_locations_per_request": 32,
        "require_registered_locations": True,
    }
    sidecar = {
        "sidecar_version": 1,
        "source_packet_path": str(source_path),
        "source_sha256": source_sha256,
        "files": deepcopy(files),
        "lookup_rules": deepcopy(lookup_rules),
    }
    packet = {
        "packet_version": 1,
        "mode": "extract",
        "chunk_id": chunk_id,
        "inventory": deepcopy(inventory),
        "entrypoint_path": str(entrypoint),
        "source_packet_path": str(source_path),
        "coordinate_sidecar_path": str(sidecar_path),
        "lookup_rules": lookup_rules,
    }
    packet_tokens = estimate_tokens(packet) + estimate_tokens(sidecar)
    projected_total_tokens = packet_tokens + 12_000
    if projected_total_tokens > TOTAL_CONTEXT_CEILING_TOKENS:
        raise ValueError("essential whole-document extract packet exceeds context ceiling")
    _write_json_atomic(sidecar, sidecar_path, compact=True)
    _write_json_atomic(packet, packet_path, compact=True)
    manifest = {
        "plan_version": 1,
        "mode": "extract",
        "source": str(source_path),
        "target_tokens": DEFAULT_TARGET_TOKENS,
        "hard_max_tokens": DEFAULT_HARD_MAX_TOKENS,
        "chunks": [
            {
                "chunk_id": chunk_id,
                "estimated_tokens": packet_tokens,
                "projected_total_tokens": projected_total_tokens,
                "packet_path": str(packet_path),
                "fragment_path": str(fragment_path),
                "progress_path": str(progress_path),
                "sidecar_path": str(sidecar_path),
                "source_packet_path": str(source_path),
                "entrypoint_path": str(entrypoint),
            }
        ],
    }
    validate_chunk_manifest(manifest)
    _write_json_atomic(manifest, output_dir / "extract-chunks.json")
    return manifest


def main(argv: Iterable[str] | None = None) -> None:
    """Plan one extraction phase and emit a concise machine report."""

    parser = argparse.ArgumentParser(description="Plan bounded mathematical extraction chunks.")
    parser.add_argument(
        "mode", choices=("inventory", "extract")
    )
    parser.add_argument("source", help="Source packet or pooled inventory")
    parser.add_argument("--source-packet", help="Immutable source packet required for extract mode")
    parser.add_argument("--entrypoint", help="Retained TeX entrypoint required for extract mode")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--hard-max-tokens", type=int, default=DEFAULT_HARD_MAX_TOKENS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    source_path = Path(args.source).resolve()
    output_dir = Path(args.out_dir).resolve()
    options = {
        "output_dir": output_dir,
        "target_tokens": args.target_tokens,
        "hard_max_tokens": args.hard_max_tokens,
    }
    if args.mode == "inventory":
        manifest = plan_inventory_chunks(
            source_path.read_text(encoding="utf-8"),
            source_packet_path=source_path,
            **options,
        )
    else:
        if not args.source_packet or not args.entrypoint:
            raise ValueError("extract mode requires --source-packet and --entrypoint")
        manifest = plan_extract_packet(
            load_json_object(source_path, "pooled inventory"),
            source_packet_path=Path(args.source_packet),
            entrypoint_path=Path(args.entrypoint),
            output_dir=output_dir,
        )
    print(
        json.dumps(
            {
                "mode": args.mode,
                "chunks": len(manifest["chunks"]),
                "manifest": str(output_dir / f"{args.mode}-chunks.json"),
            },
            indent=2,
        )
    )


class Interface(PythonArgvMachineInterface):
    """Expose deterministic extraction chunk planning through the machine protocol."""

    prog = "extraction_chunk_planner.py"

    def run(self, argv: list[str]) -> int:
        main(argv)
        return 0


if __name__ == "__main__":
    main()
