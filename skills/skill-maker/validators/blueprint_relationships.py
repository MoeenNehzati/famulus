"""Validate relationships between blueprints (inter-YAML constraints)."""
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


def _interface_id(interface_name: str, interface_spec: dict[str, Any], context: str) -> str:
    value = interface_spec.get("id")
    if not isinstance(value, str) or not value.strip():
        raise BlueprintError(f"{context}: missing non-empty string `id`")
    return value.strip()


def _default_surface(interface_spec: dict[str, Any], context: str) -> dict[str, Any]:
    explicit = interface_spec.get("default")
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise BlueprintError(f"{context}.default: expected mapping")
        return explicit
    result: dict[str, Any] = {}
    for field in ("patterns", "allow_all_skills", "allowed_callers"):
        if field in interface_spec:
            result[field] = interface_spec[field]
    return result


def _named_subinterfaces(interface_spec: dict[str, Any], context: str) -> dict[str, dict[str, Any]]:
    raw = interface_spec.get("subinterfaces")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BlueprintError(f"{context}.subinterfaces: expected mapping")
    result: dict[str, dict[str, Any]] = {}
    for sub_name, sub_spec in raw.items():
        if not isinstance(sub_spec, dict):
            raise BlueprintError(f"{context}.subinterfaces.{sub_name}: expected mapping")
        result[sub_name] = sub_spec
    return result


def _resolve_callable_surface(
    blueprint: dict[str, Any],
    export_id: str,
) -> tuple[str, dict[str, Any]]:
    interfaces = _expect_mapping(blueprint.get("script_interfaces"), "script_interfaces")
    for interface_name, interface_spec in interfaces.items():
        context = f"script_interfaces.{interface_name}"
        if not isinstance(interface_spec, dict):
            raise BlueprintError(f"{context}: expected mapping")
        if _interface_id(interface_name, interface_spec, context) == export_id:
            return export_id, _default_surface(interface_spec, context)
        for sub_name, sub_spec in _named_subinterfaces(interface_spec, context).items():
            sub_id = sub_spec.get("id")
            if not isinstance(sub_id, str) or not sub_id.strip():
                raise BlueprintError(f"{context}.subinterfaces.{sub_name}: missing non-empty string `id`")
            if sub_id.strip() == export_id:
                return sub_id.strip(), sub_spec
    raise BlueprintError(f"script interface id `{export_id}` is not defined")


def validate_relationships(
    blueprints: dict[str, dict[str, Any]], skills_root: Path
) -> list[str]:
    """Validate inter-blueprint constraints."""
    errors: list[str] = []

    for skill_name, blueprint in blueprints.items():
        blueprint_path = skills_root / skill_name / "blueprint.yaml"
        depends_on = blueprint.get("depends_on") or {}

        if not isinstance(depends_on, dict):
            continue

        for dep_name, dep_spec in depends_on.items():
            if not isinstance(dep_name, str):
                continue

            if dep_name == skill_name:
                errors.append(f"{blueprint_path}: skill cannot depend on itself")
                continue

            if dep_spec is None:
                dep_spec = {}
            if not isinstance(dep_spec, dict):
                continue

            major_version = dep_spec.get("major_version")
            exports = dep_spec.get("exports") or []
            if not isinstance(exports, list):
                exports = []

            dep_blueprint = blueprints.get(dep_name)
            if dep_blueprint is not None:
                if major_version is None:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name} must declare "
                        f"major_version because {dep_name} has a blueprint"
                    )
                    continue

                dep_interface_version = dep_blueprint.get("interface_version")
                if major_version != dep_interface_version:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name}.major_version="
                        f"{major_version} does not match {dep_name} "
                        f"interface_version={dep_interface_version}"
                    )

            if dep_blueprint is not None and exports:
                for export_name in exports:
                    if not isinstance(export_name, str):
                        continue
                    try:
                        resolved_id, surface_spec = _resolve_callable_surface(dep_blueprint, export_name)
                    except BlueprintError:
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{export_name}`, which is not defined in {dep_name}"
                        )
                        continue

                    allow_all_skills = surface_spec.get("allow_all_skills", False)
                    try:
                        allowed_callers = _expect_string_list(
                            surface_spec.get("allowed_callers"),
                            f"{dep_name}:{resolved_id}.allowed_callers",
                        )
                    except BlueprintError as exc:
                        errors.append(str(exc))
                        continue

                    if not allow_all_skills and not allowed_callers:
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{resolved_id}`, which is internal-only in {dep_name}"
                        )
                    elif not allow_all_skills and skill_name not in allowed_callers:
                        errors.append(
                            f"{blueprint_path}: skill {skill_name} is not in "
                            f"allowed_callers for {dep_name}.{resolved_id}. "
                            f"Allowed: {allowed_callers}"
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
