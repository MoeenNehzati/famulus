"""Plan and apply typed, blueprint-aware source relocations.

The engine deliberately separates repository-specific declarations from generic
mechanics.  It projects every move and rewrite in memory, validates the projected
tree, and only then publishes the complete file-level change set.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

import jsonschema
import yaml


_CACHE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "_build",
    "build",
    "node_modules",
}
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    "." + "to" + "ml",
    ".txt",
    ".yaml",
    ".yml",
}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "moves",
    "renames",
    "blueprint_documents",
    "ownership_transfers",
    "caller_additions",
    "exact_rewrites",
    "package_catalogs",
    "forbid_facade_imports",
    "text_exclusions",
    "active_address_exclusions",
    "inventory_exclusions",
    "standard_digest_roots",
}


class RelocationError(RuntimeError):
    """Signal an unsafe, ambiguous, or incomplete relocation."""


@dataclass(frozen=True)
class Rename:
    """Map one typed repository identity to its replacement."""

    old: str
    new: str


@dataclass(frozen=True)
class Move:
    """Move one repository-relative file or directory."""

    source: str
    target: str


@dataclass(frozen=True)
class ExactRewrite:
    """Replace one location-dependent spelling with a mandatory precondition."""

    path: str
    old: str
    new: str
    count: int = 1


@dataclass(frozen=True)
class PackageCatalog:
    """Describe one README-only package initializer."""

    path: str
    summary: str
    description: str
    roles: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OwnershipTransfer:
    """Transfer one behavioral source and optional export between modules."""

    from_blueprint: str
    to_blueprint: str
    source: Rename
    export: Rename | None
    content: Rename


@dataclass(frozen=True)
class RelocationManifest:
    """Store validated, repository-independent relocation declarations."""

    moves: tuple[Move, ...] = ()
    renames: Mapping[str, tuple[Rename, ...]] = field(default_factory=dict)
    blueprint_documents: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    ownership_transfers: tuple[OwnershipTransfer, ...] = ()
    caller_additions: tuple[tuple[str, str, str], ...] = ()
    exact_rewrites: tuple[ExactRewrite, ...] = ()
    package_catalogs: tuple[PackageCatalog, ...] = ()
    forbid_facade_imports: tuple[str, ...] = ()
    text_exclusions: tuple[str, ...] = ()
    active_address_exclusions: tuple[str, ...] = ()
    inventory_exclusions: tuple[str, ...] = ()
    standard_digest_roots: tuple[str, ...] = ()


def _repository_path(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RelocationError(f"{field_name} must be a non-empty repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("./"):
        raise RelocationError(f"{field_name} must be a repository-relative path: {value!r}")
    return path.as_posix()


def _rename(
    value: object,
    *,
    field_name: str,
    allow_same: bool = False,
) -> Rename:
    if not isinstance(value, dict) or set(value) != {"from", "to"}:
        raise RelocationError(f"{field_name} must contain exactly 'from' and 'to'")
    old, new = value["from"], value["to"]
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        raise RelocationError(f"{field_name} values must be non-empty strings")
    if old == new and not allow_same:
        raise RelocationError(f"{field_name} must change its value")
    return Rename(old=old, new=new)


def _sequence(value: object, *, field_name: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RelocationError(f"{field_name} must be a list")
    return value


def load_manifest(path: Path) -> RelocationManifest:
    """Load and strictly validate one relocation manifest."""

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RelocationError(f"cannot load relocation manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RelocationError("relocation manifest root must be a mapping")
    unknown = sorted(set(value) - _TOP_LEVEL_KEYS)
    if unknown:
        raise RelocationError("unknown manifest key: " + ", ".join(unknown))
    if value.get("schema_version") != 1:
        raise RelocationError("schema_version must be 1")

    schema_path = Path(__file__).with_name("relocation.schema.json")
    try:
        jsonschema.validate(value, json.loads(schema_path.read_text(encoding="utf-8")))
    except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as exc:
        message = exc.message if isinstance(exc, jsonschema.ValidationError) else str(exc)
        raise RelocationError(f"invalid relocation manifest: {message}") from exc

    moves: list[Move] = []
    endpoints: set[str] = set()
    for index, item in enumerate(_sequence(value.get("moves"), field_name="moves")):
        if not isinstance(item, dict) or set(item) != {"from", "to"}:
            raise RelocationError(f"moves[{index}] must contain exactly 'from' and 'to'")
        source = _repository_path(item["from"], field_name=f"moves[{index}].from")
        target = _repository_path(item["to"], field_name=f"moves[{index}].to")
        if source == target or source in endpoints or target in endpoints:
            raise RelocationError(f"duplicate or ambiguous move endpoint: {source} -> {target}")
        endpoints.update((source, target))
        moves.append(Move(source, target))

    rename_groups: dict[str, tuple[Rename, ...]] = {}
    raw_renames = value.get("renames", {})
    if not isinstance(raw_renames, dict):
        raise RelocationError("renames must be a mapping")
    allowed_rename_groups = {"paths", "python_modules", "source_ids", "interface_ids"}
    unknown_groups = sorted(set(raw_renames) - allowed_rename_groups)
    if unknown_groups:
        raise RelocationError("unknown rename group: " + ", ".join(unknown_groups))
    for group, records in raw_renames.items():
        parsed = tuple(
            _rename(item, field_name=f"renames.{group}[{index}]")
            for index, item in enumerate(_sequence(records, field_name=f"renames.{group}"))
        )
        if len({item.old for item in parsed}) != len(parsed):
            raise RelocationError(f"duplicate source identity in renames.{group}")
        rename_groups[group] = parsed

    blueprint_documents: list[tuple[str, Mapping[str, Any]]] = []
    for index, item in enumerate(_sequence(value.get("blueprint_documents"), field_name="blueprint_documents")):
        if not isinstance(item, dict) or set(item) != {"path", "document"} or not isinstance(item["document"], dict):
            raise RelocationError(f"blueprint_documents[{index}] must contain path and document")
        blueprint_documents.append(
            (_repository_path(item["path"], field_name=f"blueprint_documents[{index}].path"), item["document"])
        )

    transfers: list[OwnershipTransfer] = []
    for index, item in enumerate(_sequence(value.get("ownership_transfers"), field_name="ownership_transfers")):
        if not isinstance(item, dict):
            raise RelocationError(f"ownership_transfers[{index}] must be a mapping")
        expected = {"from_blueprint", "to_blueprint", "source", "content"}
        if not expected.issubset(item) or set(item) - (expected | {"export"}):
            raise RelocationError(f"ownership_transfers[{index}] has invalid keys")
        transfers.append(
            OwnershipTransfer(
                from_blueprint=_repository_path(item["from_blueprint"], field_name="from_blueprint"),
                to_blueprint=_repository_path(item["to_blueprint"], field_name="to_blueprint"),
                source=_rename(item["source"], field_name="source"),
                export=_rename(item["export"], field_name="export") if "export" in item else None,
                content=_rename(
                    item["content"],
                    field_name="content",
                    allow_same=True,
                ),
            )
        )

    caller_additions: list[tuple[str, str, str]] = []
    for index, item in enumerate(_sequence(value.get("caller_additions"), field_name="caller_additions")):
        if not isinstance(item, dict) or set(item) != {"blueprint", "interface", "caller"}:
            raise RelocationError(f"caller_additions[{index}] has invalid keys")
        if not all(isinstance(item[key], str) and item[key] for key in item):
            raise RelocationError(f"caller_additions[{index}] values must be strings")
        caller_additions.append(
            (_repository_path(item["blueprint"], field_name="caller blueprint"), item["interface"], item["caller"])
        )

    exact_rewrites: list[ExactRewrite] = []
    for index, item in enumerate(_sequence(value.get("exact_rewrites"), field_name="exact_rewrites")):
        if not isinstance(item, dict) or not {"path", "from", "to"}.issubset(item) or set(item) - {"path", "from", "to", "count"}:
            raise RelocationError(f"exact_rewrites[{index}] has invalid keys")
        count = item.get("count", 1)
        if not isinstance(count, int) or count < 1:
            raise RelocationError(f"exact_rewrites[{index}].count must be positive")
        exact_rewrites.append(
            ExactRewrite(
                _repository_path(item["path"], field_name="exact rewrite path"),
                str(item["from"]),
                str(item["to"]),
                count,
            )
        )
        if exact_rewrites[-1].old == exact_rewrites[-1].new:
            raise RelocationError(
                f"exact_rewrites[{index}] must change its value"
            )

    catalogs: list[PackageCatalog] = []
    for index, item in enumerate(_sequence(value.get("package_catalogs"), field_name="package_catalogs")):
        if not isinstance(item, dict) or not {"path", "summary", "description"}.issubset(item) or set(item) - {"path", "summary", "description", "roles"}:
            raise RelocationError(f"package_catalogs[{index}] has invalid keys")
        roles = item.get("roles", {})
        if not isinstance(roles, dict) or not all(isinstance(key, str) and isinstance(role, str) for key, role in roles.items()):
            raise RelocationError(f"package_catalogs[{index}].roles must map strings to strings")
        catalogs.append(
            PackageCatalog(
                _repository_path(item["path"], field_name="package catalog path"),
                str(item["summary"]),
                str(item["description"]),
                roles,
            )
        )

    def string_tuple(key: str, *, paths: bool = False) -> tuple[str, ...]:
        values = _sequence(value.get(key), field_name=key)
        if not all(isinstance(item, str) and item for item in values):
            raise RelocationError(f"{key} must contain non-empty strings")
        return tuple(
            _repository_path(item, field_name=key) if paths else item
            for item in values
        )

    return RelocationManifest(
        moves=tuple(moves),
        renames=rename_groups,
        blueprint_documents=tuple(blueprint_documents),
        ownership_transfers=tuple(transfers),
        caller_additions=tuple(caller_additions),
        exact_rewrites=tuple(exact_rewrites),
        package_catalogs=tuple(catalogs),
        forbid_facade_imports=string_tuple("forbid_facade_imports"),
        text_exclusions=string_tuple("text_exclusions", paths=True),
        active_address_exclusions=string_tuple("active_address_exclusions", paths=True),
        inventory_exclusions=string_tuple("inventory_exclusions", paths=True),
        standard_digest_roots=string_tuple("standard_digest_roots", paths=True),
    )


@dataclass
class ChangeSet:
    """Hold one validated projected repository change set."""

    root: Path
    inventory_exclusions: tuple[str, ...] = ()
    moves: list[Move] = field(default_factory=list)
    writes: dict[str, bytes] = field(default_factory=dict)
    deletes: set[str] = field(default_factory=set)
    expected: dict[str, bytes | None] = field(default_factory=dict)
    blueprint_changes: set[str] = field(default_factory=set)
    digest_changes: set[str] = field(default_factory=set)
    base_files: set[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Snapshot the repository inventory once for every projected-tree query."""

        self.base_files = set()
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if any(part in _CACHE_PARTS for part in PurePosixPath(relative).parts):
                continue
            if any(
                relative == excluded
                or relative.startswith(excluded.rstrip("/") + "/")
                for excluded in self.inventory_exclusions
            ):
                continue
            self.base_files.add(relative)

    def _disk_bytes(self, relative: str) -> bytes | None:
        path = self.root / relative
        return path.read_bytes() if path.is_file() else None

    def read_bytes(self, relative: str) -> bytes:
        """Read one file from the projected tree."""

        if relative in self.writes:
            return self.writes[relative]
        if relative in self.deletes:
            raise RelocationError(f"projected path does not exist: {relative}")
        value = self._disk_bytes(relative)
        if value is None:
            raise RelocationError(f"projected path does not exist: {relative}")
        return value

    def read_text(self, relative: str) -> str:
        """Read strict UTF-8 text from the projected tree."""

        return self.read_bytes(relative).decode("utf-8")

    def exists(self, relative: str) -> bool:
        """Return whether a file exists in the projected tree."""

        return relative in self.writes or (
            relative not in self.deletes and (self.root / relative).is_file()
        )

    def write_bytes(self, relative: str, payload: bytes) -> None:
        """Project one changed file without touching the repository."""

        current = None
        try:
            current = self.read_bytes(relative)
        except RelocationError:
            pass
        if current == payload:
            return
        disk_payload = self._disk_bytes(relative)
        if disk_payload == payload and relative not in self.deletes:
            self.writes.pop(relative, None)
            return
        self.expected.setdefault(relative, self._disk_bytes(relative))
        self.writes[relative] = payload
        self.deletes.discard(relative)

    def write_text(self, relative: str, text: str) -> None:
        """Project one UTF-8 text file."""

        self.write_bytes(relative, text.encode("utf-8"))

    def projected_files(self) -> set[str]:
        """Return every file path in the projected repository."""

        paths = set(self.base_files)
        paths.difference_update(self.deletes)
        paths.update(self.writes)
        return paths

    def report(self) -> dict[str, object]:
        """Return a stable machine-readable description of this change set."""

        return {
            "moves": [
                {"from": move.source, "to": move.target}
                for move in sorted(self.moves, key=lambda item: (item.source, item.target))
            ],
            "writes": sorted(self.writes),
            "deletes": sorted(self.deletes),
            "blueprint_changes": sorted(self.blueprint_changes),
            "digest_changes": sorted(self.digest_changes),
            "unresolved_references": [],
        }


def _eligible_files(root: Path, relative: str) -> list[Path]:
    path = root / relative
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return [
        child
        for child in sorted(path.rglob("*"))
        if child.is_file() and not any(part in _CACHE_PARTS for part in child.relative_to(root).parts)
    ]


def _project_moves(changes: ChangeSet, manifest: RelocationManifest) -> None:
    for move in manifest.moves:
        source_path = changes.root / move.source
        target_path = changes.root / move.target
        source_files = _eligible_files(changes.root, move.source)
        target_files = _eligible_files(changes.root, move.target)
        if source_files and target_files:
            raise RelocationError(f"both move endpoints contain files: {move.source}, {move.target}")
        if not source_files and not target_files:
            raise RelocationError(f"neither move endpoint exists: {move.source}, {move.target}")
        if not source_files:
            continue
        changes.moves.append(move)
        source_is_dir = source_path.is_dir()
        for source_file in source_files:
            suffix = source_file.relative_to(source_path).as_posix() if source_is_dir else ""
            target_relative = (
                (PurePosixPath(move.target) / suffix).as_posix()
                if suffix
                else move.target
            )
            source_relative = source_file.relative_to(changes.root).as_posix()
            if (changes.root / target_relative).exists():
                raise RelocationError(f"move target already exists: {target_relative}")
            changes.expected.setdefault(source_relative, source_file.read_bytes())
            changes.expected.setdefault(target_relative, None)
            changes.writes[target_relative] = source_file.read_bytes()
            changes.deletes.add(source_relative)


def _yaml_mapping(changes: ChangeSet, relative: str) -> dict[str, Any]:
    value = yaml.safe_load(changes.read_text(relative))
    if not isinstance(value, dict):
        raise RelocationError(f"blueprint must be a mapping: {relative}")
    return value


def _dump_yaml(value: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True)


def _project_blueprints(changes: ChangeSet, manifest: RelocationManifest) -> None:
    for relative, document in manifest.blueprint_documents:
        if changes.exists(relative):
            existing = _yaml_mapping(changes, relative)
            if existing.get("id") != document.get("id"):
                raise RelocationError(f"blueprint identity mismatch at {relative}")
        else:
            changes.write_text(relative, _dump_yaml(document))
            changes.blueprint_changes.add(relative)

    move_lookup = {move.source: move.target for move in manifest.moves}
    for transfer in manifest.ownership_transfers:
        old = _yaml_mapping(changes, transfer.from_blueprint)
        new = _yaml_mapping(changes, transfer.to_blueprint)
        old_sources = old.setdefault("sources", {})
        new_sources = new.setdefault("sources", {})
        old_exports = old.setdefault("exports", {})
        new_exports = new.setdefault("exports", {})
        if transfer.source.old not in old_sources:
            if transfer.source.new in new_sources and (
                transfer.export is None or transfer.export.new in new_exports
            ):
                continue
            raise RelocationError(
                f"ownership source missing from both endpoints: {transfer.source.old}"
            )
        source_record = old_sources.pop(transfer.source.old)
        if transfer.source.new in new_sources:
            raise RelocationError(f"target source already exists: {transfer.source.new}")
        old_root = PurePosixPath(transfer.from_blueprint).parent
        new_root = PurePosixPath(transfer.to_blueprint).parent
        sidecar = source_record.get("blueprint", {}).get("path")
        if isinstance(sidecar, str):
            old_sidecar = (old_root / sidecar).as_posix()
            target_sidecar = move_lookup.get(old_sidecar)
            if target_sidecar is not None:
                source_record["blueprint"]["path"] = PurePosixPath(target_sidecar).relative_to(new_root).as_posix()
        new_sources[transfer.source.new] = source_record
        if transfer.export is not None:
            if transfer.export.old not in old_exports:
                raise RelocationError(f"ownership export missing: {transfer.export.old}")
            if transfer.export.new in new_exports:
                raise RelocationError(f"target export already exists: {transfer.export.new}")
            new_exports[transfer.export.new] = old_exports.pop(transfer.export.old)
        old_content = old.setdefault("content", [])
        new_content = new.setdefault("content", [])
        if transfer.content.old not in old_content:
            raise RelocationError(f"ownership content missing: {transfer.content.old}")
        old_content.remove(transfer.content.old)
        if transfer.content.new not in new_content:
            new_content.append(transfer.content.new)
        changes.write_text(transfer.from_blueprint, _dump_yaml(old))
        changes.write_text(transfer.to_blueprint, _dump_yaml(new))
        changes.blueprint_changes.update((transfer.from_blueprint, transfer.to_blueprint))

    for blueprint_path, interface_id, caller in manifest.caller_additions:
        blueprint = _yaml_mapping(changes, blueprint_path)
        try:
            access = blueprint["exports"][interface_id]["access"]
            callers = access["allowed_callers"]
        except (KeyError, TypeError) as exc:
            raise RelocationError(
                f"caller addition references missing export: {blueprint_path}:{interface_id}"
            ) from exc
        if access.get("allow_all_modules"):
            raise RelocationError(f"caller addition cannot broaden an allow-all export: {interface_id}")
        if caller not in callers:
            callers.append(caller)
            changes.write_text(blueprint_path, _dump_yaml(blueprint))
            changes.blueprint_changes.add(blueprint_path)


def _text_file(relative: str, exclusions: Iterable[str]) -> bool:
    path = PurePosixPath(relative)
    if any(part in _CACHE_PARTS for part in path.parts):
        return False
    if relative in exclusions:
        return False
    return path.suffix in _TEXT_SUFFIXES or not path.suffix


def _all_renames(manifest: RelocationManifest) -> list[Rename]:
    values = [Rename(move.source, move.target) for move in manifest.moves]
    for group in ("paths", "python_modules", "interface_ids", "source_ids"):
        values.extend(manifest.renames.get(group, ()))
    unique: dict[str, Rename] = {}
    for rename in values:
        if rename.old in unique and unique[rename.old].new != rename.new:
            raise RelocationError(f"conflicting rename: {rename.old}")
        unique[rename.old] = rename
    return sorted(unique.values(), key=lambda item: len(item.old), reverse=True)


def _project_text_rewrites(changes: ChangeSet, manifest: RelocationManifest) -> None:
    renames = _all_renames(manifest)
    for relative in sorted(changes.projected_files()):
        if not _text_file(relative, manifest.text_exclusions):
            continue
        try:
            text = changes.read_text(relative)
        except UnicodeDecodeError:
            continue
        updated = text
        for rename in renames:
            updated = updated.replace(rename.old, rename.new)
        if updated != text:
            changes.write_text(relative, updated)

    for rewrite in manifest.exact_rewrites:
        text = changes.read_text(rewrite.path)
        occurrences = text.count(rewrite.old)
        replacement_occurrences = text.count(rewrite.new)
        if rewrite.old in rewrite.new:
            replacement_residual = text.replace(rewrite.new, "")
            already_applied = (
                replacement_occurrences >= rewrite.count
                and rewrite.old not in replacement_residual
            )
            ready_to_apply = (
                occurrences == rewrite.count and replacement_occurrences == 0
            )
        elif rewrite.new in rewrite.old:
            old_residual = text.replace(rewrite.old, "")
            already_applied = (
                occurrences == 0 and replacement_occurrences >= rewrite.count
            )
            ready_to_apply = (
                occurrences == rewrite.count and rewrite.new not in old_residual
            )
        else:
            already_applied = (
                occurrences == 0 and replacement_occurrences >= rewrite.count
            )
            ready_to_apply = (
                occurrences == rewrite.count and replacement_occurrences == 0
            )
        if already_applied:
            continue
        if not ready_to_apply:
            raise RelocationError(
                f"exact rewrite precondition failed for {rewrite.path}: "
                f"expected {rewrite.count} old and no replacement occurrences; "
                f"found {occurrences} old and {replacement_occurrences} replacement"
            )
        changes.write_text(
            rewrite.path,
            text.replace(rewrite.old, rewrite.new, rewrite.count),
        )


def _catalog_entries(changes: ChangeSet, package: str) -> list[str]:
    prefix = package.rstrip("/") + "/"
    descendants = sorted(
        relative[len(prefix) :]
        for relative in changes.projected_files()
        if relative.startswith(prefix)
    )
    descendants.append("__init__.py")
    child_packages = {
        PurePosixPath(relative).parts[0]
        for relative in descendants
        if len(PurePosixPath(relative).parts) > 1
        and f"{prefix}{PurePosixPath(relative).parts[0]}/__init__.py" in changes.projected_files()
    }
    entries: set[str] = set()
    for relative in descendants:
        parts = PurePosixPath(relative).parts
        if parts and parts[0] in child_packages:
            entries.add(parts[0] + "/")
        else:
            entries.add(relative)
    return sorted(entries)


def _default_role(relative: str) -> str:
    name = PurePosixPath(relative).name
    if relative == "__init__.py":
        return "Documents this package and its owned files."
    if relative.endswith("/"):
        return f"Contains the {relative.rstrip('/').replace('_', ' ')} subpackage."
    if relative == "blueprint.yaml":
        return "Declares this package's registered Officina module boundary."
    if relative.startswith("blueprints/") and relative.endswith((".yaml", ".yml")):
        return f"Declares the {PurePosixPath(relative).stem.replace('-', ' ')} behavioral source contract."
    if name == "README.md":
        return "Provides detailed package-specific operational documentation."
    if relative.endswith(".py"):
        return f"Implements the package's {PurePosixPath(relative).stem.replace('_', ' ')} responsibility."
    if relative.endswith(".js"):
        return f"Implements the browser runtime's {PurePosixPath(relative).stem.replace('_', ' ')} behavior."
    if relative.endswith(".json"):
        return "Defines a structured runtime schema or manifest owned by this package."
    if relative.endswith((".yaml", ".yml")):
        return "Provides declarative runtime configuration owned by this package."
    return "Provides a tracked runtime resource owned by this package."


def _project_catalogs(changes: ChangeSet, manifest: RelocationManifest) -> None:
    for catalog in manifest.package_catalogs:
        lines = [
            f'"""{catalog.summary}',
            "",
            catalog.description,
            "",
            "Includes",
            "--------",
        ]
        for relative in _catalog_entries(changes, catalog.path):
            role = catalog.roles.get(relative, _default_role(relative))
            lines.extend((f"``{relative}``", f"    {role}"))
        lines.extend(('"""', ""))
        changes.write_text(f"{catalog.path}/__init__.py", "\n".join(lines))


def _project_standard_digests(changes: ChangeSet, manifest: RelocationManifest) -> None:
    standard_paths: list[str] = []
    for root_name in manifest.standard_digest_roots:
        prefix = root_name.rstrip("/") + "/"
        standard_paths.extend(
            relative
            for relative in changes.projected_files()
            if relative.startswith(prefix) and relative.endswith(".standard.yaml")
        )
    standard_paths = sorted(set(standard_paths))
    id_paths: dict[str, str] = {}
    for relative in standard_paths:
        value = yaml.safe_load(changes.read_text(relative))
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            if value["id"] in id_paths:
                raise RelocationError(f"duplicate standard id: {value['id']}")
            id_paths[value["id"]] = relative
    for _ in range(len(standard_paths) + 1):
        changed = False
        for relative in standard_paths:
            value = yaml.safe_load(changes.read_text(relative))
            imports = value.get("imports", {}) if isinstance(value, dict) else {}
            if not isinstance(imports, dict):
                continue
            text = changes.read_text(relative)
            updated = text
            for key, record in imports.items():
                if not isinstance(record, dict) or record.get("standard_id") not in id_paths:
                    continue
                target = id_paths[record["standard_id"]]
                digest = "sha256:" + hashlib.sha256(changes.read_bytes(target)).hexdigest()
                pattern = re.compile(
                    rf"(^  {re.escape(str(key))}:\n(?:(?:^    .*\n)|(?:^\s*$\n))*?^    digest: )sha256:[0-9a-f]{{64}}",
                    re.MULTILINE,
                )
                updated, count = pattern.subn(rf"\g<1>{digest}", updated, count=1)
                if count != 1:
                    raise RelocationError(f"cannot refresh import digest {relative}:{key}")
            if updated != text:
                changes.write_text(relative, updated)
                changes.digest_changes.add(relative)
                changed = True
        if not changed:
            return
    raise RelocationError("standard digest refresh did not converge")


def _validate_projected_tree(changes: ChangeSet, manifest: RelocationManifest) -> None:
    excluded = set(manifest.active_address_exclusions)
    retired = [rename.old for rename in _all_renames(manifest)]
    stale: list[str] = []
    syntax: list[str] = []
    facades: list[str] = []
    for relative in sorted(changes.projected_files()):
        if relative in excluded or not _text_file(relative, manifest.text_exclusions):
            continue
        try:
            text = changes.read_text(relative)
        except UnicodeDecodeError:
            continue
        for old in retired:
            if old in text:
                stale.append(f"{relative}: {old}")
                break
        if not relative.endswith(".py"):
            continue
        try:
            tree = ast.parse(text, filename=relative)
        except SyntaxError as exc:
            syntax.append(f"{relative}:{exc.lineno}: {exc.msg}")
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module in manifest.forbid_facade_imports
            ):
                facades.append(f"{relative}:{node.lineno}: from {node.module} import ...")
    init_errors: list[str] = []
    for catalog in manifest.package_catalogs:
        relative = f"{catalog.path}/__init__.py"
        tree = ast.parse(changes.read_text(relative), filename=relative)
        if (
            len(tree.body) != 1
            or not isinstance(tree.body[0], ast.Expr)
            or not isinstance(tree.body[0].value, ast.Constant)
            or not isinstance(tree.body[0].value.value, str)
        ):
            init_errors.append(f"{relative}: initializer is not README-only")
    failures = stale + syntax + facades + init_errors
    if failures:
        raise RelocationError("projected-tree validation failed:\n" + "\n".join(failures[:100]))


def plan_relocation(root: Path, manifest: RelocationManifest) -> ChangeSet:
    """Build and validate a complete relocation without writing to disk."""

    root = root.resolve()
    if not root.is_dir():
        raise RelocationError(f"repository root does not exist: {root}")
    changes = ChangeSet(
        root=root,
        inventory_exclusions=manifest.inventory_exclusions,
    )
    _project_moves(changes, manifest)
    _project_blueprints(changes, manifest)
    _project_text_rewrites(changes, manifest)
    _project_catalogs(changes, manifest)
    _project_standard_digests(changes, manifest)
    _validate_projected_tree(changes, manifest)
    return changes


def apply_change_set(changes: ChangeSet) -> None:
    """Publish one already validated change set with file-level atomic writes."""

    for relative, expected in changes.expected.items():
        current = changes._disk_bytes(relative)
        if current != expected:
            raise RelocationError(f"repository changed after preflight: {relative}")
    staged: dict[str, Path] = {}
    try:
        for relative, payload in sorted(changes.writes.items()):
            target = changes.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.officina-relocation-{hashlib.sha256(relative.encode()).hexdigest()[:12]}"
            )
            if temporary.exists():
                raise RelocationError(f"staging path already exists: {temporary}")
            temporary.write_bytes(payload)
            staged[relative] = temporary
        for relative, temporary in staged.items():
            os.replace(temporary, changes.root / relative)
        for relative in sorted(changes.deletes, reverse=True):
            path = changes.root / relative
            if path.is_file():
                path.unlink()
    except Exception:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        raise


def render_report(changes: ChangeSet) -> str:
    """Render a stable JSON report for CLI and audit consumers."""

    return json.dumps(changes.report(), indent=2, sort_keys=True) + "\n"
