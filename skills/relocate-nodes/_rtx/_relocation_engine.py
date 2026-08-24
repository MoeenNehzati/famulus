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
import stat
from typing import Any, Iterable, Literal, Mapping, Protocol

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
    "package_boundaries",
    "relocations",
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
    "semantic_decisions",
}
_SCHEMA_VERSION = 3
_DEFAULT_INVENTORY_EXCLUSIONS = (".git", ".claude", ".codex", ".superpowers")


class RelocationError(RuntimeError):
    """Signal an unsafe, ambiguous, or incomplete relocation."""


class BlueprintSynchronizer(Protocol):
    """Synchronize or check generated blueprints in one projected repository."""

    def __call__(self, repository: Path, *, check: bool) -> None:
        """Synchronize when ``check`` is false; otherwise verify without writes."""


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
    python_modules: tuple[Rename, ...] = ()


@dataclass(frozen=True)
class DerivedIdentityMap:
    """Store every mechanically proved identity induced by one relocation."""

    source_path: str
    target_path: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    source_root: str | None = None
    target_root: str | None = None
    module_ids: tuple[Rename, ...] = ()
    source_ids: tuple[Rename, ...] = ()
    interface_ids: tuple[Rename, ...] = ()
    python_modules: tuple[Rename, ...] = ()

    @property
    def mapping_id(self) -> str:
        """Return the stable relocation identity used by semantic selectors."""

        if self.source_node_id is not None and self.target_node_id is not None:
            return f"{self.source_node_id}->{self.target_node_id}"
        return f"{self.source_path}->{self.target_path}"


@dataclass(frozen=True)
class ExactRewrite:
    """Replace one location-dependent spelling with a mandatory precondition."""

    path: str
    old: str
    new: str
    count: int = 1


@dataclass(frozen=True)
class SemanticDecision:
    """Carry one reviewed semantic occurrence selector and disposition."""

    occurrence_id: str
    mapping_kind: str
    mapping_id: str
    path: str
    original_digest: str
    byte_start: int
    byte_end: int
    ordinal: int
    match: str
    count: int
    disposition: Literal["rewrite", "preserve"]
    text: str
    reason: str
    replacement: str | None = None

    def to_report(self) -> dict[str, object]:
        result = {
            "occurrence_id": self.occurrence_id,
            "mapping_kind": self.mapping_kind,
            "mapping_id": self.mapping_id,
            "path": self.path,
            "original_digest": self.original_digest,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "ordinal": self.ordinal,
            "match": self.match,
            "count": self.count,
            "disposition": self.disposition,
            "text": self.text,
            "reason": self.reason,
        }
        if self.replacement is not None:
            result["replacement"] = self.replacement
        return result


@dataclass(frozen=True)
class PackageCatalog:
    """Describe one README-only package initializer."""

    path: str
    summary: str
    description: str
    roles: Mapping[str, str] = field(default_factory=dict)


PackageDisposition = Literal[
    "existing-module", "registered-module", "unregistered-package"
]


@dataclass(frozen=True)
class PackageBoundary:
    """Declare the approved registration policy for one projected package.

    ``path`` identifies a package whose initializer is introduced by the
    relocation projection. ``existing-module`` means its module blueprint was
    already registered before the move. ``registered-module`` supplies the
    explicit projected module identity and blueprint location. An
    ``unregistered-package`` is intentionally left outside the module graph.
    """

    path: str
    disposition: PackageDisposition
    module_id: str | None = None
    blueprint: str | None = None


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

    relocations: tuple[Move, ...] = ()
    blueprint_documents: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    ownership_transfers: tuple[OwnershipTransfer, ...] = ()
    caller_additions: tuple[tuple[str, str, str], ...] = ()
    exact_rewrites: tuple[ExactRewrite, ...] = ()
    semantic_decisions: tuple[SemanticDecision, ...] = ()
    package_catalogs: tuple[PackageCatalog, ...] = ()
    package_boundaries: tuple[PackageBoundary, ...] = ()
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
    if value.get("schema_version") == 2:
        raise RelocationError(
            "schema_version 2 is no longer supported; migrate moves/renames to "
            "schema_version 3 relocations"
        )
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise RelocationError(f"schema_version must be {_SCHEMA_VERSION}")

    schema_path = Path(__file__).resolve().parent / "schemas/relocation.schema.json"
    try:
        jsonschema.validate(value, json.loads(schema_path.read_text(encoding="utf-8")))
    except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as exc:
        message = exc.message if isinstance(exc, jsonschema.ValidationError) else str(exc)
        raise RelocationError(f"invalid relocation manifest: {message}") from exc

    moves: list[Move] = []
    endpoints: set[str] = set()
    for index, item in enumerate(
        _sequence(value.get("relocations"), field_name="relocations")
    ):
        if not isinstance(item, dict) or set(item) - {"from", "to", "python_modules"} or not {"from", "to"}.issubset(item):
            raise RelocationError(
                f"relocations[{index}] must contain from, to, and optional python_modules"
            )
        source = _repository_path(item["from"], field_name=f"relocations[{index}].from")
        target = _repository_path(item["to"], field_name=f"relocations[{index}].to")
        if source == target or source in endpoints or target in endpoints:
            raise RelocationError(f"duplicate or ambiguous move endpoint: {source} -> {target}")
        endpoints.update((source, target))
        python_modules = tuple(
            _rename(
                mapping,
                field_name=f"relocations[{index}].python_modules[{mapping_index}]",
            )
            for mapping_index, mapping in enumerate(
                _sequence(
                    item.get("python_modules"),
                    field_name=f"relocations[{index}].python_modules",
                )
            )
        )
        dotted = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
        if any(not dotted.fullmatch(mapping.old) or not dotted.fullmatch(mapping.new) for mapping in python_modules):
            raise RelocationError(
                f"relocations[{index}].python_modules must contain complete dotted import prefixes"
            )
        if len({mapping.old for mapping in python_modules}) != len(python_modules):
            raise RelocationError(
                f"duplicate source identity in relocations[{index}].python_modules"
            )
        moves.append(Move(source, target, python_modules))

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

    semantic_decisions: list[SemanticDecision] = []
    decision_ids: set[str] = set()
    selector_keys = {
        "occurrence_id",
        "mapping_kind",
        "mapping_id",
        "path",
        "original_digest",
        "byte_start",
        "byte_end",
        "ordinal",
        "match",
        "count",
        "disposition",
        "text",
        "reason",
    }
    for index, item in enumerate(
        _sequence(value.get("semantic_decisions"), field_name="semantic_decisions")
    ):
        if not isinstance(item, dict):
            raise RelocationError(f"semantic_decisions[{index}] must be a mapping")
        disposition = item.get("disposition")
        expected_keys = selector_keys | ({"replacement"} if disposition == "rewrite" else set())
        if set(item) != expected_keys or disposition not in {"rewrite", "preserve"}:
            raise RelocationError(f"semantic_decisions[{index}] has invalid keys")
        string_keys = {
            "occurrence_id",
            "mapping_kind",
            "mapping_id",
            "original_digest",
            "match",
            "text",
            "reason",
        }
        if disposition == "rewrite":
            if not isinstance(item.get("replacement"), str):
                raise RelocationError(
                    f"semantic_decisions[{index}].replacement must be a string"
                )
        if not all(isinstance(item.get(key), str) and item[key] for key in string_keys):
            raise RelocationError(f"semantic_decisions[{index}] strings must be non-empty")
        integer_keys = {"byte_start", "byte_end", "ordinal", "count"}
        if not all(isinstance(item.get(key), int) and item[key] >= 0 for key in integer_keys):
            raise RelocationError(f"semantic_decisions[{index}] selectors must be integers")
        if item["byte_end"] <= item["byte_start"] or item["ordinal"] < 1 or item["count"] < 1:
            raise RelocationError(f"semantic_decisions[{index}] span, ordinal, and count must be positive")
        occurrence_id = str(item["occurrence_id"])
        if occurrence_id in decision_ids:
            raise RelocationError(f"duplicate semantic decision occurrence ID: {occurrence_id}")
        decision_ids.add(occurrence_id)
        semantic_decisions.append(
            SemanticDecision(
                occurrence_id=occurrence_id,
                mapping_kind=str(item["mapping_kind"]),
                mapping_id=str(item["mapping_id"]),
                path=_repository_path(item["path"], field_name=f"semantic_decisions[{index}].path"),
                original_digest=str(item["original_digest"]),
                byte_start=int(item["byte_start"]),
                byte_end=int(item["byte_end"]),
                ordinal=int(item["ordinal"]),
                match=str(item["match"]),
                count=int(item["count"]),
                disposition=disposition,
                text=str(item["text"]),
                reason=str(item["reason"]),
                replacement=str(item["replacement"]) if disposition == "rewrite" else None,
            )
        )
        if disposition == "rewrite" and item["replacement"] == item["text"]:
            raise RelocationError(
                f"semantic_decisions[{index}] rewrite must change its enclosing text"
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

    boundaries: list[PackageBoundary] = []
    boundary_paths: set[str] = set()
    for index, item in enumerate(
        _sequence(value.get("package_boundaries"), field_name="package_boundaries")
    ):
        if not isinstance(item, dict):
            raise RelocationError(f"package_boundaries[{index}] must be a mapping")
        path_value = _repository_path(
            item.get("path"), field_name=f"package_boundaries[{index}].path"
        )
        if path_value in boundary_paths:
            raise RelocationError(f"duplicate package boundary path: {path_value}")
        boundary_paths.add(path_value)
        disposition = item.get("disposition")
        if disposition not in {
            "existing-module",
            "registered-module",
            "unregistered-package",
        }:
            raise RelocationError(
                f"package_boundaries[{index}].disposition is invalid: {disposition!r}"
            )
        module_id = item.get("module_id")
        blueprint = item.get("blueprint")
        if disposition == "registered-module":
            if not isinstance(module_id, str) or not module_id:
                raise RelocationError(
                    f"package_boundaries[{index}].module_id must be a non-empty string"
                )
            blueprint = _repository_path(
                blueprint, field_name=f"package_boundaries[{index}].blueprint"
            )
        else:
            module_id = None
            blueprint = None
        boundaries.append(
            PackageBoundary(
                path=path_value,
                disposition=disposition,
                module_id=module_id,
                blueprint=blueprint,
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
        relocations=tuple(moves),
        blueprint_documents=tuple(blueprint_documents),
        ownership_transfers=tuple(transfers),
        caller_additions=tuple(caller_additions),
        exact_rewrites=tuple(exact_rewrites),
        semantic_decisions=tuple(semantic_decisions),
        package_catalogs=tuple(catalogs),
        package_boundaries=tuple(boundaries),
        forbid_facade_imports=string_tuple("forbid_facade_imports"),
        text_exclusions=string_tuple("text_exclusions", paths=True),
        active_address_exclusions=string_tuple("active_address_exclusions", paths=True),
        inventory_exclusions=string_tuple("inventory_exclusions", paths=True),
        standard_digest_roots=string_tuple("standard_digest_roots", paths=True),
    )


def _replace_id_prefix(value: str, old: str, new: str) -> str | None:
    """Replace one complete leading dotted identity prefix."""

    if value == old:
        return new
    if value.startswith(old + "."):
        return new + value[len(old) :]
    return None


def _blueprint_id_inventory(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Read module, source, and interface IDs below one registered subtree."""

    modules: set[str] = set()
    sources: set[str] = set()
    interfaces: set[str] = set()
    candidates = [path / "blueprint.yaml"] if path.is_file() else sorted(path.rglob("*.yaml"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            value = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(value, Mapping) or value.get("schema_version") != 6:
            continue
        node_id = value.get("id")
        if isinstance(node_id, str):
            (modules if value.get("node_type") == "module" else sources).add(node_id)
        for key in ("exports", "interfaces"):
            records = value.get(key)
            if isinstance(records, Mapping):
                interfaces.update(item for item in records if isinstance(item, str))
    return modules, sources, interfaces


def _mapped_inventory(
    values: Iterable[str], *, old_prefix: str, new_prefix: str, target_state: bool
) -> tuple[Rename, ...]:
    """Convert IDs observed on either physical side into old-to-new mappings."""

    mapped: set[tuple[str, str]] = set()
    for value in values:
        if target_state:
            old = _replace_id_prefix(value, new_prefix, old_prefix)
            if old is not None and old != value:
                mapped.add((old, value))
        else:
            new = _replace_id_prefix(value, old_prefix, new_prefix)
            if new is not None and new != value:
                mapped.add((value, new))
    return tuple(Rename(old, new) for old, new in sorted(mapped))


def _owned_file_blueprints(root: Path, relative: str) -> tuple[str, ...]:
    """Return module blueprints whose content contract owns one exact file."""

    owners: list[str] = []
    for blueprint_path in sorted(root.rglob("blueprint.yaml")):
        if any(part in _CACHE_PARTS for part in blueprint_path.relative_to(root).parts):
            continue
        try:
            document = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(document, Mapping) or document.get("node_type") != "module":
            continue
        try:
            owned_relative = PurePosixPath(relative).relative_to(
                PurePosixPath(blueprint_path.parent.relative_to(root).as_posix())
            ).as_posix()
        except ValueError:
            continue
        content = document.get("content")
        if not isinstance(content, list):
            continue
        if any(
            isinstance(pattern, str) and re.fullmatch(pattern, owned_relative)
            for pattern in content
        ):
            owners.append(blueprint_path.relative_to(root).as_posix())
    return tuple(owners)


def _derive_identity_maps(root: Path, manifest: RelocationManifest) -> tuple[DerivedIdentityMap, ...]:
    """Resolve node relocations and retain owned-file physical mappings."""

    from ._relocation_addresses import AddressResolutionError, derive_relocations

    results: list[DerivedIdentityMap] = []
    for relocation in manifest.relocations:
        source = root / relocation.source
        target = root / relocation.target
        source_node = source.is_dir() and (source / "blueprint.yaml").is_file()
        target_node = target.is_dir() and (target / "blueprint.yaml").is_file()
        if not source_node and not target_node:
            configuration_present = (root / "officina.toml").is_file()
            existing_relative = (
                relocation.source if source.is_file() else relocation.target
            )
            existing_path = root / existing_relative
            if configuration_present:
                if not existing_path.is_file():
                    raise RelocationError(
                        "relocation endpoint is neither a registered node nor an owned file: "
                        f"{relocation.source} -> {relocation.target}"
                    )
                owners = _owned_file_blueprints(root, existing_relative)
                if len(owners) != 1:
                    raise RelocationError(
                        "owned-file relocation requires exactly one blueprint owner: "
                        f"{existing_relative}"
                    )
            results.append(
                DerivedIdentityMap(
                    relocation.source,
                    relocation.target,
                    python_modules=relocation.python_modules,
                )
            )
            continue
        try:
            derived = derive_relocations(root, (relocation,))[0]
        except AddressResolutionError as exc:
            raise RelocationError(str(exc)) from exc
        existing = target if target_node else source
        modules, sources, interfaces = _blueprint_id_inventory(existing)
        old_id = derived.source.node_id
        new_id = derived.target.node_id
        results.append(
            DerivedIdentityMap(
                source_path=relocation.source,
                target_path=relocation.target,
                source_node_id=old_id,
                target_node_id=new_id,
                source_root=derived.source.configured_root,
                target_root=derived.target.configured_root,
                module_ids=_mapped_inventory(
                    modules,
                    old_prefix=old_id,
                    new_prefix=new_id,
                    target_state=target_node,
                ),
                source_ids=_mapped_inventory(
                    sources,
                    old_prefix=old_id,
                    new_prefix=new_id,
                    target_state=target_node,
                ),
                interface_ids=_mapped_inventory(
                    interfaces,
                    old_prefix=old_id,
                    new_prefix=new_id,
                    target_state=target_node,
                ),
                python_modules=relocation.python_modules,
            )
        )
    return tuple(results)


@dataclass
class ChangeSet:
    """Hold one validated projected repository change set."""

    root: Path
    inventory_exclusions: tuple[str, ...] = _DEFAULT_INVENTORY_EXCLUSIONS
    moves: list[Move] = field(default_factory=list)
    writes: dict[str, bytes] = field(default_factory=dict)
    write_modes: dict[str, int] = field(default_factory=dict)
    deletes: set[str] = field(default_factory=set)
    expected: dict[str, bytes | None] = field(default_factory=dict)
    blueprint_changes: set[str] = field(default_factory=set)
    certification_basis_changes: set[str] = field(default_factory=set)
    digest_changes: set[str] = field(default_factory=set)
    generated_artifact_changes: set[str] = field(default_factory=set)
    validation_results: set[str] = field(default_factory=set)
    derived_relocations: tuple[DerivedIdentityMap, ...] = ()
    semantic_occurrences: list[object] = field(default_factory=list)
    skipped_text_files: list[object] = field(default_factory=list)
    semantic_decisions: list[object] = field(default_factory=list)
    unaccounted_semantic_occurrences: list[object] = field(default_factory=list)
    generated_spans: dict[str, tuple[tuple[int, int, str], ...]] = field(default_factory=dict)
    base_files: set[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Snapshot the repository inventory once for every projected-tree query."""

        self.base_files = set()
        for path in self.root.rglob("*"):
            if not path.is_file() and not path.is_symlink():
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
        disk_path = self.root / relative
        if disk_path.is_file():
            self.write_modes.setdefault(
                relative,
                stat.S_IMODE(disk_path.stat().st_mode),
            )
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
            "certification_basis_changes": sorted(self.certification_basis_changes),
            "digest_changes": sorted(self.digest_changes),
            "generated_artifact_changes": sorted(self.generated_artifact_changes),
            "validation_results": sorted(self.validation_results),
            "derived_relocations": [
                {
                    "from": item.source_path,
                    "to": item.target_path,
                    "source_node_id": item.source_node_id,
                    "target_node_id": item.target_node_id,
                }
                for item in self.derived_relocations
            ],
            "semantic_occurrences": [
                item.to_report() if hasattr(item, "to_report") else item
                for item in self.semantic_occurrences
            ],
            "skipped_text_files": [
                item.to_report() if hasattr(item, "to_report") else item
                for item in self.skipped_text_files
            ],
            "semantic_decisions": [
                item.to_report() if hasattr(item, "to_report") else item
                for item in self.semantic_decisions
            ],
            "unaccounted_semantic_occurrences": [
                item.to_report() if hasattr(item, "to_report") else item
                for item in self.unaccounted_semantic_occurrences
            ],
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
    for move in manifest.relocations:
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
            changes.write_modes[target_relative] = stat.S_IMODE(
                source_file.stat().st_mode
            )
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

    move_lookup = {move.source: move.target for move in manifest.relocations}
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


def _identity_renames(
    maps: Iterable[DerivedIdentityMap], manifest: RelocationManifest
) -> tuple[Rename, ...]:
    """Return the non-conflicting logical identity projection in longest-first order."""

    values: list[Rename] = []
    for item in maps:
        values.extend(item.module_ids)
        values.extend(item.source_ids)
        values.extend(item.interface_ids)
    for transfer in manifest.ownership_transfers:
        values.append(transfer.source)
        if transfer.export is not None:
            values.append(transfer.export)
    unique: dict[str, Rename] = {}
    for rename in values:
        prior = unique.get(rename.old)
        if prior is not None and prior.new != rename.new:
            raise RelocationError(f"conflicting derived identity mapping: {rename.old}")
        unique[rename.old] = rename
    return tuple(sorted(unique.values(), key=lambda item: len(item.old), reverse=True))


_IDENTITY_SCALAR_KEYS = {
    "id",
    "interface",
    "source_interface",
    "caller_module_id",
    "target_module_id",
    "module_id",
    "standard_id",
}
_IDENTITY_LIST_KEYS = {
    "allowed_callers",
    "dependencies",
    "setup_requires_setup_of",
}
_IDENTITY_MAPPING_KEYS = {
    "exports",
    "interfaces",
    "namespace_exports",
    "sources",
    "only",
}
_PATH_SCALAR_KEYS = {"path", "blueprint", "from_blueprint", "to_blueprint"}


def _rewrite_exact_identity(value: str, renames: tuple[Rename, ...]) -> str:
    for rename in renames:
        replaced = _replace_id_prefix(value, rename.old, rename.new)
        if replaced is not None:
            return replaced
    return value


def _rewrite_complete_path(value: str, path_renames: tuple[Rename, ...]) -> str:
    for rename in path_renames:
        if value == rename.old:
            return rename.new
        if value.startswith(rename.old.rstrip("/") + "/"):
            return rename.new.rstrip("/") + value[len(rename.old.rstrip("/")) :]
    return value


def _rewrite_blueprint_value(
    value: Any,
    *,
    field_name: str | None,
    identity_renames: tuple[Rename, ...],
    path_renames: tuple[Rename, ...],
) -> Any:
    """Rewrite only schema-known blueprint identity and path positions."""

    if isinstance(value, str):
        if field_name in _IDENTITY_SCALAR_KEYS or field_name in _IDENTITY_LIST_KEYS:
            return _rewrite_exact_identity(value, identity_renames)
        if field_name in _PATH_SCALAR_KEYS:
            return _rewrite_complete_path(value, path_renames)
        return value
    if isinstance(value, list):
        return [
            _rewrite_blueprint_value(
                item,
                field_name=field_name,
                identity_renames=identity_renames,
                path_renames=path_renames,
            )
            for item in value
        ]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        rewritten_key = (
            _rewrite_exact_identity(key, identity_renames)
            if field_name in _IDENTITY_MAPPING_KEYS and isinstance(key, str)
            else key
        )
        result[rewritten_key] = _rewrite_blueprint_value(
            item,
            field_name=str(key),
            identity_renames=identity_renames,
            path_renames=path_renames,
        )
    return result


def _project_parent_registrations(
    changes: ChangeSet, maps: Iterable[DerivedIdentityMap]
) -> None:
    """Move each registered node's direct parent-child edge."""

    for item in maps:
        if item.source_node_id is None or item.target_node_id is None:
            continue
        source_parts = item.source_node_id.split(".")
        target_parts = item.target_node_id.split(".")
        if len(source_parts) == 1 and len(target_parts) == 1:
            continue
        if item.source_root is None or item.target_root is None:
            raise RelocationError("derived node relocation is missing configured roots")
        old_parent = (
            PurePosixPath(item.source_root, *source_parts[:-1], "blueprint.yaml").as_posix()
            if len(source_parts) > 1
            else None
        )
        new_parent = (
            PurePosixPath(item.target_root, *target_parts[:-1], "blueprint.yaml").as_posix()
            if len(target_parts) > 1
            else None
        )
        if new_parent is not None and not changes.exists(new_parent):
            raise RelocationError(f"missing projected destination parent blueprint: {new_parent}")
        old_document = _yaml_mapping(changes, old_parent) if old_parent is not None else None
        new_document = (
            old_document if new_parent == old_parent else _yaml_mapping(changes, new_parent)
        ) if new_parent is not None else None
        record: Any = {}
        if old_document is not None:
            children = old_document.get("children")
            if not isinstance(children, dict) or source_parts[-1] not in children:
                target_children = (
                    new_document.get("children") if isinstance(new_document, Mapping) else None
                )
                if isinstance(target_children, Mapping) and target_parts[-1] in target_children:
                    continue
                raise RelocationError(
                    f"missing projected source parent registration: {old_parent}:{source_parts[-1]}"
                )
            record = children.pop(source_parts[-1])
        if new_document is not None:
            children = new_document.setdefault("children", {})
            if not isinstance(children, dict):
                raise RelocationError(f"invalid children mapping: {new_parent}")
            if target_parts[-1] in children and not (
                old_parent == new_parent and source_parts[-1] == target_parts[-1]
            ):
                raise RelocationError(
                    f"projected destination parent collision: {new_parent}:{target_parts[-1]}"
                )
            children[target_parts[-1]] = record
        if old_parent is not None and old_document is not None:
            changes.write_text(old_parent, _dump_yaml(old_document))
            changes.blueprint_changes.add(old_parent)
        if new_parent is not None and new_document is not None:
            changes.write_text(new_parent, _dump_yaml(new_document))
            changes.blueprint_changes.add(new_parent)


def _project_derived_blueprints(
    changes: ChangeSet,
    manifest: RelocationManifest,
    maps: tuple[DerivedIdentityMap, ...],
) -> None:
    """Project typed graph identities without touching prose-like YAML values."""

    identity_renames = _identity_renames(maps, manifest)
    path_renames = tuple(
        Rename(item.source_path, item.target_path)
        for item in sorted(maps, key=lambda value: len(value.source_path), reverse=True)
    )
    for relative in sorted(changes.projected_files()):
        if not relative.endswith((".yaml", ".yml")):
            continue
        try:
            document = _yaml_mapping(changes, relative)
        except (RelocationError, UnicodeDecodeError, yaml.YAMLError):
            continue
        rewritten = _rewrite_blueprint_value(
            document,
            field_name=None,
            identity_renames=identity_renames,
            path_renames=path_renames,
        )
        if rewritten != document:
            changes.write_text(relative, _dump_yaml(rewritten))
            changes.blueprint_changes.add(relative)
    _project_parent_registrations(changes, maps)


def _text_file(relative: str, exclusions: Iterable[str]) -> bool:
    path = PurePosixPath(relative)
    if any(part in _CACHE_PARTS for part in path.parts):
        return False
    if relative in exclusions:
        return False
    return path.suffix in _TEXT_SUFFIXES or not path.suffix


def _all_renames(manifest: RelocationManifest) -> list[Rename]:
    values = [Rename(move.source, move.target) for move in manifest.relocations]
    for relocation in manifest.relocations:
        values.extend(relocation.python_modules)
    unique: dict[str, Rename] = {}
    for rename in values:
        if rename.old in unique and unique[rename.old].new != rename.new:
            raise RelocationError(f"conflicting rename: {rename.old}")
        unique[rename.old] = rename
    return sorted(unique.values(), key=lambda item: len(item.old), reverse=True)


def _python_import_replacements(text: str, renames: tuple[Rename, ...]) -> list[tuple[int, int, bytes]]:
    """Return byte patches for absolute parsed Python import names only."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    raw = text.encode("utf-8")
    line_starts = [0]
    for match in re.finditer(b"\n", raw):
        line_starts.append(match.end())

    def absolute(line: int, column: int) -> int:
        return line_starts[line - 1] + column

    replacements: list[tuple[int, int, bytes]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                start = absolute(alias.lineno, alias.col_offset)
                old = alias.name.encode("utf-8")
                for rename in renames:
                    replacement = _replace_id_prefix(alias.name, rename.old, rename.new)
                    if replacement is not None and replacement != alias.name:
                        replacements.append((start, start + len(old), replacement.encode("utf-8")))
                        break
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            start = absolute(node.lineno, node.col_offset)
            end = absolute(node.end_lineno or node.lineno, node.end_col_offset or node.col_offset)
            segment = raw[start:end]
            marker = re.match(rb"from[ \t]+", segment)
            if marker is None:
                continue
            module_start = start + marker.end()
            for rename in renames:
                replacement = _replace_id_prefix(node.module, rename.old, rename.new)
                if replacement is not None and replacement != node.module:
                    replacements.append(
                        (
                            module_start,
                            module_start + len(node.module.encode("utf-8")),
                            replacement.encode("utf-8"),
                        )
                    )
                    break
    return replacements


def _apply_byte_replacements(payload: bytes, replacements: Iterable[tuple[int, int, bytes]]) -> bytes:
    """Apply non-overlapping byte patches from right to left."""

    result = payload
    last_start = len(payload) + 1
    for start, end, replacement in sorted(replacements, reverse=True):
        if end > last_start or start < 0 or end < start:
            raise RelocationError("overlapping structural rewrite spans")
        result = result[:start] + replacement + result[end:]
        last_start = start
    return result


def _dispatcher_replacements(
    text: str,
    *,
    identities: tuple[Rename, ...],
    paths: tuple[Rename, ...],
) -> str:
    """Rewrite only recognized dispatcher address arguments."""

    result_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if "dispatcher" not in line:
            result_lines.append(line)
            continue
        updated = line
        for rename in identities:
            token = re.compile(
                rf"(?<![A-Za-z0-9_.-]){re.escape(rename.old)}(?![A-Za-z0-9_.-])"
            )
            if ".interface." in rename.old:
                updated = token.sub(rename.new, updated)
            updated = re.sub(
                rf"(?P<prefix>--caller-skill(?:=|[ \t]+)){re.escape(rename.old)}(?![A-Za-z0-9_.-])",
                rf"\g<prefix>{rename.new}",
                updated,
            )
        for rename in paths:
            for flag in ("--manifest", "--report", "--repository-config", "--root"):
                updated = re.sub(
                    rf"(?P<prefix>{re.escape(flag)}(?:=|[ \t]+)){re.escape(rename.old)}(?=$|[ \t\r\n])",
                    rf"\g<prefix>{rename.new}",
                    updated,
                )
        result_lines.append(updated)
    return "".join(result_lines)


def _project_structural_code(
    changes: ChangeSet,
    manifest: RelocationManifest,
    maps: tuple[DerivedIdentityMap, ...],
) -> None:
    """Project parsed imports and recognized dispatcher addresses only."""

    python_renames = tuple(
        sorted(
            (rename for item in maps for rename in item.python_modules),
            key=lambda item: len(item.old),
            reverse=True,
        )
    )
    identity_renames = _identity_renames(maps, manifest)
    path_renames = tuple(
        Rename(item.source_path, item.target_path)
        for item in sorted(maps, key=lambda value: len(value.source_path), reverse=True)
    )
    for relative in sorted(changes.projected_files()):
        if not _text_file(relative, manifest.text_exclusions):
            continue
        try:
            text = changes.read_text(relative)
        except UnicodeDecodeError:
            continue
        payload = text.encode("utf-8")
        if relative.endswith(".py") and python_renames:
            payload = _apply_byte_replacements(
                payload,
                _python_import_replacements(text, python_renames),
            )
        updated = _dispatcher_replacements(
            payload.decode("utf-8"),
            identities=identity_renames,
            paths=path_renames,
        )
        if updated.encode("utf-8") != text.encode("utf-8"):
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


def _validate_package_boundary_declarations(
    changes: ChangeSet, manifest: RelocationManifest
) -> None:
    """Validate explicit registration policy for every newly projected package.

    A package boundary is new when a move or README-only catalog adds its
    ``__init__.py`` beyond the pre-move inventory. Each such path must have one
    declaration. Existing modules must resolve to a pre-move module blueprint;
    newly registered modules must match the declared projected blueprint and
    module id; unregistered packages must not project a module blueprint.

    Raises:
        RelocationError: If a new package has no declaration or a declaration
            conflicts with its pre-move or projected blueprint state.
    """

    declared = {boundary.path: boundary for boundary in manifest.package_boundaries}
    new_boundaries = {
        PurePosixPath(relative).parent.as_posix()
        for relative in changes.projected_files() - changes.base_files
        if PurePosixPath(relative).name == "__init__.py"
    }
    for path_value in sorted(new_boundaries - set(declared)):
        raise RelocationError(
            f"missing package boundary disposition; declare one for {path_value}"
        )

    for boundary in manifest.package_boundaries:
        default_blueprint = f"{boundary.path}/blueprint.yaml"
        if boundary.disposition == "existing-module":
            if default_blueprint not in changes.base_files:
                raise RelocationError(
                    "existing-module requires a pre-move module blueprint at "
                    f"{default_blueprint}"
                )
            blueprint = _yaml_mapping(changes, default_blueprint)
            if blueprint.get("node_type") != "module" or not isinstance(
                blueprint.get("id"), str
            ):
                raise RelocationError(
                    "existing-module requires node_type 'module' and a string id at "
                    f"{default_blueprint}"
                )
            continue

        if boundary.disposition == "registered-module":
            assert boundary.blueprint is not None
            assert boundary.module_id is not None
            if boundary.blueprint != default_blueprint:
                raise RelocationError(
                    "registered-module blueprint must equal "
                    f"{default_blueprint}; received {boundary.blueprint}"
                )
            if not changes.exists(boundary.blueprint):
                raise RelocationError(
                    "registered-module declaration requires projected blueprint "
                    f"{boundary.blueprint}"
                )
            blueprint = _yaml_mapping(changes, boundary.blueprint)
            if (
                blueprint.get("node_type") != "module"
                or blueprint.get("id") != boundary.module_id
            ):
                raise RelocationError(
                    "registered-module declaration requires node_type 'module' and "
                    f"id {boundary.module_id!r} at {boundary.blueprint}"
                )
            continue

        if changes.exists(default_blueprint):
            raise RelocationError(
                "unregistered-package cannot project a module blueprint at "
                f"{default_blueprint}"
            )


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
    syntax: list[str] = []
    facades: list[str] = []
    for relative in sorted(changes.projected_files()):
        if relative in excluded or not _text_file(relative, manifest.text_exclusions):
            continue
        try:
            text = changes.read_text(relative)
        except UnicodeDecodeError:
            continue
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
    failures = syntax + facades + init_errors
    if failures:
        raise RelocationError("projected-tree validation failed:\n" + "\n".join(failures[:100]))


def _decision_matches_occurrence(decision: SemanticDecision, occurrence: object) -> bool:
    """Return whether every concurrency-sensitive selector field is exact."""

    return all(
        getattr(occurrence, field) == expected
        for field, expected in (
            ("occurrence_id", decision.occurrence_id),
            ("mapping_kind", decision.mapping_kind),
            ("mapping_id", decision.mapping_id),
            ("path", decision.path),
            ("projected_digest", decision.original_digest),
            ("byte_start", decision.byte_start),
            ("byte_end", decision.byte_end),
            ("ordinal", decision.ordinal),
            ("match", decision.match),
        )
    )


def _enclosing_text_spans(payload: bytes, text: str) -> list[tuple[int, int]]:
    needle = text.encode("utf-8")
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = payload.find(needle, start)
        if found < 0:
            return spans
        spans.append((found, found + len(needle)))
        start = found + len(needle)


def _target_side_decision_matches(
    changes: ChangeSet,
    decision: SemanticDecision,
    occurrences: Iterable[object],
) -> bool:
    """Rematch one decision after apply without trusting obsolete byte spans."""

    try:
        payload = changes.read_bytes(decision.path)
    except RelocationError:
        return False
    matching = [
        item
        for item in occurrences
        if getattr(item, "path") == decision.path
        and getattr(item, "mapping_kind") == decision.mapping_kind
        and getattr(item, "mapping_id") == decision.mapping_id
        and getattr(item, "match") == decision.match
    ]
    if decision.disposition == "rewrite":
        assert decision.replacement is not None
        return (
            payload.count(decision.text.encode("utf-8")) == 0
            and payload.count(decision.replacement.encode("utf-8")) == decision.count
        )
    return (
        len(matching) >= 1
        and payload.count(decision.text.encode("utf-8")) == decision.count
    )


def _project_semantic_decisions(
    changes: ChangeSet,
    manifest: RelocationManifest,
    occurrences: tuple[object, ...],
) -> tuple[SemanticDecision, ...]:
    """Validate complete selectors and project reviewed rewrites together."""

    by_id = {getattr(item, "occurrence_id"): item for item in occurrences}
    accepted: list[SemanticDecision] = []
    source_spans: list[tuple[str, int, int]] = []
    rewrite_spans: dict[str, list[tuple[int, int, bytes]]] = {}
    for decision in manifest.semantic_decisions:
        occurrence = by_id.get(decision.occurrence_id)
        if occurrence is None:
            if _target_side_decision_matches(changes, decision, occurrences):
                accepted.append(decision)
                continue
            raise RelocationError(
                f"unknown semantic occurrence ID: {decision.occurrence_id}"
            )
        if not _decision_matches_occurrence(decision, occurrence):
            raise RelocationError(
                f"semantic decision selector mismatch: {decision.occurrence_id}"
            )
        if getattr(occurrence, "generated"):
            source = getattr(occurrence, "authored_source")
            suffix = f"; edit canonical authored source {source}" if source else ""
            raise RelocationError(
                f"semantic decision targets generated content: {decision.path}{suffix}"
            )
        for path, start, end in source_spans:
            if path == decision.path and decision.byte_start < end and decision.byte_end > start:
                raise RelocationError(
                    f"overlapping semantic decision selectors: {decision.path}"
                )
        source_spans.append((decision.path, decision.byte_start, decision.byte_end))
        payload = changes.read_bytes(decision.path)
        spans = _enclosing_text_spans(payload, decision.text)
        if len(spans) != decision.count:
            raise RelocationError(
                f"semantic decision replacement-count mismatch for {decision.path}: "
                f"expected {decision.count}, found {len(spans)}"
            )
        if not any(
            start <= decision.byte_start and decision.byte_end <= end
            for start, end in spans
        ):
            raise RelocationError(
                f"semantic decision text does not enclose selector: {decision.occurrence_id}"
            )
        if decision.disposition == "rewrite":
            assert decision.replacement is not None
            replacement = decision.replacement.encode("utf-8")
            rewrite_spans.setdefault(decision.path, []).extend(
                (start, end, replacement) for start, end in spans
            )
        accepted.append(decision)
    for path, replacements in rewrite_spans.items():
        unique = {(start, end): replacement for start, end, replacement in replacements}
        if len(unique) != len(replacements):
            raise RelocationError(f"duplicate semantic rewrite span: {path}")
        changes.write_bytes(
            path,
            _apply_byte_replacements(
                changes.read_bytes(path),
                ((start, end, replacement) for (start, end), replacement in unique.items()),
            ),
        )
    return tuple(accepted)


def _is_accounted_final_occurrence(
    changes: ChangeSet,
    occurrence: object,
    decisions: Iterable[SemanticDecision],
) -> bool:
    """Recognize preserved occurrences after other rewrites shift their spans."""

    for decision in decisions:
        if decision.disposition != "preserve":
            continue
        if (
            getattr(occurrence, "path") == decision.path
            and getattr(occurrence, "mapping_kind") == decision.mapping_kind
            and getattr(occurrence, "mapping_id") == decision.mapping_id
            and getattr(occurrence, "match") == decision.match
            and changes.read_bytes(decision.path).count(decision.text.encode("utf-8"))
            == decision.count
        ):
            return True
    return False


def plan_relocation(
    root: Path,
    manifest: RelocationManifest,
    *,
    synchronize: BlueprintSynchronizer | None = None,
) -> ChangeSet:
    """Build and validate a complete relocation without writing to disk."""

    root = root.resolve()
    if not root.is_dir():
        raise RelocationError(f"repository root does not exist: {root}")
    identity_maps = _derive_identity_maps(root, manifest)
    changes = ChangeSet(
        root=root,
        inventory_exclusions=tuple(
            dict.fromkeys(
                (*_DEFAULT_INVENTORY_EXCLUSIONS, *manifest.inventory_exclusions)
            )
        ),
        derived_relocations=identity_maps,
    )
    _project_moves(changes, manifest)
    _project_blueprints(changes, manifest)
    _project_derived_blueprints(changes, manifest, identity_maps)
    _project_structural_code(changes, manifest, identity_maps)
    _project_catalogs(changes, manifest)
    _validate_package_boundary_declarations(changes, manifest)
    _project_standard_digests(changes, manifest)
    from ._relocation_closure import MechanicalClosureError, close_projected_relocation

    try:
        closure = close_projected_relocation(
            changes,
            manifest,
            synchronize=synchronize,
        )
    except MechanicalClosureError as exc:
        raise RelocationError(str(exc)) from exc
    changes.certification_basis_changes.update(closure.certification_basis_changes)
    changes.generated_artifact_changes.update(closure.generated_artifact_changes)
    changes.validation_results.update(closure.validation_results)
    _validate_projected_tree(changes, manifest)
    from ._relocation_semantics import SemanticScan

    semantic = SemanticScan(changes).run()
    changes.semantic_occurrences.extend(semantic.occurrences)
    changes.skipped_text_files.extend(semantic.skipped_text_files)
    accepted = _project_semantic_decisions(
        changes,
        manifest,
        semantic.occurrences,
    )
    changes.semantic_decisions.extend(accepted)
    if manifest.semantic_decisions:
        try:
            closure = close_projected_relocation(
                changes,
                manifest,
                synchronize=synchronize,
            )
        except MechanicalClosureError as exc:
            raise RelocationError(str(exc)) from exc
        changes.certification_basis_changes.update(closure.certification_basis_changes)
        changes.generated_artifact_changes.update(closure.generated_artifact_changes)
        changes.validation_results.update(closure.validation_results)
        _validate_projected_tree(changes, manifest)
    final_semantic = SemanticScan(changes).run()
    accepted_ids = {decision.occurrence_id for decision in accepted}
    changes.unaccounted_semantic_occurrences.extend(
        occurrence
        for occurrence in semantic.occurrences
        if occurrence.occurrence_id not in accepted_ids
        and not _is_accounted_final_occurrence(changes, occurrence, accepted)
    )
    raw_keys = {
        (
            occurrence.path,
            occurrence.mapping_kind,
            occurrence.mapping_id,
            occurrence.byte_start,
            occurrence.byte_end,
        )
        for occurrence in semantic.occurrences
    }
    for occurrence in final_semantic.occurrences:
        key = (
            occurrence.path,
            occurrence.mapping_kind,
            occurrence.mapping_id,
            occurrence.byte_start,
            occurrence.byte_end,
        )
        if key in raw_keys:
            continue
        if not _is_accounted_final_occurrence(changes, occurrence, accepted):
            changes.unaccounted_semantic_occurrences.append(occurrence)
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
            if relative in changes.write_modes:
                temporary.chmod(changes.write_modes[relative])
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
