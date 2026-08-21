#!/usr/bin/env python3
"""Create durable, source-ordered inventory units without semantic decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Callable, Iterable

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

try:
    from ._batch_ir_merger import canonical_json_bytes, validate_inventory_fragment
except ImportError:  # pragma: no cover - supports direct script execution
    from _batch_ir_merger import canonical_json_bytes, validate_inventory_fragment


SCANNER_VERSION = 1
SCHEMA_VERSION = 2
_SOURCE_MARKER_RE = re.compile(r"^@@ source: (?P<source>.+)$")
_SOURCE_LINE_RE = re.compile(r"^(?P<line>[0-9]+) \| ?(?P<text>.*)$")
_BEGIN_RE = re.compile(r"\\begin\{(?P<name>[A-Za-z@][A-Za-z0-9@*:-]*)\}")
_END_RE = re.compile(r"\\end\{(?P<name>[A-Za-z@][A-Za-z0-9@*:-]*)\}")
_HEADING_RE = re.compile(r"^\s*(?:#+\s+|\\(?:part|chapter|section|subsection|subsubsection)\*?(?:\[[^]]*\])?\{)")
_DISPLAY_MATH_RE = re.compile(r"^\s*(?:\$\$|\\\[)")


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    Intent
    ------
    Supply the default clock for durable iterator timestamps.

    Rationale
    ---------
    Setup and acknowledgement records need one unambiguous UTC representation
    so retries and independently created state remain comparable.

    Pseudocode
    ----------
    - return the current UTC datetime

    Wraps
    -----
    - none

    """
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    """Encode one aware datetime as a canonical UTC string.

    Intent
    ------
    Reject naive clocks and serialize aware instants with a stable suffix.

    Rationale
    ---------
    Durable records must not silently mix local and UTC times because their
    ordering and retry diagnostics depend on a common time basis.

    Pseudocode
    ----------
    - set timestamp_validation = aware UTC requirement
    - return its normalized UTC ISO text

    Wraps
    -----
    - none

    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("iterator UTC clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_rows(packet_text: str) -> list[dict]:
    """Parse numbered source rows in their packet order.

    Intent
    ------
    Convert source markers and numbered packet lines into coordinate-bearing rows.

    Rationale
    ---------
    Unit construction needs preserved source identities and line coordinates so
    workers can receive contiguous author-visible material without reparsing.

    Pseudocode
    ----------
    - set source_marker = current source identity
    - set parsed_rows = numbered packet records
    - set source_validation = nonempty row requirement

    Wraps
    -----
    - none

    """

    source: str | None = None
    rows: list[dict] = []
    for raw in packet_text.splitlines():
        marker = _SOURCE_MARKER_RE.match(raw)
        if marker:
            source = marker.group("source")
            continue
        numbered = _SOURCE_LINE_RE.match(raw)
        if numbered is None:
            continue
        if source is None:
            raise ValueError("source packet has a numbered line before its source marker")
        rows.append(
            {
                "packet_index": len(rows) + 1,
                "source": source,
                "line": int(numbered.group("line")),
                "text": numbered.group("text"),
            }
        )
    if not rows:
        raise ValueError("source packet contains no numbered source rows")
    return rows


def _is_heading(row: dict) -> bool:
    """Identify whether one source row begins a document heading.

    Intent
    ------
    Recognize Markdown and LaTex section markers that establish structural context.

    Rationale
    ---------
    Headings must be retained as context rather than assigned as ordinary work
    so worker units preserve the source document's visible structure.

    Pseudocode
    ----------
    - set heading_match = supported heading pattern
    - return whether a heading marker was found

    Wraps
    -----
    - none
    """
    return bool(_HEADING_RE.match(row["text"]))


def _coordinates(rows: list[dict]) -> list[dict]:
    """Extract stable source coordinates from a row sequence.

    Intent
    ------
    Produce the packet index, source, and line fields carried by each unit.

    Rationale
    ---------
    Downstream inventory work needs exact provenance while avoiding repeated
    copies of source text in each coordinate record.

    Pseudocode
    ----------
    - set coordinate_records = row coordinate projection
    - return the ordered coordinate list

    Wraps
    -----
    - none
    """
    return [
        {"packet_index": row["packet_index"], "source": row["source"], "line": row["line"]}
        for row in rows
    ]


def _text(rows: list[dict]) -> str:
    """Join source-row text into one unit payload.

    Intent
    ------
    Preserve line ordering while presenting an assigned unit as plain text.

    Rationale
    ---------
    Workers need a readable payload, and newline joining retains the original
    line boundaries that guide their inventory decisions.

    Pseudocode
    ----------
    - set unit_text = ordered row text
    - return the newline-joined payload

    Wraps
    -----
    - none
    """
    return "\n".join(row["text"] for row in rows)


def _character_count(rows: list[dict]) -> int:
    """Count characters across an ordered row sequence.

    Intent
    ------
    Measure candidate units without adding separator characters not in source rows.

    Rationale
    ---------
    Partitioning and oversize decisions require a stable workload measure that
    is independent of formatting introduced for display.

    Pseudocode
    ----------
    - set character_total = row text lengths
    - return the total

    Wraps
    -----
    - none
    """
    return sum(len(row["text"]) for row in rows)


def _environment_ranges(rows: list[dict]) -> dict[int, tuple[int, str]]:
    """Find complete LaTex environment ranges by literal nesting.

    Intent
    ------
    Map each matching begin row to its inclusive end row and environment name.

    Rationale
    ---------
    Unit boundaries cannot split a complete environment because its meaning and
    syntax depend on retaining both delimiters in the same assignment.

    Pseudocode
    ----------
    - set environment_stack = literal begin markers
    - set completed_ranges = compatible end markers
    - return completed begin-to-end ranges

    Wraps
    -----
    - none
    """

    stack: list[tuple[int, str]] = []
    result: dict[int, tuple[int, str]] = {}
    for index, row in enumerate(rows):
        for match in _BEGIN_RE.finditer(row["text"]):
            stack.append((index, match.group("name")))
        for match in _END_RE.finditer(row["text"]):
            name = match.group("name")
            for position in range(len(stack) - 1, -1, -1):
                start, candidate = stack[position]
                if candidate == name:
                    del stack[position:]
                    result[start] = (index, name)
                    break
    return result


def _display_math_range(rows: list[dict], start: int) -> int | None:
    """Find the inclusive end of one display-math block.

    Intent
    ------
    Recognize bracketed and double-dollar display math beginning at one row.

    Rationale
    ---------
    Display equations are structural blocks and must not be divided by the
    iterator merely because their text crosses a preferred window size.

    Pseudocode
    ----------
    - set opening_delimiter = supported display math marker
    - set closing_position = matching closing delimiter

    Wraps
    -----
    - none
    """

    text = rows[start]["text"].strip()
    if text.startswith("\\["):
        if text.endswith("\\]") and len(text) > 2:
            return start
        closing = "\\]"
    elif text.startswith("$$"):
        if text.endswith("$$") and len(text) > 2:
            return start
        closing = "$$"
    else:
        return None
    for end in range(start + 1, len(rows)):
        if rows[end]["text"].strip().endswith(closing):
            return end
    return None


def _parts_for_oversize(rows: list[dict], window_chars: int) -> list[list[dict]]:
    """Split oversized rows only at safe structural boundaries.

    Intent
    ------
    Form the largest feasible row parts without breaking paragraphs or complete blocks.

    Rationale
    ---------
    Large author-visible regions still need bounded work units, but arbitrary
    splits would separate syntax and meaning that must be reviewed together.

    Pseudocode
    ----------
    - set safe_boundaries = paragraphs and completed blocks
    - set selected_boundary = largest feasible window boundary
    - return contiguous nonempty parts

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._character_count:
      why:
        computes: "Measures each candidate row range so safe boundaries remain within the requested character window."
    ._environment_ranges:
      why:
        computes: "Finds completed LaTex environments that contribute structural-safe split boundaries."

    InstantiationsFromRepo
    ----------------------
    ._display_math_range:
      why:
        constructs: "Builds each complete display-math range carried into the safe-boundary selection."
    """

    boundaries = {len(rows)}
    for index, row in enumerate(rows, start=1):
        if not row["text"].strip():
            boundaries.add(index)
    for start, (end, _name) in _environment_ranges(rows).items():
        if start > 0 and end < len(rows) - 1:
            boundaries.add(end + 1)
    for index, row in enumerate(rows):
        display_end = _display_math_range(rows, index)
        if display_end is not None and display_end < len(rows) - 1:
            boundaries.add(display_end + 1)

    parts: list[list[dict]] = []
    start = 0
    while start < len(rows):
        candidates = sorted(boundary for boundary in boundaries if boundary > start)
        feasible = [
            boundary
            for boundary in candidates
            if _character_count(rows[start:boundary]) <= window_chars
        ]
        if feasible:
            end = feasible[-1]
        else:
            end = candidates[0]
        parts.append(rows[start:end])
        start = end
    return parts


def _unit_record(
    rows: list[dict],
    *,
    environment: str | None,
    owner: str | None,
    part: int,
    oversize: bool,
    heading: str | None,
) -> dict:
    """Build one serializable inventory unit record.

    Intent
    ------
    Combine a contiguous row payload with provenance and structural metadata.

    Rationale
    ---------
    Assignment, persistence, and worker rendering need one stable record shape
    so they can share unit data without rederiving source information.

    Pseudocode
    ----------
    - set unit_payload = row-derived content and coordinates
    - set unit_metadata = supplied structural fields
    - return the complete unit record

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._character_count:
      why:
        constructs: "Builds the workload measure stored in each serializable unit record."
    ._coordinates:
      why:
        constructs: "Builds the source-coordinate records stored with the unit payload."
    ._text:
      why:
        constructs: "Builds the ordered textual payload stored with the unit record."
    """
    return {
        "text": _text(rows),
        "coordinates": _coordinates(rows),
        "character_count": _character_count(rows),
        "environment": environment,
        "owner": owner,
        "part": part,
        "oversize": oversize,
        "heading": heading,
    }


def _scan_units(packet_text: str, window_chars: int) -> tuple[list[dict], list[dict]]:
    """Scan source packet text into ordered units and structural context.

    Intent
    ------
    Preserve exact source coverage while assigning safe document regions to units.

    Rationale
    ---------
    Iterator setup needs deterministic, contiguous work units that retain
    environments, display math, and headings as source structure demands.

    Pseudocode
    ----------
    - set source_rows = parsed packet rows
    - set scanned_units = structural-safe unit records
    - set unit_ordinals = final source ordering

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._character_count:
      why:
        computes: "Measures candidate blocks while the scanner decides whether a structural range is oversized."
    ._is_heading:
      why:
        computes: "Recognizes heading rows so the scanner carries document structure separately from assignable text."
    ._parts_for_oversize:
      why:
        computes: "Splits oversized structural blocks only at the scanner's safe source boundaries."

    InstantiationsFromRepo
    ----------------------
    ._display_math_range:
      why:
        constructs: "Builds each display-math range evaluated as a standalone structural work block."
    ._environment_ranges:
      why:
        constructs: "Builds the completed environment map used to select whole LaTex blocks."
    ._parts_for_oversize:
      why:
        constructs: "Builds the contiguous parts emitted for each oversized structural block."
    ._source_rows:
      why:
        constructs: "Builds the coordinate-bearing source rows scanned into iterator units."
    ._unit_record:
      why:
        constructs: "Builds each public unit record emitted in source order."
    """
    if window_chars < 1:
        raise ValueError("window_chars must be positive")
    rows = _source_rows(packet_text)
    environments = _environment_ranges(rows)
    units: list[dict] = []
    structural_context: list[dict] = []
    heading: str | None = None
    index = 0
    outside: list[dict] = []

    def flush_outside() -> None:
        """Emit buffered non-structural rows as bounded paragraph groups.

        Intent
        ------
        Convert outside rows into contiguous units before a structure boundary.

        Rationale
        ---------
        Buffering permits paragraph-aware grouping while ensuring heading and
        environment transitions cannot accidentally merge unrelated material.

        Pseudocode
        ----------
        - set paragraph_groups = blank-line-separated rows
        - set emitted_units = character-bounded groups
        - set outside_buffer = empty

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._character_count:
          why:
            computes: "Measures buffered paragraph groups while this local flusher chooses bounded unit breaks."

        InstantiationsFromRepo
        ----------------------
        ._unit_record:
          why:
            constructs: "Builds each non-structural unit emitted from the buffered paragraph groups."
        """
        nonlocal outside
        if not outside:
            return
        paragraphs: list[list[dict]] = []
        paragraph: list[dict] = []
        for row in outside:
            paragraph.append(row)
            if not row["text"].strip():
                paragraphs.append(paragraph)
                paragraph = []
        if paragraph:
            paragraphs.append(paragraph)
        active: list[dict] = []
        for candidate in paragraphs:
            if active and _character_count(active) + _character_count(candidate) > window_chars:
                units.append(
                    _unit_record(
                        active,
                        environment=None,
                        owner=None,
                        part=1,
                        oversize=False,
                        heading=heading,
                    )
                )
                active = []
            active.extend(candidate)
            if _character_count(active) > window_chars and len(active) == len(candidate):
                units.append(
                    _unit_record(
                        active,
                        environment=None,
                        owner=None,
                        part=1,
                        oversize=True,
                        heading=heading,
                    )
                )
                active = []
        if active:
            units.append(
                _unit_record(
                    active,
                    environment=None,
                    owner=None,
                    part=1,
                    oversize=False,
                    heading=heading,
                )
            )
        outside = []

    while index < len(rows):
        row = rows[index]
        if outside and outside[-1]["source"] != row["source"]:
            flush_outside()
        if _is_heading(row):
            flush_outside()
            heading = row["text"]
            structural_context.append(
                {
                    "packet_index": row["packet_index"],
                    "source": row["source"],
                    "line": row["line"],
                    "text": row["text"],
                }
            )
            index += 1
            continue
        environment = environments.get(index)
        if environment is not None:
            flush_outside()
            end_index, name = environment
            block = rows[index : end_index + 1]
            owner = f"{row['source']}:{row['line']}"
            if _character_count(block) <= window_chars:
                parts = [block]
            else:
                parts = _parts_for_oversize(block, window_chars)
            for part, portion in enumerate(parts, start=1):
                units.append(
                    _unit_record(
                        portion,
                        environment=name,
                        owner=owner,
                        part=part,
                        oversize=_character_count(portion) > window_chars,
                        heading=heading,
                    )
                )
            index = end_index + 1
            continue
        display_end = _display_math_range(rows, index)
        if display_end is not None:
            flush_outside()
            owner = f"{row['source']}:{row['line']}"
            block = rows[index : display_end + 1]
            parts = (
                [block]
                if _character_count(block) <= window_chars
                else _parts_for_oversize(block, window_chars)
            )
            for part, portion in enumerate(parts, start=1):
                units.append(
                    _unit_record(
                        portion,
                        environment="markdown-math",
                        owner=(f"{row['source']}:{row['line'] - 1}" if heading else owner),
                        part=part,
                        oversize=_character_count(portion) > window_chars,
                        heading=heading,
                    )
                )
            index = display_end + 1
            continue
        outside.append(row)
        index += 1
    flush_outside()
    for ordinal, unit in enumerate(units, start=1):
        unit["id"] = f"u{ordinal:06d}"
        unit["ordinal"] = ordinal
    return units, structural_context


def _partition_units(units: list[dict], requested_workers: int) -> list[list[dict]]:
    """Partition ordered units into balanced contiguous worker assignments.

    Intent
    ------
    Divide all units among at most the requested number of nonempty workers.

    Rationale
    ---------
    Workers need deterministic contiguous ownership while character-weighted
    balancing keeps their initial source workload reasonably comparable.

    Pseudocode
    ----------
    - set worker_count = bounded requested workers
    - set contiguous_partitions = target-weight prefixes
    - set partition_validation = exact nonempty coverage

    Wraps
    -----
    - none

    """
    if requested_workers < 1:
        raise ValueError("requested_workers must be positive")
    if not units:
        raise ValueError("source packet has no assignable source units")
    worker_count = min(requested_workers, len(units))
    target = sum(unit["character_count"] for unit in units) / worker_count
    partitions: list[list[dict]] = []
    cursor = 0
    remaining_units = len(units)
    for worker in range(1, worker_count + 1):
        remaining_workers = worker_count - worker
        start = cursor
        used = 0
        while cursor < len(units) - remaining_workers:
            candidate = units[cursor]["character_count"]
            if cursor > start and abs((used + candidate) - target) > abs(used - target):
                break
            used += candidate
            cursor += 1
        partitions.append(units[start:cursor])
        remaining_units -= len(partitions[-1])
    if cursor != len(units) or any(not partition for partition in partitions):
        raise ValueError("unable to produce nonempty contiguous unit assignments")
    return partitions


def _write_json(path: Path, payload: dict) -> None:
    """Write one JSON payload with stable readable formatting.

    Intent
    ------
    Materialize a durable state artifact and create its parent directory.

    Rationale
    ---------
    Setup creates several independently inspected artifacts, so each needs a
    consistent UTF-8 representation at its declared durable location.

    Pseudocode
    ----------
    - set destination_parent = created path parent
    - set serialized_payload = indented UTF-8 JSON
    - set written_payload = serialized payload at destination

    Wraps
    -----
    - none

    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the iterator's durable SQLite tables.

    Intent
    ------
    Install metadata, units, assignments, leases, acknowledgements, and sequence tables.

    Rationale
    ---------
    Atomic leasing requires durable coordination state that survives worker
    retries and records the provenance of each acknowledgement transition.

    Pseudocode
    ----------
    - set schema_installation = complete iterator table definition
    - return after SQLite has created its tables

    Wraps
    -----
    - none
    """
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE units (
            id TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL UNIQUE,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE assignments (
            worker_index INTEGER PRIMARY KEY,
            first_unit_id TEXT NOT NULL,
            last_unit_id TEXT NOT NULL,
            next_ordinal INTEGER NOT NULL,
            complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE leases (
            worker_index INTEGER PRIMARY KEY,
            unit_id TEXT NOT NULL UNIQUE,
            leased_at TEXT NOT NULL,
            before_sha256 TEXT,
            before_counts_json TEXT
        );
        CREATE TABLE acknowledgements (
            id INTEGER PRIMARY KEY,
            worker_index INTEGER NOT NULL,
            unit_id TEXT NOT NULL,
            wrapped INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            semantic_counts_json TEXT NOT NULL,
            acknowledged_at TEXT NOT NULL,
            response_json TEXT NOT NULL,
            UNIQUE(worker_index, unit_id)
        );
        CREATE TABLE open_attention_sequences (
            worker_index INTEGER PRIMARY KEY,
            first_unit_id TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            before_sha256 TEXT,
            before_counts_json TEXT
        );
        CREATE TABLE attention_sequences (
            id INTEGER PRIMARY KEY,
            worker_index INTEGER NOT NULL,
            first_unit_id TEXT NOT NULL,
            last_unit_id TEXT NOT NULL,
            unit_count INTEGER NOT NULL,
            character_count INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            before_sha256 TEXT,
            before_counts_json TEXT,
            after_sha256 TEXT NOT NULL,
            after_counts_json TEXT NOT NULL,
            closure_reason TEXT NOT NULL
        );
        """
    )


def _validate_state(summary: dict, state_dir: Path) -> None:
    """Validate exact source coverage and required setup artifacts.

    Intent
    ------
    Reject published iterator state that omits, overlaps, or fails to materialize data.

    Rationale
    ---------
    A durable iterator is trustworthy only when every source coordinate and
    assignment is exact and each worker artifact exists before publication.

    Pseudocode
    ----------
    - set coordinate_validation = exact source coverage
    - set assignment_validation = contiguous unit coverage
    - set artifact_validation = required durable files

    Wraps
    -----
    - none
    """
    unit_ids = [unit["id"] for unit in summary["units"]]
    covered = [
        coordinate["packet_index"]
        for unit in summary["units"]
        for coordinate in unit["coordinates"]
    ] + [coordinate["packet_index"] for coordinate in summary["structural_context"]]
    if len(covered) != len(set(covered)) or sorted(covered) != list(range(1, len(covered) + 1)):
        raise ValueError("iterator setup does not cover source coordinates exactly once")
    assigned = [
        unit_id
        for assignment in summary["assignments"]
        for unit_id in unit_ids[
            unit_ids.index(assignment["first_unit_id"]) : unit_ids.index(assignment["last_unit_id"]) + 1
        ]
    ]
    if assigned != unit_ids:
        raise ValueError("iterator assignments are not contiguous exact coverage")
    expected = {
        state_dir / "iterator.sqlite3",
    }
    for assignment in summary["assignments"]:
        expected.update(
            (
                state_dir / "workers" / f"worker-{assignment['worker_index']}" / "inventory.json",
                state_dir / "workers" / f"worker-{assignment['worker_index']}" / "progress.md",
                state_dir / "controller" / f"worker-{assignment['worker_index']}-packet.json",
            )
        )
    if not all(path.is_file() for path in expected):
        raise ValueError("iterator setup did not materialize all durable artifacts")


def _read_manifest(state_dir: Path) -> dict:
    """Read and validate the published iterator assignments manifest.

    Intent
    ------
    Load setup metadata from its durable state directory without reconstructing it.

    Rationale
    ---------
    Idempotent setup and summary loading need one authoritative manifest with a
    clear failure when the requested iterator state is absent or malformed.

    Pseudocode
    ----------
    - set manifest_text = assignments JSON from the state directory
    - set manifest_mapping = decoded mapping payload
    - return the manifest mapping

    Wraps
    -----
    - none
    """
    path = state_dir / "inventory-assignments.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"iterator state is missing its assignments manifest: {state_dir}") from error
    if not isinstance(payload, dict):
        raise ValueError("iterator assignments manifest must be an object")
    return payload


def load_iterator_summary(state_dir: Path) -> dict:
    """Load persisted setup metadata and units without reparsing source text.

    Intent
    ------
    Reconstruct the public iterator summary from its manifest and SQLite state.

    Rationale
    ---------
    Reuse and acknowledgement paths need the original units and setup metadata
    while avoiding non-deterministic rescanning of the source packet.

    Pseudocode
    ----------
    - set resolved_state_dir = durable iterator directory
    - set stored_units = ordered SQLite rows
    - set iterator_summary = manifest plus reconstructed units

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._read_manifest:
      why:
        constructs: "Builds the persisted setup metadata merged with reconstructed unit records."
    """

    state_dir = state_dir.resolve()
    manifest = _read_manifest(state_dir)
    database = state_dir / "iterator.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id, ordinal, text, metadata_json FROM units ORDER BY ordinal"
        ).fetchall()
    units = []
    for unit_id, ordinal, text, metadata_json in rows:
        metadata = json.loads(metadata_json)
        metadata.update({"id": unit_id, "ordinal": ordinal, "text": text})
        units.append(metadata)
    return {**manifest, "units": units}


def setup_inventory_iterator(
    source_packet_path: Path,
    state_dir: Path,
    *,
    requested_workers: int,
    window_chars: int,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    utc_now: Callable[[], datetime] = _utc_now,
) -> dict:
    """Scan, validate, and atomically publish reusable iterator state.

    Intent
    ------
    Create idempotent worker assignments and durable unit records for one source packet.

    Rationale
    ---------
    Parallel inventory needs a single validated publication boundary so workers
    share exact units and retries cannot expose partial setup artifacts.

    Pseudocode
    ----------
    - set setup_validation = input and idempotency checks
    - set temporary_summary = scanned and partitioned source units
    - set publication_result = validated atomic state replacement

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._create_schema:
      why:
        computes: "Installs the durable SQLite tables before setup writes iterator coordination records."
    ._validate_state:
      why:
        computes: "Checks exact coverage and artifact existence before temporary setup state is published."
    ._write_json:
      why:
        computes: "Materializes worker and manifest JSON artifacts in the temporary durable state directory."

    InstantiationsFromRepo
    ----------------------
    ._partition_units:
      why:
        constructs: "Builds contiguous worker partitions from the scanned source units."
    ._read_manifest:
      why:
        constructs: "Builds the existing manifest used to verify idempotent setup configuration."
    ._scan_units:
      why:
        constructs: "Builds ordered source units and structural context for durable iterator state."
    ._timestamp:
      why:
        constructs: "Builds the canonical creation timestamp recorded in setup metadata."
    .load_iterator_summary:
      why:
        constructs: "Builds the public summary returned after existing or newly published setup state."
    """

    source_packet_path = source_packet_path.resolve()
    state_dir = state_dir.resolve()
    if not source_packet_path.is_file():
        raise FileNotFoundError(f"source packet not found: {source_packet_path}")
    if requested_workers < 1 or window_chars < 1:
        raise ValueError("requested_workers and window_chars must be positive")
    packet_text = source_packet_path.read_text(encoding="utf-8")
    source_sha256 = hashlib.sha256(packet_text.encode("utf-8")).hexdigest()
    configuration = {
        "source_sha256": source_sha256,
        "requested_workers": requested_workers,
        "window_chars": window_chars,
        "scanner_version": SCANNER_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    if state_dir.exists():
        existing = _read_manifest(state_dir)
        if existing.get("configuration") != configuration:
            raise ValueError("existing iterator state does not match requested setup")
        return load_iterator_summary(state_dir)

    total_start = clock_ns()
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{state_dir.name}.", suffix=".tmp", dir=state_dir.parent)
    )
    try:
        scan_start = clock_ns()
        units, structural_context = _scan_units(packet_text, window_chars)
        scan_ms = (clock_ns() - scan_start) // 1_000_000
        partition_start = clock_ns()
        partitions = _partition_units(units, requested_workers)
        partition_ms = (clock_ns() - partition_start) // 1_000_000
        assignments: list[dict] = []
        for worker_index, partition in enumerate(partitions, start=1):
            worker_dir = temporary / "workers" / f"worker-{worker_index}"
            inventory_path = worker_dir / "inventory.json"
            progress_path = worker_dir / "progress.md"
            controller_path = temporary / "controller" / f"worker-{worker_index}-packet.json"
            _write_json(
                inventory_path,
                {
                    "ir_version": 3,
                    "chunk_id": f"iterator-worker-{worker_index:03d}",
                    "files": list(
                        dict.fromkeys(
                            coordinate["source"]
                            for unit in partition
                            for coordinate in unit["coordinates"]
                        )
                    ),
                    "nodes": [],
                    "edges": [],
                    "gaps": [],
                },
            )
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text("# Inventory progress\n", encoding="utf-8")
            _write_json(
                controller_path,
                {
                    "worker_index": worker_index,
                    "unit_ids": [unit["id"] for unit in partition],
                    "source_packet_path": str(source_packet_path),
                    "source_sha256": source_sha256,
                },
            )
            assignments.append(
                {
                    "worker_index": worker_index,
                    "first_unit_id": partition[0]["id"],
                    "last_unit_id": partition[-1]["id"],
                    "unit_count": len(partition),
                    "character_count": sum(unit["character_count"] for unit in partition),
                    "source_identities": sorted(
                        {coordinate["source"] for unit in partition for coordinate in unit["coordinates"]}
                    ),
                    "inventory_path": str((state_dir / inventory_path.relative_to(temporary)).resolve()),
                    "progress_path": str((state_dir / progress_path.relative_to(temporary)).resolve()),
                    "controller_packet_path": str((state_dir / controller_path.relative_to(temporary)).resolve()),
                }
            )
        database_start = clock_ns()
        with sqlite3.connect(temporary / "iterator.sqlite3") as connection:
            _create_schema(connection)
            connection.executemany(
                "INSERT INTO units (id, ordinal, text, metadata_json) VALUES (?, ?, ?, ?)",
                [
                    (
                        unit["id"],
                        unit["ordinal"],
                        unit["text"],
                        json.dumps({key: value for key, value in unit.items() if key not in {"id", "ordinal", "text"}}),
                    )
                    for unit in units
                ],
            )
            connection.executemany(
                "INSERT INTO assignments (worker_index, first_unit_id, last_unit_id, next_ordinal) VALUES (?, ?, ?, ?)",
                [
                    (assignment["worker_index"], assignment["first_unit_id"], assignment["last_unit_id"], units.index(partition[0]) + 1)
                    for assignment, partition in zip(assignments, partitions, strict=True)
                ],
            )
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                [(key, json.dumps(value)) for key, value in configuration.items()],
            )
        database_ms = (clock_ns() - database_start) // 1_000_000
        summary = {
            "iterator_version": 1,
            "configuration": configuration,
            "requested_workers": requested_workers,
            "window_chars": window_chars,
            "source_packet_path": str(source_packet_path),
            "effective_workers": len(partitions),
            "assignments": assignments,
            "structural_context": structural_context,
            "timings_ms": {
                "scan": scan_ms,
                "unitization": scan_ms,
                "partition": partition_ms,
                "database": database_ms,
                "validation": 0,
                "total": 0,
            },
            "created_at": _timestamp(utc_now()),
            "units": units,
        }
        validate_start = clock_ns()
        _validate_state(summary, temporary)
        summary["timings_ms"]["validation"] = (clock_ns() - validate_start) // 1_000_000
        summary["timings_ms"]["total"] = (clock_ns() - total_start) // 1_000_000
        _write_json(
            temporary / "inventory-assignments.json",
            {key: value for key, value in summary.items() if key != "units"},
        )
        os.replace(temporary, state_dir)
        return load_iterator_summary(state_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _failure(code: str, message: str) -> dict:
    """Construct one structured iterator failure response.

    Intent
    ------
    Return machine-readable failure state without raising expected operation errors.

    Rationale
    ---------
    The next-unit facade must distinguish invalid worker input and durable-state
    failures using stable codes that callers can handle deterministically.

    Pseudocode
    ----------
    - set failure_payload = code and message error object
    - return the failure response mapping

    Wraps
    -----
    - none
    """
    return {"state": "failure", "error": {"code": code, "message": message}}


def _unit_for_ordinal(connection: sqlite3.Connection, ordinal: int) -> dict | None:
    """Load one persisted unit by its ordinal position.

    Intent
    ------
    Reconstruct the public unit record needed for a lease response.

    Rationale
    ---------
    Lease progression tracks ordinals in SQLite, while callers need the full
    text and metadata record identified by the selected ordinal.

    Pseudocode
    ----------
    - set unit_row = ordered SQLite record
    - return no record when it is absent
    - set reconstructed_unit = metadata identity and text

    Wraps
    -----
    - none
    """
    row = connection.execute(
        "SELECT id, ordinal, text, metadata_json FROM units WHERE ordinal = ?", (ordinal,)
    ).fetchone()
    if row is None:
        return None
    unit_id, unit_ordinal, text, metadata_json = row
    metadata = json.loads(metadata_json)
    return {**metadata, "id": unit_id, "ordinal": unit_ordinal, "text": text}


def _owned_sources(connection: sqlite3.Connection, first: int, last: int) -> list[str]:
    """List unique source identities owned by an ordinal assignment range.

    Intent
    ------
    Derive the ordered file ownership expected in one worker inventory fragment.

    Rationale
    ---------
    Before acknowledgement, the iterator must verify that a worker did not
    inventory files outside the source coordinates assigned to that worker.

    Pseudocode
    ----------
    - set assigned_metadata = ordinal-range rows
    - set source_identities = first-seen coordinate sources
    - return the ordered unique source identities

    Wraps
    -----
    - none
    """
    rows = connection.execute(
        "SELECT metadata_json FROM units WHERE ordinal BETWEEN ? AND ? ORDER BY ordinal",
        (first, last),
    ).fetchall()
    sources: list[str] = []
    for (metadata_json,) in rows:
        for coordinate in json.loads(metadata_json)["coordinates"]:
            source = coordinate["source"]
            if source not in sources:
                sources.append(source)
    return sources


def _inventory_snapshot(path: Path, worker_index: int, owned_sources: list[str]) -> dict:
    """Validate and fingerprint one worker inventory fragment.

    Intent
    ------
    Confirm inventory ownership and return canonical content and count evidence.

    Rationale
    ---------
    Acknowledgement is valid only for schema-conformant inventory content that
    belongs to the leasing worker and its assigned source identities.

    Pseudocode
    ----------
    - set inventory_payload = decoded worker JSON object
    - set inventory_validation = ownership file and schema checks
    - return canonical hash and semantic item counts

    Wraps
    -----
    - none
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ValueError("worker inventory is missing") from error
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("worker inventory is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("worker inventory must be a JSON object")
    expected_chunk_id = f"iterator-worker-{worker_index:03d}"
    if payload.get("chunk_id") != expected_chunk_id:
        raise ValueError("worker inventory chunk ownership does not match worker")
    if payload.get("files") != owned_sources:
        raise ValueError("worker inventory file ownership does not match assignment")
    try:
        validate_inventory_fragment(payload)
    except Exception as error:
        raise ValueError("worker inventory does not satisfy the inventory schema") from error
    counts = {
        "nodes": len(payload["nodes"]),
        "edges": len(payload["edges"]),
        "gaps": len(payload["gaps"]),
    }
    return {
        "sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "counts": counts,
        "payload": payload,
    }


def verify_completed_inventories(state_dir: Path) -> dict:
    """Authenticate completed worker fragments against their final acknowledgements.

    Intent
    ------
    Return current completed-worker inventory payloads only when they exactly
    match the canonical content and bounded counts acknowledged for the final
    unit in each worker assignment.

    Rationale
    ---------
    Completion alone does not authenticate a mutable inventory file.  Pooling
    must consume the same validated content recorded by the durable final
    acknowledgement, without duplicating iterator ownership logic or rereading
    the file after verification.

    Pseudocode
    ----------
    - set assignment_state = ordered durable worker assignments
    - set incomplete_workers = assignments not yet complete
    - set authenticated_fragments = current validated snapshots matching final acks
    - return worker indices, incomplete workers, and authenticated fragments

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._inventory_snapshot:
      why:
        computes: "Validates ownership and returns canonical hash, counts, and the exact payload to pool."

    InstantiationsFromRepo
    ----------------------
    ._owned_sources:
      why:
        constructs: "Builds the source ownership boundary used to validate each completed fragment."
    """

    state_dir = state_dir.resolve()
    database = state_dir / "iterator.sqlite3"
    if not database.is_file():
        raise ValueError("iterator completion state is unavailable")
    try:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT worker_index, first_unit_id, last_unit_id, complete "
                "FROM assignments ORDER BY worker_index"
            ).fetchall()
            if not rows or any(row[3] not in (0, 1) for row in rows):
                raise ValueError("iterator completion state is invalid")

            worker_indices: list[int] = []
            incomplete_workers: list[int] = []
            fragments: list[dict] = []
            for worker_index, first_unit_id, last_unit_id, complete in rows:
                worker_index = int(worker_index)
                worker_indices.append(worker_index)
                if not complete:
                    incomplete_workers.append(worker_index)
                    continue

                ordinal_rows = connection.execute(
                    "SELECT id, ordinal FROM units WHERE id IN (?, ?)",
                    (first_unit_id, last_unit_id),
                ).fetchall()
                ordinals = {unit_id: ordinal for unit_id, ordinal in ordinal_rows}
                if first_unit_id not in ordinals or last_unit_id not in ordinals:
                    raise ValueError("iterator assignment names an unknown durable unit")
                sources = _owned_sources(
                    connection,
                    int(ordinals[first_unit_id]),
                    int(ordinals[last_unit_id]),
                )
                inventory_path = (
                    state_dir / "workers" / f"worker-{worker_index}" / "inventory.json"
                )
                snapshot = _inventory_snapshot(inventory_path, worker_index, sources)
                acknowledgement = connection.execute(
                    "SELECT content_sha256, semantic_counts_json "
                    "FROM acknowledgements WHERE worker_index = ? AND unit_id = ?",
                    (worker_index, last_unit_id),
                ).fetchone()
                if acknowledgement is None:
                    raise ValueError(
                        f"worker inventory has no final acknowledgement: {worker_index}"
                    )
                try:
                    acknowledged_counts = json.loads(acknowledgement[1])
                except (TypeError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"worker final acknowledgement is invalid: {worker_index}"
                    ) from error
                if (
                    snapshot["sha256"] != acknowledgement[0]
                    or snapshot["counts"] != acknowledged_counts
                ):
                    raise ValueError(
                        "worker inventory does not match its final acknowledgement: "
                        f"{worker_index}"
                    )
                fragments.append(snapshot["payload"])
    except sqlite3.Error as error:
        raise ValueError("iterator completion state is unavailable") from error

    return {
        "worker_indices": worker_indices,
        "incomplete_workers": incomplete_workers,
        "fragments": fragments,
    }


def _lease_response(unit: dict) -> dict:
    """Construct the standard successful unit-lease response.

    Intent
    ------
    Present a loaded unit under the stable machine response state.

    Rationale
    ---------
    New leases and retry leases need the same compact public representation so
    clients do not depend on the internal SQLite transition path.

    Pseudocode
    ----------
    - set lease_payload = successful state and unit
    - return the response mapping

    Wraps
    -----
    - none
    """
    return {"state": "unit", "unit": unit}


def _sequence_totals(
    connection: sqlite3.Connection, first_unit_id: str, last_unit_id: str
) -> tuple[int, int]:
    """Compute unit and character totals for one attention sequence.

    Intent
    ------
    Measure the inclusive ordinal span between a sequence's first and last units.

    Rationale
    ---------
    Closed attention sequences need durable workload totals for review without
    retaining a redundant copy of every unit text in the sequence record.

    Pseudocode
    ----------
    - set sequence_bounds = first and last ordinals
    - set sequence_aggregate = inclusive count and text length
    - return the two sequence totals

    Wraps
    -----
    - none
    """
    row = connection.execute(
        "SELECT first.ordinal, last.ordinal "
        "FROM units AS first JOIN units AS last "
        "WHERE first.id = ? AND last.id = ?",
        (first_unit_id, last_unit_id),
    ).fetchone()
    if row is None:
        raise ValueError("attention sequence unit is missing")
    first_ordinal, last_ordinal = row
    count, characters = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(text)), 0) FROM units "
        "WHERE ordinal BETWEEN ? AND ?",
        (first_ordinal, last_ordinal),
    ).fetchone()
    return count, characters


def _open_or_close_sequence(
    connection: sqlite3.Connection,
    *,
    worker_index: int,
    unit_id: str,
    lease_before_sha256: str | None,
    lease_before_counts: str | None,
    snapshot: dict,
    closure_reason: str | None,
    now: str,
) -> None:
    """Open or close the current durable attention sequence.

    Intent
    ------
    Record a sequence start on acknowledgement and persist its totals at closure.

    Rationale
    ---------
    Worker wraps and source endings define attention boundaries that must remain
    recoverable across retries without creating duplicate sequence records.

    Pseudocode
    ----------
    - set open_sequence = worker sequence start when absent
    - set retained_sequence = open state without closure
    - set closed_sequence = persisted aggregate at closure

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._sequence_totals:
      why:
        constructs: "Builds the measured unit span persisted when an open attention sequence closes."
    """
    open_row = connection.execute(
        "SELECT first_unit_id, opened_at, before_sha256, before_counts_json "
        "FROM open_attention_sequences WHERE worker_index = ?",
        (worker_index,),
    ).fetchone()
    if open_row is None:
        connection.execute(
            "INSERT INTO open_attention_sequences "
            "(worker_index, first_unit_id, opened_at, before_sha256, before_counts_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (worker_index, unit_id, now, lease_before_sha256, lease_before_counts),
        )
        open_row = (unit_id, now, lease_before_sha256, lease_before_counts)
    if closure_reason is None:
        return
    first_unit_id, opened_at, before_sha256, before_counts = open_row
    unit_count, character_count = _sequence_totals(connection, first_unit_id, unit_id)
    connection.execute(
        "INSERT INTO attention_sequences "
        "(worker_index, first_unit_id, last_unit_id, unit_count, character_count, "
        "opened_at, closed_at, before_sha256, before_counts_json, after_sha256, "
        "after_counts_json, closure_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            worker_index,
            first_unit_id,
            unit_id,
            unit_count,
            character_count,
            opened_at,
            now,
            before_sha256,
            before_counts,
            snapshot["sha256"],
            json.dumps(snapshot["counts"], sort_keys=True),
            closure_reason,
        ),
    )
    connection.execute(
        "DELETE FROM open_attention_sequences WHERE worker_index = ?", (worker_index,)
    )


def _lease_before_snapshot(path: Path, worker_index: int, owned_sources: list[str]) -> tuple[str | None, str | None]:
    """Capture optional inventory evidence before issuing a lease.

    Intent
    ------
    Preserve a valid pre-lease hash and counts without blocking an empty inventory.

    Rationale
    ---------
    A worker may begin with no valid inventory fragment, yet later acknowledgement
    auditing still benefits from before-and-after evidence when it exists.

    Pseudocode
    ----------
    - set before_snapshot = validated worker inventory when available
    - set absent_evidence = empty result after invalid inventory
    - set before_evidence = snapshot hash and serialized counts

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._inventory_snapshot:
      why:
        constructs: "Builds validated pre-lease inventory evidence when a worker artifact is available."
    """
    try:
        snapshot = _inventory_snapshot(path, worker_index, owned_sources)
    except ValueError:
        return None, None
    return snapshot["sha256"], json.dumps(snapshot["counts"], sort_keys=True)


def next_inventory_unit(
    state_dir: Path,
    worker_index: int,
    *,
    ack: str | None = None,
    wrap: bool = False,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    utc_now: Callable[[], datetime] = _utc_now,
) -> dict:
    """Atomically lease, validate, acknowledge, and advance worker units.

    Intent
    ------
    Implement idempotent next-unit leasing and acknowledgement for one assigned worker.

    Rationale
    ---------
    Concurrent workers and retries require serialized state transitions that
    reject invalid inventory while preserving recoverable lease and sequence data.

    Pseudocode
    ----------
    - set operation_validation = worker and transaction checks
    - set lease_response = durable existing or issued unit
    - set acknowledgement_result = validated atomic cursor advance

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._open_or_close_sequence:
      why:
        computes: "Records the durable attention transition created by a valid acknowledgement."
    ._unit_for_ordinal:
      why:
        computes: "Loads the assigned ordinal as a lease response or validates cursor progression."

    InstantiationsFromRepo
    ----------------------
    ._failure:
      why:
        constructs: "Builds machine-readable failure responses for rejected worker operations and retries."
    ._inventory_snapshot:
      why:
        constructs: "Builds validated acknowledgement evidence from the worker inventory artifact."
    ._lease_before_snapshot:
      why:
        constructs: "Builds optional pre-lease evidence carried into the next durable lease row."
    ._lease_response:
      why:
        constructs: "Builds the successful unit response returned for existing and newly issued leases."
    ._owned_sources:
      why:
        constructs: "Builds the ordered source ownership checked against the worker inventory fragment."
    ._timestamp:
      why:
        constructs: "Builds the canonical acknowledgement timestamp stored in the transaction records."
    ._unit_for_ordinal:
      why:
        constructs: "Builds the full unit payload returned after a cursor lookup or acknowledgement advance."
    """

    del clock_ns
    state_dir = state_dir.resolve()
    if worker_index < 1:
        return _failure("invalid-worker", "worker index must be positive")
    if wrap and ack is None:
        return _failure("wrap-requires-ack", "wrap is valid only with an acknowledgement")
    database = state_dir / "iterator.sqlite3"
    if not database.is_file():
        return _failure("missing-state", "iterator database is missing")
    now = _timestamp(utc_now())
    try:
        with sqlite3.connect(database, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            assignment = connection.execute(
                "SELECT first_unit_id, last_unit_id, next_ordinal, complete "
                "FROM assignments WHERE worker_index = ?",
                (worker_index,),
            ).fetchone()
            if assignment is None:
                connection.rollback()
                return _failure("unknown-worker", "worker index is not assigned")
            first_unit_id, last_unit_id, next_ordinal, complete = assignment
            first_ordinal = connection.execute(
                "SELECT ordinal FROM units WHERE id = ?", (first_unit_id,)
            ).fetchone()[0]
            last_ordinal = connection.execute(
                "SELECT ordinal FROM units WHERE id = ?", (last_unit_id,)
            ).fetchone()[0]
            sources = _owned_sources(connection, first_ordinal, last_ordinal)
            inventory_path = state_dir / "workers" / f"worker-{worker_index}" / "inventory.json"
            lease = connection.execute(
                "SELECT unit_id, before_sha256, before_counts_json FROM leases WHERE worker_index = ?",
                (worker_index,),
            ).fetchone()

            if ack is None:
                if lease is not None:
                    unit = connection.execute(
                        "SELECT ordinal FROM units WHERE id = ?", (lease[0],)
                    ).fetchone()
                    response = _lease_response(_unit_for_ordinal(connection, unit[0]))
                elif complete:
                    response = {"state": "complete"}
                else:
                    unit = _unit_for_ordinal(connection, next_ordinal)
                    if unit is None or next_ordinal > last_ordinal:
                        connection.rollback()
                        return _failure("invalid-cursor", "worker cursor is outside its assignment")
                    before_sha256, before_counts = _lease_before_snapshot(
                        inventory_path, worker_index, sources
                    )
                    connection.execute(
                        "INSERT INTO leases "
                        "(worker_index, unit_id, leased_at, before_sha256, before_counts_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (worker_index, unit["id"], now, before_sha256, before_counts),
                    )
                    response = _lease_response(unit)
                connection.commit()
                return response

            latest_ack = connection.execute(
                "SELECT unit_id, wrapped, response_json FROM acknowledgements "
                "WHERE worker_index = ? ORDER BY id DESC LIMIT 1",
                (worker_index,),
            ).fetchone()
            if latest_ack is not None and latest_ack[0] == ack:
                if bool(latest_ack[1]) != wrap:
                    connection.rollback()
                    return _failure("conflicting-retry", "retry wrap intent differs from acknowledgement")
                connection.commit()
                return json.loads(latest_ack[2])
            if lease is None or lease[0] != ack:
                connection.rollback()
                return _failure("unexpected-ack", "acknowledgement does not match the outstanding lease")

            try:
                snapshot = _inventory_snapshot(inventory_path, worker_index, sources)
            except ValueError as error:
                connection.rollback()
                return _failure("invalid-inventory", str(error))

            unit_ordinal = connection.execute(
                "SELECT ordinal FROM units WHERE id = ?", (ack,)
            ).fetchone()[0]
            final = unit_ordinal == last_ordinal
            _open_or_close_sequence(
                connection,
                worker_index=worker_index,
                unit_id=ack,
                lease_before_sha256=lease[1],
                lease_before_counts=lease[2],
                snapshot=snapshot,
                closure_reason=(
                    "end-of-source" if final else ("worker-wrap" if wrap else None)
                ),
                now=now,
            )

            next_ordinal = unit_ordinal + 1
            connection.execute("DELETE FROM leases WHERE worker_index = ?", (worker_index,))
            if final:
                response = {"state": "complete"}
                connection.execute(
                    "UPDATE assignments SET next_ordinal = ?, complete = 1 WHERE worker_index = ?",
                    (next_ordinal, worker_index),
                )
            else:
                unit = _unit_for_ordinal(connection, next_ordinal)
                before_sha256, before_counts = _lease_before_snapshot(
                    inventory_path, worker_index, sources
                )
                connection.execute(
                    "INSERT INTO leases "
                    "(worker_index, unit_id, leased_at, before_sha256, before_counts_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (worker_index, unit["id"], now, before_sha256, before_counts),
                )
                connection.execute(
                    "UPDATE assignments SET next_ordinal = ? WHERE worker_index = ?",
                    (next_ordinal, worker_index),
                )
                response = _lease_response(unit)
            connection.execute(
                "INSERT INTO acknowledgements "
                "(worker_index, unit_id, wrapped, content_sha256, semantic_counts_json, "
                "acknowledged_at, response_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    worker_index,
                    ack,
                    int(wrap),
                    snapshot["sha256"],
                    json.dumps(snapshot["counts"], sort_keys=True),
                    now,
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
            return response
    except sqlite3.Error as error:
        return _failure("database-error", str(error))


def _positive_integer(value: str) -> int:
    """Parse one strictly positive command-line integer.

    Intent
    ------
    Convert numeric interface arguments while rejecting zero and negative values.

    Rationale
    ---------
    Worker indexes and setup sizes are positive by contract, and argparse should
    report invalid values before the runtime changes durable iterator state.

    Pseudocode
    ----------
    - set parsed_integer = numeric command-line text
    - set positive_validation = values above zero
    - return the positive result

    Wraps
    -----
    - none

    """

    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: Iterable[str] | None = None) -> int:
    """Run one atomic iterator operation, print JSON, and return success.

    Intent
    ------
    Dispatch setup and next CLI arguments to the matching durable runtime operation.

    Rationale
    ---------
    Behavioral-source interfaces need narrow process-facing calls while setup
    and acknowledgement retain their separate validation and state transitions.

    Pseudocode
    ----------
    - set parsed_operation = setup or next arguments
    - set wrap_validation = acknowledgement requirement
    - set printed_response = selected runtime JSON and success status

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .next_inventory_unit:
      why:
        constructs: "Builds the serialized next-unit response selected by the parsed command operation."
    .setup_inventory_iterator:
      why:
        constructs: "Builds the serialized setup response selected by the parsed command operation."
    """

    parser = argparse.ArgumentParser(description="Set up or advance inventory source units.")
    operations = parser.add_subparsers(dest="operation", required=True)
    setup = operations.add_parser("setup")
    setup.add_argument("source_packet")
    setup.add_argument("state_dir")
    setup.add_argument("--workers", required=True, type=_positive_integer)
    setup.add_argument("--window-chars", required=True, type=_positive_integer)
    next_unit = operations.add_parser("next")
    next_unit.add_argument("state_dir")
    next_unit.add_argument("worker_index", type=_positive_integer)
    next_unit.add_argument("--ack")
    next_unit.add_argument("--wrap", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.operation == "setup":
        summary = setup_inventory_iterator(
            Path(args.source_packet),
            Path(args.state_dir),
            requested_workers=args.workers,
            window_chars=args.window_chars,
        )
        response = {
            "state": "setup",
            "state_dir": str(Path(args.state_dir).resolve()),
            "effective_workers": summary["effective_workers"],
            "units": len(summary["units"]),
        }
    else:
        if args.wrap and args.ack is None:
            parser.error("next --wrap requires --ack")
        response = next_inventory_unit(
            Path(args.state_dir), args.worker_index, ack=args.ack, wrap=args.wrap
        )
    print(json.dumps(response, indent=2))
    return 0


class Interface(PythonArgvMachineInterface):
    """Expose setup and next iterator operations through the machine protocol.

    Intent
    ------
    Provide the runtime adapter required by behavioral-source process invocation.

    Rationale
    ---------
    The process runner expects a small argv interface class, keeping blueprint
    interface definitions independent from iterator state-management details.

    Pseudocode
    ----------
    - set machine_arguments = received process argv
    - set interface_result = delegated CLI operation
    - return a successful process status

    Wraps
    -----
    - none
    """

    prog = "inventory_unit_iterator.py"

    def run(self, argv: list[str]) -> int:
        """Run one machine-protocol iterator invocation.

        Intent
        ------
        Delegate received process arguments to the shared iterator CLI dispatcher.

        Rationale
        ---------
        The machine runner requires this method boundary so source interfaces
        can invoke setup and next operations through a common executable entry.

        Pseudocode
        ----------
        - set interface_arguments = received argv sequence
        - return the successful process status code

        Wraps
        -----
        - main -> preprocess: passes received machine argv unchanged; postprocess: returns the successful process status; fixed_arguments: none

        """
        return main(argv)


if __name__ == "__main__":
    main()
