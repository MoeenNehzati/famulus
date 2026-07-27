"""Disposition reporting and the sole legacy-to-v4 blueprint converter."""

from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .blueprint_graph import (
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from .blueprint_inventory import collect_blueprints
from .atomic_files import (
    AtomicWriteError,
    atomic_replace_bytes,
    read_regular_file_bytes,
)
from .git_provenance import (
    GitMaterializationError,
    GitSnapshot,
    capture_git_snapshot,
    materialize_git_commit,
    pin_blueprint_v4_mechanical_commit,
    pin_blueprint_v4_source_overlay_commit,
    run_git,
    snapshot_head_matches,
    _tree_symlink_is_confined,
)
from .migration_candidate import (
    CutoverChange,
    MigrationCandidateError,
    atomic_candidate_write,
    candidate_commit,
    candidate_cutover_manifest,
)
from officina.runtime.python_machine_interface import (
    analyze_dispatch_call_declarations,
)


class InterfaceInjectionMigrationError(ValueError):
    pass


_DISPOSITIONS = frozenset({"add-direct-edge", "keep-uninjected", "retire"})


@dataclass(frozen=True)
class InterfaceMigrationEntry:
    interface_id: str
    disposition: str
    authored_consumers: tuple[str, ...]
    target_exists: bool


@dataclass(frozen=True)
class InterfaceInjectionMigrationReport:
    entries: tuple[InterfaceMigrationEntry, ...]

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "interfaces": [
                {
                    "interface": entry.interface_id,
                    "disposition": entry.disposition,
                    "authored_consumers": list(entry.authored_consumers),
                    "target_exists": entry.target_exists,
                }
                for entry in self.entries
            ],
        }


def build_interface_injection_migration_report(
    graph: RepositoryBlueprintGraph,
    legacy_union_exports: Iterable[str],
    dispositions: Mapping[str, str],
) -> InterfaceInjectionMigrationReport:
    """Require one explicit disposition for every formerly injected export."""

    legacy = list(legacy_union_exports)
    if len(set(legacy)) != len(legacy):
        raise InterfaceInjectionMigrationError(
            "legacy union contains duplicate interface IDs"
        )
    expected = set(legacy)
    supplied = set(dispositions)
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing dispositions: {missing}")
        if extra:
            details.append(f"unexpected dispositions: {extra}")
        raise InterfaceInjectionMigrationError("; ".join(details))

    consumers_by_target: dict[str, set[str]] = {}
    for edge in graph.node_edges:
        source = graph.nodes.get(edge.source_id)
        if edge.relation != "uses-interface" or source is None:
            continue
        if source.node_type != "llm-interface":
            continue
        consumers_by_target.setdefault(edge.target_id, set()).add(source.node_id)

    entries = []
    for interface_id in sorted(expected):
        disposition = dispositions[interface_id]
        if disposition not in _DISPOSITIONS:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: invalid disposition {disposition!r}"
            )
        target_exists = interface_id in graph.exports
        if disposition == "add-direct-edge" and not target_exists:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: add-direct-edge requires a target export"
            )
        entries.append(
            InterfaceMigrationEntry(
                interface_id=interface_id,
                disposition=disposition,
                authored_consumers=tuple(
                    sorted(consumers_by_target.get(interface_id, ()))
                ),
                target_exists=target_exists,
            )
        )
    return InterfaceInjectionMigrationReport(tuple(entries))


@dataclass(frozen=True)
class BlueprintDeclarationConversion:
    """Pure conversion plan; materialization is a separate guarded operation."""

    documents: Mapping[Path, dict[str, Any]]
    removed_paths: tuple[Path, ...]
    public_graph_projection: Mapping[str, object]
    runtime_dependency_projection: Mapping[str, object]
    behavioral_source_dependency_projection: Mapping[str, object]
    predecessor_semantic_edge_projection: Mapping[str, object] = field(
        default_factory=dict
    )
    predecessor_public_graph_projection: Mapping[str, object] = field(
        default_factory=dict
    )
    predecessor_runtime_dependency_projection: Mapping[str, object] = field(
        default_factory=dict
    )
    package_support_paths: tuple[Path, ...] = ()
    findings: tuple[MigrationFinding, ...] = ()

    @property
    def declaration_paths(self) -> tuple[Path, ...]:
        """Planned declaration replacements/removals, not the candidate delta."""

        return tuple(sorted(set(self.documents) | set(self.removed_paths)))


@dataclass(frozen=True)
class BlueprintMigrationMapValidation:
    mapped_declaration_paths: tuple[Path, ...]
    non_live_local_paths: tuple[Path, ...]
    schema_file_count: int = 0
    field_occurrence_count: int = 0
    public_id_count: int = 0


@dataclass(frozen=True)
class BlueprintV4Candidate:
    root: Path
    conversion: BlueprintDeclarationConversion
    graph: RepositoryBlueprintGraph | None = None
    source_commit: str | None = None
    legacy_commit: str | None = None
    source_overlay_commit: str | None = None
    commit: str | None = None
    inspection: Mapping[str, object] | None = None
    cutover_manifest: tuple["CutoverChange", ...] = ()
    cutover_paths: tuple[Path, ...] = ()
    atomic_guarantee: bool = True


@dataclass(frozen=True)
class MigrationFinding:
    code: str
    source_path: Path
    field: str
    message: str
    target_id: str | None = None
    claim: str | None = None


@dataclass(frozen=True)
class ActiveReferenceFinding:
    code: str
    path: Path
    line: int
    reference: str

    def as_document(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path.as_posix(),
            "line": self.line,
            "reference": self.reference,
        }


@dataclass(frozen=True)
class CompiledMigrationPlan:
    module_renames: Mapping[str, str]
    local_source_includes: tuple[Path, ...]
    literal_rewrite_paths: tuple[Path, ...] = ()
    public_id_literal_paths: tuple[Path, ...] = ()
    authorized_overlay: Mapping[Path, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceMaterializationSnapshot:
    git: GitSnapshot
    entries: Mapping[Path, tuple[str, bytes, int]]
    porcelain: bytes = b""
    index: bytes = b""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise InterfaceInjectionMigrationError(
                f"unhashable YAML mapping key at line {key_node.start_mark.line + 1}"
            ) from exc
        if duplicate:
            raise InterfaceInjectionMigrationError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_blueprint_migration_map(path: Path) -> dict[str, Any]:
    """Load the authoritative map while preserving its duplicate-key contract."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise InterfaceInjectionMigrationError(
            f"migration map is not a regular file: {candidate}"
        )
    try:
        document = yaml.load(candidate.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except InterfaceInjectionMigrationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InterfaceInjectionMigrationError(f"invalid migration map: {exc}") from exc
    if not isinstance(document, dict):
        raise InterfaceInjectionMigrationError("migration map must be a mapping")
    return document


@dataclass(frozen=True)
class _InterfaceInput:
    old_id: str
    local_name: str
    version: int
    description: str | None
    usage: str | None
    gateway_path: str
    gateway_language: str
    process_entry: str | None
    has_process_binding: bool
    args_prefix: tuple[str, ...]
    patterns: tuple[dict[str, Any], ...]
    platform_support: dict[str, Any] | None
    runtime_dependencies: tuple[dict[str, Any], ...] | None
    uses_interfaces: tuple[dict[str, Any], ...]
    direct_io: dict[str, Any]
    owns_filesystem: tuple[dict[str, Any], ...]
    allow_all_modules: bool
    allowed_callers: tuple[str, ...]
    same_source_content: tuple[str, ...]
    behavior_evidence: tuple[dict[str, Any], ...]
    source_dependencies: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _BehavioralSourceInput:
    old_id: str
    version: int
    description: str | None
    gateway_path: str
    gateway_language: str
    dependencies: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ReferenceSourceTarget:
    module_id: str
    source_id: str
    version: int
    module_root: Path
    blueprint_path: Path
    gateway_path: str
    gateway_language: str
    legacy: dict[str, Any]


@dataclass(frozen=True)
class _LegacyBehaviorDependencyMapping:
    consumer: str
    authored: dict[str, Any]
    target: dict[str, Any]


_IO_SECTIONS = ("reads", "writes", "network")
_IO_PRESERVED_FIELDS = (
    "medium",
    "access",
    "system",
    "content",
    "auth",
    "sensitivity",
    "path",
    "path_match",
    "reason",
    "endpoint",
)


def _require_relative_path(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise InterfaceInjectionMigrationError(f"{context}: expected nonempty path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise InterfaceInjectionMigrationError(
            f"{context}: path must be relative and traversal-free"
        )
    return value


_ACTIVE_REFERENCE_TEXT_MARKERS = (
    ("legacy-public-interface-namespace", ".machine."),
    ("legacy-public-interface-namespace", ".llm."),
    ("legacy-node-kind", "machine-module"),
    ("legacy-node-kind", "machine_module"),
    ("legacy-node-kind", "llm-interface"),
    ("legacy-node-kind", "llm_interface"),
    ("legacy-node-kind", "behavior-source"),
    ("legacy-certifier-name", "skill-audit"),
    ("legacy-certifier-name", "skill_audit"),
    ("legacy-audit-document", "audit_and_drift"),
    ("legacy-binding-owner", "machine_interface_binding"),
)

_ACTIVE_REFERENCE_PATH_MARKERS = (
    ("legacy-health-authority", "references/blueprint/health.schema.json"),
    (
        "legacy-admissibility-authority",
        "references/blueprint/interface-admissibility",
    ),
    (
        "legacy-conformance-authority",
        "references/blueprint/interface-conformance.schema.json",
    ),
    (
        "legacy-conformance-authority",
        "references/blueprint/conformance-boundary-operations",
    ),
    (
        "legacy-conformance-authority",
        "references/blueprint/conformance-operations/",
    ),
    ("legacy-audit-document", "docs/audit_and_drift.md"),
)

_MIGRATION_EVIDENCE_PATHS = frozenset(
    {
        Path("docs/plans/unified-architecture-migration.md"),
        Path("docs/plans/unified-architecture-migration-map.yaml"),
        Path("scripts/migrate-blueprints-v4.py"),
        Path("src/officina/common/interface_injection_migration.py"),
        Path("tests/test_interface_injection_migration.py"),
    }
)

_NON_ACTIVE_EXECUTION_STATUSES = frozenset(
    {"frozen_history", "deferred_pending_approved_post_adoption_rebase"}
)


def _historical_reference_patterns(
    migration_map: Mapping[str, Any],
) -> tuple[str, ...]:
    documents = migration_map.get("documents", [])
    if not isinstance(documents, list):
        raise InterfaceInjectionMigrationError(
            "migration-map documents must be a list"
        )
    patterns: list[str] = []
    for index, entry in enumerate(documents):
        if not isinstance(entry, Mapping):
            raise InterfaceInjectionMigrationError(
                f"documents[{index}] must be a mapping"
            )
        target = entry.get("target", "")
        excluded = (
            entry.get("execution_status") in _NON_ACTIVE_EXECUTION_STATUSES
            or isinstance(target, str)
            and "historical evidence" in target
        )
        if not excluded:
            continue
        matcher = entry.get("paths")
        raw_patterns = (
            matcher
            if isinstance(matcher, list)
            else matcher.get("include")
            if isinstance(matcher, Mapping)
            else None
        )
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise InterfaceInjectionMigrationError(
                f"documents[{index}].paths must declare include patterns"
            )
        for pattern_index, value in enumerate(raw_patterns):
            patterns.append(
                _require_relative_path(
                    value,
                    f"documents[{index}].paths.include[{pattern_index}]",
                )
            )
    return tuple(sorted(set(patterns)))


def _matches_any_reference_pattern(path: Path, patterns: Sequence[str]) -> bool:
    candidate = PurePosixPath(path.as_posix())
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if candidate.as_posix() == prefix or candidate.as_posix().startswith(
                prefix + "/"
            ):
                return True
        elif candidate.match(pattern):
            return True
    return False


def check_active_migration_references(
    repo_root: Path,
    migration_map: Mapping[str, Any],
) -> tuple[ActiveReferenceFinding, ...]:
    """Find legacy v4-migration references outside classified evidence."""

    raw_root = Path(repo_root)
    if raw_root.is_symlink():
        raise InterfaceInjectionMigrationError("repository root must not be a symlink")
    root = raw_root.resolve()
    tracked = run_git(root, "ls-files", "-z", check=False)
    if tracked.returncode != 0:
        raise InterfaceInjectionMigrationError(
            "active-reference check requires a Git worktree"
        )
    try:
        tracked_paths = tuple(
            sorted(
                Path(item)
                for item in tracked.stdout.decode("utf-8").split("\0")
                if item
            )
        )
    except UnicodeError as exc:
        raise InterfaceInjectionMigrationError(
            "tracked path inventory is not UTF-8"
        ) from exc

    historical_patterns = _historical_reference_patterns(migration_map)
    findings: list[ActiveReferenceFinding] = []
    for relative in tracked_paths:
        if relative in _MIGRATION_EVIDENCE_PATHS or _matches_any_reference_pattern(
            relative, historical_patterns
        ):
            continue
        path_text = relative.as_posix()
        folded_path_text = path_text.casefold()
        candidate = root / relative
        if not candidate.exists():
            continue
        for code, marker in _ACTIVE_REFERENCE_PATH_MARKERS:
            if marker.casefold() in folded_path_text:
                findings.append(
                    ActiveReferenceFinding(code, relative, 0, marker.rsplit("/", 1)[-1])
                )
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            data = read_regular_file_bytes(candidate, allowed_root=root)
        except (AtomicWriteError, OSError) as exc:
            raise InterfaceInjectionMigrationError(
                f"cannot read tracked file during active-reference check: {path_text}"
            ) from exc
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            folded_line = line.casefold()
            for code, marker in _ACTIVE_REFERENCE_TEXT_MARKERS:
                if marker.casefold() in folded_line:
                    findings.append(
                        ActiveReferenceFinding(
                            code,
                            relative,
                            line_number,
                            marker,
                        )
                    )
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.path.as_posix(),
                finding.line,
                finding.code,
                finding.reference,
            ),
        )
    )


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(
            path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InterfaceInjectionMigrationError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InterfaceInjectionMigrationError(f"{path}: blueprint must be a mapping")
    return value


def _reviewed_generated_field_ignore_entries(
    migration_map: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    declarations = (
        migration_map.get("declarations")
        if isinstance(migration_map, Mapping)
        else None
    )
    mechanical = (
        declarations.get("mechanical_conversion")
        if isinstance(declarations, Mapping)
        else None
    )
    entries = (
        mechanical.get("reviewed_generated_field_ignores", [])
        if isinstance(mechanical, Mapping)
        else []
    )
    if not isinstance(entries, list):
        raise InterfaceInjectionMigrationError(
            "reviewed_generated_field_ignores must be a list"
        )
    validated: list[Mapping[str, Any]] = []
    for index, entry in enumerate(entries):
        context = f"reviewed_generated_field_ignores[{index}]"
        if not isinstance(entry, Mapping):
            raise InterfaceInjectionMigrationError(
                f"{context}: invalid reviewed generated-field ignore"
            )
        entry_path = _require_relative_path(entry.get("path"), context)
        parts = PurePosixPath(entry_path).parts
        if (
            len(parts) != 3
            or parts[0] != "skills"
            or not parts[1]
            or parts[2] != "blueprint.yaml"
        ):
            raise InterfaceInjectionMigrationError(
                f"{context}: invalid reviewed generated-field ignore path"
            )
        module_id = parts[1]
        interface_ids = entry.get("interface_ids")
        exact_value = entry.get("exact_value")
        if (
            entry.get("disposition") != "ignore"
            or entry.get("field") != "uses_interfaces"
            or not isinstance(interface_ids, list)
            or not interface_ids
            or not all(isinstance(value, str) and value for value in interface_ids)
            or len(set(interface_ids)) != len(interface_ids)
            or not isinstance(exact_value, list)
            or not exact_value
            or not all(isinstance(value, str) and value for value in exact_value)
        ):
            raise InterfaceInjectionMigrationError(
                f"{context}: invalid reviewed generated-field ignore"
            )
        allowed_prefixes = (f"{module_id}.machine.", f"{module_id}.llm.")
        for interface_id in interface_ids:
            if not interface_id.startswith(allowed_prefixes):
                raise InterfaceInjectionMigrationError(
                    f"{context}: ignored interface {interface_id!r} does not "
                    f"belong to {module_id}"
                )
        validated.append(entry)
    return tuple(validated)


def _validate_reviewed_generated_field_ignore_consumption(
    paths: Sequence[Path],
    migration_map: Mapping[str, Any] | None,
) -> None:
    path_counts = Counter(path.as_posix() for path in paths)
    for entry in _reviewed_generated_field_ignore_entries(migration_map):
        if path_counts.get(str(entry["path"]), 0) != 1:
            raise InterfaceInjectionMigrationError(
                f"{entry['path']}: reviewed generated-field ignore was not "
                "consumed exactly once"
            )


def _apply_reviewed_generated_field_ignores(
    path: Path,
    declaration: Mapping[str, Any],
    migration_map: Mapping[str, Any] | None,
) -> dict[str, Any]:
    cleaned = deepcopy(dict(declaration))
    entries = _reviewed_generated_field_ignore_entries(migration_map)
    if cleaned.get("schema_version") == 4:
        return cleaned
    for entry in entries:
        if entry["path"] != path.as_posix():
            continue
        field = str(entry["field"])
        interface_ids = entry["interface_ids"]
        exact_value = entry["exact_value"]
        interfaces = cleaned.get("interfaces")
        if not isinstance(interfaces, dict):
            raise InterfaceInjectionMigrationError(
                f"{path.as_posix()}: reviewed generated-field ignore is unresolved"
            )
        for interface_id in interface_ids:
            marker = ".machine." if ".machine." in interface_id else ".llm."
            if marker not in interface_id:
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: invalid ignored interface ID {interface_id!r}"
                )
            _, local_name = interface_id.split(marker, 1)
            family = marker.strip(".")
            family_declarations = interfaces.get(family)
            item = (
                family_declarations.get(local_name)
                if isinstance(family_declarations, dict)
                else None
            )
            if not isinstance(item, dict) or item.get(field) != exact_value:
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: reviewed generated-field ignore does not "
                    f"match {interface_id}.{field}"
                )
            item[field] = []
    return cleaned


def compile_migration_plan(migration_map: Mapping[str, Any]) -> CompiledMigrationPlan:
    """Compile the map facts used by path and public-ID conversion."""

    declarations = migration_map.get("declarations")
    version_2 = declarations.get("version_2") if isinstance(declarations, Mapping) else None
    decisions = version_2.get("merge_decisions") if isinstance(version_2, Mapping) else None
    if decisions is None:
        decisions = []
    if not isinstance(decisions, list):
        raise InterfaceInjectionMigrationError(
            "declarations.version_2.merge_decisions must be a list"
        )
    renames: dict[str, str] = {}
    targets: set[str] = set()
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping) or "target_module" not in decision:
            continue
        inputs = decision.get("inputs")
        target = decision.get("target_module")
        if (
            not isinstance(inputs, list)
            or not inputs
            or not isinstance(target, str)
            or not target
        ):
            raise InterfaceInjectionMigrationError(
                f"merge_decisions[{index}]: invalid module rename"
            )
        normalized_inputs = [
            Path(_require_relative_path(path, f"merge_decisions[{index}].inputs"))
            for path in inputs
        ]
        owners = {
            path.parts[1]
            for path in normalized_inputs
            if path.parts[:1] == ("skills",) and len(path.parts) >= 3
        }
        if len(owners) != 1:
            raise InterfaceInjectionMigrationError(
                f"merge_decisions[{index}]: module rename inputs have ambiguous ownership"
            )
        owner = owners.pop()
        if owner in renames or target in targets or owner == target:
            raise InterfaceInjectionMigrationError(
                f"merge_decisions[{index}]: duplicate or ineffective module rename"
            )
        renames[owner] = target
        targets.add(target)

    source_policy = migration_map.get("candidate_source", {})
    if not isinstance(source_policy, Mapping):
        raise InterfaceInjectionMigrationError("candidate_source must be a mapping")
    unknown = set(source_policy) - {
        "local_includes",
        "module_rename_literal_files",
        "public_id_literal_files",
        "authorized_overlay",
    }
    if unknown:
        raise InterfaceInjectionMigrationError(
            f"candidate_source has unknown fields {sorted(unknown)}"
        )
    raw_includes = source_policy.get("local_includes", [])
    if not isinstance(raw_includes, list):
        raise InterfaceInjectionMigrationError(
            "candidate_source.local_includes must be a list"
        )
    includes = tuple(
        Path(_require_relative_path(value, "candidate_source.local_includes"))
        for value in raw_includes
    )
    if len(includes) != len(set(includes)):
        raise InterfaceInjectionMigrationError(
            "candidate_source.local_includes contains duplicates"
        )
    raw_literal_paths = source_policy.get("module_rename_literal_files", [])
    if not isinstance(raw_literal_paths, list):
        raise InterfaceInjectionMigrationError(
            "candidate_source.module_rename_literal_files must be a list"
        )
    literal_paths = tuple(
        Path(
            _require_relative_path(
                value, "candidate_source.module_rename_literal_files"
            )
        )
        for value in raw_literal_paths
    )
    if len(literal_paths) != len(set(literal_paths)):
        raise InterfaceInjectionMigrationError(
            "candidate_source.module_rename_literal_files contains duplicates"
        )
    raw_public_id_paths = source_policy.get("public_id_literal_files", [])
    if not isinstance(raw_public_id_paths, list):
        raise InterfaceInjectionMigrationError(
            "candidate_source.public_id_literal_files must be a list"
        )
    public_id_paths = tuple(
        Path(
            _require_relative_path(
                value, "candidate_source.public_id_literal_files"
            )
        )
        for value in raw_public_id_paths
    )
    if len(public_id_paths) != len(set(public_id_paths)):
        raise InterfaceInjectionMigrationError(
            "candidate_source.public_id_literal_files contains duplicates"
        )
    raw_overlay = source_policy.get("authorized_overlay", [])
    if not isinstance(raw_overlay, list):
        raise InterfaceInjectionMigrationError(
            "candidate_source.authorized_overlay must be a list"
        )
    overlay: dict[Path, str] = {}
    for index, entry in enumerate(raw_overlay):
        if not isinstance(entry, Mapping) or set(entry) != {"path", "state"}:
            raise InterfaceInjectionMigrationError(
                f"candidate_source.authorized_overlay[{index}] is invalid"
            )
        path = Path(
            _require_relative_path(
                entry.get("path"), f"candidate_source.authorized_overlay[{index}].path"
            )
        )
        state = entry.get("state")
        if state not in {"modified", "added", "deleted"} or path in overlay:
            raise InterfaceInjectionMigrationError(
                f"candidate_source.authorized_overlay[{index}] is invalid"
            )
        overlay[path] = state
    if set(includes) - {path for path, state in overlay.items() if state == "added"}:
        raise InterfaceInjectionMigrationError(
            "candidate_source.local_includes require authorized added overlay entries"
        )
    return CompiledMigrationPlan(
        dict(sorted(renames.items())),
        tuple(sorted(includes)),
        tuple(sorted(literal_paths)),
        tuple(sorted(public_id_paths)),
        dict(sorted(overlay.items())),
    )


def validate_post_adoption_migration_map(
    migration_map: Mapping[str, Any],
) -> None:
    """Validate map invariants that remain meaningful after the atomic cutover."""

    if migration_map.get("map_version") != 1:
        raise InterfaceInjectionMigrationError(
            "post-adoption migration map requires map_version 1"
        )
    authority = migration_map.get("authority")
    version_contract = (
        authority.get("version_contract")
        if isinstance(authority, Mapping)
        else None
    )
    if (
        not isinstance(version_contract, Mapping)
        or version_contract.get("final_runtime_schema_version") != 4
    ):
        raise InterfaceInjectionMigrationError(
            "post-adoption migration map requires final runtime schema version 4"
        )
    compile_migration_plan(migration_map)
    _historical_reference_patterns(migration_map)


def _target_module_id(
    module_id: str, migration_plan: CompiledMigrationPlan | None = None
) -> str:
    return (
        migration_plan.module_renames.get(module_id, module_id)
        if migration_plan is not None
        else module_id
    )


def _rename_interface_id(
    interface_id: object,
    migration_plan: CompiledMigrationPlan | None = None,
) -> str:
    if not isinstance(interface_id, str):
        raise InterfaceInjectionMigrationError("interface edge requires a string ID")
    for marker in (".machine.", ".llm."):
        if marker in interface_id:
            owner, local = interface_id.split(marker, 1)
            return f"{_target_module_id(owner, migration_plan)}.interface.{local}"
    if ".source." in interface_id:
        owner, local = interface_id.split(".source.", 1)
        target_owner = _target_module_id(owner, migration_plan)
        if target_owner != owner:
            return f"{target_owner}.source.{local}"
    return interface_id


def _source_slug(gateway_path: str) -> str:
    if gateway_path == "SKILL.md":
        return "gateway"
    path = PurePosixPath(gateway_path)
    without_suffix = path.with_suffix("").as_posix()
    slug = re.sub(r"[/_]+", "-", without_suffix.lstrip("._/"))
    slug = re.sub(r"-+", "-", slug).strip("-").lower()
    if not slug or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) is None:
        raise InterfaceInjectionMigrationError(
            f"cannot derive canonical source slug from {gateway_path!r}"
        )
    return slug


def _exact_content_pattern(path: str) -> str:
    return re.escape(path)


def _canonical_direct_io(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InterfaceInjectionMigrationError(f"{context}: direct_io must be a mapping")
    if set(value) != set(_IO_SECTIONS):
        raise InterfaceInjectionMigrationError(
            f"{context}: direct_io requires exactly reads, writes, and network"
        )
    converted: dict[str, Any] = {}
    for section in _IO_SECTIONS:
        entries = value[section]
        if not isinstance(entries, list):
            raise InterfaceInjectionMigrationError(
                f"{context}.{section}: expected a list"
            )
        output_entries: list[dict[str, Any]] = []
        prefix = section.removesuffix("s")
        for index, raw in enumerate(entries, 1):
            if not isinstance(raw, dict):
                raise InterfaceInjectionMigrationError(
                    f"{context}.{section}[{index - 1}]: expected a mapping"
                )
            allowed = {*_IO_PRESERVED_FIELDS, "id", "format", "formats"}
            unknown = sorted(set(raw) - allowed)
            if unknown:
                raise InterfaceInjectionMigrationError(
                    f"{context}.{section}[{index - 1}]: unmapped fields {unknown}"
                )
            if "format" in raw and "formats" in raw:
                raise InterfaceInjectionMigrationError(
                    f"{context}.{section}[{index - 1}]: format and formats conflict"
                )
            entry: dict[str, Any] = {"id": f"{prefix}-{index}"}
            for field in _IO_PRESERVED_FIELDS:
                if field in raw:
                    entry[field] = deepcopy(raw[field])
            if "format" in raw:
                entry["formats"] = [raw["format"]]
            elif "formats" in raw:
                entry["formats"] = deepcopy(raw["formats"])
            if "path" in entry and "path_match" not in entry:
                entry["path_match"] = "exact"
            output_entries.append(entry)
        converted[section] = output_entries
    return converted


def _legacy_behavior_evidence(
    value: object, context: str
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise InterfaceInjectionMigrationError(
            f"{context}: behavior_sources must be a list"
        )
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "content",
            "format",
            "reason",
        }:
            raise InterfaceInjectionMigrationError(
                f"{context}: invalid behavior source evidence"
            )
        _require_relative_path(item.get("path"), context)
        if any(
            not isinstance(item.get(field), str) or not item[field]
            for field in ("content", "format", "reason")
        ):
            raise InterfaceInjectionMigrationError(
                f"{context}: incomplete behavior source evidence"
            )
        normalized.append(deepcopy(item))
    return tuple(normalized)


def _canonical_patterns(
    patterns: object, context: str
) -> tuple[dict[str, Any], ...]:
    if not isinstance(patterns, list) or not all(
        isinstance(item, dict) for item in patterns
    ):
        raise InterfaceInjectionMigrationError(f"{context}: invalid patterns")
    normalized: list[dict[str, Any]] = []
    for item in patterns:
        pattern = deepcopy(item)
        positional = pattern.get("positional_patterns")
        if positional is not None:
            if not isinstance(positional, dict):
                raise InterfaceInjectionMigrationError(
                    f"{context}: invalid positional_patterns"
                )
            converted: dict[str, Any] = {}
            for index, expression in positional.items():
                if (
                    isinstance(index, bool)
                    or not isinstance(index, (str, int))
                    or not str(index).isdigit()
                    or not isinstance(expression, str)
                    or not expression
                ):
                    raise InterfaceInjectionMigrationError(
                        f"{context}: invalid positional pattern"
                    )
                key = str(index)
                if key in converted:
                    raise InterfaceInjectionMigrationError(
                        f"{context}: duplicate positional pattern {key}"
                    )
                converted[key] = expression
            pattern["positional_patterns"] = converted
        normalized.append(pattern)
    return tuple(normalized)


def _legacy_gateway(
    declaration: Mapping[str, Any], *, interface_id: str
) -> tuple[
    str,
    str,
    str | None,
    tuple[str, ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    invocation = declaration.get("invocation")
    if isinstance(invocation, dict):
        if invocation.get("kind") != "python_machine_interface":
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: unknown invocation kind {invocation.get('kind')!r}"
            )
        entrypoint = invocation.get("entrypoint")
        if not isinstance(entrypoint, str) or ":" not in entrypoint:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: invalid Python entrypoint"
            )
        path, entry = entrypoint.rsplit(":", 1)
        path = _require_relative_path(path, f"{interface_id}.invocation.entrypoint")
        behavior = invocation.get("behavior_sources", [])
        behavior_evidence = _legacy_behavior_evidence(
            behavior, f"{interface_id}.invocation.behavior_sources"
        )
        args = invocation.get("args_prefix", [])
        patterns = declaration.get("patterns", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise InterfaceInjectionMigrationError(f"{interface_id}: invalid args_prefix")
        return (
            path,
            "Python",
            entry,
            tuple(args),
            _canonical_patterns(patterns, interface_id),
            behavior_evidence,
        )
    binding = declaration.get("binding")
    if isinstance(binding, dict):
        kind = binding.get("kind")
        if kind == "skill_file":
            path = binding.get("path")
        elif kind == "markdown_file":
            path = binding.get("path")
        else:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: unknown gateway kind {kind!r}"
            )
        return (
            _require_relative_path(path, f"{interface_id}.binding.path"),
            "Markdown",
            None,
            (),
            (),
            (),
        )
    file_path = declaration.get("file")
    if isinstance(file_path, str):
        return (
            _require_relative_path(file_path, f"{interface_id}.file"),
            "Markdown",
            None,
            (),
            (),
            (),
        )
    raise InterfaceInjectionMigrationError(f"{interface_id}: gateway is absent")


def _unversioned_interfaces(
    module_id: str, declaration: Mapping[str, Any]
) -> tuple[_InterfaceInput, ...]:
    raw_interfaces = declaration.get("interfaces")
    if not isinstance(raw_interfaces, dict):
        raise InterfaceInjectionMigrationError(f"{module_id}: interfaces must be a mapping")
    records: list[_InterfaceInput] = []
    for family in ("machine", "llm"):
        family_declarations = raw_interfaces.get(family, {})
        if not isinstance(family_declarations, dict):
            raise InterfaceInjectionMigrationError(
                f"{module_id}: interfaces.{family} must be a mapping"
            )
        for local_name, item in sorted(family_declarations.items()):
            if not isinstance(local_name, str) or not isinstance(item, dict):
                raise InterfaceInjectionMigrationError(
                    f"{module_id}: invalid {family} interface declaration"
                )
            old_id = f"{module_id}.{family}.{local_name}"
            gateway = _legacy_gateway(item, interface_id=old_id)
            uses = item.get("uses_interfaces", [])
            ownership = item.get("owns_filesystem", [])
            callers = item.get("allowed_callers", [])
            if not isinstance(uses, list) or not all(
                isinstance(edge, dict) for edge in uses
            ):
                raise InterfaceInjectionMigrationError(
                    f"{old_id}: invalid uses_interfaces"
                )
            if not isinstance(ownership, list) or not all(
                isinstance(entry, dict) for entry in ownership
            ):
                raise InterfaceInjectionMigrationError(f"{old_id}: invalid owns_filesystem")
            if not isinstance(callers, list) or not all(
                isinstance(caller, str) for caller in callers
            ):
                raise InterfaceInjectionMigrationError(f"{old_id}: invalid allowed_callers")
            version = item.get("version")
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise InterfaceInjectionMigrationError(f"{old_id}: invalid version")
            dependencies = item.get("dependencies") if family == "machine" else None
            platform_support = item.get("platform_support") if family == "machine" else None
            if dependencies is not None and (
                not isinstance(dependencies, list)
                or not all(isinstance(entry, dict) for entry in dependencies)
            ):
                raise InterfaceInjectionMigrationError(f"{old_id}: invalid dependencies")
            interface_behavior = _legacy_behavior_evidence(
                item.get("behavior_sources", []),
                f"{old_id}.behavior_sources",
            )
            records.append(
                _InterfaceInput(
                    old_id=old_id,
                    local_name=local_name,
                    version=version,
                    description=(
                        item.get("description")
                        if isinstance(item.get("description"), str)
                        else None
                    ),
                    usage=item.get("usage") if isinstance(item.get("usage"), str) else None,
                    gateway_path=gateway[0],
                    gateway_language=gateway[1],
                    process_entry=gateway[2],
                    has_process_binding=family == "machine",
                    args_prefix=gateway[3],
                    patterns=gateway[4],
                    platform_support=(
                        deepcopy(platform_support)
                        if isinstance(platform_support, dict)
                        else None
                    ),
                    runtime_dependencies=(
                        tuple(deepcopy(dependencies))
                        if isinstance(dependencies, list)
                        else None
                    ),
                    uses_interfaces=tuple(deepcopy(uses)),
                    direct_io=_canonical_direct_io(
                        item.get("direct_io"), f"{old_id}.direct_io"
                    ),
                    owns_filesystem=tuple(deepcopy(ownership)),
                    allow_all_modules=item.get("allow_all_skills") is True,
                    allowed_callers=tuple(callers),
                    same_source_content=(),
                    behavior_evidence=tuple(
                        deepcopy((*gateway[5], *interface_behavior))
                    ),
                    source_dependencies=(),
                )
            )
    return tuple(records)


def _typed_gateway(
    declaration: Mapping[str, Any], *, interface_id: str
) -> tuple[str, str, str | None, bool, tuple[str, ...], tuple[dict[str, Any], ...]]:
    binding = declaration.get("binding")
    if not isinstance(binding, dict):
        raise InterfaceInjectionMigrationError(f"{interface_id}: binding is absent")
    kind = binding.get("kind")
    path = _require_relative_path(
        binding.get("path"), f"{interface_id}.binding.path"
    )
    patterns = declaration.get("patterns", [])
    args = binding.get("args_prefix", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise InterfaceInjectionMigrationError(f"{interface_id}: invalid args_prefix")
    if kind == "instruction-file":
        return path, "Markdown", None, False, (), ()
    if kind == "python-entrypoint":
        entry = binding.get("symbol")
        if not isinstance(entry, str) or not entry:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: Python symbol is absent"
            )
        return path, "Python", entry, True, tuple(args), _canonical_patterns(
            patterns, interface_id
        )
    if kind == "command-file":
        return path, "Shell", None, True, tuple(args), _canonical_patterns(
            patterns, interface_id
        )
    raise InterfaceInjectionMigrationError(
        f"{interface_id}: unknown gateway kind {kind!r}"
    )


def _typed_interface_input(declaration: Mapping[str, Any]) -> _InterfaceInput:
    old_id = declaration.get("id")
    if not isinstance(old_id, str):
        raise InterfaceInjectionMigrationError("typed interface requires an ID")
    marker = next(
        (candidate for candidate in (".machine.", ".llm.") if candidate in old_id),
        None,
    )
    if marker is None:
        raise InterfaceInjectionMigrationError(
            f"{old_id}: typed interface ID has no machine or llm namespace"
        )
    family = marker.strip(".")
    local_name = old_id.split(marker, 1)[1]
    gateway = _typed_gateway(declaration, interface_id=old_id)
    version = declaration.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid version")
    uses = declaration.get("uses_interfaces", [])
    dependencies = declaration.get("dependencies") if family == "machine" else None
    behavior_sources = declaration.get("behavior_sources", [])
    ownership = declaration.get("owns_filesystem", [])
    callers = declaration.get("allowed_callers", [])
    local_inputs = declaration.get("local_hash_inputs", [])
    for name, value in (
        ("uses_interfaces", uses),
        ("behavior_sources", behavior_sources),
        ("owns_filesystem", ownership),
    ):
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise InterfaceInjectionMigrationError(f"{old_id}: invalid {name}")
    if not isinstance(callers, list) or not all(isinstance(item, str) for item in callers):
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid allowed_callers")
    if not isinstance(local_inputs, list) or not all(
        isinstance(item, str) for item in local_inputs
    ):
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid local_hash_inputs")
    if dependencies is not None and (
        not isinstance(dependencies, list)
        or not all(isinstance(item, dict) for item in dependencies)
    ):
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid dependencies")
    return _InterfaceInput(
        old_id=old_id,
        local_name=local_name,
        version=version,
        description=(
            declaration.get("description")
            if isinstance(declaration.get("description"), str)
            else None
        ),
        usage=(
            declaration.get("usage")
            if isinstance(declaration.get("usage"), str)
            else None
        ),
        gateway_path=gateway[0],
        gateway_language=gateway[1],
        process_entry=gateway[2],
        has_process_binding=gateway[3],
        args_prefix=gateway[4],
        patterns=gateway[5],
        platform_support=(
            deepcopy(declaration.get("platform_support"))
            if family == "machine"
            and isinstance(declaration.get("platform_support"), dict)
            else None
        ),
        runtime_dependencies=(
            tuple(deepcopy(dependencies))
            if isinstance(dependencies, list)
            else None
        ),
        uses_interfaces=tuple(deepcopy(uses)),
        direct_io=_canonical_direct_io(
            declaration.get("direct_io"), f"{old_id}.direct_io"
        ),
        owns_filesystem=tuple(deepcopy(ownership)),
        allow_all_modules=declaration.get("allow_all_skills") is True,
        allowed_callers=tuple(callers),
        same_source_content=tuple(
            _require_relative_path(item, f"{old_id}.local_hash_inputs")
            for item in local_inputs
        ),
        behavior_evidence=(),
        source_dependencies=tuple(deepcopy(behavior_sources)),
    )


def _typed_default_interface_input(
    module_id: str, declaration: Mapping[str, Any]
) -> _InterfaceInput | None:
    default = declaration.get("default_interface")
    if default is None:
        return None
    old_id = f"{module_id}.llm.default"
    if not isinstance(default, dict):
        raise InterfaceInjectionMigrationError(
            f"{old_id}: default_interface must be a mapping"
        )
    version = default.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid version")
    uses = default.get("uses_interfaces", [])
    behavior_sources = default.get("behavior_sources", [])
    ownership = default.get("owns_filesystem", [])
    for name, value in (
        ("uses_interfaces", uses),
        ("behavior_sources", behavior_sources),
        ("owns_filesystem", ownership),
    ):
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise InterfaceInjectionMigrationError(f"{old_id}: invalid {name}")
    return _InterfaceInput(
        old_id=old_id,
        local_name="default",
        version=version,
        description=(
            default.get("description")
            if isinstance(default.get("description"), str)
            else None
        ),
        usage=default.get("usage") if isinstance(default.get("usage"), str) else None,
        gateway_path="SKILL.md",
        gateway_language="Markdown",
        process_entry=None,
        has_process_binding=False,
        args_prefix=(),
        patterns=(),
        platform_support=None,
        runtime_dependencies=None,
        uses_interfaces=tuple(deepcopy(uses)),
        direct_io=_canonical_direct_io(
            default.get("direct_io"), f"{old_id}.direct_io"
        ),
        owns_filesystem=tuple(deepcopy(ownership)),
        allow_all_modules=default.get("allow_all_skills") is True,
        allowed_callers=(),
        same_source_content=(),
        behavior_evidence=(),
        source_dependencies=tuple(deepcopy(behavior_sources)),
    )


def _typed_behavioral_source_input(
    declaration: Mapping[str, Any], *, module_id: str
) -> _BehavioralSourceInput:
    old_id = declaration.get("id")
    version = declaration.get("version")
    binding = declaration.get("binding")
    if not isinstance(old_id, str) or not old_id.startswith(f"{module_id}.source."):
        raise InterfaceInjectionMigrationError(
            f"{module_id}: behavioral source has a non-owned ID"
        )
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InterfaceInjectionMigrationError(f"{old_id}: invalid version")
    if not isinstance(binding, dict) or binding.get("kind") != "file":
        raise InterfaceInjectionMigrationError(f"{old_id}: unknown gateway kind")
    path = _require_relative_path(binding.get("path"), f"{old_id}.binding.path")
    authored_format = declaration.get("format")
    language = (
        "Markdown"
        if authored_format == "markdown"
        else str(authored_format)
        if isinstance(authored_format, str) and authored_format
        else "text"
    )
    dependencies = declaration.get("uses_behavior_sources", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, dict) for item in dependencies
    ):
        raise InterfaceInjectionMigrationError(
            f"{old_id}: invalid uses_behavior_sources"
        )
    return _BehavioralSourceInput(
        old_id=old_id,
        version=version,
        description=(
            declaration.get("description")
            if isinstance(declaration.get("description"), str)
            else None
        ),
        gateway_path=path,
        gateway_language=language,
        dependencies=tuple(deepcopy(dependencies)),
    )


def _legacy_declaration_inputs(
    declarations: Mapping[Path, Mapping[str, Any]],
) -> tuple[
    dict[str, tuple[Path, Mapping[str, Any]]],
    dict[str, tuple[_InterfaceInput, ...]],
    dict[str, tuple[_BehavioralSourceInput, ...]],
]:
    """Parse only the mapped legacy declarations needed by the converter."""

    roots: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    typed_interfaces: dict[str, list[_InterfaceInput]] = {}
    typed_sources: dict[str, list[_BehavioralSourceInput]] = {}
    for relative, declaration in declarations.items():
        if len(relative.parts) < 3 or relative.parts[0] != "skills":
            raise InterfaceInjectionMigrationError(
                f"mapped declaration has no module owner: {relative.as_posix()}"
            )
        module_id = relative.parts[1]
        if relative == Path("skills") / module_id / "blueprint.yaml":
            schema_version = declaration.get("schema_version")
            if schema_version not in (None, 2):
                raise InterfaceInjectionMigrationError(
                    f"{relative.as_posix()}: unsupported root schema version"
                )
            if schema_version == 2 and declaration.get("blueprint_type") != "skill":
                raise InterfaceInjectionMigrationError(
                    f"{relative.as_posix()}: version 2 root must be a skill"
                )
            if schema_version == 2 and declaration.get("id") != module_id:
                raise InterfaceInjectionMigrationError(
                    f"{relative.as_posix()}: root ID does not match its owner"
                )
            roots[module_id] = (relative, declaration)
        elif declaration.get("schema_version") is None:
            raise InterfaceInjectionMigrationError(
                f"unversioned declaration must be a module root: {relative.as_posix()}"
            )
        elif declaration.get("schema_version") == 2:
            blueprint_type = declaration.get("blueprint_type")
            if blueprint_type in {"llm-interface", "machine-interface"}:
                typed_interfaces.setdefault(module_id, []).append(
                    _typed_interface_input(declaration)
                )
            elif blueprint_type == "behavior-source":
                typed_sources.setdefault(module_id, []).append(
                    _typed_behavioral_source_input(
                        declaration, module_id=module_id
                    )
                )
            else:
                raise InterfaceInjectionMigrationError(
                    f"{relative.as_posix()}: unsupported version 2 node type"
                )
        else:
            raise InterfaceInjectionMigrationError(
                f"{relative.as_posix()}: unsupported schema version"
            )

    missing_roots = sorted((set(typed_interfaces) | set(typed_sources)) - set(roots))
    if missing_roots:
        raise InterfaceInjectionMigrationError(
            f"mapped sidecars have no module root: {missing_roots}"
        )
    return (
        roots,
        {
            module_id: tuple(entries)
            for module_id, entries in typed_interfaces.items()
        },
        {
            module_id: tuple(entries)
            for module_id, entries in typed_sources.items()
        },
    )


def _legacy_module_interface_inputs(
    roots: Mapping[str, tuple[Path, Mapping[str, Any]]],
    typed_interfaces: Mapping[str, Sequence[_InterfaceInput]],
    migration_map: Mapping[str, Any] | None,
) -> dict[str, tuple[_InterfaceInput, ...]]:
    """Combine embedded and sidecar interfaces for conversion."""

    result: dict[str, tuple[_InterfaceInput, ...]] = {}
    for old_module_id, (_root_path, declaration) in sorted(roots.items()):
        interfaces = (
            _unversioned_interfaces(old_module_id, declaration)
            if declaration.get("schema_version") is None
            else ()
        )
        interfaces += tuple(typed_interfaces.get(old_module_id, ()))
        default_interface = _typed_default_interface_input(
            old_module_id, declaration
        )
        if default_interface is not None:
            interfaces += (default_interface,)
        by_old_id: dict[str, _InterfaceInput] = {}
        for interface in interfaces:
            previous = by_old_id.get(interface.old_id)
            if previous is not None:
                if not _duplicate_interface_merge_is_reviewed(
                    migration_map,
                    module_id=old_module_id,
                    gateway_path=interface.gateway_path,
                ):
                    raise InterfaceInjectionMigrationError(
                        f"{interface.old_id}: ambiguous duplicate declarations"
                    )
                by_old_id[interface.old_id] = _merge_interface_inputs(
                    previous, interface
                )
            else:
                by_old_id[interface.old_id] = interface
        result[old_module_id] = tuple(
            by_old_id[key] for key in sorted(by_old_id)
        )
    return result


def _regular_module_content(
    module_root: Path,
    excluded_blueprints: set[Path],
    allowed_source_paths: set[Path] | None = None,
) -> list[str]:
    transient_directories = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
    content: list[str] = []
    for path in sorted(module_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if allowed_source_paths is not None and path.resolve() not in allowed_source_paths:
            continue
        relative = path.relative_to(module_root)
        if path.resolve() in excluded_blueprints:
            continue
        name = path.name
        if (
            any(part in transient_directories for part in relative.parts)
            or name == "blueprint.yaml"
            or name.endswith(".blueprint.yaml")
            or (
                relative.parent.name == "blueprints"
                and relative.suffix in {".yaml", ".yml"}
            )
            or ".certificates" in relative.parts
            or ".certificate-history" in relative.parts
            or name == ".last_audit.json"
            or name.endswith(".audit.json")
            or name.endswith(".health.json")
            or "pooled-blueprint-review" in name
        ):
            continue
        content.append(_exact_content_pattern(relative.as_posix()))
    if not content:
        raise InterfaceInjectionMigrationError(
            f"{module_root}: module has no regular non-blueprint content"
        )
    return content


def _normalize_uses(
    edges: Sequence[Mapping[str, Any]],
    migration_plan: CompiledMigrationPlan | None = None,
) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, int], dict[str, Any]] = {}
    for edge in edges:
        interface_id = _rename_interface_id(edge.get("interface"), migration_plan)
        version = edge.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise InterfaceInjectionMigrationError(
                f"{interface_id}: interface edge requires a positive version"
            )
        normalized[(interface_id, version)] = {
            "interface": interface_id,
            "version": version,
        }
    return [normalized[key] for key in sorted(normalized)]


def _normalize_ownership(
    entries: Sequence[Mapping[str, Any]],
    migration_plan: CompiledMigrationPlan | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in entries:
        entry = deepcopy(dict(raw))
        readers = entry.get("allowed_readers")
        if not isinstance(readers, list):
            raise InterfaceInjectionMigrationError(
                "filesystem ownership requires allowed_readers"
            )
        entry["allowed_readers"] = sorted(
            _rename_interface_id(item, migration_plan) for item in readers
        )
        normalized.append(entry)
    return normalized


def _normalize_source_dependencies(
    edges: Sequence[Mapping[str, Any]],
    source_targets: Mapping[str, tuple[str, Path, _BehavioralSourceInput]],
    *,
    owner_module_id: str,
) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, int, str], dict[str, Any]] = {}
    for edge in edges:
        old_source = edge.get("source")
        version = edge.get("version")
        reason = edge.get("reason")
        if not isinstance(old_source, str) or old_source not in source_targets:
            raise InterfaceInjectionMigrationError(
                f"unresolved behavioral source {old_source!r}"
            )
        source_id, target_path, source = source_targets[old_source]
        if version != source.version:
            raise InterfaceInjectionMigrationError(
                f"{old_source}: dependency version does not match its source"
            )
        if not isinstance(reason, str) or not reason:
            raise InterfaceInjectionMigrationError(
                f"{old_source}: dependency reason is absent"
            )
        target_module = target_path.parts[1]
        if target_module == owner_module_id:
            locator = {
                "base": "module-root",
                "path": target_path.relative_to(
                    Path("skills") / owner_module_id
                ).as_posix(),
            }
        else:
            locator = {"base": "repository-root", "path": target_path.as_posix()}
        normalized[(source_id, version, reason)] = {
            "source": source_id,
            "version": version,
            "blueprint": locator,
            "reason": reason,
        }
    return [normalized[key] for key in sorted(normalized)]


def _merge_mapping_tuples(
    first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    values: dict[str, dict[str, Any]] = {}
    for item in (*first, *second):
        key = yaml.safe_dump(dict(item), sort_keys=True)
        values[key] = deepcopy(dict(item))
    return tuple(values[key] for key in sorted(values))


def _merge_direct_io(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for section in _IO_SECTIONS:
        values: dict[str, dict[str, Any]] = {}
        for raw in (*first[section], *second[section]):
            item = {key: deepcopy(value) for key, value in raw.items() if key != "id"}
            key = yaml.safe_dump(item, sort_keys=True)
            values[key] = item
        prefix = section.removesuffix("s")
        merged[section] = [
            {"id": f"{prefix}-{index}", **values[key]}
            for index, key in enumerate(sorted(values), 1)
        ]
    return merged


def _merge_interface_inputs(
    first: _InterfaceInput, second: _InterfaceInput
) -> _InterfaceInput:
    exact_fields = (
        "old_id",
        "local_name",
        "version",
        "gateway_path",
        "gateway_language",
        "process_entry",
        "has_process_binding",
        "args_prefix",
        "patterns",
        "platform_support",
        "runtime_dependencies",
        "allow_all_modules",
        "allowed_callers",
    )
    mismatched = [
        field
        for field in exact_fields
        if getattr(first, field) != getattr(second, field)
    ]
    for field in ("description", "usage"):
        left = getattr(first, field)
        right = getattr(second, field)
        if left is not None and right is not None and left != right:
            mismatched.append(field)
    if mismatched:
        raise InterfaceInjectionMigrationError(
            f"{first.old_id}: contradictory duplicate fields {sorted(mismatched)}"
        )
    return replace(
        first,
        description=first.description or second.description,
        usage=first.usage if first.usage is not None else second.usage,
        uses_interfaces=_merge_mapping_tuples(
            first.uses_interfaces, second.uses_interfaces
        ),
        direct_io=_merge_direct_io(first.direct_io, second.direct_io),
        owns_filesystem=_merge_mapping_tuples(
            first.owns_filesystem, second.owns_filesystem
        ),
        same_source_content=tuple(
            sorted(set(first.same_source_content) | set(second.same_source_content))
        ),
        behavior_evidence=_merge_mapping_tuples(
            first.behavior_evidence, second.behavior_evidence
        ),
        source_dependencies=_merge_mapping_tuples(
            first.source_dependencies, second.source_dependencies
        ),
    )


def _duplicate_interface_merge_is_reviewed(
    migration_map: Mapping[str, Any] | None,
    *,
    module_id: str,
    gateway_path: str,
) -> bool:
    if migration_map is None:
        return False
    decisions = (
        migration_map.get("declarations", {})
        .get("version_2", {})
        .get("merge_decisions", [])
    )
    if not isinstance(decisions, list):
        return False
    expected = f"skills/{module_id}/blueprints/{_source_slug(gateway_path)}.yaml"
    return any(
        isinstance(decision, dict) and decision.get("target") == expected
        for decision in decisions
    )


def _mechanical_conversion_section(
    migration_map: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if migration_map is None:
        return {}
    declarations = migration_map.get("declarations", {})
    if not isinstance(declarations, dict):
        raise InterfaceInjectionMigrationError(
            "migration-map declarations must be a mapping"
        )
    section = declarations.get("mechanical_conversion", {})
    if not isinstance(section, dict):
        raise InterfaceInjectionMigrationError(
            "declarations.mechanical_conversion must be a mapping"
        )
    return section


_PYTHON_PACKAGE_SUPPORT_POLICY = {
    "initializer_disposition": "create-behavioral-source",
    "sibling_python_disposition": "same-source-unless-claimed",
    "nested_packages": "distinct-support-sources",
    "imported_source_dependencies": "ast-resolved-exact",
    "non_python_and_blueprint_files": "exclude",
    "collision_behavior": "reject",
    "created_path_predecessor": "initializer-and-unclaimed-direct-python-siblings",
    "import_search_roots": {
        "default": ["module-root"],
        "by_gateway": {
            "skills/install-assistant-tools/_rtx/_agent_launchers.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/install-assistant-tools/_rtx/_install_scaffold.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/install-assistant-tools/_rtx/_phase_entry.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_healthcheck_probe.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_job_control.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_setup_runner.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/recurring-tasks/_rtx/_unit_writer.py": [
                "gateway-parent",
                "module-root",
            ],
            "skills/skill-drift/_rtx/_check_drift_state.py": [
                "gateway-parent",
                "module-root",
            ]
        },
    },
}


def _require_python_package_support_policy(
    migration_map: Mapping[str, Any],
) -> None:
    policy = _mechanical_conversion_section(migration_map).get(
        "python_package_support"
    )
    if policy != _PYTHON_PACKAGE_SUPPORT_POLICY:
        raise InterfaceInjectionMigrationError(
            "declarations.mechanical_conversion.python_package_support must "
            "authorize the exact supported conversion policy"
        )


def _git_path_matches(
    repo_root: Path, path: Path, *arguments: str
) -> bool:
    result = run_git(
        repo_root, *arguments, "--", path.as_posix(), check=False
    )
    return result.returncode == 0


def _matcher_document(value: object, context: str) -> dict[str, object]:
    if isinstance(value, list):
        return {"include": value, "exclude": [], "allow_empty": False}
    if not isinstance(value, dict):
        raise InterfaceInjectionMigrationError(f"{context}: invalid path matcher")
    unknown = set(value) - {"include", "exclude", "allow_empty"}
    if unknown:
        raise InterfaceInjectionMigrationError(
            f"{context}: unknown path matcher fields {sorted(unknown)}"
        )
    include = value.get("include")
    exclude = value.get("exclude", [])
    allow_empty = value.get("allow_empty", False)
    if (
        not isinstance(include, list)
        or not include
        or not isinstance(exclude, list)
        or not isinstance(allow_empty, bool)
    ):
        raise InterfaceInjectionMigrationError(f"{context}: invalid path matcher")
    return {"include": include, "exclude": exclude, "allow_empty": allow_empty}


def _normalized_pattern(value: object, context: str) -> str:
    path = _require_relative_path(value, context)
    if path.startswith("./") or "//" in path or "/./" in path:
        raise InterfaceInjectionMigrationError(
            f"{context}: path pattern is not normalized"
        )
    return path


def _expand_path_matcher(
    repo_root: Path, matcher: object, context: str
) -> set[Path]:
    document = _matcher_document(matcher, context)
    allow_empty = bool(document["allow_empty"])
    included: set[Path] = set()
    for index, raw_pattern in enumerate(document["include"]):
        pattern = _normalized_pattern(raw_pattern, f"{context}.include[{index}]")
        matches = {
            path.relative_to(repo_root)
            for path in repo_root.glob(pattern)
            if not path.is_symlink() and path.exists()
        }
        if not matches and not allow_empty:
            raise InterfaceInjectionMigrationError(
                f"{context}: include pattern matched no current path: {pattern}"
            )
        included.update(matches)
    excluded: set[Path] = set()
    for index, raw_pattern in enumerate(document["exclude"]):
        pattern = _normalized_pattern(raw_pattern, f"{context}.exclude[{index}]")
        excluded.update(
            path.relative_to(repo_root)
            for path in repo_root.glob(pattern)
            if not path.is_symlink() and path.exists()
        )
    return included - excluded


def _json_pointer_segment(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _schema_field_occurrences(
    path: Path,
) -> tuple[str, ...]:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise InterfaceInjectionMigrationError(
                    f"{path}: duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
        )
    except InterfaceInjectionMigrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InterfaceInjectionMigrationError(f"cannot load schema {path}: {exc}") from exc

    occurrences: list[str] = []

    def visit(value: object, pointer: str) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                for name in properties:
                    occurrences.append(
                        f"{pointer}/properties/{_json_pointer_segment(name)}"
                    )
            for key, child in value.items():
                visit(child, f"{pointer}/{_json_pointer_segment(key)}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{pointer}/{index}")

    visit(document, "")
    return tuple(occurrences)


def _validate_schema_field_coverage(
    repo_root: Path, migration_map: Mapping[str, Any]
) -> tuple[int, int]:
    coverage = migration_map.get("coverage_contract")
    schema_inventory = migration_map.get("schema_inventory")
    if not isinstance(coverage, dict) or not isinstance(schema_inventory, dict):
        raise InterfaceInjectionMigrationError(
            "migration map requires coverage_contract and schema_inventory"
        )
    field_enumeration = coverage.get("field_enumeration")
    groups = schema_inventory.get("field_groups")
    if not isinstance(field_enumeration, dict) or not isinstance(groups, list):
        raise InterfaceInjectionMigrationError(
            "migration map requires field enumeration and field groups"
        )
    schema_paths = _expand_path_matcher(
        repo_root,
        field_enumeration.get("schema_paths"),
        "coverage_contract.field_enumeration.schema_paths",
    )
    if not all(path.is_file() for path in schema_paths):
        raise InterfaceInjectionMigrationError("schema path matcher selected a non-file")
    occurrences = {
        path: _schema_field_occurrences(repo_root / path)
        for path in sorted(schema_paths)
    }
    expected_files = field_enumeration.get("observed_schema_files")
    expected_fields = field_enumeration.get("observed_field_occurrences")
    actual_fields = sum(len(values) for values in occurrences.values())
    if expected_files != len(schema_paths):
        raise InterfaceInjectionMigrationError(
            "observed schema file count changed: "
            f"expected {expected_files}, found {len(schema_paths)}"
        )
    if expected_fields != actual_fields:
        raise InterfaceInjectionMigrationError(
            "observed schema field occurrence count changed: "
            f"expected {expected_fields}, found {actual_fields}"
        )

    normalized_groups: list[tuple[str, set[Path], tuple[str, ...], bool]] = []
    seen_ids: set[str] = set()
    for index, raw_group in enumerate(groups):
        if not isinstance(raw_group, dict):
            raise InterfaceInjectionMigrationError(
                f"schema_inventory.field_groups[{index}] must be a mapping"
            )
        group_id = raw_group.get("id")
        if not isinstance(group_id, str) or not group_id or group_id in seen_ids:
            raise InterfaceInjectionMigrationError(
                f"schema_inventory.field_groups[{index}] has invalid or duplicate id"
            )
        seen_ids.add(group_id)
        disposition = raw_group.get("disposition")
        target = raw_group.get("target")
        if disposition not in {"preserve", "move", "rename", "derive", "retire"}:
            raise InterfaceInjectionMigrationError(f"{group_id}: invalid disposition")
        if not isinstance(target, str) or not target:
            raise InterfaceInjectionMigrationError(f"{group_id}: target is absent")
        selected_paths = _expand_path_matcher(
            repo_root,
            raw_group.get("schema_paths"),
            f"schema_inventory.field_groups[{group_id}].schema_paths",
        )
        if not selected_paths <= schema_paths:
            raise InterfaceInjectionMigrationError(
                f"{group_id}: field group selects an unobserved schema"
            )
        raw_prefixes = raw_group.get("pointer_prefixes", [])
        if not isinstance(raw_prefixes, list) or not all(
            isinstance(prefix, str) and prefix.startswith("/")
            for prefix in raw_prefixes
        ):
            raise InterfaceInjectionMigrationError(
                f"{group_id}: invalid pointer_prefixes"
            )
        fallback = raw_group.get("fallback", False)
        if not isinstance(fallback, bool) or fallback == bool(raw_prefixes):
            raise InterfaceInjectionMigrationError(
                f"{group_id}: specify either pointer_prefixes or fallback"
            )
        normalized_groups.append(
            (group_id, selected_paths, tuple(raw_prefixes), fallback)
        )

    prefix_hits: dict[tuple[str, str], int] = {}
    group_hits = {group_id: 0 for group_id, *_ in normalized_groups}
    for schema_path, pointers in occurrences.items():
        for pointer in pointers:
            specific: list[str] = []
            for group_id, selected_paths, prefixes, fallback in normalized_groups:
                if fallback or schema_path not in selected_paths:
                    continue
                matched_prefixes = [
                    prefix
                    for prefix in prefixes
                    if pointer == prefix or pointer.startswith(prefix + "/")
                ]
                if matched_prefixes:
                    specific.append(group_id)
                    for prefix in matched_prefixes:
                        prefix_hits[(group_id, prefix)] = (
                            prefix_hits.get((group_id, prefix), 0) + 1
                        )
            if len(specific) > 1:
                raise InterfaceInjectionMigrationError(
                    f"{schema_path.as_posix()}:{pointer}: overlapping field groups {specific}"
                )
            if specific:
                group_hits[specific[0]] += 1
                continue
            fallbacks = [
                group_id
                for group_id, selected_paths, _prefixes, fallback in normalized_groups
                if fallback and schema_path in selected_paths
            ]
            if len(fallbacks) != 1:
                raise InterfaceInjectionMigrationError(
                    f"{schema_path.as_posix()}:{pointer}: expected one field disposition, "
                    f"found {fallbacks}"
                )
            group_hits[fallbacks[0]] += 1

    for group_id, _paths, prefixes, fallback in normalized_groups:
        if group_hits[group_id] == 0:
            raise InterfaceInjectionMigrationError(
                f"{group_id}: field group matched no field occurrence"
            )
        for prefix in prefixes:
            if prefix_hits.get((group_id, prefix), 0) == 0:
                raise InterfaceInjectionMigrationError(
                    f"{group_id}: pointer prefix matched no field occurrence: {prefix}"
                )
    return len(schema_paths), actual_fields


def _legacy_public_id_inventory(
    repo_root: Path, mapped: Sequence[Path]
) -> tuple[set[str], set[str], set[str]]:
    machine_ids: set[str] = set()
    llm_ids: set[str] = set()
    source_ids: set[str] = set()
    for relative in mapped:
        declaration = _read_mapping(repo_root / relative)
        schema_version = declaration.get("schema_version")
        if schema_version == 2:
            blueprint_type = declaration.get("blueprint_type")
            node_id = declaration.get("id")
            if (
                blueprint_type == "skill"
                and isinstance(node_id, str)
                and isinstance(declaration.get("default_interface"), dict)
            ):
                llm_ids.add(f"{node_id}.llm.default")
            elif blueprint_type == "machine-interface" and isinstance(node_id, str):
                machine_ids.add(node_id)
            elif blueprint_type == "llm-interface" and isinstance(node_id, str):
                llm_ids.add(node_id)
            elif blueprint_type == "behavior-source" and isinstance(node_id, str):
                source_ids.add(node_id)
            continue
        if isinstance(schema_version, int):
            continue
        module_id = relative.parts[1]
        interfaces = declaration.get("interfaces")
        if not isinstance(interfaces, dict):
            raise InterfaceInjectionMigrationError(
                f"{relative.as_posix()}: unversioned interfaces must be a mapping"
            )
        for local_name in interfaces.get("machine", {}):
            machine_ids.add(f"{module_id}.machine.{local_name}")
        for local_name in interfaces.get("llm", {}):
            llm_ids.add(f"{module_id}.llm.{local_name}")
    return machine_ids, llm_ids, source_ids


def _validate_public_id_inventory(
    repo_root: Path,
    migration_map: Mapping[str, Any],
    mapped: Sequence[Path],
) -> int:
    public_ids = migration_map.get("public_ids")
    if not isinstance(public_ids, dict):
        raise InterfaceInjectionMigrationError("migration map requires public_ids")
    try:
        mapped_machine = public_ids["machine_ids"]["ids"]
        default_modules = public_ids["llm_ids"]["default_modules"]
        named_llm = public_ids["llm_ids"]["named"]
        mapped_sources = public_ids["behavior_source_ids"]["ids"]
    except (KeyError, TypeError) as exc:
        raise InterfaceInjectionMigrationError(
            "public_ids inventory is incomplete"
        ) from exc
    lists = (mapped_machine, default_modules, named_llm, mapped_sources)
    if not all(isinstance(value, list) for value in lists):
        raise InterfaceInjectionMigrationError("public_ids inventories must be lists")
    expected_machine = set(mapped_machine)
    expected_llm = {f"{module}.llm.default" for module in default_modules} | set(
        named_llm
    )
    expected_sources = set(mapped_sources)
    if any(len(value) != len(set(value)) for value in lists):
        raise InterfaceInjectionMigrationError("public_ids inventory contains duplicates")
    actual_machine, actual_llm, actual_sources = _legacy_public_id_inventory(
        repo_root, mapped
    )
    mismatches: list[str] = []
    for label, actual, expected in (
        ("machine IDs", actual_machine, expected_machine),
        ("LLM IDs", actual_llm, expected_llm),
        ("behavior source IDs", actual_sources, expected_sources),
    ):
        if actual != expected:
            mismatches.append(
                f"{label}: missing {sorted(actual - expected)}, extra {sorted(expected - actual)}"
            )
    if mismatches:
        raise InterfaceInjectionMigrationError(
            "public ID map is not exact; " + "; ".join(mismatches)
        )
    return len(actual_machine) + len(actual_llm) + len(actual_sources)


def validate_blueprint_migration_map(
    repo_root: Path, migration_map: Mapping[str, Any]
) -> BlueprintMigrationMapValidation:
    """Validate the exact active/non-live declaration partition."""

    raw_root = Path(repo_root)
    if raw_root.is_symlink():
        raise InterfaceInjectionMigrationError("repository root must not be a symlink")
    root = raw_root.resolve()
    declarations = migration_map.get("declarations")
    if not isinstance(declarations, dict):
        raise InterfaceInjectionMigrationError(
            "migration-map declarations must be a mapping"
        )
    _require_python_package_support_policy(migration_map)
    active_values: list[object] = []
    for section_name, field in (
        ("unversioned", "paths"),
        ("version_2", "paths"),
        ("version_3", "live_paths"),
    ):
        section = declarations.get(section_name)
        if not isinstance(section, dict) or not isinstance(section.get(field), list):
            raise InterfaceInjectionMigrationError(
                f"declarations.{section_name}.{field} must be a list"
            )
        active_values.extend(section[field])
    mapped: list[Path] = []
    for value in active_values:
        relative = Path(_require_relative_path(value, "mapped declaration"))
        if relative in mapped:
            raise InterfaceInjectionMigrationError(
                f"duplicate mapped declaration {relative.as_posix()}"
            )
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise InterfaceInjectionMigrationError(
                f"mapped declaration is not a regular file: {relative.as_posix()}"
            )
        if not _git_path_matches(root, relative, "ls-files", "--error-unmatch"):
            raise InterfaceInjectionMigrationError(
                f"mapped live declaration is not tracked: {relative.as_posix()}"
            )
        mapped.append(relative)

    raw_non_live = declarations.get("non_live_local_artifacts", [])
    if not isinstance(raw_non_live, list):
        raise InterfaceInjectionMigrationError(
            "declarations.non_live_local_artifacts must be a list"
        )
    non_live: list[Path] = []
    for entry in raw_non_live:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "state",
            "disposition",
            "reason",
        }:
            raise InterfaceInjectionMigrationError(
                "non-live local artifact must have path, state, disposition, and reason"
            )
        relative = Path(
            _require_relative_path(entry.get("path"), "non-live local artifact")
        )
        if (
            entry.get("state") != "ignored-stale-local"
            or entry.get("disposition") != "exclude"
            or not isinstance(entry.get("reason"), str)
            or not entry["reason"]
        ):
            raise InterfaceInjectionMigrationError(
                f"{relative.as_posix()}: invalid non-live local disposition"
            )
        if relative in mapped:
            raise InterfaceInjectionMigrationError(
                f"{relative.as_posix()}: path cannot be both live and non-live"
            )
        path = root / relative
        if not path.exists() and not path.is_symlink():
            if not _git_path_matches(root, relative, "check-ignore", "-q"):
                raise InterfaceInjectionMigrationError(
                    f"excluded non-live artifact is not covered by ignore policy: {relative.as_posix()}"
                )
            non_live.append(relative)
            continue
        if path.is_symlink() or not path.is_file():
            raise InterfaceInjectionMigrationError(
                f"non-live local artifact is not a regular file: {relative.as_posix()}"
            )
        if _git_path_matches(root, relative, "ls-files", "--error-unmatch"):
            raise InterfaceInjectionMigrationError(
                f"non-live local artifact is tracked: {relative.as_posix()}"
            )
        if not _git_path_matches(root, relative, "check-ignore", "-q"):
            raise InterfaceInjectionMigrationError(
                f"non-live local artifact is not ignored: {relative.as_posix()}"
            )
        if relative in non_live:
            raise InterfaceInjectionMigrationError(
                f"duplicate non-live local artifact {relative.as_posix()}"
            )
        non_live.append(relative)

    inventory = collect_blueprints(root, skip_parse_errors=True)
    inventory_paths = {
        document.relative_path for document in inventory.documents
    } | {issue.relative_path for issue in inventory.issues}
    declared_paths = set(mapped)
    missing = sorted(inventory_paths - declared_paths)
    extra = sorted(declared_paths - inventory_paths)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(
                "unmapped active blueprint paths: "
                f"{[path.as_posix() for path in missing]}"
            )
        if extra:
            details.append(
                "declared blueprint paths absent from inventory: "
                f"{[path.as_posix() for path in extra]}"
            )
        raise InterfaceInjectionMigrationError("; ".join(details))
    live_counts = declarations.get("live_counts")
    if not isinstance(live_counts, dict):
        raise InterfaceInjectionMigrationError("declarations.live_counts is absent")
    actual_counts = {
        "total_documents": len(mapped),
        "unversioned": len(declarations["unversioned"]["paths"]),
        "version_2": len(declarations["version_2"]["paths"]),
        "version_3": len(declarations["version_3"]["live_paths"]),
    }
    if live_counts != actual_counts:
        raise InterfaceInjectionMigrationError(
            f"declarations.live_counts changed: expected {live_counts}, found {actual_counts}"
        )
    schema_file_count, field_occurrence_count = _validate_schema_field_coverage(
        root, migration_map
    )
    public_id_count = _validate_public_id_inventory(root, migration_map, mapped)
    return BlueprintMigrationMapValidation(
        mapped_declaration_paths=tuple(sorted(mapped)),
        non_live_local_paths=tuple(sorted(non_live)),
        schema_file_count=schema_file_count,
        field_occurrence_count=field_occurrence_count,
        public_id_count=public_id_count,
    )


def _reference_module_documents(
    repo_root: Path,
    migration_map: Mapping[str, Any] | None,
    *,
    excluded_blueprints: set[Path],
    allowed_source_paths: set[Path] | None = None,
) -> tuple[
    dict[Path, dict[str, Any]],
    dict[str, _ReferenceSourceTarget],
]:
    raw_modules = _mechanical_conversion_section(migration_map).get(
        "supplemental_modules", []
    )
    if not isinstance(raw_modules, list):
        raise InterfaceInjectionMigrationError("supplemental_modules must be a list")
    documents: dict[Path, dict[str, Any]] = {}
    targets: dict[str, _ReferenceSourceTarget] = {}
    seen_roots: set[Path] = set()
    for module in raw_modules:
        required_module_fields = {
            "root",
            "id",
            "gateway",
            "sources",
        }
        if (
            not isinstance(module, dict)
            or not required_module_fields.issubset(module)
            or not set(module).issubset(
                required_module_fields | {"content", "exports"}
            )
        ):
            raise InterfaceInjectionMigrationError(
                "supplemental module has missing or unmapped fields"
            )
        raw_root = _require_relative_path(module.get("root"), "reference module root")
        module_root = Path(raw_root)
        if module_root in seen_roots:
            raise InterfaceInjectionMigrationError(
                f"duplicate reference module root {raw_root!r}"
            )
        if any(
            module_root.is_relative_to(other) or other.is_relative_to(module_root)
            for other in seen_roots
        ):
            raise InterfaceInjectionMigrationError(
                f"nested reference module root {raw_root!r}"
            )
        seen_roots.add(module_root)
        module_id = module.get("id")
        if not isinstance(module_id, str) or module_id != module_root.name:
            raise InterfaceInjectionMigrationError(
                f"reference module id must match directory {module_root.name!r}"
            )
        target_module_path = module_root / "blueprint.yaml"
        if target_module_path in documents or (repo_root / target_module_path).exists():
            raise InterfaceInjectionMigrationError(
                f"reference module target collision at {target_module_path.as_posix()}"
            )
        gateway = module.get("gateway")
        if not isinstance(gateway, dict):
            raise InterfaceInjectionMigrationError(
                f"{module_id}: reference module gateway must be a mapping"
            )
        if set(gateway) != {"path", "language"}:
            raise InterfaceInjectionMigrationError(
                f"{module_id}: reference module gateway has unmapped fields"
            )
        gateway_path = _require_relative_path(
            gateway.get("path"), f"{module_id}.gateway.path"
        )
        language = gateway.get("language")
        if not isinstance(language, str) or not language:
            raise InterfaceInjectionMigrationError(
                f"{module_id}: reference module gateway has no language"
            )
        module_gateway = repo_root / module_root / gateway_path
        if module_gateway.is_symlink() or not module_gateway.is_file():
            raise InterfaceInjectionMigrationError(
                f"{module_id}: reference module gateway is not a regular file"
            )
        raw_sources = module.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise InterfaceInjectionMigrationError(
                f"{module_id}: reference module sources must be a nonempty list"
            )
        module_sources: dict[str, Any] = {}
        source_gateway_paths: set[str] = set()
        for raw_source in raw_sources:
            required_source_fields = {
                "id",
                "version",
                "blueprint",
                "gateway",
                "legacy",
            }
            allowed_source_fields = required_source_fields | {
                "content",
                "dependencies",
                "uses_interfaces",
                "interfaces",
                "contract_references",
                "platform_support",
                "runtime_dependencies",
            }
            if (
                not isinstance(raw_source, dict)
                or not required_source_fields.issubset(raw_source)
                or not set(raw_source).issubset(allowed_source_fields)
            ):
                raise InterfaceInjectionMigrationError(
                    f"{module_id}: supplemental source has unmapped fields"
                )
            source_id = raw_source.get("id")
            version = raw_source.get("version")
            if not isinstance(source_id, str) or not source_id.startswith(
                f"{module_id}.source."
            ):
                raise InterfaceInjectionMigrationError(
                    f"{module_id}: reference source has a non-owned ID"
                )
            if source_id in targets:
                raise InterfaceInjectionMigrationError(
                    f"duplicate reference source ID {source_id!r}"
                )
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise InterfaceInjectionMigrationError(f"{source_id}: invalid version")
            raw_blueprint = _require_relative_path(
                raw_source.get("blueprint"), f"{source_id}.blueprint"
            )
            relative_blueprint = Path(raw_blueprint)
            if (
                relative_blueprint.parent != Path("blueprints")
                or relative_blueprint.suffix != ".yaml"
            ):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source blueprint must be blueprints/*.yaml"
                )
            target_path = module_root / relative_blueprint
            if target_path in documents or (repo_root / target_path).exists():
                raise InterfaceInjectionMigrationError(
                    f"reference source target collision at {target_path.as_posix()}"
                )
            source_gateway = raw_source.get("gateway")
            if not isinstance(source_gateway, dict) or set(source_gateway) != {
                "path",
                "language",
            }:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source gateway is invalid"
                )
            source_gateway_path = _require_relative_path(
                source_gateway.get("path"), f"{source_id}.gateway.path"
            )
            source_language = source_gateway.get("language")
            if not isinstance(source_language, str) or not source_language:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source gateway has no language"
                )
            if source_gateway_path in source_gateway_paths:
                raise InterfaceInjectionMigrationError(
                    f"{module_id}: reference sources share gateway {source_gateway_path!r}"
                )
            source_gateway_paths.add(source_gateway_path)
            gateway_file = repo_root / module_root / source_gateway_path
            if gateway_file.is_symlink() or not gateway_file.is_file():
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source gateway is not a regular file"
                )
            legacy = raw_source.get("legacy")
            if not isinstance(legacy, dict) or set(legacy) != {
                "path",
                "content",
                "format",
            }:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source legacy identity is invalid"
                )
            expected_legacy_path = f"$repo/{(module_root / source_gateway_path).as_posix()}"
            if legacy.get("path") != expected_legacy_path or any(
                not isinstance(legacy.get(field), str) or not legacy[field]
                for field in ("content", "format")
            ):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reference source legacy identity does not preserve its path"
                )
            raw_content = raw_source.get("content", [source_gateway_path])
            if not isinstance(raw_content, list) or not raw_content:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: supplemental source content must be nonempty"
                )
            source_content: list[str] = []
            for index, value in enumerate(raw_content):
                relative = _require_relative_path(
                    value, f"{source_id}.content[{index}]"
                )
                path = repo_root / module_root / relative
                if path.is_symlink() or not path.is_file():
                    raise InterfaceInjectionMigrationError(
                        f"{source_id}: explicit content is not a regular file: {relative}"
                    )
                source_content.append(_exact_content_pattern(relative))
            source_document = {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": version,
                "gateway": {
                    "path": source_gateway_path,
                    "language": source_language,
                },
                "content": sorted(set(source_content)),
                "dependencies": deepcopy(raw_source.get("dependencies", [])),
                "uses_interfaces": deepcopy(raw_source.get("uses_interfaces", [])),
                "interfaces": deepcopy(raw_source.get("interfaces", {})),
            }
            if "contract_references" in raw_source:
                source_document["contract_references"] = deepcopy(
                    raw_source["contract_references"]
                )
            if "platform_support" in raw_source:
                source_document["platform_support"] = deepcopy(
                    raw_source["platform_support"]
                )
            if "runtime_dependencies" in raw_source:
                source_document["runtime_dependencies"] = deepcopy(
                    raw_source["runtime_dependencies"]
                )
            documents[target_path] = source_document
            module_sources[source_id] = {
                "blueprint": {
                    "base": "module-root",
                    "path": relative_blueprint.as_posix(),
                }
            }
            targets[source_id] = _ReferenceSourceTarget(
                module_id=module_id,
                source_id=source_id,
                version=version,
                module_root=module_root,
                blueprint_path=target_path,
                gateway_path=source_gateway_path,
                gateway_language=source_language,
                legacy=deepcopy(legacy),
            )
        raw_module_content = module.get("content")
        if raw_module_content is None:
            module_content = _regular_module_content(
                repo_root / module_root,
                excluded_blueprints,
                allowed_source_paths,
            )
        else:
            if not isinstance(raw_module_content, list) or not raw_module_content:
                raise InterfaceInjectionMigrationError(
                    f"{module_id}: explicit supplemental module content must be nonempty"
                )
            normalized_content: list[str] = []
            for index, value in enumerate(raw_module_content):
                relative = _require_relative_path(
                    value, f"{module_id}.content[{index}]"
                )
                path = repo_root / module_root / relative
                if path.is_symlink() or not path.is_file():
                    raise InterfaceInjectionMigrationError(
                        f"{module_id}: explicit content is not a regular file: {relative}"
                    )
                normalized_content.append(_exact_content_pattern(relative))
            module_content = sorted(set(normalized_content))
        exports = module.get("exports", {})
        if not isinstance(exports, dict):
            raise InterfaceInjectionMigrationError(
                f"{module_id}: supplemental module exports must be a mapping"
            )
        documents[target_module_path] = {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "gateway": {"path": gateway_path, "language": language},
            "content": module_content,
            "authority": {"owns_filesystem": []},
            "sources": dict(sorted(module_sources.items())),
            "exports": deepcopy(exports),
        }
    return documents, targets


def _legacy_behavior_dependency_mappings(
    migration_map: Mapping[str, Any] | None,
    targets: Mapping[str, _ReferenceSourceTarget],
) -> dict[tuple[str, str], _LegacyBehaviorDependencyMapping]:
    raw_mappings = _mechanical_conversion_section(migration_map).get(
        "legacy_behavior_source_dependencies", []
    )
    if not isinstance(raw_mappings, list):
        raise InterfaceInjectionMigrationError(
            "legacy_behavior_source_dependencies must be a list"
        )
    mappings: dict[tuple[str, str], _LegacyBehaviorDependencyMapping] = {}
    for raw in raw_mappings:
        if not isinstance(raw, dict) or set(raw) != {
            "consumer",
            "authored",
            "target",
        }:
            raise InterfaceInjectionMigrationError(
                "legacy behavior dependency mapping has unmapped fields"
            )
        consumer = raw.get("consumer")
        authored = raw.get("authored")
        target = raw.get("target")
        if not isinstance(consumer, str) or not consumer:
            raise InterfaceInjectionMigrationError(
                "legacy behavior dependency mapping has no consumer"
            )
        if not isinstance(authored, dict) or set(authored) != {
            "path",
            "content",
            "format",
            "reason",
        }:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: legacy behavior mapping has invalid authored evidence"
            )
        _legacy_behavior_evidence([authored], f"{consumer}.mapping.authored")
        if not isinstance(target, dict) or set(target) != {
            "source",
            "version",
            "blueprint",
        }:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: legacy behavior mapping has invalid target"
            )
        source_id = target.get("source")
        source_target = targets.get(source_id) if isinstance(source_id, str) else None
        if source_target is None:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: unresolved mapped reference source {source_id!r}"
            )
        expected_blueprint = {
            "base": "repository-root",
            "path": source_target.blueprint_path.as_posix(),
        }
        if (
            target.get("version") != source_target.version
            or target.get("blueprint") != expected_blueprint
            or {key: authored[key] for key in ("path", "content", "format")}
            != source_target.legacy
        ):
            raise InterfaceInjectionMigrationError(
                f"{consumer}: mapped reference source target is not exact"
            )
        key = (consumer, yaml.safe_dump(authored, sort_keys=True))
        if key in mappings:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: duplicate legacy behavior dependency mapping"
            )
        mappings[key] = _LegacyBehaviorDependencyMapping(
            consumer=consumer,
            authored=deepcopy(authored),
            target=deepcopy(target),
        )
    return mappings


def _v4_semantic_edge_projection(
    documents: Mapping[Path, Mapping[str, Any]],
) -> dict[str, object]:
    """Project source dependencies and interface-use edges from v4 bytes."""

    projection: dict[str, object] = {}
    for document in documents.values():
        if document.get("node_type") != "behavioral_source":
            continue
        source_id = document.get("id")
        dependencies = document.get("dependencies")
        uses = document.get("uses_interfaces")
        if (
            not isinstance(source_id, str)
            or not isinstance(dependencies, list)
            or not isinstance(uses, list)
        ):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: invalid v4 semantic edge declaration"
            )
        projection[source_id] = {
            "dependencies": deepcopy(dependencies),
            "uses_interfaces": deepcopy(uses),
            "content": deepcopy(document.get("content", [])),
        }
    return dict(sorted(projection.items()))


def _legacy_predecessor_projections(
    repo_root: Path,
    interfaces_by_module: Mapping[str, Sequence[_InterfaceInput]],
    typed_sources: Mapping[str, Sequence[_BehavioralSourceInput]],
    source_targets: Mapping[str, tuple[str, Path, _BehavioralSourceInput]],
    migration_plan: CompiledMigrationPlan | None,
    *,
    behavior_dependency_mappings: Mapping[
        tuple[str, str], _LegacyBehaviorDependencyMapping
    ] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Project authored facts from conversion-normalized legacy declarations."""

    root = Path(repo_root).resolve()
    public_exports: dict[str, object] = {}
    runtime_sources: dict[str, object] = {}
    semantic: dict[str, object] = {}
    private_interfaces: dict[str, str] = {}
    source_facts: dict[str, tuple[str, str, set[str]]] = {}

    for old_module_id, interfaces in sorted(interfaces_by_module.items()):
        module_id = _target_module_id(old_module_id, migration_plan)
        grouped: dict[str, list[_InterfaceInput]] = {}
        for interface in interfaces:
            grouped.setdefault(interface.gateway_path, []).append(interface)
        for gateway, source_interfaces in grouped.items():
            source_id = f"{module_id}.source.{_source_slug(gateway)}"
            for interface in source_interfaces:
                export_id = _rename_interface_id(
                    interface.old_id, migration_plan
                )
                private_id = f"{source_id}.interface.{interface.local_name}"
                private_interfaces[export_id] = private_id
                public_exports[export_id] = {
                    "version": interface.version,
                    "source_interface": private_id,
                    "access": {
                        "allow_all_modules": interface.allow_all_modules,
                        "allowed_callers": sorted(
                            _target_module_id(caller, migration_plan)
                            for caller in interface.allowed_callers
                        ),
                    },
                }

    for old_module_id, interfaces in sorted(interfaces_by_module.items()):
        module_id = _target_module_id(old_module_id, migration_plan)
        groups: dict[str, list[_InterfaceInput]] = {}
        for interface in interfaces:
            groups.setdefault(interface.gateway_path, []).append(interface)
        for gateway, source_interfaces in sorted(groups.items()):
            source_id = f"{module_id}.source.{_source_slug(gateway)}"
            content = {gateway}
            mapped_dependencies: list[dict[str, Any]] = []
            for interface in source_interfaces:
                content.update(interface.same_source_content)
                for evidence in interface.behavior_evidence:
                    declared_path = evidence["path"]
                    if declared_path.startswith("$repo/"):
                        mapping = (behavior_dependency_mappings or {}).get(
                            (
                                interface.old_id,
                                yaml.safe_dump(evidence, sort_keys=True),
                            )
                        )
                        if mapping is None:
                            raise InterfaceInjectionMigrationError(
                                f"{interface.old_id}: unresolved repository "
                                f"behavior source {declared_path!r}"
                            )
                        mapped_dependencies.append(
                            {
                                "source": mapping.target["source"],
                                "version": mapping.target["version"],
                                "blueprint": deepcopy(mapping.target["blueprint"]),
                                "reason": evidence["reason"],
                            }
                        )
                    else:
                        content.add(declared_path)

            uses = _normalize_uses(
                [
                    edge
                    for interface in source_interfaces
                    for edge in interface.uses_interfaces
                ],
                migration_plan,
            )
            for edge in uses:
                target = edge["interface"]
                if target.startswith(f"{module_id}.interface."):
                    edge["interface"] = private_interfaces.get(target, target)

            dependencies = _normalize_source_dependencies(
                [
                    edge
                    for interface in source_interfaces
                    for edge in interface.source_dependencies
                ],
                source_targets,
                owner_module_id=module_id,
            )
            dependency_values = {
                yaml.safe_dump(item, sort_keys=True): deepcopy(item)
                for item in (*dependencies, *mapped_dependencies)
            }
            dependencies = [
                dependency_values[key] for key in sorted(dependency_values)
            ]
            semantic[source_id] = {
                "dependencies": dependencies,
                "uses_interfaces": uses,
                "content": [_exact_content_pattern(path) for path in sorted(content)],
            }
            source_facts[source_id] = (old_module_id, gateway, set(content))
            platforms = [
                deepcopy(interface.platform_support)
                for interface in source_interfaces
                if interface.platform_support is not None
            ]
            if platforms:
                runtime_values = {
                    yaml.safe_dump(item, sort_keys=True): deepcopy(item)
                    for interface in source_interfaces
                    for item in interface.runtime_dependencies or ()
                }
                runtime_sources[source_id] = {
                    "platform_support": platforms[0],
                    "runtime_dependencies": [
                        runtime_values[key] for key in sorted(runtime_values)
                    ],
                }

    for old_module_id, sources in sorted(typed_sources.items()):
        module_id = _target_module_id(old_module_id, migration_plan)
        for source in sorted(sources, key=lambda item: item.old_id):
            source_id, _source_path, _source = source_targets[source.old_id]
            dependencies = _normalize_source_dependencies(
                source.dependencies,
                source_targets,
                owner_module_id=module_id,
            )
            semantic[source_id] = {
                "dependencies": dependencies,
                "uses_interfaces": [],
                "content": [_exact_content_pattern(source.gateway_path)],
            }
            source_facts[source_id] = (
                old_module_id,
                source.gateway_path,
                {source.gateway_path},
            )
    _augment_legacy_python_edges(root, source_facts, semantic)
    return (
        dict(sorted(semantic.items())),
        {"exports": dict(sorted(public_exports.items()))},
        dict(sorted(runtime_sources.items())),
    )


def _augment_legacy_python_edges(
    repo_root: Path,
    source_facts: Mapping[str, tuple[str, str, set[str]]],
    semantic: dict[str, object],
) -> None:
    """Independently derive package/import edges from captured predecessor files."""

    by_module: dict[str, dict[str, tuple[str, set[str]]]] = {}
    for source_id, (module_id, gateway, content) in source_facts.items():
        by_module.setdefault(module_id, {})[source_id] = (gateway, set(content))
    for module_id, facts in sorted(by_module.items()):
        module_root = repo_root / "skills" / module_id
        public_module = next(iter(facts)).split(".source.", 1)[0]
        owners = {
            path: source_id
            for source_id, (_gateway, content) in facts.items()
            for path in content
            if path.endswith(".py")
        }
        package_roots = {
            PurePosixPath(gateway).parts[0]
            for gateway, _content in facts.values()
            if gateway.endswith(".py") and len(PurePosixPath(gateway).parts) > 1
        }
        for package_root in sorted(package_roots):
            for initializer_path in sorted(
                (module_root / package_root).rglob("__init__.py")
            ):
                initializer = initializer_path.relative_to(module_root).as_posix()
                if initializer in owners:
                    continue
                source_id = f"{public_module}.source.{_source_slug(initializer)}"
                content = {initializer}
                facts[source_id] = (initializer, content)
                owners[initializer] = source_id
                semantic[source_id] = {
                    "dependencies": [],
                    "uses_interfaces": [],
                    "content": [_exact_content_pattern(initializer)],
                }

        def add(source_id: str, target_id: str, reason: str) -> None:
            if source_id == target_id:
                return
            gateway, _content = facts[target_id]
            dependency = {
                "source": target_id,
                "version": 1,
                "blueprint": {
                    "base": "module-root",
                    "path": f"blueprints/{_source_slug(gateway)}.yaml",
                },
                "reason": reason,
            }
            dependencies = semantic[source_id]["dependencies"]
            if not any(item.get("source") == target_id for item in dependencies):
                dependencies.append(dependency)
                dependencies.sort(key=lambda item: yaml.safe_dump(item, sort_keys=True))

        for source_id, (gateway, _content) in sorted(facts.items()):
            if not gateway.endswith(".py") or gateway.endswith("/__init__.py"):
                continue
            parent = PurePosixPath(gateway).parent
            while parent != PurePosixPath("."):
                initializer = (parent / "__init__.py").as_posix()
                if initializer in owners:
                    add(
                        source_id,
                        owners[initializer],
                        f"Loads Python package support from {initializer}.",
                    )
                parent = parent.parent

        for relative, source_id in sorted(owners.items()):
            path = module_root / relative
            if not path.is_file() or path.is_symlink():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise InterfaceInjectionMigrationError(
                    f"{relative}: cannot derive independent Python imports: {exc}"
                ) from exc
            parent = PurePosixPath(relative).parent
            imported: dict[str, set[str]] = {}
            for node in ast.walk(tree):
                modules: list[str] = []
                search_labels = _PYTHON_PACKAGE_SUPPORT_POLICY[
                    "import_search_roots"
                ]["by_gateway"].get(
                    f"skills/{module_id}/{relative}",
                    _PYTHON_PACKAGE_SUPPORT_POLICY["import_search_roots"]["default"],
                )
                anchors = [PurePosixPath(".")]
                if "gateway-parent" in search_labels:
                    anchors.append(parent)
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        anchor = parent
                        for _ in range(node.level - 1):
                            anchor = anchor.parent
                        anchors = [anchor]
                    if node.module:
                        modules = [node.module, *(
                            f"{node.module}.{alias.name}"
                            for alias in node.names
                            if alias.name != "*"
                        )]
                    else:
                        modules = [alias.name for alias in node.names]
                else:
                    continue
                for module_name in modules:
                    match = next(
                        (
                            choice.as_posix().removeprefix("./")
                            for anchor in anchors
                            for candidate in [
                                anchor.joinpath(*module_name.split("."))
                            ]
                            for choice in (
                                candidate / "__init__.py",
                                candidate.with_suffix(".py"),
                            )
                            if choice.as_posix().removeprefix("./") in owners
                        ),
                        None,
                    )
                    if match is not None and owners[match] != source_id:
                        imported.setdefault(owners[match], set()).add(match)
            for target_id, paths in sorted(imported.items()):
                add(
                    source_id,
                    target_id,
                    "Imports Python source from " + ", ".join(sorted(paths)) + ".",
                )


def _v4_document_projections(
    documents: Mapping[Path, Mapping[str, Any]],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    intrinsic: dict[str, Mapping[str, Any]] = {}
    for document in documents.values():
        if document.get("schema_version") != 4:
            raise InterfaceInjectionMigrationError(
                "v4 projection received a non-v4 declaration"
            )
        if document.get("node_type") == "behavioral_source":
            interfaces = document.get("interfaces")
            if not isinstance(interfaces, dict):
                raise InterfaceInjectionMigrationError(
                    f"{document.get('id')}: interfaces must be a mapping"
                )
            for interface_id, interface in interfaces.items():
                if not isinstance(interface_id, str) or not isinstance(interface, dict):
                    raise InterfaceInjectionMigrationError(
                        f"{document.get('id')}: invalid source interface"
                    )
                if interface_id in intrinsic:
                    raise InterfaceInjectionMigrationError(
                        f"duplicate source interface {interface_id}"
                    )
                intrinsic[interface_id] = interface

    public_exports: dict[str, object] = {}
    runtime_sources: dict[str, object] = {}
    behavioral_dependencies: dict[str, object] = {}
    for document in documents.values():
        node_type = document.get("node_type")
        node_id = document.get("id")
        if not isinstance(node_id, str):
            raise InterfaceInjectionMigrationError("v4 declaration has no string ID")
        if node_type == "module":
            exports = document.get("exports")
            if not isinstance(exports, dict):
                raise InterfaceInjectionMigrationError(
                    f"{node_id}: exports must be a mapping"
                )
            for export_id, export in exports.items():
                if not isinstance(export_id, str) or not isinstance(export, dict):
                    raise InterfaceInjectionMigrationError(f"{node_id}: invalid export")
                source_interface = export.get("source_interface")
                interface = intrinsic.get(source_interface)
                if interface is None:
                    raise InterfaceInjectionMigrationError(
                        f"{export_id}: unresolved source interface {source_interface!r}"
                    )
                public_exports[export_id] = {
                    "version": interface.get("version"),
                    "source_interface": source_interface,
                    "access": deepcopy(export.get("access")),
                }
        elif node_type == "behavioral_source":
            dependencies = document.get("dependencies")
            if isinstance(dependencies, list) and dependencies:
                behavioral_dependencies[node_id] = deepcopy(dependencies)
            if "platform_support" in document or "runtime_dependencies" in document:
                runtime_sources[node_id] = {
                    "platform_support": deepcopy(document.get("platform_support")),
                    "runtime_dependencies": deepcopy(
                        document.get("runtime_dependencies", [])
                    ),
                }
        else:
            raise InterfaceInjectionMigrationError(
                f"{node_id}: unsupported v4 node type {node_type!r}"
            )
    return (
        {"exports": dict(sorted(public_exports.items()))},
        dict(sorted(runtime_sources.items())),
        dict(sorted(behavioral_dependencies.items())),
    )


def _add_python_package_support_sources(
    repo_root: Path,
    documents: dict[Path, dict[str, Any]],
    dependency_projection: dict[str, object],
    package_support_paths: set[Path],
    *,
    import_search_roots: Mapping[str, Any],
    allowed_source_paths: set[Path] | None,
    fixed_source_ids: set[str] | None = None,
) -> None:
    """Give imported package initializers/helpers one source and exact dependencies."""

    for module_path, module in sorted(tuple(documents.items())):
        if module.get("node_type") != "module":
            continue
        module_id = module.get("id")
        sources = module.get("sources")
        if not isinstance(module_id, str) or not isinstance(sources, dict):
            continue
        module_root = module_path.parent

        def source_documents() -> dict[str, tuple[Path, dict[str, Any]]]:
            result: dict[str, tuple[Path, dict[str, Any]]] = {}
            for source_id, source_ref in sources.items():
                locator = (
                    source_ref.get("blueprint")
                    if isinstance(source_ref, dict)
                    else None
                )
                relative = locator.get("path") if isinstance(locator, dict) else None
                if locator.get("base") != "module-root" or not isinstance(relative, str):
                    continue
                path = module_root / relative
                declaration = documents.get(path)
                if isinstance(source_id, str) and isinstance(declaration, dict):
                    result[source_id] = (path, declaration)
            return result

        current_sources = source_documents()
        by_gateway = import_search_roots.get("by_gateway", {})
        default_roots = import_search_roots.get("default", [])
        if not isinstance(by_gateway, Mapping) or not isinstance(default_roots, list):
            raise InterfaceInjectionMigrationError("invalid Python import search roots")

        def search_anchors(gateway_path: str) -> tuple[PurePosixPath, ...]:
            key = (module_root / gateway_path).as_posix()
            labels = by_gateway.get(key, default_roots)
            if not isinstance(labels, list) or not labels:
                raise InterfaceInjectionMigrationError(
                    f"{key}: invalid Python import search roots"
                )
            anchors: list[PurePosixPath] = []
            for label in labels:
                if label == "module-root":
                    anchor = PurePosixPath(".")
                elif label == "gateway-parent":
                    anchor = PurePosixPath(gateway_path).parent
                else:
                    raise InterfaceInjectionMigrationError(
                        f"{key}: unknown Python import search root {label!r}"
                    )
                if anchor not in anchors:
                    anchors.append(anchor)
            return tuple(anchors)

        package_initializers: set[str] = set()
        package_roots: set[PurePosixPath] = set()
        for _source_id, (_source_path, source) in current_sources.items():
            gateway = source.get("gateway")
            gateway_path = gateway.get("path") if isinstance(gateway, dict) else None
            language = gateway.get("language") if isinstance(gateway, dict) else None
            if (
                not isinstance(gateway_path, str)
                or not isinstance(language, str)
                or re.split(r"(?:==|>=|>|<=|<)", language, maxsplit=1)[0]
                != "Python"
            ):
                continue
            gateway_parts = PurePosixPath(gateway_path).parts
            if len(gateway_parts) > 1:
                package_roots.add(PurePosixPath(gateway_parts[0]))
            parent = PurePosixPath(gateway_path).parent
            while parent != PurePosixPath("."):
                initializer = (parent / "__init__.py").as_posix()
                candidate = repo_root / module_root / initializer
                if candidate.is_file() and not candidate.is_symlink():
                    package_initializers.add(initializer)
                parent = parent.parent

        for package_root in sorted(package_roots):
            physical_root = repo_root / module_root / package_root
            if not physical_root.is_dir() or physical_root.is_symlink():
                continue
            for candidate in sorted(physical_root.rglob("__init__.py")):
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and (
                        allowed_source_paths is None
                        or candidate.resolve() in allowed_source_paths
                    )
                ):
                    package_initializers.add(
                        candidate.relative_to(repo_root / module_root).as_posix()
                    )

        gateway_owners = {
            source["gateway"]["path"]: source_id
            for source_id, (_path, source) in current_sources.items()
            if isinstance(source.get("gateway"), dict)
            and isinstance(source["gateway"].get("path"), str)
        }
        gateway_paths = set(gateway_owners)
        source_owned_patterns = {
            pattern
            for _source_id, (_path, source) in current_sources.items()
            for pattern in source.get("content", [])
            if isinstance(pattern, str)
        }
        for initializer in sorted(package_initializers):
            support_id = gateway_owners.get(initializer)
            if support_id is None:
                slug = _source_slug(initializer)
                support_id = f"{module_id}.source.{slug}"
                support_path = module_root / "blueprints" / f"{slug}.yaml"
                if support_id in sources or support_path in documents:
                    raise InterfaceInjectionMigrationError(
                        f"{module_id}: Python package support source collision at {support_path}"
                    )
                package_dir = repo_root / module_root / PurePosixPath(initializer).parent
                support_files = []
                for candidate in sorted(package_dir.glob("*.py")):
                    relative = candidate.relative_to(repo_root / module_root).as_posix()
                    pattern = _exact_content_pattern(relative)
                    if (
                        candidate.is_file()
                        and not candidate.is_symlink()
                        and (
                            allowed_source_paths is None
                            or candidate.resolve() in allowed_source_paths
                        )
                        and relative not in gateway_paths
                        and pattern not in source_owned_patterns
                    ):
                        support_files.append(relative)
                if initializer not in support_files:
                    support_files.append(initializer)
                support_files = sorted(set(support_files))
                documents[support_path] = {
                    "schema_version": 4,
                    "node_type": "behavioral_source",
                    "id": support_id,
                    "version": 1,
                    "gateway": {"path": initializer, "language": "Python"},
                    "content": [
                        _exact_content_pattern(path) for path in support_files
                    ],
                    "dependencies": [],
                    "uses_interfaces": [],
                    "interfaces": {},
                }
                package_support_paths.add(support_path)
                sources[support_id] = {
                    "blueprint": {
                        "base": "module-root",
                        "path": support_path.relative_to(module_root).as_posix(),
                    }
                }
                gateway_owners[initializer] = support_id

        current_sources = source_documents()
        for source_id, (_source_path, source) in sorted(current_sources.items()):
            gateway = source.get("gateway")
            gateway_path = gateway.get("path") if isinstance(gateway, dict) else None
            language = gateway.get("language") if isinstance(gateway, dict) else None
            if (
                not isinstance(gateway_path, str)
                or not isinstance(language, str)
                or re.split(r"(?:==|>=|>|<=|<)", language, maxsplit=1)[0]
                != "Python"
            ):
                continue
            if gateway_path.endswith("/__init__.py"):
                continue
            needed: list[str] = []
            parent = PurePosixPath(gateway_path).parent
            while parent != PurePosixPath("."):
                initializer = (parent / "__init__.py").as_posix()
                if initializer in package_initializers:
                    needed.append(initializer)
                parent = parent.parent
            dependencies = source.get("dependencies")
            if not isinstance(dependencies, list):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: dependencies must be a list"
                )
            existing_targets = {
                dependency.get("source")
                for dependency in dependencies
                if isinstance(dependency, dict)
            }
            for initializer in sorted(needed):
                support_id = gateway_owners[initializer]
                if support_id == source_id or support_id in existing_targets:
                    continue
                support_path, support = current_sources[support_id]
                dependencies.append(
                    {
                        "source": support_id,
                        "version": support["version"],
                        "blueprint": {
                            "base": "module-root",
                            "path": support_path.relative_to(module_root).as_posix(),
                        },
                        "reason": (
                            "Loads Python package support from "
                            f"{initializer}."
                        ),
                    }
                )
                existing_targets.add(support_id)
            dependencies.sort(
                key=lambda dependency: (
                    str(dependency.get("source")),
                    str(dependency.get("reason")),
                )
            )
            if dependencies:
                dependency_projection[source_id] = deepcopy(dependencies)

        python_files = tuple(
            path
            for path in sorted((repo_root / module_root).rglob("*.py"))
            if path.is_file()
            and not path.is_symlink()
            and (
                allowed_source_paths is None or path.resolve() in allowed_source_paths
            )
        )
        python_owners: dict[str, str] = {}
        for source_id, (_source_path, source) in current_sources.items():
            patterns = source.get("content", [])
            if not isinstance(patterns, list):
                continue
            for path in python_files:
                relative = path.relative_to(repo_root / module_root).as_posix()
                if any(
                    isinstance(pattern, str)
                    and re.fullmatch(pattern, relative) is not None
                    for pattern in patterns
                ):
                    previous = python_owners.get(relative)
                    if previous is not None and previous != source_id:
                        raise InterfaceInjectionMigrationError(
                            f"{relative}: Python source has multiple source owners"
                        )
                    python_owners[relative] = source_id

        imported_targets: dict[tuple[str, str], set[str]] = {}
        for relative, source_id in sorted(python_owners.items()):
            path = repo_root / module_root / relative
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise InterfaceInjectionMigrationError(
                    f"{relative}: cannot inspect Python imports: {exc}"
                ) from exc
            current_parent = PurePosixPath(relative).parent
            candidates: set[PurePosixPath] = set()
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                    anchors = search_anchors(relative)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        anchor = current_parent
                        for _ in range(node.level - 1):
                            anchor = anchor.parent
                        anchors = (anchor,)
                    else:
                        anchors = search_anchors(relative)
                    if node.module:
                        modules.append(node.module)
                        modules.extend(
                            f"{node.module}.{alias.name}"
                            for alias in node.names
                            if alias.name != "*"
                        )
                    else:
                        modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"__import__", "import_module"}
                    or isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                ):
                    if (
                        not node.args
                        or not isinstance(node.args[0], ast.Constant)
                        or not isinstance(node.args[0].value, str)
                    ):
                        raise InterfaceInjectionMigrationError(
                            f"{relative}: dynamic Python import has no literal module name"
                        )
                    modules.append(node.args[0].value)
                    anchors = search_anchors(relative)
                else:
                    continue
                for module_name in modules:
                    matches: set[str] = set()
                    for anchor in anchors:
                        module_path = anchor.joinpath(*module_name.split("."))
                        choices = (
                            module_path / "__init__.py",
                            module_path.with_suffix(".py"),
                        )
                        for choice in choices:
                            normalized = choice.as_posix().removeprefix("./")
                            physical = repo_root / module_root / normalized
                            if physical.is_file() and normalized not in python_owners:
                                raise InterfaceInjectionMigrationError(
                                    f"{relative}: local import {module_name!r} "
                                    f"matches unowned source {normalized}"
                                )
                            if normalized in python_owners:
                                matches.add(normalized)
                    if len(matches) > 1:
                        raise InterfaceInjectionMigrationError(
                            f"{relative}: local import {module_name!r} is ambiguous "
                            f"across search roots: {sorted(matches)}"
                        )
                    if matches:
                        candidates.add(PurePosixPath(next(iter(matches))))
            for candidate in sorted(candidates):
                target_relative = candidate.as_posix().removeprefix("./")
                target_id = python_owners.get(target_relative)
                if target_id is not None and target_id != source_id:
                    imported_targets.setdefault((source_id, target_id), set()).add(
                        target_relative
                    )

        for (source_id, target_id), imported_paths in sorted(imported_targets.items()):
            _source_path, source = current_sources[source_id]
            target_path, target = current_sources[target_id]
            uses = source.get("uses_interfaces")
            target_interfaces = target.get("interfaces")
            if isinstance(uses, list) and isinstance(target_interfaces, Mapping):
                covered_by_private_interface = any(
                    isinstance(use, Mapping)
                    and isinstance(interface_id := use.get("interface"), str)
                    and interface_id in target_interfaces
                    and isinstance(target_interface := target_interfaces[interface_id], Mapping)
                    and use.get("version") == target_interface.get("version")
                    for use in uses
                )
                if covered_by_private_interface:
                    continue
            dependencies = source["dependencies"]
            if any(
                isinstance(dependency, dict)
                and dependency.get("source") == target_id
                for dependency in dependencies
            ):
                continue
            dependencies.append(
                {
                    "source": target_id,
                    "version": target["version"],
                    "blueprint": {
                        "base": "module-root",
                        "path": target_path.relative_to(module_root).as_posix(),
                    },
                    "reason": (
                        "Imports Python source from "
                        + ", ".join(sorted(imported_paths))
                        + "."
                    ),
                }
            )
            dependencies.sort(
                key=lambda dependency: (
                    str(dependency.get("source")),
                    str(dependency.get("reason")),
                )
            )
            dependency_projection[source_id] = deepcopy(dependencies)


def _rewrite_same_module_uses_to_private(
    documents: Mapping[Path, dict[str, Any]],
    public_exports: Mapping[str, object],
) -> None:
    private_targets = {
        export_id: export.get("source_interface")
        for export_id, export in public_exports.items()
        if isinstance(export, dict) and isinstance(export.get("source_interface"), str)
    }
    for document in documents.values():
        if document.get("node_type") != "behavioral_source":
            continue
        source_id = document.get("id")
        uses = document.get("uses_interfaces")
        if not isinstance(source_id, str) or not isinstance(uses, list):
            continue
        module_id = source_id.split(".source.", 1)[0]
        for edge in uses:
            interface_id = edge.get("interface") if isinstance(edge, dict) else None
            private_id = private_targets.get(interface_id)
            if (
                isinstance(interface_id, str)
                and interface_id.startswith(f"{module_id}.interface.")
                and isinstance(private_id, str)
            ):
                edge["interface"] = private_id


def _apply_reviewed_source_facts(
    repo_root: Path,
    documents: dict[Path, dict[str, Any]],
    migration_map: Mapping[str, Any] | None,
    public_exports: Mapping[str, object],
    dependency_projection: dict[str, object],
    runtime_projection: dict[str, object],
    predecessor_semantic_edges: dict[str, object],
    predecessor_runtime_projection: dict[str, object],
    *,
    allowed_source_paths: set[Path] | None,
) -> None:
    """Apply exact reviewed facts that legacy declarations cannot represent."""

    raw_entries = _mechanical_conversion_section(migration_map).get(
        "reviewed_source_facts", []
    )
    if not isinstance(raw_entries, list):
        raise InterfaceInjectionMigrationError(
            "reviewed_source_facts must be a list"
        )

    modules: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, document in documents.items():
        module_id = document.get("id")
        if document.get("node_type") == "module" and isinstance(module_id, str):
            modules[module_id] = (path, document)

    def source_index() -> dict[
        str, tuple[Path, dict[str, Any], Path, dict[str, Any]]
    ]:
        indexed: dict[
            str, tuple[Path, dict[str, Any], Path, dict[str, Any]]
        ] = {}
        for module_path, module in modules.values():
            declared = module.get("sources")
            if not isinstance(declared, Mapping):
                continue
            for source_id, reference in declared.items():
                locator = (
                    reference.get("blueprint")
                    if isinstance(reference, Mapping)
                    else None
                )
                relative = (
                    locator.get("path") if isinstance(locator, Mapping) else None
                )
                if (
                    isinstance(source_id, str)
                    and isinstance(relative, str)
                    and locator.get("base") == "module-root"
                ):
                    source_path = module_path.parent / relative
                    source = documents.get(source_path)
                    if isinstance(source, dict):
                        indexed[source_id] = (
                            module_path,
                            module,
                            source_path,
                            source,
                        )
        return indexed

    seen_sources: set[str] = set()
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, Mapping) or not set(entry).issubset(
            {"source", "create", "content", "dependencies", "uses_interfaces"}
        ):
            raise InterfaceInjectionMigrationError(
                f"reviewed_source_facts[{index}] is invalid"
            )
        source_id = entry.get("source")
        if (
            not isinstance(source_id, str)
            or ".source." not in source_id
            or source_id in seen_sources
        ):
            raise InterfaceInjectionMigrationError(
                f"reviewed_source_facts[{index}] has an invalid source"
            )
        seen_sources.add(source_id)
        create = entry.get("create")
        if create is None:
            continue
        if not isinstance(create, Mapping) or not {
            "version",
            "blueprint",
            "gateway",
        }.issubset(create) or not set(create).issubset(
            {
                "version",
                "blueprint",
                "gateway",
                "platform_support",
                "runtime_dependencies",
                "interfaces",
            }
        ):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source creation is invalid"
            )
        module_id = source_id.split(".source.", 1)[0]
        module_record = modules.get(module_id)
        if module_record is None:
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source module is unresolved"
            )
        module_path, module = module_record
        blueprint = _require_relative_path(
            create.get("blueprint"), f"{source_id}.create.blueprint"
        )
        source_path = module_path.parent / blueprint
        if (
            Path(blueprint).parent != Path("blueprints")
            or Path(blueprint).suffix != ".yaml"
            or source_path in documents
        ):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source blueprint is invalid"
            )
        gateway = create.get("gateway")
        if not isinstance(gateway, Mapping) or set(gateway) != {
            "path",
            "language",
        }:
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source gateway is invalid"
            )
        gateway_path = _require_relative_path(
            gateway.get("path"), f"{source_id}.create.gateway.path"
        )
        gateway_file = repo_root / module_path.parent / gateway_path
        if (
            gateway_file.is_symlink()
            or not gateway_file.is_file()
            or (
                allowed_source_paths is not None
                and gateway_file.resolve() not in allowed_source_paths
            )
        ):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source gateway is not tracked"
            )
        version = create.get("version")
        language = gateway.get("language")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
            or not isinstance(language, str)
            or not language
        ):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source identity is invalid"
            )
        source: dict[str, Any] = {
            "schema_version": 4,
            "node_type": "behavioral_source",
            "id": source_id,
            "version": version,
            "gateway": {"path": gateway_path, "language": language},
            "content": [],
            "dependencies": [],
            "uses_interfaces": [],
            "interfaces": deepcopy(create.get("interfaces", {})),
        }
        for field in ("platform_support", "runtime_dependencies"):
            if field in create:
                source[field] = deepcopy(create[field])
        documents[source_path] = source
        declared_sources = module.get("sources")
        if not isinstance(declared_sources, dict) or source_id in declared_sources:
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source collides with an existing source"
            )
        declared_sources[source_id] = {
            "blueprint": {
                "base": "module-root",
                "path": blueprint,
            }
        }
        declared_sources.update(dict(sorted(declared_sources.items())))

    indexed = source_index()
    for index, entry in enumerate(raw_entries):
        source_id = entry["source"]
        record = indexed.get(source_id)
        if record is None:
            raise InterfaceInjectionMigrationError(
                f"{source_id}: reviewed source is unresolved"
            )
        module_path, module, _source_path, source = record
        content = entry.get("content", [])
        dependencies = entry.get("dependencies", [])
        uses_interfaces = entry.get("uses_interfaces", [])
        if (
            not isinstance(content, list)
            or not isinstance(dependencies, list)
            or not isinstance(uses_interfaces, list)
        ):
            raise InterfaceInjectionMigrationError(
                f"reviewed_source_facts[{index}] has invalid fact lists"
            )
        source_content = source.get("content")
        if not isinstance(source_content, list):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: content must be a list"
            )
        for content_index, raw_path in enumerate(content):
            relative = _require_relative_path(
                raw_path, f"{source_id}.content[{content_index}]"
            )
            physical = repo_root / module_path.parent / relative
            if (
                physical.is_symlink()
                or not physical.is_file()
                or (
                    allowed_source_paths is not None
                    and physical.resolve() not in allowed_source_paths
                )
            ):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reviewed content is not tracked: {relative}"
                )
            pattern = _exact_content_pattern(relative)
            for other_id, (
                other_module_path,
                _other_module,
                _other_path,
                other,
            ) in indexed.items():
                if other_id == source_id or other_module_path != module_path:
                    continue
                other_content = other.get("content")
                if not isinstance(other_content, list) or pattern not in other_content:
                    continue
                other_gateway = other.get("gateway")
                if (
                    isinstance(other_gateway, Mapping)
                    and other_gateway.get("path") == relative
                ):
                    raise InterfaceInjectionMigrationError(
                        f"{source_id}: reviewed content is another source gateway"
                    )
                other_content.remove(pattern)
            if pattern not in source_content:
                source_content.append(pattern)
        source_content.sort()

        source_dependencies = source.get("dependencies")
        if not isinstance(source_dependencies, list):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: dependencies must be a list"
            )
        for dependency_index, raw_dependency in enumerate(dependencies):
            if not isinstance(raw_dependency, Mapping) or set(raw_dependency) != {
                "source",
                "version",
                "reason",
            }:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}.dependencies[{dependency_index}] is invalid"
                )
            target_id = raw_dependency.get("source")
            target = indexed.get(target_id) if isinstance(target_id, str) else None
            if target is None or raw_dependency.get("version") != target[3].get(
                "version"
            ):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reviewed dependency is unresolved"
                )
            reason = raw_dependency.get("reason")
            if not isinstance(reason, str) or not reason:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reviewed dependency has no reason"
                )
            target_module_path, _target_module, target_path, _target_source = target
            same_module = target_module_path == module_path
            dependency = {
                "source": target_id,
                "version": raw_dependency["version"],
                "blueprint": {
                    "base": "module-root" if same_module else "repository-root",
                    "path": (
                        target_path.relative_to(module_path.parent).as_posix()
                        if same_module
                        else target_path.as_posix()
                    ),
                },
                "reason": reason,
            }
            if not any(
                isinstance(existing, Mapping)
                and existing.get("source") == target_id
                for existing in source_dependencies
            ):
                source_dependencies.append(dependency)
        source_dependencies.sort(
            key=lambda value: (
                str(value.get("source")),
                str(value.get("reason")),
            )
        )

        source_uses = source.get("uses_interfaces")
        if not isinstance(source_uses, list):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: uses_interfaces must be a list"
            )
        for use_index, use in enumerate(uses_interfaces):
            if not isinstance(use, Mapping) or set(use) != {
                "interface",
                "version",
            }:
                raise InterfaceInjectionMigrationError(
                    f"{source_id}.uses_interfaces[{use_index}] is invalid"
                )
            interface_id = use.get("interface")
            export = (
                public_exports.get(interface_id)
                if isinstance(interface_id, str)
                else None
            )
            if not isinstance(export, Mapping) or export.get("version") != use.get(
                "version"
            ):
                raise InterfaceInjectionMigrationError(
                    f"{source_id}: reviewed interface use is unresolved"
                )
            normalized_use = dict(use)
            if normalized_use not in source_uses:
                source_uses.append(normalized_use)
        source_uses.sort(
            key=lambda value: (
                str(value.get("interface")),
                value.get("version", 0),
            )
        )

        semantic = predecessor_semantic_edges.setdefault(
            source_id,
            {
                "dependencies": [],
                "uses_interfaces": [],
                "content": [],
            },
        )
        if not isinstance(semantic, dict):
            raise InterfaceInjectionMigrationError(
                f"{source_id}: predecessor semantic facts are invalid"
            )
        semantic["dependencies"] = deepcopy(source_dependencies)
        semantic["uses_interfaces"] = deepcopy(source_uses)
        semantic["content"] = deepcopy(source_content)
        if source_dependencies:
            dependency_projection[source_id] = deepcopy(source_dependencies)
        else:
            dependency_projection.pop(source_id, None)
        if "platform_support" in source or "runtime_dependencies" in source:
            runtime = {
                "platform_support": deepcopy(source.get("platform_support")),
                "runtime_dependencies": deepcopy(
                    source.get("runtime_dependencies", [])
                ),
            }
            runtime_projection[source_id] = deepcopy(runtime)
            predecessor_runtime_projection[source_id] = deepcopy(runtime)


def _apply_reviewed_interface_uses(
    repo_root: Path,
    documents: Mapping[Path, dict[str, Any]],
    migration_map: Mapping[str, Any] | None,
    public_exports: Mapping[str, object],
    predecessor_semantic_edges: dict[str, object],
) -> None:
    """Add only map-reviewed interface uses proven by exact Python imports."""

    raw_entries = _mechanical_conversion_section(migration_map).get(
        "reviewed_interface_uses", []
    )
    if not isinstance(raw_entries, list):
        raise InterfaceInjectionMigrationError(
            "reviewed_interface_uses must be a list"
        )
    migration_plan = (
        compile_migration_plan(migration_map) if migration_map is not None else None
    )
    predecessor_module_ids = {
        target: source
        for source, target in (
            migration_plan.module_renames.items() if migration_plan is not None else ()
        )
    }
    sources: dict[str, tuple[Path, dict[str, Any]]] = {}
    for module_path, module in documents.items():
        if module.get("node_type") != "module":
            continue
        module_root = module_path.parent
        declared_sources = module.get("sources")
        if not isinstance(declared_sources, Mapping):
            continue
        for source_id, reference in declared_sources.items():
            locator = reference.get("blueprint") if isinstance(reference, Mapping) else None
            path = locator.get("path") if isinstance(locator, Mapping) else None
            if (
                not isinstance(source_id, str)
                or locator.get("base") != "module-root"
                or not isinstance(path, str)
            ):
                continue
            source = documents.get(module_root / path)
            if isinstance(source, dict):
                physical_module_root = module_root
                predecessor_id = predecessor_module_ids.get(module.get("id"))
                if predecessor_id is not None:
                    physical_module_root = module_root.with_name(predecessor_id)
                sources[source_id] = (physical_module_root, source)

    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, Mapping) or set(entry) != {
            "consumer",
            "interface",
            "version",
            "python_import",
        }:
            raise InterfaceInjectionMigrationError(
                f"reviewed_interface_uses[{index}] is invalid"
            )
        consumer = entry.get("consumer")
        interface_id = entry.get("interface")
        version = entry.get("version")
        python_import = entry.get("python_import")
        if (
            not isinstance(consumer, str)
            or not isinstance(interface_id, str)
            or not isinstance(version, int)
            or isinstance(version, bool)
            or not isinstance(python_import, str)
            or not python_import
            or (consumer, interface_id) in seen
        ):
            raise InterfaceInjectionMigrationError(
                f"reviewed_interface_uses[{index}] is invalid"
            )
        seen.add((consumer, interface_id))
        record = sources.get(consumer)
        if record is None:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: reviewed interface-use source is unresolved"
            )
        export = public_exports.get(interface_id)
        if not isinstance(export, Mapping) or export.get("version") != version:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: reviewed interface {interface_id!r} is unresolved"
            )
        module_root, source = record
        patterns = source.get("content")
        if not isinstance(patterns, list):
            raise InterfaceInjectionMigrationError(
                f"{consumer}: source content must be a list"
            )
        imports: set[str] = set()
        physical_root = repo_root / module_root
        for path in sorted(physical_root.rglob("*.py")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(physical_root).as_posix()
            if not any(
                isinstance(pattern, str)
                and re.fullmatch(pattern, relative) is not None
                for pattern in patterns
            ):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as exc:
                raise InterfaceInjectionMigrationError(
                    f"{consumer}: cannot inspect Python imports: {exc}"
                ) from exc
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module)
                    imports.update(
                        f"{node.module}.{alias.name}"
                        for alias in node.names
                        if alias.name != "*"
                    )
        if python_import not in imports:
            raise InterfaceInjectionMigrationError(
                f"{consumer}: reviewed import {python_import!r} is not present"
            )
        use = {"interface": interface_id, "version": version}
        uses = source.get("uses_interfaces")
        if not isinstance(uses, list):
            raise InterfaceInjectionMigrationError(
                f"{consumer}: uses_interfaces must be a list"
            )
        if use not in uses:
            uses.append(use)
            uses.sort(key=lambda value: (str(value.get("interface")), value.get("version", 0)))
        projection = predecessor_semantic_edges.get(consumer)
        if not isinstance(projection, dict):
            raise InterfaceInjectionMigrationError(
                f"{consumer}: predecessor semantic projection is unresolved"
            )
        projected_uses = projection.get("uses_interfaces")
        if not isinstance(projected_uses, list):
            raise InterfaceInjectionMigrationError(
                f"{consumer}: predecessor interface uses are invalid"
            )
        if use not in projected_uses:
            projected_uses.append(deepcopy(use))
            projected_uses.sort(
                key=lambda value: (str(value.get("interface")), value.get("version", 0))
            )


def convert_blueprint_declarations(
    repo_root: Path,
    mapped_paths: Sequence[Path],
    *,
    migration_map: Mapping[str, Any] | None = None,
) -> BlueprintDeclarationConversion:
    """Convert exactly the mapped declarations without touching either tree."""

    root = Path(repo_root).resolve()
    migration_plan = (
        compile_migration_plan(migration_map) if migration_map is not None else None
    )
    allowed_source_paths: set[Path] | None = None
    tracked = run_git(root, "ls-files", "-z", check=False)
    if tracked.returncode == 0:
        selected = {
            Path(os.fsdecode(raw))
            for raw in tracked.stdout.rstrip(b"\0").split(b"\0")
            if raw
        }
        if migration_plan is not None:
            selected.update(migration_plan.local_source_includes)
        allowed_source_paths = {(root / path).resolve() for path in selected}
    if migration_map is not None:
        _require_python_package_support_policy(migration_map)
    relative_paths = tuple(Path(path) for path in mapped_paths)
    if len(set(relative_paths)) != len(relative_paths):
        raise InterfaceInjectionMigrationError("mapped blueprint paths contain duplicates")
    _validate_reviewed_generated_field_ignore_consumption(
        relative_paths, migration_map
    )
    declarations: dict[Path, dict[str, Any]] = {}
    for relative in relative_paths:
        _require_relative_path(relative.as_posix(), "mapped blueprint")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise InterfaceInjectionMigrationError(
                f"mapped blueprint is not a regular file: {relative.as_posix()}"
            )
        declarations[relative] = _apply_reviewed_generated_field_ignores(
            relative, _read_mapping(path), migration_map
        )
    versions = {declaration.get("schema_version") for declaration in declarations.values()}
    if versions == {4}:
        public, runtime, behavioral = _v4_document_projections(declarations)
        return BlueprintDeclarationConversion(
            documents=dict(sorted(declarations.items())),
            removed_paths=(),
            public_graph_projection=public,
            runtime_dependency_projection=runtime,
            behavioral_source_dependency_projection=behavioral,
            predecessor_semantic_edge_projection=_v4_semantic_edge_projection(
                declarations
            ),
            predecessor_public_graph_projection=deepcopy(public),
            predecessor_runtime_dependency_projection=deepcopy(runtime),
        )
    if 4 in versions:
        raise InterfaceInjectionMigrationError(
            "conversion input cannot mix version 4 and legacy declarations"
        )
    excluded = {(root / path).resolve() for path in declarations}
    reference_documents, reference_targets = _reference_module_documents(
        root,
        migration_map,
        excluded_blueprints=excluded,
        allowed_source_paths=allowed_source_paths,
    )
    behavior_dependency_mappings = _legacy_behavior_dependency_mappings(
        migration_map, reference_targets
    )
    roots, typed_interfaces, typed_sources = _legacy_declaration_inputs(
        declarations
    )
    interfaces_by_module = _legacy_module_interface_inputs(
        roots, typed_interfaces, migration_map
    )
    source_targets: dict[str, tuple[str, Path, _BehavioralSourceInput]] = {}
    for old_module_id, entries in sorted(typed_sources.items()):
        module_id = _target_module_id(old_module_id, migration_plan)
        for item in entries:
            source_id = (
                f"{module_id}.source."
                + item.old_id.split(".source.", 1)[1]
            )
            target = (
                Path("skills")
                / module_id
                / "blueprints"
                / f"{_source_slug(item.gateway_path)}.yaml"
            )
            if item.old_id in source_targets:
                raise InterfaceInjectionMigrationError(
                    f"duplicate behavioral source ID {item.old_id!r}"
                )
            source_targets[item.old_id] = (source_id, target, item)
    (
        predecessor_semantic_edges,
        predecessor_public_graph,
        predecessor_runtime_dependencies,
    ) = _legacy_predecessor_projections(
        root,
        interfaces_by_module,
        typed_sources,
        source_targets,
        migration_plan,
        behavior_dependency_mappings=behavior_dependency_mappings,
    )
    supplemental_semantic_edges = _v4_semantic_edge_projection(reference_documents)
    overlap = set(predecessor_semantic_edges) & set(supplemental_semantic_edges)
    if overlap:
        raise InterfaceInjectionMigrationError(
            f"supplemental source IDs collide with legacy sources: {sorted(overlap)}"
        )
    predecessor_semantic_edges.update(supplemental_semantic_edges)
    (
        supplemental_public_graph,
        supplemental_runtime_dependencies,
        supplemental_behavioral_dependencies,
    ) = _v4_document_projections(reference_documents)
    predecessor_exports = predecessor_public_graph.get("exports")
    supplemental_exports = supplemental_public_graph.get("exports")
    if not isinstance(predecessor_exports, dict) or not isinstance(
        supplemental_exports, dict
    ):
        raise InterfaceInjectionMigrationError(
            "supplemental public graph projection is invalid"
        )
    duplicate_exports = set(predecessor_exports) & set(supplemental_exports)
    if duplicate_exports:
        raise InterfaceInjectionMigrationError(
            f"supplemental exports collide with legacy exports: {sorted(duplicate_exports)}"
        )
    predecessor_exports.update(deepcopy(supplemental_exports))
    predecessor_runtime_dependencies.update(
        deepcopy(supplemental_runtime_dependencies)
    )

    migration_findings: list[MigrationFinding] = []

    documents: dict[Path, dict[str, Any]] = dict(reference_documents)
    public_exports: dict[str, object] = deepcopy(supplemental_exports)
    runtime_sources: dict[str, object] = deepcopy(supplemental_runtime_dependencies)
    behavioral_dependencies: dict[str, object] = {
        str(document["id"]): deepcopy(document["dependencies"])
        for document in reference_documents.values()
        if document.get("node_type") == "behavioral_source"
        and isinstance(document.get("dependencies"), list)
        and document["dependencies"]
    }
    behavioral_dependencies.update(deepcopy(supplemental_behavioral_dependencies))
    consumed_behavior_mappings: set[tuple[str, str]] = set()
    for old_module_id, (root_path, declaration) in sorted(roots.items()):
        module_id = _target_module_id(old_module_id, migration_plan)
        old_module_root = root / "skills" / old_module_id
        target_module_root = Path("skills") / module_id
        skill_file = old_module_root / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            raise InterfaceInjectionMigrationError(
                f"{old_module_id}: SKILL.md must be a regular file"
            )
        interfaces = interfaces_by_module[old_module_id]
        default_interface = _typed_default_interface_input(
            old_module_id, declaration
        )

        legacy_summary = declaration.get("skill_interface")
        if legacy_summary is not None:
            if not isinstance(legacy_summary, Mapping):
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: skill_interface must be a mapping"
                )
            default_candidates = [
                item
                for item in interfaces
                if item.gateway_path == "SKILL.md"
                and item.old_id.endswith(".llm.default")
            ]
            if len(default_candidates) != 1:
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: legacy skill_interface requires one default interface"
                )
            target_id = _rename_interface_id(
                default_candidates[0].old_id, migration_plan
            )
            for section in ("inputs", "outputs", "side_effects"):
                claims = legacy_summary.get(section, [])
                if not isinstance(claims, list) or not all(
                    isinstance(claim, str) for claim in claims
                ):
                    raise InterfaceInjectionMigrationError(
                        f"{old_module_id}: skill_interface.{section} must be a string list"
                    )
                for index, claim in enumerate(claims):
                    migration_findings.append(
                        MigrationFinding(
                            code="NEEDS_CONTEXT",
                            source_path=root_path,
                            field=f"skill_interface.{section}[{index}]",
                            message=(
                                "reconcile exact legacy claim against unique default "
                                f"interface {target_id} before retirement"
                            ),
                            target_id=target_id,
                            claim=claim,
                        )
                    )

        if declaration.get("schema_version") == 2:
            root_edges = declaration.get("interfaces", [])
            if not isinstance(root_edges, list) or not all(
                isinstance(edge, dict) for edge in root_edges
            ):
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: version 2 root interfaces must be a list"
                )
            declared_edges = {
                (edge.get("interface"), edge.get("version")) for edge in root_edges
            }
            if default_interface is not None:
                declared_edges.add(
                    (default_interface.old_id, default_interface.version)
                )
            actual_edges = {(item.old_id, item.version) for item in interfaces}
            if declared_edges != actual_edges:
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: root interface edges do not match mapped sidecars"
                )
        grouped: dict[str, list[_InterfaceInput]] = {}
        for interface in interfaces:
            gateway = old_module_root / interface.gateway_path
            if gateway.is_symlink() or not gateway.is_file():
                raise InterfaceInjectionMigrationError(
                    f"{interface.old_id}: gateway is not a regular file"
                )
            grouped.setdefault(interface.gateway_path, []).append(interface)

        sources: dict[str, Any] = {}
        exports: dict[str, Any] = {}
        authority_entries: list[dict[str, Any]] = []
        seen_source_paths: set[Path] = set()
        for gateway_path, source_interfaces in sorted(grouped.items()):
            slug = _source_slug(gateway_path)
            source_id = f"{module_id}.source.{slug}"
            source_path = target_module_root / "blueprints" / f"{slug}.yaml"
            if source_path in seen_source_paths:
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: source path collision at {source_path}"
                )
            seen_source_paths.add(source_path)
            languages = {item.gateway_language for item in source_interfaces}
            if len(languages) != 1:
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}:{gateway_path}: conflicting gateway languages"
                )
            platform_claims = [item.platform_support for item in source_interfaces]
            dependency_claims = [
                item.runtime_dependencies for item in source_interfaces
            ]
            platforms = [
                claim for claim in platform_claims if claim is not None
            ]
            dependencies = [
                claim for claim in dependency_claims if claim is not None
            ]
            reviewed_conflicts: Sequence[object] = ()
            if migration_map is not None:
                reviewed = (
                    migration_map.get("declarations", {})
                    .get("mechanical_conversion", {})
                    .get("shared_gateway_merge", {})
                    .get("reviewed_conflicts", [])
                )
                if isinstance(reviewed, list):
                    reviewed_conflicts = reviewed
            decision = next(
                (
                    item
                    for item in reviewed_conflicts
                    if isinstance(item, dict)
                    and item.get("module") == old_module_id
                    and item.get("gateway") == gateway_path
                ),
                None,
            )
            if platform_claims and any(
                item != platform_claims[0] for item in platform_claims[1:]
            ):
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}:{gateway_path}: conflicting platform_support"
                )
            dependency_keys = [
                tuple(sorted(yaml.safe_dump(dict(item), sort_keys=True) for item in value))
                for value in dependencies
            ]
            claims_differ = dependency_claims and any(
                item != dependency_claims[0] for item in dependency_claims[1:]
            )
            if claims_differ:
                if not isinstance(decision, dict) or decision.get(
                    "runtime_dependencies"
                ) != "set-union":
                    raise InterfaceInjectionMigrationError(
                        f"{old_module_id}:{gateway_path}: conflicting runtime dependencies"
                    )
                dependency_keys = [
                    tuple(sorted({entry for group in dependency_keys for entry in group}))
                ]
            content_paths = {gateway_path}
            mapped_behavior_dependencies: list[dict[str, Any]] = []
            for item in source_interfaces:
                content_paths.update(item.same_source_content)
                for evidence in item.behavior_evidence:
                    mapping_key = (
                        item.old_id,
                        yaml.safe_dump(evidence, sort_keys=True),
                    )
                    mapping = behavior_dependency_mappings.get(mapping_key)
                    if mapping is not None:
                        consumed_behavior_mappings.add(mapping_key)
                        mapped_behavior_dependencies.append(
                            {
                                "source": mapping.target["source"],
                                "version": mapping.target["version"],
                                "blueprint": deepcopy(mapping.target["blueprint"]),
                                "reason": evidence["reason"],
                            }
                        )
                    elif evidence["path"].startswith("$repo/"):
                        raise InterfaceInjectionMigrationError(
                            f"{item.old_id}: unresolved repository behavior source "
                            f"{evidence['path']!r}"
                        )
                    else:
                        content_paths.add(
                            _require_relative_path(
                                evidence["path"],
                                f"{item.old_id}.behavior_sources",
                            )
                        )
            for content_path in sorted(content_paths):
                candidate = old_module_root / content_path
                if candidate.is_symlink() or not candidate.is_file():
                    raise InterfaceInjectionMigrationError(
                        f"{source_id}: source content is not a regular file: {content_path}"
                    )
            all_uses = [edge for item in source_interfaces for edge in item.uses_interfaces]
            all_source_dependencies = [
                edge
                for item in source_interfaces
                for edge in item.source_dependencies
            ]
            normalized_dependencies = _normalize_source_dependencies(
                all_source_dependencies,
                source_targets,
                owner_module_id=module_id,
            )
            dependency_values: dict[str, dict[str, Any]] = {}
            for dependency in (
                *normalized_dependencies,
                *mapped_behavior_dependencies,
            ):
                key = yaml.safe_dump(dependency, sort_keys=True)
                dependency_values[key] = deepcopy(dependency)
            source_dependencies = [
                dependency_values[key] for key in sorted(dependency_values)
            ]
            source_document: dict[str, Any] = {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": 1,
                "gateway": {
                    "path": gateway_path,
                    "language": next(iter(languages)),
                },
                "content": [
                    _exact_content_pattern(path) for path in sorted(content_paths)
                ],
                "dependencies": source_dependencies,
                "uses_interfaces": _normalize_uses(all_uses, migration_plan),
                "interfaces": {},
            }
            if platforms:
                source_document["platform_support"] = deepcopy(platforms[0])
                source_document["runtime_dependencies"] = [
                    yaml.safe_load(item) for item in dependency_keys[0]
                ]
                runtime_sources[source_id] = {
                    "platform_support": deepcopy(platforms[0]),
                    "runtime_dependencies": deepcopy(
                        source_document["runtime_dependencies"]
                    ),
                }
            for item in sorted(source_interfaces, key=lambda value: value.local_name):
                intrinsic_id = f"{source_id}.interface.{item.local_name}"
                export_id = f"{module_id}.interface.{item.local_name}"
                interface_document: dict[str, Any] = {
                    "version": item.version,
                    "contract": {"direct_io": deepcopy(item.direct_io)},
                }
                if item.description is not None:
                    interface_document["description"] = item.description
                if item.usage is not None:
                    interface_document["usage"] = item.usage
                if item.has_process_binding:
                    process_binding: dict[str, Any] = {"kind": "process"}
                    if item.process_entry is not None:
                        process_binding["entry"] = item.process_entry
                    if item.args_prefix:
                        process_binding["args_prefix"] = list(item.args_prefix)
                    if item.patterns:
                        process_binding["patterns"] = [
                            deepcopy(pattern) for pattern in item.patterns
                        ]
                    interface_document["process_binding"] = process_binding
                source_document["interfaces"][intrinsic_id] = interface_document
                allowed_callers = sorted(
                    _target_module_id(caller, migration_plan)
                    for caller in item.allowed_callers
                )
                access = {
                    "allow_all_modules": item.allow_all_modules,
                    "allowed_callers": allowed_callers,
                }
                exports[export_id] = {
                    "source_interface": intrinsic_id,
                    "access": access,
                }
                public_exports[export_id] = {
                    "version": item.version,
                    "source_interface": intrinsic_id,
                    "access": deepcopy(access),
                }
                authority_entries.extend(
                    _normalize_ownership(item.owns_filesystem, migration_plan)
                )
            documents[source_path] = source_document
            if source_dependencies:
                behavioral_dependencies[source_id] = deepcopy(source_dependencies)
            sources[source_id] = {
                "blueprint": {
                    "base": "module-root",
                    "path": f"blueprints/{slug}.yaml",
                }
            }

        for source_input in sorted(
            typed_sources.get(old_module_id, ()), key=lambda item: item.old_id
        ):
            source_id, source_path, _ = source_targets[source_input.old_id]
            if source_path in seen_source_paths or source_path in documents:
                raise InterfaceInjectionMigrationError(
                    f"{old_module_id}: source path collision at {source_path}"
                )
            seen_source_paths.add(source_path)
            gateway = old_module_root / source_input.gateway_path
            if gateway.is_symlink() or not gateway.is_file():
                raise InterfaceInjectionMigrationError(
                    f"{source_input.old_id}: gateway is not a regular file"
                )
            typed_dependencies = _normalize_source_dependencies(
                source_input.dependencies,
                source_targets,
                owner_module_id=module_id,
            )
            source_document = {
                "schema_version": 4,
                "node_type": "behavioral_source",
                "id": source_id,
                "version": source_input.version,
                "gateway": {
                    "path": source_input.gateway_path,
                    "language": source_input.gateway_language,
                },
                "content": [_exact_content_pattern(source_input.gateway_path)],
                "dependencies": typed_dependencies,
                "uses_interfaces": [],
                "interfaces": {},
            }
            if source_input.description is not None:
                source_document["description"] = source_input.description
            documents[source_path] = source_document
            if typed_dependencies:
                behavioral_dependencies[source_id] = deepcopy(typed_dependencies)
            relative_source_path = source_path.relative_to(target_module_root)
            sources[source_id] = {
                "blueprint": {
                    "base": "module-root",
                    "path": relative_source_path.as_posix(),
                }
            }

        module_document: dict[str, Any] = {
            "schema_version": 4,
            "node_type": "module",
            "id": module_id,
            "version": 1,
            "category": declaration.get("category"),
            "role": declaration.get("role"),
            "kind": declaration.get("kind"),
            "gateway": {"path": "SKILL.md", "language": "Markdown"},
            "content": _regular_module_content(
                old_module_root, excluded, allowed_source_paths
            ),
            "discovery": {"mechanism": "skill"},
            "authority": {"owns_filesystem": authority_entries},
            "sources": sources,
            "exports": exports,
        }
        permissions = declaration.get("suggested_permissions")
        if isinstance(permissions, dict):
            module_document["authority"]["suggested_permissions"] = deepcopy(
                permissions
            )
        documents[target_module_root / "blueprint.yaml"] = module_document

    unused_behavior_mappings = sorted(
        set(behavior_dependency_mappings) - consumed_behavior_mappings
    )
    if unused_behavior_mappings:
        raise InterfaceInjectionMigrationError(
            "legacy behavior dependency mappings were not matched: "
            f"{[consumer for consumer, _ in unused_behavior_mappings]}"
        )

    _rewrite_same_module_uses_to_private(documents, public_exports)
    package_support_paths: set[Path] = set()
    _add_python_package_support_sources(
        root,
        documents,
        behavioral_dependencies,
        package_support_paths,
        import_search_roots=(
            _mechanical_conversion_section(migration_map)
            .get("python_package_support", _PYTHON_PACKAGE_SUPPORT_POLICY)
            .get("import_search_roots", {})
        ),
        allowed_source_paths=allowed_source_paths,
        fixed_source_ids=set(reference_targets),
    )
    _apply_reviewed_source_facts(
        root,
        documents,
        migration_map,
        public_exports,
        behavioral_dependencies,
        runtime_sources,
        predecessor_semantic_edges,
        predecessor_runtime_dependencies,
        allowed_source_paths=allowed_source_paths,
    )
    _apply_reviewed_interface_uses(
        root,
        documents,
        migration_map,
        public_exports,
        predecessor_semantic_edges,
    )

    return BlueprintDeclarationConversion(
        documents=dict(sorted(documents.items(), key=lambda item: item[0].as_posix())),
        removed_paths=tuple(sorted(relative_paths)),
        public_graph_projection={"exports": dict(sorted(public_exports.items()))},
        runtime_dependency_projection=dict(sorted(runtime_sources.items())),
        behavioral_source_dependency_projection=dict(
            sorted(behavioral_dependencies.items())
        ),
        predecessor_semantic_edge_projection=predecessor_semantic_edges,
        predecessor_public_graph_projection=predecessor_public_graph,
        predecessor_runtime_dependency_projection=predecessor_runtime_dependencies,
        package_support_paths=tuple(sorted(package_support_paths)),
        findings=tuple(migration_findings),
    )


def _candidate_relative_path(
    path: Path, migration_plan: CompiledMigrationPlan | None = None
) -> Path:
    if len(path.parts) >= 2 and path.parts[0] == "skills":
        target = _target_module_id(path.parts[1], migration_plan)
        if target != path.parts[1]:
            return Path("skills") / target / Path(*path.parts[2:])
    return path


def _validate_created_path_predecessors(
    migration_map: Mapping[str, Any],
    conversion: BlueprintDeclarationConversion,
) -> None:
    raw_predecessors = migration_map.get("functional_predecessors")
    if not isinstance(raw_predecessors, list):
        raise InterfaceInjectionMigrationError(
            "migration map requires functional_predecessors"
        )
    patterns: list[str] = []
    for index, entry in enumerate(raw_predecessors):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"proposed", "disposition", "predecessor"}
            or entry.get("disposition")
            not in {"preserve", "move", "rename", "derive", "retire"}
            or not isinstance(entry.get("predecessor"), str)
            or not entry["predecessor"]
        ):
            raise InterfaceInjectionMigrationError(
                f"functional_predecessors[{index}] is invalid"
            )
        patterns.append(
            _normalized_pattern(
                entry.get("proposed"),
                f"functional_predecessors[{index}].proposed",
            )
        )

    old_paths = set(conversion.removed_paths)
    migration_plan = compile_migration_plan(migration_map)
    renamed_prefixes = tuple(
        Path("skills") / target
        for target in migration_plan.module_renames.values()
    )
    for path in conversion.documents:
        if (
            path in old_paths
            or any(path.is_relative_to(prefix) for prefix in renamed_prefixes)
            or path in conversion.package_support_paths
        ):
            continue
        matches = [
            pattern
            for pattern in patterns
            if not pattern.endswith("/")
            and (
                path.as_posix() == pattern
                if not any(character in pattern for character in "*?[")
                else path.match(pattern)
            )
        ]
        if len(matches) != 1:
            raise InterfaceInjectionMigrationError(
                f"{path.as_posix()}: expected one functional predecessor, found {matches}"
            )


def _source_overlay_status(
    repo_root: Path,
) -> tuple[dict[Path, str], bytes, bytes]:
    """Return one canonical, policy-comparable worktree/index fingerprint."""

    status = run_git(
        repo_root,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
        check=False,
    )
    index = run_git(repo_root, "ls-files", "--stage", "-z", check=False)
    if status.returncode != 0 or index.returncode != 0:
        raise InterfaceInjectionMigrationError("candidate source Git state is unavailable")
    overlay: dict[Path, str] = {}
    records = status.stdout.rstrip(b"\0").split(b"\0") if status.stdout else []
    cursor = 0
    while cursor < len(records):
        record = records[cursor]
        cursor += 1
        prefix = record[:1]
        if prefix in {b"2", b"u"}:
            raise InterfaceInjectionMigrationError(
                "candidate source rejects rename, copy, and unmerged index states"
            )
        if prefix == b"?":
            relative = Path(os.fsdecode(record[2:]))
            state = "added"
        elif prefix == b"1":
            fields = record.split(b" ", 8)
            if len(fields) != 9:
                raise InterfaceInjectionMigrationError(
                    "candidate source porcelain record is invalid"
                )
            xy = fields[1].decode("ascii")
            submodule = fields[2].decode("ascii")
            relative = Path(os.fsdecode(fields[8]))
            if submodule != "N...":
                raise InterfaceInjectionMigrationError(
                    "candidate source rejects submodule state"
                )
            index_state, worktree_state = xy
            if index_state != ".":
                raise InterfaceInjectionMigrationError(
                    "candidate source rejects staged index state"
                )
            if worktree_state in {"M", "T"}:
                state = "modified"
            elif worktree_state == "D":
                state = "deleted"
            else:
                raise InterfaceInjectionMigrationError(
                    "candidate source porcelain state is unsupported"
                )
        else:
            raise InterfaceInjectionMigrationError(
                "candidate source porcelain record is unsupported"
            )
        _require_relative_path(relative.as_posix(), "candidate source overlay")
        if relative in overlay:
            raise InterfaceInjectionMigrationError(
                f"candidate source overlay path is duplicated: {relative.as_posix()}"
            )
        overlay[relative] = state
    return dict(sorted(overlay.items())), status.stdout, index.stdout


def _copy_candidate_tree(
    repo_root: Path,
    candidate_root: Path,
    migration_plan: CompiledMigrationPlan,
    *,
    allow_non_atomic: bool = False,
) -> SourceMaterializationSnapshot:
    """Copy tracked worktree files plus exact map-reviewed local inputs only."""

    snapshot = capture_git_snapshot(repo_root)
    if snapshot is None or snapshot.repo_root != repo_root:
        raise InterfaceInjectionMigrationError(
            "candidate source requires the exact Git repository root"
        )
    actual_overlay, porcelain, index = _source_overlay_status(repo_root)
    expected_overlay = dict(migration_plan.authorized_overlay)
    for relative in migration_plan.local_source_includes:
        ignored = run_git(
            repo_root, "check-ignore", "--quiet", "--", relative.as_posix(), check=False
        )
        if ignored.returncode == 0:
            raise InterfaceInjectionMigrationError(
                f"candidate source include is ignored: {relative.as_posix()}"
            )
        if ignored.returncode not in {0, 1}:
            raise InterfaceInjectionMigrationError(
                f"candidate source ignore status is unavailable: {relative.as_posix()}"
            )
    if actual_overlay != expected_overlay:
        raise InterfaceInjectionMigrationError(
            "candidate source overlay policy differs from actual state: "
            f"expected={[(p.as_posix(), s) for p, s in expected_overlay.items()]}, "
            f"actual={[(p.as_posix(), s) for p, s in actual_overlay.items()]}"
        )
    entries: dict[Path, tuple[str, bytes, int]] = {}
    for relative, state in actual_overlay.items():
        _require_relative_path(relative.as_posix(), "candidate source")
        source = repo_root / relative
        if state == "deleted":
            entries[relative] = ("deleted", b"", 0)
            continue
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise InterfaceInjectionMigrationError(
                f"candidate source is unavailable: {relative.as_posix()}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(source)
            if not _tree_symlink_is_confined(relative, target):
                raise InterfaceInjectionMigrationError(
                    f"candidate source rejects escaping symlink: {relative.as_posix()}"
                )
            kind, data, mode = "symlink", os.fsencode(target), 0
        elif stat.S_ISREG(metadata.st_mode):
            try:
                data = read_regular_file_bytes(source, allowed_root=repo_root)
            except AtomicWriteError as exc:
                raise InterfaceInjectionMigrationError(
                    f"candidate source cannot be read safely: {relative.as_posix()}"
                ) from exc
            kind, mode = "file", stat.S_IMODE(metadata.st_mode)
        else:
            raise InterfaceInjectionMigrationError(
                "candidate source is not a regular file or symlink: "
                f"{relative.as_posix()}"
            )
        entries[relative] = (kind, data, mode)
    try:
        candidate_root.chmod(0o700)
        materialize_git_commit(
            repo_root,
            snapshot.commit,
            candidate_root,
            allow_non_atomic=allow_non_atomic,
        )
    except GitMaterializationError as exc:
        raise InterfaceInjectionMigrationError(
            f"cannot materialize captured source HEAD: {exc}"
        ) from exc
    return SourceMaterializationSnapshot(snapshot, entries, porcelain, index)


def _apply_source_overlay(
    candidate_root: Path, snapshot: SourceMaterializationSnapshot
) -> tuple[Path, ...]:
    changed: list[Path] = []
    for relative, (kind, data, mode) in sorted(snapshot.entries.items()):
        target = _ensure_real_parent(candidate_root, relative, context="source overlay")
        if kind == "deleted":
            if target.is_symlink() or target.is_file():
                target.unlink()
                changed.append(relative)
            continue
        current_kind = (
            "symlink"
            if target.is_symlink()
            else "file"
            if target.is_file()
            else None
        )
        current_data = (
            os.fsencode(os.readlink(target))
            if current_kind == "symlink"
            else target.read_bytes()
            if current_kind == "file"
            else None
        )
        current_mode = (
            stat.S_IMODE(target.stat().st_mode) if current_kind == "file" else 0
        )
        if current_kind == kind and current_data == data and current_mode == mode:
            continue
        if target.exists() or target.is_symlink():
            if not (target.is_file() or target.is_symlink()):
                raise InterfaceInjectionMigrationError(
                    f"unsafe source overlay target: {relative.as_posix()}"
                )
            target.unlink()
        if kind == "symlink":
            os.symlink(os.fsdecode(data), target)
        else:
            _atomic_candidate_write(
                candidate_root, relative, data, context="source overlay"
            )
            target.chmod(mode)
        changed.append(relative)
    return tuple(changed)


def _apply_candidate_module_renames(
    candidate_root: Path, migration_plan: CompiledMigrationPlan
) -> tuple[tuple[Path, Path], ...]:
    renamed: list[tuple[Path, Path]] = []
    for source_module, target_module in migration_plan.module_renames.items():
        source_relative = Path("skills") / source_module
        target_relative = Path("skills") / target_module
        source = candidate_root / source_relative
        target = _ensure_real_parent(
            candidate_root, target_relative, context="module rename"
        )
        if source.is_symlink() or not source.is_dir():
            raise InterfaceInjectionMigrationError(
                f"module rename source is unavailable: {source_relative.as_posix()}"
            )
        if target.exists() or target.is_symlink():
            raise InterfaceInjectionMigrationError(
                f"module rename target already exists: {target_relative.as_posix()}"
            )
        source.rename(target)
        renamed.append((source_relative, target_relative))
    return tuple(renamed)


def _apply_candidate_module_literal_renames(
    candidate_root: Path, migration_plan: CompiledMigrationPlan
) -> tuple[Path, ...]:
    """Rewrite exact map-reviewed path literals for renamed modules."""

    changed: list[Path] = []
    for relative in migration_plan.literal_rewrite_paths:
        target = _ensure_real_parent(
            candidate_root, relative, context="module rename literal"
        )
        if target.is_symlink() or not target.is_file():
            raise InterfaceInjectionMigrationError(
                f"module rename literal file is unavailable: {relative.as_posix()}"
            )
        try:
            values = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InterfaceInjectionMigrationError(
                f"invalid module rename literal file: {relative.as_posix()}"
            ) from exc
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise InterfaceInjectionMigrationError(
                f"module rename literal file must be a string list: {relative.as_posix()}"
            )
        rewritten = list(values)
        for index, value in enumerate(rewritten):
            for source, destination in migration_plan.module_renames.items():
                prefix = f"skills/{source}/"
                if value.startswith(prefix):
                    rewritten[index] = (
                        f"skills/{destination}/{value[len(prefix):]}"
                    )
        if rewritten != values:
            _atomic_candidate_write(
                candidate_root,
                relative,
                (json.dumps(rewritten, indent=2) + "\n").encode("utf-8"),
                context="module rename literal",
            )
            changed.append(relative)
    return tuple(changed)


def _reviewed_public_id_renames(
    migration_map: Mapping[str, Any],
    migration_plan: CompiledMigrationPlan,
) -> dict[str, str]:
    public_ids = migration_map.get("public_ids")
    if not isinstance(public_ids, Mapping):
        raise InterfaceInjectionMigrationError("migration map requires public_ids")
    machine = public_ids.get("machine_ids")
    llm = public_ids.get("llm_ids")
    machine_ids = machine.get("ids") if isinstance(machine, Mapping) else None
    default_modules = llm.get("default_modules") if isinstance(llm, Mapping) else None
    named_llm = llm.get("named") if isinstance(llm, Mapping) else None
    if not all(
        isinstance(value, list)
        for value in (machine_ids, default_modules, named_llm)
    ):
        raise InterfaceInjectionMigrationError("public ID inventory is incomplete")
    old_ids = [
        *(value for value in machine_ids if isinstance(value, str)),
        *(f"{value}.llm.default" for value in default_modules if isinstance(value, str)),
        *(value for value in named_llm if isinstance(value, str)),
    ]
    if len(old_ids) != len(set(old_ids)):
        raise InterfaceInjectionMigrationError(
            "public ID inventory contains duplicates"
        )
    return {
        old_id: _rename_interface_id(old_id, migration_plan)
        for old_id in sorted(old_ids)
    }


def _apply_candidate_public_id_literal_renames(
    candidate_root: Path,
    migration_map: Mapping[str, Any],
    migration_plan: CompiledMigrationPlan,
) -> tuple[Path, ...]:
    """Rewrite reviewed public-ID literals in explicitly named UTF-8 files."""

    replacements = _reviewed_public_id_renames(migration_map, migration_plan)
    changed: list[Path] = []
    for relative in migration_plan.public_id_literal_paths:
        target = _ensure_real_parent(
            candidate_root, relative, context="public ID literal"
        )
        if target.is_symlink() or not target.is_file():
            raise InterfaceInjectionMigrationError(
                f"public ID literal file is unavailable: {relative.as_posix()}"
            )
        try:
            original = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise InterfaceInjectionMigrationError(
                f"invalid public ID literal file: {relative.as_posix()}"
            ) from exc
        rewritten = original
        for source, destination in replacements.items():
            rewritten = rewritten.replace(source, destination)
        if rewritten != original:
            _atomic_candidate_write(
                candidate_root,
                relative,
                rewritten.encode("utf-8"),
                context="public ID literal",
            )
            changed.append(relative)
    return tuple(changed)


def _verify_source_materialization_snapshot(
    repo_root: Path, snapshot: SourceMaterializationSnapshot
) -> None:
    if not snapshot_head_matches(snapshot.git):
        raise InterfaceInjectionMigrationError("source HEAD changed during materialization")
    _overlay, porcelain, index = _source_overlay_status(repo_root)
    if porcelain != snapshot.porcelain or index != snapshot.index:
        raise InterfaceInjectionMigrationError(
            "source Git/index state changed during materialization"
        )
    for relative, (kind, expected, expected_mode) in snapshot.entries.items():
        path = repo_root / relative
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if kind == "deleted":
                continue
            raise InterfaceInjectionMigrationError(
                f"source input changed during materialization: {relative.as_posix()}"
            )
        except OSError as exc:
            raise InterfaceInjectionMigrationError(
                f"source input changed during materialization: {relative.as_posix()}"
            ) from exc
        actual_kind = "symlink" if stat.S_ISLNK(metadata.st_mode) else "file"
        if actual_kind == "symlink":
            actual = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            try:
                actual = read_regular_file_bytes(path, allowed_root=repo_root)
            except AtomicWriteError as exc:
                raise InterfaceInjectionMigrationError(
                    f"source input changed during materialization: {relative.as_posix()}"
                ) from exc
        else:
            actual = b""
        actual_mode = stat.S_IMODE(metadata.st_mode) if actual_kind == "file" else 0
        if (
            actual_kind != kind
            or actual != expected
            or actual_mode != expected_mode
        ):
            raise InterfaceInjectionMigrationError(
                f"source input changed during materialization: {relative.as_posix()}"
            )


def _yaml_document_bytes(document: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(document),
        sort_keys=True,
        allow_unicode=True,
    ).encode("utf-8")


def _ensure_real_parent(root: Path, relative: Path, *, context: str) -> Path:
    """Create a target parent without ever traversing a symlink."""

    _require_relative_path(relative.as_posix(), context)
    current = root
    for part in relative.parent.parts:
        current = current / part
        try:
            status = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            status = current.lstat()
        if current.is_symlink() or not current.is_dir():
            raise InterfaceInjectionMigrationError(
                f"unsafe {context} parent: {relative.as_posix()}"
            )
    return root / relative


def _atomic_candidate_write(
    candidate_root: Path,
    relative: Path,
    content: bytes,
    *,
    context: str,
) -> None:
    try:
        atomic_candidate_write(
            candidate_root,
            relative,
            content,
            context=context,
        )
    except MigrationCandidateError as exc:
        raise InterfaceInjectionMigrationError(str(exc)) from exc


def _apply_candidate_conversion(
    candidate_root: Path,
    conversion: BlueprintDeclarationConversion,
    migration_plan: CompiledMigrationPlan | None = None,
) -> tuple[Path, ...]:
    changed: set[Path] = set()
    for old_path in conversion.removed_paths:
        relative = _candidate_relative_path(old_path, migration_plan)
        target = _ensure_real_parent(
            candidate_root, relative, context="mapped declaration"
        )
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise InterfaceInjectionMigrationError(
                f"unsafe mapped declaration target: {relative.as_posix()}"
            )
        if target.exists():
            target.unlink()
            changed.add(relative)
    for relative, document in conversion.documents.items():
        target = _ensure_real_parent(
            candidate_root, relative, context="converted declaration"
        )
        content = _yaml_document_bytes(document)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise InterfaceInjectionMigrationError(
                f"unsafe converted declaration target: {relative.as_posix()}"
            )
        if target.exists() and target.read_bytes() == content:
            continue
        _atomic_candidate_write(
            candidate_root,
            relative,
            content,
            context="converted declaration",
        )
        changed.add(relative)
    return tuple(sorted(changed))


def _python_node_span(text: str, node: ast.Constant) -> tuple[int, int]:
    lines = text.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: node.lineno - 1])
    end = sum(len(line) for line in lines[: node.end_lineno - 1])
    start += len(
        lines[node.lineno - 1].encode("utf-8")[: node.col_offset].decode("utf-8")
    )
    end += len(
        lines[node.end_lineno - 1]
        .encode("utf-8")[: node.end_col_offset]
        .decode("utf-8")
    )
    return start, end


def _quoted_replacement(text: str, node: ast.Constant, value: str) -> tuple[int, int, str]:
    start, end = _python_node_span(text, node)
    original = text[start:end]
    quote = original[0] if original[:1] in {"'", '"'} else "'"
    escaped = value.replace("\\", "\\\\").replace(quote, f"\\{quote}")
    return start, end, f"{quote}{escaped}{quote}"


def _dispatch_call_counter(
    repo_root: Path,
    *,
    allowed_paths: set[Path] | None = None,
) -> Counter[tuple[Path, str, str]]:
    """Count alias-resolved DispatchCall edges in live skill runtime code."""

    result: Counter[tuple[Path, str, str]] = Counter()
    skills_root = repo_root / "skills"
    runtime_files: list[Path] = []
    if skills_root.is_dir():
        for skill_root in sorted(skills_root.iterdir()):
            if not skill_root.is_dir() or skill_root.is_symlink():
                continue
            for runtime_name in ("_rtx", "bin"):
                runtime_root = skill_root / runtime_name
                if runtime_root.is_dir() and not runtime_root.is_symlink():
                    runtime_files.extend(runtime_root.rglob("*.py"))
    for path in sorted(runtime_files):
        relative = path.relative_to(repo_root)
        if (
            (allowed_paths is not None and relative not in allowed_paths)
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for declaration in analyze_dispatch_call_declarations(tree):
            if declaration.caller_skill is None or declaration.interface is None:
                continue
            interface = declaration.interface
            if ".interface." in interface or ".machine." in interface:
                target = interface
            elif declaration.target_skill is not None:
                target = f"{declaration.target_skill}.machine.{interface}"
            else:
                continue
            result[(relative, declaration.caller_skill, target)] += 1
    return result


def _assert_repository_dispatch_call_counter(
    repo_root: Path,
    migration_map: Mapping[str, Any],
    migration_plan: CompiledMigrationPlan,
) -> None:
    """Bind the reviewed caller inventory to tracked live source occurrences."""

    callers = migration_map.get("callers")
    declarations = callers.get("live_declarations") if isinstance(callers, dict) else None
    if not isinstance(declarations, list):
        raise InterfaceInjectionMigrationError("caller inventory is incomplete")
    expected: Counter[tuple[Path, str, str]] = Counter()
    for entry in declarations:
        if not isinstance(entry, Mapping):
            continue
        raw_file = entry.get("file")
        caller = entry.get("caller")
        old_targets = entry.get("old_targets")
        if (
            not isinstance(raw_file, str)
            or not isinstance(caller, str)
            or not isinstance(old_targets, list)
        ):
            continue
        path = Path(_require_relative_path(raw_file, "caller declaration file"))
        for target in old_targets:
            if isinstance(target, str):
                expected[(path, caller, target)] += 1
    tracked = run_git(repo_root, "ls-files", "-z")
    allowed_paths = {
        Path(os.fsdecode(raw))
        for raw in tracked.stdout.rstrip(b"\0").split(b"\0")
        if raw
    }
    allowed_paths.update(migration_plan.local_source_includes)
    observed = _dispatch_call_counter(repo_root, allowed_paths=allowed_paths)
    if observed != expected:
        missing = sorted((expected - observed).elements())
        unexpected = sorted((observed - expected).elements())
        raise InterfaceInjectionMigrationError(
            "repository DispatchCall edges differ from the reviewed caller map: "
            f"missing={missing}, unexpected={unexpected}"
        )


def _apply_candidate_caller_renames(
    candidate_root: Path,
    migration_map: Mapping[str, Any],
    migration_plan: CompiledMigrationPlan | None = None,
) -> tuple[Path, ...]:
    callers = migration_map.get("callers")
    if not isinstance(callers, dict):
        raise InterfaceInjectionMigrationError("migration map requires callers")
    declarations = callers.get("live_declarations")
    expected_count = callers.get("live_edge_count")
    if not isinstance(declarations, list) or not isinstance(expected_count, int):
        raise InterfaceInjectionMigrationError("caller inventory is incomplete")
    actual_count = sum(
        len(entry.get("old_targets", []))
        for entry in declarations
        if isinstance(entry, dict) and isinstance(entry.get("old_targets"), list)
    )
    if actual_count != expected_count:
        raise InterfaceInjectionMigrationError(
            f"caller edge count changed: expected {expected_count}, found {actual_count}"
        )

    expected_old: Counter[tuple[Path, str, str]] = Counter()
    expected_new: Counter[tuple[Path, str, str]] = Counter()
    for entry in declarations:
        if not isinstance(entry, Mapping):
            continue
        path = _candidate_relative_path(Path(str(entry.get("file"))), migration_plan)
        caller = entry.get("caller")
        caller_target = entry.get("caller_target")
        old_targets = entry.get("old_targets")
        if not isinstance(caller, str) or not isinstance(old_targets, list):
            continue
        expected_candidate_caller = (
            caller_target if isinstance(caller_target, str) else caller
        )
        for old_target in old_targets:
            if isinstance(old_target, str):
                expected_old[(path, caller, old_target)] += 1
                expected_new[
                    (
                        path,
                        expected_candidate_caller,
                        _rename_interface_id(old_target, migration_plan),
                    )
                ] += 1
    observed_before = _dispatch_call_counter(candidate_root)
    if observed_before not in (expected_old, expected_new):
        missing = sorted((expected_old - observed_before).elements())
        unexpected = sorted((observed_before - expected_old).elements())
        raise InterfaceInjectionMigrationError(
            "repository DispatchCall edges differ from the reviewed caller map: "
            f"missing={missing}, unexpected={unexpected}"
        )

    changed: list[Path] = []
    for index, entry in enumerate(declarations):
        if not isinstance(entry, dict):
            raise InterfaceInjectionMigrationError(
                f"callers.live_declarations[{index}] must be a mapping"
            )
        raw_path = Path(
            _require_relative_path(
                entry.get("file"), f"callers.live_declarations[{index}].file"
            )
        )
        path = _candidate_relative_path(raw_path, migration_plan)
        target = candidate_root / path
        if target.is_symlink() or not target.is_file():
            raise InterfaceInjectionMigrationError(
                f"caller declaration is not a regular file: {path.as_posix()}"
            )
        old_targets = entry.get("old_targets")
        if not isinstance(old_targets, list) or not all(
            isinstance(value, str) and ".machine." in value
            for value in old_targets
        ):
            raise InterfaceInjectionMigrationError(
                f"{path.as_posix()}: invalid old caller targets"
            )
        explicit_targets = entry.get("target")
        derived_targets = [
            _rename_interface_id(value, migration_plan) for value in old_targets
        ]
        if isinstance(explicit_targets, list) and explicit_targets != derived_targets:
            raise InterfaceInjectionMigrationError(
                f"{path.as_posix()}: caller target mapping is not exact"
            )
        text = target.read_text(encoding="utf-8")
        original = text
        try:
            tree = ast.parse(text, filename=path.as_posix())
        except SyntaxError as exc:
            raise InterfaceInjectionMigrationError(
                f"{path.as_posix()}: caller declaration is not valid Python: {exc}"
            ) from exc
        calls = [item.keywords for item in analyze_dispatch_call_declarations(tree)]
        replacements: list[tuple[int, int, str]] = []
        matched_calls: list[dict[str, ast.Constant]] = []
        for old_target, new_target in zip(old_targets, derived_targets):
            target_skill, local_name = old_target.split(".machine.", 1)
            old_matches: list[dict[str, ast.Constant]] = []
            new_matches: list[dict[str, ast.Constant]] = []
            for keywords in calls:
                target_node = keywords.get("target_skill")
                interface_node = keywords.get("interface")
                if (
                    target_node is None
                    or interface_node is None
                    or target_node.value != target_skill
                ):
                    continue
                if interface_node.value in {local_name, old_target}:
                    old_matches.append(keywords)
                elif interface_node.value == new_target:
                    new_matches.append(keywords)
            if len(old_matches) > 1 or (old_matches and new_matches):
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: caller target {old_target} is ambiguous"
                )
            if not old_matches:
                if len(new_matches) != 1:
                    raise InterfaceInjectionMigrationError(
                        f"{path.as_posix()}: caller target {old_target} was not found exactly once"
                    )
                matched_calls.extend(new_matches)
                continue
            matched = old_matches[0]
            matched_calls.append(matched)
            replacements.append(
                _quoted_replacement(text, matched["interface"], new_target)
            )
        caller_target = entry.get("caller_target")
        caller = entry.get("caller")
        if caller_target is not None:
            if not isinstance(caller, str) or not isinstance(caller_target, str):
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: invalid caller rename"
                )
            if caller_target != _target_module_id(caller, migration_plan):
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: caller rename is not map-derived"
                )
            caller_nodes = [
                keywords["caller_skill"]
                for keywords in matched_calls
                if "caller_skill" in keywords
                and keywords["caller_skill"].value == caller
            ]
            existing_nodes = [
                keywords["caller_skill"]
                for keywords in matched_calls
                if "caller_skill" in keywords
                and keywords["caller_skill"].value == caller_target
            ]
            if caller_nodes:
                replacements.extend(
                    _quoted_replacement(text, node, caller_target)
                    for node in caller_nodes
                )
            elif not existing_nodes:
                raise InterfaceInjectionMigrationError(
                    f"{path.as_posix()}: caller {caller!r} was not found"
                )
        for start, end, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[end:]
        if text != original:
            _atomic_candidate_write(
                candidate_root,
                path,
                text.encode("utf-8"),
                context="caller declaration",
            )
            changed.append(path)
    observed_after = _dispatch_call_counter(candidate_root)
    if observed_after != expected_new:
        missing = sorted((expected_new - observed_after).elements())
        unexpected = sorted((observed_after - expected_new).elements())
        raise InterfaceInjectionMigrationError(
            "candidate DispatchCall edges differ from the reviewed caller map: "
            f"missing={missing}, unexpected={unexpected}"
        )
    return tuple(sorted(changed))


def _assert_candidate_projections(
    graph: RepositoryBlueprintGraph,
    conversion: BlueprintDeclarationConversion,
) -> None:
    documents = {
        node.blueprint_path: node.declaration for node in graph.nodes.values()
    }
    public, runtime, behavioral = _v4_document_projections(documents)
    semantic_edges = _v4_semantic_edge_projection(documents)
    checks = (
        (
            "public graph",
            public,
            conversion.predecessor_public_graph_projection,
        ),
        (
            "runtime dependency",
            runtime,
            conversion.predecessor_runtime_dependency_projection,
        ),
        (
            "behavioral source dependency",
            behavioral,
            {
                source_id: deepcopy(entry["dependencies"])
                for source_id, entry in conversion.predecessor_semantic_edge_projection.items()
                if isinstance(entry, Mapping) and entry.get("dependencies")
            },
        ),
    )
    for label, actual, expected in checks:
        if actual != expected:
            raise InterfaceInjectionMigrationError(
                f"candidate {label} projection differs from the legacy projection: "
                f"expected={expected!r} actual={actual!r}"
            )
    def canonical_edge_values(values: object) -> list[object]:
        if not isinstance(values, list):
            raise InterfaceInjectionMigrationError(
                "semantic edge projection must contain lists"
            )
        return [
            yaml.safe_load(value)
            for value in sorted(
                yaml.safe_dump(item, sort_keys=True)
                for item in values
                if isinstance(item, Mapping)
            )
        ]

    expected_edges = {
        source_id: {
            "dependencies": canonical_edge_values(entry.get("dependencies", [])),
            "uses_interfaces": canonical_edge_values(entry.get("uses_interfaces", [])),
        }
        for source_id, entry in conversion.predecessor_semantic_edge_projection.items()
        if isinstance(entry, Mapping)
    }
    actual_edges = {
        source_id: {
            "dependencies": canonical_edge_values(entry.get("dependencies", [])),
            "uses_interfaces": canonical_edge_values(entry.get("uses_interfaces", [])),
        }
        for source_id, entry in semantic_edges.items()
        if isinstance(entry, Mapping)
    }
    differing = sorted(
        source_id
        for source_id, expected in expected_edges.items()
        if actual_edges.get(source_id) != expected
    )
    unexpected_edges = sorted(
        source_id
        for source_id, actual in actual_edges.items()
        if source_id not in expected_edges
        and isinstance(actual, Mapping)
        and (actual.get("dependencies") or actual.get("uses_interfaces"))
    )
    differing.extend(unexpected_edges)
    if differing:
        first = differing[0]
        raise InterfaceInjectionMigrationError(
            "candidate semantic edge projection differs from the independently "
            f"read legacy projection: {differing}; first={first!r} "
            f"expected={expected_edges.get(first)!r} actual={actual_edges.get(first)!r}"
        )


_CANDIDATE_CERTIFIER_BOOTSTRAP = r"""
import importlib.util
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
action = sys.argv[2]
candidate_src = (root / "src").resolve()
sys.path.insert(0, str(candidate_src))
certifier_path = root / sys.argv[3]
spec = importlib.util.spec_from_file_location(
    "candidate_skill_certifier", certifier_path
)
if spec is None or spec.loader is None:
    raise RuntimeError("candidate certifier cannot be loaded")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if action == "inspect":
    result = module.inspect_v4_migration_candidate(root)
    payload = {
        "node_ids": list(result.node_ids),
        "source_commit": result.source_commit,
        "findings": [
            {
                "subject_id": finding.subject_id,
                "blueprint_path": Path(finding.blueprint_path).relative_to(root).as_posix(),
                "field": finding.field,
                "message": finding.message,
            }
            for finding in result.findings
        ],
        "review_context": [
            {
                "subject_id": item.subject_id,
                "blueprint_path": Path(item.blueprint_path).relative_to(root).as_posix(),
                "field": item.field,
                "message": item.message,
                "target_id": getattr(item, "target_id", None),
                "claim": getattr(item, "claim", None),
            }
            for item in getattr(result, "review_context", ())
        ],
        "reconciliation_digest": getattr(result, "reconciliation_digest", None),
    }
elif action == "finalize":
    result = module.certify_v4_migration_candidate(
        root,
        reviewed_commit=sys.argv[4],
        certified_at=sys.argv[5],
    )
    payload = {
        "node_ids": list(result.node_ids),
        "source_commit": result.source_commit,
    }
else:
    raise RuntimeError(f"unknown candidate certifier action: {action}")
print(json.dumps(payload, sort_keys=True))
"""


def _candidate_certifier_context(candidate_root: Path) -> tuple[Path, str, Path]:
    raw_root = Path(candidate_root)
    if raw_root.is_symlink():
        raise InterfaceInjectionMigrationError("unsafe candidate root")
    root = raw_root.resolve()
    if not root.is_dir() or not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise InterfaceInjectionMigrationError(
            "candidate root must be an existing temporary directory"
        )
    repository = run_git(root, "rev-parse", "--show-toplevel", check=False)
    if repository.returncode != 0:
        raise InterfaceInjectionMigrationError("candidate root is not a Git repository")
    try:
        discovered_root = Path(repository.stdout.decode("utf-8").strip()).resolve()
    except UnicodeError as exc:
        raise InterfaceInjectionMigrationError("candidate Git root is invalid") from exc
    if discovered_root != root:
        raise InterfaceInjectionMigrationError(
            "candidate root must equal the exact Git repository root"
        )
    atomic = run_git(
        root, "config", "--bool", "--get", "famulus.candidateAtomicGuarantee",
        check=False,
    )
    if atomic.returncode == 0 and atomic.stdout.strip() == b"false":
        raise InterfaceInjectionMigrationError(
            "non-atomic diagnostic candidate is non-certifiable"
        )
    head = run_git(root, "rev-parse", "HEAD", check=False)
    if head.returncode != 0:
        raise InterfaceInjectionMigrationError("candidate HEAD is unavailable")
    status = run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        check=False,
    )
    if status.returncode != 0 or status.stdout:
        raise InterfaceInjectionMigrationError(
            "candidate worktree and index must exactly match HEAD"
        )
    map_path = root / "docs/plans/unified-architecture-migration-map.yaml"
    migration_plan = compile_migration_plan(load_blueprint_migration_map(map_path))
    candidates = [
        Path("skills") / target / "_rtx" / "_node_certifier.py"
        for target in migration_plan.module_renames.values()
        if (root / "skills" / target / "_rtx" / "_node_certifier.py").is_file()
    ]
    if len(candidates) != 1:
        raise InterfaceInjectionMigrationError(
            "candidate certifier owner is not uniquely map-derived"
        )
    certifier_relative = candidates[0]
    executed_roots = (
        Path("docs/plans/unified-architecture-migration-map.yaml"),
        certifier_relative,
        Path("src/officina"),
    )
    tree = run_git(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        "HEAD",
        "--",
        *(path.as_posix() for path in executed_roots),
        check=False,
    )
    if tree.returncode != 0:
        raise InterfaceInjectionMigrationError(
            "candidate execution bytes are unavailable at HEAD"
        )
    tracked: set[Path] = set()
    for record in tree.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, _object_id = metadata.split(b" ", 2)
            relative = Path(os.fsdecode(raw_path))
        except (ValueError, UnicodeError) as exc:
            raise InterfaceInjectionMigrationError(
                "candidate HEAD tree is invalid"
            ) from exc
        if object_type != b"blob" or mode not in {b"100644", b"100755"}:
            raise InterfaceInjectionMigrationError(
                f"unsafe candidate execution entry: {relative.as_posix()}"
            )
        tracked.add(relative)
        current = root
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                current.lstat()
            except OSError as exc:
                raise InterfaceInjectionMigrationError(
                    f"candidate execution path is unavailable: {relative.as_posix()}"
                ) from exc
            if current.is_symlink():
                raise InterfaceInjectionMigrationError(
                    f"unsafe candidate execution path: {relative.as_posix()}"
                )
            if index < len(relative.parts) - 1 and not current.is_dir():
                raise InterfaceInjectionMigrationError(
                    f"unsafe candidate execution path: {relative.as_posix()}"
                )
        if not current.is_file():
            raise InterfaceInjectionMigrationError(
                f"unsafe candidate execution path: {relative.as_posix()}"
            )
    if certifier_relative not in tracked or executed_roots[0] not in tracked:
        raise InterfaceInjectionMigrationError(
            "candidate execution bytes are not tracked at HEAD"
        )
    if not any(path.is_relative_to(Path("src/officina")) for path in tracked):
        raise InterfaceInjectionMigrationError(
            "candidate src owner is unavailable at HEAD"
        )
    return root, head.stdout.decode("utf-8").strip(), certifier_relative


def _run_candidate_certifier(
    candidate_root: Path,
    action: str,
    *,
    reviewed_commit: str | None = None,
    certified_at: str | None = None,
) -> dict[str, Any]:
    root, head, certifier_relative = _candidate_certifier_context(candidate_root)
    if action == "finalize":
        if reviewed_commit != head:
            raise InterfaceInjectionMigrationError(
                "reviewed commit does not match candidate HEAD"
            )
        if not isinstance(certified_at, str) or not certified_at:
            raise InterfaceInjectionMigrationError(
                "candidate finalization requires certified_at"
            )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("GIT_")
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["FAMULUS_CANDIDATE_CERTIFIER"] = "1"
    execution_root = Path(tempfile.mkdtemp(prefix="famulus-v4-reviewed-"))
    try:
        added = run_git(
            root,
            "worktree",
            "add",
            "--detach",
            execution_root.as_posix(),
            head,
            check=False,
        )
        if added.returncode != 0:
            raise InterfaceInjectionMigrationError(
                "cannot privately materialize the reviewed candidate commit"
            )
        private_root, private_head, private_certifier = _candidate_certifier_context(
            execution_root
        )
        if private_head != head or private_certifier != certifier_relative:
            raise InterfaceInjectionMigrationError(
                "private candidate materialization does not match reviewed HEAD"
            )
        arguments = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _CANDIDATE_CERTIFIER_BOOTSTRAP,
            private_root.as_posix(),
            action,
            private_certifier.as_posix(),
        ]
        if action == "finalize":
            arguments.extend((reviewed_commit, certified_at))
        completed = subprocess.run(
            arguments,
            cwd=private_root,
            env=environment,
            check=False,
            capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        )
        # Independently recheck the parent candidate after candidate-owned code
        # returns; its bytes and reviewed HEAD must still be current.
        current_root, current_head, current_certifier = _candidate_certifier_context(root)
        if (
            current_root != root
            or current_head != head
            or current_certifier != certifier_relative
        ):
            raise InterfaceInjectionMigrationError(
                "candidate changed during private certifier execution"
            )
    finally:
        run_git(
            root,
            "worktree",
            "remove",
            "--force",
            execution_root.as_posix(),
            check=False,
        )
        shutil.rmtree(execution_root, ignore_errors=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InterfaceInjectionMigrationError(
            f"candidate certifier {action} failed: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InterfaceInjectionMigrationError(
            f"candidate certifier {action} returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict) or payload.get("source_commit") != head:
        raise InterfaceInjectionMigrationError(
            f"candidate certifier {action} did not bind exact HEAD"
        )
    return payload


def inspect_candidate_v4(candidate_root: Path) -> dict[str, Any]:
    """Run the read-only inspection API from candidate-owned bytes."""

    return _run_candidate_certifier(candidate_root, "inspect")


def finalize_candidate_v4(
    candidate_root: Path,
    *,
    reviewed_commit: str,
    certified_at: str,
) -> dict[str, Any]:
    """Run candidate-owned finalization for one exact reviewed commit."""

    return _run_candidate_certifier(
        candidate_root,
        "finalize",
        reviewed_commit=reviewed_commit,
        certified_at=certified_at,
    )


def _candidate_commit(
    candidate_root: Path, message: str, paths: Iterable[Path]
) -> str:
    try:
        return candidate_commit(candidate_root, message, paths)
    except MigrationCandidateError as exc:
        raise InterfaceInjectionMigrationError(str(exc)) from exc


def _candidate_cutover_manifest(
    candidate_root: Path, legacy_commit: str, v4_commit: str
) -> tuple[CutoverChange, ...]:
    try:
        return candidate_cutover_manifest(
            candidate_root,
            legacy_commit,
            v4_commit,
        )
    except MigrationCandidateError as exc:
        raise InterfaceInjectionMigrationError(str(exc)) from exc


def _assert_cutover_manifest_authorized(
    manifest: Sequence[CutoverChange],
    *,
    candidate_root: Path,
    source_overlay_commit: str,
    final_commit: str,
    exact_paths: Iterable[Path],
    migration_plan: CompiledMigrationPlan,
) -> None:
    """Require exact no-rename status equality with map-derived operations."""

    exact = set(exact_paths)
    tree = run_git(
        candidate_root,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        source_overlay_commit,
    )
    baseline_paths = {
        Path(os.fsdecode(raw))
        for raw in tree.stdout.rstrip(b"\0").split(b"\0")
        if raw
    }
    expected: dict[Path, str] = {}
    for source, target in migration_plan.module_renames.items():
        source_prefix = Path("skills") / source
        target_prefix = Path("skills") / target
        for old_path in baseline_paths:
            if not old_path.is_relative_to(source_prefix):
                continue
            relative = old_path.relative_to(source_prefix)
            expected[old_path] = "D"
            target_path = target_prefix / relative
            if run_git(
                candidate_root,
                "cat-file",
                "-e",
                f"{final_commit}:{target_path.as_posix()}",
                check=False,
            ).returncode == 0:
                expected[target_path] = "A"
    for path in exact:
        if path in expected:
            continue
        renamed_absent = any(
            path.is_relative_to(Path("skills") / target)
            and (
                Path("skills")
                / source
                / path.relative_to(Path("skills") / target)
            )
            in baseline_paths
            for source, target in migration_plan.module_renames.items()
        )
        if renamed_absent and run_git(
            candidate_root,
            "cat-file",
            "-e",
            f"{final_commit}:{path.as_posix()}",
            check=False,
        ).returncode != 0:
            continue
        before = run_git(
            candidate_root,
            "cat-file",
            "-e",
            f"{source_overlay_commit}:{path.as_posix()}",
            check=False,
        ).returncode == 0
        after = run_git(
            candidate_root,
            "cat-file",
            "-e",
            f"{final_commit}:{path.as_posix()}",
            check=False,
        ).returncode == 0
        if before and after:
            expected[path] = "M"
        elif before:
            expected[path] = "D"
        elif after:
            expected[path] = "A"
        else:
            raise InterfaceInjectionMigrationError(
                f"cutover operation produced no path: {path.as_posix()}"
            )
    actual = {(change.status, change.path, change.source_path) for change in manifest}
    wanted = {(status, path, None) for path, status in expected.items()}
    if actual != wanted:
        raise InterfaceInjectionMigrationError(
            "cutover manifest differs from exact map-authorized operations: "
            f"missing={sorted((s, p.as_posix()) for s, p, _ in wanted - actual)}, "
            f"unexpected={sorted((s, p.as_posix()) for s, p, _ in actual - wanted)}"
        )


def _materialize_blueprint_v4_candidate(
    repo_root: Path,
    migration_map: Mapping[str, Any],
    *,
    output_dir: Path | None = None,
    allow_non_atomic: bool = False,
) -> BlueprintV4Candidate:
    """Build an isolated mechanical candidate; semantic certification is separate."""

    raw_root = Path(repo_root)
    if raw_root.is_symlink():
        raise InterfaceInjectionMigrationError("repository root must not be a symlink")
    root = raw_root.resolve()
    if output_dir is not None and Path(output_dir).exists():
        raise InterfaceInjectionMigrationError(
            f"candidate output already exists: {output_dir}"
        )
    if not root.is_dir():
        raise InterfaceInjectionMigrationError(
            f"repository root is not a regular directory: {root}"
        )
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if output_dir is None:
        candidate_root = Path(
            tempfile.mkdtemp(prefix="blueprint-v4-candidate-")
        ).resolve()
    else:
        candidate_root = Path(output_dir).resolve()
        if not candidate_root.is_relative_to(temporary_root):
            raise InterfaceInjectionMigrationError(
                "candidate output must be inside the system temporary directory"
            )
        candidate_root.mkdir(parents=True)

    migration_plan = compile_migration_plan(migration_map)
    source_snapshot = _copy_candidate_tree(
        root,
        candidate_root,
        migration_plan,
        allow_non_atomic=allow_non_atomic,
    )
    try:
        run_git(candidate_root, "init", "-q")
        run_git(candidate_root, "config", "user.name", "Blueprint Migration")
        run_git(
            candidate_root,
            "config",
            "user.email",
            "blueprint-migration@example.invalid",
        )
        run_git(
            candidate_root,
            "config",
            "famulus.candidateAtomicGuarantee",
            "false" if allow_non_atomic else "true",
        )
        run_git(
            candidate_root,
            "fetch",
            "--no-tags",
            root.as_posix(),
            source_snapshot.git.commit,
        )
        run_git(candidate_root, "update-ref", "HEAD", source_snapshot.git.commit)
        run_git(candidate_root, "reset", "--mixed", source_snapshot.git.commit)
        legacy_commit = source_snapshot.git.commit
    except Exception as exc:
        raise InterfaceInjectionMigrationError(
            f"initialize candidate legacy evidence: {exc}"
        ) from exc
    overlay_changes = _apply_source_overlay(candidate_root, source_snapshot)
    source_overlay_commit = legacy_commit
    if overlay_changes:
        source_overlay_commit = _candidate_commit(
            candidate_root,
            "apply authorized source overlay\n\n"
            f"Source-HEAD: {source_snapshot.git.commit}",
            overlay_changes,
        )
    pin_blueprint_v4_source_overlay_commit(candidate_root, source_overlay_commit)
    _verify_source_materialization_snapshot(root, source_snapshot)

    candidate_map = load_blueprint_migration_map(
        candidate_root / "docs/plans/unified-architecture-migration-map.yaml"
    )
    if candidate_map != migration_map:
        raise InterfaceInjectionMigrationError(
            "caller migration map differs from captured candidate map"
        )
    validation = validate_blueprint_migration_map(candidate_root, candidate_map)
    migration_plan = compile_migration_plan(candidate_map)
    _assert_repository_dispatch_call_counter(
        candidate_root, candidate_map, migration_plan
    )
    conversion = convert_blueprint_declarations(
        candidate_root,
        validation.mapped_declaration_paths,
        migration_map=candidate_map,
    )
    _validate_created_path_predecessors(candidate_map, conversion)

    for relative in conversion.documents:
        existing = candidate_root / relative
        if (
            existing.exists()
            and relative not in conversion.removed_paths
            and not any(
                relative.is_relative_to(Path("skills") / target)
                for target in migration_plan.module_renames.values()
            )
        ):
            raise InterfaceInjectionMigrationError(
                f"converted target path collision: {relative.as_posix()}"
            )

    module_renames = _apply_candidate_module_renames(candidate_root, migration_plan)
    literal_changes = _apply_candidate_module_literal_renames(
        candidate_root, migration_plan
    )
    public_id_literal_changes = _apply_candidate_public_id_literal_renames(
        candidate_root, candidate_map, migration_plan
    )

    caller_changes = _apply_candidate_caller_renames(
        candidate_root, candidate_map, migration_plan
    )
    first_changes = _apply_candidate_conversion(
        candidate_root, conversion, migration_plan
    )
    if not first_changes and not caller_changes:
        raise InterfaceInjectionMigrationError("first conversion unexpectedly changed no paths")
    second_conversion = convert_blueprint_declarations(
        candidate_root,
        tuple(conversion.documents),
        migration_map=candidate_map,
    )
    if second_conversion.documents != conversion.documents or second_conversion.removed_paths:
        raise InterfaceInjectionMigrationError("materialized v4 conversion is not idempotent")
    second_caller_changes = _apply_candidate_caller_renames(
        candidate_root, candidate_map, migration_plan
    )
    if second_caller_changes:
        raise InterfaceInjectionMigrationError(
            f"candidate caller migration is not idempotent: {second_caller_changes}"
        )
    if _apply_candidate_public_id_literal_renames(
        candidate_root, candidate_map, migration_plan
    ):
        raise InterfaceInjectionMigrationError(
            "candidate public ID literal migration is not idempotent"
        )

    try:
        final_commit = _candidate_commit(
            candidate_root,
            "materialize mechanical v4 blueprint candidate",
            (
                *first_changes,
                *caller_changes,
                *literal_changes,
                *public_id_literal_changes,
                *(path for pair in module_renames for path in pair),
            ),
        )
        pin_blueprint_v4_mechanical_commit(candidate_root, final_commit)
    except Exception as exc:
        raise InterfaceInjectionMigrationError(
            f"commit mechanical v4 candidate: {exc}"
        ) from exc
    cutover_manifest = _candidate_cutover_manifest(
        candidate_root, source_overlay_commit, final_commit
    )
    _assert_cutover_manifest_authorized(
        cutover_manifest,
        candidate_root=candidate_root,
        source_overlay_commit=source_overlay_commit,
        final_commit=final_commit,
        exact_paths=(
            *caller_changes,
            *first_changes,
            *literal_changes,
            *public_id_literal_changes,
        ),
        migration_plan=migration_plan,
    )
    cutover_paths = tuple(
        sorted(
            {change.path for change in cutover_manifest}
            | {
                change.source_path
                for change in cutover_manifest
                if change.source_path is not None
            }
        )
    )
    for relative in conversion.documents:
        tracked = run_git(
            candidate_root,
            "ls-files",
            "--error-unmatch",
            "--",
            relative.as_posix(),
            check=False,
        )
        if tracked.returncode != 0:
            raise InterfaceInjectionMigrationError(
                f"converted output is not tracked: {relative.as_posix()}"
            )
    try:
        graph = load_repository_blueprint_graph(
            candidate_root,
            schema_root=candidate_root / "references" / "blueprint",
        )
    except Exception as exc:
        raise InterfaceInjectionMigrationError(
            f"candidate v4 graph is invalid: {exc}"
        ) from exc
    _assert_candidate_projections(graph, conversion)
    _verify_source_materialization_snapshot(root, source_snapshot)
    inspection = (
        {
            "source_commit": final_commit,
            "findings": [],
            "review_context": [],
            "noncertifiable_reason": "atomic_guarantee=false",
        }
        if allow_non_atomic
        else inspect_candidate_v4(candidate_root)
    )
    if inspection.get("source_commit") != final_commit:
        raise InterfaceInjectionMigrationError(
            "candidate inspection did not bind the materialized commit"
        )
    certifier_findings = inspection.get("findings")
    if not isinstance(certifier_findings, list):
        raise InterfaceInjectionMigrationError(
            "candidate inspection returned invalid findings"
        )
    review_context = inspection.get("review_context", [])
    if not isinstance(review_context, list):
        raise InterfaceInjectionMigrationError(
            "candidate inspection returned invalid review context"
        )
    inspection = {
        **inspection,
        "findings": certifier_findings,
        "review_context": review_context,
    }
    return BlueprintV4Candidate(
        root=candidate_root,
        conversion=conversion,
        graph=graph,
        source_commit=source_snapshot.git.commit,
        legacy_commit=legacy_commit,
        source_overlay_commit=source_overlay_commit,
        commit=final_commit,
        inspection=inspection,
        cutover_manifest=cutover_manifest,
        cutover_paths=cutover_paths,
        atomic_guarantee=not allow_non_atomic,
    )


def materialize_blueprint_v4_candidate(
    repo_root: Path,
    migration_map: Mapping[str, Any],
    *,
    output_dir: Path | None = None,
    diagnostic_allow_non_atomic: bool = False,
) -> BlueprintV4Candidate:
    """Build a retained successful candidate and remove failed temporary trees."""

    cleanup_root: Path
    if output_dir is None:
        cleanup_root = Path(
            tempfile.mkdtemp(prefix="blueprint-v4-candidate-")
        ).resolve()
        selected_output = cleanup_root / "candidate"
        cleanup_on_failure = True
    else:
        selected_output = Path(output_dir)
        cleanup_root = selected_output
        cleanup_on_failure = not (
            cleanup_root.exists() or cleanup_root.is_symlink()
        )
    try:
        return _materialize_blueprint_v4_candidate(
            repo_root,
            migration_map,
            output_dir=selected_output,
            allow_non_atomic=diagnostic_allow_non_atomic,
        )
    except Exception:
        if cleanup_on_failure and cleanup_root.exists() and cleanup_root.is_relative_to(
            Path(tempfile.gettempdir()).resolve()
        ):
            shutil.rmtree(cleanup_root)
        raise
