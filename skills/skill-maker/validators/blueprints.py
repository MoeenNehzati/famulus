"""Validate blueprint presence, schema correctness, and contract-block sync rules."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

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
    errors: list[str] = []
    if jsonschema is None:
        return [
            f"{blueprint_path}: cannot validate blueprint schema because required "
            "Python package `jsonschema` is not installed"
        ]
    validator = jsonschema.Draft7Validator(schema)
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


_FIELD_LEVEL_CONTENT_SUFFIXES = frozenset({
    "body",
    "date",
    "header",
    "id",
    "name",
    "subject",
    "title",
})


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
                    target_skill, target_namespace, _target_name = parsed
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
                    if source_namespace == "machine" and target_namespace != "machine":
                        errors.append(
                            f"{blueprint_path}: {source} uses_interfaces.{index}.interface "
                            f"targets {target}; machine interfaces may only use machine interfaces"
                        )
                    if (
                        source_namespace == "llm"
                        and target_namespace == "machine"
                        and target_skill != skill_name
                    ):
                        errors.append(
                            f"{blueprint_path}: {source} uses_interfaces.{index}.interface "
                            f"targets {target}; LLM interfaces may only use same-skill machine "
                            "interfaces or LLM interfaces"
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

    if not skills_root.is_dir():
        return errors

    if not blueprint_template.exists():
        errors.append(f"{blueprint_template}: missing blueprint template reference file")

    schema = _load_schema()
    if schema is None:
        errors.append(f"{_SCHEMA_PATH}: missing blueprint schema file")

    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
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
        except yaml.YAMLError as exc:
            errors.append(f"{blueprint_path}: YAML parse error: {exc}")
            continue

        if schema is not None and isinstance(blueprint, dict):
            errors.extend(_validate_blueprint_schema(blueprint_path, blueprint, schema))

        # ── Cross-field checks (Python only) ─────────────────────────────────
        if isinstance(blueprint, dict):
            loaded_blueprints[blueprint_path] = blueprint
            errors.extend(_validate_category_hierarchy(blueprint_path, blueprint))
            errors.extend(_validate_interface_cross_fields(blueprint_path, blueprint))
            errors.extend(_validate_direct_io_content_granularity(blueprint_path, blueprint))

        # ── SKILL.md marker checks ────────────────────────────────────────────
        text = skill_file.read_text(encoding="utf-8")
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

    errors.extend(_validate_interface_uses(loaded_blueprints))
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
