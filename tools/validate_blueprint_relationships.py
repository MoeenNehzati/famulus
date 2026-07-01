#!/usr/bin/env python3
"""Validate relationships between blueprints (inter-YAML constraints).

This script checks constraints that span multiple blueprint files:
- Version compatibility
- Self-dependency prevention
- Interface existence and access control
- Dependency declarations

All YAML-level structure validation is handled by blueprint.schema.json.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


class BlueprintError(Exception):
    """Raised when a blueprint relationship is invalid."""


def load_blueprints() -> dict[str, dict[str, Any]]:
    """Load all blueprint.yaml files."""
    blueprints: dict[str, dict[str, Any]] = {}
    for path in sorted(SKILLS_ROOT.glob("*/blueprint.yaml")):
        skill_name = path.parent.name
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise BlueprintError(f"{path}: failed to parse YAML: {e}")
        if not isinstance(data, dict):
            raise BlueprintError(f"{path}: top level must be a mapping")
        blueprints[skill_name] = data
    return blueprints


def validate_relationships(blueprints: dict[str, dict[str, Any]]) -> list[str]:
    """Validate inter-blueprint constraints.

    Checks:
    1. No skill depends on itself
    2. If dependency has a blueprint, major_version must be declared
    3. major_version matches dependency's interface_version
    4. Interface name in exports exists in dependency
    5. If interface is not public (allow_all_skills: false), caller must be in allowed_callers
    """
    errors: list[str] = []

    for skill_name, blueprint in blueprints.items():
        blueprint_path = SKILLS_ROOT / skill_name / "blueprint.yaml"
        depends_on = blueprint.get("depends_on") or {}

        if not isinstance(depends_on, dict):
            continue

        for dep_name, dep_spec in depends_on.items():
            if not isinstance(dep_name, str):
                continue

            # Check 1: Skill cannot depend on itself
            if dep_name == skill_name:
                errors.append(
                    f"{blueprint_path}: skill cannot depend on itself"
                )
                continue

            if dep_spec is None:
                dep_spec = {}
            if not isinstance(dep_spec, dict):
                continue

            # Get version from depends_on
            major_version = dep_spec.get("major_version")
            exports = dep_spec.get("exports") or []
            if not isinstance(exports, list):
                exports = []

            # Check 2 & 3: If dependency has a blueprint, validate version
            dep_blueprint = blueprints.get(dep_name)
            if dep_blueprint is not None:
                # Check 2: major_version must be declared
                if major_version is None:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name} must declare "
                        f"major_version because {dep_name} has a blueprint"
                    )
                    continue

                # Check 3: major_version must match
                dep_interface_version = dep_blueprint.get("interface_version")
                if major_version != dep_interface_version:
                    errors.append(
                        f"{blueprint_path}: depends_on.{dep_name}.major_version={major_version} "
                        f"does not match {dep_name} interface_version={dep_interface_version}"
                    )

            # Check 4 & 5: Validate exported interfaces
            if dep_blueprint is not None and exports:
                dep_script_interfaces = dep_blueprint.get("script_interfaces") or {}
                if not isinstance(dep_script_interfaces, dict):
                    continue

                for export_name in exports:
                    if not isinstance(export_name, str):
                        continue

                    interface_spec = dep_script_interfaces.get(export_name)

                    # Check 4: Interface must exist
                    if interface_spec is None:
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{export_name}`, which is not defined in {dep_name}"
                        )
                        continue

                    # Check 5: Access control validation
                    allow_all_skills = interface_spec.get("allow_all_skills", False)
                    allowed_callers = interface_spec.get("allowed_callers") or []
                    if not isinstance(allowed_callers, list):
                        allowed_callers = []

                    if not allow_all_skills and not allowed_callers:
                        # Interface is internal-only
                        errors.append(
                            f"{blueprint_path}: depends_on.{dep_name}.exports includes "
                            f"`{export_name}`, which is internal-only in {dep_name} "
                            f"(allow_all_skills: false and no allowed_callers)"
                        )
                    elif not allow_all_skills and skill_name not in allowed_callers:
                        # Interface is restricted and caller is not allowed
                        errors.append(
                            f"{blueprint_path}: skill {skill_name} is not in allowed_callers "
                            f"for {dep_name}.{export_name}. Allowed: {allowed_callers}"
                        )

    return errors


def main() -> int:
    """Load blueprints and validate relationships."""
    try:
        blueprints = load_blueprints()
    except BlueprintError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    errors = validate_relationships(blueprints)

    if errors:
        print("error: invalid blueprint relationships.", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
