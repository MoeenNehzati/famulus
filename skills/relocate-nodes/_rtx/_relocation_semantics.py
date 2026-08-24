"""Discover bounded retired-address occurrences in a projected relocation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Literal

from ._relocation_engine import ChangeSet, DerivedIdentityMap, RelocationError, Rename


MappingKind = Literal[
    "logical_fragment", "physical_path", "physical_fragment", "python_module"
]


_MAPPING_PRIORITY: dict[MappingKind, int] = {
    "physical_path": 0,
    "python_module": 1,
    "logical_fragment": 2,
    "physical_fragment": 3,
}


@dataclass(frozen=True)
class SemanticMapping:
    """Describe one bounded old/new semantic candidate."""

    mapping_kind: MappingKind
    mapping_id: str
    relocation_id: str
    old: str
    new: str


@dataclass(frozen=True)
class SemanticOccurrence:
    """Describe one source-side occurrence with a complete stable selector."""

    occurrence_id: str
    mapping_kind: MappingKind
    mapping_id: str
    relocation_id: str
    path: str
    projected_digest: str
    byte_start: int
    byte_end: int
    line: int
    column: int
    ordinal: int
    match: str
    candidate: str
    context: str
    generated: bool = False
    authored_source: str | None = None

    def to_report(self) -> dict[str, object]:
        result: dict[str, object] = {
            "occurrence_id": self.occurrence_id,
            "mapping_kind": self.mapping_kind,
            "mapping_id": self.mapping_id,
            "relocation_id": self.relocation_id,
            "path": self.path,
            "original_digest": self.projected_digest,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "line": self.line,
            "column": self.column,
            "ordinal": self.ordinal,
            "match": self.match,
            "candidate": self.candidate,
            "context": self.context,
            "generated": self.generated,
        }
        if self.authored_source is not None:
            result["authored_source"] = self.authored_source
        return result


@dataclass(frozen=True)
class SkippedTextFile:
    """Describe one projected inventory entry that was not scanned as text."""

    path: str
    reason: str

    def to_report(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True)
class SemanticScanResult:
    """Return raw occurrences and explicit skipped entries."""

    occurrences: tuple[SemanticOccurrence, ...]
    skipped_text_files: tuple[SkippedTextFile, ...]


def _divergent_fragments(old: str, new: str, separator: str) -> list[Rename]:
    old_parts = old.split(separator)
    new_parts = new.split(separator)
    common = 0
    while (
        common < len(old_parts)
        and common < len(new_parts)
        and old_parts[common] == new_parts[common]
    ):
        common += 1
    old_tail = old_parts[common:]
    new_tail = new_parts[common:]
    return [
        Rename(separator.join(old_tail[:count]), separator.join(new_tail[:count]))
        for count in range(min(len(old_tail), len(new_tail)), 0, -1)
        if separator.join(old_tail[:count]) != separator.join(new_tail[:count])
    ]


def logical_fragment_mappings(relocation: DerivedIdentityMap) -> tuple[SemanticMapping, ...]:
    """Derive longest-first dotted and slashed logical segment fragments."""

    if relocation.source_node_id is None or relocation.target_node_id is None:
        return ()
    dotted = _divergent_fragments(
        relocation.source_node_id, relocation.target_node_id, "."
    )
    result: list[SemanticMapping] = []
    for rename in dotted:
        result.append(
            SemanticMapping(
                "logical_fragment",
                f"{relocation.mapping_id}:logical:{rename.old}->{rename.new}",
                relocation.mapping_id,
                rename.old,
                rename.new,
            )
        )
        if "." in rename.old:
            old_path, new_path = rename.old.replace(".", "/"), rename.new.replace(".", "/")
            result.append(
                SemanticMapping(
                    "logical_fragment",
                    f"{relocation.mapping_id}:logical:{old_path}->{new_path}",
                    relocation.mapping_id,
                    old_path,
                    new_path,
                )
            )
    return tuple(result)


def _physical_mappings(relocation: DerivedIdentityMap) -> tuple[SemanticMapping, ...]:
    full = SemanticMapping(
        "physical_path",
        f"{relocation.mapping_id}:physical:{relocation.source_path}->{relocation.target_path}",
        relocation.mapping_id,
        relocation.source_path,
        relocation.target_path,
    )
    old_parts = relocation.source_path.split("/")
    new_parts = relocation.target_path.split("/")
    suffix = 0
    while suffix < min(len(old_parts), len(new_parts)) and old_parts[-1 - suffix] == new_parts[-1 - suffix]:
        suffix += 1
    old_core = old_parts[:-suffix] if suffix else old_parts
    new_core = new_parts[:-suffix] if suffix else new_parts
    core_pairs = [Rename("/".join(old_core), "/".join(new_core))]
    if len(old_core) == len(new_core):
        core_pairs.extend(
            Rename("/".join(old_core[:count]), "/".join(new_core[:count]))
            for count in range(len(old_core) - 1, 0, -1)
        )
    fragments = tuple(
        SemanticMapping(
            "physical_fragment",
            f"{relocation.mapping_id}:physical-fragment:{item.old}->{item.new}",
            relocation.mapping_id,
            item.old,
            item.new,
        )
        for item in core_pairs
        if item.old != relocation.source_path
        and item.old != item.new
    )
    return (full, *fragments)


def _python_mappings(relocation: DerivedIdentityMap) -> tuple[SemanticMapping, ...]:
    result: list[SemanticMapping] = []
    for item in relocation.python_modules:
        result.append(
            SemanticMapping(
                "python_module",
                f"{relocation.mapping_id}:python:{item.old}->{item.new}",
                relocation.mapping_id,
                item.old,
                item.new,
            )
        )
    return tuple(result)


def semantic_mappings(
    relocations: Iterable[DerivedIdentityMap],
) -> tuple[SemanticMapping, ...]:
    """Build the complete typed candidate inventory."""

    values = [
        mapping
        for relocation in relocations
        for mapping in (
            *logical_fragment_mappings(relocation),
            *_physical_mappings(relocation),
            *_python_mappings(relocation),
        )
        if mapping.old
    ]
    return tuple(
        sorted(
            values,
            key=lambda item: (
                -len(item.old.encode("utf-8")),
                _MAPPING_PRIORITY[item.mapping_kind],
                item.mapping_id,
            ),
        )
    )


def _excluded(path: str, exclusions: Iterable[str]) -> bool:
    return any(
        path == excluded or path.startswith(excluded.rstrip("/") + "/")
        for excluded in exclusions
    )


def semantic_inventory_entries(changes: ChangeSet) -> tuple[str, ...]:
    """Return projected entries without mechanical cache filtering."""

    paths: set[str] = set()
    for directory, directory_names, file_names in os.walk(
        changes.root, followlinks=False
    ):
        base = Path(directory)
        for name in file_names:
            paths.add((base / name).relative_to(changes.root).as_posix())
        for name in directory_names:
            candidate = base / name
            if candidate.is_symlink():
                paths.add(candidate.relative_to(changes.root).as_posix())
    paths.difference_update(changes.deletes)
    paths.update(changes.writes)
    return tuple(
        path for path in sorted(paths) if not _excluded(path, changes.inventory_exclusions)
    )


def _boundary_pattern(candidate: str) -> re.Pattern[str]:
    boundary = r"\w./-"
    return re.compile(rf"(?<![{boundary}]){re.escape(candidate)}(?![{boundary}])")


def _location(text: str, byte_start: int) -> tuple[int, int]:
    prefix = text.encode("utf-8")[:byte_start].decode("utf-8")
    return prefix.count("\n") + 1, len(prefix.rsplit("\n", 1)[-1]) + 1


def _context(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    relative_start = start - line_start
    relative_end = end - line_start
    if len(line) <= 240:
        return line
    window_start = max(0, min(relative_start - 100, len(line) - 240))
    window_end = max(window_start + 240, relative_end)
    window_end = min(window_end, len(line))
    window_start = max(0, window_end - 240)
    return line[window_start:window_end]


def _generated_source(path: str, changes: ChangeSet, start: int, end: int) -> tuple[bool, str | None]:
    if path in changes.generated_artifact_changes:
        if path.startswith("skills/") and path.endswith("/SKILL.md"):
            return True, str(PurePosixPath(path).parent / "blueprint.yaml")
        return True, None
    if path.startswith("skills/") and path.endswith("/SKILL.md"):
        payload = changes.read_bytes(path)
        for begin, finish in (
            (b"<!-- BEGIN BLUEPRINT CONTRACT -->", b"<!-- END BLUEPRINT CONTRACT -->"),
            (b"<!-- BEGIN BLUEPRINT INTERFACES -->", b"<!-- END BLUEPRINT INTERFACES -->"),
        ):
            span_start = payload.find(begin)
            span_end = payload.find(finish)
            if span_start >= 0 and span_end >= 0:
                span_end += len(finish)
                if start < span_end and end > span_start:
                    return True, str(PurePosixPath(path).parent / "blueprint.yaml")
    for span_start, span_end, source in changes.generated_spans.get(path, ()):
        if start < span_end and end > span_start:
            return True, source
    return False, None


class SemanticScan:
    """Scan every included projected regular file once as strict UTF-8."""

    def __init__(self, changes: ChangeSet):
        self.changes = changes

    def run(self) -> SemanticScanResult:
        mappings = semantic_mappings(self.changes.derived_relocations)
        occurrences: list[SemanticOccurrence] = []
        skipped: list[SkippedTextFile] = []
        for path in semantic_inventory_entries(self.changes):
            disk_path = self.changes.root / path
            if path in self.changes.symlink_writes or (
                disk_path.is_symlink() and path not in self.changes.writes
            ):
                skipped.append(SkippedTextFile(path, "symlink"))
                continue
            try:
                payload = self.changes.read_bytes(path)
            except (OSError, RelocationError):
                skipped.append(SkippedTextFile(path, "unreadable"))
                continue
            if b"\x00" in payload:
                skipped.append(SkippedTextFile(path, "nul-byte"))
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(SkippedTextFile(path, "non-utf8"))
                continue

            candidates: list[tuple[int, int, SemanticMapping]] = []
            for mapping in mappings:
                for match in _boundary_pattern(mapping.old).finditer(text):
                    start = len(text[: match.start()].encode("utf-8"))
                    end = len(text[: match.end()].encode("utf-8"))
                    candidates.append((start, end, mapping))
            selected: list[tuple[int, int, SemanticMapping]] = []
            for start, end, mapping in sorted(
                candidates,
                key=lambda item: (
                    -(item[1] - item[0]),
                    item[0],
                    _MAPPING_PRIORITY[item[2].mapping_kind],
                    item[2].mapping_id,
                ),
            ):
                exact = [item for item in selected if item[0] == start and item[1] == end]
                if exact:
                    if any(item[2].new != mapping.new for item in exact):
                        raise RelocationError(
                            f"conflicting semantic candidates at {path}:{start}-{end}"
                        )
                    continue
                if any(start < item_end and end > item_start for item_start, item_end, _ in selected):
                    continue
                selected.append((start, end, mapping))

            ordinals: dict[tuple[str, str], int] = {}
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            for start, end, mapping in sorted(selected):
                ordinal_key = (mapping.mapping_id, mapping.old)
                ordinal = ordinals.get(ordinal_key, 0) + 1
                ordinals[ordinal_key] = ordinal
                line, column = _location(text, start)
                char_start = len(payload[:start].decode("utf-8"))
                char_end = len(payload[:end].decode("utf-8"))
                generated, authored_source = _generated_source(path, self.changes, start, end)
                selector = {
                    "mapping_kind": mapping.mapping_kind,
                    "mapping_id": mapping.mapping_id,
                    "path": path,
                    "byte_start": start,
                    "byte_end": end,
                    "match": mapping.old,
                    "ordinal": ordinal,
                }
                occurrence_id = "sha256:" + hashlib.sha256(
                    json.dumps(selector, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                occurrences.append(
                    SemanticOccurrence(
                        occurrence_id,
                        mapping.mapping_kind,
                        mapping.mapping_id,
                        mapping.relocation_id,
                        path,
                        digest,
                        start,
                        end,
                        line,
                        column,
                        ordinal,
                        mapping.old,
                        mapping.new,
                        _context(text, char_start, char_end),
                        generated,
                        authored_source,
                    )
                )
        return SemanticScanResult(
            tuple(sorted(occurrences, key=lambda item: (item.path, item.byte_start, item.mapping_id))),
            tuple(sorted(skipped, key=lambda item: item.path)),
        )


__all__ = [
    "SemanticMapping",
    "SemanticOccurrence",
    "SemanticScan",
    "SemanticScanResult",
    "SkippedTextFile",
    "logical_fragment_mappings",
    "semantic_inventory_entries",
    "semantic_mappings",
]
