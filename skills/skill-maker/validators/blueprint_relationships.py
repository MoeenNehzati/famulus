"""Validate inter-blueprint interface-use constraints."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


class BlueprintError(Exception):
    """Raised when a blueprint relationship is invalid."""


def load_blueprints(skills_root: Path) -> dict[str, dict[str, Any]]:
    """Load all blueprint.yaml files under skills_root."""
    blueprints: dict[str, dict[str, Any]] = {}
    for path in sorted(skills_root.glob("*/blueprint.yaml")):
        skill_name = path.parent.name
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise BlueprintError(f"{path}: failed to parse YAML: {e}")
        if not isinstance(data, dict):
            raise BlueprintError(f"{path}: top level must be a mapping")
        blueprints[skill_name] = data
    return blueprints


def _expect_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BlueprintError(f"{context}: expected mapping")
    return value


def _expect_string_list(value: Any, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise BlueprintError(f"{context}: expected list of non-empty strings")
    return value


def _split_canonical_interface(name: str) -> tuple[str, str, str] | None:
    parts = name.split(".")
    if len(parts) != 3:
        return None
    skill_name, namespace, interface_name = parts
    if not skill_name or namespace not in {"machine", "llm"} or not interface_name:
        return None
    return skill_name, namespace, interface_name


def _interfaces(data: dict[str, Any], namespace: str) -> dict[str, Any]:
    interfaces = _expect_mapping(data.get("interfaces"), "interfaces")
    return _expect_mapping(interfaces.get(namespace), f"interfaces.{namespace}")


def _canonical_interfaces(
    blueprints: dict[str, dict[str, Any]]
) -> dict[str, tuple[str, str, str, dict[str, Any], int]]:
    result: dict[str, tuple[str, str, str, dict[str, Any], int]] = {}
    for skill_name, blueprint in blueprints.items():
        for namespace in ("machine", "llm"):
            try:
                specs = _interfaces(blueprint, namespace)
            except BlueprintError:
                continue
            for interface_name, spec in specs.items():
                if not isinstance(spec, dict):
                    continue
                version = spec.get("version")
                if isinstance(version, int) and version >= 1:
                    result[f"{skill_name}.{namespace}.{interface_name}"] = (
                        skill_name,
                        namespace,
                        interface_name,
                        spec,
                        version,
                    )
    return result


def validate_relationships(
    blueprints: dict[str, dict[str, Any]], skills_root: Path
) -> list[str]:
    """Validate version-pinned uses_interfaces edges."""
    errors: list[str] = []
    canonical = _canonical_interfaces(blueprints)

    for skill_name, blueprint in blueprints.items():
        blueprint_path = skills_root / skill_name / "blueprint.yaml"
        if "depends_on" in blueprint:
            errors.append(f"{blueprint_path}: top-level `depends_on` has been removed; use `uses_interfaces`")

        for namespace in ("machine", "llm"):
            try:
                specs = _interfaces(blueprint, namespace)
            except BlueprintError:
                continue
            for interface_name, spec in specs.items():
                if not isinstance(spec, dict):
                    continue
                context = f"{blueprint_path}: interfaces.{namespace}.{interface_name}.uses_interfaces"
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
                    pinned_version = entry.get("version")
                    if not isinstance(target, str) or not target:
                        errors.append(f"{entry_context}.interface: expected non-empty string")
                        continue
                    parsed = _split_canonical_interface(target)
                    if parsed is None:
                        errors.append(f"{entry_context}.interface: must be `skill.machine.name` or `skill.llm.name`")
                        continue
                    target_skill, target_namespace, _target_name = parsed
                    target_record = canonical.get(target)
                    if target_record is None:
                        errors.append(f"{entry_context}.interface targets unknown interface `{target}`")
                        continue

                    _target_skill, _namespace, _name, target_spec, actual_version = target_record
                    if pinned_version != actual_version:
                        errors.append(
                            f"{entry_context} pins `{target}` version {pinned_version}, "
                            f"but target version is {actual_version}"
                        )

                    if namespace == "machine" and target_namespace != "machine":
                        errors.append(f"{entry_context}.interface targets `{target}`; machine interfaces may only use machine interfaces")
                    if namespace == "llm" and target_namespace == "machine" and target_skill != skill_name:
                        errors.append(
                            f"{entry_context}.interface targets `{target}`; "
                            "LLM interfaces may only use same-skill machine interfaces"
                        )

                    if target_skill == skill_name:
                        continue
                    allow_all_skills = bool(target_spec.get("allow_all_skills", False))
                    try:
                        allowed_callers = _expect_string_list(
                            target_spec.get("allowed_callers"),
                            f"{target}.allowed_callers",
                        )
                    except BlueprintError as exc:
                        errors.append(str(exc))
                        continue
                    if not allow_all_skills and skill_name not in allowed_callers:
                        errors.append(
                            f"{entry_context}.interface targets `{target}`, but `{skill_name}` "
                            f"is not allowed by target access control"
                        )

    return errors


def validate(repo_root: Path) -> list[str]:
    """Entry-point for runner: load blueprints and validate relationships."""
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return []
    try:
        blueprints = load_blueprints(skills_root)
    except BlueprintError as e:
        return [str(e)]
    return validate_relationships(blueprints, skills_root)


def main() -> int:
    errors = validate(Path(__file__).resolve().parents[3])
    if errors:
        print("error: invalid blueprint relationships.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
