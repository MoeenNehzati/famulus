#!/usr/bin/env python3
"""Create durable, source-ordered inventory units without semantic decisions."""

from __future__ import annotations

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
from typing import Callable


SCANNER_VERSION = 1
SCHEMA_VERSION = 1
_SOURCE_MARKER_RE = re.compile(r"^@@ source: (?P<source>.+)$")
_SOURCE_LINE_RE = re.compile(r"^(?P<line>[0-9]+) \| ?(?P<text>.*)$")
_BEGIN_RE = re.compile(r"\\begin\{(?P<name>[A-Za-z@][A-Za-z0-9@*:-]*)\}")
_END_RE = re.compile(r"\\end\{(?P<name>[A-Za-z@][A-Za-z0-9@*:-]*)\}")
_HEADING_RE = re.compile(r"^\s*(?:#+\s+|\\(?:part|chapter|section|subsection|subsubsection)\*?(?:\[[^]]*\])?\{)")
_DISPLAY_MATH_RE = re.compile(r"^\s*(?:\$\$|\\\[)")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("iterator UTC clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_rows(packet_text: str) -> list[dict]:
    """Parse only numbered source rows in their already-expanded packet order."""

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
    return bool(_HEADING_RE.match(row["text"]))


def _coordinates(rows: list[dict]) -> list[dict]:
    return [
        {"packet_index": row["packet_index"], "source": row["source"], "line": row["line"]}
        for row in rows
    ]


def _text(rows: list[dict]) -> str:
    return "\n".join(row["text"] for row in rows)


def _character_count(rows: list[dict]) -> int:
    return sum(len(row["text"]) for row in rows)


def _environment_ranges(rows: list[dict]) -> dict[int, tuple[int, str]]:
    """Return matching begin/end indices, nesting conservatively by literal name."""

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
    """Return the inclusive end of one complete display-math block."""

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
    """Split only after paragraphs, complete nested environments, or display math."""

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _create_schema(connection: sqlite3.Connection) -> None:
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
        """
    )


def _validate_state(summary: dict, state_dir: Path) -> None:
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
    path = state_dir / "inventory-assignments.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"iterator state is missing its assignments manifest: {state_dir}") from error
    if not isinstance(payload, dict):
        raise ValueError("iterator assignments manifest must be an object")
    return payload


def load_iterator_summary(state_dir: Path) -> dict:
    """Load setup metadata and units without reparsing the source packet."""

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
    """Scan, validate, and atomically publish exactly one reusable iterator state."""

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
                    "ir_version": 2,
                    "chunk_id": f"iterator-worker-{worker_index:03d}",
                    "files": [],
                    "evidence": [],
                    "references": [],
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


def next_inventory_unit(
    state_dir: Path,
    worker_index: int,
    *,
    ack: str | None = None,
    wrap: bool = False,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    utc_now: Callable[[], datetime] = _utc_now,
) -> dict:
    """Reserved interface for the later transactional lease/acknowledgement unit."""

    del state_dir, worker_index, ack, wrap, clock_ns, utc_now
    raise NotImplementedError("inventory unit acknowledgement is not implemented yet")
