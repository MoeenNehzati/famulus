#!/usr/bin/env python3
"""Validate and sync skill blueprints into legacy compatibility artifacts.

Blueprints are hand-authored YAML files under ``skills/<name>/blueprint.yaml``.
This tool never rewrites blueprint files. It only validates them and syncs:

- ``depends_on_skills``
- ``permissions.json``
- the generated contract block near the top of ``SKILL.md``

The contract block is injected immediately after the YAML frontmatter in
``SKILL.md``. If a block already exists, it is replaced in place.
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


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"
CONTRACT_START = "<!-- BEGIN BLUEPRINT CONTRACT -->"
CONTRACT_END = "<!-- END BLUEPRINT CONTRACT -->"


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
            # Validate exports: interface can be public (allow_all_skills: true) OR
            # restricted to specific callers (allow_all_skills: false with allowed_callers).
            callee_interfaces = expect_mapping(callee.data.get("script_interfaces"), "script_interfaces")
            for export_name in expect_list_of_strings(exports, f"{blueprint.path}: depends_on.{dep_name}.exports"):
                interface_spec = callee_interfaces.get(export_name)
                if not interface_spec:
                    errors.append(
                        f"{blueprint.path}: depends_on.{dep_name}.exports includes `{export_name}`, "
                        f"which is not defined by {dep_name}"
                    )
                    continue
                allow_all_skills = interface_spec.get("allow_all_skills", False)
                has_allowed_callers = bool(expect_list_of_strings(interface_spec.get("allowed_callers"), ""))
                if not allow_all_skills and not has_allowed_callers:
                    errors.append(
                        f"{blueprint.path}: depends_on.{dep_name}.exports includes `{export_name}`, "
                        f"which is internal-only (not public and no allowed_callers)"
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
            cwd = interface_spec.get("cwd", "skill_root")
            if cwd not in {"skill_root", "repo_root"}:
                errors.append(f"{context}: cwd must be `skill_root` or `repo_root`")
            command = interface_spec.get("command")
            if not isinstance(command, list) or not all(isinstance(token, str) and token for token in command):
                errors.append(f"{context}: missing non-empty string list `command`")

            # Validate new schema: patterns
            patterns = interface_spec.get("patterns")
            if patterns is not None:
                if not isinstance(patterns, list):
                    errors.append(f"{context}: `patterns` must be a list")
                    continue
                if not patterns:
                    errors.append(f"{context}: `patterns` must have at least one pattern")
                    continue
                for idx, pattern in enumerate(patterns):
                    if not isinstance(pattern, dict):
                        errors.append(f"{context}.patterns[{idx}]: expected mapping")
                        continue
                    min_pos = pattern.get("min_positionals", 0)
                    if not isinstance(min_pos, int) or min_pos < 0:
                        errors.append(f"{context}.patterns[{idx}]: min_positionals must be non-negative integer")
                    max_pos = pattern.get("max_positionals")
                    if max_pos is not None and (not isinstance(max_pos, int) or max_pos < min_pos):
                        errors.append(f"{context}.patterns[{idx}]: max_positionals must be >= min_positionals")
                    for field_name in ("allow_stdin", "allow_extra_positionals"):
                        if field_name in pattern and not isinstance(pattern[field_name], bool):
                            errors.append(f"{context}.patterns[{idx}]: {field_name} must be boolean")
                    for field_name in ("required_flags", "allowed_flags", "forbidden_flags"):
                        if field_name in pattern:
                            try:
                                expect_list_of_strings(pattern[field_name], f"{context}.patterns[{idx}].{field_name}")
                            except BlueprintError as exc:
                                errors.append(str(exc))

            # Validate allow_all_skills and allowed_callers
            if "allow_all_skills" in interface_spec and not isinstance(interface_spec["allow_all_skills"], bool):
                errors.append(f"{context}: `allow_all_skills` must be a boolean")
            if "allowed_callers" in interface_spec:
                try:
                    expect_list_of_strings(interface_spec["allowed_callers"], f"{context}.allowed_callers")
                except BlueprintError as exc:
                    errors.append(str(exc))
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
    """Return list of public interface names (allow_all_skills: true)."""
    interfaces = expect_mapping(data.get("script_interfaces"), "script_interfaces")
    result: list[str] = []
    for name, spec in interfaces.items():
        if isinstance(spec, dict) and spec.get("allow_all_skills", False):
            result.append(name)
    return sorted(result)


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


def strip_legacy_contract_metadata(text: str) -> str:
    """Remove stale top-of-file Category/Dependencies lines after blueprint injection."""
    if CONTRACT_END not in text:
        return text

    prefix, suffix = text.split(CONTRACT_END, 1)
    lines = suffix.splitlines()
    cutoff = len(lines)
    for idx, line in enumerate(lines):
        if line.startswith("## "):
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
            errors.append(f"{skill_path}: generated blueprint contract block is out of sync")
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
                "Run `python3 tools/sync_skill_blueprints.py` to refresh generated artifacts.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
