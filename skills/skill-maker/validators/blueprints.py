"""Validate blueprint presence, schema correctness, and contract-block sync rules."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from officina.common.blueprint_graph import (  # noqa: E402
    BlueprintNode,
    BlueprintGraphError,
    SkillBlueprintGraph,
    authored_node_input_paths,
    expanded_legacy_blueprint,
    load_validated_skill_blueprint_graph,
    load_skill_blueprint_graph,
    relationship_target_types,
    typed_declaration_schema_errors,
    validate_runtime_file_path,
)

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in misconfigured envs
    jsonschema = None

_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "_rtx" / "_blueprint_syncer.py"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "references" / "blueprint" / "schema.json"

CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.YAMLError(f"duplicate key `{key}`")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_schema() -> dict[str, Any] | None:
    if not _SCHEMA_PATH.exists():
        return None
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_blueprint_schema(
    blueprint_path: Path,
    blueprint: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """Run jsonschema validation; return error strings."""
    if blueprint.get("schema_version") == 2 or "blueprint_type" in blueprint:
        try:
            return [
                str(error)
                for error in typed_declaration_schema_errors(
                    blueprint_path,
                    blueprint,
                    _SCHEMA_PATH.parent,
                )
            ]
        except (BlueprintGraphError, OSError, json.JSONDecodeError) as exc:
            return [str(exc)]
    errors: list[str] = []
    if jsonschema is None:
        return [
            f"{blueprint_path}: cannot validate blueprint schema because required "
            "Python package `jsonschema` is not installed"
        ]
    schema_root = _SCHEMA_PATH.parent
    store: dict[str, dict[str, Any]] = {}
    for child in schema_root.glob("*.schema.json"):
        document = json.loads(child.read_text(encoding="utf-8"))
        store[child.name] = document
        store[child.resolve().as_uri()] = document
        schema_id = document.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = document
    selected_schema = store.get("legacy-skill.schema.json", schema)
    resolver = jsonschema.RefResolver(
        base_uri=schema_root.resolve().as_uri() + "/",
        referrer=selected_schema,
        store=store,
    )
    validator = jsonschema.Draft7Validator(selected_schema, resolver=resolver)
    for error in sorted(validator.iter_errors(blueprint), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{blueprint_path}: schema error at {path}: {error.message}")
    return errors


# All valid category nodes (excluding structural root 'assistant').
# Must stay in sync with references/blueprint/schema.json enum.
_CATEGORY_NODES: frozenset[str] = frozenset({
    "research-assistant",
    "general-assistant",
    "productivity-general-assistant",
    "workflow-general-assistant",
    "development-assistant",
    "skill-making-development-assistant",
    "coding-development-assistant",
    "system-assistant",
})
_CATEGORY_ROOT = "assistant"


def _validate_category_hierarchy(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Enforce the postfix hierarchy rule: every non-root category must end with
    '-{parent}' where parent is a known category node or the structural root.

    Uses longest-suffix match to find the immediate parent.
    """
    errors: list[str] = []
    cat = blueprint.get("category")
    if not isinstance(cat, str):
        return errors  # schema validation handles type/enum errors
    all_nodes = _CATEGORY_NODES | {_CATEGORY_ROOT}
    # Find longest known suffix of the form '-<node>'
    parent = max(
        (node for node in all_nodes if cat != node and cat.endswith(f"-{node}")),
        key=len,
        default=None,
    )
    if parent is None:
        errors.append(
            f"{blueprint_path}: category '{cat}' has no valid parent in the taxonomy tree "
            f"(expected name ending with '-assistant' or '-<parent-node>')"
        )
    return errors


def _validate_interface_cross_fields(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Python-only checks that jsonschema cannot express.

    Currently enforces: if an interface has description, it must also have usage.
    (jsonschema marks both as optional individually; the pairing rule requires Python.)
    """
    errors: list[str] = []
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return errors
    machine = interfaces.get("machine") or {}
    if not isinstance(machine, dict):
        return errors
    for iface_name, spec in machine.items():
        if not isinstance(spec, dict):
            continue
        has_desc = bool((spec.get("description") or "").strip())
        has_usage = spec.get("usage") is not None
        if has_desc and not has_usage:
            errors.append(
                f"{blueprint_path}: machine interface '{iface_name}' has description but no usage field "
                "(add usage: \"\" for no-arg interfaces, or the full arg template)"
            )
    return errors


_REGULAR_GIT_MODES = {"100644", "100755"}


def _git_tracked_files(
    repo_root: Path,
) -> dict[str, tuple[tuple[str, str], ...]] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--stage", "-z"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    entries: dict[str, list[tuple[str, str]]] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        metadata, separator, relative_path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or not relative_path:
            return None
        mode, _object_id, stage = fields
        entries.setdefault(relative_path, []).append((mode, stage))
    return {path: tuple(values) for path, values in entries.items()}


def _validate_typed_source_files(
    graph: SkillBlueprintGraph,
    repo_root: Path,
    tracked_files: dict[str, tuple[tuple[str, str], ...]],
) -> list[str]:
    """Require lexical typed source inputs to be regular, non-symlink, and tracked."""

    errors: list[str] = []
    for node in graph.nodes.values():
        try:
            paths = authored_node_input_paths(node)
        except BlueprintGraphError as exc:
            errors.append(str(exc))
            continue
        for path in paths:
            try:
                validate_runtime_file_path(path, node.skill_root, repo_root)
            except BlueprintGraphError as exc:
                errors.append(str(exc))
            lexical_path = Path(os.path.abspath(path))
            try:
                relative_path = lexical_path.relative_to(
                    Path(os.path.abspath(repo_root))
                ).as_posix()
            except ValueError:
                relative_path = lexical_path.as_posix()
            index_entries = tracked_files.get(relative_path)
            if not index_entries:
                errors.append(
                    f"{node.blueprint_path}: authored source file is not tracked by git: "
                    f"{relative_path}"
                )
            elif any(stage != "0" for _mode, stage in index_entries):
                errors.append(
                    f"{node.blueprint_path}: authored source file has nonzero Git index "
                    f"stages: {relative_path}"
                )
            elif len(index_entries) != 1:
                errors.append(
                    f"{node.blueprint_path}: authored source file must have exactly one "
                    f"stage-0 Git index entry: {relative_path}"
                )
            elif index_entries[0][0] not in _REGULAR_GIT_MODES:
                errors.append(
                    f"{node.blueprint_path}: authored source file Git index entry is not "
                    f"a regular file: {relative_path}"
                )
    return errors


_FIELD_LEVEL_CONTENT_SUFFIXES = frozenset({
    "body",
    "date",
    "header",
    "id",
    "name",
    "subject",
    "title",
})

_FORMAT_BY_EXTENSION: dict[str, str] = {
    "bib": "bibtex",
    "bibtex": "bibtex",
    "env": "env",
    "html": "html",
    "ics": "ics",
    "ini": "ini",
    "json": "json",
    "md": "markdown",
    "markdown": "markdown",
    "pdf": "pdf",
    "png": "png",
    "rfc822": "rfc822",
    "svg": "svg",
    "tex": "tex",
    "text": "text",
    "toml": "toml",
    "txt": "text",
    "yaml": "yaml",
    "yml": "yaml",
}


def _declared_entry_formats(entry: dict[str, Any]) -> set[str]:
    format_value = entry.get("format")
    formats_value = entry.get("formats")
    declared: set[str] = set()
    if isinstance(format_value, str):
        declared.add(format_value)
    if isinstance(formats_value, list):
        declared.update(item for item in formats_value if isinstance(item, str))
    return declared


def _formats_from_path_suffix(path: str) -> set[str]:
    brace_match = re.search(r"\.\{([^{}]+)\}$", path)
    extensions: list[str]
    if brace_match:
        extensions = brace_match.group(1).split(",")
    else:
        suffix_match = re.search(r"\.([A-Za-z0-9]+)$", path)
        if not suffix_match:
            return set()
        extensions = [suffix_match.group(1)]

    formats: set[str] = set()
    for extension in extensions:
        normalized = extension.strip().lower().lstrip(".")
        if not normalized:
            return set()
        mapped = _FORMAT_BY_EXTENSION.get(normalized)
        if mapped is None:
            return set()
        formats.add(mapped)
    return formats


def _validate_glob_path(path: str) -> list[str]:
    errors: list[str] = []
    if "[" in path or "]" in path:
        errors.append(
            "glob paths do not support [] character classes; use '*.{md,pdf}' "
            "for extension families"
        )
    if "?" in path:
        errors.append("glob paths do not support '?' wildcards; use '*' or '**'")
    for segment in path.split("/"):
        if "**" in segment and segment != "**":
            errors.append("glob '**' must be a complete path segment")
    if "{" in path or "}" in path:
        brace_matches = list(re.finditer(r"\{([^{}]+)\}", path))
        if len(brace_matches) != 1 or brace_matches[0].end() != len(path):
            errors.append("glob brace groups are only allowed as a final extension family")
        else:
            items = brace_matches[0].group(1).split(",")
            if len(items) < 2:
                errors.append("glob extension family must contain at least two extensions")
            for item in items:
                if not re.fullmatch(r"[A-Za-z0-9]+", item):
                    errors.append(
                        "glob extension family entries must be comma-separated bare extensions"
                    )
                    break
    return errors


def _validate_direct_io_content_granularity(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Reject field-level content labels that should stay inside aggregate content."""
    errors: list[str] = []
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return errors
    for namespace in ("machine", "llm"):
        namespace_interfaces = interfaces.get(namespace) or {}
        if not isinstance(namespace_interfaces, dict):
            continue
        for iface_name, spec in namespace_interfaces.items():
            if not isinstance(spec, dict):
                continue
            direct_io = spec.get("direct_io") or {}
            if not isinstance(direct_io, dict):
                continue
            for section in ("reads", "writes", "network"):
                entries = direct_io.get(section) or []
                if not isinstance(entries, list):
                    continue
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    content = entry.get("content")
                    if not isinstance(content, str):
                        continue
                    suffix = content.rsplit("-", 1)[-1]
                    if suffix in _FIELD_LEVEL_CONTENT_SUFFIXES:
                        errors.append(
                            f"{blueprint_path}: {namespace} interface '{iface_name}' "
                            f"direct_io.{section}.{index}.content uses field-level value "
                            f"'{content}'; use a coarser aggregate content value"
                        )
    return errors


def _validate_direct_io_path_patterns(
    blueprint_path: Path,
    blueprint: dict[str, Any],
) -> list[str]:
    """Validate direct_io path matching mode and suffix-derived formats."""
    errors: list[str] = []
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return errors
    for namespace in ("machine", "llm"):
        namespace_interfaces = interfaces.get(namespace) or {}
        if not isinstance(namespace_interfaces, dict):
            continue
        for iface_name, spec in namespace_interfaces.items():
            if not isinstance(spec, dict):
                continue
            direct_io = spec.get("direct_io") or {}
            if not isinstance(direct_io, dict):
                continue
            for section in ("reads", "writes", "network"):
                entries = direct_io.get(section) or []
                if not isinstance(entries, list):
                    continue
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    path = entry.get("path")
                    if not isinstance(path, str):
                        continue
                    context = (
                        f"{blueprint_path}: {namespace} interface '{iface_name}' "
                        f"direct_io.{section}.{index}"
                    )
                    path_match = entry.get("path_match", "exact")
                    if path_match == "regex":
                        try:
                            re.compile(path)
                        except re.error as exc:
                            errors.append(f"{context}.path regex '{path}' is invalid: {exc}")
                    elif path_match == "glob":
                        for message in _validate_glob_path(path):
                            errors.append(f"{context}.path '{path}' is invalid: {message}")
                    elif path_match != "exact":
                        continue

                    declared_formats = _declared_entry_formats(entry)
                    inferred_formats = (
                        set() if path_match == "regex" else _formats_from_path_suffix(path)
                    )
                    if declared_formats and inferred_formats and declared_formats != inferred_formats:
                        declared = ", ".join(sorted(declared_formats))
                        inferred = ", ".join(sorted(inferred_formats))
                        errors.append(
                            f"{context}.path '{path}' implies format(s) [{inferred}] "
                            f"but declares [{declared}]"
                        )
    return errors


def _canonical_interface_names(skill_name: str, blueprint: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return names
    for namespace in ("machine", "llm"):
        namespace_interfaces = interfaces.get(namespace) or {}
        if not isinstance(namespace_interfaces, dict):
            continue
        for iface_name in namespace_interfaces:
            names.add(f"{skill_name}.{namespace}.{iface_name}")
    return names


def _canonical_interface_versions(skill_name: str, blueprint: dict[str, Any]) -> dict[str, int]:
    versions: dict[str, int] = {}
    interfaces = blueprint.get("interfaces") or {}
    if not isinstance(interfaces, dict):
        return versions
    for namespace in ("machine", "llm"):
        namespace_interfaces = interfaces.get(namespace) or {}
        if not isinstance(namespace_interfaces, dict):
            continue
        for iface_name, spec in namespace_interfaces.items():
            if not isinstance(spec, dict):
                continue
            version = spec.get("version")
            if isinstance(version, int):
                versions[f"{skill_name}.{namespace}.{iface_name}"] = version
    return versions


def _split_canonical_interface(name: str) -> tuple[str, str, str] | None:
    parts = name.split(".")
    if len(parts) != 3:
        return None
    skill, namespace, interface_name = parts
    if namespace not in {"machine", "llm"}:
        return None
    if not skill or not interface_name:
        return None
    return skill, namespace, interface_name


def _validate_interface_uses(
    blueprints: dict[Path, dict[str, Any]],
) -> list[str]:
    """Enforce version-pinned interface dependency edges."""
    errors: list[str] = []
    interface_versions: dict[str, int] = {}
    for blueprint_path, blueprint in blueprints.items():
        interface_versions.update(_canonical_interface_versions(blueprint_path.parent.name, blueprint))

    for blueprint_path, blueprint in blueprints.items():
        skill_name = blueprint_path.parent.name
        interfaces = blueprint.get("interfaces") or {}
        if not isinstance(interfaces, dict):
            continue
        for source_namespace in ("machine", "llm"):
            namespace_interfaces = interfaces.get(source_namespace) or {}
            if not isinstance(namespace_interfaces, dict):
                continue
            for source_name, spec in namespace_interfaces.items():
                if not isinstance(spec, dict):
                    continue
                source = f"{skill_name}.{source_namespace}.{source_name}"
                uses_interfaces = spec.get("uses_interfaces") or []
                if not isinstance(uses_interfaces, list):
                    continue
                for index, edge in enumerate(uses_interfaces):
                    if not isinstance(edge, dict):
                        continue
                    target = edge.get("interface")
                    requested_version = edge.get("version")
                    if not isinstance(target, str) or not isinstance(requested_version, int):
                        continue
                    parsed = _split_canonical_interface(target)
                    if parsed is None:
                        continue
                    _target_skill, target_namespace, _target_name = parsed
                    if target not in interface_versions:
                        errors.append(
                            f"{blueprint_path}: {source} uses_interfaces.{index}.interface "
                            f"targets unknown interface '{target}'"
                        )
                        continue
                    actual_version = interface_versions[target]
                    if requested_version != actual_version:
                        errors.append(
                            f"{blueprint_path}: {source} uses_interfaces.{index} pins "
                            f"{target} version {requested_version}, but target version is {actual_version}"
                        )
                    target_type = f"{target_namespace}-interface"
                    allowed_targets = relationship_target_types(
                        _SCHEMA_PATH.parent,
                        f"{source_namespace}-interface",
                        "uses-interface",
                    )
                    if target_type not in allowed_targets:
                        errors.append(
                            f"{blueprint_path}: {source} uses_interfaces.{index}.interface "
                            f"targets {target}; relationship matrix forbids this source and "
                            "target type"
                        )
    return errors


def _ownership_matches(owner: dict[str, Any], path: str) -> bool:
    owner_path = owner.get("path")
    if not isinstance(owner_path, str):
        return False
    match_kind = owner.get("match")
    if match_kind == "exact":
        return path == owner_path
    if match_kind == "regex":
        try:
            return re.fullmatch(owner_path, path) is not None
        except re.error:
            return False
    return False


def _ownerships_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_path = left.get("path")
    right_path = right.get("path")
    if not isinstance(left_path, str) or not isinstance(right_path, str):
        return False

    left_match = left.get("match")
    right_match = right.get("match")
    if left_match == "exact" and right_match == "exact":
        return left_path == right_path
    if left_match == "exact" and right_match == "regex":
        return _ownership_matches(right, left_path)
    if left_match == "regex" and right_match == "exact":
        return _ownership_matches(left, right_path)
    if left_match == "regex" and right_match == "regex":
        return left_path == right_path
    return False


def _validate_filesystem_ownership(
    blueprints: dict[Path, dict[str, Any]],
) -> list[str]:
    """Enforce interface-owned local filesystem read/write boundaries."""
    errors: list[str] = []
    all_interfaces: set[str] = set()
    ownerships: list[tuple[Path, str, dict[str, Any]]] = []
    accesses: list[tuple[Path, str, str, int, dict[str, Any]]] = []

    for blueprint_path, blueprint in blueprints.items():
        skill_name = blueprint_path.parent.name
        all_interfaces.update(_canonical_interface_names(skill_name, blueprint))
        interfaces = blueprint.get("interfaces") or {}
        if not isinstance(interfaces, dict):
            continue
        for namespace in ("machine", "llm"):
            namespace_interfaces = interfaces.get(namespace) or {}
            if not isinstance(namespace_interfaces, dict):
                continue
            for iface_name, spec in namespace_interfaces.items():
                if not isinstance(spec, dict):
                    continue
                canonical = f"{skill_name}.{namespace}.{iface_name}"
                for owner in spec.get("owns_filesystem") or []:
                    if isinstance(owner, dict):
                        ownerships.append((blueprint_path, canonical, owner))
                direct_io = spec.get("direct_io") or {}
                if not isinstance(direct_io, dict):
                    continue
                for section in ("reads", "writes"):
                    entries = direct_io.get(section) or []
                    if not isinstance(entries, list):
                        continue
                    for index, entry in enumerate(entries):
                        if (
                            isinstance(entry, dict)
                            and entry.get("medium") == "local-filesystem"
                            and isinstance(entry.get("path"), str)
                        ):
                            accesses.append((blueprint_path, canonical, section, index, entry))

    for owner_path, owner_interface, owner in ownerships:
        allowed_readers = owner.get("allowed_readers") or []
        if not isinstance(allowed_readers, list):
            continue
        if owner.get("match") == "regex" and isinstance(owner.get("path"), str):
            try:
                re.compile(owner["path"])
            except re.error as exc:
                errors.append(
                    f"{owner_path}: {owner_interface} owns_filesystem regex "
                    f"'{owner['path']}' is invalid: {exc}"
                )
        for reader in allowed_readers:
            if isinstance(reader, str) and reader not in all_interfaces:
                errors.append(
                    f"{owner_path}: {owner_interface} owns_filesystem allows unknown reader "
                    f"'{reader}'"
                )

    for left_index, (left_path, left_interface, left_owner) in enumerate(ownerships):
        for right_path, right_interface, right_owner in ownerships[left_index + 1:]:
            if left_interface == right_interface:
                continue
            if _ownerships_overlap(left_owner, right_owner):
                errors.append(
                    f"{right_path}: {right_interface} owns_filesystem overlaps with "
                    f"{left_interface}; filesystem ownership must have one writer authority"
                )

    for access_path, interface, section, index, entry in accesses:
        path = entry["path"]
        matching_owners = [
            (owner_path, owner_interface, owner)
            for owner_path, owner_interface, owner in ownerships
            if _ownership_matches(owner, path)
        ]
        for owner_path, owner_interface, owner in matching_owners:
            allowed_readers = set(owner.get("allowed_readers") or [])
            if section == "writes" and interface != owner_interface:
                errors.append(
                    f"{access_path}: {interface} direct_io.writes.{index}.path '{path}' "
                    f"is owned by {owner_interface}; only the owner may write it"
                )
            if section == "reads" and interface != owner_interface and interface not in allowed_readers:
                errors.append(
                    f"{access_path}: {interface} direct_io.reads.{index}.path '{path}' "
                    f"is owned by {owner_interface}; add this interface to allowed_readers "
                    "or read through an authorized interface"
                )
    return errors


def validate(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skills_root = repo_root / "skills"
    blueprint_template = repo_root / "references" / "blueprint" / "template.yaml"
    loaded_blueprints: dict[Path, dict[str, Any]] = {}
    legacy_blueprints: dict[Path, dict[str, Any]] = {}
    tracked_files = _git_tracked_files(repo_root)

    try:
        if not skills_root.is_dir():
            return errors
        skill_dirs = sorted(path for path in skills_root.iterdir() if path.is_dir())
    except OSError as exc:
        errors.append(f"{skills_root}: cannot traverse skills directory: {exc}")
        return errors

    if not blueprint_template.exists():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    try:
        schema = _load_schema()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{_SCHEMA_PATH}: cannot load blueprint schema: {exc}")
        schema = None
    if schema is None:
        errors.append(f"{_SCHEMA_PATH}: missing blueprint schema file")

    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        blueprint_path = skill_dir / "blueprint.yaml"

        if not skill_file.exists():
            continue
        if not blueprint_path.exists():
            errors.append(f"{skill_dir}: missing blueprint.yaml")
            continue

        # ── Schema validation (jsonschema) ───────────────────────────────────
        try:
            blueprint = yaml.load(blueprint_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            errors.append(f"{blueprint_path}: YAML parse error: {exc}")
            continue

        is_typed = isinstance(blueprint, dict) and (
            blueprint.get("schema_version") == 2 or "blueprint_type" in blueprint
        )
        if schema is not None and isinstance(blueprint, dict) and not is_typed:
            errors.extend(_validate_blueprint_schema(blueprint_path, blueprint, schema))

        if is_typed and schema is not None:
            try:
                graph = load_validated_skill_blueprint_graph(
                    skill_dir,
                    _SCHEMA_PATH.parent,
                )
            except (
                BlueprintGraphError,
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                yaml.YAMLError,
            ) as exc:
                errors.append(str(exc))
            else:
                expanded = expanded_legacy_blueprint(graph)
                loaded_blueprints[blueprint_path] = expanded
                if tracked_files is None:
                    errors.append(
                        f"{blueprint_path}: typed source validation requires a Git worktree"
                    )
                else:
                    errors.extend(_validate_typed_source_files(graph, repo_root, tracked_files))

        # ── Cross-field checks (Python only) ─────────────────────────────────
        if isinstance(blueprint, dict):
            if not is_typed:
                loaded_blueprints[blueprint_path] = blueprint
                legacy_blueprints[blueprint_path] = blueprint
            semantic_blueprint = loaded_blueprints.get(blueprint_path, blueprint)
            errors.extend(_validate_category_hierarchy(blueprint_path, semantic_blueprint))
            errors.extend(_validate_interface_cross_fields(blueprint_path, semantic_blueprint))
            errors.extend(
                _validate_direct_io_content_granularity(blueprint_path, semantic_blueprint)
            )
            errors.extend(_validate_direct_io_path_patterns(blueprint_path, semantic_blueprint))

        # ── SKILL.md marker checks ────────────────────────────────────────────
        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{skill_file}: cannot read SKILL.md: {exc}")
            continue
        start_count = text.count(CONTRACT_START)
        end_count = text.count(CONTRACT_END)
        has_contract = start_count > 0 or end_count > 0
        interface_start_count = text.count(INTERFACES_START)
        interface_end_count = text.count(INTERFACES_END)

        if start_count != end_count:
            errors.append(f"{skill_file}: blueprint contract markers are unbalanced")
        if start_count > 1 or end_count > 1:
            errors.append(f"{skill_file}: blueprint contract block must appear at most once")
        if interface_start_count != interface_end_count:
            errors.append(f"{skill_file}: blueprint interface markers are unbalanced")
        if interface_start_count > 1 or interface_end_count > 1:
            errors.append(f"{skill_file}: blueprint interface block must appear at most once")
        if not has_contract:
            errors.append(
                f"{skill_file}: local skill is missing generated blueprint contract block"
            )

    if errors:
        return errors

    errors.extend(_validate_filesystem_ownership(loaded_blueprints))
    if errors:
        return errors

    errors.extend(_validate_interface_uses(legacy_blueprints))
    if errors:
        return errors
    if not loaded_blueprints:
        return errors

    # ── Sync drift check ─────────────────────────────────────────────────────
    result = subprocess.run(
        [sys.executable, str(_SYNC_SCRIPT), "--check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        if result.stdout:
            errors.extend(result.stdout.splitlines())
        if result.stderr:
            errors.extend(result.stderr.splitlines())

    return errors


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint skill layout.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
