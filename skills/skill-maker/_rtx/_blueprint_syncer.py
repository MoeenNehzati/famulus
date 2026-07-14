#!/usr/bin/env python3
"""Validate and sync skill blueprints into generated artifacts.

Blueprints are hand-authored YAML files under ``skills/<name>/blueprint.yaml``.
This tool never rewrites blueprint files. It only validates them and syncs:

- ``references/blueprint/runtime_dependencies.json``
- the generated contract block near the top of ``SKILL.md``
- the generated owner-facing dispatcher interface block in ``SKILL.md``

The contract block is injected immediately after the YAML frontmatter in
``SKILL.md``. The owner-facing dispatcher interface block is injected
immediately after the contract block. If a generated block already exists, it
is replaced in place.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.runtime.python_machine_interface import PythonMachineInterface
from officina.runtime.python_machine_interface_runner import run_python_machine_interface
from officina.common.blueprint_graph import expanded_legacy_blueprint, load_skill_blueprint_graph

SKILLS_ROOT = REPO_ROOT / "skills"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
RUNTIME_DEPENDENCIES_PATH = REPO_ROOT / "references" / "blueprint" / "runtime_dependencies.json"
BLUEPRINT_SCHEMA_ROOT = REPO_ROOT / "references" / "blueprint"
DEPENDENCY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-\[\]]*$")
PYTHON_MACHINE_INTERFACE_ENTRYPOINT_RE = re.compile(
    r"^_rtx/[A-Za-z_][A-Za-z0-9_]*\.py:[A-Za-z_][A-Za-z0-9_]*$"
)
RELATIVE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+")
REMOVED_DIRECT_FIELDS = ("directly_reads", "directly_executes", "directly_writes")
PLATFORM_NAMES = ("linux", "macos", "windows")
RUNTIME_DEPENDENCY_KINDS = (
    "python-package",
    "binary",
    "system-service",
    "system-library",
    "external-application",
    "runtime",
    "model-data",
)
RUNTIME_SYSTEM_SERVICE_NAMES = (
    "systemd-user",
    "launchd",
    "task-scheduler",
    "cron",
)


@dataclass(frozen=True)
class SkillBlueprint:
    name: str
    path: Path
    data: dict[str, Any]


class BlueprintError(Exception):
    """Raised when a blueprint is invalid."""


def normalized_categories(data: dict[str, Any], context: str) -> list[str]:
    value = data.get("category")
    if isinstance(value, str):
        if not value.strip():
            raise BlueprintError(f"{context}: `category` must not be empty")
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return value
    raise BlueprintError(f"{context}: `category` must be a string or list of non-empty strings")


def load_blueprints() -> dict[str, SkillBlueprint]:
    blueprints: dict[str, SkillBlueprint] = {}
    for path in sorted(SKILLS_ROOT.glob("*/blueprint.yaml")):
        skill_name = path.parent.name
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise BlueprintError(f"{path}: top level must be a mapping")
        if raw.get("schema_version") == 2 or "blueprint_type" in raw:
            try:
                raw = expanded_legacy_blueprint(
                    load_skill_blueprint_graph(
                        path.parent,
                        schema_root=BLUEPRINT_SCHEMA_ROOT,
                    )
                )
            except ValueError as exc:
                raise BlueprintError(str(exc)) from exc
        blueprints[skill_name] = SkillBlueprint(skill_name, path, raw)
    return blueprints


def expect_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BlueprintError(f"{context}: expected mapping")
    return value


def expect_list_of_strings(value: Any, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BlueprintError(f"{context}: expected list of strings")
    return value


def expect_invocation(value: Any, context: str, errors: list[str]) -> None:
    """Validate machine-interface invocation metadata."""
    if not isinstance(value, dict):
        errors.append(f"{context}: expected mapping")
        return
    kind = value.get("kind")
    if kind == "python_machine_interface":
        target = value.get("entrypoint")
        if not isinstance(target, str) or not target:
            errors.append(f"{context}: python_machine_interface invocation needs non-empty `entrypoint`")
            return
        if not PYTHON_MACHINE_INTERFACE_ENTRYPOINT_RE.fullmatch(target):
            errors.append(
                f"{context}: python_machine_interface entrypoint must look like "
                "`_rtx/file.py:Interface`"
            )
        args_prefix = value.get("args_prefix", [])
        if not isinstance(args_prefix, list) or not all(isinstance(token, str) and token for token in args_prefix):
            errors.append(f"{context}: python_machine_interface invocation needs string list `args_prefix`")
        expect_behavior_sources(value.get("behavior_sources"), f"{context}.behavior_sources", errors)
        return
    if kind == "command_file":
        path = value.get("path")
        if not isinstance(path, str) or not path.startswith("_cx/"):
            errors.append(f"{context}: command_file path must be under `_cx/`")
        args_prefix = value.get("args_prefix", [])
        if not isinstance(args_prefix, list) or not all(
            isinstance(token, str) and token for token in args_prefix
        ):
            errors.append(f"{context}: command_file invocation needs string list `args_prefix`")
        expect_behavior_sources(value.get("behavior_sources"), f"{context}.behavior_sources", errors)
        return
    errors.append(
        f"{context}: invocation kind must be `python_machine_interface` or `command_file`"
    )


def invocation_entrypoint_file(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind == "python_machine_interface":
        entrypoint = value.get("entrypoint")
        if isinstance(entrypoint, str) and ":" in entrypoint:
            return entrypoint.split(":", 1)[0]
        return None
    if kind == "command_file":
        path = value.get("path")
        return path if isinstance(path, str) else None
    return None


def expect_behavior_sources(value: Any, context: str, errors: list[str]) -> list[str]:
    if value is None:
        errors.append(f"{context}: required list, use [] when there are no behavior sources")
        return []
    if not isinstance(value, list):
        errors.append(f"{context}: expected list")
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for idx, entry in enumerate(value):
        entry_context = f"{context}[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{entry_context}: expected mapping")
            continue
        path = entry.get("path")
        content = entry.get("content")
        fmt = entry.get("format")
        reason = entry.get("reason")
        if not isinstance(path, str) or not path:
            errors.append(f"{entry_context}.path: expected non-empty string")
        elif not RELATIVE_PATH_RE.fullmatch(path):
            errors.append(f"{entry_context}.path: must be relative and must not contain `..` path segments")
        elif path in seen:
            errors.append(f"{entry_context}.path: duplicate behavior source `{path}`")
        else:
            seen.add(path)
            paths.append(path)
        if not isinstance(content, str) or not content:
            errors.append(f"{entry_context}.content: expected non-empty string")
        if not isinstance(fmt, str) or not fmt:
            errors.append(f"{entry_context}.format: expected non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{entry_context}.reason: expected non-empty string")
    return paths


def expect_runtime_dependencies(value: Any, context: str, errors: list[str]) -> None:
    if value is None:
        errors.append(f"{context}: required list, use [] when the interface has no runtime dependencies")
        return
    if not isinstance(value, list):
        errors.append(f"{context}: expected list")
        return

    seen: set[tuple[str, str]] = set()
    for idx, entry in enumerate(value):
        entry_context = f"{context}[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{entry_context}: expected mapping")
            continue
        kind = entry.get("kind")
        name = entry.get("name")
        version = entry.get("version")
        platforms = entry.get("platforms")
        reason = entry.get("reason")
        if kind not in RUNTIME_DEPENDENCY_KINDS:
            allowed = "`, `".join(RUNTIME_DEPENDENCY_KINDS)
            errors.append(f"{entry_context}.kind: must be one of `{allowed}`")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{entry_context}.name: must be a non-empty string")
        elif not DEPENDENCY_NAME_RE.fullmatch(name):
            errors.append(f"{entry_context}.name: must be a package or executable name, not a path or shell command")
        elif kind == "system-service" and name not in RUNTIME_SYSTEM_SERVICE_NAMES:
            allowed = "`, `".join(RUNTIME_SYSTEM_SERVICE_NAMES)
            errors.append(f"{entry_context}.name: system-service must be one of `{allowed}`")
        if not isinstance(version, str) or not version.strip():
            errors.append(f"{entry_context}.version: must be a non-empty string, use `any` when unconstrained")
        expect_platform_support(platforms, f"{entry_context}.platforms", errors)
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{entry_context}.reason: must be a non-empty string")
        if isinstance(kind, str) and isinstance(name, str):
            key = (kind, name)
            if key in seen:
                errors.append(f"{entry_context}: duplicate dependency `{kind}:{name}`")
            seen.add(key)


def expect_platform_support(value: Any, context: str, errors: list[str]) -> dict[str, bool] | None:
    if not isinstance(value, dict):
        errors.append(f"{context}: expected mapping with linux/macos/windows booleans")
        return None
    result: dict[str, bool] = {}
    extra = set(value) - set(PLATFORM_NAMES)
    if extra:
        errors.append(f"{context}: unsupported platform keys {sorted(extra)}")
    for platform in PLATFORM_NAMES:
        item = value.get(platform)
        if not isinstance(item, bool):
            errors.append(f"{context}.{platform}: must be boolean")
        else:
            result[platform] = item
    return result


def reject_removed_direct_fields(spec: dict[str, Any], context: str, errors: list[str]) -> None:
    for field in REMOVED_DIRECT_FIELDS:
        if field in spec:
            errors.append(
                f"{context}.{field}: removed; use `direct_io` for immediate I/O and "
                "`behavior_sources` for behavior-shaping files"
            )


def expect_llm_binding(value: Any, context: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{context}: expected mapping")
        return
    kind = value.get("kind")
    if kind == "skill_file":
        path = value.get("path")
        if path != "SKILL.md":
            errors.append(f"{context}: skill_file binding `path` must be `SKILL.md`")
        extra = set(value) - {"kind", "path"}
        if extra:
            errors.append(f"{context}: skill_file binding only accepts `kind` and `path`")
        return
    if kind == "markdown_file":
        path = value.get("path")
        if not isinstance(path, str) or not path.strip():
            errors.append(f"{context}: markdown_file binding needs non-empty `path`")
        elif path.startswith("/"):
            errors.append(f"{context}: markdown_file binding `path` must be relative to the blueprint directory")
        return
    if kind == "uri":
        uri = value.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            errors.append(f"{context}: uri binding needs non-empty `uri`")
        elif ":" not in uri:
            errors.append(f"{context}: uri binding `uri` must be absolute")
        return
    errors.append(f"{context}: binding kind must be `skill_file`, `markdown_file`, or `uri`")


def normalized_interface_maps(
    data: dict[str, Any], context: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    interfaces = expect_mapping(data.get("interfaces"), f"{context}.interfaces")
    machine = expect_mapping(interfaces.get("machine"), f"{context}.interfaces.machine")
    llm = expect_mapping(interfaces.get("llm"), f"{context}.interfaces.llm")
    return machine, llm


def validate_patterns(errors: list[str], patterns: Any, context: str) -> None:
    if patterns is None:
        return
    if not isinstance(patterns, list):
        errors.append(f"{context}: `patterns` must be a list")
        return
    if not patterns:
        errors.append(f"{context}: `patterns` must have at least one pattern")
        return
    for idx, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            errors.append(f"{context}[{idx}]: expected mapping")
            continue
        min_pos = pattern.get("min_positionals", 0)
        if not isinstance(min_pos, int) or min_pos < 0:
            errors.append(f"{context}[{idx}]: min_positionals must be non-negative integer")
        max_pos = pattern.get("max_positionals")
        if max_pos is not None and (not isinstance(max_pos, int) or max_pos < min_pos):
            errors.append(f"{context}[{idx}]: max_positionals must be >= min_positionals")
        for field_name in ("allow_stdin", "allow_extra_positionals"):
            if field_name in pattern and not isinstance(pattern[field_name], bool):
                errors.append(f"{context}[{idx}]: {field_name} must be boolean")
        for field_name in ("required_flags", "allowed_flags", "forbidden_flags"):
            if field_name in pattern:
                try:
                    expect_list_of_strings(pattern[field_name], f"{context}[{idx}].{field_name}")
                except BlueprintError as exc:
                    errors.append(str(exc))


def validate_access_surface(
    errors: list[str],
    spec: dict[str, Any],
    context: str,
    *,
    allow_id: bool,
) -> None:
    if allow_id and "id" in spec and (not isinstance(spec["id"], str) or not spec["id"].strip()):
        errors.append(f"{context}: `id` must be a non-empty string")

    validate_patterns(errors, spec.get("patterns"), f"{context}.patterns")

    if "allow_all_skills" in spec and not isinstance(spec["allow_all_skills"], bool):
        errors.append(f"{context}: `allow_all_skills` must be a boolean")
    if "allowed_callers" in spec:
        try:
            expect_list_of_strings(spec["allowed_callers"], f"{context}.allowed_callers")
        except BlueprintError as exc:
            errors.append(str(exc))


def expect_interface_version(value: Any, context: str, errors: list[str]) -> int | None:
    if not isinstance(value, int) or value < 1:
        errors.append(f"{context}.version: must be a positive integer")
        return None
    return value


def default_llm_version(data: dict[str, Any], context: str, errors: list[str] | None = None) -> int | None:
    try:
        _machine, llm = normalized_interface_maps(data, context)
    except BlueprintError as exc:
        if errors is not None:
            errors.append(str(exc))
        return None
    default = llm.get("default")
    if not isinstance(default, dict):
        if errors is not None:
            errors.append(f"{context}: interfaces.llm.default is required")
        return None
    version = default.get("version")
    if not isinstance(version, int) or version < 1:
        if errors is not None:
            errors.append(f"{context}: interfaces.llm.default.version must be a positive integer")
        return None
    return version


def canonical_interface_versions(skill_name: str, data: dict[str, Any]) -> dict[str, int]:
    machine, llm = normalized_interface_maps(data, f"{skill_name}.interfaces")
    versions: dict[str, int] = {}
    for namespace, specs in (("machine", machine), ("llm", llm)):
        for interface_name, spec in specs.items():
            if not isinstance(spec, dict):
                continue
            version = spec.get("version")
            if isinstance(version, int) and version >= 1:
                versions[f"{skill_name}.{namespace}.{interface_name}"] = version
    return versions


def split_canonical_interface(name: str) -> tuple[str, str, str] | None:
    parts = name.split(".")
    if len(parts) != 3:
        return None
    skill_name, namespace, interface_name = parts
    if not skill_name or namespace not in {"machine", "llm"} or not interface_name:
        return None
    return skill_name, namespace, interface_name


def validate_interface_uses(blueprints: dict[str, SkillBlueprint]) -> list[str]:
    errors: list[str] = []
    versions: dict[str, int] = {}
    for skill_name, blueprint in blueprints.items():
        try:
            versions.update(canonical_interface_versions(skill_name, blueprint.data))
        except BlueprintError:
            continue

    for skill_name, blueprint in blueprints.items():
        try:
            machine, llm = normalized_interface_maps(blueprint.data, str(blueprint.path))
        except BlueprintError:
            continue
        for namespace, specs in (("machine", machine), ("llm", llm)):
            for interface_name, spec in specs.items():
                if not isinstance(spec, dict):
                    continue
                context = f"{blueprint.path}: interfaces.{namespace}.{interface_name}.uses_interfaces"
                raw_uses = spec.get("uses_interfaces", [])
                if not isinstance(raw_uses, list):
                    errors.append(f"{context}: expected list")
                    continue
                for idx, entry in enumerate(raw_uses):
                    entry_context = f"{context}[{idx}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{entry_context}: expected mapping with `interface` and `version`")
                        continue
                    target = entry.get("interface")
                    pinned = entry.get("version")
                    if not isinstance(target, str) or not target:
                        errors.append(f"{entry_context}.interface: expected non-empty string")
                        continue
                    parsed = split_canonical_interface(target)
                    if parsed is None:
                        errors.append(f"{entry_context}.interface: must be `skill.machine.name` or `skill.llm.name`")
                        continue
                    target_skill, target_namespace, _target_name = parsed
                    if namespace == "machine" and target_namespace != "machine":
                        errors.append(f"{entry_context}.interface targets {target}; machine interfaces may only use machine interfaces")
                    if namespace == "llm" and target_namespace == "machine" and target_skill != skill_name:
                        errors.append(
                            f"{entry_context}.interface targets {target}; LLM interfaces may only use same-skill machine interfaces"
                        )
                    actual = versions.get(target)
                    if actual is None:
                        errors.append(f"{entry_context}.interface targets unknown interface {target}")
                    elif pinned != actual:
                        errors.append(f"{entry_context} pins {target} version {pinned}, but target version is {actual}")
    return errors


def validate_blueprints(blueprints: dict[str, SkillBlueprint]) -> list[str]:
    errors: list[str] = []
    for name, blueprint in blueprints.items():
        data = blueprint.data
        try:
            normalized_categories(data, str(blueprint.path))
        except BlueprintError as exc:
            errors.append(str(exc))

        if "interface_version" in data:
            errors.append(f"{blueprint.path}: top-level `interface_version` has been removed; set interface `version` fields")
        if "depends_on" in data:
            errors.append(f"{blueprint.path}: top-level `depends_on` has been removed; use interface `uses_interfaces`")
        if "script_interfaces" in data:
            errors.append(f"{blueprint.path}: `script_interfaces` has been removed; use `interfaces.machine`")
        if not isinstance(data.get("interfaces"), dict):
            errors.append(f"{blueprint.path}: `interfaces` must be a mapping")

        suggested_permissions = expect_mapping(
            data.get("suggested_permissions"), f"{blueprint.path}:suggested_permissions"
        )
        for category, entries in suggested_permissions.items():
            if category not in {"bash", "network"}:
                errors.append(f"{blueprint.path}: unsupported suggested_permissions category `{category}`")
                continue
            if not isinstance(entries, list):
                errors.append(f"{blueprint.path}: suggested_permissions.{category} must be a list")
                continue
            for idx, entry in enumerate(entries):
                context = f"{blueprint.path}: suggested_permissions.{category}[{idx}]"
                if not isinstance(entry, dict):
                    errors.append(f"{context}: expected mapping")
                    continue
                if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
                    errors.append(f"{context}: missing non-empty `reason`")
                if category == "bash":
                    if not isinstance(entry.get("command"), list) or not all(
                        isinstance(token, str) and token for token in entry["command"]
                    ):
                        errors.append(f"{context}: bash permission needs non-empty string list `command`")
                    if "args_prefix" in entry and not (
                        isinstance(entry["args_prefix"], list)
                        and all(isinstance(token, str) and token for token in entry["args_prefix"])
                    ):
                        errors.append(f"{context}: args_prefix must be a list of non-empty strings")
                if category == "network":
                    kind = entry.get("kind")
                    if kind not in {"web_search", "web_fetch"}:
                        errors.append(f"{context}: network permission kind must be `web_search` or `web_fetch`")
                    if kind == "web_fetch":
                        domains = entry.get("domains")
                        if not isinstance(domains, list) or not all(
                            isinstance(domain, str) and domain for domain in domains
                        ):
                            errors.append(f"{context}: web_fetch permission needs non-empty string list `domains`")

        machine_interfaces, llm_interfaces = normalized_interface_maps(data, str(blueprint.path))
        for interface_name, interface_spec in machine_interfaces.items():
            context = f"{blueprint.path}: interfaces.machine.{interface_name}"
            if not isinstance(interface_spec, dict):
                errors.append(f"{context}: expected mapping")
                continue
            expect_interface_version(interface_spec.get("version"), context, errors)
            validate_access_surface(errors, interface_spec, context, allow_id=False)
            interface_platforms = expect_platform_support(
                interface_spec.get("platform_support"),
                f"{context}.platform_support",
                errors,
            )
            if "runtime" in interface_spec:
                errors.append(f"{context}.runtime: renamed to `invocation`")
            expect_invocation(interface_spec.get("invocation"), f"{context}.invocation", errors)
            expect_runtime_dependencies(interface_spec.get("dependencies"), f"{context}.dependencies", errors)
            if interface_platforms is not None:
                for idx, dependency in enumerate(interface_spec.get("dependencies") or []):
                    if not isinstance(dependency, dict):
                        continue
                    dependency_platforms = dependency.get("platforms")
                    if not isinstance(dependency_platforms, dict):
                        continue
                    for platform in PLATFORM_NAMES:
                        if dependency_platforms.get(platform) is True and interface_platforms.get(platform) is not True:
                            errors.append(
                                f"{context}.dependencies[{idx}].platforms.{platform}: "
                                "dependency cannot support a platform the interface does not support"
                            )
            reject_removed_direct_fields(interface_spec, context, errors)
        for interface_name, interface_spec in llm_interfaces.items():
            context = f"{blueprint.path}: interfaces.llm.{interface_name}"
            if not isinstance(interface_spec, dict):
                errors.append(f"{context}: expected mapping")
                continue
            expect_interface_version(interface_spec.get("version"), context, errors)
            if not isinstance(interface_spec.get("description"), str) or not interface_spec["description"].strip():
                errors.append(f"{context}: missing non-empty `description`")
            binding = interface_spec.get("binding")
            file = interface_spec.get("file")
            if binding is None:
                if not isinstance(file, str) or not file.strip():
                    errors.append(f"{context}: missing `binding` (or non-empty `file`)")
                elif file.startswith("/"):
                    errors.append(f"{context}: `file` must be relative to the blueprint directory")
            else:
                expect_llm_binding(binding, f"{context}.binding", errors)
            if "runtime" in interface_spec:
                errors.append(f"{context}.runtime: llm interfaces must not define `runtime`")
            if "invocation" in interface_spec:
                errors.append(f"{context}.invocation: llm interfaces must not define `invocation`")
            expect_behavior_sources(interface_spec.get("behavior_sources"), f"{context}.behavior_sources", errors)
            if "allow_all_skills" in interface_spec and not isinstance(interface_spec["allow_all_skills"], bool):
                errors.append(f"{context}: `allow_all_skills` must be a boolean")
            if "allowed_callers" in interface_spec:
                try:
                    expect_list_of_strings(interface_spec["allowed_callers"], f"{context}.allowed_callers")
                except BlueprintError as exc:
                    errors.append(str(exc))
            reject_removed_direct_fields(interface_spec, context, errors)
        default_llm = llm_interfaces.get("default")
        if not isinstance(default_llm, dict):
            errors.append(f"{blueprint.path}: interfaces.llm.default is required")
        else:
            binding = default_llm.get("binding")
            if binding != {"kind": "skill_file", "path": "SKILL.md"}:
                errors.append(
                    f"{blueprint.path}: interfaces.llm.default.binding must be "
                    "`{kind: skill_file, path: SKILL.md}`"
                )
    errors.extend(validate_interface_uses(blueprints))
    return errors


def exported_interfaces(skill_name: str, data: dict[str, Any]) -> list[str]:
    """Return ids of public interfaces."""
    machine, llm = normalized_interface_maps(data, "interfaces")
    result: list[str] = []
    for interface_name, spec in sorted(machine.items()):
        if not isinstance(spec, dict):
            continue
        if bool(spec.get("allow_all_skills", False)):
            result.append(f"{skill_name}.machine.{interface_name}")
    for interface_name, spec in sorted(llm.items()):
        if not isinstance(spec, dict):
            continue
        if bool(spec.get("allow_all_skills", False)):
            result.append(f"{skill_name}.llm.{interface_name}")
    return result


def owner_interfaces(
    data: dict[str, Any],
) -> list[tuple[str, str | None, str | None, list[tuple[str | None, str | None]]]]:
    """Return (id, description, usage, pattern_notes) for each interface, sorted by id."""
    interfaces, _llm = normalized_interface_maps(data, "interfaces")
    result: list[tuple[str, str | None, str | None, list[tuple[str | None, str | None]]]] = []
    for interface_name, spec in sorted(interfaces.items()):
        if not isinstance(spec, dict):
            continue
        description = spec.get("description")
        clean_description = description.strip() if isinstance(description, str) and description.strip() else None
        usage = spec.get("usage")
        if usage is None:
            clean_usage = None
        elif isinstance(usage, str):
            clean_usage = usage.strip()
        else:
            clean_usage = None
        patterns = spec.get("patterns") or []
        pattern_notes: list[tuple[str | None, str | None]] = []
        if isinstance(patterns, list):
            for pattern in patterns:
                if not isinstance(pattern, dict):
                    continue
                name = pattern.get("name") or None
                notes = pattern.get("notes") or None
                if name or notes:
                    pattern_notes.append((name, notes))
        result.append((interface_name, clean_description, clean_usage, pattern_notes))
    return result


def generated_contract_block(skill_name: str, data: dict[str, Any]) -> str:
    categories = normalized_categories(data, "generated_contract_block")
    version = default_llm_version(data, "generated_contract_block") or 1
    machine, llm = normalized_interface_maps(data, "interfaces")
    uses: list[str] = []
    for namespace, specs in (("machine", machine), ("llm", llm)):
        for interface_name, spec in sorted(specs.items()):
            if not isinstance(spec, dict):
                continue
            for entry in spec.get("uses_interfaces", []) or []:
                if isinstance(entry, dict) and isinstance(entry.get("interface"), str):
                    pinned = entry.get("version")
                    if isinstance(pinned, int):
                        uses.append(f"{skill_name}.{namespace}.{interface_name} -> {entry['interface']}@{pinned}")
                    else:
                        uses.append(f"{skill_name}.{namespace}.{interface_name} -> {entry['interface']}")
    exports = exported_interfaces(skill_name, data)

    lines = [
        CONTRACT_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    lines.extend(f"Category: {category}" for category in categories)
    lines.append("")

    lines.append(f"Skill Version: {version}")
    lines.append("")

    if uses:
        lines.append("Uses Interfaces:")
        lines.extend(f"- `{name}`" for name in sorted(set(uses)))
    else:
        lines.append("Uses Interfaces: none")
    lines.append("")

    if exports:
        lines.append("Public Interfaces:")
        for name in exports:
            lines.append(f"- `{name}`")
    else:
        lines.append("Public Interfaces: none")

    lines.extend([CONTRACT_END, ""])
    return "\n".join(lines)


def generated_interface_block(skill_name: str, data: dict[str, Any]) -> str:
    machine_interfaces = owner_interfaces(data)
    _machine, llm_interfaces = normalized_interface_maps(data, "interfaces")
    visible_machine = [entry for entry in machine_interfaces if entry[1]]
    visible_llm = [
        (name, spec)
        for name, spec in sorted(llm_interfaces.items())
        if isinstance(spec, dict) and isinstance(spec.get("description"), str) and spec["description"].strip()
    ]
    if not visible_machine and not visible_llm:
        return ""

    lines = [
        INTERFACES_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    if visible_machine:
        lines.extend([
            "Owner-Facing Machine Interfaces:",
            "",
            "Use the installed `dispatcher` command for this skill's machine interfaces:",
        ])
        for interface_name, description, usage, pattern_notes in visible_machine:
            lines.append(f"- `{interface_name}` — {description}")
            args = f" {usage}" if usage else ("" if usage == "" else " ...")
            lines.append(
                f"  - `dispatcher --caller-skill {skill_name} {skill_name}.machine.{interface_name}{args}`"
            )
            for pat_name, pat_notes in pattern_notes:
                if pat_name and pat_notes:
                    lines.append(f"  - {pat_name}: {pat_notes}")
                elif pat_notes:
                    lines.append(f"  - {pat_notes}")
        lines.append("")
    if visible_llm:
        lines.extend([
            "Owner-Facing LLM Interfaces:",
            "",
            "These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:",
        ])
        for interface_name, spec in visible_llm:
            lines.append(f"- `{interface_name}` — {spec['description'].strip()}")
            binding = spec.get("binding")
            if isinstance(binding, dict):
                kind = binding.get("kind")
                if kind == "skill_file" and isinstance(binding.get("path"), str):
                    lines.append(f"  - binding: skill file `{binding['path']}`")
                elif kind == "markdown_file" and isinstance(binding.get("path"), str):
                    lines.append(f"  - binding: relative markdown path `{binding['path']}`")
                elif kind == "uri" and isinstance(binding.get("uri"), str):
                    lines.append(f"  - binding: uri `{binding['uri']}`")
            elif isinstance(spec.get("file"), str):
                lines.append(f"  - binding: relative markdown path `{spec['file']}`")
    lines.extend([INTERFACES_END, ""])
    return "\n".join(lines)


def sync_contract_block(skill_file: Path, contract_block: str) -> str:
    """Inject or replace the generated blueprint contract block in SKILL.md."""
    text = skill_file.read_text(encoding="utf-8")
    if CONTRACT_START in text and CONTRACT_END in text:
        pattern = re.compile(
            rf"{re.escape(CONTRACT_START)}.*?{re.escape(CONTRACT_END)}\n?",
            re.DOTALL,
        )
        updated = pattern.sub(contract_block, text, count=1)
        return strip_legacy_contract_metadata(updated)

    match = re.match(r"(---\n.*?\n---\n+)", text, re.DOTALL)
    if not match:
        raise BlueprintError(f"{skill_file}: missing YAML frontmatter")
    updated = text[: match.end()] + contract_block + text[match.end() :]
    return strip_legacy_contract_metadata(updated)


def sync_interface_block(text: str, interface_block: str) -> str:
    """Inject, replace, or remove the generated owner-facing interface block."""
    if INTERFACES_START in text and INTERFACES_END in text:
        pattern = re.compile(
            rf"{re.escape(INTERFACES_START)}.*?{re.escape(INTERFACES_END)}\n?",
            re.DOTALL,
        )
        text = pattern.sub(lambda _: interface_block, text, count=1)
        return re.sub(r"\n{3,}", "\n\n", text)

    if not interface_block:
        return text

    contract_match = re.search(rf"{re.escape(CONTRACT_END)}\n*", text)
    if contract_match:
        updated = text[: contract_match.end()] + interface_block + text[contract_match.end() :]
        return re.sub(r"\n{3,}", "\n\n", updated)

    frontmatter_match = re.match(r"(---\n.*?\n---\n+)", text, re.DOTALL)
    if not frontmatter_match:
        raise BlueprintError("SKILL.md: missing YAML frontmatter for interface injection")
    updated = text[: frontmatter_match.end()] + interface_block + text[frontmatter_match.end() :]
    return re.sub(r"\n{3,}", "\n\n", updated)


def strip_legacy_contract_metadata(text: str) -> str:
    """Remove stale top-of-file Category/Dependencies lines after blueprint injection."""
    if CONTRACT_END not in text:
        return text

    prefix, suffix = text.split(CONTRACT_END, 1)
    lines = suffix.splitlines()
    cutoff = len(lines)
    for idx, line in enumerate(lines):
        if line.startswith("## ") or line == INTERFACES_START:
            cutoff = idx
            break

    cleaned_prefix: list[str] = []
    i = 0
    while i < cutoff:
        line = lines[i]
        if re.fullmatch(r"Category:\s*.+", line):
            i += 1
            continue
        if re.fullmatch(r"Dependencies:\s*none\s*", line):
            i += 1
            continue
        if re.fullmatch(r"Dependencies:\s*", line):
            i += 1
            while i < cutoff and re.fullmatch(r"\s*-\s+.+", lines[i]):
                i += 1
            continue
        cleaned_prefix.append(line)
        i += 1

    remainder = cleaned_prefix + lines[cutoff:]
    rebuilt = "\n".join(remainder)
    rebuilt = re.sub(r"\n{3,}", "\n\n", rebuilt)
    if suffix.endswith("\n"):
        rebuilt += "\n"
    return prefix + CONTRACT_END + rebuilt


def sync_skill(blueprint: SkillBlueprint, check_only: bool) -> list[str]:
    data = blueprint.data
    skill_dir = blueprint.path.parent
    expected_skill = sync_contract_block(skill_dir / "SKILL.md", generated_contract_block(blueprint.name, data))
    expected_skill = sync_interface_block(expected_skill, generated_interface_block(blueprint.name, data))

    errors: list[str] = []

    skill_path = skill_dir / "SKILL.md"
    current_skill = skill_path.read_text(encoding="utf-8")
    if current_skill != expected_skill:
        if check_only:
            errors.append(f"{skill_path}: generated blueprint blocks are out of sync")
        else:
            skill_path.write_text(expected_skill, encoding="utf-8")

    return errors


def generated_runtime_dependencies_manifest(blueprints: dict[str, SkillBlueprint]) -> dict[str, Any]:
    """Build the stdlib-readable dependency manifest from blueprint interfaces."""
    skills: dict[str, Any] = {}
    all_dependencies: dict[str, set[str]] = {kind: set() for kind in RUNTIME_DEPENDENCY_KINDS}

    for skill_name, blueprint in sorted(blueprints.items()):
        generated_interfaces: dict[str, Any] = {}

        machine_interfaces, _ = normalized_interface_maps(blueprint.data, str(blueprint.path))
        interface_items = [
            (interface_name, f"{skill_name}.machine.{interface_name}", interface_spec)
            for interface_name, interface_spec in sorted(machine_interfaces.items())
        ]

        for interface_name, interface_id_value, interface_spec in interface_items:
            if not isinstance(interface_spec, dict):
                continue
            raw_dependencies = interface_spec.get("dependencies", [])
            dependencies: list[dict[str, str]] = []
            if isinstance(raw_dependencies, list):
                for entry in raw_dependencies:
                    if not isinstance(entry, dict):
                        continue
                    kind = entry.get("kind")
                    name = entry.get("name")
                    version = entry.get("version")
                    platforms = entry.get("platforms")
                    reason = entry.get("reason")
                    if (
                        kind not in all_dependencies
                        or not isinstance(name, str)
                        or not isinstance(version, str)
                        or not isinstance(platforms, dict)
                        or not isinstance(reason, str)
                    ):
                        continue
                    clean_platforms = {
                        platform: bool(platforms.get(platform))
                        for platform in PLATFORM_NAMES
                    }
                    dependencies.append(
                        {
                            "kind": kind,
                            "name": name,
                            "version": version,
                            "platforms": clean_platforms,
                            "reason": reason,
                        }
                    )
                    all_dependencies[kind].add(name)

            generated_interfaces[interface_name] = {
                "id": interface_id_value,
                "dependencies": dependencies,
            }

        if generated_interfaces:
            skills[skill_name] = {"interfaces": generated_interfaces}

    return {
        "version": 1,
        "skills": skills,
        "all": {kind: sorted(all_dependencies[kind]) for kind in RUNTIME_DEPENDENCY_KINDS},
    }


def sync_runtime_dependencies_manifest(
    blueprints: dict[str, SkillBlueprint],
    check_only: bool,
) -> list[str]:
    expected = json.dumps(generated_runtime_dependencies_manifest(blueprints), indent=2) + "\n"
    current = RUNTIME_DEPENDENCIES_PATH.read_text(encoding="utf-8") if RUNTIME_DEPENDENCIES_PATH.exists() else ""
    if current == expected:
        return []
    if check_only:
        return [f"{RUNTIME_DEPENDENCIES_PATH}: out of sync with blueprint.yaml"]
    RUNTIME_DEPENDENCIES_PATH.write_text(expected, encoding="utf-8")
    return []


class Interface(PythonMachineInterface):
    description = "Validate and sync skill blueprints."
    prog = "_blueprint_syncer.py"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate blueprints and fail if generated artifacts are out of sync.",
        )
        return parser

    def run(self, args: argparse.Namespace) -> int:
        return run_sync(check_only=args.check)


def run_sync(*, check_only: bool) -> int:
    try:
        blueprints = load_blueprints()
    except BlueprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = validate_blueprints(blueprints)
    for blueprint in blueprints.values():
        errors.extend(sync_skill(blueprint, check_only=check_only))
    errors.extend(sync_runtime_dependencies_manifest(blueprints, check_only=check_only))

    if errors:
        print("error: invalid or out-of-sync skill blueprints.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        if check_only:
            print(
                "Run `python3 skills/skill-maker/_rtx/_blueprint_syncer.py` to refresh generated artifacts.",
                file=sys.stderr,
            )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_python_machine_interface(Interface(), sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
