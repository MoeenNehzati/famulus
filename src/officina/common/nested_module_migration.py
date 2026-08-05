"""Deterministic frozen-v4 to nested-module-v5 migration planning.

Planning is pure. The only writer materializes a reviewed plan into a new
isolated candidate through the shared confined migration-candidate helpers.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import jsonschema
import yaml

from .atomic_files import AtomicWriteError, read_regular_file_bytes
from .blueprint_graph import (
    BlueprintNode,
    CertificationEdge,
    RepositoryBlueprintGraph,
    load_repository_blueprint_graph,
)
from .blueprint_inventory import collect_blueprints
from .certification_hashing import (
    CertificationHashError,
    NodeHashState,
    _recursive_contract_references,
    _recursive_contract_references_from_roots,
    _reference_candidates,
    certification_target_postorder,
)
from .certificate_records import (
    CertificateLogError,
    certificate_public_key_root,
    parse_certificate_log,
)
from .configured_schema import ConfiguredSchemaError, configured_validator
from .git_provenance import capture_git_snapshot, run_git
from .migration_candidate import (
    CutoverChange,
    MigrationCandidateError,
    atomic_candidate_write,
    candidate_commit,
    candidate_cutover_manifest,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FROZEN_V4_ROOT = _PROJECT_ROOT / "references" / "blueprint" / "migrations" / "v4"
_FROZEN_V5_ROOT = _PROJECT_ROOT / "references" / "blueprint" / "migrations" / "v5"
_FROZEN_V5_NODE_ROOT = _FROZEN_V5_ROOT / "legacy-reference-nodes"
_SKILL_VALIDATOR_ROOT = Path("skills/skill-maker/validators")
_NEW_VALIDATOR_ROOT = Path("validators/skill")
_SUPERSEDED_V5_PATHS = frozenset(
    {
        Path(
            "skills/skill-drift/references/"
            "certification-basis-roots.json"
        ),
    }
)
_DETERMINISTIC_COMMIT_DATE = "946684800 +0000"
_VALIDATOR_REFERENCE_RENAMES = (
    ("skills/skill-maker/validators/", "validators/skill/"),
    (
        '/ "skills" / "skill-maker" / "validators"',
        '/ "validators" / "skill"',
    ),
    ("dispatch_caller_skill.py", "dispatch_caller_module.py"),
    ("dispatch_caller_skill", "dispatch_caller_module"),
    ("dispatch-caller-skill", "dispatch-caller-module"),
    ("validate_dispatch_caller_skill", "validate_dispatch_caller_module"),
    ("validate-dispatch-caller-skill", "validate-dispatch-caller-module"),
)


def _v5_schema_root(repo_root: Path) -> Path:
    root = Path(repo_root).resolve()
    canonical = root / "references" / "blueprint"
    packaged = Path(__file__).resolve().parents[3] / "references" / "blueprint"
    for candidate in (canonical, packaged):
        module_schema = candidate / "module.schema.json"
        try:
            document = json.loads(module_schema.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            continue
        version = (
            document.get("properties", {})
            .get("schema_version", {})
            .get("const")
            if isinstance(document, dict)
            else None
        )
        if version == 5:
            return candidate
    raise NestedModuleMigrationError(
        f"canonical v5 schema bundle is unavailable under {root}"
    )


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that makes duplicate mapping keys a hard failure."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class _Source:
    module_id: str
    module_root: Path
    blueprint_path: Path
    declaration: Mapping[str, Any]
    matched_files: tuple[Path, ...]
    moved: bool


@dataclass(frozen=True)
class _SourceTarget:
    source_id: str
    module_root: Path
    blueprint_path: Path
    version: int


@dataclass(frozen=True)
class _Module:
    module_root: Path
    blueprint_path: Path
    declaration: Mapping[str, Any]
    sources: tuple[_Source, ...]
    is_skill: bool


@dataclass(frozen=True)
class _Operation:
    action: str
    path: Path
    source_path: Path | None = None
    mode: int | None = None

    def document(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "path": self.path.as_posix(),
        }
        if self.source_path is not None:
            result["source_path"] = self.source_path.as_posix()
        if self.mode is not None:
            result["mode"] = self.mode
        return result


class NestedModuleMigrationError(RuntimeError):
    """Raised when a repository cannot be migrated without guessing."""


@dataclass(frozen=True)
class NestedModuleCandidate:
    """One committed isolated candidate produced from a migration plan."""

    root: Path
    commit: str
    manifest_bytes: bytes
    cutover_manifest: tuple[CutoverChange, ...]
    cutover_paths: tuple[Path, ...]


@dataclass(frozen=True)
class NestedModuleMigration:
    """Immutable deterministic migration plan and its review surfaces."""

    repo_root: Path
    source_commit: str
    node_version_map: Mapping[str, int]
    interface_version_map: Mapping[str, int]
    access_map: Mapping[str, Mapping[str, Any]]
    identity_map: Mapping[str, str]
    path_map: Mapping[str, str]
    import_map: Mapping[str, Mapping[str, Any]]
    authority_map: Mapping[str, Mapping[str, Any]]
    history_map: Mapping[str, Mapping[str, Any]]
    certificate_input_hashes: Mapping[str, str]
    file_disposition_map: Mapping[str, Mapping[str, Any]]
    unclassified_files: tuple[str, ...]
    file_hash_map: Mapping[str, str]
    file_mode_map: Mapping[str, int]
    planned_files: Mapping[str, bytes]
    operations: tuple[_Operation, ...]
    state_operations: tuple[Mapping[str, Any], ...]

    @property
    def is_noop(self) -> bool:
        return not self.operations

    def to_manifest(self) -> dict[str, Any]:
        return {
            "source_commit": self.source_commit,
            "node_versions": _plain_mapping(self.node_version_map),
            "interface_versions": _plain_mapping(self.interface_version_map),
            "access": _plain_mapping(self.access_map),
            "identities": _plain_mapping(self.identity_map),
            "paths": _plain_mapping(self.path_map),
            "imports": _plain_mapping(self.import_map),
            "authority": _plain_mapping(self.authority_map),
            "histories": _plain_mapping(self.history_map),
            "certificate_inputs": _plain_mapping(
                self.certificate_input_hashes
            ),
            "file_dispositions": _plain_mapping(
                self.file_disposition_map
            ),
            "unclassified_files": list(self.unclassified_files),
            "file_hashes": _plain_mapping(self.file_hash_map),
            "file_modes": _plain_mapping(self.file_mode_map),
            "operations": [
                operation.document() for operation in self.operations
            ],
            "state_operations": [
                _plain_value(operation)
                for operation in self.state_operations
            ],
        }

    def render_manifest(self) -> bytes:
        return (
            json.dumps(
                self.to_manifest(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )

    def materialize(self, output_dir: Path) -> NestedModuleCandidate:
        return _materialize(self, output_dir)


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _plain_value(value)
    assert isinstance(result, dict)
    return result


def _read_yaml(path: Path, *, root: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise NestedModuleMigrationError(
                f"blueprint is not a regular file: {path.relative_to(root)}"
            )
        loaded = yaml.load(path.read_bytes(), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise NestedModuleMigrationError(
            f"cannot parse blueprint {path.relative_to(root)}: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise NestedModuleMigrationError(
            f"blueprint root must be a mapping: {path.relative_to(root)}"
        )
    return loaded


def _schema_validator(schema_root: Path) -> jsonschema.protocols.Validator:
    try:
        schema_path = (schema_root / "schema.json").resolve()
        config_path = schema_root / "config.yaml"
        if config_path.is_file():
            return configured_validator(
                schema_path,
                config_path=config_path,
                allowed_schema_root=schema_root,
            )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
        resolver = jsonschema.RefResolver(
            base_uri=schema_path.as_uri(),
            referrer=schema,
        )
        return validator_class(schema, resolver=resolver)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jsonschema.SchemaError,
        ConfiguredSchemaError,
    ) as exc:
        raise NestedModuleMigrationError(
            f"schema bundle is unavailable: {schema_root}"
        ) from exc


def _validate_document(
    declaration: Mapping[str, Any],
    path: Path,
    *,
    root: Path,
    validator: jsonschema.protocols.Validator,
    version: int,
) -> None:
    if declaration.get("schema_version") != version:
        raise NestedModuleMigrationError(
            f"{path.relative_to(root)}: expected frozen schema version {version}"
        )
    errors = sorted(
        validator.iter_errors(declaration),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        detail = errors[0]
        location = ".".join(str(part) for part in detail.absolute_path) or "<root>"
        raise NestedModuleMigrationError(
            f"{path.relative_to(root)}: schema validation failed at "
            f"{location}: {detail.message}"
        )


def _tracked_paths(root: Path) -> tuple[Path, ...]:
    result = run_git(root, "ls-files", "-z")
    paths = tuple(
        sorted(
            Path(os.fsdecode(raw))
            for raw in result.stdout.rstrip(b"\0").split(b"\0")
            if raw
        )
    )
    return tuple(
        path
        for path in paths
        if not (
            len(path.parts) >= 2
            and path.parts[0] == "skills"
            and path.parts[1] == ".system"
        )
    )


def _verify_tracked_inputs_match_head(
    root: Path,
    commit: str,
    tracked: Sequence[Path],
) -> None:
    tree = run_git(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
    )
    entries: dict[Path, tuple[str, str]] = {}
    for record in tree.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise NestedModuleMigrationError("source commit tree is malformed")
        mode, object_type, object_id = (
            field.decode("ascii") for field in fields
        )
        if object_type != "blob":
            continue
        entries[Path(os.fsdecode(raw_path))] = (mode, object_id)

    index = run_git(root, "ls-files", "--stage", "-z")
    index_entries: dict[Path, tuple[str, str]] = {}
    for record in index.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise NestedModuleMigrationError(
                "source index contains a malformed or unmerged entry"
            )
        mode, object_id = (
            field.decode("ascii") for field in fields[:2]
        )
        index_entries[Path(os.fsdecode(raw_path))] = (mode, object_id)

    object_format = (
        run_git(root, "rev-parse", "--show-object-format")
        .stdout.decode("ascii")
        .strip()
    )
    if object_format not in {"sha1", "sha256"}:
        raise NestedModuleMigrationError(
            f"unsupported Git object format: {object_format!r}"
        )

    for relative in tracked:
        expected = entries.get(relative)
        if expected is None:
            raise NestedModuleMigrationError(
                f"tracked input is absent from source HEAD: {relative.as_posix()}"
            )
        mode, object_id = expected
        if index_entries.get(relative) != expected:
            raise NestedModuleMigrationError(
                f"tracked index entry differs from source HEAD: "
                f"{relative.as_posix()}"
            )
        path = root / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise NestedModuleMigrationError(
                f"tracked input differs from source HEAD: {relative.as_posix()}"
            ) from exc
        if mode == "120000":
            if stat.S_ISLNK(metadata.st_mode):
                data = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(metadata.st_mode):
                data = path.read_bytes()
            else:
                raise NestedModuleMigrationError(
                    f"tracked input mode differs from source HEAD: "
                    f"{relative.as_posix()}"
                )
        elif mode in {"100644", "100755"}:
            if not stat.S_ISREG(metadata.st_mode):
                raise NestedModuleMigrationError(
                    f"tracked input mode differs from source HEAD: "
                    f"{relative.as_posix()}"
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise NestedModuleMigrationError(
                    f"tracked input differs from source HEAD: "
                    f"{relative.as_posix()}"
                ) from exc
        else:
            raise NestedModuleMigrationError(
                f"tracked input has unsupported Git mode {mode}: "
                f"{relative.as_posix()}"
            )
        digest = hashlib.new(object_format)
        digest.update(f"blob {len(data)}\0".encode("ascii"))
        digest.update(data)
        if digest.hexdigest() != object_id:
            raise NestedModuleMigrationError(
                f"tracked input bytes differ from source HEAD: "
                f"{relative.as_posix()}"
            )


def _materialize_committed_snapshot(
    source_root: Path,
    commit: str,
    snapshot_root: Path,
) -> tuple[tuple[Path, ...], dict[Path, int]]:
    """Materialize exact commit blobs without consulting worktree bytes."""

    tree = run_git(
        source_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
    )
    tracked: list[Path] = []
    modes: dict[Path, int] = {}
    for record in tree.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[1] != b"blob":
            raise NestedModuleMigrationError(
                "source commit tree contains an unsupported entry"
            )
        mode = fields[0].decode("ascii")
        object_id = fields[2].decode("ascii")
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise NestedModuleMigrationError(
                f"unsafe committed path: {relative.as_posix()}"
            )
        data = run_git(
            source_root,
            "cat-file",
            "blob",
            object_id,
        ).stdout
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            link_target = os.fsdecode(data)
            if Path(link_target).is_absolute():
                raise NestedModuleMigrationError(
                    f"unsafe committed symlink: {relative.as_posix()}"
                )
            normalized = Path(
                os.path.normpath(
                    (relative.parent / link_target).as_posix()
                )
            )
            if normalized.is_absolute() or ".." in normalized.parts:
                raise NestedModuleMigrationError(
                    f"unsafe committed symlink: {relative.as_posix()}"
                )
            target.symlink_to(link_target)
        elif mode in {"100644", "100755"}:
            target.write_bytes(data)
            file_mode = 0o755 if mode == "100755" else 0o644
            target.chmod(file_mode)
            modes[relative] = file_mode
        else:
            raise NestedModuleMigrationError(
                f"unsupported committed mode {mode}: {relative.as_posix()}"
            )
        if not (
            len(relative.parts) >= 2
            and relative.parts[0] == "skills"
            and relative.parts[1] == ".system"
        ):
            tracked.append(relative)
    return tuple(sorted(tracked)), modes


def _live_certificate_inputs(root: Path) -> dict[Path, bytes]:
    """Capture ignored signed histories and retained public verification keys."""

    result: dict[Path, bytes] = {}
    for certificate_dir in sorted(root.rglob(".certificates")):
        relative_dir = certificate_dir.relative_to(root)
        if ".git" in relative_dir.parts:
            continue
        if relative_dir.is_relative_to(
            Path("references/certification-history/v4-cutover")
        ):
            continue
        if certificate_dir.is_symlink() or not certificate_dir.is_dir():
            raise NestedModuleMigrationError(
                f"certificate state root is unsafe: {relative_dir.as_posix()}"
            )
        for entry in sorted(certificate_dir.iterdir()):
            if entry.name == "public-keys":
                if entry.is_symlink() or not entry.is_dir():
                    raise NestedModuleMigrationError(
                        "certificate public-key root is unsafe"
                    )
                for key_file in sorted(entry.iterdir()):
                    if key_file.name != "active-key-id" and (
                        key_file.suffix != ".pub"
                    ):
                        continue
                    relative = key_file.relative_to(root)
                    result[relative] = _read_confined_regular(
                        root,
                        relative,
                        context="certificate verification key",
                    )
                continue
            if entry.suffix != ".jsonl":
                continue
            relative = entry.relative_to(root)
            result[relative] = _read_confined_regular(
                root,
                relative,
                context="certificate history",
            )
    return result


def _certificate_input_hashes(
    inputs: Mapping[Path, bytes],
) -> dict[str, str]:
    return {
        path.as_posix(): "sha256:" + hashlib.sha256(data).hexdigest()
        for path, data in sorted(inputs.items())
    }


def _install_certificate_snapshot(
    snapshot_root: Path,
    inputs: Mapping[Path, bytes],
) -> None:
    for relative, data in sorted(inputs.items()):
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)


def _assert_source_matches_plan(plan: NestedModuleMigration) -> None:
    snapshot = capture_git_snapshot(plan.repo_root)
    if (
        snapshot is None
        or snapshot.repo_root != plan.repo_root
        or snapshot.commit != plan.source_commit
    ):
        raise NestedModuleMigrationError(
            "source HEAD changed after migration planning"
        )
    if run_git(
        plan.repo_root,
        "status",
        "--porcelain=v1",
        "-z",
    ).stdout:
        raise NestedModuleMigrationError(
            "source Git/index state changed after migration planning"
        )
    tracked = _tracked_paths(plan.repo_root)
    _verify_tracked_inputs_match_head(
        plan.repo_root,
        plan.source_commit,
        tracked,
    )
    current_certificate_hashes = _certificate_input_hashes(
        _live_certificate_inputs(plan.repo_root)
    )
    if current_certificate_hashes != dict(plan.certificate_input_hashes):
        raise NestedModuleMigrationError(
            "source certificate state changed after migration planning"
        )


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _read_confined_regular(
    root: Path,
    relative: Path,
    *,
    context: str,
) -> bytes:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise NestedModuleMigrationError(
            f"{context} must be a regular confined file: {relative.as_posix()}"
        )
    try:
        return read_regular_file_bytes(path, allowed_root=root)
    except (AtomicWriteError, OSError) as exc:
        raise NestedModuleMigrationError(
            f"{context} must be a regular confined file: {relative.as_posix()}"
        ) from exc


def _module_markers(root: Path, tracked: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(
        path
        for path in tracked
        if path.name == "blueprint.yaml"
        and _is_regular(root / path)
        and "tests/fixtures" not in path.as_posix()
        and "references/blueprint/migrations" not in path.as_posix()
    )


def _skill_signals(root: Path, tracked: Sequence[Path]) -> set[Path]:
    signals = {
        path.parent
        for path in tracked
        if path.name == "SKILL.md"
        and len(path.parts) >= 2
        and path.parts[0] == "skills"
    }
    for marker in _module_markers(root, tracked):
        if marker.parent.parent == Path("skills"):
            signals.add(marker.parent)
    return signals


def _exact_repository_skill(
    root: Path,
    module_root: Path,
    declaration: Mapping[str, Any],
) -> bool:
    return (
        module_root.parent == Path("skills")
        and _is_regular(root / module_root / "SKILL.md")
        and declaration.get("id") == module_root.name
        and isinstance(declaration.get("discovery"), Mapping)
        and declaration["discovery"].get("mechanism") == "skill"
    )


_LEGACY_CATEGORY_DOMAINS = {
    "research-assistant": "research",
    "general-assistant": "personal-assistance",
    "productivity-general-assistant": "personal-assistance",
    "workflow-general-assistant": "assistant-interaction",
    "development-assistant": "software-development",
    "coding-development-assistant": "software-development",
    "skill-making-development-assistant": "assistant-development",
    "system-assistant": "assistant-operations",
}

_LEGACY_ROLE_TOPICS = {
    "productivity": "personal-organization",
    "research-writing": "research-writing",
    "math-reasoning": "mathematical-reasoning",
    "document-processing": "scholarly-documents",
    "development-assistant": "repository-workflow",
    "system-operations": "system-maintenance",
    "integration": "external-integrations",
    "meta-skill": "assistant-authoring",
    "automation": "task-automation",
    "mode": "reasoning-control",
}


def _migrated_skill_discovery(
    declaration: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate the retired v4 taxonomy into configured v5 discovery data."""

    category = declaration.get("category")
    role = declaration.get("role")
    domain = _LEGACY_CATEGORY_DOMAINS.get(category)
    topic = _LEGACY_ROLE_TOPICS.get(role)
    if domain is None or topic is None:
        raise NestedModuleMigrationError(
            f"{declaration.get('id', '<unknown>')}: repository-managed v4 skill "
            "requires recognized category and role values for catalog migration"
        )
    return {
        "mechanism": "skill",
        "catalog": {
            "domain": domain,
            "topics": [topic],
            "visibility": "listed",
        },
        "activated_by": ["user-request", "skill-workflow"],
        "persistent_modifier": role == "mode",
    }


def _validate_repository_skill_predicate(
    root: Path,
    tracked: Sequence[Path],
    declarations: Mapping[Path, Mapping[str, Any]],
) -> set[Path]:
    managed: set[Path] = set()
    signals = _skill_signals(root, tracked)
    for module_root in sorted(signals):
        declaration = declarations.get(module_root)
        accepted = (
            declaration is not None
            and _exact_repository_skill(root, module_root, declaration)
        )
        if accepted:
            managed.add(module_root)
            continue
        if (
            len(module_root.parts) == 3
            and module_root.parts[0] == "skills"
            and module_root.name == "_rtx"
        ):
            raise NestedModuleMigrationError(
                f"generated target collision: {module_root / 'blueprint.yaml'} "
                "already exists"
            )
        raise NestedModuleMigrationError(
            f"{module_root.as_posix()}: partial repository-managed skill; "
            "require a top-level skills child with regular SKILL.md, matching "
            "module id, and discovery.mechanism skill"
        )
    return managed


def _source_blueprint_path(
    module_root: Path,
    source_id: str,
    source_entry: Any,
) -> Path:
    if not isinstance(source_entry, dict):
        raise NestedModuleMigrationError(
            f"{source_id}: source registration must be a mapping"
        )
    locator = source_entry.get("blueprint")
    if (
        not isinstance(locator, dict)
        or locator.get("base") != "module-root"
        or not isinstance(locator.get("path"), str)
    ):
        raise NestedModuleMigrationError(
            f"{source_id}: frozen-v4 source locator must be module-root relative"
        )
    relative = Path(locator["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise NestedModuleMigrationError(
            f"{source_id}: unsafe source blueprint path"
        )
    return module_root / relative


def _compile_patterns(
    declaration: Mapping[str, Any],
    *,
    context: str,
) -> tuple[re.Pattern[str], ...]:
    raw_patterns = declaration.get("content")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise NestedModuleMigrationError(f"{context}: content must be nonempty")
    compiled: list[re.Pattern[str]] = []
    for raw in raw_patterns:
        if not isinstance(raw, str) or not raw:
            raise NestedModuleMigrationError(
                f"{context}: content patterns must be nonempty strings"
            )
        try:
            compiled.append(re.compile(raw))
        except re.error as exc:
            raise NestedModuleMigrationError(
                f"{context}: invalid content pattern {raw!r}: {exc}"
            ) from exc
    return tuple(compiled)


def _source_content(
    root: Path,
    module_root: Path,
    declaration: Mapping[str, Any],
    tracked: Sequence[Path],
    *,
    context: str,
) -> tuple[Path, ...]:
    patterns = _compile_patterns(declaration, context=context)
    candidates = tuple(
        path
        for path in tracked
        if path.is_relative_to(module_root)
        and path.name != "blueprint.yaml"
        and not (
            path.parent.name == "blueprints"
            and path.suffix in {".yaml", ".yml"}
        )
        and ".certificates" not in path.parts
        and _is_regular(root / path)
    )
    matched: set[Path] = set()
    for pattern in patterns:
        current = {
            path
            for path in candidates
            if pattern.fullmatch(path.relative_to(module_root).as_posix())
        }
        if not current:
            raise NestedModuleMigrationError(
                f"{context}: content pattern {pattern.pattern!r} matched no files"
            )
        matched.update(current)
    gateway = declaration.get("gateway")
    gateway_path = gateway.get("path") if isinstance(gateway, dict) else None
    if isinstance(gateway_path, str):
        expected = module_root / gateway_path
        if expected not in matched:
            raise NestedModuleMigrationError(
                f"{context}: gateway is outside source content"
            )
    return tuple(sorted(matched))


def _load_v4_modules(
    root: Path,
    tracked: Sequence[Path],
) -> tuple[_Module, ...]:
    if not _FROZEN_V4_ROOT.is_dir():
        raise NestedModuleMigrationError(
            f"frozen v4 schema bundle is unavailable: {_FROZEN_V4_ROOT}"
        )
    validator = _schema_validator(_FROZEN_V4_ROOT)
    declarations: dict[Path, Mapping[str, Any]] = {}
    marker_paths: dict[Path, Path] = {}
    for marker in _module_markers(root, tracked):
        declaration = _read_yaml(root / marker, root=root)
        declarations[marker.parent] = declaration
        marker_paths[marker.parent] = marker

    for module_root, declaration in sorted(declarations.items()):
        if (
            declaration.get("schema_version") == 4
            and _exact_repository_skill(root, module_root, declaration)
            and module_root / "_rtx" in declarations
        ):
            child_marker = marker_paths[module_root / "_rtx"]
            raise NestedModuleMigrationError(
                "generated target collision: "
                f"{child_marker.as_posix()} already exists as an "
                "unregistered nested marker"
            )

    versions = {
        declaration.get("schema_version") for declaration in declarations.values()
    }
    if versions and versions <= {5}:
        return ()
    if 5 in versions:
        raise NestedModuleMigrationError(
            "repository mixes live version 4 and version 5 module markers"
        )

    managed = _validate_repository_skill_predicate(root, tracked, declarations)
    modules: list[_Module] = []
    for module_root, declaration in sorted(declarations.items()):
        marker = marker_paths[module_root]
        _validate_document(
            declaration,
            root / marker,
            root=root,
            validator=validator,
            version=4,
        )
        if declaration.get("node_type") != "module":
            raise NestedModuleMigrationError(
                f"{marker}: canonical marker must be a module"
            )
        raw_sources = declaration.get("sources")
        if not isinstance(raw_sources, dict):
            raise NestedModuleMigrationError(f"{marker}: sources must be a mapping")
        sources: list[_Source] = []
        seen_content: dict[Path, str] = {}
        for source_id, entry in sorted(raw_sources.items()):
            if not isinstance(source_id, str):
                raise NestedModuleMigrationError(
                    f"{marker}: source id must be a string"
                )
            source_path = _source_blueprint_path(module_root, source_id, entry)
            source_declaration = _read_yaml(root / source_path, root=root)
            _validate_document(
                source_declaration,
                root / source_path,
                root=root,
                validator=validator,
                version=4,
            )
            if source_declaration.get("id") != source_id:
                raise NestedModuleMigrationError(
                    f"{source_path}: source registration id mismatch"
                )
            matched = _source_content(
                root,
                module_root,
                source_declaration,
                tracked,
                context=source_id,
            )
            for path in matched:
                previous = seen_content.get(path)
                if previous is not None:
                    raise NestedModuleMigrationError(
                        f"ambiguous ownership: overlapping content for "
                        f"{previous} and {source_id} at {path.as_posix()}"
                    )
                seen_content[path] = source_id
            gateway = source_declaration.get("gateway")
            gateway_path = (
                gateway.get("path") if isinstance(gateway, dict) else None
            )
            gateway_language = (
                gateway.get("language") if isinstance(gateway, dict) else None
            )
            moved = (
                module_root in managed
                and isinstance(gateway_language, str)
                and gateway_language.casefold() != "markdown"
            )
            if moved and module_root / "SKILL.md" in matched:
                raise NestedModuleMigrationError(
                    f"{source_id}: code source cannot own the skill gateway"
                )
            sources.append(
                _Source(
                    module_id=str(declaration["id"]),
                    module_root=module_root,
                    blueprint_path=source_path,
                    declaration=source_declaration,
                    matched_files=matched,
                    moved=moved,
                )
            )
        modules.append(
            _Module(
                module_root=module_root,
                blueprint_path=marker,
                declaration=declaration,
                sources=tuple(sources),
                is_skill=module_root in managed,
            )
        )
    return tuple(modules)


def _yaml_bytes(document: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(document),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    ).encode("utf-8")


def _exact_content_pattern(paths: Iterable[Path]) -> str:
    values = sorted(path.as_posix() for path in paths)
    if not values:
        raise NestedModuleMigrationError("cannot build empty module content")
    escaped = [re.escape(value).replace(r"\-", r"\-") for value in values]
    return escaped[0] if len(escaped) == 1 else f"(?:{'|'.join(escaped)})"


def _rebase_content_pattern(pattern: str) -> str:
    rebased = pattern.replace("_rtx/(?:", "(?:").replace("_rtx/", "")
    rebased = rebased.replace("bin/", "assets/bin/")
    if rebased.startswith("(?:(?:") and ")|" in rebased:
        rebased = "(?:" + rebased[len("(?:(?:") :].replace(")|", "|", 1)
    return rebased


def _child_content_path(relative: Path) -> Path:
    if relative.parts[:1] == ("_rtx",):
        return Path(*relative.parts[1:])
    if relative.parts[:1] == ("bin",):
        return Path("assets", *relative.parts)
    return relative


def _child_resource_root(root: str) -> str:
    return "assets/bin" if root == "bin" else root


def _rebase_module_root_path(value: str) -> str:
    if value.startswith("$module/_rtx/"):
        return "$module/" + value[len("$module/_rtx/") :]
    if value.startswith("_rtx/"):
        return value[len("_rtx/") :]
    return value


def _rebase_module_root_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {
            key: _rebase_module_root_paths(item)
            for key, item in value.items()
        }
        path = result.get("path")
        if isinstance(path, str):
            result["path"] = _rebase_module_root_path(path)
        return result
    if isinstance(value, list):
        return [_rebase_module_root_paths(item) for item in value]
    return copy.deepcopy(value)


def _renamed_source_id(source_id: str, module_id: str, child_id: str) -> str:
    prefix = f"{module_id}.source."
    if not source_id.startswith(prefix):
        raise NestedModuleMigrationError(
            f"{source_id}: source id is outside module {module_id}"
        )
    return f"{child_id}.source.{source_id[len(prefix):]}"


def _renamed_source_interface(
    interface_id: str,
    module_id: str,
    child_id: str,
) -> str:
    prefix = f"{module_id}.source."
    if not interface_id.startswith(prefix):
        raise NestedModuleMigrationError(
            f"{interface_id}: source interface is outside module {module_id}"
        )
    return child_id + interface_id[len(module_id):]


def _child_export_id(parent_interface: str, module_id: str, child_id: str) -> str:
    prefix = f"{module_id}.interface."
    if not parent_interface.startswith(prefix):
        raise NestedModuleMigrationError(
            f"{parent_interface}: export id is outside module {module_id}"
        )
    return f"{child_id}.interface.{parent_interface[len(prefix):]}"


def _access_document(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NestedModuleMigrationError(f"{context}: access must be a mapping")
    allow_all = value.get("allow_all_modules")
    callers = value.get("allowed_callers")
    if not isinstance(allow_all, bool) or not (
        isinstance(callers, list)
        and all(isinstance(caller, str) for caller in callers)
    ):
        raise NestedModuleMigrationError(f"{context}: malformed access policy")
    return {
        "allow_all_modules": allow_all,
        "allowed_callers": sorted(set(callers)),
    }


def _authority_claim_key(claim: Mapping[str, Any]) -> tuple[str, str]:
    match = claim.get("match")
    path = claim.get("path")
    if match not in {"exact", "regex"} or not isinstance(path, str):
        raise NestedModuleMigrationError("malformed filesystem authority claim")
    return str(match), path


_PARENT_AUTHORITY_DISPOSITIONS = frozenset(
    {
        (
            "skill-certifier",
            "regex",
            (
                r"^\.certificates/public-keys/"
                r"(active-key-id|[0-9a-f]{64}\.pub)$"
            ),
        ),
        (
            "skill-certifier",
            "regex",
            r"^\.certificates/[^/]+\.jsonl$",
        ),
    }
)

_CHILD_AUTHORITY_DISPOSITIONS = frozenset(
    {
        (
            "regenerate-blueprints",
            "regex",
            r"^\$tmp/[A-Za-z0-9-]+_blueprint\.yaml$",
        ),
        (
            "skill-drift",
            "regex",
            r"^_build/certificate-drift-[0-9]{8}-[0-9]{6}\.md$",
        ),
    }
)


def _claims_overlap(
    left: tuple[str, str],
    right: tuple[str, str],
) -> bool:
    if left == right or left[1] == right[1]:
        return True
    if left[0] == "exact" and right[0] == "regex":
        try:
            return re.fullmatch(right[1], left[1]) is not None
        except re.error as exc:
            raise NestedModuleMigrationError(
                f"invalid authority regex {right[1]!r}"
            ) from exc
    if left[0] == "regex" and right[0] == "exact":
        return _claims_overlap(right, left)
    return False


def _validate_authority_overlap(modules: Sequence[_Module]) -> None:
    claims: list[tuple[str, tuple[str, str]]] = []
    for module in modules:
        authority = module.declaration.get("authority", {})
        raw = authority.get("owns_filesystem", []) if isinstance(authority, dict) else []
        if not isinstance(raw, list):
            raise NestedModuleMigrationError(
                f"{module.declaration.get('id')}: filesystem authority must be a list"
            )
        for claim in raw:
            if not isinstance(claim, dict):
                raise NestedModuleMigrationError("malformed filesystem authority")
            key = _authority_claim_key(claim)
            for previous_id, previous_key in claims:
                if _claims_overlap(previous_key, key):
                    raise NestedModuleMigrationError(
                        "overlapping authority: filesystem ownership collision "
                        f"between {previous_id} and {module.declaration.get('id')}"
                    )
            claims.append((str(module.declaration.get("id")), key))


def _deep_identity_rewrite(value: Any, identities: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            identities.get(key, key) if isinstance(key, str) else key:
            _deep_identity_rewrite(item, identities)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_deep_identity_rewrite(item, identities) for item in value]
    if isinstance(value, str):
        return identities.get(value, value)
    return copy.deepcopy(value)


def _rebase_authority_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _rebase_authority_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_authority_paths(item) for item in value]
    if isinstance(value, str):
        return _rebase_module_root_path(value)
    return value


def _rebase_child_suggested_permissions(
    authority: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(authority))
    suggested = result.get("suggested_permissions")
    if suggested is None:
        return result
    if not isinstance(suggested, Mapping):
        raise NestedModuleMigrationError(
            f"{context}: suggested_permissions must be a mapping"
        )
    bash = suggested.get("bash", [])
    if not isinstance(bash, list):
        raise NestedModuleMigrationError(
            f"{context}: suggested_permissions.bash must be a list"
        )
    for index, permission in enumerate(bash):
        if not isinstance(permission, dict):
            raise NestedModuleMigrationError(
                f"{context}: suggested_permissions.bash[{index}] "
                "must be a mapping"
            )
        for field in ("command", "args_prefix"):
            values = permission.get(field)
            if values is None:
                continue
            if not (
                isinstance(values, list)
                and all(isinstance(item, str) for item in values)
            ):
                raise NestedModuleMigrationError(
                    f"{context}: suggested_permissions.bash[{index}].{field} "
                    "must be a string list"
                )
            permission[field] = [
                item[len("_rtx/") :]
                if item.startswith("_rtx/")
                else item
                for item in values
            ]
    return result


def _declared_path_values(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "path" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_declared_path_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_declared_path_values(item))
    return tuple(found)


def _module_relative_runtime_evidence(
    sources: Iterable[_Source],
) -> frozenset[Path]:
    evidence: set[Path] = set()
    for source in sources:
        for raw in _declared_path_values(source.declaration):
            value = raw
            if value.startswith("$module/"):
                value = value[len("$module/") :]
            if value.startswith("./"):
                value = value[2:]
            relative = Path(value)
            if (
                not value
                or value.startswith(("$", "<"))
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                continue
            evidence.add(relative)
    return frozenset(evidence)


def _is_parent_instruction_asset(
    relative: Path,
    *,
    gateway_path: str | None,
) -> bool:
    if gateway_path is not None and relative == Path(gateway_path):
        return True
    if relative.name == ".gitignore":
        return True
    if relative.suffix.casefold() in {".md", ".markdown", ".mmd", ".rst"}:
        return True
    return bool(
        relative.parts
        and relative.parts[0] in {"agents", "instructions", "lessons"}
    )


def _authority_claim_evidence(
    module: _Module,
    claim: Mapping[str, Any],
) -> set[bool]:
    source_by_interface: dict[str, _Source] = {}
    for source in module.sources:
        interfaces = source.declaration.get("interfaces", {})
        if isinstance(interfaces, Mapping):
            for interface_id in interfaces:
                source_by_interface[str(interface_id)] = source
    exports = module.declaration.get("exports", {})
    if isinstance(exports, Mapping):
        for export_id, declaration in exports.items():
            if not isinstance(declaration, Mapping):
                continue
            source_interface = declaration.get("source_interface")
            if not isinstance(source_interface, str):
                continue
            owner = source_by_interface.get(source_interface)
            if owner is not None:
                source_by_interface[str(export_id)] = owner

    evidence: set[bool] = set()
    readers = claim.get("allowed_readers", [])
    if isinstance(readers, list):
        for reader in readers:
            owner = source_by_interface.get(str(reader))
            if owner is not None:
                evidence.add(owner.moved)

    path = claim.get("path")
    match = claim.get("match")
    if not isinstance(path, str) or match not in {"exact", "regex"}:
        raise NestedModuleMigrationError("malformed filesystem authority claim")
    pattern: re.Pattern[str] | None = None
    if match == "regex" and not path.startswith(("^\\$", "\\$", "$")):
        try:
            pattern = re.compile(path)
        except re.error as exc:
            raise NestedModuleMigrationError(
                f"invalid authority regex {path!r}"
            ) from exc
    for source in module.sources:
        if path in _declared_path_values(source.declaration):
            evidence.add(source.moved)
        for matched in source.matched_files:
            relative = matched.relative_to(module.module_root).as_posix()
            if (match == "exact" and relative == path) or (
                pattern is not None and pattern.fullmatch(relative) is not None
            ):
                evidence.add(source.moved)
    return evidence


def _split_skill_authority(
    module: _Module,
    identities: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = copy.deepcopy(
        module.declaration.get("authority", {"owns_filesystem": []})
    )
    if not isinstance(raw, dict):
        raise NestedModuleMigrationError(
            f"{module.declaration.get('id')}: authority must be a mapping"
        )
    claims = raw.pop("owns_filesystem", [])
    if not isinstance(claims, list):
        raise NestedModuleMigrationError(
            f"{module.declaration.get('id')}: filesystem authority must be a list"
        )
    parent_claims: list[dict[str, Any]] = []
    child_claims: list[dict[str, Any]] = []
    has_moved_source = any(source.moved for source in module.sources)
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise NestedModuleMigrationError("malformed filesystem authority")
        evidence = _authority_claim_evidence(module, claim)
        module_id = str(module.declaration.get("id"))
        claim_key = _authority_claim_key(claim)
        explicit_parent = (
            module_id,
            claim_key[0],
            claim_key[1],
        ) in _PARENT_AUTHORITY_DISPOSITIONS
        explicit_child = (
            module_id,
            claim_key[0],
            claim_key[1],
        ) in _CHILD_AUTHORITY_DISPOSITIONS
        if explicit_parent and explicit_child:
            raise NestedModuleMigrationError(
                f"{module_id}: conflicting explicit authority disposition"
            )
        if explicit_parent:
            evidence.add(False)
        if explicit_child:
            evidence.add(True)
        if evidence == {False, True}:
            raise NestedModuleMigrationError(
                f"{module_id}: ambiguous authority "
                "disposition mixes parent and child interfaces"
            )
        if not evidence:
            if has_moved_source:
                raise NestedModuleMigrationError(
                    f"{module_id}: cannot determine authority disposition "
                    f"for {claim_key[0]} claim {claim_key[1]!r}"
                )
            evidence.add(False)
        rewritten = _deep_identity_rewrite(dict(claim), identities)
        assert isinstance(rewritten, dict)
        if evidence == {False}:
            parent_claims.append(rewritten)
        else:
            rebased = _rebase_authority_paths(rewritten)
            assert isinstance(rebased, dict)
            child_claims.append(rebased)

    parent_authority: dict[str, Any] = {
        "owns_filesystem": parent_claims
    }
    child_authority: dict[str, Any] = {
        "owns_filesystem": child_claims
    }
    if has_moved_source:
        child_authority.update(_deep_identity_rewrite(raw, identities))
    else:
        parent_authority.update(raw)
    return parent_authority, child_authority


def _local_module_exists(child_files: set[Path], module: str) -> bool:
    relative = Path(*module.split("."))
    return (
        relative.with_suffix(".py") in child_files
        or relative / "__init__.py" in child_files
    )


def _relative_prefix(destination: Path) -> str:
    depth = len(destination.parent.parts)
    return "." * (depth + 1)


def _aliases_text(aliases: Sequence[ast.alias]) -> str:
    return ", ".join(
        alias.name + (f" as {alias.asname}" if alias.asname else "")
        for alias in aliases
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _contains_file_name(node: ast.AST) -> bool:
    return any(
        isinstance(part, ast.Name) and part.id == "__file__"
        for part in ast.walk(node)
    )


def _division_string_parts(node: ast.AST) -> tuple[str, ...]:
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, str)
    ):
        return _division_string_parts(node.left) + (node.right.value,)
    return ()


def _literal_replacement(previous: str, value: str) -> str:
    if previous.startswith('"'):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _rewrite_imports(
    content: bytes,
    *,
    destination: Path,
    child_files: set[Path],
    context: str,
    module_id: str,
    child_id: str,
    source_relative: Path,
    moved_resource_roots: frozenset[str],
) -> tuple[bytes, tuple[dict[str, str], ...]]:
    try:
        text = content.decode("utf-8")
        tree = ast.parse(text)
    except (UnicodeError, SyntaxError) as exc:
        raise NestedModuleMigrationError(
            f"{context}: cannot parse Python for import migration: {exc}"
        ) from exc
    local_roots = {
        path.parts[0].removesuffix(".py")
        for path in child_files
        if path.parts
        and (
            (len(path.parts) == 1 and path.suffix == ".py")
            or (len(path.parts) >= 2 and path.parts[1] == "__init__.py")
        )
    }
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    replacements: dict[tuple[int, int], tuple[str, str]] = {}

    def add_replacement(node: ast.AST, replacement: str) -> None:
        if node.end_lineno is None or node.end_col_offset is None:
            raise NestedModuleMigrationError(
                f"{context}: rewrite location is unavailable"
            )
        start = line_offsets[node.lineno - 1] + node.col_offset
        end = line_offsets[node.end_lineno - 1] + node.end_col_offset
        previous = text[start:end]
        existing = replacements.get((start, end))
        if existing is not None and existing != (previous, replacement):
            raise NestedModuleMigrationError(
                f"{context}: overlapping implementation rewrites are ambiguous"
            )
        replacements[(start, end)] = (previous, replacement)

    dots = _relative_prefix(destination)
    for node in ast.walk(tree):
        replacement: str | None = None
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "_rtx":
                for alias in node.names:
                    if not _local_module_exists(child_files, alias.name):
                        raise NestedModuleMigrationError(
                            f"{context}: unresolved _rtx import {alias.name}"
                        )
                replacement = f"from {dots} import {_aliases_text(node.names)}"
            elif module.startswith("_rtx."):
                local = module[len("_rtx.") :]
                if not _local_module_exists(child_files, local):
                    raise NestedModuleMigrationError(
                        f"{context}: unresolved _rtx import {module}"
                    )
                replacement = (
                    f"from {dots}{local} import {_aliases_text(node.names)}"
                )
            elif module.startswith("_") and any(
                module == root or module.startswith(root + ".")
                for root in local_roots
            ):
                if not _local_module_exists(child_files, module):
                    raise NestedModuleMigrationError(
                        f"{context}: unresolved internal import {module}"
                    )
                replacement = (
                    f"from {dots}{module} import {_aliases_text(node.names)}"
                )
        elif isinstance(node, ast.Import):
            internal = [
                alias
                for alias in node.names
                if alias.name == "_rtx"
                or alias.name.startswith("_rtx.")
                or (
                    alias.name.startswith("_")
                    and any(
                        alias.name == root
                        or alias.name.startswith(root + ".")
                        for root in local_roots
                    )
                )
            ]
            if internal:
                if len(internal) != len(node.names):
                    raise NestedModuleMigrationError(
                        f"{context}: mixed internal and external import is ambiguous"
                    )
                aliases: list[ast.alias] = []
                for alias in internal:
                    if alias.name == "_rtx":
                        raise NestedModuleMigrationError(
                            f"{context}: absolute package import '_rtx' "
                            "cannot preserve its package binding"
                        )
                    if alias.name.startswith("_rtx.") and alias.asname is None:
                        raise NestedModuleMigrationError(
                            f"{context}: unaliased absolute import "
                            f"{alias.name!r} cannot preserve its bound name"
                        )
                    local = (
                        alias.name[len("_rtx.") :]
                        if alias.name.startswith("_rtx.")
                        else alias.name
                    )
                    if not _local_module_exists(child_files, local):
                        raise NestedModuleMigrationError(
                            f"{context}: unresolved _rtx import {alias.name}"
                        )
                    aliases.append(ast.alias(name=local, asname=alias.asname))
                replacement = f"from {dots} import {_aliases_text(aliases)}"
        if replacement is None:
            continue
        add_replacement(node, replacement)

    string_constants: dict[str, ast.Constant] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            string_constants[statement.targets[0].id] = statement.value

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in {
            "DispatchCall",
            "dispatch",
        }:
            continue
        for keyword in node.keywords:
            if keyword.arg not in {"caller_skill", "caller_module_id"}:
                continue
            literal = keyword.value
            if isinstance(literal, ast.Name):
                literal = string_constants.get(literal.id)
                if literal is None:
                    raise NestedModuleMigrationError(
                        f"{context}: cannot safely rewrite dynamic dispatcher "
                        "caller for moved code"
                    )
            if not (
                isinstance(literal, ast.Constant)
                and isinstance(literal.value, str)
            ):
                raise NestedModuleMigrationError(
                    f"{context}: cannot safely rewrite dynamic dispatcher "
                    "caller for moved code"
                )
            if literal.value == child_id:
                continue
            if literal.value != module_id:
                raise NestedModuleMigrationError(
                    f"{context}: moved dispatcher caller {literal.value!r} "
                    f"does not identify {module_id!r}"
                )
            start = line_offsets[literal.lineno - 1] + literal.col_offset
            end = line_offsets[literal.end_lineno - 1] + literal.end_col_offset
            add_replacement(
                literal,
                _literal_replacement(text[start:end], child_id),
            )

    old_module_depth = len(source_relative.parts)
    new_child_depth = len(destination.parts)
    physical_depth_delta = 1 + new_child_depth - old_module_depth

    def node_text(node: ast.AST) -> str:
        assert node.end_lineno is not None
        assert node.end_col_offset is not None
        start = line_offsets[node.lineno - 1] + node.col_offset
        end = line_offsets[node.end_lineno - 1] + node.end_col_offset
        return text[start:end]

    def file_parents_index(node: ast.AST) -> int | None:
        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and _contains_file_name(node.value.value)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            and not isinstance(node.slice.value, bool)
        ):
            return None
        return int(node.slice.value)

    def file_parent_chain(node: ast.AST) -> tuple[ast.AST, int] | None:
        current = node
        depth = 0
        while (
            isinstance(current, ast.Attribute)
            and current.attr == "parent"
        ):
            depth += 1
            current = current.value
        if depth == 0 or not _contains_file_name(current):
            return None
        return current, depth

    def is_file_module_root(node: ast.AST) -> bool:
        index = file_parents_index(node)
        if index is not None:
            return index + 1 == old_module_depth
        chain = file_parent_chain(node)
        return chain is not None and chain[1] == old_module_depth

    def rebased_file_module_root(node: ast.AST) -> str:
        index = file_parents_index(node)
        if index is not None:
            original = node_text(node)
            slice_text = node_text(node.slice)
            start = original.rfind(slice_text)
            if start < 0:
                raise NestedModuleMigrationError(
                    f"{context}: cannot rebase module-root expression"
                )
            return (
                original[:start]
                + str(new_child_depth - 1)
                + original[start + len(slice_text) :]
            )
        chain = file_parent_chain(node)
        if chain is None:
            raise NestedModuleMigrationError(
                f"{context}: cannot rebase module-root expression"
            )
        base, _ = chain
        return node_text(base) + ".parent" * new_child_depth

    module_root_names: set[str] = set()
    for statement in tree.body:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and is_file_module_root(statement.value)
        ):
            continue
        module_root_names.add(statement.targets[0].id)

    protected_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
            and (
                node.right.value == "_rtx"
                or node.right.value in moved_resource_roots
            )
        ):
            continue
        if isinstance(node.left, ast.Name) and node.left.id in module_root_names:
            rebased_root = node_text(node.left)
        elif is_file_module_root(node.left):
            rebased_root = rebased_file_module_root(node.left)
        else:
            continue
        if node.right.value == "_rtx":
            replacement = rebased_root
        else:
            replacement = (
                rebased_root
                + " / "
                + _literal_replacement(
                    node_text(node.right),
                    _child_resource_root(node.right.value),
                )
            )
        assert node.end_lineno is not None
        assert node.end_col_offset is not None
        start = line_offsets[node.lineno - 1] + node.col_offset
        end = line_offsets[node.end_lineno - 1] + node.end_col_offset
        protected_spans.append((start, end))
        add_replacement(node, replacement)

    def in_protected_span(node: ast.AST) -> bool:
        assert node.end_lineno is not None
        assert node.end_col_offset is not None
        start = line_offsets[node.lineno - 1] + node.col_offset
        end = line_offsets[node.end_lineno - 1] + node.end_col_offset
        return any(
            protected_start <= start and end <= protected_end
            for protected_start, protected_end in protected_spans
        )

    parent_by_id = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        index = file_parents_index(node)
        if index is not None and not in_protected_span(node):
            old_depth = index + 1
            if old_depth == old_module_depth:
                replacement_index = new_child_depth - 1
            elif old_depth > old_module_depth:
                replacement_index = index + physical_depth_delta
            else:
                continue
            if replacement_index != index:
                add_replacement(node.slice, str(replacement_index))
            continue

        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in module_root_names
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            and not isinstance(node.slice.value, bool)
            and not in_protected_span(node)
        ):
            continue
        add_replacement(node.slice, str(node.slice.value + 1))

    for node in ast.walk(tree):
        chain = file_parent_chain(node)
        if chain is None or in_protected_span(node):
            continue
        parent = parent_by_id.get(id(node))
        if (
            isinstance(parent, ast.Attribute)
            and parent.attr == "parent"
            and parent.value is node
        ):
            continue
        base, old_depth = chain
        if old_depth == old_module_depth:
            replacement_depth = new_child_depth
        elif old_depth > old_module_depth:
            replacement_depth = old_depth + physical_depth_delta
        else:
            continue
        if replacement_depth != old_depth:
            add_replacement(
                node,
                node_text(base) + ".parent" * replacement_depth,
            )

    repository_prefixes = {
        root: f"skills/{module_id}/{root}"
        for root in moved_resource_roots
    }
    repository_module_root_names = {
        statement.targets[0].id
        for statement in ast.walk(tree)
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and _division_string_parts(statement.value)[-2:]
            == ("skills", module_id)
        )
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for root, prefix in sorted(repository_prefixes.items()):
                if prefix not in node.value:
                    continue
                rewritten, replacement_count = re.subn(
                    re.escape(prefix) + r"(?=$|/)",
                    (
                        f"skills/{module_id}/_rtx/"
                        f"{_child_resource_root(root)}"
                    ),
                    node.value,
                )
                if replacement_count == 0:
                    raise NestedModuleMigrationError(
                        f"{context}: ambiguous moved implementation path "
                        f"{node.value!r}; cannot safely rebase"
                    )
                start = line_offsets[node.lineno - 1] + node.col_offset
                end = line_offsets[node.end_lineno - 1] + node.end_col_offset
                add_replacement(
                    node,
                    _literal_replacement(text[start:end], rewritten),
                )

        if not (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
            and (
                node.right.value in moved_resource_roots
                or node.right.value == "_rtx"
            )
        ):
            continue
        if in_protected_span(node):
            continue
        root = node.right.value
        parts = _division_string_parts(node)
        if (
            isinstance(node.left, ast.Name)
            and node.left.id in repository_module_root_names
        ):
            start = (
                line_offsets[node.right.lineno - 1]
                + node.right.col_offset
            )
            end = (
                line_offsets[node.right.end_lineno - 1]
                + node.right.end_col_offset
            )
            add_replacement(
                node.right,
                _literal_replacement(
                    text[start:end],
                    f"_rtx/{_child_resource_root(root)}",
                ),
            )
            continue
        if len(parts) >= 3 and parts[-3:] == ("skills", module_id, root):
            start = (
                line_offsets[node.right.lineno - 1]
                + node.right.col_offset
            )
            end = (
                line_offsets[node.right.end_lineno - 1]
                + node.right.end_col_offset
            )
            add_replacement(
                node.right,
                _literal_replacement(
                    text[start:end],
                    f"_rtx/{_child_resource_root(root)}",
                ),
            )
            continue
        if (
            _contains_file_name(node.left)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "parent"
            and isinstance(node.left.value, ast.Attribute)
            and node.left.value.attr == "parent"
        ):
            if source_relative.parts[:1] != ("_rtx",):
                start = (
                    line_offsets[node.left.lineno - 1]
                    + node.left.col_offset
                )
                end = (
                    line_offsets[node.left.end_lineno - 1]
                    + node.left.end_col_offset
                )
                add_replacement(
                    node.left,
                    text[start:end] + ".parent",
                )
                continue
            start = (
                line_offsets[node.left.value.lineno - 1]
                + node.left.value.col_offset
            )
            end = (
                line_offsets[node.left.value.end_lineno - 1]
                + node.left.value.end_col_offset
            )
            add_replacement(node.left, text[start:end])

    if not replacements:
        return content, ()
    rewritten = text
    records: list[dict[str, str]] = []
    for (start, end), (previous, replacement) in sorted(
        replacements.items(), reverse=True
    ):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
        records.append({"from": previous, "to": replacement})
    records.reverse()
    return rewritten.encode("utf-8"), tuple(records)


def _history_dispositions(
    root: Path,
    tracked: Sequence[Path],
    identities: Mapping[str, str],
    certifier_predecessor_order: Mapping[str, int],
    expected_histories: Mapping[str, tuple[Path, Mapping[str, Any]]],
    ignored_certificate_paths: frozenset[Path],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[Path, bytes],
    set[Path],
    dict[str, str],
]:
    history_map: dict[str, dict[str, Any]] = {}
    planned: dict[Path, bytes] = {}
    removed: set[Path] = set()
    paths: dict[str, str] = {}
    public_keys = certificate_public_key_root(root)
    for relative in tracked:
        if (
            ".certificates" not in relative.parts
            or relative.suffix != ".jsonl"
            or (root / relative).is_relative_to(public_keys)
        ):
            continue
        try:
            data = _read_confined_regular(
                root,
                relative,
                context="certificate history",
            )
            entries = parse_certificate_log(
                data,
                public_keys,
                require_active_final=False,
            )
        except (CertificateLogError, OSError, ValueError) as exc:
            raise NestedModuleMigrationError(
                f"certificate history {relative.as_posix()} is invalid: {exc}"
            ) from exc
        if not entries:
            raise NestedModuleMigrationError(
                f"certificate history {relative.as_posix()} is empty"
            )
        stem = relative.stem
        expected = expected_histories.get(stem)
        if expected is None or relative != expected[0]:
            raise NestedModuleMigrationError(
                f"certificate history {relative.as_posix()} has "
                "an unexpected historical subject or path"
            )
        expected_subject = expected[1]
        for entry in entries:
            payload = entry.get("payload")
            subject = payload.get("subject") if isinstance(payload, Mapping) else None
            if (
                not isinstance(payload, Mapping)
                or payload.get("certificate_schema_version") != 1
                or not isinstance(subject, Mapping)
                or dict(subject) != dict(expected_subject)
            ):
                raise NestedModuleMigrationError(
                    f"certificate history {relative.as_posix()} has "
                    "an unexpected historical subject"
                )
        complete_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        renamed = identities.get(stem)
        if renamed is None and stem not in certifier_predecessor_order:
            history_map[stem] = {
                "disposition": "retain-valid-v1",
                "path": relative.as_posix(),
                "complete_file_hash": complete_hash,
            }
            continue
        archive = (
            Path("references/certification-history/v4-cutover")
            / relative
        )
        if (root / archive).exists():
            raise NestedModuleMigrationError(
                f"certificate history archive collision: {archive.as_posix()}"
        )
        planned[archive] = data
        if relative not in ignored_certificate_paths:
            removed.add(relative)
        paths[relative.as_posix()] = archive.as_posix()
        history_map[stem] = {
            "disposition": "archive-and-restart-v2",
            "mapped_subject": renamed or stem,
            "archive": archive.as_posix(),
            "complete_file_hash": complete_hash,
        }
        if stem in certifier_predecessor_order:
            history_map[stem]["certifier_postorder_index"] = (
                certifier_predecessor_order[stem]
            )
    return history_map, planned, removed, paths


def _legacy_history_subjects(
    modules: Sequence[_Module],
) -> dict[str, tuple[Path, Mapping[str, Any]]]:
    expected: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    for module in modules:
        nodes = ((module.declaration, module.blueprint_path),) + tuple(
            (source.declaration, source.blueprint_path)
            for source in module.sources
        )
        for declaration, blueprint_path in nodes:
            node_id = str(declaration["id"])
            gateway = declaration.get("gateway")
            gateway_path = (
                gateway.get("path") if isinstance(gateway, Mapping) else None
            )
            subject = {
                "id": node_id,
                "node_type": declaration["node_type"],
                "version": declaration["version"],
                "blueprint_path": blueprint_path.as_posix(),
                "gateway_path": (
                    (module.module_root / gateway_path).as_posix()
                    if isinstance(gateway_path, str)
                    else None
                ),
            }
            expected[node_id] = (
                module.module_root / ".certificates" / f"{node_id}.jsonl",
                subject,
            )
    return expected


def _certifier_v5_predecessor_order(
    root: Path,
    tracked: Sequence[Path],
    modules: Sequence[_Module],
    identities: Mapping[str, str],
    node_versions: Mapping[str, int],
) -> dict[str, int]:
    modules_by_id = {
        str(module.declaration["id"]): module for module in modules
    }
    if "skill-certifier" not in modules_by_id:
        return {}

    sources_by_old_id = {
        str(source.declaration["id"]): source
        for module in modules
        for source in module.sources
    }
    exports: dict[str, str] = {}
    intrinsic: dict[str, str] = {}
    for module_id, module in modules_by_id.items():
        for interface_id, declaration in module.declaration.get(
            "exports", {}
        ).items():
            if isinstance(declaration, Mapping) and isinstance(
                declaration.get("source_interface"), str
            ):
                source_interface = str(declaration["source_interface"])
                source_id = source_interface.split(".interface.", 1)[0]
                exports[str(interface_id)] = source_id
        for source in module.sources:
            source_id = str(source.declaration["id"])
            for interface_id in source.declaration.get("interfaces", {}):
                intrinsic[str(interface_id)] = source_id

    nodes: dict[str, BlueprintNode] = {}
    module_sources: dict[str, tuple[str, ...]] = {}
    certification_edges: list[CertificationEdge] = []

    def add_node(
        node_id: str,
        node_type: str,
        version: int,
        module_root: Path,
        blueprint_path: Path,
    ) -> None:
        nodes[node_id] = BlueprintNode(
            node_id=node_id,
            node_type=node_type,
            version=version,
            module_root=module_root,
            blueprint_path=blueprint_path,
            gateway_path=None,
            declaration={
                "schema_version": 5,
                "node_type": node_type,
                "id": node_id,
                "version": version,
            },
        )

    for module_id, module in modules_by_id.items():
        add_node(
            module_id,
            "module",
            node_versions[module_id],
            module.module_root,
            module.blueprint_path,
        )
        owners: dict[str, list[str]] = {module_id: []}
        if module.is_skill:
            child_id = f"{module_id}-rtx"
            child_root = module.module_root / "_rtx"
            add_node(
                child_id,
                "module",
                node_versions[child_id],
                child_root,
                child_root / "blueprint.yaml",
            )
            owners[child_id] = []
        for source in module.sources:
            old_source_id = str(source.declaration["id"])
            source_id = identities.get(old_source_id, old_source_id)
            owner_id = (
                f"{module_id}-rtx"
                if module.is_skill and source.moved
                else module_id
            )
            source_root = (
                module.module_root / "_rtx"
                if owner_id != module_id
                else module.module_root
            )
            source_blueprint = (
                source_root / "blueprints" / source.blueprint_path.name
                if owner_id != module_id
                else source.blueprint_path
            )
            add_node(
                source_id,
                "behavioral_source",
                node_versions[source_id],
                source_root,
                source_blueprint,
            )
            owners[owner_id].append(source_id)
            certification_edges.append(
                CertificationEdge(
                    "contains-source",
                    owner_id,
                    source_id,
                    node_versions[source_id],
                )
            )
        module_sources.update(
            {
                owner_id: tuple(sorted(source_ids))
                for owner_id, source_ids in owners.items()
            }
        )

        if module.is_skill:
            child_id = f"{module_id}-rtx"
            for declaration in module.declaration.get("exports", {}).values():
                if not isinstance(declaration, Mapping):
                    continue
                source_interface = declaration.get("source_interface")
                if not isinstance(source_interface, str):
                    continue
                old_source_id = source_interface.split(".interface.", 1)[0]
                if old_source_id not in identities:
                    continue
                source_id = identities[old_source_id]
                certification_edges.extend(
                    (
                        CertificationEdge(
                            "facades-child-export",
                            module_id,
                            child_id,
                            node_versions[child_id],
                        ),
                        CertificationEdge(
                            "facades-implementing-source",
                            module_id,
                            source_id,
                            node_versions[source_id],
                        ),
                    )
                )

    for old_source_id, source in sources_by_old_id.items():
        source_id = identities.get(old_source_id, old_source_id)
        for dependency in source.declaration.get("dependencies", []):
            if not isinstance(dependency, Mapping) or not isinstance(
                dependency.get("source"), str
            ):
                continue
            target_id = identities.get(
                str(dependency["source"]),
                str(dependency["source"]),
            )
            certification_edges.append(
                CertificationEdge(
                    "uses-source",
                    source_id,
                    target_id,
                    node_versions[target_id],
                )
            )
        for interface_id in _uses_interfaces(source):
            old_target_id = exports.get(interface_id) or intrinsic.get(
                interface_id
            )
            if old_target_id is None:
                raise NestedModuleMigrationError(
                    f"{old_source_id}: unresolved interface {interface_id}"
                )
            target_id = identities.get(old_target_id, old_target_id)
            certification_edges.append(
                CertificationEdge(
                    (
                        "uses-export"
                        if interface_id in exports
                        else "uses-private-interface"
                    ),
                    source_id,
                    target_id,
                    node_versions[target_id],
                )
            )

    old_direct_owners: dict[Path, str] = {}
    declarations: list[tuple[str, Path, Mapping[str, Any]]] = []
    for module_id, module in modules_by_id.items():
        module_paths = _source_content(
            root,
            module.module_root,
            module.declaration,
            tracked,
            context=module_id,
        )
        for path in module_paths:
            old_direct_owners[path] = module_id
        declarations.append(
            (module_id, module.module_root, module.declaration)
        )
        for source in module.sources:
            old_source_id = str(source.declaration["id"])
            for path in source.matched_files:
                old_direct_owners[path] = old_source_id
            declarations.append(
                (
                    old_source_id,
                    module.module_root,
                    source.declaration,
                )
            )

    try:
        for old_node_id, owner_root, declaration in declarations:
            references = list(
                _recursive_contract_references(
                    root / owner_root,
                    _reference_candidates(declaration),
                )
            )
            structured = declaration.get("contract_references", [])
            if not isinstance(structured, list):
                raise NestedModuleMigrationError(
                    f"{old_node_id}: contract_references must be a list"
                )
            seeds: list[tuple[Path, Path, str, str]] = []
            for index, locator in enumerate(structured):
                if not isinstance(locator, Mapping):
                    raise NestedModuleMigrationError(
                        f"{old_node_id}: contract_references[{index}] "
                        "must be a mapping"
                    )
                base_name = locator.get("base")
                path = locator.get("path")
                fragment = locator.get("fragment", "#")
                if (
                    base_name not in {"module-root", "repository-root"}
                    or not isinstance(path, str)
                    or not isinstance(fragment, str)
                ):
                    raise NestedModuleMigrationError(
                        f"{old_node_id}: invalid "
                        f"contract_references[{index}]"
                    )
                confined = (
                    root / owner_root
                    if base_name == "module-root"
                    else root
                )
                seeds.append((confined, confined, path, fragment))
            references.extend(
                _recursive_contract_references_from_roots(seeds)
            )
            source_id = identities.get(old_node_id, old_node_id)
            for reference in references:
                try:
                    relative = reference.path.relative_to(root)
                except ValueError as exc:
                    raise NestedModuleMigrationError(
                        f"{old_node_id}: contract reference escapes repository"
                    ) from exc
                old_target_id = old_direct_owners.get(relative)
                if old_target_id is None:
                    raise NestedModuleMigrationError(
                        f"{old_node_id}: referenced contract "
                        f"{relative.as_posix()!r} has no direct owner"
                    )
                target_id = identities.get(old_target_id, old_target_id)
                if target_id == source_id:
                    continue
                certification_edges.append(
                    CertificationEdge(
                        "references-cross-owner-contract",
                        source_id,
                        target_id,
                        node_versions[target_id],
                    )
                )
    except CertificationHashError as exc:
        raise NestedModuleMigrationError(
            f"cannot derive v5 contract-reference dependencies: {exc}"
        ) from exc

    graph = RepositoryBlueprintGraph(
        nodes=dict(sorted(nodes.items())),
        node_edges=(),
        exports={},
        export_edges=(),
        helper_edges=(),
        certification_edges=tuple(certification_edges),
        module_sources=dict(sorted(module_sources.items())),
        schema_version=5,
    )
    dependencies: dict[str, set[tuple[str, str, int]]] = {
        node_id: set() for node_id in nodes
    }
    for edge in certification_edges:
        if edge.target_version is None:
            raise NestedModuleMigrationError(
                "projected v5 certification edge lacks a target version"
            )
        dependencies[edge.source_node_id].add(
            (
                edge.relation,
                edge.target_node_id,
                edge.target_version,
            )
        )
    states = {
        node_id: NodeHashState(
            dependency_hashes=tuple(
                {
                    "relation": relation,
                    "target": target,
                    "version": version,
                }
                for relation, target, version in sorted(
                    dependencies[node_id]
                )
            )
        )
        for node_id in nodes
    }
    certifier_modules = ["skill-certifier"]
    runtime_id = "skill-certifier-rtx"
    if runtime_id in nodes:
        certifier_modules.append(runtime_id)
    roots = {
        *certifier_modules,
        *(
            source_id
            for module_id in certifier_modules
            for source_id in module_sources.get(module_id, ())
        ),
    }
    try:
        postorder = certification_target_postorder(
            graph,
            states,
            tuple(roots),
        )
    except CertificationHashError as exc:
        raise NestedModuleMigrationError(
            f"cannot derive v5 certifier predecessor order: {exc}"
        ) from exc
    index_by_v5_id = {
        node_id: index for index, node_id in enumerate(postorder)
    }
    return {
        old_node_id: index_by_v5_id[identities.get(old_node_id, old_node_id)]
        for old_node_id in (*modules_by_id, *sources_by_old_id)
        if identities.get(old_node_id, old_node_id) in index_by_v5_id
    }


def _transformed_source(
    source: _Source,
    *,
    child_id: str,
    identities: Mapping[str, str],
    use_rewrites: Mapping[str, str],
    source_targets: Mapping[str, _SourceTarget],
) -> dict[str, Any]:
    document = _deep_identity_rewrite(source.declaration, identities)
    assert isinstance(document, dict)
    moving = bool(child_id) and source.moved
    if moving:
        rebased = _rebase_module_root_paths(document)
        assert isinstance(rebased, dict)
        document = rebased
    document["schema_version"] = 5
    old_id = str(source.declaration["id"])
    new_id = identities.get(old_id, old_id)
    document["id"] = new_id
    interfaces = document.get("interfaces")
    if not isinstance(interfaces, dict):
        raise NestedModuleMigrationError(
            f"{old_id}: interfaces must be a mapping"
        )
    document["interfaces"] = {
        identities.get(interface_id, interface_id): copy.deepcopy(definition)
        for interface_id, definition in interfaces.items()
    }
    raw_uses = source.declaration.get("uses_interfaces", [])
    if not isinstance(raw_uses, list):
        raise NestedModuleMigrationError(f"{old_id}: uses_interfaces must be a list")
    rewritten_uses: list[dict[str, Any]] = []
    for raw_edge in raw_uses:
        if not isinstance(raw_edge, Mapping) or not isinstance(
            raw_edge.get("interface"), str
        ):
            raise NestedModuleMigrationError(f"{old_id}: malformed interface use")
        edge = copy.deepcopy(dict(raw_edge))
        old_interface = str(raw_edge["interface"])
        edge["interface"] = use_rewrites.get(
            old_interface,
            identities.get(old_interface, old_interface),
        )
        rewritten_uses.append(edge)
    document["uses_interfaces"] = rewritten_uses
    current_target = source_targets[old_id]
    raw_dependencies = source.declaration.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        raise NestedModuleMigrationError(
            f"{old_id}: dependencies must be a list"
        )
    rewritten_dependencies: list[dict[str, Any]] = []
    for raw_dependency in raw_dependencies:
        if not isinstance(raw_dependency, Mapping) or not isinstance(
            raw_dependency.get("source"), str
        ):
            raise NestedModuleMigrationError(
                f"{old_id}: malformed source dependency"
            )
        target_id = str(raw_dependency["source"])
        target = source_targets.get(target_id)
        if target is None:
            raise NestedModuleMigrationError(
                f"{old_id}: unknown source dependency {target_id}"
            )
        dependency = copy.deepcopy(dict(raw_dependency))
        dependency["source"] = target.source_id
        dependency["version"] = target.version
        if target.blueprint_path.is_relative_to(current_target.module_root):
            locator = {
                "base": "module-root",
                "path": target.blueprint_path.relative_to(
                    current_target.module_root
                ).as_posix(),
            }
        else:
            locator = {
                "base": "repository-root",
                "path": target.blueprint_path.as_posix(),
            }
        dependency["blueprint"] = locator
        rewritten_dependencies.append(dependency)
    document["dependencies"] = rewritten_dependencies
    if moving:
        document["version"] = 1
        gateway = document.get("gateway")
        if isinstance(gateway, dict) and isinstance(gateway.get("path"), str):
            gateway["path"] = _rebase_module_root_path(gateway["path"])
        document["content"] = [
            _rebase_content_pattern(pattern)
            for pattern in source.declaration["content"]
        ]
    else:
        normalized = copy.deepcopy(document)
        normalized["schema_version"] = source.declaration["schema_version"]
        normalized["version"] = source.declaration["version"]
        if normalized != source.declaration:
            document["version"] = int(source.declaration["version"]) + 1
    return document


def _moved_source_interfaces(source: _Source) -> dict[str, int]:
    raw = source.declaration.get("interfaces")
    if not isinstance(raw, dict):
        raise NestedModuleMigrationError(
            f"{source.declaration.get('id')}: interfaces must be a mapping"
        )
    result: dict[str, int] = {}
    for interface_id, interface in raw.items():
        if not isinstance(interface_id, str) or not isinstance(interface, dict):
            raise NestedModuleMigrationError("malformed source interface")
        version = interface.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise NestedModuleMigrationError(
                f"{interface_id}: interface version must be an integer"
            )
        result[interface_id] = version
    return result


def _uses_interfaces(source: _Source) -> tuple[str, ...]:
    raw = source.declaration.get("uses_interfaces", [])
    if not isinstance(raw, list):
        raise NestedModuleMigrationError(
            f"{source.declaration.get('id')}: uses_interfaces must be a list"
        )
    result: list[str] = []
    for edge in raw:
        if not isinstance(edge, dict) or not isinstance(edge.get("interface"), str):
            raise NestedModuleMigrationError("malformed uses_interfaces edge")
        result.append(edge["interface"])
    return tuple(result)


def _put(
    planned: dict[Path, bytes],
    path: Path,
    content: bytes,
) -> None:
    previous = planned.get(path)
    if previous is not None and previous != content:
        raise NestedModuleMigrationError(
            f"output collision at {path.as_posix()}"
        )
    planned[path] = content


def _canonical_validator_reference_file(path: Path) -> bool:
    return (
        (
            path.parts[:1] == ("tests",)
            and (
                path.name.startswith("validate_")
                or path.name == "test_officina_certification_hashing.py"
            )
        )
        or path == Path("scripts/run-python-tests.py")
    )


def _rewrite_relocated_validator_root(content: bytes) -> bytes:
    return content.replace(
        b"Path(__file__).resolve().parents[3]",
        b"Path(__file__).resolve().parents[2]",
    )


def _rewrite_canonical_validator_references(
    root: Path,
    tracked: Sequence[Path],
    planned: dict[Path, bytes],
    removed: set[Path],
    moved_from: dict[Path, Path],
    path_map: dict[str, str],
) -> None:
    for relative in tracked:
        if not _canonical_validator_reference_file(relative):
            continue
        data = _read_confined_regular(
            root,
            relative,
            context="canonical validator reference",
        )
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise NestedModuleMigrationError(
                f"{relative}: canonical validator reference is not UTF-8"
            ) from exc
        rewritten = text
        for old, new in _VALIDATOR_REFERENCE_RENAMES:
            rewritten = rewritten.replace(old, new)
        rewritten = re.sub(
            (
                r'REPO_ROOT\s*/\s*"skills"\s*/\s*"skill-maker"'
                r'\s*/\s*"validators"'
            ),
            'REPO_ROOT\n    / "validators"\n    / "skill"',
            rewritten,
        )
        target = (
            Path("tests/validate_dispatch_caller_module.py")
            if relative
            == Path("tests/validate_dispatch_caller_skill.py")
            else relative
        )
        if target != relative:
            if (root / target).exists():
                raise NestedModuleMigrationError(
                    f"validator reference relocation collision: {target}"
                )
            removed.add(relative)
            moved_from[target] = relative
            path_map[relative.as_posix()] = target.as_posix()
        if rewritten != text or target != relative:
            _put(planned, target, rewritten.encode("utf-8"))


def _overlay_canonical_cutover_references(
    planned: dict[Path, bytes],
    removed: set[Path],
    tracked: Sequence[Path],
) -> None:
    """Install the converter-owned canonical v5 reference surface."""

    project_root = Path(__file__).resolve().parents[3]
    reference_paths = [
        path
        for root in (
            project_root / "references" / "blueprint",
            project_root / "references" / "certification",
            project_root / "references" / "node-standards",
        )
        for path in root.rglob("*")
        if path.is_file()
    ]
    for source in sorted(set(reference_paths)):
        relative = source.relative_to(project_root)
        frozen_node_name = {
            Path("references/blueprint/blueprint.yaml"): "blueprint.v5.yaml",
            Path("references/blueprint/blueprints/schema-annotated-draft.yaml"): "schema-annotated-draft.v5.yaml",
            Path("references/node-standards/blueprint.yaml"): "node-standards.v5.yaml",
            Path("references/node-standards/blueprints/standards.yaml"): "node-standards-source.v5.yaml",
        }.get(relative)
        frozen_schema = _FROZEN_V5_ROOT / source.name
        if frozen_node_name is not None:
            selected_source = _FROZEN_V5_NODE_ROOT / frozen_node_name
        elif (
            relative.parent == Path("references/blueprint")
            and frozen_schema.is_file()
        ):
            selected_source = frozen_schema
        else:
            selected_source = source
        selected_relative = selected_source.relative_to(project_root)
        _put(
            planned,
            relative,
            _read_confined_regular(
                project_root,
                selected_relative,
                context="canonical cutover reference",
            ),
        )

    legacy_roots = (
        Path("references/blueprint/v5"),
    )
    legacy_files = {
        Path("references/certification/certification-basis-roots.v5.json"),
        Path(
            "references/skill-standards/"
            "skill-guidelines.v2.candidate.yaml"
        ),
        Path(
            "references/skill-standards/"
            "skill-guidelines.v2.candidate.md"
        ),
        Path("references/skill-standards/skill-guidelines.standard.yaml"),
        Path("references/skill-standards/skill-guidelines.md"),
        Path("references/skill-standards/skill-refactoring.standard.yaml"),
        Path("references/skill-standards/skill-refactoring.md"),
    }
    for relative in tracked:
        if relative in legacy_files or any(
            relative.is_relative_to(root) for root in legacy_roots
        ):
            removed.add(relative)


def _empty_plan(
    root: Path,
    commit: str,
    *,
    certificate_input_hashes: Mapping[str, str],
) -> NestedModuleMigration:
    inventory = collect_blueprints(root, expected_schema_version=5)
    if inventory.issues:
        issue = inventory.issues[0]
        raise NestedModuleMigrationError(
            f"explicit v5 validation failed: {issue.relative_path}: {issue.message}"
        )
    try:
        load_repository_blueprint_graph(
            root,
            schema_root=_v5_schema_root(root),
            expected_schema_version=5,
        )
    except Exception as exc:
        raise NestedModuleMigrationError(
            f"explicit v5 graph validation failed: {exc}"
        ) from exc
    return NestedModuleMigration(
        repo_root=root,
        source_commit=commit,
        node_version_map={},
        interface_version_map={},
        access_map={},
        identity_map={},
        path_map={},
        import_map={},
        authority_map={},
        history_map={},
        certificate_input_hashes=dict(certificate_input_hashes),
        file_disposition_map={},
        unclassified_files=(),
        file_hash_map={},
        file_mode_map={},
        planned_files={},
        operations=(),
        state_operations=(),
    )


def _plan_v4(
    root: Path,
    commit: str,
    tracked: Sequence[Path],
    modules: Sequence[_Module],
    *,
    source_modes: Mapping[Path, int],
    certificate_input_hashes: Mapping[str, str],
    ignored_certificate_paths: frozenset[Path],
) -> NestedModuleMigration:
    _validate_authority_overlap(modules)
    identities: dict[str, str] = {}
    source_interfaces: dict[str, tuple[_Source, int]] = {}
    source_modules: dict[str, str] = {}
    for module in modules:
        module_id = str(module.declaration["id"])
        child_id = f"{module_id}-rtx"
        for source in module.sources:
            source_modules[str(source.declaration["id"])] = module_id
            for interface_id, version in _moved_source_interfaces(source).items():
                if interface_id in source_interfaces:
                    raise NestedModuleMigrationError(
                        f"duplicate source interface {interface_id}"
                    )
                source_interfaces[interface_id] = (source, version)
            if module.is_skill and source.moved:
                source_id = str(source.declaration["id"])
                identities[source_id] = _renamed_source_id(
                    source_id, module_id, child_id
                )
                for interface_id in _moved_source_interfaces(source):
                    identities[interface_id] = _renamed_source_interface(
                        interface_id, module_id, child_id
                    )

    moved_exports: dict[str, tuple[str, str, str]] = {}
    private_facades: dict[str, str] = {}
    for module in modules:
        module_id = str(module.declaration["id"])
        child_id = f"{module_id}-rtx"
        for export_id, declaration in module.declaration.get("exports", {}).items():
            if not isinstance(declaration, Mapping) or not isinstance(
                declaration.get("source_interface"), str
            ):
                continue
            source_interface = str(declaration["source_interface"])
            owner = source_interfaces.get(source_interface)
            if owner is None or not module.is_skill or not owner[0].moved:
                continue
            child_export = _child_export_id(
                str(export_id), module_id, child_id
            )
            moved_exports[str(export_id)] = (
                module_id,
                child_export,
                source_interface,
            )
            previous = private_facades.get(source_interface)
            if previous is not None and previous != export_id:
                raise NestedModuleMigrationError(
                    f"{source_interface}: private interface has ambiguous facades"
                )
            private_facades[source_interface] = str(export_id)

    def source_use_rewrites(source: _Source) -> dict[str, str]:
        source_id = str(source.declaration["id"])
        owner_module = source_modules[source_id]
        rewrites: dict[str, str] = {}
        for interface_id in _uses_interfaces(source):
            moved_export = moved_exports.get(interface_id)
            if (
                source.moved
                and moved_export is not None
                and moved_export[0] == owner_module
            ):
                rewrites[interface_id] = moved_export[1]
                continue
            if not source.moved and interface_id in identities:
                facade = private_facades.get(interface_id)
                if facade is None:
                    raise NestedModuleMigrationError(
                        f"{source_id}: moved private interface {interface_id} "
                        "has no unique parent facade"
                    )
                rewrites[interface_id] = facade
        return rewrites

    moved_callers: dict[str, set[str]] = {}
    for module in modules:
        if not module.is_skill:
            continue
        child_id = f"{module.declaration['id']}-rtx"
        for source in module.sources:
            if source.moved:
                for interface_id in _uses_interfaces(source):
                    moved_callers.setdefault(interface_id, set()).add(child_id)

    def migrated_access(
        interface_id: str,
        raw_access: Any,
        *,
        owner_module_id: str,
    ) -> dict[str, Any]:
        access = _access_document(raw_access, context=interface_id)
        if access["allow_all_modules"]:
            return access
        callers = set(access["allowed_callers"])
        for child_caller in moved_callers.get(interface_id, set()):
            parent_caller = child_caller.removesuffix("-rtx")
            if parent_caller in callers or parent_caller == owner_module_id:
                callers.add(child_caller)
        access["allowed_callers"] = sorted(callers)
        return access

    source_targets: dict[str, _SourceTarget] = {}
    for module in modules:
        module_id = str(module.declaration["id"])
        child_root = module.module_root / "_rtx"
        for source in module.sources:
            source_id = str(source.declaration["id"])
            old_version = source.declaration.get("version")
            if not isinstance(old_version, int) or isinstance(old_version, bool):
                raise NestedModuleMigrationError(
                    f"{source_id}: source version must be an integer"
                )
            if module.is_skill and source.moved:
                target = _SourceTarget(
                    source_id=identities[source_id],
                    module_root=child_root,
                    blueprint_path=(
                        child_root / "blueprints" / source.blueprint_path.name
                    ),
                    version=1,
                )
            else:
                target = _SourceTarget(
                    source_id=source_id,
                    module_root=module.module_root,
                    blueprint_path=source.blueprint_path,
                    version=old_version,
                )
            source_targets[source_id] = target

    while True:
        changed = False
        for module in modules:
            child_id = (
                f"{module.declaration['id']}-rtx"
                if module.is_skill
                else ""
            )
            for source in module.sources:
                if module.is_skill and source.moved:
                    continue
                source_id = str(source.declaration["id"])
                transformed = _transformed_source(
                    source,
                    child_id=child_id,
                    identities=identities,
                    use_rewrites=source_use_rewrites(source),
                    source_targets=source_targets,
                )
                version = transformed.get("version")
                if not isinstance(version, int) or isinstance(version, bool):
                    raise NestedModuleMigrationError(
                        f"{source_id}: transformed source version is invalid"
                    )
                current = source_targets[source_id]
                if current.version != version:
                    source_targets[source_id] = _SourceTarget(
                        source_id=current.source_id,
                        module_root=current.module_root,
                        blueprint_path=current.blueprint_path,
                        version=version,
                    )
                    changed = True
        if not changed:
            break

    planned: dict[Path, bytes] = {}
    removed: set[Path] = set()
    moved_from: dict[Path, Path] = {}
    path_map: dict[str, str] = {}
    node_versions: dict[str, int] = {}
    interface_versions: dict[str, int] = {}
    access_map: dict[str, dict[str, Any]] = {}
    authority_map: dict[str, dict[str, Any]] = {}
    import_map: dict[str, Mapping[str, Any]] = {}
    unclassified_files: set[Path] = set()

    for module in modules:
        module_id = str(module.declaration["id"])
        old_version = module.declaration.get("version")
        if not isinstance(old_version, int) or isinstance(old_version, bool):
            raise NestedModuleMigrationError(
                f"{module_id}: module version must be an integer"
            )
        if not module.is_skill:
            document = copy.deepcopy(module.declaration)
            document["schema_version"] = 5
            document["children"] = {}
            document["namespace_exports"] = {}
            raw_exports = module.declaration.get("exports")
            if not isinstance(raw_exports, dict):
                raise NestedModuleMigrationError(
                    f"{module_id}: exports must be a mapping"
                )
            rewritten_exports: dict[str, Any] = {}
            boundary_changed = False
            for interface_id, raw_export in sorted(raw_exports.items()):
                if (
                    not isinstance(interface_id, str)
                    or not isinstance(raw_export, dict)
                    or not isinstance(raw_export.get("source_interface"), str)
                ):
                    raise NestedModuleMigrationError(
                        f"{module_id}: malformed export"
                    )
                source_interface = str(raw_export["source_interface"])
                owner = source_interfaces.get(source_interface)
                if owner is None:
                    raise NestedModuleMigrationError(
                        "private facade target or unknown source interface "
                        f"{source_interface}"
                    )
                access = migrated_access(
                    interface_id,
                    raw_export.get("access"),
                    owner_module_id=module_id,
                )
                rewritten_exports[interface_id] = {
                    "source_interface": source_interface,
                    "access": access,
                }
                access_map[interface_id] = access
                interface_versions[interface_id] = owner[1]
                if access != raw_export.get("access"):
                    boundary_changed = True
            document["exports"] = rewritten_exports
            document["version"] = old_version + int(boundary_changed)
            node_versions[module_id] = int(document["version"])
            authority = document.get("authority", {"owns_filesystem": []})
            assert isinstance(authority, dict)
            authority_map[module_id] = authority
            _put(planned, module.blueprint_path, _yaml_bytes(document))
            for source in module.sources:
                source_document = _transformed_source(
                    source,
                    child_id="",
                    identities=identities,
                    use_rewrites=source_use_rewrites(source),
                    source_targets=source_targets,
                )
                source_id = str(source_document["id"])
                node_versions[source_id] = int(source_document["version"])
                for interface_id, version in _moved_source_interfaces(source).items():
                    interface_versions[interface_id] = version
                _put(
                    planned,
                    source.blueprint_path,
                    _yaml_bytes(source_document),
                )
            continue

        child_id = f"{module_id}-rtx"
        child_root = module.module_root / "_rtx"
        child_marker = child_root / "blueprint.yaml"
        if (root / child_marker).exists():
            raise NestedModuleMigrationError(
                f"generated target collision: {child_marker.as_posix()} "
                "already exists"
            )
        moved_sources = tuple(source for source in module.sources if source.moved)
        original_content = set(
            _source_content(
                root,
                module.module_root,
                module.declaration,
                tracked,
                context=module_id,
            )
        )
        moved_resource_roots = {
            relative.parts[0]
            for source in moved_sources
            for matched in source.matched_files
            for relative in (matched.relative_to(module.module_root),)
            if relative.parts and relative.parts[0] != "_rtx"
        }
        auxiliary_moved: set[Path] = set()
        child_sources: dict[str, Any] = {}
        parent_sources: dict[str, Any] = {}
        child_patterns: list[str] = []
        child_files: set[Path] = {Path("__init__.py")}

        for source in module.sources:
            old_source_id = str(source.declaration["id"])
            transformed = _transformed_source(
                source,
                child_id=child_id,
                identities=identities,
                use_rewrites=source_use_rewrites(source),
                source_targets=source_targets,
            )
            new_source_id = str(transformed["id"])
            node_versions[new_source_id] = int(transformed["version"])
            for old_interface, version in _moved_source_interfaces(source).items():
                interface_versions[identities.get(old_interface, old_interface)] = (
                    version
                )
            if not source.moved:
                parent_sources[old_source_id] = copy.deepcopy(
                    module.declaration["sources"][old_source_id]
                )
                _put(
                    planned,
                    source.blueprint_path,
                    _yaml_bytes(transformed),
                )
                continue

            target_blueprint = (
                child_root / "blueprints" / source.blueprint_path.name
            )
            if (root / target_blueprint).exists():
                raise NestedModuleMigrationError(
                    f"source blueprint collision: {target_blueprint}"
                )
            child_sources[new_source_id] = {
                "blueprint": {
                    "base": "module-root",
                    "path": f"blueprints/{source.blueprint_path.name}",
                }
            }
            child_patterns.extend(transformed["content"])
            _put(planned, target_blueprint, _yaml_bytes(transformed))
            removed.add(source.blueprint_path)
            moved_from[target_blueprint] = source.blueprint_path
            path_map[source.blueprint_path.as_posix()] = (
                target_blueprint.as_posix()
            )
            for source_path in source.matched_files:
                relative = source_path.relative_to(module.module_root)
                child_relative = _child_content_path(relative)
                target = child_root / child_relative
                if target in moved_from and moved_from[target] != source_path:
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                if target != source_path and (root / target).exists():
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                moved_from[target] = source_path
                child_files.add(child_relative)
                if target != source_path:
                    removed.add(source_path)
                    path_map[source_path.as_posix()] = target.as_posix()

        if moved_sources:
            source_owners = {
                path: source
                for source in module.sources
                for path in source.matched_files
            }
            for source_path in sorted(original_content):
                relative = source_path.relative_to(module.module_root)
                if not relative.parts or relative.parts[0] not in {
                    "tests",
                    "fixtures",
                }:
                    continue
                owner = source_owners.get(source_path)
                if owner is not None:
                    continue
                target = child_root / relative
                if target in moved_from and moved_from[target] != source_path:
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                if target != source_path and (root / target).exists():
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                moved_from[target] = source_path
                child_files.add(relative)
                auxiliary_moved.add(source_path)
                moved_resource_roots.add(relative.parts[0])
                removed.add(source_path)
                path_map[source_path.as_posix()] = target.as_posix()
            runtime_evidence = _module_relative_runtime_evidence(
                moved_sources
            )
            gateway = module.declaration.get("gateway")
            gateway_path = (
                gateway.get("path")
                if isinstance(gateway, Mapping)
                and isinstance(gateway.get("path"), str)
                else None
            )
            for source_path in sorted(original_content):
                if (
                    source_path in source_owners
                    or source_path in auxiliary_moved
                ):
                    continue
                if source_path in _SUPERSEDED_V5_PATHS:
                    removed.add(source_path)
                    continue
                if (
                    module_id == "skill-maker"
                    and source_path.is_relative_to(_SKILL_VALIDATOR_ROOT)
                ):
                    continue
                relative = source_path.relative_to(module.module_root)
                if source_path.is_relative_to(child_root):
                    previous = moved_from.get(source_path)
                    if previous is not None and previous != source_path:
                        raise NestedModuleMigrationError(
                            f"output collision at {source_path.as_posix()}"
                        )
                    child_relative = source_path.relative_to(child_root)
                    child_files.add(child_relative)
                    pattern = (
                        re.escape(child_relative.parts[0]) + r"/.*"
                        if len(child_relative.parts) > 1
                        else re.escape(child_relative.as_posix())
                    )
                    if pattern not in child_patterns:
                        child_patterns.append(pattern)
                    continue
                if relative not in runtime_evidence:
                    if not _is_parent_instruction_asset(
                        relative,
                        gateway_path=gateway_path,
                    ):
                        unclassified_files.add(source_path)
                    continue
                target = child_root / relative
                if target in moved_from and moved_from[target] != source_path:
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                if target != source_path and (root / target).exists():
                    raise NestedModuleMigrationError(
                        f"output collision at {target.as_posix()}"
                    )
                moved_from[target] = source_path
                child_files.add(relative)
                auxiliary_moved.add(source_path)
                moved_resource_roots.add(relative.parts[0])
                removed.add(source_path)
                path_map[source_path.as_posix()] = target.as_posix()
            for root_name in sorted(
                {
                    path.relative_to(module.module_root).parts[0]
                    for path in auxiliary_moved
                }
            ):
                if any(
                    len(path.relative_to(module.module_root).parts) > 1
                    and path.relative_to(module.module_root).parts[0]
                    == root_name
                    for path in auxiliary_moved
                ):
                    pattern = re.escape(root_name) + r"/.*"
                else:
                    pattern = re.escape(root_name)
                if pattern not in child_patterns:
                    child_patterns.append(pattern)
        else:
            gateway = module.declaration.get("gateway")
            gateway_path = (
                gateway.get("path")
                if isinstance(gateway, Mapping)
                and isinstance(gateway.get("path"), str)
                else None
            )
            source_owners = {
                path
                for source in module.sources
                for path in source.matched_files
            }
            for source_path in sorted(original_content - source_owners):
                relative = source_path.relative_to(module.module_root)
                if source_path.is_relative_to(child_root):
                    child_relative = source_path.relative_to(child_root)
                    child_files.add(child_relative)
                    pattern = (
                        re.escape(child_relative.parts[0]) + r"/.*"
                        if len(child_relative.parts) > 1
                        else re.escape(child_relative.as_posix())
                    )
                    if pattern not in child_patterns:
                        child_patterns.append(pattern)
                    continue
                if not _is_parent_instruction_asset(
                    relative,
                    gateway_path=gateway_path,
                ):
                    unclassified_files.add(source_path)

        if any(
            path.parts[:1] == ("tests",) and path.suffix == ".py"
            for path in child_files
        ) and Path("tests/__init__.py") not in child_files:
            child_files.add(Path("tests/__init__.py"))
            _put(
                planned,
                child_root / "tests/__init__.py",
                b'"""Runtime tests for the nested code module."""\n',
            )

        for target, source_path in sorted(moved_from.items()):
            if not target.is_relative_to(child_root):
                continue
            if target in planned:
                continue
            content = (root / source_path).read_bytes()
            if target.suffix == ".py":
                content, records = _rewrite_imports(
                    content,
                    destination=target.relative_to(child_root),
                    child_files=child_files,
                    context=source_path.as_posix(),
                    module_id=module_id,
                    child_id=child_id,
                    source_relative=source_path.relative_to(
                        module.module_root
                    ),
                    moved_resource_roots=frozenset(moved_resource_roots),
                )
                if records:
                    import_map[source_path.as_posix()] = (
                        records[0]
                        if len(records) == 1
                        else {"rewrites": list(records)}
                    )
            _put(planned, target, content)

        init_path = child_root / "__init__.py"
        init_content = (
            (root / init_path).read_bytes()
            if _is_regular(root / init_path)
            else f'"""Generated runtime package for {module_id}."""\n'.encode()
        )
        _put(planned, init_path, init_content)

        raw_exports = module.declaration.get("exports")
        if not isinstance(raw_exports, dict):
            raise NestedModuleMigrationError(
                f"{module_id}: exports must be a mapping"
            )
        parent_exports: dict[str, Any] = {}
        child_exports: dict[str, Any] = {}
        for interface_id, raw_export in sorted(raw_exports.items()):
            if not isinstance(interface_id, str) or not isinstance(raw_export, dict):
                raise NestedModuleMigrationError(
                    f"{module_id}: malformed export"
                )
            source_interface = raw_export.get("source_interface")
            owner = (
                source_interfaces.get(source_interface)
                if isinstance(source_interface, str)
                else None
            )
            if owner is None:
                raise NestedModuleMigrationError(
                    "private facade target or unknown source interface "
                    f"{source_interface}"
                )
            source, version = owner
            access = migrated_access(
                interface_id,
                raw_export.get("access"),
                owner_module_id=module_id,
            )
            if not source.moved:
                parent_exports[interface_id] = {
                    "source_interface": source_interface,
                    "access": access,
                }
                access_map[interface_id] = access
                interface_versions[interface_id] = version
                continue
            facade_access = {
                "allow_all_modules": access["allow_all_modules"],
                "allowed_callers": list(access["allowed_callers"]),
            }
            ceiling_access = copy.deepcopy(facade_access)
            if not ceiling_access["allow_all_modules"]:
                ceiling_access["allowed_callers"] = sorted(
                    set(ceiling_access["allowed_callers"]) | {module_id}
                )
            child_interface = _child_export_id(
                interface_id, module_id, child_id
            )
            parent_exports[interface_id] = {
                "facade_interface": {
                    "interface": child_interface,
                    "version": version,
                },
                "access": facade_access,
            }
            child_exports[child_interface] = {
                "source_interface": identities[str(source_interface)],
                "access": ceiling_access,
            }
            access_map[interface_id] = facade_access
            access_map[child_interface] = ceiling_access
            interface_versions[interface_id] = version
            interface_versions[child_interface] = version

        moved_content = {
            path for source in moved_sources for path in source.matched_files
        } | auxiliary_moved | {
            path
            for path in removed
            if path.is_relative_to(module.module_root)
        }
        if module_id == "skill-maker":
            moved_content.update(
                path
                for path in tracked
                if path.is_relative_to(_SKILL_VALIDATOR_ROOT)
            )
        parent_paths = {
            path.relative_to(module.module_root)
            for path in original_content - moved_content
            if not path.is_relative_to(child_root)
        }
        gateway = module.declaration.get("gateway")
        if isinstance(gateway, dict) and isinstance(gateway.get("path"), str):
            parent_paths.add(Path(gateway["path"]))

        parent_authority, child_authority = _split_skill_authority(
            module,
            identities,
        )
        child_authority = _rebase_child_suggested_permissions(
            child_authority,
            context=child_id,
        )
        authority_map[module_id] = parent_authority
        authority_map[child_id] = child_authority

        parent = copy.deepcopy(module.declaration)
        parent.update(
            {
                "schema_version": 5,
                "version": old_version + 1,
                "content": [_exact_content_pattern(parent_paths)],
                "authority": parent_authority,
                "sources": parent_sources,
                "children": {
                    child_id: {
                        "base": "module-root",
                        "path": "_rtx/blueprint.yaml",
                    }
                },
                "namespace_exports": {},
                "exports": parent_exports,
            }
        )
        parent.pop("category", None)
        parent.pop("role", None)
        parent.pop("kind", None)
        parent["discovery"] = _migrated_skill_discovery(module.declaration)
        node_versions[module_id] = old_version + 1
        node_versions[child_id] = 1
        if not any(
            re.fullmatch(pattern, "__init__.py")
            for pattern in child_patterns
        ):
            child_patterns.append(r"__init__\.py")
        child = {
            "schema_version": 5,
            "node_type": "module",
            "id": child_id,
            "version": 1,
            "description": f"Code module for the {module_id} skill.",
            "gateway": {
                "path": "__init__.py",
                "language": "Python>=3.11",
            },
            "content": child_patterns,
            "authority": child_authority,
            "sources": child_sources,
            "children": {},
            "namespace_exports": {},
            "exports": child_exports,
        }
        _put(planned, module.blueprint_path, _yaml_bytes(parent))
        _put(planned, child_marker, _yaml_bytes(child))

    if unclassified_files:
        values = ", ".join(
            path.as_posix() for path in sorted(unclassified_files)
        )
        raise NestedModuleMigrationError(
            f"unclassified_files=[{values}]"
        )

    for relative in tracked:
        if not relative.is_relative_to(_SKILL_VALIDATOR_ROOT):
            continue
        suffix = relative.relative_to(_SKILL_VALIDATOR_ROOT)
        target = _NEW_VALIDATOR_ROOT / (
            Path("dispatch_caller_module.py")
            if suffix == Path("dispatch_caller_skill.py")
            else suffix
        )
        if (root / target).exists():
            raise NestedModuleMigrationError(
                f"validator relocation collision: {target}"
            )
        content = _read_confined_regular(
            root,
            relative,
            context="validator relocation input",
        )
        _put(
            planned,
            target,
            _rewrite_relocated_validator_root(content),
        )
        removed.add(relative)
        moved_from[target] = relative
        path_map[relative.as_posix()] = target.as_posix()

    _rewrite_canonical_validator_references(
        root,
        tracked,
        planned,
        removed,
        moved_from,
        path_map,
    )
    _overlay_canonical_cutover_references(planned, removed, tracked)

    history_map, history_files, history_removed, history_paths = (
        _history_dispositions(
            root,
            tracked,
            identities,
            _certifier_v5_predecessor_order(
                root,
                tracked,
                modules,
                identities,
                node_versions,
            ),
            _legacy_history_subjects(modules),
            ignored_certificate_paths,
        )
    )
    for path, content in history_files.items():
        _put(planned, path, content)
    removed.update(history_removed)
    path_map.update(history_paths)

    operations: list[_Operation] = []
    moved_sources_in_operations: set[Path] = set()
    for path, content in sorted(planned.items()):
        source_path = moved_from.get(path)
        if source_path is not None and source_path != path:
            if (root / path).exists():
                raise NestedModuleMigrationError(
                    f"generated target collision: {path} already exists"
                )
            operations.append(
                _Operation(
                    "move",
                    path,
                    source_path,
                    source_modes.get(source_path, 0o644),
                )
            )
            moved_sources_in_operations.add(source_path)
        else:
            current = (
                (root / path).read_bytes()
                if _is_regular(root / path)
                else None
            )
            if current != content:
                operations.append(
                    _Operation(
                        "write" if current is not None else "create",
                        path,
                        mode=(
                            source_modes.get(path, 0o644)
                            if current is not None
                            else 0o644
                        ),
                    )
                )
    for path in sorted(removed - moved_sources_in_operations):
        operations.append(_Operation("delete", path))
    operations.sort(
        key=lambda operation: (
            operation.path.as_posix(),
            operation.action,
        )
    )

    string_files = {
        path.as_posix(): content for path, content in sorted(planned.items())
    }
    hashes = {
        path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in string_files.items()
    }
    modes: dict[str, int] = {}
    for path in sorted(planned):
        source_path = moved_from.get(path)
        existing = source_path if source_path is not None else path
        modes[path.as_posix()] = (
            source_modes.get(existing, 0o644)
            if _is_regular(root / existing)
            else 0o644
        )
    operation_by_path = {
        operation.path: operation for operation in operations
    }
    move_by_source = {
        operation.source_path: operation.path
        for operation in operations
        if operation.action == "move" and operation.source_path is not None
    }
    dispositions: dict[str, dict[str, Any]] = {}
    for path in tracked:
        if path in move_by_source:
            dispositions[path.as_posix()] = {
                "disposition": "move",
                "target": move_by_source[path].as_posix(),
            }
            continue
        operation = operation_by_path.get(path)
        if operation is not None and operation.action == "delete":
            dispositions[path.as_posix()] = {"disposition": "delete"}
        elif operation is not None and operation.action == "write":
            dispositions[path.as_posix()] = {"disposition": "rewrite"}
        else:
            dispositions[path.as_posix()] = {"disposition": "retain"}
    for operation in operations:
        if operation.action == "create":
            dispositions[operation.path.as_posix()] = {
                "disposition": "create"
            }
    for path in ignored_certificate_paths:
        dispositions.setdefault(
            path.as_posix(),
            {"disposition": "certificate-input"},
        )
    state_operations: list[dict[str, Any]] = []
    for old_path, archive in history_paths.items():
        relative = Path(old_path)
        if relative not in ignored_certificate_paths:
            continue
        history_item = next(
            (
                (node_id, entry)
                for node_id, entry in history_map.items()
                if entry.get("archive") == archive
            ),
            None,
        )
        if history_item is None:
            raise NestedModuleMigrationError(
                f"archived history lacks disposition metadata: {old_path}"
            )
        node_id, history = history_item
        order = history.get("certifier_postorder_index")
        if order is None:
            child_orders = [
                entry["certifier_postorder_index"]
                for candidate_id, entry in history_map.items()
                if candidate_id.startswith(node_id + ".source.")
                and isinstance(
                    entry.get("certifier_postorder_index"),
                    int,
                )
            ]
            if child_orders:
                order = min(child_orders) - 0.5
        state_operations.append(
            {
                "action": "remove-active-at-authorized-cutover",
                "archive": archive,
                "complete_file_hash": history["complete_file_hash"],
                "path": old_path,
                "_order": order,
            }
        )
        dispositions[old_path] = {
            "disposition": "state-remove-at-authorized-cutover",
            "archive": archive,
        }
    state_operations.sort(
        key=lambda operation: (
            operation["_order"] is None,
            (
                operation["_order"]
                if operation["_order"] is not None
                else operation["path"]
            ),
        )
    )
    for operation in state_operations:
        operation.pop("_order")
    return NestedModuleMigration(
        repo_root=root,
        source_commit=commit,
        node_version_map=dict(sorted(node_versions.items())),
        interface_version_map=dict(sorted(interface_versions.items())),
        access_map=dict(sorted(access_map.items())),
        identity_map=dict(sorted(identities.items())),
        path_map=dict(sorted(path_map.items())),
        import_map=dict(sorted(import_map.items())),
        authority_map=dict(sorted(authority_map.items())),
        history_map=dict(sorted(history_map.items())),
        certificate_input_hashes=dict(sorted(certificate_input_hashes.items())),
        file_disposition_map=dict(sorted(dispositions.items())),
        unclassified_files=(),
        file_hash_map=hashes,
        file_mode_map=modes,
        planned_files=string_files,
        operations=tuple(operations),
        state_operations=tuple(state_operations),
    )


def _remove_candidate_path(root: Path, relative: Path) -> None:
    if relative.is_absolute() or ".." in relative.parts:
        raise NestedModuleMigrationError(
            f"unsafe candidate removal path: {relative}"
        )
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise NestedModuleMigrationError(
                f"unsafe candidate removal parent: {relative}"
            )
    target = root / relative
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise NestedModuleMigrationError(
            f"unsafe candidate removal target: {relative}"
        )
    if target.exists():
        target.unlink()


def _candidate_output_path(source_root: Path, output_dir: Path) -> Path:
    raw = Path(output_dir)
    absolute = Path(os.path.abspath(raw))
    if absolute.exists() or absolute.is_symlink():
        raise NestedModuleMigrationError(
            f"candidate output already exists; refusing overwrite: {absolute}"
        )
    for ancestor in reversed(absolute.parents):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise NestedModuleMigrationError(
                f"unsafe candidate output ancestor: {ancestor}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise NestedModuleMigrationError(
                f"unsafe candidate output ancestor: {ancestor}"
            )
    resolved = absolute.resolve(strict=False)
    source = Path(source_root).resolve()
    if resolved == source or resolved.is_relative_to(source):
        raise NestedModuleMigrationError(
            "candidate output must be outside the source repository"
        )
    return resolved


def _verify_committed_outputs(
    candidate_root: Path,
    commit: str,
    plan: NestedModuleMigration,
) -> None:
    for relative_text, expected_hash in plan.file_hash_map.items():
        relative = Path(relative_text)
        expected_file_mode = plan.file_mode_map.get(relative_text)
        if expected_file_mode is None:
            raise NestedModuleMigrationError(
                f"planned output lacks exact mode evidence: {relative_text}"
            )
        result = run_git(
            candidate_root,
            "ls-tree",
            "-z",
            commit,
            "--",
            relative.as_posix(),
        )
        records = [
            record
            for record in result.stdout.rstrip(b"\0").split(b"\0")
            if record
        ]
        if len(records) != 1:
            raise NestedModuleMigrationError(
                f"committed candidate output is not exact: {relative_text}"
            )
        metadata, separator, returned_path = records[0].partition(b"\t")
        fields = metadata.split()
        if (
            not separator
            or returned_path != os.fsencode(relative.as_posix())
            or len(fields) != 3
            or fields[1] != b"blob"
        ):
            raise NestedModuleMigrationError(
                f"committed candidate output is malformed: {relative_text}"
            )
        expected_mode = (
            b"100755" if expected_file_mode & 0o111 else b"100644"
        )
        if fields[0] != expected_mode:
            raise NestedModuleMigrationError(
                f"committed candidate mode mismatch: {relative_text}"
            )
        blob = run_git(candidate_root, "cat-file", "blob", fields[2].decode("ascii"))
        actual_hash = "sha256:" + hashlib.sha256(blob.stdout).hexdigest()
        if actual_hash != expected_hash:
            raise NestedModuleMigrationError(
                f"committed candidate hash mismatch: {relative_text}"
            )


def _materialize(
    plan: NestedModuleMigration,
    output_dir: Path,
) -> NestedModuleCandidate:
    _assert_source_matches_plan(plan)
    output = _candidate_output_path(plan.repo_root, output_dir)
    output.mkdir(parents=True)
    try:
        run_git(output, "init", "-q")
        run_git(output, "config", "user.name", "Nested Module Migration")
        run_git(
            output,
            "config",
            "user.email",
            "nested-module-migration@example.invalid",
        )
        run_git(
            output,
            "fetch",
            "--no-tags",
            plan.repo_root.as_posix(),
            plan.source_commit,
        )
        run_git(output, "update-ref", "HEAD", plan.source_commit)
        run_git(output, "reset", "--hard", plan.source_commit)

        changed: set[Path] = set()
        for operation in plan.operations:
            if operation.action in {"move", "write", "create"}:
                content = plan.planned_files.get(operation.path.as_posix())
                if content is None:
                    raise NestedModuleMigrationError(
                        f"planned operation lacks output bytes: {operation.path}"
                    )
                try:
                    atomic_candidate_write(
                        output,
                        operation.path,
                        content,
                        context="nested-module migration",
                    )
                except MigrationCandidateError as exc:
                    raise NestedModuleMigrationError(str(exc)) from exc
                if operation.mode is not None:
                    (output / operation.path).chmod(operation.mode)
                changed.add(operation.path)
            if operation.action in {"move", "delete"}:
                source = (
                    operation.source_path
                    if operation.action == "move"
                    else operation.path
                )
                assert source is not None
                _remove_candidate_path(output, source)
                changed.add(source)
        for relative_text, expected in plan.file_hash_map.items():
            relative = Path(relative_text)
            path = output / relative
            if path.is_symlink() or not path.is_file():
                raise NestedModuleMigrationError(
                    f"planned output is not a regular file: {relative_text}"
                )
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise NestedModuleMigrationError(
                    f"planned output hash mismatch: {relative_text}"
                )
        if not changed:
            raise NestedModuleMigrationError(
                "cannot materialize an empty migration plan"
            )
        try:
            commit = candidate_commit(
                output,
                "materialize nested module v5 candidate",
                changed,
                commit_timestamp=_DETERMINISTIC_COMMIT_DATE,
            )
            _verify_committed_outputs(output, commit, plan)
            cutover = candidate_cutover_manifest(
                output,
                plan.source_commit,
                commit,
            )
        except MigrationCandidateError as exc:
            raise NestedModuleMigrationError(str(exc)) from exc
        actual_paths = {
            change.path for change in cutover
        } | {
            change.source_path
            for change in cutover
            if change.source_path is not None
        }
        if actual_paths != changed:
            raise NestedModuleMigrationError(
                "candidate Git manifest differs from planned operations: "
                f"missing={sorted(path.as_posix() for path in changed - actual_paths)}, "
                f"unexpected={sorted(path.as_posix() for path in actual_paths - changed)}"
            )
        actual_evidence = {
            (
                change.status,
                change.path.as_posix(),
                (
                    change.source_path.as_posix()
                    if change.source_path is not None
                    else None
                ),
            )
            for change in cutover
        }
        expected_evidence: set[tuple[str, str, str | None]] = set()
        for operation in plan.operations:
            if operation.action == "move":
                assert operation.source_path is not None
                expected_evidence.add(
                    ("D", operation.source_path.as_posix(), None)
                )
                expected_evidence.add(("A", operation.path.as_posix(), None))
            elif operation.action == "delete":
                expected_evidence.add(("D", operation.path.as_posix(), None))
            elif operation.action == "create":
                expected_evidence.add(("A", operation.path.as_posix(), None))
            elif operation.action == "write":
                expected_evidence.add(("M", operation.path.as_posix(), None))
        if actual_evidence != expected_evidence:
            raise NestedModuleMigrationError(
                "candidate Git evidence differs from dry-run operations"
            )
        inventory = collect_blueprints(output, expected_schema_version=5)
        if inventory.issues:
            issue = inventory.issues[0]
            raise NestedModuleMigrationError(
                f"candidate explicit v5 validation failed: "
                f"{issue.relative_path}: {issue.message}"
            )
        try:
            load_repository_blueprint_graph(
                output,
                schema_root=_v5_schema_root(output),
                expected_schema_version=5,
            )
        except Exception as exc:
            raise NestedModuleMigrationError(
                f"candidate explicit v5 graph validation failed: {exc}"
            ) from exc
        second = build_nested_module_migration(output)
        if not second.is_noop or second.operations:
            raise NestedModuleMigrationError(
                "materialized candidate does not replan as a no-op"
            )
        _assert_source_matches_plan(plan)
        return NestedModuleCandidate(
            root=output,
            commit=commit,
            manifest_bytes=plan.render_manifest(),
            cutover_manifest=cutover,
            cutover_paths=tuple(sorted(actual_paths)),
        )
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        raise


def build_nested_module_migration(repo_root: Path) -> NestedModuleMigration:
    """Build a pure migration plan for the exact committed repository state."""

    root = Path(repo_root)
    if root.is_symlink():
        raise NestedModuleMigrationError("repository root must not be a symlink")
    root = root.resolve()
    snapshot = capture_git_snapshot(root)
    if snapshot is None or snapshot.repo_root != root:
        raise NestedModuleMigrationError(
            "migration requires the exact Git repository root"
        )
    if run_git(root, "status", "--porcelain=v1", "-z").stdout:
        raise NestedModuleMigrationError(
            "migration planning requires a clean committed repository"
        )
    live_tracked = _tracked_paths(root)
    _verify_tracked_inputs_match_head(
        root,
        snapshot.commit,
        live_tracked,
    )
    certificate_inputs = _live_certificate_inputs(root)
    certificate_hashes = _certificate_input_hashes(certificate_inputs)
    ignored_certificate_paths = frozenset(
        path for path in certificate_inputs if path not in live_tracked
    )
    with tempfile.TemporaryDirectory(
        prefix="nested-module-plan-snapshot-"
    ) as temporary:
        snapshot_root = Path(temporary) / "repository"
        snapshot_root.mkdir()
        tracked, source_modes = _materialize_committed_snapshot(
            root,
            snapshot.commit,
            snapshot_root,
        )
        if tracked != live_tracked:
            raise NestedModuleMigrationError(
                "source commit inventory differs from the Git index"
            )
        _install_certificate_snapshot(
            snapshot_root,
            certificate_inputs,
        )
        planning_paths = tuple(
            sorted(set(tracked) | set(certificate_inputs))
        )
        for relative in planning_paths:
            if (
                len(relative.parts) == 4
                and relative.parts[0] == "skills"
                and relative.parts[2:] == ("_rtx", "blueprint.yaml")
            ):
                parent_marker = (
                    Path("skills") / relative.parts[1] / "blueprint.yaml"
                )
                if parent_marker in planning_paths:
                    parent = _read_yaml(
                        snapshot_root / parent_marker,
                        root=snapshot_root,
                    )
                    if parent.get("schema_version") == 4:
                        raise NestedModuleMigrationError(
                            f"generated target collision: "
                            f"{relative.as_posix()} already exists"
                        )
        modules = _load_v4_modules(snapshot_root, planning_paths)
        if not modules:
            planned = _empty_plan(
                snapshot_root,
                snapshot.commit,
                certificate_input_hashes=certificate_hashes,
            )
        else:
            planned = _plan_v4(
                snapshot_root,
                snapshot.commit,
                planning_paths,
                modules,
                source_modes=source_modes,
                certificate_input_hashes=certificate_hashes,
                ignored_certificate_paths=ignored_certificate_paths,
            )
        plan = replace(planned, repo_root=root)
    _assert_source_matches_plan(plan)
    return plan
