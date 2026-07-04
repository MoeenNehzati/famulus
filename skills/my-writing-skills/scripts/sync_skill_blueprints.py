#!/usr/bin/env python3
"""Validate and sync skill blueprints into legacy compatibility artifacts.

Blueprints are hand-authored YAML files under ``skills/<name>/blueprint.yaml``.
This tool never rewrites blueprint files. It only validates them and syncs:

- ``depends_on_skills``
- ``permissions.json``
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
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "skills"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"
INTERFACES_START = "<!-- BEGIN BLUEPRINT INTERFACES -->"
INTERFACES_END = "<!-- END BLUEPRINT INTERFACES -->"
LEGACY_DEFAULT_FIELDS = ("patterns", "allow_all_skills", "allowed_callers")


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


def interface_id(interface_name: str, interface_spec: dict[str, Any], context: str) -> str:
    value = interface_spec.get("id")
    if not isinstance(value, str) or not value.strip():
        raise BlueprintError(f"{context}: missing non-empty string `id`")
    return value.strip()


def legacy_default_fields(interface_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        field: interface_spec[field]
        for field in LEGACY_DEFAULT_FIELDS
        if field in interface_spec
    }


def default_subinterface(interface_spec: dict[str, Any]) -> dict[str, Any]:
    explicit = interface_spec.get("default")
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise BlueprintError("default subinterface must be a mapping")
        return explicit
    return legacy_default_fields(interface_spec)


def named_subinterfaces(interface_spec: dict[str, Any], context: str) -> dict[str, dict[str, Any]]:
    raw = interface_spec.get("subinterfaces")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BlueprintError(f"{context}.subinterfaces: expected mapping")
    result: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise BlueprintError(f"{context}.subinterfaces.{name}: expected mapping")
        result[name] = spec
    return result


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


def validate_blueprints(blueprints: dict[str, SkillBlueprint]) -> list[str]:
    errors: list[str] = []
    for name, blueprint in blueprints.items():
        data = blueprint.data
        try:
            normalized_categories(data, str(blueprint.path))
        except BlueprintError as exc:
            errors.append(str(exc))

        version = data.get("interface_version")
        if not isinstance(version, int) or version < 1:
            errors.append(f"{blueprint.path}: `interface_version` must be a positive integer")

        depends_on = expect_mapping(data.get("depends_on"), f"{blueprint.path}:depends_on")
        for dep_name, dep_spec in depends_on.items():
            if not isinstance(dep_name, str):
                errors.append(f"{blueprint.path}: dependency names must be strings")
                continue
            if dep_name == name:
                errors.append(f"{blueprint.path}: skill cannot depend on itself")
                continue
            if dep_spec is None:
                dep_spec = {}
            if not isinstance(dep_spec, dict):
                errors.append(f"{blueprint.path}: depends_on.{dep_name} must be a mapping")
                continue
            major_version = dep_spec.get("major_version")
            exports = dep_spec.get("exports")
            if major_version is not None and (not isinstance(major_version, int) or major_version < 1):
                errors.append(
                    f"{blueprint.path}: depends_on.{dep_name}.major_version must be a positive integer"
                )
            if exports is not None:
                try:
                    expect_list_of_strings(exports, f"{blueprint.path}: depends_on.{dep_name}.exports")
                except BlueprintError as exc:
                    errors.append(str(exc))

            callee = blueprints.get(dep_name)
            if callee is None:
                continue
            callee_version = callee.data.get("interface_version")
            if major_version is None:
                errors.append(
                    f"{blueprint.path}: depends_on.{dep_name} must declare major_version because {dep_name} has a blueprint"
                )
                continue
            if major_version != callee_version:
                errors.append(
                    f"{blueprint.path}: depends_on.{dep_name}.major_version={major_version} "
                    f"does not match {dep_name} interface_version={callee_version}"
                )

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

        script_interfaces = expect_mapping(data.get("script_interfaces"), f"{blueprint.path}:script_interfaces")
        for interface_name, interface_spec in script_interfaces.items():
            context = f"{blueprint.path}: script_interfaces.{interface_name}"
            if not isinstance(interface_spec, dict):
                errors.append(f"{context}: expected mapping")
                continue
            try:
                interface_id(interface_name, interface_spec, context)
            except BlueprintError as exc:
                errors.append(str(exc))

            cwd = interface_spec.get("cwd", "skill_root")
            if cwd not in {"skill_root", "repo_root"}:
                errors.append(f"{context}: cwd must be `skill_root` or `repo_root`")
            command = interface_spec.get("command")
            if not isinstance(command, list) or not all(isinstance(token, str) and token for token in command):
                errors.append(f"{context}: missing non-empty string list `command`")

            if "default" in interface_spec and legacy_default_fields(interface_spec):
                errors.append(
                    f"{context}: cannot mix top-level default shorthand "
                    f"(`patterns`/`allow_all_skills`/`allowed_callers`) with `default`"
                )

            validate_access_surface(errors, interface_spec, context, allow_id=False)

            default_spec = interface_spec.get("default")
            if default_spec is not None:
                if not isinstance(default_spec, dict):
                    errors.append(f"{context}.default: expected mapping")
                else:
                    if "id" in default_spec:
                        errors.append(
                            f"{context}.default: must not define `id`; the default subinterface shares the parent interface id"
                        )
                    validate_access_surface(errors, default_spec, f"{context}.default", allow_id=False)

            sub_specs_raw = interface_spec.get("subinterfaces")
            if sub_specs_raw is not None:
                if not isinstance(sub_specs_raw, dict):
                    errors.append(f"{context}.subinterfaces: expected mapping")
                else:
                    for sub_name, sub_spec in sub_specs_raw.items():
                        sub_context = f"{context}.subinterfaces.{sub_name}"
                        if not isinstance(sub_spec, dict):
                            errors.append(f"{sub_context}: expected mapping")
                            continue
                        if not isinstance(sub_spec.get("id"), str) or not sub_spec["id"].strip():
                            errors.append(f"{sub_context}: missing non-empty string `id`")
                        validate_access_surface(errors, sub_spec, sub_context, allow_id=True)
    return errors


def generated_dependency_lines(data: dict[str, Any]) -> str:
    depends_on = expect_mapping(data.get("depends_on"), "depends_on")
    names = sorted(depends_on)
    if not names:
        return ""
    return "".join(f"{name}\n" for name in names)


def bash_pattern(entry: dict[str, Any]) -> str:
    tokens = [*entry["command"], *entry.get("args_prefix", [])]
    return f"Bash({' '.join(tokens)}:*)"


def network_patterns(entry: dict[str, Any]) -> list[str]:
    kind = entry["kind"]
    if kind == "web_search":
        return ["WebSearch"]
    return [f"WebFetch(https://{domain}/*)" for domain in entry["domains"]]


def generated_permissions(data: dict[str, Any]) -> dict[str, list[str]]:
    suggested = expect_mapping(data.get("suggested_permissions"), "suggested_permissions")
    result: dict[str, list[str]] = {"bash": [], "network": []}
    for entry in suggested.get("bash", []):
        result["bash"].append(bash_pattern(entry))
    for entry in suggested.get("network", []):
        result["network"].extend(network_patterns(entry))
    return result


def exported_interfaces(data: dict[str, Any]) -> list[str]:
    """Return ids of public interfaces/subinterfaces."""
    interfaces = expect_mapping(data.get("script_interfaces"), "script_interfaces")
    result: list[str] = []
    for interface_name, spec in interfaces.items():
        if not isinstance(spec, dict):
            continue
        parent_id = interface_id(interface_name, spec, f"script_interfaces.{interface_name}")
        owner_surface = default_subinterface(spec)
        if bool(owner_surface.get("allow_all_skills", False)):
            result.append(parent_id)
        for sub_spec in named_subinterfaces(spec, f"script_interfaces.{interface_name}").values():
            if bool(sub_spec.get("allow_all_skills", False)):
                result.append(str(sub_spec["id"]).strip())
    return sorted(result)


def interface_pattern_notes(spec: dict[str, Any]) -> list[tuple[str | None, str | None]]:
    """Return (name, notes) pairs for each pattern in the default subinterface.

    Notes are injected verbatim into SKILL.md so callers can use the interface
    without reading blueprint.yaml or the underlying script.
    """
    surface = default_subinterface(spec)
    patterns = surface.get("patterns") or []
    result: list[tuple[str | None, str | None]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            continue
        name = pattern.get("name") or None
        notes = pattern.get("notes") or None
        if name or notes:
            result.append((name, notes))
    return result


def owner_interfaces(
    data: dict[str, Any],
) -> list[tuple[str, str | None, list[tuple[str | None, str | None]]]]:
    """Return (id, description, pattern_notes) for each interface, sorted by id."""
    interfaces = expect_mapping(data.get("script_interfaces"), "script_interfaces")
    result: list[tuple[str, str | None, list[tuple[str | None, str | None]]]] = []
    for interface_name, spec in sorted(interfaces.items()):
        if not isinstance(spec, dict):
            continue
        description = spec.get("description")
        clean_description = description.strip() if isinstance(description, str) and description.strip() else None
        result.append(
            (
                interface_id(interface_name, spec, f"script_interfaces.{interface_name}"),
                clean_description,
                interface_pattern_notes(spec),
            )
        )
    return result


def generated_contract_block(data: dict[str, Any]) -> str:
    categories = normalized_categories(data, "generated_contract_block")
    depends_on = sorted(expect_mapping(data.get("depends_on"), "depends_on"))
    version = data["interface_version"]
    exports = exported_interfaces(data)

    lines = [
        CONTRACT_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
    ]
    lines.extend(f"Category: {category}" for category in categories)
    lines.append("")

    if depends_on:
        lines.append("Dependencies:")
        lines.extend(f"- {name}" for name in depends_on)
    else:
        lines.append("Dependencies: none")
    lines.extend(["", f"Interface Version: {version}", ""])

    if exports:
        lines.append("Exported Script Interfaces:")
        for name in exports:
            lines.append(f"- `{name}`")
    else:
        lines.append("Exported Script Interfaces: none")

    lines.extend([CONTRACT_END, ""])
    return "\n".join(lines)


def generated_interface_block(skill_name: str, data: dict[str, Any]) -> str:
    interfaces = owner_interfaces(data)
    if not interfaces:
        return ""

    lines = [
        INTERFACES_START,
        "> Generated from `blueprint.yaml`. Do not edit this block by hand.",
        "",
        "Owner-Facing Script Interfaces:",
        "",
        "Use the installed `dispatcher` command for this skill's script interfaces:",
    ]
    for interface_name, description, pattern_notes in interfaces:
        if description:
            lines.append(f"- `{interface_name}` — {description}")
        else:
            lines.append(f"- `{interface_name}`")
        lines.append(
            f"  - `dispatcher --caller-skill {skill_name} {skill_name} {interface_name} ...`"
        )
        for pat_name, pat_notes in pattern_notes:
            if pat_name and pat_notes:
                lines.append(f"  - {pat_name}: {pat_notes}")
            elif pat_notes:
                lines.append(f"  - {pat_notes}")
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
        text = pattern.sub(interface_block, text, count=1)
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
    expected_depends = generated_dependency_lines(data)
    expected_permissions = json.dumps(generated_permissions(data), indent=2) + "\n"
    expected_skill = sync_contract_block(skill_dir / "SKILL.md", generated_contract_block(data))
    expected_skill = sync_interface_block(expected_skill, generated_interface_block(blueprint.name, data))

    errors: list[str] = []

    depends_path = skill_dir / "depends_on_skills"
    current_depends = depends_path.read_text(encoding="utf-8") if depends_path.exists() else ""
    if current_depends != expected_depends:
        if check_only:
            errors.append(f"{depends_path}: out of sync with blueprint.yaml")
        else:
            depends_path.write_text(expected_depends, encoding="utf-8")

    permissions_path = skill_dir / "permissions.json"
    current_permissions = permissions_path.read_text(encoding="utf-8") if permissions_path.exists() else ""
    if current_permissions != expected_permissions:
        if check_only:
            errors.append(f"{permissions_path}: out of sync with blueprint.yaml")
        else:
            permissions_path.write_text(expected_permissions, encoding="utf-8")

    skill_path = skill_dir / "SKILL.md"
    current_skill = skill_path.read_text(encoding="utf-8")
    if current_skill != expected_skill:
        if check_only:
            errors.append(f"{skill_path}: generated blueprint blocks are out of sync")
        else:
            skill_path.write_text(expected_skill, encoding="utf-8")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and sync skill blueprints.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate blueprints and fail if generated artifacts are out of sync.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        blueprints = load_blueprints()
    except BlueprintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = validate_blueprints(blueprints)
    for blueprint in blueprints.values():
        errors.extend(sync_skill(blueprint, check_only=args.check))

    if errors:
        print("error: invalid or out-of-sync skill blueprints.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        if args.check:
            print(
                "Run `python3 skills/my-writing-skills/scripts/sync_skill_blueprints.py` to refresh generated artifacts.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
